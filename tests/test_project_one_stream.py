"""ONE message stream per project (owner 2026-11-24): an internal note is a message with a STAFF-ONLY mark.  Seen only by MOTRES staff
(the GLOBAL role admin / engineer); never by a customer-side member, a project engineer without the global role, a factory (OEM)
account, an MCP agent or an anonymous caller - through ANY route that returns messages or counts, the digest or the owner report.
Independent of the price mark: a message may carry both."""
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

OWNER, ENGR, CUST, SEES, ADMIN, GENG, OEMO, OUTSIDER = (f"{n}@example.test" for n in
                                                          ("owner", "engr", "cust", "sees", "admin", "geng", "oemowner", "outsider"))
GHOST, DEMOTED, OFF, OEMADMIN = (f"{n}@example.test" for n in ("ghost", "demoted", "off", "oemadmin"))
#: what the CALLER dict claims (the session role) ...
ROLES = {GHOST: "admin", DEMOTED: "engineer", OFF: "engineer", OEMADMIN: "admin", OEMO: "oem", OWNER: "user", ENGR: "user", CUST: "user", SEES: "user", ADMIN: "admin", GENG: "engineer", OUTSIDER: "user"}
SECRET = "internal: we may drop the margin to 4 percent"
PRICE = "the contract price is 123 456 EUR"
NO_ONE = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / message_reads.SINCE_FILE).write_text(json.dumps({"since": "2000-01-01T00:00:00+00:00"}), encoding="utf-8")
    current = {"email": OWNER}
    monkeypatch.setattr(auth, "resolve_user", lambda token: {"email": current["email"], "role": ROLES[current["email"]]} if current["email"] else None)
    monkeypatch.setattr(auth, "caller_identity", lambda token: {"id": current["email"]})
    from motor_ai_sim import users as _users
    #: ... and what the ACCOUNT says: no account at all (GHOST), a plain user (DEMOTED), a disabled one (OFF), a factory one (OEMADMIN)
    accounts = {**{e: r for e, r in ROLES.items() if e not in (GHOST, DEMOTED, OFF, OEMADMIN)}, DEMOTED: "user", OFF: "engineer", OEMADMIN: "oem"}
    monkeypatch.setattr(_users, "get_user", lambda e: ({"email": e, "role": accounts[e], "disabled": e == OFF}) if e in accounts else None)
    app = FastAPI()
    for r in (dev_router, messages_router):
        app.include_router(r)
    client = TestClient(app, headers={"Authorization": "Bearer dev"})
    dev_store.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t",
                              "updated_at": "t", "owner_engineer": GENG, "linked_motors": [], "notes": "", "pending_invites": [],
                              "members": [{"email": OWNER, "role": "owner"}, {"email": ENGR, "role": "engineer"},
                                          {"email": CUST, "role": "customer"}, {"email": SEES, "role": "customer", "sees_prices": True},
                                          {"email": GENG, "role": "engineer"}, {"email": OEMO, "role": "owner"},
                                          {"email": ADMIN, "role": "customer"}]})   # a staff account with a mere "customer" project role

    def as_user(email):
        current["email"] = email
        return client
    return as_user


def post(as_user, who, text="hello", **extra):
    return as_user(who).post("/api/projects/alpha/thread", json={"text": text, **extra})


def texts(as_user, who):
    r = as_user(who).get("/api/projects/alpha/thread")
    assert r.status_code == 200, (who, r.text)
    return [m["text"] for m in r.json()["messages"]]


STAFF = (ADMIN, GENG)
NOT_STAFF = (OWNER, ENGR, CUST, SEES, OEMO)


def test_only_global_staff_sees_an_internal_message_in_the_thread(env):
    assert post(env, ADMIN, SECRET, staff_only=True).status_code == 200
    post(env, OWNER, "public")
    for who in STAFF:
        assert SECRET in texts(env, who), who
    for who in NOT_STAFF:                                   # the project role alone never opens it, an owner and a project engineer included
        body = json.dumps(env(who).get("/api/projects/alpha/thread").json())
        assert SECRET not in body and "staff_only" not in body, who
        assert texts(env, who) == ["public"], who


