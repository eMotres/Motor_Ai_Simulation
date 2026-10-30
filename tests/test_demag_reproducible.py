"""Two identical eddy+demag runs must give the same machine.

User, 2026-09-05: "the second solve always differs from the first — figure it out".
Two identical Runs of the Ø200 12s/10p at 470.2 A rms / 20000 rpm / 36 steps,
coupled eddy + demag, current drive, gave T_avg 237.22 vs 244.61 N·m (+3.1 %),
Br kept 91.4 vs 98.5 %, Ld 0.055 vs 0.044 mH and rotor heat 837 vs 713 W; a
third gave 232.49.

CAUSE: the irreversible Br ratchet (``MagnetDemag.update``) ran on every solved
frame, the coupled-eddy WARM-UP march at θ<0 included — and that march is the
σ·∂A/∂t START-UP transient.  Its ∂A/∂t depends on what the eddy history was
seeded with: zero on a cold start, the previous run's settled field when
``_SB_WARM_CACHE`` / ``config/.warm_cache.npz`` seeds it.  A monotone rule burns
the worst field it is ever shown in permanently, so each run recorded a
different non-physical worst case, and Br — with torque, Ld/Lq and rotor loss
behind it — inherited that.

FIX (fem_solver_2d, ``_dm_freeze_warm`` / the pre-pass splice at the eddy
handoff): the ratchet is FROZEN for the whole warm-up march, and the handoff is
followed by a dedicated demag PRE-PASS — one full electrical period at θ<0, eddy
history continued, ratchet ON, frames discarded — before the reported window
opens.  That is what the magnetostatic path has always done (``_dmskip``).

The three legs below are the ones that used to disagree: cold, warm-cache-seeded
in the same process, and cold again with SB_NO_WARM_CACHE=1.  They are run ONCE
in a module-scoped fixture because each is a full transient.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation import fem_solver_2d as F
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_physics_regression import (CASES, CONNECTION, GEO_30MM,
                                           OVERRIDE, RPM)

# Real FEM solves, three of them — same marker the physics-regression cases use.
pytestmark = pytest.mark.slow

# The pinned eddy+demag case, exactly as the regression suite runs it: 12
# steps/period on the 1.4 mm mesh, ~90 s a leg.  It is NOT coarsened further.
# Measured 2026-09-05 on this machine, with the fix in: 8 steps/period leaves a
# 1.26 % per-magnet spread, 12 leaves 0.36 %.  That is the RATCHET'S SAMPLING,
# not an asymmetry — each magnet only ever sees the rotor positions the frames
# land on, and a 12s14p rotor sweeps 1.7 slot pitches per electrical period, so
# at 8 frames two magnets sample two different sets of tooth alignments and find
# two different worst cases.  Coarsening the case would have meant relaxing the
# spread assertion below, i.e. deleting the check the user's 1.77 % report is
# about.
CASE = dict(CASES["p2_demag_eddy"])

# T_avg to 0.3 %, Br kept to 0.3 percentage points — the user's report was
# +3.1 % and 7.1 pp, i.e. an order of magnitude outside these.
RTOL_T = 3e-3
ATOL_BR_PP = 0.3


def _clear_warm_cache() -> None:
    """Make the next run a COLD one (the sandbox path follows the config)."""
    F._SB_WARM_CACHE.clear()
    try:
        p = F._warm_cache_path()
        if p.exists():
            p.unlink()
    except Exception:      # noqa: BLE001 — a missing file is the goal
        pass


def _run() -> Dict[str, Any]:
    set_request_materials(OVERRIDE)
    try:
        # the warm-seed legs are the MARCH's (TDM ignores the seed)
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                          connection=CONNECTION, eddy_method="march",
                                          **CASE)
    finally:
        set_request_materials(None)


@pytest.fixture(scope="module")
def legs() -> Dict[str, Dict[str, Any]]:
    _clear_warm_cache()
    cold = _run()
    # The cache must actually be armed, or the "seeded" leg below is a second
    # cold run and proves nothing.
    assert F._SB_WARM_CACHE.get("last") is not None, (
        "the coupled-eddy run published no warm-cache state, so the seeded leg "
        "would be a duplicate cold run")
    seeded = _run()
    os.environ["SB_NO_WARM_CACHE"] = "1"
    try:
        _clear_warm_cache()
        nocache = _run()
    finally:
        os.environ.pop("SB_NO_WARM_CACHE", None)
    _clear_warm_cache()
    return {"cold": cold, "seeded": seeded, "nocache": nocache}


def _t(d: Dict[str, Any]) -> float:
    return float(d["T_avg_Nm"])


def _br(d: Dict[str, Any]) -> float:
    """Volume-weighted Br kept [%] — the number the Demag card headlines."""
    s = d.get("demag_summary") or {}
    return float(s["br_kept_vol_pct"])


def test_the_demag_ran_at_all(legs):
    """A case whose magnet never de-rates cannot detect a ratchet bug."""
    for name, d in legs.items():
        assert (d.get("demag_summary") or {}), f"{name}: no demag summary"
        assert _br(d) < 99.999, (
            f"{name}: the magnet kept {_br(d):.4f} % of its Br — nothing "
            "ratcheted, so this case guards nothing")


def test_second_run_in_the_same_process_agrees(legs):
    """Run 2 is warm-cache-seeded; it is the user's own reproduction."""
    t0, t1 = _t(legs["cold"]), _t(legs["seeded"])
    assert abs(t1 - t0) <= RTOL_T * abs(t0), (
        f"T_avg moved {100.0 * (t1 - t0) / t0:+.3f} % between two identical "
        f"runs ({t0:.6g} -> {t1:.6g} N·m); the warm-cache seed is reaching the "
        "Br ratchet again")
    b0, b1 = _br(legs["cold"]), _br(legs["seeded"])
    assert abs(b1 - b0) <= ATOL_BR_PP, (
        f"Br kept moved {b1 - b0:+.3f} pp between two identical runs "
        f"({b0:.3f} -> {b1:.3f} %)")


