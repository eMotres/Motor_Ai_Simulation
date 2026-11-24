"""Project type Motor / Controller (coordinator 2026-11-23, owner 2026-11-22): staff choose it at creation, only an admin
changes it, a motor project carries no key (byte-identical record, payload and audit)."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, users
from motor_ai_sim.development import project_type as PT
from motor_ai_sim.development import store
from motor_ai_sim.development.routes import router

H = {"Authorization": "Bearer dev"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    current = {"email": "eng@example.test", "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    app = FastAPI()
    app.include_router(router)
    return {"client": TestClient(app), "who": current, "root": tmp_path / "projects"}


def _audit(env, pid):
    return [json.loads(line) for line in (env["root"] / pid / "audit.jsonl").read_text().splitlines()]


def test_a_motor_project_is_stored_and_answered_exactly_as_before(env):
    c = env["client"]
    for pid, extra in (("plain", {}), ("typed", {"project_type": "motor"})):
        r = c.post("/api/projects", headers=H, json={"id": pid, "title": "T", "customer_org": "Org", **extra})
        assert r.status_code == 200, r.text
        assert "project_type" not in r.json()
    a, b = store.get_project("plain"), store.get_project("typed")
    assert "project_type" not in a and "project_type" not in b
    strip = lambda d: {k: v for k, v in d.items() if k not in ("id", "created_at", "updated_at")}  # noqa: E731
    assert strip(a) == strip(b)
    assert [r["details"] for r in _audit(env, "typed") if r["action"] == "create"] == [{}]


def test_staff_create_a_controller_project(env):
    r = env["client"].post("/api/projects", headers=H, json={"id": "ctl", "title": "Board", "customer_org": "Org",
                                                               "project_type": "controller"})
    assert r.status_code == 200, r.text
    assert r.json()["project_type"] == "controller"
    assert store.get_project("ctl")["project_type"] == "controller"
    assert [r["details"] for r in _audit(env, "ctl") if r["action"] == "create"] == [{"project_type": "controller"}]


def test_a_wrong_type_is_refused(env):
    r = env["client"].post("/api/projects", headers=H, json={"id": "x", "title": "T", "customer_org": "O", "project_type": "pump"})
    assert r.status_code == 422
    assert store.get_project("x") is None


def test_a_customer_cannot_create_a_controller_project(env, monkeypatch):
    from motor_ai_sim.development import routes as R
    monkeypatch.setattr(R, "_require_data_agreement", lambda user: None)
    monkeypatch.setattr(R.platform_agreement, "require_accepted", lambda user: None)
    env["who"].update(email="customer@example.test", role="user")
    r = env["client"].post("/api/projects", headers=H, json={"title": "T", "customer_org": "O", "project_type": "controller"})
    assert r.status_code == 200, r.text
    assert "project_type" not in r.json()
    assert "project_type" not in store.get_project(r.json()["id"])


def test_only_an_admin_changes_the_type_and_back_to_motor_removes_the_key(env):
    c = env["client"]
    assert c.post("/api/projects", headers=H, json={"id": "p", "title": "T", "customer_org": "O"}).status_code == 200
    before = store.get_project("p")
    assert c.patch("/api/projects/p", headers=H, json={"project_type": "controller"}).status_code == 403   # an engineer
    assert store.get_project("p") == before
    env["who"].update(email="admin@example.test", role="admin")
    assert c.patch("/api/projects/p", headers=H, json={"project_type": "pump"}).status_code == 422
    r = c.patch("/api/projects/p", headers=H, json={"project_type": "controller"})
    assert r.status_code == 200, r.text
    assert r.json()["project_type"] == "controller"
    r = c.patch("/api/projects/p", headers=H, json={"project_type": "motor"})
    assert r.status_code == 200 and "project_type" not in r.json()
    after = store.get_project("p")
    assert {k: v for k, v in after.items() if k != "updated_at"} == {k: v for k, v in before.items() if k != "updated_at"}
    rows = [r["details"] for r in _audit(env, "p") if r["action"] == "update"]
    assert rows == [{"fields": ["project_type"], "project_type": {"from": "motor", "to": "controller"}},
                    {"fields": ["project_type"], "project_type": {"from": "controller", "to": "motor"}}]


def test_an_ordinary_patch_is_audited_as_before(env):
    c = env["client"]
    assert c.post("/api/projects", headers=H, json={"id": "p", "title": "T", "customer_org": "O"}).status_code == 200
    assert c.patch("/api/projects/p", headers=H, json={"title": "New"}).status_code == 200
    assert [r["details"] for r in _audit(env, "p") if r["action"] == "update"] == [{"fields": ["title"]}]


def test_the_type_helpers():
    assert PT.of({}) == "motor" and PT.of({"project_type": "controller"}) == "controller" and PT.of(None) == "motor"
    assert PT.parse(None) is None and PT.parse(" Controller ") == "controller"
    with pytest.raises(ValueError):
        PT.parse("pump")
    rec = {"project_type": "controller"}
    PT.apply(rec, "motor")
    assert rec == {}
