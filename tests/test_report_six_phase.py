"""The report for a six-phase machine, the temperature basis, the rotor-SF
target, the cooling basis and the first-page key points (owner 2026-10-25).

Pure-function tests on synthetic summaries: no solver, no server.  A 3-phase
machine must read exactly as before, which is pinned here row by row.
"""
from __future__ import annotations

import copy

from motor_ai_sim import report as R

SIX = {"phases": 6, "n_parallel": 4, "neutrals": "isolated",
       "set1_paths": [1, 2], "set2_paths": [3, 4], "set_connection": "star",
       "sets": [{"set": 1, "I_phase_rms_A": 300.0, "V1_phase_peak_V": 290.0,
                 "V_phase_rms_V": 205.0},
                {"set": 2, "I_phase_rms_A": 300.0, "V1_phase_peak_V": 290.0,
                 "V_phase_rms_V": 205.0}],
       "inductances": {"Ld_set_mH": 0.31, "Lq_set_mH": 0.52, "Lxy_mH": 0.04,
                       "Lxy_pct_of_Ld": 12.9}}

EM = {"rpm": 4000.0, "T_em_avg_Nm": 80.0, "P_mech_W": 33510.0,
      "P_loss_total_W": 1800.0, "P_stranded_W": 900.0, "P_core_W": 700.0,
      "coil_temp_C": 120.0, "magnet_temp_C": 110.0, "I_phase_rms_A": 600.0,
      "V_line_peak_V": 500.0, "V1_LL_V": 480.0, "V_phase_peak_V": 290.0,
      "V_phase_rms_V": 205.0, "J_coil_A_per_mm2": 9.8,
      "KV_noload_rpm_per_V_line": 40.0, "Kt_Nm_per_Arms": 0.133,
      "Kt_Nm_per_A_line": 0.133, "star_delta": "star", "efficiency": 0.96,
      "mass_total_kg": 9.5, "gamma_deg": -10.0, "n_steps_per_period": 120,
      "connection": "4S-2P", "R_phase_ohm": 0.004}

PROP = {"propeller_id": "G40x13", "rpm": 4000.0, "air_speed_mps": 14.2}
THERMAL = {
    "T_max": 120.0, "T_min": 45.0,
    "components": {"winding": {"max": 120.0, "avg": 110.0}},
    "point": {"ambient_temp": 45.0, "cooling_mode": "air",
              "air_speed_source": "propeller"},
    "cooling_air_source": PROP,
    "cooling": {"outer": {"mode": "air", "air_speed_mps": 14.2,
                          "t_sink_c": 45.0, "heat_removed_W": 1700.0},
                "inner": {"mode": "none"}, "heat_budget": {"losses_W": 1800.0}}}
MECH = {"rpm": 4000.0, "case": "hot", "overspeed_factor": 1.2, "sf_min": 2.4,
        "sf_min_part": "rotor", "point": {"torque_nm": 120.0},
        "parts": {"rotor": {"governing_stress_mpa": 160.0, "strength_mpa": 384.0,
                            "strength_kind": "yield", "safety_factor": 2.4}}}


def _col(six=True, name="rated"):
    em = copy.deepcopy(EM)
    if six:
        em["six_phase"] = copy.deepcopy(SIX)
    return {"duty": name, "em": em, "result": {}, "brg": None, "fp": None,
            "d": {"rpm": 4000.0, "mode": "motor", "gamma_deg": -10.0,
                  "mesh": {"mesh.meshSize": 2.07, "mesh.nSectors": 8,
                           "mesh.gapLayers": 1}},
            "res": {"thermal": copy.deepcopy(THERMAL),
                    "rotor_stress": copy.deepcopy(MECH)}}


# -- 1 . six phases ----------------------------------------------------------
def test_the_winding_is_named_in_the_owners_words():
    sp = R.six_phase_block(_col()["em"])
    assert R.six_phase_words(sp) == (
        "2 × 3-phase (two independent windings), 4S-2P, isolated neutrals, "
        "2 inverters")
    assert sp["i_total_a"] == 600.0 and sp["i_set_a"] == 300.0


def test_a_three_phase_machine_has_no_six_phase_block():
    assert R.six_phase_block(_col(six=False)["em"]) is None
    assert R.six_phase_block({}, {"phases": 3}) is None
    # a run saved before the block existed: the configuration's winding decides
    sp = R.six_phase_block({"I_phase_rms_A": 100.0, "connection": "2S-2P"},
                           {"phases": 6})
    assert sp and sp["i_set_a"] == 50.0 and sp["neutrals"] == "isolated"


