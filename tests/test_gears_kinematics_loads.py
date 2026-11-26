# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Kinematics (Willis), assembly conditions, tooth forces, and K_gamma load-sharing tests."""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.gears import (
    GearSpec,
    analyze_pair,
    chain_kinematics,
    compute_mesh_forces,
    default_k_gamma,
    pair_kinematics,
    planet_bearing_reaction,
    planetary_kinematics,
)


def test_khk_internal_gear_ratios_all_fixed_members():
    # Source: KHK 2025 Internal Gears pp. 208-209 via ASTRA_DESIGN_REVIEW.md Q7
    # sun=40, planet=20, ring=80, N=3
    s = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0)
    p = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0)
    r = GearSpec(z=80, m_n_mm=1.0, b_mm=10.0, is_internal=True)

    k_ring_fixed = planetary_kinematics(sun=s, planet=p, ring=r, n_planets=3, fixed_member="ring", input_member="sun", output_member="carrier", input_speed_rpm=3000.0)
    assert k_ring_fixed.ratio == pytest.approx(3.0, abs=1e-12)
    assert k_ring_fixed.equal_spacing_possible
    assert k_ring_fixed.assembly_ok

    k_sun_fixed = planetary_kinematics(sun=s, planet=p, ring=r, n_planets=3, fixed_member="sun", input_member="ring", output_member="carrier", input_speed_rpm=1500.0)
    assert k_sun_fixed.ratio == pytest.approx(1.5, abs=1e-12)

    k_carrier_fixed = planetary_kinematics(sun=s, planet=p, ring=r, n_planets=3, fixed_member="carrier", input_member="sun", output_member="ring", input_speed_rpm=2000.0)
    assert k_carrier_fixed.ratio == pytest.approx(-2.0, abs=1e-12)
    assert k_carrier_fixed.reverses_direction


def test_design_note_two_stage_ratio_and_assembly_checks():
    # Source: docs/gear_module/DESIGN.md §4 test 3 & ASTRA_DESIGN_REVIEW.md Q3
    # Stage 1: 20/16/52 (N=3, 72/3=24 -> equal spacing ok); Stage 2: 19/15/49 (N=3 fails, N=4 ok)
    s1 = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0)
    p1 = GearSpec(z=16, m_n_mm=1.0, b_mm=10.0)
    r1 = GearSpec(z=52, m_n_mm=1.0, b_mm=10.0, is_internal=True)
    k1 = planetary_kinematics(sun=s1, planet=p1, ring=r1, n_planets=3, input_speed_rpm=3182.4)
    assert k1.ratio == pytest.approx(3.6, abs=1e-9)
    assert k1.equal_spacing_possible
    assert k1.neighbour_clearance_mm == pytest.approx(2 * 18 * 0.866025403784 - 18.0, abs=1e-6)

    s2 = GearSpec(z=19, m_n_mm=1.0, b_mm=10.0)
    p2 = GearSpec(z=15, m_n_mm=1.0, b_mm=10.0)
    r2 = GearSpec(z=49, m_n_mm=1.0, b_mm=10.0, is_internal=True)

    # Mandatory check: 19/49 with N=3 MUST fail equal-spacing check!
    k2_n3 = planetary_kinematics(sun=s2, planet=p2, ring=r2, n_planets=3, input_speed_rpm=k1.output_speed_rpm)
    assert not k2_n3.equal_spacing_possible
    assert "equal_spacing_failed" in k2_n3.flags

    # With N=4: (19+49)/4 = 17 -> equal spacing possible!
    k2_n4 = planetary_kinematics(sun=s2, planet=p2, ring=r2, n_planets=4, input_speed_rpm=k1.output_speed_rpm)
    assert k2_n4.equal_spacing_possible
    assert k2_n4.ratio == pytest.approx(68.0 / 19.0, abs=1e-9)
    assert k2_n4.neighbour_clearance_mm == pytest.approx(2 * 17 * 0.707106781187 - 17.0, abs=1e-6)

    chain = chain_kinematics([k1, k2_n4])
    assert chain.overall_ratio == pytest.approx(12.884210526, abs=1e-7)
    assert chain.overall_ratio == pytest.approx(12.884, abs=1e-3)
    assert chain.output_speed_rpm == pytest.approx(247.0, abs=0.05)


