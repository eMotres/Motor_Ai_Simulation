"""Compare the 3 symmetry modes (1/4, 1/2, full) over ONE electrical period
at 72 points: T_em(rotor angle), at the load operating point (I=120, gamma=0).
Saves _sweep72.json with per-mode mean torque + ripple."""
import json, numpy as np
from motor_ai_sim.simulation.fem_solver_2d import fem_solve_for_sim
from motor_ai_sim.simulation.geometry_2d import params_from_config

p = params_from_config()
pp = p.num_poles // 2                       # pole pairs (14)
period_mech = 360.0 / pp                     # one electrical period in mech deg (25.71)
N = 72
angles = np.linspace(0.0, period_mech, N, endpoint=False)
I_RMS, GAMMA = 120.0, 0.0

out = {"angles_mech_deg": angles.tolist(), "I_phase_rms": I_RMS,
       "gamma_deg": GAMMA, "n_points": N, "period_mech_deg": period_mech}
for ns in (4, 2, 1):
    T = []
    for k, a in enumerate(angles):
        r = fem_solve_for_sim(rotor_angle_deg=float(a), gamma_deg=GAMMA,
                              mesh_size_mm=4.0, n_sectors=ns, I_phase_rms=I_RMS)
        T.append(float(r["T_em_Nm"]))
        print("ns=%d  %2d/%d  ang=%.2f  T=%.2f" % (ns, k + 1, N, a, T[-1]), flush=True)
    T = np.array(T)
    mean = float(T.mean()); pp_t = float(T.max() - T.min())
    out["ns%d" % ns] = {"T": T.tolist(), "mean": mean, "min": float(T.min()),
                        "max": float(T.max()), "ripple_pp": pp_t,
                        "ripple_pct": float(100.0 * pp_t / abs(mean)) if mean else 0.0}
    print("### ns=%d: mean=%.3f Nm  ripple_pp=%.3f  (%.1f%%)"
          % (ns, mean, pp_t, out["ns%d" % ns]["ripple_pct"]), flush=True)

json.dump(out, open("_sweep72.json", "w"))
print("SAVED _sweep72.json", flush=True)
