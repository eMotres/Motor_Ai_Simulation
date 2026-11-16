"""prepare_descent: the descent route's request assembly, callable without the
web process (step 1 of the headless optimiser, ~/jobs/TASK_headless_optimiser.md).

The route must hand its worker thread exactly what ``prepare_descent`` returns,
and leave ``_descent_state`` exactly as ``init_state`` says; a variable the
route drops is named in ``dropped`` so the headless runner can refuse it."""
from __future__ import annotations

import contextlib

import pytest

from motor_ai_sim.routes import optimization as O


def _req(**kw):
    body = {
        "variables": [
            {"name": "tooth_width", "min": 0.0, "max": 0.0},
            {"name": "slot_height", "min": 0.0, "max": 0.0, "step": 0.2},
            # not in sweep_whitelist: dropped by the route, named by the plan
            {"name": "air_gap", "min": 0.3, "max": 0.6},
        ],
        "operating_point": {"current_a": 10.0, "gamma_deg": 5.0, "rpm": 3000.0},
        "max_iters": 3, "ripple_penalty_lambda": 2.0, "thd_penalty_lambda": 1.5,
        "gap_layers": 2, "run_id": "prep-test",
    }
    body.update(kw)
    return O.DescentRequest(**body)


class _Thread:
    seen = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        _Thread.seen.append({"args": args, "kwargs": kwargs})

    def start(self):
        pass


@contextlib.contextmanager
def no_thread():
    """The route's worker thread is recorded, never started; the module's run
    state and penalty globals are restored afterwards."""
    _Thread.seen = []
    real = O.threading.Thread
    O.threading.Thread = _Thread
    saved = dict(O._descent_state)
    pen = (dict(O._RIPPLE_PEN_LAM), dict(O._THD_PEN))
    O._descent_state["running"] = False
    try:
        yield
    finally:
        O.threading.Thread = real
        O._descent_state.clear()
        O._descent_state.update(saved)
        O._RIPPLE_PEN_LAM.update(pen[0])
        O._THD_PEN.update(pen[1])


def test_route_runs_the_prepared_plan():
    with no_thread():
        plan = O.prepare_descent(_req())
        resp = O.descent_start(_req())
        assert resp == plan["response"]
        assert len(_Thread.seen) == 1
        assert _Thread.seen[0]["args"] == plan["args"]
        assert _Thread.seen[0]["kwargs"] == plan["kwargs"]
        for k, v in plan["init_state"].items():
            assert O._descent_state[k] == v, k
        assert O._RIPPLE_PEN_LAM["v"] == O._RIPPLE_PEN_LAM["v0"] == 2.0
        assert (O._THD_PEN["lam"], O._THD_PEN["max"]) == (1.5, 5.0)


def test_prepare_has_no_side_effect():
    with no_thread():
        before = (dict(O._descent_state), dict(O._RIPPLE_PEN_LAM), dict(O._THD_PEN))
        O.prepare_descent(_req())
        assert (dict(O._descent_state), dict(O._RIPPLE_PEN_LAM), dict(O._THD_PEN)) == before


def test_dropped_variables_are_named():
    plan = O.prepare_descent(_req())
    assert [s["name"] for s in plan["var_specs"]] == ["tooth_width", "slot_height"]
    assert plan["dropped"] == [{"name": "air_gap", "reason": "not in sweep_whitelist"}]
    slot = plan["var_specs"][1]
    assert slot["step"] == 0.2 and slot["quant"] == 0.1


def test_empty_plan_is_still_a_400():
    with no_thread():
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as e:
            O.descent_start(_req(variables=[{"name": "air_gap", "min": 0.3, "max": 0.6}]))
        assert e.value.status_code == 400
        assert _Thread.seen == []
