from __future__ import annotations

import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, motor_access, users, workspace
from motor_ai_sim.development import store
from motor_ai_sim.development.motors import router as motors_router
from motor_ai_sim.development.routes import router as projects_router
from motor_ai_sim.routes import family
from motor_ai_sim.routes.family import router as family_router


ADMIN = "admin@example.test"
ENGINEER = "engineer@example.test"
CUSTOMER = "customer@example.test"


def _project(project_id="alpha", members=None):
    return {
        "id": project_id, "title": project_id, "customer_org": "Customer",
        "status": "spec", "created_at": "t", "updated_at": "t",
        "owner_engineer": ADMIN,
        "members": members or [
            {"email": ADMIN, "role": "owner"},
            {"email": CUSTOMER, "role": "customer"},
        ],
        "linked_motors": [], "notes": "internal", "pending_invites": [],
    }


@pytest.fixture
def stage1b(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    projects = tmp_path / "projects"
    shared.joinpath("dies").mkdir(parents=True)
    projects.mkdir()
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(projects))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    for email, role, name in (
        (ADMIN, "admin", "Admin"), (ENGINEER, "user", "Engineer"),
        (CUSTOMER, "user", "Customer"),
    ):
        users.invite_user(email, role=role, name=name)
    current = {"email": ADMIN, "role": "admin"}
    monkeypatch.setattr(auth, "resolve_user", lambda token, **kwargs: dict(current) if token else None)
    app = FastAPI(dependencies=[Depends(motor_access.config_path_gate)])
    app.include_router(projects_router)
    app.include_router(motors_router)
    app.include_router(family_router)
    return TestClient(app), current, shared


def _write_motor(dies, die="MixedCaseDie", config="CfgA", *, visibility="private"):
    root = dies / die
    root.mkdir(parents=True)
    (root / "die.yaml").write_text(
        f"name: {die}\ngeometry:\n  stator_outer_radius: 40\n", encoding="utf-8")
    (root / f"{config}.yaml").write_text(
        f"name: {config}\nvisibility: {visibility}\nduties:\n  - name: RatedDuty\n    rpm: 1000\n",
        encoding="utf-8")
    return root


def _route_shared(monkeypatch, shared):
    dies = shared / "dies"
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{
        "name": "MixedCaseDie", "die": "MixedCaseDie", "dir": dies / "MixedCaseDie",
        "layer": "shared", "owner": "", "owner_id": ""}])
    monkeypatch.setattr(family, "_die_dir", lambda die: dies / die)
    monkeypatch.setattr(family, "_die_file", lambda die: dies / die / "die.yaml")
    monkeypatch.setattr(family, "_cfg_file", lambda die, cfg: dies / die / f"{cfg}.yaml")


def test_shared_link_inserts_exact_case_grant_and_ordinary_payload_works(stage1b, monkeypatch):
    client, current, shared = stage1b
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    store.create_project(_project())
    headers = {"Authorization": "Bearer test"}

    linked = client.post("/api/projects/alpha/motors", headers=headers,
                         json={"die": "MixedCaseDie", "config": "CfgA"})
    assert linked.status_code == 200, linked.text
    assert "MixedCaseDie/CfgA" in users.get_stored_motor_grants(CUSTOMER).get("configs", [])
    assert "mixedcasedie/cfga" not in users.get_stored_motor_grants(CUSTOMER).get("configs", [])

    current.update(email=CUSTOMER, role="user")
    payload = client.get("/api/family/payload/MixedCaseDie/CfgA", headers=headers)
    assert payload.status_code == 200, payload.text
    assert payload.json()["config"] == "CfgA"


def test_project_engineer_cannot_share_unowned_private_shared_motor(stage1b, monkeypatch):
    client, current, shared = stage1b
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    users.set_motor_grants(ENGINEER, all_motors=False, dies=[],
                           configs=["MixedCaseDie/CfgA"])
    store.create_project(_project(members=[
        {"email": ENGINEER, "role": "engineer"},
        {"email": CUSTOMER, "role": "customer"},
    ]))
    current.update(email=ENGINEER, role="user")
    response = client.post("/api/projects/alpha/motors",
                           headers={"Authorization": "Bearer test"},
                           json={"die": "MixedCaseDie", "config": "CfgA"})
    assert response.status_code == 403
    assert users.get_stored_motor_grants(CUSTOMER).get("configs", []) == []


