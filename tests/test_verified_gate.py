"""What an account with an unconfirmed e-mail may not do (verified_gate.py, enforced in auth.RoleGateMiddleware).

* every gated route refuses an unconfirmed caller with 403 and the code ``email_unverified`` (whatever its role, with or without
  AUTH_ENFORCE), naming the address and the gate;
* every other route is untouched: an unconfirmed caller reaches it, and an anonymous or confirmed caller is never refused here;
* the table is not stale: every row names a route the real API has;
* an admin cannot give an unconfirmed account a higher role.

The middleware is tried on a tiny app whose routes just answer {"ran": true}: calling the real compute routes would run them.
Temp store only; the real registry stays untouched.
"""
from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import verified_gate as VG
from motor_ai_sim.api import app as real_app
from tests.test_email_password_auth import ADMIN, NEW, PW, _admin_h, _register, client, env  # noqa: F401

#: one concrete request per GATES row, in the same order as the table
SAMPLES = [
    ("POST", "/api/nda/sign", "nda"),
    ("GET", "/api/model3d/CIANO14 40 new/L12/model.step", "step"),
    ("GET", "/api/simulation/physics/fem_transient", "compute"),
    ("GET", "/api/simulation/mesh/build2d", "compute"),
    ("POST", "/api/simulation/run", "compute"),
    ("GET", "/api/mechanical/rotor_stress", "compute"),
    ("GET", "/api/thermal/coupled", "compute"),
    ("POST", "/api/coupled/run", "compute"),
    ("POST", "/api/kernel/run", "compute"),
    ("POST", "/api/optimization/run", "compute"),
    ("POST", "/api/static3d/solve", "compute"),
    ("POST", "/api/pipeline/generate", "compute"),
    ("POST", "/api/nodes/jobs/submit", "compute"),
    ("PUT", "/api/me/comm-prefs", "mailing"),
    ("POST", "/api/agent_keys", "tokens"),
    ("POST", "/api/oauth/requests/abc123", "tokens"),
    ("POST", "/api/nodes", "tokens"),
]

#: the things an unconfirmed account CAN do from the first minute (catalogue, Configure, 3-D view, assistant, tickets, reading
#: its own preferences, progress reads, the saved-optimization store)
OPEN = [
    ("GET", "/api/me"), ("GET", "/api/catalog/references"), ("GET", "/api/family/tree"), ("GET", "/api/geometry"),
    ("GET", "/api/model3d/models"), ("GET", "/api/model3d/CIANO14 40 new/L12/model.glb"), ("GET", "/api/nda/mine"),
    ("GET", "/api/nda/current"), ("GET", "/api/me/comm-prefs"), ("GET", "/api/support/tickets"), ("POST", "/api/support/tickets"),
    ("POST", "/api/support/chat"), ("GET", "/api/system/graph"), ("GET", "/api/propellers"), ("GET", "/api/sims/saved"),
    ("POST", "/api/optimization/saved"), ("GET", "/api/simulation/physics/fem_transient/progress"),
    ("GET", "/api/simulation/physics/fem_transient/ledger_match"), ("GET", "/api/agent_keys"), ("GET", "/api/nodes"),
    ("PUT", "/api/me/preferences"), ("GET", "/api/data-sharing/mine"), ("PUT", "/api/data-sharing/mine"),
]


def _tiny_app() -> FastAPI:
    a = FastAPI()
    from motor_ai_sim.auth import RoleGateMiddleware
    a.add_middleware(RoleGateMiddleware)
    seen = set()
    for m, p, *_ in SAMPLES + [(m, p) for m, p in OPEN]:
        if (m, p) in seen:
            continue
        seen.add((m, p))
        a.add_api_route(p, lambda: {"ran": True}, methods=[m])
    return a


@pytest.fixture()
def gate(env):
    from motor_ai_sim import auth as A
    env["U"].create_user("done@example.com", "password-done")                       # a confirmed account
    env["U"].create_user("eng@example.com", "password-eng", role="engineer")
    _register()                                                                      # NEW: unconfirmed, role user
    unv = {"Authorization": "Bearer " + env["U"].issue_token(NEW)}
    ok = {"Authorization": "Bearer " + env["U"].issue_token("done@example.com")}
    adm = {"Authorization": "Bearer " + env["U"].issue_token(ADMIN)}
    env["hdr"] = {"unv": unv, "ok": ok, "admin": adm}
    env["tiny"] = TestClient(_tiny_app())
    env["A"] = A
    return env


