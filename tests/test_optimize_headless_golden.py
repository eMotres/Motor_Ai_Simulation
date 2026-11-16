"""The headless runner against the route, on a stub eval (no FEM).

Golden rule of ~/jobs/TASK_headless_optimiser.md: the same small request
through ``POST /descent/start`` and through ``optimize_headless.run_descent``
gives the same candidates and the same numbers.  The eval memo of the runner
replays a finished run without computing anything, and a run stopped by its
eval cap grows to the full run from the same memo."""
from __future__ import annotations

import contextlib
import tempfile
import time
from pathlib import Path

from motor_ai_sim import optimize_headless as H
from motor_ai_sim.routes import optimization as O


def _stub(overrides, current_a, *a, **kw):
    """A deterministic machine: torque density peaks at tooth_width +0.4 mm
    and slot_height -0.3 mm off the config values; the bumped current buys
    torque density with efficiency (the baseline line's tilt)."""
    g = O.get_config()["geometry"]
    dt = float(overrides.get("tooth_width", g["tooth_width"])) - (g["tooth_width"] + 0.4)
    ds = float(overrides.get("slot_height", g["slot_height"])) - (g["slot_height"] - 0.3)
    q = -(dt * dt + 0.5 * ds * ds)
    i = float(current_a)
    return {"ok": True, "res": {
        "T_em_Nm": 3.0 + q + 0.01 * i, "torque_per_mass_Nm_kg": 10.0 + q + 0.02 * i,
        "efficiency": 0.95 - 0.0005 * i + 0.1 * q, "T_ripple_pct": 2.0,
        "mass_total_kg": 3.0, "P_loss_total_W": 100.0 + i, "V_peak": 50.0}}


BODY = {
    "variables": [{"name": "tooth_width", "min": 0.0, "max": 0.0},
                  {"name": "slot_height", "min": 0.0, "max": 0.0}],
    "operating_point": {"current_a": 10.0, "gamma_deg": 0.0, "rpm": 3000.0},
    "max_iters": 2, "auto_expand": False, "optimize_gamma": False,
    "objective": "baseline_line", "steps_per_period": 24, "run_id": "golden",
    "gap_layers": 2,
}
KEYS = ("n_evals", "best", "history", "baseline", "baseline_line", "result",
        "final_validation_status", "error")


def _cloud(st):
    return sorted((tuple(sorted((p.get("overrides") or {}).items())),
                   p.get("kind") or p.get("phase"), repr(sorted(
                       (k, v) for k, v in p.items() if k not in ("t", "ts"))))
                  for p in st.get("points") or [])


def _final_stub(*, best_x, best_metrics, coarse_base, score, **_kw):
    """The 6x standard re-solve is its own suite's business (as in
    test_screening_descent): certify the coarse winner as it is."""
    cost, F = score(best_metrics, coarse_base)
    return {"status": "certified", "sampling_purpose": "standard",
            "shortlist_only": True, "shortlist_limit": 3, "shortlist_count": 1,
            "validated": [], "failed": [],
            "winner": {"x": dict(best_x), "res": dict(best_metrics),
                       "cost": cost, "F": F}, "baseline": coarse_base}


@contextlib.contextmanager
def _patched(fn):
    real = O._subprocess_eval
    real_final = O._finalize_standard_shortlist
    saved = dict(O._descent_state)
    O._subprocess_eval = fn
    O._finalize_standard_shortlist = _final_stub
    O._descent_state["running"] = False
    O._descent_state["cancel"] = False
    try:
        yield
    finally:
        O._subprocess_eval = real
        O._finalize_standard_shortlist = real_final
        O._descent_state.clear()
        O._descent_state.update(saved)


def _route_run():
    with _patched(_stub):
        O.descent_start(O.DescentRequest(**BODY))
        t0 = time.time()
        time.sleep(0.2)
        while O._descent_state.get("running") and time.time() - t0 < 120:
            time.sleep(0.1)
        return dict(O._descent_state)


