"""The customer report fixes of 2026-10-26 (owner + independent review "FIX FIRST").

Pure builders on synthetic records - no FEM, no store, no server.  The machine numbers are made up; no customer data.

* the requirements check (met / not met / not computed, the project winding limit, the duty's own S2 time);
* one declared basis per duty (the EM run), source labels, no hash in a customer table;
* the configuration-level winding limit read by every limit path;
* translated thermal maps never printed as solved, the real heat-budget residual, the #437 per-point filter;
* the bearing-loads table (customer input needed, UMP, magnetic axial force, ISO 281 L10);
* the mechanics sector crop and the one-line mechanics cells.
"""
from __future__ import annotations

import math

import pytest

# ---------------------------------------------------------------------------
# synthetic duty columns
# ---------------------------------------------------------------------------

EM_CONT = {"rpm": 1000.0, "I_phase_rms_A": 100.0, "T_em_avg_Nm": 95.0, "P_mech_W": 9948.4,
           "P_loss_total_W": 500.0, "coil_temp_C": 150.0, "V_line_peak_V": 300.0,
           "B_gap_mean_T": 1.0, "efficiency": 0.952}
EM_PEAK = {"rpm": 1000.0, "I_phase_rms_A": 200.0, "T_em_avg_Nm": 160.0, "P_mech_W": 16755.2,
           "P_loss_total_W": 1800.0, "coil_temp_C": 150.0, "V_line_peak_V": 340.0,
           "B_gap_mean_T": 1.0, "efficiency": 0.903}


def _col(name, em, res=None, d=None):
    return {"duty": name, "em": dict(em), "em_source": "standalone",
            "d": dict({"name": name, "rpm": em["rpm"], "current_arms": em["I_phase_rms_A"]}, **(d or {})),
            "res": dict(res or {})}


REQ = {"continuous": {"torque_Nm": 100.0, "speed_rpm": 1000.0},
       "peak": {"torque_Nm": 150.0, "speed_rpm": 1000.0, "duration_s": 60.0},
       "battery": {"v_min": 400.0, "v_nom": 450.0, "v_max": 500.0},
       "ambient_max_c": 45.0, "overspeed_rpm": 1200.0}


def _rows_by_label(rows):
    return {r[0]: r for r in rows[1:]}


# ---------------------------------------------------------------------------
# requirements check
# ---------------------------------------------------------------------------

def test_requirements_table_states_met_not_met_and_not_computed():
    from motor_ai_sim import report_customer as rc

    s1 = {"continuous_rating": {"I_cont_A_rms": 70.0, "verified": True, "limits_c": {"winding": 200.0},
                                "power": {"T_em_Nm": 66.5}}}
    peak_cp = {"mode": "limited", "limited": {"t_cold_s": 44.0, "at_limit_c": {"winding": 200.0}}}
    th = {"point": {"ambient_temp": 45.0}, "components": {}}
    cols = [_col("cont", EM_CONT, {"coupled_limit": s1, "thermal": th}),
            _col("peak", EM_PEAK, {"coupled": peak_cp, "thermal": th}, d={"duty_cycle": {"kind": "S2", "t_on_s": 10}})]
    rows = rc.requirements_rows(REQ, cols, winding_limit_c=180.0)
    assert rows[0] == rc.REQUIREMENTS_HEAD
    by = _rows_by_label(rows)
    # continuous torque: 95 against 100 -> not met, with the shortfall
    cont = by["Continuous 100 N·m @ 1,000 rpm, torque"]
    assert cont[1].startswith("95 N·m") and cont[3] == "not met (-5 %)" and "EM run" in cont[2]
    # the S1 rating exists but at 200 °C: not computed at the project's 180 °C
    held = by["100 N·m held continuously, winding ≤ 180 °C"]
    assert held[3] == "not computed at 180 °C" and "S1 search, winding 200 °C" in held[2]
    # peak torque met; its 60 s is judged on the stored time AT ITS OWN LIMIT, never the duty's 10 s setting
    assert by["Peak 150 N·m @ 1,000 rpm"][3] == "met"
    dur = by["Peak held 60 s, winding ≤ 180 °C"]
    assert dur[1] == "44 s to 200 °C" and dur[3] == "not computed at 180 °C"
    # battery: the customer's minimum, the highest line peak
    bat = by["Battery 400 / 450 / 500 V; voltage at 400 V minimum"]
    assert "340" in bat[1] and bat[3] == "met"
    assert by["Maximum ambient 45 °C"][3] == "met"
    ov = by["Mechanics at 1,200 rpm overspeed"]
    assert ov[1].startswith("pending") and ov[3] == "not computed"


