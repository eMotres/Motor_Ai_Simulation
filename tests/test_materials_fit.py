"""Tests for the Maxwell-style Bertotti fit from measured core-loss curves.

Fast (no FEM): exercises materials.fit_bertotti_from_curves /
effective_bertotti on the real library data.

Run:  python -m pytest tests/test_materials_fit.py -q
"""
import math

import pytest

from motor_ai_sim.materials import (
    SteelMaterial, fit_bertotti_from_curves, effective_bertotti, get_steel,
)


def test_fit_20sw1200_quality():
    """The fit over the REAL measured curves must stay within engineering
    accuracy (mean relative error < 10 %, p90 < 25 %)."""
    s = get_steel("20SW1200")
    fit = fit_bertotti_from_curves(s)
    assert fit is not None, "20SW1200 has measured curves — fit must succeed"
    kh, kc, ke, report = fit
    assert kh > 0 and kc > 0          # hysteresis + classical must be present
    assert report["n_points"] > 100   # all 10 frequency curves used
    assert report["rel_err_mean"] < 0.10
    assert report["rel_err_p90"] < 0.25


def test_fit_matches_measured_at_operating_point():
    """Fitted Bertotti at (466.7 Hz, 1.5 T) must be within 20 % of the
    measured-curve interpolation (the truth)."""
    s = get_steel("20SW1200")
    kh, kc, ke = effective_bertotti(s)
    f0, b0 = 466.67, 1.5
    p_fit = (kh * f0 * b0**2 + kc * f0**2 * b0**2
             + ke * f0**1.5 * b0**1.5) / s.density          # W/kg
    # measured: log-log interpolation between the 400 Hz and 500 Hz curves
    import numpy as np
    def p_of(curve):
        xs = [b for b, _ in curve]; ys = [p for _, p in curve]
        return float(np.interp(b0, xs, ys))
    p400 = p_of(s.core_loss_curves["400Hz"])
    p500 = p_of(s.core_loss_curves["500Hz"])
    p_meas = p400 * (p500 / p400) ** (math.log(f0 / 400) / math.log(500 / 400))
    assert abs(p_fit - p_meas) / p_meas < 0.20


def test_effective_bertotti_fallback_to_yaml():
    """A steel WITHOUT measured curves must fall back to its YAML coefficients."""
    s = SteelMaterial(name="dummy", core_loss_kh=11.0, core_loss_kc=2.0,
                      core_loss_ke=0.5, core_loss_curves={})
    assert effective_bertotti(s) == (11.0, 2.0, 0.5)


def test_effective_bertotti_none():
    assert effective_bertotti(None) == (0.0, 0.0, 0.0)


def test_fit_is_cached():
    s = get_steel("20SW1200")
    a = fit_bertotti_from_curves(s)
    b = fit_bertotti_from_curves(s)
    assert a is b                      # same tuple object → cache hit
