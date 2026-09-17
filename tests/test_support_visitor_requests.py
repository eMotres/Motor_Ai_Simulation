"""A visitor's words reach the team — the log, the marker, the inbox, the push.

The owner asked it plainly on 2026-09-17: *"а как сообщения, которые они пишут
боту, будут доходить до нас? Ему как-то надо объяснить, что делать и в каком
случае"*.  Three mechanisms answer it, and this file holds each of them to its
promise:

1. **Every visitor turn is kept** — ``support_store`` appends it to the day's
   JSONL beside ``users.json``, including the turns the rate limiter refused.  A
   SIGNED-IN user's chat is never written: that is the line between a doorbell
   and surveillance, and it is asserted here, not just documented.
2. **The marker is a contract** — the provider call has no tool API, so the
   assistant ends a reply with ``[[ACCESS_REQUEST: …]]`` and the backend parses
   it.  The marker must NEVER reach the visitor (parsed, malformed or tripled),
   and a request without a valid e-mail must never be filed: an address nobody
   can answer is the thing that teaches an inbox to be ignored.
3. **The inbox is admin-only** — 401 for an anonymous caller, 403 for a
   signed-in non-admin, exactly like every other admin route (the patterns come
   from tests/test_public_exhibit_off.py and tests/test_support_chat_limits.py).

And the alert itself: port 587 is open on the host even though 25/465 are not,
so a filed request is e-mailed over STARTTLS with an app password — on a daemon
thread, because a visitor must never wait on a TLS handshake.

No test here reaches a provider, a mail server or the network: ``_effective`` is
stubbed, ``smtplib.SMTP`` is a recorder, and the stores live in tmp_path.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import notify
from motor_ai_sim import support_store as S
from motor_ai_sim.api import app
from motor_ai_sim.routes import support

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"

ADMIN = "owner@example.com"
CLIENT = "client@example.com"

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _real_users_untouched():
    before = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    yield
    after = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    assert before == after, "config/users.json was modified by a test"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A throwaway registry + a throwaway support store, the tier gate enforcing.

    ``ADMIN_EMAILS`` must be non-empty or ``_is_admin_caller`` treats every
    caller as the local-dev admin and nothing anonymous exists to test.
    """
    from motor_ai_sim import auth
    from motor_ai_sim import users as U

    users_file = tmp_path / "users.json"
    shutil.copy2(_REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    monkeypatch.delenv("PUBLIC_EXHIBIT", raising=False)
    monkeypatch.setenv(S._ENV_ROOT, str(tmp_path / "support"))
    for var in support._CAP_ENV.values():
        monkeypatch.delenv(var, raising=False)
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_USER",
                "SMTP_PASSWORD", "SMTP_HOST", "SMTP_PORT", "NOTIFY_FROM",
                "NOTIFY_TO"):
        monkeypatch.delenv(var, raising=False)
    notify.reset_digest_state(time.strftime("%Y-%m-%d", time.localtime()))

    monkeypatch.setattr(support, "_load_overrides", lambda: {})
    monkeypatch.setattr(support, "_effective", lambda: {
        "provider": "none",
        "gemini": {"key": "", "model": "m", "key_source": "none"},
        "anthropic": {"key": "", "model": "m", "key_source": "none"},
    })
    support.reset_limits()

    U.create_user(ADMIN, "password-admin", tier="admin", name="Owner")
    U.create_user(CLIENT, "password-client", tier="free", name="Client")
    yield {
        "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
        "client": {"Authorization": f"Bearer {U.issue_token(CLIENT)}"},
        "tmp": tmp_path,
    }
    support.reset_limits()
    notify.reset_digest_state()


@pytest.fixture()
def answers(env, monkeypatch):
    """The provider, replaced by a script of replies the test chooses."""
    script = {"reply": "Access is by invitation — write to vadim@motresres.com."}
    monkeypatch.setattr(support, "_effective", lambda: {
        "provider": "gemini",
        "gemini": {"key": "k", "model": "gemini-test", "key_source": "env"},
        "anthropic": {"key": "", "model": "m", "key_source": "none"},
    })
    monkeypatch.setattr(support, "_gemini_reply",
                        lambda msgs, key, model, sp: script["reply"])
    return script


