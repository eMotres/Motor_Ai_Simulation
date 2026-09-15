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

import os
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterator, List, Optional

__all__ = ["ProgressTracker", "ProgressCallback", "StepLedger",
           "ProgressRegistry", "RouteProgress", "TransientProgressMap",
           "RunStateMap", "registry", "reset_registry", "use_run",
           "current_run", "route_progress", "poll", "transient_dict",
           "DEFAULT_TTL_S"]

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


# ─────────────────────────────────────────────────────────────────────────────
#  Migration Stage 4 — ONE tracker per RUN, not one per router
# ─────────────────────────────────────────────────────────────────────────────
#
# WHY
# ---
# Everything above is per-ROUTER: ``routes/thermal.py`` holds one
# ``ProgressTracker``, ``routes/mechanical.py`` another, ``routes/coupled.py`` a
# third, and ``routes/simulation.py`` keeps the original dict under the literal
# key ``"current"``.  One live solve per router FOR THE WHOLE SERVER.  With two
# accounts that is not a cosmetic problem: B's transient RESETS the bar A is
# watching (``start()`` zeroes the counters), A's poll then reports B's frame
# count and B's ETA, and when A presses Stop the id it sends is the one it read
# off that shared bar.
#
# The tracker itself needs no change — it never did; what was wrong was its
# OWNERSHIP.  So: a registry keyed by run id, TTL-evicted, and the module-level
# names in the routers become :class:`RouteProgress` proxies that resolve to
# "the tracker of the run this call is inside".  Every existing call site —
# ``_progress.start(...)``, ``_progress.callback()``, ``_progress.snapshot()`` —
# is byte-identical, and ``monkeypatch.setattr(mod, "_progress", …)`` still
# replaces the name outright, which several tests do.
#
# THE COMPATIBILITY PROMISE
# -------------------------
# A call with NO run id in context resolves to the router's DEFAULT tracker —
# one per (route, workspace) — which is precisely the object the module global
# used to be.  So a single-user server with nothing submitted through the queue
# behaves exactly as it did, and ``GET /api/<route>/progress`` with no argument
# keeps answering.  With a run id it answers that run; with no argument and a
# run in flight it answers the CALLER's newest run in that route.

#: How long a finished run's progress stays pollable.  Half an hour: long
#: enough that a user who walked away still finds out how their run ended,
#: short enough that a server that solved all night is not holding a thousand
#: dead counters.  A RUNNING tracker is never evicted, whatever its age.
DEFAULT_TTL_S = 1800.0
#: A hard ceiling on top of the TTL, for a burst nothing has expired out of yet.
DEFAULT_CAP = 256

ENV_TTL = "PROGRESS_TTL_S"


def _ttl_from_env() -> float:
    try:
        return float(str(os.environ.get(ENV_TTL, "")).strip() or DEFAULT_TTL_S)
    except (TypeError, ValueError):
        return DEFAULT_TTL_S


#: The idle transient dict, exactly as ``routes/simulation.py`` spelled it.  A
#: FUNCTION and not a constant because every run needs its own copy — the route
#: mutates it in place at a dozen sites.
def _idle_transient() -> Dict[str, Any]:
    return {"running": False, "step": 0, "total": 0, "elapsed_s": 0.0,
            "eta_s": 0.0, "ts_start": 0.0, "phase": "idle", "composition": ""}


class _Entry:
    """One run's progress: the tracker, and the transient's legacy dict.

    Two carriers because there are two shapes in the codebase and only one of
    them is worth rewriting.  ``tracker`` is the modern one (thermal,
    mechanical, coupled).  ``raw`` is ``_fem_transient_progress["current"]`` —
    a plain dict the sliding-band march writes field by field from inside a
    per-frame callback, at sites that are load-bearing for the Stop button.
    Moving those to the tracker would be a rewrite of the one route the user
    runs all day, for tidiness; giving the dict a per-run home instead costs one
    proxy and changes no solver line.
    """

    __slots__ = ("run_id", "tracker", "raw", "extra", "route", "owner",
                 "ws_id", "created", "touched", "is_default")

    def __init__(self, run_id: str, route: str = "", owner: str = "",
                 ws_id: str = "", is_default: bool = False) -> None:
        self.run_id = str(run_id)
        self.tracker = ProgressTracker()
        self.raw: Dict[str, Any] = _idle_transient()
        #: Whatever else a router keeps per run in its own shape — today only
        #: ``static3d``'s ``_solve_state``, which is neither a tracker nor the
        #: transient's dict and is not worth making into either.
        self.extra: Dict[str, Dict[str, Any]] = {}
        self.route = str(route or "")
        self.owner = str(owner or "")
        self.ws_id = str(ws_id or "")
        self.created = time.time()
        self.touched = self.created
        self.is_default = bool(is_default)

    def touch(self) -> None:
        self.touched = time.time()

    @property
    def running(self) -> bool:
        return bool(self.tracker.running or self.raw.get("running"))

    def snapshot(self) -> Dict[str, Any]:
        """The progress payload for this run, in the invariant shape.

        The transient's dict wins when it has been used, because for that route
        it IS the state; everything else reads the tracker.  The arithmetic is
        the tracker's own, so a job listing can print a transient and a thermal
        run side by side without two ETA rules.
        """
        if self.raw.get("ts_start") or self.raw.get("running"):
            return _snapshot_of_raw(self.raw)
        return self.tracker.snapshot()

    @property
    def kind(self) -> str:
        # The TRACKER's, and nothing else: an idle router answered ``kind: ""``
        # before Stage 4 and must go on doing so.  What this run IS is the job
        # record's ``kind``, which ``/api/jobs`` prints beside this.
        return self.tracker.kind


