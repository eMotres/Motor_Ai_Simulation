"""Structural facts about exported FEM systems (run once on the dump directory).

  python analyze_matrices.py <matrices dir> [--out analysis.json]

Per matrix: n, nnz, nnz/row, bandwidth before/after RCM, symmetry
(||A-A^T||_F/||A||_F), diagonal signs, the eigenvalues nearest zero and the
largest one (shift-invert Lanczos on the symmetric part, which IS the matrix
when sym_rel ~ 0), hence definiteness and a 2-norm condition estimate, plus a
SuperLU fill ratio nnz(L+U)/nnz(A) with COLAMD for reference.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as sla
from scipy.sparse.csgraph import reverse_cuthill_mckee


def bandwidth(A):
    c = A.tocoo()
    return int(np.max(np.abs(c.row - c.col))) if c.nnz else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    out = []
    for mp in sorted(glob.glob(os.path.join(a.dir, "*_meta.json"))):
        m = json.load(open(mp))
        A = sp.load_npz(mp[:-len("_meta.json")] + "_A.npz").tocsr()
        n = A.shape[0]
        r = dict(tag=m["tag"], caller=m["caller"], n=n, nnz=int(A.nnz),
                 nnz_per_row=A.nnz / n, sym_rel=m["sym_rel"],
                 n_diag_le0=m["n_diag_le0"], bw=bandwidth(A))
        p = reverse_cuthill_mckee(A, symmetric_mode=True)
        r["bw_rcm"] = bandwidth(A[p][:, p])
        S = ((A + A.T) * 0.5).tocsc()
        t0 = time.time()
        try:
            lu = sla.splu(S)
            r["superlu_fill"] = (lu.L.nnz + lu.U.nnz) / A.nnz
            op = sla.LinearOperator(S.shape, matvec=lu.solve, dtype=float)
            mu = sla.eigsh(op, k=4, which="LM", return_eigenvectors=False, tol=1e-6)
            lam_small = np.sort(1.0 / mu)
            lam_max = sla.eigsh(S, k=1, which="LM", return_eigenvectors=False, tol=1e-4)[0]
            r["eig_nearest0"] = [float(v) for v in lam_small]
            r["eig_maxabs"] = float(lam_max)
            r["cond2_est"] = float(abs(lam_max) / np.min(np.abs(lam_small)))
            r["definite"] = ("positive" if lam_small.min() > 0 and lam_max > 0 else
                             "indefinite" if lam_small.min() < 0 < lam_max or lam_small.max() > 0 > lam_small.min()
                             else "negative?")
        except Exception as e:
            r["eig_error"] = repr(e)
        r["analysis_s"] = time.time() - t0
        print(json.dumps(r), flush=True)
        out.append(r)
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
