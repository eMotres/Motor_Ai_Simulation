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


def _optvals():
    return _preset()


def _opt_request(**kw):
    """A request that carries EVERY Optimization-preset setting (what the preset button leaves in the stored fields)."""
    v = _preset()
    return _req(steps_per_period=v["n_steps_per_period"], gap_layers=v["gap_layers"], mesh_size_mm=v["mesh_size_mm"],
                min_size_mm=v["min_size_mm"], n_sectors=v["n_sectors"], rotor_eddy=v["rotor_eddy"], demag=v["demag"], **kw)


def test_a_108_step_request_runs_at_108_and_is_custom():
    plan = O.prepare_scan(_req(steps_per_period=108, gap_layers=2))
    assert plan["args"][2] == 108 and plan["args"][13] == 2
    rec = plan["response"]["mesh_preset"]
    assert rec["name"] == "custom" and rec["custom"] is True
    assert rec["requested"] == {"steps_per_period": 108, "gap_layers": 2.0}


def test_a_request_with_every_optimization_setting_is_stamped_optimization():
    v = _preset()
    plan = O.prepare_scan(_opt_request())
    rec = plan["response"]["mesh_preset"]
    assert (rec["name"], rec["custom"]) == ("optimization", False)
    assert plan["args"][2] == v["n_steps_per_period"]
    # changing ONE setting makes it custom and says which
    one = O.prepare_scan(_opt_request(hi_fidelity=False, element_order=2))
    assert one["response"]["mesh_preset"]["name"] == "optimization"
    other = O.prepare_scan(_req(**{**_opt_request().model_dump(), "demag": not v["demag"]}))
    r2 = other["response"]["mesh_preset"]
    assert r2["name"] == "custom" and "demag" in r2["differs"]


def test_simulation_preset_values_are_stamped_simulation():
    geo = dict(O.get_config().get("geometry") or {})
    v = MP.resolve("simulation", geo)["values"]
    plan = O.prepare_scan(_req(steps_per_period=v["n_steps_per_period"], gap_layers=v["gap_layers"],
                               mesh_size_mm=v["mesh_size_mm"], min_size_mm=v["min_size_mm"], n_sectors=v["n_sectors"],
                               rotor_eddy=v["rotor_eddy"], demag=v["demag"]))
    assert plan["response"]["mesh_preset"]["name"] == "simulation"


def test_a_named_preset_still_fills_what_the_request_leaves_out():
    v = _preset()
    plan = O.prepare_scan(O.ScanRequest(variables=_req().variables, preset="optimization"))
    assert plan["args"][2] == v["n_steps_per_period"]


def test_the_plan_carries_the_settings_stamp():
    plan = O.prepare_scan(_req(steps_per_period=108, gap_layers=2))
    rec = plan["response"]["mesh_preset"]
    assert plan["kwargs"]["mesh_preset"] == rec and plan["init_state"]["mesh_preset"] == rec


def test_the_effective_settings_come_from_the_solved_points():
    rec = O._sweep_settings_record({"name": "optimization", "custom": False}, 48, 1.0)
    assert rec["effective_source"] == "requested"
    pts = [{"fem": True, "feasible": True, "n_steps_per_period_solved": 48, "gap_layers_per_side": 1.0}]
    ok = O._stamp_solved(rec, pts)
    assert ok["effective_source"] == "solved" and ok["custom"] is not True
    # the solver snapped the count and raised the gap rows: the stamp says so and is no longer a preset
    pts = [{"fem": True, "feasible": True, "n_steps_per_period_solved": 52, "gap_layers_per_side": 4.0}]
    bad = O._stamp_solved(rec, pts)
    assert bad["effective"] == {"steps_per_period": 52, "gap_layers": 4.0}
    assert bad["custom"] is True and bad["solved_differs"] is True
    none = O._stamp_solved(rec, [{"fem": True, "feasible": True}])
    assert none["effective_source"] == "requested" and none["n_points_reporting"] == 0
    assert SS.sweep_settings_of({"mesh_preset": bad})["preset"] == "custom"


def test_the_eval_returns_what_the_solver_used():
    import pathlib
    src = pathlib.Path(O.__file__).resolve().parents[1].joinpath("optimization", "refine_proc.py").read_text()
    assert '"n_steps_per_period_solved": d.get("n_steps_per_period")' in src
    assert "n_steps_per_period_solved" in O._RES_KEYS


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


def test_the_descent_request_is_not_rewritten():
    body = dict(variables=[{"name": "tooth_width", "min": 0.0, "max": 0.0}], steps_per_period=108, gap_layers=2)
    ep = O.prepare_descent(O.DescentRequest(**body))["init_state"]["eval_params"]
    assert ep["steps_per_period"] == 108 and ep["gap_layers"] == 2
