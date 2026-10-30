"""Self-service projects (owner 2026-10-30): any signed-in account opens its own development project.

Pins: who may create, the generated id, the owner role, the per-account limit, staff access without a grant, the platform-agreement
gate (428) and the owner-report line.  No solver, no network: the project store lives in tmp_path.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, platform_agreement as PA, users
from motor_ai_sim.development import access, self_service, store
from motor_ai_sim.development.routes import router

CUSTOMER = "gonzalo@drone.example"
OTHER = "other@drone.example"
ENGINEER = "eng@example.test"
ADMIN = "admin@example.test"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(tmp_path / "legal"))
    monkeypatch.setenv("MOTRES_DIRECTORY_ROOT", str(tmp_path / "directory"))
    monkeypatch.delenv("PLATFORM_AGREEMENT_DISABLED", raising=False)
    PA._CACHE.update(sig=None, rows=[])
    people = {
        "t-customer": {"email": CUSTOMER, "role": "user"},
        "t-other": {"email": OTHER, "role": "user"},
        "t-eng": {"email": ENGINEER, "role": "engineer"},
        "t-admin": {"email": ADMIN, "role": "admin"},
    }
    monkeypatch.setattr(auth, "resolve_user", lambda header: dict(people[str(header or "").replace("Bearer ", "")]) if str(header or "").replace("Bearer ", "") in people else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True, "name": "Gonzalo"})
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    def call(who, method, path, **kw):
        return client.request(method, path, headers={"Authorization": f"Bearer {who}"}, **kw)
    return call


def _create(api, who="t-customer", **body):
    return api(who, "POST", "/api/projects", json={"title": "7-inch high-pitch prop motor", **body})


def test_a_plain_user_creates_a_project_and_is_its_owner(api):
    r = _create(api, customer_org="Drone Co")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == "7-inch-high-pitch-prop-motor" and out["status"] == "spec"
    assert out["can_edit_spec"] is True                          # the owner fills the requirements
    stored = store.get_project(out["id"])
    assert stored["members"] == [{"email": CUSTOMER, "role": "owner"}]
    assert stored["owner_engineer"] == ""                       # nobody in charge yet: staff pick it up
    assert store.membership_projects(CUSTOMER) == [out["id"]]
    assert access.membership(CUSTOMER, stored) == "owner"
    draft = store.load_json(out["id"], "draft.json", None)
    assert draft["revision"] == 0 and draft["draft"]["fields"] == {}


def test_a_customer_cannot_choose_members_status_or_engineer(api):
    r = _create(api, id="chosen-id", status="production", owner_engineer=ENGINEER,
                members=[{"email": ENGINEER, "role": "owner"}])
    assert r.status_code == 200
    project = store.get_project(r.json()["id"])
    assert project["id"] == "7-inch-high-pitch-prop-motor"       # the id is generated, not taken from the body
    assert project["status"] == "spec" and project["owner_engineer"] == ""
    assert project["members"] == [{"email": CUSTOMER, "role": "owner"}]


def test_ids_are_unique_slugs_and_survive_a_non_latin_title(api):
    ids = [_create(api, title="Gonzalo motor!").json()["id"] for _ in range(3)]
    assert ids == ["gonzalo-motor", "gonzalo-motor-2", "gonzalo-motor-3"]
    zh = _create(api, who="t-other", title="七寸高螺距电机").json()["id"]
    assert store.valid_id(zh) and zh == "project"
    assert self_service.slug_from_title("  Ünïcode — Motor / 7\" ") == "unicode-motor-7"
    assert len(self_service.slug_from_title("x" * 200)) <= 48


def test_title_is_required(api):
    assert api("t-customer", "POST", "/api/projects", json={"title": "   "}).status_code == 422
    assert api("t-customer", "POST", "/api/projects", json={}).status_code == 422


def test_unsigned_request_is_refused(api):
    r = api("nobody", "POST", "/api/projects", json={"title": "x"})
    assert r.status_code in (401, 403)


def test_per_account_limit_of_open_projects(api):
    for n in range(self_service.MAX_OPEN_OWNED_PROJECTS):
        assert _create(api, title=f"Motor {n}").status_code == 200
    blocked = _create(api, title="One too many")
    assert blocked.status_code == 429
    detail = blocked.json()["detail"]
    assert detail["code"] == self_service.LIMIT_CODE and detail["limit"] == self_service.MAX_OPEN_OWNED_PROJECTS
    # the limit is per account
    assert _create(api, who="t-other", title="Mine").status_code == 200
    # closing a project frees a place
    first = store.membership_projects(CUSTOMER)[0]
    store.mutate_project(first, lambda d: d.update(status="closed"))
    assert _create(api, title="After closing").status_code == 200


def test_projects_the_account_does_not_own_do_not_count(api):
    for n in range(self_service.MAX_OPEN_OWNED_PROJECTS):
        store.create_project({"id": f"staff-{n}", "title": "S", "customer_org": "", "status": "spec", "created_at": "t",
                              "updated_at": "t", "owner_engineer": ENGINEER, "linked_motors": [], "notes": "",
                              "pending_invites": [],
                              "members": [{"email": ENGINEER, "role": "owner"}, {"email": CUSTOMER, "role": "customer"}]})
    assert self_service.open_owned_count(CUSTOMER) == 0
    assert _create(api).status_code == 200


def test_staff_keep_their_own_way_and_are_not_limited(api):
    r = _create(api, who="t-eng", id="named-by-staff", customer_org="Acme",
                members=[{"email": CUSTOMER, "role": "owner"}])
    assert r.status_code == 200 and r.json()["id"] == "named-by-staff"
    assert store.get_project("named-by-staff")["members"] == [{"email": CUSTOMER, "role": "owner"}]
    assert store.get_project("named-by-staff")["owner_engineer"] == ENGINEER
    assert _create(api, who="t-eng", id="named-by-staff").status_code == 409          # a named id that exists is a conflict
    assert _create(api, who="t-eng", id="Bad Id").status_code == 422
    for n in range(self_service.MAX_OPEN_OWNED_PROJECTS + 1):
        assert _create(api, who="t-admin", title=f"Staff {n}").status_code == 200


def test_staff_see_and_edit_a_customers_project_without_any_grant(api):
    pid = _create(api).json()["id"]
    for who in ("t-eng", "t-admin"):
        assert api(who, "GET", f"/api/projects/{pid}").status_code == 200
        listed = api(who, "GET", "/api/projects").json()["projects"]
        assert pid in [p["id"] for p in listed]
        assert api(who, "PATCH", f"/api/projects/{pid}", json={"owner_engineer": ENGINEER, "status": "design"}).status_code == 200
    # another customer sees nothing of it
    assert api("t-other", "GET", f"/api/projects/{pid}").status_code == 404
    assert pid not in [p["id"] for p in api("t-other", "GET", "/api/projects").json()["projects"]]


def test_the_owner_fills_the_specification_and_others_cannot(api):
    pid = _create(api).json()["id"]
    spec = api("t-customer", "GET", f"/api/projects/{pid}/spec").json()
    saved = api("t-customer", "PUT", f"/api/projects/{pid}/spec",
                json={"draft": {**spec["draft"], "fields": {"1_1": "7-inch high-pitch propeller"}}, "revision": spec["revision"], "schema_version": 2})
    assert saved.status_code == 200, saved.text
    assert api("t-other", "PUT", f"/api/projects/{pid}/spec", json={"draft": spec["draft"], "revision": 1, "schema_version": 2}).status_code == 404


def test_the_agreement_gate_still_answers_428_until_accepted(api, tmp_path, monkeypatch):
    d = tmp_path / "legal" / "platform_agreement" / "v3-final"
    d.mkdir(parents=True)
    (d / "platform_agreement_en.md").write_text("# Agreement\n\nSigned by {{signatory}}.\n", encoding="utf-8")
    (d / "platform_agreement_zh-CN.md").write_text("# 协议\n\n签署人 {{signatory}}。\n", encoding="utf-8")
    (tmp_path / "legal" / "platform_agreement" / "current.txt").write_text("v3-final\n", encoding="utf-8")
    PA._CACHE.update(sig=None, rows=[])
    assert PA.enforced() is True
    blocked = _create(api)
    assert blocked.status_code == 428 and blocked.json()["detail"]["code"] == "agreement_required"
    assert store.list_projects() == []
    # staff are exempt
    assert _create(api, who="t-eng", id="staff-one").status_code == 200


def test_the_owner_report_names_a_self_service_project():
    from motor_ai_sim.owner_report import sources
    own = sources._project_audit_text({"action": "create", "entity": "project", "entity_id": "p", "details": {"self_service": True}})
    staff = sources._project_audit_text({"action": "create", "entity": "project", "entity_id": "p", "details": {}})
    assert own[0] == "project created by the customer (self-service)" and staff[0] == "project created"
