# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""ISO 6336-1/2/3/6, lubrication (10), bearings ISO 281/L10m (11),
all loaded parts (12-13), presets, catalogue gearbox, and spectrum fallback tests.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.gears import (
    CarrierSpec,
    CatalogueGearboxSpec,
    DutyBin,
    DutySpectrum,
    GearBearingSpec,
    GearSpec,
    Gearbox,
    KeyOrSplineSpec,
    PairStageSpec,
    PlanetaryStageSpec,
    PlanetPinSpec,
    RingRimFixationSpec,
    ShaftSectionSpec,
    analyze_pair,
    check_catalogue_gearbox,
    compute_mesh_forces,
    evaluate_against_preset,
    evaluate_bearing,
    evaluate_bearing_spectrum,
    evaluate_mesh_lubrication,
    get_gear_lubricant,
    get_gear_material,
    get_preset,
    iso281_a_iso,
    miner_damage_from_cycles,
    rate_carrier,
    rate_key_or_spline,
    rate_pair_iso6336,
    rate_planet_pin,
    rate_ring_rim_and_fixation,
    rate_shaft_din743,
    solve_grease_thermal_balance,
    summarize_part_strengths,
)


# ---------------------------------------------------------------------------
# ISO 6336-1/2/3/6 & Kinetic-Energy Reflected Inertia
# ---------------------------------------------------------------------------

def test_iso6336_external_rating_uses_lubrication_and_internal_root_is_unsupported():
    # Source: ISO 6336-1/2/3 Method B/C & ASTRA_DESIGN_REVIEW.md Blockers 1, 4
    g1 = GearSpec(z=20, m_n_mm=2.0, b_mm=16.0, tooth_thickness_allowance_mm=0.03)
    g2 = GearSpec(z=40, m_n_mm=2.0, b_mm=16.0, tooth_thickness_allowance_mm=0.03)
    geom = analyze_pair(g1, g2)
    mf = compute_mesh_forces(mesh_name="m", pair_geom=geom, total_torque_gear1_Nm=12.0, gear1_rel_speed_rpm=1500.0)
    lube = get_gear_lubricant("ISO_VG_220_mineral")
    rat = rate_pair_iso6336(
        mesh_name="m", pair_geom=geom, gear1_spec=g1, gear2_spec=g2,
        mesh_forces=mf, n1_rel_rpm=1500.0, lubricant=lube, temp_C=70.0,
    )
    assert rat.gear1_rating.Z_L != 1.0
    assert rat.gear1_rating.Z_v != 1.0
    assert rat.gear1_rating.Z_R != 1.0
    assert "Z_W=1.0" in rat.gear1_rating.simplified_factors
    assert "Z_X=1.0" in rat.gear1_rating.simplified_factors
    assert rat.gear1_rating.S_F is not None and rat.gear1_rating.S_F > 1.0
    assert rat.gear1_rating.S_H > 1.0
    assert rat.state == "estimated"
    assert rat.passed is True

    # Internal pair tooth-root bending lacks shaper cutter geometry -> MUST be unsupported & never pass!
    ring = GearSpec(z=60, m_n_mm=2.0, b_mm=16.0, is_internal=True)
    geom_int = analyze_pair(g1, ring)
    mf_int = compute_mesh_forces(mesh_name="int", pair_geom=geom_int, total_torque_gear1_Nm=25.0, gear1_rel_speed_rpm=1500.0)
    rat_int = rate_pair_iso6336(
        mesh_name="int", pair_geom=geom_int, gear1_spec=g1, gear2_spec=ring,
        mesh_forces=mf_int, n1_rel_rpm=1500.0, lubricant=lube, temp_C=70.0,
    )
    assert rat_int.gear2_rating.bending_state == "unsupported"
    assert rat_int.gear2_rating.S_F is None
    assert rat_int.gear2_rating.bending_passed is False
    assert rat_int.passed is False
    assert rat_int.state == "unsupported"

    # Outside ISO 6336-1 domain (alpha_n = 12 deg < 15 deg) -> unsupported & never passes
    g_bad1 = GearSpec(z=25, m_n_mm=2.0, b_mm=16.0, alpha_n_deg=12.0)
    g_bad2 = GearSpec(z=50, m_n_mm=2.0, b_mm=16.0, alpha_n_deg=12.0)
    geom_bad = analyze_pair(g_bad1, g_bad2)
    mf_bad = compute_mesh_forces(mesh_name="bad", pair_geom=geom_bad, total_torque_gear1_Nm=10.0, gear1_rel_speed_rpm=1000.0)
    rat_bad = rate_pair_iso6336(
        mesh_name="bad", pair_geom=geom_bad, gear1_spec=g_bad1, gear2_spec=g_bad2,
        mesh_forces=mf_bad, n1_rel_rpm=1000.0, lubricant=lube, temp_C=70.0,
    )
    assert rat_bad.state == "unsupported"
    assert rat_bad.passed is False


def test_miner_damage_oracle_and_kinetic_energy_reflected_inertia():
    # Source: ASTRA_DESIGN_REVIEW.md Q7 Miner block oracle
    # n1=1e5, N1=1e6 (0.10); n2=2e5, N2=1e7 (0.02) => D=0.12; t_b=100 h => L=833.333333 h
    m = miner_damage_from_cycles(target_name="test_gear", bins=[(1.0e5, 1.0e6), (2.0e5, 1.0e7)], block_hours=100.0)
    assert m.damage_D == pytest.approx(0.12, abs=1e-12)
    assert m.predicted_life_hours == pytest.approx(833.333333333, abs=1e-6)

    # Reflected inertia from kinetic energy (ASTRA_DESIGN_REVIEW.md Q6):
    # J_ref = J_s*(w_s/w_s)^2 + N * (J_p*(w_p_abs/w_s)^2 + m_p * a_w^2 * (w_c/w_s)^2) + J_c*(w_c/w_s)^2
    s = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0)
    p = GearSpec(z=16, m_n_mm=1.0, b_mm=10.0)
    r = GearSpec(z=52, m_n_mm=1.0, b_mm=10.0, is_internal=True)
    st = PlanetaryStageSpec(name="p", sun=s, planet=p, ring=r, n_planets=3, carrier_mass_kg=0.05, carrier_inertia_kg_m2=1.0e-5)
    gb = Gearbox([st])
    ev = gb.evaluate_point(input_speed_rpm=3600.0, torque_Nm=1.0)

    rho = get_gear_material(s.material_name).density_kg_m3
    m_s = rho * math.pi * (0.010 ** 2) * 0.010
    j_s = 0.5 * m_s * (0.010 ** 2)
    m_p = rho * math.pi * (0.008 ** 2) * 0.010
    j_p = 0.5 * m_p * (0.008 ** 2)
    a_w = 0.018
    # At n_s=3600: n_c=1000, n_p_abs=-2250 (NOT relative -3250!)
    j_ref_expected = j_s + 1.0e-5 * (1000.0 / 3600.0) ** 2 + 3 * (
        j_p * (-2250.0 / 3600.0) ** 2 + m_p * (a_w ** 2) * (1000.0 / 3600.0) ** 2
    )
    assert ev.reflected_inertia_in_kg_m2 == pytest.approx(j_ref_expected, rel=1e-9)


