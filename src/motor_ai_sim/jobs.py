"""WHO gets a core next?  (migration Stage 4)

WHY
===
Stages 1-3 made the *answers* per user: the config path, the disk stores, the
in-memory caches.  What is still one-per-server is the *machine*: sixteen cores,
128 GB, and a single solve that happily takes all of it.  Measured in this very
repo (``routes/optimization.py``, the worker-count note): a pool of ten pinned
workers stretched a 60 s eval to a 231 s median — six users pressing Run at the
same moment do not get six answers in a minute, they get six answers in twenty
and a server that looks hung to all of them.

``field_jobs._FifoGate`` already solved a small version of this for field views
in 2026-09-03: a ticket deque under one condition, FIFO by construction because
``threading.Semaphore``'s wake order is undefined.  This module generalises that
gate along the two axes it lacks — **priority** (an interactive transient must
not queue behind a four-hour optimizer campaign) and **fairness** (one account
must not hold every worker while three others wait).

THE BACKEND SEAM (the user's requirement, 2026-09-15)
-----------------------------------------------------
Today the queue is in-process; tomorrow the workers are *other machines* that
never touch this process's memory.  So the interface, not the implementation, is
the deliverable:

    a job = {run_id, workspace id, kind, request body, priority, owner}

Everything in that record is JSON.  Nothing in it is a Python object, a closure
or a file handle, because a Redis-backed worker on another host has to be able
to reconstitute the whole job from the record alone: it sets the workspace from
``ws_id``, looks ``kind`` up in :data:`HANDLERS`, calls it with ``body``, and
writes the answer into the per-workspace *disk* stores that Stage 2 gave every
workspace.  :class:`RedisQueue` is that contract, spelled out and unimplemented,
so the seam exists before it is needed and the in-process queue cannot quietly
grow an API that only works in one process.

TWO ADMISSION MODES, AND WHY THE DEFAULT IS THE BLOCKING ONE
------------------------------------------------------------
* **Blocking (default).**  The HTTP call waits in the queue and then solves, on
  the request's own thread, exactly as today.  The frontend polls progress and
  gets ``queued: true, position: 3`` while it waits.  Today's web keeps working
  with no change at all — which is the whole point, because the alternative is
  a Stage-4 that cannot ship until the frontend ships.
* **202 (``QUEUE_ASYNC=1``).**  The call returns ``202 {run_id, queued,
  position}`` immediately and a worker runs the job; the client polls
  ``GET /api/jobs/{run_id}`` for the progress and then the result.  This is the
  mode a Redis-backed deployment needs (there is no thread on this host to
  block), and it is why every admitted route is submitted with its whole body as
  a callable rather than a context manager — the same call site serves both.

  The blocking mode's one cost, stated out loud: a waiting request holds a
  thread of FastAPI's threadpool (40 by default).  With ``QUEUE_WORKERS=4`` and
  one running job per user that is a handful of threads, not forty — but it is
  the reason the 202 mode exists at all, and the number to watch first if polls
  ever go slow while the queue is deep.

WHERE THE FIELD-VIEW GATE STAYS
-------------------------------
``field_jobs`` keeps its own gate and its own dedupe (two in-flight requests
with the same cache key are ONE solve — something a generic queue cannot know).
What moves here is its ADMISSION RULE, as the :data:`Priority.FIELD` class with
the same ``SB_FIELD_MAX_CONCURRENT`` cap read from the same env var: a field
view is admitted against that cap and against nothing else, so a transient
holding the only worker slot still does not stop one, exactly as today.

RE-ENTRANCY IS NOT OPTIONAL
---------------------------
A coupled run calls ``get_fem_transient`` twelve times; a duty cycle calls the
coupled loop; an optimizer campaign calls both.  If the inner call re-entered
the queue it would wait for a slot its own caller is holding — a deadlock the
first time two users ran anything.  So admission is **per context**: a call that
is already inside an admitted job runs inline.  :data:`_IN_JOB` is that flag,
and a ContextVar for the same reason everything else here is one.

WHAT DOES NOT CHANGE FOR THE SINGLE USER
-----------------------------------------
With ``WORKSPACES_ROOT`` unset (this workstation) the per-user fairness rule is
OFF: it is a rule *among users*, and with one user it would only throttle them.
Concurrency is then bounded by ``QUEUE_WORKERS`` alone, and field views keep
their own ``SB_FIELD_MAX_CONCURRENT`` gate untouched.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from motor_ai_sim import workspace as _WSP

log = logging.getLogger(__name__)

__all__ = [
    "Priority", "JobState", "JobRecord", "JobQueue", "InProcessQueue",
    "RedisQueue", "JobCancelled", "JobAccepted", "NotOwner",
    "queue", "reset_queue", "run_job", "admit", "queued",
    "cancel_run", "is_cancelled", "clear_cancelled", "current_run_id",
    "check_cancelled",
    "current_owner", "current_tier", "priority_for",
    "new_run_id", "async_mode", "store_path",
    "HANDLERS", "register_handler", "register_cancel_hook",
    "ENV_WORKERS", "ENV_ASYNC", "ENV_PER_USER", "ENV_FIELD_LIMIT",
]

#: How many jobs may RUN at once on this host.  ``cores // 4`` because a single
#: solve already fans out across the machine internally: the measurement behind
#: the number is in ``routes/optimization.py`` (ten pinned workers → 60 s eval
#: became 231 s).  Four on the AX102, two on an AX42, and never less than one.
ENV_WORKERS = "QUEUE_WORKERS"
#: ``1`` → the 202 mode (see the module docstring).  Anything else → blocking.
ENV_ASYNC = "QUEUE_ASYNC"
#: How many jobs ONE workspace may have running at once.  Unset = 1 when
#: multi-user is on, unlimited when it is off (a rule among users is no rule
#: when there is one user).  ``0`` disables the rule explicitly.
ENV_PER_USER = "QUEUE_PER_USER"
#: The field-view class keeps the cap it has had since 2026-09-03, under its own
#: name, so ``field_jobs``' contract and this one cannot drift apart.
ENV_FIELD_LIMIT = "SB_FIELD_MAX_CONCURRENT"

#: What a job record file is called, per workspace.
JOBS_FILE = ".jobs.json"
#: How many finished records one workspace keeps on disk.
KEEP_RECORDS = 60


class Priority(IntEnum):
    """Lower runs first.  The order is the user's, and it is about ATTENTION.

    ``INTERACTIVE`` is a transient somebody is watching a bar for; ``FIELD`` is
    a view they opened; ``DUTY`` is a run they started and walked away from;
    ``CAMPAIGN`` is an optimizer sweep that will still be going at lunch.  A
    campaign that overtook a transient would be right by arrival order and wrong
    by every other measure.
    """

    INTERACTIVE = 0
    FIELD = 1
    DUTY = 2
    CAMPAIGN = 3


class JobState(str):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: A record that was RUNNING when the process died.  Not an error — nobody
    #: knows what happened to it — but it must never read as "still running",
    #: which is what a restored ``running`` record would say for ever.
    INTERRUPTED = "interrupted"


class JobCancelled(BaseException):
    """The job was cancelled while it waited.

    ``BaseException`` for the reason ``simulation._RunCancelled`` gives: a
    cancellation is a REQUEST, not an error, and the paths it travels are full
    of ``except Exception`` guards that exist to keep a broken callback from
    killing a solve.
    """


class NotOwner(Exception):
    """Somebody asked to cancel a run that is not theirs (→ 403)."""


class JobAccepted(Exception):
    """The 202 mode's answer: the job is queued, here is where to poll.

    An exception and not a return value because it must be able to leave a
    route from the middle of a call chain without every intermediate frame
    learning about the queue.
    """

    def __init__(self, record: "JobRecord") -> None:
        super().__init__(record.run_id)
        self.record = record

    def payload(self) -> Dict[str, Any]:
        return {"queued": True, "run_id": self.record.run_id,
                "position": self.record.position,
                "kind": self.record.kind,
                "poll": "/api/jobs/%s" % self.record.run_id}


@dataclass
class JobRecord:
    """One unit of work — and everything a worker on ANOTHER machine needs.

    Every field is JSON-serialisable on purpose (see the module docstring): the
    record *is* the wire format.  ``body`` is the request as the route received
    it, trimmed to what the handler needs; it is what a Redis worker replays.
    """

    run_id: str
    ws_id: str
    owner: str
    kind: str
    priority: int = int(Priority.DUTY)
    state: str = JobState.QUEUED
    body: Dict[str, Any] = field(default_factory=dict)
    queued_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    #: 1-based place in the queue while QUEUED; 0 once it is running or over.
    position: int = 0
    #: Arrival order within this process — the FIFO half of the ordering.
    seq: int = 0

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("seq", None)
        return d

    def public(self) -> Dict[str, Any]:
        """What ``GET /api/jobs`` prints, progress merged in by the route."""
        out = self.to_json()
        out["elapsed_s"] = round(
            (self.finished_at or time.time()) - self.started_at, 1
        ) if self.started_at else 0.0
        out["waited_s"] = round(
            (self.started_at or time.time()) - self.queued_at, 1
        ) if self.queued_at else 0.0
        return out


# ─────────────────────────────────────────────────────────────────────────────
#  Environment
# ─────────────────────────────────────────────────────────────────────────────

def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def default_workers() -> int:
    """``QUEUE_WORKERS``, else ``max(1, cores // 4)``.  Read per call."""
    n = _int_env(ENV_WORKERS, 0)
    if n > 0:
        return n
    try:
        cores = os.cpu_count() or 4
    except Exception:                                   # noqa: BLE001
        cores = 4
    return max(1, cores // 4)


def async_mode() -> bool:
    """Is the 202 mode on?  Read PER CALL so a test can flip it."""
    return str(os.environ.get(ENV_ASYNC, "")).strip() in ("1", "true", "yes", "on")


def _field_limit() -> int:
    return max(1, _int_env(ENV_FIELD_LIMIT, 2))


def _per_user_limit() -> int:
    """How many jobs one workspace may run at once.  0 = no rule.

    Unset: 1 when ``WORKSPACES_ROOT`` is set, 0 otherwise.  That default is the
    promise at the bottom of the module docstring — fairness is a rule AMONG
    users, and applying it to a single-user workstation would only make the
    owner's own second solve wait for their first.
    """
    raw = str(os.environ.get(ENV_PER_USER, "")).strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 1 if _WSP.workspaces_root() is not None else 0


def store_path() -> Path:
    """``<ws>/.jobs.json`` — the job records of the workspace of THIS call."""
    return Path(str(_WSP.root())) / JOBS_FILE


# ─────────────────────────────────────────────────────────────────────────────
#  Who is asking
# ─────────────────────────────────────────────────────────────────────────────

def current_owner() -> str:
    """The identity a job belongs to, for the ownership check on cancel.

    The signed-in e-mail when there is one; the workspace id otherwise (which
    for a single-user install is the string ``process`` — one owner, every job
    is theirs, and the check is a tautology exactly as it should be).
    """
    try:
        who = _WSP.caller() or {}
        ident = str(who.get("id") or "").strip()
        if ident:
            return ident
    except Exception:                                   # noqa: BLE001
        pass
    try:
        return _WSP.workspace().id
    except Exception:                                   # noqa: BLE001
        return _WSP.PROCESS_WS_ID


def current_tier() -> str:
    """The tier of the caller this job belongs to, or ``""`` outside a request.

    ``""`` on purpose and not ``"free"``: a direct call (a test, a CLI run, the
    migration script) has no tier to be demoted by, and guessing the lowest one
    would quietly reorder work nobody is queueing against.
    """
    try:
        who = _WSP.caller() or {}
        return str(who.get("tier") or "").strip().lower()
    except Exception:                                   # noqa: BLE001
        return ""


#: Tiers with no claim on the front of the queue.  ``free`` is what every
#: invited and every self-registered account starts as; ``anon`` can only appear
#: on a host that still has the public exhibit open.
_BASE_TIERS = frozenset({"free", "anon"})
#: The job KINDS that are somebody's campaign — matched on the family (the part
#: before the first dot), so ``optimizer.scan`` / ``optimizer.doe`` /
#: ``optimizer.descent`` need no list to maintain.
_CAMPAIGN_FAMILIES = frozenset({"optimizer", "pipeline", "sweep"})


def priority_for(kind: str, tier: str,
                 requested: Priority = Priority.DUTY) -> Priority:
    """The class one job actually gets — the tier's half of admission.

    The rule (Stage 10): a ``free`` account's optimizer run is a CAMPAIGN and
    everything else it submits is at best a DUTY.  ``pro`` / ``team`` / ``admin``
    keep the class the route asked for, INTERACTIVE included, so a paying user's
    transient still goes in front of the bar they are watching.

    It never promotes: a campaign stays a campaign whoever submits it, which is
    why the non-optimizer answer is a ``max`` and not a constant.  An unknown or
    empty tier is left alone (see :func:`current_tier`).
    """
    t = str(tier or "").strip().lower()
    if t not in _BASE_TIERS:
        return Priority(int(requested))
    family = str(kind or "").split(".", 1)[0].strip().lower()
    if family in _CAMPAIGN_FAMILIES:
        return Priority.CAMPAIGN
    return Priority(max(int(requested), int(Priority.DUTY)))


def new_run_id(prefix: str = "run") -> str:
    """A server-side run id, for a client that sent none.

    Every run has one from now on: it is what progress, cancel and the job list
    are all keyed by, and a client that does not send one must not thereby lose
    the ability to watch or stop its own solve.
    """
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])


