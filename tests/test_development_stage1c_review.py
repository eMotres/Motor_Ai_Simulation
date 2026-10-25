from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, motor_access, users
from motor_ai_sim.development import store
from motor_ai_sim.development.motors import router as motors_router
from motor_ai_sim.development.routes import router as projects_router
from motor_ai_sim.routes import family
from motor_ai_sim.routes.family import router as family_router


SECOND = "second@example.test"
GRANT = "MixedCaseDie/CfgA"
ADMIN = "admin@example.test"
CUSTOMER = "customer@example.test"


def _project(members=None):
    return {"id": "alpha", "title": "alpha", "customer_org": "Customer",
            "status": "spec", "created_at": "t", "updated_at": "t",
            "owner_engineer": ADMIN,
            "members": members or [{"email": ADMIN, "role": "owner"},
                                    {"email": CUSTOMER, "role": "customer"}],
            "linked_motors": [], "notes": "internal", "pending_invites": []}


@pytest.fixture
def stage1b(tmp_path, monkeypatch):
    shared, projects = tmp_path / "shared", tmp_path / "projects"
    shared.joinpath("dies").mkdir(parents=True)
    projects.mkdir()
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(projects))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    users.invite_user(ADMIN, role="admin", name="Admin")
    users.invite_user(CUSTOMER, role="user", name="Customer")
    current = {"email": ADMIN, "role": "admin"}
    monkeypatch.setattr(auth, "resolve_user",
                        lambda token, **kwargs: dict(current) if token else None)
    app = FastAPI(dependencies=[Depends(motor_access.config_path_gate)])
    app.include_router(projects_router)
    app.include_router(motors_router)
    app.include_router(family_router)
    return TestClient(app), current, shared


def _write_motor(dies):
    root = dies / "MixedCaseDie"
    root.mkdir(parents=True)
    (root / "die.yaml").write_text(
        "name: MixedCaseDie\ngeometry:\n  stator_outer_radius: 40\n", encoding="utf-8")
    (root / "CfgA.yaml").write_text(
        "name: CfgA\nvisibility: private\nduties:\n  - name: RatedDuty\n    rpm: 1000\n",
        encoding="utf-8")


def _route_shared(monkeypatch, shared):
    dies = shared / "dies"
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{
        "name": "MixedCaseDie", "die": "MixedCaseDie", "dir": dies / "MixedCaseDie",
        "layer": "shared", "owner": "", "owner_id": ""}])
    monkeypatch.setattr(family, "_die_dir", lambda die: dies / die)
    monkeypatch.setattr(family, "_die_file", lambda die: dies / die / "die.yaml")
    monkeypatch.setattr(family, "_cfg_file", lambda die, cfg: dies / die / f"{cfg}.yaml")


def _link(client, headers):
    response = client.post("/api/projects/alpha/motors", headers=headers,
                           json={"die": "MixedCaseDie", "config": "CfgA"})
    assert response.status_code == 200, response.text


def _candidates(project, email):
    return [row for row in project.get("grant_removal_candidates", [])
            if row.get("email") == email]


def test_two_customer_rerole_keeps_both_grants_and_marks_only_departing_customer(
        stage1b, monkeypatch):
    client, _current, shared = stage1b
    users.invite_user(SECOND, role="user", name="Second")
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    store.create_project(_project(members=[
        {"email": ADMIN, "role": "owner"},
        {"email": CUSTOMER, "role": "customer"},
        {"email": SECOND, "role": "customer"},
    ]))
    headers = {"Authorization": "Bearer test"}
    _link(client, headers)

    response = client.post("/api/projects/alpha/members", headers=headers,
                           json={"email": CUSTOMER, "role": "engineer"})
    assert response.status_code == 200, response.text
    assert GRANT in users.get_stored_motor_grants(CUSTOMER).get("configs", [])
    assert GRANT in users.get_stored_motor_grants(SECOND).get("configs", [])
    project = store.get_project("alpha")
    assert _candidates(project, CUSTOMER) == []   # an engineer member keeps the motors (owner 2026-10-26)
    assert _candidates(project, SECOND) == []


def test_preexisting_exact_grant_never_becomes_removal_candidate(stage1b, monkeypatch):
    client, _current, shared = stage1b
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    users.set_motor_grants(CUSTOMER, all_motors=False, dies=[], configs=[GRANT])
    store.create_project(_project())
    headers = {"Authorization": "Bearer test"}
    _link(client, headers)
    response = client.delete(
        "/api/projects/alpha/motors?die=MixedCaseDie&config=CfgA", headers=headers)
    assert response.status_code == 200, response.text
    assert GRANT in users.get_stored_motor_grants(CUSTOMER).get("configs", [])
    assert _candidates(store.get_project("alpha"), CUSTOMER) == []


