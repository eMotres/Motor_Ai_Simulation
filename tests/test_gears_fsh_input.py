"""Supplied shaft/pin misalignment: compatibility, contract and arithmetic."""
import math
from dataclasses import replace
import pytest
from motor_ai_sim.gears import (
    GearSpec, PlanetaryStageSpec, GearboxRequest, GearboxResponse, FieldValidationError,
    DutySpectrum, DutyBin, analyze_pair, compute_mesh_forces, compute_iso6336_load_factors,
)
from tests.test_gears_fma_input import legacy_cases, digest, stage_for, gear


def fsh_absent_cases():
    for name, request in legacy_cases():
        for value in (None, 0., 10.):
            stage = request.stages[0]
            fields = ({"f_ma_um": value} if hasattr(stage, "f_ma_um") else
                      {"sun_planet_f_ma_um": value, "planet_ring_f_ma_um": value})
            yield f"{name}_fma_{value}", replace(request, stages=[replace(stage, **fields)])
    g = lambda z, **kw: GearSpec(z, .6, 10.5, tooth_thickness_allowance_mm=.02, **kw)
    robot = PlanetaryStageSpec("robot_joint", g(20), g(40), g(100, is_internal=True), stage_id="robot_stage2")
    for value in (None, 0., 1., 10.):
        yield f"robot930_fma_{value}", GearboxRequest(
            [replace(robot, sun_planet_f_ma_um=value, planet_ring_f_ma_um=value)],
            request_id="robot_example", input_speed_rpm=600., torque_Nm=40., torque_at="output",
            lubricant="ROBOT_GREASE_NLGI1_PAO")


