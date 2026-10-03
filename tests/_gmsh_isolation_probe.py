# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) MOTRES d.o.o. and contributors
"""Run in a FRESH interpreter by tests/test_gmsh_isolation.py (so no earlier
test can have polluted ``sys.modules``).  It plays the API / compute process:

  1. imports the API app and EVERY module of the motor_ai_sim package;
  2. loads Intel MKL through pypardiso when it is installed (the production
     image has it), exactly as a solve would;
  3. runs one small case of every gmsh path type through its public wrapper
     (tests/_gmsh_paths.py), i.e. through the gmsh worker;
  4. reports, as one JSON line on stdout: whether gmsh / libgmsh ever loaded
     HERE, what the worker process loaded (modules + shared objects), and a
     summary of each round trip.
"""
from __future__ import annotations

import importlib
import json
import os
import pkgutil
import sys
import time
import traceback


def _maps():
    try:
        with open("/proc/self/maps", encoding="utf-8", errors="replace") as fh:
            return sorted({os.path.basename(l.rsplit(None, 1)[-1]) for l in fh
                           if ".so" in l})
    except OSError:
        return []


def main() -> None:
    out = {"pid": os.getpid(), "import_errors": {}, "cases": {}}
    import motor_ai_sim
    from motor_ai_sim.api import app  # noqa: F401  (the API process)
    out["api_routes"] = len(app.routes)
    for m in pkgutil.walk_packages(motor_ai_sim.__path__, "motor_ai_sim."):
        if m.name.endswith("gmsh_worker_main"):
            continue            # the worker program itself (imports nothing gmsh at scope)
        try:
            importlib.import_module(m.name)
        except Exception as e:  # noqa: BLE001 — reported, judged by the test
            out["import_errors"][m.name] = "%s: %s" % (type(e).__name__, e)
    out["gmsh_after_imports"] = "gmsh" in sys.modules
    try:
        import pypardiso  # noqa: F401
        import numpy as np
        import scipy.sparse as sp
        pypardiso.spsolve(sp.identity(4, format="csr"), np.ones(4))
        out["mkl_loaded_here"] = any("mkl" in s for s in _maps()) or "pypardiso" in sys.modules
    except ImportError:
        out["mkl_loaded_here"] = None

    sys.path.insert(0, os.getcwd())
    from tests import _gmsh_paths
    from motor_ai_sim.simulation import gmsh_worker
    t0 = time.monotonic()
    gmsh_worker.handshake()
    out["first_handshake_s"] = time.monotonic() - t0
    for name, wrapper, _impl, args, kwargs in _gmsh_paths.cases():
        rec = {}
        try:
            t0 = time.monotonic()
            res = wrapper(*args, **kwargs)
            rec["s"] = time.monotonic() - t0
            flat = _gmsh_paths.flatten(res)
            rec["n_fields"] = len(flat)
            rec["n_values"] = int(sum(getattr(v, "size", 1) for v in flat.values()))
            rec["ok"] = rec["n_values"] > 0
        except Exception as e:  # noqa: BLE001 — reported, judged by the test
            rec["ok"] = False
            rec["error"] = "%s: %s" % (type(e).__name__, e)
            rec["traceback"] = traceback.format_exc()[-2000:]
        out["cases"][name] = rec
    out["gmsh_in_caller"] = "gmsh" in sys.modules
    out["libgmsh_in_caller"] = [s for s in _maps() if "gmsh" in s.lower()]
    out["worker"] = gmsh_worker.worker_modules()
    gmsh_worker.shutdown_worker()
    print("PROBE_JSON " + json.dumps(out, default=str))


if __name__ == "__main__":
    main()
