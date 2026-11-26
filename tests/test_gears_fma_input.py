"""Opt-in mesh misalignment regression, legacy golden records and hand arithmetic."""
import copy
import hashlib
import json
import math
from dataclasses import replace
import pytest
from motor_ai_sim.gears import (
    GearSpec, PairStageSpec, PlanetaryStageSpec, Gearbox, GearboxRequest,
    GearboxResponse, DutySpectrum, DutyBin, FieldValidationError,
    analyze_pair, compute_mesh_forces, compute_iso6336_load_factors,
)
from tests.test_gears_core_fix_r4 import gear, reviewer_case


def legacy_cases():
    # Reuse r4 reviewer fixtures, cycle fixture and r3 stepped-planet geometry.
    for torque in (6., 2.):
        stage, spectrum = reviewer_case(torque, diameter=10., width=14.)
        stage.stage_id = "legacy_pair"
        spectrum = replace(spectrum, static_strength_limits={
            "legacy_pair."+key.rsplit(".", 1)[1]: val
            for key, val in spectrum.static_strength_limits.items()})
        yield "reviewer_"+str(int(torque)), GearboxRequest(
            [stage], operation="spectrum", spectrum=spectrum, request_id="legacy")
    stages = [
        PairStageSpec("pair", gear(20), gear(40), stage_id="legacy_pair"),
        PairStageSpec("helical", gear(20, beta_deg=15.), gear(40, beta_deg=-15.),
                      kind="helical_pair", stage_id="legacy_helical"),
        PairStageSpec("internal", gear(20), gear(60, is_internal=True),
                      kind="internal_pair", stage_id="legacy_internal"),
        PlanetaryStageSpec("planetary", gear(40), gear(20), gear(80, is_internal=True),
                           stage_id="legacy_planetary"),
        PlanetaryStageSpec("compound", gear(20), gear(30), gear(70, is_internal=True),
                           planet2=gear(20), stage_id="legacy_compound"),
    ]
    for stage in stages:
        yield stage.name, GearboxRequest([stage], request_id="legacy", input_speed_rpm=1000., torque_Nm=1.)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


# Captured before any production source edit, base commit 9c8ecf44.
LEGACY_GOLDENS = {'reviewer_6': {'request': '31fcab9efea14df688a4184a358e32d11d54f2c5562df1378edd18e792a68193', 'response': 'b5df77026cdd6729a07ac14b40aaae494c6922c96dec7982be83cd7e9c4082da'}, 'reviewer_2': {'request': '82e55e1884806d9d9f3d85b940c8d2869a1fc9fd8941add0e1edcfa0da38163a', 'response': '132933cf7406b0adfa16f9b412ce6761547c3e085da46c616c32f11b58b79d10'}, 'pair': {'request': '3b1ab443f1751db9a249e70dfc701b929ef2e2369d4704b0b6824d1d86e46987', 'response': '3b0638ce2cc29cd6ff1e1b2e973fd4371ce59dfd675068b3356a4f20f8c78779'}, 'helical': {'request': '0441d04ab61d8b1614850e2a5fe92f418808c37a025ad44ccbe2dcd7bc189993', 'response': 'cc3752082aeff5d63731396ec96aaf90ab5d743ad72191d738a166573b0d2b46'}, 'internal': {'request': '21d2548c3de106979a55424a8b5d8be313b098fa4f162e962f0b680293622073', 'response': 'cb93e6da584cd62a458c09a88427c5253b6b548e0be400a45e158352c04e5223'}, 'planetary': {'request': '5b1b10c802d7b46f2927ebe96867ede71e178236e5b29f8115be36fd2fea1fea', 'response': 'f0e5b43fd700db437a0e004a650d1b4c664b67f621a6bd862e013b032a9b63e9'}, 'compound': {'request': '39e4d4fe67f21a926887adef1b61372909fae4bea42aea04dc416e9ed14a9e0d', 'response': '10aedf85c8c1d514f64a172878cee6741f43cf12385553f352ddeb78048722ad'}}


