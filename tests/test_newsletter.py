"""The RETIRED newsletter (2026-10-15): no subscribe / confirm / campaign code, the old flag is ignored, nobody is moved into
the new topics, old unsubscribe links still work and switch the new topics off, the old records stay readable by an admin;
in-app notices.

Everything runs against tmp_path with a fake SMTP; the live registry and config/newsletter.json must stay untouched.
"""
from __future__ import annotations

import smtplib
import time
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

    def fake_send(to, subject, body, *, block=False, **kw):
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


def _login(email, pw, ip="203.0.113.5"):
    r = client.post("/api/auth/login", json={"email": email, "password": pw},
                    headers={"X-Forwarded-For": ip})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _user(env, email, role="user"):
    env["U"].create_user(email, "password-" + email.split("@")[0], role=role)
    return _login(email, "password-" + email.split("@")[0], ip="198.51.100.9")


def _legacy_subscriber(env, email, status="confirmed"):
    """A record written by the old newsletter (consent wording of 2026-09-29)."""
    N = env["N"]
    d = N._load()
    d["subscribers"][email] = {"status": status, "source": "settings", "text_version": "2026-09-29.1",
                               "consent_at": time.time() - 86400, "confirmed_at": time.time() - 86000,
                               "history": [{"ts": time.time() - 86000, "event": "confirmed", "source": "settings"}]}
    d["campaigns"]["c1"] = {"id": "c1", "subject": "Old news", "body_md": "b", "roles": [], "author": ADMIN,
                            "created": time.time() - 5000, "status": "done", "scheduled_at": None, "started_at": None,
                            "finished_at": None, "recipients": {email: {"status": "sent"}}, "unsubscribes": 0}
    N._save(d)


# -- the old subscribe / confirm / campaign surface is gone ----------------------------------------------

def test_old_flag_is_accepted_and_ignored_and_nothing_new_is_mapped_from_it(env):
    r = client.post("/api/auth/register", json={"email": NEW, "password": PW, "name": "N", "newsletter": True},
                    headers={"X-Forwarded-For": "203.0.113.77"})
    assert r.status_code == 202
    assert env["N"].status(NEW)["status"] == "none" and NEW not in env["N"]._load()["subscribers"]
    from motor_ai_sim import comm_prefs as CP
    assert CP.get(NEW)["topics"] == {t: False for t in CP.TOPICS} and CP.history(NEW) == []
    assert len(env["outbox"]) == 1 and env["outbox"][0]["subject"] == "Confirm your e-mail address"   # only the account link, no second mail
    client.post("/api/auth/verify", json={"token": env["outbox"][0]["body"].split("/api/auth/link/v/")[1].split()[0]})
    assert len(env["outbox"]) == 1                                                            # still no newsletter confirmation


def test_google_first_sign_in_with_the_old_flag_subscribes_nobody(env):
    A = env["A"]
    env["mp"].setattr(A, "GOOGLE_CLIENT_ID", "test-client")
    env["mp"].setattr(A, "_verify_google_token", lambda cred: {"email": "g@example.com", "email_verified": True, "sub": "g"})
    r = client.post("/api/auth/google", json={"credential": "x", "newsletter": True})
    assert r.status_code == 200, r.text
    assert "g@example.com" not in env["N"]._load()["subscribers"] and env["outbox"] == []


def test_subscribe_confirm_and_campaign_routes_are_gone(env):
    ah = _login(ADMIN, "password-admin", ip="198.51.100.1")
    h = _user(env, "someone@example.com")
    assert client.post("/api/newsletter/me", json={"subscribe": True}, headers=h).status_code in (404, 405)
    assert client.get("/api/newsletter/me", headers=h).status_code in (404, 405)
    assert client.post("/api/newsletter/confirm", json={"token": "x"}).status_code in (404, 405)
    for path in ("campaigns", "preview", "test"):
        assert client.post(f"/api/newsletter/admin/{path}", json={"subject": "s", "body_md": "b"}, headers=ah).status_code in (404, 405)
    assert client.post("/api/newsletter/admin/campaigns/c1/send", json={}, headers=ah).status_code in (404, 405)
    assert env["outbox"] == [] and env["delivered"] == []
    N = env["N"]
    for gone in ("request_subscribe", "record_pending", "confirm", "send_confirmation", "create_campaign", "schedule", "pump",
                 "build_message", "render", "start_worker", "audience"):
        assert not hasattr(N, gone), gone