# ---------------------------------------------------------------------------
# Item 10 & 14: Lubrication, Film Thickness, Scuffing, Thermal Balance
# ---------------------------------------------------------------------------

def test_lubrication_monotonic_viscosity_film_scuffing_and_thermal_balance():
    # Source: TASK_NEON_stage1.md items 10, 14 (ISO 3104 / ASTM D341 + Dowson-Higginson)
    oil_thin = get_gear_lubricant("ISO_VG_68_PAO")
    oil_thick = get_gear_lubricant("ISO_VG_220_PAO")

    # Anchor check + monotonic fall with higher temperature
    assert oil_thick.kinematic_viscosity_mm2_s(40.0) == pytest.approx(220.0, rel=1e-6)
    assert oil_thick.kinematic_viscosity_mm2_s(100.0) == pytest.approx(25.5, rel=1e-6)
    assert oil_thick.kinematic_viscosity_mm2_s(50.0) > oil_thick.kinematic_viscosity_mm2_s(80.0) > oil_thick.kinematic_viscosity_mm2_s(110.0)

    # Monotonic thicker oil -> thicker film at fixed temp, speed, load
    g1 = GearSpec(z=20, m_n_mm=2.0, b_mm=16.0, Ra_flank_um=0.8)
    g2 = GearSpec(z=40, m_n_mm=2.0, b_mm=16.0, Ra_flank_um=0.8)
    geom = analyze_pair(g1, g2)
    l_thin = evaluate_mesh_lubrication(pair_geom=geom, lubricant=oil_thin, temp_C=70.0, pitch_velocity_m_s=5.0, F_n_N=1200.0, b_eff_mm=16.0, sigma_Hlim_MPa=1500.0)
    l_thick = evaluate_mesh_lubrication(pair_geom=geom, lubricant=oil_thick, temp_C=70.0, pitch_velocity_m_s=5.0, F_n_N=1200.0, b_eff_mm=16.0, sigma_Hlim_MPa=1500.0)
    assert l_thick.h_min_um > l_thin.h_min_um > 0.0
    assert l_thick.lambda_film > l_thin.lambda_film
    assert l_thick.Z_L > l_thin.Z_L
    assert l_thick.scuffing_contact_temp_C > 70.0

    # Boundary lubrication check at creep speed + rough surface
    l_bound = evaluate_mesh_lubrication(pair_geom=geom, lubricant=oil_thin, temp_C=90.0, pitch_velocity_m_s=0.1, F_n_N=2000.0, b_eff_mm=16.0, sigma_Hlim_MPa=1500.0, Ra1_um=2.0, Ra2_um=2.0)
    assert l_bound.boundary_lubrication_flag is True
    assert l_bound.lambda_film < 0.4
    assert l_bound.micropitting_risk == "high"

    # Sealed grease steady convection thermal balance
    grease = get_gear_lubricant("ROBOT_GREASE_NLGI1_PAO")
    # Hand case: constant loss 15 W, A=0.05 m^2, h=15 W/(m^2 K) => UA=0.75 W/K => dT = 20 K => T_eq = 45 C
    tb = solve_grease_thermal_balance(lubricant=grease, loss_fn_W=lambda _t: 15.0, ambient_temp_C=25.0, housing_area_m2=0.05, h_conv_W_m2K=15.0)
    assert tb.converged
    assert tb.equilibrium_temp_C == pytest.approx(45.0, abs=1e-3)
    assert tb.residual_W == pytest.approx(0.0, abs=1e-3)
    assert tb.state == "estimated"


# ---------------------------------------------------------------------------
# Item 11 & 14: Bearings ISO 281 L10 / L10m, S0, Needle/AC/DG, Spectrum
# ---------------------------------------------------------------------------

def test_bearing_skf_oracles_modified_life_kappa_cap_and_spectrum():
    # Source: SKF basic-life equation via ASTRA_DESIGN_REVIEW.md Q7
    # C/P = 10, n_rel = 100 rpm => Ball: 1000 Mrev, 166666.6667 h; Roller: 2154.434690 Mrev, 359072.4483 h
    ball = GearBearingSpec(name="test_ball", bearing_type="deep_groove", d_mm=20.0, D_mm=42.0, B_mm=12.0, C_kn=10.0, C0_kn=5.0, Cu_kn=0.4)
    res_ball = evaluate_bearing(ball, role="test", F_r_N=1000.0, F_a_N=0.0, speed_rel_rpm=100.0)
    assert res_ball.L10_Mrev == pytest.approx(1000.0, rel=1e-9)
    assert res_ball.L10h_h == pytest.approx(166666.666667, rel=1e-6)
    assert res_ball.S0_static == pytest.approx(5.0, rel=1e-9)

    needle = GearBearingSpec(name="test_needle", bearing_type="needle", d_mm=12.0, D_mm=18.0, B_mm=12.0, C_kn=10.0, C0_kn=8.0, Cu_kn=1.0)
    res_needle = evaluate_bearing(needle, role="planet", F_r_N=1000.0, F_a_N=0.0, speed_rel_rpm=100.0)
    assert res_needle.L10_Mrev == pytest.approx(10.0 ** (10.0 / 3.0), rel=1e-9)
    assert res_needle.L10h_h == pytest.approx(359072.4483, rel=1e-6)

    # Needle bearing with axial load MUST be unsupported & not pass!
    res_needle_ax = evaluate_bearing(needle, role="planet", F_r_N=1000.0, F_a_N=50.0, speed_rel_rpm=100.0)
    assert res_needle_ax.state == "unsupported"
    assert res_needle_ax.passed is False

    # Standstill n=0 => L10h is None, state unsupported, S0 valid
    res_zero = evaluate_bearing(ball, role="test", F_r_N=1000.0, F_a_N=0.0, speed_rel_rpm=0.0)
    assert res_zero.L10h_h is None
    assert res_zero.S0_static == pytest.approx(5.0, rel=1e-9)
    assert res_zero.state == "unsupported"
    assert res_zero.passed is False

    # Monotonic a_ISO with kappa up to ISO 281 cap kappa = 4.0
    a_k05 = iso281_a_iso(kappa=0.5, e_contamination=0.5, Cu_N=400.0, P_N=1000.0, is_roller=False)
    a_k20 = iso281_a_iso(kappa=2.0, e_contamination=0.5, Cu_N=400.0, P_N=1000.0, is_roller=False)
    a_k40 = iso281_a_iso(kappa=4.0, e_contamination=0.5, Cu_N=400.0, P_N=1000.0, is_roller=False)
    a_k80 = iso281_a_iso(kappa=8.0, e_contamination=0.5, Cu_N=400.0, P_N=1000.0, is_roller=False)
    assert a_k05 < a_k20 < a_k40
    assert a_k80 == pytest.approx(a_k40, abs=1e-12)

    # Spectrum revolution-weighted hand check:
    # Bin 1: F_r=1000 N (L10=1000 Mrev), n=1000 rpm, dt=100 h => rev=6.0 Mrev => damage=0.006
    # Bin 2: F_r=2000 N (L10=125 Mrev), n=1000 rpm, dt=100 h => rev=6.0 Mrev => damage=0.048
    # Total block = 200 h, D = 0.054 => L10_spec = 200 / 0.054 = 3703.7037 h
    spec_res = evaluate_bearing_spectrum(
        ball, role="test", bins=[(1000.0, 0.0, 1000.0, 100.0), (2000.0, 0.0, 1000.0, 100.0)],
        required_life_h=1000.0,
    )
    assert spec_res.L10_block_damage == pytest.approx(0.054, rel=1e-9)
    assert spec_res.L10_spectrum_h == pytest.approx(200.0 / 0.054, rel=1e-6)
    assert spec_res.passed is True

    # Library bearing card bridge (618/8-2Z without Cu_kn -> basic L10 works, L10m missing Cu_kn)
    lib_brg = GearBearingSpec.from_library("618/8-2Z")
    res_lib = evaluate_bearing(lib_brg, role="input", F_r_N=50.0, F_a_N=0.0, speed_rel_rpm=3000.0)
    assert res_lib.friction_power_W > 0.0
    assert res_lib.L10h_h is not None and res_lib.L10mh_h is None
    assert "Cu_kn" in res_lib.missing_inputs


