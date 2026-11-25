"""Regression tests for the verified findings of the propeller code review of 2026-11-24 (Gemini 3.1 Pro, verified by US · propeller:
~/reports/propeller_review_verification_2026-11-24.md).  One test per TRUE / PARTLY finding, the finding id in the test name.
The two FALSE findings (3c "NaN in low-pitch flight", 10a "temp_c=nan gives HTTP 500") have no fix; 3c's true part (a negative
thrust returned without a word) is pinned here as a flag."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import propeller_airfoil as af
from motor_ai_sim import propeller_bemt as pb
from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_design_api as pda
from motor_ai_sim import propeller_inflow as pi
from motor_ai_sim import propeller_optimize as po
from motor_ai_sim import propeller_s1 as s1
from motor_ai_sim import propeller_structure as ps
from motor_ai_sim import propeller_tolerance as pt
from motor_ai_sim.routes.propellers import router

D = pd.load_design()


def stub(dz, v, op, cooling=True):
    pen = (v["twist_deg@0.45"] - 20.0) ** 2 / 100.0
    c = v["chord_R@0.2"]
    return {"design_id": "stub", "objectives": {"thrust_per_shaft_W": 1.0 - 2.0 * c - pen, "cooling_air_axial_ms": (5.0 + 20.0 * c) if cooling else None},
            "tip_mach": 0.4, "constraints": {"torque_within_limit": True, "tip_mach_within_limit": True, "bemt_converged": True},
            "constraints_met": True, "operating": {"rpm": 1.0}, "flags": []}


# ---------------------------------------------------------------- 1 cooling air ------------------------------------------------------
def test_1a_annulus_mean_is_v_plus_f_times_induced():
    prop = pd.build_propeller(D, pd.seed_vector(D, 12, 2))
    res = pb.solve(prop.blade, 2, 9000.0, 1.225, 10.0, polar=pb.polar_for(prop))
    assert np.allclose(res.axial_mean, 10.0 + res.loss_factor * res.u_axial)
    assert np.allclose(res.wake_axial_mean, 10.0 + 2.0 * res.loss_factor * res.u_axial)
    assert np.all(res.axial_mean <= res.axial_total + 1e-12)          # F <= 1: the mean never exceeds the blade-local value


def test_1ab_thin_sliver_cannot_stand_for_the_motor_band_and_no_jump_at_the_edge():
    """The review's case: 10 in, 3 blades, hub 19 mm, cut-out 19.5 mm, motor band 0-20 mm.  Before: 15.5 m/s from a 0.5 mm sliver,
    and None (-1e6 in the optimiser) once the cut-out passed 20 mm."""
    vals = {}
    for cut in (19.5, 19.9, 20.1):
        v = pd.seed_vector(D, 10, 3)
        v["hub_radius_mm"], v["root_cutout_mm"] = 19.0, cut
        rec = pd.evaluate_candidate(D, v, "L12")
        vals[cut] = rec["objectives"]["cooling_air_axial_ms"]
        if cut > 19.5:                                                # the hub is solid: at 19.5 the sliver still covers half the open band
            assert any(f.startswith("band_low_coverage") for f in rec["flags"])
    assert vals[19.5] < 8.0                                           # was 15.5 m/s
    assert vals[20.1] == pytest.approx(0.0, abs=1e-9)                 # hover: the uncovered open band gets the free stream (0), not None
    assert vals[19.5] >= vals[19.9] >= vals[20.1]                     # continuous as the cut-out moves out


# ---------------------------------------------------------------- 2 structure --------------------------------------------------------
def _tapered():
    v = pd.seed_vector(D, 10, 2)
    for x, c in ((0.2, 0.30), (0.3, 0.30), (0.45, 0.05), (0.65, 0.05), (0.85, 0.05), (1.0, 0.05)):
        v["chord_R@%g" % x] = c
    v["airfoil_mid"] = "naca4-4408"
    return pd.candidate_to_propeller2(D, v)


def test_2a_every_station_is_checked_and_the_minimum_governs():
    out = ps.root_stress(_tapered(), D.operating_points["L12"].rated_rpm_ref, "pa6_gf30")
    assert out["load_valid"] is True
    assert out["root_safety_factor"] > 3.0                            # the root alone passed (3.89 before the fix)
    assert out["safety_factor"] < 0.2 and out["passes"] is False      # the thin outboard station fails (0.04)
    assert out["governing_r_m"] > out["per_station"][0]["r_m"]
    assert out["safety_factor"] == pytest.approx(min(s["safety_factor"] for s in out["per_station"]))
    assert out["section_used"]["r_m"] == pytest.approx(out["governing_r_m"])


def test_2b_thickness_scaled_about_the_camber_line():
    e8, e12 = af.generated("naca4-4408"), af.generated("naca4-4412")
    tc8, tc12 = af.section_props(e8.x, e8.yu, e8.yl).t_c, af.section_props(e12.x, e12.yu, e12.yl).t_c
    scaled = ps._outline_props(e8.x, e8.yu, e8.yl, tc12 / tc8)
    true = ps._outline_props(e12.x, e12.yu, e12.yl, 1.0)
    assert scaled["i_flap"] == pytest.approx(true["i_flap"], rel=0.03)   # was +12 % (camber scaled with the thickness)
    assert scaled["n_max"] == pytest.approx(true["n_max"], rel=0.03)
    assert scaled["area"] == pytest.approx(true["area"], rel=0.02)


def test_2c_unusable_loads_are_refused_not_passed():
    p2 = _tapered()
    secs = ps.blade_sections(p2)
    loads = ps.bemt_blade_loads(p2, 8000.0)
    loads["flags"] = list(loads["flags"]) + ["unconverged_elements:3"]
    with pytest.raises(ps.StructureError):
        ps.root_stress_from_sections(secs, 8000.0, "pa6_gf30", loads)


def test_2d_the_result_names_what_is_not_checked():
    out = ps.root_stress(pd.candidate_to_propeller2(D, pd.seed_vector(D, 10, 2)), 8000.0, "pa6_gf30")
    nc = " ".join(out["not_checked"])
    for word in ("fatigue", "flutter", "resonance", "K_t", "torsion", "bolt", "compression", "principal axes"):
        assert word in nc


# ---------------------------------------------------------------- 3 BEMT state ------------------------------------------------------
def test_3a_reynolds_number_belongs_to_the_returned_relative_speed():
    prop = pd.build_propeller(D, pd.seed_vector(D, 12, 2))
    res = pb.solve(prop.blade, 2, 9000.0, 1.225, 15.0, polar=pb.polar_for(prop))
    rm, dr, chord, th, thick, afs, _ = pb.element_grid(prop.blade, pb.DEFAULT_N_ELEMENTS)
    W = np.hypot(res.axial_total, res.r_m * 9000.0 * math.pi / 30.0 - res.u_swirl)
    re = 1.225 * W * chord / pb.air_viscosity(15.0)
    ok = np.ones(len(rm), bool); ok[res.unconverged] = False
    assert np.max(np.abs(res.reynolds[ok] / re[ok] - 1.0)) < 1e-5


def test_3b_an_element_without_momentum_root_is_not_summed_as_zero():
    v = pd.seed_vector(D, 10, 2)
    v["twist_deg@0.85"] = 0.0
    v["twist_deg@1"] = 0.0
    prop = pd.build_propeller(D, v)
    res = pb.solve(prop.blade, 2, 6000.0, 1.225, 0.0, polar=pb.FlatPlatePolar())
    assert res.unconverged
    assert any(f.startswith("unconverged_elements") for f in res.flags) and any(f.startswith("uninduced_estimate") for f in res.flags)
    assert all(res.dQ_dr[i] > 0 for i in res.unconverged)            # the profile drag torque is there


def test_3c_negative_thrust_is_named():
    v = pd.seed_vector(D, 10, 2)
    for x in D.control:
        v["twist_deg@%g" % x] = 5.0
    prop = pd.build_propeller(D, v)
    res = pb.solve(prop.blade, 2, 8000.0, 1.225, 20.0, polar=pb.polar_for(prop))
    assert res.thrust_N < 0 and "negative_thrust" in res.flags


def test_3e_curve_rows_carry_flags_and_the_helical_tip_mach():
    c = pda.curve(pd.seed_vector(D, 12, 2), "L20", n=3, v_inf=20.0)
    for row in c["rows"]:
        assert isinstance(row["flags"], list)
        rot = pd._tip_mach(12 * 0.0127, row["rpm"], float(D.raw["air"]["temp_c"]))
        assert row["tip_mach"] == pytest.approx(math.hypot(rot, 20.0 / pb.sound_speed(float(D.raw["air"]["temp_c"]))))


# ---------------------------------------------------------------- 4 polars ----------------------------------------------------------
def test_4b_table_ending_before_zero_lift_keeps_falling_below_its_first_angle():
    a = np.radians(np.arange(-6.0, 11.0))
    t = pb.PolarTable("x", 4.5e5, a, 0.04 + 0.105 * (np.degrees(a) + 6.0), np.full(len(a), 0.012), "synthetic", ())
    tp = pb.TablePolar([t])
    cl, _ = tp._one(t, np.radians([-5.0, -6.0, -6.2, -7.0, -10.0, -20.0]))
    assert cl[1] == pytest.approx(0.04)
    assert cl[1] > cl[2] > cl[3] > cl[4]                              # was: cl(-7) = -0.006 after +0.038 at -6, slope reversed
    assert cl[3] < 0 and cl[5] < 0


def test_4c_cambered_plate_stalls_at_the_generic_angle_not_shifted_by_alpha0():
    cp = pb.CamberedPlatePolar()
    cl, _, fl = cp.eval("naca4-6412", np.radians([2.0, 4.0, 6.0, 8.0, 10.0, 12.0]), 2e5, 0.1, 0.12)
    assert all(np.diff(cl) >= 0)                                      # was: peak at 6 deg, then falling
    a0 = cp.alpha0("naca4-6412")
    assert cl[0] == pytest.approx(2 * math.pi * (math.radians(2.0) - a0))
    assert max(cl) == pytest.approx(pb.CAMBER_CL_MAX) and "camber_cl_max_capped" in fl    # follow-up: no optimistic 2.0
    assert "camber_cl_max_literature" in fl                           # no tables given: the literature ceiling, and it says so


def _table(af, re, alpha_deg, cl, gaps=()):
    a = np.radians(alpha_deg)
    return pb.PolarTable(af, re, a, np.asarray(cl, float), np.full(len(a), 0.02), "test", tuple(np.radians(gaps)))


def test_followup2_camber_ceiling_is_the_sections_own_from_the_mfoil_tables():
    """Follow-up 2 of #865: with tables for the section the lift ceiling is the table's cl max at that Re (log-Re interpolation),
    not the literature 1.4; a gap-bridged angle does not count, and a ceiling on the last tabulated angle is flagged."""
    al = [-8, -4, 0, 4, 8, 12, 14, 16]
    t1 = _table("naca4-6412", 1e5, al, [-0.5, -0.1, 0.5, 0.9, 1.2, 1.25, 1.1, 1.0])
    t2 = _table("naca4-6412", 4e5, al, [-0.6, -0.1, 0.6, 1.0, 1.4, 1.55, 1.5, 1.3])
    cp = pb.CamberedPlatePolar(cl_max_tables=pb.TablePolar([t1, t2]))
    hi, lo, f = cp.cl_limits("naca4-6412", np.array([1e5, 2e5, 4e5, 1e6]))
    w = math.log(2e5 / 1e5) / math.log(4e5 / 1e5)
    assert hi[0] == pytest.approx(1.25) and hi[2] == pytest.approx(1.55) and hi[1] == pytest.approx(1.25 + w * 0.30)
    assert hi[3] == pytest.approx(1.55) and "camber_cl_max_re_clamped" in f and "camber_cl_max_from_mfoil" in f
    assert lo[0] == pytest.approx(0.5) and lo[2] == pytest.approx(0.6)
    cl, _, fl = cp.eval("naca4-6412", np.radians([2.0, 11.0]), np.array([1e5, 1e5]), np.array([0.1, 0.1]), 0.12)
    assert cl[1] == pytest.approx(1.25) and "camber_cl_max_capped" in fl and "camber_cl_max_literature" not in fl
    # an airfoil without tables keeps the literature ceiling
    _, _, f2 = cp.cl_limits("naca4-2412", np.array([2e5]))
    assert f2 == {"camber_cl_max_literature"}
    # a gap-bridged angle is not a ceiling; a ceiling on the last row means the stall is not resolved
    tg = _table("x", 2e5, [0, 4, 8, 10, 12], [0.4, 0.8, 1.2, 1.9, 1.3], gaps=(10,))
    assert pb.table_cl_limits(tg)[0] == pytest.approx(1.3) and pb.table_cl_limits(tg)[2] is False
    te = _table("y", 2e5, [0, 4, 8, 12], [0.4, 0.8, 1.2, 1.3])
    assert pb.table_cl_limits(te)[2] is False
    _, _, f3 = pb.CamberedPlatePolar(cl_max_tables=pb.TablePolar([te, _table("y", 4e5, [0, 4, 8, 12], [0.4, 0.8, 1.2, 1.3])])).cl_limits("y", np.array([3e5]))
    assert "camber_cl_max_table_end" in f3


def test_followup2_camber_model_reads_the_shipped_mfoil_tables():
    tp = pb._mfoil_tables(pb.MFOIL_POLAR_DIR)
    assert tp is not None and "naca4-4412" in tp.airfoils()
    cp = pb.CamberedPlatePolar(cl_max_tables=tp)
    hi, _, f = cp.cl_limits("naca4-4412", np.array([1e5, 3e5]))
    assert "camber_cl_max_from_mfoil" in f and 0.8 < hi[0] < 1.8 and 0.8 < hi[1] < 1.8
    assert pb._mfoil_tables(Path("/nonexistent-mfoil")) is None


def test_followup3_w_re_not_converged_is_a_failed_constraint(monkeypatch):
    """Follow-up 3 of #865: elements whose W / Re / polar did not settle within the passes make the point infeasible."""
    d = pd.load_design()
    vec = pd.seed_vector(d, 10, 2)
    monkeypatch.setattr(pb, "_W_PASSES", 1)                            # one pass never confirms a settled W
    rec = pd.evaluate_candidate(d, vec, "L12")
    assert "w_re_not_converged" in rec["flags"] and rec["constraints"]["w_re_converged"] is False
    assert rec["constraints_met"] is False
    monkeypatch.setattr(pb, "_W_PASSES", 8)
    rec = pd.evaluate_candidate(d, vec, "L12")
    assert rec["constraints"]["w_re_converged"] is True