def test_existing_users_are_not_subscribed_to_anything(env):
    env["U"].create_user("old@example.com", "password-old", role="user")
    assert env["N"].status("old@example.com")["subscribed"] is False
    A = env["A"]
    env["mp"].setattr(A, "GOOGLE_CLIENT_ID", "test-client")
    env["mp"].setattr(A, "_verify_google_token", lambda cred: {"email": "old@example.com", "email_verified": True, "sub": "o"})
    client.post("/api/auth/google", json={"credential": "x", "newsletter": True})
    assert env["N"].status("old@example.com")["status"] == "none"


# -- legacy subscribers: untouched, not migrated; old unsubscribe links still work -----------------------------

def test_legacy_subscribers_stay_as_they_are_and_are_not_opted_into_the_new_topics(env):
    from motor_ai_sim import comm_prefs as CP
    _legacy_subscriber(env, "sub@example.com")
    env["U"].create_user("sub@example.com", "password-sub")
    assert env["N"].is_confirmed("sub@example.com")                         # the old record is left alone
    assert CP.get("sub@example.com")["topics"] == {t: False for t in CP.TOPICS}
    assert not any(CP.opted_in("sub@example.com", t) for t in CP.TOPICS)    # the old wording is not consent to the new topics


def test_old_unsubscribe_link_and_header_still_work_and_switch_the_new_topics_off(env):
    from motor_ai_sim import comm_prefs as CP
    N = env["N"]
    _legacy_subscriber(env, "sub@example.com")
    env["U"].create_user("sub@example.com", "password-sub")
    CP.set_topics("sub@example.com", {"new_motors": True}, source="prefs_page")
    tok = N.unsubscribe_token("sub@example.com", "c1")
    # RFC 8058: the provider of an old mail POSTs a form to the ?t= URL
    r = client.post(f"/api/newsletter/unsubscribe?t={tok}", data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200, r.text
    assert not N.is_confirmed("sub@example.com")
    hist = N._load()["subscribers"]["sub@example.com"]["history"]
    assert hist[-1]["event"] == "unsubscribed" and hist[-1]["source"] == "link"
    st = CP.get("sub@example.com")
    assert st["unsubscribed_all"] is True and not any(st["topics"].values())
    assert client.post(f"/api/newsletter/unsubscribe?t={tok}").status_code == 200            # idempotent
    assert client.post("/api/newsletter/unsubscribe", json={"token": "nope"}).status_code == 400


def test_link_endpoint_rate_limited(env):
    codes = [client.post("/api/newsletter/unsubscribe", json={"token": "x"}, headers={"X-Forwarded-For": "192.0.2.50"}).status_code
             for _ in range(35)]
    assert 429 in codes


# -- admin: read-only history -------------------------------------------------------------------------------------

def test_admin_only(env):
    h = _user(env, "plain@example.com")
    for method, path in [("get", "/api/newsletter/admin/status"), ("get", "/api/newsletter/admin/subscribers"),
                         ("get", "/api/newsletter/admin/campaigns"), ("get", "/api/admin/comm-prefs/summary"),
                         ("get", "/api/notices/admin"), ("post", "/api/notices/admin")]:
        kw = {"json": {"title": "t"}} if method == "post" else {}
        r = getattr(client, method)(path, headers=h, **kw)
        assert r.status_code == 403, (path, r.status_code)


def test_admin_reads_the_legacy_history_and_the_mailing_summary_carries_the_counts(env):
    _legacy_subscriber(env, "a@example.com")
    _legacy_subscriber(env, "b@example.com", status="unsubscribed")
    ah = _login(ADMIN, "password-admin", ip="198.51.100.1")
    r = client.get("/api/newsletter/admin/subscribers?format=csv", headers=ah)
    assert r.status_code == 200 and "a@example.com,confirmed" in r.text
    assert client.get("/api/newsletter/admin/status", headers=ah).json() == {"confirmed": 1, "pending": 0, "unsubscribed": 1}
    camps = client.get("/api/newsletter/admin/campaigns", headers=ah).json()["campaigns"]
    assert [c["subject"] for c in camps] == ["Old news"]
    s = client.get("/api/admin/comm-prefs/summary", headers=ah).json()
    assert s["legacy_newsletter"] == {"confirmed": 1, "pending": 0, "unsubscribed": 1, "campaigns": 1}


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