def test_requirements_met_when_the_s1_rating_is_at_the_project_limit():
    from motor_ai_sim import report_customer as rc

    s1 = {"continuous_rating": {"I_cont_A_rms": 110.0, "verified": True, "limits_c": {"winding": 180.0},
                                "power": {"T_em_Nm": 104.0}}}
    cols = [_col("cont", dict(EM_CONT, T_em_avg_Nm=104.0), {"coupled_limit": s1})]
    by = _rows_by_label(rc.requirements_rows({"continuous": REQ["continuous"]}, cols, winding_limit_c=180.0))
    assert by["Continuous 100 N·m @ 1,000 rpm, torque"][3] == "met"
    assert by["100 N·m held continuously, winding ≤ 180 °C"][3] == "met"


def test_no_requirement_no_table_and_a_duty_at_another_speed_is_not_used():
    from motor_ai_sim import report_customer as rc

    assert rc.requirements_rows({}, [_col("a", EM_CONT)]) == []
    rows = rc.requirements_rows({"continuous": {"torque_Nm": 90.0, "speed_rpm": 3000.0}}, [_col("a", EM_CONT)])
    assert rows[1][1] == "no duty at this speed" and rows[1][3] == "not computed"


def test_requirements_read_from_a_specification_draft():
    from motor_ai_sim import report_customer as rc

    draft = {"fields": {
        "key_points": {"status": "filled", "value": [
            {"name": "cruise", "torque_Nm": 1000.0, "speed_rpm": 850.0, "duration_s": "continuous"},
            {"name": "takeoff", "torque_Nm": 1500.0, "speed_rpm": 1000.0, "duration_s": 60.0}]},
        "2_1": {"status": "filled", "value": {"voltage_min_V": 633, "voltage_nominal_V": 710, "voltage_max_V": 806}},
        "3_2": {"status": "filled", "value": {"temperature_max_C": 45}},
        # the canonical proof speed: the LARGER of the maximum speed and a stored overspeed
        "1_6": {"status": "filled", "value": {"maximum_rpm": 1200, "overspeed_rpm": 1100}},
        "4_6": {"status": "filled", "value": {"axial_thrust_N": 2500}}}}
    req = rc.requirements_from_draft(draft, "project specification v3")
    assert req["source"] == "project specification v3"
    assert req["continuous"] == {"torque_Nm": 1000.0, "speed_rpm": 850.0}
    assert req["peak"]["duration_s"] == 60.0 and req["peak"]["torque_Nm"] == 1500.0
    assert req["battery"]["v_min"] == 633.0 and req["ambient_max_c"] == 45.0
    assert req["thermal_medium"] == "air"
    assert req["overspeed_rpm"] == 1200.0 and req["axial_thrust_N"] == 2500.0
    # the customer's pack replaces the derived one, and says so (M06)
    b = rc.customer_battery({"v_min": 633.6, "v_nom": 710.4, "v_max": 806.4, "cells": 192}, req)
    assert (b["v_min"], b["v_nom"], b["v_max"], b["source"]) == (633.0, 710.0, 806.0, "customer")


def test_only_filled_answers_are_requirements():
    from motor_ai_sim import report_customer as rc

    draft = {"fields": {
        "2_1": {"status": "missing", "value": {"voltage_min_V": 633}},
        "3_2": {"status": "not_known_yet", "value": {"temperature_max_C": 45}},
        "4_6": {"status": "missing", "value": {"axial_thrust_N": 2500}}}}
    req = rc.requirements_from_draft(draft)
    assert "battery" not in req and "ambient_max_c" not in req and "axial_thrust_N" not in req


def test_a_configuration_override_is_explicit_and_labelled():
    from motor_ai_sim import report_customer as rc

    assert rc.project_requirements("D", "C", {"requirements": {"overspeed_rpm": 1200}}) == {}
    req = rc.project_requirements("D", "C", {rc.REQUIREMENTS_OVERRIDE_KEY: {"overspeed_rpm": 1200}})
    assert req["source"] == rc.OVERRIDE_SOURCE
    assert rc.requirements_source_line(req) == "Requirements from: %s." % rc.OVERRIDE_SOURCE