def test_link_answers_a_hidden_configuration_like_a_missing_one(stage1b, monkeypatch):
    # The die is visible to the engineer, its private CfgA is not: linking CfgA must answer exactly like a
    # configuration that does not exist (it used to be 404 against a 500).  Swept in tests/test_config_visibility.py.
    client, current, shared = stage1b
    root = _write_motor(shared / "dies")
    (root / "CfgB.yaml").write_text("name: CfgB\nvisibility: public\n", encoding="utf-8")
    _route_shared(monkeypatch, shared)
    users.set_motor_grants(ENGINEER, all_motors=False, dies=["MixedCaseDie"], configs=[])
    store.create_project(_project(members=[
        {"email": ENGINEER, "role": "engineer"},
        {"email": CUSTOMER, "role": "customer"},
    ]))
    headers = {"Authorization": "Bearer test"}
    probe = TestClient(client.app, raise_server_exceptions=False)

    def link(cfg):
        return probe.post("/api/projects/alpha/motors", headers=headers,
                          json={"die": "MixedCaseDie", "config": cfg})

    for who, role in ((ENGINEER, "user"), (ADMIN, "admin")):
        current.update(email=who, role=role)
        missing = link("NoSuch")
        assert missing.status_code == 404, (who, missing.text)
        if who == ENGINEER:
            hidden = link("CfgA")
            assert hidden.status_code == 404
            assert hidden.text.replace("CfgA", "<CFG>") == missing.text.replace("NoSuch", "<CFG>")
    assert users.get_stored_motor_grants(CUSTOMER).get("configs", []) == []


def test_unlink_is_add_only_and_preserves_manual_and_project_grants(stage1b, monkeypatch):
    client, _current, shared = stage1b
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    users.set_motor_grants(CUSTOMER, all_motors=False, dies=["ManualDie"],
                           configs=["ManualDie/B"])
    store.create_project(_project("alpha"))
    store.create_project(_project("beta"))
    headers = {"Authorization": "Bearer test"}
    assert client.post("/api/projects/alpha/motors", headers=headers,
                       json={"die": "MixedCaseDie", "config": "CfgA"}).status_code == 200

    # Historic/recovery overlap: product linking remains exclusive, but cleanup
    # must preserve a grant while another project document still demands it.
    beta = store.get_project("beta")
    beta["linked_motors"] = [{"die": "MixedCaseDie", "config": "CfgA"}]
    store.mutate_project("beta", lambda doc: doc.update(beta))
    assert client.delete("/api/projects/alpha/motors?die=MixedCaseDie&config=CfgA",
                         headers=headers).status_code == 200
    alpha_candidates = store.get_project("alpha").get("grant_removal_candidates", [])
    grants = users.get_stored_motor_grants(CUSTOMER)
    assert grants["dies"] == ["ManualDie"]
    assert set(grants.get("configs", [])) == {"ManualDie/B", "MixedCaseDie/CfgA"}
    assert client.delete("/api/projects/beta/motors?die=MixedCaseDie&config=CfgA",
                         headers=headers).status_code == 200
    assert set(users.get_stored_motor_grants(CUSTOMER).get("configs", [])) == {
        "ManualDie/B", "MixedCaseDie/CfgA"}
    assert any(row.get("email") == CUSTOMER and row.get("die") == "MixedCaseDie"
               and row.get("config") == "CfgA" for row in alpha_candidates)


