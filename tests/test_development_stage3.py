from __future__ import annotations

from motor_ai_sim.development import motors, store
from fastapi import HTTPException
from starlette.requests import Request
from fastapi import FastAPI
from fastapi.testclient import TestClient
import copy
import yaml
from pathlib import Path


def test_snapshot_store_is_versioned_and_keeps_machine_outside_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [], "linked_motors": []})
    snapshot = {"id": "snapshot-abcd", "version": 1, "created_at": "2026-10-23T00:00:00Z",
                "machine": {"die": {"geometry": {"num_slots": 12}},
                            "configuration": {"winding": {"turns": 2}}},
                "results": [{"duty": "rated", "comparison": {"torque_nm": 1.2}}]}
    store.save_snapshot("alpha", snapshot)
    assert store.list_snapshots("alpha") == [snapshot]
    assert (store.path_for("alpha") / "snapshots" / "snapshot-abcd.json").is_file()
    assert not store.load_index().get("motors")


def test_snapshot_id_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [], "linked_motors": []})
    try:
        store.save_snapshot("alpha", {"id": "../outside", "version": 1})
    except ValueError as exc:
        assert "invalid snapshot id" in str(exc)
    else:
        raise AssertionError("invalid snapshot id accepted")


def test_unlink_records_actor_and_retains_relink_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [],
                          "linked_motors": [{"die": "DIE", "config": "A"}]})
    monkeypatch.setattr(motors, "_capture_project_motor_snapshot", lambda *args: None)
    motors.unlink_motor("alpha", "DIE", "A", actor="admin@example.test")
    history = store.get_project("alpha")["motor_history"]
    assert history[-1]["unlinked_by"] == "admin@example.test"
    assert history[-1]["unlinked_at"]
    assert history[-1]["die"] == "DIE" and history[-1]["config"] == "A"


def test_picker_share_authority_matrix(monkeypatch, tmp_path):
    from motor_ai_sim.routes import family
    from motor_ai_sim import motor_access
    die_dir = tmp_path / "die"
    die_dir.mkdir()
    cfg = die_dir / "A.yaml"
    cfg.write_text("role: motor\r\n", encoding="utf-8")
    monkeypatch.setattr(family, "_require_die_access", lambda *args: None)
    monkeypatch.setattr(family, "_cfg_file", lambda *args: cfg)
    monkeypatch.setattr(motor_access, "catalog_access", lambda auth: {"is_admin": False})
    monkeypatch.setattr(motor_access, "config_visible", lambda *args, **kwargs: True)
    allowed = {"id": "owner@example.test", "is_admin": False}
    monkeypatch.setattr(motors, "_identity", lambda request: allowed)
    entry = {"layer": "published", "owner": "owner@example.test"}
    request = Request({"type": "http", "headers": []})
    motors._picker_authorized(request, "published-die", "A", entry)
    monkeypatch.setattr(motors, "_identity", lambda request: {"id": "admin@example.test", "is_admin": True})
    motors._picker_authorized(request, "published-die", "A", entry)
    for role in ("engineer", "customer", "stranger"):
        monkeypatch.setattr(motors, "_identity", lambda request, role=role: {"id": f"{role}@example.test", "is_admin": False})
        try:
            motors._picker_authorized(request, "published-die", "A", entry)
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError(f"{role} without published ownership was allowed to share")