def _snapshot_of_raw(p: Dict[str, Any]) -> Dict[str, Any]:
    """``_fem_transient_progress["current"]`` in the tracker's shape.

    Same rules as ``ProgressTracker.snapshot`` and as the transient endpoint's
    own arithmetic — ETA unknown (0) before the first completed frame, because a
    single-sample extrapolation off the mesh build CLIMBS with elapsed time.
    """
    running = bool(p.get("running"))
    step = int(p.get("step", 0) or 0)
    total = int(p.get("total", 0) or 0)
    ts_start = float(p.get("ts_start", 0.0) or 0.0)
    elapsed = (max(time.time() - ts_start, 0.0) if (running and ts_start > 0)
               else float(p.get("elapsed_s", 0.0) or 0.0))
    if running and step <= 0:
        per_step = eta = frac = 0.0
    else:
        per_step = (elapsed / step) if step > 0 else 0.0
        eta = (per_step * max(0, total - step)) if running else 0.0
        frac = (step / total) if total > 0 else 0.0
    return {
        "running": running, "step": step, "total": total,
        "elapsed_s": round(float(elapsed), 1), "eta_s": round(float(eta), 1),
        "per_step_s": round(float(per_step), 2), "frac": round(float(frac), 3),
        "phase": str(p.get("phase", "") or ""),
        "composition": str(p.get("composition", "") or ""),
        "ts_start": float(ts_start),
    }


def _route_matches(entry_route: str, want: str) -> bool:
    """Does this entry belong to the router asking?

    A job's ``kind`` is dotted — ``"mechanical.rotor_stress"``,
    ``"thermal.duty_cycle"`` — because that is what ``GET /api/jobs`` has to
    print, while ``GET /api/mechanical/progress`` asks about the ROUTER.  So the
    router name matches its own kinds by prefix.  Without this the no-argument
    poll fell through to the router's idle default tracker after every queued
    solve, which looks exactly like a solve that never reported anything.
    """
    if not want:
        return True
    e = str(entry_route or "")
    return e == want or e.startswith(want + ".")


