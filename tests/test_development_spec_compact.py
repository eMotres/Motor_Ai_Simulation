"""Compact specification (schema v2, owner 2026-10-24): the new "torque OR power" rules and the
data risks — drafts stored with the previous property sets must still normalize, keep every
value and never turn "invalid".  All values are made up."""
import copy

import pytest

from motor_ai_sim.development import models


def _old_shape_draft():
    """Every structured field with the property sets it had before the compact layout."""
    fields = {
        "1_1": {"status": "filled", "value": "Example multicopter, direct drive"},
        "1_2": {"status": "filled", "value": "Motor and regeneration; both directions"},
        "1_3": {"status": "filled", "value": "Takeoff 30 s, cruise 20 min, landing 60 s"},
        "1_4": {"status": "filled", "value": {"torque_Nm": 12.5, "speed_rpm": 3000, "shaft_power_W": 12.5 * 2 * 3.141592653589793 * 3000 / 60}},
        "1_5": {"status": "filled", "value": {"torque_Nm": {"minimum": 20, "maximum": 25}, "speed_rpm": 2800,
                                                "duration_s": {"minimum": 10, "maximum": 30}, "frequency": "every takeoff",
                                                "output_power_W": {"minimum": 5000, "maximum": 6000}}},
        "1_6": {"status": "filled", "value": {"maximum_rpm": {"minimum": 4000, "maximum": 4500}, "overspeed_rpm": 5200,
                                                "proof_or_runaway": "1.2 x maximum for 2 min"}},
        "1_7": {"status": "filled", "value": {"minimum": 90, "maximum": 94}},
        "1_8": {"status": "filled", "value": {"torque_ripple_pct_pkpk": {"minimum": 2, "maximum": 5}, "cogging_torque_Nm": {"minimum": 0.01, "maximum": 0.05}}},
        "2_1": {"status": "filled", "value": {"chemistry": "NMC, Example cell 21700", "series_cells": 96,
                                                "voltage_min_V": 317, "voltage_nominal_V": 356, "voltage_max_V": 403}},
        "2_2": {"status": "filled", "value": {"operating_point_voltages": [{"operating_point": "S1", "voltage_V": 700}]}},
        "2_3": {"status": "filled", "value": {"continuous_A": 80, "peak_A": 150}},
        "2_4": {"status": "filled", "value": {"manufacturer_model": "Example controller X1", "supplier": "customer"}},
        "2_5": {"status": "filled", "value": {"device_type": "SiC", "voltage_class_V": 1200}},
        "2_6": {"status": "filled", "value": {"continuous_Arms": 60, "peak_Arms": 120, "peak_duration_s": 30}},
        "2_7": {"status": "filled", "value": {"switching_frequency_Hz": 16000, "modulation": "SVPWM", "field_weakening": True}},
        "2_9": {"status": "filled", "value": {"phase_count": 6, "redundancy": "two independent 3-phase sets"}},
        "3_1": {"status": "filled", "value": {"cooling_type": "forced air from the propeller", "airflow": {"mode": "propeller", "speed_mps": 12},
                                                "inlet_temperature_C": 40, "flow_rate": 2}},
        "3_2": {"status": "filled", "value": {"temperature_min_C": 0, "temperature_max_C": 45, "altitude_m": 3000}},
        "3_3": {"status": "filled", "value": "open, IP54"},
        "4_1": {"status": "filled", "value": {"outer_diameter_mm": 120, "length_mm": 60}},
        "4_2": {"status": "filled", "value": {"active_mass_kg": 1.2, "complete_mass_kg": 1.6}},
        "4_4": {"status": "filled", "value": "propeller hub, 6 x M5"},
        "4_6": {"status": "filled", "value": {"axial_thrust_N": 300, "reverse_thrust_N": 50, "radial_load_N": 20}},
        "6_1": {"status": "filled", "value": {"prototypes": 3, "pre_series": {"minimum": 10, "maximum": 20}, "series_per_year": {"minimum": 500, "maximum": 1000}}},
        "6_2": {"status": "filled", "value": {"simulation_date": "2031-01-15", "prototype_date": "2031-06-01", "series_date": "2032-03-01"}},
        # explicitly unknown, with the value the user had typed before marking it
        "4_3": {"status": "not_known_yet", "value": "flange, 4 x M4 on a 70 mm circle"},
        "load_type": {"status": "not_known_yet", "value": "air_propeller"},
    }
    return {
        "schema_version": 2,
        "fields": fields,
        "mission_profile": [{"row_id": "row-a", "source": "manual", "phase": "cruise", "torque_Nm": 8, "speed_rpm": 2500,
                             "duration_s": 1200, "battery_voltage_V": 700}],
        "mission_profile_status": "filled",
        "production": {"quantities": {"status": "filled", "value": {"prototypes": 3, "series_per_year": {"minimum": 500, "maximum": 1000}}},
                       "timeline": {"status": "filled", "value": {"series_date": "2032-03-01"}}},
        "provenance": {"schema_migrations": [{"from": 1, "to": 2, "legacy_1_4": {"value": {"torque_Nm": {"minimum": 10, "maximum": 12.5}}}}]},
    }