# ---------------------------------------------------------------------------
# Items 12-14: All Other Parts (Pins, Shafts DIN 743, Keys, Carrier, Ring Rim)
# ---------------------------------------------------------------------------

def test_all_loaded_parts_hand_arithmetic_and_governing_summary():
    # 1. Planet pin: d=8 mm, span=12 mm, seat=10 mm, straddle, F_duty=1000 N
    # Centered patch load beam: M = F*(2*L - l_seat)/8 = 1000*(24-10)/8 = 1750 N*mm
    # W_b = pi*8^3/32 = 50.265482 mm^3 => sigma_b = 1750 / 50.265482 = 34.815144 MPa
    # V = 500 N, A = 50.265482 mm^2 => tau = (4/3)*500/50.265482 = 13.262912 MPa; p_seat = 1000/(8*10) = 12.5 MPa
    pin_rec = rate_planet_pin(
        PlanetPinSpec(diameter_mm=8.0, span_mm=12.0, seat_length_mm=10.0, uts_MPa=1000.0, yield_strength_MPa=800.0),
        F_pin_duty_N=1000.0, F_pin_shock_N=2500.0,
    )
    assert pin_rec.stress_summary["sigma_b_duty_MPa"] == pytest.approx(1750.0 / (math.pi * 512 / 32), rel=1e-6)
    assert pin_rec.stress_summary["p_seat_duty_MPa"] == pytest.approx(12.5, rel=1e-9)
    assert pin_rec.is_simplified is True

    # 2. Shaft DIN 743 (keyway): d=20 mm => W_t = pi*20^3/16 = 1570.7963 mm^3; T=50 N*m => tau = 31.831 MPa
    shaft_rec = rate_shaft_din743(
        ShaftSectionSpec(name="output_shaft", diameter_mm=20.0, notch_type="keyway", yield_strength_MPa=650.0, uts_MPa=950.0),
        torque_duty_Nm=50.0, bending_moment_duty_Nm=10.0, torque_shock_Nm=125.0,
    )
    assert shaft_rec.stress_summary["tau_ta_MPa"] == pytest.approx(50000.0 / (math.pi * 8000 / 16), rel=1e-6)
    assert shaft_rec.passed is True

    # 3. Parallel key DIN 6885: d=20 mm, b=6, h=6, L=20, T=60 N*m => Ft=6000 N, hc=2.7 mm, p=111.111 MPa, tau=50.0 MPa
    key_rec = rate_key_or_spline(
        KeyOrSplineSpec(name="output_key", kind="key", pitch_or_shaft_diameter_mm=20.0, effective_length_mm=20.0, key_width_mm=6.0, key_height_mm=6.0, allowable_pressure_MPa=150.0),
        torque_duty_Nm=60.0, torque_shock_Nm=120.0,
    )
    assert key_rec.stress_summary["p_duty_MPa"] == pytest.approx(6000.0 / (2.7 * 20.0), rel=1e-6)
    assert key_rec.stress_summary["tau_duty_MPa"] == pytest.approx(50.0, rel=1e-9)

    # 4. Carrier: pin_d=8, t=5, 2 plates => A_hole = 80 mm^2 => p_hole = 1000/80 = 12.5 MPa
    car_rec = rate_carrier(
        CarrierSpec(pin_diameter_mm=8.0, plate_thickness_mm=5.0, n_plates=2, n_posts=3),
        F_pin_worst_duty_N=1000.0, T_carrier_duty_Nm=50.0, orbit_radius_mm=25.0,
    )
    assert car_rec.stress_summary["p_hole_duty_MPa"] == pytest.approx(12.5, rel=1e-9)
    assert car_rec.is_simplified is True

    # 5. Ring gear rim & bolted fixation (8 bolts M4 on PCD 70 mm)
    rim_rec, fix_rec = rate_ring_rim_and_fixation(
        RingRimFixationSpec(d_f_ring_mm=54.5, rim_thickness_mm=3.0, b_ring_mm=10.0, m_n_mm=1.0, n_fasteners=8, fastener_diameter_mm=4.0, bolt_circle_diameter_mm=70.0),
        F_t_ring_branch_N=400.0, F_r_ring_branch_N=150.0, T_ring_duty_Nm=30.0,
    )
    # Fixation shear: F_total = 30000 / 35 = 857.14 N => per bolt = 107.14 N => tau = 107.14 / (pi*4) = 8.526 MPa
    assert fix_rec.stress_summary["tau_duty_MPa"] == pytest.approx((30000.0 / 35.0 / 8.0) / (4.0 * math.pi), rel=1e-6)
    assert rim_rec.is_simplified is True

    # 6. Summary ranks governing weakest part first
    summary = summarize_part_strengths([pin_rec, shaft_rec, key_rec, car_rec, rim_rec, fix_rec])
    assert summary.governing_part is summary.parts[0]
    ratios = [(p.min_safety_factor / (p.required_safety_factor or 1.0)) for p in summary.parts]
    assert ratios == sorted(ratios)


# ---------------------------------------------------------------------------
# Presets, Catalogue Gearbox, and Spectrum vs Motor Fallback
# ---------------------------------------------------------------------------

