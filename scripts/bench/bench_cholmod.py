"""CHOLMOD (scikit-sparse, OpenBLAS) vs MKL PARDISO -- priority-1 table.

Sweeps CHOLMOD ordering (amd, metis) x mode (supernodal, simplicial) on every
matrix class, using symbfact() for pure symbolic analysis and
CholeskyFactor.factorize(A_later) to measure refactorisation with the
symbolic pattern reused (CHOLMOD's own "analyze once, factorize many").
Thread count is fixed per *process* via OMP_NUM_THREADS/OPENBLAS_NUM_THREADS
set in the environment before this script starts (see run_cholmod.sh).

  mamba run -n solvers python bench_cholmod.py --matrices /matrices \
      --repeat 7 --threads 4 --out results/cholmod_t4.json
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import time

import numpy as np
import scipy.sparse as sp
import sksparse.cholmod as cm

TAGS = [
    "d40_rated_s1_pic2_sweeps",             # Ø40 static Newton K+T, n~17.6k
    "d40_rated_s1_eddy_solve",              # Ø40 bordered eddy Jacobian, n~23.9k
    "l13_rated_s1_pic2_sweeps",             # L13 static Newton, n~27.0k
    "l13_rated_s1_eddy_solve",              # L13 bordered eddy, n~30.8k
    "l155_rated_s1_pic2_sweeps",            # L155 static Newton, n~39.0k
    "l155_rated_s1_eddy_solve",             # L155 bordered eddy, n~44.3k
    "l155_rated_s0.3_eddy_solve",           # refined, n~97.4k
    "l155_rated_s0.2_eddy_solve",           # refined, n~168.5k
]

ORDERS = ["amd", "metis"]
MODES = ["supernodal", "simplicial"]


def load(base):
    m = json.load(open(base + "_meta.json"))
    A = sp.load_npz(base + "_A.npz").tocsc()
    bx = np.load(base + "_bx.npz")
    return m, A, bx["b"], bx["x"]


def rel(a, b):
    nb = np.linalg.norm(b)
    return float(np.linalg.norm(a - b) / (nb if nb > 0 else 1.0))


def maxrss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run_one(tag, order, mode, matdir, repeat):
    base1 = os.path.join(matdir, tag + "_first")
    base2 = os.path.join(matdir, tag + "_later")
    m1, A1, b1, x1 = load(base1)
    has_later = os.path.exists(base2 + "_meta.json")
    if has_later:
        m2, A2, b2, x2 = load(base2)
        if A2.nnz != A1.nnz:
            has_later = False
    rec = dict(tag=tag, order=order, mode=mode, n=A1.shape[0], nnz=int(A1.nnz),
               sym_rel=m1["sym_rel"], caller=m1["caller"], mesh_scale=m1["mesh_scale"],
               pardiso_ref=m1.get("pardiso"))
    rss0 = maxrss_mb()
    try:
        # 1. pure symbolic analysis (ordering + elimination tree), no numeric values
        t0 = time.perf_counter()
        cm.symbfact(A1, kind="sym")
        rec["symbolic_s"] = time.perf_counter() - t0

        # 2. first factor: symbolic (again, CHOLMOD recomputes internally via
        #    cho_factor's own analyze step) + first numeric factorization,
        #    combined -- this is the "cold" cost of a brand-new pattern.
        t0 = time.perf_counter()
        factor = cm.cho_factor(A1, order=order, supernodal_mode=mode)
        rec["analyze_plus_first_factor_s"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        x = factor.solve(b1)
        rec["first_solve_s"] = time.perf_counter() - t0
        rec["first_rel_residual"] = float(np.linalg.norm(b1 - A1 @ x) / max(np.linalg.norm(b1), 1e-300))
        rec["first_rel_diff_vs_pardiso_x"] = rel(x, x1)

        # 3. refactorisation with the SAME symbolic pattern, repeat times,
        #    on the "later" matrix (same Newton frame, new values) when
        #    available, else re-using A1's own values (still exercises the
        #    same reuse-of-analysis code path).
        A_r, b_r, x_r = (A2, b2, x2) if has_later else (A1, b1, x1)
        rec["refactor_uses"] = "later_matrix_same_pattern" if has_later else "same_matrix_repeated"
        fac, sol, tot = [], [], []
        for _ in range(repeat):
            t0 = time.perf_counter()
            factor.factorize(A_r)
            t1 = time.perf_counter()
            x = factor.solve(b_r)
            t2 = time.perf_counter()
            fac.append(t1 - t0)
            sol.append(t2 - t1)
            tot.append(t2 - t0)
        rec.update(refactor_s=statistics.median(fac), resolve_s=statistics.median(sol),
                    refactor_cycle_s=statistics.median(tot), refactor_cycle_min_s=min(tot))
        rec["refactor_rel_residual"] = float(np.linalg.norm(b_r - A_r @ x) / max(np.linalg.norm(b_r), 1e-300))
        rec["refactor_rel_diff_vs_pardiso_x"] = rel(x, x_r)
        rec["maxrss_delta_MB"] = maxrss_mb() - rss0
        rec["is_super"] = bool(factor.is_super)
    except Exception as e:
        rec["error"] = repr(e)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrices", required=True)
    ap.add_argument("--repeat", type=int, default=7)
    ap.add_argument("--threads", type=int, default=int(os.environ.get("OMP_NUM_THREADS", 0)) or None)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    tags = TAGS if not a.only else [t for t in TAGS if a.only in t]
    results = []
    for tag in tags:
        for order in ORDERS:
            for mode in MODES:
                r = run_one(tag, order, mode, a.matrices, a.repeat)
                r["threads"] = a.threads
                results.append(r)
                print(tag, order, mode, "->",
                      {k: (round(v, 6) if isinstance(v, float) else v)
                       for k, v in r.items() if k in
                       ("symbolic_s", "analyze_plus_first_factor_s", "refactor_s",
                        "resolve_s", "refactor_rel_residual", "refactor_rel_diff_vs_pardiso_x", "error")},
                      flush=True)
                json.dump(dict(threads=a.threads, results=results), open(a.out, "w"), indent=1, default=str)
    print("done ->", a.out)


if __name__ == "__main__":
    main()