def test_old_shape_draft_normalizes_keeps_every_value_and_is_complete():
    draft = _old_shape_draft()
    normalized = models.normalize_draft(copy.deepcopy(draft))
    for fid, answer in draft["fields"].items():
        assert normalized["fields"][fid]["status"] == answer["status"], fid
        if isinstance(answer["value"], dict):
            for key, item in answer["value"].items():
                if fid == "1_4" and key == "shaft_power_W":
                    assert normalized["fields"][fid]["value"][key] == pytest.approx(item)
                else:
                    assert normalized["fields"][fid]["value"][key] == item, f"{fid}.{key}"
        else:
            assert normalized["fields"][fid]["value"] == answer["value"], fid
    assert normalized["fields"]["1_2"]["value"] == "Motor and regeneration; both directions"
    assert normalized["mission_profile"][0]["row_id"] == "row-a" and normalized["mission_profile"][0]["source"] == "manual"
    assert normalized["provenance"] == draft["provenance"]
    stats = models.completeness(normalized)
    assert stats["invalid"] == 0 and stats["missing"] == 0 and stats["complete"] is True
    assert models.validation_errors(normalized) == []


def test_partial_and_unknown_old_drafts_never_become_invalid():
    minimal = {"schema_version": 2, "fields": {
        "1_4": {"status": "filled", "value": {"torque_Nm": 3, "speed_rpm": 6000}},
        "2_1": {"status": "filled", "value": {"chemistry": "LiPo", "series_cells": 6, "voltage_nominal_V": 22.2}},
        "4_1": {"status": "filled", "value": {"outer_diameter_mm": 40}},
        "6_1": {"status": "filled", "value": {"prototypes": 2}},
        "6_2": {"status": "not_known_yet", "value": {"prototype_date": "2031-01-01"}},
        "3_2": {"status": "filled", "value": {"temperature_max_C": 40}},
        "1_5": {"status": "missing", "value": {"frequency": "rarely"}},
    }, "mission_profile": [], "mission_profile_status": "not_known_yet", "production": {}}
    normalized = models.normalize_draft(minimal)
    stats = models.completeness(normalized)
    assert stats["invalid"] == 0
    for fid in ("1_4", "2_1", "4_1", "6_1", "3_2"):
        section = models._field_map(2)[fid][0]["id"]
        assert models._answer_complete(normalized["fields"][fid], models._field_map(2)[fid][1]), fid
        assert stats["sections"][section]["answered"] >= 1
    assert normalized["fields"]["6_2"] == {"status": "not_known_yet", "value": {"prototype_date": "2031-01-01"}}
    assert normalized["fields"]["1_5"]["value"]["frequency"] == "rarely"


def test_required_set_and_counts():
    required = sorted(f["id"] for s in models.schema(2)["sections"] for f in s["fields"] if f.get("required"))
    assert required == sorted(["1_1", "1_2", "1_4", "1_6", "2_1", "2_4", "3_1", "3_2", "3_3", "4_1", "4_2", "6_1", "6_2", "mission_profile"])
    empty = models.normalize_draft({"schema_version": 2})
    assert models.completeness(empty)["total_required"] == 14


