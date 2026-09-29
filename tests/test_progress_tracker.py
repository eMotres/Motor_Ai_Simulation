"""The shared progress mechanism — ``motor_ai_sim.progress``.

Written 2026-09-07 with the Mechanical / Thermal progress bars.  The class is
tiny, but everything it does is a contract two routers and a frontend strip are
built on, and every one of these assertions is a bug somebody would otherwise
find on screen rather than here:

  (a) the payload SHAPE is invariant — the same ten keys idle, running and
      finished, because a strip that has to guard for missing keys is a strip
      that renders "NaN%" on the first poll;
  (b) the ETA rule — 0 (unknown) until a step has completed, never a
      single-sample extrapolation off the mesh build, which climbs with elapsed
      time and tells the user the wait is getting longer;
  (c) ``finish`` stops the bar AND fills it: a finished solve whose last stage
      converged early must not read as a run that died three steps from the end;
  (d) it survives being written from several threads while it is polled, which
      is exactly what happens — the solve runs in FastAPI's threadpool, the poll
      arrives on the event loop.
"""
from __future__ import annotations

import threading
import time

import pytest

from motor_ai_sim.progress import ProgressTracker, StepLedger

#: The response contract.  Named here rather than inline so the route tests in
#: tests/test_progress_routes.py can import the ONE list.
KEYS = {"running", "step", "total", "elapsed_s", "eta_s", "per_step_s", "frac",
        "phase", "composition", "ts_start"}


# ---------------------------------------------------------------------------
# (a) the shape
# ---------------------------------------------------------------------------

def test_idle_snapshot_has_every_key_and_is_not_running():
    t = ProgressTracker()
    s = t.snapshot()
    assert set(s) == KEYS
    assert s["running"] is False
    assert s["step"] == 0 and s["total"] == 0
    assert s["eta_s"] == 0.0 and s["frac"] == 0.0 and s["per_step_s"] == 0.0
    assert s["phase"] == "idle"
    assert s["composition"] == ""
    assert s["ts_start"] == 0.0


def test_shape_is_the_same_while_running_and_after_finishing():
    t = ProgressTracker()
    t.start(total=10, phase="meshing", composition="1 mesh + 9 frames")
    assert set(t.snapshot()) == KEYS
    t.update(done=3)
    assert set(t.snapshot()) == KEYS
    t.finish()
    assert set(t.snapshot()) == KEYS


def test_start_publishes_total_phase_and_composition():
    t = ProgressTracker()
    t.start(total=7, phase="assembly", composition="mesh + 6 cases",
            kind="rotor_stress")
    s = t.snapshot()
    assert s["running"] is True
    assert s["total"] == 7 and s["step"] == 0
    assert s["phase"] == "assembly"
    assert s["composition"] == "mesh + 6 cases"
    assert s["ts_start"] > 0
    # `kind` is deliberately OUTSIDE the snapshot (that dict is the transient's
    # shape, byte for byte); the routes merge it in.
    assert "kind" not in s
    assert t.kind == "rotor_stress"


def test_update_treats_none_as_keep_what_is_there():
    t = ProgressTracker()
    t.start(total=10, phase="stage one", composition="ten steps")
    t.update(done=4)                       # phase / composition untouched
    s = t.snapshot()
    assert s["step"] == 4
    assert s["phase"] == "stage one" and s["composition"] == "ten steps"
    t.update(phase="stage two")            # step untouched
    s = t.snapshot()
    assert s["step"] == 4 and s["phase"] == "stage two"


# ---------------------------------------------------------------------------
# (b) the ETA rule
# ---------------------------------------------------------------------------

def test_eta_is_unknown_until_the_first_step_completes():
    """0, not a guess.

    Before step 1 the only elapsed time is the mesh build plus the first solve —
    on a cold machine many times a steady-state step.  Extrapolating from it
    gives an ETA that GROWS while you wait, which is worse than admitting
    ignorance.
    """
    t = ProgressTracker()
    t.start(total=40, phase="fem-solve")
    time.sleep(0.05)
    s = t.snapshot()
    assert s["running"] is True
    assert s["step"] == 0
    assert s["eta_s"] == 0.0
    assert s["per_step_s"] == 0.0
    assert s["frac"] == 0.0
    assert s["elapsed_s"] >= 0.0          # the clock IS running


def test_eta_extrapolates_from_the_average_once_steps_have_completed():
    t = ProgressTracker()
    t.start(total=10, phase="fem-solve")
    time.sleep(0.12)
    t.update(done=2)
    s = t.snapshot()
    assert s["frac"] == pytest.approx(0.2, abs=1e-9)
    assert s["per_step_s"] > 0.0
    # 8 steps left at the measured rate, within the rounding the endpoint does.
    assert s["eta_s"] == pytest.approx(s["per_step_s"] * 8, abs=0.2)


