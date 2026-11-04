"""SOLVE_ADMIN_ONLY - nobody but an admin STARTS a calculation (owner 2026-11-04: "расчёты никто не может запускать кроме меня пока").

One server setting, ``MOTRES_SOLVE_ADMIN_ONLY`` (default ON, read per call in ``auth.solve_admin_only``).  What is pinned here:

* the tier table: every solve route resolves to ``admin`` while the setting is on and to its OLD tier when it is off ("exactly as today");
* the role gate (the real ``RoleGateMiddleware`` in front of stub handlers, so nothing is solved): a user (a project member is a user
  account - membership gives no tier), a global engineer, an OEM producer and an anonymous caller are refused on every solve class
  (403 ``solve.admin_only`` with the one sentence; 401 for the anonymous one), an admin passes; setting off restores today's answers;
* reads stay open: stored results, previews and progress keep their tier, ``/api/me`` says ``canSolve``;
* the second lock at the job queue's door and the MCP ``simulate`` tool (``/mcp`` never meets the role gate);
* the route-walking test: every route that can start a solve (everything the compute worker serves, plus any endpoint whose own
  source submits a job) is either an admin route while the setting is on, or named in the REVIEWED read list below.  A new route that
  starts work and is in neither FAILS this test - decide its tier (``solve:<old>`` in ``auth._GATED``) or review it into the list.

No solver runs; throwaway registry.
"""
from __future__ import annotations

import inspect
import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim import auth, roles
from motor_ai_sim import users as U

ENV = "MOTRES_SOLVE_ADMIN_ONLY"
OWNER, ADMIN2, ENG, USR, OEM = ("owner@example.com", "admin2@example.com", "eng@example.com", "usr@example.com", "oem@example.com")
SENTENCE = "Simulations are run by MOTRES for now."

#: (method, concrete path) -> the tier the route had BEFORE the rule (None = open).  The whole solve class.
SOLVE_ROUTES = {
    ("GET", "/api/simulation/physics/fem_transient"): "user",
    ("GET", "/api/simulation/physics/fem_field2d"): "user",
    ("GET", "/api/simulation/physics/harm_screening"): "user",
    ("GET", "/api/simulation/physics/bench_ldq"): None,
    ("GET", "/api/simulation/pwm_waveform"): None,
    ("POST", "/api/simulation/cogging/run"): "user",
    ("POST", "/api/simulation/run"): "admin",
    ("GET", "/api/mechanical/rotor_stress"): "user",
    ("GET", "/api/mechanical/modes"): "user",
    ("GET", "/api/mechanical/critical_speeds"): "user",
    ("POST", "/api/mechanical/limit_speed"): "user",
    ("POST", "/api/mechanical/holder_fem_job"): None,
    ("GET", "/api/thermal/field"): "user",
    ("GET", "/api/thermal/coupled"): "user",
    ("POST", "/api/thermal/duty_cycle"): "user",
    ("POST", "/api/coupled/run"): "user",
    ("POST", "/api/coupled/continuous_rating"): "user",
    ("POST", "/api/coupled/constants_20c"): "user",
    ("POST", "/api/controller/solve"): None,
    ("POST", "/api/kernel/run"): "user",
    ("POST", "/api/kernel/study"): "user",
    ("POST", "/api/system/run"): "engineer",
    ("POST", "/api/system/graphs/run"): "engineer",
    ("POST", "/api/nodes/jobs/submit"): None,
    ("POST", "/api/nodes/jobs/submit-shared"): "admin",
    ("POST", "/api/motor-tests/DIE/CFG/compute"): None,
    ("GET", "/api/static3d/mesh"): None,
    ("POST", "/api/static3d/solve"): "admin",
    # already admin through the prefix list (kept admin whatever the setting says)
    ("POST", "/api/optimization/scan"): "admin",
    ("POST", "/api/optimization/refine"): "admin",
    ("POST", "/api/optimization/doe/start"): "admin",
    ("POST", "/api/optimization/descent/start"): "admin",
    ("POST", "/api/optimization/auto"): "admin",
    ("POST", "/api/pipeline/generate"): "admin",
    ("POST", "/api/catalog/some_motor/passport"): "admin",
}

