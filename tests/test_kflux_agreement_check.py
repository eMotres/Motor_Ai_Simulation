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
    assert l8["verdict"] == "PASS" and l8["rows_mm"] == [8.0, 12.0]       # without the pole pitch: the 8 mm row fits
    assert l8["eps"] == pytest.approx((12 * (1 - 0.92271) - 8 * (1 - 0.88685)) / 4, rel=1e-9)   # +0.56 %
    # with the pole pitch (5.6 mm) the 8 mm row is inside the end-interaction zone (< 2 pole pitches): one row left
    l8z = EE.end_deficit_check(_curve([(8.0, 0.88685), (12.0, 0.92271), (6.0, 0.85826)]), 8.0, pole_pitch_mm=5.6)
    assert l8z["verdict"] == "NOT CHECKED" and l8z["rows_mm"] == [12.0]
    assert {d["stack_mm"] for d in l8z["dropped_rows"]} == {6.0, 8.0}
    l12 = EE.end_deficit_check(_curve([(12.0, 0.92270), (8.0, 0.88702), (6.0, 0.85845)]), 12.0)
    assert l12["verdict"] == "NOT CHECKED"


def test_a_long_stack_machine_passes_at_two_axial_meshes():
    """48-slot 56-pole 400 mm machine, 90 mm reference (own-ratio rows, derived): the axial mesh n_stack 8 (90 / 135 /
    180 mm) and n_stack 12 (90 / 180 mm) give the same eps, -0.38 %: the fit measures the machine, not the mesh."""
    a = EE.end_deficit_check(_curve([(90.0, 0.96942381), (135.0, 0.98104254), (180.0, 0.98661238)]), 90.0)
    b = EE.end_deficit_check(_curve([(90.0, 0.96933232), (180.0, 0.98658446)]), 90.0)
    assert a["verdict"] == b["verdict"] == "PASS"
    assert a["eps"] == pytest.approx(-0.0038, abs=1e-4) and b["eps"] == pytest.approx(a["eps"], abs=1e-4)
    assert a["D0_mm"] == pytest.approx(3.09, abs=0.02)



# --------------------------------------------------------------------------
# fit-quality guards (review of 2026-11-25)
# --------------------------------------------------------------------------

def test_near_equal_lengths_are_not_checked():
    c = EE.end_deficit_check(_curve([(12.0, 0.9227), (13.0, 0.9290)]), 12.0)
    assert c["verdict"] == "NOT CHECKED" and "span" in c["reason"] and c["span_ratio"] == pytest.approx(13 / 12)


def test_two_rows_are_an_exact_fit_with_the_error_amplification_said():
    c = EE.end_deficit_check(_curve([(12.0, 0.92270), (18.0, 1 - 0.928 / 18)]), 12.0)
    assert c["exact_fit"] is True and c["residual_rms_mm"] is None and c["n_rows"] == 2
    assert "5.0 x dk" in c["note"]                                       # (1.0 + 1.5) / 0.5


def test_three_rows_record_the_residual_and_a_bent_curve_is_not_checked():
    ok = EE.end_deficit_check(_curve([(90.0, 0.96942381), (135.0, 0.98104254), (180.0, 0.98661238)]), 90.0)
    assert ok["exact_fit"] is False and ok["residual_rms_mm"] / (sum(ok["D_mm"]) / 3) < 0.01
    bent = EE.end_deficit_check(_curve([(12.0, 1 - 0.9 / 12), (18.0, 1 - 1.3 / 18), (24.0, 1 - 0.9 / 24)]), 12.0)
    assert bent["verdict"] == "NOT CHECKED" and "not linear" in bent["reason"]


def test_rows_without_a_finite_positive_ratio_are_dropped_and_recorded():
    curve = _curve([(12.0, 0.92270), (24.0, 0.96067)])
    curve += [{"stack_mm": 36.0, "k_flux": 0.97, "ratio_3d_mid_over_2d": float("nan")},
              {"stack_mm": 48.0, "k_flux": 0.98, "ratio_3d_mid_over_2d": 0.0},
              {"stack_mm": 60.0, "k_flux": 0.98, "ratio_3d_mid_over_2d": None}]
    c = EE.end_deficit_check(curve, 12.0)
    assert c["rows_mm"] == [12.0, 24.0]
    assert sorted(d["stack_mm"] for d in c["dropped_rows"]) == [36.0, 48.0, 60.0]
    assert all("ratio" in d["reason"] for d in c["dropped_rows"])


