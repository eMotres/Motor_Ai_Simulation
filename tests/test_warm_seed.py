"""Each sweep point continues the previous one — the seed, and its honesty.

User, 2026-09-06: "мы же уже договаривались, что проход демагнитизации делается
для каждого sweep только один раз; изменения геометрии небольшие, и каждый
следующий расчёт берётся из предыдущего."  Sweep points had gone from 700-800 s
to 1100-1700 s because every subprocess eval started COLD: the eddy warm-up
march from zero, plus — since the 2026-09-05 reproducibility fix — a whole extra
electrical period of demag PRE-PASS in front of the reported window.

The fix has three parts and this file guards all three:

  * the warm cache carries the Br RATCHET state (per-magnet-element de-rating
    factor + element centroids + the magnet domain tag that lets another mesh
    pick it up), published by EVERY coupled-eddy demag run — the interactive
    Simulation included, so one Run at the base point is the "once" of a sweep;
  * ``SB_SEED_FROM_PREVIOUS=1``, set in ``routes/optimization._EVAL_ENV`` and
    nowhere else, lets an eval CONTINUE that state: the eddy seed is accepted at
    any current and γ (the settle test still judges the handoff) and, when a Br
    map came with it, the dedicated pre-pass period is skipped;
  * the coordinator solves the first queued point ALONE when nothing usable
    exists yet and the queue is longer than the worker pool.

The INTERACTIVE path is unchanged by construction — it never sets the flag — and
``test_interactive_still_runs_the_full_prepass`` is what says so out loud.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict

import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation import fem_solver_2d as F
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_physics_regression import (CASES, CONNECTION, GEO_30MM,
                                           OVERRIDE, RPM)

CASE = dict(CASES["p2_demag_eddy"])

# What "the seeded eval is the same machine" means, measured on the sandbox
# 30 mm 12s14p at 60 A / 12 steps (see the module docstring of the fixture).
RTOL_T = 5e-3          # 0.5 % on T_avg
ATOL_BR_PP = 0.5       # 0.5 percentage points on Br kept


def _clear_warm_cache() -> None:
    """Make the next run a COLD one (the sandbox path follows the config)."""
    F._SB_WARM_CACHE.clear()
    try:
        p = F._warm_cache_path()
        if p.exists():
            p.unlink()
    except Exception:      # noqa: BLE001 — a missing file is the goal
        pass


# ── (a) the Br map round-trips through the npz ──────────────────────────────
def test_br_map_round_trips_through_the_npz():
    """A seed that loses the magnet on the way to disk is not a seed.

    The mirror is what an eval SUBPROCESS reads (its in-memory slot is empty),
    so the disk format — not the dict — is what has to carry the ratchet.
    """
    _clear_warm_cache()
    rng = np.random.default_rng(7)
    n_dof, n_mag = 40, 11
    wc = {
        "doflocs": rng.random((n_dof, 2)).astype(np.float32),
        "A": rng.random(n_dof), "Ued": rng.random(3),
        "solid": rng.random(12),
        # nspp 54 SOLVED against 36 REQUESTED — the snap the solver applies to
        # land the rotor on whole slip nodes.
        "meta": {"nspp": 54, "npd": 1.0, "conn": "np2", "temp": 120.0,
                 "mscale": 1.0},
        "nspp_req": 36,
        "I": 60.0, "rpm": 15000.0, "gam": 0.0, "geo_fp": "abc123",
        "br_val": np.clip(rng.random(n_mag), 0.0, 1.0),
        "br_cen": rng.random((n_mag, 2)) * 1e-2,
        "br_tag": np.array([300 + (i % 3) for i in range(n_mag)], int),
    }
    try:
        F._warm_cache_store(wc)
        F._SB_WARM_CACHE.clear()          # force the DISK path, not the slot
        back = F._warm_cache_load()
        assert back is not None, "the disk mirror did not come back at all"
        assert back["geo_fp"] == "abc123"
        assert np.allclose(back["br_val"], wc["br_val"])
        assert np.allclose(back["br_cen"], wc["br_cen"])
        assert np.array_equal(back["br_tag"], wc["br_tag"])
        # …and the light identity read the coordinator uses agrees with it.
        meta = F._warm_cache_meta()
        assert meta is not None and meta["has_br"] is True
        assert meta["meta"] == wc["meta"] and meta["geo_fp"] == "abc123"
        assert meta["nspp_req"] == 36, (
            "the REQUESTED steps/period did not survive; the coordinator can "
            "only compare against that one — the solved count is snapped up to "
            "a divisor of the slip ring and it does not know the ring")
        # The coordinator's question, asked the way it asks it: the request is
        # 36 steps even though the parent SOLVED 54.
        from motor_ai_sim.routes.optimization import (_seed_usable_for,
                                                      _warm_seed_memo)
        _warm_seed_memo.clear()
        ok, why = _seed_usable_for(36, 120.0, 15000.0, 1.0, need_br=True)
        assert ok is True, why
        _warm_seed_memo.clear()
        ok, why = _seed_usable_for(48, 120.0, 15000.0, 1.0, need_br=True)
        assert ok is False and "steps/period" in why
        _warm_seed_memo.clear()
        ok, why = _seed_usable_for(36, 120.0, 20000.0, 1.0, need_br=True)
        assert ok is False and "rpm" in why
    finally:
        _clear_warm_cache()
        from motor_ai_sim.routes.optimization import _warm_seed_memo as _m
        _m.clear()


def test_a_cache_without_a_br_map_still_loads():
    """Backward compatibility: a mirror written before 2026-09-06 (or by a run
    with demag off) carries no Br keys, and the reader must not care."""
    _clear_warm_cache()
    rng = np.random.default_rng(3)
    wc = {"doflocs": rng.random((8, 2)).astype(np.float32), "A": rng.random(8),
          "Ued": rng.random(2), "solid": rng.random(12),
          "meta": {"nspp": 12, "npd": 1.0, "conn": "np2", "temp": 120.0,
                   "mscale": 1.0},
          "I": 60.0, "rpm": 15000.0, "gam": 0.0,
          "br_val": None, "br_cen": None, "br_tag": None}
    try:
        F._warm_cache_store(wc)
        F._SB_WARM_CACHE.clear()
        back = F._warm_cache_load()
        assert back is not None and back.get("br_val") is None
        assert F._warm_cache_meta()["has_br"] is False
    finally:
        _clear_warm_cache()


def test_the_br_map_projects_onto_a_different_mesh_per_magnet():
    """Small geometry steps mean a DIFFERENT mesh, so the map is picked up by
    nearest centroid — but only ever within the same magnet domain, or a magnet
    would inherit its neighbour's de-rating across the gap between them."""
    wc = {"br_val": np.array([0.5, 0.6, 0.9, 0.95]),
          "br_cen": np.array([[0.0, 0.0], [1.0, 0.0],      # tag 300
                              [10.0, 0.0], [11.0, 0.0]]),  # tag 301
          "br_tag": np.array([300, 300, 301, 301], int)}
    # New mesh: the magnets moved a little and each got one extra element.
    # 0.55 sits nearer the seed's 1.0 element than its 0.0 one, so it inherits
    # 0.6 — nearest CENTROID, not nearest index.
    cen = np.array([[0.05, 0.0], [0.55, 0.0], [1.05, 0.0],
                    [10.05, 0.0], [11.05, 0.0]])
    tag = np.array([300, 300, 300, 301, 301], int)
    out, n_hit = F._project_br(wc, cen, tag)
    assert n_hit == 5
    assert out.tolist() == [0.5, 0.6, 0.6, 0.9, 0.95]
    # A tag the seed never saw keeps its pristine 1.0 rather than borrowing.
    out2, n2 = F._project_br(wc, np.array([[0.0, 0.0], [0.5, 0.0]]),
                             np.array([300, 999], int))
    assert n2 == 1 and out2.tolist() == [0.5, 1.0]


