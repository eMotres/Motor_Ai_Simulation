# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""ISO 21771 gear geometry, contact ratios, undercut, interference, and rack limit tests."""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.gears import GearSpec, analyze_pair, compute_gear_dimensions


def test_design_note_hand_case_contact_ratio_and_undercut():
    # Source: docs/gear_module/DESIGN.md §4 test 2 & ASTRA_DESIGN_REVIEW.md Q3
    # m = 1 mm, z = 20 / 40, alpha_n = 20 deg, beta = 0, x1 = x2 = 0, a = 30 mm
    g1 = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, x=0.0, alpha_n_deg=20.0)
    g2 = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, x=0.0, alpha_n_deg=20.0)
    res = analyze_pair(g1, g2)

    assert res.a_w_mm == pytest.approx(30.0, abs=1e-9)
    assert res.alpha_wt_deg == pytest.approx(20.0, abs=1e-9)
    assert res.epsilon_alpha == pytest.approx(1.635, abs=1e-3)
    assert res.epsilon_alpha == pytest.approx(1.635185964, abs=1e-7)
    assert res.epsilon_beta == pytest.approx(0.0, abs=1e-12)
    assert res.gear1.z_min_undercut == pytest.approx(17.09726434, abs=1e-6)
    assert not res.gear1.has_undercut
    assert res.valid_mesh
    assert res.state == "valid"

    d = res.to_dict()
    assert d["method"] == "ISO21771:2007"
    assert isinstance(d["flags"], list)


def test_undercut_limit_on_small_pinion_and_profile_shift_recovery():
    # Source: ASTRA_DESIGN_REVIEW.md Q3: z=16, 15 undercut at x=0; x_min = 1 - z*sin^2(20)/2
    alpha = math.radians(20.0)
    x_min_16 = 1.0 - 16.0 * (math.sin(alpha) ** 2) / 2.0
    x_min_15 = 1.0 - 15.0 * (math.sin(alpha) ** 2) / 2.0

    p16_zero = compute_gear_dimensions(GearSpec(z=16, m_n_mm=1.0, b_mm=8.0, x=0.0))
    assert p16_zero.has_undercut
    assert "undercut" in p16_zero.flags
    assert p16_zero.x_min_undercut == pytest.approx(x_min_16, abs=1e-7)

    p16_shifted = compute_gear_dimensions(GearSpec(z=16, m_n_mm=1.0, b_mm=8.0, x=x_min_16 + 0.01))
    assert not p16_shifted.has_undercut

    p15_zero = compute_gear_dimensions(GearSpec(z=15, m_n_mm=1.0, b_mm=8.0, x=0.0))
    assert p15_zero.x_min_undercut == pytest.approx(x_min_15, abs=1e-7)
    assert p15_zero.has_undercut


def test_profile_shift_and_centre_distance_round_trip_external_and_helical():
    # Source: ISO 21771 cl. 4.3 transverse/working pressure angle & centre distance solver
    # External parallel helical pair requires opposite hands (right + left)
    g1 = GearSpec(z=19, m_n_mm=1.5, b_mm=12.0, x=0.35, beta_deg=15.0, hand="right")
    g2 = GearSpec(z=41, m_n_mm=1.5, b_mm=12.0, x=0.15, beta_deg=15.0, hand="left")
    fwd = analyze_pair(g1, g2)
    assert fwd.valid_mesh
    assert fwd.a_w_mm > fwd.a_d_mm
    assert fwd.alpha_wt_deg > fwd.gear1.alpha_t_deg > 20.0
    assert fwd.epsilon_beta == pytest.approx(
        12.0 * math.sin(math.radians(15.0)) / (math.pi * 1.5), abs=1e-9
    )
    assert fwd.epsilon_gamma == pytest.approx(fwd.epsilon_alpha + fwd.epsilon_beta, abs=1e-12)

    # Inverse solve from target centre_distance_mm
    g1_blank = GearSpec(z=19, m_n_mm=1.5, b_mm=12.0, x=0.0, beta_deg=15.0, hand="right")
    g2_blank = GearSpec(z=41, m_n_mm=1.5, b_mm=12.0, x=0.0, beta_deg=15.0, hand="left")
    inv_res = analyze_pair(g1_blank, g2_blank, centre_distance_mm=fwd.a_w_mm, solve_shift_mode="equal")
    assert inv_res.valid_mesh
    assert inv_res.a_w_mm == pytest.approx(fwd.a_w_mm, abs=1e-9)
    assert (inv_res.gear1.x + inv_res.gear2.x) == pytest.approx(g1.x + g2.x, abs=1e-8)