def test_presets_catalogue_gearbox_and_spectrum_fallback():
    # Source: OWNER_DECISIONS.md & ASTRA_DESIGN_REVIEW.md Q5
    std = get_preset("standard")
    rob = get_preset("humanoid_robot_joint")
    assert std.min_S_F_duty == 1.5 and std.min_S_H_duty == 1.1 and std.required_life_h == 10000.0
    assert rob.min_S_F_duty == 1.75 and rob.min_S_H_duty == 1.20 and rob.min_S_F_static == 2.0 and rob.required_life_h == 20000.0
    assert "min_S_F_duty" in rob.proposed_fields

    # Unsupported rating must NEVER pass preset evaluation
    ev_unsup = evaluate_against_preset(
        std, min_S_F_duty=2.5, min_S_H_duty=1.8, S_F_duty_state="unsupported", S_H_duty_state="valid",
        gear_life_h=50000.0, bearing_life_h=50000.0, min_bearing_S0=2.0,
    )
    assert ev_unsup.all_passed is False
    assert ev_unsup.state == "unsupported"

    # Catalogue gearbox cube-root-mean hand check:
    # Two bins at 100 rpm, 100 h each: T1=10 N*m, T2=20 N*m => T_eq = ((10^3 + 20^3)/2)^(1/3) = 4500^(1/3) = 16.509636 N*m
    cat = CatalogueGearboxSpec(
        name="cat_10", ratio=10.0, efficiency=0.92, rated_torque_out_Nm=20.0,
        peak_torque_out_Nm=45.0, rated_speed_in_max_rpm=4000.0,
        rated_life_h=10000.0, rated_speed_out_rpm=100.0, backlash_arcmin=5.0, backdrivable=True,
    )
    spec = DutySpectrum(bins=[DutyBin(10.0, 100.0, 100.0), DutyBin(20.0, 100.0, 100.0)], peak_torque_out_Nm=40.0)
    cat_res = check_catalogue_gearbox(cat, spec, preset_name="standard")
    t_eq_exp = ((1000.0 + 8000.0) / 2.0) ** (1.0 / 3.0)
    assert cat_res.equivalent_torque_out_Nm == pytest.approx(t_eq_exp, rel=1e-9)
    assert cat_res.predicted_life_h == pytest.approx(10000.0 * ((20.0 / t_eq_exp) ** 3), rel=1e-9)
    assert cat_res.passed is True

    # Spectrum vs motor duties fallback on own planetary design
    st = PlanetaryStageSpec(
        name="j1",
        sun=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.015),
        planet=GearSpec(z=16, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.015),
        ring=GearSpec(z=52, m_n_mm=1.0, b_mm=10.0, is_internal=True, tooth_thickness_allowance_mm=0.015),
        n_planets=3,
        planet_bearing=GearBearingSpec(name="p_brg", bearing_type="needle", d_mm=6.0, D_mm=10.0, B_mm=8.0, C_kn=2.5, C0_kn=2.2, Cu_kn=0.28),
        planet_pin=PlanetPinSpec(diameter_mm=6.0, span_mm=12.0, seat_length_mm=8.0),
        carrier=CarrierSpec(pin_diameter_mm=6.0, plate_thickness_mm=4.0),
        ring_rim_fixation=RingRimFixationSpec(d_f_ring_mm=54.5, rim_thickness_mm=3.0, b_ring_mm=10.0, m_n_mm=1.0),
    )
    gb = Gearbox([st], name="joint_stage")
    res_spec = gb.evaluate_spectrum(DutySpectrum(bins=[DutyBin(torque_out_Nm=5.0, speed_out_rpm=200.0, duration_h=100.0)]), preset_name="humanoid_robot_joint")
    assert res_spec.duty_source == "spectrum"
    assert res_spec.preset_name == "humanoid_robot_joint"
    assert res_spec.parts_summary is not None and len(res_spec.parts_summary.parts) > 0

    res_fb = gb.evaluate_spectrum(None, fallback_motor_continuous_Nm=1.0, fallback_motor_continuous_rpm=1000.0, preset_name="standard")
    assert res_fb.duty_source == "motor_duties_fallback"
    assert "motor_duties_fallback_used_no_project_spectrum" in res_fb.flags

    # JSON serialization round-trip of GearboxEvaluationResult & angular-contact / spline checks
    import json
    payload_str = json.dumps(res_spec.to_dict())
    assert "humanoid_robot_joint" in payload_str

    ac_brg = GearBearingSpec(name="ac_15", bearing_type="angular_contact", d_mm=20.0, D_mm=42.0, B_mm=12.0, C_kn=11.0, C0_kn=6.0, Cu_kn=0.45, contact_angle_deg=15.0)
    res_ac = evaluate_bearing(ac_brg, role="input", F_r_N=500.0, F_a_N=400.0, speed_rel_rpm=2000.0)
    assert res_ac.P_equiv_N > 500.0
    assert res_ac.L10mh_h is not None

    spline_rec = rate_key_or_spline(
        KeyOrSplineSpec(name="out_spline", kind="spline", pitch_or_shaft_diameter_mm=24.0, effective_length_mm=16.0, n_teeth=16, tooth_contact_height_mm=1.0),
        torque_duty_Nm=50.0,
    )
    assert spline_rec.passed is True


def test_review_iso6336_ys_factor_and_unsafe_gates():
    # Source: strength_factors.json, internal_root_gate.json, unsafe_ratings.json
    # 1. Exact ISO 6336-3 Y_S = (1.2 + 0.13*L)*q_s^(1/(1.21+2.3/L)) with L = sFn/hFe = 1.95886169 => Y_S = 1.81597482
    g1 = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02)
    g2 = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02)
    geom = analyze_pair(g1, g2)
    mf = compute_mesh_forces(mesh_name="sf", pair_geom=geom, total_torque_gear1_Nm=1.0, gear1_rel_speed_rpm=1000.0)
    lube = get_gear_lubricant("ISO_VG_220_PAO")
    rat = rate_pair_iso6336(
        mesh_name="sf", pair_geom=geom, gear1_spec=g1, gear2_spec=g2,
        mesh_forces=mf, n1_rel_rpm=1000.0, lubricant=lube, temp_C=70.0,
    )
    assert rat.gear1_rating.Y_S == pytest.approx(1.8159748217, rel=1e-6)
    assert rat.gear1_rating.sigma_H0_MPa == pytest.approx(364.07237653, rel=1e-5)

    # 2. internal_root_gate.json: supplying shaper_cutter_z0=20 must NOT let unimplemented internal root pass!
    r80 = GearSpec(z=80, m_n_mm=1.0, b_mm=10.0, is_internal=True, tooth_thickness_allowance_mm=0.02)
    geom_80 = analyze_pair(g1, r80)
    mf_80 = compute_mesh_forces(mesh_name="int80", pair_geom=geom_80, total_torque_gear1_Nm=1.0, gear1_rel_speed_rpm=1000.0)
    rat_int = rate_pair_iso6336(
        mesh_name="int80", pair_geom=geom_80, gear1_spec=g1, gear2_spec=r80,
        mesh_forces=mf_80, n1_rel_rpm=1000.0, lubricant=lube, temp_C=70.0, shaper_cutter_z0=20,
    )
    assert rat_int.gear2_rating.bending_state == "unsupported"
    assert rat_int.gear2_rating.S_F is None
    assert rat_int.gear2_rating.bending_passed is False
    assert rat_int.passed is False

    # 3. unsafe_ratings.json: zero backlash, thin rim below scope, and out-of-range oil temp all MUST fail!
    g1_0 = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.0)
    g2_0 = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.0)
    rat_zbl = rate_pair_iso6336(
        mesh_name="zbl", pair_geom=analyze_pair(g1_0, g2_0), gear1_spec=g1_0, gear2_spec=g2_0,
        mesh_forces=mf, n1_rel_rpm=1000.0, lubricant=lube, temp_C=70.0,
    )
    assert rat_zbl.passed is False
    assert "zero_operating_backlash_jamming_risk" in rat_zbl.flags

    g2_thin = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02, rim_thickness_mm=0.5)
    rat_rim = rate_pair_iso6336(
        mesh_name="rim", pair_geom=analyze_pair(g1, g2_thin), gear1_spec=g1, gear2_spec=g2_thin,
        mesh_forces=mf, n1_rel_rpm=1000.0, lubricant=lube, temp_C=70.0,
    )
    assert rat_rim.gear2_rating.bending_state == "unsupported"
    assert rat_rim.gear2_rating.S_F is None
    assert rat_rim.passed is False

    rat_hot = rate_pair_iso6336(
        mesh_name="hot", pair_geom=geom, gear1_spec=g1, gear2_spec=g2,
        mesh_forces=mf, n1_rel_rpm=1000.0, lubricant=lube, temp_C=200.0,
    )
    assert rat_hot.lubrication.state == "unsupported"
    assert rat_hot.state == "unsupported"
    assert rat_hot.passed is False


