"""Process boundary between the API and gmsh (GPL-2.0-or-later).

Licence separation
-------------------
gmsh is GPL-2.0-or-later. Its exception list does not cover Intel MKL, which
the API process links (via pypardiso) under an AGPL section 7 exception we
grant for our own code. For that exception to hold, the API process must
never ``import gmsh`` or link ``libgmsh`` in-process. This module is the
*only* thing the API process is allowed to import to reach gmsh: it talks to
a separate ``python -m motor_ai_sim.simulation.gmsh_worker_main`` program
over a pipe, exchanging plain data (pickled Python objects framed with a
length prefix). gmsh itself is imported nowhere in this file and nowhere in
the API process's import graph — only inside the worker subprocess, dynamically,
by ``gmsh_worker_main.py``.

A worker crash or a timeout raises ``WorkerCrashError`` / ``WorkerTimeoutError``
(both ``WorkerError``). There is no silent fallback to another mesher: the
caller sees a clear error and decides what to do.

Protocol
--------
Length-prefixed pickle frames in both directions: a 4-byte big-endian
unsigned length, followed by that many bytes of ``pickle.dumps(obj,
protocol=4)``. A request is ``{"op": "call", "func": "pkg.mod:name",
"args": (...), "kwargs": {...}}`` or ``{"op": "handshake"}``. A response is
``{"ok": True, "result": obj}`` or ``{"ok": False, "error": str,
"traceback": str}``.

Pickle (not pure JSON) was chosen for the request/response payload because
the existing mesh-building functions already pass and return native Python
objects (Shapely polygons, dicts of them, scikit-fem ``MeshTri``, numpy
arrays) — reserializing that whole object graph to JSON would be a much
larger and riskier rewrite than the process boundary itself. The worker is
our own trusted subprocess (never fed untrusted external data), so this is
safe. A future all-JSON geometry/size-field protocol (as sketched for the
CDT backend) can be layered on top of the same subprocess without changing
this module's public surface.

Performance
-----------
Cold start (interpreter + ``import gmsh``) costs tens to a few hundred ms —
see ``docs/gmsh-out-of-process.md`` for a measurement. That is why the
default mode is ``persistent``: one worker subprocess is started lazily and
reused for the life of the API process, so the overhead is paid once, not
per mesh. Set ``MOTOR_AI_SIM_GMSH_WORKER_MODE=fresh`` to force a new
subprocess per call (used by the crash/timeout tests, and generally safer
for short-lived CLI scripts that only mesh once).
"""

from __future__ import annotations

import os
import pickle
import queue
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

_WORKER_MODULE = "motor_ai_sim.simulation.gmsh_worker_main"

_MODE_ENV = "MOTOR_AI_SIM_GMSH_WORKER_MODE"
_TIMEOUT_ENV = "MOTOR_AI_SIM_GMSH_WORKER_TIMEOUT_S"
_PYTHON_ENV = "MOTOR_AI_SIM_GMSH_WORKER_PYTHON"

_DEFAULT_TIMEOUT_S = 180.0


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
    # Determinism: gmsh/OCC must run single-threaded so results are
    # bit-identical for the same input in the same environment.
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "GMSH_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[var] = "1"
    return env


