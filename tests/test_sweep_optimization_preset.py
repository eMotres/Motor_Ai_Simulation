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


# ── run settings: ONE resolver for the display and the run (owner 2026-11-24) ─────────────────────────────────
import copy

import pytest
from fastapi import HTTPException

from motor_ai_sim import run_settings as RS


def _cfg():
    c = copy.deepcopy(O.get_config())
    c["mesh"] = {}                      # nothing stored on the machine: the machine-read keys equal the preset's defaults
    c.setdefault("simulation", {})["eddy"] = True
    return c


def test_the_optimization_preset_resolves_to_its_own_values_and_reads_as_that_preset():
    cfg = _cfg()
    r = RS.resolve("optimization", {}, cfg)
    v = MP.resolve("optimization", dict(cfg["geometry"]))["values"]
    assert r["label"] == "optimization" and r["custom"] is False and r["differs"] == {}
    st = r["settings"]
    assert (st["steps_per_period"], st["gap_layers"], st["rotor_eddy"], st["demag"], st["hi_fidelity"]) == (
        v["n_steps_per_period"], v["gap_layers"], v["rotor_eddy"], v["demag"], False)
    assert st["mesh_size_mm"] == v["mesh_size_mm"] and st["n_sectors"] == v["n_sectors"]


def test_an_override_makes_it_custom_and_names_every_difference():
    r = RS.resolve("optimization", {"steps_per_period": 108, "gap_layers": 2}, _cfg())
    assert r["label"] == "custom" and set(r["differs"]) == {"steps_per_period", "gap_layers"}
    assert r["settings"]["steps_per_period"] == 108 and r["sources"]["steps_per_period"] == "override"


def test_the_simulation_preset_and_more_steps_than_it_asks_for():
    cfg = _cfg()
    sim = RS.resolve("simulation", {}, cfg)
    assert sim["label"] == "simulation" and sim["settings"]["gap_layers"] == 2.0
    more = RS.resolve("simulation", {"steps_per_period": sim["settings"]["steps_per_period"] + 12}, cfg)
    assert more["label"] == "simulation"


def test_validation_clamps_report_their_reason_and_unknown_keys_are_refused():
    r = RS.resolve("optimization", {"gap_layers": 5, "mesh_size_mm": 50}, _cfg())
    assert r["settings"]["gap_layers"] == 2.0 and r["settings"]["mesh_size_mm"] == 12.0
    assert {c["key"] for c in r["clamps"]} == {"gap_layers", "mesh_size_mm"} and all(c["reason"] for c in r["clamps"])
    hi = RS.resolve("optimization", {"hi_fidelity": True}, _cfg())
    assert any("hi_fidelity" in c["reason"] for c in hi["clamps"]) and hi["custom"]
    for bad in ({"outer_air_factor": 1.3}, {"eddy": False}, {"nope": 1}, {"steps_per_period": "x"}):
        with pytest.raises(RS.RunSettingsError):
            RS.resolve("optimization", bad, _cfg())
    with pytest.raises(RS.RunSettingsError):
        RS.resolve("descent_preset", {}, _cfg())
    with pytest.raises(RS.RunSettingsError):               # the descent evals read demag from the stored Simulation block
        RS.resolve("optimization", {"demag": False}, _cfg(), "descent")


def test_what_the_machine_stores_counts_in_the_label():
    cfg = _cfg()
    cfg["mesh"] = {"outer_air_factor": 1.5}
    r = RS.resolve("optimization", {}, cfg)
    assert r["custom"] and "outer_air_factor" in r["differs"] and r["sources"]["outer_air_factor"] == "machine"


