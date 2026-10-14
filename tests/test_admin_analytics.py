"""Admin -> Analytics: GET /api/admin/analytics (routes/admin_analytics.py).

Umami is mocked with httpx.MockTransport - no network.  Covers: admin only, the shaped
payload (Umami 2.x ``{value, prev}`` and 1.x bare numbers), bearer-token caching and
re-login on 401, the 503 paths (not configured, unreachable, login refused) and that
no credential is ever echoed back.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import admin_analytics as AN

ADMIN = "admin@example.com"
ALICE = "alice@example.com"
SITE = "8fe8df44-5870-4f08-bfc4-16a79aa44f17"
BASE = "http://umami.internal:3000"
SECRET_PW = "s3cret-pw-do-not-leak"

client = TestClient(app)


class FakeUmami:
    def __init__(self, *, v1: bool = False, expire_first_token: bool = False):
        self.v1 = v1
        self.logins = 0
        self.calls: list[httpx.Request] = []
        self.expire_first_token = expire_first_token

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path = req.url.path
        if path == "/api/auth/login":
            self.logins += 1
            return httpx.Response(200, json={"token": f"tok-{self.logins}"})
        if req.headers.get("authorization") != f"Bearer tok-{self.logins}" or (
                self.expire_first_token and self.logins == 1):
            return httpx.Response(401, json={"error": "unauthorized"})
        assert path.startswith(f"/api/websites/{SITE}/")
        if path.endswith("/stats"):
            if self.v1:
                return httpx.Response(200, json={"pageviews": 120, "visitors": 40, "visits": 50,
                                                 "bounces": 20, "totaltime": 5000})
            return httpx.Response(200, json={
                "pageviews": {"value": 120, "prev": 1}, "visitors": {"value": 40, "prev": 1},
                "visits": {"value": 50, "prev": 1}, "bounces": {"value": 20, "prev": 1},
                "totaltime": {"value": 5000, "prev": 1}})
        if path.endswith("/pageviews"):
            return httpx.Response(200, json={
                "pageviews": [{"x": "2026-10-13 00:00:00", "y": 70}, {"x": "2026-10-14 00:00:00", "y": 50}],
                "sessions": [{"x": "2026-10-13 00:00:00", "y": 25}, {"x": "2026-10-14 00:00:00", "y": 15}]})
        if path.endswith("/metrics"):
            typ = req.url.params["type"]
            if typ == "path":
                return httpx.Response(200, json=[{"x": "/motors", "y": 60}, {"x": "/compare", "y": 30}])
            if typ == "referrer":
                return httpx.Response(200, json=[{"x": "", "y": 12}, {"x": "google.com", "y": 7}])
            return httpx.Response(200, json=[{"x": f"{typ}-a", "y": 9}])
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

    def install(fake):
        monkeypatch.setattr(AN, "_make_client",
                            lambda: httpx.Client(transport=httpx.MockTransport(fake), timeout=3))
    yield install
    AN.reset_token_cache()


@pytest.fixture()
def headers(tmp_path, monkeypatch):
    from motor_ai_sim import users as U
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(ALICE, "password-a", role="user", name="Alice")
    return {"admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
            "alice": {"Authorization": f"Bearer {U.issue_token(ALICE)}"}}


def test_admin_only(env, headers):
    env(FakeUmami())
    assert client.get("/api/admin/analytics", headers=headers["alice"]).status_code == 403


def test_payload_shape_v2(env, headers):
    fake = FakeUmami()
    env(fake)
    r = client.get("/api/admin/analytics?range=7d", headers=headers["admin"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["range"] == "7d" and d["unit"] == "day"
    assert d["totals"] == {"visitors": 40, "visits": 50, "pageviews": 120,
                           "bounce_rate": 40.0, "avg_visit_s": 100}
    assert d["series"] == [
        {"t": "2026-10-13 00:00:00", "pageviews": 70, "visitors": 25},
        {"t": "2026-10-14 00:00:00", "pageviews": 50, "visitors": 15}]
    assert d["pages"][0] == {"name": "/motors", "count": 60}
    assert d["referrers"][0] == {"name": "(direct / unknown)", "count": 12}
    for k in ("countries", "browsers", "os", "devices"):
        assert d[k] and d[k][0]["count"] == 9
    assert SECRET_PW not in r.text and "tok-" not in r.text


def test_umami_1x_bare_numbers_and_24h_uses_hours(env, headers):
    fake = FakeUmami(v1=True)
    env(fake)
    r = client.get("/api/admin/analytics?range=24h", headers=headers["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["pageviews"] == 120 and r.json()["unit"] == "hour"
    units = [c.url.params.get("unit") for c in fake.calls if c.url.path.endswith("/pageviews")]
    assert units == ["hour"]


def test_token_cached_across_requests_and_relogin_on_401(env, headers):
    fake = FakeUmami()
    env(fake)
    assert client.get("/api/admin/analytics", headers=headers["admin"]).status_code == 200
    assert client.get("/api/admin/analytics", headers=headers["admin"]).status_code == 200
    assert fake.logins == 1, "the bearer token is cached in memory"
    AN.reset_token_cache()
    fake2 = FakeUmami(expire_first_token=True)
    env(fake2)
    r = client.get("/api/admin/analytics", headers=headers["admin"])
    assert r.status_code == 200, r.text
    assert fake2.logins == 2, "a 401 forces exactly one new login"


def test_bad_range_is_400(env, headers):
    env(FakeUmami())
    assert client.get("/api/admin/analytics?range=1y", headers=headers["admin"]).status_code == 400


@pytest.mark.parametrize("missing", ["UMAMI_API_URL", "UMAMI_WEBSITE_ID", "UMAMI_USERNAME", "UMAMI_PASSWORD"])
def test_not_configured_is_503_with_message(env, headers, monkeypatch, missing):
    env(FakeUmami())
    monkeypatch.delenv(missing)
    r = client.get("/api/admin/analytics", headers=headers["admin"])
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_unreachable_is_503_never_500(env, headers):
    def boom(req):
        raise httpx.ConnectError("no route", request=req)
    env(boom)
    r = client.get("/api/admin/analytics", headers=headers["admin"])
    assert r.status_code == 503
    assert "unreachable" in r.json()["detail"]
    assert SECRET_PW not in r.text


def test_login_refused_is_503(env, headers):
    env(lambda req: httpx.Response(401, json={"error": "bad login"}))
    r = client.get("/api/admin/analytics", headers=headers["admin"])
    assert r.status_code == 503
    assert SECRET_PW not in r.text


def test_old_umami_path_type_falls_back_to_url(env, headers):
    base = FakeUmami()

    def fake(req):
        if req.url.path.endswith("/metrics") and req.url.params.get("type") == "path":
            return httpx.Response(400, json={"error": "bad type"})
        if req.url.path.endswith("/metrics") and req.url.params.get("type") == "url":
            return httpx.Response(200, json=[{"x": "/old", "y": 3}])
        return base(req)
    env(fake)
    r = client.get("/api/admin/analytics", headers=headers["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["pages"] == [{"name": "/old", "count": 3}]
