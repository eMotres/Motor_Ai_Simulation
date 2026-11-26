"""Round-3 counterexamples: independent arithmetic, explicit validity gates."""
import math
from dataclasses import replace
import pytest
from motor_ai_sim.gears import *


def gear(z, **kwargs):
    return GearSpec(z=z, m_n_mm=1., b_mm=kwargs.pop("b_mm", 10.),
                    tooth_thickness_allowance_mm=.04, **kwargs)


def test_compound_unequal_width_planes():
    a = build_planetary_assembly(sun=gear(40, b_mm=30.), planet=gear(20),
                                planet2=gear(30), ring=gear(90, is_internal=True))
    for angle in (0., 21.):
        for m in a.pose_at(angle).values():
            assert m.center_mm[2] == (30. if m.role in ("ring", "planet2") else 0.)


def test_resolved_shift_rating_and_identity():
    from motor_ai_sim.gears.rating_iso6336 import rate_pair_iso6336
    from motor_ai_sim.gears.loads import compute_mesh_forces
    a, b = gear(20), gear(40)
    pg = analyze_pair(a, b, centre_distance_mm=31.)
    # Independent involute inversion from aw=31, alpha=20deg and x1=x2.
    alpha = math.radians(20.)
    aw = math.acos(30. * math.cos(alpha) / 31.)
    x = 60. * ((math.tan(aw)-aw)-(math.tan(alpha)-alpha)) / (4.*math.tan(alpha))
    assert x == pytest.approx(.5566927449137862)
    mf = compute_mesh_forces(mesh_name="test", pair_geom=pg, total_torque_gear1_Nm=1., gear1_rel_speed_rpm=1000.)
    kw = dict(mesh_name="test", pair_geom=pg, mesh_forces=mf, n1_rel_rpm=1000.,
              lubricant=get_gear_lubricant("ISO_VG_220_PAO"))
    r = rate_pair_iso6336(gear1_spec=a, gear2_spec=b, **kw)
    explicit = rate_pair_iso6336(gear1_spec=a.with_x(x), gear2_spec=b.with_x(x), **kw)
    for member in ("gear1_rating", "gear2_rating"):
        for key in ("Y_F", "Y_S", "sigma_F_MPa"):
            assert getattr(getattr(r, member), key) == pytest.approx(getattr(getattr(explicit, member), key))
    with pytest.raises(ValueError):
        rate_pair_iso6336(gear1_spec=gear(21), gear2_spec=b, **kw)
    assert (a.x, b.x) == (0., 0.)


def test_miner_missing_and_infinite_capacity():
    from motor_ai_sim.gears.rating_iso6336 import miner_damage_from_cycles
    kw = dict(target_name="test", block_hours=1.)
    with pytest.raises(ValueError):
        miner_damage_from_cycles(bins=[(100., -math.inf), (100., 1000.)], **kw)
    missing = miner_damage_from_cycles(bins=[(100., None), (100., 1000.)], **kw)
    assert missing.state == "unsupported" and missing.predicted_life_hours is None
    assert missing.damage_D is None
    endurance = miner_damage_from_cycles(bins=[(100., math.inf), (100., 1000.)], **kw)
    assert endurance.damage_D == .1 and endurance.predicted_life_hours == 10.


@pytest.mark.parametrize("bad", [-1., float("nan"), float("inf"), "2", True])
def test_peak_and_force_boundaries(bad):
    from motor_ai_sim.gears.parts import CarrierSpec, rate_carrier, RingRimFixationSpec
    with pytest.raises(ValueError):
        DutySpectrum([DutyBin(2., 100., 1.)], peak_torque_out_Nm=bad)
    with pytest.raises(ValueError):
        rate_carrier(CarrierSpec(8., 5.), F_pin_worst_duty_N=bad, T_carrier_duty_Nm=0., orbit_radius_mm=25.)
    with pytest.raises(ValueError):
        CarrierSpec(8., 5., yield_strength_MPa=bad)
    with pytest.raises(ValueError):
        RingRimFixationSpec(80., 5., 10., 1., fastener_shear_allow_MPa=bad)


