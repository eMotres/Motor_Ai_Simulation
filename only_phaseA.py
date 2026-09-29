"""Energise ONLY phase A (i_A = I, i_B = i_C = 0) and see where the rotor sits.

We monkeypatch build_materials so that whatever balanced triple the solver
computes, B and C are zeroed and only A's current survives.  gamma is chosen so
theta_e = 0 -> the solver's A current is at its peak I_coil_peak (DC on A).

Single-phase A still points the MMF along the phase-A axis, so the rotor d-axis
should settle at the SAME physical zero (~2.1 deg mech) as the balanced case —
only the magnitude/harmonics differ.  Let's verify."""
import math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"

pp = C.get_config()["geometry"].get("num_poles", 28) // 2
DSHIFT = fs.DAXIS_SHIFT_DEG

# ── monkeypatch: keep only phase-A current, zero B and C ──────────────────────
_orig_bm = fs.build_materials
def _only_A(I_ph, *a, **k):
    return _orig_bm({'A': float(I_ph.get('A', 0.0)), 'B': 0.0, 'C': 0.0}, *a, **k)

I = 250.0
thetas = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

print("ONLY phase A energised:  i_A = I_peak,  i_B = i_C = 0   (I_rms = %.0f A)" % I)
print(" rotor_mech |  i_A   i_B  i_C |  T_static [Nm]")
fs.build_materials = _only_A
Ts = []
for th in thetas:
    g = -(th * pp + DSHIFT)                      # theta_e = 0 -> i_A at peak
    r = fs.fem_solve_for_sim(rotor_angle_deg=float(th), gamma_deg=float(g),
                             mesh_size_mm=4.0, n_sectors=4, I_phase_rms=float(I))
    T = float(r["T_em_Nm"]); Ts.append(T)
    print("  %8.3f |  +1.0  0.0  0.0 | %+10.3f" % (th, T), flush=True)
fs.build_materials = _orig_bm   # restore

zc = None
for i in range(len(thetas)-1):
    if (Ts[i] > 0) and (Ts[i+1] <= 0):
        t0,t1,y0,y1 = thetas[i],thetas[i+1],Ts[i],Ts[i+1]
        zc = t0 - y0*(t1-t0)/(y1-y0); slope = (y1-y0)/(t1-t0); break
if zc is not None:
    print("\n=> ONLY-phase-A equilibrium (stable T=0) at rotor = %.3f deg mech"
          " (%.2f deg elec), slope %+.1f Nm/deg" % (zc, zc*pp, slope))
    print("   compare: balanced A,B,C gave ~2.10 deg;  open-circuit d-axis 2.143 deg")
else:
    print("\n=> no +to- crossing in [0,6]; T:", [round(x,2) for x in Ts])
