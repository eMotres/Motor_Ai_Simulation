"""A transient WITHOUT ``mat=`` solves the machine's CURRENT material assignment.

Production report, 2026-11-13: ``PATCH /api/materials {part: magnet, material: F52SH_150C}`` answered 200, ``GET
/api/materials`` and the workspace file both said F52SH_150C, and the next ``GET /api/simulation/physics/fem_transient``
"without mat" solved (and stamped ``magnet_card``) the PREVIOUS magnet, N52UH_150C.

What is pinned here, through the real HTTP stack (the router's ``mat=`` dependency, the job-queue decorator, the
route body) with a stub in place of the FEM solve - no physics runs:

* after a PATCH, a solve with no ``mat=`` sees the new magnet, both inside the solve (what ``build_materials`` reads)
  and in the summary stamp;
* a ``mat=`` request still wins over the config, and it does not leak into the NEXT request that sends none;
* the per-request cache key (``_config_physics_fingerprint``) moves when the PATCH moves the assignment;
* ``mat={}`` is "no override" - the way a script running INSIDE the web app (whose fetch interceptor appends the UI's
  own ``mat=`` to every physics request that has none, ``web/src/lib/apiAuth.ts``) asks for the live assignment;
* in the 202 mode (``QUEUE_ASYNC=1``) a parked job runs in a copy of its SUBMITTER's context: it sees that request's
  ``mat=`` and nothing it sets survives into the next job on the same worker thread.

Finding, 2026-11-13: the blocking-mode server path was already right - the same sequence passes here in the default
role AND in the production posture (``MOTRES_ROLE=worker``, per-user workspace, auth enforced, owner's bearer).  What
matches the production observation is the browser: a ``fetch()`` run inside the signed-in app goes through the
interceptor, which appends ``mat=`` built from the UI's in-memory assignment (``MaterialOverrideSync``, with the active
duty's materials laid over it), and a raw ``PATCH /api/materials`` or ``POST /api/family/activate`` issued that way
updates neither.  The 202-mode leak pinned last is real but latent (production runs the queue in blocking mode).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


# Two library cards that resolve in the sandbox (the production report named N52UH_150C / F52SH_150C).
OLD, NEW = "N52UH", "F52SH"

# A pinned d-axis (no calibration solve), a tiny frame count, no eddy - the stub answers anyway.
Q = {"n_steps_per_period": 4, "n_periods": 1.0, "gamma_deg": 0.0, "I_phase_rms": 0.1, "rpm": 280,
     "daxis_deg": 0.0, "eddy": "false", "rotor_eddy": "false", "fresh": "true", "ledger": "false"}


@pytest.fixture
def sim():
    from motor_ai_sim.routes import simulation as s
    return s


@pytest.fixture
def client():
    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture
def restore_config():
    """The PATCH writes the sandbox config (tests/conftest.py); put its text back afterwards."""
    from motor_ai_sim.config import config_path, clear_config_cache
    p = config_path()
    before = p.read_text(encoding="utf-8")
    yield p
    p.write_text(before, encoding="utf-8")
    clear_config_cache()


@pytest.fixture
def solver(monkeypatch, sim, tmp_path):
    """Stub solve: records the magnet the solve WOULD resolve (config assignment + request override - exactly the
    two inputs ``fem_solver_2d.build_materials`` merges) and the per-request cache fingerprint."""
    from motor_ai_sim.config import get_material_assignments
    from motor_ai_sim.material_context import get_request_materials
    sim.clear_simulation_caches(reason="live-material test setup")
    seen = []

    def fake_eval(**kw):
        asg = dict(get_material_assignments() or {})
        ov = get_request_materials() or {}
        asg.update({k: v for k, v in (ov.get("assignment") or {}).items() if v})
        seen.append({"magnet": asg.get("magnet"),
                     "stamp": sim._magnet_temperature_stamp(None).get("magnet_card"),
                     "fp": sim._config_physics_fingerprint(with_request_materials=True)})
        return {"time_s": [0.0, 0.5, 1.0], "T_avg_Nm": 1.0, "T_em_Nm": [1.0, 1.0, 1.0], "rpm": 280.0,
                "f_elec_Hz": 10.0, "P_loss_total_W": [1.0, 1.0, 1.0], "n_frames_solved": 3}

    monkeypatch.setattr("motor_ai_sim.simulation.fem_solver_2d.em_transient_eval", fake_eval)
    monkeypatch.setattr(sim, "_bench_read", lambda *a, **k: {"stubbed": True})
    monkeypatch.setattr(sim, "_bench_compute", lambda *a, **k: None)
    monkeypatch.setattr(sim, "_append_run_journal", lambda *a, **k: None)
    monkeypatch.setattr(sim, "_ledger_dir", lambda: tmp_path / ".run_ledger")
    yield seen
    sim.clear_simulation_caches(reason="live-material test teardown")


def _patch_magnet(client, name):
    r = client.patch("/api/materials", json={"part": "magnet", "material": name})
    assert r.status_code == 200, r.text
    assert r.json()["assignments"]["magnet"] == name
    assert client.get("/api/materials").json()["magnet"] == name


def _solve(client, **extra):
    r = client.get("/api/simulation/physics/fem_transient", params={**Q, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _stamp(res, seen):
    """The summary's ``magnet_card`` when the route built a summary around the stub, else the stamp the stub took
    in the solve's own context (the same ``_magnet_temperature_stamp``)."""
    s = res.get("summary")
    if isinstance(s, dict) and s.get("magnet_card"):
        return s["magnet_card"]
    return seen[-1]["stamp"]


