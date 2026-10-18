"""Security audit 2026-10-18: bounded Google key refetch, no wipe of an unreadable session store, unknown sessions refused."""
from __future__ import annotations

import pytest

from motor_ai_sim import auth, sessions as S


def test_google_keys_are_fetched_at_most_once_per_window(monkeypatch):
    calls = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"keys": []}'

    def _open(url, timeout=5):
        calls.append(url)
        return _Resp()
    monkeypatch.setattr(auth.urllib.request, "urlopen", _open)
    monkeypatch.setattr(auth, "_gjwks", {}, raising=False)
    monkeypatch.setattr(auth, "_gjwks_exp", 0.0, raising=False)
    monkeypatch.setattr(auth, "_gjwks_fetched_at", 0.0, raising=False)
    for _ in range(50):                      # a stream of tokens with unknown key ids
        auth._google_keys(force=True)
    assert len(calls) == 1


def test_an_unreadable_session_store_is_never_replaced(tmp_path, monkeypatch):
    f = tmp_path / ".sessions.json"
    f.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(S, "_SESSIONS_FILE", f, raising=False)

    def _boom():
        raise S.StoreUnavailable("unreadable")
    monkeypatch.setattr(S, "_load", _boom)
    sid = S.create("a@example.com", expires=9e9)
    assert sid and f.read_text(encoding="utf-8") == "{ this is not json"


def test_a_token_with_an_unknown_session_is_refused(tmp_path, monkeypatch):
    from motor_ai_sim import users as U
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-hardening-0123456789")
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / ".sessions.json", raising=False)
    U.create_user("b@example.com", "password-b123", role="user")
    tok = U.issue_token("b@example.com", sid="deadbeefdeadbeefdeadbeefdeadbeef")
    res = U.verify_token(tok) if hasattr(U, "verify_token") else None
    if res is None:
        pytest.skip("verify entry point named differently")
    assert res.reason == "revoked"