# Captured from merged #930, commit 30cf8a12, before production source changes.
FSH_ABSENT_GOLDENS = {'reviewer_6_fma_None': {'request': '31fcab9efea14df688a4184a358e32d11d54f2c5562df1378edd18e792a68193', 'response': 'b5df77026cdd6729a07ac14b40aaae494c6922c96dec7982be83cd7e9c4082da'}, 'reviewer_6_fma_0.0': {'request': '634d8ffc800936ddbf424bce81d1fb0adaf73fcc250692e06e92126229039eb2', 'response': '36d5496780bf9dd02765f9e2d63f094101e2290ce3c3daa3f37570a753dbd21c'}, 'reviewer_6_fma_10.0': {'request': '6ff5ded6142445a6327b486656ce067a8c9beabbaccb7dcf7de78f3606b98a9d', 'response': '914d11c5249105d5be97eb015c1283944b888515903e4d635d5de4350f262ef5'}, 'reviewer_2_fma_None': {'request': '82e55e1884806d9d9f3d85b940c8d2869a1fc9fd8941add0e1edcfa0da38163a', 'response': '132933cf7406b0adfa16f9b412ce6761547c3e085da46c616c32f11b58b79d10'}, 'reviewer_2_fma_0.0': {'request': 'a6e610a8e34e8391b630d62bae47052f997c06d503466954e3442f97f0ee255a', 'response': '589c9fc90c89120ecfc72868c4ab1d654fab1a50b531820e9da6e578af4935c7'}, 'reviewer_2_fma_10.0': {'request': 'cf8c3160ea6b93c99a97201899f4a8d2d6d85a0dfdbce6fbfd79d353f5aa9026', 'response': '216b5a50604e32297d751c9c272d61cdf3d4ed3878a5e989e6ea33de8e468cba'}, 'pair_fma_None': {'request': '3b1ab443f1751db9a249e70dfc701b929ef2e2369d4704b0b6824d1d86e46987', 'response': '3b0638ce2cc29cd6ff1e1b2e973fd4371ce59dfd675068b3356a4f20f8c78779'}, 'pair_fma_0.0': {'request': 'c3567170e834a1101933239204d6591bca888b1fbb0a45fcb63ad2431250e3e9', 'response': 'dd692b8971b836e9e79010bc9d0958dfeb28bf758b5a7e9c39e0dfff092100df'}, 'pair_fma_10.0': {'request': '75c3f6687177f760bc347fdea6eff6ebf90ee9e43027c4cd289702f488968333', 'response': '314d18413b729ec1508d60e1ea195371dda011f2d1c9b063b98cc1647740c880'}, 'helical_fma_None': {'request': '0441d04ab61d8b1614850e2a5fe92f418808c37a025ad44ccbe2dcd7bc189993', 'response': 'cc3752082aeff5d63731396ec96aaf90ab5d743ad72191d738a166573b0d2b46'}, 'helical_fma_0.0': {'request': '2404caf47a3a4999bb6c283e13fae91ba185da1ca0d8a9aeadff4494a5d7ef57', 'response': '12e439220358290f5faf4268a50ce376c2ed979d689d48c20db54e1b42a069ec'}, 'helical_fma_10.0': {'request': 'b8c8814332223107718399471a62de50570fe1f815064443dfd52f057e90a648', 'response': 'f129ad3e32949edeb8863cc8e0ec96b90128cded9f1ccdfc418cce6efa78e1a0'}, 'internal_fma_None': {'request': '21d2548c3de106979a55424a8b5d8be313b098fa4f162e962f0b680293622073', 'response': 'cb93e6da584cd62a458c09a88427c5253b6b548e0be400a45e158352c04e5223'}, 'internal_fma_0.0': {'request': 'b23fa22e56bd5e3cb6e9004be5c9553cac7e1be393ec04c88f552bfa3f6bd23f', 'response': 'a98e9fbbd32dd53f34aab8278c64b0d8e7f4c7b777ce9a64ce3d51c77dfd1dcc'}, 'internal_fma_10.0': {'request': '398f85fb65bbe155cf3a1e31990ca024fca49549d6dba0cbd3d1a19a36a33f2a', 'response': 'ec01d38a65d52476e6ed76b153168b3862bc9035f99334f13905ead19969afc6'}, 'planetary_fma_None': {'request': '5b1b10c802d7b46f2927ebe96867ede71e178236e5b29f8115be36fd2fea1fea', 'response': 'f0e5b43fd700db437a0e004a650d1b4c664b67f621a6bd862e013b032a9b63e9'}, 'planetary_fma_0.0': {'request': '1a595640a9eeadd13330687050502818ffcffe52bdd933549d345fc218d7c38a', 'response': 'ce1701fb135c7b7710438c98fd87755f47f6bacc863870b7ac54991b132d4f16'}, 'planetary_fma_10.0': {'request': '852bc8b965506c01c528a403e0fa0d4f76a61216bcf9242401b6684130773c45', 'response': 'ccf23d7e99d7c830fcbafa61ea6206c2c305087b80c09dee3c0f0bb25934f943'}, 'compound_fma_None': {'request': '39e4d4fe67f21a926887adef1b61372909fae4bea42aea04dc416e9ed14a9e0d', 'response': '10aedf85c8c1d514f64a172878cee6741f43cf12385553f352ddeb78048722ad'}, 'compound_fma_0.0': {'request': 'bd6323a0b845c998ecb8a12844a33714a2ed9fcd4d6e30b1052fb666050d5f0f', 'response': '5fc327f0b323d1e2c94d70a24d7548feb41c53990bb1a223df3022b1cf44f814'}, 'compound_fma_10.0': {'request': '62f5f1a456904e39c76f81ad5bc18a1de234177eb38b9b3eb9e1a4a5ce856ebd', 'response': '2feebffd58cd1b32fdb7d8cf8ef2f7593c9138dab35eb717d58171589c089ee4'}, 'robot930_fma_None': {'request': '89a75c1c45cc362a2a8f860d3239caecb043cbcc6e132d9ddc86b787a7cdda5d', 'response': '793f6ecaf349b14ee693f977743dce0b7ed44e8d2d0bdbe89522a8e714f5f5b3'}, 'robot930_fma_0.0': {'request': 'de1dcb500d9b6100f78fbae2e4c44992a8dc679fc3933004b3bba622b129f64f', 'response': 'b2afb068f98e79df5d7169eb0702267702522d17cbce722d54348ff43efe3292'}, 'robot930_fma_1.0': {'request': '6a1f56dc3acf5be7a02ee1c0d627e0d61668ba93ca0c84763d79aabc76e09e65', 'response': '8a32d6da8b0646da76eb8ff317feec85c99bc042c32a7de6338cdfd8b715797f'}, 'robot930_fma_10.0': {'request': 'c336f7437925dea60994d19c4cd8755037c24e1f435ef9970c4cee83e88ad07f', 'response': 'db20382fc7884bfd8f408be4bfe9db0e7db6b3897a8e073cd841ea1fc06c2e70'}}


