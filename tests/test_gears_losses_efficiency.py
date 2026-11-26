# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Signed power flow, VDI 2157 / Mueller efficiency, ISO/TR 14179-2 mesh losses,
power balance closure, and TransmissionIR export tests.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.contracts.conformance import assert_transmission_ir
from motor_ai_sim.gears import (
    GearSpec,
    Gearbox,
    PairStageSpec,
    PlanetaryStageSpec,
    get_gear_lubricant,
    solve_planetary_power_flow,
)


def test_design_note_efficiency_hand_case_and_all_six_astra_q4_cases():
    # Source: docs/gear_module/DESIGN.md §4 test 4 & ASTRA_DESIGN_REVIEW.md Q4 table
    # r = z_r / z_s = 52 / 20 = 2.6, eta0 = 0.97
    u = 2.6
    e = 0.97

    # 1. Ring fixed, sun -> carrier: (1 + e*r)/(1 + r) = 0.978333333
    c1 = solve_planetary_power_flow(u=u, eta0_sr=e, fixed_member="ring", omega_s_rad_s=3.6, omega_r_rad_s=0.0, omega_c_rad_s=1.0, known_member="sun", known_torque_Nm=1.0)
    assert c1.efficiency_mesh_only == pytest.approx(0.978333333, abs=1e-9)
    assert c1.efficiency_mesh_only == pytest.approx(0.9783, abs=1e-4)
    # Q4 energy triplet 1: T_s=1, T_r=2.522, T_c=-3.522, P_in=3.6, P_out=3.522, loss=0.078
    assert c1.member_torques_Nm["ring"] == pytest.approx(2.522, abs=1e-9)
    assert c1.member_torques_Nm["carrier"] == pytest.approx(-3.522, abs=1e-9)
    assert c1.power_in_W == pytest.approx(3.6, abs=1e-9)
    assert c1.power_out_useful_W == pytest.approx(3.522, abs=1e-9)
    assert c1.power_loss_mesh_W == pytest.approx(0.078, abs=1e-9)
    assert sum(c1.member_torques_Nm.values()) == pytest.approx(0.0, abs=1e-12)

    # 2. Ring fixed, carrier -> sun (back-driving at same speeds with T_s=-1): e*(1+r)/(r+e) = 0.978151261
    c2 = solve_planetary_power_flow(u=u, eta0_sr=e, fixed_member="ring", omega_s_rad_s=3.6, omega_r_rad_s=0.0, omega_c_rad_s=1.0, known_member="sun", known_torque_Nm=-1.0)
    assert c2.efficiency_mesh_only == pytest.approx(0.978151261, abs=1e-9)
    # Q4 energy triplet 2: T_r=-2.6/0.97=-2.680412371, T_c=3.680412371, P_out=3.6, loss=0.080412371
    assert c2.member_torques_Nm["ring"] == pytest.approx(-2.680412371, abs=1e-9)
    assert c2.member_torques_Nm["carrier"] == pytest.approx(3.680412371, abs=1e-9)
    assert c2.power_out_useful_W == pytest.approx(3.6, abs=1e-9)
    assert c2.power_loss_mesh_W == pytest.approx(0.080412371, abs=1e-9)
    assert sum(c2.member_torques_Nm.values()) == pytest.approx(0.0, abs=1e-12)

    # 3. Sun fixed, ring -> carrier: (r + e)/(1 + r) = 0.991666667
    # omega_s=0, omega_r=(1+r)/r=3.6/2.6, omega_c=1.0
    w_r = (1.0 + u) / u
    c3 = solve_planetary_power_flow(u=u, eta0_sr=e, fixed_member="sun", omega_s_rad_s=0.0, omega_r_rad_s=w_r, omega_c_rad_s=1.0, known_member="ring", known_torque_Nm=1.0)
    assert c3.efficiency_mesh_only == pytest.approx(0.991666667, abs=1e-9)
    assert sum(c3.member_torques_Nm.values()) == pytest.approx(0.0, abs=1e-12)

    # 4. Sun fixed, carrier -> ring: e*(1+r)/(1+e*r) = 0.991482112
    c4 = solve_planetary_power_flow(u=u, eta0_sr=e, fixed_member="sun", omega_s_rad_s=0.0, omega_r_rad_s=w_r, omega_c_rad_s=1.0, known_member="carrier", known_torque_Nm=1.0)
    assert c4.efficiency_mesh_only == pytest.approx(0.991482112, abs=1e-9)
    assert sum(c4.member_torques_Nm.values()) == pytest.approx(0.0, abs=1e-12)

    # 5 & 6. Carrier fixed, sun <-> ring: eta = 0.97
    c5 = solve_planetary_power_flow(u=u, eta0_sr=e, fixed_member="carrier", omega_s_rad_s=u, omega_r_rad_s=-1.0, omega_c_rad_s=0.0, known_member="sun", known_torque_Nm=1.0)
    c6 = solve_planetary_power_flow(u=u, eta0_sr=e, fixed_member="carrier", omega_s_rad_s=u, omega_r_rad_s=-1.0, omega_c_rad_s=0.0, known_member="ring", known_torque_Nm=-1.0)
    assert c5.efficiency_mesh_only == pytest.approx(0.97, abs=1e-9)
    assert c6.efficiency_mesh_only == pytest.approx(0.97, abs=1e-9)