def test_current_and_voltage_are_labelled_total_and_per_set():
    em = _col()["em"]
    tp = {r[0]: r[1:] for r in R.em_torque_rows(em, {"v_min": 549.6})}
    assert tp["Phase current, total of both sets [A rms]"][0] == "600"
    per = [k for k in tp if k.startswith("Phase current per set")]
    assert per and tp[per[0]][0] == "300"
    kt_set = [v for k, v in tp.items()
              if k.startswith("Torque constant Kt per set")]
    # torque per ampere of ONE set's current: twice the machine's per ampere
    # of the total
    assert kt_set and float(kt_set[0][0]) == 0.266
    op = {r[0]: r for r in R.em_operating_rows(em, {}, {}, None, {})}
    assert op["Phase current, total of both sets"][1].startswith("600 A rms")
    assert op["Phase current per set (one inverter)"][1].startswith("300 A rms")
    assert op["Phase current per set (one inverter)"][3] == "290 V"


def test_the_three_phase_rows_are_untouched():
    em = _col(six=False)["em"]
    tp = [r[0] for r in R.em_torque_rows(em, {"v_min": 549.6})]
    assert "Phase current [A rms]" in tp
    assert not any("per set" in x or "total of both" in x for x in tp)
    op = [r[0] for r in R.em_operating_rows(em, {}, {}, None, {})]
    assert op[0] == "Phase current" and not any("per set" in x for x in op)
    consts = [r[0] for r in R.em_constant_rows(em)]
    assert not any("per set" in x for x in consts)
    assert not any("per set" in str(r[0]) for r in
                   R.em_compare_rows([_col(six=False)], {})[1])


def test_the_comparison_and_the_constants_carry_the_per_set_values():
    rows = {r[0]: r[1:] for r in R.em_compare_rows(
        [_col(), _col(False, "peak")], {"v_min": 549.6})[1]}
    assert rows["Phase current, total of both sets [A rms]"] == ["600", "600"]
    assert rows["Current per set (one inverter) [A rms]"] == ["300", "—"]
    assert rows["Winding"][1] == "3-phase"
    assert rows["Current density [A/mm²] (same in each set)"][0] == "9.8"
    assert rows["Phase voltage, waveform peak [V] (each set the same)"] == \
        ["290"] * 2
    consts = {r[0]: r[1:] for r in R.em_constant_rows(_col()["em"])}
    assert consts["Phase resistance per set (one inverter)"][0] == "8 mOhm"
    assert "Ld per set (one inverter)" in consts
    assert "L_xy (the sets driven against each other)" in consts


def test_a_loss_not_stored_per_set_is_a_dash_never_half_the_total():
    rows = {r[0]: r[1] for r in R.em_loss_rows(_col()["em"], None)}
    assert rows["Copper, per set (one inverter)"] == "—"


def test_the_controller_names_two_inverters_and_their_current():
    rec = {"topology": {"preset": "two_3ph_sets", "n_bridges": 2},
           "point": {"i_phase_rms_A": 600.0, "i_leg_rms_3ph_A": 600.0,
                     "star_delta": "star", "modulation_index": 0.8,
                     "power_factor": 0.95, "f_carrier_hz": 24000.0,
                     "v_dc_V": 750.0},
           "bridges": [{"id": "B1", "legs": [{"leg": "U", "i_leg_rms_A": 300.0}]},
                       {"id": "B2", "legs": [{"leg": "U", "i_leg_rms_A": 300.0}]}]}
    rows = {r[0]: r for r in R.controller_rows(rec)}
    assert rows["Inverters"][1] == "2 × 3-phase"
    assert "300 A per inverter" in rows["Operating point"][1]
    assert "600 A phase, machine total" in rows["Operating point"][1]
    # a pure motor needs no 'Power direction' row
    assert "Power direction" not in rows
    gen = dict(rec, power_direction="generator")
    assert "Power direction" in {r[0] for r in R.controller_rows(gen)}


def test_the_overview_and_the_key_points_show_total_and_per_set():
    what, _ = R.duty_overview_rows([_col()])
    i = R.DUTY_OVERVIEW_HEAD.index("Current")
    assert what[0][i] == "600 A (2 × 300 A)"
    assert what[0][R.DUTY_OVERVIEW_HEAD.index("Phase V peak")] == "290 V"
    head, kp = R.key_point_rows(
        "motor", [_col(), _col(False, "peak")],
        {"cells": 216, "v_nom": 756.0, "v_min": 600.0, "v_max": 900.0})
    rows = {r[0]: r[1:] for r in kp}
    assert head == ["Key points", "rated", "peak"]
    assert rows["Winding"][1] == "3-phase" and "2 × 3-phase" in rows["Winding"][0]
    cur = [k for k in rows if k.startswith("Phase current, rms: total")][0]
    assert rows[cur] == ["600 A (2 × 300 A)", "600 A"]
    assert rows["Phase voltage, peak"] == ["290 V", "290 V"]
    assert rows["Battery"][0] == "216 cells · 756 V nominal (600 – 900 V)"
    assert rows["Maximum ambient"] == ["45 °C", "45 °C"]
    for k in ("Speed", "Torque at the rotor", "Shaft power", "Cooling", "Mass"):
        assert k in rows
    assert any(k.startswith("Efficiency") for k in rows)


