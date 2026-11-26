"""R4 review regressions and independent cycle/S-N boundary arithmetic."""
import copy
import json
import math
from dataclasses import replace
import pytest
from motor_ai_sim.gears import *


def gear(z, **kw):
    return GearSpec(z, 1., kw.pop('b_mm', 12.), tooth_thickness_allowance_mm=.04, **kw)


def reviewer_case(torque=6., diameter=2., width=12., **kw):
    bearing = GearBearingSpec('6000', 'deep_groove', 10., 26., 8., 4.75, 1.96, Cu_kn=.083)
    st = PairStageSpec('review', gear(20, b_mm=width), gear(40, b_mm=width),
        bearing_1a=bearing, bearing_1b=bearing, bearing_2a=bearing, bearing_2b=bearing,
        shaft1_span_mm=20., shaft2_span_mm=20.,
        shaft1_section=ShaftSectionSpec('input', diameter), **kw)
    limits = {st.stage_id+'.'+k: StaticStrengthLimits('test supplied allowables', 70., 2000., 3000.)
              for k in ('gear1', 'gear2')}
    spec = DutySpectrum([DutyBin(torque,100.,2000.), DutyBin(torque/2.,200.,3000.)],
                        static_strength_limits=limits)
    return st, spec


def test_reviewer_failing_shaft_blocks_both_pass_flags():
    st, spec = reviewer_case()
    r = Gearbox([st]).evaluate_spectrum(spec)
    shaft = next(p for p in r.parts_summary.parts if p.part_name == 'input')
    assert shaft.state == 'failed' and not shaft.passed and shaft.min_safety_factor < .218
    assert r.state == 'failed' and not r.preset_evaluation.all_passed and not r.passed
    assert 'part:input:failed' in r.pass_reasons
    assert not next(c for c in r.preset_evaluation.checks if c.criterion == 'parts').passed
    assert GearboxEvaluationResult.from_dict(r.to_dict()).to_dict() == r.to_dict()


@pytest.mark.parametrize('part', ['shaft', 'pin', 'carrier', 'rim', 'spline', 'key'])
@pytest.mark.parametrize('state', ['failed', 'unsupported'])
def test_every_part_failure_and_missing_required_check_blocks_preset(part, state):
    summary = summarize_part_strengths([PartStrengthRecord(part,'duty',.2,1.5,True,False,state)])
    result = evaluate_against_preset(get_preset('standard'), min_S_F_duty=3., min_S_H_duty=2.,
        min_S_F_static=3., min_S_H_static=2., S_F_static_state='valid', S_H_static_state='valid',
        gear_life_h=math.inf, bearing_life_h=1e6, min_bearing_S0=2., parts_summary=summary)
    assert not result.all_passed
    assert next(c for c in result.checks if c.criterion == 'parts').state == state


def test_supported_endurance_and_monotonic_torque():
    # 14 mm width keeps the 6 Nm case above S_H=1.1 with the corrected life cycles.
    for torque in (6., 2.):
        st, spec = reviewer_case(torque, diameter=10., width=14.)
        r = Gearbox([st]).evaluate_spectrum(spec)
        assert r.passed and r.preset_evaluation.all_passed
        if torque == 2.:
            assert r.min_gear_life_h == math.inf
            data = json.loads(json.dumps(r.to_dict(), allow_nan=False))
            assert data['min_gear_life_h'] is None
            assert data['metric_availability']['/min_gear_life_h']['status'] == 'endurance'
            assert GearboxEvaluationResult.from_dict(data).min_gear_life_h == math.inf
    st = PlanetaryStageSpec('p',gear(40),gear(20),gear(80,is_internal=True))
    r = Gearbox([st]).evaluate_spectrum(DutySpectrum([DutyBin(2.,100.,1.)]))
    assert r.min_gear_life_h is None and not r.passed
    assert any('internal_root_rating_not_implemented' in m.flags for m in r.gear_miner_lives)


def test_failed_bearing_has_no_aggregate_life():
    st, spec = reviewer_case(diameter=10.)
    # Explicit overspeed failure while the load-based L10 calculation is finite.
    st.bearing_1a = replace(st.bearing_1a, n_limit_rpm=10.)
    r = Gearbox([st]).evaluate_spectrum(spec)
    assert any(b.state == 'failed' for b in r.bearing_spectrum_lives)
    assert r.min_bearing_life_h is None and not r.passed


