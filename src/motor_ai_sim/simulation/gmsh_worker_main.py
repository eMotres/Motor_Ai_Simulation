"""Standalone gmsh worker — the only program in this codebase allowed to
``import gmsh``.

Run as ``python -m motor_ai_sim.simulation.gmsh_worker_main``. It never
imports MKL, pypardiso, or a scipy.sparse direct solver: it only ever
dynamically imports the mesh-building functions it is asked to run (e.g.
``motor_ai_sim.simulation.mesher:_build_mesh_from_polygons_impl``), which in
turn ``import gmsh`` locally. See ``gmsh_worker.py`` for the framing
protocol and the licence-separation rationale.

Kept deliberately tiny and free of project imports at module scope, so the
import-graph guard test (``tests/test_gmsh_process_boundary.py``) has a
short, obviously-correct list of "this file's own imports" to check against.
"""

from __future__ import annotations

import importlib
import pickle
import struct
import sys
import traceback
from typing import Any, Dict


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
        "python": sys.version,
        "pid": __import__("os").getpid(),
    }


def _handle(request: Dict[str, Any]) -> Dict[str, Any]:
    op = request.get("op")
    try:
        if op == "handshake":
            return {"ok": True, "result": _handshake()}
        if op == "call":
            func = _resolve(request["func"])
            result = func(*request.get("args", ()), **request.get("kwargs", {}))
            return {"ok": True, "result": result}
        return {"ok": False, "error": f"unknown op {op!r}", "traceback": ""}
    except Exception as exc:  # noqa: BLE001 - reported to the parent, not swallowed
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }


def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
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
        _write_frame(stdout, pickle.dumps(response, protocol=4))


if __name__ == "__main__":
    main()
