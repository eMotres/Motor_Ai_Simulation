"""P2 of the propeller module: /api/propellers/design/* (routes/propellers.py -> propeller_design_api.py).

A throwaway app with only the propellers router; drafts go to a temporary folder.  Pins the answers, the validation, the draft store
(the caller's workspace, never the catalogue) and the access tier of the new paths (the catalogue routes stay open).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_design_api as pda
from motor_ai_sim import workspace
from motor_ai_sim.routes.propellers import router

D = pd.load_design()
B = "/api/propellers/design"


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def seed(diam=10, blades=2):
    return pd.seed_vector(D, diam, blades)


def with_tmp_drafts(fn):
    old = pda._draft_dir
    tmp = Path(tempfile.mkdtemp())
    pda._draft_dir = lambda: tmp / pda.DRAFT_DIR
    try:
        return fn(tmp)
    finally:
        pda._draft_dir = old


def test_family_lists_variables_bounds_and_operating_points():
    r = client().get(B + "/family", params={"diameter_in": 12, "blades": 3})
    assert r.status_code == 200
    j = r.json()
    assert j["id"] == "own_family" and j["banner"] == pd.BANNER and j["diameters_in"] == [10, 11, 12, 13] and j["blades_options"] == [2, 3]
    assert j["seed"]["diameter_in"] == 12 and j["seed"]["blades"] == 3
    assert sorted(j["operating_points"]) == ["L12", "L20"] and j["operating_points"]["L20"]["torque_limit_Nm"] == 0.82
    assert j["tip_mach_limit"] == {"value": 0.9, "confirmed": True, "source": j["tip_mach_limit"]["source"]}       # owner 2026-11-16: free, guard only
    assert j["inputs_unconfirmed"] is True and j["manufacturing"]["limits"] == {} and "wall_min_mm" in j["manufacturing"]["unset"]
    assert len(j["variables"]) == len(pd.variable_names(D))
    assert client().get(B + "/family", params={"diameter_in": 14}).status_code == 422
    assert client().get(B + "/family", params={"blades": 5}).status_code == 422


def test_check_and_geometry():
    c = client()
    ok = c.post(B + "/check", json={"vector": seed()}).json()
    assert ok == {"valid": True, "violations": []}
    bad = seed()
    bad["chord_R@0.45"] = 0.9
    j = c.post(B + "/check", json={"vector": bad}).json()
    assert j["valid"] is False and j["violations"][0]["rule"] == "out_of_bounds"
    g = c.post(B + "/geometry", json={"vector": seed(13, 3)}).json()
    assert g["propeller"]["schema"] == "propeller-2" and [z["zone"] for z in g["zones"]] == ["root", "mid", "tip"]
    assert len(g["outline"]) == pd.N_STATIONS and g["zones"][0]["active_limits"] == []
    assert g["zones"][0]["t_c_manufactured"] == pytest.approx(g["zones"][0]["t_c_ideal"], abs=1e-3)
    assert c.post(B + "/geometry", json={"vector": bad}).status_code == 422
    assert c.post(B + "/check", json={"vector": "x"}).status_code == 422
    assert c.post(B + "/check", json={"vector": {"blades": True}}).status_code == 422


def test_evaluate():
    c = client()
    r = c.post(B + "/evaluate", json={"vector": seed(), "op": "L12"})
    assert r.status_code == 200
    j = r.json()
    assert j["model_id"] == "bemt-1" and j["banner"] == pd.BANNER and j["objectives"]["thrust_per_shaft_W"] > 0
    assert j["estimated"] is True and j["validated_against"] is None and "root_flow_not_validated" in j["flags"]
    assert c.post(B + "/evaluate", json={"vector": seed(), "op": "L99"}).status_code == 422
    assert c.post(B + "/evaluate", json={"vector": seed(), "op": "L12", "v_inf_ms": 90}).status_code == 422
    bad = seed()
    bad["blades"] = 4
    r2 = c.post(B + "/evaluate", json={"vector": bad, "op": "L12"})
    assert r2.status_code == 422 and r2.json()["detail"]["violations"][0]["rule"] == "blades_not_offered"
    fwd = c.post(B + "/evaluate", json={"vector": seed(), "op": "L12", "v_inf_ms": 10.0}).json()
    assert fwd["operating"]["v_inf_ms"] == 10.0


def test_curve_against_rpm_with_the_motor_limit():
    c = client()
    j = c.post(B + "/curve", json={"vector": seed(), "op": "L12", "n": 5}).json()
    assert j["torque_limit_Nm"] == 0.64 and len(j["rows"]) == 5 and j["banner"] == pd.BANNER
    rpms = [r["rpm"] for r in j["rows"]]
    assert rpms == sorted(rpms) and rpms[0] == pytest.approx(0.4 * 13628.1) and rpms[-1] == pytest.approx(1.3 * 13628.1)
    t = [r["torque_Nm"] for r in j["rows"]]
    assert t == sorted(t) and all(x > 0 for x in t)
    assert j["rows"][-1]["thrust_N"] > j["rows"][0]["thrust_N"]
    for body in ({"n": 2}, {"n": 50}, {"n": True}, {"op": "L99"}, {"v_inf_ms": -1}):
        assert c.post(B + "/curve", json={"vector": seed(), "op": "L12", **body}).status_code == 422, body


def test_optimize_route_runs_stores_and_validates():
    def run(tmp):
        c = client()
        r = c.post(B + "/optimize", json={"vector": seed(11, 3), "op": "L12", "pop": 4, "generations": 1, "seed": 2})
        assert r.status_code == 200
        j = r.json()
        assert j["banner"] == pd.BANNER and j["operating_point"] == "L12" and j["front"] and j["estimated"] is True
        assert j["settings"]["pop"] == 4 and j["settings"]["tip_mach_limit_confirmed"] is True
        stored = tmp / pda.DRAFT_DIR / "_runs" / (j["run_id"] + ".jsonl")
        assert stored.is_file() and len(stored.read_text().splitlines()) == len(j["evaluations"])
        assert c.get(B + "/drafts").json() == {"drafts": []}                              # run files are not drafts
        for body in ({"pop": 3}, {"pop": 20, "generations": 10}, {"generations": "x"}, {"seed": True}, {"objectives": ["nope"]},
                     {"objectives": "thrust"}, {"free": []}, {"enforce_tip_mach": "yes"}, {"tip_mach_limit": 9}, {"op": "L99"}):
            payload = {"vector": seed(11, 3), "op": "L12", "pop": 4, "generations": 1}
            payload.update(body)
            assert c.post(B + "/optimize", json=payload).status_code == 422, body
        bad = seed(11, 3)
        bad["blades"] = 5
        assert c.post(B + "/optimize", json={"vector": bad, "op": "L12"}).status_code == 422

    with_tmp_drafts(run)


def test_sweep_one_variable():
    c = client()
    j = c.post(B + "/sweep", json={"vector": seed(), "op": "L12", "variable": "blades", "values": [2, 3, 4]}).json()
    assert [r["value"] for r in j["rows"]] == [2, 3, 4]
    assert j["rows"][0]["valid"] and j["rows"][1]["valid"] and not j["rows"][2]["valid"]
    assert j["rows"][2]["violations"][0]["rule"] == "blades_not_offered" and j["rows"][0]["design_id"] != j["rows"][1]["design_id"]
    k = c.post(B + "/sweep", json={"vector": seed(), "op": "L12", "variable": "twist_deg@0.45", "lo": 14.0, "hi": 22.0, "n": 3}).json()
    assert [round(r["value"], 6) for r in k["rows"]] == [14.0, 18.0, 22.0]
    assert all(r["valid"] for r in k["rows"]) is False or all("objectives" in r for r in k["rows"] if r["valid"])
    for body in ({"variable": "nope", "values": [1]}, {"variable": "blades"}, {"variable": "twist_deg@0.45", "lo": 5, "hi": 5, "n": 3},
                 {"variable": "twist_deg@0.45", "lo": 1, "hi": 9, "n": 99}, {"variable": "blades", "values": list(range(40))},
                 {"variable": "airfoil_tip", "lo": 0, "hi": 1}):
        assert c.post(B + "/sweep", json={"vector": seed(), "op": "L12", **body}).status_code == 422, body


def test_drafts_live_in_the_callers_workspace_and_round_trip():
    def run(tmp):
        c = client()
        assert c.get(B + "/drafts").json() == {"drafts": []}
        v = seed(11, 3)
        r = c.put(B + "/drafts/first.try", json={"vector": v, "op": "L20"}).json()
        assert r == {"saved": True, "name": "first.try", "valid": True, "violations": []}
        assert (tmp / pda.DRAFT_DIR / "first.try.json").is_file()
        got = c.get(B + "/drafts/first.try").json()
        assert got["vector"] == v and got["operating_point"] == "L20" and got["schema"] == "propeller-draft-1"
        broken = dict(v)
        broken["twist_deg@1"] = 99.0
        r2 = c.put(B + "/drafts/wip", json={"vector": broken}).json()
        assert r2["saved"] and not r2["valid"] and r2["violations"]                      # work in progress is kept, the violations are listed
        assert [d["name"] for d in c.get(B + "/drafts").json()["drafts"]] == ["first.try", "wip"]
        assert c.delete(B + "/drafts/wip").json() == {"deleted": True, "name": "wip"}
        assert c.get(B + "/drafts/wip").status_code == 404 and c.delete(B + "/drafts/wip").status_code == 404
        for bad_name in ("..%2Fx", ".hidden", "a b", "x" * 61):
            assert c.put(B + "/drafts/" + bad_name, json={"vector": v}).status_code in (404, 405, 422), bad_name
        assert c.put(B + "/drafts/ok", json={"vector": v, "op": "L99"}).status_code == 422
        assert c.put(B + "/drafts/ok", json={"vector": [1]}).status_code == 422

    with_tmp_drafts(run)


def test_the_draft_folder_is_under_the_workspace_root_never_the_catalogue():
    p = pda._draft_dir()
    assert p == Path(workspace.root()) / "propeller_designs"
    assert "propellers" not in p.parts[-2:-1] and p.name == "propeller_designs"
    cat = Path(__file__).resolve().parents[1] / "config" / "propellers"
    assert cat not in p.parents and p != cat


def test_access_tiers_the_designer_is_a_solve_the_catalogue_stays_open():
    old = os.environ.get("MOTRES_SOLVE_ADMIN_ONLY")
    try:
        os.environ.pop("MOTRES_SOLVE_ADMIN_ONLY", None)
        for m, p in (("GET", B + "/family"), ("POST", B + "/evaluate"), ("POST", B + "/sweep"), ("POST", B + "/optimize"), ("POST", B + "/curve"), ("PUT", B + "/drafts/x"),
                     ("GET", B + "/drafts"), ("DELETE", B + "/drafts/x")):
            assert auth.required_role(m, p) == "admin", (m, p)
        os.environ["MOTRES_SOLVE_ADMIN_ONLY"] = "0"
        for m, p in (("GET", B + "/family"), ("POST", B + "/evaluate"), ("PUT", B + "/drafts/x")):
            assert auth.required_role(m, p) == "engineer", (m, p)
    finally:
        if old is None:
            os.environ.pop("MOTRES_SOLVE_ADMIN_ONLY", None)
        else:
            os.environ["MOTRES_SOLVE_ADMIN_ONLY"] = old
    for m, p in (("GET", "/api/propellers"), ("GET", "/api/propellers/x"), ("GET", "/api/propellers/x/point"), ("GET", "/api/propellers/cooling-options")):
        assert auth.required_role(m, p) is None, (m, p)


def test_polar_source_is_selectable_per_request_and_defaults_to_the_flat_plate():
    c = client()
    fam = c.get(B + "/family", params={"diameter_in": 10, "blades": 3}).json()
    assert fam["polar_source"] == "flatplate_model" and fam["polar_sources"] == ["flatplate_model", "camber_model"]
    body = {"vector": seed(10, 3), "op": "L12"}
    flat = c.post(B + "/evaluate", json=body).json()
    same = c.post(B + "/evaluate", json=dict(body, polar_source=None)).json()
    assert same["thrust_N"] == flat["thrust_N"] and pd.F_AIRFOIL in flat["flags"]           # default unchanged
    cam = c.post(B + "/evaluate", json=dict(body, polar_source="camber_model")).json()
    assert pd.F_AIRFOIL_DRAG in cam["flags"] and cam["operating"]["rpm"] < flat["operating"]["rpm"]
    cur = c.post(B + "/curve", json=dict(body, n=3, polar_source="camber_model")).json()
    assert len(cur["rows"]) == 3
    for bad in ("neuralfoil", 5):
        assert c.post(B + "/evaluate", json=dict(body, polar_source=bad)).status_code == 422
