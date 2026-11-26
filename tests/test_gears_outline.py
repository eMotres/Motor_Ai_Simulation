# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""3-D tooth outlines, pitch-circle thickness, helical lead, meshing phase,
and zero-interpenetration tests (Addition 1 items 7-9).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.gears import (
    GearSpec,
    PairStageSpec,
    PlanetaryStageSpec,
    analyze_pair,
    build_chain_assembly,
    build_pair_assembly,
    build_planetary_assembly,
    generate_gear_outline,
    mesh_signed_distance_mm,
)


def _measure_tooth_arc_thickness_at_pitch(outline) -> float:
    """Intersect tooth 0 flanks near +X with reference radius r = d_mm / 2."""
    pts = np.asarray(outline.outline_mm, dtype=float)
    r_ref = 0.5 * outline.d_mm
    # Keep points belonging to tooth 0 (angle in (-pi/z, +pi/z))
    ang = np.arctan2(pts[:, 1], pts[:, 0])
    rad = np.hypot(pts[:, 0], pts[:, 1])
    half_pitch = math.pi / outline.z
    mask = np.abs(ang) < 0.9 * half_pitch

    # Find crossings of radius r_ref on lower (y < 0) and upper (y > 0) flanks
    def _crossing_angle(sub_pts: np.ndarray) -> float:
        r_s = np.hypot(sub_pts[:, 0], sub_pts[:, 1])
        for i in range(len(sub_pts) - 1):
            if (r_s[i] - r_ref) * (r_s[i + 1] - r_ref) <= 0.0 and abs(r_s[i + 1] - r_s[i]) > 1e-12:
                t = (r_ref - r_s[i]) / (r_s[i + 1] - r_s[i])
                x = sub_pts[i, 0] + t * (sub_pts[i + 1, 0] - sub_pts[i, 0])
                y = sub_pts[i, 1] + t * (sub_pts[i + 1, 1] - sub_pts[i, 1])
                return math.atan2(y, x)
        raise AssertionError("No reference radius crossing found")

    tooth_pts = pts[mask]
    lower = tooth_pts[tooth_pts[:, 1] <= 0.0]
    upper = tooth_pts[tooth_pts[:, 1] >= 0.0]
    a_lo = _crossing_angle(lower)
    a_hi = _crossing_angle(upper)
    return r_ref * abs(a_hi - a_lo)


def test_outlines_close_and_pitch_thickness_equals_theory():
    # Source: TASK_NEON_stage1.md item 9 & ISO 21771 circular tooth thickness theory
    cases = [
        GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, x=0.0),
        GearSpec(z=20, m_n_mm=1.5, b_mm=12.0, x=0.35, tooth_thickness_allowance_mm=0.04),
        GearSpec(z=24, m_n_mm=1.2, b_mm=14.0, x=-0.1, beta_deg=15.0, hand="left"),
        GearSpec(z=52, m_n_mm=1.0, b_mm=10.0, x=0.0, is_internal=True),
        GearSpec(z=48, m_n_mm=1.0, b_mm=10.0, x=-0.25, is_internal=True, beta_deg=12.0),
    ]
    for g in cases:
        out = generate_gear_outline(g, points_per_flank=24)
        # Closed polyline
        assert out.outline_mm[0] == out.outline_mm[-1]
        assert len(out.outline_mm) > 20 * g.z
        # Theoretical transverse tooth thickness at reference circle d
        an = math.radians(g.alpha_n_deg)
        cos_b = math.cos(math.radians(abs(g.beta_deg)))
        s_t_theory = (g.m_n_mm * (0.5 * math.pi + 2.0 * g.x * math.tan(an)) - g.tooth_thickness_allowance_mm) / cos_b
        assert out.s_t_pitch_mm == pytest.approx(s_t_theory, abs=1e-12)
        measured = _measure_tooth_arc_thickness_at_pitch(out)
        assert measured == pytest.approx(s_t_theory, abs=1e-4)

        if abs(g.beta_deg) > 1e-9:
            assert out.helix_lead_mm is not None
            sign = 1.0 if g.effective_hand == "right" else -1.0
            expected_lead = sign * math.pi * out.d_mm / math.tan(math.radians(abs(g.beta_deg)))
            assert out.helix_lead_mm == pytest.approx(expected_lead, rel=1e-9)
            assert out.twist_across_face_deg == pytest.approx(360.0 * g.b_mm / expected_lead, rel=1e-9)
        else:
            assert out.helix_lead_mm is None
            assert out.twist_across_face_deg == 0.0


