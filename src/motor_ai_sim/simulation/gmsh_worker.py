"""Process boundary between the API and gmsh (GPL-2.0-or-later).

Licence separation
-------------------
gmsh is GPL-2.0-or-later. Its exception list does not cover Intel MKL, which
the API process links (via pypardiso). So the API process (and every compute
process that may load MKL) must never ``import gmsh`` or link ``libgmsh``:
gmsh runs as a separate program, ``python -m
motor_ai_sim.simulation.gmsh_worker_main``, and this module talks to it over
a pipe, exchanging plain data (pickled Python objects framed with a length
prefix). gmsh is imported nowhere in this file and nowhere in the API
process's import graph — only inside the worker subprocess, by the ``*_impl``
functions it is asked to run. The worker in turn refuses to import MKL /
pypardiso (``gmsh_worker_main``), so the two never share an address space.

Every gmsh use goes through here (owner decision 2026-10-03): the OCC/legacy
2-D mesher (``mesher``), the gmsh CDT backend (``geo_mesh_gmsh``), thermal and
mechanical meshes (``mechanical.modal`` / ``mechanical.rotor_stress``), the
static 3-D meshes (``static3d.*``), and provenance (``handshake``).

A worker crash or a timeout raises ``WorkerCrashError`` / ``WorkerTimeoutError``
(both ``WorkerError``). There is no silent fallback to another mesher.

Protocol
--------
Length-prefixed pickle frames in both directions: a 4-byte big-endian
unsigned length, followed by ``pickle.dumps(obj, protocol=4)``. A request is
``{"op": "call", "func": "pkg.mod:name", "args": (...), "kwargs": {...}}``,
``{"op": "handshake"}`` or ``{"op": "modules"}``. A response is
``{"ok": True, "result": obj}`` or ``{"ok": False, "error": str,
"traceback": str}``. Pickle, not JSON: the mesh functions pass and return
Shapely polygons, scikit-fem meshes and numpy arrays; the worker is our own
trusted subprocess and is never fed external input.

The frames travel on a PRIVATE copy of the worker's stdout file descriptor;
the worker points fd 1 at its stderr before it imports anything, so gmsh's
own terminal output can never corrupt a frame. Its stderr is drained
continuously by a thread here (a full pipe would otherwise block the worker)
and the last lines are attached to every worker error.

Performance
-----------
Default mode ``persistent``: one worker is started lazily per API (or compute)
process and reused, so interpreter start + ``import gmsh`` is paid once. The
per-call overhead is the pickle round trip. ``MOTOR_AI_SIM_GMSH_WORKER_MODE=
fresh`` forces a new subprocess per call (tests, one-shot scripts). The
worker is single-threaded (``OMP/MKL/OPENBLAS/GMSH_NUM_THREADS=1``) so a
mesh is bit-identical for the same input, as it was in-process.
"""

from __future__ import annotations

import atexit
import collections
import os
import pickle
import queue
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional, Tuple

_WORKER_MODULE = "motor_ai_sim.simulation.gmsh_worker_main"

_MODE_ENV = "MOTOR_AI_SIM_GMSH_WORKER_MODE"
_TIMEOUT_ENV = "MOTOR_AI_SIM_GMSH_WORKER_TIMEOUT_S"
_PYTHON_ENV = "MOTOR_AI_SIM_GMSH_WORKER_PYTHON"

#: A static 3-D or a fine 2-D OCC mesh can legitimately take minutes; the
#: timeout is a hang guard, not a performance budget.
_DEFAULT_TIMEOUT_S = 3600.0


class WorkerError(RuntimeError):
    """The gmsh worker subprocess failed to produce a result."""


class WorkerCrashError(WorkerError):
    """The worker process exited (crash, segfault, killed) before answering."""


class WorkerTimeoutError(WorkerError):
    """The worker did not answer within the timeout; it was killed."""


def _mode() -> str:
    m = os.environ.get(_MODE_ENV, "persistent").strip().lower()
    if m not in ("persistent", "fresh"):
        raise ValueError(f"{_MODE_ENV}={m!r} must be 'persistent' or 'fresh'")
    return m


def _timeout_s() -> float:
    raw = os.environ.get(_TIMEOUT_ENV)
    return float(raw) if raw else _DEFAULT_TIMEOUT_S


def _python() -> str:
    return os.environ.get(_PYTHON_ENV) or sys.executable


def _write_frame(stream, payload: bytes) -> None:
    stream.write(struct.pack(">I", len(payload)))
    stream.write(payload)
    stream.flush()