def test_8d_prandtl_glauert_is_faded_out_past_the_attached_range():
    base = pb.FlatPlatePolar()
    pg = pb.PrandtlGlauertPolar(base)
    for adeg, full in ((5.0, True), (30.0, False)):
        cl0, _, _ = base.eval("x", np.radians([adeg]), np.array([2e5]), np.array([0.5]), 0.1)
        cl1, _, fl = pg.eval("x", np.radians([adeg]), np.array([2e5]), np.array([0.5]), 0.1)
        assert cl1[0] == pytest.approx(cl0[0] / math.sqrt(1 - 0.25) if full else cl0[0])
    assert "prandtl_glauert_faded_post_stall" in fl


def test_8b_clamped_reynolds_number_is_unsupported_data():
    assert "re_outside_tables" in pd.F_POLAR_UNSUPPORTED


# ---------------------------------------------------------------- 5 optimiser ------------------------------------------------------
def test_5a_repair_leaves_fixed_variables_alone():
    v = pd.seed_vector(D, 10, 2)
    v["twist_deg@0.3"], v["twist_deg@0.45"] = 15.0, 22.0
    free = {"twist_deg@0.2", "twist_deg@0.3"}
    assert po._repair(D, dict(v), free)["twist_deg@0.45"] == 22.0
    assert po._repair(D, dict(v))["twist_deg@0.45"] == 15.0          # all names free: as before
    w = dict(v, hub_radius_mm=12.0, root_cutout_mm=10.0)                 # cut-out inside the hub; only the hub is free
    assert po._repair(D, dict(w), {"hub_radius_mm"})["root_cutout_mm"] == 10.0