def test_20_16_52_kinematic_and_load_oracle():
    # Source: ASTRA_DESIGN_REVIEW.md Q7 oracle (20/16/52, N=3, m=1 mm, n_s=3600 rpm, T_s=1 N*m)
    s = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0)
    p = GearSpec(z=16, m_n_mm=1.0, b_mm=10.0)
    r = GearSpec(z=52, m_n_mm=1.0, b_mm=10.0, is_internal=True)
    sp = analyze_pair(s, p)
    pr = analyze_pair(p, r)

    kin = planetary_kinematics(sun=s, planet=p, ring=r, n_planets=3, input_speed_rpm=3600.0, sun_planet_geom=sp, planet_ring_geom=pr)
    assert kin.member_speeds_rpm["carrier"] == pytest.approx(1000.0, abs=1e-9)
    assert kin.member_speeds_rpm["planet_abs"] == pytest.approx(-2250.0, abs=1e-9)
    assert abs(kin.planet_relative_speed_rpm) == pytest.approx(3250.0, abs=1e-9)

    mf_sp = compute_mesh_forces(mesh_name="sp", pair_geom=sp, total_torque_gear1_Nm=1.0, gear1_rel_speed_rpm=2600.0, n_parallel_paths=3, k_gamma=1.0)
    mf_pr = compute_mesh_forces(mesh_name="pr", pair_geom=pr, total_torque_gear1_Nm=0.8, gear1_rel_speed_rpm=-3250.0, n_parallel_paths=3, k_gamma=1.0)
    assert mf_sp.F_t_N == pytest.approx(33.333333333, abs=1e-6)
    assert mf_pr.F_t_N == pytest.approx(33.333333333, abs=1e-6)

    p_load = planet_bearing_reaction(mf_sp=mf_sp, mf_pr=mf_pr, planet_rel_rpm=kin.planet_relative_speed_rpm, n_planets=3, d_w_planet_sp_mm=sp.d_w2_mm)
    assert p_load.F_r_N == pytest.approx(66.666666667, abs=1e-5)
    assert p_load.speed_rpm == pytest.approx(3250.0, abs=1e-9)


def test_k_gamma_separates_worst_branch_from_physical_sum_and_helical_thrust():
    # Source: ASTRA_DESIGN_REVIEW.md Blocker 3 & Q7
    s = GearSpec(z=20, m_n_mm=1.0, b_mm=12.0, beta_deg=15.0, hand="right")
    p = GearSpec(z=16, m_n_mm=1.0, b_mm=12.0, beta_deg=15.0, hand="left")
    sp = analyze_pair(s, p)

    mf = compute_mesh_forces(mesh_name="sp", pair_geom=sp, total_torque_gear1_Nm=3.0, gear1_rel_speed_rpm=1000.0, n_parallel_paths=3, k_gamma=1.5)
    # Physical branches MUST sum exactly to total transmitted torque (3.0 N*m)
    assert sum(mf.branch_torques_gear1_Nm) == pytest.approx(3.0, abs=1e-12)
    assert mf.F_t_worst_branch_N == pytest.approx(1.5 * mf.F_t_N, abs=1e-12)
    assert mf.F_a_N > 0.0
    assert "helical_axial_thrust_present" in mf.flags

    # Screening default table check
    assert default_k_gamma(3, floating_member=True) == 1.0
    assert default_k_gamma(4, floating_member=True) == 1.10


def test_compound_stepped_planet_kinematics_and_pair_ratio_signs():
    # Compound stepped planet: sun 18 -> p1 24 (keyed to p2 16) -> ring 58 (a_w = (18+24)/2 = 21 = (58-16)/2!)
    s = GearSpec(z=18, m_n_mm=1.0, b_mm=10.0)
    p1 = GearSpec(z=24, m_n_mm=1.0, b_mm=10.0)
    p2 = GearSpec(z=16, m_n_mm=1.0, b_mm=10.0)
    r = GearSpec(z=58, m_n_mm=1.0, b_mm=10.0, is_internal=True)
    kin = planetary_kinematics(sun=s, planet=p1, planet2=p2, ring=r, n_planets=2, input_speed_rpm=1000.0)
    # u = (24 * 58) / (18 * 16) = 4.833333...; ring fixed => i = 1 + u = 5.833333...
    assert kin.stage_kind == "compound_planetary"
    assert kin.ratio == pytest.approx(1.0 + (24.0 * 58.0) / (18.0 * 16.0), abs=1e-9)
    assert kin.coaxiality_ok

    # Single pair ratio signs
    ext_pair = pair_kinematics(GearSpec(z=20, m_n_mm=1.0, b_mm=10.0), GearSpec(z=40, m_n_mm=1.0, b_mm=10.0))
    int_pair = pair_kinematics(GearSpec(z=20, m_n_mm=1.0, b_mm=10.0), GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, is_internal=True))
    assert ext_pair.ratio == pytest.approx(-2.0, abs=1e-12)
    assert ext_pair.reverses_direction
    assert int_pair.ratio == pytest.approx(2.0, abs=1e-12)
    assert not int_pair.reverses_direction

    # Neighbour planet interference failure (5 large planets z_p=24 around z_s=12)
    s_small = GearSpec(z=12, m_n_mm=1.0, b_mm=10.0, x=0.4)
    p_large = GearSpec(z=24, m_n_mm=1.0, b_mm=10.0)
    r_big = GearSpec(z=60, m_n_mm=1.0, b_mm=10.0, x=-0.4, is_internal=True)
    kin_clash = planetary_kinematics(sun=s_small, planet=p_large, ring=r_big, n_planets=5)
    assert kin_clash.neighbour_clearance_ok is False
    assert "neighbour_planet_interference" in kin_clash.flags
    assert kin_clash.assembly_ok is False


