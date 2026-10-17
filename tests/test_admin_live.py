"""Tests for real-time live analytics: GET /api/admin/analytics/live (routes/admin_analytics.py).

Covers:
- Admin only (403 for regular accounts, 401 for anonymous, 200 for admin).
- Privacy: no IP address or IP hash present anywhere in the payload.
- Feed ordering: events strictly sorted newest-first (descending timestamp).
- Cache: 10-second server-side caching avoids duplicate upstream / file reads.
- Signed-in active users (within 300s) from activity.sqlite.
- Umami resilience: unconfigured or unreachable Umami degrades gracefully without failing.
"""
from __future__ import annotations

import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import admin_analytics as AN

ADMIN = "admin@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
SITE = "8fe8df44-5870-4f08-bfc4-16a79aa44f17"
BASE = "http://umami.internal:3000"
SECRET_PW = "s3cret-pw-do-not-leak"

client = TestClient(app)


class FakeLiveUmami:
    def __init__(self, *, active_count: int = 5, fail: bool = False):
        self.active_count = active_count
        self.fail = fail
        self.logins = 0
        self.active_calls = 0
        self.metrics_calls = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if self.fail:
            return httpx.Response(503, json={"error": "service unavailable"})
        if path == "/api/auth/login":
            self.logins += 1
            return httpx.Response(200, json={"token": f"tok-{self.logins}"})
        if req.headers.get("authorization") != f"Bearer tok-{self.logins}":
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == f"/api/websites/{SITE}/active":
            self.active_calls += 1
            return httpx.Response(200, json={"visitors": self.active_count})
        if path == f"/api/websites/{SITE}/metrics":
            self.metrics_calls += 1
            typ = req.url.params.get("type")
            if typ in ("path", "url"):
                return httpx.Response(200, json=[{"x": "/motors", "y": 14}, {"x": "/compare", "y": 8}])
            if typ == "country":
                return httpx.Response(200, json=[{"x": "DE", "y": 10}, {"x": "US", "y": 6}])
            return httpx.Response(200, json=[])
        if path == f"/api/websites/{SITE}/events":
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return httpx.Response(200, json=[
                {"id": "ev-1", "urlPath": "/configure", "country": "DE", "createdAt": now_iso},
                {"id": "ev-2", "urlPath": "/motors", "country": "US", "createdAt": now_iso},
            ])
        return httpx.Response(404)


@pytest.fixture()
def env(monkeypatch):
    from motor_ai_sim import auth

    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("UMAMI_API_URL", BASE)
    monkeypatch.setenv("UMAMI_WEBSITE_ID", SITE)
    monkeypatch.setenv("UMAMI_USERNAME", "stats-reader")
    monkeypatch.setenv("UMAMI_PASSWORD", SECRET_PW)
    AN.reset_token_cache()
    AN.reset_live_cache()

    def install(fake):
        monkeypatch.setattr(AN, "_make_client",
                            lambda: httpx.Client(transport=httpx.MockTransport(fake), timeout=3))
    yield install
    AN.reset_token_cache()
    AN.reset_live_cache()


@pytest.fixture()
def headers(tmp_path, monkeypatch):
    from motor_ai_sim import auth, users as U
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin User")
    U.create_user(ALICE, "password-a", role="user", name="Alice Wonderland")
    return {
        "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
        "alice": {"Authorization": f"Bearer {U.issue_token(ALICE)}"},
    }


def test_admin_only(env, headers):
    env(FakeLiveUmami())
    # Anonymous -> 401 or 403
    r_anon = client.get("/api/admin/analytics/live")
    assert r_anon.status_code in (401, 403)

    # Non-admin user -> 403
    r_alice = client.get("/api/admin/analytics/live", headers=headers["alice"])
    assert r_alice.status_code == 403

    # Admin -> 200
    r_admin = client.get("/api/admin/analytics/live", headers=headers["admin"])
    assert r_admin.status_code == 200


def test_payload_structure_and_no_ip_privacy(env, headers, tmp_path, monkeypatch):
    fake = FakeLiveUmami(active_count=7)
    env(fake)

    # Record some auth events that contain IPs
    from motor_ai_sim import sessions as S
    ev_file = tmp_path / "auth_events.jsonl"
    monkeypatch.setattr(S, "_EVENTS_FILE", ev_file)
    S.record_event("login", email="alice@example.com", ip="192.168.1.100", reason="password")
    S.record_event("register", email="bob@example.com", ip="10.0.0.50", reason="ok")
    S.record_event("verified", email="bob@example.com", ip="10.0.0.50", reason="link")

    res = client.get("/api/admin/analytics/live", headers=headers["admin"])
    assert res.status_code == 200
    data = res.json()

    # Structure checks
    assert "active_users" in data
    assert "anonymous" in data
    assert "events" in data
    assert "cached_at" in data

    anon = data["anonymous"]
    assert anon["configured"] is True
    assert anon["active"] == 7
    assert len(anon["pages"]) > 0
    assert len(anon["countries"]) > 0

    # Privacy verification: NO IP in raw JSON payload
    raw_text = res.text.lower()
    assert '"ip"' not in raw_text
    assert '"ip_hash"' not in raw_text
    assert "192.168.1.100" not in raw_text
    assert "10.0.0.50" not in raw_text

    # Verify events in feed
    types = [e["type"] for e in data["events"]]
    assert "login" in types
    assert "signup" in types
    assert "email_confirmed" in types


