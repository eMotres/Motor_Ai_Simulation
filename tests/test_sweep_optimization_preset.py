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


# ── the WHOLE preset, solved provenance, descent (PR #845 review) ────────────
def _sent(**kw):
    return _req(**kw)


def test_a_bare_sweep_request_runs_every_preset_setting():
    vals = _preset()
    plan = O.prepare_scan(_req())
    a = plan["args"]
    assert a[15] is bool(vals["rotor_eddy"]) and a[22] is bool(vals["demag"])      # rotor_eddy, demag
    assert a[16] is False                                                           # hi_fidelity
    assert a[12] == (-1 if int(vals["n_sectors"]) <= 1 else int(vals["n_sectors"]))
    assert plan["response"]["mesh_preset"]["custom"] is False


def test_client_sizes_sectors_eddy_demag_and_hi_fidelity_are_ignored_and_listed():
    vals = _preset()
    plan = O.prepare_scan(_req(mesh_size_mm=2.5, min_size_mm=0.5, n_sectors=1, rotor_eddy=False, demag=False,
                               hi_fidelity=True))
    rec = plan["response"]["mesh_preset"]
    assert rec["custom"] is False and rec["name"] == "optimization"
    for k in ("mesh_size_mm", "min_size_mm", "rotor_eddy", "demag", "hi_fidelity"):
        assert k in rec["ignored"], k
    assert plan["args"][16] is False and plan["args"][15] is bool(vals["rotor_eddy"])
    assert plan["response"]["mesh_size_mm"] == max(0.02, min(float(vals["mesh_size_mm"]), 12.0))


def test_the_effective_settings_come_from_the_solved_points():
    rec = O._sweep_settings_record({"name": "optimization", "custom": False}, 48, 1.0)
    assert rec["effective_source"] == "requested"
    pts = [{"fem": True, "feasible": True, "n_steps_per_period_solved": 48, "gap_layers_per_side": 1.0}]
    ok = O._stamp_solved(rec, pts)
    assert ok["effective_source"] == "solved" and ok["custom"] is not True
    # the solver snapped the count and raised the gap rows: the stamp says so and is no longer "Optimization"
    pts = [{"fem": True, "feasible": True, "n_steps_per_period_solved": 52, "gap_layers_per_side": 4.0}]
    bad = O._stamp_solved(rec, pts)
    assert bad["effective"] == {"steps_per_period": 52, "gap_layers": 4.0}
    assert bad["custom"] is True and bad["solved_differs"] is True
    # points that report nothing leave the request values, labelled as such
    none = O._stamp_solved(rec, [{"fem": True, "feasible": True}])
    assert none["effective_source"] == "requested" and none["n_points_reporting"] == 0
    assert SS.sweep_settings_of({"mesh_preset": bad})["preset"] == "custom"


def test_the_eval_returns_what_the_solver_used():
    import pathlib
    src = pathlib.Path(O.__file__).resolve().parents[1].joinpath("optimization", "refine_proc.py").read_text()
    assert '"n_steps_per_period_solved": d.get("n_steps_per_period")' in src
    assert "n_steps_per_period_solved" in O._RES_KEYS


def test_a_descent_request_runs_the_preset_for_every_client_value():
    vals = _preset()
    body = dict(variables=[{"name": "tooth_width", "min": 0.0, "max": 0.0}], steps_per_period=108, gap_layers=2,
                mesh_size_mm=2.0, rotor_eddy=False)
    plan = O.prepare_descent(O.DescentRequest(**body))
    ep = plan["init_state"]["eval_params"]
    assert ep["steps_per_period"] == vals["n_steps_per_period"]
    assert ep["gap_layers"] == vals["gap_layers"]
    assert ep["rotor_eddy"] is bool(vals["rotor_eddy"])
    rec = plan["response"]["mesh_preset"]
    assert rec["name"] == "optimization" and rec["ignored"]["steps_per_period"]["sent"] == 108


def test_compare_sweep_settings_rule_is_in_the_web_helper():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    h = root / "web" / "src" / "lib" / "sweepSettings.ts"
    if h.exists():                       # the job container carries no web tree
        assert "sweepMetricsComparable" in h.read_text()
