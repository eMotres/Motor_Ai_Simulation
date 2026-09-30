"""Tabulate e2e_linear_backend.py results: PARDISO against the open backends.

usage: python e2e_table.py OUTDIR   (expects <case>_<backend>.json files)
Licence: same as the repository.
"""
import glob
import json
import os
import sys

import numpy as np

CASES = [("d40", "Ø40 L12 rated, eddy march"), ("l13", "L13 rated, eddy march"),
         ("l155", "L155 rated, eddy march"), ("l155s", "L155 rated, static Newton"),
         ("l155tdm", "L155 rated, TDM")]
BACKENDS = ["pardiso", "open", "cholmod", "base"]


def rel(a, b):
    if a is None or b is None:
        return None
    return abs(a - b) / max(abs(b), 1e-300)


def series_rel(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.size == 0 or a.shape != b.shape:
        return None
    return float(np.max(np.abs(a - b)) / max(np.max(np.abs(b)), 1e-300))


def main(d):
    runs = {}
    for f in glob.glob(os.path.join(d, "*.json")):
        k = os.path.basename(f)[:-5]
        if "_" not in k:
            continue
        case, be = k.rsplit("_", 1)
        if be in BACKENDS:
            try:
                runs[(case, be)] = json.load(open(f))
            except ValueError:
                pass
    print("| case | backend | wall s | linear solves s (share) | frames solved | peak RSS MB "
          "| wall ÷ PARDISO | Coulomb mean N·m | rel. diff. | ripple % | rel. diff. "
          "| total loss W | rel. diff. | max rel. diff. torque series |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for case, label in CASES:
        ref = runs.get((case, "pardiso"))
        for be in BACKENDS:
            r = runs.get((case, be))
            if r is None:
                continue
            ls = r.get("linear_s") or 0.0
            share = ("%.0f (%.0f %%)" % (ls, 100 * r["linear_share"])
                     if r.get("linear_calls") else "n/a")
            wr = (r["wall_s"] / ref["wall_s"]) if ref else None

            def fmt(v, p="%.6g"):
                return "—" if v is None else p % v
            T = r.get("T_avg_Nm")
            rp = r.get("T_ripple_pct")
            P = r.get("P_loss_total_avg_W")
            print("| %s | %s%s | %.0f | %s | %s | %.0f | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                label, be, "" if be != "base" else " (unchanged code)", r["wall_s"], share,
                r.get("n_frames_solved"), r.get("peak_rss_MB") or 0,
                fmt(wr, "%.2f"), fmt(T, "%.6f"),
                fmt(rel(T, ref and ref.get("T_avg_Nm")), "%.1e") if be != "pardiso" else "ref",
                fmt(rp, "%.4f"),
                fmt(rel(rp, ref and ref.get("T_ripple_pct")), "%.1e") if be != "pardiso" else "ref",
                fmt(P, "%.3f"),
                fmt(rel(P, ref and ref.get("P_loss_total_avg_W")), "%.1e") if be != "pardiso" else "ref",
                fmt(series_rel(r.get("T_em_Nm"), ref and ref.get("T_em_Nm")), "%.1e")
                if be != "pardiso" else "ref"))
    print()
    for (case, be), r in sorted(runs.items()):
        ls = r.get("linear_solver") or {}
        print(case, be, "backend=%s spd=%s lu=%s chol=%s/%s lu=%s/%s declined=%s failures=%s/%s "
              "mkl_loaded=%s warmup=%s settled=%s notes=%s" % (
                  ls.get("backend"), ls.get("spd_backend"), ls.get("lu_backend"),
                  ls.get("cholesky_solves"), ls.get("cholesky_analyses"),
                  ls.get("lu_solves"), ls.get("lu_analyses"), ls.get("cholesky_declined"),
                  ls.get("cholesky_failures"), ls.get("lu_failures"), r.get("mkl_loaded"),
                  r.get("eddy_warmup_frames"), r.get("eddy_settled"), ls.get("notes")))


if __name__ == "__main__":
    main(sys.argv[1])
