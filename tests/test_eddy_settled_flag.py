"""A number that did not settle must SAY so — the eddy-settle verdict.

User's sweep, 2026-09-07.  Ninety points, and every single one of them came
back with ``eddy_warmup_frames = 57``: two probe frames, the handoff frame, and
one whole 54-step electrical period of extension — i.e. every probe failed and
every march then ran to its one-shot cap.  The frame count was the ONLY thing
the point carried about the warm-up, and a count says what the march COST, never
whether it WORKED.  On the designs whose aluminium shaft tube sits closest to
the field (a shorter magnet pushes ``rotor_inner_radius`` outward) the shaft
eddy loss then read 504 W at air gap 2.6 mm, 3558 W at 3.1 mm and 6652 W at
1.6 mm with the magnet fixed at 24 mm — start-up transients, not physics, which
drove efficiency and total loss on the sweep chart and were read as
demagnetisation.  They also cost ~3x the runtime of a settled point.

So the run now carries the VERDICT beside the cost:

  * ``eddy_settled``           — the quiet test passed at the handoff;
  * ``eddy_capped``            — the march ended at its maximum allowed length
                                 (the one-shot extension to one electrical
                                 period, an ``SB_EDDY_WARM`` pin, or the
                                 voltage settling schedule) instead;
  * ``eddy_settle_residual`` / ``eddy_settle_tol`` — what was left, against what.

THE CRITERION itself is pinned next door, in
``test_physics_regression.test_eddy_settle_resid_reports_the_tail_not_the_last_step``:
block-average the solid-conductor loss over one 6th-harmonic ripple period,
Aitken-extrapolate the geometric tail of the last three block means, and report
that TAIL (not the last step) as a fraction of the settled level, against a 2 %
tolerance.  This file pins that the verdict of that criterion reaches the
result dict and the sweep point instead of only a log line.
"""
from __future__ import annotations

import types
from typing import Any, Dict

import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.optimization import refine_proc
from motor_ai_sim.simulation import fem_solver_2d as F
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_optimizer_honesty import CFG, _raw
from tests.test_physics_regression import (CASES, CONNECTION, GEO_30MM,
                                           OVERRIDE, RPM)


def _clear_warm_cache() -> None:
    """Make the next run a COLD one — no seeded same-angle reference.

    The seeded path has its own shortcut through the quiet test (compare the
    probe against the previous run's frames at the SAME rotor angle), and these
    tests are about the trend gauge, so they must not inherit a neighbour's
    settled field from an earlier test in the session.
    """
    F._SB_WARM_CACHE.clear()
    try:
        p = F._warm_cache_path()
        if p.exists():
            p.unlink()
    except Exception:      # noqa: BLE001 — a missing file is the goal
        pass


def _eddy_run(**over: Any) -> Dict[str, Any]:
    """One coupled-eddy transient on the sandbox 30 mm 12s14p (raw dict)."""
    kw = dict(CASES["p2_eddy"]); kw.update(over)
    _clear_warm_cache()
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                          connection=CONNECTION, **kw)
    finally:
        set_request_materials(None)
        _clear_warm_cache()


# ── (a) a march that is long enough says so ─────────────────────────────────
@pytest.mark.slow
def test_a_settled_march_reports_settled_and_not_capped():
    """The 30 mm settles inside the probe (199 -> 1.5 W in one frame)."""
    d = _eddy_run()
    assert d["eddy_settled"] is True, (
        "a machine whose start-up transient is gone after one frame reported "
        f"NOT settled (residual {d['eddy_settle_residual']!r} against tol "
        f"{d['eddy_settle_tol']!r})")
    assert d["eddy_capped"] is False
    # The verdict is not a bare boolean: it comes with the number it was made
    # on, under the same tolerance the criterion uses.
    assert d["eddy_settle_residual"] is not None
    assert d["eddy_settle_tol"] == pytest.approx(0.02)
    assert d["eddy_settle_residual"] <= d["eddy_settle_tol"]
    # …and the two spellings of the same measurement agree (eddy_warmup_* is
    # what the physics-regression pins and the Simulation payload have always
    # read; eddy_settle_* is what the sweep points and the UI carry).
    assert d["eddy_settle_residual"] == d["eddy_warmup_resid"]
    assert d["eddy_settle_tol"] == d["eddy_warmup_tol"]


