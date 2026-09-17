"""The in-app assistant answers a VISITOR — and only so many times.

Until 2026-09-17 ``POST /api/support/chat`` was tier "free" ("calls the paid
provider API — require a signed-in account"), so on the public server the
landing page rendered the "Help & feedback" widget to a signed-out visitor and
the widget's every answer was 401 → "Sorry — I couldn't answer just now".  The
owner tested it twice as a visitor, asking the one question a visitor has: *how
can I get access to this site?*

So the route is open to anonymous callers now, and the bill is bounded where it
is actually spent instead of at the door:

* per IP — ``ANON_BURST_MAX`` per ``ANON_BURST_WINDOW_S``, ``ANON_DAY_MAX``/day;
* for the anonymous audience as a whole — ``ANON_GLOBAL_DAY_MAX``/day;
* an anonymous conversation carries at most ``ANON_MAX_TURNS`` messages of
  ``ANON_MAX_CHARS`` characters into a provider call;
* over a cap: 429, a written canned reply, NO provider call, one log line.

A signed-in caller meets none of it.  And the door itself (``PUBLIC_EXHIBIT=0``)
is unchanged for everything else — that half is asserted here as well as in
``tests/test_public_exhibit_off.py``, because this is the change that opened the
first hole in it.

No test here touches a provider: ``_effective`` is stubbed, and where the
messages themselves are the subject the provider call is a recorder.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.auth import anonymous_allowed
from motor_ai_sim.routes import support

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"

ADMIN = "owner@example.com"
CLIENT = "client@example.com"

client = TestClient(app)


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _real_users_untouched():
    before = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    yield
    after = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    assert before == after, "config/users.json was modified by a test"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A throwaway registry with an admin and an ordinary account, the tier gate
    enforcing (what the server runs), and no provider configured.

    ADMIN_EMAILS must be non-empty or ``_is_admin_caller`` treats EVERY caller —
    the anonymous one included — as the local-dev admin, and the limiter under
    test would never see an anonymous request at all.
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
    for var in support._CAP_ENV.values():
        monkeypatch.delenv(var, raising=False)

    # No provider: the route answers its mock, so nothing in this file can
    # reach the network or spend a token.
    monkeypatch.setattr(support, "_effective", lambda: {
        "provider": "none",
        "gemini": {"key": "", "model": "m", "key_source": "none"},
        "anthropic": {"key": "", "model": "m", "key_source": "none"},
    })
    monkeypatch.setattr(support, "_load_overrides", lambda: {})
    support.reset_limits()

    U.create_user(ADMIN, "password-admin", tier="admin", name="Owner")
    U.create_user(CLIENT, "password-client", tier="free", name="Client")
    yield {
        "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
        "client": {"Authorization": f"Bearer {U.issue_token(CLIENT)}"},
    }
    support.reset_limits()


def ask(text: str = "How can I get access to this site?", *, ip: str = "203.0.113.7",
        headers: dict | None = None, messages: list | None = None):
    h = {"X-Forwarded-For": ip} if ip else {}
    h.update(headers or {})
    body = {"messages": messages if messages is not None
            else [{"role": "user", "content": text}]}
    return client.post("/api/support/chat", json=body, headers=h)


# ── the door ─────────────────────────────────────────────────────────────────

def test_the_chat_is_on_the_anonymous_allowlist():
    assert anonymous_allowed("/api/support/chat") is True
    assert anonymous_allowed("/api/support/chat/") is True


def test_nothing_else_of_support_opened():
    """Only the chat. The admin half of the support surface is unchanged."""
    for closed in ("/api/support", "/api/support/chat/anything",
                   "/api/admin/support", "/api/admin/tickets"):
        assert anonymous_allowed(closed) is False, closed


def test_the_door_is_still_shut_for_everything_else(env, monkeypatch):
    """PUBLIC_EXHIBIT=0: the chat answers, the rest of the API does not."""
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    assert ask().status_code == 200
    for method, path in (("GET", "/api/family/tree"), ("GET", "/api/geometry"),
                         ("GET", "/api/catalog"), ("GET", "/api/my_motors"),
                         ("GET", "/api/admin/users")):
        r = client.request(method, path)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
        assert r.json()["detail"] == "Sign in to use this server."


def test_an_anonymous_visitor_gets_an_answer(env, monkeypatch):
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    r = ask()
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["reply"], str) and r.json()["reply"].strip()


# ── the per-IP limits ────────────────────────────────────────────────────────

def test_the_ninth_message_from_one_ip_is_the_canned_reply(env):
    for i in range(support.ANON_BURST_MAX):
        assert ask(ip="198.51.100.4").status_code == 200, f"message {i + 1}"
    r = ask(ip="198.51.100.4")
    assert r.status_code == 429
    j = r.json()
    assert j["source"] == "rate_limited" and j["limit"] == "ip_burst"
    assert "vadim@motresres.com" in j["reply"]
    assert r.headers.get("Retry-After") == str(int(support.ANON_BURST_WINDOW_S))


def test_the_limit_is_per_ip_not_global(env):
    for _ in range(support.ANON_BURST_MAX + 1):
        ask(ip="198.51.100.4")
    assert ask(ip="198.51.100.5").status_code == 200


def test_a_forged_forwarded_for_prefix_cannot_reset_the_counter(env):
    """nginx APPENDS the address it saw, so only the LAST entry is trustworthy —
    reading the first would let a client mint a fresh quota per request."""
    for i in range(support.ANON_BURST_MAX):
        r = ask(ip=f"10.0.0.{i}, 192.0.2.99")
        assert r.status_code == 200, f"message {i + 1}"
    assert ask(ip="10.0.0.250, 192.0.2.99").status_code == 429


def test_x_real_ip_is_used_when_there_is_no_forwarded_for(env):
    for _ in range(support.ANON_BURST_MAX):
        assert ask(ip="", headers={"X-Real-IP": "192.0.2.10"}).status_code == 200
    assert ask(ip="", headers={"X-Real-IP": "192.0.2.10"}).status_code == 429
    assert ask(ip="", headers={"X-Real-IP": "192.0.2.11"}).status_code == 200


def test_the_daily_cap_per_ip(env, monkeypatch):
    monkeypatch.setenv("SUPPORT_ANON_BURST_MAX", "99")
    monkeypatch.setenv("SUPPORT_ANON_DAY_MAX", "3")
    for _ in range(3):
        assert ask(ip="203.0.113.99").status_code == 200
    r = ask(ip="203.0.113.99")
    assert r.status_code == 429 and r.json()["limit"] == "ip_day"


def test_the_global_daily_cap_catches_a_thousand_addresses(env, monkeypatch):
    monkeypatch.setenv("SUPPORT_ANON_GLOBAL_DAY_MAX", "3")
    for i in range(3):
        assert ask(ip=f"203.0.113.{i}").status_code == 200
    r = ask(ip="203.0.113.200")
    assert r.status_code == 429 and r.json()["limit"] == "global_day"
    assert "vadim@motresres.com" in r.json()["reply"]


def test_a_refusal_costs_nothing(env, monkeypatch):
    """No provider call over the cap — that is the whole point of the limit."""
    calls: list = []
    monkeypatch.setattr(support, "_effective", lambda: {
        "provider": "gemini",
        "gemini": {"key": "k", "model": "m", "key_source": "env"},
        "anthropic": {"key": "", "model": "m", "key_source": "none"},
    })
    monkeypatch.setattr(support, "_gemini_reply",
                        lambda msgs, key, model, sp: calls.append((msgs, sp)) or "ok")
    for _ in range(support.ANON_BURST_MAX):
        ask(ip="198.51.100.77")
    assert len(calls) == support.ANON_BURST_MAX
    assert ask(ip="198.51.100.77").status_code == 429
    assert len(calls) == support.ANON_BURST_MAX, "a refused call reached the provider"


def test_a_refusal_is_not_charged_to_the_caller(env, monkeypatch):
    """The counters measure what was SPENT: a refusal spends nothing, so being
    refused must not also burn a day's quota."""
    monkeypatch.setenv("SUPPORT_ANON_BURST_MAX", "2")
    monkeypatch.setenv("SUPPORT_ANON_DAY_MAX", "4")
    for _ in range(2):
        assert ask(ip="198.51.100.31").status_code == 200
    for _ in range(10):
        r = ask(ip="198.51.100.31")
        assert r.status_code == 429 and r.json()["limit"] == "ip_burst", \
            "a refusal was counted and pushed the caller into the DAILY cap"


