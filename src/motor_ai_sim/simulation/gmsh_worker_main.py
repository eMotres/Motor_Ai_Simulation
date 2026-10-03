"""Standalone gmsh worker — the only program in this codebase that loads gmsh.

Run as ``python -m motor_ai_sim.simulation.gmsh_worker_main``. It dynamically
imports and runs the worker-side mesh functions it is asked for (e.g.
``motor_ai_sim.simulation.mesher:_build_mesh_from_polygons_impl``), which in
turn ``import gmsh`` locally. See ``gmsh_worker.py`` for the framing protocol.

WHY IT IS LICENCE-CLEAN.  gmsh (GPL-2.0-or-later) is a separate program here:
the API / compute process (Apache-2.0 code, may load proprietary Intel MKL)
never loads it and talks to this worker only over a pipe.  Inside the worker
gmsh is combined only with GPL-compatible code: this project (Apache-2.0,
compatible with GPL-3.0, which gmsh's "or later" allows), the Python standard
library, numpy / scipy / shapely / scikit-fem (BSD) and their OpenBLAS / GEOS
builds (BSD / LGPL).  Intel MKL and pypardiso are BLOCKED from importing in this
process (``_BlockProprietary``): an import attempt raises ImportError, and
``{"op": "modules"}`` reports the licence-relevant modules and, on Linux, the
shared objects actually mapped, so a test can prove that no ``libmkl*`` or
``libiomp*`` is ever loaded next to libgmsh.  The API process, which does load
MKL, never loads gmsh (tests/test_gmsh_isolation.py).

Kept free of project imports at module scope.
"""

from __future__ import annotations

import importlib
import importlib.abc
import os
import pickle
import struct
import sys
import traceback
from typing import Any, Dict

#: Top-level modules that must never load next to gmsh (proprietary Intel
#: MKL and its Python front ends).
BLOCKED = ("pypardiso", "mkl", "mkl_fft", "mkl_random", "pydiso",
           "intel_openmp")


class _BlockProprietary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: D401
        if fullname.split(".", 1)[0] in BLOCKED:
            raise ImportError(
                "%s is blocked in the gmsh worker: MKL/PARDISO must never load "
                "in the same process as gmsh (GPL)" % fullname)
        return None


def _write_frame(stream, payload: bytes) -> None:
    stream.write(struct.pack(">I", len(payload)))
    stream.write(payload)
    stream.flush()


def _read_exact(stream, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return b""
        buf.extend(chunk)
    return bytes(buf)


def _read_frame(stream):
    header = _read_exact(stream, 4)
    if len(header) < 4:
        return None
    (n,) = struct.unpack(">I", header)
    body = _read_exact(stream, n)
    if len(body) < n:
        return None
    return body


def _resolve(func_ref: str):
    module_name, _, func_name = func_ref.partition(":")
    if not func_name:
        raise ValueError(f"func_ref must be 'module:function', got {func_ref!r}")
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def _handshake() -> Dict[str, Any]:
    import gmsh  # noqa: PLC0415 - the one place this is expected

    return {
        "gmsh_api_version": getattr(gmsh, "GMSH_API_VERSION", None),
        "gmsh_version": getattr(gmsh, "__version__", None),
        "python": sys.version,
        "pid": os.getpid(),
    }


def _modules() -> Dict[str, Any]:
    mods = sorted(m for m in sys.modules
                  if m.split(".", 1)[0] in BLOCKED + ("gmsh", "numpy", "scipy"))
    libs = []
    try:
        with open("/proc/self/maps", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                p = line.rsplit(None, 1)[-1]
                if p.endswith(".so") or ".so." in p or p.endswith(".dll"):
                    libs.append(os.path.basename(p))
    except OSError:
        pass
    return {"modules": mods, "shared_objects": sorted(set(libs)),
            "pid": os.getpid()}


_THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "GMSH_NUM_THREADS", "NUMEXPR_NUM_THREADS")


def _apply_env(env) -> None:
    """Run each call under the CALLER's environment as of the call (a
    persistent worker would otherwise keep the environment of its spawn;
    mesh options are read from env vars inside the build functions).  The
    thread counts stay pinned to 1 for determinism."""
    if not isinstance(env, dict):
        return
    os.environ.clear()
    os.environ.update({str(k): str(v) for k, v in env.items()})
    for var in _THREAD_VARS:
        os.environ[var] = "1"


def _trace_take():
    """The mesher's build trace recorded by the last call (then cleared), so
    the caller can replay fallbacks/notes into its own trace."""
    m = sys.modules.get("motor_ai_sim.simulation.mesher")
    take = getattr(m, "_trace_take_for_worker", None) if m is not None else None
    return take() if take is not None else None


def _handle(request: Dict[str, Any]) -> Dict[str, Any]:
    op = request.get("op")
    trace = [None]
    try:
        if op == "handshake":
            return {"ok": True, "result": _handshake()}
        if op == "modules":
            return {"ok": True, "result": _modules()}
        if op == "call":
            _apply_env(request.get("env"))
            try:
                func = _resolve(request["func"])
            except Exception as exc:  # noqa: BLE001 - a bad request, not the
                # function's own error: reported as a plain WorkerError
                return {"ok": False, "error": "cannot resolve %r: %s: %s"
                        % (request.get("func"), type(exc).__name__, exc),
                        "traceback": traceback.format_exc()}
            _trace_take()                       # start from a clean trace
            try:
                result = func(*request.get("args", ()), **request.get("kwargs", {}))
            except Exception:
                trace[0] = _trace_take()
                raise
            return {"ok": True, "result": result, "trace": _trace_take()}
        return {"ok": False, "error": f"unknown op {op!r}", "traceback": ""}
    except Exception as exc:  # noqa: BLE001 - reported to the parent, not swallowed
        out = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "trace": trace[0],
        }
        # The exception object itself, so the caller can re-raise the SAME
        # type it would have seen in-process (GmshCDTError,
        # MeshBudgetExceeded, ValueError, ...) and keep its own handling.
        try:
            blob = pickle.dumps(exc, protocol=4)
            pickle.loads(blob)
            out["exc"] = blob
        except Exception:  # noqa: BLE001 - not picklable: the text is enough
            pass
        return out


def main() -> None:
    sys.meta_path.insert(0, _BlockProprietary())
    # Frames go out on a PRIVATE duplicate of fd 1; fd 1 itself (where gmsh's
    # C++ terminal output and any print() land) is pointed at stderr, so
    # nothing but a frame can ever reach the parent's reader.
    frame_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    stdout = os.fdopen(frame_fd, "wb", buffering=0)
    stdin = sys.stdin.buffer
    while True:
        raw = _read_frame(stdin)
        if raw is None:
            return  # parent closed the pipe: exit quietly
        try:
            request = pickle.loads(raw)
        except Exception as exc:  # malformed request: report, keep serving
            _write_frame(stdout, pickle.dumps(
                {"ok": False, "error": f"could not unpickle request: {exc}",
                 "traceback": traceback.format_exc()},
                protocol=4,
            ))
            continue
        response = _handle(request)
        try:
            payload = pickle.dumps(response, protocol=4)
        except Exception as exc:  # an unpicklable result: say so, keep serving
            payload = pickle.dumps(
                {"ok": False, "error": f"result of {request.get('func')} is not "
                 f"picklable: {type(exc).__name__}: {exc}",
                 "traceback": traceback.format_exc()}, protocol=4)
        _write_frame(stdout, payload)


if __name__ == "__main__":
    main()