def test_customer_snapshot_projection_hides_geometry_and_respects_config_grant(tmp_path, monkeypatch):
    from motor_ai_sim.development import routes
    from motor_ai_sim import motor_access
    from motor_ai_sim.routes import family
    from starlette.requests import Request
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [], "linked_motors": []})
    store.save_snapshot("alpha", {
        "id": "snapshot-customer", "version": 1, "created_at": "2026-10-23T00:00:00Z",
        "access_scope": {"kind": "catalog", "die": "DIE", "config": "A", "grant_key": "DIE/A"},
        "machine": {"die_name": "DIE", "config_name": "A", "die": {"geometry": {"secret": 1}},
                    "configuration": {"materials": {"secret": 1}}},
        "results": [{"duty": "rated", "comparison": {"torque_nm": 1.2}}],
    })
    catalog_die = tmp_path / "catalog-die"
    catalog_die.mkdir()
    (catalog_die / "A.yaml").write_text("role: motor\r\n", encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": "DIE", "dir": catalog_die, "layer": "shared"}])
    monkeypatch.setattr(family, "_die_dir", lambda die: catalog_die)
    monkeypatch.setattr(family, "_require_die_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes.access, "require_project", lambda *args, **kwargs: (
        {"email": "customer@example.test", "role": "customer"}, store.get_project("alpha"), "customer"))
    grants = {"configs": frozenset()}
    monkeypatch.setattr(motor_access, "catalog_access", lambda auth: grants)
    monkeypatch.setattr(motor_access, "config_visible", lambda *args, **kwargs: False)
    request = Request({"type": "http", "headers": []})
    response = routes.list_snapshots(request, "alpha")
    assert response["snapshots"] == []
    monkeypatch.setattr(motor_access, "config_visible", lambda *args, **kwargs: True)
    response = routes.list_snapshots(request, "alpha")
    assert response["snapshots"][0]["results"][0]["comparison"] == {"torque_nm": 1.2}
    assert "geometry" not in response["snapshots"][0]
    (catalog_die / "A.yaml").unlink()
    grants["configs"] = frozenset({"DIE/A"})
    response = routes.list_snapshots(request, "alpha")
    # A grant cannot replace the ordinary file visibility check after the
    # catalog source disappears; linked motors would no longer be readable.
    assert response["snapshots"] == []
    grants["configs"] = frozenset()
    response = routes.list_snapshots(request, "alpha")
    assert response["snapshots"] == []
    (catalog_die / "A.yaml").write_text("private: true\r\n", encoding="utf-8")
    grants["configs"] = frozenset({"DIE/A"})
    monkeypatch.setattr(motor_access, "config_visible", lambda *args, **kwargs: False)
    response = routes.list_snapshots(request, "alpha")
    assert response["snapshots"] == []



def test_project_only_snapshot_results_follow_project_membership(tmp_path, monkeypatch):
    from motor_ai_sim.development import routes
    from starlette.requests import Request
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    project = {"id": "alpha", "members": [{"email": "customer@example.test", "role": "customer"}],
               "linked_motors": []}
    store.create_project(project)
    store.save_snapshot("alpha", {"id": "snapshot-workspace", "version": 1,
                                   "created_at": "2026-10-23T00:00:00Z",
                                   "access_scope": {"kind": "project"},
                                   "machine": {"die": {"geometry": {"private": 1}},
                                               "configuration": {"materials": {"secret": 1}}},
                                   "results": [{"duty": "rated", "comparison": {"torque_nm": 3.0}}]})
    monkeypatch.setattr(routes.access, "require_project", lambda *args, **kwargs: (
        {"email": "customer@example.test", "role": "customer"}, project, "customer"))
    request = Request({"type": "http", "headers": []})
    row = routes.list_snapshots(request, "alpha")["snapshots"][0]
    assert row["results"][0]["comparison"] == {"torque_nm": 3.0}
    assert "machine" not in row
    assert "geometry" not in row


def test_snapshot_write_routes_are_absent_for_admin_and_have_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [], "linked_motors": []})
    before_project = store.get_project("alpha")
    before_audit = store._file("alpha", "audit.jsonl").read_bytes() if store._file("alpha", "audit.jsonl").exists() else b""
    routes = __import__("motor_ai_sim.development.routes", fromlist=["router"])
    monkeypatch.setattr(routes.access, "require_project", lambda *_a, **_kw: (
        {"email": "admin@example.test", "role": "admin"}, before_project, "owner"))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    machine = {"machine": {"die": {"geometry": {"stator_diameter": 30}},
                           "configuration": {"winding": {"turns": 2}}}}
    for method, path, payload in [
        ("post", "/api/projects/alpha/snapshots", machine),
        ("post", "/api/projects/alpha/snapshots/snapshot-x/load", {}),
        ("post", "/api/projects/alpha/snapshots/snapshot-x/save-as-motor", {"die": "D", "name": "C"}),
    ]:
        response = getattr(client, method)(path, json=payload)
        assert response.status_code in (404, 405)
    assert store.get_project("alpha") == before_project
    assert store.list_snapshots("alpha") == []
    assert (store._file("alpha", "audit.jsonl").read_bytes() if store._file("alpha", "audit.jsonl").exists() else b"") == before_audit