def _spawn() -> subprocess.Popen:
    return subprocess.Popen(
        [_python(), "-m", _WORKER_MODULE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_worker_env(),
        bufsize=0,
    )


@dataclass
class _Pending:
    frame: Optional[bytes] = None
    error: Optional[BaseException] = None


def _read_one_frame_async(proc: subprocess.Popen, box: "queue.Queue[_Pending]") -> None:
    try:
        frame = _read_frame(proc.stdout)
        box.put(_Pending(frame=frame))
    except BaseException as exc:  # noqa: BLE001 - relayed to the caller, not swallowed
        box.put(_Pending(error=exc))


class _PersistentWorker:
    """Owns at most one live worker subprocess, reused across calls."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def _ensure_alive(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        self._proc = _spawn()
        return self._proc

    def _kill(self) -> None:
        if self._proc is None:
            return
        proc, self._proc = self._proc, None
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

    def call(self, request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        with self._lock:
            proc = self._ensure_alive()
            try:
                _write_frame(proc.stdin, pickle.dumps(request, protocol=4))
            except (BrokenPipeError, OSError) as exc:
                stderr_tail = _drain_stderr(proc)
                self._kill()
                raise WorkerCrashError(
                    f"gmsh worker died before accepting a request: {exc}\n{stderr_tail}"
                ) from exc

            box: "queue.Queue[_Pending]" = queue.Queue(maxsize=1)
            reader = threading.Thread(
                target=_read_one_frame_async, args=(proc, box), daemon=True
            )
            reader.start()
            try:
                pending = box.get(timeout=timeout)
            except queue.Empty:
                stderr_tail = _drain_stderr(proc)
                self._kill()
                raise WorkerTimeoutError(
                    f"gmsh worker did not answer within {timeout:.1f}s "
                    f"(request killed, subprocess terminated)\n{stderr_tail}"
                )

            if pending.error is not None:
                stderr_tail = _drain_stderr(proc)
                self._kill()
                raise WorkerCrashError(
                    f"gmsh worker crashed while answering: {pending.error}\n{stderr_tail}"
                ) from pending.error

            return pickle.loads(pending.frame)

    def shutdown(self) -> None:
        with self._lock:
            self._kill()


def _drain_stderr(proc: subprocess.Popen, max_bytes: int = 4000) -> str:
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        if proc.poll() is None:
            proc.terminate()
        _, err = proc.communicate(timeout=2)
    except Exception:
        err = b""
    tail = (err or b"")[-max_bytes:]
    return tail.decode("utf-8", errors="replace")


_persistent = _PersistentWorker()


def _call_fresh(request: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    proc = _spawn()
    try:
        try:
            _write_frame(proc.stdin, pickle.dumps(request, protocol=4))
        except (BrokenPipeError, OSError) as exc:
            stderr_tail = _drain_stderr(proc)
            raise WorkerCrashError(
                f"gmsh worker died before accepting a request: {exc}\n{stderr_tail}"
            ) from exc

        box: "queue.Queue[_Pending]" = queue.Queue(maxsize=1)
        reader = threading.Thread(
            target=_read_one_frame_async, args=(proc, box), daemon=True
        )
        reader.start()
        try:
            pending = box.get(timeout=timeout)
        except queue.Empty:
            stderr_tail = _drain_stderr(proc)
            try:
                proc.kill()
            except Exception:
                pass
            raise WorkerTimeoutError(
                f"gmsh worker did not answer within {timeout:.1f}s "
                f"(request killed, subprocess terminated)\n{stderr_tail}"
            )

        if pending.error is not None:
            stderr_tail = _drain_stderr(proc)
            raise WorkerCrashError(
                f"gmsh worker crashed while answering: {pending.error}\n{stderr_tail}"
            ) from pending.error

        return pickle.loads(pending.frame)
    finally:
        if proc.poll() is None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def _dispatch(request: Dict[str, Any], timeout: Optional[float]) -> Dict[str, Any]:
    t = _timeout_s() if timeout is None else timeout
    if _mode() == "fresh":
        return _call_fresh(request, t)
    return _persistent.call(request, t)


def call(func_ref: str, args: Tuple[Any, ...] = (), kwargs: Optional[Dict[str, Any]] = None,
          timeout: Optional[float] = None) -> Any:
    """Run ``func_ref`` (``"pkg.module:function_name"``) inside the gmsh worker.

    Blocks until the worker answers, times out, or crashes. Never falls back
    to running the function in-process — that would defeat the whole point
    of the boundary (and would import gmsh here).
    """
    request = {"op": "call", "func": func_ref, "args": args, "kwargs": kwargs or {}}
    response = _dispatch(request, timeout)
    if not response.get("ok"):
        raise WorkerError(
            f"gmsh worker raised while running {func_ref}: {response.get('error')}\n"
            f"{response.get('traceback', '')}"
        )
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


def measure_cold_start_s() -> float:
    """Time a single fresh handshake round trip (process start + ``import gmsh``)."""
    request = {"op": "handshake"}
    t0 = time.monotonic()
    response = _call_fresh(request, _timeout_s())
    dt = time.monotonic() - t0
    if not response.get("ok"):
        raise WorkerError(f"handshake failed: {response.get('error')}")
    return dt


def shutdown_worker() -> None:
    """Terminate the persistent worker, if one is running. For tests/cleanup."""
    _persistent.shutdown()
