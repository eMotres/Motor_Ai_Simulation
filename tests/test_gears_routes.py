"""Stage 2 of the gear module: /api/gears routes (routes/gears.py).

A throwaway app with only the gears router.  Pins: staff-only access, the field-path 422 body, every operation, the
unchanged core envelope (round trip against ``GearboxRequest.execute()``) and the outline payload options.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.gears import (DutyBin, DutySpectrum, GearSpec, GearboxRequest, GearboxResponse, PairStageSpec,
                                PlanetaryStageSpec, list_bearing_catalogue, list_lubricant_catalogue,
                                list_material_catalogue, list_preset_catalogue)
from motor_ai_sim.routes.gears import router

B = "/api/gears"


def gear(z, **kw):
    return GearSpec(z, 1., kw.pop("b_mm", 12.), tooth_thickness_allowance_mm=.04, **kw)


def pair_request(**kw):
    return GearboxRequest([PairStageSpec("p", gear(20), gear(40), stage_id="stage_p")], request_id="req_fixed", **kw)


def planetary_request(**kw):
    st = PlanetaryStageSpec("pl", gear(40), gear(20), gear(80, is_internal=True), stage_id="stage_pl")
    return GearboxRequest([st], request_id="req_planet", **kw)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", set())
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def enforced(monkeypatch):
    """Auth on; the bearer token IS the role name (resolve_user is replaced)."""
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"owner@example.com"})
    monkeypatch.setattr(auth, "resolve_user", lambda authorization, **kw: (
        {"uid": "u", "email": authorization.split()[-1] + "@example.com", "role": authorization.split()[-1]}
        if authorization else None))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def as_(role):
    return {"Authorization": "Bearer " + role}


# ----------------------------------------------------------------------------- access

def test_staff_ok_and_everyone_else_refused(enforced):
    body = pair_request().to_dict()
    assert enforced.post(B + "/evaluate", json=body).status_code == 401
    for role in ("user", "oem"):
        assert enforced.post(B + "/evaluate", json=body, headers=as_(role)).status_code == 403
        assert enforced.get(B + "/catalogues", headers=as_(role)).status_code == 403
        assert enforced.post(B + "/pose", json=body, headers=as_(role)).status_code == 403
        assert enforced.post(B + "/assembly", json=body, headers=as_(role)).status_code == 403
    for role in ("engineer", "admin"):
        assert enforced.post(B + "/evaluate", json=body, headers=as_(role)).status_code == 200
        assert enforced.get(B + "/catalogues", headers=as_(role)).status_code == 200


def test_role_gate_prefix_registered():
    assert ("POST", "/api/gears", "engineer") in auth._GATED_PREFIX
    assert ("GET", "/api/gears", "engineer") in auth._GATED_PREFIX
    assert auth.required_role("POST", "/api/gears/evaluate") == "engineer"


# ----------------------------------------------------------------------------- operations

def envelope_ok(payload, operation, request):
    assert payload["schema_type"] and payload["schema_version"] == 1
    assert payload["operation"] == operation and payload["request_id"] == request.request_id
    assert payload["core_version"] == "gear-core-r4"
    assert payload["request_hash"] == GearboxResponse.from_dict(payload).request_hash
    json.dumps(payload, allow_nan=False)


def test_evaluate_point_round_trip_against_core(client):
    req = pair_request(torque_Nm=2., input_speed_rpm=500.)
    r = client.post(B + "/evaluate", json=req.to_dict())
    assert r.status_code == 200
    payload = r.json()
    envelope_ok(payload, "point", req)
    assert payload == json.loads(json.dumps(req.execute().to_dict(), allow_nan=False))
    # point results are never a full-duty qualification: the core's own flag and reasons pass through
    assert payload["result"]["passed"] is False and payload["result"]["pass_reasons"]
    assert GearboxResponse.from_dict(payload).to_dict() == payload


def test_evaluate_spectrum_round_trip_and_default_operation(client):
    req = pair_request(operation="spectrum", spectrum=DutySpectrum([DutyBin(2., 100., 1.)]))
    body = req.to_dict()
    expected = json.loads(json.dumps(req.execute().to_dict(), allow_nan=False))
    r = client.post(B + "/evaluate", json=body)
    assert r.status_code == 200 and r.json() == expected
    envelope_ok(r.json(), "spectrum", req)
    assert isinstance(r.json()["result"]["passed"], bool)
    assert "pass_reasons" in r.json()["result"]
    body.pop("operation")
    assert client.post(B + "/evaluate", json=body).json() == expected  # operation inferred from the spectrum


def test_assembly_round_trip_and_outline_options(client):
    req = planetary_request(operation="assembly")
    body = req.to_dict()
    full = client.post(B + "/assembly", json=body)
    assert full.status_code == 200
    assert full.json() == json.loads(json.dumps(req.execute().to_dict(), allow_nan=False))
    envelope_ok(full.json(), "assembly", req)
    body.update(outline_once=True, outline_stride=4)
    compact = client.post(B + "/assembly", json=body)
    assert compact.status_code == 200
    members = compact.json()["result"]["members"]
    assert all(m["outline"] is None and m["outline_id"] in compact.json()["result"]["outlines"] for m in members)
    assert len(compact.content) < len(full.content) / 3
    expected = json.loads(json.dumps(
        planetary_request(operation="assembly", outline_once=True, outline_stride=4).execute().to_dict(), allow_nan=False))
    assert compact.json() == expected | {"request_hash": compact.json()["request_hash"]}
    assert compact.json()["request_hash"] != full.json()["request_hash"]  # viewer options are part of the request


def test_pose_matches_core_and_member_instances(client):
    req = planetary_request(operation="pose", input_angle_deg=37.)
    r = client.post(B + "/pose", json=req.to_dict())
    assert r.status_code == 200
    assert r.json() == json.loads(json.dumps(req.execute().to_dict(), allow_nan=False))
    envelope_ok(r.json(), "pose", req)
    assert r.json()["member_instances"]["stage_pl.planet"] == [f"stage_pl.planet_{i}" for i in range(3)]
    assert r.json()["result"]["input_angle_deg"] == 37.
    assert all("member_id" in m for m in r.json()["result"]["members"].values())


def test_stable_ids_survive_a_rename(client):
    req = pair_request()
    renamed = GearboxRequest([PairStageSpec("renamed", gear(20), gear(40), stage_id="stage_p")], request_id="req_fixed")
    a = client.post(B + "/evaluate", json=req.to_dict()).json()
    b = client.post(B + "/evaluate", json=renamed.to_dict()).json()
    assert a["request_id"] == b["request_id"] == "req_fixed"
    assert a["member_instances"] == b["member_instances"]


# ----------------------------------------------------------------------------- validation

def errors(response):
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "validation"
    return detail["errors"]


def test_nested_field_path_in_422(client):
    body = pair_request().to_dict()
    body["stages"][0]["gear1"]["z"] = "20"
    errs = errors(client.post(B + "/evaluate", json=body))
    assert errs[0]["path"] == "/stages/0/gear1/z" and errs[0]["code"] == "invalid_value" and errs[0]["message"]


def test_missing_field_unknown_reference_and_request_level_paths(client):
    body = pair_request().to_dict()
    del body["stages"][0]["gear2"]["z"]
    assert errors(client.post(B + "/evaluate", json=body))[0] == dict(
        path="/stages/0/gear2/z", code="missing_field", message=errors(client.post(B + "/evaluate", json=body))[0]["message"])
    body = pair_request().to_dict()
    body["preset_name"] = "absent"
    assert errors(client.post(B + "/evaluate", json=body))[0]["path"] == "/preset_name"
    body = pair_request(operation="assembly").to_dict()
    body["outline_stride"] = 0
    assert errors(client.post(B + "/assembly", json=body))[0]["path"] == "/outline_stride"
    body = pair_request().to_dict()
    body["stages"][0]["gear1"]["material_name"] = "absent"
    e = errors(client.post(B + "/evaluate", json=body))[0]
    assert e["path"] == "/stages/0/gear1/material_name" and e["code"] == "unknown_reference"


def test_operation_is_fixed_by_the_route(client):
    body = pair_request().to_dict()
    assert errors(client.post(B + "/pose", json=body))[0]["path"] == "/operation"      # body says "point"
    assert errors(client.post(B + "/assembly", json=body))[0]["path"] == "/operation"
    body["operation"] = "pose"
    assert errors(client.post(B + "/evaluate", json=body))[0]["path"] == "/operation"
    assert errors(client.post(B + "/evaluate", json=[1, 2]))[0]["path"] == ""


def test_spectrum_without_spectrum_is_refused(client):
    body = pair_request().to_dict()
    body["operation"] = "spectrum"
    assert errors(client.post(B + "/evaluate", json=body))[0]["path"] == "/spectrum"


# ----------------------------------------------------------------------------- catalogues

def test_catalogues_match_the_core(client):
    r = client.get(B + "/catalogues")
    assert r.status_code == 200
    assert r.json() == json.loads(json.dumps(dict(
        materials=list_material_catalogue(), lubricants=list_lubricant_catalogue(),
        bearings=list_bearing_catalogue(), presets=list_preset_catalogue())))
    for name, count in (("materials", 7), ("lubricants", 6), ("bearings", 12), ("presets", 2)):
        one = client.get(B + "/catalogues/" + name).json()
        assert list(one) == [name] and len(one[name]) == count == len(r.json()[name])
    missing = client.get(B + "/catalogues/absent")
    assert missing.status_code == 404 and "presets" in missing.json()["detail"]["catalogues"]


# ----------------------------------------------------------------------------- template

def test_template_default_is_complete_and_evaluates(client):
    r = client.post(B + "/template", json={})
    assert r.status_code == 200
    body = r.json()
    assert GearboxRequest.from_dict(body).to_dict() == body
    assert body["preset_name"] == "humanoid_robot_joint" and len(body["stages"]) == 2
    assert client.post(B + "/evaluate", json=body).status_code == 200


def test_template_edit_pair_and_bearings_and_spectrum(client):
    plain = {"preset_name": "standard", "stages": [
        {"kind": "pair", "name": "p", "gears": {"gear1": {"z": 20, "m_n_mm": 1., "b_mm": 12.}, "gear2": {"z": 40, "m_n_mm": 1., "b_mm": 12.}},
         "bearings": {"bearing_1a": "618/8-2Z"}}],
        "spectrum": {"bins": [{"torque_out_Nm": 2., "speed_out_rpm": 100., "duration_h": 1.}, {"torque_out_Nm": 1., "speed_out_rpm": 100., "duration_h": 1.}]}}
    r = client.post(B + "/template", json=plain)
    assert r.status_code == 200
    body = r.json()
    assert body["operation"] == "spectrum" and body["stages"][0]["bearing_1a"]["name"] == "618/8-2Z"
    assert GearboxRequest.from_dict(body).to_dict() == body


def test_template_compound_stage_kind(client):
    g = lambda z: {"z": z, "m_n_mm": 1., "b_mm": 12.}
    plain = {"stages": [{"kind": "compound_planetary", "name": "c",
                         "gears": {"sun": g(30), "planet": g(40), "planet2": g(30), "ring": g(100)}}]}
    r = client.post(B + "/template", json=plain)
    assert r.status_code == 200 and r.json()["stages"][0]["kind"] == "compound_planetary"


def test_template_errors_carry_field_paths(client):
    bad = {"stages": [{"kind": "planetary", "gears": {"sun": {"z": -1, "m_n_mm": 1., "b_mm": 5.}}}]}
    r = client.post(B + "/template", json=bad)
    assert r.status_code == 422
    err = r.json()["detail"]["errors"][0]
    assert err["path"].startswith("/stages/0/gears/sun")
    r = client.post(B + "/template", json={"stages": [{"kind": "pair", "bearings": {"bearing_1a": "absent"}}]})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["path"] == "/stages/0/bearings/bearing_1a"
    assert client.post(B + "/template", json={"nonsense": 1}).status_code == 422


def test_template_staff_only(enforced):
    assert enforced.post(B + "/template", json={}).status_code == 401
    assert enforced.post(B + "/template", json={}, headers=as_("user")).status_code == 403
    assert enforced.post(B + "/template", json={}, headers=as_("engineer")).status_code == 200


def test_template_default_has_no_bearings_and_auto_fits(client):
    from motor_ai_sim.routes.gears import planet_bearing_default
    body = client.post(B + "/template", json={}).json()
    for stage in body["stages"]:
        assert stage["input_bearing"] is None and stage["planet_bearing"] is None and stage["output_bearing"] is None
    cat = {b["name"]: b for b in list_bearing_catalogue()}
    assert planet_bearing_default(32, .5) == "W 638/2-2Z" and cat["W 638/2-2Z"]["D_mm"] <= .5 * (32 - 2.5) - 1.5
    assert planet_bearing_default(8, 0.5) is None      # nothing fits a tiny planet: left empty
    auto = {"stages": [{"kind": "planetary", "name": "a", "bearings": {"planet_bearing": "auto"},
                        "gears": {"sun": {"z": 40, "m_n_mm": .5, "b_mm": 10.}, "planet": {"z": 32, "m_n_mm": .5, "b_mm": 10.}, "ring": {"z": 104, "m_n_mm": .5, "b_mm": 10.}}}]}
    assert client.post(B + "/template", json=auto).json()["stages"][0]["planet_bearing"]["name"] == "W 638/2-2Z"
    tiny = {"stages": [{"kind": "planetary", "name": "a", "bearings": {"planet_bearing": "auto"},
                        "gears": {"sun": {"z": 40, "m_n_mm": .5, "b_mm": 10.}, "planet": {"z": 8, "m_n_mm": .5, "b_mm": 10.}, "ring": {"z": 56, "m_n_mm": .5, "b_mm": 10.}}}]}
    r = client.post(B + "/template", json=tiny)
    assert r.status_code in (200, 422)   # no card fits: no bearing is set (the core may refuse the geometry on its own terms)
    if r.status_code == 200: assert r.json()["stages"][0]["planet_bearing"] is None
    # an explicit empty bearings block removes them
    bare = client.post(B + "/template", json={"stages": [{"kind": "pair", "name": "p", "gears": {"gear1": {"z": 20, "m_n_mm": 1., "b_mm": 12.}, "gear2": {"z": 40, "m_n_mm": 1., "b_mm": 12.}}}]}).json()
    assert bare["stages"][0]["bearing_1a"] is None

def test_template_carries_the_optional_misalignment_inputs_and_absent_stays_absent(client):
    g = lambda z, b=12.: {"z": z, "m_n_mm": 1., "b_mm": b}
    planetary = {"kind": "planetary", "name": "s", "gears": {"sun": g(40), "planet": g(32), "ring": g(104)}}
    pair = {"kind": "pair", "name": "p", "gears": {"gear1": g(20), "gear2": g(40)}}
    r = client.post(B + "/template", json={"stages": [dict(planetary), dict(pair)]})
    assert r.status_code == 200
    stages = r.json()["stages"]
    for st in stages:
        assert not {"f_ma_um", "sun_planet_f_ma_um", "planet_ring_f_ma_um"} & set(st)          # absent fields do not appear in the record
    supplied = {"stages": [{**planetary, "sun_planet_f_ma_um": 1.5, "planet_ring_f_ma_um": 0}, {**pair, "f_ma_um": 2.0}]}
    r = client.post(B + "/template", json=supplied)
    assert r.status_code == 200
    body = r.json()
    assert body["stages"][0]["sun_planet_f_ma_um"] == 1.5 and body["stages"][0]["planet_ring_f_ma_um"] == 0   # 0 is "supplied"
    assert body["stages"][1]["f_ma_um"] == 2.0
    assert GearboxRequest.from_dict(body).to_dict() == body                                   # schema v1 round trip
    assert client.post(B + "/evaluate", json=body).status_code == 200
    bad = client.post(B + "/template", json={"stages": [{**planetary, "sun_planet_f_ma_um": -1}]})
    assert bad.status_code == 422 and "f_ma" in bad.text


def test_template_carries_the_optional_f_sh_inputs_and_absent_stays_absent(client):
    g = lambda z, b=12.: {"z": z, "m_n_mm": 1., "b_mm": b}
    planetary = {"kind": "planetary", "name": "s", "gears": {"sun": g(40), "planet": g(32), "ring": g(104)}}
    pair = {"kind": "pair", "name": "p", "gears": {"gear1": g(20), "gear2": g(40)}}
    r = client.post(B + "/template", json={"stages": [dict(planetary), dict(pair)]})
    assert r.status_code == 200
    for st in r.json()["stages"]:
        assert not {"f_sh_um", "sun_planet_f_sh_um", "planet_ring_f_sh_um"} & set(st)
    r = client.post(B + "/template", json={"stages": [{**planetary, "sun_planet_f_sh_um": 0.1, "planet_ring_f_sh_um": 0}, {**pair, "f_sh_um": 1.0}]})
    assert r.status_code == 200
    body = r.json()
    assert body["stages"][0]["sun_planet_f_sh_um"] == 0.1 and body["stages"][0]["planet_ring_f_sh_um"] == 0 and body["stages"][1]["f_sh_um"] == 1.0
    assert GearboxRequest.from_dict(body).to_dict() == body
    assert client.post(B + "/evaluate", json=body).status_code == 200
    assert client.post(B + "/template", json={"stages": [{**planetary, "planet_ring_f_sh_um": -1}]}).status_code == 422