def test_link_failure_keeps_addition_as_candidate_without_project_link(stage1b, monkeypatch):
    client, _current, shared = stage1b
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    store.create_project(_project())
    from motor_ai_sim import json_store
    real_mutate = json_store.mutate_json

    def fail_index(path, mutator, **kwargs):
        if path == store.index_path():
            raise OSError("injected index failure")
        return real_mutate(path, mutator, **kwargs)

    monkeypatch.setattr(json_store, "mutate_json", fail_index)
    with pytest.raises(OSError, match="injected index failure"):
        client.post("/api/projects/alpha/motors", headers={"Authorization": "Bearer test"},
                    json={"die": "MixedCaseDie", "config": "CfgA"})
    assert users.get_stored_motor_grants(CUSTOMER).get("configs", []) == ["MixedCaseDie/CfgA"]
    project = store.get_project("alpha")
    assert project.get("linked_motors", []) == []
    assert any(row.get("email") == CUSTOMER for row in project.get("grant_removal_candidates", []))


def test_unlink_index_failure_restores_grant_link_and_index(stage1b, monkeypatch):
    client, _current, shared = stage1b
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    store.create_project(_project())
    headers = {"Authorization": "Bearer test"}
    assert client.post("/api/projects/alpha/motors", headers=headers,
                       json={"die": "MixedCaseDie", "config": "CfgA"}).status_code == 200
    from motor_ai_sim import json_store
    real_mutate = json_store.mutate_json

    def fail_index(path, mutator, **kwargs):
        if path == store.index_path():
            raise OSError("injected unlink index failure")
        return real_mutate(path, mutator, **kwargs)

    monkeypatch.setattr(json_store, "mutate_json", fail_index)
    with pytest.raises(OSError, match="injected unlink index failure"):
        client.delete("/api/projects/alpha/motors?die=MixedCaseDie&config=CfgA",
                      headers=headers)
    assert users.get_stored_motor_grants(CUSTOMER).get("configs", []) == ["MixedCaseDie/CfgA"]
    assert store.get_project("alpha")["linked_motors"] == [
        {"die": "MixedCaseDie", "config": "CfgA"}]
    row = store.load_index()["motors"]["mixedcasedie\0cfga"]
    assert row["project_id"] == "alpha"


def test_customer_rerole_failure_restores_membership_and_success_is_add_only(stage1b, monkeypatch):
    client, _current, shared = stage1b
    _write_motor(shared / "dies")
    _route_shared(monkeypatch, shared)
    store.create_project(_project())
    headers = {"Authorization": "Bearer test"}
    assert client.post("/api/projects/alpha/motors", headers=headers,
                       json={"die": "MixedCaseDie", "config": "CfgA"}).status_code == 200
    real_mutate = store.mutate_project

    failed = False
    def fail_alpha(project_id, mutator):
        nonlocal failed
        if project_id == "alpha" and not failed:
            failed = True
            raise OSError("injected member write failure")
        return real_mutate(project_id, mutator)

    monkeypatch.setattr(store, "mutate_project", fail_alpha)
    with pytest.raises(OSError, match="injected member write failure"):
        client.post("/api/projects/alpha/members", headers=headers,
                    json={"email": CUSTOMER, "role": "engineer"})
    assert users.get_stored_motor_grants(CUSTOMER).get("configs", []) == ["MixedCaseDie/CfgA"]
    member = next(m for m in store.get_project("alpha")["members"] if m["email"] == CUSTOMER)
    assert member["role"] == "customer"

    monkeypatch.setattr(store, "mutate_project", real_mutate)
    response = client.post("/api/projects/alpha/members", headers=headers,
                           json={"email": CUSTOMER, "role": "engineer"})
    assert response.status_code == 200, response.text
    assert users.get_stored_motor_grants(CUSTOMER).get("configs", []) == ["MixedCaseDie/CfgA"]
    assert any(row.get("email") == CUSTOMER
               for row in store.get_project("alpha").get("grant_removal_candidates", []))


