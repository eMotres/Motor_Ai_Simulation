"""A2 of the propeller module: the blade-element momentum kernel (propeller_bemt.py).

No test asserts accuracy against a real propeller (that is stage A3).  They pin what the CODE must satisfy by
construction: both momentum balances close at every annulus, the scaling laws of the equations, bounds that any
admissible solution obeys (figure of merit <= 1), monotonic trends, the polar identities, the table loader, the
registration behind the inflow interface, and that nothing in the thermal side imports the model yet.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_bemt as pb
from motor_ai_sim import propeller_inflow as pi
from motor_ai_sim.propeller_blade import parse_blade

RHO = 1.225


class IdealPolar(pb.PolarProvider):
    """cl = 2 pi alpha, cd constant: no Re dependence, no stall (alpha stays small in the tests)."""

    def __init__(self, cd0=0.01):
        self.cd0 = cd0

    def _eval_half(self, airfoil, alpha, Re, mach, thickness):
        a = np.asarray(alpha, float)
        return 2.0 * math.pi * a, np.full_like(a, self.cd0), set()


def blade_dict(d_m=0.254, hub_frac=0.12, chord_frac=0.09, twist_root=35.0, twist_tip=12.0, n=10):
    tip = d_m / 2.0 * 1000.0
    hub = tip * hub_frac
    st = []
    for i in range(n):
        f = (i + 1) / n
        r = hub + (tip - hub) * f
        st.append({"r_mm": r, "chord_mm": tip * chord_frac * (1.15 - 0.5 * f), "twist_deg": twist_root + (twist_tip - twist_root) * f,
                   "airfoil": "af", "thickness_rel": 0.08})
    return {"geometry_source": "published", "source_ref": "synthetic test blade", "hub_radius_mm": hub, "stations": st,
            "interpolation": "linear"}


def blade(**kw):
    d = kw.get("d_m", 0.254)
    return parse_blade(blade_dict(**kw), d, "t")


def solve(b=None, rpm=6000.0, v=0.0, polar=None, B=2, n=40):
    return pb.solve(b or blade(), B, rpm, RHO, v, polar=polar or IdealPolar(), n_elements=n)


def test_both_momentum_balances_close_at_every_annulus():
    for v in (0.0, 5.0, 12.0):
        res = solve(v=v)
        assert not res.unconverged
        # blade element side == momentum side:  dT/dr = 4 pi rho r F Ua ua ,  dQ/dr = 4 pi rho r^2 F Ua ut
        Ua = res.axial_total
        mom_T = 4 * math.pi * RHO * res.r_m * res.loss_factor * Ua * res.u_axial
        mom_Q = 4 * math.pi * RHO * res.r_m ** 2 * res.loss_factor * Ua * res.u_swirl
        assert np.allclose(res.dT_dr, mom_T, rtol=1e-6, atol=1e-9)
        assert np.allclose(res.dQ_dr, mom_Q, rtol=1e-6, atol=1e-12)


def test_thrust_scales_with_rpm_squared_when_the_polar_has_no_reynolds_dependence():
    a, b = solve(rpm=4000.0), solve(rpm=8000.0)
    assert b.thrust_N / a.thrust_N == pytest.approx(4.0, rel=1e-6)
    assert b.power_W / a.power_W == pytest.approx(8.0, rel=1e-6)
    assert a.ct == pytest.approx(b.ct, rel=1e-9)


def test_geometric_scaling_thrust_with_diameter_to_the_fourth():
    a, b = solve(blade(d_m=0.254)), solve(blade(d_m=0.508))
    assert b.thrust_N / a.thrust_N == pytest.approx(16.0, rel=1e-6)
    assert b.torque_Nm / a.torque_Nm == pytest.approx(32.0, rel=1e-6)


def test_figure_of_merit_never_exceeds_one_and_is_reported_only_in_hover():
    for tw in (20.0, 30.0, 40.0):
        res = solve(blade(twist_root=tw + 10.0, twist_tip=tw - 10.0))
        assert 0.2 < res.figure_of_merit < 1.0
        assert res.efficiency is None
    assert solve(v=8.0).figure_of_merit is None


def test_more_blade_pitch_more_thrust_and_more_power():
    lo, hi = solve(blade(twist_root=30.0, twist_tip=10.0)), solve(blade(twist_root=38.0, twist_tip=18.0))
    assert hi.thrust_N > lo.thrust_N > 0 and hi.power_W > lo.power_W


def test_thrust_falls_with_advance_ratio_and_efficiency_is_between_zero_and_one():
    prev = None
    effs = []
    for v in (0.0, 4.0, 8.0, 12.0, 16.0):
        res = solve(v=v)
        assert prev is None or res.thrust_N < prev
        prev = res.thrust_N
        if v > 0:
            assert 0.0 < res.efficiency < 1.0
            effs.append(res.efficiency)
    assert effs[0] < effs[1]            # efficiency rises from the lightly-moving end


def test_swirl_is_positive_and_axial_velocity_exceeds_the_free_stream():
    res = solve(v=6.0)
    assert np.all(res.u_swirl > 0) and np.all(res.u_axial > 0)
    assert np.all(res.axial_total > 6.0)
    assert np.allclose(res.wake_axial, 6.0 + 2.0 * res.u_axial)


def test_tip_loss_reduces_loading_at_the_tip_and_more_blades_less_loss():
    two, four = solve(B=2), solve(B=4)
    assert two.loss_factor[-1] < two.loss_factor[len(two.loss_factor) // 2] <= 1.0
    assert four.loss_factor[-1] > two.loss_factor[-1]


def test_static_wake_speed_is_of_the_order_of_momentum_theory():
    # sanity of magnitude only (NOT an accuracy claim): far-wake mean vs sqrt(2T/(rho A))
    res = solve()
    A = math.pi * (0.127) ** 2
    ratio = np.sum(res.wake_axial * res.r_m * res.dr_m) / np.sum(res.r_m * res.dr_m) / math.sqrt(2 * res.thrust_N / (RHO * A))
    assert 0.6 < ratio < 1.6


def test_refusals():
    b = blade()
    for kw in ({"rpm": 0.0}, {"rpm": float("nan")}):
        with pytest.raises(pb.BemtError):
            pb.solve(b, 2, kw["rpm"], RHO, polar=IdealPolar())
    with pytest.raises(pb.BemtError):
        pb.solve(b, 2, 5000.0, RHO, -1.0, polar=IdealPolar())
    with pytest.raises(pb.BemtError):
        pb.solve(b, 0, 5000.0, RHO, polar=IdealPolar())
    with pytest.raises(pb.BemtError):
        pb.solve(b, 2, 5000.0, 0.0, polar=IdealPolar())


# ---- polars ---------------------------------------------------------------------------------------------------

def test_flat_plate_polar_limits_continuity_and_symmetry():
    fp = pb.FlatPlatePolar(aspect_ratio=8.0)
    a_s = fp.a_s
    cl1, cd1, fl = fp.eval("x", np.array([a_s - 1e-9, a_s + 1e-9]), 1e5, 0.1, 0.08)
    assert abs(cl1[0] - cl1[1]) < 1e-6 and abs(cd1[0] - cd1[1]) < 1e-6 and "polar_estimated" in fl
    cl90, cd90, _ = fp.eval("x", np.array([math.pi / 2.0]), 1e5, 0.1, 0.08)
    assert abs(cl90[0]) < 1e-9 and cd90[0] == pytest.approx(pb.cd_max(8.0))
    a = np.radians([-170.0, -60.0, -5.0, 5.0, 60.0, 170.0])
    cl, cd, _ = fp.eval("x", a, 2e5, 0.1, 0.08)
    assert np.allclose(cl, -cl[::-1]) and np.allclose(cd, cd[::-1])          # odd lift, even drag
    cl_w, _, _ = fp.eval("x", np.array([math.radians(5.0) + 2 * math.pi]), 2e5, 0.1, 0.08)
    assert cl_w[0] == pytest.approx(cl[3])                                    # 2 pi periodic
    assert fp.cd0(5e4, 0.08) > fp.cd0(5e5, 0.08)                              # drag falls with Re
    assert pb.cd_max(500) == pytest.approx(1.11 + 0.018 * 50)                 # AR capped


def _write_table(path: Path, re, source="synthetic", airfoil="af", a_deg=None):
    fp = pb.FlatPlatePolar()
    a_deg = np.arange(-20, 21, 2.0) if a_deg is None else a_deg
    cl, cd, _ = fp.eval("x", np.radians(a_deg), re, 0.1, 0.08)
    lines = ["# airfoil: %s" % airfoil, "# re: %g" % re, "# source: %s" % source, "alpha_deg,cl,cd"]
    lines += ["%g,%.8g,%.8g" % (x, c, d) for x, c, d in zip(a_deg, cl, cd)]
    path.write_text("\n".join(lines) + "\n")


def test_table_polar_reproduces_its_source_and_extends_continuously(tmp_path):
    _write_table(tmp_path / "re1.csv", 5e4)
    _write_table(tmp_path / "re2.csv", 2e5)
    tp = pb.TablePolar.from_dir(tmp_path)
    assert tp.airfoils() == ["af"] and not tp.estimated
    a = np.radians(np.array([-8.0, 0.0, 6.0, 12.0]))
    cl, cd, _ = tp.eval("af", a, 5e4, 0.1, 0.08)
    ref, refd, _ = pb.FlatPlatePolar().eval("x", a, 5e4, 0.1, 0.08)
    assert np.allclose(cl, ref, atol=2e-3) and np.allclose(cd, refd, atol=2e-3)
    # Reynolds in between: between the two tables (log interpolation), outside: clamped and flagged
    cd_mid = tp.eval("af", np.array([0.1]), 1e5, 0.1, 0.08)[1][0]
    assert tp.eval("af", np.array([0.1]), 2e5, 0.1, 0.08)[1][0] < cd_mid < tp.eval("af", np.array([0.1]), 5e4, 0.1, 0.08)[1][0]
    assert "re_outside_tables" in tp.eval("af", np.array([0.1]), 1e7, 0.1, 0.08)[2]
    # beyond the table ends: continuous at the edge, odd/even symmetric, drag toward cdmax at 90 deg
    edge = math.radians(20.0)
    c_in, d_in, _ = tp.eval("af", np.array([edge - 1e-9, edge + 1e-9]), 2e5, 0.1, 0.08)
    assert abs(c_in[0] - c_in[1]) < 1e-5 and abs(d_in[0] - d_in[1]) < 1e-5
    c_n, d_n, _ = tp.eval("af", np.array([-math.radians(50.0), math.radians(50.0)]), 2e5, 0.1, 0.08)
    assert c_n[0] == pytest.approx(-c_n[1], rel=1e-6) and d_n[0] == pytest.approx(d_n[1], rel=1e-6)
    with pytest.raises(pb.BemtError):
        tp.eval("other", np.array([0.1]), 1e5, 0.1, 0.08)


def test_table_files_are_validated(tmp_path):
    f = tmp_path / "bad.csv"
    for text, match in (("alpha_deg,cl,cd\n0,0,0.01\n", "airfoil"),
                        ("# airfoil: a\n# re: 1e5\n# source: s\nx,y,z\n0,0,0\n", "header"),
                        ("# airfoil: a\n# re: 1e5\n# source: s\nalpha_deg,cl,cd\n" + "\n".join("%d,0,0.01" % i for i in (0, 1, 1, 2, 3)) + "\n", "increasing"),
                        ("# airfoil: a\n# re: 1e5\n# source: s\nalpha_deg,cl,cd\n0,0,0.01\n1,0,0.01\n", "at least 5"),
                        ("# airfoil: a\n# re: 1e5\n# source: s\nalpha_deg,cl,cd\n" + "\n".join("%d,0,0.01" % i for i in (0, 20, 40, 60, 95)) + "\n", "within")):
        f.write_text(text)
        with pytest.raises(pb.BemtError, match=match):
            pb.load_polar_csv(f)


def test_solution_with_table_polar_matches_the_flat_plate_it_was_made_from(tmp_path):
    for re in (2e4, 5e4, 1e5, 3e5, 1e6):
        _write_table(tmp_path / ("re%g.csv" % re), re)
    b = blade()
    a = solve(b, polar=pb.FlatPlatePolar(8.0), n=30)
    t = solve(b, polar=pb.TablePolar.from_dir(tmp_path), n=30)
    assert t.thrust_N == pytest.approx(a.thrust_N, rel=0.02)
    assert not t.polar_estimated and a.polar_estimated and "polar_estimated" in a.flags


# ---- catalogue / interface --------------------------------------------------------------------------------------

def _prop(polars=None, blades=2, with_blade=True):
    d_m = 0.254
    raw = {"schema": "propeller-2", "id": "bemt_test", "vendor": "TEST", "model": "t", "status": "current",
           "geometry": {"diameter_in": 10.0, "diameter_mm": 254.0, "blades": blades},
           "performance": {"data_quality": "geometry_only"}}
    if with_blade:
        raw["blade"] = blade_dict(d_m)
        if polars:
            raw["blade"]["polars"] = polars
    return pp._build(raw, "t")


def test_model_is_registered_and_gives_the_profile_with_flags():
    assert pb.MODEL_BEMT in pi.available_models()
    prop = _prop()
    f = pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=prop, rpm=6000.0, rho=RHO, r_min_m=0.03, r_max_m=0.06))
    assert f.model_id == "bemt-1" and f.thrust_N > 0 and f.torque_Nm > 0
    assert f.air_speed_ms is not None and f.air_speed_ms > 0
    assert all(b.swirl_ms is not None and b.axial_ms is not None for b in f.bands)
    assert {"polar_estimated", "profile_at_disc", "no_wake_contraction"} <= set(f.flags)
    assert any("not yet done" in s for s in f.assumptions)
    # the band the motor sits in: inside the hub there is no value, and it says so
    g = pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=prop, rpm=6000.0, rho=RHO, r_min_m=0.0, r_max_m=0.01))
    assert g.air_speed_ms is None and "band_inside_hub_not_modelled" in g.flags
    h = pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=prop, rpm=6000.0, rho=RHO, r_min_m=0.0, r_max_m=0.05))
    assert "band_partly_inside_hub" in h.flags and h.air_speed_ms is not None


def test_forward_flight_through_the_interface_and_refusals():
    prop = _prop()
    a = pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=prop, rpm=6000.0, rho=RHO, v_inf_ms=0.0))
    b = pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=prop, rpm=6000.0, rho=RHO, v_inf_ms=10.0))
    assert b.thrust_N < a.thrust_N and b.extra["efficiency"] > 0 and a.extra["figure_of_merit"] > 0
    with pytest.raises(pi.InflowModelError):                      # no blade geometry
        pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=_prop(with_blade=False), rpm=6000.0, rho=RHO))
    with pytest.raises(pi.InflowModelError, match="not available"):   # trained-surrogate polars are not shipped
        pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=_prop(polars={"source": "neuralfoil"}), rpm=6000.0, rho=RHO))
    with pytest.raises(pi.InflowModelError, match="no polar table"):
        pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=_prop(polars={"source": "measured"}), rpm=6000.0, rho=RHO))


def test_evaluate_record_and_design_variables():
    prop = _prop()
    rec = pb.evaluate(prop, 6000.0, RHO, 0.0)
    assert rec["model_id"] == "bemt-1" and rec["estimated"] is True and rec["validated_against"] is None
    assert len(rec["profile"]["r_m"]) == len(rec["profile"]["swirl_ms"]) == pb.DEFAULT_N_ELEMENTS
    dv = pb.design_variables(prop)
    names = [d["name"] for d in dv]
    assert "diameter_mm" in names and "blades" in names and "chord_mm@r/R=0.25" in names and "twist_deg@r/R=1" in names
    assert all(d["lo"] is None and d["hi"] is None for d in dv)       # no bound is invented
    assert next(d for d in dv if d["name"] == "blades")["value"] == 2
    with pytest.raises(pb.BemtError):
        pb.evaluate(_prop(with_blade=False), 6000.0)


def test_catalogue_props_without_blade_are_unaffected_and_the_kernel_is_not_wired_into_the_thermal_side():
    assert all(not p.bemt_ready for p in pp.list_propellers())
    root = Path(pb.__file__).parent
    for rel in ("routes/thermal.py", "cooling_options.py", "propeller.py", "propeller_s1.py", "assistant_estimate.py"):
        assert "propeller_bemt" not in (root / rel).read_text(), rel
