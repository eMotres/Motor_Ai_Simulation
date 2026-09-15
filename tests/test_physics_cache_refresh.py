"""A Run always solves, and it REPLACES the physics caches.

Two failures, one root cause — a cached entry outliving the run it belongs to:

1. Pressing Run returned the PREVIOUS run's object.  Same torque, same ripple,
   same `computed_at`, no solver call.  For an engineer a Run that solves
   nothing is not a cache hit, it is a lie about what happened (user,
   2026-09-04: "их нужно очищать при каждом расчёте и обновлять").
2. Old field pictures and old field snapshots kept sitting BESIDE the fresh
   run, where the relaxed snapshot lookup could still serve them.

No FEM here: `em_transient_eval` is replaced by a counting stub, so what is
pinned is the ROUTE's cache behaviour, not the solver's physics.
"""
from __future__ import annotations

import pytest


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sim():
    from motor_ai_sim.routes import simulation as s
    return s


@pytest.fixture
def clean(sim):
    """Start and finish with empty stores — this module must not leak cache
    state into (or out of) the rest of the suite."""
    sim.clear_simulation_caches(reason="test setup")
    yield
    sim.clear_simulation_caches(reason="test teardown")


@pytest.fixture
def solver(monkeypatch, sim, clean):
    """A counting stub in place of the sliding-band solve.

    Returns the handful of keys the route's post-processing touches; the
    summary build is allowed to fail (the route records `summary_error` and
    carries on), because the summary is not what these tests are about.
    """
    calls = []

    def fake_eval(**kw):
        calls.append(kw)
        out = {
            "time_s": [0.0, 0.5, 1.0],
            "T_avg_Nm": 1.234,
            "T_em_Nm": [1.2, 1.25, 1.23],
            "rpm": 1000.0,
            "f_elec_Hz": 100.0,
            "P_loss_total_W": [10.0, 10.0, 10.0],
            "n_frames_solved": 3,
        }
        if kw.get("return_field"):
            # Shape-only: the store copies it verbatim and nothing here reads it.
            out["field"] = {"mesh": {"p": [], "t": []}, "A": [], "B": []}
        return out

    monkeypatch.setattr("motor_ai_sim.simulation.fem_solver_2d.em_transient_eval",
                        fake_eval)
    # The bench Ld/Lq probe rides on every live-machine run and costs three real
    # solves on a miss — not this test's business.
    monkeypatch.setattr(sim, "_bench_read", lambda *a, **k: {"stubbed": True})
    monkeypatch.setattr(sim, "_bench_compute", lambda *a, **k: None)
    # Persisting is a separate concern (and a disk write); the refresh under
    # test sits NEXT to it, not inside it.
    monkeypatch.setattr(sim, "_save_last_transient", lambda *a, **k: None)
    # …and so is the RESULTS LEDGER (2026-09-05), which sits next to it too and
    # is the one thing allowed to answer a repeated Run without solving — but
    # only on an exact key match, labelled, with a one-click Recompute.  That
    # trade is pinned in tests/test_run_ledger.py; here it is switched off so
    # these tests keep saying what they were written to say: with nothing
    # stored, a Run reaches the solver and REPLACES the in-memory caches.
    monkeypatch.setattr(sim, "_ledger_write", lambda *a, **k: None)
    monkeypatch.setattr(sim, "_ledger_lookup", lambda *a, **k: None)
    return calls


# The minimum a run needs to be reproducible here: a pinned d-axis (an unpinned
# one would be MEASURED, i.e. a real 24-frame solve) and a small frame count.
RUN = dict(n_steps_per_period=4, n_periods=1.0, gamma_deg=0.0,
           I_phase_rms=10.0, daxis_deg=0.0)


# ── 1. a Run always reaches the solver ──────────────────────────────────────

def test_two_identical_runs_both_solve(sim, solver):
    """THE headline: press Run twice with identical inputs, get two solves."""
    a = sim.get_fem_transient(**RUN)
    b = sim.get_fem_transient(**RUN)
    assert len(solver) == 2, ("the second Run was served from "
                              "_fem_transient_cache instead of solving")
    assert a["computed_at"] and b["computed_at"]
    assert a is not b


def test_the_internal_memo_still_works_when_opted_in(sim, solver):
    """The bus-coupling / charge-max outer loops re-enter this route dozens of
    times at the SAME point inside one request; they opt in explicitly."""
    sim.get_fem_transient(**RUN)
    assert len(solver) == 1
    tok = sim._TRANSIENT_MEMO.set(True)
    try:
        sim.get_fem_transient(**RUN)
    finally:
        sim._TRANSIENT_MEMO.reset(tok)
    assert len(solver) == 1, "an opted-in internal re-entry re-solved"


# ── 2. a finished run REPLACES the stores ───────────────────────────────────

