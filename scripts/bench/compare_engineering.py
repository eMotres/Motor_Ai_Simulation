"""Compare engineering outputs of two full runs (CPU PARDISO FP64 vs a GPU backend).

  python compare_engineering.py cpu.json gpu.json [--tol 1e-6]

Both files come from profile_fem_run.py (the GPU one with --backend ...).
Reports every scalar the solver returned (torque, ripple, flux linkage, EMF /
terminal voltage, Ld/Lq when inc_ldq ran, peak B, losses ...) with its
relative difference, the torque/flux waveforms' max deviation, and a verdict:
PASS when every headline quantity is within --tol relative (default 1e-6,
i.e. far below any engineering tolerance and near the run-to-run noise of
threaded PARDISO, ~1e-12..1e-9 on whole runs).
"""
from __future__ import annotations

import argparse
import json

import numpy as np

HEADLINE = ("T_avg_Nm", "T_ripple_pct", "V_peak", "V_line_peak_solved_V", "psi_pm_Wb",
            "Ld_mH", "Lq_mH", "Ld_H", "Lq_H", "B_peak_T", "B_max_T", "P_mag_solve_W",
            "P_shaft_solve_W", "P_sleeve_solve_W", "P_cu_ac_W", "P_cu_ac_solve_W", "P_fe_W",
            "P_fe_avg_W", "P_loss_total_avg_W", "P_mech_avg_W", "efficiency")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("test")
    ap.add_argument("--tol", type=float, default=1e-6)
    ap.add_argument("--all", action="store_true", help="print every scalar, not only headline/over-tol")
    a = ap.parse_args()
    R, G = json.load(open(a.ref)), json.load(open(a.test))
    rs, gs = R.get("all_scalars", {}), G.get("all_scalars", {})
    print(f"ref {R.get('backend')} wall {R['wall_s']:.1f} s  |  test {G.get('backend')} "
          f"wall {G['wall_s']:.1f} s  (x{R['wall_s'] / G['wall_s']:.2f})")
    print(f"linear-solve time: {R['solve_ff_total_s']:.1f} s vs {G['solve_ff_total_s']:.1f} s")
    worst = 0.0
    fails = []
    rows = []
    for k in sorted(set(rs) & set(gs)):
        x, y = rs[k], gs[k]
        d = abs(y - x) / max(abs(x), 1e-12)
        head = any(k == h or k.endswith("." + h) for h in HEADLINE)
        if head:
            worst = max(worst, d)
            if d > a.tol:
                fails.append(k)
        if a.all or head or d > a.tol:
            rows.append((k, x, y, d, head))
    for k, x, y, d, head in rows:
        print(f"{'*' if head else ' '} {k:45s} {x:16.9g} {y:16.9g}  rel {d:.2e}")
    for k in sorted(set(R.get("series", {})) & set(G.get("series", {}))):
        x = np.asarray(R["series"][k]); y = np.asarray(G["series"][k])
        if x.shape == y.shape and x.size:
            sc = max(np.max(np.abs(x)), 1e-30)
            print(f"  series {k:20s} max|diff|/max|ref| = {np.max(np.abs(y - x)) / sc:.2e}")
        else:
            print(f"  series {k}: shapes differ {x.shape} vs {y.shape} (frame count changed!)")
    fr, fg = R["results"].get("n_frames_solved"), G["results"].get("n_frames_solved")
    print(f"frames solved: {fr} vs {fg}")
    ok = not fails and fr == fg
    print(f"VERDICT: {'PASS' if ok else 'FAIL'}  worst headline rel diff {worst:.2e}"
          + (f"; over tol: {fails}" if fails else ""))


if __name__ == "__main__":
    main()
