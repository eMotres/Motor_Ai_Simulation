"""A Sweep is an optimisation run: it runs the Optimization preset (steps per period and gap rows read from the stored
preset, a client count ignored), records the effective settings, and does not solve a solo first point under TDM."""
from __future__ import annotations

import inspect

from motor_ai_sim import mesh_presets as MP
from motor_ai_sim import sweep_saves as SS
from motor_ai_sim.routes import optimization as O
from tests.test_optimize_headless_prepare_scan import _req


def _preset():
    geo = dict(O.get_config().get("geometry") or {})
    return MP.resolve("optimization", geo)["values"]


def test_a_108_step_request_runs_at_the_presets_value():
    vals = _preset()
    plan = O.prepare_scan(_req(steps_per_period=108, gap_layers=2))
    assert plan["args"][2] == vals["n_steps_per_period"] != 108
    assert plan["args"][13] == vals["gap_layers"]
    assert plan["response"]["steps_per_period"] == vals["n_steps_per_period"]


def test_the_preset_is_read_not_hard_coded(monkeypatch):
    real = MP.resolve

    def fake(name, geometry, **kw):
        out = real(name, geometry, **kw)
        out["values"] = dict(out["values"], n_steps_per_period=60, gap_layers=1.0)
        return out
    monkeypatch.setattr(MP, "resolve", fake)
    plan = O.prepare_scan(_req(steps_per_period=108, gap_layers=2))
    assert plan["args"][2] == 60


def test_the_plan_carries_the_effective_settings():
    vals = _preset()
    plan = O.prepare_scan(_req(steps_per_period=108, gap_layers=2))
    rec = plan["response"]["mesh_preset"]
    assert rec["name"] == "optimization"
    assert rec["effective"] == {"steps_per_period": vals["n_steps_per_period"], "gap_layers": vals["gap_layers"]}
    assert rec["ignored"]["steps_per_period"] == {"sent": 108, "used": vals["n_steps_per_period"]}
    assert plan["kwargs"]["mesh_preset"] == rec
    assert plan["init_state"]["mesh_preset"] == rec


def test_a_request_that_already_matches_lists_nothing_ignored():
    vals = _preset()
    plan = O.prepare_scan(_req(steps_per_period=vals["n_steps_per_period"], gap_layers=vals["gap_layers"]))
    assert "ignored" not in plan["response"]["mesh_preset"]


def test_the_preset_named_by_the_client_cannot_turn_a_sweep_into_a_simulation_run():
    vals = _preset()
    plan = O.prepare_scan(_req(preset="simulation", steps_per_period=300))
    assert plan["response"]["mesh_preset"]["name"] == "optimization"
    assert plan["args"][2] == vals["n_steps_per_period"]


def test_the_worker_stores_and_the_journal_replays_the_stamp():
    assert "mesh_preset" in inspect.signature(O._scan_worker).parameters
    src = inspect.getsource(O._scan_worker)
    assert '"mesh_preset": mesh_preset' in src
    from motor_ai_sim import sweep_resume
    assert '"mesh_preset": req_body.get("mesh_preset")' in inspect.getsource(sweep_resume)


def test_the_descent_plan_reads_the_preset():
    geo = dict(O.get_config().get("geometry") or {})
    assert O._optimization_preset_steps() == MP.steps_for("optimization", geo)


def test_no_solo_first_point_under_tdm_but_a_march_sweep_still_seeds():
    D = O.serial_first_decision
    ok, why = D(40, 10, demag=True, rotor_eddy=True, seed_ok=False, eddy_method="tdm")
    assert ok is False and "TDM" in why
    ok, _ = D(40, 10, demag=True, rotor_eddy=True, seed_ok=False, eddy_method="march")
    assert ok is True
    ok, _ = D(40, 10, demag=True, rotor_eddy=True, seed_ok=False)          # unknown method: the old rule
    assert ok is True


def test_the_sweep_asks_the_eval_for_its_method(monkeypatch):
    monkeypatch.setenv("SB_EDDY_METHOD", "march")
    assert O._sweep_eddy_method() == "march"
    monkeypatch.setenv("SB_EDDY_METHOD", "tdm")
    assert O._sweep_eddy_method() == "tdm"


def test_a_stored_sweep_with_the_stamp_is_labelled_by_the_preset():
    res = {"steps_per_period": 48, "points": [], "mesh_preset": {
        "name": "optimization", "version": 1, "custom": False, "effective": {"steps_per_period": 48, "gap_layers": 1.0}}}
    s = SS.sweep_settings_of(res)
    assert (s["source"], s["preset"], s["steps_per_period"], s["gap_layers"]) == ("preset", "optimization", 48, 1.0)


def test_an_old_sweep_without_the_fields_is_labelled_by_its_stored_request():
    old = {"steps_per_period": 108, "points": [], "scan_params": {"gap_layers": 1.0}}
    s = SS.sweep_settings_of(old, {"steps_per_period": 108})
    assert (s["source"], s["preset"], s["steps_per_period"], s["gap_layers"]) == ("stored_request", None, 108, 1.0)
    assert SS.sweep_settings_of({})["steps_per_period"] is None
    entry = {"id": "x", "name": "old", "config": {"steps_per_period": 108}, "result": old}
    row = SS.meta(entry)
    assert row["settings"]["source"] == "stored_request" and row["settings"]["steps_per_period"] == 108
