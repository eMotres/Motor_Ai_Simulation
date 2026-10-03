# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""In-process gmsh vs the gmsh worker: same output? what does the boundary cost?

For one representative case of every gmsh path type (tests/_gmsh_paths.py)
this runs the worker-side body IN THIS PROCESS (the old in-process behaviour)
and the public wrapper (the body in the persistent gmsh worker), compares the
two results field by field (meshes by node coordinates and connectivity, tags,
every array) and times both.  Also measures the worker's cold start and the
bare pipe round trip.

A MEASUREMENT tool, not product code: it imports gmsh in its own process to
reproduce the old behaviour, so it never imports pypardiso/MKL.  Threads are
pinned to 1 here exactly as the worker pins them, so both sides run the same
deterministic gmsh.

    python scripts/gmsh_worker_parity.py [--repeat 3] [--out parity.json]
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import statistics
import sys
import time

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "GMSH_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.getcwd())          # the checkout it is run from (tests/)


def _timed(fn, args, kwargs, repeat):
    out, ts = None, []
    for _ in range(repeat):
        t0 = time.perf_counter()
        out = fn(*args, **kwargs)
        ts.append(time.perf_counter() - t0)
    return out, ts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if "pypardiso" in sys.modules:
        raise SystemExit("refusing: pypardiso is loaded in this process")

    from motor_ai_sim.simulation import gmsh_worker
    from tests import _gmsh_paths as GP

    rep = {"cold_start_s": [gmsh_worker.measure_cold_start_s() for _ in range(3)]}
    gmsh_worker.handshake()                                   # warm the worker
    rt = []
    for _ in range(20):
        t0 = time.perf_counter()
        gmsh_worker.handshake()
        rt.append(time.perf_counter() - t0)
    rep["roundtrip_ms_median"] = 1e3 * statistics.median(rt)
    rep["cases"] = {}
    for name, wrapper, impl, args, kwargs in GP.cases():
        impl(*args, **kwargs)                                 # warm-up (imports)
        r_in, t_in = _timed(impl, args, kwargs, a.repeat)
        r_wk, t_wk = _timed(wrapper, args, kwargs, a.repeat)
        cmp_ = GP.compare(r_in, r_wk)
        try:
            payload = len(pickle.dumps(r_wk, protocol=4))
        except Exception:                                     # noqa: BLE001
            payload = None
        rec = dict(cmp_, inproc_s=statistics.median(t_in),
                   worker_s=statistics.median(t_wk),
                   overhead_ms=1e3 * (statistics.median(t_wk) - statistics.median(t_in)),
                   payload_bytes=payload)
        rep["cases"][name] = rec
        print("%-26s identical=%-5s maxdev=%-10.3g in=%.3fs worker=%.3fs "
              "overhead=%+.1f ms payload=%s"
              % (name, rec["identical"], rec["max_abs_dev"], rec["inproc_s"],
                 rec["worker_s"], rec["overhead_ms"], payload), flush=True)
    gmsh_worker.shutdown_worker()
    print("cold start s:", ["%.2f" % x for x in rep["cold_start_s"]],
          " bare round trip ms (median of 20): %.2f" % rep["roundtrip_ms_median"])
    print("PARITY_JSON " + json.dumps(rep, default=str))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, default=str)


if __name__ == "__main__":
    main()
