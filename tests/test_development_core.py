from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from motor_ai_sim import auth, users
from motor_ai_sim.development import access, models, store
from motor_ai_sim.development.admin_routes import router as admin_router
from motor_ai_sim.development.routes import router
from motor_ai_sim.development import motors


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    return tmp_path / "projects"


def make_project(project_id="alpha", members=None):
    return {"id": project_id, "title": "Alpha", "customer_org": "Org", "status": "spec",
            "created_at": "t", "updated_at": "t", "owner_engineer": "eng@example.test",
            "members": members or [{"email": "eng@example.test", "role": "owner"},
                                    {"email": "customer@example.test", "role": "customer"}],
            "linked_motors": [], "notes": "internal", "pending_invites": []}


def test_store_create_mutate_list_is_atomic_and_none_mutator(project_root):
    store.create_project(make_project())
    def edit(doc):
        doc["title"] = "Changed"
        return None
    result = store.mutate_project("alpha", edit)
    assert result["title"] == "Changed"
    assert [p["id"] for p in store.list_projects()] == ["alpha"]
    with pytest.raises(FileExistsError):
        store.create_project(make_project())
    with pytest.raises(ValueError):
        store.path_for("../outside")


def test_store_mutator_none_return_does_not_serialize_null(project_root):
    store.create_project(make_project())
    result = store.mutate_project("alpha", lambda d: d.update({"x": 1}))
    assert result["x"] == 1
    assert json.loads((project_root / "alpha" / "project.json").read_text(encoding="utf-8"))["id"] == "alpha"


def test_create_project_rolls_back_when_membership_index_publish_fails(project_root, monkeypatch):
    monkeypatch.setattr(store, "sync_membership_index", lambda *args: (_ for _ in ()).throw(OSError("index fail")))
    with pytest.raises(OSError, match="index fail"):
        store.create_project(make_project())
    assert store.get_project("alpha") is None


