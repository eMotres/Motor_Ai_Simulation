"""A project message marked price-restricted (owner 2026-11-22, part C): seen by admins, project owners and members with
``sees_prices`` (and its author); for everybody else neither its text nor its existence reaches the thread, the unread
counters, the digest or a direct id."""
from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, message_reads
from motor_ai_sim.development import store as dev_store
from motor_ai_sim.development.routes import router as dev_router
from motor_ai_sim.routes.messages import router as messages_router

OWNER, OWNER2, ENGR, CUST, SEES, ADMIN, GENG = (f"{n}@example.test" for n in
                                                  ("owner", "owner2", "engr", "cust", "sees", "admin", "geng"))
OEMO = "oemowner@example.test"
ROLES = {OEMO: "oem", OWNER: "user", OWNER2: "user", ENGR: "user", CUST: "user", SEES: "user", ADMIN: "admin", GENG: "engineer"}
SECRET = "the contract price is 123 456 EUR"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / message_reads.SINCE_FILE).write_text(json.dumps({"since": "2000-01-01T00:00:00+00:00"}), encoding="utf-8")
    current = {"email": OWNER}
    monkeypatch.setattr(auth, "resolve_user", lambda token: {"email": current["email"], "role": ROLES[current["email"]]})
    monkeypatch.setattr(auth, "caller_identity", lambda token: {"id": current["email"]})
    from motor_ai_sim import users as _users
    monkeypatch.setattr(_users, "get_user", lambda e: {"email": e, "role": ROLES.get(e, "user")} if e in ROLES else None)
    app = FastAPI()
    for r in (dev_router, messages_router):
        app.include_router(r)
    client = TestClient(app, headers={"Authorization": "Bearer dev"})
    dev_store.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t",
                              "updated_at": "t", "owner_engineer": OWNER, "linked_motors": [], "notes": "", "pending_invites": [],
                              "members": [{"email": OWNER, "role": "owner"}, {"email": OWNER2, "role": "owner"},
                                          {"email": ENGR, "role": "engineer"}, {"email": CUST, "role": "customer"},
                                          {"email": SEES, "role": "customer", "sees_prices": True},
                                          {"email": GENG, "role": "engineer"}, {"email": OEMO, "role": "owner"}]})

    def as_user(email):
        current["email"] = email
        return client
    return as_user


def post(as_user, who, text="hello", **extra):
    return as_user(who).post("/api/projects/alpha/thread", json={"text": text, **extra})


def texts(as_user, who):
    r = as_user(who).get("/api/projects/alpha/thread")
    assert r.status_code == 200
    return [m["text"] for m in r.json()["messages"]]


def test_a_flagged_message_is_listed_only_for_who_sees_prices(env):
    assert post(env, OWNER, "open note").status_code == 200
    assert post(env, OWNER, SECRET, price_restricted=True).status_code == 200
    for who in (OWNER, OWNER2, SEES, ADMIN, GENG):                  # the staff engineer always (central rule, as for the quote)
        assert SECRET in texts(env, who), who
    for who in (ENGR, CUST):                                        # members without the switch
        got = texts(env, who)
        assert got == ["open note"], who
        assert SECRET not in json.dumps(env(who).get("/api/projects/alpha/thread").json()), who


def test_unread_counters_never_count_a_hidden_message(env):
    post(env, OWNER, SECRET, price_restricted=True)
    assert env(CUST).get("/api/projects/alpha/thread").json()["unread"] == 0
    assert env(SEES).get("/api/projects/alpha/thread").json()["unread"] == 1
    assert env(CUST).get("/api/messages/unread").json().get("projects", {}).get("alpha", 0) == 0
    assert env(SEES).get("/api/messages/unread").json().get("projects", {}).get("alpha", 0) == 1
    assert env(CUST).get("/api/projects").status_code == 200
    mine = [p for p in env(CUST).get("/api/projects").json().get("projects", env(CUST).get("/api/projects").json())
            if isinstance(p, dict) and p.get("id") == "alpha"]
    assert all(p.get("unread_messages", 0) == 0 for p in mine)


def test_a_hidden_id_is_not_found_not_forbidden(env):
    mid = post(env, OWNER, SECRET, price_restricted=True).json()["id"]
    r = env(CUST).post("/api/projects/alpha/thread/read", json={"up_to": mid})
    assert r.status_code == 404
    r = env(CUST).post(f"/api/projects/alpha/thread/{mid}/resolve", json={"resolved": True})
    assert r.status_code in (403, 404)                                 # a customer cannot resolve at all; never a 200 with the text
    r = env(OWNER2).post("/api/projects/alpha/thread/read", json={"up_to": mid})
    assert r.status_code == 200


