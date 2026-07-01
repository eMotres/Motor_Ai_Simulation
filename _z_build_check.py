"""Build the FULL-DISK structured-gap mesh via the production transient entry
(fem_transient_sliding_band, n_sectors=-1) and report gap rings + torque.
Captures the solver's INFO logs so we see 'cells set transfinite / skipped' and
whether any filler is emitted.
"""
import _use40  # noqa
import sys, math, logging
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s|%(message)s")
import motor_ai_sim.simulation.fem_solver_2d as F


def run(gap_layers, structured, n_steps=6, gamma=10.0, I=60.0):
    # n_periods chosen so n_steps_per_period*n_periods = n_steps frames.
    res = F.fem_transient_sliding_band(
        n_steps_per_period=12, n_periods=float(n_steps) / 12.0,
        gamma_deg=gamma, I_phase_rms=I,
        mesh_size_mm=1.5, min_size_mm=0.25, outer_air_factor=1.3,
        gap_layers=gap_layers, n_sectors=-1, structured_gap=structured,
        return_field=True, torque_filter=False,
    )
    return res


if __name__ == "__main__":
    gl = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    struct = (sys.argv[2] != "free") if len(sys.argv) > 2 else True
    print(f"\n===== gap_layers={gl} structured={struct} =====", flush=True)
    res = run(gl, struct, n_steps=2)
    print("keys:", list(res.keys()), flush=True)
    T = res.get("torque")
    if T is not None:
        T = np.asarray(T)
        print(f"torque samples: {T}", flush=True)
        print(f"MEAN torque = {np.mean(T):.4f} Nm", flush=True)
    # gap radial levels from the field mesh, if present
    fld = res.get("field") or {}
    P = fld.get("nodes") or fld.get("p")
    if P is not None:
        P = np.asarray(P)
        if P.shape[0] == 2:
            P = P.T
        r = np.hypot(P[:, 0], P[:, 1]) * (1000.0 if np.max(np.hypot(P[:, 0], P[:, 1])) < 1 else 1.0)
        gap = r[(r > 12.05) & (r < 12.35)]
        lv = []
        for v in np.sort(np.unique(np.round(gap, 4))):
            if not lv or v - lv[-1] > 2e-3:
                lv.append(round(float(v), 4))
        print(f"gap radial levels in [12.05,12.35]: {len(lv)} -> {lv}", flush=True)
    print("DONE", flush=True)
