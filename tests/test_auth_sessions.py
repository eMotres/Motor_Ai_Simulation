"""Full session control: sids, reason codes, revocation, renewal, event log.

WHY these exist.  Between 2026-08-20 and 2026-09-03 the user was signed out of
the web app roughly daily and nobody could say why, because the verification
path logged NOTHING and answered `None` for every possible failure.  `/api/me`
reported `tokenRejected = (a token was presented and did not resolve)`, and the
frontend cleared the stored session on that flag — so a users.json that was
unreadable for 40 ms (a Windows lock during the atomic replace, an antivirus
hold) was indistinguishable from a genuinely expired token and cost the user a
session either way.

What is under test:

* a token carries its session id, and it survives the round trip;
* every rejection has a REASON: expired / revoked / disabled / unknown_user /
  bad_signature / malformed;
* ``store_unavailable`` is NOT a rejection — `/api/me` reports it as
  `tokenRejected: false` with `authError`, so the client keeps its session;
* a revoked sid stops verifying immediately, `revoke_all` empties an account;
* sliding renewal inside the last 7 days returns a fresh token with the SAME
  sid, so a session in daily use never expires under the user;
* every login and every rejection lands in logs/auth_events.jsonl;
* the admin views are admin-only.

Everything runs against throwaway files in tmp_path; the session asserts the
real config/users.json, config/.sessions.json and config/.auth_secret were
never touched.
"""
from __future__ import annotations

import builtins
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_SESSIONS = _ROOT / "config" / ".sessions.json"
_REAL_SECRET = _ROOT / "config" / ".auth_secret"

ADMIN = "owner@example.com"
CLIENT = "client@example.com"

client = TestClient(app)


def _stamp(p: Path):
    return p.stat().st_mtime_ns if p.exists() else None