def test_a_long_end_loss_is_flagged_not_failed():
    c = EE.end_deficit_check(_curve([(12.0, 1 - 3.0 / 12), (24.0, 1 - 3.0 / 24)]), 12.0, pole_pitch_mm=5.0)
    assert c["verdict"] == "PASS" and "flag" in c and c["D0_over_pole_pitch"] == pytest.approx(0.6)


def test_version_and_keys_of_a_new_passport(stubbed):  # noqa: F811
    pp = EE.run_stage_a(l_factors=(1.0, 0.5), do_bracket=False, do_2d=True, verbose=False)
    t = pp["two_d"]
    assert pp["version"] == EE.PASSPORT_VERSION == "static3d-stageA-3"
    assert "agreement_tolerance" not in t and t["agreement_check"]["tolerance_eps"] == EE.AGREEMENT_EPS_TOL
    assert t["k_flux_status"] == "unverified" and t["agreement_check"]["pole_pitch_mm"] == pytest.approx(
        3.141592653589793 * 24.8 / 14)


def test_coarse_screen_refuses_a_gross_offset_on_an_uncheckable_run(stubbed, monkeypatch):  # noqa: F811
    from types import SimpleNamespace
    from motor_ai_sim.simulation.static3d import ref2d as REF2D
    r2 = SimpleNamespace(B1_T=2.2, harmonics={}, n_tri=1, ndofs=1, wall_s=0.0, outer_r_mm=40.0, relaxation=1.0,
                         picard_sweeps=1, picard_residual=0.0, picard_converged=True, even_over_odd=0.0)
    monkeypatch.setattr(REF2D, "solve_2d_reference_converged", lambda *a, **kw: r2)
    pp = EE.run_stage_a(l_factors=(1.0, 0.5), do_bracket=False, do_2d=True, verbose=False)
    t = pp["two_d"]                                                      # mid / 2-D = 1.68 / 2.2 = 0.76
    assert t["verdict"] == "FAIL" and t["k_flux_usable"] is False and "coarse screen" in t["agreement_check"]["reason"]
    assert "dimensionless" not in t["warning"] and "coarse screen" in t["warning"]


def test_the_fail_warning_calls_eps_dimensionless(stubbed, monkeypatch):  # noqa: F811
    k12 = TABLE[REF_MM][1] * TABLE[REF_MM][0] / B1_2D
    d12 = REF_MM * (1 - k12)
    _longer(monkeypatch, lambda L: 1 - (d12 + 0.03 * (L - REF_MM)) / L, lambda L: 0.99)
    t = EE.run_stage_a(l_factors=(1.0, 1.5, 2.0), do_bracket=False, do_2d=True, verbose=False)["two_d"]
    assert "dimensionless" in t["warning"] and "per unit length" not in t["warning"]


def test_invalid_path_has_no_old_tolerance_key(stubbed, monkeypatch):  # noqa: F811
    from types import SimpleNamespace
    from motor_ai_sim.simulation.static3d import ref2d as REF2D
    r2 = SimpleNamespace(B1_T=0.0, harmonics={}, n_tri=1, ndofs=1, wall_s=0.0, outer_r_mm=40.0, relaxation=1.0,
                         picard_sweeps=1, picard_residual=0.0, picard_converged=True, even_over_odd=0.0)
    monkeypatch.setattr(REF2D, "solve_2d_reference_converged", lambda *a, **kw: r2)
    t = EE.run_stage_a(l_factors=(1.0,), do_bracket=False, do_2d=True, verbose=False)["two_d"]
    assert t["verdict"] == "INVALID" and "agreement_tolerance" not in t and t["k_flux_status"] == "invalid"