def test_requirement_torque_is_on_the_main_tables_basis_and_s1_is_shaft():
    from motor_ai_sim import report_customer as rc

    em = dict(EM_CONT, end3d={"k_flux": 0.97, "T_corrected_Nm": 101.0})
    s1 = {"continuous_rating": {"I_cont_A_rms": 110.0, "verified": True, "limits_c": {"winding": 180.0},
                                "reference": {"rpm": 1000.0},
                                "power": {"T_em_Nm": 104.0, "P_shaft_W": 100.0 * 2 * math.pi * 1000.0 / 60.0}}}
    by = _rows_by_label(rc.requirements_rows({"continuous": REQ["continuous"]},
                                             [_col("cont", em, {"coupled_limit": s1})], winding_limit_c=180.0))
    t = by["Continuous 100 N·m @ 1,000 rpm, torque"]
    assert t[1].startswith("101 N·m") and "3-D corrected" in t[2] and t[3] == "met"
    held = by["100 N·m held continuously, winding ≤ 180 °C"]
    assert held[1] == "100 N·m shaft at 110 A" and held[3] == "met"


# ---------------------------------------------------------------------------
# one basis, source labels
# ---------------------------------------------------------------------------

def test_a_sine_duty_reads_its_em_run_not_the_coupled_loop():
    from motor_ai_sim import report as R

    rec = {"drive": "sine", "converged": True, "coil_temp_c": 170.0, "magnet_temp_c": 90.0,
           "em": {"T_em_avg_Nm": 105.0, "P_loss_total_W": 520.0}}
    summary = dict(EM_CONT, coupling={"magnet_temp_c": 87.0})
    col = R.apply_pwm_view({"duty": "cont", "d": {"rpm": 1000.0, "mode": "motor"}, "em": summary,
                            "result": {}, "res": {"coupled": rec}}, {"duties": []})
    assert col["em_source"] == "standalone"
    assert col["em"]["T_em_avg_Nm"] == 95.0                 # the run whose waveforms the figures draw
    assert "coupling" not in col["em"]                      # the embedded loop is another record
    # the controller keeps its operating-point resolution (coupled first)
    out = R.duty_em_source("", "", "cont", {"duties": []}, d={"rpm": 1000.0}, res={"coupled": rec},
                           sine=summary)
    assert out["kind"] == "coupled" and out["em"]["T_em_avg_Nm"] == 105.0


def test_basis_words_and_em_table_basis_row_without_hash():
    from motor_ai_sim import report as R
    from motor_ai_sim import report_customer as rc

    c = _col("cont", EM_CONT)
    c["fp"] = "0123456789abcdef"
    assert rc.duty_basis_words(c, 150.0) == "EM run · winding 150 °C · magnets 150 °C (card)"
    c["basis"] = rc.duty_basis_words(c, 150.0)
    _h, rows = R.em_compare_rows([c], {})
    labels = [r[0] for r in rows]
    assert "Basis" in labels and "Geometry snapshot" not in labels
    assert "0123456789abcdef" not in str(rows)
    assert any(l.startswith("EM efficiency, EM losses only") for l in labels)


def test_coupled_table_labels_its_own_torque_and_state():
    from motor_ai_sim import report as R

    rec = {"mode": "limited", "limited": {"t_cold_s": 44.0}, "em": {"T_em_avg_Nm": 120.0},
           "coil_temp_c": 200.0}
    _h, rows = R.coupled_compare_rows([_col("peak", EM_PEAK, {"coupled": rec})])
    by = {r[0]: r for r in rows}
    assert by["Basis"][1] == "coupled loop · stopped at the limit after 44 s from cold"
    assert by["Torque at these temperatures [N·m]"][1] == "120"


def test_power_accounting_residual_is_printed_as_it_is():
    from motor_ai_sim import report as R

    em = {"P_elec_in_solved_W": 1000.0, "P_mech_W": 900.0, "P_loss_total_W": 120.0}
    w, pct = R.power_accounting_residual(em)
    assert w == pytest.approx(-20.0) and pct == pytest.approx(-2.0)
    assert R.power_accounting_cell({}) == "—"