# ── (d) the coordinator's serial-first rule ─────────────────────────────────
def test_serial_first_decision_covers_the_four_cases():
    from motor_ai_sim.routes.optimization import serial_first_decision as D

    # no seed, more tasks than workers -> pay the warm-up + pre-pass ONCE
    ok, why = D(40, 10, demag=True, rotor_eddy=True, seed_ok=False)
    assert ok is True and "no usable seed" in why
    # no seed, but the whole queue fits one wave -> a solo point only adds time
    ok, why = D(8, 10, demag=True, rotor_eddy=True, seed_ok=False)
    assert ok is False and "one parallel wave" in why
    ok, _ = D(10, 10, demag=True, rotor_eddy=True, seed_ok=False)
    assert ok is False, "tasks == workers is still one wave"
    # a usable seed exists -> nothing to amortise, fan out
    ok, why = D(40, 10, demag=True, rotor_eddy=True, seed_ok=True)
    assert ok is False and "already exists" in why
    ok, _ = D(8, 10, demag=True, rotor_eddy=True, seed_ok=True)
    assert ok is False
    # …and a run with no coupled-eddy demag has no one-off to pay for at all
    for dm, re_ in ((False, True), (True, False), (False, False)):
        ok, why = D(40, 10, demag=dm, rotor_eddy=re_, seed_ok=False)
        assert ok is False and "no coupled-eddy demag" in why


