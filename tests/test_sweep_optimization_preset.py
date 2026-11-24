"""A sweep, descent or auto run uses the steps per period and the gap rows its panel shows: chosen by a preset button or typed,
never read from another tab, recorded as solved, and labelled "optimization" / "simulation" / "custom" for that PAIR only.
Also: no solo first point under TDM, and the legacy (no run_settings) request is unchanged."""
from __future__ import annotations

import inspect
import math

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
    assert set(rec["effective_source"].values()) == {"requested"}
    pts = [{"fem": True, "feasible": True, "n_steps_per_period_solved": 48, "gap_layers_per_side": 1.0}]
    ok = O._stamp_solved(rec, pts)
    assert set(ok["effective_source"].values()) == {"solved"} and ok["custom"] is not True
    # the solver snapped the count and raised the gap rows: the stamp says so and is no longer a preset
    pts = [{"fem": True, "feasible": True, "n_steps_per_period_solved": 52, "gap_layers_per_side": 4.0}]
    bad = O._stamp_solved(rec, pts)
    assert bad["effective"] == {"steps_per_period": 52, "gap_layers": 4.0}
    assert bad["custom"] is True and bad["solved_differs"] is True and bad["name"] == "custom"
    none = O._stamp_solved(rec, [{"fem": True, "feasible": True}])
    assert set(none["effective_source"].values()) == {"requested"} and none["n_points_reporting"] == 0
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


def _cfg(coil_rel=2):
    c = copy.deepcopy(O.get_config())
    # the machine's stored wire cell: 2h = the Optimization preset's (a stored wire cell that is not the preset's makes the run custom)
    c["mesh"] = {"component_mesh": {"coil_rel": coil_rel}} if coil_rel else {}
    c.setdefault("simulation", {})["eddy"] = True
    return c


def test_the_simulation_preset_and_more_steps_than_it_asks_for():
    cfg = _cfg(coil_rel=1)              # the Simulation preset's wire cell
    sim = RS.resolve("simulation", {}, cfg)
    assert sim["label"] == "simulation" and sim["settings"]["gap_layers"] == 2.0
    more = RS.resolve("simulation", {"steps_per_period": sim["settings"]["steps_per_period"] + 12}, cfg)
    assert more["label"] == "simulation"