@pytest.mark.parametrize("name,req", list(legacy_cases()))
def test_absent_input_is_bit_identical_to_base(name, req):
    assert digest(req.to_dict()) == LEGACY_GOLDENS[name]["request"]
    assert digest(req.execute().to_dict()) == LEGACY_GOLDENS[name]["response"]
    restored = GearboxRequest.from_dict(req.to_dict())
    assert restored.to_dict() == req.to_dict()


INPUTS = [("pair", "f_ma_um"), ("planetary", "sun_planet_f_ma_um"),
          ("planetary", "planet_ring_f_ma_um")]


def stage_for(kind):
    if kind == "pair":
        return PairStageSpec("pair", gear(20), gear(40), stage_id="stage")
    return PlanetaryStageSpec("planetary", gear(40), gear(20), gear(80, is_internal=True),
                              stage_id="stage")


@pytest.mark.parametrize("kind,field", INPUTS)
@pytest.mark.parametrize("bad", [-1., math.nan, math.inf, -math.inf, True, "10", []])
def test_misalignment_validation_and_decoder_path(kind, field, bad):
    stage = stage_for(kind)
    with pytest.raises(ValueError):
        replace(stage, **{field: bad})
    payload = GearboxRequest([replace(stage, **{field: 10.})]).to_dict()
    payload["stages"][0][field] = bad
    with pytest.raises(FieldValidationError) as exc:
        GearboxRequest.from_dict(payload)
    assert exc.value.path == "/stages/0/"+field


@pytest.mark.parametrize("kind,field", INPUTS)
@pytest.mark.parametrize("value", [0., 10.])
def test_schema_v1_supplied_value_source_and_availability(kind, field, value):
    stage = replace(stage_for(kind), **{field: value})
    req = GearboxRequest([stage], request_id="supplied")
    payload = json.loads(json.dumps(req.to_dict(), allow_nan=False))
    assert payload["schema_version"] == 1
    info = payload["stages"][0]["metric_availability"]["/"+field]
    assert info["status"] == "available"
    restored = GearboxRequest.from_dict(payload)
    assert restored.to_dict() == payload
    result = restored.execute()
    mesh = {"f_ma_um": "main_mesh", "sun_planet_f_ma_um": "sun_planet",
            "planet_ring_f_ma_um": "planet_ring"}[field]
    rating = result.result.stage_results[0].pair_ratings[mesh]
    lf = rating.load_factors
    assert lf.f_ma_um == value and lf.f_ma_source == "supplied"
    assert lf.to_dict()["f_ma_source"] == "supplied"
    assert lf.state == "estimated"
    assert "misalignment_f_ma_estimated_rigid_carrier_caveat" not in lf.flags
    assert "f_ma_um_including_shaft_housing_deflection" not in lf.missing_inputs
    for member in (rating.gear1_rating, rating.gear2_rating):
        assert "simplified:estimated_misalignment_f_ma" not in member.flags
    encoded = json.loads(json.dumps(result.to_dict(), allow_nan=False))
    assert GearboxResponse.from_dict(encoded).to_dict() == encoded
    # Availability is derived; it cannot be used to turn the supplied value into missing.
    payload["stages"][0]["metric_availability"]["/"+field]["status"] = "unavailable"
    with pytest.raises(FieldValidationError):
        GearboxRequest.from_dict(payload)


