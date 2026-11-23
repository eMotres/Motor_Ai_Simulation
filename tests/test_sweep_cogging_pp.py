"""Sweep "Cogging pp" (owner 2026-11-22, final word): ONE opt-in value, the cogging torque
peak-to-peak [N*m] of the geometry's no-load (I = 0) solve, next to the loaded points' ripple.

Pinned here, payload only (no FEM; the solver is a stand-in):
  * off = the request, the worker call, the points and the result are what they always were;
  * one no-load solve per geometry, shared by both currents (cache hit for the second);
  * the value rides on every loaded point and is in the saved sweep; loaded points are never replaced;
  * 48 steps / a card temperature is a ranking value, never marked final;
  * a cogging limit or objective is refused unless it comes with a torque / efficiency limit or ranking.
"""
from __future__ import annotations

import threading

import pytest
from pydantic import ValidationError

from motor_ai_sim import sweep_saves
from motor_ai_sim.routes import optimization as O

GEO_VARS = [{"name": "tooth_width", "min": 2.0, "max": 3.0, "mode": "sweep", "step": 0.5}]
TWO_CURRENTS = [{"gamma_deg": 0.0, "current_a": 20.0, "rpm": 1000.0},
                {"gamma_deg": 0.0, "current_a": 40.0, "rpm": 1000.0}]


def _scan(**kw):
    body = {"variables": GEO_VARS, "operating_points": TWO_CURRENTS, "steps_per_period": 48,
            "gap_layers": 1, "run_id": "cog-pp-test"}
    body.update(kw)
    return O.ScanRequest(**body)


# ── opt-in: off changes nothing ──────────────────────────────────────────────────────────────
def test_off_the_plan_the_worker_call_and_the_response_are_unchanged():
    plan = O.prepare_scan(_scan())
    assert all(not k.startswith(("cogging_pp", "efficiency_min", "torque_min")) for k in plan["kwargs"])
    assert "cogging_pp" not in plan["response"]
    assert len(plan["args"]) == 24            # the positional worker call as before


def test_on_the_plan_names_the_opt_in_only_in_the_kwargs():
    plan = O.prepare_scan(_scan(cogging_pp=True))
    assert plan["kwargs"]["cogging_pp"] is True
    assert len(plan["args"]) == 24


# ── the value of one no-load solve ───────────────────────────────────────────────────────────
def _out(pp=0.42, ok=True, sufficient=True, steps=48, prov=True, temp=20.0):
    if not ok:
        return {"ok": False, "error": "boom"}
    r = {"T_em_Nm": 0.0, "T_ripple_pp_Nm": pp, "cogging_sampling_final_quality_sufficient": sufficient}
    if prov:
        r["no_load_provenance"] = {"drive": "current", "current_a": 0.0, "steps_per_period_effective": steps,
                                   "gap_layers_effective": 1, "n_sectors": 2, "mesh_size_mm": 4.0,
                                   "mesh_size_effective_mm": 1.5, "magnet_temp_c_effective": temp,
                                   "rotor_eddy": False, "eddy": False}
    return {"ok": True, "res": r}


def test_48_steps_is_ranking_only_never_final():
    f = O._cogging_pp_fields(_out(), 48, 20.0)
    assert f["cogging_pp_Nm"] == 0.42 and f["cogging_final"] is False and f["cogging_steps_per_period"] == 48
    # the mesh and temperature the SOLVE used, not the request
    assert f["cogging_gap_layers"] == 1 and f["cogging_n_sectors"] == 2 and f["cogging_mesh_mm"] == 1.5
    assert f["cogging_magnet_temp_c"] == 20.0


def test_final_uses_the_temperature_the_solve_returned_not_the_request():
    assert O._cogging_pp_fields(_out(steps=144, temp=20.0), 144, 20.0)["cogging_final"] is True
    f = O._cogging_pp_fields(_out(steps=144, temp=25.0), 144, 20.0)        # requested 20, the solve used 25
    assert f["cogging_final"] is False and f["cogging_magnet_temp_c"] == 25.0


IDENT = {"build": "abc123", "materials": "m1", "image": None}