def test_5b_duplicates_are_neither_re_evaluated_nor_listed_twice():
    r = po.optimize(D, pd.seed_vector(D, 10, 2), "L12", pop=6, generations=3, seed=4, evaluate=stub, free=["airfoil"])
    keys = [json.dumps(e["vector"], sort_keys=True) for e in r["evaluations"]]
    assert len(keys) == len(set(keys))
    assert all(e["index"] >= 0 for e in r["evaluations"])


def test_5c_missing_requested_objective_is_infeasible():
    r = po.optimize(D, pd.seed_vector(D, 10, 2), "L12", pop=4, generations=1, seed=1,
                    evaluate=lambda dz, v, op: stub(dz, v, op, cooling=False), free=["outer"])
    assert all(e["violation"] >= 1.0 and e["note"].startswith("objective_unavailable") for e in r["evaluations"] if not e["note"].startswith("invalid"))
    assert r["front"] == []


def test_5d_unknown_free_group_is_refused():
    with pytest.raises(po.OptimizeError):
        po.optimize(D, pd.seed_vector(D, 10, 2), "L12", pop=4, generations=1, seed=1, evaluate=stub, free=["outer", "typo"])


def test_5e_structure_screening_uses_the_run_air_speed(monkeypatch):
    seen = {}

    def fake(p2, rpm, material, **kw):
        seen.update(kw)
        return {"load_valid": True, "safety_factor": 5.0}
    monkeypatch.setattr(ps, "root_stress", fake)
    rec = {"operating": {"rpm": 9000.0, "v_inf_ms": 17.0, "rho_kg_m3": 1.2}}
    assert po._structure_violation(D, pd.seed_vector(D, 10, 2), rec, "pa6_gf30", 3.0) == 0.0
    assert seen == {"rho_air": 1.2, "v_inf": 17.0}


