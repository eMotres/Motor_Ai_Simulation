"""Role "engineer" (owner 2026-10-14: "Configure only" or "the whole simulation" - two different things).

* roles are user / engineer / admin; a role change is admin-only, audited (from -> to), and never takes admin from the owner's
  account (ADMIN_EMAILS) or from the last admin;
* the CPU quota field (sim_quota_cpu_h_month) is validated and stored, nothing is blocked by it;
* the route matrix: an engineer gets every "user" route (the solves, through the job queue) and, with the per-user workspaces
  on, the run-ledger routes; every other admin route stays admin-only;
* an engineer with no grants has an empty catalogue.

Throwaway registry; no solver is run (allowed routes are checked in the gate table, denied ones are refused before any handler).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import users as U
from motor_ai_sim.api import app

OWNER = "owner@example.com"
ADMIN2 = "admin2@example.com"
ENG = "eng@example.com"
USR = "usr@example.com"
GRANTED = "granted@example.com"
DIE = "CIANO14 40 new"

client = TestClient(app)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {OWNER})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    U.create_user(OWNER, "password-owner", role="admin", name="Owner")
    U.create_user(ADMIN2, "password-admin2", role="admin", name="Second")
    U.create_user(ENG, "password-engineer", role="engineer", name="Eve Engineer")
    U.create_user(USR, "password-user", role="user", name="Una User")
    U.create_user(GRANTED, "password-granted", role="engineer", name="Gran Ted")
    U.set_motor_grants(GRANTED, all_motors=False, dies=[DIE])
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    return dict(owner=h(OWNER), admin2=h(ADMIN2), eng=h(ENG), usr=h(USR), granted=h(GRANTED), tmp=tmp_path)


def _audit(env):
    p = env["tmp"] / "admin_audit.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# -- role validation ----------------------------------------------------------

def test_roles_and_validation(env):
    assert U.ROLES == ("user", "engineer", "admin")
    with pytest.raises(ValueError):
        U.create_user("x@example.com", "password-x", role="superuser")
    with pytest.raises(ValueError):
        U.update_user(USR, role="staff")
    assert U.update_user(USR, role="engineer")["role"] == "engineer"
    assert U.get_user(USR)["role"] == "engineer"
    r = client.patch(f"/api/auth/users/{USR}", json={"role": "root"}, headers=env["owner"])
    assert r.status_code == 422


def test_engineer_identity_and_me(env):
    me = client.get("/api/me", headers=env["eng"]).json()
    assert me["role"] == "engineer" and me["isAdmin"] is False
    who = auth.caller_identity(env["eng"]["Authorization"])
    assert who["role"] == "engineer" and who["is_admin"] is False


# -- admin-only role change, audit, protections --------------------------------

def test_role_change_is_admin_only(env):
    for who in ("usr", "eng"):
        r = client.patch(f"/api/auth/users/{USR}", json={"role": "engineer"}, headers=env[who])
        assert r.status_code == 403
    assert U.get_user(USR)["role"] == "user"
    r = client.patch(f"/api/auth/users/{USR}", json={"role": "engineer"}, headers=env["admin2"])
    assert r.status_code == 200 and r.json()["user"]["role"] == "engineer"


def test_role_change_is_audited_from_to(env):
    client.patch(f"/api/auth/users/{USR}", json={"role": "engineer"}, headers=env["owner"])
    client.patch(f"/api/auth/users/{USR}", json={"name": "Una"}, headers=env["owner"])            # no role change
    client.patch(f"/api/auth/users/{USR}", json={"role": "engineer"}, headers=env["owner"])       # same role again
    rows = [r for r in _audit(env) if r["action"] == "user.role"]
    assert len(rows) == 1
    assert rows[0]["target"] == USR and rows[0]["details"] == {"from": "user", "to": "engineer"} and rows[0]["actor"] == OWNER


def test_owner_account_is_never_downgraded(env):
    for role in ("user", "engineer"):
        r = client.patch(f"/api/auth/users/{OWNER}", json={"role": role}, headers=env["admin2"])
        assert r.status_code == 409 and "owner" in r.json()["detail"]
    r = client.post(f"/api/admin/users/{OWNER}/role", json={"role": "user"}, headers=env["admin2"])
    assert r.status_code == 409
    assert U.get_user(OWNER)["role"] == "admin"
    assert not [x for x in _audit(env) if x["action"] == "user.role" and x["target"] == OWNER]


def test_last_admin_is_never_downgraded(env, monkeypatch):
    # two admins: ADMIN2 may be demoted while the owner stays
    assert client.patch(f"/api/auth/users/{ADMIN2}", json={"role": "engineer"}, headers=env["owner"]).status_code == 200
    # make ADMIN2 admin again, then take the owner out of the env list: ADMIN2 is the only admin left besides a disabled one
    client.patch(f"/api/auth/users/{ADMIN2}", json={"role": "admin"}, headers=env["owner"])
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", set())
    U.update_user(OWNER, disabled=True)
    r = client.patch(f"/api/auth/users/{ADMIN2}", json={"role": "user"}, headers=env["admin2"])
    assert r.status_code == 409 and "last admin" in r.json()["detail"]
    assert U.get_user(ADMIN2)["role"] == "admin"
    # a re-invite cannot downgrade the last admin either
    with pytest.raises(U.RoleProtected):
        U.invite_user(ADMIN2, role="user")


# -- quota (stored, never enforced) ---------------------------------------------

def test_quota_validation_and_storage(env):
    assert U.normalize_quota(None) is None and U.normalize_quota("") is None and U.normalize_quota("  ") is None
    assert U.normalize_quota("12.5") == 12.5 and U.normalize_quota(0) == 0.0
    for bad in (-1, "abc", True, float("nan"), 10 ** 9):
        with pytest.raises(ValueError):
            U.normalize_quota(bad)
    r = client.patch(f"/api/auth/users/{ENG}", json={"sim_quota_cpu_h_month": 40}, headers=env["owner"])
    assert r.status_code == 200 and r.json()["user"]["sim_quota_cpu_h_month"] == 40.0
    r = client.patch(f"/api/auth/users/{ENG}", json={"name": "Eve"}, headers=env["owner"])       # untouched when absent
    assert r.json()["user"]["sim_quota_cpu_h_month"] == 40.0
    r = client.patch(f"/api/auth/users/{ENG}", json={"sim_quota_cpu_h_month": None}, headers=env["owner"])
    assert r.json()["user"]["sim_quota_cpu_h_month"] is None
    assert client.patch(f"/api/auth/users/{ENG}", json={"sim_quota_cpu_h_month": -5}, headers=env["owner"]).status_code == 422


def test_user_list_shows_month_cpu_and_never_blocks(env, monkeypatch):
    from motor_ai_sim import usage_stats as US
    monkeypatch.setattr(US, "_jobs", lambda s, e: [dict(user=ENG.upper(), cpu_s=7200.0), dict(user=ENG, cpu_s=1800.0)])
    rows = {r["email"]: r for r in client.get("/api/auth/users", headers=env["owner"]).json()["users"]}
    assert rows[ENG]["cpu_h_month"] == 2.5 and rows[USR]["cpu_h_month"] == 0.0 and rows[ENG]["role"] == "engineer"
    assert "sim_quota_cpu_h_month" in rows[ENG]
    # an allowance far below the usage blocks nothing: the account is still served
    U.update_user(ENG, sim_quota_cpu_h_month=0.001)
    assert client.get("/api/me", headers=env["eng"]).status_code == 200
    monkeypatch.setattr(US, "_jobs", lambda s, e: (_ for _ in ()).throw(OSError("no store")))
    rows = {r["email"]: r for r in client.get("/api/auth/users", headers=env["owner"]).json()["users"]}
    assert rows[ENG]["cpu_h_month"] is None                                     # shown as "-" by the drawer


# -- the route matrix ---------------------------------------------------------

def _rank(role):
    return auth._ROLE_RANK[role]


def _allowed(role, method, path):
    need = auth.required_role(method, path)
    return need is None or _rank(role) >= _rank(need)


USER_TIER = [  # solves and their companions: the job queue and the panels' stores
    ("GET", "/api/simulation/physics/fem_transient"), ("GET", "/api/simulation/physics/fem_field2d"),
    ("GET", "/api/mechanical/rotor_stress"), ("GET", "/api/mechanical/modes"), ("GET", "/api/mechanical/critical_speeds"),
    ("GET", "/api/thermal/field"), ("GET", "/api/thermal/coupled"), ("POST", "/api/thermal/duty_cycle"),
    ("POST", "/api/coupled/run"), ("POST", "/api/kernel/run"), ("PUT", "/api/panel_settings/simulation"),
    ("GET", "/api/simulation/mesh/build2d"), ("POST", "/api/simulation/physics/fem_transient/cancel"),
]
LEDGER = [
    ("GET", "/api/simulation/ledger"), ("DELETE", "/api/simulation/ledger"), ("GET", "/api/simulation/ledger/recent"),
    ("POST", "/api/simulation/ledger/abc/load"), ("DELETE", "/api/simulation/ledger/abc"),
]
ADMIN_ONLY = [  # stay admin for an engineer, workspaces or not
    ("POST", "/api/optimization/run"), ("POST", "/api/optimization/scan"), ("POST", "/api/optimization/descent/start"),
    ("POST", "/api/pipeline/generate"), ("POST", "/api/static3d/solve"), ("POST", "/api/simulation/run"),
    ("GET", "/api/simulation/caches"), ("POST", "/api/simulation/caches/clear"),
    ("POST", "/api/catalog/cards/device"), ("POST", "/api/controller/devices"),
    ("POST", "/api/materials/global"), ("DELETE", "/api/materials/global/steel/X"),
    ("PUT", "/api/geometry"), ("POST", "/api/geometry/reset"), ("PATCH", "/api/materials"), ("PATCH", "/api/mesh/config"),
    ("PATCH", "/api/simulation/config"),
]


@pytest.mark.parametrize("method,path", USER_TIER)
def test_engineer_and_user_may_call_the_solves(method, path):
    assert _allowed("user", method, path) and _allowed("engineer", method, path)


@pytest.mark.parametrize("method,path", ADMIN_ONLY)
def test_admin_only_routes_stay_admin_for_an_engineer(method, path, monkeypatch):
    for layering in (False, True):
        monkeypatch.setattr(auth, "_layering_on", lambda v=layering: v)
        assert auth.required_role(method, path) == "admin"
        assert not _allowed("engineer", method, path) and not _allowed("user", method, path) and _allowed("admin", method, path)


@pytest.mark.parametrize("method,path", LEDGER)
def test_ledger_is_the_engineers_only_with_workspaces(method, path, monkeypatch):
    monkeypatch.setattr(auth, "_layering_on", lambda: False)
    assert auth.required_role(method, path) == "admin" and not _allowed("engineer", method, path)
    monkeypatch.setattr(auth, "_layering_on", lambda: True)
    assert auth.required_role(method, path) == "engineer"
    assert _allowed("engineer", method, path) and _allowed("admin", method, path) and not _allowed("user", method, path)


def test_engineer_is_refused_on_admin_routes_over_http(env):
    cases = [("POST", "/api/optimization/run"), ("POST", "/api/static3d/solve"), ("GET", "/api/simulation/caches"),
             ("POST", "/api/controller/devices"), ("GET", "/api/auth/users"), ("GET", "/api/admin/stats"),
             ("GET", "/api/model3d/downloads"), ("GET", "/api/nda/signatures"), ("GET", "/api/newsletter/admin/campaigns"),
             ("POST", "/api/auth/users")]
    for method, path in cases:
        r = client.request(method, path, json={}, headers=env["eng"])
        assert r.status_code == 403, (method, path, r.status_code)
        r2 = client.request(method, path, json={}, headers=env["usr"])
        assert r2.status_code == 403, (method, path, r2.status_code)
    # the owner is not refused by the gate (the handler may answer anything else)
    assert client.get("/api/auth/users", headers=env["owner"]).status_code == 200


def test_engineer_gate_message_names_the_role_needed(env):
    r = client.post("/api/optimization/run", json={}, headers=env["eng"])
    assert r.status_code == 403 and r.json()["required_role"] == "admin" and r.json()["your_role"] == "engineer"


def test_engineer_queues_like_a_user():
    from motor_ai_sim import jobs
    for role in ("user", "engineer"):
        assert jobs.priority_for("optimizer.scan", role) == jobs.Priority.CAMPAIGN
        assert jobs.priority_for("em.transient", role, jobs.Priority.INTERACTIVE) == jobs.Priority.DUTY
    assert jobs.priority_for("em.transient", "admin", jobs.Priority.INTERACTIVE) == jobs.Priority.INTERACTIVE


# -- own dies only ------------------------------------------------------------

def test_engineer_without_grants_sees_no_dies_and_with_grants_only_theirs(env):
    from motor_ai_sim import motor_access as MA
    a = MA.catalog_access(env["eng"]["Authorization"])
    assert a["mode"] == MA.MODE_GRANTED and a["dies"] == frozenset() and a["is_admin"] is False
    assert not MA.may_see_die(a, DIE)
    g = MA.catalog_access(env["granted"]["Authorization"])
    assert g["dies"] == frozenset({DIE}) and MA.may_see_die(g, DIE) and not MA.may_see_die(g, "Some other die")