def test_stage_and_spectrum_identity_guards():
    for bad in (True, 1.5, float("nan"), -1):
        with pytest.raises(ValueError): DutySpectrum([DutyBin(2., 100., 1.)], peak_events=bad)
    with pytest.raises(ValueError): DutySpectrum([DutyBin(2., 100., 0.)])
    a = PairStageSpec("a", gear(20), gear(40))
    with pytest.raises(ValueError): Gearbox([a, a])
    for bad in (float("nan"), "1.2", .9):
        with pytest.raises(ValueError): PlanetaryStageSpec("p", gear(40), gear(20), gear(80, is_internal=True), k_gamma=bad)
    with pytest.raises(ValueError): PairStageSpec("a", gear(20), gear(40), shaft1_span_mm=-10.)


def test_assembly_invalidity_reaches_all_miner_lives():
    stage = PlanetaryStageSpec("bad", gear(40), gear(20), gear(82, is_internal=True))
    r = Gearbox([stage]).evaluate_spectrum(DutySpectrum([DutyBin(60., 100., 1.)]))
    assert r.stage_results[0].kinematics.coaxiality_error_mm == pytest.approx(1.)
    assert r.min_gear_life_h is None
    assert all(m.state == "failed" and m.predicted_life_hours is None for m in r.gear_miner_lives)
    assert any(m.target_name == "bad.ring" and m.mechanism == "bending_root" for m in r.gear_miner_lives)


def test_missing_support_and_invalid_ir_gates():
    from motor_ai_sim.gears.parts import ShaftSectionSpec
    st = PairStageSpec("p", gear(20), gear(40), shaft1_section=ShaftSectionSpec("shaft", 4.))
    r = Gearbox([st]).evaluate_spectrum(DutySpectrum([DutyBin(3.,100.,1.)]))
    shaft = next(p for p in r.parts_summary.parts if p.part_name == "shaft")
    assert shaft.state == "unsupported" and not shaft.passed
    bad = PairStageSpec("bad", gear(20,beta_deg=15.), gear(40,beta_deg=15.))
    with pytest.raises(ValueError, match="geometry"):
        Gearbox([bad]).to_transmission_ir(n_speed_points=2,n_torque_points=2)


@pytest.mark.parametrize("grade,expected", [(4, .8734754760982049), (6, 1.)])
def test_deep_tooth_factor_precision_condition(grade, expected):
    # Independent spur contact ratio from tip/base circle path length.
    alpha = math.radians(20.)
    ea = (math.sqrt(21.3**2-(20.*math.cos(alpha))**2)
          + math.sqrt(41.3**2-(40.*math.cos(alpha))**2)
          - 60.*math.sin(alpha)) / (math.pi*math.cos(alpha))
    correct = 2.366-.666*ea if grade <= 4 else 1.
    assert correct == pytest.approx(expected)
    a = gear(40,ha_star=1.3,hf_star=1.55,accuracy_grade=grade)
    b = gear(80,ha_star=1.3,hf_star=1.55,accuracy_grade=grade)
    r = Gearbox([PairStageSpec("deep",a,b)]).evaluate_point(input_speed_rpm=1000.,torque_Nm=1.)
    assert r.stage_results[0].pair_ratings["main_mesh"].gear1_rating.Y_DT == pytest.approx(correct)


@pytest.mark.parametrize("known_torque", [1., -1.])
def test_planet_bearing_local_equilibrium_independent(known_torque):
    from motor_ai_sim.gears.planetary_equilibrium import solve_local_planetary
    # Fixed ring: ws=3, wc=1, wp-wc=-4, eps=.98, epr=.97, B=.12 W.
    # Forward Tr=2*eps*epr*Ts-epr*B. Reverse Tr=2*Ts/(eps*epr)-B/epr.
    r = solve_local_planetary(u=2., eta_sp=.98, eta_pr=.97,
        omegas={"sun":3.,"ring":0.,"carrier":1.}, planet_rel_rad_s=-4.,
        fixed_member="ring", known_member="sun", known_torque_Nm=known_torque,
        planet_bearing_power_W=.12)
    expected_tr = (2*.98*.97*known_torque-.97*.12 if known_torque>0
                   else 2*known_torque/(.98*.97)-.12/.97)
    assert r.member_torques_Nm["sun"] == known_torque
    assert r.member_torques_Nm["ring"] == pytest.approx(expected_tr)
    assert r.member_torques_Nm["carrier"] == pytest.approx(-known_torque-expected_tr)
    e = r.applicability_evidence
    assert e["planet_bearing_torque_on_planets_Nm"] == .03
    assert e["planet_bearing_torque_on_carrier_Nm"] == -.03
    assert e["planet_equilibrium_residual_Nm"] == pytest.approx(0.,abs=1e-14)
    assert e["power_balance_residual_W"] == pytest.approx(0.,abs=1e-14)


