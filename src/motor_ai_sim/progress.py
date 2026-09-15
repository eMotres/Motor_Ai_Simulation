"""One live-progress mechanism, shared by every long solve in this backend.

WHY THIS EXISTS
---------------
The Simulation tab's transient has had a progress strip since the first
sliding-band run: ``routes/simulation.py`` keeps a plain dict
(``_fem_transient_progress``), the solver fills it from a per-frame callback and
``GET /physics/fem_transient/progress`` turns it into
"frame 37 / 96 — 41 s elapsed — ETA 62 s".  The Mechanical and Thermal tabs had
nothing: a rotor-stress solve on a 200 mm machine is a minute of gmsh plus three
nonlinear contact solves, a coupled thermal run is up to twelve full EM
transients, and both of them showed a spinner that never moved.  A bar that does
not move is read as a hung run — that is the whole bug this module closes.

Rather than copy the transient's dict twice more (three places computing the
same ETA three ways, drifting the moment one of them is fixed), the arithmetic
lives here ONCE and each router owns one instance.  ``snapshot()`` reproduces
the transient endpoint's response shape EXACTLY — same keys, same rounding, same
ETA rules — so the frontend has one strip component and one contract to read,
whatever is running.  The transient's own dict is deliberately left alone: it is
load-bearing for the Stop button and the cancel path, and rewriting it to prove
a point about tidiness is not worth the risk to a route the user runs all day.

THE ETA RULE, AND WHY IT IS NOT AN AVERAGE
------------------------------------------
Before the first step COMPLETES there is no timing sample — only a one-time mesh
build and a first solve, which on a cold machine is many times a steady-state
step.  Extrapolating from it produces an ETA that climbs with elapsed time (the
longer you wait, the longer it says you have left), which is worse than no ETA
at all.  So ``eta_s`` and ``per_step_s`` are reported as 0 — "unknown" — until
``step > 0``, exactly as the transient does.

THE CALLBACK CONTRACT
---------------------
Solvers must not import a router (that is a cycle, and it welds a physics module
to an HTTP shape), so they take a plain ``progress=None`` callable:

    progress(done: int, total: int, phase: str | None = None,
             composition: str | None = None)

``None`` for ``phase`` / ``composition`` means "keep what is there" — a solver
that has nothing new to say about the stage does not have to invent a label.
``ProgressTracker.callback()`` hands out exactly that function, so a route wires
a solve up with one argument and the solver stays testable with a plain lambda.
``total`` is the solver's OWN honest count: a caller that nests one solve inside
another (rotor stress runs a contact iteration per case) adapts the numbers in a
small closure of its own rather than making the inner solver aware of the outer.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

__all__ = ["ProgressTracker", "ProgressCallback", "StepLedger"]

#: What a solver's ``progress=`` parameter is called with.  Documented as a type
#: alias so the four mechanical / thermal solvers cannot each invent their own.
ProgressCallback = Callable[..., None]


class ProgressTracker:
    """The live state of ONE router's current solve, safe to poll from anywhere.

    One instance per router (``routes/mechanical.py``, ``routes/thermal.py``),
    module-level, because that is what a poll endpoint can reach without a
    request context.  The lock is not decoration: the solve runs in FastAPI's
    threadpool while the poll arrives on the event loop, so ``snapshot()`` and
    the solver's callback genuinely race — without it a poll can catch a half
    written state and report ``step`` from one stage against ``total`` from the
    next (a bar that jumps backwards, which reads as a restart).

    ``kind`` is what the UI NAMES: the same tracker carries a rotor-stress solve,
    a modal solve and a mesh build, and "Solving…" over a 90 s modal run tells
    the user nothing.  It is kept out of ``snapshot()`` on purpose — that dict is
    the transient's shape, byte for byte — and the routes merge it in.
    """

    __slots__ = ("_lock", "_running", "_step", "_total", "_phase",
                 "_composition", "_ts_start", "_elapsed", "_kind")

    def __init__(self, kind: str = "") -> None:
        self._lock = threading.Lock()
        self._running = False
        self._step = 0
        self._total = 0
        self._phase = "idle"
        self._composition = ""
        self._ts_start = 0.0
        self._elapsed = 0.0
        self._kind = str(kind or "")

    # ── the solve's own bookkeeping ─────────────────────────────────────────

    def start(self, total: int, phase: str,
              composition: str = "", kind: Optional[str] = None) -> None:
        """A solve is beginning: reset the counters and start the clock.

        ``total`` is an HONEST pre-run count built from the request (cases x
        iterations, frames + stages), not a guess at wall-clock — the solver
        revises it through :meth:`update` the moment it knows better, which is
        what makes an adaptive stage (an eddy warm-up, a contact loop that
        converges early) legible rather than a bar that lies twice.
        """
        with self._lock:
            self._running = True
            self._step = 0
            self._total = max(int(total), 0)
            self._phase = str(phase or "")
            self._composition = str(composition or "")
            self._ts_start = time.time()
            self._elapsed = 0.0
            if kind is not None:
                self._kind = str(kind)

    def update(self, done: Optional[int] = None, total: Optional[int] = None,
               phase: Optional[str] = None,
               composition: Optional[str] = None) -> None:
        """Report progress.  Every argument is optional; ``None`` keeps the
        current value, so a solver that only advanced a step does not have to
        repeat the label it set three stages ago.

        ``total`` is allowed to move in BOTH directions.  Down: a contact
        iteration that converges in 4 of its budgeted 30 hands the unused 26
        back, and a bar that finished early should say so rather than jump.  Up:
        an eddy warm-up splices frames in front of the reported window, so the
        count genuinely grows — that IS what adaptive means.  It is never
        allowed BELOW ``step``, because a fraction over 1 is not a fraction.
        """
        with self._lock:
            if done is not None:
                self._step = max(int(done), 0)
            if total is not None:
                self._total = max(int(total), 0)
            if self._total < self._step:
                self._total = self._step
            if phase is not None:
                self._phase = str(phase)
            if composition is not None:
                self._composition = str(composition)

    def finish(self) -> None:
        """The solve is over: stop the clock, freeze the elapsed seconds.

        ``step`` is snapped to ``total`` deliberately.  A finished run whose bar
        stopped at 37/40 (the last contact case converged early, the last three
        budgeted iterations were never spent) reads on screen as a run that died
        three steps from the end.  ``running=False`` is what says "over"; the
        counter is what says "all of it".

        Call it in a ``finally``: an exception must never leave the endpoint
        reporting a live solve, or the panel waits for a run that is not there.
        """
        with self._lock:
            if self._ts_start > 0:
                self._elapsed = max(time.time() - self._ts_start, 0.0)
            if self._total > 0:
                self._step = self._total
            self._running = False

    def callback(self) -> ProgressCallback:
        """A plain ``progress(done, total, phase=None, composition=None)``.

        Handed to solvers so they stay decoupled from the router and from this
        class — they call a function, and a test calls them with a list-append
        lambda.  It swallows nothing and raises nothing of its own: the FEM
        march wraps callbacks in ``except TypeError`` arity probing, so a
        callback that threw a TypeError of its own would be silently retried
        with fewer arguments and quietly lose the phase.
        """
        def _cb(done: Optional[int] = None, total: Optional[int] = None,
                phase: Optional[str] = None,
                composition: Optional[str] = None) -> None:
            self.update(done=done, total=total, phase=phase,
                        composition=composition)
        return _cb

    # ── what the poll endpoint answers ──────────────────────────────────────

    @property
    def kind(self) -> str:
        """Which solve this is — ``rotor_stress``, ``modes``, ``field``… .

        Set by :meth:`start`; it SURVIVES the finish so a poll that arrives just
        after a run can still name what has just ended.
        """
        with self._lock:
            return self._kind

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def snapshot(self) -> Dict[str, Any]:
        """The progress payload, in the transient endpoint's exact shape.

        ``{running, step, total, elapsed_s, eta_s, per_step_s, frac, phase,
        composition, ts_start}`` — every key on every call, including when
        nothing is running.  The transient's own endpoint omits ``per_step_s`` /
        ``frac`` on the idle path, which forces the client to guard for them;
        here the shape is invariant so a strip can bind to it once.
        """
        with self._lock:
            running = self._running
            step = self._step
            total = self._total
            phase = self._phase
            composition = self._composition
            ts_start = self._ts_start
            elapsed = (max(time.time() - ts_start, 0.0)
                       if (running and ts_start > 0) else self._elapsed)

        if running and step <= 0:
            # No completed step = no timing sample.  See the module docstring:
            # a single-sample extrapolation off the mesh build climbs with
            # elapsed, and an ETA that grows while you wait is worse than none.
            per_step = 0.0
            eta = 0.0
            frac = 0.0
        else:
            per_step = (elapsed / step) if step > 0 else 0.0
            eta = (per_step * max(0, total - step)) if running else 0.0
            frac = (step / total) if total > 0 else 0.0

        return {
            "running": bool(running),
            "step": int(step),
            "total": int(total),
            "elapsed_s": round(float(elapsed), 1),
            "eta_s": round(float(eta), 1),
            "per_step_s": round(float(per_step), 2),
            "frac": round(float(frac), 3),
            "phase": phase,
            "composition": composition,
            "ts_start": float(ts_start),
        }


class StepLedger:
    """A solver's own step budget, on the near side of the callback.

    Three solvers (rotor stress, modal, rotordynamics) all need the same three
    things and none of them is worth writing three times: a running count, a
    total they may have to REVISE when a stage finishes early, and a phase label
    they change every few steps.  Doing this with bare integers inside the
    solvers meant either ``nonlocal`` gymnastics in nested closures or a total
    that drifted away from the count — both of which show up on screen as a bar
    that jumps.

    A ``None`` callback makes every method a no-op, so a solver wires progress in
    unconditionally and nothing in the physics path has to ask "was I given a
    bar" at each of its dozen report points.
    """

    __slots__ = ("_cb", "done", "total", "composition")

    def __init__(self, cb: Optional[ProgressCallback], total: int,
                 composition: str = "") -> None:
        self._cb = cb
        self.done = 0
        self.total = max(int(total), 0)
        self.composition = str(composition or "")
        # Announce the SOLVER's total straight away.  The route had to guess one
        # before the solve to open the bar at all; this is the first moment
        # anybody knows the real count (cases actually solved, budget actually
        # granted), and correcting it before step 1 costs nothing.
        self._emit(None)

    def _emit(self, phase: Optional[str]) -> None:
        if self._cb is None:
            return
        try:
            self._cb(self.done, self.total, phase, self.composition)
        except Exception:  # noqa: BLE001 - a bar must never break a solve
            self._cb = None

    def at(self, done: int, phase: Optional[str] = None) -> None:
        """Absolute position — for a stage whose inner counter starts over."""
        self.done = max(int(done), 0)
        self._emit(phase)

    def advance(self, n: int = 1, phase: Optional[str] = None) -> None:
        self.done += int(n)
        self._emit(phase)

    def phase(self, phase: str) -> None:
        """Same step, new label: post-processing that is one long stage."""
        self._emit(phase)

    def grow(self, n: int) -> None:
        """Add budget for work that was NOT planned — a contact loop that needs
        more iterations than the previous solve did, a lift-off search that a
        rotor with an opening joint actually has to run.  The bar lengthens
        instead of sticking at 100 % while the solver is still working."""
        if n > 0:
            self.total += int(n)
            self._emit(None)

    def give_back(self, n: int) -> None:
        """Hand unspent budget back — a contact loop that converged in 4 of 30.

        The bar then reaches its end when the work does, instead of stopping at
        two thirds and looking like a run that died.
        """
        if n > 0:
            self.total = max(self.done, self.total - int(n))
            self._emit(None)