class ProgressRegistry:
    """run id → :class:`_Entry`, TTL-evicted.

    Deliberately NOT per workspace.  A run id is unique across the server (it is
    a uuid stem, or whatever nonce the client sent), the entries are tiny, and
    keeping ONE map is what lets ``GET /api/jobs`` answer for every route at
    once.  Isolation is by the OWNER field, checked where it matters — on the
    listing and on cancel — rather than by having four maps that each have to be
    searched anyway.
    """

    def __init__(self, ttl_s: Optional[float] = None,
                 cap: int = DEFAULT_CAP) -> None:
        self._lock = threading.RLock()
        self._e: "OrderedDict[str, _Entry]" = OrderedDict()
        self._ttl = ttl_s
        self._cap = int(cap)

    @property
    def ttl_s(self) -> float:
        return _ttl_from_env() if self._ttl is None else float(self._ttl)

    # ── eviction ─────────────────────────────────────────────────────────────
    def sweep(self) -> int:
        """Drop expired entries.  A RUNNING one is never expired.

        Called on every lookup (it is a walk of at most ``cap`` slots holding a
        dozen scalars each) rather than from a timer thread: a background
        sweeper is one more thing to shut down, and progress entries only ever
        appear as a side effect of somebody asking about them.
        """
        now = time.time()
        ttl = self.ttl_s
        dropped = 0
        with self._lock:
            for rid in [k for k, e in self._e.items()
                        if not e.running and not e.is_default
                        and (now - e.touched) > ttl]:
                self._e.pop(rid, None)
                dropped += 1
            while len(self._e) > self._cap:
                # Oldest first, and never a live one — a full registry must
                # drop history, not the bar somebody is watching.
                victim = next((k for k, e in self._e.items()
                               if not e.running and not e.is_default), None)
                if victim is None:
                    break
                self._e.pop(victim, None)
                dropped += 1
        return dropped

    # ── lookup ───────────────────────────────────────────────────────────────
    def entry(self, run_id: str, *, create: bool = False, route: str = "",
              owner: str = "", ws_id: str = "",
              is_default: bool = False) -> Optional[_Entry]:
        rid = str(run_id or "")
        if not rid:
            return None
        self.sweep()
        with self._lock:
            e = self._e.get(rid)
            if e is None and create:
                e = _Entry(rid, route=route, owner=owner, ws_id=ws_id,
                           is_default=is_default)
                self._e[rid] = e
            if e is not None:
                if route and not e.route:
                    e.route = str(route)
                if owner and not e.owner:
                    e.owner = str(owner)
                if ws_id and not e.ws_id:
                    e.ws_id = str(ws_id)
                e.touch()
                self._e.move_to_end(rid)
            return e

    def tracker(self, run_id: str, **kw: Any) -> ProgressTracker:
        e = self.entry(run_id, create=True, **kw)
        return e.tracker if e is not None else ProgressTracker()

    def get(self, run_id: str) -> Optional[ProgressTracker]:
        e = self.entry(run_id)
        return e.tracker if e is not None else None

    def newest(self, route: str = "", owner: str = "",
               include_default: bool = True) -> Optional[_Entry]:
        """The caller's most recent run in ``route`` — what a no-arg poll means.

        A RUNNING entry always beats a finished one however old; among equals,
        the most recently touched.  That is what makes ``GET /progress`` with no
        argument keep meaning what it meant to today's frontend while a second
        account is solving something else in the same router.
        """
        self.sweep()
        with self._lock:
            cands = [e for e in self._e.values()
                     if _route_matches(e.route, route)
                     and (not owner or not e.owner or e.owner == owner)
                     and (include_default or not e.is_default)]
        if not cands:
            return None
        return max(cands, key=lambda e: (1 if e.running else 0,
                                         e.raw.get("ts_start", 0.0) or 0.0,
                                         e.touched))

    def list_for_owner(self, owner: str = "", is_admin: bool = False
                       ) -> "List[_Entry]":
        self.sweep()
        with self._lock:
            return [e for e in reversed(self._e.values())
                    if not e.is_default
                    and (is_admin or not owner or e.owner == owner)]

    def clear(self) -> None:
        with self._lock:
            self._e.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._e)


# ── the process registry ─────────────────────────────────────────────────────

_REGISTRY: Optional[ProgressRegistry] = None
_REG_GUARD = threading.Lock()


def registry() -> ProgressRegistry:
    global _REGISTRY
    with _REG_GUARD:
        if _REGISTRY is None:
            _REGISTRY = ProgressRegistry()
        return _REGISTRY


def reset_registry(reg: Optional[ProgressRegistry] = None) -> ProgressRegistry:
    """Replace the process registry — tests, and nothing else."""
    global _REGISTRY
    with _REG_GUARD:
        _REGISTRY = reg if reg is not None else ProgressRegistry()
        return _REGISTRY


# ── the run in flight ────────────────────────────────────────────────────────

_RUN: "ContextVar[Optional[tuple]]" = ContextVar("motor_ai_sim_progress_run",
                                                 default=None)


def current_run() -> Optional[str]:
    """The run id this call is reporting progress for, or ``None``."""
    v = _RUN.get()
    return v[0] if v else None


@contextmanager
def use_run(run_id: str, route: str = "", owner: str = "",
            ws_id: str = "") -> Iterator[Optional[str]]:
    """Inside this block every ``_progress`` in every router means THIS run.

    Set by ``jobs``' admission around a whole solve, so a route does not have to
    thread a run id through thirty call frames to the solver that fills the bar.
    """
    rid = str(run_id or "")
    if not rid:
        yield None
        return
    registry().entry(rid, create=True, route=route, owner=owner, ws_id=ws_id)
    token = _RUN.set((rid, route, owner, ws_id))
    try:
        yield rid
    finally:
        _RUN.reset(token)