def test_coupled_planet_bearings_and_nonconvergence(monkeypatch):
    import motor_ai_sim.gears.gearbox as module
    b = GearBearingSpec("needle", "needle", 8., 12., 4., 2.5, 2.2, Cu_kn=.25)
    st = PlanetaryStageSpec("p",gear(40),gear(20),gear(80,is_internal=True),planet_bearing=b)
    for torque in (1., -1.):
        r = evaluate_stage(st,input_speed_rpm=1000.,known_torque_Nm=torque,
                           lubricant=get_gear_lubricant("ISO_VG_220_mineral"))
        pf = r.planetary_power_flow
        e = pf.applicability_evidence
        assert e["converged"] and e["relative_iteration_residual"] <= 1.e-11
        assert e["mesh_torques_Nm"]["sun"] == pytest.approx(torque, abs=1e-12)
        psp = r.mesh_losses["sun_planet"]
        ppr = r.mesh_losses["planet_ring"]
        # Independent local power balance from stored physical mesh powers.
        residual = (psp.transmitted_power_rel_W-psp.P_VZ_W-ppr.transmitted_power_rel_W
                    if torque>0 else ppr.transmitted_power_rel_W-ppr.P_VZ_W-psp.transmitted_power_rel_W)
        assert residual == pytest.approx(r.power_loss_bearings_W,abs=1e-9)
    monkeypatch.setattr(module,"_PLANETARY_MAX_ITER",1)
    bad = evaluate_stage(st,input_speed_rpm=1000.,known_torque_Nm=1.,lubricant=get_gear_lubricant("ISO_VG_220_mineral"))
    assert bad.state == "failed" and bad.efficiency is None
    assert not bad.planetary_power_flow.applicability_evidence["converged"]


def test_translated_envelope_and_stable_identity():
    a,b=PairStageSpec("a",gear(20),gear(40)),PairStageSpec("b",gear(20),gear(40))
    r=Gearbox([a,b]).evaluate_point(input_speed_rpm=1000.,torque_Nm=1.)
    # Centres 0,30,30,60; external addendum radii 11,21,11,21.
    assert r.envelope_diameter_mm == pytest.approx(81.-(-11.))
    assert r.envelope_length_mm == 25.  # 10+5 gap+10
    before=build_chain_assembly([a,b]); a.name="renamed"
    after=build_chain_assembly([b,a])
    assert {m.member_id for m in before.members} == {m.member_id for m in after.members}
    assert len({m.member_id for m in before.members}) == 4
    with pytest.raises(ValueError): Gearbox([a,replace(b,stage_id=a.stage_id)])


def test_unsupported_needle_life_is_unavailable():
    b=GearBearingSpec("n","needle",8.,12.,4.,2.5,2.2,Cu_kn=.25)
    r=evaluate_bearing(b,role="test",F_r_N=100.,F_a_N=10.,speed_rel_rpm=1000.)
    assert r.state=="unsupported" and r.L10h_h is None and r.L10mh_h is None
    r=evaluate_bearing_spectrum(b,role="test",bins=[(100.,0.,1000.,1.),(100.,10.,1000.,1.)])
    assert r.L10_spectrum_h is None and r.L10m_spectrum_h is None


@pytest.mark.parametrize("operation", ["point", "spectrum", "assembly"])
def test_versioned_request_result_roundtrip(operation):
    import json
    stage=PairStageSpec("p",gear(20),gear(40))
    request=GearboxRequest([stage],operation=operation,
        spectrum=DutySpectrum([DutyBin(8.,100.,1.)]) if operation=="spectrum" else None)
    data=json.loads(json.dumps(request.to_dict(),allow_nan=False))
    decoded=GearboxRequest.from_dict(data)
    assert decoded.to_dict()==data
    assert isinstance(decoded.stages[0].gear1,GearSpec)
    response=decoded.execute()
    payload=json.loads(json.dumps(response.to_dict(),allow_nan=False))
    restored=GearboxResponse.from_dict(payload)
    assert restored.to_dict()==payload
    assert payload["schema_version"]==1
    assert payload["result"]["metric_availability"]