def test_review_lubrication_zl_scuffing_and_thermal_no_root():
    # Source: lubrication_factors.json, lubrication.json
    # 1. 2019 Z_L: C_ZL=.91, nominal nu40=220, denominator 1.2+134/220.
    oil = get_gear_lubricant("ISO_VG_220_PAO")
    g1 = GearSpec(z=20, m_n_mm=2.0, b_mm=16.0, Ra_flank_um=0.8, Rz_flank_um=4.0)
    g2 = GearSpec(z=40, m_n_mm=2.0, b_mm=16.0, Ra_flank_um=0.8, Rz_flank_um=4.0)
    geom = analyze_pair(g1, g2)
    lres = evaluate_mesh_lubrication(
        pair_geom=geom, lubricant=oil, temp_C=70.0, pitch_velocity_m_s=5.0,
        F_n_N=1200.0, b_eff_mm=16.0, sigma_Hlim_MPa=1200.0,
        Rz1_um=4.0, Rz2_um=4.0,
    )
    assert lres.nu_mm2_s == pytest.approx(62.87860575, rel=1e-7)
    assert lres.Z_L == pytest.approx(.91 + 4.*.09/(1.2+134./220.)**2, rel=1e-12)
    assert lres.Z_v == pytest.approx(0.9821749195, rel=1e-7)
    assert lres.Z_R == pytest.approx(0.9569986217, rel=1e-7)

    # 2. Scuffing pinion-tip surface & sliding velocities:
    # v1 = 5 * sqrt(22^2 - (20 cos 20)^2) / 20 = 2.85909854 m/s
    # v2 = 5 * (60 sin 20 - 11.4363942) / 40 = 1.13560180 m/s => |v1 - v2| = 1.72349673 m/s
    ev = lres.applicability_evidence
    assert ev["v1_tip_m_s"] == pytest.approx(2.8590985399, rel=1e-7)
    assert ev["v2_tip_m_s"] == pytest.approx(1.1356018050, rel=1e-7)
    assert ev["v_slide_tip_m_s"] == pytest.approx(1.7234967349, rel=1e-7)

    # 3. Thermal equation with no root above T_amb=25 C: 1000 + T = 0.75*(T - 25) => converged=False, failed!
    grease = get_gear_lubricant("ROBOT_GREASE_NLGI1_PAO")
    no_root = solve_grease_thermal_balance(
        lubricant=grease, loss_fn_W=lambda t: 1000.0 + t, ambient_temp_C=25.0,
        housing_area_m2=0.05, h_conv_W_m2K=15.0,
    )
    assert no_root.converged is False
    assert no_root.state == "failed"
    assert no_root.within_lubricant_limit is False
    assert no_root.residual_W > 1.0