def test_no_hash_in_the_geometry_agreement_note():
    from motor_ai_sim import report as R

    assert R.duty_fingerprint_note("aaa111", True, []) == "All records of this duty were solved on one geometry."
    bad = R.duty_fingerprint_note("aaa111", False, [("Electromagnetic", "bbb222"), ("Thermal", "aaa111")])
    assert "DIFFERENT geometries" in bad and "Electromagnetic" in bad
    assert "bbb222" not in bad and "aaa111" not in bad


# ---------------------------------------------------------------------------
# the configuration's winding limit
# ---------------------------------------------------------------------------

def test_the_configuration_winding_limit_is_read_by_every_path(monkeypatch):
    from motor_ai_sim import configure_limits as CL
    from motor_ai_sim import coupled_time_to_limit as ttl
    from motor_ai_sim import report as R
    from motor_ai_sim import thermal_duty_cycle as tdc
    from motor_ai_sim import winding_limit as wl

    cfg = {"limits": {"winding_c": 180}, "materials": {}}
    assert wl.config_winding_limit_c(cfg) == 180.0
    assert wl.config_winding_limit_c({"limits": {"winding_c": 9999}}) is None
    assert ttl.winding_limit(cfg)[0] == 180.0
    assert ttl.rated_winding_limit("robot", cfg)[0] == 180.0
    assert R._insulation_limit({}, cfg)[0] == 180.0
    assert CL.thermal_limits(cfg)["winding_C"] == 180.0
    # a configuration without it keeps the project defaults
    assert ttl.winding_limit({"materials": {}})[0] == R.PROJECT_INSULATION_C
    assert R._insulation_limit({}, {})[0] == R.PROJECT_INSULATION_C
    assert tdc.default_limits()["winding"] == R.PROJECT_INSULATION_C
    # the solver paths pass the SOLVED configuration's limit explicitly ...
    assert tdc.default_limits(150.0, 180.0) == {"winding": 180.0, "magnet": 150.0}
    assert ttl.winding_limit(limit_c=180.0)[0] == 180.0
    assert ttl.rated_winding_limit("propeller", limit_c=180.0)[0] == 180.0
    # ... and NOTHING reads the editor's active configuration implicitly
    monkeypatch.setattr(wl, "active_config_winding_limit_c", lambda: 180.0)
    assert tdc.default_limits()["winding"] == R.PROJECT_INSULATION_C
    assert ttl.winding_limit()[0] == R.PROJECT_INSULATION_C


def test_the_solved_configuration_limit_is_read_once_per_file_change(monkeypatch, tmp_path):
    from motor_ai_sim import winding_limit as wl
    from motor_ai_sim.routes import family as fam

    f = tmp_path / "C.yaml"
    f.write_text("limits:\n  winding_c: 180\n", encoding="utf-8")
    reads = []

    def _doc(die, cfg):
        reads.append((die, cfg))
        import yaml
        return yaml.safe_load(f.read_text(encoding="utf-8"))

    monkeypatch.setattr(fam, "_cfg_file", lambda die, cfg: f)
    monkeypatch.setattr(fam, "config_doc", _doc)
    wl._CACHE.clear()
    assert wl.for_config("D", "C") == 180.0 and wl.for_config("D", "C") == 180.0
    assert len(reads) == 1
    # the request's own die / config name the configuration being solved
    assert wl.solved_config({"die": "D", "config": "C"}) == ("D", "C")
    assert wl.for_solve({"die": "D", "config": "C"}) == 180.0
    assert wl.for_config("", "") is None


def test_winding_limit_validation():
    from motor_ai_sim import winding_limit as wl

    assert wl.validate(None) == (None, None)
    assert wl.validate(180) == (180.0, None)
    v, why = wl.validate(5)
    assert v is None and "between" in why


def test_the_materials_table_states_the_project_limit_once():
    from motor_ai_sim import report as R

    rows = R.material_rows({"magnet": "N52UH_150C"}, {}, {}, None, winding_limit_c=180.0)
    by = {r[0]: r for r in rows}
    assert by["Winding limit"][1] == "180 °C" and "Insulation class" not in by