def test_strict_decoder_rejects_nested_unknowns_types_and_versions():
    import copy
    request=GearboxRequest([PlanetaryStageSpec("p",gear(40),gear(20),gear(80,is_internal=True))])
    data=request.to_dict()
    assert data["stages"][0]["kind"]=="planetary"
    for edit in (lambda d:d.update(schema_version=2),
                 lambda d:d["stages"][0].update(kind="compound_planetary"),
                 lambda d:d["stages"][0]["sun"].update(z="40"),
                 lambda d:d["stages"][0]["sun"].update(unknown=1),
                 lambda d:d["stages"][0]["sun"].update(z=True),
                 lambda d:d["stages"][0]["sun"].pop("m_n_mm"),
                 lambda d:d["metric_availability"]["/torque_Nm"].update(state="failed")):
        broken=copy.deepcopy(data); edit(broken)
        with pytest.raises(ValueError): GearboxRequest.from_dict(broken)


def test_endurance_vs_missing_metric_roundtrip():
    from motor_ai_sim.gears.rating_iso6336 import miner_damage_from_cycles, MinerDamageResult
    a=miner_damage_from_cycles(target_name="a",bins=[(100.,math.inf)],block_hours=1.)
    b=miner_damage_from_cycles(target_name="b",bins=[(100.,None)],block_hours=1.)
    assert a.to_dict()["metric_availability"]["/predicted_life_hours"]["status"]=="endurance"
    assert b.to_dict()["metric_availability"]["/predicted_life_hours"]["status"]=="unavailable"
    assert MinerDamageResult.from_dict(a.to_dict()).to_dict()==a.to_dict()


def test_peak_static_ratio_and_discrete_damage_hand_formula():
    material=get_gear_material("18CrNiMo7-6_case_carburised_MQ")
    # Use explicit material anchors in an independently inverted log-log line.
    material=replace(material,sigma_Flim_MPa=430.,N_F_ref=3e6,N_F_static=1e3,Y_NT_static=2.5)
    expected_capacity=3e6/1.5**(math.log(3e6/1e3)/math.log(2.5))
    r=rate_peak_member(target_id="s.g",target_name="g",mechanism="bending_root",stress_MPa=1290.,
        material=material,fatigue_allowable_factor=1.,events=100,engagements_per_event=3,
        speed_known=True,static_allowable_MPa=1500.,static_source="specified tooth-root static limit",
        required_static_safety=1.1)
    assert r.static_safety==pytest.approx(1500./1290.)
    assert r.capacity_cycles==pytest.approx(expected_capacity)
    assert r.damage_D==pytest.approx(300./expected_capacity)
    assert r.static_passed


def test_peak_events_change_life_and_missing_semantics_cannot_pass():
    st=PairStageSpec("p",gear(20),gear(40)); gb=Gearbox([st])
    counts={st.stage_id+".gear1":2,st.stage_id+".gear2":1}
    limits={key:StaticStrengthLimits("explicit test allowables",70.,root_MPa=2000.,flank_MPa=3000.) for key in counts}
    spec=DutySpectrum([DutyBin(8.,100.,1.)],peak_torque_out_Nm=30.,peak_events=1,
        peak_speed_out_rpm=100.,peak_member_engagements=counts,static_strength_limits=limits)
    one=gb.evaluate_spectrum(spec)
    many=gb.evaluate_spectrum(replace(spec,peak_events=1000000))
    assert one.min_gear_life_h is not None and many.min_gear_life_h < one.min_gear_life_h
    for a,b in zip(one.gear_miner_lives,many.gear_miner_lives):
        peak=next(p for p in one.peak_member_ratings if (p.target_id,p.mechanism)==(a.target_id,a.mechanism))
        if peak.capacity_cycles is not None and math.isfinite(peak.capacity_cycles):
            assert b.damage_D-a.damage_D==pytest.approx(999999*counts[a.target_id]/peak.capacity_cycles)
        assert b.total_cycles_in_block-a.total_cycles_in_block==999999*counts[a.target_id]
    assert all(p.static_safety is not None for p in one.peak_member_ratings)
    incomplete=gb.evaluate_spectrum(replace(spec,peak_member_engagements={}))
    assert incomplete.min_gear_life_h is None
    assert all(p.fatigue_state=="unsupported" for p in incomplete.peak_member_ratings)
    reversing=gb.evaluate_spectrum(replace(spec,peak_reversing=True))
    assert reversing.min_gear_life_h is None
    assert len(one.bin_evaluations)==1
    assert GearboxEvaluationResult.from_dict(one.to_dict()).to_dict()==one.to_dict()