def _runner_run(memo_path, max_evals=None):
    with _patched(_stub):
        memo = H.EvalMemo(O, Path(memo_path), max_evals)
        O._subprocess_eval = memo
        st = H.run_descent(O, dict(BODY), log=lambda m: None, poll_s=0.05, memo=memo)
        return st, memo


def _same(a, b):
    for k in KEYS:
        assert O._json_sane(a.get(k)) == O._json_sane(b.get(k)), k
    assert _cloud(a) == _cloud(b)


def test_runner_gives_the_route_s_candidates_and_numbers():
    route = _route_run()
    assert not route.get("error"), route.get("error")
    assert route["n_evals"] > 3
    with tempfile.TemporaryDirectory() as d:
        st, memo = _runner_run(Path(d) / H.MEMO_FILE)
        _same(route, st)
        assert memo.hits == 0 and memo.computed > 0


def test_a_finished_run_replays_from_the_memo():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / H.MEMO_FILE
        first, m1 = _runner_run(p)
        again, m2 = _runner_run(p)
        assert m2.computed == 0 and m2.hits == m1.computed
        _same(first, again)


def test_a_capped_run_grows_to_the_full_run():
    with tempfile.TemporaryDirectory() as d:
        full, _ = _runner_run(Path(d) / "full.jsonl")
        p = Path(d) / H.MEMO_FILE
        part, m1 = _runner_run(p, max_evals=3)
        assert m1.capped and m1.computed == 3
        grown, m2 = _runner_run(p)
        assert m2.hits >= 3 and not m2.capped
        _same(full, grown)


def test_a_dropped_variable_is_refused():
    body = dict(BODY, variables=BODY["variables"] + [{"name": "air_gap", "min": 0.3, "max": 0.6}])
    with _patched(_stub):
        try:
            H.run_descent(O, body, log=lambda m: None)
        except H.Refused as e:
            assert "air_gap (not in sweep_whitelist)" in str(e)
        else:
            raise AssertionError("not refused")


def test_a_frozen_variable_is_refused():
    with _patched(_stub):
        try:
            H.run_descent(O, dict(BODY), frozen=["tooth_width"], log=lambda m: None)
        except H.Refused as e:
            assert "tooth_width" in str(e)
        else:
            raise AssertionError("not refused")


def test_fewer_than_two_gap_layers_are_refused():
    with _patched(_stub):
        try:
            H.run_descent(O, dict(BODY, gap_layers=1), log=lambda m: None)
        except H.Refused as e:
            assert "gap_layers 1.0" in str(e)
        else:
            raise AssertionError("not refused")


def test_suspect_results_are_used_but_not_replayed():
    with tempfile.TemporaryDirectory() as d:
        calls = []

        def fn(ov, cur, *a, **kw):
            calls.append(1)
            out = _stub(ov, cur, *a, **kw)
            if len(calls) == 1:
                out["res"]["p2_cut_edge_weld"] = {"rescued": 2, "unmatched": 0}
            if len(calls) == 2:
                out["res"]["ripple_self_check_rel"] = 0.7
            return out
        p = Path(d) / H.MEMO_FILE
        with _patched(fn):
            m = H.EvalMemo(O, p, None)
            a = m({"tooth_width": 1.0}, 10.0, 24, 120.0)
            b = m({"tooth_width": 2.0}, 10.0, 24, 120.0)
            c = m({"tooth_width": 3.0}, 10.0, 24, 120.0)
            assert a["ok"] and b["ok"] and c["ok"]
            assert len(m.memo) == 1                 # only the clean one is kept
            m({"tooth_width": 1.0}, 10.0, 24, 120.0)
            assert len(calls) == 4                  # the repaired one is solved again