def test_legacy_estimated_source_roundtrip_and_strict_fields():
    req = GearboxRequest([stage_for("pair")])
    result = req.execute()
    lf = result.result.stage_results[0].pair_ratings["main_mesh"].load_factors
    assert lf.f_ma_source == "estimated"
    assert "f_ma_source" not in lf.to_dict()  # v1 default preserves legacy bytes
    assert type(lf).from_dict(lf.to_dict()).f_ma_source == "estimated"
    payload = req.to_dict()
    assert "f_ma_um" not in payload["stages"][0]
    payload["stages"][0]["fma_um"] = 1.
    with pytest.raises(FieldValidationError, match="Unknown field"):
        GearboxRequest.from_dict(payload)
    payload = req.to_dict()
    del payload["stages"][0]["gear1"]
    with pytest.raises(FieldValidationError, match="Missing field"):
        GearboxRequest.from_dict(payload)


@pytest.mark.parametrize("compound", [False, True])
@pytest.mark.parametrize("field,mesh,other", [
    ("sun_planet_f_ma_um", "sun_planet", "planet_ring"),
    ("planet_ring_f_ma_um", "planet_ring", "sun_planet")])
@pytest.mark.parametrize("operation", ["point", "spectrum"])
def test_mesh_inputs_are_independent_through_stage_and_spectrum(compound, field, mesh, other, operation):
    stage = (PlanetaryStageSpec("compound", gear(20), gear(30), gear(70,is_internal=True),
             planet2=gear(20), stage_id="stage") if compound else stage_for("planetary"))
    spectrum = DutySpectrum([DutyBin(4.,100.,1.)], peak_torque_out_Nm=8.,
        peak_events=2, peak_speed_out_rpm=100.) if operation == "spectrum" else None
    req = GearboxRequest([stage], operation=operation, spectrum=spectrum, request_id="independent")
    before = req.execute().result.stage_results[0].pair_ratings
    after = replace(req, stages=[replace(stage, **{field: 0.})]).execute().result.stage_results[0].pair_ratings
    assert after[other].to_dict() == before[other].to_dict()
    assert after[mesh].load_factors.f_ma_source == "supplied"
    assert after[mesh].load_factors.f_ma_um == 0.
    assert after[mesh].load_factors.K_Hbeta < before[mesh].load_factors.K_Hbeta


@pytest.mark.parametrize("supplied", [0., 1., 10., 100.])
def test_method_c_independent_hand_oracle(supplied):
    # Existing strength fixture: z20/40, m2, b16, T1=12 Nm; standstill => Kv=1.
    # Ft=2*12000/40=600 N; Fm/b=600/16=37.5 N/mm; KA=Kgamma=1.
    # f_sh=.023*37.5*(16/40)^2*10=1.38 um; Fbx=1.8354+supplied um.
    # y_beta=min(.15*Fbx,6); Fby=Fbx-y_beta. 0/1 um: full contact;
    # 10/100 um: partial contact; 100 um also exercises the running-in cap.
    a=GearSpec(20,2.,16.,tooth_thickness_allowance_mm=.03)
    b=GearSpec(40,2.,16.,tooth_thickness_allowance_mm=.03)
    pg=analyze_pair(a,b)
    mf=compute_mesh_forces(mesh_name="oracle", pair_geom=pg,
        total_torque_gear1_Nm=12., gear1_rel_speed_rpm=0.)
    lf=compute_iso6336_load_factors(pair_geom=pg,mesh_forces=mf,
        gear1_spec=a,gear2_spec=b,n1_rel_rpm=0.,f_ma_um=supplied)
    # Independent spur contact path and stiffness (no production helper outputs).
    alpha=math.radians(20.)
    epsilon=(math.sqrt(22.**2-(20.*math.cos(alpha))**2)
             +math.sqrt(42.**2-(40.*math.cos(alpha))**2)
             -60.*math.sin(alpha))/(2.*math.pi*math.cos(alpha))
    c_prime=.8/(.04723+.15551/20.+.25791/40.)
    c_beta=.85*c_prime*(.75*epsilon+.25)  # N/(mm*um), not crowning C_beta
    fbx=1.8354+supplied
    fby=fbx-min(.15*fbx,6.)
    ratio=fby*c_beta/75.
    kh=1.+ratio if ratio<=1. else 2.*math.sqrt(ratio)
    nf=(16./4.5)**2/(1.+16./4.5+(16./4.5)**2)
    assert lf.K_v == 1.
    assert lf.K_Hbeta == pytest.approx(kh,rel=1e-12)
    assert lf.K_Fbeta == pytest.approx(kh**nf,rel=1e-12)
    assert (ratio<=1.) == (supplied<=1.)


