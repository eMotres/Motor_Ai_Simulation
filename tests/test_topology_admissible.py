"""The slot/pole topology has to be one the product family stamps (2026-09-20).

Owner: *"Poles per Segment у нас 5 или 7, других комбинаций пока не бывает"* —
after a stray 7 → 8 (12s16p) reached the live machine and released the die
context.  ONE table (``geometry_validation.ADMISSIBLE_POLES_PER_SEGMENT``):

* the value validator emits a DERIVED record naming the admissible values, so
  ``PUT /api/geometry`` answers 422 for an inadmissible pair however it was
  reached (poles typed, or slots typed under a now-wrong pole count);
* ``GET /api/geometry/schema`` serves the table on ``num_poles_per_segment``
  as ``allowed_by`` (the Geometry table renders a select from it);
* every combination the catalog holds (6/7 and 6/5) stays silent, and a
  slots-per-segment count the table does not know carries no rule.
"""
from __future__ import annotations

import pytest

from motor_ai_sim.geometry_validation import (
    ADMISSIBLE_POLES_PER_SEGMENT, admissible_poles_per_segment, topology_error,
    validate_parameter_values,
)

BASE = {"stator_diameter": 50.0, "num_seg": 2, "num_slots_per_segment": 6,
        "num_poles_per_segment": 7, "motor_length": 15.0, "tooth_width": 4.2,
        "magnet_height": 7.0, "slot_height": 7.5, "core_thickness": 2.4,
        "air_gap": 0.25, "wire_width": 3.0, "wire_height": 0.5,
        "num_wires_per_slot": 9, "wire_parallel": 1, "wire_split": 1}


def test_the_table_is_the_two_stamped_families():
    assert ADMISSIBLE_POLES_PER_SEGMENT == {6: (5, 7)}
    assert admissible_poles_per_segment(6) == (5, 7)
    assert admissible_poles_per_segment(6.0) == (5, 7)
    assert admissible_poles_per_segment(12) is None
    assert admissible_poles_per_segment(None) is None
    assert admissible_poles_per_segment("x") is None


@pytest.mark.parametrize("poles", [5, 7, 5.0, 7.0])
def test_the_stamped_pairs_are_silent(poles):
    assert topology_error({**BASE, "num_poles_per_segment": poles}) is None
    assert validate_parameter_values({**BASE, "num_poles_per_segment": poles}) == []


@pytest.mark.parametrize("poles", [8, 6, 4, 9, 1])
def test_an_inadmissible_pole_count_is_named_with_the_allowed_values(poles):
    rec = topology_error({**BASE, "num_poles_per_segment": poles})
    assert rec is not None
    assert rec["field"] == "num_poles_per_segment" and rec["kind"] == "derived"
    assert rec["allowed"] == [5, 7] and rec["value"] == poles
    assert "5 or 7" in rec["message"] and "6 slots per segment" in rec["message"]
    bad = validate_parameter_values({**BASE, "num_poles_per_segment": poles})
    assert [b["field"] for b in bad] == ["num_poles_per_segment"]
    assert bad[0]["allowed"] == [5, 7]


def test_a_slot_count_the_table_does_not_know_carries_no_rule():
    assert topology_error({**BASE, "num_slots_per_segment": 12, "num_poles_per_segment": 8}) is None
    assert topology_error({"num_poles_per_segment": 8}) is None      # no slots given
    assert topology_error({"num_slots_per_segment": 6}) is None      # no poles given


def test_the_geometry_put_refuses_an_inadmissible_topology_with_422():
    """Through the route, on the suite's sandbox config: nothing is written."""
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    from motor_ai_sim.services.geometry_service import get_current_geometry

    client = TestClient(app)
    before = dict(get_current_geometry(reload=True).to_dict())
    r = client.put("/api/geometry", json={"num_slots_per_segment": 6,
                                          "num_poles_per_segment": 8})
    assert r.status_code == 422, r.text
    body = r.json()["detail"]
    assert body["error"] == "invalid geometry parameter value"
    (rec,) = [x for x in body["invalid_parameters"] if x["field"] == "num_poles_per_segment"]
    assert rec["allowed"] == [5, 7] and "5 or 7" in rec["message"]
    after = dict(get_current_geometry(reload=True).to_dict())
    assert after.get("num_poles_per_segment") == before.get("num_poles_per_segment")
    assert after.get("num_slots_per_segment") == before.get("num_slots_per_segment")


def test_the_schema_serves_the_table_on_poles_per_segment():
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app

    r = TestClient(app).get("/api/geometry/schema")
    assert r.status_code == 200
    by = {p["name"]: p for p in r.json()["parameters"]}
    assert by["num_poles_per_segment"]["allowed_by"] == {"num_slots_per_segment": {"6": [5, 7]}}
    assert "allowed_by" not in by["num_slots_per_segment"]
    assert "allowed_by" not in by["tooth_width"]