def test_external_and_internal_pair_meshing_no_interpenetration():
    # Source: TASK_NEON_stage1.md item 9
    # Test 1: zero-backlash shifted external pair at 0, fractional pitch, and 1 full pitch
    g1 = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, x=0.3)
    g2 = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, x=0.2)
    asm_ext = build_pair_assembly(g1, g2, points_per_flank=48)
    m1, m2 = asm_ext.members
    pitch_deg = 360.0 / g1.z
    for th in (0.0, 0.25 * pitch_deg, 0.5 * pitch_deg, pitch_deg):
        pose = asm_ext.pose_at(th)
        sd = mesh_signed_distance_mm(
            m1.outline, pose[m1.name].rotation_deg, pose[m1.name].center_mm[:2],
            m2.outline, pose[m2.name].rotation_deg, pose[m2.name].center_mm[:2],
        )
        assert sd >= -1e-6

    # Test 2: with backlash allowance, signed distance is strictly >= 0
    g1_bl = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, x=0.0, tooth_thickness_allowance_mm=0.03)
    g2_bl = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, x=0.0, tooth_thickness_allowance_mm=0.03)
    asm_bl = build_pair_assembly(g1_bl, g2_bl, points_per_flank=32)
    b1, b2 = asm_bl.members
    for th in (0.0, pitch_deg):
        pose = asm_bl.pose_at(th)
        sd = mesh_signed_distance_mm(
            b1.outline, pose[b1.name].rotation_deg, pose[b1.name].center_mm[:2],
            b2.outline, pose[b2.name].rotation_deg, pose[b2.name].center_mm[:2],
        )
        assert sd > 0.0

    # Test 3: internal pair meshing (pinion 16 inside ring 52) at reference and +1 tooth pitch
    # Note: concave internal involute chord sagitta scales as O(1/N^2); N=200 keeps discretization < 1e-6 mm
    pin = GearSpec(z=16, m_n_mm=1.0, b_mm=10.0)
    ring = GearSpec(z=52, m_n_mm=1.0, b_mm=10.0, is_internal=True)
    asm_int = build_pair_assembly(pin, ring, points_per_flank=240)
    ip1, ip2 = asm_int.members
    pitch_int = 360.0 / pin.z
    for th in (0.0, 0.3 * pitch_int, pitch_int):
        pose = asm_int.pose_at(th)
        sd = mesh_signed_distance_mm(
            ip1.outline, pose[ip1.name].rotation_deg, pose[ip1.name].center_mm[:2],
            ip2.outline, pose[ip2.name].rotation_deg, pose[ip2.name].center_mm[:2],
        )
        assert sd >= -1e-6


