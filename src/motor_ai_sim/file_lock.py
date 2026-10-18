"""A lock that holds across PROCESSES - for the small identity / catalog stores (deploy stage 2).

WHY
===
Every store under ``config/`` was written by "load the file, change one entry, write it back" under a
``threading.RLock``.  That is correct for ONE process and nothing else.  Stage 2 runs several: two API instances while an
update switches over, and the compute worker beside them - and a sign-in, a session touch or a catalog write can land in
two of them within the same millisecond.  The atomic ``os.replace`` keeps a reader from seeing a half-written file, but
two read-modify-write cycles that interleave still lose one of the two edits (and a shared ``*.tmp`` name lets one
process rename the other's half-written file).

:class:`StoreLock` is a drop-in for the ``threading.RLock`` those modules already use (``with lock:``).  It adds an
``flock`` on a sidecar ``.<name>.lock`` file next to the store, taken AFTER the thread lock and released BEFORE it, so
the order of two stores' locks is exactly the order the in-process code already had (no new lock-order cycle).

* Off unless :func:`motor_ai_sim.roles.multi_process` - a workstation, a BYO install and the suite pay nothing and behave
  exactly as before (no sidecar files are created).
* No ``fcntl`` (Windows): a no-op, same as before.
* Never wedges a request: waiting is bounded (``MOTRES_LOCK_TIMEOUT_S``, default 20 s); on a timeout it logs and goes on
  without the process lock - the same fail-open stance the audit writers take.
* Re-entrant like an RLock: the same thread may nest ``with`` blocks.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

try:                                                    # pragma: no cover - platform
    import fcntl as _fcntl
except ImportError:                                     # Windows
    _fcntl = None

log = logging.getLogger(__name__)

ENV_TIMEOUT = "MOTRES_LOCK_TIMEOUT_S"
_POLL_S = 0.02

PathLike = Union[str, os.PathLike, Callable[[], Union[str, os.PathLike]]]


def _enabled() -> bool:
    if _fcntl is None:
        return False
    from motor_ai_sim import roles
    return roles.multi_process()


def _timeout_s() -> float:
    try:
        return max(0.1, float(os.environ.get(ENV_TIMEOUT, "") or 20.0))
    except ValueError:
        return 20.0


def sidecar(path: Union[str, os.PathLike]) -> Path:
    """``<dir>/.<name>.lock`` for a store file ``<dir>/<name>`` (a directory store: ``<dir>/.store.lock``)."""
    p = Path(path)
    if p.is_dir():
        return p / ".store.lock"
    return p.with_name("." + p.name + ".lock")


class StoreLock:
    """``threading.RLock`` + a cross-process ``flock`` on a sidecar file.  See the module docstring."""

    def __init__(self, path: PathLike) -> None:
        self._path = path
        self._tl = threading.RLock()
        self._local = threading.local()           # per-thread: depth + the open fd

    # -- the file -----------------------------------------------------------
    def _lock_path(self) -> Path:
        p = self._path() if callable(self._path) else self._path
        return sidecar(p)

    # -- RLock interface ----------------------------------------------------
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if not self._tl.acquire(blocking, timeout):
            return False
        depth = getattr(self._local, "depth", 0)
        if depth == 0 and _enabled():
            try:
                self._local.fd = self._flock()
            except Exception as exc:                    # noqa: BLE001 - never fail a request over the lock
                log.warning("store lock %s not taken: %s", self._lock_path(), exc)
                self._local.fd = None
        self._local.depth = depth + 1
        return True

    def release(self) -> None:
        depth = getattr(self._local, "depth", 0) - 1
        self._local.depth = max(0, depth)
        if depth <= 0:
            fd: Optional[int] = getattr(self._local, "fd", None)
            self._local.fd = None
            if fd is not None:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                except Exception:                       # noqa: BLE001
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._tl.release()

    def __enter__(self) -> "StoreLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    # -- flock with a bounded wait -----------------------------------------
    def _flock(self) -> Optional[int]:
        lp = self._lock_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lp), os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + _timeout_s()
        while True:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                return fd
            except OSError:
                if time.monotonic() >= deadline:
                    log.error("store lock %s: still held by another process after %.0f s - continuing WITHOUT it",
                              lp, _timeout_s())
                    os.close(fd)
                    return None
                time.sleep(_POLL_S)


def store_lock(path: PathLike) -> StoreLock:
    """A cross-process lock for the store at ``path`` (or a callable returning it, for late-bound paths)."""
    return StoreLock(path)