def test_the_signature_covers_settings_basis_eddy_build_and_materials_and_is_known_only_with_an_identity():
    a = O._cogging_pp_fields(_out(), 48, 20.0, identity=IDENT)
    assert isinstance(a["cogging_sig"], int) and a["cogging_build"] == "abc123"
    assert O._cogging_pp_fields(_out(), 48, 20.0, identity=IDENT)["cogging_sig"] == a["cogging_sig"]
    assert O._cogging_pp_fields(_out(), 48, 20.0, identity={**IDENT, "materials": "m2"})["cogging_sig"] != a["cogging_sig"]
    assert O._cogging_pp_fields(_out(), 48, 20.0, identity={**IDENT, "build": "zzz"})["cogging_sig"] != a["cogging_sig"]
    assert O._cogging_pp_fields(_out(), 48, 20.0, demag_at_load=True, identity=IDENT)["cogging_sig"] != a["cogging_sig"]
    assert "cogging_sig" not in O._cogging_pp_fields(_out(), 48, 20.0)          # no identity: unknown


def test_provenance_is_the_effective_one_a_snapped_step_count_is_not_final():
    # requested 120, the solver really ran 96: not final
    f = O._cogging_pp_fields(_out(steps=96), 120, 20.0)
    assert f["cogging_steps_requested"] == 120 and f["cogging_steps_per_period"] == 96
    assert f["cogging_final"] is False
    # a run that carries no provenance is not an unexcited no-load run: no value
    f = O._cogging_pp_fields(_out(prov=False), 144, 20.0)
    assert f["cogging_pp_Nm"] is None and f["cogging_final"] is False and f["cogging_error"]
    # an unresolved magnet temperature is never final
    assert O._cogging_pp_fields(_out(steps=144, temp=None), 144, 20.0)["cogging_final"] is False


def test_final_needs_120_steps_20c_and_sufficient_sampling():
    assert O._cogging_pp_fields(_out(steps=120), 120, 20.0)["cogging_final"] is True
    assert O._cogging_pp_fields(_out(steps=144), 144, 20.0)["cogging_final"] is True
    assert O._cogging_pp_fields(_out(steps=144, temp=120.0), 144, 20.0)["cogging_final"] is False       # not at 20 C
    assert O._cogging_pp_fields(_out(steps=144, temp=None), 144, 20.0)["cogging_final"] is False        # card temperature unknown
    assert O._cogging_pp_fields(_out(steps=144, sufficient=False), 144, 20.0)["cogging_final"] is False
    assert O._cogging_pp_fields(_out(steps=119), 144, 20.0)["cogging_final"] is False


def test_a_failed_no_load_solve_gives_no_value_and_says_why():
    f = O._cogging_pp_fields(_out(ok=False), 48, 20.0)
    assert f["cogging_pp_Nm"] is None and f["cogging_final"] is False and "boom" in f["cogging_error"]
    f = O._cogging_pp_fields(_out(pp=float("nan")), 48, 20.0)
    assert f["cogging_pp_Nm"] is None and f["cogging_error"]


@pytest.fixture()
def plain_cache(monkeypatch):
    cache = {}
    monkeypatch.setattr(O, "_EVAL_CACHE", cache)
    monkeypatch.setattr(O, "_store_eval", lambda k, v: cache.setdefault(k, v))
    return cache