def test_planetary_and_chain_assembly_animation_no_interpenetration():
    # Source: TASK_NEON_stage1.md items 8-9
    # Verify both even-planet (20/16/52, x=0, N=3) and profile-shifted odd-planet
    # (19/15/49, x=(0.2, 0.2, -0.6), N=4, eliminating z=15 undercut) planetary assemblies!
    configs = (
        (20, 16, 52, 3, 0.0, 0.0, 0.0),
        (19, 15, 49, 4, 0.2, 0.2, -0.6),
    )
    for z_s, z_p, z_r, n_p, xs, xp, xr in configs:
        s = GearSpec(z=z_s, m_n_mm=1.0, b_mm=10.0, x=xs)
        p = GearSpec(z=z_p, m_n_mm=1.0, b_mm=10.0, x=xp)
        r = GearSpec(z=z_r, m_n_mm=1.0, b_mm=10.0, x=xr, is_internal=True)
        asm = build_planetary_assembly(sun=s, planet=p, ring=r, n_planets=n_p, points_per_flank=240)
        sun_m = next(m for m in asm.members if m.role == "sun")
        ring_m = next(m for m in asm.members if m.role == "ring")
        planets = [m for m in asm.members if m.role == "planet"]
        assert len(planets) == n_p

        pitch_sun = 360.0 / z_s
        for th in (0.0, 0.4 * pitch_sun, pitch_sun):
            pose = asm.pose_at(th)
            ps = pose[sun_m.name]
            pr = pose[ring_m.name]
            for pl in planets:
                pp = pose[pl.name]
                sd_sp = mesh_signed_distance_mm(
                    sun_m.outline, ps.rotation_deg, ps.center_mm[:2],
                    pl.outline, pp.rotation_deg, pp.center_mm[:2],
                )
                sd_pr = mesh_signed_distance_mm(
                    pl.outline, pp.rotation_deg, pp.center_mm[:2],
                    ring_m.outline, pr.rotation_deg, pr.center_mm[:2],
                )
                assert sd_sp >= -1e-6
                assert sd_pr >= -1e-6

    # Chain assembly of two planetary stages stacked axially
    st1 = PlanetaryStageSpec(
        name="s1",
        sun=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0),
        planet=GearSpec(z=16, m_n_mm=1.0, b_mm=10.0),
        ring=GearSpec(z=52, m_n_mm=1.0, b_mm=10.0, is_internal=True),
        n_planets=3,
    )
    st2 = PlanetaryStageSpec(
        name="s2",
        sun=GearSpec(z=19, m_n_mm=1.0, b_mm=12.0),
        planet=GearSpec(z=15, m_n_mm=1.0, b_mm=12.0),
        ring=GearSpec(z=49, m_n_mm=1.0, b_mm=12.0, is_internal=True),
        n_planets=4,
    )
    chain_asm = build_chain_assembly([st1, st2], stage_gap_mm=4.0, points_per_flank=32)
    assert chain_asm.overall_ratio == pytest.approx(12.884210526, abs=1e-7)
    pose_chain = chain_asm.pose_at(18.0)
    # Stage 1 Z = 0, Stage 2 Z = 10 + 4 = 14 mm
    z_coords = {round(pm.center_mm[2], 3) for pm in pose_chain.values()}
    assert z_coords == {0.0, 14.0}

    # Helical stepped-planet compound assembly: external sun(right)-planet1(left) opposite hands;
    # internal planet2(left, x=0.35)-ring(left, x=-0.35) same hand & balanced shift keeps exact coaxiality!
    sc = GearSpec(z=18, m_n_mm=1.0, b_mm=10.0, beta_deg=12.0, hand="right")
    p1c = GearSpec(z=24, m_n_mm=1.0, b_mm=10.0, beta_deg=12.0, hand="left")
    p2c = GearSpec(z=16, m_n_mm=1.0, b_mm=10.0, beta_deg=12.0, hand="left", x=0.35)
    rc = GearSpec(z=58, m_n_mm=1.0, b_mm=10.0, beta_deg=12.0, hand="left", is_internal=True, x=-0.35)
    comp_asm = build_planetary_assembly(sun=sc, planet=p1c, planet2=p2c, ring=rc, n_planets=2, points_per_flank=24)
    assert comp_asm.state == "valid"
    assert any(m.role == "planet2" for m in comp_asm.members)
    assert comp_asm.members[0].outline.helix_lead_mm is not None
    assert isinstance(comp_asm.to_dict(), dict)