# ── the signed-in caller meets none of it ────────────────────────────────────

def test_a_signed_in_user_is_never_limited(env):
    for i in range(support.ANON_BURST_MAX * 3):
        r = ask(ip="198.51.100.4", headers=env["client"])
        assert r.status_code == 200, f"message {i + 1}"
    # …and the anonymous quota for that same address was never touched
    assert ask(ip="198.51.100.4").status_code == 200


def test_the_owner_is_never_limited(env):
    for _ in range(support.ANON_BURST_MAX + 5):
        assert ask(ip="198.51.100.4", headers=env["admin"]).status_code == 200


# ── what an anonymous conversation may carry ─────────────────────────────────

@pytest.fixture()
def recorded(env, monkeypatch):
    """The provider call, recorded instead of made: [(messages, system_prompt)]."""
    seen: list = []
    monkeypatch.setattr(support, "_effective", lambda: {
        "provider": "gemini",
        "gemini": {"key": "k", "model": "m", "key_source": "env"},
        "anthropic": {"key": "", "model": "m", "key_source": "none"},
    })
    monkeypatch.setattr(
        support, "_gemini_reply",
        lambda msgs, key, model, sp: seen.append((msgs, sp)) or "answered")
    return seen


def _long_history(n: int) -> list[dict]:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"question {i}"})
        out.append({"role": "assistant", "content": f"answer {i}"})
    return out


