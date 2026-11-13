"""cuDSS analysis-phase cost vs its host-side knobs (one matrix).

  python cudss_analysis_knobs.py <dir>/l155_rated_s1_eddy_solve_later [--threads 6]

The production solver changes its sparsity pattern once per rotor position,
so the analysis (reordering + symbolic factorisation) is paid once per frame.
cuDSS runs its reordering on the host; this script measures that phase with:
  * default (nested dissection, single host thread),
  * the multithreaded host layer (cudssSetThreadingLayer + CONFIG_HOST_NTHREADS),
  * AMD reordering instead of nested dissection,
and reports the factorise+solve cycle and the fill for each (a cheaper
ordering may cost more in every later factorisation).
"""
from __future__ import annotations

import argparse
import ctypes
import glob
import os
import statistics
import sys
import time

import numpy as np
import scipy.sparse as sp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gpu_backends import CuDSSDirect, _cudss_lib, _ck, _sync  # noqa: E402

CFG_REORDERING_ALG, CFG_HOST_NTHREADS = 0, 14
REORDER = {"default": 0, "amd": 3, "nd": 4}


def run(base, mtype, reorder, threads, mtlayer, reps=5):
    A = sp.load_npz(base + "_A.npz").tocsr()
    b = np.load(base + "_bx.npz")["b"]
    be = CuDSSDirect("float64", mtype=mtype)
    lib = be.lib
    lib.cudssSetThreadingLayer.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.cudssSetThreadingLayer.restype = ctypes.c_int
    if mtlayer:
        _ck(lib.cudssSetThreadingLayer(be.h, mtlayer.encode()), "threading layer")
        v = ctypes.c_int(threads)
        _ck(lib.cudssConfigSet(be.cfg, CFG_HOST_NTHREADS, ctypes.byref(v), 4), "host threads")
    if reorder != "default":
        v = ctypes.c_int(REORDER[reorder])
        _ck(lib.cudssConfigSet(be.cfg, CFG_REORDERING_ALG, ctypes.byref(v), 4), "reordering")
    ana = []
    for _ in range(3):                      # a new analysis each time, as per frame
        t0 = time.perf_counter()
        be.analyze(A)
        ana.append(time.perf_counter() - t0)
    cyc = []
    for _ in range(reps):
        t0 = time.perf_counter()
        be.factorize(A)
        x = be.solve(b)
        cyc.append(time.perf_counter() - t0)
    res = float(np.linalg.norm(b - A @ x) / np.linalg.norm(b))
    out = dict(mtype=mtype, reorder=reorder, mt=bool(mtlayer), threads=threads,
               analysis_ms=1e3 * statistics.median(ana), cycle_ms=1e3 * statistics.median(cyc),
               lu_nnz=be.info.get("lu_nnz"), residual=res)
    be.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("matrix")
    ap.add_argument("--threads", type=int, default=6)
    a = ap.parse_args()
    _cudss_lib()
    mt = None
    for d in sys.path:
        g = glob.glob(os.path.join(d, "nvidia", "cu1*", "bin", "cudss_mtlayer_*.dll")) + \
            glob.glob(os.path.join(d, "nvidia", "cu1*", "lib", "libcudss_mtlayer_*.so*"))
        if g:
            mt = g[0]
            break
    print("threading layer:", mt)
    for mtype in ("general", "spd"):
        for reorder in ("default", "amd"):
            for layer in (None, mt):
                if layer is None and reorder == "amd" and mtype == "general":
                    pass
                try:
                    r = run(a.matrix, mtype, reorder, a.threads, layer)
                    print("{mtype:8s} {reorder:8s} mt={mt!s:5s} analysis {analysis_ms:7.1f} ms  "
                          "cycle {cycle_ms:6.2f} ms  lu_nnz {lu_nnz}  res {residual:.1e}".format(**r),
                          flush=True)
                except Exception as e:
                    print(mtype, reorder, "mt" if layer else "", "FAILED", repr(e)[:200], flush=True)
    _sync()


if __name__ == "__main__":
    main()