def test_a_solve_without_mat_follows_the_patched_assignment(client, solver, restore_config):
    _patch_magnet(client, OLD)
    first = _solve(client)
    assert solver[-1]["magnet"] == OLD
    assert _stamp(first, solver) == OLD

    _patch_magnet(client, NEW)
    second = _solve(client)
    assert solver[-1]["magnet"] == NEW, "the solve used the magnet assigned BEFORE the PATCH"
    assert _stamp(second, solver) == NEW, "the summary stamps the magnet assigned BEFORE the PATCH"
    assert solver[-1]["fp"] != solver[0]["fp"], "the cache key did not move with the assignment"


def test_a_mat_override_wins_and_does_not_leak_into_the_next_request(client, solver, restore_config):
    _patch_magnet(client, NEW)
    ov = json.dumps({"assignment": {"magnet": OLD}, "materials": {}})
    with_ov = _solve(client, mat=ov)
    assert solver[-1]["magnet"] == OLD
    assert _stamp(with_ov, solver) == OLD

    plain = _solve(client)
    assert solver[-1]["magnet"] == NEW, "the previous request's mat= override leaked into this one"
    assert _stamp(plain, solver) == NEW


def test_an_empty_mat_is_the_live_assignment(client, solver, restore_config):
    _patch_magnet(client, NEW)
    out = _solve(client, mat="{}")
    assert solver[-1]["magnet"] == NEW
    assert _stamp(out, solver) == NEW