# ─────────────────────────────────────────────────────────────────────────────
#  The kind → handler table (the Redis contract's other half)
# ─────────────────────────────────────────────────────────────────────────────

#: ``kind`` -> ``handler(body: dict) -> Any``.  A worker process that pulled a
#: record off Redis has nothing but the record; this is how it finds the work.
HANDLERS: "Dict[str, Callable[[Dict[str, Any]], Any]]" = {}
#: ``kind`` -> ``hook(run_id)``, called when a RUNNING job of that kind is
#: cancelled.  The optimizer's subprocess killer and static3d's cancel flag hang
#: here, so ``POST /api/jobs/{run_id}/cancel`` reaches them without this module
#: importing a single route.
_CANCEL_HOOKS: "Dict[str, Callable[[str], None]]" = {}


def register_handler(kind: str, fn: Callable[[Dict[str, Any]], Any]) -> None:
    HANDLERS[str(kind)] = fn


def register_cancel_hook(kind: str, fn: Callable[[str], None]) -> None:
    _CANCEL_HOOKS[str(kind)] = fn


# ─────────────────────────────────────────────────────────────────────────────
#  The interface
# ─────────────────────────────────────────────────────────────────────────────

class JobQueue(ABC):
    """What a queue must do, whatever is behind it.

    Five methods, and they are the five things the API process asks of a queue
    it cannot see inside: put work in, ask about one job, stop one job, list a
    user's jobs, and — for a worker, which today is a thread and tomorrow is a
    process on another host — run the loop that drains it.
    """

    @abstractmethod
    def submit(self, record: JobRecord,
               work: Optional[Callable[[], Any]] = None,
               block: Optional[bool] = None) -> Any:
        """Queue ``record``.

        ``block`` (default: not :func:`async_mode`) runs ``work`` on the calling
        thread once the queue admits it and returns its result.  Otherwise the
        job is parked and :class:`JobAccepted` is raised with the record and its
        queue position.
        """

    @abstractmethod
    def status(self, run_id: str) -> Optional[JobRecord]:
        """The record for ``run_id``, or ``None``."""

    @abstractmethod
    def cancel(self, run_id: str, requester: str = "",
               is_admin: bool = False) -> Dict[str, Any]:
        """Stop ``run_id``.  Raises :class:`NotOwner` unless it is the
        requester's own run (or the requester is an admin)."""

    @abstractmethod
    def list_for_owner(self, owner: str, limit: int = 50,
                       is_admin: bool = False) -> List[JobRecord]:
        """This caller's jobs, newest first.  An admin sees everyone's."""

    @abstractmethod
    def worker_loop(self, stop: Optional[threading.Event] = None) -> None:
        """Drain the queue until ``stop`` is set.  In-process this is a thread;
        under Redis it is the whole body of a worker process."""

    # ── not abstract: only a queue that hands out admission needs it ─────────
    def finish(self, record: JobRecord, state: str, error: str = "") -> None:
        """Release the slot of a job the CALLER ran itself (:func:`admit`).

        The background-thread shape: ``submit`` admits, the caller's own thread
        does the work, and this hands the slot back.  A remote queue whose
        workers own the whole lifecycle never needs it, which is why it has a
        default and is not part of the abstract contract.
        """
        raise NotImplementedError("this queue does not hand out admission")


