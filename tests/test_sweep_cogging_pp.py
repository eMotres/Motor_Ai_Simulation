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
def _out(pp=0.42, ok=True, sufficient=True):
    if not ok:
        return {"ok": False, "error": "boom"}
    return {"ok": True, "res": {"T_em_Nm": 0.0, "T_ripple_pp_Nm": pp,
                                "cogging_sampling_final_quality_sufficient": sufficient}}


def test_48_steps_is_ranking_only_never_final():
    f = O._cogging_pp_fields(_out(), 48, 20.0)
    assert f["cogging_pp_Nm"] == 0.42 and f["cogging_final"] is False and f["cogging_steps_per_period"] == 48


def test_final_needs_120_steps_20c_and_sufficient_sampling():
    assert O._cogging_pp_fields(_out(), 120, 20.0)["cogging_final"] is True
    assert O._cogging_pp_fields(_out(), 144, 20.0)["cogging_final"] is True
    assert O._cogging_pp_fields(_out(), 144, 120.0)["cogging_final"] is False       # not at 20 C
    assert O._cogging_pp_fields(_out(), 144, None)["cogging_final"] is False        # card temperature unknown
    assert O._cogging_pp_fields(_out(sufficient=False), 144, 20.0)["cogging_final"] is False
    assert O._cogging_pp_fields(_out(), 119, 20.0)["cogging_final"] is False


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


# ── the whole sweep, solver faked ────────────────────────────────────────────────────────────
@pytest.fixture()
def sweep(monkeypatch, plain_cache):
    calls = []

    def fake_eval(overrides, current_a, steps, coil_temp_c, **kw):
        calls.append({"ov": dict(overrides), "I": current_a, "steps": steps, **kw})
        tw = float(overrides.get("tooth_width", 2.0))
        if current_a == 0.0:
            return {"ok": True, "res": {"T_em_Nm": 0.0, "T_ripple_pp_Nm": round(0.1 * tw, 4),
                                        "cogging_sampling_final_quality_sufficient": True}}
        return {"ok": True, "res": {"T_em_Nm": current_a * tw, "efficiency": 0.9 + 0.001 * current_a,
                                    "torque_per_mass_Nm_kg": 1.0, "T_ripple_pct": 3.0, "mass_total_kg": 1.0,
                                    "P_loss_total_W": 10.0, "V_peak": 10.0}}

    saved = {}
    monkeypatch.setattr(O, "_subprocess_eval", fake_eval)
    monkeypatch.setattr(O, "_geometry_reject", lambda ov: None)
    monkeypatch.setattr(O, "_save_last_scan", lambda r: None)
    monkeypatch.setattr(O._sweep_saves, "autosave", lambda r, **k: saved.setdefault("result", r))
    monkeypatch.setattr(O, "_job_workers", lambda: 2)

    def run(**kw):
        calls.clear()
        saved.clear()
        rid = kw.pop("run_id", "cog-pp-run")
        with O._scan_lock:
            O._scan_state.update({"running": True, "done": 0, "total": 0, "result": None, "points": [],
                                  "run_id": rid, "error": None, "cancel": False, "cached": 0})
        O._scan_worker(GEO_VARS, TWO_CURRENTS, 48, 120.0, 100.0, 24, 1, rid, mesh_size_mm=4.0,
                       min_size_mm=0.3, n_sectors=-1, gap_layers=1.0, element_order=2, **kw)
        with O._scan_lock:
            err = O._scan_state.get("error")
        assert not err, err
        return saved.get("result"), list(calls)
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


# ── cogging never alone ──────────────────────────────────────────────────────────────────────
def test_a_scan_cogging_limit_is_refused_alone_and_accepted_with_a_floor():
    with pytest.raises(ValidationError):
        _scan(cogging_pp=True, cogging_max_nm=0.5)                    # no efficiency / torque limit
    with pytest.raises(ValidationError):
        _scan(cogging_max_nm=0.5, efficiency_min_pct=90.0)            # not measured
    assert _scan(cogging_pp=True, cogging_max_nm=0.5, efficiency_min_pct=90.0).cogging_max_nm == 0.5
    assert _scan(cogging_pp=True, cogging_max_nm=0.5, torque_min_nm=10.0).torque_min_nm == 10.0


def _descent(**kw):
    body = {"variables": [{"name": "tooth_width", "min": 0.0, "max": 0.0}],
            "operating_point": {"current_a": 10.0, "gamma_deg": 5.0, "rpm": 3000.0},
            "max_iters": 2, "gap_layers": 1, "run_id": "cog-pp-descent"}
    body.update(kw)
    return O.DescentRequest(**body)


def test_the_descent_cogging_objective_is_refused_alone():
    with pytest.raises(ValidationError):
        _descent(objective="min_cogging")
    with pytest.raises(ValidationError):
        _descent(objective="min_ripple", cogging_max_nm=1.0)
    assert _descent(objective="min_cogging", efficiency_min_pct=95.0).objective == "min_cogging"
    assert _descent(cogging_max_nm=1.0).cogging_max_nm == 1.0         # beside the efficiency / torque ranking
    assert _descent().objective == "baseline_line"                    # off: as before