def test_anonymous_history_is_cut_to_six_turns(recorded):
    ask(messages=_long_history(20) + [{"role": "user", "content": "and finally?"}])
    msgs, _sp = recorded[-1]
    assert len(msgs) <= support.ANON_MAX_TURNS
    assert msgs[-1]["content"] == "and finally?"
    assert msgs[0]["role"] == "user", "a provider call must start on a user turn"


def test_anonymous_messages_are_cut_to_a_thousand_characters(recorded):
    ask(messages=[{"role": "user", "content": "x" * 50_000}])
    msgs, _sp = recorded[-1]
    assert len(msgs[-1]["content"]) == support.ANON_MAX_CHARS


def test_a_signed_in_conversation_keeps_the_long_caps(recorded, env):
    ask(messages=_long_history(20) + [{"role": "user", "content": "y" * 3000}],
        headers=env["client"])
    msgs, _sp = recorded[-1]
    assert len(msgs) > support.ANON_MAX_TURNS
    assert len(msgs[-1]["content"]) == 3000


def test_the_visitor_note_is_injected_only_for_a_visitor(recorded, env):
    ask()
    _msgs, sp = recorded[-1]
    assert support.VISITOR_NOTE.strip() in sp
    ask(headers=env["client"])
    _msgs, sp = recorded[-1]
    assert support.VISITOR_NOTE.strip() not in sp


def test_the_visitor_note_says_what_a_visitor_needs(env):
    note = support.VISITOR_NOTE
    assert "Request access" in note
    assert "vadim@motresres.com" in note
    assert "invitation" in note.lower()
    for forbidden in ("customer machine", "catalog's contents"):
        assert forbidden in note, "the note must forbid internal data explicitly"


# ── the prompt itself ────────────────────────────────────────────────────────

def test_the_prompt_no_longer_sells_a_plan():
    """It said "Pro ($19/mo)", "Team ($99/mo)", "save up to 3 designs" and
    "start exploring for free". None of that exists: access is by invitation and
    pricing is agreed one customer at a time."""
    p = support.SYSTEM_PROMPT
    for stale in ("$19", "$99", "Pro (", "Team (", "up to 3 designs",
                  "My designs", "for free", "Free —"):
        assert stale not in p, f"the stale plan text is back: {stale!r}"
    assert "vadim@motresres.com" in p
    assert "invitation" in p.lower()
    assert "Request access" in p


