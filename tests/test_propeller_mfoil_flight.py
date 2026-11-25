"""mfoil polar tables and the forward-flight mode of the own-propeller designer (owner 2026-11-19)."""
import dataclasses

import pytest

from motor_ai_sim import propeller_bemt as pb
from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_optimize as po


@pytest.fixture(scope="module")
def design():
    return pd.load_design()


def test_mfoil_tables_cover_every_airfoil_option(design):
    tp = pb.TablePolar.from_dir(pb.MFOIL_POLAR_DIR)
    options = {a for zone in design.raw["airfoil_options"].values() for a in zone}
    assert options <= set(tp.airfoils())


def test_mfoil_polar_static_evaluation_is_flagged(design):
    d = pd.with_polar_source(design, "mfoil_table")
    rec = pd.evaluate_candidate(d, pd.seed_vector(d, 10, 3), "L12")
    assert pd.F_POLAR_MFOIL in rec["flags"]
    assert pd.F_AIRFOIL not in rec["flags"] and pd.F_AIRFOIL_DRAG not in rec["flags"]
    assert rec["thrust_N"] > 0 and rec["torque_Nm"] > 0


def test_static_record_unchanged_without_flight(design):
    rec = pd.evaluate_candidate(design, pd.seed_vector(design, 10, 3), "L12")
    assert set(rec["objectives"]) == {"thrust_per_shaft_W", "cooling_air_axial_ms"}
    assert "flight" not in rec and "torque_matches_rated" not in rec["constraints"]
    assert rec["operating"]["v_inf_ms"] == 0.0


def test_flight_mode_uses_the_flight_speed_and_checks_the_rated_rpm(design):
    rec = pd.evaluate_candidate(design, pd.seed_vector(design, 10, 3), "L12", flight=True)
    op = design.operating_points["L12"]
    assert rec["operating"]["v_inf_ms"] == op.flight_speed_ms == 17.0
    assert rec["objectives"]["flight_thrust_N"] == rec["thrust_N"]
    assert rec["operating"]["rpm"] == op.rated_rpm_ref                       # review H4: evaluated AT the rated rpm, no search
    err = rec["torque_Nm"] / op.torque_limit_Nm - 1.0
    assert rec["flight"]["torque_error_vs_limit"] == pytest.approx(err)
    assert rec["constraints"]["torque_matches_rated"] == (abs(err) <= pd.FLIGHT_TORQUE_TOL)
    assert rec["flight"]["rated_power_W"] == pytest.approx(op.torque_limit_Nm * op.rated_rpm_ref * 3.141592653589793 / 30.0)
    assert 0.0 < rec["flight"]["propulsive_efficiency"] < 1.0


def test_flight_mode_refused_without_a_flight_speed(design):
    ops = dict(design.operating_points)
    ops["L12"] = dataclasses.replace(ops["L12"], flight_speed_ms=None)
    d = dataclasses.replace(design, operating_points=ops)
    with pytest.raises(pd.DesignError):
        pd.evaluate_candidate(d, pd.seed_vector(d, 10, 3), "L12", flight=True)


def test_optimizer_flight_objective_needs_flight(design):
    with pytest.raises(po.OptimizeError):
        po.optimize(design, pd.seed_vector(design, 10, 3), "L12", objectives=["flight_thrust_N"], pop=4, generations=1)


def test_optimizer_flight_run(design):
    res = po.optimize(design, pd.seed_vector(design, 10, 3), "L12", objectives=["flight_thrust_N"], pop=4, generations=1, flight=True)
    vals = [e["objectives"]["flight_thrust_N"] for e in res["evaluations"] if e["objectives"].get("flight_thrust_N") is not None]
    assert vals and max(vals) > 0                    # a low-pitch candidate may brake at the flight speed (negative thrust): ranked last, not an error