def test_feed_ordering_newest_first(env, headers, tmp_path, monkeypatch):
    env(FakeLiveUmami())

    from motor_ai_sim import sessions as S, nda as N, admin_audit as AA
    ev_file = tmp_path / "auth_events.jsonl"
    monkeypatch.setattr(S, "_EVENTS_FILE", ev_file)

    # Create events with distinct known times
    t1 = 1729000000.0
    t2 = 1729000050.0
    t3 = 1729000100.0
    t4 = 1729000150.0

    monkeypatch.setattr(S, "read_events", lambda limit=50: [
        {"event": "login", "email": "a@x.com", "t": t1, "ts": "2024-10-15T10:00:00Z", "reason": "pwd"},
        {"event": "register", "email": "b@x.com", "t": t3, "ts": "2024-10-15T10:01:40Z", "reason": "ok"},
    ])
    monkeypatch.setattr(N, "read_records", lambda limit=50: [
        {"id": "n1", "ts": t2, "iso": "2024-10-15T10:00:50Z", "email": "c@x.com", "company": "Acme", "country": "DE"},
    ])
    monkeypatch.setattr(AA, "read", lambda limit=50: [
        {"action": "user.role", "actor": "admin@x.com", "ts": t4, "iso": "2024-10-15T10:02:30Z", "target": "b@x.com"},
    ])

    AN.reset_live_cache()
    res = client.get("/api/admin/analytics/live", headers=headers["admin"])
    assert res.status_code == 200
    events = res.json()["events"]

    assert len(events) >= 4
    # Timestamps must be strictly non-increasing (descending / newest first)
    timestamps = [e["ts"] for e in events]
    assert timestamps == sorted(timestamps, reverse=True)
    non_pv = [e for e in events if e["type"] != "page_view"]
    assert non_pv[0]["type"] == "admin_action"
    assert non_pv[0]["ts"] == t4
    assert non_pv[1]["type"] == "signup"
    assert non_pv[1]["ts"] == t3


def test_10s_cache_behavior(env, headers):
    fake = FakeLiveUmami()
    env(fake)

    AN.reset_live_cache()
    r1 = client.get("/api/admin/analytics/live", headers=headers["admin"])
    assert r1.status_code == 200
    c1 = r1.json()["cached_at"]
    calls_after_r1 = fake.active_calls

    # Second request immediately: should hit cache without calling upstream
    r2 = client.get("/api/admin/analytics/live", headers=headers["admin"])
    assert r2.status_code == 200
    c2 = r2.json()["cached_at"]
    assert c1 == c2
    assert fake.active_calls == calls_after_r1

    # Invalidate cache: should fetch fresh
    AN.reset_live_cache()
    r3 = client.get("/api/admin/analytics/live", headers=headers["admin"])
    assert r3.status_code == 200
    assert fake.active_calls > calls_after_r1


def test_active_users_from_activity_store(env, headers, tmp_path, monkeypatch):
    env(FakeLiveUmami())

    from motor_ai_sim import activity as AC
    db_file = tmp_path / "activity.sqlite"
    monkeypatch.setattr(AC, "_path", lambda: db_file)

    now = time.time()
    con = AC._db()
    # Insert two active sessions (within 300s) and one stale session (1000s ago)
    con.execute("INSERT INTO activity_session(user, sid, day, last_ts) VALUES (?,?,?,?)",
                ("alice@example.com", "sid-alice-1234", AC.day_of(now), now - 30))
    con.execute("INSERT INTO activity_session(user, sid, day, last_ts) VALUES (?,?,?,?)",
                ("bob@example.com", "sid-bob-5678", AC.day_of(now), now - 60))
    con.execute("INSERT INTO activity_session(user, sid, day, last_ts) VALUES (?,?,?,?)",
                ("stale@example.com", "sid-stale-9999", AC.day_of(now), now - 1000))
    # Give alice a tab in activity_day
    con.execute("INSERT INTO activity_day(user, day, active_s, sessions, first_ts, last_ts, tabs) VALUES (?,?,?,?,?,?,?)",
                ("alice@example.com", AC.day_of(now), 120, 1, now - 120, now - 30, json.dumps({"compare": 90, "motors": 30})))
    con.commit()
    con.close()

    AN.reset_live_cache()
    res = client.get("/api/admin/analytics/live", headers=headers["admin"])
    assert res.status_code == 200
    active_users = res.json()["active_users"]

    active_emails = [u["user"] for u in active_users]
    assert "alice@example.com" in active_emails
    assert "bob@example.com" in active_emails
    assert "stale@example.com" not in active_emails

    alice_entry = next(u for u in active_users if u["user"] == "alice@example.com")
    assert alice_entry["tab"] == "compare"  # picked highest duration tab
    assert alice_entry["name"] == "Alice Wonderland"


def test_umami_unavailable_resilience(headers, monkeypatch):
    from motor_ai_sim import auth

    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    # Clear Umami environment variables
    monkeypatch.delenv("UMAMI_API_URL", raising=False)
    monkeypatch.delenv("UMAMI_WEBSITE_ID", raising=False)
    AN.reset_token_cache()
    AN.reset_live_cache()

    # The endpoint must still return 200, with anonymous marked unconfigured
    res = client.get("/api/admin/analytics/live", headers=headers["admin"])
    assert res.status_code == 200
    data = res.json()
    assert data["anonymous"]["configured"] is False
    assert data["anonymous"]["active"] == 0
    assert isinstance(data["events"], list)
    assert isinstance(data["active_users"], list)