#: Reads and previews that must keep their tier whatever the setting says: what Configure, the project views, the read-only member
#: view, the catalog, Production and Tests read.  (method, path) -> tier.
READS = {
    ("GET", "/api/thermal/last"): "user", ("GET", "/api/mechanical/last"): "user", ("GET", "/api/coupled/last"): "user",
    ("GET", "/api/thermal/em_run"): "user", ("GET", "/api/thermal/duty_cycle/last"): "user",
    ("GET", "/api/simulation/physics/fem_transient/ledger_match"): "user",       # a lookup: it answers before any mesh or solve
    ("GET", "/api/simulation/physics/fem_transient/progress"): None, ("GET", "/api/mechanical/progress"): None,
    ("GET", "/api/thermal/progress"): None, ("GET", "/api/coupled/progress"): None,
    ("POST", "/api/simulation/physics/fem_transient/cancel"): "user", ("POST", "/api/coupled/cancel"): "user",
    ("GET", "/api/simulation/mesh/build2d"): "user", ("GET", "/api/thermal/mesh"): "user", ("GET", "/api/mechanical/mesh"): "user",
    ("GET", "/api/family/tree"): None, ("GET", "/api/family/context"): None,
    ("GET", "/api/motor-view/DIE/CFG"): None, ("GET", "/api/motor-view/DIE/CFG/DUTY/field/em"): None,
    ("GET", "/api/catalog/some_motor/passport"): None, ("GET", "/api/catalog/some_motor/configure_context"): None,
    ("GET", "/api/projects/p1/motors/sim"): None, ("GET", "/api/projects/p1/motors/sim/run"): None,
    ("GET", "/api/projects/p1/motors/view"): None, ("GET", "/api/projects/p1/motors/tests"): None,
    ("GET", "/api/production/DIE/CFG"): None, ("GET", "/api/motor-tests/DIE/CFG"): None,
    ("POST", "/api/motor-tests/DIE/CFG/tests"): None,                             # add a bench test record: data, not a solve
    ("GET", "/api/controller/point"): None, ("POST", "/api/controller/schematic"): None, ("GET", "/api/controller/last"): None,
    ("GET", "/api/mechanical/bearing_fit"): None, ("GET", "/api/mechanical/holder_strength"): None, ("GET", "/api/bearings/losses"): None,
    ("GET", "/api/propellers/p1/s1"): None,
    ("GET", "/api/jobs"): None, ("GET", "/api/system/graph"): "engineer",
}