# -- 2 . temperature basis ---------------------------------------------------
def test_every_comparison_table_states_its_temperature_basis():
    assert "maximum ambient" in R.COMPARE_INTRO
    assert "20 °C" in R.COMPARE_INTRO and "EMF" in R.COMPARE_INTRO
    c = _col()
    em_rows = {r[0]: r[1] for r in R.em_compare_rows([c], {})[1]}
    assert em_rows["Solved at (hot state)"] == \
        "winding 120 / magnets 110 °C, ambient 45 °C"
    th_rows = {r[0]: r[1] for r in R.thermal_compare_rows([c])[1]}
    assert th_rows["Ambient, the project's maximum [°C]"] == "45"


# -- 3 . rotor mechanics -----------------------------------------------------
def test_the_rotor_sf_row_carries_the_target_and_the_verdict():
    key = "Lowest safety factor (target ≥ 3)"
    rows = {r[0]: r[1:] for r in R.mech_compare_rows([_col(), _col(name="peak")])[1]}
    assert rows[key] == ["2.4 — BELOW 3", "2.4 — BELOW 3"]
    assert rows["Worst case solved"][0] == "overspeed ×1.2, torque 120 N·m"
    ok = _col()
    ok["res"]["rotor_stress"]["sf_min"] = 3.4
    assert {r[0]: r[1] for r in R.mech_compare_rows([ok])[1]}[key] == "3.4 — meets 3"


def test_bridge_and_housing_thickness_print_only_when_the_result_has_them():
    c = _col()
    assert not any("thickness" in r[0] for r in R.mech_compare_rows([c])[1])
    c["res"]["rotor_stress"].update({"bridge_thickness_mm": 0.8,
                                     "rotor_housing_thickness_mm": 1.7})
    rows = {r[0]: r[1] for r in R.mech_compare_rows([c])[1]}
    assert rows["Rotor iron bridge thickness [mm]"] == "0.8"
    assert rows["Rotor housing thickness [mm]"] == "1.7"
    geo = R.geometry_rows({"rotor_house_height": 1.7, "magnet_fill_down": 0.9},
                          {}, None, {})
    assert ["Rotor housing thickness", "1.7", "mm"] == geo[-1][:3]


def test_a_sleeveless_rotor_prints_no_sleeve_rows():
    labels = [r[0] for r in R.mech_compare_rows([_col()])[1]]
    assert not any("leeve" in x for x in labels)


# -- 4 . cooling basis -------------------------------------------------------
def test_propeller_slipstream_cooling_is_named_with_its_air_speed():
    words = R.cooling_basis_words(THERMAL)
    assert "propeller slipstream cooling (propeller_air)" in words
    assert "14.2 m/s" in words and "G40x13" in words and "4,000 rpm" in words
    plain = {"cooling": {"outer": {"mode": "air", "air_speed_mps": 10.0}}}
    assert R.cooling_basis_words(plain) == "forced air, 10 m/s"


def test_the_stored_thermal_record_keeps_the_propeller_source():
    from motor_ai_sim.duty_results import compact_thermal
    stored = compact_thermal({"T_max": 100.0, "cooling_air_source": PROP,
                              "cooling": {"outer": {"mode": "air"}}},
                             {"ambient_temp": 45.0, "cooling_mode": "air"}, "fp")
    assert stored["cooling_air_source"]["propeller_id"] == "G40x13"
    assert "propeller_air" in R.cooling_basis_words(stored)
    assert R.ambient_c_of(stored) == 45.0


# -- 6 . mesh basis ----------------------------------------------------------
def test_the_duty_own_mesh_block_is_printed():
    assert R.mesh_basis_cell(_col()) == (
        "2.07 mm · 1/8 sector · 1 gap layer · 120 steps/period")
    rows = {r[0]: r[1] for r in R.em_compare_rows([_col()], {})[1]}
    assert rows["Mesh and time step"].startswith("2.07 mm · 1/8 sector")
    fb = _col()
    fb["em"]["mesh_settings_source"] = \
        "fallback: the machine has no saved Mesh setting"
    assert "fallback" in R.mesh_basis_cell(fb)


# -- cleanup -----------------------------------------------------------------
def test_the_overview_section_keeps_only_what_the_cover_does_not_carry():
    head, rows = R.duty_overview_slim([_col()])
    assert head == ["Duty", "Mode", "gamma", "Supply", "Solved for"]
    assert len(rows[0]) == 5


def test_no_pwm_means_no_pwm_intro():
    import inspect
    src = inspect.getsource(R._pwm_page)
    assert src.index("PWM_NO_RECORD_AT_ALL") < src.index("pwm_intro(")