def test_an_anonymous_caller_gets_nothing(env):
    post(env, ADMIN, SECRET, staff_only=True)
    r = env(NO_ONE).get("/api/projects/alpha/thread")
    assert r.status_code in (401, 403, 404) and SECRET not in r.text
    r = env(NO_ONE).post("/api/projects/alpha/thread/read", json={})
    assert r.status_code in (401, 403, 404) and SECRET not in r.text
    assert env(NO_ONE).get("/api/messages/unread").status_code in (401, 403)
    assert env(NO_ONE).get("/api/projects").status_code in (401, 403)


def test_who_may_post_an_internal_message(env):
    for who in (OWNER, ENGR, CUST, SEES, OEMO):
        assert post(env, who, "x", staff_only=True).status_code == 403, who
    assert post(env, GENG, "x", staff_only=True).status_code == 200
    assert post(env, ADMIN, "x", staff_only=True).status_code == 200
    assert post(env, ADMIN, "x", staff_only="yes").status_code == 422
    assert post(env, ADMIN, "x", staff_only=False).status_code == 200


def test_the_capability_flag_in_the_project_payload_follows_the_global_role(env):
    for who, expected in ((ADMIN, True), (GENG, True), (OWNER, False), (ENGR, False), (CUST, False), (OEMO, False)):
        r = env(who).get("/api/projects/alpha")
        assert r.status_code == 200 and r.json().get("can_post_internal") is expected, who


def test_counts_and_badges_never_include_an_internal_message(env):
    post(env, ADMIN, SECRET, staff_only=True)
    for who in NOT_STAFF:
        assert env(who).get("/api/projects/alpha/thread").json()["unread"] == 0, who
        assert env(who).get("/api/messages/unread").json()["projects"].get("alpha", 0) == 0, who
        listing = env(who).get("/api/projects").json()
        rows = listing["projects"] if isinstance(listing, dict) else listing
        assert all(p.get("unread_messages", 0) == 0 for p in rows if p.get("id") == "alpha"), who
        assert SECRET not in json.dumps(listing)
        assert SECRET not in json.dumps(env(who).get("/api/projects/alpha").json()), who
    assert env(GENG).get("/api/messages/unread").json()["projects"].get("alpha") == 1       # the other staff member: counted


def test_a_hidden_id_is_404_on_read_and_resolve_like_an_unknown_id(env):
    mid = post(env, ADMIN, SECRET, staff_only=True).json()["id"]
    for who in (CUST, ENGR, OWNER, OEMO):
        r = env(who).post("/api/projects/alpha/thread/read", json={"up_to": mid})
        u = env(who).post("/api/projects/alpha/thread/read", json={"up_to": "nope"})
        assert r.status_code == u.status_code == 404 and r.json() == u.json(), who
        r = env(who).post(f"/api/projects/alpha/thread/{mid}/resolve", json={"resolved": True})
        u = env(who).post("/api/projects/alpha/thread/nope/resolve", json={"resolved": True})
        assert r.status_code == u.status_code == 404 and r.json() == u.json(), who
    assert env(GENG).post("/api/projects/alpha/thread/read", json={"up_to": mid}).status_code == 200


def test_reply_chain_a_customer_cannot_reply_to_an_internal_message_and_a_staff_reply_stays_internal(env):
    root = post(env, ADMIN, SECRET, staff_only=True).json()["id"]
    assert post(env, CUST, "what?", reply_to=root).status_code == 404            # the parent is hidden: it does not exist for him
    assert post(env, OEMO, "what?", reply_to=root).status_code == 404
    reply = post(env, GENG, "agreed", reply_to=root)                              # no flag: it inherits
    assert reply.status_code == 200 and reply.json().get("staff_only") is True
    assert "agreed" in texts(env, ADMIN) and "agreed" not in texts(env, OWNER)
    public = post(env, OWNER, "question").json()["id"]
    internal_answer = post(env, ADMIN, "private view", reply_to=public, staff_only=True)   # a staff reply to a customer message may be internal
    assert internal_answer.status_code == 200
    assert "private view" in texts(env, GENG) and "private view" not in texts(env, CUST)
    assert post(env, CUST, "thanks", reply_to=public).status_code == 200            # the customer message itself stays open to all
    # a stored reply that lost its own mark is still hidden with its internal parent
    path = dev_store._file("alpha", "thread.jsonl")
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    for row in rows:
        if row["id"] == reply.json()["id"]:
            row.pop("staff_only", None)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert "agreed" not in texts(env, OWNER) and "agreed" in texts(env, ADMIN)