#: REVIEWED (2026-11-04): worker-served routes that read stored data, preview, report progress, cancel, or write a store - none
#: starts a calculation.  Every other worker-served route must be an admin route while the setting is on.
REVIEWED_NOT_A_SOLVE = frozenset({
    "GET /api/catalog/{motor_id}/passport",
    "GET /api/config",
    "GET /api/controller/cooling_from_thermal",
    "GET /api/controller/devices",
    "GET /api/controller/devices/{part}",
    "GET /api/controller/devices/{part}/image",
    "GET /api/controller/history",
    "GET /api/controller/last",
    "GET /api/controller/point",
    "POST /api/controller/schematic",
    "GET /api/controller/settings",
    "GET /api/controller/thermal_cooling",
    "GET /api/controller/topologies",
    "POST /api/coupled/cancel",
    "GET /api/coupled/last",
    "GET /api/coupled/progress",
    "POST /api/family/config/{die}/{cfg}/move",
    "POST /api/family/config/{die}/{cfg}/rename",
    "DELETE /api/family/die/{die}",
    "PATCH /api/family/die/{die}",
    "GET /api/family/report/progress",
    "GET /api/family/report/{die}/{cfg}",
    "GET /api/geometry",
    "GET /api/geometry/constraints",
    "GET /api/geometry/dimension_sheet",
    "GET /api/geometry/mesh",
    "GET /api/geometry/mesh2d",
    "GET /api/geometry/mesh_extruded",
    "GET /api/geometry/optimizable",
    "GET /api/geometry/pointcloud",
    "GET /api/geometry/schema",
    "GET /api/geometry/summary",
    "GET /api/geometry/validation",
    "GET /api/history",
    "DELETE /api/history/{key}",
    "POST /api/history/{key}/load",
    "GET /api/jobs",
    "GET /api/jobs/{run_id}",
    "POST /api/jobs/{run_id}/cancel",
    "GET /api/kernel/capabilities",
    "GET /api/materials",
    "GET /api/mechanical/bearing_fit",
    "GET /api/mechanical/holder_strength",
    "GET /api/mechanical/last",
    "GET /api/mechanical/materials",
    "GET /api/mechanical/mesh",
    "GET /api/mechanical/progress",
    "GET /api/mesh/config",
    "GET /api/optimization/auto/status",
    "GET /api/optimization/descent/progress",
    "GET /api/optimization/doe/progress",
    "GET /api/optimization/refine/progress",
    "GET /api/optimization/saved",
    "GET /api/optimization/saved/{sid}",
    "GET /api/optimization/scan/progress",
    "GET /api/optimization/surrogate",
    "GET /api/optimization/variables",
    "GET /api/parts",
    "PATCH /api/parts",
    "GET /api/sims/saved",
    "GET /api/simulation/cogging/{die}/{cfg}",
    "PUT /api/simulation/cogging/{die}/{cfg}",
    "GET /api/simulation/config",
    "GET /api/simulation/mesh/build2d",
    "GET /api/simulation/mesh/build2d_sliding_band",
    "POST /api/simulation/physics/fem_transient/cancel",
    "GET /api/simulation/physics/fem_transient/ledger_match",
    "GET /api/simulation/physics/fem_transient/progress",
    "GET /api/simulation/physics/field_busy",
    "GET /api/simulation/result/{job_id}",
    "GET /api/simulation/status",
    "GET /api/static3d/field",
    "GET /api/static3d/geometry",
    "GET /api/static3d/machine",
    "GET /api/static3d/passport",
    "GET /api/static3d/solve/progress",
    "GET /api/static3d/solves",
    "GET /api/system/graph",
    "GET /api/system/graphs",
    "POST /api/system/graphs/validate",
    "DELETE /api/system/graphs/{name}",
    "GET /api/system/graphs/{name}",
    "PUT /api/system/graphs/{name}",
    "POST /api/system/validate",
    "GET /api/thermal/duty_cycle/last",
    "GET /api/thermal/em_run",
    "GET /api/thermal/heat_paths/last",
    "GET /api/thermal/last",
    "GET /api/thermal/mesh",
    "GET /api/thermal/progress",
    "GET /api/winding/config",
    "PATCH /api/winding/config",
})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    monkeypatch.delenv(ENV, raising=False)                                       # the DEFAULT: closed
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {OWNER})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    U.create_user(OWNER, "password-owner", role="admin", name="Owner")
    U.create_user(ADMIN2, "password-admin2", role="admin", name="Second")
    U.create_user(ENG, "password-engineer", role="engineer", name="Eve Engineer")
    U.create_user(USR, "password-user", role="user", name="Una User")
    U.create_user(OEM, "password-oem", role="oem", name="Olga Oem")
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    return dict(owner=h(OWNER), admin2=h(ADMIN2), eng=h(ENG), usr=h(USR), oem=h(OEM), anon={}, mp=monkeypatch)


@pytest.fixture()
def stub(env):
    """The real role gate in front of one stub handler per route: the gate's decision, with nothing solved."""
    app = FastAPI()
    auth.install_role_gate(app)
    for (method, path) in list(SOLVE_ROUTES) + list(READS):
        tmpl = path.replace("/DIE/CFG/DUTY/", "/{die}/{cfg}/{duty}/").replace("/DIE/CFG", "/{die}/{cfg}")
        tmpl = tmpl.replace("/some_motor/", "/{motor}/").replace("/p1/", "/{pid}/")
        app.add_api_route(tmpl, lambda: {"ran": True}, methods=[method])
    return TestClient(app)


def _call(c, method, path, headers):
    return c.request(method, path, headers=headers, json={} if method == "POST" else None)


# -- the setting -------------------------------------------------------------------------------------------------------------

def test_the_setting_is_closed_by_default_and_read_in_one_place(env):
    assert auth.solve_admin_only() is True
    for off in ("0", "false", "No", "OFF"):
        env["mp"].setenv(ENV, off)
        assert auth.solve_admin_only() is False, off
    for on in ("1", "true", "yes"):
        env["mp"].setenv(ENV, on)
        assert auth.solve_admin_only() is True, on
    assert SENTENCE == auth.SOLVE_REFUSAL


# -- the tier table -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("route", sorted(SOLVE_ROUTES))
def test_every_solve_route_is_admin_while_closed_and_back_to_its_old_tier_when_open(env, route):
    method, path = route
    assert auth.required_role(method, path) == "admin"
    env["mp"].setenv(ENV, "0")
    assert auth.required_role(method, path) == SOLVE_ROUTES[route], "setting off must be exactly today's tier"


@pytest.mark.parametrize("route", sorted(READS))
def test_reads_and_previews_keep_their_tier_in_both_states(env, route):
    method, path = route
    assert auth.required_role(method, path) == READS[route]
    env["mp"].setenv(ENV, "0")
    assert auth.required_role(method, path) == READS[route]


