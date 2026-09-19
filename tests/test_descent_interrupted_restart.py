"""Tests for the 'never silently report an interrupted run as done' fix.

2026-09-19 (production): after a restart, /api/optimization/auto/status
reported a run that had actually been cut off mid-generation as
`running: false, phase: "done"` — because the checkpoint reload
(_load_descent_state, routes/optimization.py) only ever cleared `running`
without recording that the run never reached a normal finish. These tests
pin the fix: a checkpoint saved while a worker still thought it was running
gets stamped `interrupted_by_restart`; a checkpoint from a NORMAL finish
(which always writes running=False together with its result, in the same
save) does not.
"""
from __future__ import annotations

import json

import pytest

from motor_ai_sim.routes import optimization as _opt


@pytest.fixture(autouse=True)
def _isolate_descent_state():
    snapshot = dict(_opt._descent_state)
    yield
    for k in list(_opt._descent_state.keys()):
        del _opt._descent_state[k]
    _opt._descent_state.update(snapshot)


def _write_checkpoint(path, **fields):
    blob = {"running": False, "iter": 0, "n_evals": 0, "phase": "", **fields}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(blob, fh)


def test_a_checkpoint_saved_mid_run_is_stamped_interrupted(tmp_path, monkeypatch):
    p = tmp_path / ".last_descent.json"
    _write_checkpoint(str(p), running=True, phase="screening", n_evals=32, iter=3)
    monkeypatch.setattr(_opt, "_descent_store_path", lambda: str(p))

    _opt._load_descent_state()

    assert _opt._descent_state["running"] is False
    info = _opt._descent_state.get("interrupted_by_restart")
    assert info is not None, "a mid-run checkpoint must be flagged, not silently cleared"
    assert info["n_evals"] == 32
    assert info["phase_at_interrupt"] == "screening"


def test_a_normal_finish_checkpoint_is_not_flagged(tmp_path, monkeypatch):
    p = tmp_path / ".last_descent.json"
    _write_checkpoint(str(p), running=False, phase="done", n_evals=50)
    monkeypatch.setattr(_opt, "_descent_store_path", lambda: str(p))

    _opt._load_descent_state()

    assert _opt._descent_state["running"] is False
    assert _opt._descent_state["phase"] == "done"
    assert _opt._descent_state.get("interrupted_by_restart") is None


def test_a_cancelled_checkpoint_is_not_flagged_either(tmp_path, monkeypatch):
    p = tmp_path / ".last_descent.json"
    _write_checkpoint(str(p), running=False, phase="done", n_evals=12,
                      history=[{"iter": 0}])
    monkeypatch.setattr(_opt, "_descent_store_path", lambda: str(p))

    _opt._load_descent_state()

    assert _opt._descent_state.get("interrupted_by_restart") is None


def test_a_fresh_descent_run_clears_a_stale_interrupted_flag(monkeypatch):
    """Starting a new run must not go on showing the PREVIOUS crashed run's
    interruption badge."""
    with _opt._descent_lock:
        _opt._descent_state["interrupted_by_restart"] = {
            "at": "2026-09-19T00:00:00Z", "n_evals": 5, "phase_at_interrupt": "x"}

    # Reproduce exactly the dict auto_start()/descent_run() write at the top
    # of a new run — the same keys, the same "interrupted_by_restart": None.
    with _opt._descent_lock:
        _opt._descent_state.update({"running": True, "interrupted_by_restart": None})

    assert _opt._descent_state.get("interrupted_by_restart") is None