def test_review_crossing_polygons_undercut_trim_pair_x_and_compound_axial():
    # Source: root_outline.json, outline.json, compound_assembly.json
    from motor_ai_sim.gears.common import inv
    from motor_ai_sim.gears.outline import GearOutline

    # 1. Crossing rectangles (+ shape, overlap area = 1 mm^2) MUST return negative clearance
    rect_h = GearOutline(
        z=4, is_internal=False, m_n_mm=1.0, m_t_mm=1.0, d_mm=1.0, d_b_mm=1.0, d_a_mm=2.0, d_f_mm=1.0,
        s_t_pitch_mm=1.0, b_mm=1.0, beta_deg=0.0, hand="none", helix_lead_mm=None, twist_across_face_deg=0.0,
        outer_diameter_mm=None, outline_mm=[(-2.0, -0.5), (2.0, -0.5), (2.0, 0.5), (-2.0, 0.5), (-2.0, -0.5)],
        points_per_flank=4, points_per_fillet=3, root_fillet_model="test",
    )
    rect_v = GearOutline(
        z=4, is_internal=False, m_n_mm=1.0, m_t_mm=1.0, d_mm=1.0, d_b_mm=1.0, d_a_mm=2.0, d_f_mm=1.0,
        s_t_pitch_mm=1.0, b_mm=1.0, beta_deg=0.0, hand="none", helix_lead_mm=None, twist_across_face_deg=0.0,
        outer_diameter_mm=None, outline_mm=[(-0.5, -2.0), (0.5, -2.0), (0.5, 2.0), (-0.5, 2.0), (-0.5, -2.0)],
        points_per_flank=4, points_per_fillet=3, root_fillet_model="test",
    )
    assert mesh_signed_distance_mm(rect_h, 0.0, (0.0, 0.0), rect_v, 0.0, (0.0, 0.0)) < 0.0

    # 2. root_outline.json: z=8 undercut fillet endpoint matches involute at equal radius (< 1e-10 mm)
    g8 = GearSpec(z=8, m_n_mm=1.0, b_mm=10.0)
    o8 = generate_gear_outline(g8, points_per_flank=24, points_per_fillet=12)
    assert "undercut_trochoid_trimmed_at_involute_intersection" in o8.flags
    an = math.radians(g8.alpha_n_deg)
    rb = 0.5 * o8.d_b_mm
    psi_ref = o8.s_t_pitch_mm / o8.d_mm
    px, py = o8.outline_mm[3 + o8.points_per_fillet]  # fillet end point
    r_junc = math.hypot(px, py)
    assert r_junc >= rb
    af_junc = math.atan2(-py, px)
    psi_inv_junc = psi_ref + inv(an) - inv(math.acos(min(1.0, rb / r_junc)))
    assert abs(af_junc - psi_inv_junc) * r_junc < 1e-10

    # 3. outline.json profile_shift & compound_assembly.json axial alignment:
    g1_sh = GearSpec(z=19, m_n_mm=1.5, b_mm=12.0, x=0.35)
    g2_sh = GearSpec(z=41, m_n_mm=1.5, b_mm=12.0, x=0.15)
    geom_sh = analyze_pair(g1_sh, g2_sh)
    desc_pair = build_pair_assembly(g1_sh, g2_sh, centre_distance_mm=geom_sh.a_w_mm)
    assert desc_pair.members[0].outline.s_t_pitch_mm == pytest.approx(geom_sh.gear1.s_t_mm, abs=1e-9)

    comp_asm = build_planetary_assembly(
        sun=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0),
        planet=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0),
        planet2=GearSpec(z=30, m_n_mm=1.0, b_mm=10.0),
        ring=GearSpec(z=90, m_n_mm=1.0, b_mm=10.0, is_internal=True),
        n_planets=3,
    )
    z_ring = next(m for m in comp_asm.members if m.role == "ring").center_mm[2]
    z_p2 = next(m for m in comp_asm.members if m.role == "planet2").center_mm[2]
    assert z_ring == pytest.approx(10.0, abs=1e-9)
    assert z_ring == pytest.approx(z_p2, abs=1e-9)