def test_staff_only_and_price_marks_are_independent_and_staff_passes_both(env):
    both = post(env, ADMIN, "both marks", staff_only=True, price_restricted=True)
    assert both.status_code == 200
    only_staff = post(env, ADMIN, "staff mark only", staff_only=True).json()["id"]
    only_price = post(env, ADMIN, PRICE, price_restricted=True).json()["id"]
    for who in STAFF:
        assert {"both marks", "staff mark only", PRICE} <= set(texts(env, who)), who
    assert texts(env, SEES) == [PRICE]                      # sees prices, but is not staff: the price one only
    assert texts(env, OWNER) == [PRICE]                     # a project owner sees prices: not the internal ones
    assert texts(env, CUST) == [] and texts(env, OEMO) == []
    assert only_staff and only_price


def test_the_mcp_unread_count_excludes_an_internal_message(env):
    from motor_ai_sim.development import spec_order, unread
    post(env, OWNER, "public")
    post(env, ADMIN, SECRET, staff_only=True)
    project = dev_store.get_project("alpha")
    staff = {"email": GENG, "role": "engineer"}
    assert len(unread.unread_ids("alpha", GENG, user=staff)) == 2                  # the staff web user sees both
    assert len(unread.unread_ids("alpha", GENG, user=staff, agent=True)) == 1      # an agent acting for staff: never
    assert len(unread.unread_ids("alpha", GENG, user=None)) == 1
    row = spec_order._row(staff, project, "en")
    assert row["unread_messages"] == 1


def test_an_agent_context_is_never_staff(env):
    from motor_ai_sim.development import access
    token = auth._AGENT_USER.set({"email": GENG})
    try:
        assert access.is_staff({"email": GENG, "role": "engineer"}) is False
        assert access.can_see_message({"email": GENG, "role": "engineer"}, {}, {"staff_only": True}) is False
    finally:
        auth._AGENT_USER.reset(token)
    assert access.is_staff({"email": GENG, "role": "engineer"}) is True
    assert access.is_staff(None) is False and access.is_staff({"email": OEMO, "role": "oem"}) is False
    assert access.is_staff_email(ADMIN) and access.is_staff_email(GENG)
    assert not access.is_staff_email(OWNER) and not access.is_staff_email("nobody@example.test") and not access.is_staff_email("")


def test_a_disabled_staff_account_is_not_staff_by_address(env, monkeypatch):
    from motor_ai_sim import users as _users
    from motor_ai_sim.development import access
    monkeypatch.setattr(_users, "get_user", lambda e: {"email": e, "role": "engineer", "disabled": True})
    assert access.is_staff_email(GENG) is False


def _items():
    from motor_ai_sim.digest import collect as C
    t = time.time()
    return C.collect_project(dev_store.get_project("alpha"), t - 3600, t + 3600, results=lambda d, c: {})


