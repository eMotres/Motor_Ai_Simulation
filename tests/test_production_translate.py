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
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

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
