"""Open-project limit per account (owner 2026-10-30): default 2, an admin override (an integer or unlimited) that also works for an
address with no account yet.  Only synthetic e-mails here - the real override is set on the server by the orchestrator."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, users
from motor_ai_sim.development import project_limits as PL, self_service, store
from motor_ai_sim.development.routes import router as projects_router
from motor_ai_sim.routes.auth_local import router as auth_router

PAYING = "paying@customer.example"
PLAIN = "plain@customer.example"
NEW = "not-yet-registered@customer.example"
ENGINEER = "eng@example.test"
ADMIN = "admin@example.test"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(tmp_path / "legal"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    who = {"t-paying": {"email": PAYING, "role": "user"}, "t-plain": {"email": PLAIN, "role": "user"},
           "t-new": {"email": NEW, "role": "user"}, "t-eng": {"email": ENGINEER, "role": "engineer"}}
    monkeypatch.setattr(auth, "resolve_user", lambda h: dict(who[str(h or "").replace("Bearer ", "")]) if str(h or "").replace("Bearer ", "") in who else None)
    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(auth_router)
    app.dependency_overrides[auth.require_admin] = lambda: {"email": ADMIN, "role": "admin"}
    client = TestClient(app, raise_server_exceptions=False)

    def call(token, method, path, **kw):
        return client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)
    return call


def _make(env, token, n):
    return [env(token, "POST", "/api/projects", json={"title": f"Motor {i}"}).status_code for i in range(n)]


def test_default_is_two_and_the_message_names_the_accounts_own_limit(env):
    assert PL.DEFAULT_OPEN_LIMIT == 2 and PL.limit_for(PLAIN) == 2
    assert _make(env, "t-plain", 3) == [200, 200, 429]
    refused = env("t-plain", "POST", "/api/projects", json={"title": "x"}).json()["detail"]
    assert refused["limit"] == 2 and "up to 2 open projects" in refused["message"]


@pytest.mark.parametrize("value,allowed", [(0, 0), (1, 1), (4, 4), (4.0, 4), ("3", 3)])
def test_an_integer_override_sets_that_accounts_limit(env, value, allowed):
    PL.set_override(PLAIN, value)
    assert _make(env, "t-plain", allowed + 1) == [200] * allowed + [429]
    assert env("t-plain", "POST", "/api/projects", json={"title": "x"}).json()["detail"]["limit"] == allowed
    assert _make(env, "t-paying", 2) == [200, 200]                     # nobody else is affected


def test_unlimited_override(env):
    PL.set_override(PAYING, "unlimited")
    assert PL.limit_for(PAYING) is None and self_service.limit_of({"email": PAYING, "role": "user"}) is None
    assert _make(env, "t-paying", 6) == [200] * 6
    assert len(store.membership_projects(PAYING)) == 6


def test_override_for_an_address_with_no_account_applies_at_sign_up(env):
    assert users.get_user(NEW) is None                                    # no account yet
    PL.set_override("  Not-Yet-Registered@Customer.Example ", "unlimited")          # set before sign-up, any case
    assert PL.get_override(NEW) == "unlimited"
    assert _make(env, "t-new", 4) == [200] * 4                            # the first sign-in already has it


def test_clearing_goes_back_to_the_default_and_a_closed_project_frees_a_place(env):
    PL.set_override(PLAIN, 3)
    assert PL.set_override(PLAIN, None) is None and PL.get_override(PLAIN) is None
    assert _make(env, "t-plain", 3) == [200, 200, 429]
    first = store.membership_projects(PLAIN)[0]
    store.mutate_project(first, lambda d: d.update(status="closed"))
    assert _make(env, "t-plain", 1) == [200]


def test_staff_are_never_limited(env):
    PL.set_override(ENGINEER, 0)
    assert _make(env, "t-eng", 4) == [200] * 4


@pytest.mark.parametrize("bad", [-1, 1.5, True, "many", 10_000, [1], {"a": 1}])
def test_a_bad_value_is_refused(env, bad):
    r = env("x", "PUT", f"/api/auth/project-limits/{PLAIN}", json={"limit": bad})
    assert r.status_code == 422 and PL.get_override(PLAIN) is None


def test_admin_api_sets_lists_clears_and_audits(env, tmp_path):
    put = env("x", "PUT", f"/api/auth/project-limits/{NEW}", json={"limit": 7})
    assert put.status_code == 200 and put.json()["limit"] == 7 and put.json()["default"] == 2
    assert env("x", "PUT", f"/api/auth/project-limits/{PAYING}", json={"limit": "unlimited"}).json()["limit"] == "unlimited"
    listed = env("x", "GET", "/api/auth/project-limits").json()
    assert listed["default"] == 2
    assert {(r["email"], r["limit"]) for r in listed["limits"]} == {(NEW, 7), (PAYING, "unlimited")}
    assert all(r["account"] is False for r in listed["limits"])           # neither address has a registry record in this tmp store
    assert env("x", "PUT", f"/api/auth/project-limits/{NEW}", json={"limit": None}).json()["limit"] is None
    assert env("x", "PUT", f"/api/auth/project-limits/{PAYING}", json={"limit": ""}).json()["limit"] is None
    assert env("x", "GET", "/api/auth/project-limits").json()["limits"] == []
    assert (tmp_path / "admin_audit.jsonl").read_text(encoding="utf-8").count("user.project_limit") == 4


def test_users_list_carries_the_override_and_the_default(env):
    users.create_user(PAYING, "password-paying", role="user", name="Paying")
    users.create_user(PLAIN, "password-plain", role="user", name="Plain")
    PL.set_override(PAYING, "unlimited")
    body = env("x", "GET", "/api/auth/users").json()
    rows = {r["email"]: r for r in body["users"]}
    assert rows[PAYING]["project_limit"] == "unlimited" and rows[PLAIN]["project_limit"] is None
    assert body["project_limit_default"] == 2
    assert env("x", "GET", "/api/auth/project-limits").json()["limits"] == [{"email": PAYING, "limit": "unlimited", "account": True}]


def test_the_endpoints_need_an_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "_is_admin_caller", lambda authorization: (False, {"email": PLAIN, "role": "user"}) if authorization else (False, None))
    app = FastAPI()
    app.include_router(auth_router)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/auth/project-limits").status_code == 401
    assert client.get("/api/auth/project-limits", headers={"Authorization": "Bearer u"}).status_code == 403
    assert client.put(f"/api/auth/project-limits/{PLAIN}", json={"limit": "unlimited"}, headers={"Authorization": "Bearer u"}).status_code == 403
    assert client.put(f"/api/auth/project-limits/{PLAIN}", json={"limit": "unlimited"}).status_code == 401
    assert PL.get_override(PLAIN) is None


def test_account_purge_removes_only_that_override(env):
    from motor_ai_sim import account_lifecycle as AL
    PL.set_override(PLAIN, "unlimited")
    PL.set_override(PAYING, 5)
    report = AL.purge(PLAIN, actor="test")
    assert report["steps"]["project_limit"] == 1
    assert PL.get_override(PLAIN) is None and PL.get_override(PAYING) == 5