@pytest.mark.parametrize("name,req", list(fsh_absent_cases()))
def test_absent_fsh_preserves_complete_v1_records(name, req):
    assert digest(req.to_dict()) == FSH_ABSENT_GOLDENS[name]["request"]
    result = req.execute()
    assert digest(result.to_dict()) == FSH_ABSENT_GOLDENS[name]["response"]
    assert GearboxResponse.from_dict(result.to_dict()).to_dict() == result.to_dict()


INPUTS = [("pair", "f_sh_um", "main_mesh"),
          ("planetary", "sun_planet_f_sh_um", "sun_planet"),
          ("planetary", "planet_ring_f_sh_um", "planet_ring")]


@pytest.mark.parametrize("kind,field,mesh", INPUTS)
@pytest.mark.parametrize("bad", [-1., math.nan, math.inf, -math.inf, True, "0.1", []])
def test_fsh_input_validation_and_strict_decoder_path(kind, field, mesh, bad):
    stage = stage_for(kind)
    with pytest.raises(ValueError):
        replace(stage, **{field: bad})
    payload = GearboxRequest([replace(stage, **{field: .1})]).to_dict()
    payload["stages"][0][field] = bad
    with pytest.raises(FieldValidationError) as exc:
        GearboxRequest.from_dict(payload)
    assert exc.value.path == "/stages/0/"+field


@pytest.mark.parametrize("kind,field,mesh", INPUTS)
@pytest.mark.parametrize("value", [0., .1, 1.])
def test_fsh_v1_value_availability_and_provenance(kind, field, mesh, value):
    request = GearboxRequest([replace(stage_for(kind), **{field: value})])
    payload = request.to_dict()
    assert payload["stages"][0]["metric_availability"]["/"+field]["status"] == "available"
    assert GearboxRequest.from_dict(payload).to_dict() == payload
    result = request.execute()
    lf = result.result.stage_results[0].pair_ratings[mesh].load_factors
    assert lf.f_sh_um == value and lf.f_sh_source == "supplied"
    assert lf.to_dict()["f_sh_um"] == value
    assert lf.to_dict()["metric_availability"]["/f_sh_um"]["state"] == "estimated"
    assert "simplified:placeholder_shaft_bending_f_sh" not in lf.flags
    assert lf.f_ma_source == "estimated" and lf.state == "estimated"
    assert "misalignment_f_ma_estimated_rigid_carrier_caveat" in lf.flags
    assert GearboxResponse.from_dict(result.to_dict()).to_dict() == result.to_dict()
    payload["stages"][0]["metric_availability"]["/"+field]["status"] = "unavailable"
    with pytest.raises(FieldValidationError):
        GearboxRequest.from_dict(payload)


def test_legacy_estimated_fsh_diagnostic_is_not_invented_on_decode():
    lf = GearboxRequest([stage_for("pair")]).execute().result.stage_results[0].pair_ratings["main_mesh"].load_factors
    assert lf.f_sh_source == "estimated" and lf.f_sh_um > 0.
    encoded = lf.to_dict()
    assert "f_sh_um" not in encoded and "f_sh_source" not in encoded
    assert "/f_sh_um" not in encoded["metric_availability"]
    decoded = type(lf).from_dict(encoded)
    assert decoded.f_sh_um is None and decoded.f_sh_source == "estimated"
    assert decoded.to_dict() == encoded


