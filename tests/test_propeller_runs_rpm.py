"""Stored runs in the Designer, opt-in rpm / power operating points, exact half-inch diameters (owner 2026-11-22).

Pins: a half-inch diameter is kept exactly (round 6 ran 11.5 in as 11 in through an int() cast) and a diameter outside the family
is refused, not truncated; integer diameters keep their stored ids; an rpm / power override sets torque = P / omega and is recorded,
and no override leaves the design object unchanged; the motor check refuses a point the S1 model cannot hold and says "unchecked"
without a model; S1 ``continuous_torque`` stops at a limit; every stored run writes its settings file and the runs routes list it
and return its evaluations.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_design_api as pda
from motor_ai_sim import propeller_optimize as po
from motor_ai_sim import propeller_s1 as s1
from motor_ai_sim.routes.propellers import router
from tests.test_propeller_optimize import stub
from tests.test_propeller_s1 import _model

D = pd.load_design()
B = "/api/propellers/design"


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def with_tmp_drafts(fn):
    old = pda._draft_dir
    tmp = Path(tempfile.mkdtemp())
    pda._draft_dir = lambda: tmp / pda.DRAFT_DIR
    try:
        return fn(tmp)
    finally:
        pda._draft_dir = old


def test_half_inch_diameter_is_kept_and_unknown_diameters_are_refused():
    v = pd.seed_vector(D, 11.5, 2)
    assert v["diameter_in"] == 11.5 and pd.check_candidate(D, v) == []
    p2 = pd.candidate_to_propeller2(D, v)
    assert p2["geometry"]["diameter_mm"] == pytest.approx(11.5 * 25.4) and p2["id"].startswith("own_11.5in_2b_")
    v12 = pd.seed_vector(D, 12, 2)
    assert v12["diameter_in"] == 12 and isinstance(v12["diameter_in"], int)
    assert pd.candidate_to_propeller2(D, v12)["id"].startswith("own_12in_2b_")      # integer ids as before
    for bad in (11.3, 9.5, 14.5):
        w = dict(v, diameter_in=bad)
        assert any(b["rule"] == "diameter_not_in_family" for b in pd.check_candidate(D, w)), bad
    assert 14 in D.diameters_in and 13.5 in D.diameters_in


def test_operating_override_sets_torque_from_power_and_is_recorded():
    assert pd.with_operating_point(D, "L12") is D                                  # no override: the same object
    d2 = pd.with_operating_point(D, "L12", rpm=12000.0, power_W=800.0)
    o = d2.operating_points["L12"]
    assert o.rated_rpm_ref == 12000.0 and o.torque_limit_Nm == pytest.approx(800.0 / (2 * math.pi * 200.0))
    assert D.operating_points["L12"].rated_rpm_ref == 13628.1                        # the stored point is untouched
    ov = d2.raw["operating_override"]["L12"]
    assert ov["power_W"] == 800.0 and ov["stored_rpm"] == 13628.1
    d3 = pd.with_operating_point(D, "L20", rpm=14000.0)                              # rpm only: the stored torque limit
    assert d3.operating_points["L20"].torque_limit_Nm == D.operating_points["L20"].torque_limit_Nm
    for kw in ({"rpm": 10.0}, {"power_W": -5.0}, {"rpm": float("nan")}, {"rpm": True}):
        with pytest.raises(pd.DesignError):
            pd.with_operating_point(D, "L12", **kw)
    with pytest.raises(pd.DesignError):
        pd.with_operating_point(D, "L99", rpm=12000.0)


def test_evaluation_record_carries_the_override_only_when_given():
    v = pd.seed_vector(D, 11, 3)
    plain = pd.evaluate_candidate(D, v, "L12")
    assert "override" not in plain["operating"]
    d2 = pd.with_operating_point(D, "L12", rpm=12000.0, power_W=800.0)
    rec = pd.evaluate_candidate(d2, v, "L12", flight=True)
    assert rec["operating"]["override"]["power_W"] == 800.0 and rec["operating"]["rpm"] == pytest.approx(12000.0)


def test_continuous_torque_stops_at_a_limit():
    m = _model()
    env = s1.continuous_torque(m, 6000.0, 15.0)
    assert env["torque_Nm"] > 0 and env["stops"] is not None
    a = env["at"]
    lims = s1.limits_of(m)
    assert a["T_winding_C"] <= lims["winding_C"] and a["T_magnet_C"] <= lims["magnet_C"] and a["current_A"] <= lims["I_max_A"]
    # more air holds more torque
    assert s1.continuous_torque(m, 6000.0, 30.0)["torque_Nm"] >= env["torque_Nm"]


def test_motor_check_refuses_what_the_motor_cannot_hold(monkeypatch):
    monkeypatch.setattr(s1, "load_model", lambda die, cfg: {"fake": True})
    monkeypatch.setattr(s1, "continuous_torque", lambda m, rpm, air, **k: {"torque_Nm": 0.5, "stops": {"state": "winding"}})
    with pytest.raises(pda.ApiProblem) as e:
        pda._operating(D, "L12", 12000.0, 800.0, True)                               # needs 0.637 N*m
    assert e.value.status == 422 and e.value.detail["motor_check"]["status"] == "exceeds"
    d, mc = pda._operating(D, "L12", 12000.0, 600.0, True)                           # 0.477 N*m
    assert mc["status"] == "ok" and mc["air_speed_ms"] == 17.0 and d.operating_points["L12"].rated_rpm_ref == 12000.0

    def missing(die, cfg):
        raise s1.S1ModelNotFound("none")
    monkeypatch.setattr(s1, "load_model", missing)
    _, mc = pda._operating(D, "L12", 12000.0, 800.0, True)
    assert mc["status"] == "unchecked"
    assert pda._operating(D, "L12", None, None, True) == (D, None)


def test_stored_run_settings_file_and_runs_routes(monkeypatch):
    monkeypatch.setattr(s1, "load_model", lambda die, cfg: (_ for _ in ()).throw(s1.S1ModelNotFound("none")))

    def run(tmp):
        store = tmp / pda.DRAFT_DIR / "_runs"
        d2 = pd.with_operating_point(D, "L12", rpm=12000.0, power_W=800.0)
        r = po.optimize(d2, pd.seed_vector(D, 11.5, 2), "L12", pop=4, generations=1, seed=3, evaluate=stub, store_dir=store,
                        motor_check={"status": "ok"})
        side = json.loads((store / (r["run_id"] + ".run.json")).read_text())
        op = side["settings"]["operating"]
        assert side["schema"] == po.RUN_SCHEMA and side["n_evaluations"] == len(r["evaluations"])
        assert op["rpm"] == 12000.0 and op["shaft_power_W"] == pytest.approx(800.0) and op["override"]["power_W"] == 800.0
        assert "motor_envelope" not in side["settings"]["not_checked"]               # checked ok -> not listed as unchecked
        assert side["best"]["thrust_per_shaft_W"]["value"] == max(e["objectives"]["thrust_per_shaft_W"] for e in r["evaluations"]
                                                                     if e["constraints_met"])
        c = client()
        lst = c.get(B + "/runs").json()["runs"]
        assert [x["run_id"] for x in lst] == [r["run_id"]] and lst[0]["diameter_in"] == 11.5
        one = c.get(B + "/runs/" + r["run_id"]).json()
        assert len(one["evaluations"]) == len(r["evaluations"]) and {"feasible", "rpm", "vector"} <= set(one["evaluations"][0])
        assert c.get(B + "/runs/opt-0000000000").status_code == 404
        assert c.get(B + "/runs/../../etc").status_code in (404, 422)
        assert c.get(B + "/runs/not-a-run").status_code == 422
        # the route passes rpm / power through; an unknown S1 model leaves the motor envelope listed as unchecked
        j = c.post(B + "/optimize", json={"vector": pd.seed_vector(D, 11, 3), "op": "L12", "pop": 4, "generations": 1,
                                          "rpm": 12000, "power_W": 700}).json()
        assert j["settings"]["operating"]["rpm"] == 12000.0 and "motor_envelope" in j["settings"]["not_checked"]
        assert j["settings"]["operating"]["motor_check"]["status"] == "unchecked"
        assert c.post(B + "/optimize", json={"vector": pd.seed_vector(D, 11, 3), "op": "L12", "pop": 4, "generations": 1,
                                             "rpm": 5}).status_code == 422

    with_tmp_drafts(run)