def test_cold_and_seeded_agree(legs):
    """SB_NO_WARM_CACHE=1 (no seed at all) against the seeded leg."""
    t0, t1 = _t(legs["nocache"]), _t(legs["seeded"])
    assert abs(t1 - t0) <= RTOL_T * abs(t0), (
        f"T_avg differs {100.0 * (t1 - t0) / t0:+.3f} % between a run with the "
        f"warm cache OFF and a seeded one ({t0:.6g} -> {t1:.6g} N·m)")
    b0, b1 = _br(legs["nocache"]), _br(legs["seeded"])
    assert abs(b1 - b0) <= ATOL_BR_PP, (
        f"Br kept differs {b1 - b0:+.3f} pp between a warm-cache-OFF run and a "
        f"seeded one ({b0:.3f} -> {b1:.3f} %)")


def test_the_magnets_end_up_identical(legs):
    """After a full-period pre-pass every magnet has seen the same worst MMF.

    The solver already warns above 0.5 % (``demag per-magnet spread``); this
    asserts it, because the asymmetry the user saw (1.77 %) was the ratchet
    recording a start-up transient that reached each magnet differently.
    Measured on his own Ø200 12s/10p at 470.2 A: 1.701 % before the fix,
    0.097 % after — the spread WAS the bug, not the machine.
    """
    # 0.8 %, not 0.5 %, since the per-element H is no longer SMOOTHED before
    # the ratchet (no-filter pass 2026-09-24, docs/NO_FILTERS_2026-09-24.md §3):
    # the corner elements now read the field they are in, so the 12-frame
    # sampling spread measured above (0.36 % smoothed) reads 0.64 % on the cold
    # leg — each magnet still meets a different set of tooth alignments.  The
    # user's ratchet bug was 1.7-1.8 %; this still fails it by 2x.
    for name, d in legs.items():
        sp = float((d.get("demag_summary") or {})["per_magnet_spread_pct"])
        assert sp <= 0.8, (
            f"{name}: per-magnet kept-energy spread {sp:.3f} % — the magnets "
            "of a symmetric machine must be identical after the pre-pass")