def test_customer_thread_and_version_metadata_never_exposes_other_email(stage1b):
    client, current, _shared = stage1b
    project = _project()
    store.create_project(project)
    store.save_json("alpha", "draft.json", {"draft": {"fields": {}, "mission_profile": [],
                    "mission_profile_status": "missing", "production": {}, "provenance": {}},
                    "revision": 0, "version": None})
    store.append_jsonl("alpha", "thread.jsonl", {
        "id": "m1", "author_email": ADMIN, "created_at": "t", "text": "hello"})
    store.save_json("alpha", "spec/index.json", {"versions": [{
        "version": 1, "author_email": ADMIN, "created_at": "t", "source_revision": 0}]})
    store.save_json("alpha", "spec/v1.json", {"version": 1, "author_email": ADMIN,
        "created_at": "t", "source_revision": 0,
        "draft": {"fields": {}, "mission_profile": [], "mission_profile_status": "missing",
                  "production": {}, "provenance": {"x": {"source": "C:/private/input.md",
                                                             "sha256": "a" * 64}}}})
    current.update(email=CUSTOMER, role="user")
    headers = {"Authorization": "Bearer test"}
    for url in ("/api/projects/alpha/thread", "/api/projects/alpha/spec",
                "/api/projects/alpha/spec/versions", "/api/projects/alpha/spec/versions/1"):
        response = client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        text = response.text
        assert ADMIN not in text
        assert "author_email" not in text
        assert "C:/private" not in text


def test_published_mixed_case_identity_is_customer_resolvable(stage1b, tmp_path, monkeypatch):
    client, current, shared = stage1b
    workspaces = tmp_path / "workspaces"
    published = tmp_path / "published"
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    owner_ws = workspace.workspace_for_identity(ENGINEER)
    customer_ws = workspace.workspace_for_identity(CUSTOMER)
    namespace = published / owner_ws.id
    _write_motor(namespace / "dies")
    # Published namespaces store dies directly, not below an extra `dies/`.
    motor_dir = namespace / "dies" / "MixedCaseDie"
    target = namespace / "MixedCaseDie"
    motor_dir.rename(target)
    namespace.joinpath("dies").rmdir()
    (namespace / workspace.OWNER_FILE).write_text(json.dumps({"email": ENGINEER}), encoding="utf-8")
    users.set_motor_grants(ENGINEER, all_motors=False, dies=["MixedCaseDie"],
                           configs=["MixedCaseDie/CfgA"])
    store.create_project(_project(members=[
        {"email": ENGINEER, "role": "owner"},
        {"email": CUSTOMER, "role": "customer"},
    ]))
    headers = {"Authorization": "Bearer test"}
    current.update(email=ENGINEER, role="user")
    with workspace.use_workspace(owner_ws):
        linked = client.post("/api/projects/alpha/motors", headers=headers,
                             json={"die": "MixedCaseDie", "config": "CfgA"})
    assert linked.status_code == 200, linked.text
    with workspace.use_workspace(owner_ws):
        owner_listing = client.get("/api/projects/alpha/motors", headers=headers)
        assert owner_listing.status_code == 200
        owner_row = owner_listing.json()["motors"][0]
        assert owner_row["available"] is True
        assert owner_row["die"] == "MixedCaseDie"
        owner_payload = client.get(
            "/api/family/payload/MixedCaseDie/CfgA?duty=RatedDuty", headers=headers)
        assert owner_payload.status_code == 200, owner_payload.text
        assert owner_payload.json()["duty"]["name"] == "RatedDuty"
        assert isinstance(owner_payload.json().get("geometry"), dict)

    current.update(email=CUSTOMER, role="user")
    with workspace.use_workspace(customer_ws):
        listing = client.get("/api/projects/alpha/motors", headers=headers)
        assert listing.status_code == 200, listing.text
        row = listing.json()["motors"][0]
        assert row["available"] is True
        assert row["die"].startswith("MixedCaseDie") and "by Engineer" in row["die"]
        payload = client.get(
            f"/api/family/payload/{row['die']}/CfgA?duty=RatedDuty", headers=headers)
        assert payload.status_code == 200, payload.text
        assert payload.json()["duty"]["name"] == "RatedDuty"
        assert isinstance(payload.json().get("geometry"), dict)
