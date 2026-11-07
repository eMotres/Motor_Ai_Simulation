"""Tests section: the simulation curves a load record carries (validated, kept on edit, passed to the charts)."""
from __future__ import annotations

import pytest

from motor_ai_sim.motor_tests import comparison, models

ROWS = [{"rpm": 1000, "i_phase_a": 32, "torque_nm": 48, "v_dc_v": 540, "i_dc_a": 11},
        {"rpm": 1000, "i_phase_a": 59, "torque_nm": 81.7, "v_dc_v": 540, "i_dc_a": 18.4}]
CURVES = [{"rpm": 1000, "points": [{"current_a": 20, "torque_nm": 30, "loss_w": 700, "eff_system_pct": 85, "i_dc_a": 7},
                                   {"current_a": 60, "torque_nm": 83}]}]


def _load(**kw):
    return models.normalize({"kind": "load", "date": "2026-01-15", "rows": ROWS, **kw}, who="eng@example.test")


def _model():
    return {"available": True, "duties": [], "noload": [], "r_ref": None, "l_ref": None, "l_stator_only": None,
            "magnet": {"points": []}, "mass_kg": None, "mech": None, "connection": "star", "has_bearings": False,
            "end3d": {"k_flux": None, "k_torque": None}}


def test_a_record_without_curves_is_unchanged():
    rec = _load()
    assert "simulation_curves" not in rec and "simulation_note" not in rec


def test_curves_and_note_are_stored_with_any_point_field_missing():
    rec = _load(simulation_curves=CURVES, simulation_note="with free-run loss")
    assert rec["simulation_curves"] == [{"rpm": 1000.0, "points": [
        {"current_a": 20.0, "torque_nm": 30.0, "loss_w": 700.0, "eff_system_pct": 85.0, "i_dc_a": 7.0},
        {"current_a": 60.0, "torque_nm": 83.0}]}]
    assert rec["simulation_note"] == "with free-run loss"


@pytest.mark.parametrize("bad", [
    "x", [{"points": [{"torque_nm": 1}]}], [{"rpm": 1000}], [{"rpm": 1000, "points": []}],
    [{"rpm": 1000, "points": [{}]}], [{"rpm": 1000, "points": [{"torque_nm": float("nan")}]}],
    [{"rpm": 1000, "points": [{"eff_system_pct": 120}]}], [{"rpm": 1000, "points": [{"current_a": -1}]}],
    [{"rpm": 1000, "points": [{"torque_nm": 1}] * 201}], [{"rpm": r, "points": [{"torque_nm": 1}]} for r in range(1, 10)],
    [{"rpm": 1000, "points": [{"torque_nm": 1}]}, {"rpm": 1000.2, "points": [{"torque_nm": 2}]}],
])
def test_bad_curves_are_refused_loudly(bad):
    with pytest.raises(models.TestInputError):
        _load(simulation_curves=bad)


def test_only_a_load_test_carries_curves():
    with pytest.raises(models.TestInputError):
        models.normalize({"kind": "free_run", "date": "2026-01-15", "rows": [{"rpm": 1000}],
                          "simulation_curves": CURVES}, who="eng@example.test")


def test_an_edit_that_does_not_send_the_curves_keeps_them():
    old = _load(simulation_curves=CURVES, simulation_note="with free-run loss")
    new = models.normalize({"kind": "load", "date": "2026-01-16", "rows": ROWS}, who="eng@example.test", previous=old)
    assert new["simulation_curves"] == old["simulation_curves"] and new["simulation_note"] == "with free-run loss"
    gone = models.normalize({"kind": "load", "date": "2026-01-16", "rows": ROWS, "simulation_curves": []},
                            who="eng@example.test", previous=old)
    assert "simulation_curves" not in gone and "simulation_note" not in gone


def test_the_comparison_passes_curves_on_and_curves_alone_are_not_lost():
    rec = _load(simulation_curves=CURVES, simulation_note="with free-run loss")
    s = comparison.compare_record(rec, _model(), "D", "C")["series"]
    assert s["simulation_curves"] == rec["simulation_curves"] and s["simulation_note"] == "with free-run loss"
    # a record whose rows plot nothing (no torque, current, ...) but that has curves still gets a series
    bare = models.normalize({"kind": "load", "date": "2026-01-15", "rows": [{"rpm": 1000}],
                             "simulation_curves": CURVES}, who="eng@example.test")
    assert comparison.compare_record(bare, _model(), "D", "C")["series"]["simulation_curves"] == bare["simulation_curves"]
    plain = comparison.compare_record(_load(), _model(), "D", "C")["series"]
    assert "simulation_curves" not in plain
