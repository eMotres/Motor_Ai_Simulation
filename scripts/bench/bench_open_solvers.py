"""Open-source sparse solver benchmark vs (previously measured) MKL PARDISO.

Runs inside the `solver-study:open` container (OpenBLAS-linked numpy/scipy,
scikit-sparse/CHOLMOD, python-mumps, scikit-umfpack -- no MKL anywhere in
this image). Loads real exported FEM systems written by profile_fem_run.py
(motor_ai_sim, branch perf/profiling-gpu-tdm): `<tag>_A.npz` (scipy CSR),
`<tag>_bx.npz` (b, and the CPU MKL-PARDISO FP64 reference solution x), and
`<tag>_meta.json` (n, nnz, sym_rel, and the PARDISO stats captured at
export time on this same server).

For each (matrix, backend): one symbolic analysis, then --repeat
numeric-factorize + solve cycles reusing that same analysis where the
library allows it (CHOLMOD does; SuperLU/MUMPS/UMFPACK redo more of the
symbolic work per call through the API used here -- noted per backend).
Median times reported. Accuracy: FP64 relative residual and relative
difference to the PARDISO x captured at export time.

  mamba run -n solvers python bench_open_solvers.py \
      --matrices /matrices --only d40_rated_s1_pic2_sweeps,... \
      --repeat 7 --threads 4 --out results/open_solver_bench.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import resource
import statistics
import time
import traceback

import numpy as np
import scipy.sparse as sp


def load(meta_path):
    m = json.load(open(meta_path))
    base = meta_path[: -len("_meta.json")]
    A = sp.load_npz(base + "_A.npz").tocsr()
    bx = np.load(base + "_bx.npz")
    return m, A, bx["b"], bx["x"]


def rel(a, b):
    nb = np.linalg.norm(b)
    return float(np.linalg.norm(a - b) / (nb if nb > 0 else 1.0))


def maxrss_mb():
    # ru_maxrss is KB on Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ---------------------------------------------------------------- backends --
class Backend:
    name = "base"
    needs_symmetric = False

    def analyze(self, A):
        raise NotImplementedError

    def factorize(self, A):
        raise NotImplementedError

    def solve(self, b):
        raise NotImplementedError

    def close(self):
        pass


class CholmodBackend(Backend):
    """AMD ordering, supernodal mode -- the weighted-cheapest combination at
    production sizes (17-45k DOF), see bench_cholmod.py for the full
    order x mode x thread sweep this was chosen from."""
    name = "cholmod"
    needs_symmetric = True

    def analyze(self, A):
        import sksparse.cholmod as cm
        # cho_factor does symbolic analysis + first numeric factorization in
        # one call; .factorize(A2) below reuses the symbolic step as long as
        # A2 has the same sparsity pattern (CHOLMOD does not check this --
        # the caller must, same as PARDISO's phase-11/23 split in
        # p2_nonlinear.py's _solve_reuse).
        self._factor = cm.cho_factor(A, order="amd", supernodal_mode="supernodal")

    def factorize(self, A):
        self._factor.factorize(A)

    def solve(self, b):
        return self._factor.solve(b)


class SuperLUBackend(Backend):
    name = "superlu"

    def analyze(self, A):
        # scipy's splu couples symbolic (COLAMD) + numeric in one call;
        # there is no public reuse-the-symbolic-only path, so "analyze" is
        # a no-op and every factorize() redoes ordering+numeric together.
        self._Acsc = None

    def factorize(self, A):
        self._lu = sp.linalg.splu(A.tocsc(), permc_spec="COLAMD")

    def solve(self, b):
        return self._lu.solve(b)


class UmfpackBackend(Backend):
    name = "umfpack"

    def analyze(self, A):
        import scikits.umfpack as um
        self._ctx = um.UmfpackContext()
        self._Acsc = A.tocsc()
        self._ctx.symbolic(self._Acsc)

    def factorize(self, A):
        self._Acsc = A.tocsc()
        self._ctx.numeric(self._Acsc)

    def solve(self, b):
        return self._ctx.solve(um_sys(), self._Acsc, b, autoTranspose=True)


def um_sys():
    import scikits.umfpack as um
    return um.UMFPACK_A


class MumpsBackend(Backend):
    name = "mumps"

    def __init__(self, symmetric=False):
        self._sym = symmetric

    def analyze(self, A):
        import mumps
        self._ctx = mumps.Context()
        self._ctx.set_matrix(A, symmetric=self._sym)
        self._ctx.analyze()

    def factorize(self, A):
        self._ctx.factor(a=A, reuse_analysis=True)

    def solve(self, b):
        return self._ctx.solve(b)

    def close(self):
        try:
            self._ctx.free()
        except Exception:
            pass


class MumpsSymBackend(MumpsBackend):
    name = "mumps_sym"

    def __init__(self):
        super().__init__(symmetric=True)


BACKEND_FACTORY = {
    "cholmod": CholmodBackend,
    "superlu": SuperLUBackend,
    "umfpack": UmfpackBackend,
    "mumps": MumpsBackend,
    "mumps_sym": MumpsSymBackend,
}


def bench_one(name, A, b, x_ref, repeat):
    rec = dict(backend=name)
    rss0 = maxrss_mb()
    try:
        be = BACKEND_FACTORY[name]()
    except Exception as e:
        rec["unavailable"] = repr(e)
        return rec
    try:
        t0 = time.perf_counter()
        be.analyze(A)
        rec["analyze_s"] = time.perf_counter() - t0
        fac, sol, tot = [], [], []
        x = None
        for _ in range(repeat):
            t0 = time.perf_counter()
            be.factorize(A)
            t1 = time.perf_counter()
            x = be.solve(b)
            t2 = time.perf_counter()
            fac.append(t1 - t0)
            sol.append(t2 - t1)
            tot.append(t2 - t0)
        rec.update(
            factorize_s=statistics.median(fac),
            solve_s=statistics.median(sol),
            cycle_s=statistics.median(tot),
            cycle_min_s=min(tot),
            first_cycle_s=tot[0],
        )
        r = b - A @ x
        rec["rel_residual"] = float(np.linalg.norm(r) / max(np.linalg.norm(b), 1e-300))
        rec["rel_diff_vs_pardiso_x"] = rel(x, x_ref)
        rec["maxrss_delta_MB"] = maxrss_mb() - rss0
    except Exception as e:
        rec["error"] = repr(e)
        rec["trace"] = traceback.format_exc()[-2000:]
    finally:
        try:
            be.close()
        except Exception:
            pass
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrices", required=True)
    ap.add_argument("--backends", default="cholmod,superlu,umfpack,mumps,mumps_sym")
    ap.add_argument("--repeat", type=int, default=7)
    ap.add_argument("--only", default="", help="comma list of tag substrings")
    ap.add_argument("--max-n-umfpack", type=int, default=200000)
    ap.add_argument("--max-n-mumps", type=int, default=200000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    metas = sorted(glob.glob(os.path.join(a.matrices, "*_meta.json")))
    if a.only:
        subs = [s for s in a.only.split(",") if s]
        metas = [m for m in metas if any(s in m for s in subs)]

    res = dict(env=dict(numpy=np.__version__, scipy=sp.__name__ and __import__("scipy").__version__,
                         omp=os.environ.get("OMP_NUM_THREADS"),
                         openblas=os.environ.get("OPENBLAS_NUM_THREADS")),
               results=[])
    for mp in metas:
        m, A, b, x_ref = load(mp)
        print(f"{m['tag']}: n={A.shape[0]} nnz={A.nnz} sym_rel={m['sym_rel']:.1e}", flush=True)
        for be_name in [s.strip() for s in a.backends.split(",") if s.strip()]:
            if be_name == "umfpack" and A.shape[0] > a.max_n_umfpack:
                continue
            if be_name.startswith("mumps") and A.shape[0] > a.max_n_mumps:
                continue
            if be_name in ("cholmod", "mumps_sym") and m["sym_rel"] > 1e-10:
                res["results"].append(dict(tag=m["tag"], n=A.shape[0], nnz=A.nnz,
                                            backend=be_name, skipped="matrix not symmetric"))
                continue
            r = bench_one(be_name, A, b, x_ref, a.repeat)
            r.update(tag=m["tag"], n=int(A.shape[0]), nnz=int(A.nnz), caller=m["caller"],
                      case=m["case"], mesh_scale=m["mesh_scale"], sym_rel=m["sym_rel"],
                      pardiso_ref=m.get("pardiso"))
            res["results"].append(r)
            print("   ", {k: (round(v, 5) if isinstance(v, float) else v)
                          for k, v in r.items() if k not in ("trace", "pardiso_ref")}, flush=True)
            json.dump(res, open(a.out, "w"), indent=1, default=str)
    json.dump(res, open(a.out, "w"), indent=1, default=str)
    print("done ->", a.out)


if __name__ == "__main__":
    main()
