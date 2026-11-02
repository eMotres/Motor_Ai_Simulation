"""The mechanics routes take the torque (and speed) of THE MACHINE ASKED ABOUT (2026-11-02).

Bug: recomputing a motor and two generators that share one die, the rotor-mechanics route filled a missing torque
from the last run of the process - for one generator that was another machine's torque, and the answer went
straight to the caller (the mechanics result is not stored).  Now a missing torque comes from the machine's own
catalogue entry (saved result -> its last run -> its duty point) or the route refuses with a 422.

Synthetic data only; no FEM solve runs here (the 422s are raised before the geometry build).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import mechanical as mech

A_TORQUE = 245.5          # the last run of the process: machine A


@pytest.fixture
def a_ran_last(monkeypatch):
    """Machine A has just been solved: its torque sits in every "last run" store."""
    from motor_ai_sim.routes import presets, simulation as sim
    monkeypatch.setattr(presets, "_last_transient_summary", lambda: {"T_avg_Nm": A_TORQUE}, raising=True)
    monkeypatch.setattr(sim, "_last_transient_ref", {"result": {"T_avg_Nm": A_TORQUE}}, raising=True)


def _machine(monkeypatch, loaded, docs):
    """``loaded`` = (die, config, duty) in the editor; ``docs`` = {(die, config): configuration yaml}."""
    monkeypatch.setattr(mech, "_loaded_machine", lambda: loaded, raising=True)
    monkeypatch.setattr(mech, "_machine_config_doc", lambda die, cfg: docs.get((die, cfg)), raising=True)
    from motor_ai_sim import duty_results
    monkeypatch.setattr(duty_results, "get", lambda die, cfg: {}, raising=True)


def test_b_gets_its_own_saved_torque_never_the_last_run_of_a(monkeypatch, a_ran_last):
    _machine(monkeypatch, ("Die", "G1", "rated"),
             {("Die", "G1"): {"duties": [{"name": "rated", "summary": {"T_em_avg_Nm": -12.0}}]}})
    assert mech._machine_torque_nm() == (12.0, "Die/G1/rated saved result")
    torque, source, loads = mech._resolve_torque("both", None)
    assert (torque, source, loads) == (12.0, "Die/G1/rated saved result", "both")


def test_b_with_no_torque_of_its_own_is_a_422_never_a_s_torque(monkeypatch, a_ran_last):
    _machine(monkeypatch, ("Die", "G2", "rated"), {("Die", "G2"): {"duties": [{"name": "rated", "rpm": 3000}]}})
    for loads in ("both", "torque"):
        with pytest.raises(HTTPException) as exc:
            mech._resolve_torque(loads, None)
        assert exc.value.status_code == 422
        assert exc.value.detail["error"] == "no torque for this machine; give torque_nm"
        assert "Die/G2/rated has no stored torque" in exc.value.detail["invalid_parameters"][0]["message"]
    assert mech._resolve_torque("centrifugal", None) == (0.0, "none", "centrifugal")
    assert mech._resolve_torque("both", 40.0) == (40.0, "given", "both")
    assert mech._resolve_torque("both", 0.0) == (0.0, "given", "centrifugal")   # the caller said "no torque"


def test_the_route_refuses_b_without_a_torque_after_a_ran(monkeypatch, a_ran_last):
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    _machine(monkeypatch, ("Die", "G2", "rated"), {("Die", "G2"): {"duties": [{"name": "rated"}]}})
    r = TestClient(app).get("/api/mechanical/rotor_stress",
                            params={"rpm": 6000, "mesh_size_mm": 4.0, "order": 1, "cases": "single",
                                    "lift_off_solves": 0, "field": False})
    assert r.status_code == 422, r.text[:400]
    assert r.json()["detail"]["error"] == "no torque for this machine; give torque_nm"


def test_the_fallbacks_are_the_machine_s_own_last_run_then_its_duty_point(monkeypatch):
    _machine(monkeypatch, ("Die", "G1", "rated"),
             {("Die", "G1"): {"duties": [{"name": "rated", "torque_nm": 30.0}]}})
    from motor_ai_sim import duty_results
    monkeypatch.setattr(duty_results, "get", lambda die, cfg: {"rated": {"em_run": {"numbers": {
        "T_em_avg_Nm": 20.0, "end3d": None}}}} if (die, cfg) == ("Die", "G1") else {}, raising=True)
    assert mech._machine_torque_nm() == (20.0, "Die/G1/rated last run")
    monkeypatch.setattr(duty_results, "get", lambda die, cfg: {}, raising=True)
    assert mech._machine_torque_nm() == (30.0, "Die/G1/rated duty point")


def test_the_3d_end_effect_factor_is_applied_like_the_em_tab():
    assert mech._torque_of_block({"T_em_avg_Nm": 10.0, "end3d": {"k_flux": 0.9}}) == pytest.approx(9.0)
    assert mech._torque_of_block({"T_avg_Nm": "nan"}) is None
    assert mech._torque_of_block(None) is None


def test_several_duties_and_none_named_is_no_torque(monkeypatch):
    _machine(monkeypatch, ("Die", "M1", None),
             {("Die", "M1"): {"duties": [{"name": "rated", "torque_nm": 5.0}, {"name": "peak", "torque_nm": 9.0}]}})
    torque, note = mech._machine_torque_nm()
    assert torque is None and "2 duties and none is named" in note


def test_a_named_machine_uses_its_own_duty_not_the_loaded_one(monkeypatch):
    docs = {("Die", "M1"): {"duties": [{"name": "rated", "torque_nm": 50.0}]},
            ("Die", "G1"): {"duties": [{"name": "rated", "torque_nm": 7.0}, {"name": "peak", "torque_nm": 9.0}]}}
    _machine(monkeypatch, ("Die", "M1", "rated"), docs)
    assert mech._machine_torque_nm("Die", "G1", "peak") == (9.0, "Die/G1/peak duty point")
    # G1 is not loaded: the loaded machine's duty name is NOT borrowed for it
    assert mech._machine_torque_nm("Die", "G1")[0] is None
    # the loaded machine named explicitly: its loaded duty is used
    assert mech._machine_torque_nm("Die", "M1") == (50.0, "Die/M1/rated duty point")


def test_holder_strength_takes_the_named_machine_s_torque_or_refuses(monkeypatch, a_ran_last):
    docs = {("Die", "G1"): {"duties": [{"name": "rated", "summary": {"T_em_avg_Nm": 12.0}}]},
            ("Die", "G2"): {"duties": [{"name": "rated"}]}}
    _machine(monkeypatch, ("Die", "M1", "rated"), docs)
    monkeypatch.setattr(mech, "_gate_config", lambda *a, **k: None, raising=True)
    seen = {}
    import motor_ai_sim.holder_strength as hs
    monkeypatch.setattr(hs, "assess_holder_strength", lambda **kw: seen.update(kw) or {}, raising=True)
    kw = dict(thrust_n=49.05, torque_nm=None, offset_bearing=0.04, offset_mode="diametral", t_working=70.0,
              motor_dia=40.0, bearing="618/8-2Z", duty=None, authorization=None)
    out = mech.get_holder_strength(die="Die", config="G1", **kw)
    assert seen["torque_nm"] == 12.0 and out["torque_source"] == "Die/G1/rated saved result"
    with pytest.raises(HTTPException) as exc:
        mech.get_holder_strength(die="Die", config="G2", **kw)
    assert exc.value.status_code == 422


def test_bearing_fit_speed_of_a_named_machine_is_its_own(monkeypatch):
    docs = {("Die", "G1"): {"duties": [{"name": "rated", "rpm": 12000}]},
            ("Die", "G2"): {"duties": [{"name": "rated"}]}}
    _machine(monkeypatch, ("Die", "M1", "rated"), docs)
    monkeypatch.setattr(mech, "_gate_config", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(mech, "_default_rpm", lambda: 99000.0, raising=True)        # the LOADED machine's speed
    seen = {}
    import motor_ai_sim.bearing_fit as bf
    monkeypatch.setattr(bf, "bearing_fit_analysis", lambda **kw: seen.update(kw) or {}, raising=True)
    kw = dict(bearing="618/8-2Z", offset_bearing=0.04, offset_mode="diametral", t_assembly=20.0, t_max=150.0,
              rpm=None, thrust_n=49.05, radial_load_n=0.0, hub_od=None, duty=None, authorization=None)
    mech.get_bearing_fit(die="Die", config="G1", **kw)
    assert seen["rpm"] == 12000.0
    with pytest.raises(HTTPException) as exc:
        mech.get_bearing_fit(die="Die", config="G2", **kw)
    assert exc.value.status_code == 422 and exc.value.detail["invalid_parameters"][0]["field"] == "rpm"


def test_the_coupled_hook_no_longer_reads_the_panel_torque_box(monkeypatch):
    seen = {}
    monkeypatch.setattr(mech, "_mech_panel_settings", lambda authorization=None: {"torque": "245.5"}, raising=True)
    monkeypatch.setattr(mech, "rotor_stress", lambda **kw: seen.update(kw), raising=True)
    mech.run_rotor_stress_at({}, rpm=8000, loads="both", cases="single", field=False)
    assert seen["torque_nm"] is None          # the route resolves it to THIS machine's own torque