def test_membership_endpoint_is_index_only(project_root, monkeypatch):
    current = {"email": "customer@example.test", "role": "user"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project(make_project())
    monkeypatch.setattr(store, "list_projects", lambda: pytest.fail("membership endpoint scanned projects"))
    app = FastAPI(); app.include_router(router)
    response = TestClient(app).get("/api/projects/membership", headers={"Authorization": "Bearer dev"})
    assert response.status_code == 200
    assert response.json() == {"has_projects": True}


def test_member_role_change_keeps_other_customer_grant_and_marks_candidate(project_root, monkeypatch):
    current = {"email": "eng@example.test", "role": "engineer"}
    grants = {"a@example.test": {"all": False, "dies": [], "default": None, "configs": []},
              "b@example.test": {"all": False, "dies": [], "default": None, "configs": []}}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email": email, "email_verified": True} if email in grants else {"email": email})
    monkeypatch.setattr(motors.users, "get_user", users.get_user)
    monkeypatch.setattr(motors.users, "get_stored_motor_grants", lambda email: dict(grants[email]))
    def set_grants(email, *, all_motors, dies, default, configs):
        grants[email] = {"all": all_motors, "dies": dies, "default": default, "configs": configs}
    monkeypatch.setattr(motors.users, "set_motor_grants", set_grants)
    monkeypatch.setattr(motors, "_motor", lambda *args: (Path("."), Path("."), {"is_admin": True}, {}))
    project = make_project(members=[{"email": "eng@example.test", "role": "owner"},
                                    {"email": "a@example.test", "role": "customer"},
                                    {"email": "b@example.test", "role": "customer"}])
    store.create_project(project)
    motors.link_motor("alpha", "DIE", "A", Request({"type": "http", "headers": []}))
    app = FastAPI(); app.include_router(router)
    response = TestClient(app).post("/api/projects/alpha/members", headers={"Authorization": "Bearer dev"},
                                    json={"email": "a@example.test", "role": "engineer"})
    assert response.status_code == 200
    assert "DIE/A" in grants["a@example.test"]["configs"]
    assert "DIE/A" in grants["b@example.test"]["configs"]
    candidates = store.get_project("alpha")["grant_removal_candidates"]
    assert [x["email"] for x in candidates] == ["a@example.test"]


def test_member_removal_rolls_back_if_membership_index_write_fails(project_root, monkeypatch):
    current = {"email": "eng@example.test", "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project(make_project())
    app = FastAPI(); app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": "Bearer dev"}
    index_before = store.load_membership_index()
    monkeypatch.setattr(store, "sync_membership_index", lambda *args: (_ for _ in ()).throw(OSError("index fail")))
    response = client.delete("/api/projects/alpha/members?email=customer%40example.test", headers=headers)
    assert response.status_code == 500
    assert any(m["email"] == "customer@example.test" for m in store.get_project("alpha")["members"])
    assert store.load_membership_index() == index_before


def test_concurrent_revision_mutations_one_wins(project_root):
    store.create_project(make_project())
    store.save_json("alpha", "draft.json", {"revision": 0, "draft": {}})
    barrier = threading.Barrier(2)
    outcomes = []
    def writer(value):
        barrier.wait()
        result = {"won": False}
        def mut(doc):
            if doc.get("revision", 0) != 0:
                return
            doc["revision"] = 1
            doc["writer"] = value
            result["won"] = True
        store.mutate_json("alpha", "draft.json", mut)
        outcomes.append(result["won"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(writer, ("a", "b")))
    assert sorted(outcomes) == [False, True]
    assert store.load_json("alpha", "draft.json", {})["revision"] == 1


def test_schema_covers_checklist_mission_and_production():
    data = models.schema()
    ids = {f["checklist_id"] for section in data["sections"] for f in section["fields"] if f.get("checklist_id")}
    expected = {f"{section}.{item}" for section, items in {
        "1": range(1, 11), "2": range(1, 13), "3": range(1, 6), "4": range(1, 10),
        "5": range(1, 5), "6": range(1, 6)}.items() for item in items}
    assert expected <= ids
    assert {"mission_profile", "production"} <= {s["id"] for s in data["sections"]}
    assert all("en" in f["label"] and "zh-CN" in f["label"] for s in data["sections"] for f in s["fields"])
    mission = next(s for s in data["sections"] if s["id"] == "mission_profile")["fields"][0]
    assert {c["id"] for c in mission["columns"]} == {"phase", "torque_Nm", "speed_rpm", "duration_s", "battery_voltage_V"}


def _all_unknown_draft():
    fields = {}
    production = {}
    for section in models.schema()["sections"]:
        for field in section["fields"]:
            if field["id"] == "mission_profile":
                continue
            target = production if section["id"] == "production" else fields
            target[field["id"]] = {"value": None, "status": "not_known_yet"}
    return {"schema_version": 2, "fields": fields, "mission_profile": [], "mission_profile_status": "not_known_yet",
            "production": production}


def test_completeness_and_submit_validation_required_or_not_known():
    draft = models.normalize_draft(_all_unknown_draft())
    stats = models.completeness(draft)
    assert stats["complete"] is True
    assert stats["filled_required"] == stats["total_required"]
    assert stats["sections"]["5"]["required"] == 0
    assert stats["sections"]["5"]["complete"] is True
    assert models.validation_errors(draft) == []
    draft["fields"].pop("1_1")
    assert any(e["field"] == "1_1" for e in models.validation_errors(draft))
    assert any(e["section"] == "1" for e in models.validation_errors(draft))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "12"])
def test_typed_numeric_fields_reject_bad_values(bad):
    with pytest.raises(ValueError):
        models.normalize_draft({"fields": {"1_6": {"value": {"maximum_rpm": bad}, "status": "filled"}}})


def test_empty_and_unknown_payloads_do_not_bypass_validation():
    for payload in ({"fields": {"unknown": {"value": "x", "status": "filled"}}},
                    {"fields": {}, "unexpected": True},
                    {"mission_profile": [{"phase": "takeoff", "unexpected": 1}]}):
        with pytest.raises(ValueError):
            models.normalize_draft(payload)
    blank = models.normalize_draft({"fields": {"1_1": {"value": "   ", "status": "filled"}}})
    assert blank["fields"]["1_1"]["status"] == "missing"
    partial = models.normalize_draft({"fields": {"2_1": {"value": {"chemistry": "LiPo"}, "status": "filled"}}})
    assert partial["fields"]["2_1"]["value"]["chemistry"] == "LiPo"
    assert not models.completeness(partial)["sections"]["2"]["complete"]
    assert any(e["field"] == "2_1.series_cells" for e in models.validation_errors(partial))
    blank_mission = models.normalize_draft({"mission_profile": [{"phase": "", "torque_Nm": ""}]})
    assert blank_mission["mission_profile_status"] == "filled"
    assert any(e["field"].startswith("mission_profile[") for e in models.validation_errors(blank_mission))
    ranged = models.normalize_draft({"fields": {"1_4": {"status": "filled", "value": {"torque_Nm": {"minimum": -5, "maximum": 5}, "speed_rpm": {"minimum": 100, "maximum": 200}}}}})
    assert ranged["fields"]["1_4"]["value"]["torque_Nm"]["minimum"] == -5
    with pytest.raises(ValueError):
        models.normalize_draft({"fields": {"1_4": {"status": "filled", "value": {"torque_Nm": {"minimum": 5, "maximum": -5}}}}})
    with pytest.raises(ValueError):
        models.normalize_draft({"production": {"quantities": {"status": "filled", "value": {"series_per_year": -1}}}})
    with pytest.raises(ValueError):
        models.normalize_draft({"fields": {"1_9": {"status": "filled", "value": {"minimum": 1, "maximum": 2}}}})


def test_nested_schema_values_validate_recursively_and_empty_required_arrays_are_incomplete():
    with pytest.raises(ValueError, match="3_1.airflow"):
        models.normalize_draft({"fields": {"3_1": {"status": "filled", "value": {
            "cooling_type": "air", "airflow": "not an object"}}}})
    with pytest.raises(ValueError, match="finite number"):
        models.normalize_draft({"fields": {"3_1": {"status": "filled", "value": {
            "cooling_type": "air", "airflow": {"speed_mps": "fast"}}}}})
    empty_points = models.normalize_draft({"fields": {"2_2": {"status": "filled", "value": {
        "operating_point_voltages": []}}}})
    assert not models.completeness(empty_points)["sections"]["2"]["complete"]
    assert any(e["field"].startswith("2_2") for e in models.validation_errors(empty_points))


def test_access_matrix(project_root, monkeypatch):
    p = make_project()
    store.create_project(p)
    customer = {"email": "customer@example.test", "role": "user"}
    engineer = {"email": "eng@example.test", "role": "user"}
    admin = {"email": "admin@example.test", "role": "admin"}
    stranger = {"email": "stranger@example.test", "role": "user"}
    assert access.can_view(customer, p)
    assert access.can_view(engineer, p)
    assert access.can_view(admin, p)
    assert not access.can_view(stranger, p)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": False})
    assert not access.can_edit_spec(customer, p)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    assert access.can_edit_spec(customer, p)
    assert access.can_edit_spec(engineer, p)
    assert access.can_edit_spec(admin, p)


def test_admin_project_routes_require_admin_and_return_management_summary(project_root, monkeypatch):
    project = make_project()
    project["grant_removal_candidates"] = [
        {"email": "customer@example.test", "die": "SYNTHETIC-DIE", "config": "A", "reason": "motor_unlinked"}
    ]
    store.create_project(project)
    store.save_json("alpha", "draft.json", {"draft": {}, "revision": 0})
    store.append_audit("alpha", "admin@example.test", "project.update", "project", "alpha", {})
    app = FastAPI(); app.include_router(admin_router)
    client = TestClient(app)
    monkeypatch.setattr(auth, "_is_admin_caller", lambda token: (False, {"email": "eng@example.test", "role": "engineer"}))
    denied = [
        client.get("/api/admin/projects"),
        client.post("/api/admin/projects", json={"id": "beta", "title": "Beta"}),
        client.post("/api/admin/projects/alpha/members", json={"email": "new@example.test", "role": "customer"}),
        client.delete("/api/admin/projects/alpha/members?email=customer%40example.test"),
        client.post("/api/admin/projects/alpha/motors", json={"die": "SYNTHETIC-DIE", "config": "A"}),
        client.delete("/api/admin/projects/alpha/motors?die=SYNTHETIC-DIE&config=A"),
        client.get("/api/admin/projects/alpha/grant-removal-candidates"),
    ]
    assert [response.status_code for response in denied] == [403] * len(denied)

    monkeypatch.setattr(auth, "_is_admin_caller", lambda token: (True, {"email": "admin@example.test", "role": "admin"}))
    monkeypatch.setattr(auth, "resolve_user", lambda token: {"email": "admin@example.test", "role": "admin"})
    response = client.get("/api/admin/projects")
    assert response.status_code == 200
    item = response.json()["projects"][0]
    assert item["completeness"]["complete"] is False
    assert item["members"] == project["members"]
    assert item["motors"] == item["linked_motors"] == []
    assert item["grant_removal_candidates"] == project["grant_removal_candidates"]
    assert item["last_activity"]

    created = client.post("/api/admin/projects", json={
        "id": "beta", "title": "Beta", "customer_org": "Synthetic Labs"})
    assert created.status_code == 200
    assert store.get_project("beta")["id"] == "beta"
    assert store.load_json("beta", "draft.json", {})["revision"] == 0
    create_events = [json.loads(line) for line in
                     (project_root / "beta" / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert create_events[-1]["action"] == "create"
    assert client.post("/api/admin/projects", json={
        "id": "beta", "title": "Again", "customer_org": "Synthetic Labs"}).status_code == 409

    monkeypatch.setattr(users, "get_user", lambda email: {"email": email, "disabled": False})
    response = client.post("/api/admin/projects/alpha/members",
                           json={"email": "new@example.test", "role": "customer"})
    assert response.status_code == 200
    events = [json.loads(line) for line in (project_root / "alpha" / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1]["action"] == "invite"


def test_admin_project_mutations_reuse_grants_and_audit_paths(project_root, monkeypatch):
    project = make_project()
    store.create_project(project)
    grants = {
        "customer@example.test": {"all": False, "dies": [], "default": None, "configs": []},
        "new@example.test": {"all": False, "dies": [], "default": None, "configs": []},
    }
    monkeypatch.setattr(auth, "_is_admin_caller", lambda token: (
        True, {"email": "admin@example.test", "role": "admin"}))
    monkeypatch.setattr(auth, "resolve_user", lambda token: {
        "email": "admin@example.test", "role": "admin"})
    monkeypatch.setattr(users, "get_user", lambda email: {
        "email": email, "disabled": False, "email_verified": True})
    monkeypatch.setattr(users, "get_stored_motor_grants", lambda email: dict(grants[email]))
    def set_grants(email, *, all_motors, dies, default, configs):
        grants[email] = {"all": all_motors, "dies": dies,
                         "default": default, "configs": configs}
    monkeypatch.setattr(users, "set_motor_grants", set_grants)
    monkeypatch.setattr(motors, "_motor", lambda *args: (
        Path("."), Path("."), {"is_admin": True}, {"canonical_die": "SYNTHETIC-DIE"}))
    app = FastAPI(); app.include_router(admin_router)
    client = TestClient(app)

    linked = client.post("/api/admin/projects/alpha/motors",
                         json={"die": "SYNTHETIC-DIE", "config": "A"})
    assert linked.status_code == 200
    assert store.get_project("alpha")["linked_motors"] == [
        {"die": "SYNTHETIC-DIE", "config": "A", "status": "current"}]
    assert "SYNTHETIC-DIE/A" in grants["customer@example.test"]["configs"]

    added = client.post("/api/admin/projects/alpha/members",
                        json={"email": "new@example.test", "role": "customer"})
    assert added.status_code == 200
    assert "SYNTHETIC-DIE/A" in grants["new@example.test"]["configs"]
    removed = client.delete(
        "/api/admin/projects/alpha/members?email=new%40example.test")
    assert removed.status_code == 200
    assert "SYNTHETIC-DIE/A" in grants["new@example.test"]["configs"]

    unlinked = client.delete(
        "/api/admin/projects/alpha/motors?die=SYNTHETIC-DIE&config=A")
    assert unlinked.status_code == 200
    assert store.get_project("alpha")["linked_motors"] == []
    assert "SYNTHETIC-DIE/A" in grants["customer@example.test"]["configs"]
    candidates = client.get(
        "/api/admin/projects/alpha/grant-removal-candidates").json()["candidates"]
    assert {row["email"] for row in candidates} == {
        "customer@example.test", "new@example.test"}

    events = [json.loads(line) for line in
              (project_root / "alpha" / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["action"] for event in events] == [
        "motor.link", "invite", "remove", "motor.unlink"]


def test_admin_member_remove_rolls_back_on_membership_index_failure(project_root, monkeypatch):
    store.create_project(make_project())
    before_project = store.get_project("alpha")
    before_index = store.load_membership_index()
    monkeypatch.setattr(auth, "_is_admin_caller", lambda token: (
        True, {"email": "admin@example.test", "role": "admin"}))
    monkeypatch.setattr(auth, "resolve_user", lambda token: {
        "email": "admin@example.test", "role": "admin"})
    monkeypatch.setattr(store, "sync_membership_index", lambda *args: (
        _ for _ in ()).throw(OSError("index fail")))
    app = FastAPI(); app.include_router(admin_router)
    response = TestClient(app, raise_server_exceptions=False).delete(
        "/api/admin/projects/alpha/members?email=customer%40example.test")
    assert response.status_code == 500
    assert store.get_project("alpha") == before_project
    assert store.load_membership_index() == before_index
    audit = project_root / "alpha" / "audit.jsonl"
    assert not audit.exists()


def test_customer_routes_private_project_and_versioned_submit(project_root, monkeypatch):
    current = {"email": "eng@example.test", "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)
    app.dependency_overrides[auth.require_admin] = lambda: {"email": "admin@example.test", "role": "admin"}
    client = TestClient(app)
    created = client.post("/api/projects", headers={"Authorization": "Bearer dev"}, json={"id": "alpha", "title": "Alpha", "customer_org": "Org"})
    assert created.status_code == 200
    current.update(email="customer@example.test", role="user")
    invisible = client.get("/api/projects/alpha", headers={"Authorization": "Bearer dev"})
    assert invisible.status_code == 404
    project = store.get_project("alpha")
    project["members"].append({"email": "customer@example.test", "role": "customer"})
    store.save_json("alpha", "project.json", project)
    view = client.get("/api/projects/alpha", headers={"Authorization": "Bearer dev"})
    assert view.status_code == 200
    assert "notes" not in view.json()
    assert "pending_invites" not in view.json()
    draft = _all_unknown_draft()
    saved = client.put("/api/projects/alpha/spec", headers={"Authorization": "Bearer dev"}, json={"draft": draft, "revision": 0})
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    submitted = client.post("/api/projects/alpha/spec/submit", headers={"Authorization": "Bearer dev"}, json={"revision": 1})
    assert submitted.status_code == 200
    assert submitted.json()["version"] == 1
    assert (project_root / "alpha" / "spec" / "v1.json").exists()
    assert (project_root / "alpha" / "audit.jsonl").read_text(encoding="utf-8").count('"action": "submit"') == 1
    first_version = client.get("/api/projects/alpha/spec/versions/1", headers={"Authorization": "Bearer dev"})
    assert first_version.status_code == 200
    assert first_version.json()["draft"] == models.normalize_draft(draft)
    # A new draft and submit creates v2 while v1 remains immutable.
    resaved = client.put("/api/projects/alpha/spec", headers={"Authorization": "Bearer dev"}, json={"draft": draft, "revision": 2})
    assert resaved.status_code == 200
    second = client.post("/api/projects/alpha/spec/submit", headers={"Authorization": "Bearer dev"}, json={"revision": 3})
    assert second.status_code == 200 and second.json()["version"] == 2
    assert first_version.json()["draft"] == client.get("/api/projects/alpha/spec/versions/1", headers={"Authorization": "Bearer dev"}).json()["draft"]
    assert (project_root / "alpha" / "spec" / "v2.json").exists()
    duplicate = client.post("/api/projects/alpha/spec/submit", headers={"Authorization": "Bearer dev"}, json={"revision": 3})
    assert duplicate.status_code == 409


def test_membership_gate_uses_index_without_project_documents(project_root, monkeypatch):
    current = {"email": "customer@example.test", "role": "user"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project(make_project())
    monkeypatch.setattr(store, "list_projects", lambda: pytest.fail("membership gate scanned project docs"))
    app = FastAPI(); app.include_router(router)
    response = TestClient(app).get("/api/projects/membership", headers={"Authorization": "Bearer dev"})
    assert response.status_code == 200
    assert response.json() == {"has_projects": True}


def test_draft_autosave_conflict_returns_409(project_root, monkeypatch):
    current = {"email": "customer@example.test", "role": "user"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    store.create_project(make_project())
    store.save_json("alpha", "draft.json", {"draft": {"schema_version": 2, "fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {}}, "schema_version": 2, "revision": 0, "version": None})
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    draft = models.normalize_draft(_all_unknown_draft())
    okay = client.put("/api/projects/alpha/spec", headers={"Authorization": "Bearer dev"}, json={"draft": draft, "revision": 0})
    stale = client.put("/api/projects/alpha/spec", headers={"Authorization": "Bearer dev"}, json={"draft": draft, "revision": 0})
    assert okay.status_code == 200
    assert stale.status_code == 409


def test_partial_structured_row_autosaves_but_submit_requires_nested_cells(project_root, monkeypatch):
    current = {"email": "customer@example.test", "role": "user"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    store.create_project(make_project())
    store.save_json("alpha", "draft.json", {"draft": {"schema_version": 2}, "schema_version": 2, "revision": 0, "version": None})
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer dev"}
    draft = _all_unknown_draft()
    draft["fields"]["2_2"] = {"status": "filled", "value": {
        "operating_point_voltages": [{"operating_point": "", "voltage_V": None}]}}

    saved = client.put("/api/projects/alpha/spec", headers=headers,
                       json={"draft": draft, "revision": 0})
    assert saved.status_code == 200
    rejected = client.post("/api/projects/alpha/spec/submit", headers=headers,
                           json={"revision": 1})
    assert rejected.status_code == 422
    assert any(error["field"].endswith("operating_point")
               for error in rejected.json()["detail"]["errors"])

    draft["fields"]["2_2"]["value"]["operating_point_voltages"][0]["operating_point"] = "peak"
    draft["fields"]["2_2"]["value"]["operating_point_voltages"][0]["voltage_V"] = 48
    completed = client.put("/api/projects/alpha/spec", headers=headers,
                           json={"draft": draft, "revision": 1})
    assert completed.status_code == 200
    assert client.post("/api/projects/alpha/spec/submit", headers=headers,
                       json={"revision": 2}).status_code == 200


def test_project_access_denies_customer_metadata_and_unverified_spec_edits(project_root, monkeypatch):
    current = {"email": "customer@example.test", "role": "user"}
    verified = {"value": True}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": verified["value"]})
    store.create_project(make_project())
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer dev"}
    assert client.patch("/api/projects/alpha", headers=headers, json={"notes": "leak"}).status_code == 403
    assert client.patch("/api/projects/alpha", headers=headers, json={"status": "design"}).status_code == 403
    assert client.post("/api/projects/alpha/members", headers=headers, json={"email": "x@example.test"}).status_code == 403
    verified["value"] = False
    draft = _all_unknown_draft()
    assert client.put("/api/projects/alpha/spec", headers=headers, json={"draft": draft, "revision": 0}).status_code == 403
    current["email"] = "stranger@example.test"
    assert client.patch("/api/projects/alpha", headers=headers, json={"notes": "leak"}).status_code == 404
    assert client.get("/api/projects/alpha", headers=headers).status_code == 404


def test_pending_invite_requires_explicit_reinvite_after_account_creation(project_root, monkeypatch):
    current = {"email": "eng@example.test", "role": "engineer"}
    accounts = set()
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email": email, "email_verified": True} if email in accounts else None)
    store.create_project(make_project(members=[{"email": "eng@example.test", "role": "owner"}]))
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer dev"}
    pending = client.post("/api/projects/alpha/members", headers=headers,
                          json={"email": "new@example.test", "role": "customer"})
    assert pending.status_code == 200 and pending.json()["pending"] is True
    accounts.add("new@example.test")
    current.update(email="new@example.test", role="user")
    assert client.get("/api/projects/alpha", headers=headers).status_code == 404
    current.update(email="eng@example.test", role="engineer")
    accepted = client.post("/api/projects/alpha/members", headers=headers,
                           json={"email": "new@example.test", "role": "customer"})
    assert accepted.status_code == 200 and accepted.json()["pending"] is False
    current.update(email="new@example.test", role="user")
    assert client.get("/api/projects/alpha", headers=headers).status_code == 200
    assert not store.get_project("alpha")["pending_invites"]


def test_concurrent_submit_endpoint_has_one_winner(project_root, monkeypatch):
    current = {"email": "customer@example.test", "role": "user"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    store.create_project(make_project())
    store.save_json("alpha", "draft.json", {"draft": {"schema_version": 2, "fields": {}, "mission_profile": [],
                    "mission_profile_status": "missing", "production": {}}, "schema_version": 2, "revision": 0, "version": None})
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer dev"}
    saved = client.put("/api/projects/alpha/spec", headers=headers, json={"draft": _all_unknown_draft(), "revision": 0})
    assert saved.status_code == 200
    def submit(_):
        return client.post("/api/projects/alpha/spec/submit", headers=headers, json={"revision": 1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(submit, (1, 2)))
    assert sorted(codes) == [200, 409]
    assert len(list((project_root / "alpha" / "spec").glob("v*.json"))) == 1
    log = (project_root / "alpha" / "audit.jsonl").read_text(encoding="utf-8")
    assert log.count('"action": "submit"') == 1
    assert log.count('"action": "submit_attempt"') == 2


def test_version_listing_recovers_version_written_before_index(project_root, monkeypatch):
    current = {"email": "customer@example.test", "role": "user"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project(make_project())
    draft = models.normalize_draft(_all_unknown_draft())
    store.save_json("alpha", "draft.json", {"draft": draft, "revision": 4, "version": None})
    orphan = models.version_record(1, current["email"], draft, source_revision=4)
    store.save_json("alpha", "spec/v1.json", orphan)
    app = FastAPI(); app.include_router(router)
    response = TestClient(app).get("/api/projects/alpha/spec/versions", headers={"Authorization": "Bearer dev"})
    assert response.status_code == 200
    assert response.json()["versions"][0]["version"] == 1
    assert store.load_json("alpha", "spec/index.json", {})["versions"][0]["version"] == 1
    recovered = store.load_json("alpha", "draft.json", {})
    assert recovered["version"] == 1 and recovered["revision"] == 5



def test_customer_projection_hides_member_emails_from_metadata_thread_and_versions(monkeypatch):
    from motor_ai_sim.development.routes import _customer_spec, _summary
    monkeypatch.setattr(users, "get_user", lambda email: {"name": "Friendly Name"})
    project = {"id": "alpha", "title": "Alpha", "owner_engineer": "eng@example.test",
               "members": [{"email": "cust@example.test", "role": "customer"}],
               "linked_motors": [], "notes": "secret"}
    customer = {"email": "cust@example.test", "role": "user"}
    monkeypatch.setattr(store, "load_json", lambda *a, **k: {"draft": {}, "revision": 0})
    projection = _summary(project, customer)
    assert projection["owner_engineer"] == "Friendly Name"
    assert "eng@example.test" not in str(projection)
    answer = _customer_spec({"author_email": "eng@example.test", "draft": {"provenance": {
        "1_1": {"source": "C:/internal/customer.md", "sha256": "a" * 64}}}})
    assert "eng@example.test" not in str(answer)
    assert "C:/internal" not in str(answer) and answer["draft"]["provenance"] == {}
    assert answer["author_display_name"] == "Friendly Name"


def test_pending_invite_is_claimed_by_a_verified_sign_in(project_root, monkeypatch):
    from motor_ai_sim.development import routes as R
    store.create_project(make_project())
    accounts = {}
    monkeypatch.setattr(users, "get_user", lambda email: accounts.get(email))
    R._apply_member("alpha", "late@example.test", "owner")
    assert [m["email"] for m in store.get_project("alpha")["pending_invites"]] == ["late@example.test"]
    assert R.claim_pending_invites("late@example.test") == []          # still no account: stays pending
    accounts["late@example.test"] = {"email": "late@example.test"}
    assert R.claim_pending_invites("Late@Example.test") == ["alpha"]
    project = store.get_project("alpha")
    assert {"email": "late@example.test", "role": "owner"} in project["members"]
    assert project["pending_invites"] == []
