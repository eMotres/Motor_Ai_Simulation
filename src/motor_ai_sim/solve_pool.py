"""Process-level solve scheduler: independent solves in worker processes.

WHY
===
The 2-D FEM solve (P2, BDF2 eddy, Newton B-H, PARDISO) scales poorly with
threads.  Measured on the AX42 (docs/GPU_TDM_STUDY_2026-09-29.md, section 3):
six single-thread processes give 1.7-1.8x (LU) and 2.65-2.7x (Cholesky) the
solve throughput of one six-thread process on the same six cores.  The job
queue (:mod:`motor_ai_sim.jobs`) decides WHO runs; this module decides WHERE
and HOW WIDE: every independent solve (an optimizer or sweep eval, a
Simulation run, a duty recompute, several users' jobs) runs in its own worker
process, taken from a bounded pool.

OPT-IN
------
``SOLVE_POOL=1`` turns it on; anything else (the default) keeps today's
in-process path — behaviourally equivalent, not literally the same code: the
route calls this module's drop-in, which forwards to the in-process solver
with the same arguments, and picks the old process-wide transient lock.
A pool child never pools again
(``SOLVE_POOL_CHILD=1`` is set in its environment).

THE POOL
--------
* ``QUEUE_PROCS`` worker slots, default ``physical cores - 2`` (6 on the AX42).
* A slot is only granted while the free RAM covers one more solve:
  ``available - reserve - (what running children have not yet grown into)
  >= per-solve RSS estimate``.  The estimate is ``SOLVE_POOL_RSS_MB`` when set,
  else the largest peak RSS of the last finished children (>= 3 samples), else
  :data:`DEFAULT_RSS_MB`.  One solve is always allowed (no deadlock).
* A worker is a fresh interpreter per solve (``python -m
  motor_ai_sim.solve_pool_child``): the BLAS/MKL thread count is fixed in its
  environment before numpy is imported, the memory goes back to the OS when it
  exits, and a native crash takes down one solve, not the API.

THE LOAD RULE (decided at dispatch, re-decided whenever the load changes)
------------------------------------------------------------------------
``threads = clamp(procs // n_active, 1, solo)`` where ``n_active`` counts the
running and the waiting solves.  Alone -> one job x ``solo`` threads
(``SOLVE_POOL_SOLO_THREADS``, default ``min(procs, 4)``: wider was measured
slower, see :data:`DEFAULT_SOLO_MAX`); pool full or a queue behind it -> N
jobs x 1 thread.
In between the idle cores are split evenly instead of left idle.  A running
worker that speaks the child protocol is re-threaded (threadpoolctl) at its
next solver callback, or from its control thread after
``SOLVE_POOL_RETHREAD_S`` (10 s) if no callback came, and CONFIRMS the new
width; until it has, its old width stays reserved in the budget.  A plain
command (the optimizer's ``refine_proc``) or a caller that pinned its thread
count keeps what it got and is counted by that width, so the pool is never
oversubscribed by it.

FAIRNESS
--------
Waiting solves are ordered by (effective priority, solves the owner has
RUNNING, solves the owner STARTED in the last ``SOLVE_POOL_FAIR_WINDOW_S``,
arrival).  The effective priority is the job's
:class:`~motor_ai_sim.jobs.Priority` minus one class per
``SOLVE_POOL_AGING_S`` (300 s) waited, so a campaign behind a steady stream of
interactive runs is promoted instead of starved.

CANCEL, CRASH, CPU ACCOUNTING
-----------------------------
* The owning thread polls every :data:`POLL_S`; a cancelled job
  (``jobs.cancel_run``) or a cancelled tag (:func:`cancel_tag`) kills the
  child's whole process tree at once.  Only the pool kills and reaps its
  workers: each is the leader of its own tree (a Windows Job Object with
  kill-on-close, a POSIX session / process group), and whatever is left of
  the tree is killed after the leader exits, normally or not.
* A child that exits without an answer (OOM kill, segfault) raises
  :class:`SolveWorkerDied` with the exit code decoded and the stderr tail.
* Each child's CPU time is reported to :mod:`motor_ai_sim.job_usage` under the
  run and owner it solves for, so Admin -> Overview live load stays per user.
* Children run at BelowNormal priority on Windows and ``nice 10`` on Linux
  (``SOLVE_POOL_NICE``).
"""
from __future__ import annotations

import contextlib
import itertools
import logging
import os
import pickle
import queue as _queue_mod
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

__all__ = [
    "ENV_ENABLE", "ENV_PROCS", "ENV_SOLO", "ENV_RSS", "ENV_RESERVE", "ENV_CHILD",
    "ENV_NICE", "enabled", "default_procs", "physical_cores", "SolvePool",
    "Ticket", "pool", "reset_pool", "call", "run_command", "cancel_tag",
    "snapshot", "em_transient_eval", "SolveWorkerError", "SolveWorkerDied",
    "NotPoolable", "THREAD_ENV_KEYS",
]

ENV_ENABLE = "SOLVE_POOL"
ENV_PROCS = "QUEUE_PROCS"
ENV_SOLO = "SOLVE_POOL_SOLO_THREADS"
ENV_RSS = "SOLVE_POOL_RSS_MB"
ENV_RESERVE = "SOLVE_POOL_RAM_RESERVE_MB"
ENV_CHILD = "SOLVE_POOL_CHILD"
ENV_NICE = "SOLVE_POOL_NICE"
#: Hard floor of free RAM (MB) below which NO solve starts, the first included.
ENV_RAM_FLOOR = "SOLVE_POOL_RAM_FLOOR_MB"
#: ``1`` = constrained deployment: when free RAM cannot be read, run one solve
#: at a time instead of assuming there is room.
ENV_RAM_STRICT = "SOLVE_POOL_RAM_STRICT"
#: Seconds a waiting solve needs to climb one priority class (aging).
ENV_AGING = "SOLVE_POOL_AGING_S"
#: Window (s) of the per-owner "recently served" fairness counter.
ENV_FAIR_WINDOW = "SOLVE_POOL_FAIR_WINDOW_S"
#: Seconds a child may take to apply a width change at a safe point before it
#: applies it from its control thread (the reservation is kept until then).
ENV_RETHREAD = "SOLVE_POOL_RETHREAD_S"

#: Fallback per-solve RSS before any child has been measured.  A 40 mm march
#: child peaked at ~355 MB on the AX42 (docs/SOLVE_POOL_2026-09-29.md).  The
#: time-periodic eddy solve (eddy_method="tdm", the default since 2026-09-30)
#: keeps one factor per frame of the (full, since the Codex review) period:
#: peak RSS 1.6 GB (Ø40), 2.2-2.3 GB (L13), 4.5 GB (L155) measured against the
#: march's 0.4-0.75 GB (docs/TDM_PROTOTYPE_2026-09-30.md §3.8) — 4.6 GB covers
#: the largest; six parallel solves (28 GB) fit the API container's 40 GB with
#: the reserve.  After three finished children the measured peaks take over.
DEFAULT_RSS_MB = 4600
DEFAULT_RESERVE_MB = 2048
#: Widest a lone solve gets by default.  Measured on the AX42 (40 mm static,
#: docs/SOLVE_POOL_2026-09-29.md): 1 thread 60 s, 4 threads 61 s, 6 threads
#: 64 s, 8 threads 171 s — past four the solve only gets slower.
DEFAULT_SOLO_MAX = 4
DEFAULT_RAM_FLOOR_MB = 1024
DEFAULT_AGING_S = 300.0
DEFAULT_FAIR_WINDOW_S = 1800.0
DEFAULT_RETHREAD_S = 10.0
#: How often the owning thread looks at its child (cancel, liveness, CPU).
POLL_S = 0.2
#: CPU / RSS sampling period of a running child.
SAMPLE_S = 1.0

