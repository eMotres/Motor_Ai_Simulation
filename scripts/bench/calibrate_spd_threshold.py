"""Calibrate the CHOLMOD -> MUMPS switch for SPD systems (open build).

For every exported production system pair (``<tag>_first`` / ``<tag>_later``:
same pattern, new values, written by scripts/bench/profile_fem_run.py on
branch study/open-solvers) this times, through linear_backend's own
``FactorStream`` (the exact production call path):

* cold  = analyze + factorize + solve on the FIRST matrix (a new pattern);
* warm  = factorize + solve on the LATER matrix with the analysis reused
          (median of ``--repeat``);

for CHOLMOD (AMD, CHOLMOD picks supernodal/simplicial), MUMPS SYM=1 and
MUMPS LU, plus the relative difference to the MKL PARDISO solution stored
with each system.  The weighted cost per solve is
``cold_fraction * cold + (1 - cold_fraction) * warm`` with the cold fraction
measured in docs/SOLVER_PROFILING_2026-09-29.md (eddy 10-14 %).

usage: python calibrate_spd_threshold.py --matrices DIR --out res.json
Licence: same as the repository.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import time

import numpy as np
import scipy.sparse as sp

from motor_ai_sim.simulation import linear_backend as LB


def load(base):
    A = sp.load_npz(base + "_A.npz").tocsr()
    A.sort_indices()
    bx = np.load(base + "_bx.npz")
    meta = json.load(open(base + "_meta.json"))
    return A, np.asarray(bx["b"], float), np.asarray(bx["x"], float), meta


def rel(x, ref):
    return float(np.max(np.abs(x - ref)) / max(np.max(np.abs(ref)), 1e-300))


def bench(name, A1, b1, x1, A2, b2, x2, repeat):
    s = LB.FactorStream(LB.make_factor(name))
    t0 = time.perf_counter()
    s.factor(A1)
    x = s.solve(b1)
    cold = time.perf_counter() - t0
    d1 = rel(x, x1)
    warm = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        s.factor(A2)
        x = s.solve(b2)
        warm.append(time.perf_counter() - t0)
    d2 = rel(x, x2)
    assert s.analyses == 1, "pattern reuse broken"
    s.free()
    return dict(cold_s=cold, warm_s=statistics.median(warm),
                rel_first=d1, rel_later=d2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrices", required=True)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--cold-fraction", type=float, default=0.14)
    ap.add_argument("--max-n", type=int, default=200000)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = []
    for mp in sorted(glob.glob(os.path.join(a.matrices, "*_first_meta.json"))):
        base1 = mp[:-len("_meta.json")]
        base2 = base1[:-len("_first")] + "_later"
        if a.only and not any(o in base1 for o in a.only.split(",")):
            continue
        if not os.path.exists(base2 + "_meta.json"):
            continue
        A1, b1, x1, m1 = load(base1)
        A2, b2, x2, m2 = load(base2)
        if A1.shape[0] > a.max_n or A1.nnz != A2.nnz \
                or not np.array_equal(A1.indices, A2.indices):
            continue
        if m1.get("sym_rel", 1.0) > 1e-12:
            continue
        tag = os.path.basename(base1)[:-len("_first")]
        row = dict(tag=tag, n=int(A1.shape[0]), nnz=int(A1.nnz),
                   pardiso_first_s=(m1.get("pardiso") or {}).get("dt"),
                   pardiso_later_s=(m2.get("pardiso") or {}).get("dt"))
        for name in ("cholmod", "mumps-spd", "mumps-lu"):
            try:
                r = bench(name, A1, b1, x1, A2, b2, x2, a.repeat)
                r["weighted_s"] = (a.cold_fraction * r["cold_s"]
                                   + (1 - a.cold_fraction) * r["warm_s"])
            except Exception as e:           # noqa: BLE001 — record, continue
                r = dict(error=repr(e))
            row[name] = r
        rows.append(row)
        c, m = row["cholmod"], row["mumps-spd"]
        print("%-40s n=%7d  chol cold %7.1f warm %7.1f | mumps-spd cold %7.1f "
              "warm %7.1f | mumps-lu warm %7.1f ms | pardiso later %s | rel %.1e %.1e"
              % (tag, row["n"], 1e3 * c.get("cold_s", np.nan),
                 1e3 * c.get("warm_s", np.nan), 1e3 * m.get("cold_s", np.nan),
                 1e3 * m.get("warm_s", np.nan),
                 1e3 * row["mumps-lu"].get("warm_s", np.nan),
                 row["pardiso_later_s"], c.get("rel_later", np.nan),
                 m.get("rel_later", np.nan)), flush=True)
    with open(a.out, "w") as f:
        json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