# ---------------------------------------------------------------- 6 design ------------------------------------------------------------
def test_6a_rpm_search_with_oncoming_air_does_not_give_up_at_zero_torque():
    v = pd.seed_vector(D, 10, 2)
    for x in D.control:
        v["twist_deg@%g" % x] = max(4.0, v["twist_deg@%g" % x] * 0.35)
    rec = pd.evaluate_candidate(D, v, "L12", v_inf=17.0)             # was DesignError "absorbs no positive torque at 13628 rpm"
    assert rec["operating"]["rpm"] > D.operating_points["L12"].rated_rpm_ref
    lim = D.operating_points["L12"].torque_limit_Nm
    assert rec["constraints"]["rpm_search_converged"] and rec["torque_Nm"] == pytest.approx(lim, rel=0.01) \
        or ("rpm_capped" in rec["flags"] and rec["torque_Nm"] < lim)


def test_6b_capped_design_far_below_the_torque_limit_is_not_feasible():
    v = pd.seed_vector(D, 10, 2)
    for x in D.control:
        v["chord_R@%g" % x] = 0.05
    rec = pd.evaluate_candidate(D, v, "L12")
    assert "rpm_capped" in rec["flags"]
    assert rec["constraints"]["torque_reached_at_cap"] is False and rec["constraints_met"] is False