def test_no_solve_entry_names_a_route_that_does_not_exist():
    """A typo in a ``solve:`` entry would silently leave the real route open."""
    from motor_ai_sim.api import app
    live = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())}
    for (method, path), tier in auth._GATED.items():
        if tier.startswith("solve:"):
            assert (method, path) in live, f"{method} {path} is in the solve class but is not a route"
    for method, path in list(SOLVE_ROUTES) + list(READS):
        # the concrete example paths of this file match a real route template
        assert any(m == method and re.fullmatch(re.sub(r"\{[^/]+\}", "[^/]+", r.path), path) for r in app.routes
                   for m in (getattr(r, "methods", None) or ())), (method, path)


# -- the role gate -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("route", sorted(SOLVE_ROUTES))
def test_user_engineer_oem_are_refused_with_the_sentence_and_the_admin_passes(env, stub, route):
    method, path = route
    for who in ("usr", "eng", "oem"):                       # a project member is a user account: membership gives no tier
        r = _call(stub, method, path, env[who])
        assert r.status_code == 403, (who, route)
        b = r.json()
        assert b["detail"] == SENTENCE and b["code"] == "solve.admin_only" and b["required_role"] == "admin", (who, route)
        assert "Traceback" not in str(b)
    assert _call(stub, method, path, env["anon"]).status_code == 401, route       # anonymous: sign in first
    for who in ("owner", "admin2"):                         # ADMIN_EMAILS and a registry admin
        assert _call(stub, method, path, env[who]).json() == {"ran": True}, (who, route)


@pytest.mark.parametrize("route", sorted(SOLVE_ROUTES))
def test_setting_off_restores_today(env, stub, route):
    method, path = route
    env["mp"].setenv(ENV, "0")
    old = SOLVE_ROUTES[route]
    rank = auth._ROLE_RANK
    for who, role in (("usr", "user"), ("eng", "engineer"), ("oem", "oem"), ("anon", "anon"), ("admin2", "admin")):
        allowed = old is None or rank[role] >= rank[old]
        r = _call(stub, method, path, env[who])
        assert (r.status_code == 200) is allowed, (who, route, r.status_code)
        if not allowed:
            assert r.json().get("code") != "solve.admin_only"                    # today's own refusal, not the new one


@pytest.mark.parametrize("route", sorted(READS))
def test_read_routes_stay_open_for_the_readers_they_had(env, stub, route):
    method, path = route
    old = READS[route]
    rank = auth._ROLE_RANK
    for who, role in (("usr", "user"), ("eng", "engineer"), ("oem", "oem"), ("anon", "anon")):
        allowed = old is None or rank[role] >= rank[old]
        assert (_call(stub, method, path, env[who]).status_code == 200) is allowed, (who, route)


def test_api_me_carries_can_solve(env):
    from motor_ai_sim.api import app
    c = TestClient(app)
    assert c.get("/api/me", headers=env["usr"]).json()["canSolve"] is False
    assert c.get("/api/me", headers=env["eng"]).json()["canSolve"] is False
    assert c.get("/api/me", headers=env["owner"]).json()["canSolve"] is True
    assert c.get("/api/me", headers=env["admin2"]).json()["canSolve"] is True
    env["mp"].setenv(ENV, "0")
    assert c.get("/api/me", headers=env["usr"]).json()["canSolve"] is True


def test_a_real_read_route_answers_a_normal_user_and_a_real_solve_route_does_not(env):
    from motor_ai_sim.api import app
    c = TestClient(app)
    r = c.get("/api/family/tree", headers=env["usr"])
    assert r.status_code == 200
    r = c.get("/api/simulation/physics/fem_transient", headers=env["usr"])       # refused at the door: no solve starts
    assert r.status_code == 403 and r.json()["code"] == "solve.admin_only" and r.json()["detail"] == SENTENCE


# -- the second lock: the job queue's door --------------------------------------------------------------------------------------

def test_the_queue_refuses_a_compute_job_of_a_non_admin_and_never_touches_running_ones(env):
    from motor_ai_sim import jobs as J
    from motor_ai_sim import workspace as WSP

    @J.queued("test.solve_admin_only")
    def start():
        return {"ran": True}

    with WSP.use_caller({"id": USR, "role": "user", "is_admin": False}):
        with pytest.raises(HTTPException) as e:
            start()
    assert e.value.status_code == 403 and e.value.detail == SENTENCE
    with WSP.use_caller({"id": OWNER, "role": "admin", "is_admin": True}):
        assert start()["ran"] is True
    assert start()["ran"] is True                           # no caller (CLI, test, background thread): not this lock's business
    env["mp"].setenv(ENV, "0")
    with WSP.use_caller({"id": USR, "role": "user", "is_admin": False}):
        assert start()["ran"] is True
    # a call already INSIDE an admitted job runs on: queued and running work is never cancelled by the rule
    env["mp"].delenv(ENV)
    tok = J._IN_JOB.set("run-already-admitted")
    try:
        with WSP.use_caller({"id": USR, "role": "user", "is_admin": False}):
            assert start()["ran"] is True
    finally:
        J._IN_JOB.reset(tok)


