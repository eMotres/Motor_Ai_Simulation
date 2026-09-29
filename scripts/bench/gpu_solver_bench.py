"""Stage-2 benchmark: exported real FEM matrices, CPU PARDISO vs GPU backends.

  python gpu_solver_bench.py --matrices C:\\Users\\vadim\\Downloads\\solver_matrices \
      --backends pardiso,pardiso_spd,superlu,cudss_fp64,cudss_fp64_spd,cudss_mixed,cudss_fp32,cupy_qr,cupy_gmres_jac \
      --repeat 7 --out gpu_bench.json

For each matrix (``*_meta.json`` written by profile_fem_run.py --dump):
  * analysis once (pattern), then ``--repeat`` numeric factorisations + solves
    with the SAME pattern - that is the production cycle (one analysis per
    pattern, phase 23 per Newton iteration).  Median times are reported;
  * transfer: host->device bytes and time, device->host of the solution;
  * device memory in use after factorisation (cudaMemGetInfo delta);
  * accuracy: FP64 relative residual ||b - A x|| / ||b|| on the host and the
    relative difference to the CPU PARDISO FP64 solution captured at dump time;
  * break-even: per backend, the smallest n at which (factorise + solve +
    transfers) beats PARDISO phase 22+33 on this machine.

A backend that cannot be imported is recorded as unavailable, not fatal.
Run on the owner's PC in the night window only (see night_gpu_bench.ps1).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import statistics
import time
import traceback

import numpy as np
import scipy.sparse as sp

from gpu_backends import make_backend, gpu_mem_used


def load(meta_path):
    m = json.load(open(meta_path))
    base = meta_path[:-len("_meta.json")]
    A = sp.load_npz(base + "_A.npz").tocsr()
    bx = np.load(base + "_bx.npz")
    return m, A, bx["b"], bx["x"]


def rel(a, b):
    nb = np.linalg.norm(b)
    return float(np.linalg.norm(a - b) / (nb if nb > 0 else 1.0))


def env_info():
    info = dict(platform=platform.platform(), python=platform.python_version(),
                cpu=platform.processor(),
                threads={k: os.environ.get(k) for k in ("MKL_NUM_THREADS", "OMP_NUM_THREADS")})
    try:
        import cupy as cp
        p = cp.cuda.runtime.getDeviceProperties(0)
        info["gpu"] = p["name"].decode() if isinstance(p["name"], bytes) else p["name"]
        info["cc"] = f"{p['major']}.{p['minor']}"
        info["cuda_runtime"] = cp.cuda.runtime.runtimeGetVersion()
        info["cuda_driver"] = cp.cuda.runtime.driverGetVersion()
        info["cupy"] = cp.__version__
    except Exception as e:
        info["gpu"] = f"unavailable: {e!r}"
    try:
        import nvmath
        info["nvmath"] = nvmath.__version__
    except Exception:
        pass
    return info


def bench_one(name, A, b, x_ref, repeat):
    rec = dict(backend=name)
    try:
        mem0 = gpu_mem_used()
        be = make_backend(name)
    except Exception as e:
        rec["unavailable"] = repr(e)
        return rec
    try:
        t0 = time.perf_counter()
        be.analyze(A)
        rec["analyze_s"] = time.perf_counter() - t0
        fac, sol, h2d, tot = [], [], [], []
        x = None
        for _ in range(repeat):
            t_before = dict(be.t)
            t0 = time.perf_counter()
            be.factorize(A)
            t1 = time.perf_counter()
            x = be.solve(b)
            t2 = time.perf_counter()
            fac.append(t1 - t0); sol.append(t2 - t1); tot.append(t2 - t0)
            h2d.append(be.t["h2d"] - t_before["h2d"] + be.t["d2h"] - t_before["d2h"])
        rec.update(factorize_s=statistics.median(fac), solve_s=statistics.median(sol),
                   transfer_s=statistics.median(h2d), cycle_s=statistics.median(tot),
                   cycle_min_s=min(tot), first_cycle_s=tot[0])
        mem1 = gpu_mem_used()
        if mem0 is not None and mem1 is not None:
            rec["gpu_mem_delta_MB"] = (mem1 - mem0) / 2 ** 20
        r = b - A @ x
        rec["rel_residual"] = float(np.linalg.norm(r) / max(np.linalg.norm(b), 1e-300))
        rec["rel_diff_vs_cpu"] = rel(x, x_ref)
        for k in ("info", "ir_iters", "iters"):
            v = getattr(be, k, None)
            if v:
                rec[k] = v if k == "info" else v[-min(len(v), 8):]
    except Exception as e:
        rec["error"] = repr(e)
        rec["trace"] = traceback.format_exc()[-2000:]
    finally:
        try:
            be.close()
        except Exception:
            pass
    return rec


def break_even(results):
    """Smallest n per backend whose cycle beats PARDISO on the same matrix."""
    out = {}
    by_m = {}
    for r in results:
        by_m.setdefault(r["tag"], {})[r["backend"]] = r
    for tag, d in by_m.items():
        ref = d.get("pardiso", {}).get("cycle_s")
        if not ref:
            continue
        n = d["pardiso"]["n"]
        for be, r in d.items():
            if be == "pardiso" or "cycle_s" not in r:
                continue
            if r["cycle_s"] < ref:
                out[be] = min(out.get(be, 10 ** 12), n)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrices", required=True)
    ap.add_argument("--backends", default="pardiso,superlu,cudss_fp64,cudss_mixed,cudss_fp32")
    ap.add_argument("--repeat", type=int, default=7)
    ap.add_argument("--only", default="", help="substring filter on the matrix tag")
    ap.add_argument("--max-n-superlu", type=int, default=400000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    metas = sorted(glob.glob(os.path.join(a.matrices, "*_meta.json")))
    if a.only:
        metas = [m for m in metas if a.only in m]
    res = dict(env=env_info(), results=[])
    for mp in metas:
        m, A, b, x_ref = load(mp)
        print(f"{m['tag']}: n={A.shape[0]} nnz={A.nnz} sym_rel={m['sym_rel']:.1e}", flush=True)
        for be in [s.strip() for s in a.backends.split(",") if s.strip()]:
            if be == "superlu" and A.shape[0] > a.max_n_superlu:
                continue
            if be in ("cudss_fp64_sym", "cudss_fp64_spd", "pardiso_spd", "pardiso_sym") and m["sym_rel"] > 1e-12:
                res["results"].append(dict(tag=m["tag"], n=A.shape[0], nnz=A.nnz, backend=be,
                                           skipped="matrix not symmetric"))
                continue
            r = bench_one(be, A, b, x_ref, a.repeat)
            r.update(tag=m["tag"], n=int(A.shape[0]), nnz=int(A.nnz), caller=m["caller"],
                     case=m["case"], mesh_scale=m["mesh_scale"])
            res["results"].append(r)
            print("   ", {k: (round(v, 5) if isinstance(v, float) else v)
                          for k, v in r.items() if k not in ("trace", "info")}, flush=True)
            json.dump(res, open(a.out, "w"), indent=1, default=str)
    res["break_even_n"] = break_even(res["results"])
    json.dump(res, open(a.out, "w"), indent=1, default=str)
    print("break-even n:", res["break_even_n"])


if __name__ == "__main__":
    main()