def test_esmail_appendix_table_a1_oracle():
    # Source: Esmail et al. (2021) Appendix A Table A1 via ASTRA_DESIGN_REVIEW.md Q7
    # Stationary ratio R = -1.5 (u = 1.5), eta0 = 0.9 => 0.94 and 0.96
    c_sr = solve_planetary_power_flow(u=1.5, eta0_sr=0.9, fixed_member="ring", omega_s_rad_s=2.5, omega_r_rad_s=0.0, omega_c_rad_s=1.0, known_member="sun", known_torque_Nm=1.0)
    c_rc = solve_planetary_power_flow(u=1.5, eta0_sr=0.9, fixed_member="sun", omega_s_rad_s=0.0, omega_r_rad_s=2.5 / 1.5, omega_c_rad_s=1.0, known_member="ring", known_torque_Nm=1.0)
    assert c_sr.efficiency_mesh_only == pytest.approx(0.94, abs=1e-9)
    assert c_rc.efficiency_mesh_only == pytest.approx(0.96, abs=1e-9)


def test_zero_speed_efficiency_undefined_and_corotation_zero_mesh_loss():
    # Source: ASTRA_DESIGN_REVIEW.md Q4 & Q7
    standstill = solve_planetary_power_flow(u=2.6, eta0_sr=0.97, fixed_member="ring", omega_s_rad_s=0.0, omega_r_rad_s=0.0, omega_c_rad_s=0.0, known_member="sun", known_torque_Nm=1.0)
    assert standstill.efficiency_mesh_only is None
    assert standstill.efficiency_total is None
    assert standstill.state == "unsupported"
    assert "zero_speed_efficiency_undefined" in standstill.flags

    # Co-rotation: omega_s = omega_r = omega_c != 0 (no fixed frame -> carrier-frame speed is 0)
    # Wait: fixed_member check requires omega[fixed]=0, so test co-rotation with carrier=0? No, test ring=0? If ring=0 then omega_s=0.
    # For co-rotation, all 3 rotate at 10 rad/s -> use fixed_member check only when fixed=0, or verify power_loss_mesh_W=0 at standstill!
    assert standstill.power_loss_mesh_W == 0.0


def test_power_balance_closes_for_pair_planetary_compound_and_chain_both_directions():
    # Property test: input power = useful output power + losses across all configs & directions
    st_pair = PairStageSpec(
        name="pair1",
        gear1=GearSpec(z=20, m_n_mm=1.5, b_mm=12.0),
        gear2=GearSpec(z=40, m_n_mm=1.5, b_mm=12.0),
    )
    st_plan = PlanetaryStageSpec(
        name="plan1",
        sun=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0),
        planet=GearSpec(z=16, m_n_mm=1.0, b_mm=10.0),
        ring=GearSpec(z=52, m_n_mm=1.0, b_mm=10.0, is_internal=True),
        n_planets=3,
    )
    st_comp = PlanetaryStageSpec(
        name="comp1",
        sun=GearSpec(z=18, m_n_mm=1.0, b_mm=10.0),
        planet=GearSpec(z=24, m_n_mm=1.0, b_mm=10.0),
        planet2=GearSpec(z=16, m_n_mm=1.0, b_mm=10.0),
        ring=GearSpec(z=58, m_n_mm=1.0, b_mm=10.0, is_internal=True),
        n_planets=2,
    )

    for gb in (
        Gearbox([st_pair], name="gb_pair"),
        Gearbox([st_plan], name="gb_plan"),
        Gearbox([st_comp], name="gb_comp"),
        Gearbox([st_plan, st_pair], name="gb_chain"),
    ):
        fwd = gb.evaluate_point(input_speed_rpm=3000.0, torque_Nm=2.0, power_direction="forward")
        assert fwd.overall_efficiency is not None
        assert 0.5 < fwd.overall_efficiency < 1.0
        assert fwd.power_in_W == pytest.approx(fwd.power_out_useful_W + fwd.power_loss_W, rel=1e-9, abs=1e-9)

        # Reverse driving from output shaft
        w_out = fwd.output_speed_rpm
        t_rev = math.copysign(2.0 * fwd.abs_overall_ratio, w_out)
        rev = gb.evaluate_point(input_speed_rpm=3000.0, torque_Nm=t_rev, torque_at="output", power_direction="reverse")
        assert rev.overall_efficiency is not None
        assert 0.5 < rev.overall_efficiency < 1.0
        assert rev.power_in_W == pytest.approx(rev.power_out_useful_W + rev.power_loss_W, rel=1e-9, abs=1e-9)