def test_the_optimizer_env_sets_the_flag_and_the_process_does_not():
    """The flag is a SWEEP flag.  If it ever leaked into os.environ the
    interactive Simulation run would silently start continuing other people's
    magnets, and the 2026-09-05 reproducibility fix would be gone."""
    from motor_ai_sim.routes.optimization import _EVAL_ENV
    assert _EVAL_ENV.get("SB_SEED_FROM_PREVIOUS") == "1"
    assert os.environ.get("SB_SEED_FROM_PREVIOUS") is None
    assert F._seed_from_previous() is False


def test_the_seed_gate_relaxes_current_and_gamma_only_under_the_flag():
    meta = {"nspp": 12, "npd": 1.0, "conn": "np2", "temp": 120.0, "mscale": 1.0}
    wc = {"meta": dict(meta), "I": 60.0, "rpm": 15000.0, "gam": 0.0}
    # Interactive: a far-away current or γ is refused up front, as before.
    assert F._warm_seed_accept(wc, meta, 200.0, 15000.0, 0.0)[0] is False
    assert F._warm_seed_accept(wc, meta, 60.0, 15000.0, 30.0)[0] is False
    assert F._warm_seed_accept(wc, meta, 60.0, 15000.0, 0.0)[0] is True
    os.environ["SB_SEED_FROM_PREVIOUS"] = "1"
    try:
        # Sweep mode: any current, any γ — the settle test guards the handoff.
        assert F._warm_seed_accept(wc, meta, 200.0, 15000.0, 30.0)[0] is True
        # …but NEVER a different schedule/machine or a different speed.
        assert F._warm_seed_accept(wc, meta, 60.0, 20000.0, 0.0)[0] is False
        bad = dict(meta, nspp=36)
        assert F._warm_seed_accept(wc, bad, 60.0, 15000.0, 0.0)[0] is False
        assert F._warm_seed_accept(None, meta, 60.0, 15000.0, 0.0)[0] is False
    finally:
        os.environ.pop("SB_SEED_FROM_PREVIOUS", None)


