"""Window-aware help assistant (owner 2026-11-26): ui_context sanitiser, role drop, caps, the "user is here" prompt part, the help
documents loaded by tab id, and the rule that every registered window has an EN and a zh-CN document.  No provider call."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from motor_ai_sim import help_context as H
from motor_ai_sim import support_context as C
from motor_ai_sim.routes import support

DOCS = Path(H.__file__).resolve().parent / "help_docs"

GEARS = {"tab": "gears", "sub": "motor", "preset": "humanoid_robot_joint", "stageKinds": ["planetary", "pair"], "stageCount": 2,
         "calculated": True, "stale": False, "die": "CIANO56 400_80", "configuration": "cfg A", "source": "inline", "rows": 2,
         "status": ["row_ok", "row_missing"], "error": "/load/1/torque_out_Nm: must be above 12.5", "record": False,
         "secretNote": "x", "price": 5, "mode": "calc"}


def test_every_registered_window_has_en_and_zh_documents():
    reg = H.registry()
    assert "gears" in reg
    for tab, entry in reg.items():
        assert tab in entry["docs"], f"{tab} needs its overview document"
        for sub in entry.get("subs", []):
            assert f"{tab}.{sub}" in entry["docs"], f"{tab}.{sub} is a registered sub-view without a document"
        for d in entry["docs"]:
            for lang in ("en", "zh-CN"):
                p = DOCS / f"{d}.{lang}.md"
                assert p.is_file() and p.read_text(encoding="utf-8").strip(), f"missing help document {p.name}"
            assert len((DOCS / f"{d}.en.md").read_text(encoding="utf-8").split()) <= 350, f"{d} is longer than 350 words"


def test_the_gears_ids_are_registered():
    reg = H.registry()["gears"]
    assert reg["subs"] == ["point", "spectrum", "assembly", "motor"]
    assert {"gears.point", "gears.spectrum", "gears.assembly", "gears.motor", "gears.saved"} <= set(reg["docs"])


def test_engineer_keeps_whitelisted_keys_only_and_no_numbers():
    out = H.sanitize_ui_context(GEARS, "engineer")
    assert out["tab"] == "gears" and out["sub"] == "motor"
    assert out["die"] == "CIANO56 400_80" and out["stageCount"] == 2 and out["status"] == ["row_ok", "row_missing"]
    assert "secretNote" not in out and "price" not in out
    assert out["error"] == "/load/#/torque_out_Nm: must be above #"      # every digit run masked
    assert "12.5" not in json.dumps(out)


def test_numbers_and_floats_never_ride_as_values():
    out = H.sanitize_ui_context({**GEARS, "stageCount": 2.5, "rows": 1000, "calculated": "yes"}, "admin")
    assert "stageCount" not in out and "rows" not in out and "calculated" not in out


@pytest.mark.parametrize("role", ["anon", "", "user", "oem"])
def test_role_below_the_tab_minimum_gets_tab_ids_only(role):
    assert H.sanitize_ui_context(GEARS, role) == {"tab": "gears", "sub": "motor"}


def test_unknown_tab_and_bad_ids():
    assert H.sanitize_ui_context({"tab": "cost", "sub": "x", "motor": "M1"}, "admin") == {"tab": "cost", "sub": "x"}
    assert H.sanitize_ui_context({"tab": "../etc", "sub": "a"}, "admin") == {}
    assert H.sanitize_ui_context({"tab": "gears", "sub": "nope"}, "admin") == {"tab": "gears"}
    assert H.sanitize_ui_context("x", "admin") == {} and H.sanitize_ui_context(None, "admin") == {}


def test_caps_and_secret_scrub():
    big = {"tab": "gears", "sub": "point", "preset": "p" * 500, "stageKinds": ["planetary"] * 50,
           "die": "Bearer abcdefghijklmnop1234", "error": "token=hunter2 " + "z" * 900}
    out = H.sanitize_ui_context(big, "admin")
    assert len(out["preset"]) <= H.MAX_STR and len(out["stageKinds"]) <= H.MAX_LIST
    assert "abcdefghijklmnop" not in out["die"] and "hunter#" not in out["error"] and "hunter2" not in out["error"]
    assert len(out["error"]) <= H.MAX_ERROR
    assert len(json.dumps(out, ensure_ascii=False)) <= H.MAX_JSON_CHARS


def test_sanitize_context_routes_ui_context_through_the_role():
    raw = {"tab": "gears", "lang": "en", "ui_context": GEARS}
    assert C.sanitize_context(raw, "engineer")["ui_context"]["die"] == "CIANO56 400_80"
    assert C.sanitize_context(raw, "user")["ui_context"] == {"tab": "gears", "sub": "motor"}
    assert C.sanitize_context(raw)["ui_context"] == {"tab": "gears", "sub": "motor"}          # no role: ids only (a ticket)


def test_docs_are_loaded_by_tab_id_and_language():
    en = H.help_docs_for({"tab": "gears", "sub": "motor"}, "en")
    assert "With motor" in en and "Gears (Simulations > Gears)" in en
    assert "# Gears: Saved gearboxes" not in en                                              # the saved document only with a record
    zh = H.help_docs_for({"tab": "gears", "sub": "motor"}, "zh-CN")
    assert "配电机" in zh
    saved = H.help_docs_for({"tab": "gears", "sub": "point", "record": True}, "en")
    assert "# Gears: Saved gearboxes" in saved and "# Gears: Point" in saved
    assert len(saved) <= H.MAX_DOC_CHARS


def test_no_document_for_an_unknown_id_or_a_forged_one():
    assert H.help_docs_for({"tab": "cost"}, "en") == ""
    assert H.help_docs_for({"tab": "gears", "sub": "../../registry"}, "en").count("# Gears") == 1       # only the overview
    assert H._read_doc("../registry", "en") == ""


def test_doc_cap_is_enforced(monkeypatch):
    monkeypatch.setattr(H, "MAX_DOC_CHARS", 300)
    assert len(H.help_docs_for({"tab": "gears", "sub": "point"}, "en")) <= 300


def test_prompt_has_user_is_here_first_and_rules():
    ctx = C.sanitize_context({"lang": "en", "tab": "gears", "ui_context": GEARS}, "engineer")
    sp = support.build_system_prompt("engineer", anon=False, context=ctx)
    assert "The user is here" in sp and "The user is here" in sp.split("## Session context")[0]
    assert sp.index("The user is here") < sp.index("## Session context")
    assert "ONLY source for UI steps" in sp and "do not have the step-by-step" in sp
    assert "# Gears: With motor" in sp and "CIANO56 400_80" in sp
    assert "12.5" not in sp                                                                  # no number from the error


def test_window_without_a_document_says_so():
    ctx = C.sanitize_context({"lang": "en", "tab": "cost", "ui_context": {"tab": "cost"}}, "admin")
    sp = support.build_system_prompt("admin", anon=False, context=ctx)
    assert "No help document exists for this window" in sp and "do not have the step-by-step" in sp


def test_visitor_gets_tab_ids_only():
    ctx = C.sanitize_context({"lang": "en", "ui_context": GEARS, "configure": {"a": 1}}, "anon")
    assert ctx["ui_context"] == {"tab": "gears", "sub": "motor"}
    sp = support.build_system_prompt("anon", anon=True, context=ctx)
    assert "The user is here" in sp and "CIANO56" not in sp and "humanoid" not in sp


def test_spec_mode_has_no_window_block():
    ctx = C.sanitize_context({"ui_context": {"tab": "development"}}, "admin")
    assert "The user is here" not in support.build_system_prompt("admin", anon=False, context=ctx, spec=True)


def test_added_tokens_stay_small():
    ctx = C.sanitize_context({"lang": "en", "ui_context": GEARS}, "engineer")
    block = C.render_for_model(ctx, role="engineer")
    assert len(block) <= H.MAX_DOC_CHARS + 2500 + len(H.WINDOW_RULES)                        # ~ 1500 tokens at 4+ chars/token
