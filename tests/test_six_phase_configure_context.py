import importlib.util
from pathlib import Path

import pytest

from motor_ai_sim.configure_limits import preset_of
from motor_ai_sim.routes.catalog import _passport_winding_context, _phase_voltage_limit_per_set


ROOT = Path.cwd()
spec = importlib.util.spec_from_file_location(
    "build_catalog_card_from_passport", ROOT / "scripts" / "build_catalog_card_from_passport.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_configure_context_propagates_six_phase_per_set_values():
    record = {"passport": {"passport": {
        "phase_count": 6, "phase_sets": 2,
        "current_basis": "equivalent_three_phase_total",
        "nP0": 2,
        "six_phase": {"current_per_inverter_A": 40.0, "voltage_per_set_V": 31.0},
    }}}
    ctx = _passport_winding_context(record, {"winding": {"phases": 6}})
    assert ctx["phase_count"] == 6
    assert ctx["phase_sets"] == 2
    assert ctx["current_per_inverter_A"] == 40.0
    assert ctx["six_phase"]["voltage_per_set_V"] == 31.0
    assert ctx["phase_model_supported"] is True


def test_phase_voltage_limit_per_set_comes_from_pack_and_modulation():
    ctx = {"phase_count": 6, "battery": {"v_max": 48.0}, "modulation": {"m": 0.9}}
    assert _phase_voltage_limit_per_set(ctx) == pytest.approx(24.942)


def test_configure_context_marks_legacy_six_phase_passport_stale():
    ctx = _passport_winding_context({"passport": {"passport": {"I0_A": 80.0}}},
                                    {"winding": {"phases": 6}})
    assert ctx["phase_count"] == 6
    assert ctx["phase_model_supported"] is False
    assert ctx["phase_model_status"] == "passport_requires_regeneration"
    assert ctx["current_per_inverter_A"] is None


def test_configure_preset_carries_its_own_phase_topology_and_current():
    pr = preset_of({"name": "synthetic-2x3ph", "die": "public synthetic", "winding": {
        "phases": 6, "connection": "4S-2P", "n_parallel": 2, "set_neutrals": "isolated"},
        "duties": [{"name": "rated", "current_arms": 80.0, "rpm": 3000.0}]})
    assert pr["phase_count"] == 6
    assert pr["phase_model_supported"] is True
    assert pr["phase_sets"] == 2
    assert pr["current_basis"] == "equivalent_three_phase_total"
    assert pr["current_per_inverter_A"] == 40.0
    assert pr["winding"]["connection"] == "4S-2P"
    assert pr["knobs"]["I_A"] == 80.0
    odd = preset_of({"name": "odd", "die": "synthetic", "winding": {
        "phases": 6, "n_parallel": 1}, "duties": []})
    assert odd["phase_model_supported"] is False
    assert odd["phase_model_status"] == "unsupported_parallel_path"


def test_card_builder_keeps_three_phase_metadata_identical_and_adds_six_phase_basis():
    three = {"phase_count": 3, "I0_A": 24.0}
    three_out = {"legacy": "unchanged"}
    builder._copy_phase_metadata(three_out, three, 24.0)
    assert three_out == {"legacy": "unchanged"}

    six_out = {}
    builder._copy_phase_metadata(six_out, {
        "phase_count": 6, "phase_sets": 2,
        "current_basis": "equivalent_three_phase_total",
        "six_phase": {"voltage_per_set_V": 48.0, "current_per_inverter_A": 39.0},
    }, 80.0)
    assert six_out == {"phase_count": 6, "phase_sets": 2,
                       "current_basis": "equivalent_three_phase_total",
                       "six_phase": {"voltage_per_set_V": 48.0, "current_per_inverter_A": 40.0}}
    with pytest.raises(ValueError, match="unsupported passport phase_count"):
        builder._copy_phase_metadata({}, {"phase_count": 9}, 80.0)


def test_actual_six_phase_card_build_uses_dynamic_grid_and_no_stage_a():
    sp = {"phase_count": 6, "phase_sets": 2,
          "current_basis": "equivalent_three_phase_total",
          "six_phase": {"voltage_per_set_V": 48.0},
          "N0": 8, "L0_mm": 20, "wireH0_mm": 0.5, "I0_A": 80.0,
          "rpm0": 3000.0, "nP0": 2, "ldq0": {"connection": "4S-2P"}}
    static = {"poles": 14, "passport": {"passport": sp}}
    groups = {"P_cu_dc_W": 30, "P_cu_ac_W": 2, "P_fe_stator_W": 3,
              "P_fe_rotor_W": 1, "P_mag_W": 0.5, "P_shaft_W": 0.2}
    full = {"phase_count": 6, "machine": {"tag": "six"},
            "card": {"constants_cold": {
                "psi_PM_Wb": {"value": 0.02}, "R_phase_ohm": {"value": 0.01},
                "Ld_bench_mH": {"value": 0.1}, "Lq_bench_mH": {"value": 0.2}}},
            "hot_map": {"mtpa": [{"fI": 1.0, "I": 80.0, "conf_id": "p0",
                                     "T_fem_vertex": 10.0}],
                        "points": [{"id": "p0", "i_d": 0.0, "i_q": 80.0,
                                    "psi_d": 0.02, "psi_q": 0.0}]},
            "cold": {"k_levels": [{"fI": 1.0, "T_cold": 10.0, "T_hot": 9.0}]},
            "demag": {"mtpa_retention": [[80.0, 100.0]]},
            "loss_grid": {"plan": {"R_hot_ohm": 0.015},
                          "points": [{"id": "l0", "rpm": 3000.0, "I_rms": 80.0,
                                      "groups_W": groups, "T_ripple_pct": 4.0}]},
            "stage2_3d": {"status": "unavailable", "k_psi": {"value": None},
                          "k_T": {"value": None}},
            "inputs": {"temperatures": {"hot_magnet_c": 100, "hot_coil_c": 120}}}
    out = builder.build_block(full, static)
    assert out["phase_count"] == 6 and out["six_phase"]["current_per_inverter_A"] == 40
    assert out["loss_grid"]["rpm"] == [3000.0]
    assert out["loss_grid"]["I_A"] == [80.0]
    assert out["end3d"] is None
    assert out["six_phase"]["voltage_per_set_V"] == out["Vload0_fund_V"]
    assert out["six_phase"]["resistance_per_set_ohm"] == 2 * out["R0_ohm"]
    assert out["six_phase"]["copper_loss_per_set_W"] == 16.0
    with pytest.raises(ValueError, match="source topology mismatch"):
        builder.build_block({**full, "phase_count": 3}, static)
    with pytest.raises(ValueError, match="six_phase.n_parallel"):
        builder.build_block({**full, "six_phase": {"n_parallel": 4}},
                            {**static, "passport": {"passport": {
                                **sp, "six_phase": {**sp["six_phase"], "n_parallel": 2}}}})
