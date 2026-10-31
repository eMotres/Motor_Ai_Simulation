"""Translation tests for AeroStator Production module:
Provider mocking, error degradation to 'pending' (saving never blocked),
retry endpoint, and confirmation (as-is and manual edit).
"""
from __future__ import annotations

import pytest
from motor_ai_sim.production import translate as T
from tests.test_production_access import (
    ADMIN,
    CONFIG,
    DIE,
    ENGINEER,
    OEM_NDA,
    prod_env,
)


def test_translate_text_unit(monkeypatch):
    for k in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "PRODUCTION_TRANSLATE_PROVIDER", "PRODUCTION_TRANSLATE_MODEL"):
        monkeypatch.delenv(k, raising=False)

    # 1. Without key, raises TranslateError
    with pytest.raises(T.TranslateError) as exc:
        T.translate_text("Test", "en", "zh")
    assert "no provider key" in str(exc.value)

    # 2. Empty text returns empty string without network
    assert T.translate_text("", "en", "zh") == ""
    assert T.translate_text("   ", "zh", "en") == ""

    # 3. Unsupported language pair
    with pytest.raises(T.TranslateError) as exc:
        T.translate_text("Test", "fr", "de")
    assert "unsupported language pair" in str(exc.value)

    # 4. Mocked http_post succeeds
    mock_response = {
        "choices": [
            {"message": {"content": "转子组件"}}
        ]
    }
    out = T.translate_text("Rotor assembly", "en", "zh", http_post=lambda p, t: mock_response)
    assert out == "转子组件"

    # 5. Mocked http_post raising network error converts to TranslateError
    def failing_post(p, t):
        raise ConnectionResetError("Connection dropped")

    with pytest.raises(T.TranslateError) as exc:
        T.translate_text("Rotor assembly", "en", "zh", http_post=failing_post)
    assert "translation failed" in str(exc.value)

    # 6. Mocked http_post returning empty choices converts to TranslateError
    with pytest.raises(T.TranslateError) as exc:
        T.translate_text("Rotor assembly", "en", "zh", http_post=lambda p, t: {"choices": [{"message": {"content": ""}}]})
    assert "came back empty" in str(exc.value)


def test_translate_payload_denies_data_collection_and_keeps_fallbacks(monkeypatch, caplog):
    """Owner 2026-10-31: OpenRouter must not route to a provider that stores/trains on prompts, and the translation must keep working."""
    monkeypatch.delenv("PRODUCTION_TRANSLATE_MODEL", raising=False)
    monkeypatch.setenv("PRODUCTION_TRANSLATE_PROVIDER", "openrouter")             # OpenRouter is an explicit opt-in now
    seen = []

    def post(payload, timeout):
        seen.append(payload)
        return {"provider": "Fireworks", "choices": [{"message": {"content": "转子组件"}}]}

    with caplog.at_level("INFO", logger=T.log.name):
        assert T.translate_text("Rotor assembly", "en", "zh", http_post=post) == "转子组件"
    assert len(seen) == 1
    assert seen[0]["provider"] == {"data_collection": "deny", "order": ["DeepInfra"], "allow_fallbacks": True}
    assert "ignore" not in seen[0]["provider"]
    assert seen[0]["model"] == T.DEFAULT_MODELS["openrouter"]
    # the provider that answered is logged, nothing else of the exchange
    assert any("Fireworks" in r.getMessage() for r in caplog.records)
    assert not any("Rotor assembly" in r.getMessage() for r in caplog.records)
    # the module constant is not shared with the payload (a caller cannot mutate it through the request)
    seen[0]["provider"]["order"].append("X")
    assert T.PROVIDER_PREFS["order"] == ["DeepInfra"]


def test_a_host_remains_for_deepseek_chat_under_the_provider_rules():
    """The public endpoint list of deepseek/deepseek-chat as fetched on 2026-10-31: no rule of ours removes every host."""
    endpoints = [{"provider_name": "DeepInfra", "tag": "deepinfra/fp4/flex", "status": 0},
                 {"provider_name": "StreamLake", "tag": "streamlake", "status": 0},
                 {"provider_name": "DeepInfra", "tag": "deepinfra/fp4", "status": -2}]
    assert T.usable_endpoints(endpoints) == ["DeepInfra", "StreamLake"]
    assert T.usable_endpoints([{"provider_name": "DeepInfra", "status": -2}]) == []          # all down: nothing usable
    assert T.usable_endpoints([]) == []


def test_translate_with_no_allowed_provider_fails_to_pending():
    """OpenRouter answers 404 when every provider is excluded: the text stays pending, saving is not blocked."""
    def post(payload, timeout):
        raise OSError("HTTP 404: No allowed providers are available for the selected model")

    with pytest.raises(T.TranslateError):
        T.translate_text("Rotor assembly", "en", "zh", http_post=post)


