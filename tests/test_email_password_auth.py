"""E-mail + password accounts: register, verify, login, lockout, reset, linking.

Security properties under test:

* passwords are stored as argon2id, never in plain text; legacy PBKDF2 rows
  still sign in and are upgraded;
* register / reset-request answers are byte-identical for new, pending and
  existing addresses (no user enumeration);
* an account cannot sign in before its address is proven (link, admin
  approval, Google, or a reset link);
* links are signed, single-use, purpose-bound and expire;
* failed logins lock the ACCOUNT (from any IP) and the IP (for any account);
* a password reset revokes every session;
* Google + password on the same address = one account, and a pre-registered
  (unverified) password does not survive Google's proof of the mailbox.

Everything runs against tmp_path; the real registry must stay untouched.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL = [_ROOT / "config" / n for n in ("users.json", ".sessions.json", ".auth_secret")]

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
    assert before == [_stamp(p) for p in _REAL], "a test touched the live registry"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth as A
    from motor_ai_sim import auth_email as E
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U

    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(S, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(A, "AUTH_ENFORCE", False)
    monkeypatch.setattr(A, "_reject_seen", {})
    monkeypatch.setattr(S, "_last_touch", {})
    for k in ("SMTP_USER", "SMTP_PASS", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    # workspace provisioning is a no-op without WORKSPACES_ROOT
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    E.reset_limits()

    outbox: list[dict] = []

    def fake_send(to, subject, body, *, block=False):
        outbox.append({"to": to, "subject": subject, "body": body})
        return True

    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    yield {"U": U, "S": S, "E": E, "A": A, "tmp": tmp_path, "outbox": outbox,
           "fake_send": fake_send, "mp": monkeypatch}
    E.reset_limits()


def _smtp_on(env):
    env["mp"].setattr(env["E"], "send", env["fake_send"])


def _token_from(mail: dict, kind: str) -> str:
    import urllib.parse
    line = next(ln for ln in mail["body"].splitlines() if f"?{kind}=" in ln)
    return urllib.parse.unquote(line.split(f"?{kind}=", 1)[1].strip())


def _register(email=NEW, pw=PW, name="New Person", ip="203.0.113.5"):
    return client.post("/api/auth/register", json={"email": email, "password": pw, "name": name},
                       headers={"X-Forwarded-For": ip})


def _login(email=NEW, pw=PW, ip="203.0.113.5"):
    return client.post("/api/auth/login", json={"email": email, "password": pw},
                       headers={"X-Forwarded-For": ip})


def _admin_h():
    tok = _login(ADMIN, "password-admin", ip="198.51.100.1").json()["token"]
    return {"Authorization": f"Bearer {tok}"}


# ── register + verify + login ────────────────────────────────────────────────

def test_register_verify_login_round_trip(env):
    _smtp_on(env)
    r = _register(email="  New.Person@Example.COM ")
    assert r.status_code == 202, r.text
    rec = json.loads((env["tmp"] / "users.json").read_text())[NEW]   # normalized key
    assert rec["email_verified"] is False
    assert _login().status_code == 403                  # correct pw, not yet proven
    tok = _token_from(env["outbox"][-1], "verify")
    assert env["outbox"][-1]["to"] == NEW
    assert client.post("/api/auth/verify", json={"token": tok}).status_code == 200
    # single use
    assert client.post("/api/auth/verify", json={"token": tok}).status_code == 400
    r = _login()
    assert r.status_code == 200, r.text
    me = client.get("/api/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.status_code == 200 and me.json().get("email", NEW) == NEW


def test_password_stored_as_argon2id_never_plain(env):
    _smtp_on(env)
    _register()
    raw = (env["tmp"] / "users.json").read_text()
    assert PW not in raw
    rec = json.loads(raw)[NEW]
    assert rec["pw_algo"] == "argon2id" and rec["pw_hash"].startswith("$argon2id$")
    assert "pw_salt" not in rec


def test_password_policy_is_loud(env):
    assert _register(pw="short").status_code == 422
    assert _register(pw="password123").status_code == 422          # common
    assert _register(pw="aaaaaaaaaaaa").status_code == 422
    assert _register(email="not-an-email").status_code == 422
    assert _register(name="  ").status_code == 422


def test_register_does_not_enumerate(env):
    _smtp_on(env)
    a = _register(email="fresh@example.com", ip="203.0.113.10")
    b = _register(email=ADMIN, ip="203.0.113.11")                   # exists, verified
    c = _register(email="fresh@example.com", ip="203.0.113.12")     # exists, pending
    assert a.status_code == b.status_code == c.status_code == 202
    assert a.json() == b.json() == c.json()
    # the existing account's password was NOT replaced
    assert _login(ADMIN, "password-admin", ip="198.51.100.9").status_code == 200
    # the existing owner is told by mail, not the requester by HTTP
    assert any(m["to"] == ADMIN and "already exists" in m["body"] for m in env["outbox"])


def test_reset_request_does_not_enumerate(env):
    _smtp_on(env)
    a = client.post("/api/auth/reset/request", json={"email": ADMIN})
    b = client.post("/api/auth/reset/request", json={"email": "nobody@example.com"})
    assert a.status_code == b.status_code == 202 and a.json() == b.json()
    assert [m["to"] for m in env["outbox"]] == [ADMIN]


def test_login_unknown_and_wrong_password_look_the_same(env):
    a = _login("nobody@example.com", "whatever-12345", ip="192.0.2.1")
    b = _login(ADMIN, "wrong-password-1", ip="192.0.2.2")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


# ── lockout ──────────────────────────────────────────────────────────────────

def test_account_lockout_across_ips(env):
    E = env["E"]
    for i in range(E.LOGIN_ACCOUNT.max_hits):
        assert _login(ADMIN, "bad-password-x", ip=f"192.0.2.{i + 1}").status_code == 401
    # even the right password from a fresh IP is refused while locked
    assert _login(ADMIN, "password-admin", ip="192.0.2.200").status_code == 429


def test_ip_lockout_across_accounts(env):
    E = env["E"]
    for i in range(E.LOGIN_IP.max_hits):
        assert _login(f"u{i}@example.com", "bad-password-x", ip="192.0.2.77").status_code == 401
    assert _login(ADMIN, "password-admin", ip="192.0.2.77").status_code == 429
    assert _login(ADMIN, "password-admin", ip="192.0.2.78").status_code == 200


# ── reset ────────────────────────────────────────────────────────────────────

def test_reset_sets_new_password_and_revokes_sessions(env):
    _smtp_on(env)
    U, S = env["U"], env["S"]
    old = _login(ADMIN, "password-admin", ip="198.51.100.2").json()["token"]
    assert U.verify_token(old).reason == "ok"
    client.post("/api/auth/reset/request", json={"email": ADMIN})
    tok = _token_from(env["outbox"][-1], "reset")
    bad = client.post("/api/auth/reset/confirm", json={"token": tok, "password": "short"})
    assert bad.status_code == 422
    ok = client.post("/api/auth/reset/confirm", json={"token": tok, "password": "a brand new secret"})
    assert ok.status_code == 200, ok.text
    assert U.verify_token(old).reason == "revoked"
    assert _login(ADMIN, "password-admin", ip="198.51.100.3").status_code == 401
    assert _login(ADMIN, "a brand new secret", ip="198.51.100.4").status_code == 200
    # link is single-use
    again = client.post("/api/auth/reset/confirm", json={"token": tok, "password": "another new secret"})
    assert again.status_code == 400


def test_links_are_purpose_bound_and_expire(env):
    U = env["U"]
    t = U.issue_link_token(ADMIN, "reset")
    assert client.post("/api/auth/verify", json={"token": t}).status_code == 400
    expired = U.issue_link_token(ADMIN, "reset", ttl_s=-10)
    r = client.post("/api/auth/reset/confirm", json={"token": expired, "password": "a brand new secret"})
    assert r.status_code == 400
    # a session token is not a link and a link is not a session token
    sess = _login(ADMIN, "password-admin", ip="198.51.100.5").json()["token"]
    assert client.post("/api/auth/verify", json={"token": sess}).status_code == 400
    assert U.verify_token(t).reason != "ok"


# ── SMTP not configured → admin pending list ─────────────────────────────────

def test_without_smtp_account_lands_in_admin_pending(env):
    assert _register().status_code == 202
    h = _admin_h()
    rows = client.get("/api/auth/pending", headers=h).json()["pending"]
    row = next(r for r in rows if r["email"] == NEW)
    assert row["reason"] == "smtp_not_configured"
    assert _login().status_code == 403
    assert client.post(f"/api/auth/pending/{NEW}/approve", headers=h).status_code == 200
    assert _login().status_code == 200
    assert all(r["email"] != NEW for r in client.get("/api/auth/pending", headers=h).json()["pending"])


def test_pending_is_admin_only(env):
    _register()
    tok = _login(ADMIN, "password-admin", ip="198.51.100.6").json()["token"]  # sanity
    env["U"].create_user("plain@example.com", "password-plain", tier="free")
    ptok = _login("plain@example.com", "password-plain", ip="198.51.100.7").json()["token"]
    r = client.get("/api/auth/pending", headers={"Authorization": f"Bearer {ptok}"})
    assert r.status_code in (401, 403)
    assert tok


# ── Google linking ───────────────────────────────────────────────────────────

def _google_as(env, email):
    A = env["A"]
    env["mp"].setattr(A, "GOOGLE_CLIENT_ID", "test-client")
    env["mp"].setattr(A, "_verify_google_token",
                      lambda cred: {"email": email, "email_verified": True, "sub": "g-1"})
    return client.post("/api/auth/google", json={"credential": "x"})


def test_google_login_for_existing_password_account_is_one_account(env):
    r = _google_as(env, ADMIN)
    assert r.status_code == 200, r.text
    users = json.loads((env["tmp"] / "users.json").read_text())
    assert list(users) == [ADMIN]
    # password door still open
    assert _login(ADMIN, "password-admin", ip="198.51.100.8").status_code == 200


def test_google_proof_discards_squatted_unverified_password(env):
    _register(email="victim@example.com", pw="attacker chosen pw")
    r = _google_as(env, "victim@example.com")
    assert r.status_code == 200
    rec = json.loads((env["tmp"] / "users.json").read_text())["victim@example.com"]
    assert rec["email_verified"] is True and rec["has_password"] is False
    assert _login("victim@example.com", "attacker chosen pw", ip="198.51.100.9").status_code == 401


def test_google_account_can_set_password_via_reset(env):
    _smtp_on(env)
    _google_as(env, "g.only@example.com")
    client.post("/api/auth/reset/request", json={"email": "g.only@example.com"})
    tok = _token_from(env["outbox"][-1], "reset")
    assert client.post("/api/auth/reset/confirm",
                       json={"token": tok, "password": "my new password!"}).status_code == 200
    assert _login("g.only@example.com", "my new password!", ip="198.51.100.10").status_code == 200


# ── schema compatibility ─────────────────────────────────────────────────────

def test_legacy_pbkdf2_row_signs_in_and_is_upgraded(env):
    import hashlib
    import secrets
    U = env["U"]
    salt = secrets.token_hex(16)
    legacy = {"pw_salt": salt, "pw_iters": 1000,
              "pw_hash": hashlib.pbkdf2_hmac("sha256", b"legacy-pass", bytes.fromhex(salt), 1000).hex(),
              "tier": "pro", "name": "Old", "disabled": False, "created": "2026-08-21T10:00:00"}
    users = json.loads((env["tmp"] / "users.json").read_text())
    users["old@example.com"] = legacy
    (env["tmp"] / "users.json").write_text(json.dumps(users))
    assert U.public_user("old@example.com")["email_verified"] is True
    assert _login("old@example.com", "legacy-pass", ip="198.51.100.11").status_code == 200
    rec = json.loads((env["tmp"] / "users.json").read_text())["old@example.com"]
    assert rec["pw_algo"] == "argon2id" and "pw_salt" not in rec and rec["tier"] == "pro"
    assert _login("old@example.com", "legacy-pass", ip="198.51.100.12").status_code == 200


def test_methods_endpoint_is_anonymous(env):
    r = client.get("/api/auth/methods")
    assert r.status_code == 200 and r.json()["password"] is True and r.json()["mail"] is False
    assert env["A"].anonymous_allowed("/api/auth/register")
    assert env["A"].anonymous_allowed("/api/auth/reset/confirm")
    assert not env["A"].anonymous_allowed("/api/auth/pending")