def test_a_limit_stopped_record_at_another_limit_is_not_judged():
    from motor_ai_sim import report as R

    cp = {"mode": "limited", "coil_temp_c": 200.0,
          "limited": {"t_cold_s": 44.0, "at_limit_c": {"winding": 200.0}, "line": "stopped"}}
    col = _col("peak", EM_PEAK, {"coupled": cp})
    ctx = R._warning_context(col, mats={}, batt={}, brg=None, max_speed_rpm=None, mag_lim=None, mag_note="",
                             ins_lim=180.0, ins_note="", cold_k=1.0, cold_note="")
    assert ctx["winding_temp_c"] is None and ctx["hot_spot_c"] is None
    ctx2 = R._warning_context(col, mats={}, batt={}, brg=None, max_speed_rpm=None, mag_lim=None, mag_note="",
                              ins_lim=200.0, ins_note="", cold_k=1.0, cold_note="")
    assert ctx2["winding_temp_c"] == 200.0


# ---------------------------------------------------------------------------
# thermal maps
# ---------------------------------------------------------------------------

def test_a_translated_map_prints_its_basis_line_and_no_temperature():
    from motor_ai_sim import report as R

    th_lim = {"state": "limited", "T_max": 200.0, "T_min": -25.7,
              "components": {"winding": {"max": 200.0, "avg": 190.0}}}
    th_ok = {"state": "steady", "T_max": 150.0, "T_min": 60.0, "point": {"ambient_temp": 45.0},
             "components": {"winding": {"max": 150.0, "avg": 140.0}}}
    _h, rows = R.thermal_compare_rows([_col("a", EM_CONT, {"thermal": th_ok}),
                                       _col("b", EM_PEAK, {"thermal": th_lim})])
    by = {r[0]: r for r in rows}
    assert by["Basis"][2] == R.TRANSLATED_MAP_WORDS
    assert by["Winding (copper), max / avg [°C]"][1:] == ["150 / 140", "—"]
    assert "-25.7" not in str(rows)
    assert by["Coolest point [°C]"][2] == "—"


def test_the_heat_budget_residual_is_in_minus_out_not_the_stored_zero():
    from motor_ai_sim import report as R

    cooling = {"heat_budget": {"losses_W": 2334.9, "residual_W": 0.0},
               "outer": {"heat_removed_W": 3000.0}, "inner": {"heat_removed_W": 4.3}}
    assert R.heat_budget_residual_w(cooling) == pytest.approx(2334.9 - 3004.3)
    rows = R.thermal_budget_rows({"cooling": cooling}, {})
    assert ["Residual, in − out (closure error)", "-669.4"] in rows


def test_a_record_of_another_point_is_not_used():
    from motor_ai_sim import report as R

    stored = {"thermal": {"point": {"rpm": 1000.0, "I_phase_rms": 100.0}, "state": "steady", "T_max": 120.0},
              "coupled": {"point": {"rpm": 1000.0, "I_phase_rms": 140.0}, "coil_temp_c": 180.0}}
    kept = R.own_point_records(stored, {"name": "a", "rpm": 1000.0, "current_arms": 100.0})
    assert "thermal" in kept and "coupled" not in kept


# ---------------------------------------------------------------------------
# bearing loads
# ---------------------------------------------------------------------------

GEO = {"stator_inner_radius": 50.5, "rotor_outer_radius": 50.0, "air_gap": 0.5, "motor_length": 40.0}


def test_magnetic_forces_follow_the_stated_formulas():
    from motor_ai_sim import report_customer as rc

    ump = rc.unbalanced_magnetic_pull_n(1.0, 0.05, 0.04, 0.1)
    assert ump == pytest.approx(math.pi * 0.05 * 0.04 * 0.1 / rc.MU0)
    ax = rc.magnetic_axial_force_n(1.0, 0.05, 0.0005, 0.00025)
    assert ax == pytest.approx(0.5 * (1.0 / (2 * rc.MU0)) * 2 * math.pi * 0.05 * 0.0005)
    assert rc.magnetic_axial_force_n(1.0, 0.05, 0.0005, 0.01) == pytest.approx(2 * ax)    # saturates at one gap


