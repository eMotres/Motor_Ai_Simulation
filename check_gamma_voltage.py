"""Empirical check of the γ (load-angle) sign convention.

For a surface-PM machine, MTPA (max torque) is at i_d = 0 (q-axis).  Adding a
DEMAGNETISING d-current (field weakening) lowers the net d-axis flux linkage →
lowers the back-EMF → lowers the terminal voltage.  Adding a MAGNETISING
d-current (field strengthening) raises flux → raises voltage.

So: the γ direction where V_rms DROPS is field weakening; where it RISES is
strengthening.  This script measures T_avg and V_rms vs γ so we can read the
convention straight off the numbers (no theory hand-waving)."""
import math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"

print("DAXIS_SHIFT_DEG =", fs.DAXIS_SHIFT_DEG)
print("gamma |  I_A(rotor=0) |  T_avg [Nm] |  V_rms [V] |  Vpk [V] | |psi|_rms")
print("-" * 78)

def rms(x):
    a = np.asarray(x, float)
    return float(np.sqrt(np.mean(a * a)))

pole_pairs = C.get_config()["geometry"].get("num_poles", 28) // 2
for g in (-40, -30, -20, -10, 0, 10, 20, 30, 40):
    # phase-A current at rotor=0 with the same convention the solver uses
    te = math.radians(0 * pole_pairs + g + fs.DAXIS_SHIFT_DEG)
    iA0 = math.cos(te)
    d = fs.fem_transient_sliding_band(
        eddy=False, n_steps_per_period=12, n_periods=1.0, I_phase_rms=120.0,
        n_sectors=4, coil_temp_c=120.0, mesh_size_mm=4.0, gamma_deg=float(g))
    T = float(d["T_avg_Nm"])
    Vr = rms(d["V_A"]); Vpk = float(d["V_peak"])
    psiR = rms(d["psi_A_Wb"])
    print("%+5d | %+12.3f | %11.2f | %10.2f | %8.1f | %8.4f"
          % (g, iA0, T, Vr, Vpk, psiR), flush=True)