@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("mutation", ["source", "flag", "value"])
def test_decoder_rejects_inconsistent_fsh_provenance(supplied, mutation):
    request = GearboxRequest([replace(stage_for("pair"), f_sh_um=.1 if supplied else None)])
    payload = request.execute().to_dict()
    lf = payload["result"]["stage_results"][0]["pair_ratings"]["main_mesh"]["load_factors"]
    if mutation == "source":
        if supplied:
            del lf["f_sh_source"]
        else:
            lf["f_sh_source"] = "supplied"
    elif mutation == "flag":
        token = "simplified:placeholder_shaft_bending_f_sh"
        if supplied:
            lf["flags"].append(token)
        else:
            lf["flags"].remove(token)
    elif supplied:
        del lf["f_sh_um"]
    else:
        lf["f_sh_um"] = .1  # noncanonical injection into old estimated record
    with pytest.raises(FieldValidationError):
        GearboxResponse.from_dict(payload)


@pytest.mark.parametrize("compound", [False, True])
@pytest.mark.parametrize("field,mesh,other", [
    ("sun_planet_f_sh_um", "sun_planet", "planet_ring"),
    ("planet_ring_f_sh_um", "planet_ring", "sun_planet")])
@pytest.mark.parametrize("operation", ["point", "spectrum"])
def test_fsh_per_mesh_propagates_independently(compound, field, mesh, other, operation):
    stage = (PlanetaryStageSpec("compound", gear(20), gear(30), gear(70, is_internal=True),
        planet2=gear(20), stage_id="stage") if compound else stage_for("planetary"))
    spectrum = DutySpectrum([DutyBin(4., 100., 1.)], peak_torque_out_Nm=8.,
        peak_events=2, peak_speed_out_rpm=100.) if operation == "spectrum" else None
    req = GearboxRequest([stage], operation=operation, spectrum=spectrum)
    before = req.execute().result.stage_results[0].pair_ratings
    after = replace(req, stages=[replace(stage, **{field: 0.})]).execute().result.stage_results[0].pair_ratings
    assert after[other].to_dict() == before[other].to_dict()
    assert after[mesh].load_factors.f_sh_source == "supplied"
    assert after[mesh].load_factors.f_sh_um == 0.
    assert after[mesh].load_factors.K_Hbeta < before[mesh].load_factors.K_Hbeta


@pytest.mark.parametrize("supplied", [0., .1, 1., 10., 100.])
def test_fsh_method_c_independent_hand_oracle(supplied):
    # Spur z20/40, m2, b16, T1=12 Nm, standstill: Ft=2*12000/40=600 N;
    # Fm/b=600/16=37.5 N/mm; KA=Kgamma=Kv=1; supply f_ma=0.
    # Fbx=1.33*f_sh; Fby=Fbx-min(.15*Fbx,6); ratio=Fby*c_beta/75.
    # 0/.1/1 um full contact, 10/100 partial; 100 reaches the 6 um run-in cap.
    a = GearSpec(20, 2., 16., tooth_thickness_allowance_mm=.03)
    b = GearSpec(40, 2., 16., tooth_thickness_allowance_mm=.03)
    pg = analyze_pair(a, b)
    mf = compute_mesh_forces(mesh_name="oracle", pair_geom=pg,
                            total_torque_gear1_Nm=12., gear1_rel_speed_rpm=0.)
    lf = compute_iso6336_load_factors(pair_geom=pg, mesh_forces=mf,
        gear1_spec=a, gear2_spec=b, n1_rel_rpm=0., f_ma_um=0., f_sh_um=supplied)
    alpha = math.radians(20.)
    epsilon = (math.sqrt(22.**2-(20.*math.cos(alpha))**2)
        + math.sqrt(42.**2-(40.*math.cos(alpha))**2)-60.*math.sin(alpha))/(2.*math.pi*math.cos(alpha))
    c_prime = .8/(.04723+.15551/20.+.25791/40.)
    c_beta = .85*c_prime*(.75*epsilon+.25)
    fbx = 1.33*supplied
    ratio = (fbx-min(.15*fbx, 6.))*c_beta/75.
    expected = 1.+ratio if ratio <= 1. else 2.*math.sqrt(ratio)
    nf = (16./4.5)**2/(1.+16./4.5+(16./4.5)**2)
    assert lf.K_Hbeta == pytest.approx(expected, rel=1e-12)
    assert lf.K_Fbeta == pytest.approx(expected**nf, rel=1e-12)
    assert lf.K_v == 1.
    assert (ratio <= 1.) == (supplied <= 1.)