def test_bearing_loads_without_thrust_ask_the_customer():
    from motor_ai_sim import report_customer as rc

    em = dict(EM_CONT, rotating_mass_kg=2.0)
    rows, notes = rc.bearing_load_rows({}, [_col("cont", em)], geo=GEO, brg_assign={}, cfg_doc={})
    assert rows[0] == rc.BEARING_LOADS_HEAD
    assert rows[1][2].startswith(rc.CUSTOMER_INPUT_NEEDED)
    assert rows[1][4] == "no bearings assigned"
    w = 2.0 * rc.G0 * rc.BEARING_LOAD_DEFAULTS["load_factor_g"]
    assert ("weight %s" % format(round(w), ",")) in rows[1][3]
    assert any("customer input needed" in n for n in notes)


def test_bearing_loads_with_thrust_and_a_bearing_give_an_l10(monkeypatch):
    from motor_ai_sim import bearings as B
    from motor_ai_sim import report_customer as rc

    card = B.BearingCard(name="TEST 6010", d=50, D=80, B=16, C_kn=22.9, C0_kn=16.0)
    monkeypatch.setattr(B, "get_bearing", lambda name: card)
    rows, _n = rc.bearing_load_rows({"axial_thrust_N": 500.0}, [_col("cont", dict(EM_CONT, rotating_mass_kg=2.0))],
                                    geo=GEO, brg_assign={"A": {"card": "TEST 6010"}},
                                    cfg_doc={"bearing_loads": {"axial_offset_mm": 0.0}})
    assert rows[1][2].startswith("500 N (thrust 500")
    assert rows[1][4].endswith("h (TEST 6010)")


# ---------------------------------------------------------------------------
# mechanics
# ---------------------------------------------------------------------------

def _ring_mesh(n=72):
    verts, tris = [], []
    for k in range(n):
        a = 2 * math.pi * k / n
        verts += [(10 * math.cos(a), 10 * math.sin(a)), (12 * math.cos(a), 12 * math.sin(a))]
    for k in range(n):
        i, j = 2 * k, 2 * ((k + 1) % n)
        tris += [(i, j, i + 1), (j, j + 1, i + 1)]
    return verts, tris


def test_the_mechanics_maps_are_a_sector_around_the_hot_spot():
    from motor_ai_sim import report as R

    verts, tris = _ring_mesh()
    m = R.sector_tri_mask(verts, tris, 0, 45.0)
    assert m is not None and 0 < int(m.sum()) < len(tris) / 4
    assert R.sector_tri_mask(verts, tris[:4], 0, 45.0) is None          # already a sector: nothing to crop
    assert R.mech_sector_span_deg({"n_poles": 56}) == pytest.approx(3 * 360.0 / 56)
    assert R.mech_sector_span_deg({}) == R.MECH_SECTOR_DEG


def test_mechanics_cells_are_one_line():
    from motor_ai_sim import report as R

    m = {"torque_path": {"verdict": "no verdict - friction-only capacity on magnet_rotor is 1,097 N*m at 722 N*m "
                                    "applied; this excludes pocket-wall normal forces"},
         "magnet_retention": {"verdict": "pocket bottom / shoulder (iron) - 56 magnets travelled 0.2 um"},
         "contact": {"seated": [{"part": "magnet", "travel_rel_um": 0.2}] * 3},
         "interfaces": {"magnet_rotor": {"type": "separation", "open_fraction": 0.91, "lift_off": True}},
         "rpm": 850.0, "sf_min": 15.6}
    assert R.torque_path_cell(m) == "no verdict (friction alone 1,097 N·m vs 722 applied)"
    assert R.magnet_retention_cell(m) == "held by the iron, 0.2 µm"
    assert R.retention_words(False, 91.0) == "assembly only; retention not confirmed (91 % open)"
    assert R.overspeed_case_cell(m, 1200.0) == "pending (1,200 rpm)"
    assert R.overspeed_case_cell(dict(m, rpm=1200.0), 1200.0) == "SF 15.6 at 1,200 rpm"
    _h, rows = R.mech_compare_rows([{"duty": "a", "res": {"rotor_stress": m}, "em": {}, "d": {}}], 1200.0)
    labels = [r[0] for r in rows]
    assert "Overspeed case" in labels and "Magnets seated during the solve" not in labels
    assert "LIFT-OFF" not in str(rows)