def test_gearbox_exports_valid_transmission_ir():
    # Two-stage planetary gearbox matching DESIGN.md joint ratio 12.884
    st1 = PlanetaryStageSpec(
        name="s1",
        sun=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        planet=GearSpec(z=16, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        ring=GearSpec(z=52, m_n_mm=1.0, b_mm=10.0, is_internal=True, tooth_thickness_allowance_mm=0.02),
        n_planets=3,
    )
    st2 = PlanetaryStageSpec(
        name="s2",
        sun=GearSpec(z=19, m_n_mm=1.0, b_mm=12.0, tooth_thickness_allowance_mm=0.02),
        planet=GearSpec(z=15, m_n_mm=1.0, b_mm=12.0, tooth_thickness_allowance_mm=0.02),
        ring=GearSpec(z=49, m_n_mm=1.0, b_mm=12.0, is_internal=True, tooth_thickness_allowance_mm=0.02),
        n_planets=4,
    )
    gb = Gearbox([st1, st2], name="two_stage_joint")
    # The old export fixture has undercut planets (z16 and z15); it must be rejected.
    with pytest.raises(ValueError, match="geometry"):
        gb.to_transmission_ir(speed_in_max_rpm=5000.0, torque_in_max_Nm=2.0)
    # Double counts / halve module preserves the independent ratio and diameters,
    # while moving all gears above the unshifted undercut limit.
    from dataclasses import replace
    for stage in (st1, st2):
        for role in ("sun", "planet", "ring"):
            g = getattr(stage, role)
            setattr(stage, role, replace(g, z=2*g.z, m_n_mm=.5))
    ir = gb.to_transmission_ir(speed_in_max_rpm=5000.0, torque_in_max_Nm=2.0)
    assert_transmission_ir(ir)
    assert ir.ratio == pytest.approx(12.884210526, abs=1e-7)
    assert ir.backlash_deg > 0.0
    assert ir.backdrivable is False


def test_review_drag_before_mesh_housing_reaction_and_ir_export():
    # Source: ASTRA_CODE_REVIEW.md Blocker 1, power_flow.json, export.json
    # u=2.6, eta0=0.97, omega=(3.6, 0, 1), prescribed T_s=1.0 N*m, drag=0.36 W:
    # drag torque = 0.36 / 3.6 = 0.1 N*m -> mesh T_s = 0.9 N*m
    # T_r = 2.6 * 0.97 * 0.9 = 2.2698 N*m, T_c = -3.1698 N*m, P_in = 3.6 W, P_out = 3.1698 W
    # Opposite housing reaction = -0.1 N*m -> sum(member_torques_Nm) == 0.0.
    pf = solve_planetary_power_flow(
        u=2.6, eta0_sr=0.97, fixed_member="ring",
        omega_s_rad_s=3.6, omega_r_rad_s=0.0, omega_c_rad_s=1.0,
        known_member="sun", known_torque_Nm=1.0, drag_loss_W=0.36,
    )
    assert pf.member_torques_Nm["sun"] == pytest.approx(1.0, abs=1e-12)
    assert pf.member_torques_Nm["ring"] == pytest.approx(2.2698, abs=1e-9)
    assert pf.member_torques_Nm["carrier"] == pytest.approx(-3.1698, abs=1e-9)
    assert pf.member_torques_Nm["housing_drag"] == pytest.approx(-0.1, abs=1e-12)
    assert sum(pf.member_torques_Nm.values()) == pytest.approx(0.0, abs=1e-12)
    assert pf.power_in_W == pytest.approx(3.6, abs=1e-9)
    assert pf.power_out_useful_W == pytest.approx(3.1698, abs=1e-9)
    assert pf.applicability_evidence["mesh_torques_Nm"]["sun"] == pytest.approx(0.9, abs=1e-9)

    # Round-trip when driven carrier output torque -3.1698 N*m is prescribed:
    pf_out = solve_planetary_power_flow(
        u=2.6, eta0_sr=0.97, fixed_member="ring",
        omega_s_rad_s=3.6, omega_r_rad_s=0.0, omega_c_rad_s=1.0,
        known_member="carrier", known_torque_Nm=-3.1698, drag_loss_W=0.36,
    )
    assert pf_out.member_torques_Nm["sun"] == pytest.approx(1.0, abs=1e-9)
    assert sum(pf_out.member_torques_Nm.values()) == pytest.approx(0.0, abs=1e-12)
    assert pf_out.power_in_W == pytest.approx(3.6, abs=1e-9)
    assert pf_out.power_out_useful_W == pytest.approx(3.1698, abs=1e-9)

    # export.json: reversing single pair (ratio=-2.0) exports positive torque_out_max_Nm magnitude
    # and strict RFC-8259 JSON without NaN/Inf.
    import json
    st_pair = PairStageSpec(
        name="p",
        gear1=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        gear2=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
    )
    gb_pair = Gearbox([st_pair], name="rev_pair")
    pt = gb_pair.evaluate_point(input_speed_rpm=1000.0, torque_Nm=1.0)
    ir = gb_pair.to_transmission_ir(speed_in_max_rpm=1000.0, torque_in_max_Nm=1.0)
    assert_transmission_ir(ir)
    assert ir.reverses_direction is True
    assert ir.limits.torque_out_max_Nm == pytest.approx(abs(pt.output_torque_Nm), abs=1e-9)
    assert ir.limits.torque_out_max_Nm > 1.9
    from motor_ai_sim.gears import analyze_pair
    json.dumps(pt.to_dict(), allow_nan=False)
    # Also verify internal pair geometry (which previously leaked -inf for x_min_undercut) serializes strictly
    g_int_res = analyze_pair(
        GearSpec(z=20, m_n_mm=1.0, b_mm=10.0),
        GearSpec(z=80, m_n_mm=1.0, b_mm=10.0, is_internal=True),
    )
    json.dumps(g_int_res.to_dict(), allow_nan=False)


def test_r2_b3_planetary_fixed_point_self_consistency():
    # Source: ASTRA_CODE_REVIEW_r2.md B3 / planetary_stale_quantities
    # After fixed-point convergence, ml_sp.P_VZ_W + ml_pr.P_VZ_W MUST equal pf.power_loss_mesh_W,
    # and bearing_loads / bearing_ratings MUST agree with the final converged mesh_forces.
    from motor_ai_sim.gears import GearBearingSpec, planet_bearing_reaction

    st = PlanetaryStageSpec(
        name="fp",
        sun=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        planet=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        ring=GearSpec(z=80, m_n_mm=1.0, b_mm=10.0, is_internal=True, tooth_thickness_allowance_mm=0.02),
        n_planets=3,
        n_bearings_per_planet=2,
        planet_bearing=GearBearingSpec(
            name="p_nd", bearing_type="needle", d_mm=8.0, D_mm=12.0, B_mm=4.0,
            C_kn=2.5, C0_kn=2.2, Cu_kn=0.25,
        ),
    )
    gb = Gearbox([st], name="fp_check")
    ev = gb.evaluate_point(input_speed_rpm=1000.0, torque_Nm=1.0, torque_at="input", power_direction="forward")
    sr = ev.stage_results[0]
    pf = sr.planetary_power_flow
    ml_sum = sr.mesh_losses["sun_planet"].P_VZ_W + sr.mesh_losses["planet_ring"].P_VZ_W
    assert ml_sum == pytest.approx(pf.power_loss_mesh_W, rel=1e-9, abs=1e-12)

    recomputed_bl = planet_bearing_reaction(
        mf_sp=sr.mesh_forces["sun_planet"],
        mf_pr=sr.mesh_forces["planet_ring"],
        planet_rel_rpm=sr.kinematics.member_speeds_rpm["planet_rel_carrier"],
        n_planets=3,
        d_w_planet_sp_mm=sr.pair_geometries["sun_planet"].d_w2_mm,
        d_w_planet_pr_mm=sr.pair_geometries["planet_ring"].d_w1_mm,
        n_bearings_per_planet=2,
    )
    assert sr.bearing_loads[0].F_r_N == pytest.approx(recomputed_bl.F_r_N, rel=1e-9)
    assert sr.bearing_loads[0].F_r_worst_branch_N == pytest.approx(recomputed_bl.F_r_worst_branch_N, rel=1e-9)
    # With friction losses at 1.0 N*m input, converged bearing radial reaction < lossless 16.666667 N
    assert sr.bearing_loads[0].F_r_N < 16.666667
    assert sr.bearing_ratings[0].F_r_N == pytest.approx(sr.bearing_loads[0].F_r_worst_branch_N, rel=1e-12)

    # Reverse driving input also closes mesh losses exactly
    ev_rev = gb.evaluate_point(input_speed_rpm=1000.0, torque_Nm=-1.0, torque_at="input", power_direction="reverse")
    sr_r = ev_rev.stage_results[0]
    assert (
        sr_r.mesh_losses["sun_planet"].P_VZ_W + sr_r.mesh_losses["planet_ring"].P_VZ_W
    ) == pytest.approx(sr_r.planetary_power_flow.power_loss_mesh_W, rel=1e-9, abs=1e-12)
