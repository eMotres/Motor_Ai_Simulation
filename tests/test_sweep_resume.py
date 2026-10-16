"""Tests for automatic sweep resumption at API startup (sweep_resume.py).

Companion to test_sweep_journal.py, which only covers the on-disk journal in
isolation. These pin the wiring between sweep_resume._enqueue_resume_sweep and
routes.optimization's shared ``_scan_state`` / ``_scan_worker`` — the seam
where the original Haiku-4.5 patch left the resumed worker unable to ever do
anything (it bailed on its own ownership check because ``_scan_state`` was
never told the resume's run_id owned it), and the layered-mode gate added on
review.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from motor_ai_sim import sweep_journal as _journal
from motor_ai_sim import sweep_resume as _resume
from motor_ai_sim import workspace as _WSP
from motor_ai_sim.routes import optimization as _opt


def _ws_dir() -> str:
    return str(_WSP.root())


def _write_journal(sweep_id="resume-test", machine_fp="fp-match",
                    state="running", total_points=4, done_points=None,
                    started_at=None, request_body=None):
    record = _journal.SweepJournalRecord(
        sweep_id=sweep_id,
        started_at=started_at or (datetime.utcnow().isoformat() + "Z"),
        owner="test",
        machine_fp=machine_fp,
        request_body=request_body if request_body is not None else {
            "variables": [],
            "operating_points": [{"current_a": 10.0, "gamma_deg": 0.0, "rpm": 1000.0}],
            "steps_per_period": 6, "ripple_max_pct": 100.0, "max_geometries": 1,
            "coil_temp_c": 60.0, "seed": 1,
        },
        total_points=total_points,
        done_points=[] if done_points is None else list(done_points),
        state=state,
    )
    _journal.save_sweep_journal(_ws_dir(), record)
    return record


@pytest.fixture(autouse=True)
def _isolate_shared_state(monkeypatch):
    """The scan campaign state and the resume memo are process-wide singletons
    (same shape the real app runs with, in single-user mode) — snapshot and
    restore both around every test so this file cannot leak into another."""
    snapshot = dict(_opt._scan_state)
    resumed_snapshot = set(_resume._RESUMED_THIS_PROCESS)
    journal_path = _journal._journal_path(_ws_dir())
    import os
    had_journal = os.path.exists(journal_path)
    journal_bytes = open(journal_path, "rb").read() if had_journal else None
    yield
    for k in list(_opt._scan_state.keys()):
        del _opt._scan_state[k]
    _opt._scan_state.update(snapshot)
    _resume._RESUMED_THIS_PROCESS.clear()
    _resume._RESUMED_THIS_PROCESS.update(resumed_snapshot)
    if had_journal:
        with open(journal_path, "wb") as fh:
            fh.write(journal_bytes)
    elif os.path.exists(journal_path):
        os.remove(journal_path)


def test_layered_mode_is_a_noop(monkeypatch):
    """WORKSPACES_ROOT set -> resume never even looks at a workspace, and the
    on-disk journal is left exactly as it was (see sweep_resume module
    docstring for why this is gated off rather than attempted)."""
    _write_journal()
    monkeypatch.setenv("WORKSPACES_ROOT", str(_ws_dir()))
    called = []
    monkeypatch.setattr(_resume, "_resume_sweep_in_workspace",
                        lambda d: called.append(d))

    _resume.resume_incomplete_sweeps()

    assert called == []
    loaded = _journal.load_sweep_journal(_ws_dir())
    assert loaded.state == "running"


def test_resume_establishes_scan_state_ownership_before_starting_worker(monkeypatch):
    """The bug this file exists to catch: _scan_worker refuses to do anything
    unless _scan_state already names its run_id as the current one. The fix
    sets that ownership SYNCHRONOUSLY, before the worker thread starts, so it
    must be true immediately after resume_incomplete_sweeps() returns — no
    waiting for a background thread required."""
    _write_journal(sweep_id="resume-owns", done_points=[0, 1], total_points=4)
    monkeypatch.setattr(_opt, "_config_fingerprint", lambda *a, **kw: "fp-match")

    captured = {}

    def _fake_scan_worker(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        captured["owns_at_start"] = _opt._scan_owns(args[7])

    monkeypatch.setattr(_opt, "_scan_worker", _fake_scan_worker)

    _resume.resume_incomplete_sweeps()

    # Ownership + progress fields set BEFORE the worker thread was started.
    assert _opt._scan_state["running"] is True
    assert _opt._scan_state["run_id"] == "resume-owns"
    assert _opt._scan_state["done"] == 2
    assert _opt._scan_state["total"] == 4
    resume_info = _opt._scan_state.get("resumed_from_restart")
    assert resume_info and resume_info["done_before"] == 2 and resume_info["total"] == 4

    for _ in range(100):
        if "args" in captured:
            break
        time.sleep(0.02)
    assert captured, "the resumed worker thread never ran _scan_worker"
    assert captured["owns_at_start"] is True
    # every solver knob the journal carried is threaded through, not silently
    # reset to _scan_worker's own defaults.
    assert captured["kwargs"]["hi_fidelity"] is False
    assert captured["kwargs"]["element_order"] == 2


def test_finished_journal_is_left_alone_not_marked_stale(monkeypatch):
    """A journal that already recorded its own outcome must not be relabeled
    'stale' just because it is not resumable."""
    _write_journal(sweep_id="already-done", state="finished")
    monkeypatch.setattr(_opt, "_config_fingerprint", lambda *a, **kw: "fp-match")

    _resume.resume_incomplete_sweeps()

    loaded = _journal.load_sweep_journal(_ws_dir())
    assert loaded.state == "finished"


def test_disqualified_running_journal_is_marked_stale(monkeypatch):
    """A genuinely-interrupted sweep that fails the fingerprint/age check DOES
    get marked stale, so it is not retried forever."""
    _write_journal(sweep_id="mismatched", state="running", machine_fp="old-fp")
    monkeypatch.setattr(_opt, "_config_fingerprint", lambda *a, **kw: "new-fp")

    _resume.resume_incomplete_sweeps()

    loaded = _journal.load_sweep_journal(_ws_dir())
    assert loaded.state == "stale"


def test_concurrent_resume_guard_skips_if_a_scan_is_already_running(monkeypatch):
    """If _scan_state already says a scan is running (this process resumed
    already, or a user's own Run raced startup), resume must not clobber it."""
    _write_journal(sweep_id="should-not-start", done_points=[0], total_points=4)
    monkeypatch.setattr(_opt, "_config_fingerprint", lambda *a, **kw: "fp-match")
    with _opt._scan_lock:
        _opt._scan_state.update({"running": True, "run_id": "someone-elses-run"})

    _resume.resume_incomplete_sweeps()

    assert _opt._scan_state["run_id"] == "someone-elses-run"
    loaded = _journal.load_sweep_journal(_ws_dir())
    assert loaded.resumed_from_restart is None


def test_resume_is_idempotent_within_one_process(monkeypatch):
    """resume_incomplete_sweeps() is documented as safe to call twice; a
    second call must not re-enqueue a sweep this process already resumed."""
    _write_journal(sweep_id="once-only", done_points=[0], total_points=3)
    monkeypatch.setattr(_opt, "_config_fingerprint", lambda *a, **kw: "fp-match")
    calls = []
    monkeypatch.setattr(_opt, "_scan_worker",
                        lambda *a, **kw: calls.append(a))

    _resume.resume_incomplete_sweeps()
    for _ in range(100):
        if calls:
            break
        time.sleep(0.02)
    assert len(calls) == 1

    _resume.resume_incomplete_sweeps()
    time.sleep(0.1)
    assert len(calls) == 1, "a second resume call re-enqueued the same sweep"
