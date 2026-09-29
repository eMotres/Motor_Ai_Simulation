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
in-process path byte for byte.  A pool child never pools again
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
(``SOLVE_POOL_SOLO_THREADS``, default = ``procs``), so a single interactive run
is not slower than today; pool full or a queue behind it -> N jobs x 1 thread.
In between the idle cores are split evenly instead of left idle.  A running
worker that speaks the child protocol is re-threaded at its next progress
callback (threadpoolctl); a plain command (the optimizer's ``refine_proc``) or
a caller that pinned its thread count keeps what it got and is counted in the
budget by its real width, so the pool is never oversubscribed by it.

FAIRNESS
--------
Waiting solves are ordered by (priority of the job they belong to, solves the
owner has RUNNING, solves the owner has been SERVED, arrival): the queue's
:class:`~motor_ai_sim.jobs.Priority` first, then round robin among owners.

CANCEL, CRASH, CPU ACCOUNTING
-----------------------------
* The owning thread polls every :data:`POLL_S`; a cancelled job
  (``jobs.cancel_run``) or a cancelled tag (:func:`cancel_tag`) kills the
  child's whole process tree at once.
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

#: Fallback per-solve RSS before any child has been measured.  The measured
#: L155 eddy child peaks well below this (docs/SOLVE_POOL_2026-09-29.md); the
#: margin covers the P2 frame keyframes a Simulation run keeps.
DEFAULT_RSS_MB = 1500
DEFAULT_RESERVE_MB = 2048
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
    """Physical cores this process may use (affinity and cgroup quota), >= 1."""
    n: Optional[int] = None
    try:
        import psutil
        n = psutil.cpu_count(logical=False) or None
    except Exception:                                   # noqa: BLE001
        n = None
    logical = os.cpu_count() or 4
    try:
        allowed = len(os.sched_getaffinity(0))          # Linux only
    except (AttributeError, OSError):
        allowed = logical
    if n is None:
        n = logical // 2 if logical > 4 else logical
    if allowed < logical:                               # pinned: scale down
        n = max(1, int(round(n * allowed / float(logical))))
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


def _mem_available() -> Optional[int]:
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except Exception:                                   # noqa: BLE001
        return None


def _proc_rss(pid: int) -> int:
    try:
        import psutil
        return int(psutil.Process(pid).memory_info().rss)
    except Exception:                                   # noqa: BLE001
        return 0


class SolvePool:
    """Slots, ordering, the load rule and the RAM cap.  No processes here:
    :func:`call` / :func:`run_command` own the children and ask this object
    for admission, so the scheduling rules are testable without spawning."""

    def __init__(self, procs: Optional[int] = None, solo: Optional[int] = None,
                 rss_mb: Optional[float] = None, reserve_mb: Optional[float] = None,
                 mem_available: Optional[Callable[[], Optional[int]]] = None,
                 child_rss: Optional[Callable[[Ticket], int]] = None) -> None:
        self._cv = threading.Condition()
        self._procs = int(procs) if procs else 0
        self._solo = int(solo) if solo else 0
        self._rss_mb = float(rss_mb) if rss_mb else 0.0
        self._reserve_mb = float(reserve_mb) if reserve_mb is not None else -1.0
        self._mem_available = mem_available or _mem_available
        self._child_rss = child_rss or (lambda t: _proc_rss(t.pid) if t.pid else 0)
        self._waiting: List[Ticket] = []
        self._running: List[Ticket] = []
        self._served: Dict[str, int] = {}
        self._seq = itertools.count(1)
        self._peaks: Deque[int] = deque(maxlen=20)
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
        return s if s > 0 else self.procs

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

    # ── the rules ────────────────────────────────────────────────────────────
    def _order_key(self, t: Ticket):
        running_by_owner = sum(1 for r in self._running if r.owner == t.owner)
        return (t.priority, running_by_owner, self._served.get(t.owner, 0), t.seq)

    def _weight(self, t: Ticket) -> int:
        """Slots a RUNNING ticket occupies: its width if it is fixed (it will
        keep those threads), else 1 (the load rule will narrow it)."""
        return max(1, int(t.threads)) if t.fixed else 1

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
        headroom = self._ram_headroom() if self._waiting else None
        est = self.rss_estimate()
        out: List[Ticket] = []
        for t in sorted(self._waiting, key=self._order_key):
            if n_run > 0 or out:
                if used + 1 > procs:
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
        t.state = RUNNING
        t.started_at = time.time()
        self._running.append(t)
        self._served[t.owner] = self._served.get(t.owner, 0) + 1
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

    # ── the interface the owning thread uses ─────────────────────────────────
    def submit(self, t: Ticket) -> Ticket:
        with self._cv:
            t.seq = next(self._seq)
            t.state = WAITING
            t.queued_at = t.queued_at or time.time()
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
                    "running": len(running), "waiting": len(waiting),
                    "threads_in_use": sum(max(1, t["threads"]) for t in running),
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


def _popen(argv: List[str], env: Dict[str, str], **kw) -> subprocess.Popen:
    """Start a child at low priority, in its own process group/session."""
    if os.name == "nt":
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000) \
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200) \
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return subprocess.Popen(argv, env=env, creationflags=flags, **kw)
    nice = shutil.which("nice")
    lvl = _nice_level()
    if nice and lvl > 0:
        argv = [nice, "-n", str(lvl)] + list(argv)     # nice execs: same pid
    p = subprocess.Popen(argv, env=env, start_new_session=True, **kw)
    if not nice and lvl > 0:
        with contextlib.suppress(Exception):
            os.setpriority(os.PRIO_PROCESS, p.pid, lvl)
    return p