def test_total_may_grow_and_shrink_but_never_below_the_step():
    """Adaptive stages move the total in both directions — an eddy warm-up
    splices frames in FRONT of the reported window, a contact loop hands back
    what it did not spend.  A total under the step would put the fraction over
    1, which is not a fraction."""
    t = ProgressTracker()
    t.start(total=10, phase="x")
    t.update(done=6, total=20)
    assert t.snapshot()["total"] == 20
    t.update(total=8)
    s = t.snapshot()
    assert s["total"] == 8 and s["step"] == 6
    t.update(total=2)                      # below the step: clamped
    s = t.snapshot()
    assert s["total"] == 6 and s["frac"] == 1.0


# ---------------------------------------------------------------------------
# (c) finishing
# ---------------------------------------------------------------------------

def test_finish_stops_the_bar_and_fills_it():
    t = ProgressTracker()
    t.start(total=12, phase="cases")
    t.update(done=5)
    t.finish()
    s = t.snapshot()
    assert s["running"] is False
    assert s["step"] == s["total"] == 12    # filled, not frozen at 5/12
    assert s["frac"] == 1.0
    assert s["eta_s"] == 0.0                # nothing left to wait for
    assert s["elapsed_s"] >= 0.0


def test_finish_on_an_untouched_tracker_is_harmless():
    """The routes call it in a ``finally``, including on paths that 422 before
    anything starts."""
    t = ProgressTracker()
    t.finish()
    s = t.snapshot()
    assert s["running"] is False and s["step"] == 0 and s["total"] == 0


def test_elapsed_freezes_at_finish():
    t = ProgressTracker()
    t.start(total=4, phase="x")
    time.sleep(0.05)
    t.finish()
    first = t.snapshot()["elapsed_s"]
    time.sleep(0.15)
    assert t.snapshot()["elapsed_s"] == first


def test_a_second_start_resets_the_counters():
    t = ProgressTracker()
    t.start(total=5, phase="one", composition="a")
    t.update(done=5)
    t.finish()
    t.start(total=3, phase="two", composition="b", kind="modes")
    s = t.snapshot()
    assert s["running"] is True and s["step"] == 0 and s["total"] == 3
    assert s["phase"] == "two" and s["composition"] == "b"
    assert t.kind == "modes"


# ---------------------------------------------------------------------------
# the solver-facing callback
# ---------------------------------------------------------------------------

def test_callback_accepts_the_four_argument_solver_contract():
    """The FEM march calls ``progress_cb(done, total, phase, composition)`` and
    falls back to shorter arities on TypeError.  A callback that raised a
    TypeError of its own would be silently retried with fewer arguments and
    would quietly lose the phase — so it has to accept all of them."""
    t = ProgressTracker()
    t.start(total=1, phase="x")
    cb = t.callback()
    cb(3, 48, "eddy warm-up", "2x36 settle + 144 reported")
    s = t.snapshot()
    assert s["step"] == 3 and s["total"] == 48
    assert s["phase"] == "eddy warm-up"
    assert s["composition"] == "2x36 settle + 144 reported"
    cb(4, 48)                               # two-argument form: phase kept
    assert t.snapshot()["phase"] == "eddy warm-up"


def test_step_ledger_counts_reports_and_hands_budget_back():
    seen = []
    led = StepLedger(lambda d, t, p=None, c=None: seen.append((d, t, p, c)),
                     total=10, composition="mesh + 9")
    assert seen[0] == (0, 10, None, "mesh + 9")   # the total, announced at once
    led.advance(1, "mesh build")
    led.at(4, "case 1/1")
    led.phase("post-processing")
    assert seen[-1] == (4, 10, "post-processing", "mesh + 9")
    led.give_back(3)
    assert seen[-1][1] == 7
    led.give_back(99)                              # never below what is done
    assert seen[-1][1] == 4


def test_step_ledger_with_no_callback_is_a_no_op():
    led = StepLedger(None, total=5)
    led.advance(2, "x")
    led.give_back(1)
    assert led.done == 2


def test_a_broken_callback_never_reaches_the_solver():
    """A progress bar must not be able to kill a solve."""
    def boom(*_a, **_k):
        raise RuntimeError("the panel went away")

    led = StepLedger(boom, total=3)         # raises inside __init__ and is caught
    led.advance(1, "still fine")
    assert led.done == 1


# ---------------------------------------------------------------------------
# (d) thread safety
# ---------------------------------------------------------------------------

def test_concurrent_updates_and_polls_stay_consistent():
    """The solve writes from the threadpool while the poll reads on the event
    loop.  Nothing here should raise, and no snapshot may show a step past its
    total (the half-written state a missing lock would expose)."""
    t = ProgressTracker()
    t.start(total=400, phase="fem-solve")
    errors: list = []
    seen: list = []

    def writer(lo, hi):
        try:
            for i in range(lo, hi):
                t.update(done=i, total=400, phase=f"frame {i}")
        except Exception as exc:            # noqa: BLE001
            errors.append(exc)

    def reader():
        try:
            for _ in range(300):
                s = t.snapshot()
                seen.append((s["step"], s["total"]))
        except Exception as exc:            # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(0, 200)),
               threading.Thread(target=writer, args=(200, 400)),
               threading.Thread(target=reader),
               threading.Thread(target=reader)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors
    assert seen and all(0 <= st <= tot for st, tot in seen)
    assert set(t.snapshot()) == KEYS