def test_review_bearing_gates_parts_catalogue_and_spectrum_invariance():
    # Source: bearings.json, parts.json, catalogue.json, spectrum.json
    import json

    # 1. bearings.json:
    # (a) Inline bearing with Cu_kn where L10h=166667 >= 50000 but L10mh < 50000 MUST fail!
    # Check exact ISO 281 formula: nu1 = 45000 * 100^-0.83 * 31^-0.5; kappa = nu/nu1;
    # at nu_op=20: a_ISO = 0.12076280256 -> L10mh = 20127.133760 h; at nu_op=10: kappa=0.1 -> a_ISO ~ 0.1 -> 16666.67 h.
    ball = GearBearingSpec(name="inline_b", bearing_type="deep_groove", d_mm=20.0, D_mm=42.0, B_mm=12.0, C_kn=10.0, C0_kn=5.0, Cu_kn=0.4)
    res_mod_10 = evaluate_bearing(
        ball, role="test", F_r_N=1000.0, F_a_N=0.0, speed_rel_rpm=100.0,
        nu_op_mm2_s=10.0, required_life_h=50000.0,
    )
    res_mod_20 = evaluate_bearing(
        ball, role="test", F_r_N=1000.0, F_a_N=0.0, speed_rel_rpm=100.0,
        nu_op_mm2_s=20.0, required_life_h=50000.0,
    )
    assert res_mod_10.L10h_h == pytest.approx(166666.666667, rel=1e-6)
    assert res_mod_10.L10mh_h < 50000.0
    assert res_mod_10.passed is False
    assert res_mod_10.state == "failed"
    assert res_mod_20.L10mh_h == pytest.approx(20127.133760323388, rel=1e-6)
    assert res_mod_20.passed is False

    # (b) Below minimum bearing load (Fr=10 N < 1% of C0=5000 N = 50 N) MUST fail!
    res_minl = evaluate_bearing(ball, role="test", F_r_N=10.0, F_a_N=0.0, speed_rel_rpm=100.0, required_life_h=100.0)
    assert res_minl.min_load_ok is False
    assert res_minl.passed is False

    # (c) Overspeed in one spectrum bin MUST make spectrum fail even if total life exceeds requirement
    lim_brg = GearBearingSpec(
        name="lim", bearing_type="deep_groove", d_mm=20.0, D_mm=42.0, B_mm=12.0,
        C_kn=10.0, C0_kn=5.0, Cu_kn=0.4, n_limit_rpm=5000.0,
    )
    spec_ov = evaluate_bearing_spectrum(
        lim_brg, role="in", bins=[(200.0, 0.0, 6000.0, 10.0), (200.0, 0.0, 1000.0, 100.0)],
        required_life_h=100.0,
    )
    assert spec_ov.bin_results[0].speed_limit_ok is False
    assert spec_ov.passed is False

    # 2. parts.json:
    # (a) Pin F=1000 N, span=12 mm, seat=2 mm, d=8 mm => M = 1000*(24-2)/8 = 2750 N*mm => sigma = 54.709512 MPa
    pin_2 = rate_planet_pin(
        PlanetPinSpec(diameter_mm=8.0, span_mm=12.0, seat_length_mm=2.0, uts_MPa=1000.0, yield_strength_MPa=800.0),
        F_pin_duty_N=1000.0,
    )
    assert pin_2.stress_summary["sigma_b_duty_MPa"] == pytest.approx(54.7095116878, rel=1e-6)

    # (b) Single-plate carrier (n_plates=1) carries full post load without 0.5 plate split => 41.666667 MPa
    car_1 = rate_carrier(
        CarrierSpec(pin_diameter_mm=8.0, plate_thickness_mm=5.0, n_plates=1, n_posts=3, arm_width_mm=12.0, arm_thickness_mm=10.0, arm_length_mm=15.0),
        F_pin_worst_duty_N=1000.0, T_carrier_duty_Nm=50.0, orbit_radius_mm=25.0,
    )
    assert car_1.stress_summary["sigma_arm_duty_MPa"] == pytest.approx(41.6666666667, rel=1e-6)

    # (c) Simplified DIN 743 shaft carries state="estimated", never "valid"
    sh = rate_shaft_din743(ShaftSectionSpec(name="s", diameter_mm=20.0), torque_duty_Nm=50.0)
    assert sh.is_simplified is True
    assert sh.state == "estimated"

    # 3. catalogue.json: backdrivable=False MUST fail humanoid_robot_joint, and missing exponent marks "estimated"
    cat_non_bd = CatalogueGearboxSpec(
        name="audit", ratio=20.0, efficiency=0.9, rated_torque_out_Nm=20.0, peak_torque_out_Nm=45.0,
        rated_speed_in_max_rpm=4000.0, rated_life_h=10000.0, rated_speed_out_rpm=100.0,
        backlash_arcmin=5.0, backdrivable=False,
    )
    cat_res_bd = check_catalogue_gearbox(
        cat_non_bd, DutySpectrum(bins=[DutyBin(2.0, 100.0, 100.0)]), preset_name="humanoid_robot_joint",
    )
    assert cat_res_bd.passed is False
    assert cat_res_bd.state == "failed"

    cat_exp = check_catalogue_gearbox(
        cat_non_bd, DutySpectrum(bins=[DutyBin(2.0, 100.0, 100.0)]), preset_name="standard",
    )
    assert "torque_life_exponent" in cat_exp.missing_inputs
    assert cat_exp.state == "estimated"

    # 4. spectrum.json: non-vacuous finite-life bin-order invariance, signed regeneration,
    # deduplicated tooth part names, strict JSON (m_n=1.0, b=10.0 at 8 N*m yields finite life)
    st_p = PairStageSpec(
        name="p",
        gear1=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        gear2=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
    )
    gb_p = Gearbox([st_p])
    r_ab = gb_p.evaluate_spectrum(DutySpectrum(bins=[DutyBin(8.0, 100.0, 1.0), DutyBin(8.0, 1000.0, 1.0)]))
    r_ba = gb_p.evaluate_spectrum(DutySpectrum(bins=[DutyBin(8.0, 1000.0, 1.0), DutyBin(8.0, 100.0, 1.0)]))
    assert r_ab.min_gear_life_h is not None
    # Independent S-N inversion, conditional on rated stress; 2019 Z_L changes
    # the prior hybrid-edition life 4030.069226 h. No production capacity call.
    damage = 0.
    zl = .91 + .36/(1.2+134./220.)**2
    for speed in (100., 1000.):
        rating = gb_p.evaluate_point(input_speed_rpm=-2.*speed, torque_Nm=8., torque_at="output").stage_results[0].pair_ratings["main_mesh"].gear1_rating
        zreq = rating.sigma_H_MPa / (1500.*zl*rating.Z_v*rating.Z_R)
        capacity = 5e7 * (1e10/5e7)**(math.log(zreq)/math.log(.85))
        damage += 60.*2.*speed/capacity
    assert r_ab.min_gear_life_h == pytest.approx(2./damage, rel=1e-10)
    assert r_ab.output_torque_Nm == pytest.approx(r_ba.output_torque_Nm, abs=1e-12)
    assert r_ab.min_gear_life_h == pytest.approx(r_ba.min_gear_life_h, rel=1e-12)
    assert len(r_ab.parts_summary.parts) == 4  # deduplicated across the 2 bins
    assert len({p.part_name for p in r_ab.parts_summary.parts}) == 4
    json.dumps(r_ab.to_dict(), allow_nan=False)

    # Signed regeneration: DutyBin(torque_out_Nm=-8.0, speed_out_rpm=100.0) keeps output_torque_Nm == -8.0 N*m!
    r_reg = gb_p.evaluate_spectrum(DutySpectrum(bins=[DutyBin(-8.0, 100.0, 1.0)]))
    assert r_reg.output_torque_Nm == pytest.approx(-8.0, abs=1e-9)
    assert r_reg.power_in_W > r_reg.power_out_useful_W > 0.0