def test_prandtl_glauert_is_opt_in_and_raises_lift_at_high_mach(design):
    d = pd.with_polar_source(design, "mfoil_table")
    v = pd.seed_vector(d, 13, 2)
    plain = pd.evaluate_candidate(d, v, "L20", flight=True)
    pg = pd.evaluate_candidate(pd.with_compressibility(d, "prandtl_glauert"), v, "L20", flight=True)
    assert "prandtl_glauert" in pg["flags"] and "prandtl_glauert" not in plain["flags"]
    assert pg["operating"]["rpm"] == plain["operating"]["rpm"]                # flight: both at the rated rpm
    assert pg["thrust_N"] > plain["thrust_N"] and pg["torque_Nm"] > plain["torque_Nm"]   # more lift at the same rpm
    with pytest.raises(pd.DesignError):
        pd.with_compressibility(d, "karman_tsien")


def test_chord_shape_rule(design):
    v = pd.seed_vector(design, 10, 3)
    assert pd.chord_shape_violation(design, v) == 0.0
    jag = dict(v)
    jag["chord_R@0.65"], jag["chord_R@0.85"], jag["chord_R@1"] = 0.29, 0.05, 0.27
    assert pd.chord_shape_violation(design, jag) > 1.0


def test_optimizer_chord_rule_is_opt_in(design):
    base = pd.seed_vector(design, 10, 3)
    res = po.optimize(design, base, "L12", objectives=["flight_thrust_N"], pop=4, generations=1, flight=True, chord_max_step=0.08)
    for e in res["evaluations"]:
        if e["violation"] == 0:
            assert pd.chord_shape_violation(design, e["vector"], 0.08) == 0.0
    with pytest.raises(po.OptimizeError):
        po.optimize(design, base, "L12", pop=4, generations=1, chord_max_step=0.9)


def test_failed_loads_never_pass_the_structure_check(design):
    from motor_ai_sim import propeller_structure as ps
    p2 = pd.candidate_to_propeller2(design, pd.seed_vector(design, 10, 3))
    loads = ps.bemt_blade_loads(p2, 1000.0)
    bad = dict(loads, dT_dr_N_per_m=[0.0] * len(loads["dT_dr_N_per_m"]), dQ_dr_Nm_per_m=[0.0] * len(loads["dQ_dr_Nm_per_m"]),
               flags=list(loads.get("flags", [])) + ["unconverged_elements:%d" % len(loads["r_m"])])
    good = ps.root_stress(p2, 1000.0, "pa6_gf30", loads)
    failed = ps.root_stress(p2, 1000.0, "pa6_gf30", bad)
    assert good["load_valid"] is True
    assert failed["load_valid"] is False and failed["passes"] is False and failed["load_problems"]


def test_flight_tip_mach_is_relative_and_polar_range_is_flagged(design):
    d = pd.with_polar_source(design, "mfoil_table")
    v = pd.seed_vector(d, 13, 2)
    st = pd.evaluate_candidate(d, v, "L20")
    fl = pd.evaluate_candidate(d, v, "L20", flight=True)
    rot = fl["operating"]["rpm"] * 3.141592653589793 / 30.0 * 13 * 0.0254 / 2
    assert fl["tip_mach"] > rot / 340.6 * 0.99                                # includes the flight speed
    assert (pd.F_MACH_BEYOND_POLAR in fl["flags"]) == (fl["tip_mach"] > pd.MFOIL_MACH_LIMIT)
    assert pd.F_MACH_BEYOND_POLAR not in pd.evaluate_candidate(design, v, "L20")["flags"]   # only for the Mach-0 tables
    assert st["tip_mach"] > 0


# ---- pre-merge review 2026-11-22 -------------------------------------------------------------------------------------------------

def test_partial_or_malformed_loads_are_refused_before_integration(design):
    import numpy as np
    from motor_ai_sim import propeller_structure as ps
    p2 = pd.candidate_to_propeller2(design, pd.seed_vector(design, 10, 3))
    loads = ps.bemt_blade_loads(p2, 1000.0)
    sliver = {"r_m": [0.0140, 0.0141], "dT_dr_N_per_m": [0.0, 0.0], "dQ_dr_Nm_per_m": [0.0, 0.0], "flags": []}
    s = ps.root_stress(p2, 1000.0, "pa6_gf30", sliver)
    assert s["load_valid"] is False and s["passes"] is False and s["safety_factor"] is None
    assert any(p.startswith("span_not_covered") for p in s["load_problems"])
    short = dict(loads, dQ_dr_Nm_per_m=loads["dQ_dr_Nm_per_m"][:-1])
    assert ps.root_stress(p2, 1000.0, "pa6_gf30", short)["load_problems"] == ["length_mismatch"]
    arrays = {k: (np.asarray(v) if k != "flags" else v) for k, v in loads.items()}      # NumPy arrays are accepted
    assert ps.root_stress(p2, 1000.0, "pa6_gf30", arrays)["load_valid"] is True