def test_a_run_refreshes_the_caches(sim, solver):
    """Field pictures from before the run are gone; the snapshot store holds
    exactly this run."""
    sim._fem_field_cache[("stale", "picture")] = {"whatever": 1}
    sim._transient_field_snap[("stale", "snapshot")] = {
        "field": {}, "scalars": {}, "meta": {"computed_at": "2020-01-01T00:00:00"}}

    res = sim.get_fem_transient(field_snapshot=True, **RUN)

    assert len(sim._fem_field_cache) == 0, "an old field picture survived a Run"
    assert len(sim._transient_field_snap) == 1, (
        "the snapshot store kept an older run beside the fresh one")
    assert ("stale", "snapshot") not in sim._transient_field_snap
    only = next(iter(sim._transient_field_snap.values()))
    assert only["meta"]["computed_at"] == res["computed_at"]
    assert len(sim._fem_transient_cache) == 1
    assert sim._cache_state["last_refreshed_by"] == res["computed_at"]


def test_a_run_without_a_snapshot_does_not_blank_the_store(sim, solver):
    """The animation viewer's request asks for no snapshot; it must not wipe the
    one the charts' run just parked (the two fire together on tab mount)."""
    sim.get_fem_transient(field_snapshot=True, **RUN)
    kept = dict(sim._transient_field_snap)
    assert len(kept) == 1

    sim.get_fem_transient(field_snapshot=False, **RUN)
    assert list(sim._transient_field_snap) == list(kept), (
        "a snapshot-less Run emptied the field-snapshot store")


# ── 3. an explicit clear empties everything ─────────────────────────────────

def test_clear_simulation_caches_empties_all_three(sim, solver):
    sim.get_fem_transient(field_snapshot=True, **RUN)
    assert sim._fem_transient_cache and sim._transient_field_snap
    sim._fem_field_cache[("a",)] = {"b": 1}

    sim.clear_simulation_caches(reason="unit test")

    assert len(sim._fem_field_cache) == 0
    assert len(sim._fem_transient_cache) == 0
    assert len(sim._transient_field_snap) == 0
    assert sim._cache_state["last_cleared_reason"] == "unit test"


# ── 4. the endpoints report what is there ───────────────────────────────────

def test_cache_endpoints_report_and_clear(sim, solver):
    sim.get_fem_transient(field_snapshot=True, **RUN)

    stats = sim.get_cache_stats()
    assert stats["field_cache"] == 0
    assert stats["snapshots"] == 1
    assert stats["transient_cache"] == 1
    assert stats["snapshot_computed_at"] == [stats["last_refreshed_by"]]
    # Reported when the optimizer module is importable, null when it is not —
    # never a 500 and never a fabricated zero.
    assert stats["optimization_eval_cache"] is None or \
        isinstance(stats["optimization_eval_cache"], int)

    after = sim.clear_cache_endpoint()
    assert (after["field_cache"], after["snapshots"],
            after["transient_cache"]) == (0, 0, 0)
    assert after["last_cleared_reason"]
    assert after["last_refreshed_by"] is None


def test_the_clear_endpoint_never_touches_the_disk_stores(sim, solver, monkeypatch):
    """Memory only.  `.last_transient.json` / `.scan_cache.jsonl` /
    `.daxis_cache.json` are records of work done — hours of solving."""
    import pathlib
    sim._cache_stats()      # warm the lazy optimizer import (it reads its own
                            # disk cache once) so the watch below sees only the
                            # clear's own file access
    opened = []
    _real_open = pathlib.Path.open

    def _watch(self, *a, **k):
        if a and "r" not in str(a[0]):
            opened.append(str(self))
        return _real_open(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "open", _watch)
    monkeypatch.setattr("builtins.open",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError(f"clear wrote/read a file: {a[:1]}")))
    sim.clear_cache_endpoint()
    assert opened == []


# ── 5. restore never solves ─────────────────────────────────────────────────

def test_restore_never_calls_the_solver(sim, solver, monkeypatch):
    """The page-open path returns the last saved run (stale-flagged) or says
    nothing has been computed.  It must never spend a solve."""
    monkeypatch.setitem(sim._last_transient_ref, "key", ("some", "other", "key"))
    monkeypatch.setitem(sim._last_transient_ref, "result",
                        {"time_s": [0.0], "T_avg_Nm": 1.0,
                         "geo_fingerprint": "machine_A_fp"})

    out = sim.get_fem_transient(restore=True, **RUN)

    assert out["restored"] is True
    assert len(solver) == 0, "restore=true reached the solver"


def test_restore_with_nothing_saved_still_never_solves(sim, solver, monkeypatch):
    monkeypatch.setitem(sim._last_transient_ref, "key", None)
    monkeypatch.setitem(sim._last_transient_ref, "result", None)

    out = sim.get_fem_transient(restore=True, **RUN)

    assert out == {"restored": False, "stale": False}
    assert len(solver) == 0