# ── (b) a march cut short says THAT ─────────────────────────────────────────
@pytest.mark.slow
def test_a_capped_march_reports_unsettled_and_capped(monkeypatch):
    """SB_EDDY_WARM pins the march length and disables the extension.

    ``SB_EDDY_WARM=0`` is the pre-adaptive cold start: no probe, no extension
    (``_eddy_cap`` is zeroed with the pin), so the handoff is taken on frame 0
    whatever state the field is in.  That is the bug of 2026-08 reproduced on
    purpose — and the point here is that the result now REFUSES to look like a
    settled one.  The residual is None because three samples are the minimum
    the criterion needs and a pinned zero-length march has one: "unmeasured" is
    itself honest, and it must not read as "fine".
    """
    monkeypatch.setenv("SB_EDDY_WARM", "0")
    d = _eddy_run()
    assert d["eddy_settled"] is False, (
        "the cold start — frame 0 IS the transient, measured 130x the settled "
        "band on this machine — reported as settled")
    assert d["eddy_capped"] is True
    assert d["eddy_settle_residual"] is None
    assert int(d["eddy_warmup_frames"]) == 0, (
        "SB_EDDY_WARM=0 solved warm-up frames after all — this leg no longer "
        "reproduces the cold start")


# ── (c) the verdict reaches the sweep point ─────────────────────────────────
# The solver is faked here on purpose (same reason as test_optimizer_honesty):
# what is under test is refine_proc's bookkeeping, not a transient.
@pytest.fixture()
def fake_solver(monkeypatch):
    import motor_ai_sim.config as _cfgmod

    def _install(raw: dict):
        monkeypatch.setattr(_cfgmod, "get_config", lambda *a, **k: CFG)
        fake = types.SimpleNamespace(
            run=lambda cap, args: {
                "ok": True, "result": types.SimpleNamespace(raw=raw)})
        monkeypatch.setattr(refine_proc, "_kernel", lambda: fake)
    return _install


def _point(fake_solver, **verdict) -> Dict[str, Any]:
    raw = dict(_raw(converged=True))
    raw.update(verdict)
    fake_solver(raw)
    return refine_proc.run_one({}, current_a=60.0, steps=12, coil_temp_c=120.0)


def test_the_sweep_point_carries_the_verdict(fake_solver):
    """An unsettled eval is still a POINT — but a flagged one.

    ``_point_from_eval`` spreads this payload verbatim, so what run_one puts
    here is what the panel gets.  It must not be dropped on the way (it was:
    only the frame COUNT travelled, which is how 90 points reported 57 frames
    and nothing else).
    """
    p = _point(fake_solver, eddy_settled=False, eddy_capped=True,
               eddy_settle_residual=0.44, eddy_settle_tol=0.02,
               eddy_warmup_frames=57)
    assert p["eddy_settled"] is False
    assert p["eddy_capped"] is True
    assert p["eddy_settle_residual"] == pytest.approx(0.44)
    assert p["eddy_settle_tol"] == pytest.approx(0.02)
    assert p["eddy_warmup_frames"] == 57
    # It stays a usable point: the numbers are there, and the coordinator's
    # non-physical gate has no business vetoing it — the panel flags it.
    from motor_ai_sim.routes.optimization import _nonphysical_result
    assert _nonphysical_result(p) is None
    assert p["T_em_Nm"] and p["efficiency"]


def test_a_settled_point_says_so_too(fake_solver):
    p = _point(fake_solver, eddy_settled=True, eddy_capped=False,
               eddy_settle_residual=0.004, eddy_settle_tol=0.02)
    assert p["eddy_settled"] is True and p["eddy_capped"] is False
    assert p["eddy_settle_residual"] == pytest.approx(0.004)


def test_a_result_that_says_nothing_defaults_to_settled(fake_solver):
    """A pre-2026-09-07 solver dict (or a magnetostatic run) carries no
    verdict.  Defaulting to settled is the only reading that leaves those
    results looking exactly as they always did — the flag is an ADDITION, and
    the panel's column only appears when the points actually speak."""
    p = _point(fake_solver)
    assert p["eddy_settled"] is True and p["eddy_capped"] is False
    assert p["eddy_settle_residual"] is None


def test_the_cache_seeding_keys_carry_the_verdict():
    """A stored sweep re-seeded into the eval cache is reduced to _RES_KEYS.

    Dropping the verdict there would hand the panel a point that silently
    reads as settled while its P_mag / P_shaft / efficiency are start-up
    values — the exact failure this whole change exists to end.
    """
    from motor_ai_sim.routes.optimization import _RES_KEYS
    for k in ("eddy_settled", "eddy_capped", "eddy_settle_residual",
              "eddy_settle_tol"):
        assert k in _RES_KEYS, k
