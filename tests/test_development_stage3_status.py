from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from motor_ai_sim.development import access, motors, store


def _request():
    return Request({"type": "http", "headers": []})


def _setup(tmp_path, monkeypatch, linked=None):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    project = {"id": "alpha", "members": [], "linked_motors": linked or []}
    store.create_project(project)
    store.save_index({"motors": {"die/a": {"project_id": "alpha", "die": "die", "config": "A"}}})
    return project


def _authorize(monkeypatch, user, project=None, role=None):
    project = project or {"id": "alpha", "members": [], "linked_motors": []}
    monkeypatch.setattr(access, "require_project", lambda *args, **kwargs: (user, project, role))


def test_motor_status_authorization_requires_global_engineer_and_editable_project(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [{"die": "die", "config": "A"}])
    body = motors.MotorStatusUpdate(die="die", config="A", status="trial")
    for user, role in (({"email": "eng@example.test", "role": "engineer"}, None),
                       ({"email": "admin@example.test", "role": "admin"}, None)):
        _authorize(monkeypatch, user, role=role)
        assert motors.update_project_motor_status("alpha", body, _request())["status"] == "trial"
        store.mutate_project("alpha", lambda p: p["linked_motors"][0].update(status="current"))
    # Project-owner permission does not turn a customer into a global engineer.
    _authorize(monkeypatch, {"email": "owner@example.test", "role": "customer"}, role="owner")
    with pytest.raises(HTTPException) as exc:
        motors.update_project_motor_status("alpha", body, _request())
    assert exc.value.status_code == 403
    assert store.get_project("alpha")["linked_motors"][0].get("status") == "current"


def test_motor_status_validation_missing_and_legacy_default_are_non_mutating(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [{"die": "die", "config": "A"}])
    _authorize(monkeypatch, {"email": "eng@example.test", "role": "engineer"})
    before = store.get_project("alpha")
    with pytest.raises(ValidationError):
        motors.MotorStatusUpdate(die="die", config="A", status="archived")
    assert store.get_project("alpha") == before
    with pytest.raises(HTTPException) as exc:
        motors.update_project_motor_status("alpha", motors.MotorStatusUpdate(die="missing", config="A", status="trial"), _request())
    assert exc.value.status_code == 404
    assert store.get_project("alpha") == before
    response = motors.update_project_motor_status("alpha", motors.MotorStatusUpdate(die="die", config="A", status="current"), _request())
    assert response["status"] == "current"
    assert "status" not in store.get_project("alpha")["linked_motors"][0]
    assert not (store.path_for("alpha") / "audit.jsonl").exists()