def test_member_cycles_and_life_factors_hand_value():
    st = PlanetaryStageSpec('p',gear(40),gear(20),gear(80,is_internal=True))
    r = evaluate_stage(st,input_speed_rpm=1000.,known_torque_Nm=1.,
        lubricant=get_gear_lubricant('ISO_VG_220_mineral'),required_life_h=100.)
    # Willis: carrier=1000/3 rpm; sun relative=2000/3; planet relative=-4000/3.
    # Per tooth: sun 3*2000/3*60*100=12e6; planet 4000/3*60*100=8e6.
    sp, pr = r.pair_ratings['sun_planet'], r.pair_ratings['planet_ring']
    planet = sp.gear2_rating
    assert sp.gear1_rating.applicability_evidence['life_cycles'] == pytest.approx(12e6)
    assert planet.applicability_evidence['life_cycles'] == pytest.approx(8e6)
    assert pr.gear1_rating.applicability_evidence['life_cycles'] == pytest.approx(8e6)
    assert pr.gear2_rating.applicability_evidence['life_cycles'] == pytest.approx(6e6)
    # Independent log interpolation between (1e5,1.6) and (5e7,1).
    expected_z = 1.6**(math.log(5e7/8e6)/math.log(5e7/1e5))
    expected_y = .85**(math.log(8e6/3e6)/math.log(1e10/3e6))
    assert planet.Z_NT == pytest.approx(expected_z)
    assert planet.Y_NT == pytest.approx(expected_y)


def test_spectrum_cycles_are_required_life_weighted_exposure():
    st, spec = reviewer_case(diameter=10.)
    r = Gearbox([st]).evaluate_spectrum(spec)
    # Output mean absolute rpm=(100*2000+200*3000)/5000=160; input=320.
    for b in r.bin_evaluations:
        pair = b.stage_results[0].pair_ratings['main_mesh']
        assert pair.gear1_rating.applicability_evidence['life_cycles'] == 192e6
        assert pair.gear2_rating.applicability_evidence['life_cycles'] == 96e6


@pytest.mark.parametrize('mechanism', ['bending_root', 'pitting_flank'])
def test_above_static_plateau_is_outside_sn_domain(mechanism):
    mat = get_gear_material('18CrNiMo7-6_case_carburised_MQ')
    plateau = mat.sigma_FE_MPa*mat.Y_NT_static if mechanism == 'bending_root' else mat.sigma_Hlim_MPa*mat.Z_NT_static
    r = evaluate_gear_miner_spectrum(target_name='g',material=mat,
        bins=[(plateau*1.01,100.,1.)],mechanism=mechanism)
    assert r.state == 'unsupported' and r.predicted_life_hours is None
    assert 'stress_above_static_plateau_outside_SN_domain' in r.flags
    peak = rate_peak_member(target_id='s.g',target_name='g',mechanism=mechanism,
        stress_MPa=plateau*1.01,material=mat,fatigue_allowable_factor=1.,events=2,
        engagements_per_event=1,speed_known=True,static_allowable_MPa=plateau*2.,
        static_source='test',required_static_safety=1.)
    assert peak.capacity_cycles is None and peak.damage_D is None and peak.static_passed
    assert peak.fatigue_state == 'unsupported'


@pytest.mark.parametrize('grades', [(4,6),(6,4)])
def test_deep_tooth_factor_uses_worst_pair_grade(grades):
    st = PairStageSpec('deep',gear(40,ha_star=1.3,hf_star=1.55,accuracy_grade=grades[0]),
                      gear(80,ha_star=1.3,hf_star=1.55,accuracy_grade=grades[1]))
    pair = Gearbox([st]).evaluate_point(input_speed_rpm=1000.,torque_Nm=1.).stage_results[0].pair_ratings['main_mesh']
    assert pair.gear1_rating.Y_DT == pair.gear2_rating.Y_DT == 1.


def test_missing_mesh_efficiency_propagates(monkeypatch):
    import motor_ai_sim.gears.gearbox as module
    original = module.compute_mesh_loss
    def unavailable(**kw):
        return replace(original(**kw), mesh_efficiency=None, state='unsupported')
    monkeypatch.setattr(module,'compute_mesh_loss',unavailable)
    st = PlanetaryStageSpec('p',gear(40),gear(20),gear(80,is_internal=True))
    r = Gearbox([st]).evaluate_point(input_speed_rpm=1000.,torque_Nm=1.)
    pf = r.stage_results[0].planetary_power_flow
    assert pf.eta0_sr is None and pf.efficiency_total is None
    assert 'mesh_efficiency_unavailable' in pf.flags and not r.passed
    assert r.overall_efficiency is None
    json.dumps(r.to_dict(),allow_nan=False)
    spectrum = Gearbox([st]).evaluate_spectrum(DutySpectrum([DutyBin(2.,100.,1.)]))
    assert spectrum.min_gear_life_h is None and not spectrum.preset_evaluation.all_passed


def test_point_never_claims_full_qualification():
    r = Gearbox([PairStageSpec('p',gear(20),gear(40))]).evaluate_point(input_speed_rpm=100.,torque_Nm=1.)
    assert not r.passed and 'preset_not_evaluated' in r.pass_reasons


def test_unavailable_pair_efficiency_alone_blocks_preset(monkeypatch):
    import motor_ai_sim.gears.gearbox as module
    original = module.compute_mesh_loss
    monkeypatch.setattr(module, 'compute_mesh_loss', lambda **kw: replace(original(**kw), mesh_efficiency=None))
    st, spec = reviewer_case(2.,diameter=10.,width=14.)
    r = Gearbox([st]).evaluate_spectrum(spec)
    assert r.overall_efficiency is None and r.state == 'unsupported'
    assert not r.passed and not r.preset_evaluation.all_passed


