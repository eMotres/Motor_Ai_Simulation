"""ONE Configure load, resolved on the server (``motor_ai_sim.configure_load`` and
``GET /api/catalog/{id}/configure_load``, owner 2026-10-19).

* the target is ONE configuration + duty: the requested duty, else ``rated``; a rated variant
  such as ``rated_short_time`` is never a preset (rated is continuous, short-time is peak only);
* the propeller is the page's own default rule;
* the series and the S1 limit in the answer are EXACTLY the existing routes' answers for the
  same request (no second copy of the physics);
* a machine without propeller cooling gets no propeller part; an unknown configuration is 404.
Closed-form, no FEM.
"""
from __future__ import annotations

import json
import types

import pytest
import yaml

from motor_ai_sim import configure_load as ld
from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_s1 as s1
from tests.test_propeller_s1 import _model, _prop

S6 = {"chemistry": "NMC", "cells": 6, "v_cell_min": 3.0, "v_cell_nom": 3.7, "v_cell_max": 4.2,
      "v_min": 18.0, "v_nom": 22.2, "v_max": 25.2}


def _dp(name, i, rpm, cls=None):
    d = {"name": name, "current_A": i, "rpm": rpm, "gamma_deg": 2.0, "mode": "motor"}
    if cls:
        d["duty_class"] = cls
    return d


# 85 L15 as it stood on 2026-10-19 (before the owner deleted rated_short_time on the server)
D85_L15 = {"config": "L15", "die": "CIANO28 85 20SW1200", "duty": "rated_short_time",
           "duty_points": [_dp("rated_short_time", 25.88, 1000.0, "short_time"),
                           _dp("peak", 45.962, 1000.0, "short_time"),
                           _dp("rated", 6.3, 1000.0, "continuous")],
           "battery": S6, "drive_variant": "si_48k", "pwm_variants": [{"id": "si_48k"}]}


def test_a_rated_variant_is_never_a_preset_and_rated_comes_first():
    names = [m["name"] for m in ld.preset_modes(D85_L15)]
    assert names == ["rated", "peak"]
    assert ld.pick_mode(D85_L15, None)["name"] == "rated"
    assert ld.pick_mode(D85_L15, "peak")["name"] == "peak"
    assert ld.pick_mode(D85_L15, "rated_short_time")["name"] == "rated"   # not a button: falls back
    assert ld.pick_mode(D85_L15, "nope")["name"] == "rated"
    assert ld.pick_mode(D85_L15, None)["current_A"] == 6.3
    assert not ld.is_preset_mode("Rated short time") and ld.is_preset_mode("rated")
    assert ld.pick_mode({"duty_points": []}, None) is None


def test_the_preset_pack_is_the_pages_window():
    assert ld.preset_pack({"battery": S6}) == {"min": 6 * 3.0, "max": 6 * 4.2}
    assert ld.preset_pack({"battery": {"cells": 12, "v_min": 36.0, "v_max": 50.4}}) == pytest.approx(
        {"min": 36.0, "max": 50.4})
    assert ld.preset_pack({"battery": None}) is None


def test_the_propeller_follows_the_pages_default_rule():
    P = lambda sel, pd="measured_torque": types.SimpleNamespace(selectable=sel, power_data=pd)  # noqa: E731
    cat = {"a": P(True, "estimated"), "b": P(True), "c": P(False), "d": P(True)}
    cool = {"cooling_options": ["propeller_air"], "propellers": ["c", "a", "b", "d", "zz"],
            "defaults": {"L12": "d", "L20": "c"}}
    assert ld.pick_propeller(cool, "L12", None, cat) == "d"           # the configuration's default
    assert ld.pick_propeller(cool, "L12", "a", cat) == "a"            # the viewer's own pick
    assert ld.pick_propeller(cool, "L12", "c", cat) == "d"            # not computable: ignored
    assert ld.pick_propeller(cool, "L20", None, cat) == "b"           # bad default: first measured
    assert ld.pick_propeller({**cool, "cooling_options": ["robotics"]}, "L12", None, cat) is None
    assert ld.pick_propeller({}, "L12", None, cat) is None


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """A propeller catalogue, an S1 model and a cooling registry in tmp; the context is synthetic."""
    from motor_ai_sim import cooling_options as co
    from motor_ai_sim import motor_access as ma
    from motor_ai_sim.routes import catalog as cat_mod
    prop = _prop(tmp_path)
    (tmp_path / "props" / "v").mkdir(parents=True)
    (tmp_path / "props" / "v" / "syn_s1.yaml").write_text(yaml.safe_dump(prop.raw), encoding="utf-8")
    monkeypatch.setattr(pp, "_DIR", tmp_path / "props")
    (tmp_path / "s1" / "TEST DIE").mkdir(parents=True)
    (tmp_path / "s1" / "TEST DIE" / "L1.json").write_text(json.dumps(_model()), encoding="utf-8")
    monkeypatch.setattr(s1, "_DIR", tmp_path / "s1")
    f = tmp_path / "co.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {"TEST DIE": {
        "cooling_options": ["propeller_air"], "propellers": ["syn_s1"]}}}), encoding="utf-8")
    monkeypatch.setattr(co, "_FILE", f)
    monkeypatch.setattr(ma, "catalog_access", lambda a=None: {"mode": ma.MODE_ALL, "dies": frozenset()})
    monkeypatch.setattr(ma, "may_see_config", lambda acc, die, cfg: True)
    ctx = {"motor_id": "cat_t",
           "cooling": {"die": "TEST DIE", "config": "L1", "cooling_options": ["propeller_air"],
                       "propellers": ["syn_s1"], "defaults": {}, "restricted": True},
           "presets": [{"config": "L1", "die": "TEST DIE", "duty": "rated",
                        "knobs": {"L_mm": 15, "N": 7, "wireH_mm": 0.6, "split": 1, "nP": 1,
                                  "I_A": 20.0, "rpm": 6000.0},
                        "duty_points": [_dp("rated_short_time", 30.0, 7000.0, "short_time"),
                                        _dp("rated", 20.0, 6000.0), _dp("peak", 40.0, 8000.0)],
                        "battery": S6, "pwm_variants": [], "drive_variant": None}]}
    robot = {"motor_id": "cat_r", "cooling": {"die": "R", "cooling_options": ["robotics"], "propellers": []},
             "presets": [{"config": "L15", "die": "R", **{k: D85_L15[k] for k in ("duty_points", "battery")},
                          "duty": "rated", "knobs": {}, "pwm_variants": [], "drive_variant": None}]}

    def fake_ctx(motor_id, authorization=None):
        if motor_id == "cat_t":
            return json.loads(json.dumps(ctx))
        if motor_id == "cat_r":
            return json.loads(json.dumps(robot))
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="not found")
    monkeypatch.setattr(cat_mod, "get_configure_context", fake_ctx)
    return tmp_path


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import catalog as cat_mod
    from motor_ai_sim.routes import propellers as prop_mod
    app = FastAPI()
    app.include_router(cat_mod.router)
    app.include_router(prop_mod.router)
    return TestClient(app)


