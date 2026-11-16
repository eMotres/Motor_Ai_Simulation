"""prepare_scan: the Sweep route's request assembly, callable without the web
process (headless optimiser, Sweep mode).  The route hands its worker thread
exactly the plan's arguments and installs the plan's initial state; a variable
the whitelist leaves out is named in ``dropped``."""
from __future__ import annotations

import contextlib

from motor_ai_sim.routes import optimization as O


def _req(**kw):
    body = {"variables": [{"name": "tooth_width", "min": 2.0, "max": 3.0, "mode": "sweep", "step": 0.5},
                          {"name": "air_gap", "min": 0.3, "max": 0.6, "mode": "sweep", "step": 0.1},
                          {"name": "gamma_deg", "min": -10, "max": 10, "mode": "sweep", "step": 10}],
            "operating_points": [{"gamma_deg": 0.0, "current_a": 0.0, "rpm": 630.9}],
            "steps_per_period": 144, "gap_layers": 2, "run_id": "prep-scan"}
    body.update(kw)
    return O.ScanRequest(**body)


class _Thread:
    seen = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        _Thread.seen.append({"args": args, "kwargs": kwargs})

    def start(self):
        pass

    def is_alive(self):
        return False


@contextlib.contextmanager
def no_thread():
    _Thread.seen = []
    real = O.threading.Thread
    O.threading.Thread = _Thread
    saved = dict(O._scan_state)
    O._scan_state["running"] = False
    try:
        yield
    finally:
        O.threading.Thread = real
        O._scan_state.clear()
        O._scan_state.update(saved)


def test_route_runs_the_prepared_plan():
    with no_thread():
        plan = O.prepare_scan(_req())
        resp = O.scan_designs(_req())
        assert resp == plan["response"]
        assert _Thread.seen[0]["args"] == plan["args"]
        for k, v in plan["init_state"].items():
            if k != "ts_start":
                assert O._scan_state[k] == v, k


def test_dropped_variables_are_named_and_gamma_is_kept():
    plan = O.prepare_scan(_req())
    assert [v["name"] for v in plan["variables"]] == ["tooth_width", "gamma_deg"]
    assert plan["dropped"] == [{"name": "air_gap", "reason": "not in sweep_whitelist"}]


def test_prepare_has_no_side_effect():
    with no_thread():
        before = dict(O._scan_state)
        O.prepare_scan(_req())
        assert dict(O._scan_state) == before
