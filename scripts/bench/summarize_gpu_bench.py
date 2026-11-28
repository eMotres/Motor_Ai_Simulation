"""Tables from gpu_solver_bench.py results (Markdown to stdout).

  python summarize_gpu_bench.py gpu_bench.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

BACKENDS = ("pardiso", "pardiso_spd", "superlu", "cudss_fp64", "cudss_fp64_spd", "cudss_fp32",
            "cudss_fp32_spd", "cudss_mixed", "cudss_mixed_spd", "cupy_qr", "cupy_gmres_jac")


def main():
    d = json.load(open(sys.argv[1]))
    R = defaultdict(dict)
    meta = {}
    for r in d["results"]:
        R[r["tag"]][r["backend"]] = r
        meta[r["tag"]] = (r["n"], r["nnz"], r.get("caller"))
    tags = sorted(R, key=lambda t: meta[t][0])
    print("GPU:", d["env"].get("gpu"), "| CUDA runtime", d["env"].get("cuda_runtime"),
          "| driver", d["env"].get("cuda_driver"), "| threads", d["env"].get("threads"))
    print()
    print("| system | n | nnz | CPU LU | CPU Chol | GPU LU FP64 | GPU Chol FP64 | GPU FP32 LU | "
          "GPU mixed LU | GPU mixed Chol | GPU analysis (once per pattern) | CPU analysis (LU) | "
          "GPU mem [MB] | best GPU ÷ CPU LU | best GPU ÷ CPU Chol |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    f = lambda v: "-" if v is None else "%.1f" % (1e3 * v)
    for t in tags:
        rr = R[t]
        c = lambda b, k="cycle_s": rr.get(b, {}).get(k)
        gpu = [c(b) for b in ("cudss_fp64", "cudss_fp64_spd") if c(b)]
        best = min(gpu) if gpu else None
        name = t.replace("_rated", "").replace("_later", "").replace(
            "fem_transient_sliding_band", "static Newton").replace("eddy_solve", "eddy bordered").replace(
            "eddy_static_state", "eddy static start").replace("pic2_sweeps", "Picard K")
        n, nnz, _ = meta[t]
        print(f"| {name} | {n:,} | {nnz:,} | {f(c('pardiso'))} | {f(c('pardiso_spd'))} | "
              f"{f(c('cudss_fp64'))} | {f(c('cudss_fp64_spd'))} | {f(c('cudss_fp32'))} | "
              f"{f(c('cudss_mixed'))} | {f(c('cudss_mixed_spd'))} | {f(c('cudss_fp64_spd', 'analyze_s'))} | "
              f"{f(c('pardiso', 'analyze_s'))} | {rr.get('cudss_fp64_spd', {}).get('gpu_mem_delta_MB', '-')} | "
              f"{'-' if not best else '%.1f×' % (c('pardiso') / best)} | "
              f"{'-' if not best else '%.1f×' % (c('pardiso_spd') / best)} |")
    print()
    print("Accuracy per backend (max over systems): FP64 relative residual ||b-Ax||/||b||, "
          "relative difference to the CPU PARDISO FP64 solution, refinement steps, errors")
    print()
    print("| backend | systems | max residual | max diff vs CPU x | IR steps (max) | errors / skipped |")
    print("|---|---:|---:|---:|---:|---|")
    for b in BACKENDS:
        rs = [R[t][b] for t in tags if b in R[t]]
        if not rs:
            continue
        ok = [r for r in rs if "rel_residual" in r]
        err = [r.get("error") or r.get("unavailable") or r.get("skipped") for r in rs if "rel_residual" not in r]
        irs = [max(r.get("ir_iters") or [0]) for r in ok if r.get("ir_iters")]
        print(f"| {b} | {len(ok)} | {max((r['rel_residual'] for r in ok), default=float('nan')):.1e} | "
              f"{max((r['rel_diff_vs_cpu'] for r in ok), default=float('nan')):.1e} | "
              f"{max(irs) if irs else '-'} | {len(err)}{(': ' + str(err[0])[:80]) if err else ''} |")


if __name__ == "__main__":
    main()
