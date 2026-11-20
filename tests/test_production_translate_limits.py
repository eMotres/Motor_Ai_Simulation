"""Limits on the paid production translation: text length, a persistent daily allowance per account and for the server, a bounded
number of concurrent provider calls, and an unknown thread is a 404 before anything is translated (security review 2026-11-19, A08)."""
from __future__ import annotations

import threading

import pytest

from motor_ai_sim import workspace as WS
from motor_ai_sim.production import translate as T
from motor_ai_sim.production import translate_budget as TB
from tests.test_production_access import CONFIG, DIE, ENGINEER, OEM_NDA, prod_env  # noqa: F401  (fixture)

ANSWER = {"choices": [{"message": {"content": "译文"}}], "provider": "p"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "prod"))
    for k in ("PRODUCTION_TRANSLATE_MAX_CHARS", "PRODUCTION_TRANSLATE_DAILY_CHARS_PER_ACCOUNT",
              "PRODUCTION_TRANSLATE_DAILY_CHARS_GLOBAL", "PRODUCTION_TRANSLATE_CONCURRENCY"):
        monkeypatch.delenv(k, raising=False)


class Provider:
    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def __call__(self, payload, timeout):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return ANSWER


def _as(email):
    class _Cm:
        def __enter__(self):
            self.c = WS.use_caller({"id": email, "is_admin": False})
            self.c.__enter__()

        def __exit__(self, *a):
            self.c.__exit__(*a)
    return _Cm()


def test_a_text_over_the_cap_is_refused_before_the_provider(monkeypatch):
    p = Provider()
    with pytest.raises(T.TranslateError, match="longer than 8000"):
        T.translate_text("x" * 8001, "en", "zh", http_post=p)
    assert p.calls == 0
    assert T.translate_text("x" * 8000, "en", "zh", http_post=p) == "译文" and p.calls == 1
    monkeypatch.setenv("PRODUCTION_TRANSLATE_MAX_CHARS", "0")                     # 0 switches the cap off
    assert T.translate_text("x" * 9000, "en", "zh", http_post=p) == "译文"


def test_the_daily_allowance_is_per_account_and_persistent(monkeypatch):
    monkeypatch.setenv("PRODUCTION_TRANSLATE_DAILY_CHARS_PER_ACCOUNT", "100")
    p = Provider()
    with _as("a@example.test"):
        assert T.translate_text("a" * 60, "en", "zh", http_post=p) == "译文"
        with pytest.raises(T.TranslateError, match="allowance of this account"):
            T.translate_text("a" * 60, "en", "zh", http_post=p)
        assert T.translate_text("b" * 40, "en", "zh", http_post=p) == "译文"        # exactly up to the allowance
    with _as("b@example.test"):
        assert T.translate_text("a" * 60, "en", "zh", http_post=p) == "译文"        # another account has its own
    assert p.calls == 3
    assert TB._load()[TB._today()]["accounts"]["a@example.test"] == 100             # it is on disk, not in memory


def test_the_server_wide_allowance_stops_every_account(monkeypatch):
    monkeypatch.setenv("PRODUCTION_TRANSLATE_DAILY_CHARS_GLOBAL", "100")
    p = Provider()
    with _as("a@example.test"):
        T.translate_text("a" * 70, "en", "zh", http_post=p)
    with _as("b@example.test"):
        with pytest.raises(T.TranslateError, match="allowance of the server"):
            T.translate_text("a" * 70, "en", "zh", http_post=p)


def test_a_failed_call_gives_the_reserved_characters_back(monkeypatch):
    monkeypatch.setenv("PRODUCTION_TRANSLATE_DAILY_CHARS_PER_ACCOUNT", "100")
    with _as("a@example.test"):
        for _ in range(3):
            with pytest.raises(T.TranslateError):
                T.translate_text("a" * 80, "en", "zh", http_post=Provider(fail=True))
        assert T.translate_text("a" * 80, "en", "zh", http_post=Provider()) == "译文"      # nothing was spent by the failures


def test_provider_calls_run_a_few_at_a_time(monkeypatch):
    monkeypatch.setenv("PRODUCTION_TRANSLATE_CONCURRENCY", "1")
    monkeypatch.setattr(TB, "SLOT_WAIT_S", 0.05)
    inside, release = threading.Event(), threading.Event()

    def slow(payload, timeout):
        inside.set()
        release.wait(5)
        return ANSWER
    t = threading.Thread(target=lambda: T.translate_text("one", "en", "zh", http_post=slow), daemon=True)
    t.start()
    assert inside.wait(5)
    with pytest.raises(T.TranslateError, match="too many translations"):
        T.translate_text("two", "en", "zh", http_post=Provider())
    release.set()
    t.join(5)
    assert T.translate_text("three", "en", "zh", http_post=Provider()) == "译文"


def test_a_message_to_an_unknown_thread_is_a_404_before_any_translation(prod_env, monkeypatch):
    c = prod_env["client"]
    payload = {"title": {"en": "P", "zh": "P", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}}, "order_quantity": 10,
               "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}]}
    assert c.post("/api/admin/production/%s/%s" % (DIE, CONFIG), json=payload, headers=prod_env["admin"]).status_code in (200, 201)
    p = Provider()
    monkeypatch.setattr(T, "_post", p)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r = c.post("/api/production/%s/%s/threads/no-such-thread/messages" % (DIE, CONFIG),
               json={"text": {"en": "a long monolingual message", "src_lang": "en"}, "src_lang": "en"}, headers=prod_env["engineer"])
    assert r.status_code == 404 and p.calls == 0
