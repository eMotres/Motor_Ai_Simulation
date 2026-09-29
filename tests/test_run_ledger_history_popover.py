"""The EM ledger's History popover verbs (2026-09-22): list the last N rows,
load one back as though it had just been solved, delete one.

The EM transient predates `motor_ai_sim.run_history` and keeps its own store
(`.run_ledger/`, see `tests/test_run_ledger.py`) — these three endpoints
(`GET /ledger/recent`, `POST /ledger/{key}/load`, `DELETE /ledger/{key}`) are
the web History popover's other half: `historyNoticeFor`/the "Loaded from
history" notice already read `served_from_history`/`computed_at` off a
LEDGER HIT (`ledger_hit=True` path inside `get_fem_transient`); these verbs
answer the SAME store but by KEY, for a row that is not currently on screen.

Same fixtures as `test_run_ledger.py` (a stub solver, a tmp_path ledger, no
FEM) — what is pinned is routing and shape, not physics.
"""
from __future__ import annotations

import gzip
import json

import pytest


@pytest.fixture
def sim():
    from motor_ai_sim.routes import simulation as s
    return s


@pytest.fixture
def ledger_dir(tmp_path, sim, monkeypatch):
    d = tmp_path / ".run_ledger"
    monkeypatch.setattr(sim, "_ledger_dir", lambda: d)
    return d


@pytest.fixture
def solver(monkeypatch, sim, ledger_dir):
    sim.clear_simulation_caches(reason="ledger-history-popover test setup")
    calls = []

    def fake_eval(**kw):
        calls.append(kw)
        return {
            "time_s": [0.0, 0.5, 1.0],
            "T_avg_Nm": 1.234,
            "T_em_Nm": [1.2, 1.25, 1.23],
            "rpm": 1000.0,
            "f_elec_Hz": 100.0,
            "P_loss_total_W": [10.0, 10.0, 10.0],
            "n_frames_solved": 3,
        }

    monkeypatch.setattr("motor_ai_sim.simulation.fem_solver_2d.em_transient_eval",
                        fake_eval)
    monkeypatch.setattr(sim, "_bench_read", lambda *a, **k: {"stubbed": True})
    monkeypatch.setattr(sim, "_bench_compute", lambda *a, **k: None)
    monkeypatch.setattr(sim, "_append_run_journal", lambda *a, **k: None)
    yield calls
    sim.clear_simulation_caches(reason="ledger-history-popover test teardown")


RUN_A = dict(n_steps_per_period=4, n_periods=1.0, gamma_deg=0.0,
             I_phase_rms=10.0, daxis_deg=0.0, rpm=1000.0)
RUN_B = dict(n_steps_per_period=4, n_periods=1.0, gamma_deg=5.0,
             I_phase_rms=25.0, daxis_deg=0.0, rpm=1500.0)


def _one_key(sim, ledger_dir):
    """The stem `/ledger/{key}` takes — the filename minus `.json.gz`."""
    files = sorted(p.name for p in ledger_dir.glob("*.json.gz"))
    assert len(files) == 1
    return files[0][: -len(".json.gz")]


# ── list ─────────────────────────────────────────────────────────────────────

def test_recent_lists_newest_first_with_a_readable_summary(sim, solver, ledger_dir):
    sim.get_fem_transient(**RUN_A)
    sim.get_fem_transient(**RUN_B)

    out = sim.get_run_ledger_recent(limit=10)
    rows = out["entries"]
    assert len(rows) == 2
    # newest first — RUN_B was solved second
    assert rows[0]["summary"].startswith("25.0 A")
    assert "1500" in rows[0]["summary"]
    assert "5.0" in rows[0]["summary"]
    assert rows[1]["summary"].startswith("10.0 A")
    for r in rows:
        assert r["kind"] == "simulation.fem_transient"
        assert r["computed_at"]
        assert r["key"] and "/" not in r["key"]


def test_recent_respects_limit(sim, solver, ledger_dir):
    sim.get_fem_transient(**RUN_A)
    sim.get_fem_transient(**RUN_B)
    out = sim.get_run_ledger_recent(limit=1)
    assert len(out["entries"]) == 1


def test_recent_is_empty_before_any_run(sim, ledger_dir):
    assert sim.get_run_ledger_recent(limit=10) == {"entries": []}


# ── load ─────────────────────────────────────────────────────────────────────

def test_load_serves_the_row_labelled_exactly_like_a_ledger_hit(sim, solver, ledger_dir):
    first = sim.get_fem_transient(**RUN_A)
    key = _one_key(sim, ledger_dir)

    loaded = sim.load_run_ledger_entry(key)
    assert len(solver) == 1, "loading a History row must never solve"
    assert loaded["served_from_history"] is True
    assert loaded["computed_at"] == first["computed_at"]
    assert loaded["history_key"] == key
    assert loaded["ledger_hit"] is True
    assert loaded["restored"] is False
    assert loaded["stale"] is False
    assert "frames" not in loaded
    assert loaded["T_avg_Nm"] == first["T_avg_Nm"]


def test_load_flags_a_different_machine_never_silently(sim, solver, ledger_dir, monkeypatch):
    sim.get_fem_transient(**RUN_A)
    key = _one_key(sim, ledger_dir)
    # The live machine changed under the stored row.
    monkeypatch.setattr(sim, "_geometry_fingerprint", lambda *a, **k: "a-different-machine")
    loaded = sim.load_run_ledger_entry(key)
    assert loaded["stale_geometry"] is True


def test_load_an_unknown_key_is_a_404(sim, ledger_dir):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        sim.load_run_ledger_entry("nope-not-a-real-key")
    assert exc.value.status_code == 404


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "..", ""])
def test_load_refuses_a_path_shaped_key(sim, ledger_dir, bad):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        sim.load_run_ledger_entry(bad)
    assert exc.value.status_code in (400, 404)
    if bad:
        assert exc.value.status_code == 400


# ── delete ───────────────────────────────────────────────────────────────────

def test_delete_removes_the_one_row_and_nothing_else(sim, solver, ledger_dir):
    sim.get_fem_transient(**RUN_A)
    sim.get_fem_transient(**RUN_B)
    assert len(sim.get_run_ledger_recent(limit=10)["entries"]) == 2

    key = sorted(p.name for p in ledger_dir.glob("*.json.gz"))[0][: -len(".json.gz")]
    out = sim.delete_run_ledger_entry(key)
    assert out == {"deleted": True, "key": key}
    remaining = sim.get_run_ledger_recent(limit=10)["entries"]
    assert len(remaining) == 1
    assert remaining[0]["key"] != key


def test_delete_an_unknown_key_is_a_404(sim, ledger_dir):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        sim.delete_run_ledger_entry("nope-not-a-real-key")
    assert exc.value.status_code == 404


# ── gating (auth._GATED) ─────────────────────────────────────────────────────

def test_the_three_popover_verbs_are_gated_like_the_whole_ledger():
    """Same 'admin' bargain as GET/DELETE /api/simulation/ledger right next to
    them — the ledger is the SHARED machine's store, not per-workspace."""
    from motor_ai_sim import auth

    assert auth.required_tier("GET", "/api/simulation/ledger/recent") == "admin"
    assert auth.required_tier(
        "POST", "/api/simulation/ledger/abc123/load") == "admin"
    assert auth.required_tier("DELETE", "/api/simulation/ledger/abc123") == "admin"
    # Unaffected: the exact-path table still wins for the whole-ledger verbs.
    assert auth.required_tier("GET", "/api/simulation/ledger") == "admin"
    assert auth.required_tier("DELETE", "/api/simulation/ledger") == "admin"
