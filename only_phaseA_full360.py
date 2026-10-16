"""FULL 360 machine (n_sectors=1, NO sector symmetry), only phase A energised.

This removes the 90-degree anti-periodic sector model entirely.  If the rotor
equilibrium still sits at ~2 deg mech, the offset is a real geometric property of
the winding+magnet registration.  If it collapses to ~0, the sector model was the
culprit and the user's symmetry argument wins."""
import math
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"

pp = C.get_config()["geometry"].get("num_poles", 28) // 2
DSHIFT = fs.DAXIS_SHIFT_DEG

_orig_bm = fs.build_materials
def _only_A(I_ph, *a, **k):
    return _orig_bm({'A': float(I_ph.get('A', 0.0)), 'B': 0.0, 'C': 0.0}, *a, **k)

I = 250.0
thetas = [-2.0, 0.0, 2.0, 4.0]

print("FULL 360 model (n_sectors=1), ONLY phase A:  i_A=I, i_B=i_C=0  (I=%.0f A)" % I)
print(" rotor_mech |  T_static [Nm]")
fs.build_materials = _only_A
Ts = []
for th in thetas:
    g = -(th * pp + DSHIFT)
    r = fs.fem_solve_for_sim(rotor_angle_deg=float(th), gamma_deg=float(g),
                             mesh_size_mm=4.0, n_sectors=1, I_phase_rms=float(I))
    T = float(r["T_em_Nm"]); Ts.append(T)
    print("  %8.3f | %+10.3f" % (th, T), flush=True)
fs.build_materials = _orig_bm

# interpolate zero crossing near the +to- transition
zc = None
for i in range(len(thetas)-1):
    if (Ts[i] > 0) and (Ts[i+1] <= 0):
        t0,t1,y0,y1 = thetas[i],thetas[i+1],Ts[i],Ts[i+1]
        zc = t0 - y0*(t1-t0)/(y1-y0); break
print("\n full-360 only-A torque at rotor=0 : %+.3f Nm" % Ts[thetas.index(0.0)])
if zc is not None:
    print(" full-360 only-A equilibrium       : %.3f deg mech (%.1f deg elec)" % (zc, zc*pp))
print(" (sector 90deg model gave: T(0)=+34 Nm, equilibrium 2.02 deg)")
