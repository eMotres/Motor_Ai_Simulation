"""Physical zero under LOAD, large currents, to swamp the cogging torque.

Frozen current vector on the phase-A axis: i_A=+I, i_B=i_C=-I/2 (theta_e=0).
Cogging (~+-8 Nm) is current-INDEPENDENT; the magnet x current alignment torque
scales with I.  So as I grows the equilibrium (stable T=0 crossing) converges to
the pure d-axis<->phase-A alignment, unperturbed by slotting.  Find that zero for
several currents and watch it converge."""
import math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"

pp = C.get_config()["geometry"].get("num_poles", 28) // 2
DSHIFT = fs.DAXIS_SHIFT_DEG
thetas = [0.0, 1.0, 2.0, 3.0, 4.0]     # rotor mech angles to bracket the zero
currents = [120.0, 250.0, 400.0]       # phase RMS [A]

print("Frozen vector i_A=+I, i_B=i_C=-I/2 (theta_e=0).  Cogging ~ +-8 Nm.")
for I in currents:
    print("\n=== I_phase_rms = %.0f A ===" % I)
    print(" rotor_mech |  T_static [Nm]")
    Ts = []
    for th in thetas:
        g = -(th * pp + DSHIFT)                 # freeze current vector on phase A
        r = fs.fem_solve_for_sim(rotor_angle_deg=float(th), gamma_deg=float(g),
                                 mesh_size_mm=4.0, n_sectors=4, I_phase_rms=float(I))
        T = float(r["T_em_Nm"]); Ts.append(T)
        print("  %8.3f | %+10.3f" % (th, T), flush=True)
    # interpolate the first + -> - (stable) zero crossing
    zc = None
    for i in range(len(thetas)-1):
        if (Ts[i] > 0) and (Ts[i+1] <= 0):
            t0,t1,y0,y1 = thetas[i],thetas[i+1],Ts[i],Ts[i+1]
            zc = t0 - y0*(t1-t0)/(y1-y0)
            slope = (y1-y0)/(t1-t0)
            break
    if zc is not None:
        print("  -> stable zero (physical zero) at rotor = %.3f deg mech"
              " (%.2f deg elec), slope %+.1f Nm/deg" % (zc, zc*pp, slope))
    else:
        print("  -> no + to - crossing in [0,4]; widen range")
