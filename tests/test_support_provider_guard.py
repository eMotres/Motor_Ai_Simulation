"""The help / specification assistants do not train a model provider on user content (owner decision D3, 2026-10-31).

* production default = Anthropic (``ANTHROPIC_SUPPORT_MODEL``, default ``claude-haiku-5-5``);
* Gemini is refused unless the key is explicitly marked paid (``GEMINI_PAID_TIER=1``): logged once, then Anthropic when it has
  a key, else the "unavailable" (mock) path;
* the specification assistant goes through the same guarded ``_effective``.

No test touches a provider: the Firestore override loader and the provider clients are stubbed.
"""
from __future__ import annotations

import importlib

import pytest

from motor_ai_sim.development import spec_assistant as A
from motor_ai_sim.routes import support


@pytest.fixture()
def cfg(monkeypatch):
    """Control the env-derived module constants and the admin override."""
    state = {"ov": {}}
    monkeypatch.setattr(support, "_load_overrides", lambda: state["ov"])
    monkeypatch.setattr(support, "ENV_PROVIDER", "")
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "")
    monkeypatch.setattr(support, "ENV_ANTHROPIC_KEY", "")
    monkeypatch.delenv("GEMINI_PAID_TIER", raising=False)
    monkeypatch.setattr(support, "_gemini_refusal_logged", False)
    return state


def test_default_anthropic_model_is_haiku_5_5(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_SUPPORT_MODEL", raising=False)
    try:
        assert importlib.reload(support).ENV_ANTHROPIC_MODEL == "claude-haiku-5-5"
        monkeypatch.setenv("ANTHROPIC_SUPPORT_MODEL", "claude-x-1")
        assert importlib.reload(support).ENV_ANTHROPIC_MODEL == "claude-x-1"
    finally:
        monkeypatch.delenv("ANTHROPIC_SUPPORT_MODEL", raising=False)
        importlib.reload(support)


def test_auto_prefers_anthropic_even_when_a_gemini_key_exists(cfg, monkeypatch):
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "g")
    monkeypatch.setattr(support, "ENV_ANTHROPIC_KEY", "a")
    assert support._effective()["provider"] == "anthropic"


def test_auto_with_only_an_unpaid_gemini_key_is_unavailable(cfg, monkeypatch):
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "g")
    eff = support._effective()
    assert eff["provider"] == "none" and eff["gemini_blocked"] is True


@pytest.mark.parametrize("how", ["env", "admin"])
def test_explicit_gemini_without_the_paid_flag_falls_back_to_anthropic(cfg, monkeypatch, how):
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "g")
    monkeypatch.setattr(support, "ENV_ANTHROPIC_KEY", "a")
    if how == "env":
        monkeypatch.setattr(support, "ENV_PROVIDER", "gemini")
    else:
        cfg["ov"] = {"provider": "gemini"}
    eff = support._effective()
    assert eff["provider"] == "anthropic" and eff["gemini_blocked"] is True
    assert support.provider_status()["geminiBlocked"] is True


def test_explicit_gemini_without_the_paid_flag_and_no_anthropic_key_is_unavailable(cfg, monkeypatch):
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "g")
    monkeypatch.setattr(support, "ENV_PROVIDER", "gemini")
    assert support._effective()["provider"] == "none"
    assert support.provider_status()["configured"] is False


@pytest.mark.parametrize("flag", ["", "0", "true", "yes", "2"])
def test_only_the_exact_value_one_marks_gemini_paid(cfg, monkeypatch, flag):
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "g")
    monkeypatch.setattr(support, "ENV_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_PAID_TIER", flag)
    assert support._effective()["provider"] == "none"


def test_gemini_is_allowed_when_marked_paid(cfg, monkeypatch):
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "g")
    monkeypatch.setattr(support, "ENV_ANTHROPIC_KEY", "a")
    monkeypatch.setattr(support, "ENV_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_PAID_TIER", "1")
    eff = support._effective()
    assert eff["provider"] == "gemini" and eff["gemini_blocked"] is False


def test_the_refusal_is_logged_once(cfg, monkeypatch, caplog):
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "g")
    monkeypatch.setattr(support, "ENV_ANTHROPIC_KEY", "a")
    monkeypatch.setattr(support, "ENV_PROVIDER", "gemini")
    with caplog.at_level("WARNING", logger=support.logger.name):
        for _ in range(5):
            support._effective()
    assert len([r for r in caplog.records if "Gemini refused" in r.getMessage()]) == 1


def test_the_spec_assistant_uses_anthropic_when_gemini_is_unpaid(cfg, monkeypatch):
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "g")
    monkeypatch.setattr(support, "ENV_ANTHROPIC_KEY", "a")
    monkeypatch.setattr(support, "ENV_PROVIDER", "gemini")
    calls = {"gemini": 0, "anthropic": []}

    def no_gemini(*a, **k):
        calls["gemini"] += 1
        return "NO"

    class _Block:
        type, text = "text", "from claude"

    class _Client:
        class messages:                                       # noqa: N801
            @staticmethod
            def create(**kw):
                calls["anthropic"].append(kw["model"])
                return type("R", (), {"content": [_Block()]})()

    monkeypatch.setattr(support, "_gemini_reply", no_gemini)
    monkeypatch.setattr(support, "_anthropic_client", lambda key: _Client())
    reply, source, model = A.complete([{"role": "user", "content": "hi"}], "sys")
    assert (reply, source) == ("from claude", "claude") and calls["gemini"] == 0 and calls["anthropic"]


def test_the_spec_assistant_is_unavailable_with_only_an_unpaid_gemini_key(cfg, monkeypatch):
    monkeypatch.setattr(support, "ENV_GEMINI_KEY", "g")
    monkeypatch.setattr(support, "ENV_PROVIDER", "gemini")
    monkeypatch.setattr(support, "_gemini_reply", lambda *a, **k: pytest.fail("Gemini must not be called"))
    with pytest.raises(A.AssistantUnavailable):
        A.complete([{"role": "user", "content": "hi"}], "sys")