def test_the_digest_line_needs_staff_account_of_every_recipient(env):
    from motor_ai_sim.digest import build as B
    post(env, ADMIN, SECRET, staff_only=True)
    post(env, OWNER, "open note")
    both = post(env, ADMIN, "both marks", staff_only=True, price_restricted=True).json()["id"]
    reply = post(env, GENG, "inherits", reply_to=both).json()["id"]
    by_key = {i.key: i for i in _items() if i.kind == "message"}
    internal = next(i for i in by_key.values() if SECRET in i.en)
    ordinary = next(i for i in by_key.values() if "open note" in i.en)
    assert internal.requires == frozenset({"staff"}) and not ordinary.requires
    assert by_key[f"msg:{both}"].requires == frozenset({"staff", "prices"})
    assert by_key[f"msg:{reply}"].requires == frozenset({"staff", "prices"})          # a reply to a hidden message is hidden
    group = B.project_group(dev_store.get_project("alpha"))
    rec = lambda e, role: {"email": e, "role": role}                                  # noqa: E731
    staff_list = [rec(ADMIN, "customer"), rec(GENG, "engineer")]       # staff by account, whatever the project role
    assert {"staff", "prices"} <= B.common_caps(group, staff_list)
    # a project owner and a project engineer WITHOUT the global role are not staff, whatever their project role
    for lone in (rec(OWNER, "owner"), rec(ENGR, "engineer"), rec(CUST, "customer"), rec(OEMO, "owner"), rec("stranger@example.test", "customer")):
        assert "staff" not in group.caps(lone["role"], lone["email"]), lone
        assert not internal.requires <= B.common_caps(group, staff_list + [lone]), lone      # intersection over every recipient
    assert ordinary.requires <= B.common_caps(group, staff_list + [rec(CUST, "customer")])


def test_the_built_draft_has_no_trace_of_an_internal_message(env):
    from motor_ai_sim.digest import build as B
    post(env, ADMIN, SECRET, staff_only=True)
    group = B.project_group(dev_store.get_project("alpha"))
    t = time.time()
    for who, role in ((OWNER, "owner"), (ENGR, "engineer"), (CUST, "customer")):
        recipients = [{"email": who, "role": role, "name": "N", "lang": "en"}]
        draft = B.build_draft(group, {"mode": "daily"}, _items(), recipients, [], t - 3600, t)
        assert SECRET not in json.dumps(draft, default=str), who
    staff = [{"email": GENG, "role": "engineer", "name": "N", "lang": "en"}]
    assert "more_requires" in B.build_draft(group, {"mode": "daily"}, _items(), staff, [], t - 3600, t)


def test_the_notification_recipients_skip_non_staff_for_an_internal_message(env):
    from motor_ai_sim.development import routes
    project = dev_store.get_project("alpha")
    assert routes.notification_recipients(project, {"staff_only": True}) == [GENG, ADMIN]
    assert OWNER in routes.notification_recipients(project, {})


def test_the_owner_report_labels_an_internal_message_and_never_calls_it_customer(env, monkeypatch):
    from motor_ai_sim.owner_report import send, sources
    monkeypatch.setattr(send, "recipients", lambda: [ADMIN])
    post(env, ADMIN, SECRET, staff_only=True)
    post(env, CUST, "customer question")
    t = time.time()
    blocks = sources._fmt_projects(t - 3600, t + 3600)
    lines = [ln for b in blocks for ln in b.lines]
    internal = next(ln for ln in lines if SECRET in ln.text)
    assert internal.side == "staff" and internal.text.startswith("[internal]")
    assert next(ln for ln in lines if "customer question" in ln.text).side == "customer"


def test_the_audit_intent_records_the_mark_without_the_text(env):
    mid = post(env, ADMIN, SECRET, staff_only=True).json()["id"]
    rows = [json.loads(x) for x in dev_store._file("alpha", "audit.jsonl").read_text().splitlines()]
    mine = [r for r in rows if r.get("entity_id") == mid]
    assert len(mine) == 1 and mine[0]["details"].get("staff_only") is True and SECRET not in json.dumps(mine[0])


# -- the migration of the legacy "Internal notes" --------------------------------------------------------------------------------

def _set_notes(text):
    dev_store.mutate_project("alpha", lambda d: d.update({"notes": text}))


def test_migration_dry_run_counts_and_writes_nothing(env):
    from motor_ai_sim.development import notes_migration as M
    _set_notes("call the supplier first")
    before = dev_store._file("alpha", "thread.jsonl").is_file()
    assert M.plan() == [{"project": "alpha", "state": "migrate", "chars": len("call the supplier first")}]
    assert dev_store._file("alpha", "thread.jsonl").is_file() == before
    assert not dev_store._file("alpha", M.BACKUP).exists()