def ask(text="How can I get access?", *, ip="203.0.113.7", headers=None,
        messages=None):
    h = {"X-Forwarded-For": ip, "User-Agent": "Mozilla/5.0 (Test Visitor)"} if ip \
        else {"User-Agent": "Mozilla/5.0 (Test Visitor)"}
    h.update(headers or {})
    body = {"messages": messages if messages is not None
            else [{"role": "user", "content": text}]}
    return client.post("/api/support/chat", json=body, headers=h)


MARKER = ('[[ACCESS_REQUEST: name="Jane Doe"; company="ACME Robotics"; '
          'email="jane@acme.com"; note="40 mm joint motor, needs a quote"]]')


# ── 1. the marker is parsed, and never reaches the visitor ───────────────────

def test_a_complete_marker_is_parsed_and_stripped():
    reply = ("Thanks Jane — I've passed this to the team; you'll hear from "
             "vadim@motresres.com.\n\n" + MARKER)
    clean, req = support.parse_access_request(reply)
    assert req == {"name": "Jane Doe", "company": "ACME Robotics",
                   "email": "jane@acme.com",
                   "note": "40 mm joint motor, needs a quote"}
    assert "ACCESS_REQUEST" not in clean and "[[" not in clean
    assert clean.endswith("vadim@motresres.com.")


def test_a_reply_without_a_marker_is_untouched():
    reply = "AeroStator Core is an engineering portal for PM motor design."
    clean, req = support.parse_access_request(reply)
    assert req is None and clean == reply


def test_a_malformed_marker_files_nothing_and_still_disappears():
    """Three ways to get it wrong, and none of them may show the visitor a
    bracketed blob or file a row nobody can answer."""
    for bad in (
        '[[ACCESS_REQUEST: name="Jane"]]',                      # no e-mail
        '[[ACCESS_REQUEST: email="not-an-email"]]',             # not an address
        '[[ACCESS_REQUEST: email="jane@acme"]]',                # no TLD
        '[[ACCESS_REQUEST: email="jane at acme dot com"]]',     # a sentence
        '[[ACCESS_REQUEST: email="a b@acme.com"]]',             # a space in it
        '[[ACCESS_REQUEST: name="Jane"; email="jane@acme.com"',  # never closed
    ):
        clean, req = support.parse_access_request("Sure, one moment.\n\n" + bad)
        assert req is None, bad
        assert "ACCESS_REQUEST" not in clean, bad
        assert clean == "Sure, one moment."


def test_several_markers_the_last_valid_one_wins_and_all_are_stripped():
    reply = ("ok\n" + '[[ACCESS_REQUEST: email="first@acme.com"]]' + "\nmore\n"
             + '[[ACCESS_REQUEST: email="oops"]]' + "\n"
             + '[[ACCESS_REQUEST: name="Jane"; email="last@acme.com"]]')
    clean, req = support.parse_access_request(reply)
    assert req is not None and req["email"] == "last@acme.com"
    assert "ACCESS_REQUEST" not in clean
    assert clean.splitlines() == ["ok", "more"]


def test_the_marker_survives_the_model_quoting_it_loosely():
    """A model is not a parser generator: single quotes and bare values parse,
    unknown keys are ignored rather than fatal."""
    clean, req = support.parse_access_request(
        "[[ACCESS_REQUEST: name='Jane Doe'; company=ACME; "
        "email=jane@acme.com; phone=\"+386 1 234\"; note='robot joint']]")
    assert req == {"name": "Jane Doe", "company": "ACME",
                   "email": "jane@acme.com", "note": "robot joint"}
    assert clean == ""


def test_the_email_validator_is_strict_about_the_one_field_that_matters():
    for good in ("test-visitor@example.com", "a.b+c@sub.domain.co.uk",
                 "JANE@ACME.COM"):
        assert support.valid_email(good) is True, good
    for bad in ("", "  ", "jane@acme", "jane.acme.com", "jane@@acme.com",
                "jane@acme .com", "I'll send it later", "a@b.c" * 60):
        assert support.valid_email(bad) is False, bad