@pytest.mark.parametrize("fixed,omegas",[("ring",dict(sun=3.,ring=0.,carrier=1.)),
    ("sun",dict(sun=0.,ring=1.5,carrier=1.)),("carrier",dict(sun=2.,ring=-1.,carrier=0.))])
@pytest.mark.parametrize("known",["sun","ring","carrier"])
@pytest.mark.parametrize("sign",[1.,-1.])
def test_all_local_planetary_frames_hand_oracle(fixed,omegas,known,sign):
    from motor_ai_sim.gears.planetary_equilibrium import solve_local_planetary
    vs=omegas["sun"]-omegas["carrier"]; vr=omegas["ring"]-omegas["carrier"]
    r=solve_local_planetary(u=2.,eta_sp=.98,eta_pr=.97,omegas=omegas,planet_rel_rad_s=-2*vs,
        fixed_member=fixed,known_member=known,known_torque_Nm=sign,planet_bearing_power_W=.01)
    assert r.state=="valid"
    ts,tr,tc=(r.member_torques_Nm[k] for k in ("sun","ring","carrier"))
    # Direct two-mesh relative power equations, independent of affine solver.
    if ts*vs>=0:
        assert -tr*vr==pytest.approx(.97*(.98*ts*vs-.01))
    else:
        assert -ts*vs==pytest.approx(.98*(.97*tr*vr-.01))
    assert ts+tr+tc==pytest.approx(0.,abs=1e-14)
    assert r.member_torques_Nm[known]==pytest.approx(sign)


def test_planetary_external_load_and_support_moment():
    from motor_ai_sim.gears.parts import ShaftSectionSpec
    st=PlanetaryStageSpec("p",gear(40),gear(20),gear(80,is_internal=True),
        input_external_radial_N=100.,input_shaft_span_mm=40.,
        input_shaft_section=ShaftSectionSpec("shaft",10.))
    r=Gearbox([st]).evaluate_spectrum(DutySpectrum([DutyBin(3.,100.,1.)]))
    shaft=next(x for x in r.parts_summary.parts if x.part_name=="shaft")
    # Central force on simply supported beam: M=FL/4=1000 Nmm; W=pi*d^3/32.
    assert shaft.stress_summary["sigma_ba_MPa"]==pytest.approx(1000./(math.pi*10.**3/32.))
    missing=Gearbox([replace(st,input_shaft_span_mm=None)]).evaluate_spectrum(DutySpectrum([DutyBin(3.,100.,1.)]))
    assert next(x for x in missing.parts_summary.parts if x.part_name=="shaft").state=="unsupported"


def test_tooth_peak_maps_reject_carrier_and_unknown_ids():
    st=PlanetaryStageSpec("p",gear(40),gear(20),gear(80,is_internal=True))
    for identity in (st.stage_id+".carrier","unknown.gear1"):
        spec=DutySpectrum([DutyBin(3.,100.,1.)],peak_torque_out_Nm=5.,
            peak_member_engagements={identity:1})
        with pytest.raises(ValueError,match="Unknown peak member"):
            Gearbox([st]).evaluate_spectrum(spec)


def test_nonconvergence_reaches_spectrum_life(monkeypatch):
    import motor_ai_sim.gears.gearbox as module
    b=GearBearingSpec("n","needle",8.,12.,4.,2.5,2.2,Cu_kn=.25)
    st=PlanetaryStageSpec("p",gear(40),gear(20),gear(80,is_internal=True),planet_bearing=b)
    monkeypatch.setattr(module,"_PLANETARY_MAX_ITER",1)
    gb=Gearbox([st])
    point=gb.evaluate_point(input_speed_rpm=1000.,torque_Nm=1.)
    assert point.overall_efficiency is None
    r=gb.evaluate_spectrum(DutySpectrum([DutyBin(3.,100.,1.)]))
    assert r.min_gear_life_h is None and all(m.state=="failed" for m in r.gear_miner_lives)