def test_the_seed_gate_refuses_a_state_from_another_symmetry():
    """2026-09-07: the Thermal tab's FULL-RING loss solve published its state and
    the next 1/2-sector sweep priced every point as seeded, then refused the
    seed in the subprocess and died at the seeded hang cap (10 of 10 timeout).
    A state carries the sector count it was solved on; a run on another
    symmetry must refuse it, and a mirror without the field (0) is still
    accepted, as before."""
    meta = {"nspp": 12, "npd": 1.0, "conn": "np2", "temp": 120.0, "mscale": 1.0}
    wc = {"meta": dict(meta), "I": 60.0, "rpm": 15000.0, "gam": 0.0, "nsect": 1}
    assert F._warm_seed_accept(wc, meta, 60.0, 15000.0, 0.0, n_sectors=2)[0] is False
    assert F._warm_seed_accept(wc, meta, 60.0, 15000.0, 0.0, n_sectors=1)[0] is True
    assert F._warm_seed_accept(wc, meta, 60.0, 15000.0, 0.0, n_sectors=-1)[0] is True
    assert F._warm_seed_accept(wc, meta, 60.0, 15000.0, 0.0)[0] is True   # caller did not say
    old = dict(wc, nsect=0)                                                 # pre-2026-09-07 mirror
    assert F._warm_seed_accept(old, meta, 60.0, 15000.0, 0.0, n_sectors=2)[0] is True
    # the coordinator's cheap gate agrees with the solver's
    from motor_ai_sim.routes import optimization as O
    O._warm_seed_state = lambda: {"meta": dict(meta), "I": 60.0, "rpm": 15000.0, "gam": 0.0,
                                  "geo_fp": "", "nspp_req": 12, "nsect": 1, "has_br": True}
    try:
        assert O._seed_usable_for(12, 120.0, 15000.0, 1.0, n_sectors=2)[0] is False
        assert O._seed_usable_for(12, 120.0, 15000.0, 1.0, n_sectors=1)[0] is True
    finally:
        import importlib; importlib.reload(O)


def test_the_warm_cache_gate_is_per_call_as_well_as_per_process():
    """The Thermal route disables the seed for ITS solve without touching the
    process-wide env var another request's transient may be relying on."""
    assert F._warm_cache_disabled() is False
    tok = F._NO_WARM_CACHE_CTX.set(True)
    try:
        assert F._warm_cache_disabled() is True
    finally:
        F._NO_WARM_CACHE_CTX.reset(tok)
    assert F._warm_cache_disabled() is False


# ── (b)+(c) the real solves ─────────────────────────────────────────────────
pytestmark_slow = pytest.mark.slow


def _run() -> Dict[str, Any]:
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                          connection=CONNECTION, **CASE)
    finally:
        set_request_materials(None)


@pytest.fixture(scope="module")
def legs() -> Dict[str, Dict[str, Any]]:
    """Three real transients of the pinned eddy+demag case, in order.

    cold      — nothing cached: full warm-up march + full demag pre-pass.
                This is the run that PUBLISHES the state (the "once").
    seeded    — SB_SEED_FROM_PREVIOUS=1: continues the cold run's eddy field
                AND its Br map, so the pre-pass period is skipped.
    flag_off  — the interactive path with a cache sitting right there: it may
                seed the eddy history (it always could) but never the magnet,
                so it still runs the whole pre-pass.
    """
    out: Dict[str, Dict[str, Any]] = {}
    _clear_warm_cache()
    t0 = time.monotonic(); out["cold"] = _run()
    out["cold"]["_secs"] = time.monotonic() - t0
    # Snapshot what the cold run PUBLISHED before anything else touches the
    # slot — the assertions below run after all three legs, by which time the
    # cache holds the last leg's state (and then none at all).
    _pub = F._SB_WARM_CACHE.get("last")
    assert _pub is not None, (
        "the cold run published no warm-cache state, so nothing below is a "
        "seeded run")
    out["_published"] = {"br_val": (None if _pub.get("br_val") is None
                                    else np.asarray(_pub["br_val"]).copy()),
                         "geo_fp": _pub.get("geo_fp")}
    os.environ["SB_SEED_FROM_PREVIOUS"] = "1"
    try:
        t0 = time.monotonic(); out["seeded"] = _run()
        out["seeded"]["_secs"] = time.monotonic() - t0
    finally:
        os.environ.pop("SB_SEED_FROM_PREVIOUS", None)
    t0 = time.monotonic(); out["flag_off"] = _run()
    out["flag_off"]["_secs"] = time.monotonic() - t0
    _clear_warm_cache()
    print("\n--- warm seed, measured on this machine ---")
    for _n in ("cold", "seeded", "flag_off"):
        _d = out[_n]
        print("  %-9s T_avg %.6g Nm | Br kept %.3f %% | discarded frames %d "
              "(pre-pass %d) | %.1f s"
              % (_n, _t(_d), _br(_d), int(_d["eddy_warmup_frames"]),
                 int(_d["demag_prepass_frames"]), _d["_secs"]))
    return out