def test_r2_b7_equal_teeth_compound_outline_and_chain_xy_alignment():
    # Source: ASTRA_CODE_REVIEW_r2.md B7 / follow-ups (r2_final.json & r2_extended.json)
    # 1. Equal-teeth different-module compound stage (z=48/24/24/72, m=1.0/1.5 => a_w_sp=36 mm, a_w_pr=36 mm):
    # MUST be treated as compound_planetary, emit planet2, and place ring in planet2's axial plane z=10 mm.
    st_eq = PlanetaryStageSpec(
        name="eq_mod",
        sun=GearSpec(z=48, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.015),
        planet=GearSpec(z=24, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.015),
        planet2=GearSpec(z=24, m_n_mm=1.5, b_mm=10.0, tooth_thickness_allowance_mm=0.015),
        ring=GearSpec(z=72, m_n_mm=1.5, b_mm=10.0, is_internal=True, tooth_thickness_allowance_mm=0.015),
        n_planets=2,
    )
    assert st_eq.kind == "compound_planetary"
    asm_eq = build_planetary_assembly(
        sun=st_eq.sun, planet=st_eq.planet, planet2=st_eq.planet2, ring=st_eq.ring, n_planets=2, points_per_flank=20,
    )
    assert any(m.role == "planet2" for m in asm_eq.members)
    assert next(m for m in asm_eq.members if m.role == "ring").center_mm[2] == pytest.approx(10.0, abs=1e-9)
    assert asm_eq.state == "valid"
    pose_eq = asm_eq.pose_at(0.0)
    sun_p = next(m for m in asm_eq.members if m.role == "sun")
    p1_p = next(m for m in asm_eq.members if m.role == "planet")
    p2_p = next(m for m in asm_eq.members if m.role == "planet2")
    ring_p = next(m for m in asm_eq.members if m.role == "ring")
    assert mesh_signed_distance_mm(
        sun_p.outline, pose_eq[sun_p.name].rotation_deg, pose_eq[sun_p.name].center_mm[:2],
        p1_p.outline, pose_eq[p1_p.name].rotation_deg, pose_eq[p1_p.name].center_mm[:2],
    ) > 0.0
    assert mesh_signed_distance_mm(
        p2_p.outline, pose_eq[p2_p.name].rotation_deg, pose_eq[p2_p.name].center_mm[:2],
        ring_p.outline, pose_eq[ring_p.name].rotation_deg, pose_eq[ring_p.name].center_mm[:2],
    ) > 0.0

    # 2. Multi-stage chain alignment: external PairStageSpec(20/40, m=1.5 => a_w=45 mm) offsets downstream
    # planetary stage axis from (0, 0) to (45, 0) for both static placement and animated orbital pose.
    st_pair = PairStageSpec(
        name="p", gear1=GearSpec(z=20, m_n_mm=1.5, b_mm=10.0), gear2=GearSpec(z=40, m_n_mm=1.5, b_mm=10.0),
    )
    st_plan = PlanetaryStageSpec(
        name="pl",
        sun=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0),
        planet=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0),
        ring=GearSpec(z=80, m_n_mm=1.0, b_mm=10.0, is_internal=True),
        n_planets=3,
    )
    chain_asm = build_chain_assembly([st_pair, st_plan], stage_gap_mm=5.0, points_per_flank=12, points_per_fillet=6)
    g2_xy = next(m for m in chain_asm.members if m.role == "gear2").center_mm[:2]
    sun_xy = next(m for m in chain_asm.members if m.role == "sun").center_mm[:2]
    ring_xy = next(m for m in chain_asm.members if m.role == "ring").center_mm[:2]
    p_carrier_xy = next(m for m in chain_asm.members if m.role == "planet").carrier_center_mm[:2]
    assert g2_xy == pytest.approx((45.0, 0.0), abs=1e-9)
    assert sun_xy == pytest.approx(g2_xy, abs=1e-12)
    assert ring_xy == pytest.approx(g2_xy, abs=1e-12)
    assert p_carrier_xy == pytest.approx(g2_xy, abs=1e-12)
    pose_90 = chain_asm.pose_at(90.0)
    p0_name = next(m.name for m in chain_asm.members if m.role == "planet")
    orb_r = next(m.orbital_radius_mm for m in chain_asm.members if m.role == "planet")
    assert math.hypot(pose_90[p0_name].center_mm[0] - 45.0, pose_90[p0_name].center_mm[1]) == pytest.approx(orb_r, abs=1e-9)