@pytest.mark.parametrize('operation', ['point', 'spectrum', 'assembly', 'pose'])
def test_response_provenance_and_member_mapping(operation):
    planet = PlanetaryStageSpec('planetary',gear(40),gear(20),gear(80,is_internal=True),stage_id='stage_planet')
    pair = PairStageSpec('pair',gear(20),gear(40),stage_id='stage_pair')
    req = GearboxRequest([planet,pair], operation=operation, input_angle_deg=37.,
        spectrum=DutySpectrum([DutyBin(2.,100.,1.)]) if operation == 'spectrum' else None)
    response = req.execute()
    assert response.core_version == CORE_VERSION
    assert response.request_hash == request_hash(req)
    assert response.member_instances['stage_planet.planet'] == [f'stage_planet.planet_{i}' for i in range(3)]
    assert response.member_instances['stage_pair.gear1'] == ['stage_pair.gear1']
    payload = json.loads(json.dumps(response.to_dict(),allow_nan=False))
    assert GearboxResponse.from_dict(payload).to_dict() == payload
    assert request_hash(replace(req,torque_Nm=2.)) != response.request_hash
    if operation == 'pose':
        expected = build_chain_assembly(req.stages).pose_at(37.)
        assert {k:v.to_dict() for k,v in expected.items()} == payload['result']['members']


def test_outline_deduplication_decimation_is_reversible_for_pose():
    req = GearboxRequest([PlanetaryStageSpec('p',gear(40),gear(20),gear(80,is_internal=True))],
                         operation='assembly')
    full = req.execute().result
    compact = full.with_outline_options(stride=4,once=True)
    assert len(compact.outlines) == 3
    assert all(m.outline is None and m.outline_id in compact.outlines for m in compact.members)
    assert all(o.outline_mm[0] == o.outline_mm[-1] for o in compact.outlines.values())
    assert compact.pose_at(23.) == full.pose_at(23.)
    restored = AssemblyDescription.from_dict(json.loads(json.dumps(compact.to_dict(),allow_nan=False)))
    assert restored.pose_at(23.) == full.pose_at(23.)
    assert len(json.dumps(compact.to_dict())) < len(json.dumps(full.to_dict()))/3
    assert all(m.outline is not None for m in full.members)


def test_catalogues_are_json_safe_and_resolve_names():
    catalogues = [list_material_catalogue(),list_lubricant_catalogue(),
                  list_bearing_catalogue(),list_preset_catalogue()]
    for cards in catalogues:
        assert cards and all(isinstance(c['name'],str) for c in cards)
        json.dumps(cards,allow_nan=False)
    for card in catalogues[0]: assert get_gear_material(card['name']).sigma_Flim_MPa == card['sigma_Flim_MPa']
    for card in catalogues[1]: assert get_gear_lubricant(card['name']).kind == card['kind']
    for card in catalogues[2]:
        if card['core_supported']:
            assert GearBearingSpec.from_library(card['name']).d_mm == card['d_mm']
    for card in catalogues[3]: assert get_preset(card['name']).required_life_h == card['required_life_h']


@pytest.mark.parametrize('value', ['20', True, -1, math.nan])
def test_validation_error_has_nested_field_path(value):
    data = GearboxRequest([PairStageSpec('p',gear(20),gear(40))]).to_dict()
    data['stages'][0]['gear1']['z'] = value
    with pytest.raises(FieldValidationError) as caught:
        GearboxRequest.from_dict(data)
    assert caught.value.path == '/stages/0/gear1/z'
    assert caught.value.to_dict()['code'] == 'invalid_value'


def test_validation_missing_field_and_direct_numeric_path():
    data = GearboxRequest([PairStageSpec('p',gear(20),gear(40))]).to_dict()
    del data['stages'][0]['gear1']['z']
    with pytest.raises(FieldValidationError) as caught: GearboxRequest.from_dict(data)
    assert caught.value.path == '/stages/0/gear1/z' and caught.value.code == 'missing_field'
    with pytest.raises(FieldValidationError) as caught: GearboxRequest([],outline_stride=False)
    assert caught.value.path == '/outline_stride'


def test_request_reference_errors_and_duplicate_ids_have_paths():
    stage = PairStageSpec('p',gear(20),gear(40))
    for kwargs, path in [({'preset_name':'absent'},'/preset_name'),
                         ({'lubricant':'absent'},'/lubricant'),
                         ({'torque_at':'absent'},'/torque_at')]:
        with pytest.raises(FieldValidationError) as caught: GearboxRequest([stage],**kwargs)
        assert caught.value.path == path
    with pytest.raises(FieldValidationError) as caught: GearboxRequest([stage,replace(stage,name='second')])
    assert caught.value.path == '/stages/1/stage_id'
    bad = replace(stage,gear1=replace(stage.gear1,material_name='absent'))
    with pytest.raises(FieldValidationError) as caught: GearboxRequest([bad])
    assert caught.value.path == '/stages/0/gear1/material_name'