def test_admin_remove_and_reissue_survives_unlink_as_manual_candidate(stage1b, monkeypatch):
    client, _current, shared = stage1b
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    store.create_project(_project())
    headers = {"Authorization": "Bearer test"}
    _link(client, headers)

    users.set_config_grant_holders(GRANT, [])
    assert GRANT not in users.get_stored_motor_grants(CUSTOMER).get("configs", [])
    users.set_config_grant_holders(GRANT, [CUSTOMER])
    response = client.delete(
        "/api/projects/alpha/motors?die=MixedCaseDie&config=CfgA", headers=headers)
    assert response.status_code == 200, response.text
    assert GRANT in users.get_stored_motor_grants(CUSTOMER).get("configs", [])
    assert len(_candidates(store.get_project("alpha"), CUSTOMER)) == 1


def test_failed_link_keeps_addition_and_records_admin_only_cleanup_candidate(
        stage1b, monkeypatch):
    client, current, shared = stage1b
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    store.create_project(_project())
    headers = {"Authorization": "Bearer test"}
    from motor_ai_sim import json_store
    real_mutate = json_store.mutate_json

    def fail_index(path, mutator, **kwargs):
        if path == store.index_path():
            raise OSError("injected index failure")
        return real_mutate(path, mutator, **kwargs)

    monkeypatch.setattr(json_store, "mutate_json", fail_index)
    with pytest.raises(OSError, match="injected index failure"):
        client.post("/api/projects/alpha/motors", headers=headers,
                    json={"die": "MixedCaseDie", "config": "CfgA"})
    assert GRANT in users.get_stored_motor_grants(CUSTOMER).get("configs", [])
    project = store.get_project("alpha")
    assert project.get("linked_motors", []) == []
    assert len(_candidates(project, CUSTOMER)) == 1

    current.update(email=CUSTOMER, role="user")
    projection = client.get("/api/projects/alpha", headers=headers)
    assert projection.status_code == 200
    assert "grant_removal_candidates" not in projection.json()


def test_membership_gate_reads_index_without_enumerating_project_documents(
        stage1b, monkeypatch):
    client, current, _shared = stage1b
    store.create_project(_project())
    current.update(email=CUSTOMER, role="user")
    monkeypatch.setattr(store, "list_projects", lambda: (_ for _ in ()).throw(
        AssertionError("membership endpoint scanned project documents")))
    response = client.get("/api/projects/membership",
                          headers={"Authorization": "Bearer test"})
    assert response.status_code == 200, response.text
    assert response.json() == {"has_projects": True}


def test_failed_customer_role_change_rolls_back_membership_but_keeps_candidate_grant(
        stage1b, monkeypatch):
    client, _current, shared = stage1b
    users.invite_user(SECOND, role="user", name="Second")
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    store.create_project(_project(members=[
        {"email": ADMIN, "role": "owner"},
        {"email": CUSTOMER, "role": "customer"},
        {"email": SECOND, "role": "engineer"},
    ]))
    headers = {"Authorization": "Bearer test"}
    _link(client, headers)

    monkeypatch.setattr(store, "sync_membership_index", lambda *args: (_ for _ in ()).throw(
        OSError("injected membership index failure")))
    with pytest.raises(OSError, match="injected membership index failure"):
        client.post("/api/projects/alpha/members", headers=headers,
                    json={"email": SECOND, "role": "customer"})
    assert GRANT in users.get_stored_motor_grants(SECOND).get("configs", [])
    project = store.get_project("alpha")
    member = next(row for row in project["members"] if row["email"] == SECOND)
    assert member["role"] == "engineer"
    # the grant came from the link (engineers get the motors too), not from the failed re-role
    assert _candidates(project, SECOND) == []


def test_failed_member_removal_restores_project_and_membership_index(stage1b, monkeypatch):
    client, _current, _shared = stage1b
    store.create_project(_project())
    headers = {"Authorization": "Bearer test"}
    assert store.membership_projects(CUSTOMER) == ["alpha"]

    monkeypatch.setattr(store, "sync_membership_index", lambda *args: (_ for _ in ()).throw(
        OSError("injected membership removal index failure")))
    with pytest.raises(OSError, match="injected membership removal index failure"):
        client.delete(f"/api/projects/alpha/members?email={CUSTOMER}", headers=headers)
    project = store.get_project("alpha")
    member = next(row for row in project["members"] if row["email"] == CUSTOMER)
    assert member["role"] == "customer"
    assert store.membership_projects(CUSTOMER) == ["alpha"]