def test_motor_tests_compute_checks_the_same_lock(env):
    from motor_ai_sim import workspace as WSP
    from motor_ai_sim.motor_tests import compute as MC
    with WSP.use_caller({"id": USR, "role": "user", "is_admin": False}):
        with pytest.raises(HTTPException) as e:
            MC.create_and_queue("DIE", "CFG", [], {})
    assert e.value.status_code == 403 and e.value.detail == SENTENCE


# -- MCP -------------------------------------------------------------------------------------------------------------------------

def test_mcp_simulate_is_refused_as_data_for_a_non_admin_and_passes_the_no_price_guard(env):
    from motor_ai_sim import agent_designs as AD
    from motor_ai_sim import mcp_tools as T
    from motor_ai_sim.agent_keys import Principal
    p = Principal(email=USR, credential_id="k1", kind="oauth", scopes=("simulate",), client_name="agent")
    out = AD.simulate(p, "d-0001", "em")
    assert out["status"] == "simulations_closed" and out["user_message"] == SENTENCE and out["design_id"] == "d-0001"
    assert T.no_commercial(out) is out                      # the MCP gives no prices; the refusal carries none
    for e in (ENG, OEM):
        assert AD.simulate(Principal(email=e, credential_id="k", scopes=("simulate",)), "d-1", "em")["status"] == "simulations_closed"
    # an admin gets past the refusal (here it stops at the next check: a bad ``what``); a non-admin never reaches that check
    for admin in (OWNER, ADMIN2):
        with pytest.raises(AD.DesignError, match="what must be one of"):
            AD.simulate(Principal(email=admin, credential_id="k2", scopes=("simulate",)), "d-0001", "bogus")
    env["mp"].setenv(ENV, "0")                              # off: the usual checks, no refusal
    with pytest.raises(AD.DesignError, match="what must be one of"):
        AD.simulate(p, "d-0001", "bogus")


# -- the route-walking test --------------------------------------------------------------------------------------------------------

_SUBMITS = re.compile(r"queue\(\)\s*\.\s*submit|\.queued\(|\badmit\(|\bCN\.submit\(|solve_pool|run_in_pool")


def test_every_route_that_can_start_a_solve_is_admin_only_or_reviewed(env):
    """The no-slip-through net.  Candidates: everything the compute worker serves (``roles.WORKER_PREFIXES`` + patterns - the
    deploy's own list of "routes that compute") and any endpoint whose own source submits a job.  Each must be an admin route while
    the setting is on, or be named in REVIEWED_NOT_A_SOLVE above."""
    from motor_ai_sim.api import app
    seen, offenders = set(), []
    for r in app.routes:
        methods = getattr(r, "methods", None)
        if not methods or not str(getattr(r, "path", "")).startswith("/"):
            continue
        ep = getattr(r, "endpoint", None)
        try:
            src = inspect.getsource(ep) if ep is not None else ""
        except (OSError, TypeError):
            src = ""
        candidate = roles.is_worker_path(r.path) or bool(_SUBMITS.search(src))
        for m in sorted(methods - {"HEAD", "OPTIONS"}):
            key = f"{m} {r.path}"
            seen.add(key)
            if candidate and auth.required_role(m, r.path) != "admin" and key not in REVIEWED_NOT_A_SOLVE:
                offenders.append(key)
    assert not offenders, ("these routes can start work and have no solve tier (add ``solve:<old tier>`` in auth._GATED, or review "
                           "them into REVIEWED_NOT_A_SOLVE): %s" % offenders)
    stale = sorted(REVIEWED_NOT_A_SOLVE - seen)
    assert not stale, f"REVIEWED_NOT_A_SOLVE names routes that no longer exist: {stale}"


def test_a_reviewed_route_is_never_a_solve_route(env):
    for key in REVIEWED_NOT_A_SOLVE:
        method, path = key.split(" ", 1)
        assert not auth.is_solve_route(method, path), key