def kill_tree(proc: subprocess.Popen, wait_s: float = 5.0) -> None:
    """Kill ``proc`` and every descendant, now."""
    if proc is None:
        return
    kids = []
    try:
        import psutil
        kids = psutil.Process(proc.pid).children(recursive=True)
    except Exception:                                   # noqa: BLE001
        kids = []
    if os.name != "nt":
        with contextlib.suppress(Exception):
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    for k in kids:
        with contextlib.suppress(Exception):
            k.kill()
    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=wait_s)


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
    """One running worker: the process, its reader threads, its samples."""

    def __init__(self, proc: subprocess.Popen, framed: bool) -> None:
        self.proc = proc
        self.framed = framed
        self.msgs: "_queue_mod.Queue" = _queue_mod.Queue()
        self.stderr_tail: Deque[str] = deque(maxlen=40)
        self.stderr_all: List[str] = []
        self.stdout_all: List[str] = []
        self.keep_all_stderr = not framed
        self.cpu_s = 0.0
        self._threads: List[threading.Thread] = []
        if framed:
            self._spawn(self._read_frames)
        else:
            self._spawn(self._read_stdout_text)
        self._spawn(self._read_stderr)

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

    def sample(self, t: Ticket) -> None:
        try:
            import psutil
            p = psutil.Process(self.proc.pid)
            cpu = 0.0
            rss = 0
            for q in [p] + p.children(recursive=True):
                try:
                    ct = q.cpu_times()
                    cpu += ct.user + ct.system
                    rss += q.memory_info().rss
                except Exception:                       # noqa: BLE001
                    continue
            self.cpu_s = max(self.cpu_s, cpu)
            t.peak_rss = max(t.peak_rss, rss)
        except Exception:                               # noqa: BLE001
            pass

    def stderr_last(self) -> str:
        lines = [ln for ln in self.stderr_tail if ln.strip()]
        return " | ".join(lines[-6:])[:1200] if lines else "no stderr"


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


# ─────────────────────────────────────────────────────────────────────────────
#  Context carried into a child (what a bare interpreter cannot know)
# ─────────────────────────────────────────────────────────────────────────────

#: ContextVars a solve reads, carried by value.  Only modules already imported
#: here are read (an unimported module's var is at its default anyway).
CARRIED_VARS: Tuple[Tuple[str, str], ...] = (
    ("motor_ai_sim.material_context", "_OVERRIDE"),
    ("motor_ai_sim.mech_losses", "BEARING_TEMP_C"),
    ("motor_ai_sim.simulation.fem_solver_2d", "_OPT_CANDIDATE"),
    ("motor_ai_sim.simulation.fem_solver_2d", "_NO_WARM_CACHE_CTX"),
    ("motor_ai_sim.run_recording", "_RECORD"),
)


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
        try:
            from omegaconf import OmegaConf, DictConfig
            data = (OmegaConf.to_container(cfg, resolve=True)
                    if isinstance(cfg, DictConfig) else cfg)
        except ImportError:
            data = cfg
        ctx["config"] = {"path": str(path), "data": data,
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
                                  progress_cb is not None),
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
    cpu_final: Optional[float] = None
    try:
        env = _child_env(None, t.threads)
        proc = _popen([sys.executable, "-m", CHILD_MODULE], env,
                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE)
        t.pid = proc.pid
        child = _Child(proc, framed=True)
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
                kill_tree(proc)
                from motor_ai_sim import jobs as _J
                _cancel(_J.JobCancelled(rid or tag))
            try:
                check()
            except BaseException:
                state = CANCELLED
                kill_tree(proc)
                if progress_cb is not None:
                    progress_cb(None, None)
                raise
            want = t.pending_threads
            if want is not None and want != sent_threads and proc.poll() is None:
                t.pending_threads = None
                with contextlib.suppress(Exception):
                    write_frame(proc.stdin, ("threads", int(want)))
                    sent_threads = want
            now = time.monotonic()
            if now >= next_sample:
                child.sample(t)
                _usage_cpu(t, child.cpu_s)
                next_sample = now + SAMPLE_S
            try:
                msg = child.msgs.get(timeout=POLL_S)
            except _queue_mod.Empty:
                continue
            kind = msg[0]
            if kind == "progress":
                if progress_cb is not None:
                    try:
                        progress_cb(*msg[1], **(msg[2] if len(msg) > 2 else {}))
                    except BaseException:
                        state = CANCELLED
                        kill_tree(proc)
                        raise
                continue
            if kind == "result":
                cpu_final = msg[2] if len(msg) > 2 else None
                if len(msg) > 3 and msg[3]:
                    t.peak_rss = max(t.peak_rss, int(msg[3]))
                return msg[1]
            if kind == "error":
                state = "failed"
                _, type_name, text, tb, blob = msg[:5]
                cpu_final = msg[5] if len(msg) > 5 else None
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
                with contextlib.suppress(Exception):
                    proc.wait(timeout=10)
                child.join_io(timeout=2)
                why = describe_exit(proc.poll())
                extra = (" (protocol: %s)" % msg[1]) if kind == "protocol_error" else ""
                raise SolveWorkerDied(
                    "solve worker process died before returning a result: %s%s; "
                    "last stderr: %s" % (why, extra, child.stderr_last()))
    finally:
        if child is not None:
            if child.proc.poll() is None:
                kill_tree(child.proc) if state != DONE else \
                    _reap(child.proc)
            child.sample(t)
            _usage_finish(t, cpu_final if cpu_final is not None else child.cpu_s)
            with contextlib.suppress(Exception):
                child.proc.stdin.close()
        p.release(t, state=state)


