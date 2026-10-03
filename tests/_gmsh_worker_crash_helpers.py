"""Tiny functions the gmsh worker crash/timeout tests dispatch to.

Kept in their own module (not inline in the test file) because the worker
subprocess resolves them by dotted path (``importlib.import_module``) —
same mechanism used for the real mesh-building ``*_impl`` functions.
"""

from __future__ import annotations

import os
import time


def raise_value_error() -> None:
    raise ValueError("deliberate failure for the crash test")


def hard_crash() -> None:
    """Exit the interpreter immediately — no traceback, no cleanup.

    Stands in for a gmsh/OCC native crash (segfault), which a Python
    ``try/except`` in the worker cannot catch either way. The parent must
    detect the dead process, not a caught exception.
    """
    os._exit(1)


def sleep_forever(seconds: float) -> None:
    time.sleep(seconds)


def raise_gmsh_cdt_error() -> None:
    from motor_ai_sim.simulation.geo_mesh_gmsh import GmshCDTError
    raise GmshCDTError("synthetic gmsh CDT failure")


def cdt_with_failing_generate(V, S, area, hole_pts):
    """Inside the worker: make gmsh's generate fail, run the gmsh CDT body,
    and report what the failure left behind (session, process lock)."""
    import threading

    import gmsh
    from motor_ai_sim.simulation.geo_mesh_gmsh import (GmshCDTError,
                                                       _triangulate_gmsh_impl)
    from motor_ai_sim.simulation.sb_domains import _GMSH_LOCK

    real = gmsh.model.mesh.generate

    def boom(*a, **k):
        raise Exception("synthetic meshing failure")
    gmsh.model.mesh.generate = boom
    out = {"raised": None, "message": ""}
    try:
        _triangulate_gmsh_impl(V, S, area, hole_pts=hole_pts)
    except GmshCDTError as e:
        out["raised"], out["message"] = "GmshCDTError", str(e)
    finally:
        gmsh.model.mesh.generate = real
    out["initialized_after"] = bool(gmsh.isInitialized())
    got = []

    def _probe():
        ok = _GMSH_LOCK.acquire(timeout=5)
        got.append(ok)
        if ok:
            _GMSH_LOCK.release()
    th = threading.Thread(target=_probe)
    th.start()
    th.join()
    out["lock_free_after"] = got == [True]
    return out


def budget_preflight_probe(V, S, area, budget):
    """Inside the worker: count gmsh generate calls during a budget reject."""
    import gmsh
    from motor_ai_sim.simulation.geo_mesh import MeshBudgetExceeded
    from motor_ai_sim.simulation.geo_mesh_gmsh import _triangulate_gmsh_impl

    real = gmsh.model.mesh.generate
    calls = []
    gmsh.model.mesh.generate = lambda *a, **k: calls.append(1)
    out = {"raised": None}
    try:
        _triangulate_gmsh_impl(V, S, area, budget=budget)
    except MeshBudgetExceeded:
        out["raised"] = "MeshBudgetExceeded"
    finally:
        gmsh.model.mesh.generate = real
    out["generate_calls"] = len(calls)
    return out


def import_pypardiso() -> None:
    import pypardiso  # noqa: F401  (must be refused in the worker)


def record_trace(event: str, note: str) -> None:
    from motor_ai_sim.simulation import mesher
    mesher._trace_event(event)
    mesher._trace_note(note)
    mesher._trace_gap_built()


def read_env(name: str) -> dict:
    return {"value": os.environ.get(name), "omp": os.environ.get("OMP_NUM_THREADS")}
