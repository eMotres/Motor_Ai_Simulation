"""The results panel block of a stored point (owner 2026-11-01; synthetic data only).

``motor_view.build_panel`` hands every display of a motor ONE shape: the electromagnetic summary of the point with a
``coupling`` block, taken from the point's FINAL stored result - the coupled loop's when the point has one (EM numbers,
temperatures, controller), else the saved EM run's, with a steady temperature map standing in for the temperatures only.
"""
from __future__ import annotations

from motor_ai_sim.development import motor_view

SUMMARY = {
    "T_em_avg_Nm": 100.0, "P_mech_W": 10000.0, "P_loss_total_W": 1000.0, "P_loss_total_incl_mech_W": 1100.0,
    "P_loss_stator_W": 800.0, "P_loss_rotor_W": 200.0, "P_loss_split_measured": True,
    "V_line_peak_V": 400.0, "V_phase_peak_V": 231.0, "torque_per_mass_Nm_kg": 5.0, "loss_density_W_kg": 50.0,
    "KV_noload_rpm_per_V_line": 12.0, "Kt_Nm_per_Arms": 0.9, "Ld_mH": 0.2, "Lq_mH": 0.3,
    "coil_temp_C": 150.0, "end3d": {"k_flux": 0.9, "T_corrected_Nm": 90.0},
    "coupling": {"coil_temp_c": 999.0},            # a stale block saved beside the run: never shown
    "runs": [{"x": 1}], "history": [1, 2, 3],
}


def _coupled(**extra):
    row = {"computed_at": "2026-10-30T10:00:00Z", "coil_temp_c": 118.0, "magnet_temp_c": 96.0, "magnet_temp_max_c": 101.0,
           "drive": "sine", "converged": True,
           "em": {"T_em_avg_Nm": 90.0, "P_loss_total_W": 1200.0, "P_shaft_W": 8000.0, "V_line_peak_V": 360.0},
           "P_bearings_W": 20.0, "P_windage_W": 5.0, "P_mech_extra_W": 25.0, "P_loss_total_incl_mech_W": 1225.0,
           "efficiency_shaft": 0.86}
    row.update(extra)
    return row


def test_without_any_record_the_panel_is_empty():
    assert motor_view.build_panel(None, {}, None, None) is None


def test_an_em_run_alone_shows_its_numbers_and_never_the_stale_coupling_block():
    p = motor_view.build_panel(SUMMARY, {}, None, None)
    s = p["summary"]
    assert s["Kt_Nm_per_Arms"] == 0.9 and s["Ld_mH"] == 0.2
    assert "coupling" not in s and "runs" not in s and "history" not in s
    assert p["source"] == {"em": "em_run", "thermal": None, "drive": "sine", "em_winding_c": 150.0}


def test_a_steady_map_supplies_the_temperatures_only():
    thermal = {"duty_temps": {"kind": "thermal", "winding_c": 111.0, "magnet_c": 90.0, "housing_c": 60.0}, "cooling_air_speed_mps": 12.0}
    p = motor_view.build_panel(SUMMARY, {}, thermal, None)
    assert p["summary"]["coupling"] == {"coil_temp_c": 111.0, "magnet_temp_c": 90.0}
    assert p["summary"]["T_em_avg_Nm"] == 100.0                      # the EM numbers stay the run's
    assert p["cooling"] == {"housing_c": 60.0, "air_speed_mps": 12.0}
    assert p["source"]["thermal"] == "thermal" and p["source"]["em"] == "em_run"


def test_the_coupled_loop_is_the_final_result_and_its_numbers_win():
    p = motor_view.build_panel(SUMMARY, {"coupled": _coupled()}, None, None)
    s = p["summary"]
    assert s["T_em_avg_Nm"] == 90.0 and s["P_loss_total_W"] == 1200.0
    assert s["coupling"]["coil_temp_c"] == 118.0 and s["coupling"]["magnet_temp_c"] == 96.0
    assert s["P_bearings_W"] == 20.0 and s["efficiency_shaft"] == 0.86
    # derived numbers follow the loop's torque / losses by proportion, the voltages the line peak
    assert abs(s["P_mech_W"] - 9000.0) < 1e-6
    assert abs(s["torque_per_mass_Nm_kg"] - 4.5) < 1e-9
    assert abs(s["end3d"]["T_corrected_Nm"] - 81.0) < 1e-9
    assert abs(s["V_phase_peak_V"] - 231.0 * 0.9) < 1e-9
    assert abs(s["loss_density_W_kg"] - 50.0 * 1225.0 / 1100.0) < 1e-9
    # the heat split is scaled and flagged as an estimate
    assert abs(s["P_loss_stator_W"] - 960.0) < 1e-9 and s["P_loss_split_measured"] is False
    # constants of the EM run stay
    assert s["Kt_Nm_per_Arms"] == 0.9
    assert p["source"] == {"em": "coupled", "thermal": "coupled", "drive": "sine"}


def test_a_pwm_record_carries_its_controller_carrier_and_motor_pwm_loss():
    cp = _coupled(drive="inverter", inverter={"f_carrier_hz": 10000.0},
                  controller={"losses": {"total_W": 300.0}, "efficiency": {"shaft": 0.95, "wall_to_shaft": 0.93}, "limits_verdict": "pass",
                              "thermal": {"t_j_c": 88.0, "t_j_max_c": 175.0}, "schematic_svg": "<svg/>"},
                  sine_comparison={"rows": [{"key": "P_loss_total_W", "sine": 1100.0, "inverter": 1200.0}]})
    p = motor_view.build_panel(SUMMARY, {"coupled": cp}, None, None)
    c = p["summary"]["coupling"]
    assert c["controller"]["t_j_c"] == 88.0 and c["controller"]["losses"]["total_W"] == 300.0
    assert "schematic_svg" not in c["controller"]
    assert c["motor_pwm_loss_W"] == 100.0
    assert p["source"]["drive"] == "inverter" and p["source"]["f_carrier_hz"] == 10000.0


def test_a_limit_search_is_never_the_point_result_but_a_within_limits_run_is():
    search = _coupled(solve_to="continuous", mode="limited", time_to_limit={"within_limits": False})
    p = motor_view.build_panel(SUMMARY, {"coupled_limit": search}, None, None)
    assert "coupling" not in p["summary"] and p["summary"]["T_em_avg_Nm"] == 100.0
    within = _coupled(solve_to="limits", time_to_limit={"within_limits": True, "reported_state": "steady"})
    p = motor_view.build_panel(SUMMARY, {"coupled_limit": within}, None, None)
    assert p["summary"]["coupling"]["coil_temp_c"] == 118.0


def test_with_no_saved_summary_the_kernel_runs_numbers_stand_in():
    p = motor_view.build_panel(None, {}, None, {"numbers": {"T_em_avg_Nm": 80.0, "Km_Nm_sqrtW": 1.1}})
    assert p["summary"] == {"T_em_avg_Nm": 80.0, "Km_Nm_sqrtW": 1.1}
    assert p["source"]["em"] == "em_run"
