"""A disabled account stays disabled on every authentication path, and tokens without a session id are recorded.

Pinned: an account in the admin allowlist that was disabled in the registry is refused on the raw Google path too (and on the local
token path); an enabled allowlisted account still gets its tier; a token without a session id writes ONE 'sidless' event per hour per
account and address, a token with a session id none; nothing about the answer to a sidless token changes."""
from __future__ import annotations

import pytest

from tests.test_auth_sessions import ADMIN, CLIENT, _bearer, _events, _login, env  # noqa: F401  (fixture)



def test_a_disabled_allowlisted_account_is_refused_on_the_google_path(env, monkeypatch):
    A, U = env["A"], env["U"]
    monkeypatch.setattr(A, "_verify_google_token", lambda cred: {"email": ADMIN, "email_verified": True, "sub": "g-1"})
    ok = A.resolve_user_detail("Bearer google-credential")
    assert ok["reason"] == "ok" and ok["user"]["role"] == "admin"                  # active: the allowlist raises the tier
    U.update_user(ADMIN, disabled=True)
    refused = A.resolve_user_detail("Bearer google-credential")
    assert refused["user"] is None and refused["reason"] == "disabled"
    U.update_user(ADMIN, disabled=False)
    assert A.resolve_user_detail("Bearer google-credential")["reason"] == "ok"


def test_a_disabled_allowlisted_account_is_refused_on_the_local_token_path(env):
    A, U = env["A"], env["U"]
    token = _login(ADMIN, "password-admin")["token"]
    assert A.resolve_user_detail("Bearer " + token)["reason"] == "ok"
    U.update_user(ADMIN, disabled=True)
    assert A.resolve_user_detail("Bearer " + token)["reason"] == "disabled"


def test_the_registry_role_never_re_enables_a_disabled_allowlisted_account(env):
    A, U = env["A"], env["U"]
    assert A._registry_role(ADMIN) == "admin"
    U.update_user(ADMIN, disabled=True)
    assert A._registry_role(ADMIN) == "__disabled__"
    assert A._registry_role(CLIENT) == "user"


def test_a_token_without_a_session_id_is_recorded_once_per_hour_and_still_works(env):
    A, U = env["A"], env["U"]
    legacy = U.issue_token(CLIENT)                                                  # no sid: not in the session registry
    for _ in range(3):
        assert A.resolve_user_detail("Bearer " + legacy, ip="203.0.113.7", user_agent="legacy-script")["reason"] == "ok"
    ev = [e for e in _events(env) if e["event"] == "sidless"]
    assert len(ev) == 1 and ev[0]["email"] == CLIENT and ev[0]["sid"] == "" and ev[0]["ip"] == env["S"]._ip("203.0.113.7")                # the log keeps the hashed address, as for every event
    # another address of the same account is a different row of the list
    A.resolve_user_detail("Bearer " + legacy, ip="203.0.113.8", user_agent="legacy-script")
    assert len([e for e in _events(env) if e["event"] == "sidless"]) == 2


def test_a_token_with_a_session_id_writes_no_sidless_event(env):
    A = env["A"]
    token = _login(CLIENT, "password-client")["token"]
    A.resolve_user_detail("Bearer " + token, ip="203.0.113.9")
    assert [e for e in _events(env) if e["event"] == "sidless"] == []


def test_an_unreadable_registry_never_accepts_a_google_credential_and_keeps_the_session(env, monkeypatch):
    A, U = env["A"], env["U"]
    monkeypatch.setattr(A, "_verify_google_token", lambda cred: {"email": ADMIN, "email_verified": True, "sub": "g-1"})

    def boom():
        raise OSError("registry locked")
    monkeypatch.setattr(U, "_load", boom)
    for who in (ADMIN, CLIENT):                      # allowlisted and ordinary: neither is trusted on the fallback tier
        monkeypatch.setattr(A, "_verify_google_token", lambda cred, w=who: {"email": w, "email_verified": True, "sub": "g"})
        out = A.resolve_user_detail("Bearer google-credential")
        assert out["user"] is None and out["reason"] == "store_unavailable"


def test_the_default_registry_role_keeps_its_fallback_for_the_login_paths(env, monkeypatch):
    A, U = env["A"], env["U"]

    def boom():
        raise OSError("registry locked")
    monkeypatch.setattr(U, "_load", boom)
    assert A._registry_role(CLIENT) == "user"                                   # unchanged for callers that did not ask to fail closed
    assert A._registry_role(CLIENT, fail_closed=True) == "__unavailable__"


def test_a_raw_google_credential_is_part_of_the_sidless_inventory(env, monkeypatch):
    A = env["A"]
    monkeypatch.setattr(A, "_verify_google_token", lambda cred: {"email": CLIENT, "email_verified": True, "sub": "g-2"})
    for _ in range(2):
        assert A.resolve_user_detail("Bearer google-credential", ip="203.0.113.5")["reason"] == "ok"
    ev = [e for e in _events(env) if e["event"] == "sidless" and e.get("reason") == "google_raw_credential"]
    assert len(ev) == 1 and ev[0]["email"] == CLIENT
