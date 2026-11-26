"""Stop means stop: a cancelled descent / auto run starts no further final-quality solve (measured 2026-11-25: a cancelled run sat 5+
minutes in ``final_validation``, six paid 6x solves for a result nobody reads, holding the worker), and its best stays as the
PRELIMINARY one (not apply-eligible, no error)."""
from __future__ import annotations

import pytest

from motor_ai_sim.routes import optimization as O


@pytest.fixture()
def state():
    keep = {k: O._descent_state.get(k) for k in ("cancel", "final_validation_status", "apply_eligible", "error", "best", "result")}
    yield O._descent_state
    for k, v in keep.items():
        O._descent_state[k] = v


def _args(evaluate):
    return dict(points=[], best_x={"tooth_width": 3.0}, best_metrics={"current_a": 10.0}, coarse_base={"current_a": 10.0},
                base_x={"tooth_width": 3.0}, base_current=10.0, bump_pct=10.0, evaluate=evaluate, score=lambda m, b: (0.0, 0.0))


def test_a_cancelled_run_starts_no_final_solve(state):
    calls = []
    state["cancel"] = True
    out = O._finalize_standard_shortlist(**_args(lambda d, c: calls.append((d, c))))
    assert calls == [] and out["status"] == "failed" and out["cancelled"] is True and out["reason"] == "run was cancelled"


def test_a_cancel_after_baseline_a_starts_no_parallel_wave(state):
    calls = []

    def evaluate(d, c):
        calls.append((d, c))
        O._descent_state["cancel"] = True                                      # the user presses Stop while baseline A is solving
        return {"ok": True, "res": {"current_a": c}, "overrides": d}
    state["cancel"] = False
    out = O._finalize_standard_shortlist(**_args(evaluate))
    assert len(calls) <= 1                                                     # baseline A at most; no B, no finalists
    assert out["status"] == "failed" and (out.get("cancelled") is True or "baseline A" in out["reason"])


def test_a_cancelled_run_keeps_its_best_as_preliminary(state):
    best = {"x": {"tooth_width": 3.0}, "metrics": {"current_a": 10.0}}
    state["cancel"] = True
    result = {"best": best}
    ok = O._publish_standard_final(result, {"status": "failed", "reason": "run was cancelled", "cancelled": True}, [])
    assert ok is False
    assert state["final_validation_status"] == "preliminary" and state["apply_eligible"] is False and state["error"] is None
    assert result["screening_best"] == best and state["result"]["best"] == best          # the best is kept, not wiped
    assert result["final_validation"]["status"] == "failed" and result["final_validation"]["reason"] == "run was cancelled"


def test_a_failed_validation_still_wipes_the_best(state):
    state["cancel"] = False
    result = {"best": {"x": {"a": 1.0}}}
    O._publish_standard_final(result, {"status": "failed", "reason": "no shortlisted design passed standard FEM"}, [])
    assert state["final_validation_status"] == "failed" and result["best"] is None and "final 6x FEM validation failed" in state["error"]