def test_motor_status_alias_update_persists_and_only_audits_real_change(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family
    _setup(tmp_path, monkeypatch, [{"die": "Canonical", "config": "A"}])
    die_dir = tmp_path / "catalog"
    die_dir.mkdir()
    cfg = die_dir / "A.yaml"
    cfg.write_text("role: motor\r\n", encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": "Alias", "die": "Canonical", "dir": die_dir, "layer": "shared"}])
    monkeypatch.setattr(motors, "_canonical_entry_name", lambda entry: "Canonical")
    monkeypatch.setattr(family, "_die_dir", lambda die: die_dir)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: cfg)
    _authorize(monkeypatch, {"email": "eng@example.test", "role": "engineer"})
    store.mutate_project("alpha", lambda project: project.update(project_motor_grants={"die\u0000a": {"grant_key": "Canonical/A", "emails": ["customer@example.test"]}}))
    grants_before = store.get_project("alpha")["project_motor_grants"]
    index_before = store.load_index()
    response = motors.update_project_motor_status("alpha", motors.MotorStatusUpdate(die="Alias", config="A", status="trial"), _request())
    assert response == {"die": "Canonical", "config": "A", "status": "trial"}
    persisted = store.get_project("alpha")
    assert persisted["linked_motors"] == [{"die": "Canonical", "config": "A", "status": "trial"}]
    assert store.load_index() == index_before
    audit = [json.loads(line) for line in (store.path_for("alpha") / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit[-1]["action"] == "motor.status"
    assert audit[-1]["details"] == {"old": "current", "new": "trial"}
    assert len(audit) == 1
    motors.update_project_motor_status("alpha", motors.MotorStatusUpdate(die="Canonical", config="A", status="trial"), _request())
    assert len((store.path_for("alpha") / "audit.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert store.get_project("alpha")["project_motor_grants"] == grants_before


def test_get_motors_hides_trials_by_default_and_includes_status_for_customers(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family
    _setup(tmp_path, monkeypatch, [
        {"die": "die", "config": "A"},
        {"die": "die", "config": "B", "status": "trial"},
    ])
    store.save_index({"motors": {
        "die\u0000a": {"project_id": "alpha"},
        "die\u0000b": {"project_id": "alpha"},
    }})
    die_dir = tmp_path / "catalog"
    die_dir.mkdir()
    for name in ("A", "B"):
        (die_dir / f"{name}.yaml").write_text("duties: []\r\n", encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": "die", "dir": die_dir, "layer": "shared"}])
    monkeypatch.setattr(family, "_die_dir", lambda die: die_dir)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: die_dir / f"{config}.yaml")
    monkeypatch.setattr(family, "_require_die_access", lambda *args: None)
    monkeypatch.setattr(motors, "_stored_results_index", lambda *args: {})
    monkeypatch.setattr(access, "require_project", lambda *args, **kwargs: (
        {"email": "customer@example.test", "role": "customer"}, store.get_project("alpha"), "customer"))
    default = motors.list_project_motors("alpha", _request())
    all_rows = motors.list_project_motors("alpha", _request(), show_all=True)
    assert [(row["config"], row["status"]) for row in default["motors"]] == [("A", "current")]
    assert [(row["config"], row["status"]) for row in all_rows["motors"]] == [("A", "current"), ("B", "trial")]
    assert store.get_project("alpha")["linked_motors"][0].get("status") is None

def test_stranger_cannot_change_motor_status(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [{"die": "die", "config": "A"}])
    def denied(*args, **kwargs):
        raise HTTPException(404, detail="Project not found")
    monkeypatch.setattr(access, "require_project", denied)
    with pytest.raises(HTTPException) as exc:
        motors.update_project_motor_status("alpha", motors.MotorStatusUpdate(die="die", config="A", status="trial"), _request())
    assert exc.value.status_code == 404
    assert store.get_project("alpha")["linked_motors"] == [{"die": "die", "config": "A"}]


def test_link_defaults_new_and_restored_links_to_current_and_is_idempotent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, [{"die": "die", "config": "A", "status": "trial"}])
    monkeypatch.setattr(motors, "_motor", lambda request, die, config: (Path("."), Path("."), {}, {"canonical_die": "die"}))
    monkeypatch.setattr(motors, "_sync_grants", lambda *args: None)
    monkeypatch.setattr(motors, "_capture_link_snapshot", lambda *args: None)
    request = _request()
    # Repeating an existing link keeps its chosen status.
    motors.link_motor("alpha", "die", "A", request)
    assert store.get_project("alpha")["linked_motors"][0]["status"] == "trial"
    motors.unlink_motor("alpha", "die", "A")
    # A restored link is a fresh current version, retaining unlink history.
    motors.link_motor("alpha", "die", "A", request)
    project = store.get_project("alpha")
    assert project["linked_motors"] == [{"die": "die", "config": "A", "status": "current"}]
    assert project["motor_history"][-1]["linked_at"]


def test_http_status_role_matrix_invalid_value_and_show_all_query(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import family

    project = _setup(tmp_path, monkeypatch, [
        {"die": "die", "config": "A"},
        {"die": "die", "config": "B", "status": "trial"},
    ])
    project["members"] = [
        {"email": "customer@example.test", "role": "customer"},
        {"email": "owner@example.test", "role": "owner"},
    ]
    store.mutate_project("alpha", lambda doc: doc.update(members=project["members"]))
    store.save_index({"motors": {
        "die\u0000a": {"project_id": "alpha"},
        "die\u0000b": {"project_id": "alpha"},
    }})
    identities = {
        "admin": {"email": "admin@example.test", "role": "admin"},
        "engineer": {"email": "engineer@example.test", "role": "engineer"},
        "customer": {"email": "customer@example.test", "role": "customer"},
        "owner": {"email": "owner@example.test", "role": "customer"},
        "stranger": {"email": "stranger@example.test", "role": "customer"},
    }
    monkeypatch.setattr(access, "require_signed_in", lambda request: identities[request.headers.get("x-user")])
    die_dir = tmp_path / "catalog"
    die_dir.mkdir()
    for name in ("A", "B"):
        (die_dir / f"{name}.yaml").write_text("duties: []\r\n", encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": "die", "dir": die_dir, "layer": "shared"}])
    monkeypatch.setattr(family, "_die_dir", lambda die: die_dir)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: die_dir / f"{config}.yaml")
    monkeypatch.setattr(family, "_require_die_access", lambda *args: None)
    monkeypatch.setattr(motors, "_stored_results_index", lambda *args: {})
    app = FastAPI()
    app.include_router(motors.router)
    client = TestClient(app)

    for who, expected in (("admin", 200), ("engineer", 200), ("customer", 403),
                          ("owner", 403), ("stranger", 404)):
        response = client.patch("/api/projects/alpha/motors/status", headers={"x-user": who},
                                json={"die": "die", "config": "A", "status": "trial"})
        assert response.status_code == expected, (who, response.text)
        if expected == 200:
            store.mutate_project("alpha", lambda doc: doc["linked_motors"][0].update(status="current"))
    invalid = client.patch("/api/projects/alpha/motors/status", headers={"x-user": "engineer"},
                           json={"die": "die", "config": "A", "status": "archived"})
    assert invalid.status_code == 422
    assert store.get_project("alpha")["linked_motors"][0]["status"] == "current"
    visible = client.get("/api/projects/alpha/motors", headers={"x-user": "customer"})
    complete = client.get("/api/projects/alpha/motors?show_all=true", headers={"x-user": "customer"})
    assert [item["config"] for item in visible.json()["motors"]] == ["A"]
    assert [(item["config"], item["status"]) for item in complete.json()["motors"]] == [
        ("A", "current"), ("B", "trial")]
