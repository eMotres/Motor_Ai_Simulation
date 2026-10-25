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
    # 4_6 joined the set on 2026-10-25: the axial load must be stated (owner)
    assert required == sorted(["1_1", "1_2", "1_6", "key_points", "2_1", "2_4", "3_1", "3_2", "3_3", "4_1", "4_2", "4_6", "6_1", "6_2", "mission_profile"])
    empty = models.normalize_draft({"schema_version": 2})
    assert models.completeness(empty)["total_required"] == 15


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


# ---- one-value spec, cooling switch, shaft load, menus (owner 2026-10-25) ------------------------------------

def _legacy_draft():
    """A draft stored before the switches: free-text cooling, ranges, no cooling_medium and no 4_6."""
    return {"schema_version": 2, "fields": {
        "1_1": {"status": "filled", "value": "Example survey drone"},
        "1_2": {"status": "filled", "value": "Motor, one direction"},
        "1_4": {"status": "filled", "value": {"torque_Nm": 3, "speed_rpm": 6000}},
        "1_5": {"status": "filled", "value": {"torque_Nm": {"minimum": 5, "maximum": 6}, "duration_s": {"minimum": 10, "maximum": 30}}},
        "1_6": {"status": "filled", "value": {"maximum_rpm": {"minimum": 7000, "maximum": 8000}}},
        "1_8": {"status": "filled", "value": {"torque_ripple_pct_pkpk": {"minimum": 2, "maximum": 5}}},
        "2_1": {"status": "filled", "value": {"voltage_nominal_V": 22.2}},
        "3_1": {"status": "filled", "value": {"cooling_type": "propeller wash", "airflow": {"speed_mps": 8}}},
        "3_2": {"status": "filled", "value": {"temperature_max_C": 40}},
        "3_3": {"status": "filled", "value": "open"},
        "4_1": {"status": "filled", "value": {"outer_diameter_mm": 40}},
        "load_type": {"status": "filled", "value": "air_propeller"},
    }, "mission_profile": [], "mission_profile_status": "not_known_yet", "production": {}}


def _schema_without_required_loads(current):
    previous = copy.deepcopy(current)
    for section in previous["sections"]:
        for field in section["fields"]:
            if field["id"] == "4_6":
                field["required"] = False
    return previous


def test_schema_loads_with_ui_only_suggestions():
    fields = models._field_map(2)
    assert fields["3_1"][1]["required_any"] == [["cooling_medium", "cooling_type"]]
    with_menus = [fid for fid, (_, f) in fields.items() if f.get("suggestions") or any(p.get("suggestions") for p in f.get("properties", []))]
    assert {"1_1", "1_2", "2_8", "3_1", "3_3", "3_4", "4_3", "4_4", "4_5", "2_4", "2_5", "2_7", "2_9"} <= set(with_menus)


def test_old_drafts_normalize_and_count_as_before_except_the_axial_load(monkeypatch):
    draft = models.normalize_draft(_legacy_draft())
    assert draft["fields"]["3_1"]["value"]["cooling_type"] == "propeller wash"
    assert draft["fields"]["1_8"]["value"]["torque_ripple_pct_pkpk"] == {"minimum": 2, "maximum": 5}
    assert "cooling_medium" not in draft["fields"]["3_1"]["value"] and "4_6" not in draft["fields"]
    now = models.completeness(draft)
    assert models._answer_complete(draft["fields"]["3_1"], models._field_map(2)["3_1"][1])  # the old text still answers 3_1
    previous = _schema_without_required_loads(models.schema(2))
    current_schema = models.schema
    monkeypatch.setattr(models, "schema", lambda version=None: copy.deepcopy(previous) if version != 1 else current_schema(1))
    before = models.completeness(draft)
    monkeypatch.undo()
    assert now["total_required"] == before["total_required"] + 1
    assert now["answered"] == before["answered"] and now["unknown"] == before["unknown"] and now["invalid"] == before["invalid"] == 0
    assert now["missing"] == before["missing"] + 1
    assert "4_6" in {e["field"].split(".")[0] for e in models.validation_errors(draft)}
    # stating the axial load answers it; another load alone does not
    draft["fields"]["4_6"] = {"status": "filled", "value": {"axial_thrust_N": 25}}
    assert models.completeness(models.normalize_draft(draft))["missing"] == before["missing"]
    draft["fields"]["4_6"] = {"status": "filled", "value": {"radial_load_N": 10}}
    assert not models._answer_complete(models.normalize_draft(draft)["fields"]["4_6"], models._field_map(2)["4_6"][1])
    draft["fields"]["4_6"] = {"status": "not_known_yet", "value": None}
    assert models.completeness(models.normalize_draft(draft))["unknown"] == before["unknown"] + 1