THREAD_ENV_KEYS = ("MKL_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                   "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")

CHILD_MODULE = "motor_ai_sim.solve_pool_child"


class SolveWorkerError(RuntimeError):
    """The child raised an exception that could not be re-raised as itself."""

    def __init__(self, type_name: str, message: str, remote_tb: str = "") -> None:
        super().__init__("%s: %s" % (type_name, message))
        self.type_name = type_name
        self.remote_traceback = remote_tb


class SolveWorkerDied(RuntimeError):
    """The worker process ended without an answer (OOM kill, segfault, ...)."""


class NotPoolable(Exception):
    """This call cannot run in a child (an argument does not pickle, a
    diagnostic capture is active).  The caller runs it in-process."""


# ─────────────────────────────────────────────────────────────────────────────
#  Knobs (read per call, so a test can move them)
# ─────────────────────────────────────────────────────────────────────────────

def _truthy(v: Optional[str]) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    """Is the pool on for THIS process?  Never inside a pool child."""
    if _truthy(os.environ.get(ENV_CHILD)):
        return False
    return _truthy(os.environ.get(ENV_ENABLE))


def _cgroup_cpu_quota() -> Optional[float]:
    try:
        with open("/sys/fs/cgroup/cpu.max") as fh:
            q, p = fh.read().split()[:2]
        if q != "max":
            return max(1.0, float(q) / float(p))
    except (OSError, ValueError):
        pass
    return None


def physical_cores() -> int:
    """Physical cores this process may use (affinity and cgroup quota), >= 1.

    The same rule as ``routes.optimization._physical_cores_available``: on
    Linux the allowed CPUs are mapped to distinct (package, core) pairs, so a
    container pinned to ``0-11`` on an 8-core / 16-thread AX42 counts 8.
    """
    n: Optional[int] = None
    try:
        allowed = sorted(os.sched_getaffinity(0))       # Linux only
    except (AttributeError, OSError):
        allowed = None
    if allowed:
        try:
            cores = set()
            for c in allowed:
                base = "/sys/devices/system/cpu/cpu%d/topology/" % int(c)
                with open(base + "core_id") as fh:
                    core = fh.read().strip()
                with open(base + "physical_package_id") as fh:
                    pkg = fh.read().strip()
                cores.add((pkg, core))
            n = len(cores) or None
        except OSError:
            n = None
        if n is None:
            n = len(allowed) // 2 if len(allowed) > 4 else len(allowed)
    if n is None:
        try:
            import psutil
            n = psutil.cpu_count(logical=False) or None
        except Exception:                               # noqa: BLE001
            n = None
    if n is None:
        logical = os.cpu_count() or 4
        n = logical // 2 if logical > 4 else logical
    q = _cgroup_cpu_quota()
    if q:
        n = min(n, max(1, int(q)))
    return max(1, int(n))


def default_procs() -> int:
    """``QUEUE_PROCS``, else ``max(1, physical cores - 2)``."""
    n = _int_env(ENV_PROCS, 0)
    return n if n > 0 else max(1, physical_cores() - 2)


# ─────────────────────────────────────────────────────────────────────────────
#  The ticket and the scheduler (pure bookkeeping, unit-testable)
# ─────────────────────────────────────────────────────────────────────────────

WAITING, RUNNING, DONE, CANCELLED = "waiting", "running", "done", "cancelled"


@dataclass(eq=False)
class Ticket:
    """One solve's claim on the pool."""

    owner: str = ""
    ws_id: str = ""
    run_id: str = ""
    priority: int = 2
    #: None = the load rule decides and may re-decide (the child can re-thread);
    #: an int = the caller asked for exactly up to that many, fixed at dispatch.
    pinned: Optional[int] = None
    #: False for a plain command that cannot be re-threaded once running.
    retunable: bool = True
    tag: str = ""
    label: str = ""
    client: str = "web"
    seq: int = 0
    state: str = WAITING
    threads: int = 0
    #: The thread count the owning thread still has to send to the child.
    pending_threads: Optional[int] = None
    #: The width the child has CONFIRMED it runs (its dispatch width until the
    #: first ``threads_ack``).  The budget reserves max(threads, acked_threads)
    #: so a narrowing is not counted before the child has actually narrowed.
    acked_threads: int = 0
    #: When the current, not yet acknowledged width change was requested.
    rethread_at: float = 0.0
    pid: int = 0
    queued_at: float = 0.0
    started_at: float = 0.0
    peak_rss: int = 0
    cancel_requested: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def fixed(self) -> bool:
        """Its width cannot change after dispatch."""
        return self.pinned is not None or not self.retunable

    def public(self) -> Dict[str, Any]:
        now = time.time()
        return {"owner": self.owner, "run_id": self.run_id, "label": self.label,
                "priority": self.priority, "state": self.state,
                "threads": self.threads, "pid": self.pid,
                "waited_s": round((self.started_at or now) - self.queued_at, 1),
                "elapsed_s": round(now - self.started_at, 1) if self.started_at else 0.0,
                "peak_rss": self.peak_rss}


def _cgroup_mem_available(base: str = "/sys/fs/cgroup") -> Optional[int]:
    """Bytes left under this container's memory limit (cgroup v2), or None.

    psutil reports the HOST's memory, and the API container is capped below it
    (40 GB of 64 on the AX42): the limit is what an out-of-memory kill obeys.
    Reclaimable page cache (``inactive_file``) does not count as used.
    """
    try:
        with open(os.path.join(base, "memory.max")) as fh:
            lim = fh.read().strip()
        if lim == "max":
            return None
        with open(os.path.join(base, "memory.current")) as fh:
            cur = int(fh.read().strip())
        inactive = 0
        with contextlib.suppress(OSError, ValueError):
            with open(os.path.join(base, "memory.stat")) as fh:
                for line in fh:
                    if line.startswith("inactive_file "):
                        inactive = int(line.split()[1])
                        break
        return max(0, int(lim) - max(0, cur - inactive))
    except (OSError, ValueError):
        return None


def _cgroup_memory_events(base: str = "/sys/fs/cgroup") -> Dict[str, int]:
    """``memory.events`` of this cgroup (``oom_kill``, ``max``, ``high`` ...).

    Read around every child, so one the kernel killed for memory is reported
    as exactly that, and shown in :meth:`SolvePool.snapshot`.  ``{}`` outside
    cgroup v2."""
    out: Dict[str, int] = {}
    try:
        with open(os.path.join(base, "memory.events")) as fh:
            for line in fh:
                k, _, v = line.strip().partition(" ")
                with contextlib.suppress(ValueError):
                    out[k] = int(v)
    except OSError:
        pass
    return out


def _float_env(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def _mem_available() -> Optional[int]:
    avail: Optional[int] = None
    try:
        import psutil
        avail = int(psutil.virtual_memory().available)
    except Exception:                                   # noqa: BLE001
        avail = None
    cg = _cgroup_mem_available()
    if cg is not None:
        avail = cg if avail is None else min(avail, cg)
    return avail


def _proc_rss(pid: int) -> int:
    try:
        import psutil
        return int(psutil.Process(pid).memory_info().rss)
    except Exception:                                   # noqa: BLE001
        return 0


class SolvePool:
    """Slots, ordering, the load rule and the RAM cap.  No processes here:
    :func:`call` / :func:`run_command` own the children and ask this object
    for admission, so the scheduling rules are testable without spawning.

    ADMISSION IS NOT AN OOM GUARANTEE.  The RAM test is a point-in-time
    estimate: it keeps the pool from starting a solve that obviously does not
    fit, and the hard floor (``SOLVE_POOL_RAM_FLOOR_MB``) keeps even the first
    solve from starting on an exhausted box.  A child that grows past what is
    left can still be killed; it is then reported as such (``memory.events``).
    """

    def __init__(self, procs: Optional[int] = None, solo: Optional[int] = None,
                 rss_mb: Optional[float] = None, reserve_mb: Optional[float] = None,
                 mem_available: Optional[Callable[[], Optional[int]]] = None,
                 child_rss: Optional[Callable[[Ticket], int]] = None,
                 floor_mb: Optional[float] = None, strict_ram: Optional[bool] = None,
                 aging_s: Optional[float] = None,
                 fair_window_s: Optional[float] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self._cv = threading.Condition()
        self._procs = int(procs) if procs else 0
        self._solo = int(solo) if solo else 0
        self._rss_mb = float(rss_mb) if rss_mb else 0.0
        self._reserve_mb = float(reserve_mb) if reserve_mb is not None else -1.0
        self._floor_mb = float(floor_mb) if floor_mb is not None else -1.0
        self._strict = strict_ram
        self._aging_s = float(aging_s) if aging_s is not None else -1.0
        self._fair_window_s = float(fair_window_s) if fair_window_s is not None else -1.0
        self._mem_available = mem_available or _mem_available
        self._child_rss = child_rss or (lambda t: _proc_rss(t.pid) if t.pid else 0)
        self._clock = clock or time.time
        self._waiting: List[Ticket] = []
        self._running: List[Ticket] = []
        #: owner -> start times inside the fairness window (rolling, not lifetime)
        self._served: Dict[str, Deque[float]] = {}
        self._seq = itertools.count(1)
        self._peaks: Deque[int] = deque(maxlen=20)
        self._floor_logged = 0.0
        self.started_total = 0
        self.finished_total = 0

    # ── knobs ────────────────────────────────────────────────────────────────
    @property
    def procs(self) -> int:
        return self._procs if self._procs > 0 else default_procs()

    @property
    def solo(self) -> int:
        if self._solo > 0:
            return self._solo
        s = _int_env(ENV_SOLO, 0)
        return s if s > 0 else min(self.procs, DEFAULT_SOLO_MAX)

    def rss_estimate(self) -> int:
        """Bytes one more solve is expected to need."""
        if self._rss_mb > 0:
            return int(self._rss_mb * 2 ** 20)
        env = _int_env(ENV_RSS, 0)
        if env > 0:
            return env * 2 ** 20
        if len(self._peaks) >= 3:
            return int(max(self._peaks) * 1.15)
        return DEFAULT_RSS_MB * 2 ** 20

    def reserve(self) -> int:
        if self._reserve_mb >= 0:
            return int(self._reserve_mb * 2 ** 20)
        return _int_env(ENV_RESERVE, DEFAULT_RESERVE_MB) * 2 ** 20

    def floor(self) -> int:
        """Free RAM below which nothing starts, not even the first solve."""
        if self._floor_mb >= 0:
            return int(self._floor_mb * 2 ** 20)
        return int(_float_env(ENV_RAM_FLOOR, DEFAULT_RAM_FLOOR_MB) * 2 ** 20)

    def strict_ram(self) -> bool:
        return bool(self._strict) if self._strict is not None \
            else _truthy(os.environ.get(ENV_RAM_STRICT))

    def aging_s(self) -> float:
        return self._aging_s if self._aging_s > 0 else \
            max(1.0, _float_env(ENV_AGING, DEFAULT_AGING_S))

    def fair_window_s(self) -> float:
        return self._fair_window_s if self._fair_window_s > 0 else \
            max(1.0, _float_env(ENV_FAIR_WINDOW, DEFAULT_FAIR_WINDOW_S))

    # ── the rules ────────────────────────────────────────────────────────────
    def effective_priority(self, t: Ticket, now: Optional[float] = None) -> int:
        """Priority minus one class per ``aging_s`` waited: a campaign behind
        a steady stream of interactive runs is promoted, never starved."""
        now = self._clock() if now is None else now
        waited = max(0.0, now - (t.queued_at or now))
        return max(0, int(t.priority) - int(waited // self.aging_s()))

    def _served_recent(self, owner: str, now: float) -> int:
        """Solves ``owner`` STARTED inside the fairness window (rolling: old
        history stops counting instead of dominating for ever)."""
        dq = self._served.get(owner)
        if not dq:
            return 0
        horizon = now - self.fair_window_s()
        while dq and dq[0] < horizon:
            dq.popleft()
        return len(dq)

    def _order_key(self, t: Ticket):
        now = self._clock()
        running_by_owner = sum(1 for r in self._running if r.owner == t.owner)
        return (self.effective_priority(t, now), running_by_owner,
                self._served_recent(t.owner, now), t.seq)

    def _weight(self, t: Ticket) -> int:
        """Cores a RUNNING ticket holds.  A fixed-width command: its width.  A
        re-tunable child: the wider of what it was told and what it has
        CONFIRMED — a narrowing frees cores only once the child says so."""
        if t.fixed:
            return max(1, int(t.threads))
        return max(1, int(t.threads), int(t.acked_threads or 0))

    def _share(self, n_active: int) -> int:
        return max(1, min(self.solo, self.procs // max(1, int(n_active))))

    def _threads_for(self, t: Ticket, n_active: int) -> int:
        share = self._share(n_active)
        if t.pinned is not None:
            return max(1, min(int(t.pinned), share))
        return share

    def _ram_headroom(self) -> Optional[int]:
        avail = self._mem_available()
        if avail is None:
            return None
        est = self.rss_estimate()
        growing = 0
        for r in self._running:
            growing += max(0, est - int(self._child_rss(r) or 0))
        return avail - self.reserve() - growing

    def _admission_round(self) -> List[Ticket]:
        """Every waiter that may start now, best first (one whole round)."""
        used = sum(self._weight(r) for r in self._running)
        n_run = len(self._running)
        procs = self.procs
        out: List[Ticket] = []
        if not self._waiting:
            return out
        avail = self._mem_available()
        if avail is not None and avail < self.floor():
            now = self._clock()
            if now - self._floor_logged > 60.0:
                self._floor_logged = now
                log.warning("solve pool: %.0f MB free is below the %.0f MB floor "
                            "(%s): no solve starts until memory is freed",
                            avail / 2 ** 20, self.floor() / 2 ** 20, ENV_RAM_FLOOR)
            return out
        headroom = self._ram_headroom() if avail is not None else None
        strict_unknown = avail is None and self.strict_ram()
        est = self.rss_estimate()
        for t in sorted(self._waiting, key=self._order_key):
            if n_run > 0 or out:
                if used + 1 > procs:
                    break
                if strict_unknown:
                    break
                if headroom is not None and headroom < est:
                    break
            out.append(t)
            used += 1
            if headroom is not None:
                headroom -= est
            n_run += 1
        return out

    def _start_locked(self, t: Ticket) -> None:
        self._waiting.remove(t)
        n_active = len(self._running) + 1 + len(self._waiting)
        t.threads = self._threads_for(t, n_active)
        t.acked_threads = t.threads          # the width it is launched with
        t.pending_threads = None
        t.rethread_at = 0.0
        t.state = RUNNING
        t.started_at = self._clock()
        self._running.append(t)
        self._served.setdefault(t.owner, deque()).append(t.started_at)
        self.started_total += 1

    def _rebalance_locked(self) -> None:
        """Re-decide the width of every running re-tunable solve."""
        n_active = len(self._running) + len(self._waiting)
        for r in self._running:
            if r.fixed:
                continue
            want = self._threads_for(r, n_active)
            if want != r.threads:
                r.threads = want
                r.pending_threads = want
                r.rethread_at = self._clock()

    def ack_threads(self, t: Ticket, n: int) -> None:
        """The child confirms it now runs ``n`` threads: the reservation of
        its old width ends here, not when the change was requested."""
        with self._cv:
            t.acked_threads = max(1, int(n))
            if t.acked_threads == t.threads:
                t.rethread_at = 0.0
            self._cv.notify_all()

    def overdue_rethreads(self, timeout_s: float) -> List[Ticket]:
        """Running children that have not confirmed a width change in time."""
        now = self._clock()
        with self._cv:
            return [r for r in self._running
                    if r.rethread_at and r.acked_threads != r.threads
                    and now - r.rethread_at > timeout_s]

    # ── the interface the owning thread uses ─────────────────────────────────
    def submit(self, t: Ticket) -> Ticket:
        with self._cv:
            t.seq = next(self._seq)
            t.state = WAITING
            t.queued_at = t.queued_at or self._clock()
            self._waiting.append(t)
            self._rebalance_locked()
            self._cv.notify_all()
        return t

    def try_start(self, t: Ticket) -> bool:
        """Start ``t`` if it is in the current admission round."""
        with self._cv:
            if t.state == RUNNING:
                return True
            if t.state != WAITING:
                return False
            if any(c is t for c in self._admission_round()):
                self._start_locked(t)
                self._rebalance_locked()
                self._cv.notify_all()
                return True
            return False

    def acquire(self, t: Ticket, cancel_check: Optional[Callable[[], None]] = None,
                poll: float = POLL_S) -> Ticket:
        """Block until ``t`` is dispatched.  ``cancel_check`` is called every
        ``poll`` seconds and may raise; the ticket is then withdrawn."""
        if t.state != WAITING or t not in self._waiting:
            self.submit(t)
        try:
            while True:
                if t.cancel_requested:
                    raise _TagCancelled(t.tag)
                if cancel_check is not None:
                    cancel_check()
                if self.try_start(t):
                    return t
                with self._cv:
                    self._cv.wait(timeout=poll)
        except BaseException:
            self.withdraw(t)
            raise

    def withdraw(self, t: Ticket) -> None:
        with self._cv:
            if t in self._waiting:
                self._waiting.remove(t)
                t.state = CANCELLED
                self._rebalance_locked()
                self._cv.notify_all()

    def release(self, t: Ticket, state: str = DONE) -> None:
        with self._cv:
            if t in self._running:
                self._running.remove(t)
                self.finished_total += 1
                if t.peak_rss > 0 and state != CANCELLED:
                    self._peaks.append(int(t.peak_rss))
            elif t in self._waiting:
                self._waiting.remove(t)
            t.state = state
            self._rebalance_locked()
            self._cv.notify_all()

    def cancel_tag(self, tag: str) -> List[Ticket]:
        """Mark every ticket with ``tag`` cancelled; return the RUNNING ones
        (their owners kill the children at their next poll)."""
        if not tag:
            return []
        with self._cv:
            hit = [t for t in self._waiting + self._running if t.tag == tag]
            for t in hit:
                t.cancel_requested = True
            self._cv.notify_all()
            return [t for t in hit if t.state == RUNNING]

    def snapshot(self) -> Dict[str, Any]:
        with self._cv:
            running = [t.public() for t in self._running]
            waiting = [t.public() for t in sorted(self._waiting, key=self._order_key)]
            return {"enabled": enabled(), "procs": self.procs, "solo": self.solo,
                    "rss_estimate_mb": round(self.rss_estimate() / 2 ** 20, 1),
                    "ram_floor_mb": round(self.floor() / 2 ** 20, 1),
                    "memory_events": _cgroup_memory_events(),
                    "running": len(running), "waiting": len(waiting),
                    "threads_in_use": sum(self._weight(t) for t in self._running),
                    "started_total": self.started_total,
                    "finished_total": self.finished_total,
                    "items": running + waiting}


class _TagCancelled(BaseException):
    """A ticket's tag was cancelled (:func:`cancel_tag`) — internal."""


_POOL: Optional[SolvePool] = None
_POOL_GUARD = threading.Lock()


def pool() -> SolvePool:
    global _POOL
    with _POOL_GUARD:
        if _POOL is None:
            _POOL = SolvePool()
            log.info("solve pool: %d process slot(s) (%s), solo width %d",
                     _POOL.procs, ENV_PROCS, _POOL.solo)
        return _POOL


def reset_pool(p: Optional[SolvePool] = None) -> SolvePool:
    global _POOL
    with _POOL_GUARD:
        _POOL = p if p is not None else SolvePool()
        return _POOL


def snapshot() -> Dict[str, Any]:
    return pool().snapshot()


def cancel_tag(tag: str) -> int:
    """Cancel every waiting and running solve registered under ``tag``.
    Returns how many were running (their trees are killed within POLL_S)."""
    return len(pool().cancel_tag(tag))


# ─────────────────────────────────────────────────────────────────────────────
#  Who is asking (resolved on the submitting thread)
# ─────────────────────────────────────────────────────────────────────────────

def _job_identity() -> Tuple[str, str, str, int, str]:
    """``(owner, ws_id, run_id, priority, client)`` for the current call."""
    from motor_ai_sim import jobs as _J
    from motor_ai_sim import workspace as _WSP
    owner = ""
    ws_id = ""
    try:
        owner = _J.current_owner()
    except Exception:                                   # noqa: BLE001
        owner = ""
    try:
        ws_id = _WSP.workspace().id
    except Exception:                                   # noqa: BLE001
        ws_id = ""
    rid = _J.current_run_id() or ""
    prio = int(_J.Priority.DUTY)
    client = "web"
    if rid:
        try:
            rec = _J.queue().status(rid)
            if rec is not None:
                prio = int(rec.priority)
                owner = str(rec.owner or owner)
                body = rec.body if isinstance(rec.body, dict) else {}
                agent = body.get("agent") if isinstance(body.get("agent"), dict) else None
                if agent:
                    client = str(agent.get("client_name") or "agent")
        except Exception:                               # noqa: BLE001
            pass
    return owner or ws_id or "process", ws_id, rid, prio, client


def _job_cancel_check(run_id: str) -> Callable[[], None]:
    from motor_ai_sim import jobs as _J

    def _check() -> None:
        if run_id and _J.is_cancelled(run_id):
            raise _J.JobCancelled(run_id)
    return _check


# ─────────────────────────────────────────────────────────────────────────────
#  Children: spawn, priority, kill, the wire
# ─────────────────────────────────────────────────────────────────────────────

_HDR = struct.Struct(">Q")


def write_frame(fh, obj: Any) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    fh.write(_HDR.pack(len(data)))
    fh.write(data)
    fh.flush()


def read_frame(fh) -> Any:
    """One frame, or raises EOFError."""
    hdr = _read_exact(fh, _HDR.size)
    (n,) = _HDR.unpack(hdr)
    return pickle.loads(_read_exact(fh, n))


def _read_exact(fh, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = fh.read(n - len(buf))
        if not chunk:
            raise EOFError("solve pool pipe closed")
        buf += chunk
    return bytes(buf)


def _nice_level() -> int:
    return _int_env(ENV_NICE, 10)


def _child_env(base: Optional[Dict[str, str]], threads: int) -> Dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k in THREAD_ENV_KEYS:
        env[k] = str(int(threads))
    env[ENV_CHILD] = "1"
    env[ENV_NICE] = str(_nice_level())
    env.setdefault("PYTHONUNBUFFERED", "1")
    # The child imports motor_ai_sim the way this process did.
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pp = env.get("PYTHONPATH", "")
    if src not in pp.split(os.pathsep):
        env["PYTHONPATH"] = src + (os.pathsep + pp if pp else "")
    try:
        from motor_ai_sim.simulation.pardiso_runtime import pardiso_subprocess_env
        env = pardiso_subprocess_env(env)
    except Exception:                                   # noqa: BLE001
        pass
    return env


# ── Windows: every worker in its own Job Object (kill-on-close) ──────────────
# A recursive psutil snapshot followed by kills misses a descendant created
# after the snapshot.  A Job Object does not: every process the worker starts
# is in the job by construction, TerminateJobObject ends all of them at once,
# closing the last handle does the same (so an API crash takes its workers
# with it), and the job's accounting keeps the CPU time of every process that
# ever ran in it, exited grandchildren included.  The worker is created
# SUSPENDED and resumed only after it is in the job, so not even its first
# instruction can run outside it.

class _WinJob:
    """A kill-on-close Job Object holding one worker's whole tree."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self._ct = ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32 = k32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD)
        k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                  ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.c_void_p)
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _Ext(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _Io),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        class _Acct(ctypes.Structure):
            _fields_ = [("TotalUserTime", ctypes.c_int64),
                        ("TotalKernelTime", ctypes.c_int64),
                        ("ThisPeriodTotalUserTime", ctypes.c_int64),
                        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                        ("TotalPageFaultCount", wintypes.DWORD),
                        ("TotalProcesses", wintypes.DWORD),
                        ("ActiveProcesses", wintypes.DWORD),
                        ("TotalTerminatedProcesses", wintypes.DWORD)]
        self._Ext, self._Acct = _Ext, _Acct
        h = k32.CreateJobObjectW(None, None)
        if not h:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        self.handle = h
        info = _Ext()
        info.BasicLimitInformation.LimitFlags = 0x2000   # KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(h, 9, ctypes.byref(info),
                                           ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            self.close()
            raise OSError(err, "SetInformationJobObject failed")

    def assign(self, process_handle: int) -> bool:
        return bool(self.handle) and bool(
            self._k32.AssignProcessToJobObject(self.handle, int(process_handle)))

    def terminate(self) -> None:
        if self.handle:
            self._k32.TerminateJobObject(self.handle, 1)

    def active_processes(self) -> int:
        a = self._query_acct()
        return int(a.ActiveProcesses) if a is not None else 0

    def cpu_seconds(self) -> Optional[float]:
        """User + kernel CPU of every process that ever ran in the job."""
        a = self._query_acct()
        if a is None:
            return None
        return (a.TotalUserTime + a.TotalKernelTime) / 1e7

    def peak_memory(self) -> int:
        if not self.handle:
            return 0
        info = self._Ext()
        ok = self._k32.QueryInformationJobObject(self.handle, 9, self._ct.byref(info),
                                                 self._ct.sizeof(info), None)
        return int(info.PeakJobMemoryUsed) if ok else 0

    def _query_acct(self):
        if not self.handle:
            return None
        a = self._Acct()
        ok = self._k32.QueryInformationJobObject(self.handle, 1, self._ct.byref(a),
                                                 self._ct.sizeof(a), None)
        return a if ok else None

    def close(self) -> None:
        """Close the job; with KILL_ON_JOB_CLOSE anything still in it dies."""
        h, self.handle = self.handle, None
        if h:
            self._k32.CloseHandle(h)


def _win_resume(process_handle: int) -> None:
    import ctypes
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = (ctypes.c_void_p,)
    ntdll.NtResumeProcess(ctypes.c_void_p(int(process_handle)))


def _popen(argv: List[str], env: Dict[str, str], **kw):
    """Start a child at low priority, as the leader of its own process tree.

    Windows: suspended, put into a kill-on-close Job Object, then resumed.
    POSIX: a new session (the child's pid is its process-group id, so the
    whole tree can be signalled even after the leader has exited), through
    ``nice`` so every thread starts at the pool's niceness.
    Returns ``(Popen, job_or_None)``.
    """
    if os.name == "nt":
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000) \
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200) \
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        job = None
        try:
            job = _WinJob()
        except Exception as exc:                        # noqa: BLE001
            log.warning("solve pool: no Job Object (%s); tree kill falls back "
                        "to process snapshots", exc)
        if job is None:
            return subprocess.Popen(argv, env=env, creationflags=flags, **kw), None
        p = subprocess.Popen(argv, env=env, creationflags=flags | 0x4, **kw)
        try:
            if not job.assign(p._handle):
                log.warning("solve pool: could not put pid %d in its Job Object",
                            p.pid)
                job.close()
                job = None
        finally:
            _win_resume(p._handle)
        return p, job
    nice = shutil.which("nice")
    lvl = _nice_level()
    if nice and lvl > 0:
        argv = [nice, "-n", str(lvl)] + list(argv)     # nice execs: same pid
    p = subprocess.Popen(argv, env=env, start_new_session=True, **kw)
    if not nice and lvl > 0:
        with contextlib.suppress(Exception):
            os.setpriority(os.PRIO_PROCESS, p.pid, lvl)
    return p, None


def _tree_cpu_rss(pid: int) -> Tuple[float, int]:
    """CPU-seconds and RSS of ``pid`` and its live descendants.  CPU includes
    each process's REAPED children (``children_user/system``), so a helper
    that already exited still counts; a zombie leader is still readable."""
    try:
        import psutil
        p = psutil.Process(pid)
        cpu = 0.0
        rss = 0
        for q in [p] + p.children(recursive=True):
            try:
                ct = q.cpu_times()
                cpu += ct.user + ct.system + getattr(ct, "children_user", 0.0) \
                    + getattr(ct, "children_system", 0.0)
                rss += q.memory_info().rss
            except Exception:                           # noqa: BLE001
                continue
        return cpu, rss
    except Exception:                                   # noqa: BLE001
        return 0.0, 0


def describe_exit(code: Optional[int]) -> str:
    """Human words for a child's exit status."""
    if code is None:
        return "still running"
    if code < 0:
        import signal
        try:
            name = signal.Signals(-code).name
        except (ValueError, AttributeError):
            name = "signal %d" % -code
        hint = {"SIGKILL": " (killed; with no cancel this is most likely the "
                           "kernel's out-of-memory killer)",
                "SIGSEGV": " (segmentation fault in native code)",
                "SIGABRT": " (aborted by native code)",
                "SIGBUS": " (bus error in native code)"}.get(name, "")
        return "exit code %d = %s%s" % (code, name, hint)
    win = {0xC0000005: "access violation in native code",
           0xC00000FD: "stack overflow",
           0xC0000409: "stack buffer overrun / fail-fast abort",
           0xC0000017: "out of memory", 0xC000012D: "out of memory (commit limit)",
           3: "aborted by native code"}
    u = code & 0xFFFFFFFF
    if u in win:
        return "exit code %d (0x%08X = %s)" % (code, u, win[u])
    if code == 137:
        return "exit code 137 (SIGKILL from a container runtime; most likely out of memory)"
    return "exit code %d" % code


class _Child:
    """One worker: its process (tree), reader threads, samples and the ONE
    place it is killed and reaped.

    Nothing else may kill or reap a worker (``_kill_live_evals`` cancels its
    TAG in pool mode).  :meth:`finish` always runs: it takes the last CPU/RSS
    reading while the process is still observable (POSIX: a zombie, then
    ``wait4`` for the exact rusage of the child and its reaped descendants;
    Windows: the Job Object's accounting), then kills whatever is left of the
    tree (process group / Job Object) even when the leader exited normally.
    """

    def __init__(self, proc: subprocess.Popen, job, framed: bool) -> None:
        self.proc = proc
        self.job = job
        self.framed = framed
        self.msgs: "_queue_mod.Queue" = _queue_mod.Queue()
        self.stderr_tail: Deque[str] = deque(maxlen=40)
        self.stderr_all: List[str] = []
        self.stdout_all: List[str] = []
        self.keep_all_stderr = not framed
        self.cpu_s = 0.0
        self.peak_rss = 0
        self.finished = False
        self.returncode: Optional[int] = None
        self.mem_events_at_start = _cgroup_memory_events()
        self._threads: List[threading.Thread] = []
        if framed:
            self._spawn(self._read_frames)
        else:
            self._spawn(self._read_stdout_text)
        self._spawn(self._read_stderr)

    # ── io ───────────────────────────────────────────────────────────────────
    def _spawn(self, fn) -> None:
        th = threading.Thread(target=fn, daemon=True,
                              name="solve-pool-io-%d" % self.proc.pid)
        th.start()
        self._threads.append(th)

    def _read_frames(self) -> None:
        try:
            while True:
                self.msgs.put(read_frame(self.proc.stdout))
        except EOFError:
            pass
        except Exception as exc:                        # noqa: BLE001
            self.msgs.put(("protocol_error", repr(exc)))
        self.msgs.put(("eof",))

    def _read_stdout_text(self) -> None:
        try:
            for raw in iter(self.proc.stdout.readline, b""):
                self.stdout_all.append(raw.decode("utf-8", "replace"))
        except Exception:                               # noqa: BLE001
            pass
        self.msgs.put(("eof",))

    def _read_stderr(self) -> None:
        try:
            for raw in iter(self.proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace")
                self.stderr_tail.append(line.rstrip("\n"))
                if self.keep_all_stderr:
                    self.stderr_all.append(line)
        except Exception:                               # noqa: BLE001
            pass

    def join_io(self, timeout: float = 5.0) -> None:
        for th in self._threads:
            th.join(timeout=timeout)

    def stderr_last(self) -> str:
        lines = [ln for ln in self.stderr_tail if ln.strip()]
        return " | ".join(lines[-6:])[:1200] if lines else "no stderr"

    # ── liveness without reaping ─────────────────────────────────────────────
    def exited(self) -> bool:
        """Has the leader exited?  POSIX: asked with ``WNOWAIT``, so it stays
        a readable zombie until :meth:`finish` has sampled it."""
        if self.finished or self.proc.returncode is not None:
            return True
        if os.name == "nt":
            return self.proc.poll() is not None
        try:
            info = os.waitid(os.P_PID, self.proc.pid,
                             os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return True
        return info is not None

    def sample(self, t: Ticket) -> None:
        cpu, rss = _tree_cpu_rss(self.proc.pid)
        if self.job is not None:
            jc = self.job.cpu_seconds()
            if jc is not None:
                cpu = max(cpu, jc)
        self.cpu_s = max(self.cpu_s, cpu)
        if rss:
            self.peak_rss = max(self.peak_rss, rss)
            t.peak_rss = max(t.peak_rss, rss)

    # ── the one kill and the one reap ────────────────────────────────────────
    def kill(self) -> None:
        """Kill the whole tree now (idempotent)."""
        if self.job is not None:
            with contextlib.suppress(Exception):
                self.job.terminate()
        pids = []
        try:
            import psutil
            pids = psutil.Process(self.proc.pid).children(recursive=True)
        except Exception:                               # noqa: BLE001
            pids = []
        if os.name != "nt":
            with contextlib.suppress(Exception):
                import signal
                os.killpg(self.proc.pid, signal.SIGKILL)
        for k in pids:
            with contextlib.suppress(Exception):
                k.kill()
        if not self.exited():
            with contextlib.suppress(Exception):
                self.proc.kill()

    def _reap_leader(self, timeout: float) -> None:
        """Wait for the leader and collect its final accounting."""
        deadline = time.monotonic() + max(0.0, timeout)
        while not self.exited() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self.exited():
            self.kill()
            deadline = time.monotonic() + 5.0
            while not self.exited() and time.monotonic() < deadline:
                time.sleep(0.02)
        if os.name != "nt" and self.proc.returncode is None:
            # A zombie is still observable: this is the last psutil reading.
            cpu, _ = _tree_cpu_rss(self.proc.pid)
            self.cpu_s = max(self.cpu_s, cpu)
            # Leftover descendants stay in the leader's process group.  Signal
            # the GROUP while the unreaped leader still pins its id (after the
            # reap the id could be reused by an unrelated new session).
            with contextlib.suppress(Exception):
                import signal
                os.killpg(self.proc.pid, signal.SIGKILL)
            try:
                _, status, ru = os.wait4(self.proc.pid, 0)
                self.proc.returncode = os.waitstatus_to_exitcode(status)
                self.cpu_s = max(self.cpu_s, ru.ru_utime + ru.ru_stime)
                self.peak_rss = max(self.peak_rss, int(ru.ru_maxrss) * 1024)
            except ChildProcessError:
                with contextlib.suppress(Exception):
                    self.proc.wait(timeout=5)
        else:
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=5)
        self.returncode = self.proc.returncode

    def finish(self, t: Ticket, graceful_s: float = 0.0) -> None:
        """Reap the leader (after up to ``graceful_s`` of waiting; kill after),
        take the final accounting, and make sure nothing of the tree survives."""
        if self.finished:
            return
        self._reap_leader(graceful_s)
        if self.job is not None:
            jc = self.job.cpu_seconds()
            if jc is not None:
                self.cpu_s = max(self.cpu_s, jc)
            self.peak_rss = max(self.peak_rss, self.job.peak_memory())
            with contextlib.suppress(Exception):
                self.job.terminate()
            self.job.close()
        self.finished = True
        t.peak_rss = max(t.peak_rss, self.peak_rss)

    def oom_note(self) -> str:
        now = _cgroup_memory_events()
        a = self.mem_events_at_start.get("oom_kill", 0)
        b = now.get("oom_kill", 0)
        if b > a:
            return "; cgroup memory.events oom_kill %d -> %d: an out-of-memory " \
                   "kill happened while it ran" % (a, b)
        return ""


def _usage_start(t: Ticket) -> None:
    with contextlib.suppress(Exception):
        from motor_ai_sim import job_usage as _U
        _U.child_started(t.pid, t.run_id, t.owner, t.client)


def _usage_cpu(t: Ticket, cpu_s: float) -> None:
    with contextlib.suppress(Exception):
        from motor_ai_sim import job_usage as _U
        _U.child_cpu(t.pid, cpu_s)


def _usage_finish(t: Ticket, cpu_s: Optional[float]) -> None:
    with contextlib.suppress(Exception):
        from motor_ai_sim import job_usage as _U
        _U.child_finished(t.pid, cpu_s)


def rethread_timeout_s() -> float:
    return max(0.5, _float_env(ENV_RETHREAD, DEFAULT_RETHREAD_S))


# ─────────────────────────────────────────────────────────────────────────────
#  Context carried into a child (what a bare interpreter cannot know)
# ─────────────────────────────────────────────────────────────────────────────

#: ContextVars a solve reads, carried by value.  Only modules already imported
#: here are read (an unimported module's var is at its default anyway).
CARRIED_VARS: Tuple[Tuple[str, str], ...] = (
    ("motor_ai_sim.material_context", "_OVERRIDE"),
    ("motor_ai_sim.mech_losses", "BEARING_TEMP_C"),
    ("motor_ai_sim.simulation.fem_solver_2d", "_OPT_CANDIDATE"),
    ("motor_ai_sim.simulation.fem_solver_2d", "_TDM_DEMAG_REQUEST"),
    ("motor_ai_sim.simulation.fem_solver_2d", "_NO_WARM_CACHE_CTX"),
    ("motor_ai_sim.run_recording", "_RECORD"),
)


def _config_data(cfg: Any) -> Any:
    try:
        from omegaconf import OmegaConf, DictConfig
        if isinstance(cfg, DictConfig):
            return OmegaConf.to_container(cfg, resolve=True)
    except ImportError:
        pass
    return cfg


def context_fingerprint() -> str:
    """Identity of the machine a solve is ABOUT, in whatever process asks:
    workspace (id + root), config file, the parsed config's content and the
    request's material override.  Computed on both sides of the pipe; a
    cache update from a child is only installed when they agree."""
    import hashlib
    import json
    from motor_ai_sim import workspace as _WSP
    from motor_ai_sim import config as _CFG
    parts: Dict[str, Any] = {}
    try:
        ws = _WSP.workspace()
        parts["ws"] = ws.id
        parts["root"] = str(ws.root)
    except Exception as exc:                            # noqa: BLE001
        parts["ws"] = "error:%s" % exc
    try:
        parts["cfg"] = str(_CFG.config_path())
        parts["cfg_data"] = hashlib.sha1(json.dumps(
            _config_data(_CFG.get_config()), sort_keys=True,
            default=str).encode("utf-8")).hexdigest()
    except Exception as exc:                            # noqa: BLE001
        parts["cfg_err"] = str(exc)
    mc = sys.modules.get("motor_ai_sim.material_context")
    if mc is not None:
        with contextlib.suppress(Exception):
            parts["mat"] = mc.get_request_materials()
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str)
                        .encode("utf-8")).hexdigest()


def capture_context() -> Dict[str, Any]:
    """Everything the child needs to answer about the SAME machine."""
    from motor_ai_sim import workspace as _WSP
    from motor_ai_sim import config as _CFG
    ctx: Dict[str, Any] = {"default_config_path": str(_CFG.DEFAULT_CONFIG_PATH)}
    ws = _WSP._WS.get()
    if ws is not None:
        ctx["workspace"] = {"id": ws.id, "email": ws.email, "root": str(ws.root),
                            "shared_root": str(ws.shared_root),
                            "config_file": str(ws.config_file) if ws.config_file else None,
                            "is_process": bool(ws.is_process)}
    ctx["caller"] = _WSP.caller()
    ctx["write_layer"] = _WSP._WRITE_LAYER.get()
    vars_: Dict[str, Any] = {}
    for mod_name, attr in CARRIED_VARS:
        mod = sys.modules.get(mod_name)
        var = getattr(mod, attr, None) if mod is not None else None
        if var is None:
            continue
        with contextlib.suppress(LookupError):
            vars_["%s:%s" % (mod_name, attr)] = var.get()
    ctx["vars"] = vars_
    gm = sys.modules.get("motor_ai_sim.simulation.geo_mesh")
    if gm is not None:
        with contextlib.suppress(Exception):
            ctx["tri_budget"] = gm.tri_budget()
    # The PARSED config this process holds for the caller's file, so an
    # in-memory view the child could not see on disk still reaches it.
    try:
        path = _CFG.config_path()
        cfg = _CFG.get_config()
        slot = _CFG._config_cache.get(str(path))
        ctx["config"] = {"path": str(path), "data": _config_data(cfg),
                         "mtime": slot[1] if slot else None}
    except Exception as exc:                            # noqa: BLE001
        log.debug("solve pool: config not carried (%s)", exc)
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
#  Function mode: call(target, payload) in a pool child
# ─────────────────────────────────────────────────────────────────────────────

def call(target: str, payload: Any, *,
         progress_cb: Optional[Callable[..., Any]] = None,
         threads: Optional[int] = None, label: str = "", tag: str = "",
         the_pool: Optional[SolvePool] = None,
         identity: Optional[Tuple[str, str, str, int, str]] = None,
         context: Optional[Dict[str, Any]] = None,
         cancel_check: Optional[Callable[[], None]] = None) -> Any:
    """Run ``target(payload, child)`` in a pool worker and return its answer.

    ``target`` is ``"package.module:function"``.  Progress calls the child makes
    (``child.progress(*args)``) are replayed on THIS thread through
    ``progress_cb`` — so a route's callback, its per-run progress entry and its
    cancel check behave exactly as in-process.  Raises :class:`NotPoolable`
    before anything is queued when the payload does not pickle.
    """
    p = the_pool or pool()
    owner, ws_id, rid, prio, client = identity or _job_identity()
    if context is None:
        context = capture_context()
    try:
        task_blob = pickle.dumps(("task", target, payload, context,
                                  progress_cb is not None, rethread_timeout_s()),
                                 protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:                            # noqa: BLE001
        raise NotPoolable("payload does not pickle: %s" % exc) from exc
    t = Ticket(owner=owner, ws_id=ws_id, run_id=rid, priority=prio,
               pinned=(int(threads) if threads else None), retunable=True,
               tag=tag, label=label or target.rsplit(":", 1)[-1], client=client)
    check = cancel_check or _job_cancel_check(rid)

    def _cancel(exc: BaseException) -> None:
        # The route's own cancel type first (a Simulation run answers 499).
        if progress_cb is not None:
            progress_cb(None, None)
        raise exc

    try:
        p.acquire(t, cancel_check=check)
    except _TagCancelled:
        from motor_ai_sim import jobs as _J
        _cancel(_J.JobCancelled(rid or tag))
    except BaseException as exc:                        # noqa: BLE001
        from motor_ai_sim import jobs as _J
        if isinstance(exc, _J.JobCancelled):
            _cancel(exc)
        raise

    state = DONE
    child: Optional[_Child] = None
    cpu_reported: Optional[float] = None
    warned_rethread = False
    try:
        env = _child_env(None, t.threads)
        proc, job = _popen([sys.executable, "-m", CHILD_MODULE], env,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        t.pid = proc.pid
        child = _Child(proc, job, framed=True)
        _usage_start(t)
        try:
            proc.stdin.write(_HDR.pack(len(task_blob)))
            proc.stdin.write(task_blob)
            proc.stdin.flush()
        except OSError:
            pass            # died at start-up: the loop below reports how
        sent_threads = t.threads
        next_sample = 0.0
        while True:
            if t.cancel_requested:
                state = CANCELLED
                child.kill()
                from motor_ai_sim import jobs as _J
                _cancel(_J.JobCancelled(rid or tag))
            try:
                check()
            except BaseException:
                state = CANCELLED
                child.kill()
                if progress_cb is not None:
                    progress_cb(None, None)
                raise
            want = t.pending_threads
            if want is not None and want != sent_threads and not child.exited():
                t.pending_threads = None
                with contextlib.suppress(Exception):
                    write_frame(proc.stdin, ("threads", int(want)))
                    sent_threads = want
            now = time.monotonic()
            if now >= next_sample:
                child.sample(t)
                _usage_cpu(t, child.cpu_s)
                next_sample = now + SAMPLE_S
                if not warned_rethread and t.rethread_at and \
                        t.acked_threads != t.threads and \
                        time.time() - t.rethread_at > 3 * rethread_timeout_s():
                    warned_rethread = True
                    log.warning("solve pool: pid %d has not confirmed its width "
                                "change %d -> %d; its old width stays reserved",
                                proc.pid, t.acked_threads, t.threads)
            try:
                msg = child.msgs.get(timeout=POLL_S)
            except _queue_mod.Empty:
                continue
            kind = msg[0]
            if kind == "threads_ack":
                p.ack_threads(t, int(msg[1]))
                continue
            if kind == "progress":
                if progress_cb is not None:
                    try:
                        progress_cb(*msg[1], **(msg[2] if len(msg) > 2 else {}))
                    except BaseException:
                        state = CANCELLED
                        child.kill()
                        raise
                continue
            if kind == "result":
                cpu_reported = msg[2] if len(msg) > 2 else None
                if len(msg) > 3 and msg[3]:
                    t.peak_rss = max(t.peak_rss, int(msg[3]))
                return msg[1]
            if kind == "error":
                state = "failed"
                _, type_name, text, tb, blob = msg[:5]
                cpu_reported = msg[5] if len(msg) > 5 else None
                exc_obj = None
                if blob is not None:
                    with contextlib.suppress(Exception):
                        exc_obj = pickle.loads(blob)
                if isinstance(exc_obj, BaseException):
                    with contextlib.suppress(Exception):
                        exc_obj.__notes__ = ["raised in solve pool worker:\n" + tb]
                    raise exc_obj
                raise SolveWorkerError(type_name, text, tb)
            if kind in ("eof", "protocol_error"):
                state = "failed"
                child.finish(t, graceful_s=10.0)
                child.join_io(timeout=2)
                why = describe_exit(child.returncode)
                extra = (" (protocol: %s)" % msg[1]) if kind == "protocol_error" else ""
                raise SolveWorkerDied(
                    "solve worker process died before returning a result: %s%s%s; "
                    "last stderr: %s" % (why, extra, child.oom_note(),
                                         child.stderr_last()))
    finally:
        if child is not None:
            with contextlib.suppress(Exception):
                if not child.finished:
                    if state != DONE:
                        child.kill()
                    child.finish(t, graceful_s=10.0 if state == DONE else 0.0)
            cpu = child.cpu_s if cpu_reported is None else max(child.cpu_s,
                                                                float(cpu_reported))
            _usage_finish(t, cpu)
            with contextlib.suppress(Exception):
                child.proc.stdin.close()
        p.release(t, state=state)


# ─────────────────────────────────────────────────────────────────────────────
#  Command mode: an existing worker program (the optimizer's refine_proc)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CommandResult:
    stdout: str
    stderr: str
    returncode: Optional[int]
    threads: int = 1
    waited_s: float = 0.0
    wall_s: float = 0.0
    peak_rss: int = 0
    cpu_s: float = 0.0
    #: True when the command was cancelled (Stop / its job's cancel) — while
    #: it waited for a slot (it never started) or while it ran (tree killed).
    #: A cancelled command is a STOPPED eval, not a failed one.
    cancelled: bool = False
    cancelled_while_waiting: bool = False


def run_command(argv: List[str], *, input_text: str = "",
                env: Optional[Dict[str, str]] = None,
                timeout: Optional[float] = None, threads: Optional[int] = None,
                tag: str = "", label: str = "",
                on_spawn: Optional[Callable[[subprocess.Popen], None]] = None,
                on_exit: Optional[Callable[[subprocess.Popen], None]] = None,
                the_pool: Optional[SolvePool] = None,
                identity: Optional[Tuple[str, str, str, int, str]] = None,
                cancel_check: Optional[Callable[[], bool]] = None) -> CommandResult:
    """``subprocess.run(argv, input=..., capture_output=True, timeout=...)``
    through the pool.

    ``threads`` None/<=1 pins the command to one thread (the optimizer's
    bit-identical evals); >1 asks for up to that many, granted only while the
    pool is otherwise idle.  The width is fixed for the command's lifetime and
    counted in the budget.  A cancel (``cancel_check()`` returning True, or
    :func:`cancel_tag`) returns ``cancelled=True``: while it waits it never
    starts, while it runs its whole tree is killed.  ``timeout`` counts from
    the start of the process, not from the wait, and raises
    ``subprocess.TimeoutExpired``.  Whatever happens, the tree is killed and
    reaped by the pool — the caller never kills the process itself.
    """
    p = the_pool or pool()
    owner, ws_id, rid, prio, client = identity or _job_identity()
    pinned = int(threads) if threads and int(threads) > 1 else 1
    t = Ticket(owner=owner, ws_id=ws_id, run_id=rid, priority=prio,
               pinned=pinned, retunable=False, tag=tag,
               label=label or os.path.basename(str(argv[-1])), client=client)

    def _check() -> None:
        if cancel_check is not None and cancel_check():
            raise _TagCancelled(tag)

    t0 = time.monotonic()
    try:
        p.acquire(t, cancel_check=_check)
    except _TagCancelled:
        return CommandResult("", "cancelled while waiting for a solve slot", None,
                             waited_s=time.monotonic() - t0, cancelled=True,
                             cancelled_while_waiting=True)
    waited = time.monotonic() - t0
    state = DONE
    child: Optional[_Child] = None
    t_run = time.monotonic()
    try:
        full_env = _child_env(env, t.threads)
        proc, job = _popen(list(argv), full_env, stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        t.pid = proc.pid
        child = _Child(proc, job, framed=False)
        _usage_start(t)
        if on_spawn is not None:
            with contextlib.suppress(Exception):
                on_spawn(proc)

        def _feed() -> None:
            with contextlib.suppress(Exception):
                if input_text:
                    proc.stdin.write(input_text.encode("utf-8"))
                proc.stdin.close()
        threading.Thread(target=_feed, daemon=True).start()
        next_sample = 0.0
        while not child.exited():
            if t.cancel_requested or (cancel_check is not None and cancel_check()):
                state = CANCELLED
                child.kill()
                break
            if timeout is not None and time.monotonic() - t_run > float(timeout):
                state = CANCELLED
                child.kill()
                child.finish(t)
                child.join_io(timeout=2)
                raise subprocess.TimeoutExpired(argv, timeout)
            now = time.monotonic()
            if now >= next_sample:
                child.sample(t)
                _usage_cpu(t, child.cpu_s)
                next_sample = now + SAMPLE_S
            time.sleep(POLL_S / 2)
        if state == DONE and t.cancel_requested:
            state = CANCELLED               # exited while the cancel landed
        child.finish(t, graceful_s=10.0)
        child.join_io(timeout=5)
        return CommandResult("".join(child.stdout_all), "".join(child.stderr_all),
                             child.returncode, threads=t.threads, waited_s=waited,
                             wall_s=time.monotonic() - t_run, peak_rss=t.peak_rss,
                             cpu_s=child.cpu_s, cancelled=(state == CANCELLED))
    finally:
        if child is not None:
            with contextlib.suppress(Exception):
                if not child.finished:
                    child.kill()
                    child.finish(t)
            _usage_finish(t, child.cpu_s)
            if on_exit is not None:
                with contextlib.suppress(Exception):
                    on_exit(child.proc)
        p.release(t, state=state)


# ─────────────────────────────────────────────────────────────────────────────
#  The canonical 2-D transient through the pool
# ─────────────────────────────────────────────────────────────────────────────

EM_TARGET = "motor_ai_sim.solve_pool_child:em_transient_eval_target"


def em_transient_eval(**kwargs) -> Dict:
    """Drop-in for ``fem_solver_2d.em_transient_eval``.

    Pool off (the default), or a call that cannot leave this process (an
    ``excitation`` object, an active P2 state capture, an argument that does
    not pickle): the in-process function, called with the same arguments.
    Pool on: the same call in a pool worker; progress replays through
    ``progress_cb`` here, and the cache updates the solve made (the eddy warm
    seed of THIS workspace, new d-axis calibrations) are installed here only
    when the worker proves it solved the same machine (:func:`apply_fem_echo`).
    """
    from motor_ai_sim.simulation import fem_solver_2d as _FS
    if not enabled() or kwargs.get("excitation") is not None:
        return _FS.em_transient_eval(**kwargs)
    try:
        from motor_ai_sim.simulation.p2_state_capture import current_p2_state_capture
        if current_p2_state_capture() is not None:
            return _FS.em_transient_eval(**kwargs)
    except Exception:                                   # noqa: BLE001
        pass
    import uuid
    progress_cb = kwargs.pop("progress_cb", None)
    nonce = uuid.uuid4().hex
    expected = context_fingerprint()
    try:
        out = call(EM_TARGET, {"kwargs": kwargs, "nonce": nonce},
                   progress_cb=progress_cb, label="em_transient_eval")
    except NotPoolable as exc:
        log.warning("solve pool: running in-process (%s)", exc)
        return _FS.em_transient_eval(progress_cb=progress_cb, **kwargs)
    apply_fem_echo(out.get("echo") or {}, nonce=nonce, expected=expected)
    return out["result"]


def apply_fem_echo(echo: Dict[str, Any], *, nonce: str, expected: str) -> bool:
    """Install a worker's cache updates in THIS process — or refuse them.

    Scope, and why it is enough:

    * the warm seed lives in ``fem_solver_2d._SB_WARM_CACHE``, a PER-WORKSPACE
      map (``workspace.ws_map``): it is written here, on the job's own thread,
      in the caller's workspace, so it cannot reach another account;
    * the update is accepted only when it carries this call's ``nonce`` AND the
      worker's own :func:`context_fingerprint` (workspace, config file, config
      CONTENT, material override) equals the one taken when the call was
      submitted AND the one of this thread now.  Anything else — a config
      edited mid-solve, another workspace, another material override — is
      refused, and this workspace's in-memory seed is DROPPED so the next
      in-process read falls back to the per-workspace disk mirror the worker
      wrote instead of an older state;
    * d-axis calibrations are added only for keys this workspace does not hold
      (an in-process solve would have hit its own entry first).  Their keys
      are the solver's topology key (geometry fingerprint + winding), and
      ``fem_solver_2d._DAXIS_CACHE`` is a per-workspace map like the warm
      seed (2026-09-30), so the entries land in the caller's workspace only.

    Returns True when installed.
    """
    from motor_ai_sim.simulation import fem_solver_2d as _FS
    ok = (echo.get("nonce") == nonce
          and echo.get("fingerprint") == expected
          and context_fingerprint() == expected)
    if not ok:
        if echo:
            log.warning("solve pool: cache update from a worker refused (nonce "
                        "%s, fingerprint %s): it does not belong to this request "
                        "and machine", "ok" if echo.get("nonce") == nonce
                        else "mismatch", "ok" if echo.get("fingerprint") == expected
                        else "mismatch")
        with contextlib.suppress(Exception):
            _FS._SB_WARM_CACHE.pop("last", None)
        return False
    wc = echo.get("warm_last")
    if wc is not None:
        with contextlib.suppress(Exception):
            _FS._SB_WARM_CACHE["last"] = wc
    dax = echo.get("daxis") or {}
    if dax:
        with _FS._DAXIS_LOCK:
            for k, v in dax.items():
                if k not in _FS._DAXIS_CACHE:
                    _FS._DAXIS_CACHE[k] = v
    return True