def test_api_translation_degrades_to_pending_and_retry(prod_env, monkeypatch):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]
    oem_nda = prod_env["oem_nda"]

    # Ensure no OpenRouter key is set
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    # Setup production
    init_payload = {
        "title": {
            "en": "Translation Test Production",
            "zh": "翻译测试项目",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "order_quantity": 100,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
        ],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=init_payload)

    # Engineer creates a part with only Chinese name/description (no English translation provided)
    part_payload = {
        "name": {"zh": "高强度定子铁心", "en": "", "src_lang": "zh", "status": {"zh": "confirmed", "en": "pending"}},
        "kind": "made",
        "qty_per_motor": 1,
        "material": "Silicon Steel 0.2mm",
        "spec": "OD140 x ID90 x L40",
        "description": {"zh": "环氧真空浸漆", "en": "", "src_lang": "zh", "status": {"zh": "confirmed", "en": "pending"}},
    }
    # Part creation MUST NOT fail even if translation provider is missing
    create_res = client.post(f"/api/production/{DIE}/{CONFIG}/parts", headers=engineer, json=part_payload)
    assert create_res.status_code == 200
    part = create_res.json()
    part_id = part["id"]
    assert part["name"]["zh"] == "高强度定子铁心"
    assert part["name"]["status"]["en"] == "pending"

    # Now mock translation to simulate provider recovering or key being set
    monkeypatch.setattr(T, "translate_text", lambda text, s, t, **kw: "High-strength stator core" if "铁心" in text else "Epoxy vacuum dipping")

    # Member triggers retry for part name
    retry_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/translate/retry",
        headers=engineer,
        json={"entity": "part", "entity_id": part_id, "field": "name"},
    )
    assert retry_res.status_code == 200

    # Verify part name is now translated with machine status
    parts = client.get(f"/api/production/{DIE}/{CONFIG}/parts", headers=engineer).json()["parts"]
    p = [x for x in parts if x["id"] == part_id][0]
    assert p["name"]["en"] == "High-strength stator core"
    assert p["name"]["status"]["en"] == "machine"

    # Confirm translation as-is
    confirm_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/translate/confirm",
        headers=oem_nda,
        json={"entity": "part", "entity_id": part_id, "field": "name", "lang": "en"},
    )
    assert confirm_res.status_code == 200
    parts = client.get(f"/api/production/{DIE}/{CONFIG}/parts", headers=engineer).json()["parts"]
    p = [x for x in parts if x["id"] == part_id][0]
    assert p["name"]["status"]["en"] == "confirmed"

    # Confirm translation with manual edit override
    edit_confirm_res = client.post(
        f"/api/production/{DIE}/{CONFIG}/translate/confirm",
        headers=engineer,
        json={
            "entity": "part",
            "entity_id": part_id,
            "field": "name",
            "lang": "en",
            "text": "Laminated Stator Core (Custom)",
        },
    )
    assert edit_confirm_res.status_code == 200
    parts = client.get(f"/api/production/{DIE}/{CONFIG}/parts", headers=engineer).json()["parts"]
    p = [x for x in parts if x["id"] == part_id][0]
    assert p["name"]["en"] == "Laminated Stator Core (Custom)"
    assert p["name"]["status"]["en"] == "confirmed"


def test_default_provider_is_anthropic_claude_with_the_same_prompt(monkeypatch, caplog):
    """Owner 2026-10-31 «переводи перевод на Claude»: Anthropic by default, the same system prompt, no OpenRouter fields."""
    for k in ("PRODUCTION_TRANSLATE_PROVIDER", "PRODUCTION_TRANSLATE_MODEL"):
        monkeypatch.delenv(k, raising=False)
    seen = []

    def post(payload, timeout):
        seen.append(payload)
        return {"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "转子组件，公差 H7/k6"}]}

    with caplog.at_level("INFO", logger=T.log.name):
        assert T.translate_text("Rotor assembly, tolerance H7/k6", "en", "zh", http_post=post) == "转子组件，公差 H7/k6"
    p = seen[0]
    assert T.provider() == "anthropic" and p["model"] == "claude-sonnet-5-5" and "temperature" not in p and p["max_tokens"] > 0
    assert p["system"] == T.SYSTEM_PROMPT.format(src="English", tgt="Simplified Chinese")
    assert p["messages"] == [{"role": "user", "content": "Rotor assembly, tolerance H7/k6"}] and "provider" not in p
    assert any("anthropic:claude-sonnet-5-5" in r.getMessage() for r in caplog.records)
    # a stale OpenRouter model id in the env does not reach Anthropic
    monkeypatch.setenv("PRODUCTION_TRANSLATE_MODEL", "deepseek/deepseek-chat")
    assert T.model() == "claude-sonnet-5-5"
    monkeypatch.setenv("PRODUCTION_TRANSLATE_MODEL", "claude-haiku-5-5")
    assert T.model() == "claude-haiku-5-5"
    # the key of the chosen provider is the one required
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    with pytest.raises(T.TranslateError) as exc:
        T.translate_text("Shaft", "en", "zh")
    assert "no provider key" in str(exc.value)