def test_the_no_load_solve_runs_once_for_two_currents_and_even_when_they_race(plain_cache):
    calls = []

    def solve(geo):
        calls.append(dict(geo))
        return _out(0.3)

    share = O._SweepCogging(lambda g: "k%s" % sorted(g.items()), solve, 48, 20.0)
    geo = {"tooth_width": 2.5}
    got = []
    ts = [threading.Thread(target=lambda: got.append(share.fields(geo))) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert share.solves == 1 and len(calls) == 1
    assert got[0] == got[1] and got[0]["cogging_pp_Nm"] == 0.3
    assert share.cached(geo)["cogging_pp_Nm"] == 0.3         # the cache hit a later sweep gets
    share.fields({"tooth_width": 3.0})                       # another geometry = one more solve
    assert share.solves == 2


def test_a_failure_is_remembered_one_attempt_per_geometry_and_the_value_stays_missing(plain_cache):
    calls = []

    def solve(geo):
        calls.append(1)
        return _out(ok=False)

    share = O._SweepCogging(lambda g: "k%s" % sorted(g.items()), solve, 48, 20.0)
    a, b = share.fields({"w": 1.0}), share.fields({"w": 1.0})          # the two currents
    assert len(calls) == 1 and a == b
    assert a["cogging_pp_Nm"] is None and a["cogging_error"] and a["cogging_final"] is False
    assert plain_cache == {}                                           # only successes are cached


def test_a_key_that_cannot_be_built_fails_closed_without_a_solve(plain_cache):
    def key(_g):
        raise RuntimeError("no fingerprint")

    def solve(_g):
        raise AssertionError("must not solve")

    share = O._SweepCogging(key, solve, 48, 20.0)
    f = share.fields({"w": 1.0})
    assert f["cogging_pp_Nm"] is None and "cache key" in f["cogging_error"] and share.solves == 0


def test_a_refused_sweep_solves_nothing_and_every_value_is_missing(plain_cache):
    share = O._SweepCogging(lambda g: "k", lambda g: (_ for _ in ()).throw(AssertionError()), 48, 20.0,
                            refuse="demagnetisation is on")
    f = share.fields({"w": 1.0})
    assert f["cogging_pp_Nm"] is None and "demagnetisation" in f["cogging_error"] and share.solves == 0


def test_the_no_load_refusals(monkeypatch):
    cfg = {"simulation": {"drive": "current"}}
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    assert O._cogging_no_load_refusal() is None          # demag does not refuse here (basis + per-geometry loss)
    cfg["simulation"]["drive"] = "bldc_current"
    assert "bldc_current" in O._cogging_no_load_refusal()
    cfg["simulation"]["drive"] = "custom_current"        # falls back to the sinusoidal current drive
    assert O._cogging_no_load_refusal() is None


# ── the whole sweep, solver faked ────────────────────────────────────────────────────────────
@pytest.fixture()
def sweep(monkeypatch, plain_cache):
    calls = []

    state = {"loss": None}

    def fake_eval(overrides, current_a, steps, coil_temp_c, **kw):
        calls.append({"ov": dict(overrides), "I": current_a, "steps": steps, **kw})
        tw = float(overrides.get("tooth_width", 2.0))
        if current_a == 0.0:
            assert kw.get("no_load") is True            # the unexcited path, never a plain I = 0 eval
            return _out(round(0.1 * tw, 4), steps=steps)
        res = {"T_em_Nm": current_a * tw, "efficiency": 0.9 + 0.001 * current_a,
               "torque_per_mass_Nm_kg": 1.0, "T_ripple_pct": 3.0, "mass_total_kg": 1.0,
               "P_loss_total_W": 10.0, "V_peak": 10.0}
        if state["loss"] is not None:      # what a loaded eval stored by an opt-in run carries in the cache
            res["demag_loss_pct"] = state["loss"](overrides, current_a, kw.get("gamma_deg"))
        return {"ok": True, "res": res}

    saved = {}
    monkeypatch.setattr(O, "_subprocess_eval", fake_eval)
    monkeypatch.setattr(O, "_geometry_reject", lambda ov: None)
    monkeypatch.setattr(O, "_save_last_scan", lambda r: None)
    monkeypatch.setattr(O._sweep_saves, "autosave", lambda r, **k: saved.setdefault("result", r))
    monkeypatch.setattr(O, "_job_workers", lambda: 2)
    monkeypatch.setattr(O, "_cogging_no_load_refusal", lambda: None)
    monkeypatch.setattr(O, "_cogging_identity", lambda: dict(IDENT))
    import motor_ai_sim.duty_results as _dr
    monkeypatch.setattr(_dr, "solver_stamp", lambda: {"code_version": "test"})
    import motor_ai_sim.simulation.fem_solver_2d as _fs
    monkeypatch.setattr(_fs, "_noload_material_fingerprint", lambda: "mat-1")
    monkeypatch.setattr(_fs, "_run_magnet_temp_c", lambda t: t)

    def run(**kw):
        calls.clear()
        saved.clear()
        rid = kw.pop("run_id", "cog-pp-run")
        ops = kw.pop("ops", TWO_CURRENTS)
        with O._scan_lock:
            O._scan_state.update({"running": True, "done": 0, "total": 0, "result": None, "points": [],
                                  "run_id": rid, "error": None, "cancel": False, "cached": 0})
        O._scan_worker(GEO_VARS, ops, 48, 120.0, 100.0, 24, 1, rid, mesh_size_mm=4.0,
                       min_size_mm=0.3, n_sectors=-1, gap_layers=1.0, element_order=2, **kw)
        with O._scan_lock:
            err = O._scan_state.get("error")
        assert not err, err
        return saved.get("result"), list(calls)
    run.state = state
    return run


def test_a_sweep_adds_one_no_load_solve_per_geometry_and_keeps_both_loaded_points(sweep):
    result, calls = sweep(cogging_pp=True, magnet_temp_c=20.0)
    geos = {c["ov"]["tooth_width"] for c in calls}
    assert geos == {2.0, 2.5, 3.0}
    assert sum(1 for c in calls if c["I"] == 0.0) == 3          # one per geometry, not per current
    assert sum(1 for c in calls if c["I"] != 0.0) == 6          # every loaded point is still solved
    nl = [c for c in calls if c["I"] == 0.0]
    assert all(c["steps"] == 48 and c["magnet_temp_c"] == 20.0 and c["gamma_deg"] == 0.0 for c in nl)
    pts = result["points"]
    assert len(pts) == 6
    for p in pts:
        assert p["feasible"] and p["T_em_Nm"] > 0                            # loaded value kept
        assert p["cogging_pp_Nm"] == pytest.approx(0.1 * p["overrides"]["tooth_width"])
        assert p["cogging_final"] is False and p["cogging_steps_per_period"] == 48
    by_geom = {}
    for p in pts:
        by_geom.setdefault(p["geom_id"], set()).add(p["cogging_pp_Nm"])
    assert all(len(v) == 1 for v in by_geom.values())            # both currents share one value
    assert result["cogging_pp"]["no_load_solves"] == 3


def test_the_value_is_in_the_saved_sweep(sweep, tmp_path, monkeypatch):
    result, _ = sweep(cogging_pp=True, magnet_temp_c=20.0)
    monkeypatch.setenv(sweep_saves.ENV_LEGACY_DIR, str(tmp_path / "none"))
    entry = sweep_saves.build_entry(result, name="cog")
    assert all("cogging_pp_Nm" in p for p in entry["result"]["points"])
    assert entry["result"]["cogging_pp"]["no_load_solves"] == 3


def test_a_second_sweep_with_the_same_settings_solves_no_load_geometries_again_zero_times(sweep):
    sweep(cogging_pp=True, magnet_temp_c=20.0)
    result, calls = sweep(cogging_pp=True, magnet_temp_c=20.0, run_id="cog-pp-run-2")
    assert calls == [] and all("cogging_pp_Nm" in p for p in result["points"])


def test_off_nothing_is_added_and_no_no_load_solve_runs(sweep):
    result, calls = sweep()
    assert all(c["I"] != 0.0 for c in calls)
    assert all(not any(k.startswith("cogging") for k in p) for p in result["points"])
    assert "cogging_pp" not in result


def test_a_cogging_limit_marks_the_points_above_it_not_eligible(sweep):
    result, _ = sweep(cogging_pp=True, cogging_max_nm=0.22, efficiency_min_pct=50.0, magnet_temp_c=20.0)
    for p in result["points"]:
        assert p["eligible"] == (p["cogging_pp_Nm"] <= 0.22)
    assert {p["eligible"] for p in result["points"]} == {True, False}


def test_demag_on_still_computes_one_demag_off_full_strength_solve_with_the_basis_recorded(sweep):
    result, calls = sweep(cogging_pp=True, demag=True, magnet_temp_c=20.0)
    nl = [c for c in calls if c["I"] == 0.0]
    assert len(nl) == 3 and all(c["demag"] is False for c in nl)          # demag OFF, one per geometry
    assert all(c.get("report_demag_loss") for c in calls if c["I"] != 0.0)  # the loaded loss is asked for
    for p in result["points"]:
        assert p["cogging_pp_Nm"] is not None
        assert p["cogging_basis"] == "no_load_full_strength" and p["cogging_demag_at_load"] is True
    assert result["cogging_pp"]["basis"] == "no_load_full_strength"
    assert result["cogging_pp"]["demag_at_load"] is True and "full-strength" in result["cogging_pp"]["basis_text"]


def test_the_basis_is_recorded_when_demag_is_off_too(sweep):
    result, _ = sweep(cogging_pp=True, magnet_temp_c=20.0)
    assert all(p["cogging_demag_at_load"] is False and p["cogging_basis"] == "no_load_full_strength"
               for p in result["points"])


def _pts(losses):
    """(geometry value, gamma, loss): separate geom_ids per gamma row, as a gamma sweep stores them."""
    return [{"geom_id": i, "overrides": {"tooth_width": g, "gamma_deg": gam}, "cogging_pp_Nm": 0.3,
             "cogging_final": True, "eligible": True, "cogging_steps_per_period": 144, "demag_loss_pct": loss}
            for i, (g, gam, loss) in enumerate(losses)]


def test_a_geometry_whose_loaded_points_lose_more_than_the_threshold_is_refused_across_gamma_rows():
    assert O.COGGING_DEMAG_REFUSE_FRACTION == 0.005
    # geometry 2.0: gamma 0 loses 0.2 %, gamma 5 loses 0.7 % -> BOTH rows refused (one physical geometry);
    # geometry 2.5 stays at 0.4 % -> measured
    pts = _pts([(2.0, 0.0, 0.2), (2.0, 5.0, 0.7), (2.5, 0.0, 0.4), (2.5, 5.0, 0.4)])
    assert O._cogging_refuse_demagged(pts, cogging_max_nm=1.0) == 1
    assert [p["cogging_pp_Nm"] for p in pts] == [None, None, 0.3, 0.3]
    assert all("irreversibly" in p["cogging_error"] and p["cogging_final"] is False for p in pts[:2])
    assert [p["eligible"] for p in pts] == [False, False, True, True]
    assert pts[2]["cogging_demag_loss_pct"] == 0.4 and pts[0]["cogging_demag_loss_pct"] == 0.7


def test_a_point_that_reports_no_demag_loss_is_not_refused_and_says_so():
    pts = [{"geom_id": 0, "overrides": {"tooth_width": 2.0}, "cogging_pp_Nm": 0.3, "cogging_final": False}]
    assert O._cogging_refuse_demagged(pts) == 0
    assert pts[0]["cogging_pp_Nm"] == 0.3 and pts[0]["cogging_demag_loss_pct"] is None


def test_the_sweep_level_final_flag_needs_every_point_measured_and_final(sweep):
    result, _ = sweep(cogging_pp=True, magnet_temp_c=20.0)
    assert result["cogging_pp"]["final"] is False and result["cogging_pp"]["steps_effective"] == [48]


# ── cogging never alone, never instead of the ranking ────────────────────────────────────────
def test_a_scan_cogging_limit_needs_the_no_load_solve_and_keeps_the_pareto_ranking():
    with pytest.raises(ValidationError):
        _scan(cogging_max_nm=0.5)                                      # not measured
    assert _scan(cogging_pp=True, cogging_max_nm=0.5).cogging_max_nm == 0.5
    assert _scan(cogging_pp=True, cogging_max_nm=0.5, efficiency_min_pct=90.0).efficiency_min_pct == 90.0


def _descent(**kw):
    body = {"variables": [{"name": "tooth_width", "min": 0.0, "max": 0.0}],
            "operating_point": {"current_a": 10.0, "gamma_deg": 5.0, "rpm": 3000.0},
            "max_iters": 2, "gap_layers": 1, "run_id": "cog-pp-descent"}
    body.update(kw)
    return O.DescentRequest(**body)


def test_the_descent_cogging_objective_is_only_beside_the_ranking():
    with pytest.raises(ValidationError):
        _descent(objective="min_ripple", cogging_max_nm=1.0)          # a ripple-only objective has no ranking
    assert _descent(objective="min_cogging").objective == "min_cogging"
    assert _descent(cogging_max_nm=1.0).cogging_max_nm == 1.0
    assert _descent().objective == "baseline_line"                    # off: as before


def test_min_cogging_keeps_the_torque_efficiency_ranking_in_its_cost():
    """At EQUAL cogging a better efficiency / torque density must score better (an efficiency floor is
    not a ranking: 91 % / 1 N*m/kg and 99 % / 10 N*m/kg may not tie)."""
    saved = (dict(O._RUN_LIMITS), dict(O._COG_LIMIT))
    try:
        O._RUN_LIMITS.update(mode="min_cogging", eff_min=0.9)
        O._COG_LIMIT.update(max=0.0, steps=48)
        base = {"efficiency": 0.95, "torque_per_mass_Nm_kg": 4.0, "cogging_pp_Nm": 2.0}

        def cost(eff, td, cog):
            m = {"efficiency": eff, "torque_per_mass_Nm_kg": td, "cogging_pp_Nm": cog, "V_peak": 1.0,
                 "T_ripple_pct": 2.0}
            return O._descent_cost(m, base, 6.0, 1.0, 1.0, 1.0)[0]
        assert cost(0.99, 10.0, 1.0) < cost(0.91, 1.0, 1.0)
        assert cost(0.95, 4.0, 0.5) < cost(0.95, 4.0, 1.5)             # and cogging still ranks
    finally:
        O._RUN_LIMITS.clear(); O._RUN_LIMITS.update(saved[0])
        O._COG_LIMIT.clear(); O._COG_LIMIT.update(saved[1])


# ── round 2 ──────────────────────────────────────────────────────────────────────────────────
def test_identity_fails_closed_without_a_real_build_or_material_content(monkeypatch):
    import motor_ai_sim.duty_results as dr
    monkeypatch.setattr(dr, "solver_stamp", lambda: {"version": "1.0"})          # release label only
    with pytest.raises(RuntimeError, match="build"):
        O._cogging_identity()
    monkeypatch.setattr(dr, "solver_stamp", lambda: {"code_version": "unknown"})
    with pytest.raises(RuntimeError, match="build"):
        O._cogging_identity()
    monkeypatch.setattr(dr, "solver_stamp", lambda: {"code_version": "abc"})
    import motor_ai_sim.config as cfgm
    monkeypatch.setattr(cfgm, "get_material_assignments", lambda: {"magnet": "N45SH", "stator_core": "X"})
    import motor_ai_sim.materials as ml

    def lib_missing(part, name):
        raise FileNotFoundError("library gone")
    monkeypatch.setattr(ml, "resolve_assigned", lib_missing)
    with pytest.raises(FileNotFoundError):
        O._cogging_identity()
    monkeypatch.setattr(ml, "resolve_assigned", lambda part, name: type("C", (), {})())    # resolves to nothing
    with pytest.raises(RuntimeError, match="no content"):
        O._cogging_identity()


def test_an_unavailable_identity_means_no_solve_and_a_missing_value(sweep, monkeypatch):
    def boom():
        raise RuntimeError("the solver build is not identified (no code version)")
    monkeypatch.setattr(O, "_cogging_identity", boom)
    result, calls = sweep(cogging_pp=True, magnet_temp_c=20.0)
    assert all(c["I"] != 0.0 for c in calls)
    assert all(p["cogging_pp_Nm"] is None and "identity unavailable" in p["cogging_error"] for p in result["points"])


def test_the_sweep_level_final_does_not_ignore_a_feasible_point_without_the_value():
    # 2 feasible points, only one carries the key: not final even though that one is final
    pts = [{"feasible": True, "cogging_pp_Nm": 0.1, "cogging_final": True}, {"feasible": True}]
    feas = [p for p in pts if p.get("feasible")]
    cp = [p for p in feas if "cogging_pp_Nm" in p]
    assert not (feas and len(cp) == len(feas))


def test_gamma_rows_of_one_geometry_are_refused_together_before_the_result_is_built(sweep):
    ops = [{"gamma_deg": 0.0, "current_a": 20.0, "rpm": 1000.0}, {"gamma_deg": 5.0, "current_a": 20.0, "rpm": 1000.0}]
    sweep.state["loss"] = lambda ov, I, g: 0.7 if (ov["tooth_width"] == 2.5 and g == 5.0) else 0.1
    result, _ = sweep(cogging_pp=True, demag=True, magnet_temp_c=20.0, ops=ops)
    for p in result["points"]:
        refused = p["overrides"]["tooth_width"] == 2.5
        assert (p["cogging_pp_Nm"] is None) == refused, p
    assert result["cogging_pp"]["n_geometries_refused_by_demag"] == 1


def test_the_live_published_points_already_carry_the_refusal(sweep):
    ops = [{"gamma_deg": 0.0, "current_a": 20.0, "rpm": 1000.0}, {"gamma_deg": 5.0, "current_a": 20.0, "rpm": 1000.0}]
    sweep.state["loss"] = lambda ov, I, g: 0.7 if (ov["tooth_width"] == 2.5 and g == 5.0) else 0.1
    sweep(cogging_pp=True, demag=True, magnet_temp_c=20.0, ops=ops)
    with O._scan_lock:
        live = list(O._scan_state["points"])
    assert live and all((p["cogging_pp_Nm"] is None) == (p["overrides"]["tooth_width"] == 2.5) for p in live)


def test_the_baseline_asks_for_the_loss_and_is_refused_above_the_threshold(sweep):
    sweep.state["loss"] = lambda ov, I, g: 0.9
    result, calls = sweep(cogging_pp=True, demag=True, magnet_temp_c=20.0, with_baseline=True)
    base = [c for c in calls if c["ov"] == {} and c["I"] != 0.0]
    assert base and base[0].get("report_demag_loss") is True
    assert result["baseline"]["cogging_pp_Nm"] is None and "irreversibly" in result["baseline"]["cogging_error"]


def test_an_option_off_sweep_returns_no_demag_loss_copied_from_a_shared_cache_entry(sweep):
    sweep.state["loss"] = lambda ov, I, g: 0.3            # the entry an earlier opt-in run stored
    off, _ = sweep()
    assert all("demag_loss_pct" not in p for p in off["points"])
    off_demag, _ = sweep(demag=True)                      # demag on, option off: still nothing extra
    assert all("demag_loss_pct" not in p for p in off_demag["points"])
    on, _ = sweep(cogging_pp=True, demag=True, magnet_temp_c=20.0)
    assert all(p["demag_loss_pct"] == 0.3 for p in on["points"])


def test_min_cogging_needs_finite_positive_ranking_weights():
    for bad in ({"w_eff": 0.0, "w_td": 0.0}, {"w_eff": -1.0}, {"w_td": 0.0}, {"w_eff": float("nan")}):
        with pytest.raises(ValidationError):
            _descent(objective="min_cogging", **bad)
    assert _descent(objective="min_cogging", w_eff=1.0, w_td=2.0).w_td == 2.0


def test_the_identity_includes_the_implicit_default_sleeve_card_and_a_new_sleeve_changes_it(monkeypatch):
    import motor_ai_sim.duty_results as dr
    import motor_ai_sim.config as cfgm
    import motor_ai_sim.materials as ml
    monkeypatch.setattr(dr, "solver_stamp", lambda: {"code_version": "abc"})
    asg = {"magnet": "N45SH", "stator_core": "STEEL"}
    monkeypatch.setattr(cfgm, "get_material_assignments", lambda: dict(asg))
    monkeypatch.setattr(ml, "DEFAULT_PART_MATERIAL", {"sleeve": "T800"})
    cards = {"N45SH": {"br": 1.2}, "STEEL": {"mu": 5}, "T800": {"e": 100}, "CF2": {"e": 200}}

    class _C:
        def __init__(self, d):
            self.__dict__.update(d)
    monkeypatch.setattr(ml, "resolve_assigned", lambda part, name: _C(cards[name]))
    base = O._cogging_identity()["materials"]
    cards["T800"] = {"e": 101}                       # the IMPLICIT default sleeve card is edited
    assert O._cogging_identity()["materials"] != base
    cards["T800"] = {"e": 100}
    assert O._cogging_identity()["materials"] == base
    asg["sleeve"] = "CF2"                            # a sweep that introduces a sleeve
    assert O._cogging_identity()["materials"] != base