def test_published_entry_hidden_by_ordinary_workspace_resolution_is_not_actionable(tmp_path, monkeypatch):
    from motor_ai_sim.development import routes
    from starlette.requests import Request
    from motor_ai_sim.routes import family
    published = tmp_path / "published"
    workspace = tmp_path / "workspace"
    published.mkdir(); workspace.mkdir()
    (published / "die.yaml").write_text("geometry: {}\n", encoding="utf-8")
    (published / "A.yaml").write_text("role: motor\n", encoding="utf-8")
    entry = {"name": "DIE by Owner", "die": "DIE", "owner": "owner@example.test",
             "layer": "published", "dir": published}
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [entry])
    # The ordinary namespace resolves the visible label to a workspace shadow.
    monkeypatch.setattr(family, "_die_dir", lambda name: workspace)
    origin = routes._catalog_origin({"die_name": "DIE by Owner", "config_name": "A"})
    assert origin["kind"] == "catalog"
    assert routes._catalog_visible(Request({"type": "http", "headers": []}), origin) is False
    assert motors._picker_records(family, {"is_admin": True}) == []
    assert motors._ordinary_alias_for_canonical(
        family, motors._canonical_entry_name(entry)) is None


def test_picker_endpoint_role_matrix_and_workspace_shadow(tmp_path, monkeypatch):
    from motor_ai_sim.development import routes as project_routes
    from motor_ai_sim.routes import family
    from motor_ai_sim import motor_access
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    project = {"id": "alpha", "members": [{"email": "cust@example.test", "role": "owner"}],
               "linked_motors": [], "motor_history": []}
    store.create_project(project)
    entries = []
    for name, layer, owner, config in (
        ("SHADOW · by Owner", "published", "owner@example.test", "PUBLISHED-A"),
        ("SHADOW · by Owner", "workspace", "owner@example.test", "WORKSPACE-COPY"),
        ("OPEN", "shared", None, "SHARED-A"),
        ("PUBLISHED-OTHER", "published", "other@example.test", "OTHER-A"),
    ):
        directory = tmp_path / f"{layer}-{name}-{config}"
        directory.mkdir()
        (directory / "die.yaml").write_text(yaml.safe_dump({"geometry": {
            "stator_diameter": 30, "num_slots": 12, "num_poles": 14}}), encoding="utf-8")
        (directory / f"{config}.yaml").write_text(yaml.safe_dump({"geometry_overrides": {"motor_length": 10}}), encoding="utf-8")
        entries.append({"name": name, "die": "SHADOW" if name.startswith("SHADOW") else name,
                        "layer": layer, "owner": owner, "dir": directory})
    monkeypatch.setattr(family, "_iter_die_entries", lambda: entries)
    grants = {"configs": frozenset()}
    monkeypatch.setattr(motor_access, "catalog_access", lambda auth: grants)
    monkeypatch.setattr(motor_access, "may_see_config_file", lambda *args, **kwargs: True)
    monkeypatch.setattr(motor_access, "config_visible", lambda *args, **kwargs: True)
    monkeypatch.setattr(family, "_require_die_access", lambda *args: None)
    publish_dir = next(entry["dir"] for entry in entries if entry["layer"] == "published")
    monkeypatch.setattr(family, "_die_dir", lambda die: publish_dir if die.startswith("SHADOW") else next(e["dir"] for e in entries if e["name"] == die))
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: family._die_dir(die) / f"{config}.yaml")
    monkeypatch.setattr(motors.users, "get_user", lambda email: {"email": email, "name": "Owner"})
    identities = {
        "admin-token": {"id": "admin@example.test", "email": "admin@example.test", "is_admin": True},
        "owner-token": {"id": "owner@example.test", "email": "owner@example.test", "is_admin": False},
        "engineer-token": {"id": "engineer@example.test", "email": "engineer@example.test", "is_admin": False},
        "customer-token": {"id": "cust@example.test", "email": "cust@example.test", "is_admin": False},
        "stranger-token": {"id": "stranger@example.test", "email": "stranger@example.test", "is_admin": False},
    }
    monkeypatch.setattr(motors, "_identity", lambda request: identities[request.headers["authorization"].removeprefix("Bearer ")])
    def require_project(request, project_id, *, edit=False):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token == "stranger-token":
            raise HTTPException(404, "Project not found")
        if token == "customer-token":
            return {"email": "cust@example.test", "role": "customer"}, project, "owner"
        role = "admin" if token == "admin-token" else "engineer"
        return {"email": identities[token]["email"], "role": role}, project, "owner"
    monkeypatch.setattr(motors.access, "require_project", require_project)
    app = FastAPI(); app.include_router(motors.router)
    client = TestClient(app)
    def list_for(token):
        return client.get("/api/projects/alpha/motors/picker", headers={"authorization": f"Bearer {token}"})
    admin_rows = list_for("admin-token")
    assert admin_rows.status_code == 200
    admin_configs = {row["config"] for row in admin_rows.json()["catalog"]}
    assert {"PUBLISHED-A", "SHARED-A", "OTHER-A"} <= admin_configs
    assert "WORKSPACE-COPY" not in admin_configs
    owner_rows = list_for("owner-token")
    assert owner_rows.status_code == 200
    owner_configs = {row["config"] for row in owner_rows.json()["catalog"]}
    assert owner_configs == {"PUBLISHED-A"}
    assert all("diameter_mm" in row and "slots" in row and "poles" in row and "stack_mm" in row
               for row in owner_rows.json()["catalog"])
    engineer_rows = list_for("engineer-token")
    assert engineer_rows.status_code == 200 and engineer_rows.json()["catalog"] == []
    customer_rows = list_for("customer-token")
    assert customer_rows.status_code == 403
    stranger_rows = list_for("stranger-token")
    assert stranger_rows.status_code == 404
    app.include_router(project_routes.router)
    monkeypatch.setattr("motor_ai_sim.duty_results.get", lambda die, config: {})
    owner_link = client.post("/api/projects/alpha/motors", headers={"authorization": "Bearer owner-token"},
                             json={"die": "SHADOW · by Owner", "config": "PUBLISHED-A"})
    assert owner_link.status_code == 200, owner_link.text
    assert owner_link.json()["motor"]["die"] == "SHADOW · by Owner"
    assert len(store.list_snapshots("alpha")) == 1