def test_internal_pair_interference_and_rack_limit_property():
    # Property test: internal pair with z2 -> large approaches the closed-form pinion-rack contact ratio!
    # Source: ISO 21771 cl. 4.4 / Annex A
    alpha = math.radians(20.0)
    m = 1.0
    z1 = 20
    g1 = GearSpec(z=z1, m_n_mm=m, b_mm=10.0, x=0.0)

    # Pinion-to-rack theoretical contact ratio:
    # g_alpha_rack = sqrt(ra1^2 - rb1^2) - r1*sin(alpha) + (ha*m)/sin(alpha)
    r1 = 0.5 * m * z1
    rb1 = r1 * math.cos(alpha)
    ra1 = r1 + m
    g_rack = math.sqrt(ra1**2 - rb1**2) - r1 * math.sin(alpha) + m / math.sin(alpha)
    eps_rack = g_rack / (math.pi * m * math.cos(alpha))

    prev_err = 1e9
    for z2 in (80, 200, 1000, 10000):
        g2 = GearSpec(z=z2, m_n_mm=m, b_mm=10.0, x=0.0, is_internal=True)
        res = analyze_pair(g1, g2)
        assert res.is_internal_mesh
        assert res.valid_mesh
        err = abs(res.epsilon_alpha - eps_rack)
        assert err < prev_err
        prev_err = err
    assert prev_err < 1e-3

    # Base-circle interference when internal ring tooth count is too small without negative shift
    g_small_ring = GearSpec(z=30, m_n_mm=1.0, b_mm=10.0, x=0.0, is_internal=True)
    bad = analyze_pair(g1, g_small_ring)
    assert bad.involute_interference
    assert not bad.valid_mesh
    assert bad.state == "failed"


def test_external_pair_involute_interference_and_pointed_tip():
    # Source: ISO 21771 cl. 5.3 / 5.5
    # Very small pinion z=8 at x=0 against z=60 suffers base-circle interference
    g1 = GearSpec(z=8, m_n_mm=1.0, b_mm=10.0, x=0.0)
    g2 = GearSpec(z=60, m_n_mm=1.0, b_mm=10.0, x=0.0)
    res = analyze_pair(g1, g2)
    assert res.involute_interference
    assert not res.valid_mesh

    # Excessive positive shift causes pointed tip (s_an <= 0)
    g_pointed = compute_gear_dimensions(GearSpec(z=12, m_n_mm=1.0, b_mm=10.0, x=1.5))
    assert g_pointed.pointed_tip
    assert "pointed_tip" in g_pointed.flags

    # Internal pair tip interference check (ISO 21771 Annex A.2) on tiny tooth difference
    g_p_close = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, ha_star=0.45, hf_star=0.6)
    g_r_close = GearSpec(z=22, m_n_mm=1.0, b_mm=10.0, is_internal=True, ha_star=0.45, hf_star=0.6)
    res_close = analyze_pair(g_p_close, g_r_close)
    assert res_close.internal_tip_margin_deg is not None
    assert res_close.tip_interference or res_close.involute_interference or (res_close.epsilon_alpha < 1.0)
    assert not res_close.valid_mesh


def test_review_helical_hand_compatibility_and_auto_shift_preserve():
    # Source: ASTRA_CODE_REVIEW.md Blocker 3, geometry_loads.json, outline.json
    # External mate 20/40, m_n=1, beta=20 deg, both right-hand MUST fail (opposite hands required).
    g1 = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, beta_deg=20.0, hand="right")
    g2_same = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, beta_deg=20.0, hand="right")
    bad_ext = analyze_pair(g1, g2_same)
    assert bad_ext.valid_mesh is False
    assert bad_ext.state == "failed"
    assert "incompatible_helical_hands_external_require_opposite" in bad_ext.flags

    g2_opp = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, beta_deg=20.0, hand="left")
    ok_ext = analyze_pair(g1, g2_opp)
    assert ok_ext.valid_mesh is True
    assert ok_ext.state == "valid"

    # Internal mate 20/80, beta=20 deg requires SAME hand.
    r_opp = GearSpec(z=80, m_n_mm=1.0, b_mm=10.0, beta_deg=20.0, hand="left", is_internal=True)
    bad_int = analyze_pair(g1, r_opp)
    assert bad_int.valid_mesh is False
    assert bad_int.state == "failed"
    assert "incompatible_helical_hands_internal_require_same" in bad_int.flags

    r_same = GearSpec(z=80, m_n_mm=1.0, b_mm=10.0, beta_deg=20.0, hand="right", is_internal=True)
    ok_int = analyze_pair(g1, r_same)
    assert ok_int.valid_mesh is True

    # Auto centre-distance solve preserves existing matching x=(0.35, 0.15) instead of resetting to (0.25, 0.25)
    g1_sh = GearSpec(z=19, m_n_mm=1.5, b_mm=12.0, x=0.35)
    g2_sh = GearSpec(z=41, m_n_mm=1.5, b_mm=12.0, x=0.15)
    ref = analyze_pair(g1_sh, g2_sh)
    preserved = analyze_pair(g1_sh, g2_sh, centre_distance_mm=ref.a_w_mm)
    assert preserved.gear1.x == pytest.approx(0.35, abs=1e-12)
    assert preserved.gear2.x == pytest.approx(0.15, abs=1e-12)
    assert preserved.gear1.s_t_mm == pytest.approx(ref.gear1.s_t_mm, abs=1e-12)