def test_the_prompt_names_the_tabs_the_app_actually_has():
    """Read the tab registry in web/src/App.tsx — the bar is generated from it —
    and hold the prompt to it, so a renamed tab cannot rot into a wrong answer."""
    import re
    src = (_ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    labels = re.findall(r"\{\s*id:\s*'[^']+',\s*label:\s*'([^']+)'", src)
    assert len(labels) >= 12, f"the tab registry moved: {labels}"
    for label in labels:
        assert f"**{label}**" in support.SYSTEM_PROMPT, f"tab not described: {label}"
    # …and it must not resurrect a tab that no longer exists under that name
    assert "**Simulation**" not in support.SYSTEM_PROMPT


def test_the_prompt_answers_the_three_how_tos():
    p = support.SYSTEM_PROMPT
    assert "Load a motor:" in p and "▶" in p
    assert "Coupled thermal" in p
    assert "⭳ report" in p and "⭳ datasheet" in p


# ── the provider's own bad minute ────────────────────────────────────────────

def test_a_503_is_retried_once_so_a_visitor_sees_an_answer(env, monkeypatch):
    """Live on 2026-09-17: three anonymous asks in a row got Gemini's 503 "the
    model is overloaded", and a visitor who reads "Sorry — I couldn't answer
    just now" simply leaves.  The same call succeeded two seconds later."""
    monkeypatch.setattr(support, "_RETRY_PAUSE_S", 0.01)
    calls = {"n": 0}

    class _HTTP503(Exception):
        code = 503

    def flaky(msgs, key, model, sp):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _HTTP503("HTTP Error 503: Service Unavailable")
        return "Access is by invitation."

    monkeypatch.setattr(support, "_effective", lambda: {
        "provider": "gemini",
        "gemini": {"key": "k", "model": "m", "key_source": "env"},
        "anthropic": {"key": "", "model": "m", "key_source": "none"},
    })
    monkeypatch.setattr(support, "_gemini_reply", flaky)
    r = ask()
    assert r.status_code == 200 and r.json()["reply"] == "Access is by invitation."
    assert calls["n"] == 2


def test_a_real_error_is_not_retried(env, monkeypatch):
    calls = {"n": 0}

    def broken(msgs, key, model, sp):
        calls["n"] += 1
        raise ValueError("API key not valid")

    monkeypatch.setattr(support, "_effective", lambda: {
        "provider": "gemini",
        "gemini": {"key": "k", "model": "m", "key_source": "env"},
        "anthropic": {"key": "", "model": "m", "key_source": "none"},
    })
    monkeypatch.setattr(support, "_gemini_reply", broken)
    r = ask()
    assert r.status_code == 200 and r.json()["source"] == "error"
    assert calls["n"] == 1, "a broken key must not be tried twice"


def test_what_counts_as_transient():
    class E(Exception):
        def __init__(self, msg, code=None):
            super().__init__(msg)
            if code is not None:
                self.code = code
    for yes in (E("x", 503), E("x", 429), E("x", 500), E("overloaded"),
                E("The service is currently unavailable"), E("read timed out")):
        assert support._is_transient(yes) is True, yes
    for no in (E("API key not valid", 400), E("permission denied", 403),
               E("model not found", 404)):
        assert support._is_transient(no) is False, no


# ── the ip helper ────────────────────────────────────────────────────────────

class _Req:
    def __init__(self, headers: dict, host: str = "10.1.1.1"):
        self.headers = headers
        self.client = type("C", (), {"host": host})()


def test_client_ip_prefers_the_last_forwarded_entry():
    assert support.client_ip(_Req({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})) == "5.6.7.8"
    assert support.client_ip(_Req({"x-forwarded-for": " 9.9.9.9 "})) == "9.9.9.9"
    assert support.client_ip(_Req({"x-real-ip": "7.7.7.7"})) == "7.7.7.7"
    assert support.client_ip(_Req({"x-forwarded-for": "", "x-real-ip": "7.7.7.7"})) == "7.7.7.7"
    assert support.client_ip(_Req({})) == "10.1.1.1"
    assert support.client_ip(None) == ""


def test_client_ip_steps_over_our_own_plumbing():
    """THE production chain: host nginx (TLS) appends the visitor, the container
    nginx appends the docker bridge.  Reading "the last entry" gave
    ``172.18.0.1`` for everyone — one bucket for the whole internet, which is
    what the live log showed on 2026-09-17 before this was fixed."""
    chain = "203.0.113.9, 172.18.0.1"
    assert support.client_ip(_Req({"x-forwarded-for": chain})) == "203.0.113.9"
    # a forged prefix still cannot win: every hop appends AFTER it
    forged = "8.8.8.8, 203.0.113.9, 172.18.0.1"
    assert support.client_ip(_Req({"x-forwarded-for": forged})) == "203.0.113.9"
    # deeper plumbing, same answer
    assert support.client_ip(
        _Req({"x-forwarded-for": "203.0.113.9, 10.0.0.5, 127.0.0.1, 172.18.0.1"})
    ) == "203.0.113.9"
    # …and an all-internal chain (a LAN call, a dev proxy) keeps its last hop
    assert support.client_ip(_Req({"x-forwarded-for": "10.0.0.5, 172.18.0.1"})) == "172.18.0.1"
    # IPv6 visitors are read the same way
    assert support.client_ip(_Req({"x-forwarded-for": "2001:db8::1, 172.18.0.1"})) == "2001:db8::1"


def test_two_visitors_behind_the_same_bridge_have_their_own_quota(env):
    for _ in range(support.ANON_BURST_MAX):
        assert ask(ip="203.0.113.11, 172.18.0.1").status_code == 200
    assert ask(ip="203.0.113.11, 172.18.0.1").status_code == 429
    assert ask(ip="203.0.113.12, 172.18.0.1").status_code == 200, \
        "the second visitor was punished for the first one's questions"