def test_review_branch_share_k_gamma_and_pair_bearing_resultant():
    # Source: ASTRA_CODE_REVIEW.md Blocker 2, geometry_loads.json, stage_boundary.json
    # 1. With T=1 N*m, d1=20 mm, N=3, shares=[0.8, 0.1, 0.1], even if K_gamma=1.0 is passed,
    # worst branch carries 0.8 N*m => F_t_worst = 2000 * 0.8 / 20 = 80.0 N (not 33.333 N).
    from motor_ai_sim.gears import GearBearingSpec, Gearbox, PairStageSpec, PlanetaryStageSpec, get_gear_lubricant
    g1 = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0)
    g2 = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0)
    pair = analyze_pair(g1, g2)
    mf = compute_mesh_forces(
        mesh_name="b", pair_geom=pair, total_torque_gear1_Nm=1.0, gear1_rel_speed_rpm=1000.0,
        n_parallel_paths=3, k_gamma=1.0, branch_shares=[0.8, 0.1, 0.1],
    )
    assert mf.k_gamma == pytest.approx(2.4, abs=1e-12)
    assert mf.F_t_worst_branch_N == pytest.approx(80.0, abs=1e-9)
    assert "k_gamma_raised_to_match_max_physical_branch_share" in mf.flags

    # 2. Pair bearing reaction combines tangential & radial tooth force:
    # 20/40, m_n=1, T1=1 N*m, alpha=20 deg => lossless reaction = 50/cos(20) = 53.208889 N;
    # inline Coulomb bearing drag slightly reduces transmitted mesh torque t_mesh < 1.0 N*m.
    brg = GearBearingSpec(name="dg", bearing_type="deep_groove", d_mm=10.0, D_mm=22.0, B_mm=6.0, C_kn=2.7, C0_kn=1.2, Cu_kn=0.05)
    st = PairStageSpec(
        name="p", gear1=g1, gear2=g2,
        bearing_1a=brg, bearing_1b=brg, shaft1_span_mm=20.0, shaft1_offset_mm=10.0,
    )
    gb = Gearbox([st], lubricant=get_gear_lubricant("ISO_VG_220_PAO"))
    ev_in = gb.evaluate_point(input_speed_rpm=1000.0, torque_Nm=1.0)
    sr_in = ev_in.stage_results[0]
    t1_mesh_obs = sr_in.mesh_forces["main_mesh"].transmitted_torque_gear1_Nm
    expected_brg = t1_mesh_obs * 50.0 / math.cos(math.radians(20.0))
    assert t1_mesh_obs < 1.0
    assert sr_in.bearing_loads[0].F_r_N < 50.0 / math.cos(math.radians(20.0))
    assert sr_in.bearing_loads[0].F_r_N == pytest.approx(expected_brg, rel=1e-6)
    assert sr_in.bearing_loads[1].F_r_N == pytest.approx(expected_brg, rel=1e-6)

    # 3. Output-specified torque boundary preserves exact ON-gear2 torque (+2.0 N*m resisting -500 rpm => -2.0 N*m output)
    ev_out = gb.evaluate_point(input_speed_rpm=1000.0, torque_Nm=2.0, torque_at="output", power_direction="forward")
    sr_out = ev_out.stage_results[0]
    assert sr_out.member_torques_Nm["gear2"] == pytest.approx(2.0, rel=1e-9)
    assert ev_out.output_torque_Nm == pytest.approx(-2.0, rel=1e-9)
    w2 = abs(ev_out.output_speed_rpm) * math.pi / 30.0
    assert ev_out.power_out_useful_W == pytest.approx(2.0 * w2, rel=1e-9)
    assert ev_out.power_in_W == pytest.approx(ev_out.power_out_useful_W + ev_out.power_loss_W, rel=1e-9)

    # 4. B6: reverse_input_boundary — prescribed braking torque -1.0 N*m at input (+1000 rpm) propagates 0->N-1
    st_plan = PlanetaryStageSpec(
        name="pl",
        sun=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        planet=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        ring=GearSpec(z=80, m_n_mm=1.0, b_mm=10.0, is_internal=True, tooth_thickness_allowance_mm=0.02),
        n_planets=3,
    )
    gb_multi = Gearbox([st, st_plan], name="pair_plus_plan", lubricant=get_gear_lubricant("ISO_VG_220_PAO"))
    ev_rev_in = gb_multi.evaluate_point(
        input_speed_rpm=1000.0, torque_Nm=-1.0, torque_at="input", power_direction="reverse",
    )
    assert ev_rev_in.input_torque_Nm == pytest.approx(-1.0, abs=1e-12)
    w1_rad = 1000.0 * math.pi / 30.0
    assert ev_rev_in.power_out_useful_W == pytest.approx(w1_rad, rel=1e-9)
    assert ev_rev_in.power_in_W == pytest.approx(ev_rev_in.power_out_useful_W + ev_rev_in.power_loss_W, rel=1e-9)
    with pytest.raises(ValueError, match="oppose input speed"):
        gb_multi.evaluate_point(
            input_speed_rpm=1000.0, torque_Nm=1.0, torque_at="input", power_direction="reverse",
        )
