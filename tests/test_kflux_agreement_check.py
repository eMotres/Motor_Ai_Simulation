"""The Stage A 3D-vs-2D agreement check: a constant end deficit, not a mid-plane tolerance.

A real end effect costs a length per machine: D(L) = L (1 - k_flux(L)) is nearly constant in L.  A 3D-vs-2D model
offset eps adds eps x L.  run_stage_a fits D = D0 + eps L over the rows at >= 1.0 x the reference stack: PASS when
|eps| <= 1 % and D0 > 0, FAIL otherwise, NOT CHECKED with a single such row (k_flux stays usable).  Numbers: own-ratio
k_flux of a 28-pole 80 mm machine (12 / 24 / 48 mm) and of a 12s14p 40 mm machine (12 / 20 / 22 mm), derived only.
"""
from __future__ import annotations

import pytest

from motor_ai_sim.simulation.static3d import end_effect as EE
from tests.test_kflux_stack_length import B1_2D, REF_MM, TABLE, stubbed  # noqa: F401  (fixture)


def _curve(pairs):
    return [{"stack_mm": L, "k_flux": k, "ratio_3d_mid_over_2d": 1.0} for L, k in pairs]


def test_measured_machines_pass():
    c40 = EE.end_deficit_check(_curve([(12.0, 0.94091), (20.0, 0.96463), (22.0, 0.96788)]), 12.0)
    assert c40["verdict"] == "PASS" and abs(c40["eps"]) < 1e-3 and c40["D0_mm"] == pytest.approx(0.71, abs=0.01)
    f80 = EE.end_deficit_check(_curve([(12.0, 0.92270), (24.0, 0.96067), (48.0, 0.98006)]), 12.0)
    assert f80["verdict"] == "PASS" and f80["eps"] == pytest.approx(0.0008, abs=3e-4)
    assert f80["rows_mm"] == [12.0, 24.0, 48.0]


def test_a_model_offset_fails():
    # D0 0.93 mm plus a 2 % offset: k = 1 - 0.93 / L - 0.02
    c = EE.end_deficit_check(_curve([(L, 1 - 0.93 / L - 0.02) for L in (12.0, 18.0, 24.0)]), 12.0)
    assert c["verdict"] == "FAIL" and c["eps"] == pytest.approx(0.02, rel=1e-9)
    c = EE.end_deficit_check(_curve([(L, 1 + 0.1 / L) for L in (12.0, 24.0)]), 12.0)   # D0 < 0
    assert c["verdict"] == "FAIL"


def test_short_rows_stay_out_and_one_row_is_not_checked():
    c = EE.end_deficit_check(_curve([(12.0, 0.92270), (8.0, 0.88702), (6.0, 0.85845)]), 12.0)
    assert c["verdict"] == "NOT CHECKED" and c["rows_mm"] == [12.0] and c["eps"] is None
    # rows without their own 3D-mid / 2D ratio (no usable 2D leg) are not fitted
    c = EE.end_deficit_check([{"stack_mm": 12.0, "k_flux": 0.95}, {"stack_mm": 24.0, "k_flux": 0.97}], 12.0)
    assert c["verdict"] == "NOT CHECKED" and c["rows_mm"] == []


def test_run_stage_a_not_checked_keeps_k_flux_usable(stubbed):  # noqa: F811
    pp = EE.run_stage_a(l_factors=(1.0, 8.0 / 12.0, 0.5), do_bracket=False, do_2d=True, verbose=False)
    t = pp["two_d"]
    assert t["verdict"] == "NOT CHECKED" and t["k_flux_usable"] is True and "warning" not in t
    assert t["mid_plane_check"]["within"] is False                      # 0.957: the old rule would have said FAIL
    assert t["mid_plane_check"]["ratio"] == t["ratio_3d_mid_over_2d"]
    b1, ks = TABLE[REF_MM]
    assert pp["k_flux"] == ks * (b1 / B1_2D)                              # numbers unchanged


def _longer(monkeypatch, k_of_L, ratio_of_L):
    """Extra stack lengths for the stub: k_flux(L) and mid/2D(L) given, k_flux_self = k / ratio."""
    base = EE.spill_profile

    def prof(ss, n_theta=180, **kw):
        L = round(ss.stack_mm, 6)
        if L in (18.0, 24.0):
            r = ratio_of_L(L)
            b1, ks = r * B1_2D, k_of_L(L) / r
            return dict(z_m=[0.0], z_over_half=[0.0], B1_T=[b1], phase=[0.0], profile=[1.0],
                        B1_mid_T=b1, k_flux_self=ks, stack_half_m=0.5e-3 * L)
        return base(ss, n_theta=n_theta, **kw)
    monkeypatch.setattr(EE, "spill_profile", prof)


def test_run_stage_a_pass_and_fail_on_longer_rows(stubbed, monkeypatch):  # noqa: F811
    k12 = TABLE[REF_MM][1] * TABLE[REF_MM][0] / B1_2D                    # 0.92270, D = 0.928 mm
    d12 = REF_MM * (1 - k12)
    _longer(monkeypatch, lambda L: 1 - d12 / L, lambda L: {18.0: 0.975, 24.0: 0.9909}[L])
    pp = EE.run_stage_a(l_factors=(1.0, 1.5, 2.0), do_bracket=False, do_2d=True, verbose=False)
    t = pp["two_d"]
    assert t["verdict"] == "PASS" and t["k_flux_usable"] is True
    assert t["agreement_check"]["eps"] == pytest.approx(0.0, abs=1e-9)
    _longer(monkeypatch, lambda L: 1 - (d12 + 0.03 * (L - REF_MM)) / L, lambda L: 0.99)
    pp = EE.run_stage_a(l_factors=(1.0, 1.5, 2.0), do_bracket=False, do_2d=True, verbose=False)
    t = pp["two_d"]
    assert t["verdict"] == "FAIL" and t["k_flux_usable"] is False and "grows with the stack length" in t["warning"]
    assert t["agreement_check"]["eps"] == pytest.approx(0.03, rel=1e-6)


def test_short_stack_single_reference_runs_on_the_80mm_machine():
    """Two real single-reference runs of the 28-pole 80 mm cross-section (own-ratio rows, derived numbers): the old
    mid-plane rule said FAIL on both (mid / 2-D 0.923 at 8 mm, 0.957 at 12 mm) although their k_flux agree to 0.02 %."""
    l8 = EE.end_deficit_check(_curve([(8.0, 0.88685), (12.0, 0.92271), (6.0, 0.85826)]), 8.0)
    assert l8["verdict"] == "PASS" and l8["rows_mm"] == [8.0, 12.0]
    assert l8["eps"] == pytest.approx((12 * (1 - 0.92271) - 8 * (1 - 0.88685)) / 4, rel=1e-9)   # +0.56 %
    l12 = EE.end_deficit_check(_curve([(12.0, 0.92270), (8.0, 0.88702), (6.0, 0.85845)]), 12.0)
    assert l12["verdict"] == "NOT CHECKED"