def test_cooling_medium_shaft_and_free_text_beside_the_menus_validate():
    value = {
        "3_1": {"status": "filled", "value": {"cooling_medium": "submerged", "water_temperature_C": 28, "water_speed_mps": 1.5, "depth_m": 50,
                                                "airflow": {"mode": "Fan", "speed_mps": 3}, "coolant": "Our own blend"}},
        "load_type": {"status": "filled", "value": "shaft"},
        "2_8": {"status": "filled", "value": "Custom optical encoder"},
        "2_5": {"status": "filled", "value": {"device_type": "Si IGBT", "voltage_class_V": 750}},
        "4_6": {"status": "filled", "value": {"axial_thrust_N": 120, "bending_moment_Nm": 4, "tilt_rate": 1.2, "acceleration": 6}},
    }
    normalized = models.normalize_draft({"schema_version": 2, "fields": copy.deepcopy(value)})
    for fid, answer in value.items():
        assert normalized["fields"][fid]["value"] == answer["value"], fid
    fields = models._field_map(2)
    assert models._answer_complete(normalized["fields"]["3_1"], fields["3_1"][1])
    assert models._answer_complete(normalized["fields"]["4_6"], fields["4_6"][1])
    assert models._answer_complete({"status": "filled", "value": {"cooling_medium": "air"}}, fields["3_1"][1])
    assert not models._answer_complete({"status": "filled", "value": {"airflow": {"speed_mps": 4}}}, fields["3_1"][1])
    for bad in ({"3_1": {"status": "filled", "value": {"cooling_medium": "oil bath"}}},
                {"3_1": {"status": "filled", "value": {"depth_m": -1}}}):
        with pytest.raises(ValueError):
            models.normalize_draft({"schema_version": 2, "fields": bad})


def _load_cooling_draft(load, cooling):
    return {"schema_version": 2, "fields": {"load_type": {"status": "filled", "value": load},
                                            "3_1": {"status": "filled", "value": cooling}}}


def test_load_cooling_refuses_what_cannot_occur():
    # owner 2026-10-25: an air propeller never runs under water; propeller wash needs an air propeller
    errs = models.cooling_load_errors(_load_cooling_draft("air_propeller", {"cooling_medium": "submerged"}))
    assert [e["field"] for e in errs] == ["3_1.cooling_medium"]
    errs = models.cooling_load_errors(_load_cooling_draft("wheel", {"cooling_medium": "air", "airflow": {"mode": "Propeller wash"}}))
    assert [e["field"] for e in errs] == ["3_1.airflow.mode"]
    # what does occur passes: submerged wheel, liquid behind an air propeller with its wash, an engine generator on a jacket
    for load, cooling in (("wheel", {"cooling_medium": "submerged"}),
                          ("air_propeller", {"cooling_medium": "liquid", "airflow": {"mode": "Propeller wash"}}),
                          ("engine_driven_generator", {"cooling_medium": "liquid", "coolant": "Glycol"}),
                          ("water_jet", {"cooling_medium": "submerged"}),
                          ("shaft", {"cooling_medium": "air", "airflow": {"mode": "duct from the radiator fan"}})):
        assert models.cooling_load_errors(_load_cooling_draft(load, cooling)) == [], (load, cooling)
    # nothing is judged before a load is chosen
    assert models.cooling_load_errors(_load_cooling_draft(None, {"cooling_medium": "submerged"})) == []


def test_load_cooling_misfit_blocks_submit_and_completeness():
    draft = _load_cooling_draft("air_propeller", {"cooling_medium": "submerged"})
    assert any(e["field"] == "3_1.cooling_medium" for e in models.validation_errors(draft))
    ok = _load_cooling_draft("air_propeller", {"cooling_medium": "air", "airflow": {"mode": "Propeller wash"}})
    assert not any(e["field"].startswith("3_1") for e in models.validation_errors(ok))


def test_load_cooling_judges_only_active_answers():
    # Astra re-review 2026-10-25: a retained (not_known_yet) cooling or load is not judged
    draft = _load_cooling_draft("air_propeller", {"cooling_medium": "submerged"})
    draft["fields"]["3_1"]["status"] = "not_known_yet"
    assert models.cooling_load_errors(draft) == []
    draft = _load_cooling_draft("air_propeller", {"cooling_medium": "submerged"})
    draft["fields"]["load_type"]["status"] = "not_known_yet"
    assert models.cooling_load_errors(draft) == []
    # a hidden airflow from the air-propeller days does not block a water propeller with a liquid jacket
    draft = _load_cooling_draft("marine_propeller", {"cooling_medium": "liquid", "airflow": {"mode": "Propeller wash"}})
    assert models.cooling_load_errors(draft) == []
    # nor under water
    draft = _load_cooling_draft("wheel", {"cooling_medium": "submerged", "airflow": {"mode": "Propeller wash"}})
    assert models.cooling_load_errors(draft) == []