@pytest.fixture(scope="module", autouse=True)
def _assert_the_real_auth_files_are_untouched():
    before = tuple(_stamp(p) for p in (_REAL_USERS, _REAL_SESSIONS, _REAL_SECRET))
    yield
    after = tuple(_stamp(p) for p in (_REAL_USERS, _REAL_SESSIONS, _REAL_SECRET))
    assert before == after, (
        "a test wrote one of config/users.json, config/.sessions.json, "
        "config/.auth_secret — the suite must never touch the live registry")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Registry, session store and event log redirected into tmp_path.  Only
    the paths are faked — signing, verification and admin-ness are the shipping
    code."""
    from motor_ai_sim import auth as A
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U

    users_file = tmp_path / "users.json"
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(S, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    # Signing secret from the env, so config/.auth_secret is never read/created.
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    # With ADMIN_EMAILS empty and AUTH_ENFORCE off every caller is the local-dev
    # admin, which would make the admin gate untestable — so name an admin.
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(A, "AUTH_ENFORCE", False)
    # Per-process caches that would otherwise leak between tests.
    monkeypatch.setattr(A, "_reject_seen", {})
    monkeypatch.setattr(S, "_last_touch", {})

    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    U.create_user(CLIENT, "password-client", tier="free", name="Client")
    yield {"tmp": tmp_path, "users": users_file,
           "events": tmp_path / "auth_events.jsonl", "S": S, "U": U, "A": A}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(email: str, password: str) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _events(env) -> list[dict]:
    p = env["events"]
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── sid round trip ───────────────────────────────────────────────────────────

def test_token_carries_its_session_id_and_it_round_trips(env):
    U, S = env["U"], env["S"]
    j = _login(CLIENT, "password-client")
    res = U.verify_token(j["token"])
    assert res.reason == "ok"
    assert res.sid, "the token must name a server-side session"
    rec = S.get(res.sid)
    assert rec["email"] == CLIENT
    assert rec["login_method"] == "password"
    assert rec["revoked"] is False
    assert rec["expires"] > time.time()

    # and the session is listable by its owner
    r = client.get("/api/auth/sessions", headers=_bearer(j["token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"] == res.sid
    assert [s["sid"] for s in body["sessions"]] == [res.sid]
    assert body["sessions"][0]["loginMethod"] == "password"


# ── reason codes ─────────────────────────────────────────────────────────────

def test_expired_token_is_rejected_and_says_expired(env):
    U = env["U"]
    sid = env["S"].create(CLIENT, expires=time.time() - 10, login_method="password")
    token = U.issue_token(CLIENT, sid=sid, ttl_s=-10)
    assert U.verify_token(token).reason == "expired"

    r = client.get("/api/me", headers=_bearer(token))
    j = r.json()
    assert j["tokenPresented"] is True
    assert j["tokenRejected"] is True
    assert j["authError"] == "expired"
    assert j["email"] is None


def test_a_foreign_signature_is_bad_signature_not_a_shrug(env, monkeypatch):
    U = env["U"]
    token = U.issue_token(CLIENT, sid=env["S"].new_sid())
    monkeypatch.setenv("AUTH_SECRET", "somebody-elses-secret")
    assert U.verify_token(token).reason == "bad_signature"


def test_garbage_is_malformed_and_a_deleted_account_is_unknown_user(env):
    U = env["U"]
    assert U.verify_token("not-a-jwt-at-all").reason == "malformed"
    token = U.issue_token(CLIENT, sid=env["S"].new_sid())
    U.delete_user(CLIENT)
    assert U.verify_token(token).reason == "unknown_user"


def test_disabled_account_is_rejected_with_its_own_reason(env):
    U = env["U"]
    token = U.issue_token(CLIENT, sid=env["S"].new_sid())
    U.update_user(CLIENT, disabled=True)
    assert U.verify_token(token).reason == "disabled"
    j = client.get("/api/me", headers=_bearer(token)).json()
    assert j["tokenRejected"] is True and j["authError"] == "disabled"


# ── the case that cost the user a session a day ──────────────────────────────

def test_an_unreadable_registry_is_not_a_rejected_token(env, monkeypatch):
    """A PermissionError on users.json must NOT read as `tokenRejected`.

    This is the whole point of the change: the client keeps its session and
    retries instead of signing the user out over a transient file lock.
    """
    U = env["U"]
    j = _login(CLIENT, "password-client")

    real_open = builtins.open
    target = str(env["users"])

    def flaky_open(file, *a, **kw):
        if str(file) == target:
            raise PermissionError(32, "The process cannot access the file")
        return real_open(file, *a, **kw)

    # Patched and restored by hand: monkeypatch.undo() would also drop the
    # AUTH_SECRET override and the run would sign with a different key.
    builtins.open = flaky_open
    try:
        res = U.verify_token(j["token"])
        assert res.reason == "store_unavailable"

        r = client.get("/api/me", headers=_bearer(j["token"]))
        body = r.json()
        assert body["tokenPresented"] is True
        assert body["tokenRejected"] is False, (
            "a file lock must never sign the user out — that is the daily-logout bug")
        assert body["authError"] == "store_unavailable"
    finally:
        builtins.open = real_open

    # and once the file is readable again the same token just works
    assert U.verify_token(j["token"]).reason == "ok"


def test_an_existing_secret_is_never_regenerated(env, monkeypatch, tmp_path):
    """An empty/unreadable .auth_secret must fail closed, not mint a new one:
    a fresh secret invalidates every live session on the deployment at once."""
    U = env["U"]
    from motor_ai_sim.sessions import StoreUnavailable
    secret_file = tmp_path / ".auth_secret"
    secret_file.write_text("", encoding="utf-8")
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.setattr(U, "_SECRET_FILE", secret_file)
    with pytest.raises(StoreUnavailable):
        U._secret()
    assert secret_file.read_text(encoding="utf-8") == "", "the secret was overwritten"
    assert U.verify_token("anything").reason == "store_unavailable"


# ── revocation ───────────────────────────────────────────────────────────────

def test_revoking_a_session_rejects_its_token_with_reason_revoked(env):
    U = env["U"]
    j = _login(CLIENT, "password-client")
    sid = U.verify_token(j["token"]).sid

    r = client.post(f"/api/auth/sessions/{sid}/revoke", headers=_bearer(j["token"]))
    assert r.status_code == 200, r.text

    assert U.verify_token(j["token"]).reason == "revoked"
    me = client.get("/api/me", headers=_bearer(j["token"])).json()
    assert me["tokenRejected"] is True and me["authError"] == "revoked"


def test_logout_revokes_the_current_session(env):
    U = env["U"]
    j = _login(CLIENT, "password-client")
    r = client.post("/api/auth/logout", headers=_bearer(j["token"]))
    assert r.status_code == 200, r.text
    assert r.json()["sid"] == U.verify_token(j["token"]).sid
    assert U.verify_token(j["token"]).reason == "revoked"


def test_a_session_of_another_account_is_a_404_not_a_403(env):
    """An account must not be able to probe for other people's session ids."""
    other = _login(ADMIN, "password-admin")
    mine = _login(CLIENT, "password-client")
    other_sid = env["U"].verify_token(other["token"]).sid
    r = client.post(f"/api/auth/sessions/{other_sid}/revoke", headers=_bearer(mine["token"]))
    assert r.status_code == 404
    assert env["U"].verify_token(other["token"]).reason == "ok"


def test_admin_revoke_all_empties_an_account(env):
    U = env["U"]
    a = _login(CLIENT, "password-client")
    b = _login(CLIENT, "password-client")
    admin = _login(ADMIN, "password-admin")
    r = client.post(f"/api/admin/users/{CLIENT}/revoke_all", headers=_bearer(admin["token"]))
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] == 2
    assert U.verify_token(a["token"]).reason == "revoked"
    assert U.verify_token(b["token"]).reason == "revoked"
    # the admin's own session is untouched
    assert U.verify_token(admin["token"]).reason == "ok"


# ── sliding renewal ──────────────────────────────────────────────────────────

