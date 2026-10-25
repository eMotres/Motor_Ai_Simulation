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