@pytest.mark.parametrize("bad", [-1., math.nan, math.inf, True, "10"])
def test_low_level_supplied_misalignment_is_strict(bad):
    a,b=gear(20),gear(40); pg=analyze_pair(a,b)
    mf=compute_mesh_forces(mesh_name="m",pair_geom=pg,total_torque_gear1_Nm=1.,gear1_rel_speed_rpm=0.)
    with pytest.raises(ValueError):
        compute_iso6336_load_factors(pair_geom=pg,mesh_forces=mf,gear1_spec=a,
                                    gear2_spec=b,n1_rel_rpm=0.,f_ma_um=bad)


def test_robot_joint_example_reports_actual_response_without_qualification():
    # Illustrative stage 2 of 36:1: ring fixed, ratio6, 40 Nm carrier output,
    # sun600 rpm/carrier100 rpm, three planets, 70 C, default material/grade.
    # Assumed f_ma is engineer input, NOT a measured alignment tolerance.
    g=lambda z,**kw: GearSpec(z,.6,10.5,tooth_thickness_allowance_mm=.02,**kw)
    stage=PlanetaryStageSpec("robot",g(20),g(40),g(100,is_internal=True),stage_id="robot")
    def evaluate(value):
        req=GearboxRequest([replace(stage,sun_planet_f_ma_um=value)],
            input_speed_rpm=600.,torque_Nm=40.,torque_at="output",lubricant="ROBOT_GREASE_NLGI1_PAO")
        return req.execute().result.stage_results[0]
    baseline,aligned,ten = evaluate(None),evaluate(1.),evaluate(10.)
    a,b,c=[r.pair_ratings["sun_planet"] for r in (baseline,aligned,ten)]
    assert a.load_factors.f_ma_um == pytest.approx(4.395706345359769)
    assert a.load_factors.K_Hbeta == pytest.approx(3.1373184948801485)
    assert b.load_factors.K_Hbeta == pytest.approx(2.68855099919455)
    assert c.load_factors.K_Hbeta == pytest.approx(3.762670443988302)
    assert [a.min_S_H,b.min_S_H,c.min_S_H] == pytest.approx(
        [.6549647993686292,.6894496930807115,.6145333874748088])
    assert b.min_S_H > a.min_S_H > c.min_S_H
    assert aligned.pair_ratings["planet_ring"].gear2_rating.bending_state == "unsupported"
    assert not b.passed  # no threshold relaxed and no fabricated safe design


@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("mutation", ["source", "flag", "missing"])
def test_decoder_rejects_inconsistent_misalignment_provenance(supplied, mutation):
    stage=replace(stage_for("pair"),f_ma_um=1. if supplied else None)
    result=GearboxRequest([stage]).execute().to_dict()
    lf=result["result"]["stage_results"][0]["pair_ratings"]["main_mesh"]["load_factors"]
    if mutation == "source":
        if supplied:
            del lf["f_ma_source"]  # must not silently default to estimated
        else:
            lf["f_ma_source"]="supplied"
    else:
        key,token = (("flags","misalignment_f_ma_estimated_rigid_carrier_caveat")
            if mutation == "flag" else ("missing_inputs","f_ma_um_including_shaft_housing_deflection"))
        if supplied:
            lf[key].append(token)
        else:
            lf[key].remove(token)
    with pytest.raises(FieldValidationError) as exc:
        GearboxResponse.from_dict(result)
    assert exc.value.path.endswith("/load_factors/f_ma_source")
