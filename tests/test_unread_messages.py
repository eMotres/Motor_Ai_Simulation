"""Unread messages of project and production threads (owner 2026-10-28): read marks, counts, access filtering, migration."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, message_reads, model3d
from motor_ai_sim.development import store as dev_store
from motor_ai_sim.development.routes import router as dev_router
from motor_ai_sim.production import store as prod_store
from motor_ai_sim.production.routes import router as prod_router
from motor_ai_sim.routes.messages import router as messages_router

ENG = "eng@example.test"
CUSTOMER = "customer@example.test"
OTHER = "other@example.test"
ADMIN = "admin@example.test"
OEM = "oem@example.test"
DIE, CONFIG = "Demo X1", "L10"

ROLES = {ENG: "engineer", CUSTOMER: "user", OTHER: "user", ADMIN: "admin", OEM: "oem"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    (tmp_path / "projects").mkdir()
    (tmp_path / "production").mkdir()
    # the feature "started" long ago, so every message written by a test counts as new
    for root in (tmp_path / "projects", tmp_path / "production"):
        (root / message_reads.SINCE_FILE).write_text(json.dumps({"since": "2000-01-01T00:00:00+00:00"}), encoding="utf-8")
    current = {"email": ENG}
    monkeypatch.setattr(auth, "resolve_user", lambda token: {"email": current["email"], "role": ROLES[current["email"]]})
    monkeypatch.setattr(auth, "caller_identity", lambda token: {"id": current["email"]})
    monkeypatch.setattr(model3d, "can_download", lambda identity, rec: False)      # nobody has signed the NDA
    monkeypatch.setattr(model3d, "denial_detail", lambda identity, rec: "NDA required")
    app = FastAPI()
    for router in (dev_router, prod_router, messages_router):
        app.include_router(router)
    client = TestClient(app, headers={"Authorization": "Bearer dev"})

    def as_user(email):
        current["email"] = email
        return client

    dev_store.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t",
                              "updated_at": "t", "owner_engineer": ENG, "linked_motors": [], "notes": "", "pending_invites": [],
                              "members": [{"email": ENG, "role": "owner"}, {"email": CUSTOMER, "role": "customer"}]})
    prod_store.save_file(DIE, CONFIG, "production.json", {
        "die": DIE, "config": CONFIG,
        "members": [{"email": ENG, "role": "engineer"}, {"email": OEM, "role": "oem"}]})
    return as_user


def unread(as_user, email):
    return as_user(email).get("/api/messages/unread").json()


def say_project(as_user, email, text="hello"):
    r = as_user(email).post("/api/projects/alpha/thread", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def say_production(as_user, email, thread_id, text="hi"):
    r = as_user(email).post(f"/api/production/{DIE}/{CONFIG}/threads/{thread_id}/messages",
                            json={"text": {"en": text, "zh": "", "src_lang": "en"}, "src_lang": "en"})
    assert r.status_code == 200, r.text
    return r.json()["messages"][-1]["id"]


def new_thread(as_user, email, part_id=None):
    r = as_user(email).post(f"/api/production/{DIE}/{CONFIG}/threads", json={"part_id": part_id})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_project_unread_count_mark_read_and_own_messages(env):
    first = say_project(env, ENG, "one")
    say_project(env, ENG, "two")
    assert unread(env, ENG)["projects"] == {}                       # own messages are never unread
    body = unread(env, CUSTOMER)
    assert body["projects"] == {"alpha": 2} and body["total_projects"] == 2 and body["total_productions"] == 0
    listed = env(CUSTOMER).get("/api/projects").json()["projects"]
    assert listed[0]["unread_messages"] == 2
    thread = env(CUSTOMER).get("/api/projects/alpha/thread").json()
    assert thread["unread"] == 2 and [m["unread"] for m in thread["messages"]] == [True, True]
    assert unread(env, CUSTOMER)["total_projects"] == 2             # reading the thread does not mark it read
    r = env(CUSTOMER).post("/api/projects/alpha/thread/read", json={"up_to": first})
    assert r.status_code == 200 and r.json()["unread"] == 1
    assert unread(env, CUSTOMER)["projects"] == {"alpha": 1}
    assert env(CUSTOMER).post("/api/projects/alpha/thread/read", json={}).json()["unread"] == 0
    assert unread(env, CUSTOMER)["projects"] == {}
    assert unread(env, ENG)["projects"] == {}
    say_project(env, CUSTOMER, "reply")
    assert unread(env, ENG)["projects"] == {"alpha": 1}
    assert unread(env, CUSTOMER)["projects"] == {}


def test_project_mark_never_moves_back_and_rejects_unknown_id(env):
    first = say_project(env, ENG, "one")
    second = say_project(env, ENG, "two")
    say_project(env, ENG, "three")
    assert env(CUSTOMER).post("/api/projects/alpha/thread/read", json={"up_to": second}).json()["unread"] == 1
    assert env(CUSTOMER).post("/api/projects/alpha/thread/read", json={"up_to": first}).json()["unread"] == 1
    assert env(CUSTOMER).post("/api/projects/alpha/thread/read", json={"up_to": "nope"}).status_code == 404


def test_project_resolution_events_are_not_messages(env):
    message = say_project(env, CUSTOMER, "question")
    assert env(ENG).post(f"/api/projects/alpha/thread/{message}/resolve", json={"resolved": True}).status_code == 200
    assert unread(env, CUSTOMER)["projects"] == {}                  # the resolution is bookkeeping, not an unread message
    assert unread(env, ENG)["projects"] == {"alpha": 1}


def test_project_counts_respect_access(env):
    say_project(env, ENG)
    assert unread(env, OTHER) == {"projects": {}, "productions": {}, "total_projects": 0, "total_productions": 0}
    assert env(OTHER).post("/api/projects/alpha/thread/read", json={}).status_code == 404
    assert env(OTHER).get("/api/projects").json()["projects"] == []
    assert unread(env, ADMIN)["projects"] == {"alpha": 1}           # staff may open every project


def test_marks_are_per_user(env):
    say_project(env, ENG)
    env(CUSTOMER).post("/api/projects/alpha/thread/read", json={})
    assert unread(env, CUSTOMER)["projects"] == {}
    assert unread(env, ADMIN)["projects"] == {"alpha": 1}


def test_messages_older_than_the_feature_are_read(env, tmp_path):
    say_project(env, ENG, "written before the deploy")
    (tmp_path / "projects" / message_reads.SINCE_FILE).write_text(
        json.dumps({"since": "2999-01-01T00:00:00+00:00"}), encoding="utf-8")
    assert unread(env, CUSTOMER)["projects"] == {}


def test_since_marker_is_created_once_on_first_use(tmp_path):
    first = message_reads.since_for(tmp_path)
    assert (tmp_path / message_reads.SINCE_FILE).is_file()
    assert message_reads.since_for(tmp_path) == first


def test_production_unread_per_thread_and_total(env):
    general = new_thread(env, ENG)
    part = new_thread(env, ENG, "p1")
    say_production(env, ENG, general)
    last = say_production(env, ENG, general, "second")
    say_production(env, ENG, part)
    assert unread(env, OEM)["productions"] == {}                    # an OEM without the NDA cannot open the threads: no count
    assert env(ADMIN).get("/api/production").json()["productions"][0]["unread_messages"] == 3
    body = unread(env, ADMIN)
    assert body["productions"] == {f"{DIE}/{CONFIG}": 3} and body["total_productions"] == 3
    threads = {t["id"]: t for t in env(ADMIN).get(f"/api/production/{DIE}/{CONFIG}/threads").json()["threads"]}
    assert threads[general]["unread"] == 2 and len(threads[general]["unread_ids"]) == 2 and threads[part]["unread"] == 1
    r = env(ADMIN).post(f"/api/production/{DIE}/{CONFIG}/threads/{general}/read", json={"up_to": last})
    assert r.status_code == 200 and r.json()["unread"] == 1
    assert unread(env, ADMIN)["total_productions"] == 1
    assert unread(env, ENG)["total_productions"] == 0               # own messages
    assert env(ADMIN).post(f"/api/production/{DIE}/{CONFIG}/threads/nope/read", json={}).status_code == 404
    assert env(ADMIN).post(f"/api/production/{DIE}/{CONFIG}/threads/{general}/read", json={"up_to": "x"}).status_code == 404


def test_production_oem_with_nda_sees_count_and_non_member_sees_none(env, monkeypatch):
    thread = new_thread(env, ENG)
    say_production(env, ENG, thread)
    monkeypatch.setattr(model3d, "can_download", lambda identity, rec: True)
    assert unread(env, OEM)["productions"] == {f"{DIE}/{CONFIG}": 1}
    assert unread(env, OTHER)["productions"] == {}
    assert env(OTHER).post(f"/api/production/{DIE}/{CONFIG}/threads/{thread}/read", json={}).status_code == 404


def test_production_marks_follow_the_folder(env):
    thread = new_thread(env, ENG)
    say_production(env, ENG, thread)
    env(ADMIN).post(f"/api/production/{DIE}/{CONFIG}/threads/{thread}/read", json={})
    assert (prod_store.path_for(DIE, CONFIG) / message_reads.MARKS_FILE).is_file()