def test_who_may_set_the_flag(env):
    assert post(env, CUST, "x", price_restricted=True).status_code == 403
    assert post(env, ENGR, "x", price_restricted=True).status_code == 403
    assert post(env, SEES, "x", price_restricted=True).status_code == 403
    assert post(env, OWNER2, "x", price_restricted=True).status_code == 200
    assert post(env, ADMIN, "x", price_restricted=True).status_code == 200
    assert post(env, GENG, "x", price_restricted=True).status_code == 200
    assert post(env, OEMO, "x", price_restricted=True).status_code == 403          # a factory owner never
    assert post(env, OWNER, "x", price_restricted="yes").status_code == 422


def test_a_revoked_author_no_longer_sees_the_own_message_nor_its_replies(env):
    mid = post(env, SEES, "plain from sees").json()["id"]
    r = post(env, OWNER, SECRET, price_restricted=True, reply_to=mid)
    assert r.status_code == 200
    own = post(env, SEES, SECRET + " by sees", reply_to=r.json()["id"]).json()["id"]      # SEES replies inside the restricted one
    assert SECRET in " ".join(texts(env, SEES))
    assert env(OWNER).patch("/api/projects/alpha/members/sees_prices", json={"email": SEES, "sees_prices": False}).status_code == 200
    body = json.dumps(env(SEES).get("/api/projects/alpha/thread").json())
    assert SECRET not in body and own not in body                                             # no author bypass, no descendants
    assert env(SEES).post("/api/projects/alpha/thread/read", json={"up_to": own}).status_code == 404
    assert env(SEES).post(f"/api/projects/alpha/thread/{own}/resolve", json={"resolved": True}).status_code == 404


def test_the_staff_engineer_sees_and_the_factory_account_never_does(env):
    post(env, OWNER, SECRET, price_restricted=True)
    assert SECRET in texts(env, GENG)
    assert SECRET not in json.dumps(env(OEMO).get("/api/projects/alpha/thread").json())          # an OEM owner: identity first
    assert env(OEMO).get("/api/projects/alpha/thread").json()["unread"] == 0


def test_a_visible_reply_never_names_a_hidden_parent(env):
    parent = post(env, OWNER, SECRET, price_restricted=True).json()["id"]
    child = post(env, OWNER, "tail", reply_to=parent).json()["id"]
    path = dev_store._file("alpha", "thread.jsonl")
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    for row in rows:
        if row["id"] == child:
            row.pop("price_restricted", None)                                   # a stored reply that lost its flag
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    data = json.dumps(env(CUST).get("/api/projects/alpha/thread").json())
    assert parent not in data and "tail" not in data                            # still hidden with its parent
    assert env(CUST).get("/api/projects/alpha/thread").json()["unread"] == 0


def test_resolve_of_a_hidden_message_is_404_like_an_unknown_id(env):
    mid = post(env, OWNER, SECRET, price_restricted=True).json()["id"]
    hidden = env(CUST).post(f"/api/projects/alpha/thread/{mid}/resolve", json={"resolved": True})
    unknown = env(CUST).post("/api/projects/alpha/thread/nope/resolve", json={"resolved": True})
    assert hidden.status_code == unknown.status_code == 404 and hidden.json() == unknown.json()


def test_the_mcp_unread_count_excludes_a_restricted_message(env):
    from motor_ai_sim.development import spec_order, unread
    post(env, OWNER, "public")
    post(env, OWNER, SECRET, price_restricted=True)
    project = dev_store.get_project("alpha")
    owner = {"email": OWNER, "role": "user"}
    assert len(unread.unread_ids("alpha", OWNER2, user=owner)) == 2                     # the web owner sees both
    assert len(unread.unread_ids("alpha", OWNER2, user=owner, agent=True)) == 1         # an agent acting for the owner: 1, not 2
    row = spec_order._row({"email": OWNER2, "role": "user"}, project, "en")
    assert row["unread_messages"] == 1


def _audit_rows(action):
    path = dev_store._file("alpha", "audit.jsonl")
    if not path.is_file():
        return []
    return [r for r in (json.loads(x) for x in path.read_text().splitlines()) if r.get("action") == action]