def test_6c_blade_count_must_be_an_exact_whole_number():
    for bad in (2.9, "2", True):
        assert any(b["rule"] == "blades_not_offered" for b in pd.check_candidate(D, dict(pd.seed_vector(D, 10, 2), blades=bad)))
    assert pd.check_candidate(D, dict(pd.seed_vector(D, 10, 2), blades=2.0)) == []
    assert pd.check_candidate(D, dict(pd.seed_vector(D, 10, 2), diameter_in="abc"))   # a violation, not a ValueError


# ---------------------------------------------------------------- 7 S1 ------------------------------------------------------------------
def test_7a_voltage_check_warm_start_bisects_below_a_stale_hint(monkeypatch):
    """ok(i) holds from i ~ 30.6 A; a stale hint of 40 A was returned as the answer before the fix."""
    m = {"psi_map": {"I_A": [0.0, 100.0]}, "voltage": {"m": 1.0, "v_dc_grid": 60.0}, "magnet_ref_temp_C": 20.0, "R_phase_ohm": 0.01}
    v_grid_lim = 60.0 / s1.SQ3
    monkeypatch.setattr(s1, "resistance", lambda mm, t: 0.01)
    monkeypatch.setattr(s1, "current_cap", lambda mm: 100.0)
    monkeypatch.setattr(s1, "operating_angle", lambda mm, rpm, i, vlim, tm, r: 10.0 if vlim >= v_grid_lim - 1e-9 else 50.0)
    monkeypatch.setattr(s1, "dq_state", lambda mm, i, g, tm: {"T": i * math.cos(math.radians(g))})
    monkeypatch.setattr(s1, "phase_voltage", lambda mm, rpm, i, g, tm, r: 1e3)
    cold = s1.voltage_check(m, {}, 10000.0, 20.0, 60.0, 80.0, 40.0)
    warm = s1.voltage_check(m, {}, 10000.0, 20.0, 60.0, 80.0, 40.0, i_hint=40.0)
    edge = 20.0 * math.cos(math.radians(10.0)) / math.cos(math.radians(50.0))
    assert cold["status"] == warm["status"] == "deeper_fw"
    assert cold["I_A"] == pytest.approx(edge, abs=0.02) and warm["I_A"] == pytest.approx(edge, abs=0.02)