# ─────────────────────────────────────────────────────────────────────────────
#  Re-entrancy + the run id of the call in flight
# ─────────────────────────────────────────────────────────────────────────────

#: Set for the duration of one admitted job.  A nested solve sees it and runs
#: inline — see RE-ENTRANCY in the module docstring.
_IN_JOB: "ContextVar[Optional[str]]" = ContextVar("motor_ai_sim_in_job",
                                                  default=None)


def current_run_id() -> Optional[str]:
    """The run id of the admitted job this call is inside, if any."""
    return _IN_JOB.get()


# ─────────────────────────────────────────────────────────────────────────────
#  The in-process implementation
# ─────────────────────────────────────────────────────────────────────────────

class _Waiter:
    """One blocked caller (blocking mode) or one parked job (202 mode)."""

    __slots__ = ("record", "work", "ws", "who", "claimed", "done", "result",
                 "error")

    def __init__(self, record: JobRecord,
                 work: Optional[Callable[[], Any]] = None) -> None:
        self.record = record
        self.work = work
        self.ws = None          # the Workspace a parked job must be run in
        self.who = None
        self.claimed = False
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None


class InProcessQueue(JobQueue):
    """Priority + FIFO + one-running-job-per-user, on this host's threads.

    THE ORDERING, in one place, because it is the whole design:

      1. a job whose CLASS has no free slot is not a candidate.  Field views
         count against ``SB_FIELD_MAX_CONCURRENT`` and against nothing else —
         that gate is machine-wide and predates this queue; everything else
         counts against ``QUEUE_WORKERS``;
      2. a job whose WORKSPACE already has ``per_user`` jobs running is not a
         candidate (fairness; off for a single-user install);
      3. among what is left: lowest :class:`Priority` first; then the workspace
         that has STARTED the fewest jobs (round robin among waiting users, so
         one account cannot hold the queue by submitting fastest); then arrival
         order, which is what makes it FIFO within a priority for one user.

    Rule 3's middle term is the difference between this and ``_FifoGate``: pure
    FIFO gives the whole machine to whoever queued a hundred evals first.
    """

    def __init__(self, workers: Optional[int] = None,
                 field_limit: Optional[int] = None,
                 per_user: Optional[int] = None,
                 persist: bool = True) -> None:
        self._cv = threading.Condition()
        self._workers = int(workers) if workers else 0
        self._field_limit = int(field_limit) if field_limit else 0
        self._per_user = per_user
        self._persist = bool(persist)
        self._waiting: "List[_Waiter]" = []
        self._running: "Dict[str, JobRecord]" = {}
        self._records: "OrderedDict[str, JobRecord]" = OrderedDict()
        self._served: "Dict[str, int]" = {}
        self._cancelled: "OrderedDict[str, float]" = OrderedDict()
        self._seq = 0
        self._threads: "List[threading.Thread]" = []
        self._stop = threading.Event()
        #: ws_id -> that workspace's ROOT, as a string.  Why a path and not the
        #: Workspace: :meth:`_save` runs on whatever thread finished the job,
        #: and in the 202 mode that is a worker thread whose ContextVar is empty
        #: — ``store_path()`` there would resolve to the PROCESS workspace and
        #: file one account's job records under another's.  A path is also the
        #: thing that must not keep an evicted workspace's caches alive.
        self._roots: Dict[str, str] = {}

    # ── knobs, read per call so a test can move them ─────────────────────────
    @property
    def workers(self) -> int:
        return self._workers if self._workers > 0 else default_workers()

    @property
    def field_limit(self) -> int:
        return self._field_limit if self._field_limit > 0 else _field_limit()

    @property
    def per_user(self) -> int:
        return _per_user_limit() if self._per_user is None else int(self._per_user)

    # ── the scheduler ────────────────────────────────────────────────────────
    def _running_counts(self):
        n_field = sum(1 for r in self._running.values()
                      if r.priority == int(Priority.FIELD))
        per_ws: Dict[str, int] = {}
        for r in self._running.values():
            if r.priority != int(Priority.FIELD):
                per_ws[r.ws_id] = per_ws.get(r.ws_id, 0) + 1
        return n_field, len(self._running) - n_field, per_ws

    def _order_key(self, w: _Waiter):
        return (w.record.priority, self._served.get(w.record.ws_id, 0),
                w.record.seq)

    def _candidates(self) -> "List[_Waiter]":
        """Every waiter that COULD start right now, best first.

        Capacity is re-checked as the list is walked so the answer describes a
        whole admission round, not just its first step: that is what lets one
        ``notify_all`` start two field views and one transient together.
        """
        n_field, n_other, per_ws = self._running_counts()
        per_user = self.per_user
        workers = self.workers
        field_limit = self.field_limit
        out: List[_Waiter] = []
        for w in sorted(self._waiting, key=self._order_key):
            if w.claimed:
                continue
            is_field = w.record.priority == int(Priority.FIELD)
            if is_field:
                if n_field >= field_limit:
                    continue
            else:
                if n_other >= workers:
                    continue
                if per_user and per_ws.get(w.record.ws_id, 0) >= per_user:
                    continue
            out.append(w)
            if is_field:
                n_field += 1
            else:
                n_other += 1
                per_ws[w.record.ws_id] = per_ws.get(w.record.ws_id, 0) + 1
        return out

    def _positions_locked(self) -> None:
        """Re-number every waiting job.  1 = next."""
        for i, w in enumerate(sorted(self._waiting, key=self._order_key), 1):
            w.record.position = i

    def _start_locked(self, w: _Waiter) -> None:
        w.claimed = True
        try:
            self._waiting.remove(w)
        except ValueError:                              # pragma: no cover
            pass
        rec = w.record
        rec.state = JobState.RUNNING
        rec.started_at = time.time()
        rec.position = 0
        self._running[rec.run_id] = rec
        self._served[rec.ws_id] = self._served.get(rec.ws_id, 0) + 1
        self._positions_locked()

    def _finish_locked(self, rec: JobRecord, state: str, error: str = "") -> None:
        self._running.pop(rec.run_id, None)
        rec.state = state
        rec.error = str(error or "")[:400]
        rec.finished_at = time.time()
        rec.position = 0
        self._positions_locked()

    # ── records ──────────────────────────────────────────────────────────────
    def _remember(self, rec: JobRecord) -> None:
        self._records[rec.run_id] = rec
        self._records.move_to_end(rec.run_id)
        while len(self._records) > 512:
            self._records.popitem(last=False)

    # ── submit ───────────────────────────────────────────────────────────────
    def submit(self, record: JobRecord,
               work: Optional[Callable[[], Any]] = None,
               block: Optional[bool] = None) -> Any:
        if block is None:
            block = not async_mode()
        if work is None and not block:
            raise ValueError("the 202 mode needs a work callable")

        w = _Waiter(record, work)
        try:
            # Remembered HERE, on the submitter's thread, because this is the
            # last moment the workspace is certainly resolvable: ``_save`` may
            # run on a worker thread whose context is empty.
            self._roots[record.ws_id] = str(_WSP.workspace().root)
        except Exception:                               # noqa: BLE001
            pass
        with self._cv:
            self._seq += 1
            record.seq = self._seq
            record.queued_at = record.queued_at or time.time()
            record.state = JobState.QUEUED
            self._waiting.append(w)
            self._remember(record)
            self._positions_locked()
            if not block:
                # The parked job must be run in the SUBMITTER's workspace and
                # as the submitter — a worker thread starts with an empty
                # context and would otherwise solve the owner's machine.
                w.ws = _WSP.workspace()
                w.who = _WSP.caller()
                self._ensure_workers()
                self._cv.notify_all()
                accepted = JobAccepted(record)
                self._save(record.ws_id)
                raise accepted
            self._cv.notify_all()

        # Blocking mode: this thread IS the worker.  Wait for our turn.
        try:
            self._await_turn(w)
        finally:
            self._save(record.ws_id)
        if work is None:
            return None
        return self._run_admitted(w, inline=True)

    def _await_turn(self, w: _Waiter) -> None:
        with self._cv:
            while True:
                if w.record.run_id in self._cancelled:
                    self._finish_locked(w.record, JobState.CANCELLED)
                    try:
                        self._waiting.remove(w)
                    except ValueError:
                        pass
                    self._cv.notify_all()
                    raise JobCancelled(w.record.run_id)
                # ``_candidates`` describes a whole admission ROUND, not just
                # its first step, so "am I in it" is the right question: with
                # four free slots the first four waiters all start on one
                # ``notify_all`` instead of one per finished job.
                if any(c is w for c in self._candidates()):
                    self._start_locked(w)
                    self._cv.notify_all()
                    return
                self._cv.wait(timeout=1.0)

    def _run_admitted(self, w: _Waiter, inline: bool) -> Any:
        """Run an ALREADY-ADMITTED job and release its slot."""
        rec = w.record
        try:
            with _WSP.use_workspace(w.ws) if (not inline and w.ws is not None) \
                    else _nullctx():
                with _WSP.use_caller(w.who) if (not inline and w.who is not None) \
                        else _nullctx():
                    with _job_context(rec):
                        w.result = w.work()
        except JobCancelled:
            with self._cv:
                self._finish_locked(rec, JobState.CANCELLED)
                self._cv.notify_all()
            self._save(rec.ws_id)
            w.done.set()
            raise
        except BaseException as exc:                    # noqa: BLE001
            w.error = exc
            with self._cv:
                self._finish_locked(rec, JobState.FAILED, repr(exc))
                self._cv.notify_all()
            self._save(rec.ws_id)
            w.done.set()
            raise
        else:
            with self._cv:
                state = (JobState.CANCELLED if rec.run_id in self._cancelled
                         else JobState.DONE)
                self._finish_locked(rec, state)
                self._cv.notify_all()
            self._save(rec.ws_id)
            w.done.set()
            return w.result

    # ── the workers (202 mode only) ──────────────────────────────────────────
    def _ensure_workers(self) -> None:
        alive = [t for t in self._threads if t.is_alive()]
        self._threads = alive
        want = self.workers + self.field_limit
        while len(self._threads) < want:
            t = threading.Thread(target=self.worker_loop, daemon=True,
                                 name="job-worker-%d" % (len(self._threads) + 1))
            self._threads.append(t)
            t.start()

    def worker_loop(self, stop: Optional[threading.Event] = None) -> None:
        """Take parked jobs and run them.  The Redis worker's shape, in a thread.

        It only ever claims a job that was submitted with ``block=False``: a
        blocking caller is its own worker and is waiting on the same condition.
        """
        stop = stop or self._stop
        while not stop.is_set():
            w = None
            with self._cv:
                for cand in self._candidates():
                    if cand.work is not None and cand.ws is not None:
                        self._start_locked(cand)
                        w = cand
                        break
                if w is None:
                    self._cv.wait(timeout=0.25)
                    continue
                self._cv.notify_all()
            try:
                self._run_admitted(w, inline=False)
            except BaseException:                       # noqa: BLE001
                pass                                    # recorded on the job

    # ── the rest of the interface ────────────────────────────────────────────
    def finish(self, record: JobRecord, state: str, error: str = "") -> None:
        with self._cv:
            self._finish_locked(record, state, error)
            self._cv.notify_all()
        self._save(record.ws_id)

    def status(self, run_id: str) -> Optional[JobRecord]:
        with self._cv:
            rec = self._records.get(str(run_id))
            if rec is not None:
                return rec
        return self._load_one(str(run_id))

    def result(self, run_id: str) -> Any:
        """The answer of a finished 202-mode job, if this process still holds it."""
        with self._cv:
            for w in list(self._waiting):
                if w.record.run_id == run_id:
                    return w.result
        return None

    def cancel(self, run_id: str, requester: str = "",
               is_admin: bool = False) -> Dict[str, Any]:
        rid = str(run_id or "")
        if not rid:
            return {"cancelled": False, "run_id": "", "found": False}
        rec = self.status(rid)
        if rec is not None and not is_admin and requester:
            if str(rec.owner) != str(requester):
                raise NotOwner(rid)
        with self._cv:
            self._cancelled[rid] = time.time()
            while len(self._cancelled) > 256:
                self._cancelled.popitem(last=False)
            waiting = [w for w in self._waiting if w.record.run_id == rid]
            for w in waiting:
                self._waiting.remove(w)
                self._finish_locked(w.record, JobState.CANCELLED)
            self._cv.notify_all()
        if rec is not None and rec.state == JobState.RUNNING:
            hook = _CANCEL_HOOKS.get(rec.kind)
            if hook is not None:
                try:
                    hook(rid)
                except Exception as exc:                # noqa: BLE001
                    log.warning("cancel hook for %s failed: %s", rec.kind, exc)
        if rec is not None:
            self._save(rec.ws_id)
        return {"cancelled": True, "run_id": rid, "found": rec is not None}

    def is_cancelled(self, run_id: str) -> bool:
        if not run_id:
            return False
        with self._cv:
            return str(run_id) in self._cancelled

    def clear_cancelled(self, run_id: str = "") -> None:
        with self._cv:
            if run_id:
                self._cancelled.pop(str(run_id), None)
            else:
                self._cancelled.clear()

    def list_for_owner(self, owner: str, limit: int = 50,
                       is_admin: bool = False) -> List[JobRecord]:
        with self._cv:
            live = list(self._records.values())
        seen = {r.run_id for r in live}
        for r in self._load_all():
            if r.run_id not in seen:
                live.append(r)
                seen.add(r.run_id)
        if not is_admin:
            live = [r for r in live if str(r.owner) == str(owner)]
        live.sort(key=lambda r: (r.queued_at, r.seq), reverse=True)
        return live[:max(1, int(limit))]

    def snapshot(self) -> Dict[str, Any]:
        """``{running, queued, workers, field_limit, per_user, items}`` — the
        shape ``field_jobs.snapshot()`` established, for the same panel."""
        with self._cv:
            running = [r.public() for r in self._running.values()]
            waiting = [w.record.public()
                       for w in sorted(self._waiting, key=self._order_key)]
        return {"running": len(running), "queued": len(waiting),
                "workers": self.workers, "field_limit": self.field_limit,
                "per_user": self.per_user, "items": running + waiting}

    # ── persistence ──────────────────────────────────────────────────────────
    def _save(self, ws_id: str) -> None:
        """Write this workspace's records to ``<ws>/.jobs.json``, atomically.

        Why bother: a restart during a four-hour campaign otherwise leaves the
        user with a panel that says nothing ever ran.  The file is the answer to
        "what was this server doing when it went down", and it is small — sixty
        records of a dozen scalars.
        """
        if not self._persist:
            return
        with self._cv:
            rows = [r.to_json() for r in self._records.values()
                    if r.ws_id == ws_id][-KEEP_RECORDS:]
            root = self._roots.get(ws_id)
        try:
            p = (Path(root) / JOBS_FILE) if root else store_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
            tmp.write_text(json.dumps({"jobs": rows}, indent=1),
                           encoding="utf-8")
            os.replace(str(tmp), str(p))
        except OSError as exc:                          # noqa: BLE001
            log.debug("job records not persisted: %s", exc)

    def _load_all(self) -> List[JobRecord]:
        """The records on disk for the workspace of THIS call.

        A record that was RUNNING when the process stopped comes back as
        ``interrupted``: nobody knows how it ended, and the one answer that is
        certainly wrong is "still running".
        """
        try:
            raw = json.loads(store_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out: List[JobRecord] = []
        for row in (raw or {}).get("jobs") or []:
            try:
                row = dict(row)
                row.pop("seq", None)
                rec = JobRecord(**row)
            except TypeError:
                continue
            if rec.state in (JobState.RUNNING, JobState.QUEUED):
                rec.state = JobState.INTERRUPTED
            out.append(rec)
        return out

    def _load_one(self, run_id: str) -> Optional[JobRecord]:
        for r in self._load_all():
            if r.run_id == run_id:
                return r
        return None


class RedisQueue(JobQueue):
    """The seam.  Same interface, workers on OTHER MACHINES.

    THE CONTRACT, so that the day this is written there is nothing to design:

    * **Transport.**  One Redis list (or stream) per :class:`Priority`, plus a
      hash ``job:<run_id>`` holding the :class:`JobRecord` as JSON.  ``submit``
      writes the hash and pushes the id; a worker ``BRPOP``s the highest
      priority list it is allowed to serve.  Nothing but JSON crosses the wire.
    * **Fairness.**  The one-running-job-per-workspace rule becomes a Redis SET
      ``running:<ws_id>``; a worker that pops a job whose workspace is already
      at its limit pushes the id onto ``deferred:<ws_id>`` and takes the next.
      Round robin among waiting users is a sorted set keyed by the workspace's
      started-job count — the same ordering :class:`InProcessQueue` computes in
      :meth:`InProcessQueue._order_key`, evaluated centrally instead of locally.
    * **The worker.**  A process on any host with the same code and the same
      mounted ``WORKSPACES_ROOT`` / ``SHARED_ROOT``.  It sets the workspace
      ContextVar from ``record.ws_id`` (``workspace.workspace_for_identity``),
      looks ``record.kind`` up in :data:`HANDLERS`, calls it with
      ``record.body``, and writes the answer into the per-workspace DISK stores.
      **It never touches the API process's memory** — which is the whole reason
      the Stage 1-3 work had to land first, and why no handler may return a
      mesh object or a numpy array to its caller.
    * **Progress.**  ``ProgressTracker.snapshot()`` is a flat JSON dict; the
      worker writes it to ``progress:<run_id>`` every second with a TTL, and the
      API process's :class:`~motor_ai_sim.progress.ProgressRegistry` reads it
      through the same key when the local registry misses.
    * **Cancel.**  A ``cancel:<run_id>`` key with a TTL, polled by the worker at
      the same points ``jobs.is_cancelled`` is polled today.
    * **Liveness.**  A worker heartbeats ``worker:<id>``; a job whose worker's
      heartbeat lapsed goes back on its priority list, once, and then to
      ``failed``.

    Every method raises :class:`NotImplementedError`.  That is deliberate: a
    half-working Redis queue that silently degraded to in-process would be a
    two-user server pretending to be a cluster.
    """

    def __init__(self, url: str = "", **kw: Any) -> None:
        self.url = url or os.environ.get("REDIS_URL", "")
        self._kw = kw

    def _unimplemented(self, what: str):
        raise NotImplementedError(
            "RedisQueue.%s: the multi-host queue is the documented Stage-4 seam, "
            "not yet implemented — see the class docstring for the contract" % what)

    def submit(self, record: JobRecord,
               work: Optional[Callable[[], Any]] = None,
               block: Optional[bool] = None) -> Any:
        self._unimplemented("submit")

    def status(self, run_id: str) -> Optional[JobRecord]:
        self._unimplemented("status")

    def cancel(self, run_id: str, requester: str = "",
               is_admin: bool = False) -> Dict[str, Any]:
        self._unimplemented("cancel")

    def list_for_owner(self, owner: str, limit: int = 50,
                       is_admin: bool = False) -> List[JobRecord]:
        self._unimplemented("list_for_owner")

    def worker_loop(self, stop: Optional[threading.Event] = None) -> None:
        self._unimplemented("worker_loop")


# ─────────────────────────────────────────────────────────────────────────────
#  The process queue
# ─────────────────────────────────────────────────────────────────────────────

_QUEUE: Optional[JobQueue] = None
_QUEUE_GUARD = threading.Lock()


def queue() -> JobQueue:
    """The one queue this process submits to (built on first use)."""
    global _QUEUE
    with _QUEUE_GUARD:
        if _QUEUE is None:
            _QUEUE = InProcessQueue()
            log.info("job queue: %d worker slot(s) (%s), field views %d (%s), "
                     "%s per user", _QUEUE.workers, ENV_WORKERS,
                     _QUEUE.field_limit, ENV_FIELD_LIMIT,
                     _QUEUE.per_user or "unlimited")
        return _QUEUE


def reset_queue(q: Optional[JobQueue] = None) -> JobQueue:
    """Replace the process queue.  Tests, and a future ``RedisQueue`` switch."""
    global _QUEUE
    with _QUEUE_GUARD:
        _QUEUE = q if q is not None else InProcessQueue()
        return _QUEUE


@contextmanager
def _nullctx():
    yield None


@contextmanager
def _job_context(rec: JobRecord) -> Iterator[JobRecord]:
    """Inside an admitted job: the run id is set, and so is the progress run."""
    from motor_ai_sim import progress as _prog

    token = _IN_JOB.set(rec.run_id)
    try:
        with _prog.use_run(rec.run_id, route=rec.kind, owner=rec.owner,
                           ws_id=rec.ws_id):
            yield rec
    finally:
        _IN_JOB.reset(token)


def make_record(kind: str, *, priority: Priority = Priority.DUTY,
                run_id: str = "", body: Optional[Dict[str, Any]] = None,
                owner: str = "") -> JobRecord:
    """A record for THIS call: workspace, owner, tier and run id resolved here.

    The ONE funnel every submission goes through — ``run_job``, ``admit`` and
    the ``queued`` decorator all build their record here — which is why the
    per-tier class (:func:`priority_for`) is applied at this line and not at
    eight call sites that would drift apart.
    """
    ws = _WSP.workspace()
    prio = priority_for(str(kind), current_tier(), Priority(int(priority)))
    return JobRecord(run_id=str(run_id) or new_run_id(str(kind).split(".")[0]),
                     ws_id=ws.id, owner=str(owner) or current_owner(),
                     kind=str(kind), priority=int(prio),
                     body=dict(body or {}), queued_at=time.time())


def run_job(kind: str, work: Callable[[], Any], *,
            priority: Priority = Priority.DUTY, run_id: str = "",
            body: Optional[Dict[str, Any]] = None,
            block: Optional[bool] = None) -> Any:
    """Submit ``work`` and, in blocking mode, return its result.

    THE call site for a route whose whole body is a closure; :func:`queued` is
    the decorator that makes any endpoint into one.  Re-entrant: a nested solve
    runs inline and never queues (see the module docstring).
    """
    if current_run_id() is not None:
        return work()
    rec = make_record(kind, priority=priority, run_id=run_id, body=body)
    return queue().submit(rec, work, block=block)


@contextmanager
def admit(kind: str, *, priority: Priority = Priority.DUTY, run_id: str = "",
          body: Optional[Dict[str, Any]] = None) -> Iterator[Optional[JobRecord]]:
    """``with admit(...)``: block until the queue admits this work, then solve.

    For the places a closure is the wrong shape — a background thread that
    already owns its own lifecycle (``static3d._solve_worker``, the optimizer's
    campaign threads).  BLOCKING ONLY, by construction: there is no body to hand
    a worker.  Re-entrant, like everything here.
    """
    if current_run_id() is not None:
        yield None
        return
    rec = make_record(kind, priority=priority, run_id=run_id, body=body)
    q = queue()
    q.submit(rec, None, block=True)          # returns once admitted
    try:
        with _job_context(rec):
            yield rec
    except JobCancelled:
        q.finish(rec, JobState.CANCELLED)
        raise
    except BaseException as exc:                        # noqa: BLE001
        q.finish(rec, JobState.FAILED, repr(exc))
        raise
    else:
        q.finish(rec, JobState.DONE)


def body_run_id(prefix: str) -> Callable[[Dict[str, Any]], str]:
    """``run_id`` out of a POST body — ``coupled.run``, ``thermal.duty_cycle``.

    Also INJECTS a server-side id when the client sent none, because those two
    routes read it back out of the body to key their own cancel checks: an id
    the queue knows and the loop does not is a Stop button that does nothing.
    """

    def _extract(kw: Dict[str, Any]) -> str:
        b = kw.get("body")
        if not isinstance(b, dict):
            return ""
        rid = str(b.get("run_id") or "")
        if not rid:
            rid = new_run_id(prefix)
            b["run_id"] = rid
        return rid

    return _extract


def queued(kind: str, *, priority: Priority = Priority.DUTY,
           skip: Optional[Callable[[Dict[str, Any]], bool]] = None,
           run_id_from: Optional[Callable[[Dict[str, Any]], str]] = None,
           body_keys: "tuple" = ()) -> Callable:
    """Decorator: this endpoint's work goes through the queue.

    ONE line per route, and it serves both modes — which is why it is a
    decorator and not a context manager inside the body: the 202 mode needs the
    endpoint's whole body as a callable, and ``functools.wraps`` keeps
    ``inspect.signature`` (which is what FastAPI *and* ``modules.solvers.
    _call_filtered`` read) pointing at the real parameters.

    ``skip(kwargs) -> bool`` keeps the cheap paths out of the queue: a ledger
    PROBE and a ``restore`` read answer from disk in milliseconds and must never
    wait behind a solve — least of all behind the very solve they are asking
    about.  ``body_keys`` names the request fields worth recording on the job.
    """
    import functools

    def _decorate(fn):
        @functools.wraps(fn)
        def _wrapped(*a, **kw):
            if current_run_id() is not None or (skip is not None and skip(kw)):
                return fn(*a, **kw)
            rid = (str(run_id_from(kw) or "") if run_id_from is not None
                   else str(kw.get("run_id") or ""))
            body = {k: kw.get(k) for k in body_keys if k in kw}
            if not rid:
                rid = new_run_id(str(kind).split(".")[0])
                if "run_id" in kw or _accepts(fn, "run_id"):
                    kw["run_id"] = rid
            try:
                out = run_job(kind, lambda: fn(*a, **kw), priority=priority,
                              run_id=rid, body=body)
            except JobAccepted as acc:
                # THE 202 MODE.  The body is already parked with a worker; the
                # answer is where to poll.  A real ``Response`` and not a dict,
                # because the status code is half the message.
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=202, content=acc.payload())
            except JobCancelled:
                # Stopped BEFORE it ever started.  499 is what this backend
                # already answers for a transient stopped mid-march
                # (``simulation._RunCancelled``), and the frontend already
                # treats it as "the run you cancelled", not as an error.
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=499,
                    detail="simulation stopped while it waited in the queue")
            # EVERY run id is generated server-side when the client sends none,
            # and the ANSWER has to carry it — otherwise a client that did not
            # send one has no way to poll or stop the run it just started.
            if isinstance(out, dict) and not out.get("run_id"):
                out["run_id"] = rid
            return out
        return _wrapped

    return _decorate