@pytest.mark.parametrize("bad", [-1., math.nan, math.inf, -math.inf, True, "0.1", []])
def test_low_level_fsh_validation(bad):
    a, b = gear(20), gear(40)
    pg = analyze_pair(a, b)
    mf = compute_mesh_forces(mesh_name="m", pair_geom=pg, total_torque_gear1_Nm=1., gear1_rel_speed_rpm=0.)
    with pytest.raises(ValueError):
        compute_iso6336_load_factors(pair_geom=pg, mesh_forces=mf,
            gear1_spec=a, gear2_spec=b, n1_rel_rpm=0., f_sh_um=bad)


@pytest.mark.parametrize("kind", ["pair", "planetary"])
def test_all_duty_and_peak_evaluations_receive_fsh(kind, monkeypatch):
    import motor_ai_sim.gears.gearbox as gearbox_module
    original = gearbox_module.rate_pair_iso6336
    calls = []

    def record(**kwargs):
        calls.append((kwargs["f_sh_um"], kwargs["mesh_forces"].F_t_N))
        return original(**kwargs)

    monkeypatch.setattr(gearbox_module, "rate_pair_iso6336", record)
    stage = stage_for(kind)
    fields = ({"f_sh_um": .11} if kind == "pair" else
              {"sun_planet_f_sh_um": .11, "planet_ring_f_sh_um": .11})
    spectrum = DutySpectrum([DutyBin(4., 100., 1.), DutyBin(6., 200., 1.)],
                           peak_torque_out_Nm=8., peak_events=2, peak_speed_out_rpm=100.)
    response = GearboxRequest([replace(stage, **fields)], operation="spectrum", spectrum=spectrum).execute()
    assert response.result.peak_member_ratings
    assert len({force for _, force in calls}) >= 3
    assert all(value == .11 for value, _ in calls)


def robot_request(fsh=None):
    g = lambda z, **kw: GearSpec(z, .6, 10.5, tooth_thickness_allowance_mm=.02, **kw)
    stage = PlanetaryStageSpec("robot", g(20), g(40), g(100, is_internal=True),
        stage_id="robot", sun_planet_f_sh_um=fsh, planet_ring_f_sh_um=fsh)
    return GearboxRequest([stage], input_speed_rpm=600., torque_Nm=40., torque_at="output",
        lubricant="ROBOT_GREASE_NLGI1_PAO", request_id="fsh_robot_sensitivity")


def test_robot_fsh_sensitivity_remains_estimated():
    baseline = robot_request().execute().result.stage_results[0]
    for fsh in (.1, .11, 1.):
        result = robot_request(fsh).execute().result.stage_results[0]
        for mesh in ("sun_planet", "planet_ring"):
            before, after = baseline.pair_ratings[mesh], result.pair_ratings[mesh]
            lf = after.load_factors
            assert lf.f_sh_um == fsh
            assert lf.f_ma_um > 1.33*fsh  # manufacturing/assembly now dominates Fbx
            assert lf.K_Hbeta < before.load_factors.K_Hbeta
            assert after.min_S_H > before.min_S_H
            assert lf.state == "estimated"
        assert result.pair_ratings["planet_ring"].gear2_rating.bending_state == "unsupported"