def test_mtpa_run_settings_are_opt_in():
    seen = []

    def fn(ov, cur, *a, **kw):
        seen.append(kw)
        return _stub(ov, cur, *a, **kw)
    body = dict(BODY, optimize_gamma=True, max_iters=1, rotor_eddy=True,
                structured_gap=True, end_winding_factor=0.0)
    for on in (False, True):
        seen.clear()
        with _patched(fn):
            H.run_descent(O, dict(body, mtpa_run_settings=on), log=lambda m: None, poll_s=0.05)
        sweep = seen[:11]                     # the pre-sweep runs first: 11 angles
        assert sorted(kw["gamma_deg"] for kw in sweep) == [-50.0 + 10 * i for i in range(11)]
        if not on:
            # the eval defaults, exactly as before: no run switches passed
            assert all("structured_gap" not in kw and "rotor_eddy" not in kw for kw in sweep)
        else:
            assert all(kw["structured_gap"] is True and kw["gap_layers"] == 2.0
                       and kw["rotor_eddy"] is True and kw["rpm"] == 3000.0 for kw in sweep)


def test_default_descent_kwargs_are_unchanged():
    plan = O.prepare_descent(O.DescentRequest(**BODY))
    assert "mtpa_run_settings" not in plan["kwargs"]
    plan = O.prepare_descent(O.DescentRequest(**dict(BODY, mtpa_run_settings=True)))
    assert plan["kwargs"]["mtpa_run_settings"] is True


def _sweep_body(**kw):
    """Six points next to the config's own design (the pre-solve geometry
    fence must pass all of them, or nothing is evaluated)."""
    g = O.get_config()["geometry"]
    tw, sh = float(g["tooth_width"]), float(g["slot_height"])
    body = {
        "variables": [{"name": "tooth_width", "min": round(tw - 0.2, 1), "max": round(tw + 0.2, 1),
                       "mode": "sweep", "step": 0.2},
                      {"name": "slot_height", "min": round(sh - 0.2, 1), "max": round(sh, 1),
                       "mode": "sweep", "step": 0.2}],
        "operating_points": [{"gamma_deg": 0.0, "current_a": 0.0, "rpm": 630.9}],
        "steps_per_period": 24, "gap_layers": 2, "max_geometries": 20, "mesh_size_mm": 1.0,
        "min_size_mm": 0.3, "run_id": "golden-sweep",
    }
    body.update(kw)
    return body


@contextlib.contextmanager
def _patched_sweep(fn):
    real, real_store, real_cache = O._subprocess_eval, O._store_eval, O._EVAL_CACHE
    saved = dict(O._scan_state)
    O._subprocess_eval = fn
    O._store_eval = lambda k, v: None          # no cross-run cache hits between the two runs
    O._EVAL_CACHE = {}
    O._scan_state["running"] = False
    O._scan_state["cancel"] = False
    try:
        yield
    finally:
        O._subprocess_eval, O._store_eval, O._EVAL_CACHE = real, real_store, real_cache
        O._scan_state.clear()
        O._scan_state.update(saved)


def _sweep_points(st):
    return sorted(repr(sorted((k, v) for k, v in p.items() if k not in ("t", "ts", "elapsed_s")))
                  for p in st.get("points") or [])


def test_sweep_runner_gives_the_route_s_points():
    with _patched_sweep(_stub):
        O.scan_designs(O.ScanRequest(**_sweep_body()))
        t0 = time.time()
        time.sleep(0.2)
        while O._scan_state.get("running") and time.time() - t0 < 120:
            time.sleep(0.1)
        route = dict(O._scan_state)
    assert not route.get("error"), route.get("error")
    assert len(route["points"]) == 6
    assert all(p.get("fem") is not False and not p.get("geometry_rejected") for p in route["points"])
    with _patched_sweep(_stub):
        st = H.run_sweep(O, _sweep_body(), log=lambda m: None, poll_s=0.05)
    assert _sweep_points(route) == _sweep_points(st)