def test_7b_missing_pwm_loss_refuses_the_point():
    from tests.test_propeller_s1 import _model, RPM, CUR
    m = _model()
    drive = {"rpm": RPM, "I_A": CUR, "pwm_extra_W": [[None] * len(CUR) for _ in RPM]}
    out = s1.losses(m, 6000.0, 20.0, 80.0, drive)
    assert "pwm_extra" in out["unavailable"] and "pwm_extra" in out["drive_unavailable"]


# ---------------------------------------------------------------- 9 tolerance ------------------------------------------------------
def test_9a_thickness_case_passes_its_scale_explicitly():
    assert not hasattr(pt, "_perturbed_build")
    v = pd.seed_vector(D, 10, 2)
    a, b = pd.candidate_to_propeller2(D, v), pd.candidate_to_propeller2(D, v, 1.1)
    assert all(y["thickness_rel"] == pytest.approx(1.1 * x["thickness_rel"]) for x, y in zip(a["blade"]["stations"], b["blade"]["stations"]))


def test_9bc_tolerance_payload_says_what_it_cannot_show():
    r = pt.evaluate_robust(pd.with_polar_source(D, "mfoil_table"), pd.seed_vector(D, 10, 2), "L12", n_random=0)
    assert r["worst_is_sampled"] is True and "not a bound" in r["sampling"]
    assert "thickness" in r["insensitive"] and r["insensitive_reason"]
    r0 = pt.evaluate_robust(D, pd.seed_vector(D, 10, 2), "L12", n_random=0)
    assert r0["insensitive"] == []


# ---------------------------------------------------------------- 10 routes ---------------------------------------------------------
def test_10b_bad_inputs_are_422_not_500():
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app, raise_server_exceptions=False)
    seed = pd.seed_vector(D, 10, 2)
    assert c.post("/api/propellers/design/check", json={"vector": dict(seed, diameter_in="abc")}).status_code == 422
    assert c.post("/api/propellers/design/sweep", json={"vector": seed, "op": "L12", "variable": "twist_deg@0.45", "lo": "a", "hi": "b"}).status_code == 422
    assert c.post("/api/propellers/design/optimize", json={"vector": seed, "op": "L12", "seed": -1, "pop": 4, "generations": 1}).status_code == 422


def test_10c_inputs_are_bounded():
    with pytest.raises(pda.ApiProblem):
        pda._clean_vector({"k%d" % i: 1.0 for i in range(pda.MAX_VECTOR_KEYS + 1)})
    prop = pd.build_propeller(D, pd.seed_vector(D, 10, 2))
    with pytest.raises(pb.BemtError):
        pb.solve(prop.blade, 2, 6000.0, 1.225, 0.0, polar=pb.FlatPlatePolar(), n_elements=pb.MAX_N_ELEMENTS + 1)


def test_11_6_no_converged_element_is_no_wake(monkeypatch):
    prop = pd.build_propeller(D, pd.seed_vector(D, 10, 2))
    monkeypatch.setattr(pb, "_solve_element", lambda *a, **k: (None, 0))
    with pytest.raises(pi.InflowModelError):
        pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=prop, rpm=6000.0, rho=1.225, v_inf_ms=0.0, r_min_m=0.0, r_max_m=0.02))
