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
}
KEYS = ("n_evals", "best", "history", "baseline", "baseline_line", "result",
        "final_validation_status", "error")


def _cloud(st):
    return sorted((tuple(sorted((p.get("overrides") or {}).items())),
                   p.get("kind") or p.get("phase"), repr(sorted(
                       (k, v) for k, v in p.items() if k not in ("t", "ts"))))
                  for p in st.get("points") or [])


@contextlib.contextmanager
def _patched(fn):
    real = O._subprocess_eval
    saved = dict(O._descent_state)
    O._subprocess_eval = fn
    O._descent_state["running"] = False
    O._descent_state["cancel"] = False
    try:
        yield
    finally:
        O._subprocess_eval = real
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
