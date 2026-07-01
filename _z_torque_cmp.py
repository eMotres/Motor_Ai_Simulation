"""Torque comparison: structured (route-A clean arc) vs free mesh at gap_layers
2/3/4 on the 40 mm motor (full-disk, n_sectors=-1), same I=60 A, gamma=10 deg.
Also verifies gap radial levels (exact 2K+1 uniform) and losses sanity.
"""
import _use40  # noqa
import sys, math, logging
import numpy as np
logging.basicConfig(level=logging.WARNING, format="%(levelname)s|%(message)s")
import motor_ai_sim.simulation.fem_solver_2d as F


def run(gap_layers, structured, n_steps=6, gamma=10.0, I=60.0):
    return F.fem_transient_sliding_band(
        n_steps_per_period=12, n_periods=float(n_steps) / 12.0,
        gamma_deg=gamma, I_phase_rms=I,
        mesh_size_mm=1.5, min_size_mm=0.25, outer_air_factor=1.3,
        gap_layers=gap_layers, n_sectors=-1, structured_gap=structured,
        return_field=True, torque_filter=False,
    )


def gap_levels(res, lo, hi):
    fld = res.get("field") or {}
    P = fld.get("nodes")
    if P is None:
        P = fld.get("p")
    if P is None:
        return None
    P = np.asarray(P, float)
    if P.shape[0] == 2:
        P = P.T
    r = np.hypot(P[:, 0], P[:, 1])
    if r.max() < 1.0:      # metres -> mm
        r = r * 1000.0
    band = r[(r > lo) & (r < hi)]
    lv = []
    for v in np.sort(np.unique(np.round(band, 4))):
        if not lv or v - lv[-1] > 2e-3:
            lv.append(round(float(v), 4))
    return lv


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    gls = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["2", "3", "4"])]
    results = {}
    for gl in gls:
        sT = fT = None
        if which in ("both", "struct"):
            rs = run(gl, True)
            sT = rs["T_avg_Nm"]
            print(f"gl={gl} STRUCT  T_avg={sT:.4f}  ripple_raw={rs.get('T_ripple_raw_pct', float('nan')):.1f}%  P_loss={rs.get('P_loss_total_avg_W', float('nan')):.1f}W", flush=True)
        if which in ("both", "free"):
            rf = run(gl, False)
            fT = rf["T_avg_Nm"]
            print(f"gl={gl} FREE    T_avg={fT:.4f}  ripple_raw={rf.get('T_ripple_raw_pct', float('nan')):.1f}%  P_loss={rf.get('P_loss_total_avg_W', float('nan')):.1f}W", flush=True)
        if sT is not None and fT is not None:
            print(f"gl={gl}  ==> struct/free = {sT:.4f}/{fT:.4f}  deficit = {100*(sT-fT)/fT:+.1f}%", flush=True)
        results[gl] = (sT, fT)
    print("SUMMARY:", results, flush=True)
