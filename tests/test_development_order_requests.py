"""Order this motor: POST/GET/PATCH /api/projects/{id}/order-requests (a request to our team, never a price)."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, admin_audit, auth_email, nda
from motor_ai_sim.development import motors, store
from motor_ai_sim.development.routes import router

OWNER = "owner@example.test"
ENGINEER = "engineer@example.test"
CUSTOMER = "customer@example.test"
OUTSIDER = "outsider@example.test"
ROLES = {"t-owner": (OWNER, "user"), "t-engineer": (ENGINEER, "user"), "t-customer": (CUSTOMER, "user"),
         "t-outsider": (OUTSIDER, "user"), "t-admin": ("admin@example.test", "admin")}
BODY = {"die": "Die1", "config": "cfg", "quantity": 3, "note": "Sample run"}


def hdr(who: str) -> dict:
    return {"Authorization": "Bearer " + who}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "downloads.jsonl"))
    monkeypatch.setattr(auth, "resolve_user", lambda token: ({"email": ROLES[token.split()[-1]][0], "role": ROLES[token.split()[-1]][1]} if token else None))
    store.create_project({"id": "ciano", "title": "Controller", "customer_org": "Org", "status": "spec", "created_at": "t",
                          "updated_at": "t", "owner_engineer": OWNER, "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": OWNER, "role": "owner"}, {"email": ENGINEER, "role": "engineer"}, {"email": CUSTOMER, "role": "customer"}]})
    state = {"motors": [{"die": "Die1", "canonical_die": "Die1", "config": "cfg", "can_open_points": True, "results": [{"duty": "rated", "has_result": True}]},
                        {"die": "Die2", "canonical_die": "Die2", "config": "cfg", "can_open_points": False, "results": [{"duty": "rated", "has_result": False}]}]}
    monkeypatch.setattr(motors, "list_project_motors", lambda project_id, request, show_all=False: {"motors": state["motors"]})
    sent, audits = [], []
    monkeypatch.setattr(auth_email, "send", lambda to, subject, body, **kw: sent.append((to, subject, body)) or True)
    monkeypatch.setattr(auth_email, "app_url", lambda: "https://app.example.test")
    monkeypatch.setattr(nda, "admin_address", lambda: "team@example.test")
    monkeypatch.setattr(admin_audit, "record", lambda actor, action, target="", **kw: audits.append((actor, action, target, kw.get("details"))))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), tmp_path, sent, audits


def post(client, who, body=BODY):
    return client.post("/api/projects/ciano/order-requests", headers=hdr(who), json=body)


def test_owner_request_is_stored_with_message_and_team_notice(env):
    client, tmp_path, sent, audits = env
    r = post(client, "t-owner")
    assert r.status_code == 200 and r.json()["created"] is True
    rec = r.json()["order_request"]
    assert (rec["die"], rec["config"], rec["quantity"], rec["note"], rec["by"], rec["status"]) == ("Die1", "cfg", 3, "Sample run", OWNER, "open")
    assert rec["id"] and rec["at"]
    assert store.get_project("ciano")["order_requests"] == [rec]
    thread = [json.loads(x) for x in (tmp_path / "projects" / "ciano" / "thread.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [m["text"] for m in thread] == ["Order requested: Die1 cfg, 3 pcs"]
    assert len(sent) == 1 and sent[0][0] == "team@example.test"      # the team only, never the customer
    assert "Quantity: 3" in sent[0][2]
    assert audits and audits[0][1] == "project.order_request"
    assert "project.order_request" in admin_audit.ACTIONS
    for _, subject, body in sent:
        assert not any(w in (subject + body).lower() for w in ("price", "quote", "cost", "lead time"))


def test_second_press_returns_the_existing_request(env):
    client, _, sent, _ = env
    first = post(client, "t-owner").json()["order_request"]
    again = post(client, "t-engineer", {**BODY, "quantity": 9})
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["order_request"]["id"] == first["id"]
    assert len(store.get_project("ciano")["order_requests"]) == 1 and len(sent) == 1
    # a closed request no longer blocks a new one
    client.patch(f"/api/projects/ciano/order-requests/{first['id']}", headers=hdr("t-admin"), json={"status": "closed"})
    assert post(client, "t-owner").json()["created"] is True


def test_motor_without_results_is_refused(env):
    client, *_ = env
    r = post(client, "t-owner", {**BODY, "die": "Die2"})
    assert r.status_code == 409
    assert "order_requests" not in store.get_project("ciano")
    assert post(client, "t-owner", {**BODY, "die": "Nope"}).status_code == 404


def test_permissions(env):
    client, *_ = env
    assert post(client, "t-customer").status_code == 403         # a viewing customer cannot order
    assert post(client, "t-outsider").status_code == 404
    assert client.post("/api/projects/ciano/order-requests", json=BODY).status_code in (401, 403)
    assert post(client, "t-engineer").status_code == 200
    assert client.get("/api/projects/ciano/order-requests", headers=hdr("t-customer")).status_code == 200
    assert client.get("/api/projects/ciano/order-requests", headers=hdr("t-outsider")).status_code == 404


def test_validation(env):
    client, *_ = env
    for bad in ({**BODY, "quantity": 0}, {**BODY, "quantity": "2"}, {**BODY, "quantity": True}, {**BODY, "note": "x" * 2001}, {"quantity": 1}):
        assert post(client, "t-owner", bad).status_code == 422
    assert post(client, "t-owner", {"die": "Die1", "config": "cfg", "quantity": 1}).status_code == 200


def test_staff_can_acknowledge_and_close_but_customers_cannot(env):
    client, *_ = env
    rid = post(client, "t-owner").json()["order_request"]["id"]
    url = f"/api/projects/ciano/order-requests/{rid}"
    assert client.patch(url, headers=hdr("t-owner"), json={"status": "closed"}).status_code == 403
    assert client.patch(url, headers=hdr("t-admin"), json={"status": "open"}).status_code == 422
    done = client.patch(url, headers=hdr("t-admin"), json={"status": "acknowledged"})
    assert done.status_code == 200 and done.json()["order_request"]["status"] == "acknowledged"
    assert client.patch(url + "x", headers=hdr("t-admin"), json={"status": "closed"}).status_code == 404
    listed = client.get("/api/projects/ciano/order-requests", headers=hdr("t-owner")).json()["order_requests"]
    assert listed[0]["status"] == "acknowledged"