def test_optimizer_records_its_requirements_and_refuses_sf_below_3(design):
    base = pd.seed_vector(design, 10, 3)
    with pytest.raises(po.OptimizeError):
        po.optimize(design, base, "L12", pop=4, generations=1, structure_material="pa6_gf30", min_safety_factor=1.0)
    res = po.optimize(design, base, "L12", pop=4, generations=1)
    req = res["settings"]["requirements"]
    assert req["structure"] == "off" and req["chord_max_step"] == "off" and res["settings"]["qualified"] is False
    assert {"structure", "chord_shape", "full_structure", "motor_envelope"} <= set(res["settings"]["not_checked"])


def test_one_feasibility_policy_for_front_and_serialized_status(design):
    res = po.optimize(design, pd.seed_vector(design, 10, 3), "L12", pop=6, generations=1, enforce_tip_mach=False, tip_mach_limit=0.3)
    assert res["settings"]["constraints_disabled"] == ["tip_mach_within_limit"]
    by = {e["index"]: e for e in res["evaluations"]}
    for e in res["evaluations"]:
        assert e["constraints_met"] == (e["violation"] == 0 and all(v is not None for v in e["objectives"].values()))
    assert all(by[i]["constraints_met"] for i in res["front"])


def test_one_torque_band_and_static_search_convergence_is_a_constraint(design):
    assert pd.FLIGHT_TORQUE_TOL == pd.TORQUE_TOL                              # one symmetric band, the same as the cap
    rec = pd.evaluate_candidate(design, pd.seed_vector(design, 10, 3), "L12")
    assert rec["constraints"]["rpm_search_converged"] is True
    assert po._violation({"constraints": {"rpm_search_converged": False}}, True, 0.9) >= 1.0


def test_the_whole_bridged_alpha_interval_is_flagged_and_gates_mfoil_designs(design):
    import math
    tp = pb.TablePolar.from_dir(pb.MFOIL_POLAR_DIR)
    t = [x for x in tp._by["naca4-0012"] if x.re == 70000][0]
    assert any(abs(lo - math.radians(-7)) < 1e-9 and abs(hi - math.radians(-5)) < 1e-9 for lo, hi in tp.gap_intervals(t))
    assert "polar_alpha_gap" in tp._table_flags([t], math.radians(-6.75))           # inside the bridge, 0.75 deg from the failed angle
    assert "polar_alpha_gap" not in tp._table_flags([t], math.radians(-4.0))
    d = pd.with_polar_source(design, "mfoil_table")
    rec = pd.evaluate_candidate(d, pd.seed_vector(d, 10, 3), "L12", flight=True)
    assert rec["constraints"]["polar_data_supported"] == (not ({"polar_alpha_gap", "polar_beyond_table"} & set(rec["flags"])))
    assert rec["constraints"]["tip_mach_within_polar_model"] == (rec["tip_mach"] <= pd.MFOIL_MACH_LIMIT)


def test_polar_mach_envelope_is_a_constraint_and_v_inf_uses_helical_mach(design):
    d = pd.with_polar_source(design, "mfoil_table")
    v = pd.seed_vector(d, 13, 2)
    rec = pd.evaluate_candidate(d, v, "L20", v_inf=30.0)
    rot = rec["operating"]["rpm"] * 3.141592653589793 / 30.0 * 13 * 0.0254 / 2
    assert rec["tip_mach"] > rot / 340.6 * 1.001                               # the static-rpm path with v_inf is helical too
    if rec["tip_mach"] > pd.MFOIL_MACH_LIMIT:
        assert rec["constraints_met"] is False