def test_r2_blockers_iso6336_parts_lube_compound_and_validation():
    # Independent R2 checks covering B1, B2, B4, B5, B7, and follow-ups.
    import json
    from motor_ai_sim.gears import compute_iso6336_load_factors, rate_key_or_spline

    lube = get_gear_lubricant("ISO_VG_220_mineral")

    # --- B1 (ISO 6336-1/-2): K_Halpha/K_Falpha caps, K_Fbeta b/h>=3, K_v N_s & cv4, Z_beta 2019, estimated state ---
    g1_12 = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, accuracy_grade=12)
    g2_12 = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, accuracy_grade=12)
    geom_12 = analyze_pair(g1_12, g2_12)
    mf_12 = compute_mesh_forces(mesh_name="m", pair_geom=geom_12, total_torque_gear1_Nm=1.0, gear1_rel_speed_rpm=100.0)
    lf_12 = compute_iso6336_load_factors(pair_geom=geom_12, mesh_forces=mf_12, gear1_spec=g1_12, gear2_spec=g2_12, n1_rel_rpm=100.0)
    # Hand oracle: ea=eg=1.635186, eb=0 => Z_eps^2=(4-ea)/3 => K_Halpha_cap=1/Z_eps^2=1.268598695; K_Falpha_cap=ea/(0.25*ea+0.75)=1.411107107
    assert lf_12.K_Halpha == pytest.approx(1.268598694775466, rel=1e-9)
    assert lf_12.K_Falpha == pytest.approx(1.4111071067461594, rel=1e-9)

    # Even with explicit f_ma/f_pt/f_pb, placeholder f_sh/Bk=0.2/m_red marks state="estimated"
    lf_exp = compute_iso6336_load_factors(
        pair_geom=geom_12, mesh_forces=mf_12, gear1_spec=g1_12, gear2_spec=g2_12,
        n1_rel_rpm=100.0, f_ma_um=5.0, f_pt_um=10.0, f_pb_um=10.0,
    )
    assert lf_exp.state == "estimated"
    assert "simplified:placeholder_shaft_bending_f_sh" in lf_exp.flags

    # Narrow face width b=1 mm, h=2.25 mm => b/h clamped to ISO 6336-1 min 3.0 => n_f = 9/13
    g1_n = GearSpec(z=20, m_n_mm=1.0, b_mm=1.0, accuracy_grade=12)
    g2_n = GearSpec(z=40, m_n_mm=1.0, b_mm=1.0, accuracy_grade=12)
    geom_n = analyze_pair(g1_n, g2_n)
    mf_n = compute_mesh_forces(mesh_name="n", pair_geom=geom_n, total_torque_gear1_Nm=5.0, gear1_rel_speed_rpm=100.0)
    lf_n = compute_iso6336_load_factors(pair_geom=geom_n, mesh_forces=mf_n, gear1_spec=g1_n, gear2_spec=g2_n, n1_rel_rpm=100.0)
    assert lf_n.K_Fbeta == pytest.approx(lf_n.K_Hbeta ** (9.0 / 13.0), rel=1e-12)

    # Dynamic K_v: verify exact N_s = 0.5 + 0.35*sqrt(f_m/100), subcritical K_v = 1 + c_v_k*N, and cv4 in main resonance
    mf_dyn = compute_mesh_forces(mesh_name="d", pair_geom=geom_12, total_torque_gear1_Nm=1.0, gear1_rel_speed_rpm=100.0)
    lf_sub = compute_iso6336_load_factors(
        pair_geom=geom_12, mesh_forces=mf_dyn, gear1_spec=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, accuracy_grade=10),
        gear2_spec=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, accuracy_grade=10), n1_rel_rpm=1000.0,
    )
    # F_m/b = 100 / 10 = 10 N/mm => N_s = 0.5 + 0.35*sqrt(10/100) = 0.6106797181
    assert lf_sub.applicability_evidence["N_s"] == pytest.approx(0.5 + 0.35 * math.sqrt(0.1), rel=1e-12)
    # Main resonance at N = 0.90 (between N_s and 1.15) uses cv4=0.90 (NOT cv3=0.23) for eg<=2
    n_e1_d = lf_sub.applicability_evidence["n_E1_rpm"]
    lf_res = compute_iso6336_load_factors(
        pair_geom=geom_12, mesh_forces=mf_dyn, gear1_spec=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, accuracy_grade=10),
        gear2_spec=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, accuracy_grade=10), n1_rel_rpm=0.90 * n_e1_d,
    )
    b_pf = (lf_res.c_prime_N_mm_um * max(max(lf_res.f_pb_um * 0.9, 1.0), 0.0)) / 10.0
    assert lf_res.K_v == pytest.approx(1.0 + 0.32 * b_pf + 0.34 * b_pf + 0.90 * 0.2, rel=1e-9)

    # ISO 6336-2:2019 Z_beta = 1/sqrt(cos beta)
    g1_h = GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, beta_deg=30.0, hand="right")
    g2_h = GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, beta_deg=30.0, hand="left")
    gh = analyze_pair(g1_h, g2_h)
    mf_h = compute_mesh_forces(mesh_name="h", pair_geom=gh, total_torque_gear1_Nm=5.0, gear1_rel_speed_rpm=1000.0)
    r_h = rate_pair_iso6336(mesh_name="h", pair_geom=gh, gear1_spec=g1_h, gear2_spec=g2_h, mesh_forces=mf_h, n1_rel_rpm=1000.0, lubricant=lube, temp_C=70.0)
    assert r_h.gear1_rating.Z_beta == pytest.approx(1.0 / math.sqrt(math.cos(math.radians(30.0))), rel=1e-12)
    assert "ISO 6336-1/-2/-3:2019" in r_h.applicability_evidence["iso_edition"]

    # --- B2: Pair shaft bending & axial force, two-bearing whole-pin load ---
    g1_s = GearSpec(z=20, m_n_mm=1.5, b_mm=12.0, tooth_thickness_allowance_mm=0.02)
    g2_s = GearSpec(z=40, m_n_mm=1.5, b_mm=12.0, tooth_thickness_allowance_mm=0.02)
    st_sh = PairStageSpec(
        name="ps", gear1=g1_s, gear2=g2_s,
        shaft1_section=ShaftSectionSpec(name="in_shaft", diameter_mm=4.0),
        shaft1_span_mm=100.0, shaft1_offset_mm=50.0,
    )
    ev_sh = Gearbox([st_sh]).evaluate_spectrum(DutySpectrum(bins=[DutyBin(3.0, 100.0, 1.0)]))
    sh_rec = next(p for p in ev_sh.parts_summary.parts if p.part_name == "in_shaft")
    w_mf = ev_sh.stage_results[0].mesh_forces["main_mesh"]
    m_b_exp = math.hypot(w_mf.F_tw_N, w_mf.F_r_N) * 0.025  # a*b/(L*1000) = 50*50/(100*1000) = 0.025 m
    sig_ba_exp = m_b_exp * 1000.0 / (math.pi * (4.0 ** 3) / 32.0)
    assert sh_rec.stress_summary["sigma_ba_MPa"] == pytest.approx(sig_ba_exp, rel=1e-6)
    assert sh_rec.passed is False and sh_rec.state == "failed"

    st_2brg = PlanetaryStageSpec(
        name="p2b",
        sun=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        planet=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        ring=GearSpec(z=80, m_n_mm=1.0, b_mm=10.0, is_internal=True, tooth_thickness_allowance_mm=0.02),
        n_planets=3,
        n_bearings_per_planet=2,
        planet_bearing=GearBearingSpec(name="pb", bearing_type="needle", d_mm=8.0, D_mm=12.0, B_mm=4.0, C_kn=2.5, C0_kn=2.2, Cu_kn=0.25),
        planet_pin=PlanetPinSpec(diameter_mm=8.0, span_mm=12.0, seat_length_mm=8.0),
    )
    ev_2b = Gearbox([st_2brg]).evaluate_spectrum(DutySpectrum(bins=[DutyBin(3.0, 100.0, 10.0)]))
    pin_rec_2b = next(p for p in ev_2b.parts_summary.parts if p.part_name == "planet_pin")
    f_pin_whole = ev_2b.stage_results[0].bearing_loads[0].applicability_evidence["worst_branch_pin_force_N"]
    # Two-bearing bore divides single-bearing F_r_N by 2, while whole-pin force is ~2 * F_r_N
    assert f_pin_whole == pytest.approx(2.0 * ev_2b.stage_results[0].bearing_loads[0].F_r_worst_branch_N, rel=1e-12)
    sig_pin_exp = (f_pin_whole * (2.0 * 12.0 - 8.0) / 8.0) / (math.pi * (8.0 ** 3) / 32.0)
    assert pin_rec_2b.stress_summary["sigma_b_duty_MPa"] == pytest.approx(sig_pin_exp, rel=1e-6)

    # --- B4: Separate compound planet1/planet2 Miner, planetary in/out bearings, invalid geom life ---
    st_comp = PlanetaryStageSpec(
        name="cmp",
        sun=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02),
        planet=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02, material_name="18CrNiMo7-6_case_carburised_MQ"),
        planet2=GearSpec(z=30, m_n_mm=1.0, b_mm=10.0, tooth_thickness_allowance_mm=0.02, material_name="C45_normalized_ML"),
        ring=GearSpec(z=90, m_n_mm=1.0, b_mm=10.0, is_internal=True, tooth_thickness_allowance_mm=0.02),
        n_planets=3,
        input_bearing=GearBearingSpec(name="inb", bearing_type="deep_groove", d_mm=15.0, D_mm=32.0, B_mm=9.0, C_kn=5.6, C0_kn=2.8, Cu_kn=0.12),
        output_bearing=GearBearingSpec(name="outb", bearing_type="deep_groove", d_mm=20.0, D_mm=42.0, B_mm=12.0, C_kn=9.4, C0_kn=5.0, Cu_kn=0.21),
    )
    ev_cmp = Gearbox([st_comp]).evaluate_spectrum(DutySpectrum(bins=[DutyBin(20.0, 200.0, 100.0)]))
    miner_targets = {m.target_name: m for m in ev_cmp.gear_miner_lives}
    assert "cmp.planet1" in miner_targets and "cmp.planet2" in miner_targets
    p2_h = min(m.predicted_life_hours for m in ev_cmp.gear_miner_lives if m.target_name == "cmp.planet2" and m.predicted_life_hours)
    p1_h_list = [m.predicted_life_hours for m in ev_cmp.gear_miner_lives if m.target_name == "cmp.planet1" and m.predicted_life_hours]
    if p1_h_list:
        assert p2_h < min(p1_h_list)
    brg_roles = {b.role for b in ev_cmp.bearing_spectrum_lives}
    assert "cmp.input_bearing" in brg_roles and "cmp.output_bearing" in brg_roles

    # B7: Compound compliance, backlash, and dual axial envelope (40/20/30/90, q=1.5)
    assert ev_cmp.torsional_stiffness_out_Nm_rad == pytest.approx(927285.7507777407, rel=1e-6)
    assert ev_cmp.backlash_out_deg == pytest.approx(0.07639437268410976, rel=1e-6)
    assert ev_cmp.envelope_length_mm == pytest.approx(20.0, abs=1e-9)

    # B4: Invalid geometry (same-hand external helical) MUST never claim finite valid gear life or pass preset!
    st_bad_h = PairStageSpec(
        name="badh",
        gear1=GearSpec(z=20, m_n_mm=1.0, b_mm=10.0, beta_deg=20.0, hand="right"),
        gear2=GearSpec(z=40, m_n_mm=1.0, b_mm=10.0, beta_deg=20.0, hand="right"),
    )
    ev_bad_h = Gearbox([st_bad_h]).evaluate_spectrum(DutySpectrum(bins=[DutyBin(15.0, 100.0, 1.0)]))
    assert ev_bad_h.min_gear_life_h is None
    assert all(m.predicted_life_hours is None and m.state == "failed" for m in ev_bad_h.gear_miner_lives)
    life_chk = next(c for c in ev_bad_h.preset_evaluation.checks if c.criterion == "gear_life_h")
    assert life_chk.passed is False
    assert ev_bad_h.preset_evaluation.all_passed is False
    json.dumps(ev_bad_h.to_dict(), allow_nan=False)

    # --- B5 & follow-ups: Input validation, negative Fa, missing reverse_eff, thin rim, thermal runaway ---
    with pytest.raises(ValueError):
        CatalogueGearboxSpec(name="b1", ratio=0.0, efficiency=0.9, rated_torque_out_Nm=10.0, peak_torque_out_Nm=20.0, rated_speed_in_max_rpm=1000.0, rated_life_h=1000.0, rated_speed_out_rpm=100.0)
    with pytest.raises(ValueError):
        CatalogueGearboxSpec(name="b2", ratio=10.0, efficiency=1.5, rated_torque_out_Nm=10.0, peak_torque_out_Nm=20.0, rated_speed_in_max_rpm=1000.0, rated_life_h=1000.0, rated_speed_out_rpm=100.0)
    with pytest.raises(ValueError):
        DutyBin(torque_out_Nm=5.0, speed_out_rpm=100.0, duration_h=-5.0)
    with pytest.raises(ValueError):
        DutyBin(torque_out_Nm=float("nan"), speed_out_rpm=100.0, duration_h=1.0)
    with pytest.raises(ValueError):
        GearBearingSpec(name="bad_t", bearing_type="unknown_type")
    with pytest.raises(ValueError):
        evaluate_bearing(GearBearingSpec(name="ok"), role="r", F_r_N=-10.0, F_a_N=0.0, speed_rel_rpm=100.0)

    # Negative axial force on roller/needle bearing is unsupported (never clamped silently to 0 and passed)
    nd = GearBearingSpec(name="nd", bearing_type="needle", d_mm=12.0, D_mm=18.0, B_mm=12.0, C_kn=10.0, C0_kn=8.0, Cu_kn=1.0)
    res_nd_neg_fa = evaluate_bearing(nd, role="r", F_r_N=1000.0, F_a_N=-50.0, speed_rel_rpm=100.0)
    assert res_nd_neg_fa.state == "unsupported" and res_nd_neg_fa.passed is False

    # Catalogue with backdrivable=True but reverse_efficiency=None MUST be unsupported on humanoid_robot_joint
    cat_no_rev = CatalogueGearboxSpec(
        name="norev", ratio=10.0, efficiency=0.92, rated_torque_out_Nm=20.0,
        peak_torque_out_Nm=45.0, rated_speed_in_max_rpm=4000.0,
        rated_life_h=10000.0, rated_speed_out_rpm=100.0, backlash_arcmin=5.0,
        backdrivable=True, reverse_efficiency=None,
    )
    cat_no_rev_res = check_catalogue_gearbox(
        cat_no_rev, DutySpectrum(bins=[DutyBin(5.0, 100.0, 100.0)]), preset_name="humanoid_robot_joint",
    )
    assert cat_no_rev_res.state == "unsupported" and cat_no_rev_res.passed is False

    # Thin ring rim <= 1.75 * m_n MUST be unsupported & passed=False (never passed=True)
    thin_rim, _ = rate_ring_rim_and_fixation(
        RingRimFixationSpec(d_f_ring_mm=54.5, rim_thickness_mm=0.5, b_ring_mm=10.0, m_n_mm=1.0),
        F_t_ring_branch_N=100.0, F_r_ring_branch_N=40.0, T_ring_duty_Nm=5.0,
    )
    assert thin_rim.state == "unsupported" and thin_rim.passed is False
    k_rec = rate_key_or_spline(
        KeyOrSplineSpec(name="k", kind="key", pitch_or_shaft_diameter_mm=20.0, effective_length_mm=20.0),
        torque_duty_Nm=20.0,
    )
    assert k_rec.is_simplified is True and k_rec.state == "estimated"

    # Runaway thermal balance MUST return equilibrium_temp_C=None, state="failed", valid JSON
    grease = get_gear_lubricant("ROBOT_GREASE_NLGI1_PAO")
    no_root_tb = solve_grease_thermal_balance(
        lubricant=grease, loss_fn_W=lambda t: 1000.0 + t, ambient_temp_C=25.0, housing_area_m2=0.05, h_conv_W_m2K=15.0,
    )
    assert no_root_tb.converged is False
    assert no_root_tb.state == "failed"
    assert no_root_tb.equilibrium_temp_C is None
    assert no_root_tb.nu_at_equilibrium_mm2_s is None
    json.dumps(no_root_tb.to_dict(), allow_nan=False)