def as_job(kind: str, *, priority: Priority = Priority.DUTY,
           run_id: str = "") -> Callable:
    """Wrap a BACKGROUND worker so it holds a queue slot while it runs.

    For the campaign threads and the 3-D solve worker: the HTTP call returns
    "started" straight away (it always did), and the thread then waits its turn
    like everything else instead of starting a four-hour sweep across cores four
    other people are solving on.  The wait is visible — the campaign's own state
    still says what it says, and ``GET /api/jobs`` says ``queued, position N``.
    """
    import functools

    def _decorate(fn):
        @functools.wraps(fn)
        def _wrapped(*a, **kw):
            with admit(kind, priority=priority, run_id=run_id):
                return fn(*a, **kw)
        return _wrapped

    return _decorate


def _accepts(fn, name: str) -> bool:
    try:
        import inspect
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):                     # pragma: no cover
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Cancellation — the module-level surface the routes use
# ─────────────────────────────────────────────────────────────────────────────

def cancel_run(run_id: str, requester: Optional[str] = None,
               is_admin: Optional[bool] = None) -> Dict[str, Any]:
    """Cancel by run id, with the OWNERSHIP check.

    ``requester=None`` means "this request's caller"; pass ``""`` for an
    internal cancel that must not be refused (the coupled loop forwarding a
    cancel into the transient it is running).
    """
    who = current_owner() if requester is None else str(requester)
    admin = _WSP.is_admin() if is_admin is None else bool(is_admin)
    return queue().cancel(str(run_id), requester=who, is_admin=admin)


def is_cancelled(run_id: str) -> bool:
    """Has this run been asked to stop?  Polled from inside solver callbacks."""
    q = queue()
    getter = getattr(q, "is_cancelled", None)
    return bool(getter(run_id)) if getter is not None else False


def check_cancelled(run_id: Optional[str] = None) -> None:
    """Raise :class:`JobCancelled` if THIS job (or ``run_id``) was asked to stop.

    For the long loops that sit INSIDE a job and have no progress callback of
    their own to carry a cancel — the rotor-stress contact iterations, the
    limit-speed search, the modal sweep, the thermal solves of a coupled run
    (owner 2026-09-25: Stop must stop every phase, not only the frame march).
    Outside a job, with no id given, it does nothing.
    """
    rid = run_id if run_id is not None else current_run_id()
    if rid and is_cancelled(rid):
        raise JobCancelled(str(rid))


def clear_cancelled(run_id: str = "") -> None:
    q = queue()
    fn = getattr(q, "clear_cancelled", None)
    if fn is not None:
        fn(run_id)