def _default_key(route: str) -> str:
    """The per-(route, workspace) default entry — today's module global.

    Keyed with the workspace because Stage 3's whole point is that two accounts
    do not share a store; keyed with a prefix that cannot collide with a run id
    because the two live in one map.
    """
    try:
        from motor_ai_sim import workspace as _WSP
        ws = _WSP.workspace().id
    except Exception:                                   # noqa: BLE001
        ws = "process"
    return "default:%s:%s" % (route, ws)


def _resolve(route: str) -> _Entry:
    """The entry the module-level ``_progress`` of ``route`` means right now."""
    reg = registry()
    rid = current_run()
    if rid:
        e = reg.entry(rid, create=True, route=route)
        if e is not None:
            return e
    key = _default_key(route)
    e = reg.entry(key, create=True, route=route, is_default=True)
    return e if e is not None else _Entry(key, route=route, is_default=True)


class RouteProgress:
    """The module-level name of a router's tracker, resolved per run.

    Forwards the whole :class:`ProgressTracker` surface the routers use.  It is
    a proxy and not a ``__getattr__`` for the reason ``workspace.StateMapping``
    gives at length: the routers touch this name from INSIDE their own module,
    by plain global lookup, which never consults a module ``__getattr__``.
    """

    __slots__ = ("_route",)

    def __init__(self, route: str) -> None:
        self._route = str(route)

    @property
    def route(self) -> str:
        return self._route

    @property
    def entry(self) -> _Entry:
        return _resolve(self._route)

    @property
    def target(self) -> ProgressTracker:
        return _resolve(self._route).tracker

    # ── ProgressTracker, forwarded ───────────────────────────────────────────
    def start(self, total: int, phase: str, composition: str = "",
              kind: Optional[str] = None) -> None:
        e = _resolve(self._route)
        e.touch()
        e.tracker.start(total, phase, composition, kind)

    def update(self, done: Optional[int] = None, total: Optional[int] = None,
               phase: Optional[str] = None,
               composition: Optional[str] = None) -> None:
        self.target.update(done=done, total=total, phase=phase,
                           composition=composition)

    def finish(self) -> None:
        e = _resolve(self._route)
        e.touch()
        e.tracker.finish()

    def callback(self) -> ProgressCallback:
        # Bound to the tracker of the run in flight AT WIRE-UP TIME, on purpose:
        # the callback is handed to a solver that may fire it from a worker
        # thread, where the ContextVar is not set.
        return self.target.callback()

    def snapshot(self) -> Dict[str, Any]:
        return self.target.snapshot()

    @property
    def kind(self) -> str:
        return self.target.kind

    @property
    def running(self) -> bool:
        return self.target.running

    def __repr__(self) -> str:                          # pragma: no cover
        return "<RouteProgress %s -> %s>" % (self._route, _resolve(self._route).run_id)


class RunStateMap:
    """A module-level dict whose CONTENTS belong to the run this call is inside.

    ``routes/static3d.py``'s ``_solve_state`` is a plain dict of a dozen keys in
    its own shape (``stem``, ``fidelity``, ``quote_s``, ``cancel``…), written
    from a background worker and read by two endpoints.  It was ONE slot for the
    whole server: a second account starting a 3-D solve got 409 because somebody
    else's was running, and its cancel flag stopped that stranger's run.

    Forwarding the dict surface (``[...]``, ``.get``, ``.update``, ``dict(...)``)
    keeps all ten call sites exactly as they are.
    """

    __slots__ = ("_route", "_seed")

    def __init__(self, route: str, seed: Callable[[], Dict[str, Any]]) -> None:
        self._route = str(route)
        self._seed = seed

    @property
    def target(self) -> Dict[str, Any]:
        e = _resolve(self._route)
        d = e.extra.get(self._route)
        if d is None:
            d = self._seed()
            e.extra[self._route] = d
        return d

    def for_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """That run's state, or ``None`` if the registry never saw it."""
        e = registry().entry(str(run_id or ""))
        return None if e is None else e.extra.get(self._route)

    def __getitem__(self, key):
        return self.target[key]

    def __setitem__(self, key, value) -> None:
        self.target[key] = value

    def __delitem__(self, key) -> None:
        del self.target[key]

    def __iter__(self):
        return iter(self.target)

    def __len__(self) -> int:
        return len(self.target)

    def __contains__(self, key) -> bool:
        return key in self.target

    def get(self, key, default=None):
        return self.target.get(key, default)

    def update(self, *a, **kw) -> None:
        self.target.update(*a, **kw)

    def keys(self):
        return self.target.keys()

    def values(self):
        return self.target.values()

    def items(self):
        return self.target.items()

    def copy(self) -> Dict[str, Any]:
        return dict(self.target)

    def __repr__(self) -> str:                          # pragma: no cover
        return "<RunStateMap %s %r>" % (self._route, self.target)


