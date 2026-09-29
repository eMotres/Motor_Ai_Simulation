"""Newsletter consent (GDPR double opt-in), campaigns queue, in-app notices.

Everything runs against tmp_path with a fake SMTP; the live registry and
config/newsletter.json must stay untouched.
"""
from __future__ import annotations

import smtplib
import time
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL = [_ROOT / "config" / n for n in ("users.json", ".sessions.json",
                                        ".auth_secret", "newsletter.json")]

ADMIN = "owner@example.com"
NEW = "new.person@example.com"
PW = "correct horse battery"

client = TestClient(app)


def _stamp(p: Path):
    return p.stat().st_mtime_ns if p.exists() else None


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    before = [_stamp(p) for p in _REAL]
    yield
    assert before == [_stamp(p) for p in _REAL], "a test touched the live store"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth as A
    from motor_ai_sim import auth_email as E
    from motor_ai_sim import newsletter as N
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U

    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(S, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setattr(N, "_STORE_FILE", tmp_path / "newsletter.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setenv("SMTP_USER", "noreply@emotres.com")
    monkeypatch.setenv("SMTP_PASS", "x")
    monkeypatch.setenv("MAIL_FROM", "noreply@emotres.com")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.com")
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(A, "AUTH_ENFORCE", False)
    monkeypatch.setattr(A, "_reject_seen", {})
    monkeypatch.setattr(S, "_last_touch", {})
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    E.reset_limits()
    N.LINK_IP.reset_all()

    outbox: list[dict] = []

    def fake_send(to, subject, body, *, block=False):
        outbox.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr(E, "send", fake_send)
    delivered: list = []
    monkeypatch.setattr(E, "deliver", lambda msg: delivered.append(msg))
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    yield {"U": U, "N": N, "E": E, "A": A, "outbox": outbox,
           "delivered": delivered, "mp": monkeypatch}
    E.reset_limits()
    N.LINK_IP.reset_all()


def _tok(body: str, kind: str) -> str:
    line = next(ln for ln in body.splitlines() if f"?{kind}=" in ln)
    return urllib.parse.unquote(line.split(f"?{kind}=", 1)[1].strip())


def _login(email, pw, ip="203.0.113.5"):
    r = client.post("/api/auth/login", json={"email": email, "password": pw},
                    headers={"X-Forwarded-For": ip})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _user(env, email, role="user"):
    env["U"].create_user(email, "password-" + email.split("@")[0], role=role)
    return _login(email, "password-" + email.split("@")[0], ip="198.51.100.9")


def _subscribe_confirmed(env, email, role="user"):
    h = _user(env, email, role)
    assert client.post("/api/newsletter/me", json={"subscribe": True}, headers=h).status_code == 200
    tok = _tok(env["outbox"][-1]["body"], "nl_confirm")
    assert client.post("/api/newsletter/confirm", json={"token": tok}).status_code == 200
    return h


# ── consent ─────────────────────────────────────────────────────────────────

def test_default_unchecked_signup_does_not_subscribe(env):
    r = client.post("/api/auth/register", json={"email": NEW, "password": PW, "name": "N"})
    assert r.status_code == 202
    assert env["N"].status(NEW)["status"] == "none"
    tok = _tok(env["outbox"][-1]["body"], "verify")
    client.post("/api/auth/verify", json={"token": tok})
    assert env["N"].status(NEW)["status"] == "none"
    assert not any("subscription" in m["subject"] for m in env["outbox"])


def test_signup_double_opt_in(env):
    N = env["N"]
    r = client.post("/api/auth/register",
                    json={"email": NEW, "password": PW, "name": "N", "newsletter": True},
                    headers={"X-Forwarded-For": "203.0.113.77"})
    assert r.status_code == 202
    st = N._load()["subscribers"][NEW]
    assert st["status"] == "pending" and st["source"] == "signup"
    assert st["text_version"] == N.CONSENT_TEXT_VERSION
    assert st["ip_hash"] and "203.0.113.77" not in str(st)
    assert len(env["outbox"]) == 1                     # only the account link yet
    client.post("/api/auth/verify", json={"token": _tok(env["outbox"][0]["body"], "verify")})
    assert env["outbox"][-1]["subject"] == "Confirm your subscription"
    assert not N.is_confirmed(NEW)                     # not before the click
    ctok = _tok(env["outbox"][-1]["body"], "nl_confirm")
    assert client.post("/api/newsletter/confirm", json={"token": ctok}).status_code == 200
    assert N.is_confirmed(NEW)
    assert client.post("/api/newsletter/confirm", json={"token": ctok}).status_code == 400
    assert client.post("/api/newsletter/confirm", json={"token": "forged"}).status_code == 400


def test_google_first_sign_in_consent(env):
    A = env["A"]
    env["mp"].setattr(A, "GOOGLE_CLIENT_ID", "test-client")
    env["mp"].setattr(A, "_verify_google_token",
                      lambda cred: {"email": "g@example.com", "email_verified": True, "sub": "g"})
    r = client.post("/api/auth/google", json={"credential": "x", "newsletter": True})
    assert r.status_code == 200, r.text
    rec = env["N"]._load()["subscribers"]["g@example.com"]
    assert rec["status"] == "pending" and rec["source"] == "google"
    assert env["outbox"][-1]["subject"] == "Confirm your subscription"


def test_existing_users_are_not_subscribed(env):
    env["U"].create_user("old@example.com", "password-old", role="user")
    assert env["N"].status("old@example.com")["subscribed"] is False
    assert env["N"].audience() == []
    # an existing account signing in with Google + ticked box is NOT opted in
    A = env["A"]
    env["mp"].setattr(A, "GOOGLE_CLIENT_ID", "test-client")
    env["mp"].setattr(A, "_verify_google_token",
                      lambda cred: {"email": "old@example.com", "email_verified": True, "sub": "o"})
    client.post("/api/auth/google", json={"credential": "x", "newsletter": True})
    assert env["N"].status("old@example.com")["status"] == "none"


# ── unsubscribe ─────────────────────────────────────────────────────────────

def test_unsubscribe_link_and_headers(env):
    N = env["N"]
    h = _subscribe_confirmed(env, "sub@example.com")
    msg = N.build_message("sub@example.com", "Hello", "Body **bold**", "c1")
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    lu = msg["List-Unsubscribe"]
    assert lu.startswith("<https://app.example.com/api/newsletter/unsubscribe?t=")
    html = msg.get_body(("html",)).get_content()
    text = msg.get_body(("plain",)).get_content()
    assert "<strong>bold</strong>" in html and "Kotnikova 34" in html
    assert "?unsubscribe=" in text and "Kotnikova 34" in text
    # RFC 8058: the provider POSTs a form to the header URL
    url = lu.strip("<>").replace("https://app.example.com", "")
    r = client.post(url, data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200, r.text
    assert not N.is_confirmed("sub@example.com")
    hist = N._load()["subscribers"]["sub@example.com"]["history"]
    assert hist[-1]["event"] == "unsubscribed" and hist[-1]["source"] == "link"
    # idempotent, and a bad token says nothing
    assert client.post(url).status_code == 200
    assert client.post("/api/newsletter/unsubscribe", json={"token": "nope"}).status_code == 400
    # settings toggle
    assert client.get("/api/newsletter/me", headers=h).json()["subscribed"] is False


def test_settings_toggle_off_is_instant(env):
    h = _subscribe_confirmed(env, "t@example.com")
    r = client.post("/api/newsletter/me", json={"subscribe": False}, headers=h)
    assert r.json()["status"] == "unsubscribed"
    assert env["N"].audience() == []


def test_link_endpoints_rate_limited(env):
    codes = [client.post("/api/newsletter/confirm", json={"token": "x"},
                         headers={"X-Forwarded-For": "192.0.2.50"}).status_code
             for _ in range(35)]
    assert 429 in codes


# ── campaigns ───────────────────────────────────────────────────────────────

def test_campaign_only_to_confirmed_and_throttled(env):
    N = env["N"]
    _subscribe_confirmed(env, "a@example.com")
    _subscribe_confirmed(env, "b@example.com", role="user")
    _subscribe_confirmed(env, "c@example.com")
    _user(env, "none@example.com")                       # never opted in
    hp = _user(env, "pend@example.com")                  # pending, never clicked
    client.post("/api/newsletter/me", json={"subscribe": True}, headers=hp)
    env["mp"].setenv("NEWSLETTER_RATE_PER_MIN", "2")
    ah = _login(ADMIN, "password-admin", ip="198.51.100.1")
    c = client.post("/api/newsletter/admin/campaigns",
                    json={"subject": "News", "body_md": "# Hi\n\ntext"}, headers=ah).json()
    assert client.post(f"/api/newsletter/admin/campaigns/{c['id']}/send",
                       json={}, headers=ah).status_code == 200
    sent: list = []
    t0 = time.time() + 1
    assert N.pump(now=t0, sender=sent.append) == 2         # rate 2/min
    assert N.pump(now=t0 + 10, sender=sent.append) == 0
    assert N.pump(now=t0 + 61, sender=sent.append) == 1
    assert sorted(m["To"] for m in sent) == ["a@example.com", "b@example.com", "c@example.com"]
    st = N.get_campaign(c["id"])
    assert st["stats"]["sent"] == 3
    N.pump(now=t0 + 200, sender=sent.append)
    assert N.get_campaign(c["id"])["status"] == "done"


def test_role_filter_bounce_and_unsubscribe_mid_campaign(env):
    N = env["N"]
    _subscribe_confirmed(env, "a@example.com", role="user")
    hb = _subscribe_confirmed(env, "b@example.com", role="user")
    _subscribe_confirmed(env, "c@example.com", role="admin")
    c = N.create_campaign("S", "body", roles=["user"])
    N.schedule(c["id"])
    env["mp"].setenv("NEWSLETTER_RATE_PER_MIN", "1")

    def sender(msg):
        raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})

    t0 = time.time() + 1
    assert N.pump(now=t0, sender=sender) == 1                # a -> bounced
    # b unsubscribes while the campaign is running: honoured before its send
    client.post("/api/newsletter/me", json={"subscribe": False}, headers=hb)
    assert N.pump(now=t0 + 61, sender=sender) == 0
    s = N.get_campaign(c["id"])["stats"]
    assert s["total"] == 2 and s["bounced"] == 1 and s["skipped"] == 1


def test_scheduled_campaign_waits_and_resume_after_restart(env):
    N = env["N"]
    _subscribe_confirmed(env, "a@example.com")
    c = N.create_campaign("S", "body")
    N.schedule(c["id"], at=5_000_000.0)
    assert N.pump(now=4_999_000.0, sender=lambda m: None) == 0
    # simulate a crash mid-send: a claimed recipient becomes failed, not resent
    d = N._load()
    d["campaigns"][c["id"]].update(status="sending", recipients={"a@example.com": {"status": "sending"}})
    N._save(d)
    assert N.recover_interrupted() == 1
    assert N.get_campaign(c["id"])["stats"]["failed"] == 1


def test_admin_only(env):
    h = _user(env, "plain@example.com")
    for method, path in [("get", "/api/newsletter/admin/status"),
                         ("get", "/api/newsletter/admin/subscribers"),
                         ("get", "/api/newsletter/admin/campaigns"),
                         ("post", "/api/newsletter/admin/campaigns"),
                         ("post", "/api/newsletter/admin/test"),
                         ("get", "/api/notices/admin"),
                         ("post", "/api/notices/admin")]:
        kw = {"json": {"subject": "s", "body_md": "b", "title": "t"}} if method == "post" else {}
        r = getattr(client, method)(path, headers=h, **kw)
        assert r.status_code == 403, (path, r.status_code)


def test_admin_csv_and_preview(env):
    _subscribe_confirmed(env, "a@example.com")
    ah = _login(ADMIN, "password-admin", ip="198.51.100.1")
    r = client.get("/api/newsletter/admin/subscribers?format=csv", headers=ah)
    assert r.status_code == 200 and "a@example.com,confirmed" in r.text
    p = client.post("/api/newsletter/admin/preview",
                    json={"subject": "S", "body_md": "<script>x</script> [l](https://x.io)"},
                    headers=ah).json()
    assert "<script>" not in p["html"] and '<a href="https://x.io"' in p["html"]
    assert p["audience"] == 1
    t = client.post("/api/newsletter/admin/test", json={"subject": "S", "body_md": "b"}, headers=ah)
    assert t.status_code == 200 and env["delivered"][-1]["To"] == ADMIN


# ── in-app notices ──────────────────────────────────────────────────────────

def test_notice_read_state_and_targeting(env):
    ha = _user(env, "u1@example.com", role="admin")
    hb = _user(env, "u2@example.com", role="user")
    ah = _login(ADMIN, "password-admin", ip="198.51.100.1")
    n_all = client.post("/api/notices/admin", json={"title": "Maintenance tonight"}, headers=ah).json()
    n_admin = client.post("/api/notices/admin", json={"title": "Admins only", "roles": ["admin"]}, headers=ah).json()
    la = client.get("/api/notices", headers=ha).json()["notices"]
    lb = client.get("/api/notices", headers=hb).json()["notices"]
    assert {n["id"] for n in la} == {n_all["id"], n_admin["id"]}
    assert {n["id"] for n in lb} == {n_all["id"]}
    assert all(not n["read"] for n in la)
    assert client.post(f"/api/notices/{n_all['id']}/read", headers=ha).status_code == 200
    la = {n["id"]: n["read"] for n in client.get("/api/notices", headers=ha).json()["notices"]}
    assert la[n_all["id"]] is True and la[n_admin["id"]] is False
    # read state is per user
    assert all(not n["read"] for n in client.get("/api/notices", headers=hb).json()["notices"])
    # a notice not addressed to you cannot be touched
    assert client.post(f"/api/notices/{n_admin['id']}/read", headers=hb).status_code == 404
    client.post(f"/api/notices/admin/{n_all['id']}/withdraw", headers=ah)
    assert client.get("/api/notices", headers=hb).json()["notices"] == []