def test_a_parked_job_runs_in_its_submitters_context_and_leaks_nothing(monkeypatch, tmp_path):
    """202 mode: the work runs on a long-lived ``job-worker`` thread.  It must see the material override of the request
    that submitted it, and an override the work sets itself (``get_fem_transient`` sets ``mat=`` in its own body) must
    not be what the NEXT job on that thread reads when its request sent none."""
    import threading
    import time
    from motor_ai_sim import jobs as J
    from motor_ai_sim import workspace as WS
    from motor_ai_sim.material_context import get_request_materials, set_request_materials

    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    monkeypatch.setenv(J.ENV_ASYNC, "1")
    monkeypatch.setattr(J, "_note_duration", lambda *a, **k: None)   # no duration stats written for a stub
    J.reset_queue(J.InProcessQueue(workers=1, field_limit=1))
    seen = {}
    done = threading.Event()

    def first():
        seen["first"] = get_request_materials()
        set_request_materials({"assignment": {"magnet": OLD}})      # what the route body does with its own mat=
        return {}

    def second():
        seen["second"] = get_request_materials()
        done.set()
        return {}

    def submit(work, override):
        ws = WS.workspace_for_identity("a@x.com")
        set_request_materials(override)                            # what the router dependency does
        try:
            with WS.use_workspace(ws), WS.use_caller({"id": "a@x.com"}):
                with pytest.raises(J.JobAccepted):
                    J.run_job("transient", work, priority=J.Priority.INTERACTIVE)
        finally:
            set_request_materials(None)

    try:
        submit(first, {"assignment": {"magnet": NEW}})
        deadline = time.time() + 5
        while "first" not in seen and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        submit(second, None)
        assert done.wait(5.0), "the second parked job never ran"
    finally:
        J.reset_queue(J.InProcessQueue())
    assert seen["second"] is None, (
        "the previous job's material override leaked into the next job on the same worker thread")
    assert (seen["first"] or {}).get("assignment") == {"magnet": NEW}, (
        "a parked job did not see its own request's mat= override")


# ── the production posture: per-user workspace, auth enforced, the compute-worker role ──────────────────────────────

ADMIN = "owner@example.com"


@pytest.fixture
def prod_posture(tmp_path, monkeypatch):
    """Deploy stage 2 as far as one process can be: ``MOTRES_ROLE=worker`` (the process nginx sends both
    ``PATCH /api/materials`` and ``/api/simulation/*`` to), ``WORKSPACES_ROOT`` / ``SHARED_ROOT`` set, ``AUTH_ENFORCE``
    on, and the owner signed in with a bearer token - so the config the solve reads is the owner's
    ``<workspaces>/<id>/motor_config.yaml``, as on the server."""
    import shutil
    from pathlib import Path
    from motor_ai_sim import auth
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache

    src_dir = Path(str(DEFAULT_CONFIG_PATH)).parent
    shared, works = tmp_path / "shared", tmp_path / "workspaces"
    for d in (shared, works):
        d.mkdir(parents=True)
    for name in ("motor_config.yaml", "materials_library.yaml", "part_materials.yaml"):
        if (src_dir / name).exists():
            shutil.copy2(src_dir / name, shared / name)
    monkeypatch.setenv("MOTRES_ROLE", "worker")
    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    users_file = tmp_path / "users.json"
    users_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    U.create_user(ADMIN, "password-admin", role="admin", name="Owner")
    ws = W.provision(ADMIN)
    assert ws is not None and (ws.root / "motor_config.yaml").exists()
    clear_config_cache()
    yield {"headers": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"}, "ws": ws}
    clear_config_cache()


def test_the_worker_solves_the_owners_patched_magnet(client, solver, prod_posture):
    h = prod_posture["headers"]
    cfg = prod_posture["ws"].root / "motor_config.yaml"

    def patch(name):
        r = client.patch("/api/materials", json={"part": "magnet", "material": name}, headers=h)
        assert r.status_code == 200, r.text
        assert client.get("/api/materials", headers=h).json()["magnet"] == name
        assert f"magnet: {name}\n" in cfg.read_text(encoding="utf-8"), "the PATCH did not write the owner's workspace"

    def solve(**extra):
        r = client.get("/api/simulation/physics/fem_transient", params={**Q, **extra}, headers=h)
        assert r.status_code == 200, r.text
        return r.json()

    patch(OLD)
    solve()
    assert solver[-1]["magnet"] == OLD
    patch(NEW)
    out = solve()
    assert solver[-1]["magnet"] == NEW, "the worker solved the magnet assigned BEFORE the PATCH"
    assert _stamp(out, solver) == NEW
    solve(mat=json.dumps({"assignment": {"magnet": OLD}, "materials": {}}))
    assert solver[-1]["magnet"] == OLD
    out = solve()
    assert solver[-1]["magnet"] == NEW, "a mat= override leaked into the next request"
    assert _stamp(out, solver) == NEW