def test_table_has_a_sample_per_row_and_no_stale_route():
    assert len(SAMPLES) == len(VG.GATES)
    for (m, p, group), (methods, rx, g) in zip(SAMPLES, VG.GATES):
        assert g == group and rx.match(p) and (methods is None or m in methods), (m, p)
        assert VG.gate_for(m, p) == group
    # every row names something the real API serves (a regex of the real routes, path params as wildcards)
    real = []
    for r in real_app.routes:
        path, methods = getattr(r, "path", None), getattr(r, "methods", None)
        if path and methods:
            real.append((re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"), set(methods)))
    for m, p, group in SAMPLES:
        assert any(rx.match(p) and m in ms for rx, ms in real), f"no real route for {m} {p}"


def test_open_routes_are_not_gated():
    for m, p in OPEN:
        assert VG.gate_for(m, p) is None, (m, p)
    assert VG.gate_for("GET", "/api/optimization/saved") is None and VG.gate_for("POST", "/api/optimization/saved") is None


@pytest.mark.parametrize("enforce", [False, True])
def test_every_gated_route_refuses_an_unconfirmed_caller_with_a_clear_403(gate, enforce):
    env = gate
    env["mp"].setattr(env["A"], "AUTH_ENFORCE", enforce)
    for m, p, group in SAMPLES:
        r = env["tiny"].request(m, p, headers=env["hdr"]["unv"])
        assert r.status_code == 403, (m, p, r.text)
        j = r.json()
        assert j["code"] == "email_unverified" and j["params"] == {"email": NEW, "gate": group}, (m, p, j)
        assert NEW in j["detail"] and "Confirm your e-mail" in j["detail"]


def test_every_other_route_works_for_an_unconfirmed_caller(gate):
    env = gate
    for m, p in OPEN:
        r = env["tiny"].request(m, p, headers=env["hdr"]["unv"])
        assert r.status_code == 200 and r.json() == {"ran": True}, (m, p, r.text)


def test_confirmed_and_anonymous_callers_are_never_refused_by_the_gate(gate):
    env = gate
    for who in ("ok", "admin", None):
        h = env["hdr"][who] if who else {}
        for m, p, _g in SAMPLES:
            r = env["tiny"].request(m, p, headers=h)
            assert r.status_code == 200 and r.json() == {"ran": True}, (who, m, p, r.text)


def test_the_gate_comes_before_the_role_check(gate):
    env = gate
    env["mp"].setattr(env["A"], "AUTH_ENFORCE", True)
    r = TestClient(real_app).post("/api/simulation/run", headers=env["hdr"]["unv"], json={})      # admin-tier route, role user
    assert r.status_code == 403 and r.json()["code"] == "email_unverified"
    r = TestClient(real_app).post("/api/agent_keys", headers=env["hdr"]["unv"], json={"name": "k"})
    assert r.status_code == 403 and r.json()["code"] == "email_unverified"
    # a confirmed user meets the ordinary role message instead
    r = TestClient(real_app).post("/api/simulation/run", headers=env["hdr"]["ok"], json={})
    assert r.status_code == 403 and r.json().get("code") != "email_unverified"


def test_me_reports_the_unconfirmed_state(gate):
    env = gate
    assert client.get("/api/me", headers=env["hdr"]["unv"]).json()["emailVerified"] is False
    assert client.get("/api/me", headers=env["hdr"]["ok"]).json()["emailVerified"] is True
    assert client.get("/api/me").json()["emailVerified"] is True                      # anonymous: nothing to confirm


def test_an_admin_cannot_upgrade_an_unconfirmed_account(gate):
    env = gate
    h = _admin_h()
    for role in ("engineer", "admin"):
        r = client.patch(f"/api/auth/users/{NEW}", json={"role": role}, headers=h)
        assert r.status_code == 409 and r.json()["code"] == "email_unverified", r.text
    assert env["U"].get_user(NEW)["role"] == "user"
    assert client.patch(f"/api/auth/users/{NEW}", json={"role": "user", "name": "N"}, headers=h).status_code == 200   # not an upgrade
    assert client.patch("/api/auth/users/done@example.com", json={"role": "engineer"}, headers=h).status_code == 200  # confirmed: fine
