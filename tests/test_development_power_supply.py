"""Owner 2026-10-27: ONE power supply block (2_1) - a battery or a DC power source - instead of a voltage per operating point,
and the phase current limits / phase count (2_6, 2_9) are no longer asked from the customer.  The schema change is additive
(`supply_type`, `dc_source_V`; schema_version unchanged); every earlier answer stays valid.  All values are made up."""
import pytest

from motor_ai_sim import report_customer
from motor_ai_sim.development import models
from motor_ai_sim.inverter import controller_catalog


def _field(fid):
    return models._field_map(2)[fid][1]


def _draft(fields):
    return {"schema_version": 2, "fields": fields, "mission_profile": [], "mission_profile_status": "missing",
            "production": {}, "provenance": {}}


def _supply(value, status="filled"):
    return _draft({"2_1": {"status": status, "value": value}})


def _complete(value):
    d = models.normalize_draft(_supply(value))
    return models._answer_complete(d["fields"]["2_1"], _field("2_1"))


def test_schema_is_additive_and_version_unchanged():
    assert models.schema(2)["schema_version"] == 2
    props = [p["id"] for p in _field("2_1")["properties"]]
    assert props[:8] == ["chemistry", "series_cells", "voltage_min_V", "voltage_nominal_V", "voltage_max_V",
                         "cell_voltage_min_V", "cell_voltage_nominal_V", "cell_voltage_max_V"]
    assert props[8:] == ["supply_type", "dc_source_V"]
    assert "supply_type" not in [p["id"] for p in models._field_map(1)["2_1"][1]["properties"]]


def test_the_supply_is_complete_with_a_battery_or_a_dc_source_voltage():
    assert _complete({"voltage_nominal_V": 48})                                               # an earlier answer
    assert _complete({"chemistry": "NMC", "series_cells": 192, "voltage_min_V": 633, "voltage_nominal_V": 710, "voltage_max_V": 806})
    assert not _complete({"chemistry": "NMC", "series_cells": 192})
    assert _complete({"supply_type": "dc_source", "dc_source_V": 48})
    assert not _complete({"supply_type": "dc_source"})
    assert not _complete({"supply_type": "dc_source", "voltage_nominal_V": 710})            # a leftover pack does not complete a DC source
    assert _complete({"supply_type": "dc_source", "voltage_nominal_V": 710, "dc_source_V": 48})
    assert _complete({"supply_type": "battery", "voltage_nominal_V": 710, "dc_source_V": 48})
    assert not _complete({"supply_type": "battery", "dc_source_V": 48})


def test_submit_errors_name_the_missing_voltage_of_the_chosen_supply():
    def errors(value):
        d = models.normalize_draft(_supply(value))
        return [e["field"] for e in models.validation_errors(d) if e["field"].startswith("2_1")]
    assert errors({"supply_type": "dc_source"}) == ["2_1.dc_source_V"]
    assert errors({"series_cells": 10}) == ["2_1.voltage_nominal_V"]
    assert errors({"supply_type": "dc_source", "dc_source_V": 48}) == []


def test_bad_supply_values_are_rejected_loudly():
    for bad in ({"supply_type": "mains"}, {"supply_type": 3}, {"dc_source_V": -1}, {"dc_source_V": 0}, {"dc_source_V": "high"}):
        with pytest.raises(ValueError):
            models.normalize_draft(_supply(bad))


def test_nothing_stored_for_the_other_choice_is_removed():
    stored = {"supply_type": "dc_source", "dc_source_V": 48, "chemistry": "NMC", "series_cells": 192, "voltage_nominal_V": 710}
    assert models.normalize_draft(_supply(stored))["fields"]["2_1"]["value"] == stored


def test_a_per_point_voltage_stays_in_the_data():
    point = {"name": "Takeoff", "torque_Nm": 10, "speed_rpm": 3000, "duration_s": "continuous", "voltage_V": 48}
    d = models.normalize_draft(_draft({"key_points": {"status": "filled", "value": [point]}}))
    assert d["fields"]["key_points"]["value"][0]["voltage_V"] == 48
    props = {p["id"]: p for p in _field("key_points")["items"]["properties"]}
    assert props["voltage_V"]["ui_hidden"] is True and props["voltage_V"]["required"] is False


def test_phase_limits_and_phase_count_are_never_required():
    for fid in ("2_6", "2_9"):
        field = _field(fid)
        assert not field["required"] and all(not p.get("required") for p in field["properties"])
    base = models.completeness(models.normalize_draft(_draft({})))
    pre = models.normalize_draft(_draft({"2_6": {"status": "filled", "value": {"continuous_Arms": 365}}}))
    assert models.completeness(pre)["total_required"] == base["total_required"]
    assert models._answer_complete(pre["fields"]["2_6"], _field("2_6"))                      # a catalogue pre-fill alone is complete
    assert pre["fields"]["2_6"]["value"] == {"continuous_Arms": 365}                         # stored answers are kept


def test_a_dc_source_voltage_is_the_supply_for_the_controller_check_and_the_report():
    draft = _supply({"supply_type": "dc_source", "dc_source_V": 48, "voltage_max_V": 806})
    assert controller_catalog._battery_max_V(draft) == 48
    assert controller_catalog._battery_max_V(_supply({"voltage_max_V": 806})) == 806
    req = report_customer.requirements_from_draft(draft, "project specification")
    assert req["battery"] == {"v_min": 48.0, "v_nom": 48.0, "v_max": 48.0, "cells": None, "chemistry": None}
    battery = report_customer.customer_battery({"v_nom": 700, "cells": 100}, req)
    assert (battery["v_min"], battery["v_nom"], battery["v_max"]) == (48.0, 48.0, 48.0) and battery["source"] == "customer"