def test_migration_twice_is_once_keeps_the_backup_and_hides_from_customers(env):
    from motor_ai_sim.development import notes_migration as M
    _set_notes("call the supplier first")
    assert M.apply()[0]["state"] == "migrated"
    assert M.apply()[0]["state"] == "done" and M.plan()[0]["state"] == "done"
    rows = [json.loads(x) for x in dev_store._file("alpha", "thread.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["id"] == M.message_id("alpha") and rows[0]["staff_only"] is True
    assert rows[0]["text"] == "call the supplier first" and rows[0]["author_email"] == GENG and rows[0]["created_at"] == "t"
    audit = [json.loads(x) for x in dev_store._file("alpha", "audit.jsonl").read_text().splitlines() if '"post"' in x]
    assert len(audit) == 1 and audit[0]["entity_id"] == M.message_id("alpha")        # the same durable path: one audit row
    backup = json.loads(dev_store._file("alpha", M.BACKUP).read_text())
    assert backup["text"] == "call the supplier first"
    assert dev_store.get_project("alpha")["notes"] == "call the supplier first"      # the original store is untouched
    assert "call the supplier first" in texts(env, ADMIN)
    for who in NOT_STAFF:
        assert texts(env, who) == [] and env(who).get("/api/projects/alpha/thread").json()["unread"] == 0, who
    # a later edit of the old field does not overwrite the first backup, and a re-run does not duplicate
    _set_notes("changed later")
    M.apply()
    assert json.loads(dev_store._file("alpha", M.BACKUP).read_text())["text"] == "call the supplier first"
    assert len(dev_store._file("alpha", "thread.jsonl").read_text().splitlines()) == 1


def test_migration_skips_empty_notes_and_sends_nothing(env, monkeypatch):
    from motor_ai_sim.development import notes_migration as M, routes
    called = []
    monkeypatch.setattr(routes, "notify_project_members", lambda *a, **k: called.append(a))
    assert M.apply() == [{"project": "alpha", "state": "empty", "chars": 0}]
    assert not dev_store._file("alpha", "thread.jsonl").is_file() and called == []
    _set_notes("x")
    M.apply()
    assert called == []


# -- one account resolution for read AND create (Astra P1 a) --------------------------------------------------------------------------

LYING = (GHOST, DEMOTED, OFF, OEMADMIN)


def test_a_caller_that_only_claims_staff_is_not_staff_for_read_create_notes_and_by_address(env):
    from motor_ai_sim.development import access
    post(env, ADMIN, SECRET, staff_only=True)
    for who in LYING:
        assert not access.is_staff({"email": who, "role": ROLES[who]}), who
        assert not access.is_staff_email(who), who
        r = env(who).get("/api/projects/alpha/thread")
        assert SECRET not in r.text, who                                     # read
        assert post(env, who, "x", staff_only=True).status_code == 403, who   # create
        assert env(who).get("/api/projects/alpha").json().get("can_post_internal") is False, who
        assert "notes" not in env(who).get("/api/projects/alpha").json(), who  # the legacy notes read path: same function
        assert env(who).get("/api/messages/unread").json()["projects"].get("alpha", 0) in (0, 1)
    assert access.is_staff({"email": ADMIN, "role": "admin"})


def test_the_legacy_notes_patch_follows_the_same_staff_rule(env):
    assert env(OEMADMIN).patch("/api/projects/alpha", json={"notes": "x"}).status_code in (403, 404)
    assert env(ADMIN).patch("/api/projects/alpha", json={"notes": "x"}).status_code == 200


# -- hidden ids and demotion (Astra 2) -------------------------------------------------------------------------------------------------

def test_after_a_demotion_the_hidden_read_cursor_id_does_not_leak_or_break_the_counts(env, monkeypatch):
    from motor_ai_sim import users as _users
    post(env, OWNER, "public")
    mid = post(env, GENG, SECRET, staff_only=True).json()["id"]
    assert env(GENG).post("/api/projects/alpha/thread/read", json={"up_to": mid}).status_code == 200
    real = _users.get_user
    monkeypatch.setattr(_users, "get_user", lambda e: {**real(e), "role": "user"} if e == GENG else real(e))
    monkeypatch.setattr(auth, "resolve_user", lambda token: {"email": GENG, "role": "user"})
    r = env(GENG).get("/api/projects/alpha/thread")
    assert r.status_code == 200 and SECRET not in r.text and mid not in r.text
    assert env(GENG).post("/api/projects/alpha/thread/read", json={"up_to": mid}).status_code == 404
    assert env(GENG).post("/api/projects/alpha/thread/read", json={}).status_code == 200      # no up_to: newest VISIBLE
    assert env(GENG).get("/api/messages/unread").json()["total_projects"] == 0


def test_a_resolution_and_a_reply_reference_never_name_a_hidden_message(env):
    mid = post(env, ADMIN, SECRET, staff_only=True).json()["id"]
    assert env(ADMIN).post(f"/api/projects/alpha/thread/{mid}/resolve", json={"resolved": True}).status_code == 200
    reply = post(env, ADMIN, "inner", reply_to=mid).json()["id"]
    for who in (OWNER, ENGR, CUST, OEMO):
        body = env(who).get("/api/projects/alpha/thread").text
        assert mid not in body and reply not in body and "resolved_at" not in body, who


# -- the owner report goes only to enabled staff accounts (Astra P1 b) ---------------------------------------------------------------

def _report_lines():
    from motor_ai_sim.owner_report import sources
    t = time.time()
    return [ln for b in sources._fmt_projects(t - 3600, t + 3600) for ln in b.lines]


def test_the_owner_report_shows_internal_lines_only_when_every_recipient_is_enabled_staff(env, monkeypatch):
    from motor_ai_sim.owner_report import send
    mid = post(env, ADMIN, SECRET, staff_only=True).json()["id"]
    env(ADMIN).post(f"/api/projects/alpha/thread/{mid}/resolve", json={"resolved": True})
    post(env, CUST, "customer question")
    for recipients, shown in (([ADMIN], True), ([ADMIN, GENG], True), ([ADMIN, OWNER], False), ([GHOST], False), ([OFF], False),
                              ([OEMADMIN], False), ([ADMIN, "nobody@example.test"], False)):
        monkeypatch.setattr(send, "recipients", lambda r=recipients: list(r))
        lines = _report_lines()
        text = " ".join(ln.text for ln in lines)
        assert (SECRET in text) is shown, recipients
        assert ("customer question" in text), recipients
        if not shown:
            assert mid not in text and not any("thread" in ln.text and "reopened" in ln.text for ln in lines), recipients


# -- the migration survives a crash and a concurrent run (Astra P2 f) ----------------------------------------------------------------

def test_migration_crash_between_append_and_flush_is_settled_on_retry(env, monkeypatch):
    from motor_ai_sim.development import notes_migration as M, thread_audit
    _set_notes("n1")
    with monkeypatch.context() as crash:
        crash.setattr(thread_audit, "flush", lambda pid: (_ for _ in ()).throw(RuntimeError("crash")))
        with pytest.raises(RuntimeError):
            M.apply()
    assert M.plan()[0]["state"] == "done"                                        # appended, intent still pending in the record
    audit = dev_store._file("alpha", "audit.jsonl")
    assert not (audit.is_file() and M.message_id("alpha") in audit.read_text())      # the audit row is still pending
    M.apply()                                                                    # the retry flushes the existing intent
    rows = [x for x in dev_store._file("alpha", "audit.jsonl").read_text().splitlines() if M.message_id("alpha") in x]
    assert len(rows) == 1
    M.apply()
    assert len([x for x in dev_store._file("alpha", "audit.jsonl").read_text().splitlines() if M.message_id("alpha") in x]) == 1
    assert len(dev_store._file("alpha", "thread.jsonl").read_text().splitlines()) == 1


def test_two_concurrent_migrations_append_one_message(env):
    import threading
    from motor_ai_sim.development import notes_migration as M
    _set_notes("n2")
    barrier = threading.Barrier(4)
    errors = []

    def worker():
        try:
            barrier.wait()
            M.apply()
        except Exception as exc:                                                # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(dev_store._file("alpha", "thread.jsonl").read_text().splitlines()) == 1
