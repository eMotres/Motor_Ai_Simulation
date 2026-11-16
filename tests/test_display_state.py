"""Active / Parked / Archived (owner 2026-11-16): a presentation state for staff that changes NOTHING else.

The points: the default is read off the existing status (closed -> archived, paused -> parked, archived -> archived); an
explicit state wins and is reversible in one call; customers and the factory are refused; the project and production
records stay byte-identical; a parked object never wakes by itself.  Temporary stores, neutral ids, no solver."""
from __future__ import annotations

import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import display_state as DS
from motor_ai_sim.development import store
from motor_ai_sim.production import store as pstore
from motor_ai_sim.routes.display_state import router

USERS = {
    "admin": {"email": "admin@example.test", "role": "admin"},
    "staff": {"email": "staff@example.test", "role": "engineer"},
    "customer": {"email": "customer@example.test", "role": "user"},
    "oem": {"email": "oem@example.test", "role": "user"},
}


def _h(who):
    return {"Authorization": "Bearer " + who}


def _user(authorization):
    return USERS.get(str(authorization or "").split(" ", 1)[-1].strip())


def _production(die, config, status):
    path = pstore.path_for(die, config) / "production.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"die": die, "config": config, "status": status, "title": {"en": die},
                                "members": [{"email": "oem@example.test", "role": "oem"}]}), encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_OBJECT_REGISTRY_ROOT", str(tmp_path / "registry"))
    monkeypatch.setattr(auth, "resolve_user", lambda authorization, **_k: _user(authorization))
    base = {"customer_org": "Org", "created_at": "t", "updated_at": "t", "linked_motors": [], "notes": "",
            "members": [{"email": "customer@example.test", "role": "owner"}], "pending_invites": []}
    store.create_project({**base, "id": "alpha", "title": "Alpha", "status": "design"})
    store.create_project({**base, "id": "beta", "title": "Beta", "status": "closed"})
    files = {
        "alpha": store.path_for("alpha") / "project.json",
        "beta": store.path_for("beta") / "project.json",
        "p1": _production("DIE A", "L12", "active"),
        "p2": _production("DIE A", "L20", "paused"),
        "p3": _production("DIE B", "L40", "archived"),
    }
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), files, tmp_path


def _snapshot(files):
    return {k: p.read_bytes() for k, p in files.items()}


def test_defaults_map_the_existing_statuses(env):
    client, _files, _tmp = env
    body = client.get("/api/display-state", headers=_h("admin")).json()
    assert body["projects"]["alpha"]["state"] == "active"
    assert body["projects"]["beta"]["state"] == "archived"
    assert body["productions"]["DIE A/L12"]["state"] == "active"
    assert body["productions"]["DIE A/L20"]["state"] == "parked"
    assert body["productions"]["DIE B/L40"]["state"] == "archived"
    assert not any(v["explicit"] for v in [*body["projects"].values(), *body["productions"].values()])


def test_park_and_resume_in_one_call_each_records_are_untouched(env):
    client, files, tmp = env
    before = _snapshot(files)
    r = client.put("/api/display-state", headers=_h("staff"),
                   json={"kind": "project", "project_id": "alpha", "state": "parked", "reason": "waiting  for the customer"})
    assert r.status_code == 200
    assert r.json()["state"] == "parked" and r.json()["by"] == "staff@example.test"
    assert r.json()["reason"] == "waiting for the customer"
    assert client.get("/api/display-state", headers=_h("admin")).json()["projects"]["alpha"]["state"] == "parked"
    r = client.put("/api/display-state", headers=_h("admin"), json={"kind": "project", "project_id": "alpha", "state": "active"})
    assert r.json()["state"] == "active" and r.json()["explicit"]
    # a closed project can be shown as active (and an archived production as parked) without touching its status
    client.put("/api/display-state", headers=_h("admin"), json={"kind": "project", "project_id": "beta", "state": "active"})
    client.put("/api/display-state", headers=_h("admin"),
               json={"kind": "production", "die": "DIE B", "config": "L40", "state": "parked"})
    body = client.get("/api/display-state", headers=_h("admin")).json()
    assert body["projects"]["beta"]["state"] == "active"
    assert body["productions"]["DIE B/L40"]["state"] == "parked"
    assert _snapshot(files) == before                       # byte-identical project.json / production.json
    hist = json.loads((tmp / "registry" / "display_state.json").read_text())["history"]
    assert [h["to"] for h in hist] == ["parked", "active", "active", "parked"]


def test_clear_returns_to_the_default(env):
    client, _files, _tmp = env
    client.put("/api/display-state", headers=_h("admin"),
               json={"kind": "production", "die": "DIE A", "config": "L20", "state": "active"})
    r = client.put("/api/display-state", headers=_h("admin"),
                   json={"kind": "production", "die": "DIE A", "config": "L20", "state": None})
    assert r.json()["state"] == "parked" and not r.json()["explicit"]


@pytest.mark.parametrize("who", ["customer", "oem"])
def test_customers_and_factory_are_refused(env, who):
    client, files, _tmp = env
    before = _snapshot(files)
    assert client.get("/api/display-state", headers=_h(who)).status_code == 403
    r = client.put("/api/display-state", headers=_h(who), json={"kind": "project", "project_id": "alpha", "state": "archived"})
    assert r.status_code == 403
    assert client.get("/api/display-state").status_code == 401
    assert _snapshot(files) == before


def test_unknown_objects_and_states_are_refused(env):
    client, _files, tmp = env
    assert client.put("/api/display-state", headers=_h("admin"),
                      json={"kind": "project", "project_id": "nope", "state": "parked"}).status_code == 404
    assert client.put("/api/display-state", headers=_h("admin"),
                      json={"kind": "production", "die": "DIE A", "config": "nope", "state": "parked"}).status_code == 404
    assert client.put("/api/display-state", headers=_h("admin"),
                      json={"kind": "project", "project_id": "alpha", "state": "hidden"}).status_code == 422
    assert not (tmp / "registry" / "display_state.json").exists()


def test_a_parked_object_never_wakes_by_itself(env):
    """Owner correction 2026-11-16: activity inside a parked object changes nothing; only staff set it back."""
    client, _files, _tmp = env
    client.put("/api/display-state", headers=_h("admin"), json={"kind": "project", "project_id": "alpha", "state": "parked"})
    store.mutate_project("alpha", lambda p: p.update({"updated_at": "2099-01-01T00:00:00Z", "status": "simulation"}))
    assert client.get("/api/display-state", headers=_h("admin")).json()["projects"]["alpha"]["state"] == "parked"


def test_parallel_changes_are_not_lost(env):
    _client, _files, _tmp = env
    keys = [DS.object_key("project", project_id=f"p{i}") for i in range(12)]
    threads = [threading.Thread(target=DS.set_state, args=(k, "parked"), kwargs={"by": "t"}) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(DS.load()["states"]) == set(keys)
