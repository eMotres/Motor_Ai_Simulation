"""1-parameter optimisation of the d-axis alignment angle.

Convention fixed at I_A=0 (DAXIS_SHIFT_DEG=270).  We then find — by simulation —
the load angle γ that MAXIMISES torque (the true q-axis).  That γ* is the residual
d-axis↔phase-A misalignment: rotating the rotor reference by γ* makes γ=0 land on
BOTH I_A=0 and peak torque.  Golden-section search (same principle as the optimiser,
one parameter = the angle)."""
import math
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"
fs.DAXIS_SHIFT_DEG = 270.0     # I_A=0 convention

# Torque at a load angle γ — average over one electrical period (true q-axis metric).
_cache = {}
def torque(gamma):
    g = round(gamma, 2)
    if g in _cache:
        return _cache[g]
    d = fs.fem_transient_sliding_band(
        eddy=False, n_steps_per_period=12, n_periods=1.0, I_phase_rms=120.0,
        n_sectors=4, coil_temp_c=120.0, mesh_size_mm=3.0, gamma_deg=g)
    T = float(d["T_avg_Nm"]); _cache[g] = T
    print("    gamma=%+6.2f  T_avg=%7.2f Nm" % (g, T), flush=True)
    return T

# ── Golden-section maximisation on γ ∈ [lo, hi] ──────────────────────────────
gr = (math.sqrt(5) - 1) / 2
lo, hi = -60.0, 0.0
c = hi - gr * (hi - lo); d = lo + gr * (hi - lo)
fc, fd = torque(c), torque(d)
print("golden-section search for the q-axis (max torque):", flush=True)
for _ in range(7):
    if fc > fd:
        hi, d, fd = d, c, fc
        c = hi - gr * (hi - lo); fc = torque(c)
    else:
        lo, c, fc = c, d, fd
        d = lo + gr * (hi - lo); fd = torque(d)
gstar = (lo + hi) / 2
Tstar = torque(gstar)
print("\n=> q-axis at gamma* = %.2f deg el  (T_max = %.2f Nm)" % (gstar, Tstar))
print("   residual d-axis↔phaseA offset = %.2f deg el = %.3f deg mech (pp=14)"
      % (gstar, gstar / 14.0))
print("   => to align: add %.2f deg el to the rotor reference so gamma=0 = peak"
      % (-gstar))
print("   (then DAXIS_SHIFT_DEG=270 gives I_A=0 AND max torque at gamma=0)")
