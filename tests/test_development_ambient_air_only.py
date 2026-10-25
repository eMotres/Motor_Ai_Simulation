"""The maximum ambient temperature (3_2) is asked and required for air cooling only (owner 2026-10-26).

Liquid cooling takes the coolant inlet temperature, underwater cooling the water temperature (both in 3_1).  While no
medium is chosen the ambient stays asked (air is the default assumption).  The same rule lives in the web
(`fieldApplies`, wizardState.ts); web/src/modules/development/__tests__/ambientAirOnly.test.mjs mirrors these cases.
All values are made up."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from motor_ai_sim.development import models


def _field(fid):
    return next(f for s in models.schema(2)["sections"] for f in s["fields"] if f["id"] == fid)


def _draft(cooling=None, ambient=None, cooling_status="filled"):
    fields = {}
    if cooling is not None:
        fields["3_1"] = {"status": cooling_status, "value": cooling}
    if ambient is not None:
        fields["3_2"] = ambient
    return {"schema_version": 2, "fields": fields}


def _required_ids(draft):
    return [f["id"] for s in models.schema(2)["sections"] for f in s["fields"] if models._field_required(f, draft)]


def test_schema_marks_3_2_air_only_and_keeps_every_stored_property():
    f = _field("3_2")
    assert f["applies_when_cooling_medium"] == ["air"]
    # nothing is deleted from the schema: a stored minimum and altitude stay valid data
    assert {p["id"] for p in f["properties"]} == {"temperature_min_C", "temperature_max_C", "altitude_m"}
    assert [p["id"] for p in f["properties"] if p.get("required")] == ["temperature_max_C"]


CASES = json.loads((Path(__file__).resolve().parents[1] / "web/src/modules/development/__tests__/ambient_cases.json").read_text(encoding="utf8"))


@pytest.mark.parametrize("case", CASES["applies"], ids=lambda c: json.dumps(c["cooling"]) + "/" + c["status"])
def test_ambient_applies_only_to_air_or_an_unchosen_medium(case):
    """The shared case table: the web test runs the very same rows through `fieldApplies`."""
    d = _draft(case["cooling"], cooling_status=case["status"])
    assert models.field_applies(_field("3_2"), d) is case["applies"]
    assert ("3_2" in _required_ids(d)) is case["applies"]


def test_a_cooling_that_is_not_known_yet_is_no_choice():
    d = _draft({"cooling_medium": "liquid"}, cooling_status="not_known_yet")
    assert models.field_applies(_field("3_2"), d) is True


def test_other_fields_are_unaffected():
    ids = _required_ids(_draft({"cooling_medium": "submerged"}))
    assert "3_1" in ids and "3_3" in ids and "3_2" not in ids


def test_completeness_and_validation_skip_a_hidden_ambient():
    base = _draft({"cooling_medium": "liquid", "coolant": "Water", "inlet_temperature_C": 40})
    with_air = _draft({"cooling_medium": "air"})
    hidden = models.completeness(base)
    shown = models.completeness(with_air)
    # one required field fewer, and it is neither missing nor counted
    assert hidden["total_required"] == shown["total_required"] - 1
    assert not any(e["field"].startswith("3_2") for e in models.validation_errors(base))
    assert any(e["field"].startswith("3_2") for e in models.validation_errors(with_air))
    # section totals agree with the field count
    assert hidden["sections"]["3"]["required"] == shown["sections"]["3"]["required"] - 1


def test_a_stored_ambient_is_kept_and_not_judged_when_hidden():
    d = _draft({"cooling_medium": "liquid"}, ambient={"status": "filled", "value": {"temperature_max_C": 45, "altitude_m": 1200}})
    normalized = models.normalize_draft(d)
    assert normalized["fields"]["3_2"]["value"] == {"temperature_max_C": 45, "altitude_m": 1200}
    assert not any(e["field"].startswith("3_2") for e in models.validation_errors(normalized))


def test_a_filled_ambient_with_air_cooling_is_complete_without_altitude_or_minimum():
    d = _draft({"cooling_medium": "air"}, ambient={"status": "filled", "value": {"temperature_max_C": 45}})
    assert models._answer_complete(d["fields"]["3_2"], _field("3_2")) is True
    assert not any(e["field"].startswith("3_2") for e in models.validation_errors(d))


@pytest.mark.parametrize("case", CASES["thermal_basis"], ids=lambda c: c["medium"] + "/" + str(c["temperature_C"]))
def test_thermal_basis_follows_the_cooling_medium(case):
    ambient = {"status": "filled", "value": case["ambient"]} if case["ambient"] else None
    basis = models.thermal_basis(_draft(case["cooling"], ambient))
    assert (basis["medium"], basis["source"], basis["temperature_C"]) == (case["medium"], case["source"], case["temperature_C"])


@pytest.mark.parametrize("case", CASES["cooling_required"], ids=lambda c: json.dumps(c["cooling"]))
def test_cooling_temperature_required_for_liquid_and_underwater_only(case):
    """Liquid needs the coolant inlet temperature, underwater the water temperature, air neither (3_2 is its basis)."""
    props = _field("3_1")["properties"]
    required = [p["id"] for p in props if models._property_required(p, case["cooling"])]
    assert required == case["required"]


@pytest.mark.parametrize("case", CASES["cooling_complete"], ids=lambda c: json.dumps(c["cooling"]))
def test_cooling_answer_completeness_and_validation(case):
    answer = {"status": "filled", "value": case["cooling"]}
    assert models._answer_complete(answer, _field("3_1")) is case["complete"]
    errors = [e["field"] for e in models.validation_errors(_draft(case["cooling"])) if e["field"].startswith("3_1.")]
    assert (errors == []) is case["complete"], errors


def test_not_known_yet_cooling_needs_no_temperature():
    d = _draft({"cooling_medium": "liquid"}, cooling_status="not_known_yet")
    assert models._answer_complete(d["fields"]["3_1"], _field("3_1")) is True
    assert not any(e["field"].startswith("3_1.") for e in models.validation_errors(d))


def _section3_answered(draft):
    return models.completeness(draft)["sections"]["3"]["answered"]


def test_air_cooled_project_stays_complete_and_liquid_one_needs_the_inlet():
    air = _draft({"cooling_type": "forced air", "airflow": {"mode": "Fan"}}, {"status": "filled", "value": {"temperature_max_C": 45}})
    liquid = _draft({"cooling_medium": "liquid", "coolant": "Water"})
    assert _section3_answered(air) == 2        # 3_1 and 3_2 (3_3 is not filled in these drafts)
    assert _section3_answered(liquid) == 0     # 3_1 incomplete without the inlet; 3_2 is not required
    liquid["fields"]["3_1"]["value"]["inlet_temperature_C"] = 55
    assert _section3_answered(liquid) == 1


# --- load x cooling medium x airflow mode (owner 2026-10-26) ----------------------------------------------------
MATRIX = CASES["load_matrix"]
MODE_LABEL = {"propeller wash": "Propeller wash", "ram air": "Ram air", "still air": "Still air", "fan": "Fan"}


def test_the_backend_tables_equal_the_shared_matrix():
    assert {g: tuple(m["media"]) for g, m in MATRIX.items()} == models._LOAD_COOLING
    assert {g: tuple(m["airflow"]) for g, m in MATRIX.items()} == models._AIRFLOW_BY_LOAD
    assert {g: m["default_airflow"] for g, m in MATRIX.items()} == models._DEFAULT_AIRFLOW
    for g, m in MATRIX.items():
        assert m["default_airflow"] in m["airflow"], g          # a default is always allowed
    assert "ram air" in MATRIX["air_propeller"]["airflow"] and MATRIX["air_propeller"]["default_airflow"] != "ram air"


@pytest.mark.parametrize("load,stored", [(g, g) for g in MATRIX] + [("marine_propeller", "water_jet"), ("shaft", "pump"),
                                                                   ("shaft", "engine_driven_generator"), ("shaft", "robot_joint")])
def test_every_load_x_medium_x_airflow_combination(load, stored):
    """Each cell of the matrix: an excluded medium or a standard airflow mode the load cannot make is a loud error (never
    changed silently); underwater cooling never judges an airflow; a liquid jacket on a water propeller has no airflow."""
    cell = MATRIX[load]
    for medium in ("air", "liquid", "submerged"):
        for mode in [None, *MODE_LABEL.values()]:
            cooling = {"cooling_medium": medium, **({"airflow": {"mode": mode}} if mode else {})}
            if medium == "liquid":
                cooling["inlet_temperature_C"] = 50
            expected = []
            if medium not in cell["media"]:
                expected.append("3_1.cooling_medium")
            asked = medium == "air" or (medium == "liquid" and load != "marine_propeller")
            if mode and asked and mode.lower() not in cell["airflow"]:
                expected.append("3_1.airflow.mode")
            got = [e["field"] for e in models.cooling_load_errors(_load_cooling_draft(stored, cooling))]
            assert got == expected, (load, medium, mode)


def _load_cooling_draft(load, cooling):
    return {"schema_version": 2, "fields": {"load_type": {"status": "filled", "value": load}, "3_1": {"status": "filled", "value": cooling}}}


def test_a_typed_own_airflow_and_a_stored_excluded_combination():
    # a typed own description is not judged; a stored excluded combination is an error, the data are not changed
    assert models.cooling_load_errors(_load_cooling_draft("shaft", {"cooling_medium": "air", "airflow": {"mode": "duct from the radiator fan"}})) == []
    stored = _load_cooling_draft("wheel", {"cooling_medium": "submerged"})
    before = json.dumps(stored, sort_keys=True)
    assert [e["field"] for e in models.validation_errors(stored)].count("3_1.cooling_medium") == 1
    assert json.dumps(stored, sort_keys=True) == before
    # the medium the form shows (inferred from the stored values) is judged the same way
    assert [e["field"] for e in models.cooling_load_errors(_load_cooling_draft("air_propeller", {"cooling_type": "submerged in sea water"}))] == ["3_1.cooling_medium"]


# --- max immersion depth: underwater only, moved out of the cooling block ---------------------------------------
@pytest.mark.parametrize("case", CASES["depth_applies"], ids=lambda c: json.dumps(c["cooling"]) + "/" + c["status"])
def test_immersion_depth_applies_only_underwater(case):
    f = _field("3_6")
    d = _draft(case["cooling"], cooling_status=case["status"])
    assert models.field_applies(f, d) is case["applies"]
    assert "3_6" not in _required_ids(d)                      # never required


def test_immersion_depth_field_and_the_legacy_cooling_depth():
    f = _field("3_6")
    assert f["only_when_cooling_medium"] == ["submerged"] and not f.get("required") and f["unit"] == "m" and f["min"] == 0
    assert f["short_label"]["en"] == "Max immersion depth (sealing)"
    depth = next(p for p in _field("3_1")["properties"] if p["id"] == "depth_m")
    assert depth["moved_to"] == "3_6" and not depth.get("required")        # still valid stored data
    # a stored 3_1 depth shows in the new place; nothing is lost and a cleared 3_6 stays cleared
    stored = _draft({"cooling_medium": "submerged", "water_temperature_C": 20, "depth_m": 35})
    before = json.dumps(stored, sort_keys=True)
    lifted = models.lift_legacy_depth(stored)
    assert lifted["fields"]["3_6"] == {"status": "filled", "value": 35}
    assert lifted["fields"]["3_1"]["value"]["depth_m"] == 35
    assert json.dumps(stored, sort_keys=True) == before                       # the stored draft is untouched
    assert models.normalize_draft(lifted)["fields"]["3_6"]["value"] == 35     # and the lifted draft saves
    cleared = _draft({"cooling_medium": "submerged", "depth_m": 35})
    cleared["fields"]["3_6"] = {"status": "missing", "value": ""}
    assert models.lift_legacy_depth(cleared)["fields"]["3_6"] == {"status": "missing", "value": ""}
    assert "3_6" not in models.lift_legacy_depth(_draft({"cooling_medium": "air"}))["fields"]


def test_no_guaranteed_in_the_customer_text():
    text = json.dumps(models.schema(2), ensure_ascii=False)
    assert "uaranteed" not in text and "保证" not in text
    labels = {p["id"]: p["label"] for p in _field("3_1")["properties"] + _field("3_1")["properties"][2]["properties"]}
    assert labels["speed_mps"]["en"] == "Air speed" and labels["flow_rate"]["en"] == "Coolant flow"
    assert labels["water_speed_mps"]["en"] == "Water flow speed"