def test_display_equals_run_for_a_sweep(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    for rec in ({"preset": "optimization", "overrides": {}},
                {"preset": "simulation", "overrides": {}},
                {"preset": "optimization", "overrides": {"steps_per_period": 108, "gap_layers": 2, "demag": False,
                                                          "rotor_eddy": False, "n_sectors": 2}}):
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


# ── round 3: the guarantee is the steps-and-gap pair; every coercion of those two numbers happens in the resolver ──
def test_the_preset_resolves_and_the_label_is_about_steps_and_gap_rows_only():
    cfg = _cfg()
    r = RS.resolve("optimization", {}, cfg)
    v = MP.resolve("optimization", dict(cfg["geometry"]))["values"]
    assert r["label"] == "optimization" and r["differs"] == {}
    assert (r["settings"]["steps_per_period"], r["settings"]["gap_layers"]) == (v["n_steps_per_period"], v["gap_layers"])
    # other settings are shown with their source but never change the label or the digest
    other = RS.resolve("optimization", {"demag": False, "mesh_size_mm": 2.0}, cfg)
    assert other["label"] == "optimization" and other["sig"] == r["sig"]
    assert other["sources"]["demag"] == "override" and r["sources"]["eddy"] == "machine"
    cfg2 = copy.deepcopy(cfg)
    cfg2["mesh"] = {"outer_air_factor": 1.9, "component_mesh": {"shaft": 0.1, "coil_rel": 2}}
    assert RS.resolve("optimization", {}, cfg2)["label"] == "optimization"
    custom = RS.resolve("optimization", {"steps_per_period": 108, "gap_layers": 2}, cfg)
    assert custom["label"] == "custom" and set(custom["differs"]) == {"steps_per_period", "gap_layers"}
    assert custom["sig"] != r["sig"]


def test_a_descent_override_below_the_plan_minimum_is_shown_as_what_runs():
    cfg = _cfg()
    shown = RS.resolve("optimization", {"steps_per_period": 4}, cfg, "descent")
    assert shown["settings"]["steps_per_period"] == 8
    assert any(c["key"] == "steps_per_period" and c["requested"] == 4 and c["used"] == 8 for c in shown["clamps"])
    assert RS.resolve("optimization", {"steps_per_period": 4}, cfg, "sweep")["settings"]["steps_per_period"] == 4


def test_a_preset_value_outside_the_bounds_is_bounded_in_the_resolver_and_reads_custom(monkeypatch):
    cfg = _cfg()
    real = MP.resolve

    def fake(name, geometry, **kw):
        out = real(name, geometry, **kw)
        out["values"] = dict(out["values"], n_steps_per_period=324)
        return out
    monkeypatch.setattr(MP, "resolve", fake)
    shown = RS.resolve("simulation", {}, cfg)
    assert shown["settings"]["steps_per_period"] == 180 and shown["label"] == "custom"
    assert any(c["source"] == "preset" and c["requested"] == 324 for c in shown["clamps"])
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    plan = O.prepare_scan(_req(run_settings={"preset": "simulation", "overrides": {}}))
    assert plan["args"][2] == shown["settings"]["steps_per_period"] == 180      # display == run


def test_hi_fidelity_cannot_be_set_it_would_raise_the_gap_rows():
    with pytest.raises(RS.RunSettingsError):
        RS.resolve("optimization", {"hi_fidelity": True}, _cfg())
    assert RS.resolve("optimization", {}, _cfg())["settings"]["hi_fidelity"] is False


def test_a_full_ring_request_shows_the_symmetry_sector_it_is_solved_on():
    cfg = _cfg()
    g = cfg["geometry"]
    r = RS.resolve("optimization", {"n_sectors": 1}, cfg)
    sym = math.gcd(int(g["num_slots"]), int(g["num_poles"]))
    if sym >= 2:
        assert r["settings"]["n_sectors"] == sym and any(c["key"] == "n_sectors" for c in r["clamps"])
    assert (r["settings"]["steps_per_period"], r["settings"]["gap_layers"]) == (
        RS.resolve("optimization", {}, cfg)["settings"]["steps_per_period"],
        RS.resolve("optimization", {}, cfg)["settings"]["gap_layers"])


def test_no_solved_report_means_an_unknown_identity():
    rec = O._sweep_settings_record({"name": "optimization", "custom": False, "sig": 99}, 48, 1.0)
    none = O._stamp_solved(rec, [{"fem": True, "feasible": True}])
    assert none["sig"] is None
    assert SS.sweep_settings_of({"mesh_preset": none})["sig"] is None
    nul = {"name": "optimization", "sig": 5, "effective": {"steps_per_period": None, "gap_layers": 1.0},
           "effective_source": {"steps_per_period": "solved", "gap_layers": "solved"}}
    assert SS.sweep_settings_of({"mesh_preset": nul})["sig"] is None


def test_an_old_sidecar_without_the_settings_label_is_rebuilt_not_the_save(tmp_path):
    import json
    entry = {"id": "s1", "name": "old", "created_at": "2026-11-01T10:00:00", "config": {"steps_per_period": 108},
             "result": {"steps_per_period": 108, "points": [], "scan_params": {"gap_layers": 1.0}}, "auto": True}
    f = tmp_path / "s1.json"
    f.write_text(json.dumps(entry))
    side = SS._meta_path(f)
    side.write_text(json.dumps({"id": "s1", "auto": True, "name": "old"}))
    saved = f.read_text()
    row = SS._read_meta_row(f)
    assert row["settings"]["steps_per_period"] == 108 and row["settings"]["source"] == "stored_request"
    assert f.read_text() == saved


# ── round 4 ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_both_auto_plan_branches_carry_the_run_settings_stamp():
    for mode in ("cmaes", "screen"):
        plan = O.auto_plan(O.AutoOptRequest(max_ripple_pct=5.0, mode=mode,
                                            run_settings={"preset": "optimization", "overrides": {}}))["plan"]
        shown = RS.resolve("optimization", {}, O.get_config(), "auto")
        assert plan["run_settings"]["sig"] == shown["sig"], mode        # what the panel's Run button compares
        assert plan["eval"]["steps_per_period"] == shown["settings"]["steps_per_period"]


def test_descent_and_auto_record_the_solved_pair():
    launch = {"name": "optimization", "custom": False, "sig": 7}
    pt = {"kind": "candidate", "n_steps_per_period_solved": 48, "gap_layers_per_side": 1.0}
    st = {"mesh_preset": launch, "eval_params": {"steps_per_period": 48, "gap_layers": 1.0},
          "points": [dict(pt), dict(pt),
                     {"kind": "final_validation", "n_steps_per_period_solved": 96, "gap_layers_per_side": 2.0}]}
    ok = O._descent_solved_stamp(st)
    assert ok["effective_source"] == {"steps_per_period": "solved", "gap_layers": "solved"} and ok["sig"] == 7
    assert not ok.get("custom")                                   # the final validations keep their own purpose
    st["points"].append({"kind": "candidate", "n_steps_per_period_solved": 52, "gap_layers_per_side": 1.0})
    assert O._descent_solved_stamp(st)["mixed"] is True
    # the eval summary and the cloud point carry the solved pair
    res = {"n_steps_per_period_solved": 52, "gap_layers_per_side": 1.0, "T_em_Nm": 1.0, "efficiency": 0.9}
    assert O._msum(res)["n_steps_per_period_solved"] == 52
    assert O._pt({"ok": True, "res": res, "overrides": {}}, "candidate")["gap_layers_per_side"] == 1.0
    assert O._descent_solved_stamp({"mesh_preset": None}) is None


def test_the_identity_needs_both_numbers_solved():
    rec = O._sweep_settings_record({"name": "optimization", "custom": False, "sig": 99}, 48, 1.0)
    steps_only = O._stamp_solved(rec, [{"fem": True, "feasible": True, "n_steps_per_period_solved": 48}])
    assert steps_only["effective_source"] == {"steps_per_period": "solved", "gap_layers": "requested"}
    assert steps_only["sig"] is None
    assert SS.sweep_settings_of({"mesh_preset": steps_only})["sig"] is None
    both = O._stamp_solved(rec, [{"fem": True, "feasible": True, "n_steps_per_period_solved": 48, "gap_layers_per_side": 1.0}])
    assert both["sig"] == 99


def test_a_sidecar_from_an_older_rule_is_rebuilt_even_when_it_has_settings(tmp_path):
    import json
    entry = {"id": "s2", "name": "x", "created_at": "2026-11-01T10:00:00", "config": {"steps_per_period": 48}, "auto": True,
             "result": {"steps_per_period": 48, "points": [], "mesh_preset": {
                 "name": "optimization", "sig": 111, "effective": {"steps_per_period": 48, "gap_layers": 1.0},
                 "effective_source": {"steps_per_period": "requested", "gap_layers": "requested"}}}}
    f = tmp_path / "s2.json"
    f.write_text(json.dumps(entry))
    SS._meta_path(f).write_text(json.dumps({"id": "s2", "auto": True,
        "settings": {"preset": "optimization", "source": "preset", "sig": 111, "steps_per_period": 48, "gap_layers": 1.0}}))
    saved = f.read_text()
    row = SS._read_meta_row(f)
    assert row["settings"]["sig"] is None and row["settings"]["v"] == SS.SETTINGS_VERSION
    assert f.read_text() == saved


def test_gap_rows_and_steps_are_whole_numbers():
    cfg = _cfg()
    for bad in (1.231, 1.5, 100.5):
        for key in ("gap_layers", "steps_per_period"):
            with pytest.raises(RS.RunSettingsError):
                RS.resolve("optimization", {key: bad}, cfg)
    assert RS.resolve("optimization", {"gap_layers": 2.0}, cfg)["settings"]["gap_layers"] == 2.0
    assert RS.resolve("optimization", {"gap_layers": 1}, cfg)["sig"] == RS.resolve("optimization", {}, cfg)["sig"]


# ── the digest also covers the preset version and the wire cell (preset version 2, owner 2026-11-24) ──
def test_the_digest_changes_with_the_preset_version_and_the_wire_cell():
    assert RS.identity_sig(48, 1.0, 1, 1.0) != RS.identity_sig(48, 1.0, 2, 2.0)
    assert RS.identity_sig(48, 1.0, 2, 2.0) != RS.identity_sig(48, 1.0, 2, 1.0)
    assert RS.identity_sig(48, 1.0, 2, 2.0) == RS.identity_sig(48, 1.0, 2, 2)
    cfg = _cfg()
    a = RS.resolve("optimization", {}, cfg)
    cfg2 = _cfg(coil_rel=1)
    b = RS.resolve("optimization", {}, cfg2)
    # the run uses the PRESET's wire cell whatever the stored block says: same label, same digest, a per-run override when they differ
    assert a["coil_rel"] == 2.0 and b["coil_rel"] == 2.0 and a["sig"] == b["sig"]
    assert a["label"] == b["label"] == "optimization" and a["differs"] == b["differs"] == {}
    assert a["coil_rel_override"] is None and b["coil_rel_override"] == 2.0 and b["coil_rel_stored"] == 1.0
    assert b["sources"]["coil_rel"] == "preset"
    # only an explicit override of the cell makes the run custom, and the digest records the cell the run used
    c = RS.resolve("optimization", {"coil_rel": 1}, cfg)
    assert c["label"] == "custom" and c["differs"]["coil_rel"] == {"preset": 2.0, "current": 1.0} and c["sig"] != a["sig"]
    assert c["coil_rel_override"] == 1.0 and c["sources"]["coil_rel"] == "override"
    with pytest.raises(RS.RunSettingsError):
        RS.resolve("optimization", {"coil_rel": 3}, cfg)
    # the Simulation preset is 1h: on a stored-2h machine the run is at 1h (a per-run override), still the preset
    sim = RS.resolve("simulation", {}, cfg)
    assert sim["label"] == "simulation" and sim["coil_rel"] == 1.0 and sim["coil_rel_override"] == 1.0
    assert RS.resolve("simulation", {}, _cfg(coil_rel=1))["coil_rel_override"] is None
    assert a["sig_scope"] == 2 and a["version"] == MP.version()


def test_a_stored_sweep_without_the_digest_scope_is_unknown_and_never_ranked():
    base = {"name": "optimization", "version": 1, "sig": 5, "effective": {"steps_per_period": 48, "gap_layers": 1.0},
            "effective_source": {"steps_per_period": "solved", "gap_layers": "solved"}}
    assert SS.sweep_settings_of({"mesh_preset": base})["sig"] is None
    solved3 = {"steps_per_period": "solved", "gap_layers": "solved", "coil_rel": "solved"}
    new = SS.sweep_settings_of({"mesh_preset": {**base, "version": 2, "sig_scope": 2, "coil_rel": 2.0, "effective_source": solved3}})
    assert new["sig"] == 5 and new["version"] == 2 and new["coil_rel"] == 2.0 and new["sig_scope"] == 2
    assert SS.sweep_settings_of({"mesh_preset": base})["sig_scope"] is None
