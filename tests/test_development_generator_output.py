"""Owner 2026-10-27 (review F01): field 2_13 "Generator output window" is ONE new optional, additive field of schema v2: the AC voltage
a generator delivers, with its definition (line/phase, RMS/peak) and the speeds it applies at.  The DC fields (2_1, key point
voltage_V) keep their meaning in every project; nothing migrates or converts.  All values are made up."""
from motor_ai_sim.development import models


def _field():
    return models._field_map(2)["2_13"][1]


def _draft(value, status="filled", version=2):
    return {"schema_version": version, "fields": {"2_13": {"status": status, "value": value}},
            "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}


def test_schema_field_is_optional_additive_with_zh_mirror():
    field = _field()
    assert field["type"] == "object" and field["required"] is False and field["allow_not_known_yet"] is True
    assert field["checklist_id"] == "2.13" and field["presentation_group"] == "2"
    props = {p["id"]: p for p in field["properties"]}
    assert list(props) == ["ac_min_V", "ac_max_V", "basis", "at_rpm_min", "at_rpm_max"]
    assert [o["value"] for o in props["basis"]["options"]] == ["line_line_rms", "line_line_peak", "phase_rms"]
    for p in props.values():
        assert not p["required"]
    for item in (field, *props.values(), *props["basis"]["options"]):
        assert item["label"]["zh-CN"].strip() and item["label"]["en"].strip()
    assert field["short_label"]["zh-CN"].strip() and field["help"]["en"].strip() and field["help"]["zh-CN"].strip()


def test_v1_schema_is_untouched():
    assert "2_13" not in models._field_map(1)


def test_completeness_and_required_set_are_unaffected():
    empty = models.normalize_draft({"schema_version": 2, "fields": {}})
    assert models.completeness(empty)["total_required"] == 15
    filled = models.normalize_draft(_draft({"ac_min_V": 380, "ac_max_V": 520, "basis": "line_line_rms", "at_rpm_min": 600, "at_rpm_max": 1500}))
    assert models.completeness(filled)["total_required"] == 15
    assert filled["fields"]["2_13"]["value"]["basis"] == "line_line_rms"
    required = [f for sec in models.schema(2)["sections"] for f in sec["fields"] if f.get("required")]
    assert "2_13" not in [f["id"] for f in required]


def test_a_partial_answer_and_unknown_are_accepted():
    assert models.normalize_draft(_draft({"ac_max_V": 400}))["fields"]["2_13"]["value"] == {"ac_max_V": 400}
    assert models.normalize_draft(_draft(None, status="not_known_yet"))["fields"]["2_13"]["status"] == "not_known_yet"


def test_bad_values_are_rejected_loudly():
    for bad in ({"basis": "delta"}, {"ac_min_V": -1}, {"at_rpm_max": "fast"}):
        try:
            models.normalize_draft(_draft(bad))
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_existing_dc_answers_are_never_converted():
    dc = {"voltage_nominal_V": 700, "voltage_min_V": 633, "voltage_max_V": 806}
    draft = {"schema_version": 2, "fields": {"2_1": {"status": "filled", "value": dc}, "2_13": {"status": "filled", "value": {"ac_max_V": 400}}},
             "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}
    out = models.normalize_draft(draft)
    assert out["fields"]["2_1"]["value"] == dc
