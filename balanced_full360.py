"""Decisive test: BALANCED frozen current (i_A=I, i_B=i_C=-I/2) in the FULL 360
machine (n_sectors=1).  Does the equilibrium sit at rotor=0 (=> the whole 2.1 deg
offset was a 90-degree sector-model artifact) or at ~2.1 deg (=> real)?

Balanced excitation should produce a LARGE torque (~60 Nm at 250 A), so this also
validates that the full-360 model itself is working (unlike the ~0 single-phase
case, which would otherwise be ambiguous)."""
import math
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"

pp = C.get_config()["geometry"].get("num_poles", 28) // 2
DSHIFT = fs.DAXIS_SHIFT_DEG

I = 250.0
thetas = [-4.0, -2.0, 0.0, 2.0, 4.0, 6.0]

print("FULL 360 model (n_sectors=1), BALANCED i_A=I, i_B=i_C=-I/2  (I=%.0f A)" % I)
print(" rotor_mech |  T_static [Nm]")
Ts = []
for th in thetas:
    g = -(th * pp + DSHIFT)                 # freeze balanced vector on phase A
    r = fs.fem_solve_for_sim(rotor_angle_deg=float(th), gamma_deg=float(g),
                             mesh_size_mm=4.0, n_sectors=1, I_phase_rms=float(I))
    T = float(r["T_em_Nm"]); Ts.append(T)
    print("  %8.3f | %+10.3f" % (th, T), flush=True)

# find + -> - (stable) crossing
zc = None
for i in range(len(thetas)-1):
    if (Ts[i] > 0) and (Ts[i+1] <= 0):
        t0,t1,y0,y1 = thetas[i],thetas[i+1],Ts[i],Ts[i+1]
        zc = t0 - y0*(t1-t0)/(y1-y0); slope=(y1-y0)/(t1-t0); break
print("\n full-360 balanced T at rotor=0 : %+.3f Nm" % Ts[thetas.index(0.0)])
if zc is not None:
    print(" full-360 balanced equilibrium  : %.3f deg mech (%.1f deg elec), slope %+.1f"
          % (zc, zc*pp, slope))
print(" (sector 90deg model gave: T(0)=+60.4 Nm, equilibrium 2.10 deg)")