def _reap(proc: subprocess.Popen) -> None:
    """A child that answered exits on its own within a moment; make sure."""
    try:
        proc.wait(timeout=10)
    except Exception:                                   # noqa: BLE001
        kill_tree(proc)


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
    :func:`cancel_tag`) while it waits returns ``returncode=-15`` without
    starting it, and while it runs kills the tree — the caller sees what it saw
    when its own kill hook fired.  ``timeout`` counts from the start of the
    process, not from the wait, and raises ``subprocess.TimeoutExpired``.
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
        return CommandResult("", "cancelled while waiting for a solve slot", -15,
                             waited_s=time.monotonic() - t0)
    waited = time.monotonic() - t0
    state = DONE
    child: Optional[_Child] = None
    t_run = time.monotonic()
    try:
        full_env = _child_env(env, t.threads)
        proc = _popen(list(argv), full_env, stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        t.pid = proc.pid
        child = _Child(proc, framed=False)
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
        while proc.poll() is None:
            if t.cancel_requested or (cancel_check is not None and cancel_check()):
                state = CANCELLED
                kill_tree(proc)
                break
            if timeout is not None and time.monotonic() - t_run > float(timeout):
                state = CANCELLED
                kill_tree(proc)
                child.join_io(timeout=2)
                raise subprocess.TimeoutExpired(argv, timeout)
            now = time.monotonic()
            if now >= next_sample:
                child.sample(t)
                _usage_cpu(t, child.cpu_s)
                next_sample = now + SAMPLE_S
            time.sleep(POLL_S / 2)
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        child.join_io(timeout=5)
        return CommandResult("".join(child.stdout_all), "".join(child.stderr_all),
                             proc.returncode, threads=t.threads, waited_s=waited,
                             wall_s=time.monotonic() - t_run, peak_rss=t.peak_rss)
    finally:
        if child is not None:
            if child.proc.poll() is None:
                kill_tree(child.proc)
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
    not pickle): the in-process function, unchanged.  Pool on: the same call in
    a pool worker; progress replays through ``progress_cb`` here, and the two
    in-memory caches the solve feeds (the eddy warm seed and the d-axis
    calibration) are copied back so the next in-process read sees what an
    in-process solve would have left behind.
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
    progress_cb = kwargs.pop("progress_cb", None)
    try:
        out = call(EM_TARGET, {"kwargs": kwargs}, progress_cb=progress_cb,
                   label="em_transient_eval")
    except NotPoolable as exc:
        log.warning("solve pool: running in-process (%s)", exc)
        return _FS.em_transient_eval(progress_cb=progress_cb, **kwargs)
    apply_fem_echo(out.get("echo") or {})
    return out["result"]


def apply_fem_echo(echo: Dict[str, Any]) -> None:
    """Install the child's cache updates in THIS process (caller's workspace)."""
    from motor_ai_sim.simulation import fem_solver_2d as _FS
    wc = echo.get("warm_last")
    if wc is not None:
        with contextlib.suppress(Exception):
            _FS._SB_WARM_CACHE["last"] = wc
    dax = echo.get("daxis") or {}
    if dax:
        with _FS._DAXIS_LOCK:
            for k, v in dax.items():
                _FS._DAXIS_CACHE[k] = v
