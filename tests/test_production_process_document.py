"""The process document generated from the assembly steps (owner 2026-10-18): graph numbering, sections per step."""
from motor_ai_sim.production import process_steps as PS


def _sec(sid, en, body="", zh=""):
    return {"id": sid, "kind": "winding", "title": {"en": en, "zh": zh or en}, "body": {"en": body, "zh": body},
            "tables": [[["Item", "Value"], ["Turns", "33"]]], "tables_i18n": {}}


def test_document_follows_step_numbers_and_strips_old_section_numbers():
    steps = PS.default_steps("motor")
    sections = [_sec("w", "6a. Winding and wiring", "16 coils"), _sec("m", "3. Magnet insertion"),
                _sec("x", "9. Packing and shipping")]
    assert [s["id"] for s in PS.related_sections(steps[0], sections)] == ["w"]
    doc = PS.document_html(steps, sections, "CIANO14 40 L20", 3, "en")
    assert "1.1 Winding and wiring" in doc and "6a." not in doc
    # the winding section is printed once (step 1) and referenced from step 2
    assert doc.count("16 coils") == 1 and "see 1.1" in doc
    assert "5.1 Magnet insertion" in doc
    # a section no step names lands in General, numbered after the last step
    last = max(PS.step_number(s, i) for i, s in enumerate(steps))
    assert "%d.1 Packing and shipping" % (last + 1) in doc
    assert "<td>33</td>" in doc
    zh = PS.document_html(steps, sections, "CIANO14 40 L20", 3, "zh")
    assert "装配与测试工艺" in zh and 'lang="zh"' in zh


def test_document_escapes_text():
    doc = PS.document_html([], [_sec("a", "<b>x</b>", "a < b")], "T", None, "en")
    assert "<b>x</b>" not in doc and "a &lt; b" in doc


def test_keywords_match_whole_words_and_own_sections_win():
    steps = {s["id"]: s for s in PS.default_steps("motor")}
    secs = [_sec("b", "Bearing Acceptance"), _sec("w", "6a. Winding and Wiring"), _sec("r", "8.1 Blue ring")]
    assert [s["id"] for s in PS.related_sections(steps["s4"], secs)] == ["r"]     # not bearing / wiring
    own = dict(steps["s4"], sections=["b"])
    assert [s["id"] for s in PS.related_sections(own, secs)] == ["b"]
    assert PS.normalize_step(dict(own))["sections"] == ["b"]