def test_a_token_inside_its_last_week_is_renewed_with_the_same_sid(env):
    U, S = env["U"], env["S"]
    sid = S.create(CLIENT, expires=time.time() + 3 * 86400, login_method="google")
    # 3 days left — inside the 7-day renewal window
    token = U.issue_token(CLIENT, sid=sid, ttl_s=3 * 86400)

    me = client.get("/api/me", headers=_bearer(token)).json()
    assert me["tokenRejected"] is False
    assert me["email"] == CLIENT
    fresh = me.get("renewedToken")
    assert fresh, "a session in daily use must never expire under the user"

    res = U.verify_token(fresh)
    assert res.reason == "ok"
    assert res.sid == sid, "renewal must keep the SAME session"
    assert res.exp > time.time() + 29 * 86400
    # the session record's expiry moved with it
    assert S.get(sid)["expires"] > time.time() + 29 * 86400


def test_a_fresh_token_is_not_renewed(env):
    j = _login(CLIENT, "password-client")
    me = client.get("/api/me", headers=_bearer(j["token"])).json()
    assert "renewedToken" not in me


# ── the event log ────────────────────────────────────────────────────────────

def test_every_login_and_every_rejection_lands_in_the_event_log(env):
    U = env["U"]
    j = _login(CLIENT, "password-client")
    logins = [e for e in _events(env) if e["event"] == "login"]
    assert len(logins) == 1
    assert logins[0]["email"] == CLIENT
    assert logins[0]["reason"] == "password"
    assert logins[0]["sid"] == U.verify_token(j["token"]).sid

    expired = U.issue_token(CLIENT, sid=env["S"].new_sid(), ttl_s=-10)
    client.get("/api/me", headers=_bearer(expired))
    rejects = [e for e in _events(env) if e["event"] == "reject"]
    assert len(rejects) == 1
    assert rejects[0]["reason"] == "expired"
    assert rejects[0]["email"] == CLIENT
    assert rejects[0]["path"] == "/api/me"

    client.post("/api/auth/logout", headers=_bearer(j["token"]))
    assert any(e["event"] == "logout" for e in _events(env))


def test_the_admin_event_feed_serves_the_log_newest_first(env):
    _login(CLIENT, "password-client")
    admin = _login(ADMIN, "password-admin")
    r = client.get("/api/admin/auth_events?limit=50", headers=_bearer(admin["token"]))
    assert r.status_code == 200, r.text
    ev = r.json()["events"]
    assert ev and ev[0]["email"] == ADMIN          # newest first
    filtered = client.get(f"/api/admin/auth_events?email={CLIENT}",
                          headers=_bearer(admin["token"])).json()["events"]
    assert filtered and all(e["email"] == CLIENT for e in filtered)


# ── admin gate ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/sessions"),
    ("get", "/api/admin/auth_events"),
])
def test_the_admin_session_views_are_admin_only(env, method, path):
    anon = getattr(client, method)(path)
    assert anon.status_code == 401, anon.text
    mine = _login(CLIENT, "password-client")
    denied = getattr(client, method)(path, headers=_bearer(mine["token"]))
    assert denied.status_code == 403, denied.text
    admin = _login(ADMIN, "password-admin")
    ok = getattr(client, method)(path, headers=_bearer(admin["token"]))
    assert ok.status_code == 200, ok.text


def test_admin_sessions_lists_everyone_and_revokes_one(env):
    U = env["U"]
    mine = _login(CLIENT, "password-client")
    admin = _login(ADMIN, "password-admin")
    rows = client.get("/api/admin/sessions", headers=_bearer(admin["token"])).json()["sessions"]
    assert {r["email"] for r in rows} == {CLIENT, ADMIN}
    only = client.get(f"/api/admin/sessions?email={CLIENT}",
                      headers=_bearer(admin["token"])).json()["sessions"]
    assert [r["email"] for r in only] == [CLIENT]

    sid = U.verify_token(mine["token"]).sid
    r = client.post(f"/api/admin/sessions/{sid}/revoke", headers=_bearer(admin["token"]))
    assert r.status_code == 200, r.text
    assert U.verify_token(mine["token"]).reason == "revoked"
    assert client.post("/api/admin/sessions/nosuchsid/revoke",
                       headers=_bearer(admin["token"])).status_code == 404


def test_admin_session_revocation_of_an_unknown_sid_is_a_404(env):
    admin = _login(ADMIN, "password-admin")
    r = client.post("/api/admin/sessions/deadbeef/revoke", headers=_bearer(admin["token"]))
    assert r.status_code == 404


# ── last_seen ────────────────────────────────────────────────────────────────

def test_last_seen_is_touched_but_not_on_every_single_request(env, monkeypatch):
    U, S = env["U"], env["S"]
    j = _login(CLIENT, "password-client")
    sid = U.verify_token(j["token"]).sid
    S._last_touch.clear()

    client.get("/api/me", headers=_bearer(j["token"]))
    first = S.get(sid)["last_seen"]
    assert first > 0

    # a second call inside the quiet window must not write again
    writes = []
    real_save = S._save
    monkeypatch.setattr(S, "_save", lambda d: (writes.append(1), real_save(d)))
    client.get("/api/me", headers=_bearer(j["token"]))
    assert writes == [], "last_seen must not cost a disk write per request"