def test_a_reply_that_is_only_a_marker_still_says_something():
    """The bubble must never be empty: the visitor gave their address and has to
    be told it arrived somewhere."""
    clean, req = support.parse_access_request(MARKER)
    assert req is not None and clean == ""
    # …the route substitutes the confirmation (asserted end-to-end below)
    assert "vadim@motresres.com" in support.ACCESS_CONFIRMATION


# ── 2. the visitor log ───────────────────────────────────────────────────────

def test_every_visitor_turn_is_logged_under_the_admin_root(env, answers):
    assert ask("what is this?").status_code == 200
    assert ask("and who is it for?").status_code == 200
    day_files = list((env["tmp"] / "support" / "visitor_chats").glob("*.jsonl"))
    assert len(day_files) == 1, day_files
    rows = [json.loads(line) for line in
            day_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert rows[0]["user"] == "what is this?"
    assert rows[0]["reply"] == answers["reply"]
    assert rows[0]["source"] == "gemini" and rows[0]["model"] == "gemini-test"
    assert rows[0]["ua"].startswith("Mozilla/5.0")
    # the address itself is NEVER stored — only a salted hash of it
    blob = day_files[0].read_text(encoding="utf-8")
    assert "203.0.113.7" not in blob
    assert len(rows[0]["ip_hash"]) == 12


def test_a_signed_in_chat_is_not_logged(env, answers):
    assert ask("what is this?", headers=env["client"]).status_code == 200
    assert ask("and this?", headers=env["admin"]).status_code == 200
    assert not list((env["tmp"] / "support" / "visitor_chats").glob("*.jsonl"))


def test_a_refused_turn_is_logged_with_the_cap_it_hit(env, answers, monkeypatch):
    monkeypatch.setenv("SUPPORT_ANON_BURST_MAX", "1")
    assert ask(ip="198.51.100.9").status_code == 200
    r = ask(ip="198.51.100.9")
    assert r.status_code == 429 and r.json()["limit"] == "ip_burst"
    rows = S.read_day()
    assert [x["limit"] for x in rows] == [None, "ip_burst"]
    assert rows[-1]["source"] == "rate_limited"


def test_turns_of_one_visit_share_a_conversation_id(env, answers):
    first = [{"role": "user", "content": "what is this?"}]
    ask(messages=first)
    ask(messages=first + [{"role": "assistant", "content": "a portal"},
                          {"role": "user", "content": "and the price?"}])
    ask(messages=[{"role": "user", "content": "unrelated first question"}])
    convs = S.conversations()
    assert convs["count"] == 2
    turns = {c["conv"]: len(c["turns"]) for c in convs["conversations"]}
    assert sorted(turns.values()) == [1, 2]
    assert convs["day"] == time.strftime("%Y-%m-%d", time.localtime())
    assert convs["days"] == [convs["day"]]


def test_two_visitors_do_not_share_a_conversation(env, answers):
    same = [{"role": "user", "content": "what is this?"}]
    ask(messages=same, ip="203.0.113.21")
    ask(messages=same, ip="203.0.113.22")
    assert S.conversations()["count"] == 2


def test_a_day_log_stops_growing_at_its_cap(env, monkeypatch):
    monkeypatch.setattr(S, "DAY_MAX_BYTES", 400)
    for i in range(50):
        S.log_visitor_turn(ip="203.0.113.7", user_message=f"q{i}", reply="r")
    path = S.chats_dir() / f"{time.strftime('%Y-%m-%d', time.localtime())}.jsonl"
    assert path.stat().st_size < 1200, "the cap did not hold"
    assert 0 < len(S.read_day()) < 50


def test_logs_older_than_the_retention_window_are_pruned(env):
    S.chats_dir().mkdir(parents=True, exist_ok=True)
    old = time.strftime("%Y-%m-%d",
                        time.localtime(time.time() - (S.RETENTION_DAYS + 5) * 86400))
    recent = time.strftime("%Y-%m-%d", time.localtime(time.time() - 3 * 86400))
    for d in (old, recent):
        (S.chats_dir() / f"{d}.jsonl").write_text(
            json.dumps({"ts": 1, "user": "x"}) + "\n", encoding="utf-8")
    S._prune(force=True)
    assert not (S.chats_dir() / f"{old}.jsonl").exists()
    assert (S.chats_dir() / f"{recent}.jsonl").exists()


def test_the_store_never_raises_when_the_root_is_unusable(env, monkeypatch):
    """A doorbell that takes the door down is worse than no doorbell."""
    monkeypatch.setenv(S._ENV_ROOT, str(env["tmp"] / "support" / "nope.jsonl"))
    (env["tmp"] / "support").mkdir(parents=True, exist_ok=True)
    (env["tmp"] / "support" / "nope.jsonl").write_text("not a directory")
    S.log_visitor_turn(ip="203.0.113.7", user_message="q", reply="r")
    assert S.read_day() == [] and S.list_access_requests() == []


# ── 3. the inbox ─────────────────────────────────────────────────────────────

def test_a_request_is_filed_end_to_end_and_the_marker_is_invisible(env, answers):
    answers["reply"] = ("Thanks Jane — I've passed this to the team; you'll hear "
                        "from vadim@motresres.com.\n\n" + MARKER)
    r = ask("I'd like access, I'm Jane from ACME, jane@acme.com")
    assert r.status_code == 200
    body = r.json()
    assert "ACCESS_REQUEST" not in body["reply"] and "[[" not in body["reply"]
    assert "vadim@motresres.com" in body["reply"]

    rows = S.list_access_requests()
    assert len(rows) == 1
    rec = rows[0]
    assert rec["email"] == "jane@acme.com" and rec["company"] == "ACME Robotics"
    assert rec["name"] == "Jane Doe" and rec["status"] == "new"
    assert rec["note"].startswith("40 mm joint")
    assert len(rec["ip_hash"]) == 12
    # the conversation travels with the request — the team reads what was said
    assert [t["role"] for t in rec["transcript"]] == ["user", "assistant"]
    assert "jane@acme.com" in rec["transcript"][0]["content"]
    assert "ACCESS_REQUEST" not in rec["transcript"][1]["content"]


def test_a_marker_only_reply_becomes_the_confirmation(env, answers):
    answers["reply"] = MARKER
    body = ask("jane@acme.com").json()
    assert body["reply"] == support.ACCESS_CONFIRMATION
    assert len(S.list_access_requests()) == 1


def test_a_signed_in_reply_is_not_scanned_for_a_marker(env, answers):
    """The contract is only ever given to a visitor; a signed-in user pasting
    the line must not be able to file anything."""
    answers["reply"] = "here you go\n" + MARKER
    body = ask("what?", headers=env["client"]).json()
    assert "ACCESS_REQUEST" in body["reply"]
    assert S.list_access_requests() == []


def test_two_requests_from_one_address_inside_a_day_merge(env):
    first = S.file_access_request(email="jane@acme.com", name="Jane",
                                  transcript=[{"role": "user", "content": "hi"}],
                                  ip="203.0.113.7")
    second = S.file_access_request(email="JANE@acme.com", company="ACME",
                                   note="robot joint",
                                   transcript=[{"role": "user", "content": "hi"},
                                               {"role": "assistant", "content": "ok"}],
                                   ip="203.0.113.7")
    rows = S.list_access_requests()
    assert len(rows) == 1, "a second message from the same person made a second row"
    assert first["id"] == second["id"]
    rec = rows[0]
    assert rec["merged"] == 2
    assert rec["name"] == "Jane", "a later turn erased a field it did not carry"
    assert rec["company"] == "ACME" and rec["note"] == "robot joint"
    assert len(rec["transcript"]) == 2


def test_an_older_request_from_the_same_address_does_not_merge(env):
    S.file_access_request(email="jane@acme.com", name="Jane")
    doc = json.loads(S.requests_file().read_text(encoding="utf-8"))
    for rec in doc.values():
        rec["ts"] = rec["updated"] = time.time() - S.MERGE_WINDOW_S - 60
    S.requests_file().write_text(json.dumps(doc), encoding="utf-8")
    S.file_access_request(email="jane@acme.com", name="Jane")
    assert len(S.list_access_requests()) == 2


def test_a_request_without_an_email_is_never_stored(env):
    assert S.file_access_request(email="") is None
    assert S.list_access_requests() == []


# ── 4. the admin routes ──────────────────────────────────────────────────────

INBOX = "/api/admin/support/requests"
CHATS = "/api/admin/support/visitor_chats"


def test_the_inbox_is_closed_to_an_anonymous_caller(env, monkeypatch):
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    for method, path in (("GET", INBOX), ("GET", CHATS),
                         ("PATCH", INBOX + "/req_x"),
                         ("DELETE", INBOX + "/req_x")):
        r = client.request(method, path, json={"status": "contacted"})
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_the_inbox_is_closed_to_a_signed_in_non_admin(env):
    for method, path in (("GET", INBOX), ("GET", CHATS),
                         ("PATCH", INBOX + "/req_x"),
                         ("DELETE", INBOX + "/req_x")):
        r = client.request(method, path, headers=env["client"],
                           json={"status": "contacted"})
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"


def test_the_inbox_is_not_on_the_anonymous_allowlist():
    from motor_ai_sim.auth import anonymous_allowed
    for path in (INBOX, CHATS, INBOX + "/req_x"):
        assert anonymous_allowed(path) is False, path


def test_the_admin_reads_the_inbox_and_moves_a_status(env, answers):
    answers["reply"] = "noted\n" + MARKER
    ask("I'm Jane, jane@acme.com")

    r = client.get(INBOX, headers=env["admin"])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["count"] == 1 and j["new"] == 1
    rid = j["requests"][0]["id"]

    r = client.patch(f"{INBOX}/{rid}", headers=env["admin"],
                     json={"status": "invited"})
    assert r.status_code == 200 and r.json()["request"]["status"] == "invited"
    assert client.get(INBOX, headers=env["admin"]).json()["new"] == 0

    bad = client.patch(f"{INBOX}/{rid}", headers=env["admin"],
                       json={"status": "spam"})
    assert bad.status_code == 422
    missing = client.patch(f"{INBOX}/req_nope", headers=env["admin"],
                           json={"status": "invited"})
    assert missing.status_code == 404


def test_the_admin_deletes_a_request(env):
    rec = S.file_access_request(email="test-visitor@example.com", name="Test")
    r = client.delete(f"{INBOX}/{rec['id']}", headers=env["admin"])
    assert r.status_code == 200 and r.json()["ok"] is True
    assert S.list_access_requests() == []
    assert client.delete(f"{INBOX}/{rec['id']}",
                         headers=env["admin"]).status_code == 404


def test_the_admin_reads_a_day_of_visitor_chats(env, answers):
    ask("what is this?")
    r = client.get(CHATS, headers=env["admin"])
    assert r.status_code == 200
    j = r.json()
    today = time.strftime("%Y-%m-%d", time.localtime())
    assert j["day"] == today and j["days"] == [today] and j["count"] == 1
    conv = j["conversations"][0]
    assert conv["turns"][0]["user"] == "what is this?"
    assert conv["ip_hash"] and "203.0.113.7" not in json.dumps(j)
    # an empty day answers the same shape rather than 404
    empty = client.get(f"{CHATS}?day=2001-01-01", headers=env["admin"]).json()
    assert empty["conversations"] == [] and empty["day"] == "2001-01-01"


# ── 5. the notification ──────────────────────────────────────────────────────
# Port 25 and 465 are blocked on the host; 587 (submission, STARTTLS) is open
# and motresres.com is on Google Workspace, so the alert is plain smtplib with
# an app password.  Nothing here opens a socket: smtplib.SMTP is a recorder.


class _FakeSMTP:
    """Everything ``_send_email`` is allowed to call, and a record of it."""

    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list = []
        self.sent: list = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.calls.append("close")
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture()
def smtp(env, monkeypatch):
    """SMTP configured, and every send recorded instead of made."""
    monkeypatch.setenv("SMTP_USER", "vadim@motresres.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password-16ch")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.delenv("NOTIFY_FROM", raising=False)
    monkeypatch.delenv("NOTIFY_TO", raising=False)
    _FakeSMTP.instances = []
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    return _FakeSMTP.instances


def test_smtp_unconfigured_does_nothing_and_says_so(env, caplog, monkeypatch):
    import logging
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    _FakeSMTP.instances = []
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    caplog.set_level(logging.INFO, logger="motor_ai_sim.notify")
    assert notify.smtp_configured() is False and notify.configured() is False
    assert notify.send("subject", "body", block=True) is False
    assert _FakeSMTP.instances == [], "an unconfigured notifier opened a socket"
    assert any("smtp not configured" in r.getMessage() for r in caplog.records)


def test_one_mail_per_access_request(env, smtp):
    rec = S.file_access_request(
        email="jane@acme.com", name="Jane Doe", company="ACME Robotics",
        note="40 mm robot joint, needs a quote",
        transcript=[{"role": "user", "content": "I need a joint motor"},
                    {"role": "assistant", "content": "Happy to pass this on."}])
    assert notify.access_request(rec, block=True) is True
    assert len(smtp) == 1
    s = smtp[0]
    # the submission handshake, in order
    assert s.host == "smtp.gmail.com" and s.port == 587
    assert s.timeout == notify.SMTP_TIMEOUT_S
    assert "starttls" in s.calls
    assert ("login", "vadim@motresres.com", "app-password-16ch") in s.calls
    assert s.calls.index("starttls") < [i for i, c in enumerate(s.calls)
                                        if isinstance(c, tuple)][0]
    msg = s.sent[0]
    assert msg["Subject"] == ("AeroStator Core: access request from "
                              "Jane Doe (ACME Robotics)")
    assert msg["To"] == "vadim@motresres.com"
    assert msg["From"] == "vadim@motresres.com"
    # answering the alert answers the visitor
    assert msg["Reply-To"] == "jane@acme.com"
    body = msg.get_content()
    for wanted in ("Jane Doe", "ACME Robotics", "jane@acme.com",
                   "40 mm robot joint", "I need a joint motor",
                   "Visitor requests"):
        assert wanted in body, wanted


def test_a_merged_request_says_so_in_the_subject(env, smtp):
    notify.access_request({"email": "j@acme.com", "name": "J", "merged": 3},
                          block=True)
    assert "[updated x3]" in smtp[0].sent[0]["Subject"]


def test_the_recipients_and_the_server_are_configurable(env, smtp, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.office365.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("NOTIFY_FROM", "bot@motresres.com")
    monkeypatch.setenv("NOTIFY_TO", "vadim@motresres.com, sales@motresres.com")
    assert notify.recipients() == ["vadim@motresres.com", "sales@motresres.com"]
    notify.access_request({"email": "j@acme.com", "name": "J"}, block=True)
    s = smtp[0]
    assert s.host == "smtp.office365.com"
    assert s.sent[0]["From"] == "bot@motresres.com"
    assert s.sent[0]["To"] == "vadim@motresres.com, sales@motresres.com"


def test_a_mail_server_having_a_bad_minute_never_reaches_the_visitor(
        env, answers, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "vadim@motresres.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password-16ch")

    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(notify.smtplib, "SMTP", boom)
    answers["reply"] = "noted\n" + MARKER
    r = ask("jane@acme.com")
    assert r.status_code == 200 and "ACCESS_REQUEST" not in r.json()["reply"]
    assert len(S.list_access_requests()) == 1, "the inbox is the record either way"


def test_the_send_does_not_block_the_reply(env, answers, monkeypatch):
    """The default is a daemon thread: a visitor never waits on a TLS handshake."""
    monkeypatch.setenv("SMTP_USER", "vadim@motresres.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password-16ch")
    started = threading.Event()
    release = threading.Event()

    class _SlowSMTP(_FakeSMTP):
        def __init__(self, host, port, timeout=None):
            super().__init__(host, port, timeout)
            started.set()
            release.wait(5)

    _FakeSMTP.instances = []
    monkeypatch.setattr(notify.smtplib, "SMTP", _SlowSMTP)
    answers["reply"] = "noted\n" + MARKER
    t0 = time.time()
    r = ask("jane@acme.com")
    elapsed = time.time() - t0
    release.set()
    assert r.status_code == 200
    assert elapsed < 2.0, f"the reply waited {elapsed:.1f}s on the mail send"
    assert started.wait(5), "the mail was never attempted"


def test_the_daily_digest_goes_out_once_for_yesterday(env, smtp):
    yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    S.chats_dir().mkdir(parents=True, exist_ok=True)
    (S.chats_dir() / f"{yesterday}.jsonl").write_text(
        "\n".join(json.dumps({"ts": 1.0, "conv": "aaaa", "user": "q", "reply": "r"})
                  for _ in range(4)) + "\n", encoding="utf-8")
    notify.reset_digest_state()
    assert notify.maybe_daily_digest(block=True) is True
    assert notify.maybe_daily_digest(block=True) is False, "the digest repeated"
    assert len(smtp) == 1
    msg = smtp[0].sent[0]
    assert yesterday in msg["Subject"] and "4 visitor messages" in msg["Subject"]
    assert "Visitor chats" in msg.get_content()


def test_a_day_that_already_mailed_a_request_sends_no_digest(env, smtp):
    """The owner has read those alerts already — a summary of them is the noise
    that teaches a person to filter the alert away."""
    yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    S.chats_dir().mkdir(parents=True, exist_ok=True)
    (S.chats_dir() / f"{yesterday}.jsonl").write_text(
        json.dumps({"ts": 1.0, "conv": "aaaa", "user": "q", "reply": "r"}) + "\n",
        encoding="utf-8")
    rec = S.file_access_request(email="jane@acme.com", name="Jane")
    doc = json.loads(S.requests_file().read_text(encoding="utf-8"))
    doc[rec["id"]]["ts"] = time.time() - 86400
    S.requests_file().write_text(json.dumps(doc), encoding="utf-8")
    notify.reset_digest_state()
    assert notify.maybe_daily_digest(block=True) is False
    assert smtp == []


def test_a_quiet_day_sends_nothing(env, smtp):
    notify.reset_digest_state()
    assert notify.maybe_daily_digest(block=True) is False
    assert smtp == []


def test_telegram_stays_an_optional_second_channel(env, monkeypatch):
    """Kept because it costs nothing when unset — and it is the one channel that
    survives the mailbox the alerts are about."""
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:AAtest")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    seen: list = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen.append({"url": req.full_url, "body": json.loads(req.data),
                     "timeout": timeout, "method": req.get_method()})
        return _Resp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    assert notify.configured() is True
    assert notify.access_request({"email": "j@acme.com", "name": "J"},
                                 block=True) is True
    assert len(seen) == 1
    call = seen[0]
    assert call["url"] == "https://api.telegram.org/bot123:AAtest/sendMessage"
    assert call["method"] == "POST" and call["body"]["chat_id"] == "987654321"
    assert "access request from J" in call["body"]["text"]
    assert call["timeout"] == notify.TELEGRAM_TIMEOUT_S


# ── 6. the contract the assistant is given ───────────────────────────────────

def test_the_visitor_note_carries_the_whole_contract():
    note = support.VISITOR_NOTE
    assert "[[ACCESS_REQUEST:" in note, "the marker itself must be in the note"
    for field in ('name="', 'company="', 'email="', 'note="'):
        assert field in note, field
    for rule in ("ONE QUESTION AT A TIME", "never as a form",
                 "I've passed this to the team", "vadim@motresres.com",
                 "Report", "Never promise a timeline"):
        assert rule in note, rule
    # …and the old promises it must keep making
    assert "Request access" in note and "invitation" in note.lower()
    for forbidden in ("customer machine", "catalog's contents"):
        assert forbidden in note


def test_the_signed_in_prompt_still_points_at_the_report_tab():
    p = support.SYSTEM_PROMPT
    assert "**Report** tab" in p
    assert "files a ticket" in p
    assert "[[ACCESS_REQUEST" not in p, \
        "the marker contract belongs to the visitor note only"