def _read_exact(stream, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise EOFError("gmsh worker closed its output pipe unexpectedly")
        buf.extend(chunk)
    return bytes(buf)


def _read_frame(stream) -> bytes:
    (n,) = struct.unpack(">I", _read_exact(stream, 4))
    return _read_exact(stream, n)


def _worker_env() -> Dict[str, str]:
    env = dict(os.environ)
    # Determinism: gmsh/OCC run single-threaded so results are bit-identical
    # for the same input in the same environment.
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "GMSH_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[var] = "1"
    # The worker must import the SAME motor_ai_sim the caller runs (a test
    # session or script may have put src/ on sys.path without PYTHONPATH),
    # and resolve "tests.*" helpers from the caller's working directory.
    pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    parts = [pkg_root, os.getcwd()]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _drain_stderr_forever(stream, tail: Deque[str]) -> None:
    try:
        for raw in iter(stream.readline, b""):
            tail.append(raw.decode("utf-8", errors="replace").rstrip("\n"))
    except Exception:  # noqa: BLE001 - the pipe closed; nothing to relay
        pass


def _spawn() -> subprocess.Popen:
    proc = subprocess.Popen(
        [_python(), "-m", _WORKER_MODULE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_worker_env(),
        bufsize=0,
    )
    tail: Deque[str] = collections.deque(maxlen=200)
    proc._stderr_tail = tail  # type: ignore[attr-defined]
    t = threading.Thread(target=_drain_stderr_forever, args=(proc.stderr, tail),
                         daemon=True, name="gmsh-worker-stderr")
    t.start()
    proc._stderr_thread = t  # type: ignore[attr-defined]
    return proc


def _stderr_tail(proc: subprocess.Popen, max_chars: int = 4000) -> str:
    t = getattr(proc, "_stderr_thread", None)
    if t is not None and proc.poll() is not None:
        t.join(timeout=2)
    tail = getattr(proc, "_stderr_tail", None)
    return "\n".join(tail or ())[-max_chars:]


def _kill(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    try:
        proc.stdin.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


class _NoGmshUnpickler(pickle.Unpickler):
    """Unpickling a worker answer must never import gmsh HERE: an object of a
    gmsh class (or a gmsh exception) in an answer is refused instead."""

    def find_class(self, module, name):
        if module.split(".", 1)[0] == "gmsh":
            raise WorkerError("the gmsh worker returned a gmsh object (%s.%s); "
                              "only plain data may cross the process boundary"
                              % (module, name))
        return super().find_class(module, name)


def _loads(blob: bytes) -> Any:
    import io
    return _NoGmshUnpickler(io.BytesIO(blob)).load()


@dataclass
class _Pending:
    frame: Optional[bytes] = None
    error: Optional[BaseException] = None


def _read_one_frame_async(proc: subprocess.Popen, box: "queue.Queue[_Pending]") -> None:
    try:
        box.put(_Pending(frame=_read_frame(proc.stdout)))
    except BaseException as exc:  # noqa: BLE001 - relayed to the caller, not swallowed
        box.put(_Pending(error=exc))


def _roundtrip(proc: subprocess.Popen, request: Dict[str, Any], timeout: float,
               on_fail) -> Dict[str, Any]:
    """Send one request and wait for its answer; ``on_fail()`` disposes of
    the process on any failure before the error is raised."""
    try:
        _write_frame(proc.stdin, pickle.dumps(request, protocol=4))
    except (BrokenPipeError, OSError) as exc:
        on_fail()
        raise WorkerCrashError(
            f"gmsh worker died before accepting a request: {exc}\n"
            f"{_stderr_tail(proc)}") from exc
    box: "queue.Queue[_Pending]" = queue.Queue(maxsize=1)
    threading.Thread(target=_read_one_frame_async, args=(proc, box),
                     daemon=True).start()
    try:
        pending = box.get(timeout=timeout)
    except queue.Empty:
        on_fail()
        raise WorkerTimeoutError(
            f"gmsh worker did not answer within {timeout:.1f}s "
            f"(request killed, subprocess terminated)\n{_stderr_tail(proc)}")
    if pending.error is not None:
        on_fail()
        raise WorkerCrashError(
            f"gmsh worker crashed while answering (exit code {proc.poll()}): "
            f"{pending.error}\n{_stderr_tail(proc)}") from pending.error
    return _loads(pending.frame)


class _PersistentWorker:
    """Owns at most one live worker subprocess, reused across calls.

    Fork-safe: a child process (multiprocessing fork) that inherits this
    object never talks to the parent's worker; it starts its own."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._pid = os.getpid()
        self._lock = threading.Lock()

    def _ensure_alive(self) -> subprocess.Popen:
        if self._pid != os.getpid():          # forked: the pipe is the parent's
            self._proc = None
            self._pid = os.getpid()
            self._lock = threading.Lock()
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        self._proc = _spawn()
        return self._proc

    def _drop(self) -> None:
        proc, self._proc = self._proc, None
        _kill(proc)

    def call(self, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        if self._pid != os.getpid():
            self._lock = threading.Lock()
        with self._lock:
            proc = self._ensure_alive()
            return _roundtrip(proc, request, timeout, self._drop)

    def shutdown(self) -> None:
        if self._pid != os.getpid():
            self._proc = None
            return
        with self._lock:
            self._drop()


_persistent = _PersistentWorker()
atexit.register(lambda: _persistent.shutdown())


def _call_fresh(request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    proc = _spawn()
    try:
        return _roundtrip(proc, request, timeout, lambda: _kill(proc))
    finally:
        _kill(proc)


def _dispatch(request: Dict[str, Any], timeout: Optional[float]) -> Dict[str, Any]:
    t = _timeout_s() if timeout is None else timeout
    if _mode() == "fresh":
        return _call_fresh(request, t)
    return _persistent.call(request, t)


def call(func_ref: str, args: Tuple[Any, ...] = (), kwargs: Optional[Dict[str, Any]] = None,
         timeout: Optional[float] = None) -> Any:
    """Run ``func_ref`` (``"pkg.module:function_name"``) inside the gmsh worker.

    Blocks until the worker answers, times out, or crashes. Never falls back
    to running the function in-process — that would import gmsh here.
    """
    request = {"op": "call", "func": func_ref, "args": args, "kwargs": kwargs or {},
               "env": dict(os.environ)}
    response = _dispatch(request, timeout)
    if response.get("trace"):
        # the build trace (fallback events / notes) recorded in the worker,
        # replayed into this thread's trace as if the build had run here
        from motor_ai_sim.simulation.mesher import _trace_merge_from_worker
        _trace_merge_from_worker(response["trace"])
    if not response.get("ok"):
        werr = WorkerError(
            f"gmsh worker raised while running {func_ref}: {response.get('error')}\n"
            f"{response.get('traceback', '')}"
        )
        orig = None
        if response.get("exc") is not None:
            try:
                orig = _loads(response["exc"])
            except Exception:  # noqa: BLE001 - fall back to the WorkerError
                orig = None
        if isinstance(orig, Exception):
            # The same exception type the in-process call raised, so callers
            # keep their own handling (MeshBudgetExceeded, GmshCDTError,
            # ValueError, ...); the worker traceback rides along as a note.
            try:
                orig.add_note("raised in the gmsh worker process:\n"
                              + str(response.get("traceback", "")))
            except Exception:  # noqa: BLE001 - add_note is 3.11+
                pass
            raise orig from werr
        raise werr
    return response["result"]


def handshake(timeout: Optional[float] = None) -> Dict[str, Any]:
    """Provenance: gmsh version, API version, and worker interpreter, without meshing anything."""
    response = _dispatch({"op": "handshake"}, timeout)
    if not response.get("ok"):
        raise WorkerError(
            f"gmsh worker handshake failed: {response.get('error')}\n"
            f"{response.get('traceback', '')}"
        )
    return response["result"]


def worker_modules(timeout: Optional[float] = None) -> Dict[str, Any]:
    """What the (persistent) worker has loaded: the licence-relevant Python
    modules and, on Linux, the shared objects mapped into it.  Used by the
    isolation tests to prove the worker never loads MKL / pypardiso."""
    response = _dispatch({"op": "modules"}, timeout)
    if not response.get("ok"):
        raise WorkerError(f"gmsh worker module report failed: {response.get('error')}")
    return response["result"]


def measure_cold_start_s() -> float:
    """Time a single fresh handshake round trip (process start + ``import gmsh``)."""
    t0 = time.monotonic()
    response = _call_fresh({"op": "handshake"}, _timeout_s())
    dt = time.monotonic() - t0
    if not response.get("ok"):
        raise WorkerError(f"handshake failed: {response.get('error')}")
    return dt


def shutdown_worker() -> None:
    """Terminate the persistent worker, if one is running. For tests/cleanup."""
    _persistent.shutdown()
