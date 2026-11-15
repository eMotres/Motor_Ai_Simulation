"""Tolerance robustness of the own propeller (propeller_tolerance.py): corner set, determinism, bounds, honest labels, failures reported.

The tolerance values are placeholders; no assertion claims a factory value or accuracy against a real propeller.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_tolerance as pt

D = pd.load_design()
TOL = pt.Tolerance()
LABEL = "placeholder - factory question (ISO 20457 class or the shop's own)"


def seed():
    return pd.seed_vector(D, 10, 2)


def test_corner_count_and_names():
    ps = pt.perturbations(seed(), TOL, n_random=0, d=D)
    assert [p.name for p in ps] == ["chord+", "chord-", "twist+", "twist-", "thickness+", "thickness-", "trailing_edge_min", "all_plus", "all_minus"]
    assert len(pt.perturbations(seed(), TOL, n_random=5, seed=1, d=D)) == 14


def test_perturbations_deterministic_for_a_seed_and_differ_between_seeds():
    a = pt.perturbations(seed(), TOL, n_random=4, seed=7, d=D)
    b = pt.perturbations(seed(), TOL, n_random=4, seed=7, d=D)
    c = pt.perturbations(seed(), TOL, n_random=4, seed=8, d=D)
    assert [(p.name, p.vec, p.thickness_scale) for p in a] == [(p.name, p.vec, p.thickness_scale) for p in b]
    assert [p.vec for p in a[9:]] != [p.vec for p in c[9:]]


def test_every_perturbation_is_a_valid_candidate_inside_the_bounds():
    big = pt.Tolerance(chord_frac=0.9, twist_deg=30.0, thickness_frac=0.5)        # forces clamping at the bounds
    for tol in (TOL, big):
        for p in pt.perturbations(seed(), tol, n_random=5, seed=3, d=D):
            assert pd.check_candidate(D, p.vec) == [], p.name
            b = D.raw["bounds"]
            for r in D.control:
                assert b["chord_over_R"][0] <= p.vec["chord_R@%g" % r] <= b["chord_over_R"][1]
                assert b["twist_deg"][0] <= p.vec["twist_deg@%g" % r] <= b["twist_deg"][1]
    assert any(p.clamped for p in pt.perturbations(seed(), big, n_random=0, d=D))


def test_corner_directions():
    v = seed()
    ps = {p.name: p for p in pt.perturbations(v, TOL, n_random=0, d=D)}
    assert ps["chord+"].vec["chord_R@0.45"] == pytest.approx(v["chord_R@0.45"] * 1.02)
    assert ps["twist-"].vec["twist_deg@0.45"] == pytest.approx(v["twist_deg@0.45"] - 1.0)
    assert ps["thickness+"].thickness_scale == pytest.approx(1.05) and ps["thickness-"].thickness_scale == pytest.approx(0.95)
    assert ps["trailing_edge_min"].te_min_mm == TOL.te_min_mm and ps["trailing_edge_min"].vec == v


def test_nominal_equals_evaluate_candidate_and_placeholder_label_present():
    r = pt.evaluate_robust(D, seed(), "L12", TOL, n_random=0)
    ref = pd.evaluate_candidate(D, seed(), "L12")
    assert r["nominal"] == ref
    assert r["metrics"]["thrust_N"]["nominal"] == ref["thrust_N"]
    assert r["metrics"]["thrust_per_shaft_W"]["nominal"] == ref["objectives"]["thrust_per_shaft_W"]
    assert r["tolerance_source"] == LABEL and "placeholder" in r["note"]
    assert pt.Tolerance().source == LABEL
    assert r["n_cases"] == 9 and r["n_failed"] == 0 and r["complete"] and len(r["cases"]) == 9


def test_worst_not_above_nominal_not_above_best_and_spread_consistent():
    r = pt.evaluate_robust(D, seed(), "L12", TOL, n_random=2, seed=5)
    for m in ("thrust_N", "thrust_per_shaft_W"):
        s = r["metrics"][m]
        assert s["worst"] <= s["nominal"] <= s["best"]
        vals = [c[m] for c in r["cases"]] + [s["nominal"]]
        assert s["worst"] == min(vals) and s["best"] == max(vals) and s["spread"] == pytest.approx(max(vals) - min(vals))
        assert s["worst_case"] in [c["name"] for c in r["cases"]] + ["nominal"]
    p = r["metrics"]["shaft_power_W"]
    assert p["worst"] >= p["nominal"] >= p["best"]                                  # power: higher is worse
    assert r["metrics"]["thrust_per_shaft_W"]["spread"] > 0                          # the perturbations do move the result


def test_robust_evaluation_is_deterministic_for_a_fixed_seed():
    a = pt.evaluate_robust(D, seed(), "L20", TOL, n_random=2, seed=11)
    b = pt.evaluate_robust(D, seed(), "L20", TOL, n_random=2, seed=11)
    assert a == b


def test_thickness_hook_is_restored_after_the_run():
    orig = pd.candidate_to_propeller2
    pt.evaluate_robust(D, seed(), "L12", TOL, n_random=0)
    assert pd.candidate_to_propeller2 is orig


def test_a_failing_perturbation_is_reported_not_dropped():
    real = pd.evaluate_candidate
    calls = {"n": 0}

    def flaky(d, vec, op, **kw):
        calls["n"] += 1
        if calls["n"] == 3:                                                          # nominal is call 1; this is the second perturbation
            raise pd.DesignError("simulated failure")
        return real(d, vec, op, **kw)

    pd.evaluate_candidate = flaky
    try:
        r = pt.evaluate_robust(D, seed(), "L12", TOL, n_random=0)
    finally:
        pd.evaluate_candidate = real
    assert r["n_failed"] == 1 and r["complete"] is False and r["n_cases"] == 9 and len(r["cases"]) == 8
    assert r["failed"][0]["name"] == "chord-" and "simulated failure" in r["failed"][0]["error"]


def test_invalid_nominal_raises_and_bad_tolerance_is_refused():
    v = seed()
    v["chord_R@0.45"] = 5.0
    with pytest.raises(pd.DesignError):
        pt.evaluate_robust(D, v, "L12", TOL, n_random=0)
    with pytest.raises(pd.DesignError):
        pt.Tolerance(chord_frac=-0.1)