def test_link_unlink_picker_conflict_and_restore_endpoints(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family
    from motor_ai_sim import motor_access, duty_results
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    project = {"id": "alpha", "members": [], "linked_motors": [], "motor_history": []}
    store.create_project(project)
    store.create_project({"id": "beta", "members": [], "linked_motors": []})
    die = tmp_path / "shared-die"
    die.mkdir()
    die_doc = {"geometry": {"stator_diameter": 30, "num_slots": 12, "num_poles": 14,
                             "tooth_tip": 1.2}}
    cfg_doc = {"role": "motor", "geometry_overrides": {"motor_length": 10}, "winding": {"turns": 2}, "duties": []}
    (die / "die.yaml").write_text(yaml.safe_dump(die_doc), encoding="utf-8")
    (die / "A.yaml").write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    entry = {"name": "DIE", "layer": "shared", "owner": None, "dir": die}
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [entry])
    monkeypatch.setattr(family, "_die_dir", lambda name: die)
    monkeypatch.setattr(family, "_cfg_file", lambda name, config: die / f"{config}.yaml")
    monkeypatch.setattr(family, "_require_die_access", lambda *args: None)
    monkeypatch.setattr(motor_access, "catalog_access", lambda auth: {"is_admin": True})
    monkeypatch.setattr(motor_access, "config_visible", lambda *args, **kwargs: True)
    monkeypatch.setattr(motor_access, "may_see_config_file", lambda *args, **kwargs: True)
    monkeypatch.setattr(duty_results, "get", lambda die_name, config: {})
    monkeypatch.setattr(motors, "_identity", lambda request: {"id": "admin@example.test", "email": "admin@example.test", "is_admin": True})
    from motor_ai_sim.development import routes as project_routes
    monkeypatch.setattr(motors.access, "require_project", lambda request, project_id, edit=False: (
        {"email": "admin@example.test", "role": "admin"}, store.get_project(project_id), None))
    app = FastAPI(); app.include_router(motors.router); app.include_router(project_routes.router)
    client = TestClient(app)
    headers = {"authorization": "Bearer admin-token"}
    linked = client.post("/api/projects/alpha/motors", json={"die": "DIE", "config": "A"}, headers=headers)
    assert linked.status_code == 200, linked.text
    assert len(store.list_snapshots("alpha")) == 1
    unlinked = client.delete("/api/projects/alpha/motors?die=DIE&config=A", headers=headers)
    assert unlinked.status_code == 200
    assert len(store.list_snapshots("alpha")) == 2
    picker = client.get("/api/projects/alpha/motors/picker", headers=headers)
    restored = next(row for row in picker.json()["this_project"] if row["config"] == "A")
    assert restored["unlinked_by"] == "admin@example.test"
    from motor_ai_sim.development.motors import _key
    store.save_index({"motors": {_key("DIE", "A"): {"project_id": "beta", "die": "DIE", "config": "A"}}})
    conflicted = client.get("/api/projects/alpha/motors/picker", headers=headers).json()["this_project"]
    conflict_row = next(row for row in conflicted if row["config"] == "A")
    assert conflict_row["disabled"] is True
    store.save_index({"motors": {}})
    relinked = client.post("/api/projects/alpha/motors", json={"die": "DIE", "config": "A"}, headers=headers)
    assert relinked.status_code == 200, relinked.text
    assert any(row["die"] == "DIE" and row["config"] == "A" for row in store.get_project("alpha")["linked_motors"])
    assert len(store.list_snapshots("alpha")) == 3


