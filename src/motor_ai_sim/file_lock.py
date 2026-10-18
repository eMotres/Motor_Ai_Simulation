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
* ONE lock per store path in a process: two ``StoreLock`` objects made for the same file (a module-level lock and a
  ``json_store.lock_for`` one, say) share the thread lock and the per-thread flock state.  Without that, a thread holding
  one and taking the other would flock the same sidecar through two descriptors - and wait for ITSELF until the timeout.
  ``isolated=True`` opts out (a test that wants to play "the other process").
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


class _Core:
    """What the instances for one store path share: the thread lock and the per-thread (depth, fd) state."""

    __slots__ = ("tl", "local")

    def __init__(self) -> None:
        self.tl = threading.RLock()
        self.local = threading.local()


_CORES: "dict[str, _Core]" = {}
_CORES_LOCK = threading.Lock()


def _core_for(lock_path: Path) -> _Core:
    key = os.path.abspath(str(lock_path))
    with _CORES_LOCK:
        c = _CORES.get(key)
        if c is None:
            c = _CORES[key] = _Core()
        return c


class StoreLock:
    """``threading.RLock`` + a cross-process ``flock`` on a sidecar file.  See the module docstring."""

    def __init__(self, path: PathLike, isolated: bool = False) -> None:
        self._path = path
        self._isolated = bool(isolated)
        self._own = _Core() if isolated else None
        self._held = threading.local()            # per thread: the core taken by the outermost acquire (release uses it)

    # -- the file -----------------------------------------------------------
    def _lock_path(self) -> Path:
        p = self._path() if callable(self._path) else self._path
        return sidecar(p)

    def _core(self) -> _Core:
        return self._own if self._own is not None else _core_for(self._lock_path())

    # -- RLock interface ----------------------------------------------------
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        stack = getattr(self._held, "stack", None)
        if stack is None:
            stack = self._held.stack = []
        # a nested acquire by the same thread goes to the core the outer one took (a late-bound path may have moved)
        core = stack[-1] if stack else self._core()
        if not core.tl.acquire(blocking, timeout):
            return False
        stack.append(core)
        depth = getattr(core.local, "depth", 0)
        if depth == 0 and _enabled():
            try:
                core.local.fd = self._flock()
            except Exception as exc:                    # noqa: BLE001 - never fail a request over the lock
                log.warning("store lock %s not taken: %s", self._lock_path(), exc)
                core.local.fd = None
        core.local.depth = depth + 1
        return True

    def release(self) -> None:
        stack = getattr(self._held, "stack", None)
        if not stack:
            raise RuntimeError("cannot release an un-acquired StoreLock")
        core = stack.pop()
        depth = getattr(core.local, "depth", 0) - 1
        core.local.depth = max(0, depth)
        if depth <= 0:
            fd: Optional[int] = getattr(core.local, "fd", None)
            core.local.fd = None
            if fd is not None:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                except Exception:                       # noqa: BLE001
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass
        core.tl.release()

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


def store_lock(path: PathLike, isolated: bool = False) -> StoreLock:
    """A cross-process lock for the store at ``path`` (or a callable returning it, for late-bound paths).

    Every lock made for the same path in this process is the same lock (see the module docstring)."""
    return StoreLock(path, isolated=isolated)
