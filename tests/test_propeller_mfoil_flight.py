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
    assert "flight" not in rec and "rpm_matches_rated" not in rec["constraints"]
    assert rec["operating"]["v_inf_ms"] == 0.0


def test_flight_mode_uses_the_flight_speed_and_checks_the_rated_rpm(design):
    rec = pd.evaluate_candidate(design, pd.seed_vector(design, 10, 3), "L12", flight=True)
    op = design.operating_points["L12"]
    assert rec["operating"]["v_inf_ms"] == op.flight_speed_ms == 17.0
    assert rec["objectives"]["flight_thrust_N"] == rec["thrust_N"]
    err = rec["operating"]["rpm"] / op.rated_rpm_ref - 1.0
    assert rec["flight"]["rpm_error_vs_rated"] == pytest.approx(err)
    assert rec["constraints"]["rpm_matches_rated"] == (abs(err) <= pd.FLIGHT_RPM_TOL)
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
    assert pg["torque_Nm"] == pytest.approx(plain["torque_Nm"], rel=0.02)      # both meet the torque limit
    assert pg["operating"]["rpm"] < plain["operating"]["rpm"]                 # more lift per rpm -> the limit is met earlier
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
