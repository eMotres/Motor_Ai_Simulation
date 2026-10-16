"""Dedupe + throttle for FIELD-VIEW solves, and a registry of what is running.

Why this exists (measured 2026-09-03, 18:48-19:05).  The API process sat at
~17 cores / 345 threads while the Simulation panel showed nothing running: the
load was ``GET /api/simulation/physics/fem_field2d`` — the 8-frame demag probe
and the 24-frame loss/eddy mini-transients the field views used to start BY
THEMSELVES on tab open and machine load.  Fourteen of them on one machine;
three on a large machine at 5-7 min each; two of those DUPLICATED within a
second with a byte-identical cache key, i.e. the same solve run twice at the
same time.  Nothing on screen said any of it was happening, because the
progress strip only tracks the main transient.

Three separate rules, and they are separate on purpose:

1. DEDUPE.  Two in-flight requests with the same cache key are the same solve.
   The first one owns it; every later one waits on its result and NEVER starts
   a second solve.  (The field cache already collapses SEQUENTIAL twins — this
   is the concurrent case it cannot see.)
2. LIMIT.  At most ``SB_FIELD_MAX_CONCURRENT`` (default 2) field solves run at
   once; the rest queue FIFO.  A field solve already runs a whole worker pool
   inside itself, so three of them do not go three times faster — they go
   slower and they starve the transient the user is actually waiting for.
3. VISIBILITY.  Anything that is solving or queued is IN a registry that the
   frontend can read (``/physics/field_busy``, and the same object on the
   transient-progress response).  A server that is busy has to be able to say
   so; the whole incident was invisible from the UI.

The queue is FIFO by construction (a ticket deque under one condition), not by
``threading.Semaphore``, whose wake-up order is undefined — a request that
arrived first must not be overtaken by one that arrived while it waited.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# How many field solves may run at once.  Two, because a single solve already
# parallelises across the machine internally; the point of the second slot is
# that a fast single-angle view is not stuck behind a 6-minute loss map.
DEFAULT_MAX_CONCURRENT = 2
_ENV_LIMIT = "SB_FIELD_MAX_CONCURRENT"


def _limit_from_env(default: int = DEFAULT_MAX_CONCURRENT) -> int:
    try:
        v = int(str(os.environ.get(_ENV_LIMIT, "")).strip() or default)
    except (TypeError, ValueError):
        return default
    return max(1, v)


def key_short(key: Any) -> str:
    """A short, stable name for a cache key — for logs and the busy list.

    The real key is a 25-field tuple carrying a config fingerprint; printing it
    is unreadable and leaks nothing useful, so the registry shows a hash.
    """
    try:
        raw = repr(key).encode("utf-8", "replace")
    except Exception:                                          # noqa: BLE001
        raw = str(id(key)).encode()
    return hashlib.blake2s(raw, digest_size=4).hexdigest()


class _FifoGate:
    """A counting gate that admits waiters strictly in arrival order."""

    def __init__(self, limit: int):
        self.limit = max(1, int(limit))
        self._cv = threading.Condition()
        self._queue: deque = deque()
        self._active = 0

    def acquire(self, token: object) -> None:
        with self._cv:
            self._queue.append(token)
            while self._active >= self.limit or self._queue[0] is not token:
                self._cv.wait()
            self._queue.popleft()
            self._active += 1
            # A slot may still be free for the next in line (limit > 1).
            self._cv.notify_all()

    def release(self) -> None:
        with self._cv:
            self._active = max(0, self._active - 1)
            self._cv.notify_all()

    @property
    def active(self) -> int:
        with self._cv:
            return self._active


class _Job:
    """One field solve: who owns it, when it started, what it produced."""

    __slots__ = ("key", "kind", "key_short", "queued_at", "started_at",
                 "done", "result", "error", "waiters")

    def __init__(self, key: Any, kind: str):
        self.key = key
        self.kind = str(kind or "field solve")
        self.key_short = key_short(key)
        self.queued_at = time.time()
        self.started_at = 0.0
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.waiters = 0          # extra callers riding on this one solve


class FieldJobRegistry:
    """Dedupe + FIFO limit + a live list of what is solving or queued."""

    def __init__(self, max_concurrent: Optional[int] = None):
        self.limit = (_limit_from_env() if max_concurrent is None
                      else max(1, int(max_concurrent)))
        self._gate = _FifoGate(self.limit)
        self._lock = threading.Lock()
        self._jobs: "Dict[Any, _Job]" = {}
        # Cumulative, for the log line at the end of a solve.
        self.deduped = 0

    # ── the one entry point ──────────────────────────────────────────────────
    def run(self, key: Any, kind: str, work: Callable[[], Any]) -> Any:
        """Run ``work()`` for ``key`` — or wait for the twin already running it.

        Returns whatever ``work`` returned.  If ``work`` raised, the SAME
        exception is raised in the owner and in every waiter (an HTTPException
        stays an HTTPException, so a duplicate request gets the same 500 the
        real one got instead of a silent empty payload).
        """
        job, mine = self._claim(key, kind)
        if not mine:
            job.done.wait()
            if job.error is not None:
                raise job.error
            return job.result

        token = object()
        try:
            self._gate.acquire(token)
            job.started_at = time.time()
            try:
                job.result = work()
            finally:
                self._gate.release()
        except BaseException as e:                             # noqa: BLE001
            job.error = e
            raise
        finally:
            with self._lock:
                self._jobs.pop(key, None)
            if job.waiters:
                log.info("field solve %s (%s): %d duplicate request(s) served "
                         "from this one solve", job.key_short, job.kind,
                         job.waiters)
            job.done.set()
        return job.result

    def _claim(self, key: Any, kind: str) -> Tuple[_Job, bool]:
        with self._lock:
            job = self._jobs.get(key)
            if job is not None:
                job.waiters += 1
                self.deduped += 1
                return job, False
            job = _Job(key, kind)
            self._jobs[key] = job
            return job, True

    # ── what the UI reads ────────────────────────────────────────────────────
    def snapshot(self) -> Dict[str, Any]:
        """``{solving, queued, limit, items:[{key_short, kind, since_s, ...}]}``."""
        now = time.time()
        with self._lock:
            jobs = list(self._jobs.values())
        items: List[Dict[str, Any]] = []
        solving = queued = 0
        for j in jobs:
            running = j.started_at > 0.0
            if running:
                solving += 1
            else:
                queued += 1
            items.append({
                "key_short": j.key_short,
                "kind": j.kind,
                "state": "solving" if running else "queued",
                # Time in the state the item is IN: how long it has been
                # solving, or how long it has been waiting for a slot.
                "since_s": round(now - (j.started_at if running
                                        else j.queued_at), 1),
                "waiters": int(j.waiters),
            })
        items.sort(key=lambda it: (it["state"] != "solving", -it["since_s"]))
        return {"solving": solving, "queued": queued,
                "limit": self.limit, "items": items}


# ── process-wide registry ────────────────────────────────────────────────────
_REGISTRY: Optional[FieldJobRegistry] = None
_REGISTRY_GUARD = threading.Lock()


def get_registry() -> FieldJobRegistry:
    """The one registry every field-solve route shares (built on first use)."""
    global _REGISTRY
    with _REGISTRY_GUARD:
        if _REGISTRY is None:
            _REGISTRY = FieldJobRegistry()
            log.info("field-solve queue: max %d concurrent (%s)",
                     _REGISTRY.limit, _ENV_LIMIT)
        return _REGISTRY


def run_field_job(key: Any, kind: str, work: Callable[[], Any]) -> Any:
    """Deduped, rate-limited ``work()`` for this field-solve cache key."""
    return get_registry().run(key, kind, work)


def field_busy() -> Dict[str, Any]:
    """What the field solver is doing right now (never raises)."""
    try:
        return get_registry().snapshot()
    except Exception as e:                                     # noqa: BLE001
        log.warning("field_busy unavailable: %s", e)
        return {"solving": 0, "queued": 0, "limit": 0, "items": []}