def test_mechanics_table_with_nothing_solved_is_two_lines():
    from motor_ai_sim import report as R

    _h, rows = R.mech_compare_rows([{"duty": "a", "res": {}, "em": {}, "d": {}}], 1200.0)
    assert [r[0] for r in rows] == ["Basis", "Overspeed case"]
    assert rows[1][1] == "pending (1,200 rpm)"


def test_the_ring_mode_remedy_follows_the_excitation():
    from motor_ai_sim import report as R

    def _rem(exc):
        ws = R.duty_warnings({"duty": "a", "ring_mode_margin_pct": 0.15, "ring_mode_excitation": exc})
        return next(w["remedy"] for w in ws if w["rule"] == "ring_mode_vs_carrier")

    assert "carrier" not in _rem("slot passing 2×").lower()
    assert "carrier" in _rem("PWM carrier").lower()


# ---------------------------------------------------------------------------
# still to compute
# ---------------------------------------------------------------------------

def test_still_to_compute_names_the_runs_at_the_project_limit(monkeypatch):
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim import report_missing as RM

    stored = {"peak": {"coupled": {"mode": "limited", "limited": {"t_cold_s": 44.0, "at_limit_c": {"winding": 200.0}},
                                   "point": {"rpm": 1000.0}}},
              "cont": {"coupled_limit": {"continuous_rating": {"limits_c": {"winding": 200.0}}}}}
    monkeypatch.setattr(dr, "get", lambda die, cfg: stored)
    cfg = {"limits": {"winding_c": 180},
           "requirements_override": {"peak": {"torque_Nm": 150.0, "speed_rpm": 1000.0, "duration_s": 60.0},
                            "overspeed_rpm": 1200.0},
           "duties": [{"name": "cont", "rpm": 1000.0, "summary": dict(EM_CONT)},
                      {"name": "peak", "rpm": 1000.0, "summary": dict(EM_PEAK)}]}
    rows = RM.still_to_compute("D", "C", cfg)
    items = {(r["duty"], r["item"]) for r in rows}
    assert ("peak", RM.ITEM_S2_LIMIT.format(dur="60", lim="180")) in items
    assert ("cont", RM.ITEM_S1_LIMIT.format(lim="180")) in items
    assert (RM.MOTOR_LEVEL, RM.ITEM_OVERSPEED.format(rpm="1,200")) in items
    assert "our engineers" not in RM.RECOMMENDED_LINE


def test_unstamped_coupled_results_are_named_once(monkeypatch):
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim import report_missing as RM

    stored = {"a": {"coupled": {"coil_temp_c": 150.0}}, "b": {"coupled": {"coil_temp_c": 160.0}},
              "c": {"coupled": {"coil_temp_c": 150.0, "point": {"rpm": 1000.0, "I_phase_rms": 100.0}}}}
    monkeypatch.setattr(dr, "get", lambda die, cfg: stored)
    rows = [r for r in RM.still_to_compute("D", "C", {"duties": []}) if r["item"] == RM.ITEM_UNSTAMPED]
    assert len(rows) == 1 and rows[0]["duty"] == "a, b"


def test_duty_at_point_uses_the_per_point_tolerances():
    from motor_ai_sim import report as R

    duties = [{"name": "a", "rpm": 1000.0, "current_arms": 100.0}]
    assert R._duty_at_point(duties, 1009.0, 102.5) == "a"          # inside 1 % / 3 %
    assert R._duty_at_point(duties, 1000.0, 104.0) is None          # 4 % off in current
    assert R._duty_at_point(duties, 1015.0, 100.0) is None          # 1.5 % off in speed


def test_record_ids_live_in_the_appendix_only():
    from motor_ai_sim import report_customer as rc

    rows = rc.record_id_rows([{"duty": "a", "fp2": "0123456789abcdef",
                               "res": {"thermal": {"geometry_fingerprint": "fedcba9876543210"}}}])
    assert rows[1] == ["a", "01234567", "fedcba98*", "—", "—"]


def test_the_bearing_load_notes_call_the_magnetic_forces_estimates():
    from motor_ai_sim import report_customer as rc

    _rows, notes = rc.bearing_load_rows({}, [_col("cont", dict(EM_CONT, rotating_mass_kg=2.0))], geo=GEO,
                                        brg_assign={}, cfg_doc={})
    assert "estimate" in notes[0] and "axial offset" in notes[0]
    assert "estimate" in notes[1] and "mean air-gap |B|" in notes[1]