def test_a_failed_audit_append_leaves_the_post_recorded_once_on_retry(env, monkeypatch):
    real = dev_store.append_jsonl

    def flaky(project_id, name, row):
        if name == "audit.jsonl" and flaky.fail and row.get("action") == "post":
            raise OSError("disk full")
        return real(project_id, name, row)
    flaky.fail = True
    monkeypatch.setattr(dev_store, "append_jsonl", flaky)
    r = post(env, OWNER, SECRET, price_restricted=True)
    assert r.status_code == 200 and not _audit_rows("post")                             # the message stands, the row is pending
    assert SECRET in texts(env, OWNER)
    flaky.fail = False
    post(env, OWNER, "next")                                                            # the next call writes the pending row
    mine = [x for x in _audit_rows("post") if x["entity_id"] == r.json()["id"]]
    assert len(mine) == 1 and mine[0]["details"].get("price_restricted") is True
    post(env, OWNER, "again")
    assert len([x for x in _audit_rows("post") if x["entity_id"] == r.json()["id"]]) == 1


def test_a_failed_message_write_leaves_no_audit_row_even_when_another_request_flushes(env, monkeypatch):
    real = dev_store.append_jsonl

    def broken(project_id, name, row):
        if name == "thread.jsonl":
            raise OSError("no space")
        return real(project_id, name, row)
    monkeypatch.setattr(dev_store, "append_jsonl", broken)
    with pytest.raises(OSError):
        post(env, OWNER, "x")
    from motor_ai_sim.development.project_uploads import flush_events
    flush_events("alpha", "project.json")                                 # a concurrent flusher runs at any moment
    monkeypatch.setattr(dev_store, "append_jsonl", real)
    assert _audit_rows("post") == []                                      # nothing was stored, nothing was audited
    post(env, OWNER, "y")
    assert len(_audit_rows("post")) == 1


def test_a_reply_inside_a_restricted_message_is_restricted_too(env):
    parent = post(env, OWNER, SECRET, price_restricted=True).json()["id"]
    reply = post(env, SEES, "agreed", reply_to=parent)                  # no flag needed: it inherits
    assert reply.status_code == 200
    assert "agreed" in texts(env, OWNER) and "agreed" not in texts(env, CUST)
    assert post(env, CUST, "what?", reply_to=parent).status_code == 404   # a hidden parent does not exist for them
    plain = post(env, OWNER, "plain").json()["id"]
    assert post(env, CUST, "re plain", reply_to=plain).status_code == 200


def test_a_message_without_the_flag_is_stored_and_shown_as_before(env):
    mid = post(env, OWNER, "ordinary").json()["id"]
    rows = [json.loads(line) for line in dev_store._file("alpha", "thread.jsonl").read_text().splitlines()]
    assert "price_restricted" not in rows[0] and "reply_to" not in rows[0]
    assert env(CUST).get("/api/projects/alpha/thread").json()["messages"][0]["id"] == mid


def test_the_switch_changes_what_a_member_sees(env):
    post(env, OWNER, SECRET, price_restricted=True)
    assert SECRET not in texts(env, CUST)
    assert env(OWNER).patch("/api/projects/alpha/members/sees_prices", json={"email": CUST, "sees_prices": True}).status_code == 200
    assert SECRET in texts(env, CUST)


def _items(project_id="alpha"):
    from motor_ai_sim.digest import collect as C
    t = time.time()
    return C.collect_project(dev_store.get_project(project_id), t - 3600, t + 3600, results=lambda d, c: {})


def test_the_digest_line_needs_the_price_capability_of_every_recipient(env):
    from motor_ai_sim.digest import build as B
    post(env, OWNER, SECRET, price_restricted=True)
    post(env, OWNER, "open note")
    msg = {i.en: i for i in _items() if i.kind == "message"}
    restricted = next(i for i in msg.values() if SECRET in i.en)
    ordinary = next(i for i in msg.values() if "open note" in i.en)
    assert restricted.requires == frozenset({"prices"}) and not ordinary.requires
    group = B.project_group(dev_store.get_project("alpha"))
    rec = lambda e, role: {"email": e, "role": role}                     # noqa: E731
    owners = [rec(OWNER, "owner"), rec(OWNER2, "owner"), rec(SEES, "customer")]
    assert restricted.requires <= B.common_caps(group, owners)           # everybody on the list sees prices: the line may go
    with_cust = owners + [rec(CUST, "customer")]
    assert not restricted.requires <= B.common_caps(group, with_cust)    # one recipient without: no line for the whole mail
    assert ordinary.requires <= B.common_caps(group, with_cust)


def test_the_built_draft_has_no_trace_of_a_hidden_message(env):
    from motor_ai_sim.digest import build as B
    post(env, OWNER, SECRET, price_restricted=True)
    group = B.project_group(dev_store.get_project("alpha"))
    recipients = [{"email": CUST, "role": "customer", "name": "C", "lang": "en"}]
    t = time.time()
    draft = B.build_draft(group, {"mode": "daily"}, _items(), recipients, [], t - 3600, t)
    assert SECRET not in json.dumps(draft, default=str)