def _t(d):
    return float(d["T_avg_Nm"])


def _br(d):
    return float((d.get("demag_summary") or {})["br_kept_vol_pct"])


@pytestmark_slow
def test_the_cold_run_publishes_a_br_map(legs):
    """Without a published magnet there is nothing for a sweep to continue —
    and the publish must happen on the INTERACTIVE path too (this leg is one)."""
    assert legs["cold"]["demag_seeded"] is False
    wc = legs["_published"]
    assert wc.get("br_val") is not None, "no Br map beside the eddy state"
    assert wc.get("geo_fp"), "the published state does not say which design"
    assert np.min(wc["br_val"]) < 0.999, (
        "the published magnet is pristine — this case ratchets nothing and "
        "guards nothing")


@pytestmark_slow
def test_the_seeded_eval_is_the_same_machine_for_fewer_frames(legs):
    """The point of the whole change: same answer, less work."""
    cold, seed = legs["cold"], legs["seeded"]
    assert seed["demag_seeded"] is True and seed["warm_seeded"] is True
    assert (seed.get("demag_seed_from") or {}).get("same_geometry") is True
    t0, t1 = _t(cold), _t(seed)
    assert abs(t1 - t0) <= RTOL_T * abs(t0), (
        f"T_avg moved {100.0 * (t1 - t0) / t0:+.3f} % between the cold run and "
        f"the seeded one ({t0:.6g} -> {t1:.6g} N·m)")
    b0, b1 = _br(cold), _br(seed)
    assert abs(b1 - b0) <= ATOL_BR_PP, (
        f"Br kept moved {b1 - b0:+.3f} pp ({b0:.3f} -> {b1:.3f} %)")
    # …and it got there on fewer SOLVED frames — the whole point.  The pre-pass
    # period is gone entirely; the warm-up march is whatever the settle test
    # still asks for.
    assert int(seed["demag_prepass_frames"]) == 0, (
        "the seeded run re-ran the demag pre-pass the parent already paid for")
    n0 = int(cold["eddy_warmup_frames"]); n1 = int(seed["eddy_warmup_frames"])
    assert n1 < n0, (
        f"the seeded run solved {n1} discarded frame(s) against the cold run's "
        f"{n0} — no work was saved")


@pytestmark_slow
def test_interactive_still_runs_the_full_prepass(legs):
    """The flag is off on the interactive path, and that must still be true
    with a fully-populated cache sitting beside the config."""
    d = legs["flag_off"]
    assert d["demag_seeded"] is False, (
        "an interactive Simulation run continued another run's magnet — the "
        "2026-09-05 reproducibility fix is gone")
    assert d.get("demag_seed_from") is None
    nrep = len(d.get("T_em_Nm") or [])
    assert int(d["demag_prepass_frames"]) == nrep > 0, (
        "the interactive pre-pass is no longer one whole electrical period")
    # It agrees with the cold leg to the same tolerance test_demag_reproducible
    # pins — the seeding work must not have touched that.
    t0, t1 = _t(legs["cold"]), _t(d)
    assert abs(t1 - t0) <= 3e-3 * abs(t0), (
        f"two interactive runs disagree by {100.0 * (t1 - t0) / t0:+.3f} %")