def test_sweep_refusals():
    with _patched_sweep(_stub):
        sw = _sweep_body()
        for body, word in ((dict(sw, mesh_size_mm=0.6), "mesh_size_mm 0.6 -> 1.0"),
                           (dict(sw, gap_layers=1), "gap_layers 1.0"),
                           (dict(sw, variables=sw["variables"] + [
                               {"name": "air_gap", "min": 0.3, "max": 0.6, "mode": "sweep", "step": 0.1}]),
                            "air_gap (not in sweep_whitelist)")):
            try:
                H.run_sweep(O, body, log=lambda m: None)
            except H.Refused as e:
                assert word in str(e), (word, str(e))
            else:
                raise AssertionError("not refused: " + word)


def test_sweep_magnet_temperature_is_opt_in():
    assert O._scan_pins(630.9, None) == {"rpm": 630.9}
    assert O._scan_pins(None, None) is None
    assert O._scan_pins(630.9, 20.0) == {"rpm": 630.9, "magnet_temp_c": 20.0}
    assert O.prepare_scan(O.ScanRequest(**_sweep_body()))["kwargs"] == {}
    assert O.prepare_scan(O.ScanRequest(**_sweep_body(magnet_temp_c=20)))["kwargs"] == {"magnet_temp_c": 20.0}
    seen = []

    def fn(ov, cur, *a, **kw):
        seen.append(kw.get("magnet_temp_c", "absent"))
        return _stub(ov, cur, *a, **kw)
    with _patched_sweep(fn):
        H.run_sweep(O, _sweep_body(magnet_temp_c=20.0), log=lambda m: None, poll_s=0.05)
    assert seen and set(seen) == {20.0}
    seen.clear()
    with _patched_sweep(fn):
        H.run_sweep(O, _sweep_body(), log=lambda m: None, poll_s=0.05)
    assert seen and set(seen) == {None}


def test_a_timeout_is_solved_once_more():
    calls = []

    def fn(ov, cur, *a, **kw):
        calls.append(1)
        return {"ok": False, "error": "timeout"} if len(calls) == 1 else _stub(ov, cur, *a, **kw)
    with tempfile.TemporaryDirectory() as d, _patched(fn):
        m = H.EvalMemo(O, Path(d) / H.MEMO_FILE, None)
        r = m({"tooth_width": 1.0}, 10.0, 24, 120.0)
        assert r["ok"] and len(calls) == 2 and m.retried == 1 and len(m.memo) == 1


def test_cogging_screen_is_opt_in():
    res = {"T_em_Nm": 0.001, "T_ripple_pct": 4.05e4, "mass_total_kg": 1.0}
    assert "T_ripple_pct" in (O._nonphysical_result(res) or "")
    assert O._nonphysical_result(res, skip=("T_ripple_pct",)) is None
    assert O._scan_pins(630.9, 20.0, True) == {"rpm": 630.9, "magnet_temp_c": 20.0, "cogging_screen": True}
    assert O._scan_pins(630.9, 20.0) == {"rpm": 630.9, "magnet_temp_c": 20.0}
    assert "cogging_screen" not in O.prepare_scan(O.ScanRequest(**_sweep_body()))["kwargs"]
    assert O.prepare_scan(O.ScanRequest(**_sweep_body(cogging_screen=True)))["kwargs"] == {"cogging_screen": True}
    seen = []

    def fn(ov, cur, *a, **kw):
        seen.append(kw.get("ripple_pct_gate", "absent"))
        return _stub(ov, cur, *a, **kw)
    with _patched_sweep(fn):
        H.run_sweep(O, _sweep_body(cogging_screen=True), log=lambda m: None, poll_s=0.05)
    assert seen and set(seen) == {False}
    seen.clear()
    with _patched_sweep(fn):
        H.run_sweep(O, _sweep_body(), log=lambda m: None, poll_s=0.05)
    assert seen and set(seen) == {"absent"}