def test_s1_torque_or_power():
    def check(point):
        draft = models.normalize_draft({"schema_version": 2, "fields": {"1_4": {"status": "filled", "value": point}}})
        field = models._field_map(2)["1_4"][1]
        errors = [e for e in models.validation_errors(draft) if e["field"].startswith("1_4")]
        return models._answer_complete(draft["fields"]["1_4"], field), errors, draft
    complete, errors, draft = check({"speed_rpm": 3000, "power_W": 2500, "power_basis": "electrical_input"})
    assert complete and errors == [] and draft["fields"]["1_4"]["value"]["power_W"] == 2500
    complete, errors, _ = check({"speed_rpm": 3000})
    assert not complete and [e["field"] for e in errors] == ["1_4.torque_Nm"]
    complete, errors, draft = check({"torque_Nm": 2, "speed_rpm": 3000, "power_W": 900})
    assert complete and draft["fields"]["1_4"]["value"]["power_W"] == 900  # the stated power is kept as entered
    with pytest.raises(ValueError, match="shaft_power_W"):
        check({"torque_Nm": 2, "speed_rpm": 3000, "shaft_power_W": 1})
    with pytest.raises(ValueError, match="unsupported choice"):
        check({"speed_rpm": 3000, "power_W": 1, "power_basis": "wheel"})


def test_peak_optional_and_torque_or_output_power():
    peak = models._field_map(2)["1_5"][1]
    assert not peak.get("required")
    assert models._answer_complete({"status": "filled", "value": {"output_power_W": 4000, "period_s": 600}}, peak)
    assert not models._answer_complete({"status": "filled", "value": {"duration_s": 20}}, peak)
    no_peak = models.normalize_draft({"schema_version": 2, "fields": {"1_5": {"status": "missing", "value": {"torque_Nm": "", "output_power_W": ""}}}})
    assert not any(e["field"].startswith("1_5") for e in models.validation_errors(no_peak))


def test_new_properties_and_choices_validate():
    value = {
        "1_6": {"status": "filled", "value": {"maximum_rpm": 9000, "runaway_rpm": {"minimum": 11000, "maximum": 12000}}},
        "2_1": {"status": "filled", "value": {"voltage_nominal_V": 400, "cell_voltage_min_V": 3.0, "cell_voltage_nominal_V": 3.7, "cell_voltage_max_V": 4.2}},
        "2_3": {"status": "filled", "value": {"charge_A": 40}},
        "2_4": {"status": "filled", "value": {"manufacturer_model": "own design", "supplier": "us", "count": 2}},
        "2_9": {"status": "filled", "value": {"phase_count": 6, "independent_sets": 2}},
        "3_2": {"status": "filled", "value": {"temperature_min_C": -40, "temperature_max_C": 50}},
        "additional_requirements": {"status": "filled", "value": "Cut-in speed 150 rpm\nController input 300-450 V"},
        "load_type": {"status": "filled", "value": "turbine"},
    }
    normalized = models.normalize_draft({"schema_version": 2, "fields": copy.deepcopy(value)})
    for fid, answer in value.items():
        assert normalized["fields"][fid]["value"] == answer["value"], fid
    assert models._answer_complete(normalized["fields"]["1_6"], models._field_map(2)["1_6"][1])
    for bad in ({"2_4": {"status": "filled", "value": {"count": 0}}},
                {"load_type": {"status": "filled", "value": "invented"}}):
        with pytest.raises(ValueError):
            models.normalize_draft({"schema_version": 2, "fields": bad})


def test_required_range_property_counts_as_complete():
    speed = models._field_map(2)["1_6"][1]
    assert models._answer_complete({"status": "filled", "value": {"maximum_rpm": {"minimum": 4000, "maximum": 4500}}}, speed)
    assert not models._answer_complete({"status": "filled", "value": {"maximum_rpm": {"minimum": 4000, "maximum": None}}}, speed)


def test_quantities_and_timeline_need_one_value():
    draft = models.normalize_draft({"schema_version": 2, "fields": {
        "6_1": {"status": "filled", "value": {"prototypes": None, "pre_series": {"minimum": None, "maximum": None}}},
        "6_2": {"status": "filled", "value": {"series_date": "2032-01-01"}}}})
    errors = {e["field"] for e in models.validation_errors(draft)}
    assert "6_1" in errors and "6_2" not in errors