def test_the_prepass_ran_and_is_reported(legs):
    """The discarded-frame count must name the pre-pass, or the Solid tile's
    'settled window' tooltip is telling the user something untrue."""
    for name, d in legs.items():
        npre = int(d.get("demag_prepass_frames") or 0)
        # n_periods is 1 here, so the REPORTED window is itself one electrical
        # period — and the pre-pass must be exactly as long.  Compared against
        # the window rather than against n_steps_per_period because the solver
        # snaps the step count UP to a divisor of the slip-node count.
        nrep = len(d.get("T_em_Nm") or [])
        # Since 2026-09-30 the pre-pass is REPEATED until one whole period
        # moves Br by <= the demag settle tolerance (the shared fixed-point
        # rule, docs/TDM_PROTOTYPE_2026-09-30.md §3.3): a whole number of
        # electrical periods, as many as `demag_settle` says.
        nper = int((d.get("demag_settle") or {}).get("prepass_periods") or 0)
        assert nper >= 1 and npre == nper * nrep > 0, (
            f"{name}: {npre} demag pre-pass frame(s) against a {nrep}-frame "
            f"reported window and {nper} pre-pass period(s) — the pre-pass is "
            "not a whole number of electrical periods")
        assert int(d.get("eddy_warmup_frames") or 0) >= npre, (
            f"{name}: the discarded-frame total does not include the pre-pass")


# ── the WARM LIBRARY keeps the guarantee (owner 2026-10-30) ─────────────────
# "We set 40 frames and it is already 280 steps — why is the warm-up not kept?"
# An interactive run now continues a stored state of the SAME geometry + mesh
# (simulation/warm_library.py): its eddy history at any operating point and,
# at the SAME operating point, the Br map a full pre-pass built — verified by
# one pre-pass period of its own instead of re-running the whole image history.
# These two legs are the pair the brief pins: a library-seeded interactive run
# agrees with a cold one to the tolerances above, for fewer solved frames.
@pytest.fixture(scope="module")
def lib_legs() -> Dict[str, Dict[str, Any]]:
    from motor_ai_sim.simulation import warm_library as W
    os.environ["SB_WARM_LIB"] = "1"
    try:
        _clear_warm_cache(); W.clear()
        cold = _run()
        seeded = _run()
    finally:
        os.environ.pop("SB_WARM_LIB", None)
        _clear_warm_cache(); W.clear()
    return {"cold": cold, "seeded": seeded}


def test_library_cold_run_says_it_was_cold(lib_legs):
    ws = lib_legs["cold"].get("warm_start") or {}
    assert ws.get("seed_source") == "cold"
    assert ws.get("note") == "cold start: no stored state for this geometry", ws
    assert lib_legs["cold"]["demag_seeded"] is False


def test_library_seeded_run_is_the_same_machine_for_fewer_frames(lib_legs):
    cold, seed = lib_legs["cold"], lib_legs["seeded"]
    ws = seed.get("warm_start") or {}
    assert ws.get("seed_source") == "library", ws
    assert ws.get("br_map_seeded") is True and ws.get("eddy_history_seeded") is True
    src = seed.get("demag_seed_from") or {}
    assert src.get("source") == "library" and src.get("same_operating_point") is True
    t0, t1 = _t(cold), _t(seed)
    assert abs(t1 - t0) <= RTOL_T * abs(t0), (
        f"T_avg moved {100.0 * (t1 - t0) / t0:+.3f} % between a cold run and a "
        f"library-seeded one ({t0:.6g} -> {t1:.6g} N·m)")
    b0, b1 = _br(cold), _br(seed)
    assert abs(b1 - b0) <= ATOL_BR_PP, (
        f"Br kept moved {b1 - b0:+.3f} pp ({b0:.3f} -> {b1:.3f} %)")
    # the pre-pass is VERIFIED, not skipped: at least one whole period, and the
    # reported window is still judged settled by the unchanged rule
    nrep = len(seed.get("T_em_Nm") or [])
    npre = int(seed["demag_prepass_frames"])
    assert npre >= nrep > 0, "the library-seeded run skipped the verification period"
    assert int(cold["demag_prepass_frames"]) > npre, (
        "the library-seeded run solved as many pre-pass frames as the cold one")
    assert seed.get("demag_settled") is True, seed.get("steady_state_note")
    assert ws.get("prepass_frames") == npre