def test_picker_recovers_legacy_audit_history_when_links_are_empty(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [], "linked_motors": [], "motor_history": []})
    store.append_jsonl("alpha", "audit.jsonl", {"time": "2026-10-01T08:00:00Z",
        "actor_email": "engineer@example.test", "action": "motor.link", "entity": "motor",
        "entity_id": "LEGACY/A", "details": {}})
    store.append_jsonl("alpha", "audit.jsonl", {"time": "2026-10-02T09:30:00Z",
        "actor_email": "owner@example.test", "action": "motor.unlink", "entity": "motor",
        "entity_id": "LEGACY/A", "details": {}})
    directory = tmp_path / "published"
    directory.mkdir()
    (directory / "die.yaml").write_text(yaml.safe_dump({"geometry": {"stator_diameter": 30,
        "num_slots": 12, "num_poles": 14}}), encoding="utf-8")
    (directory / "A.yaml").write_text(yaml.safe_dump({"geometry_overrides": {"motor_length": 10}}), encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": "LEGACY", "layer": "shared", "dir": directory}])
    monkeypatch.setattr("motor_ai_sim.motor_access.catalog_access", lambda auth: {"configs": frozenset()})
    monkeypatch.setattr("motor_ai_sim.motor_access.may_see_config_file", lambda *args, **kwargs: True)
    monkeypatch.setattr(family, "_require_die_access", lambda *args: None)
    monkeypatch.setattr(family, "_die_dir", lambda die: directory)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: directory / f"{config}.yaml")
    monkeypatch.setattr(motors, "_identity", lambda request: {"id": "admin@example.test", "email": "admin@example.test", "is_admin": True})
    monkeypatch.setattr(motors.access, "require_project", lambda *args, **kwargs:
        ({"email": "admin@example.test", "role": "admin"}, store.get_project("alpha"), "owner"))
    app = FastAPI(); app.include_router(motors.router)
    response = TestClient(app).get("/api/projects/alpha/motors/picker", headers={"authorization": "Bearer admin"})
    assert response.status_code == 200, response.text
    legacy = next(row for row in response.json()["this_project"] if row["config"] == "A")
    assert legacy["linked_at"] == "2026-10-01T08:00:00Z"
    assert legacy["unlinked_at"] == "2026-10-02T09:30:00Z"
    assert legacy["unlinked_by"] == "owner@example.test"
