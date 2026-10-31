"""Throughput of independent work items (rotor positions / optimizer candidates).

  python throughput_bench.py --matrix <dir>/l155_rated_s1_eddy_solve_later --cycles 60 \
      --layouts 1x6,2x3,3x2,6x1 --hybrid 5x1+gpu:cudss_fp64_spd --backend pardiso --out tp.json

Each worker is a separate process (its own MKL/PARDISO handle, as independent
work items would be) that runs ``--cycles`` factorise+solve cycles on the same
real matrix; a layout KxT runs K workers with T MKL threads each. Reported: the
wall time of the whole layout and the aggregate cycles/s, against 1x<threads>.
A hybrid layout adds one GPU worker (``+gpu:<backend>``) next to the CPU workers.

This measures the linear-solve part of the throughput only; assembly and the
Python frame loop parallelise across processes the same way (they are
per-process) and are not included here.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time


def _worker(args):
    base, backend, threads, cycles, barrier_t0 = args
    for k in ("MKL_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[k] = str(threads)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import numpy as np
    import scipy.sparse as sp
    from gpu_backends import make_backend
    A = sp.load_npz(base + "_A.npz").tocsr()
    b = np.load(base + "_bx.npz")["b"]
    be = make_backend(backend)
    be.analyze(A)
    be.factorize(A); be.solve(b)          # warm-up, not timed
    while time.time() < barrier_t0:       # start together
        time.sleep(0.001)
    t0 = time.perf_counter()
    for _ in range(cycles):
        be.factorize(A)
        x = be.solve(b)
    dt = time.perf_counter() - t0
    res = float(np.linalg.norm(b - A @ x) / np.linalg.norm(b))
    be.close()
    return dict(backend=backend, threads=threads, cycles=cycles, wall_s=dt,
                per_cycle_ms=1e3 * dt / cycles, rel_residual=res)


def run_layout(base, cpu_workers, threads, cycles, backend, gpu_backend=None):
    jobs = [(base, backend, threads, cycles) for _ in range(cpu_workers)]
    if gpu_backend:
        jobs.append((base, gpu_backend, 1, cycles))
    t_start = time.time() + 8.0 + 2.0 * len(jobs)     # time for every worker to load + warm up
    ctx = mp.get_context("spawn")
    with ctx.Pool(len(jobs)) as pool:
        out = pool.map(_worker, [j + (t_start,) for j in jobs])
    wall = max(o["wall_s"] for o in out)
    total = sum(o["cycles"] for o in out)
    return dict(layout=f"{cpu_workers}x{threads}" + (f"+gpu:{gpu_backend}" if gpu_backend else ""),
                workers=out, wall_s=wall, cycles_total=total,
                cycles_per_s=total / wall)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", required=True, help="path prefix without _A.npz")
    ap.add_argument("--cycles", type=int, default=60)
    ap.add_argument("--layouts", default="1x6,2x3,3x2,6x1")
    ap.add_argument("--hybrid", default="", help="e.g. 5x1+gpu:cudss_fp64_spd")
    ap.add_argument("--backend", default="pardiso")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = dict(matrix=os.path.basename(a.matrix), backend=a.backend, layouts=[])
    specs = [s for s in a.layouts.split(",") if s] + [s for s in a.hybrid.split(",") if s]
    for spec in specs:
        gpu = None
        if "+gpu:" in spec:
            spec, gpu = spec.split("+gpu:")
        k, t = (int(v) for v in spec.split("x"))
        r = run_layout(a.matrix, k, t, a.cycles, a.backend, gpu)
        res["layouts"].append(r)
        print(f"{r['layout']:28s} wall {r['wall_s']:7.2f} s  {r['cycles_per_s']:7.1f} cycles/s  "
              + " ".join(f"{w['backend']}:{w['per_cycle_ms']:.1f}ms" for w in r["workers"]), flush=True)
        json.dump(res, open(a.out, "w"), indent=1)
    base = res["layouts"][0]["cycles_per_s"]
    for r in res["layouts"]:
        r["speedup_vs_first"] = r["cycles_per_s"] / base
    json.dump(res, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
