"""camber_model: flat-plate polar shifted by the thin-airfoil zero-lift angle of the section (propeller_bemt.CamberedPlatePolar)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim import propeller_airfoil as af
from motor_ai_sim import propeller_bemt as pb
from motor_ai_sim import propeller_design as pd


def a0_deg(code):
    e = af.generated("naca4-" + code, n=201)
    return math.degrees(pb.zero_lift_angle(e.x, e.yu, e.yl))


def test_zero_lift_angle_matches_thin_airfoil_theory():
    # closed-form thin-airfoil values of the NACA 4-digit camber line (m, p): 4412 -4.15 deg, 2412 -2.08 deg, 6412 -6.23 deg
    assert a0_deg("0012") == pytest.approx(0.0, abs=1e-9)
    assert a0_deg("4412") == pytest.approx(-4.15, abs=0.05)
    assert a0_deg("2412") == pytest.approx(-2.08, abs=0.05)
    assert a0_deg("6412") == pytest.approx(-6.23, abs=0.08)
    assert a0_deg("4408") == pytest.approx(a0_deg("4412"), abs=1e-6)       # thickness does not change the camber line


def test_camber_polar_is_the_flat_plate_shifted():
    flat, cam = pb.FlatPlatePolar(8.0), pb.CamberedPlatePolar(8.0)
    a = np.radians(np.array([-4.0, 0.0, 3.0, 8.0]))
    a0 = cam.alpha0("naca4-4412")
    cl_c, cd_c, f = cam.eval("naca4-4412", a, 1e5, 0.2, 0.12)
    cl_f, cd_f, _ = flat.eval("naca4-4412", a - a0, 1e5, 0.2, 0.12)
    assert np.allclose(cl_c, cl_f) and np.allclose(cd_c, cd_f)
    assert "camber_thin_airfoil" in f and "polar_estimated" in f
    cl0, _, _ = cam.eval("naca4-4412", np.array([0.0]), 1e5, 0.2, 0.12)
    assert cl0[0] == pytest.approx(2 * math.pi * -a0, rel=1e-9) and cl0[0] > 0.4    # lift at zero incidence
    sym, _, _ = cam.eval("naca4-0012", a, 1e5, 0.2, 0.12)
    assert np.allclose(sym, flat.eval("naca4-0012", a, 1e5, 0.2, 0.12)[0])


def test_designer_default_unchanged_and_camber_opt_in():
    D = pd.load_design()
    vec = pd.seed_vector(D, 10, 3)
    assert pd.polar_source(D) == "flatplate_model"
    assert pd.candidate_to_propeller2(D, vec)["blade"]["polars"]["source"] == "flatplate_model"
    base = pd.evaluate_candidate(D, vec, "L12")
    Dc = pd.with_polar_source(D, "camber_model")
    assert pd.candidate_to_propeller2(Dc, vec)["blade"]["polars"]["source"] == "camber_model"
    cam = pd.evaluate_candidate(Dc, vec, "L12")
    assert pd.F_AIRFOIL in base["flags"] and pd.F_AIRFOIL not in cam["flags"] and pd.F_AIRFOIL_DRAG in cam["flags"]
    # positive camber adds lift at the same pitch: the torque limit is met at a lower speed, with more thrust per watt
    assert cam["operating"]["rpm"] < base["operating"]["rpm"]
    assert cam["objectives"]["thrust_per_shaft_W"] > 0
    assert pd.with_polar_source(D, None) is D
    with pytest.raises(pd.DesignError):
        pd.with_polar_source(D, "neuralfoil")