def test_display_equals_run_for_a_sweep(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    for rec in ({"preset": "optimization", "overrides": {}},
                {"preset": "simulation", "overrides": {}},
                {"preset": "optimization", "overrides": {"steps_per_period": 108, "gap_layers": 2, "demag": False,
                                                          "rotor_eddy": False, "n_sectors": 2, "hi_fidelity": True}}):
        shown = RS.resolve(rec["preset"], rec["overrides"], cfg)
        plan = O.prepare_scan(_req(run_settings={**rec, "expect_sig": shown["sig"]}, steps_per_period=7, gap_layers=1,
                                   mesh_size_mm=9.0, rotor_eddy=not shown["settings"]["rotor_eddy"]))
        a, st = plan["args"], shown["settings"]
        assert (a[2], a[13], a[15], a[16], a[22]) == (st["steps_per_period"], st["gap_layers"], st["rotor_eddy"],
                                                      st["hi_fidelity"], st["demag"])
        assert a[8] == st["mesh_size_mm"] and a[12] == (-1 if st["n_sectors"] <= 1 else st["n_sectors"])
        assert plan["response"]["mesh_preset"]["sig"] == shown["sig"]
        assert plan["response"]["mesh_preset"]["name"] == shown["label"]


def test_display_equals_run_for_a_descent_and_an_auto_plan(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    rec = {"preset": "simulation", "overrides": {"steps_per_period": 120}}
    shown = RS.resolve(rec["preset"], rec["overrides"], cfg, "descent")
    d = O.prepare_descent(O.DescentRequest(variables=[{"name": "tooth_width", "min": 0.0, "max": 0.0}],
                                           run_settings=rec, steps_per_period=24))
    ep, st = d["init_state"]["eval_params"], shown["settings"]
    assert (ep["steps_per_period"], ep["gap_layers"], ep["rotor_eddy"], ep["mesh_size_mm"]) == (
        st["steps_per_period"], st["gap_layers"], st["rotor_eddy"], st["mesh_size_mm"])
    assert d["response"]["mesh_preset"]["sig"] == shown["sig"]


def test_a_changed_setting_since_the_display_is_refused_not_run():
    cfg = _cfg()
    shown = RS.resolve("optimization", {}, cfg)
    with pytest.raises(HTTPException) as ei:
        O._resolve_run_settings(O.RunSettingsIn(preset="optimization", overrides={"steps_per_period": 96},
                                                expect_sig=shown["sig"]), "sweep")
    assert ei.value.status_code == 409 and "run settings changed" in ei.value.detail


def test_the_resolve_endpoint_answers_with_the_complete_settings():
    out = O.run_settings_resolve(O.RunSettingsIn(preset="optimization", overrides={"steps_per_period": 108}), "sweep")
    assert out["label"] == "custom" and out["settings"]["steps_per_period"] == 108
    for k in ("mesh_size_mm", "min_size_mm", "n_sectors", "element_order", "eddy", "rotor_eddy", "demag", "hi_fidelity",
              "outer_air_factor", "component_mesh"):
        assert k in out["settings"], k
    with pytest.raises(HTTPException) as ei:
        O.run_settings_resolve(O.RunSettingsIn(preset="nope"), "sweep")
    assert ei.value.status_code == 422


def test_opening_the_resolver_changes_nothing_on_the_server():
    cfg = _cfg()
    before = copy.deepcopy(cfg)
    RS.resolve("simulation", {"steps_per_period": 120}, cfg)
    assert cfg == before


def test_mixed_solved_counts_are_mixed_custom_and_carry_no_digest():
    rec = O._sweep_settings_record({"name": "optimization", "custom": False, "sig": 99}, 48, 1.0)
    pts = [{"fem": True, "feasible": True, "n_steps_per_period_solved": 48, "gap_layers_per_side": 1.0},
           {"fem": True, "feasible": True, "n_steps_per_period_solved": 52, "gap_layers_per_side": 1.0}]
    m = O._stamp_solved(rec, pts)
    assert m["mixed"] is True and m["name"] == "custom" and m["sig"] is None
    assert m["effective"]["steps_per_period"] is None and m["effective_source"]["steps_per_period"] == "mixed"
    s = SS.sweep_settings_of({"mesh_preset": m})
    assert s["mixed"] is True and s["preset"] == "custom" and s["sig"] is None
    # only some points report: mixed, never presented as solved
    part = O._stamp_solved(rec, [pts[0], {"fem": True, "feasible": True}])
    assert part["effective_source"]["steps_per_period"] == "mixed"
    # all agree and equal the request: solved, digest kept
    ok = O._stamp_solved(rec, [pts[0], dict(pts[0])])
    assert ok["effective_source"]["steps_per_period"] == "solved" and ok["sig"] == 99 and not ok.get("custom")


def test_a_legacy_stored_sweep_has_no_digest_so_it_never_compares():
    s = SS.sweep_settings_of({"steps_per_period": 108, "scan_params": {"gap_layers": 1.0}})
    assert s["sig"] is None and s["source"] == "stored_request"
    assert SS.sweep_settings_of({"mesh_preset": {"name": "optimization", "sig": 5,
            "effective": {"steps_per_period": 48, "gap_layers": 1.0}, "solved_differs": True}})["sig"] is None