def route_progress(route: str) -> RouteProgress:
    """Declare a router's progress name: ``_progress = route_progress("thermal")``."""
    return RouteProgress(route)


def poll(route: str, run_id: str = "") -> Dict[str, Any]:
    """What ``GET /api/<route>/progress`` answers.

    * ``?run_id=`` — THAT run, whoever started it (a progress counter is a
      status read, like the three routes that were already ungated);
    * no argument — the CALLER's newest run in this router, falling back to the
      router's default tracker.  That fall-back is the compatibility promise:
      today's frontend polls with no argument and must keep working.

    The key set is INVARIANT (``tests/test_progress_routes.py`` asserts equality
    on it) — with one deliberate exception: while a run is WAITING for a queue
    slot, and only then, ``queued`` and ``position`` are added.  A strip that
    knows about them says "queued, position 3"; one that does not renders the
    idle bar it would have rendered anyway.
    """
    rid = str(run_id or "").strip()
    if rid:
        e = registry().entry(rid)
        if e is None:
            out = _idle_transient()
            out = _snapshot_of_raw(out)
            out["kind"] = ""
            out["phase"] = "unknown run"
            return _with_queue(out, rid)
    else:
        owner = ""
        try:
            from motor_ai_sim import jobs as _jobs
            owner = _jobs.current_owner()
        except Exception:                               # noqa: BLE001
            owner = ""
        e = registry().newest(route=route, owner=owner) or _resolve(route)
        rid = "" if e.is_default else e.run_id
    out = dict(e.snapshot())
    out["kind"] = e.kind
    return _with_queue(out, rid)


def transient_dict(route: str, run_id: str = "") -> "tuple":
    """``(the run's legacy progress dict, its run id)`` for the poll endpoint.

    ``routes/simulation.py``'s progress route does its own arithmetic on this
    dict (it predates :class:`ProgressTracker` and is byte-for-byte what the
    frontend has parsed for months).  This only answers WHICH dict.
    """
    rid = str(run_id or "").strip()
    if rid:
        e = registry().entry(rid)
        return ((e.raw if e is not None else _idle_transient()), rid)
    owner = ""
    try:
        from motor_ai_sim import jobs as _jobs
        owner = _jobs.current_owner()
    except Exception:                                   # noqa: BLE001
        owner = ""
    e = registry().newest(route=route, owner=owner) or _resolve(route)
    return (e.raw, "" if e.is_default else e.run_id)


def _with_queue(out: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Add ``queued``/``position`` — ONLY while the job is actually waiting."""
    if not run_id:
        return out
    try:
        from motor_ai_sim import jobs as _jobs
        rec = _jobs.queue().status(run_id)
        if rec is not None and rec.state == _jobs.JobState.QUEUED:
            out["queued"] = True
            out["position"] = int(rec.position)
    except Exception:                                   # noqa: BLE001
        pass
    return out


class TransientProgressMap:
    """The module-level name of ``_fem_transient_progress``.

    It behaves exactly like ``{"current": {...}}`` — ``["current"]``,
    ``.get("current", {})``, ``["current"]["step"] = 4``, and assignment of a
    whole fresh dict — while the dict it hands back is the one belonging to the
    RUN this call is inside.  Twelve call sites in ``routes/simulation.py``, and
    not one of them changes.
    """

    __slots__ = ("_route",)

    def __init__(self, route: str = "transient") -> None:
        self._route = str(route)

    @property
    def entry(self) -> _Entry:
        return _resolve(self._route)

    def __getitem__(self, key: str) -> Dict[str, Any]:
        if key != "current":
            raise KeyError(key)
        return _resolve(self._route).raw

    def __setitem__(self, key: str, value: Dict[str, Any]) -> None:
        if key != "current":
            raise KeyError(key)
        e = _resolve(self._route)
        e.touch()
        e.raw = dict(value)

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        return key == "current"

    def __iter__(self):
        return iter(("current",))

    def __len__(self) -> int:
        return 1

    def keys(self):
        return ("current",)

    def values(self):
        return (self["current"],)

    def items(self):
        return (("current", self["current"]),)

    def __repr__(self) -> str:                          # pragma: no cover
        return "<TransientProgressMap %r>" % (self["current"],)