def test_one_answer_carries_the_target_and_the_existing_routes_numbers(client, wired):
    q = {"config": "L1", "ambient_c": 30, "housing_d_mm": 40, "series_rpm_max": 12000, "series_n": 41}
    r = client.get("/api/catalog/cat_t/configure_load", params=q)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["config"] == "L1" and j["duty"] == "rated" and j["context"]["motor_id"] == "cat_t"
    pr = j["propeller"]
    assert pr["id"] == "syn_s1" and pr["drive"] == "sine" and pr["pack"] == {"min": 18.0, "max": 6 * 4.2}
    series = client.get("/api/propellers/syn_s1/series", params={
        "rpm_max": 12000, "n": 41, "temp_c": 30, "housing_d_mm": 40}).json()
    assert pr["series"] == series and len(series["h_W_m2K"]) == 41
    s1_direct = client.get("/api/propellers/syn_s1/s1", params={
        "die": "TEST DIE", "config": "L1", "ambient_c": 30, "drive": "sine",
        "pack_v_min": 18.0, "pack_v_max": 6 * 4.2})
    assert s1_direct.status_code == 200, s1_direct.text
    assert pr["s1"] == s1_direct.json() and pr["s1"]["limit"]["rpm"] > 0
    assert pr["s1_error"] is None and pr["series_error"] is None
    # THE thermal model of the configuration travels with the load (the page evaluates it itself)
    assert j["thermal_model"]["schema"] == "configure-thermal-1" and j["thermal_model"]["config"] == "L1"
    assert pr["slipstream_factor"] == j["thermal_model"]["slipstream_factor"]


def test_the_series_uses_the_models_own_slipstream_factor(client, wired):
    m = json.loads((wired / "s1" / "TEST DIE" / "L1.json").read_text(encoding="utf-8"))
    m["thermal_calibration"] = {"housing_film_factor": 0.5, "slipstream_factor": 0.7}
    (wired / "s1" / "TEST DIE" / "L1.json").write_text(json.dumps(m), encoding="utf-8")
    j = client.get("/api/catalog/cat_t/configure_load", params={"config": "L1", "housing_d_mm": 40,
                                                                "series_rpm_max": 12000, "series_n": 41}).json()
    assert j["propeller"]["slipstream_factor"] == 0.7
    with_f = client.get("/api/propellers/syn_s1/series", params={"rpm_max": 12000, "n": 41, "temp_c": 25,
                                                                  "housing_d_mm": 40, "factor": 0.7}).json()
    assert j["propeller"]["series"] == with_f


def test_the_requested_duty_and_the_robot_machine(client, wired):
    j = client.get("/api/catalog/cat_t/configure_load", params={"config": "L1", "duty": "peak",
                                                                "housing_d_mm": 40}).json()
    assert j["duty"] == "peak"
    j = client.get("/api/catalog/cat_t/configure_load", params={"config": "L1", "duty": "rated_short_time"}).json()
    assert j["duty"] == "rated"
    j = client.get("/api/catalog/cat_r/configure_load", params={"config": "L15"}).json()
    assert j["duty"] == "rated" and j["propeller"] is None          # still air: no propeller part
    assert client.get("/api/catalog/cat_t/configure_load", params={"config": "L9"}).status_code == 404
    assert client.get("/api/catalog/nope/configure_load", params={"config": "L1"}).status_code == 404
