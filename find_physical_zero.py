"""Find the PHYSICAL ZERO of the rotor.

Definition (user): the rotor position where, holding a frozen current vector
on the phase-A axis (i_A = +I_max, i_B = i_C = -I_max/2), the rotor sits in
equilibrium — i.e. the rotor d-axis is aligned with the phase-A winding axis.
At that rotor angle the holding torque T = 0 with a STABLE (restoring) slope
dT/dθ < 0, and the phase-A flux linkage is maximal.

Method: freeze the current vector at the phase-A peak by choosing, for each
rotor angle θ, gamma = -(θ·pole_pairs + DAXIS_SHIFT_DEG) so that
  theta_e = θ·pp + gamma + DAXIS_SHIFT = 0  →  i_A = cos(0) = max,
                                               i_B = i_C = cos(±120°) = -0.5.
Then sweep θ over one full electrical period (360°/pp) and record the static
Maxwell-stress torque.  The stable zero crossing is the physical zero."""
import math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"

pole_pairs = C.get_config()["geometry"].get("num_poles", 28) // 2
elec_period_mech = 360.0 / pole_pairs
print("pole_pairs =", pole_pairs, " electrical period =", round(elec_period_mech, 4), "deg mech")
print("DAXIS_SHIFT_DEG =", fs.DAXIS_SHIFT_DEG)
print("Frozen current vector on phase-A axis: i_A=+Imax, i_B=i_C=-Imax/2")
print()
print(" rotor_mech | i_A    i_B    i_C   |  T_static [Nm]")
print("-" * 56)

rows = []
N = 14  # points over one electrical period
for k in range(N + 1):
    th = elec_period_mech * k / N           # rotor mechanical angle [deg]
    g = -(th * pole_pairs + fs.DAXIS_SHIFT_DEG)   # freeze current vector at phase-A peak
    r = fs.fem_solve_for_sim(
        rotor_angle_deg=float(th), gamma_deg=float(g),
        mesh_size_mm=4.0, n_sectors=4, I_phase_rms=120.0)
    T = float(r["T_em_Nm"])
    te = math.radians(th * pole_pairs + g + fs.DAXIS_SHIFT_DEG)
    iA, iB, iC = math.cos(te), math.cos(te - 2*math.pi/3), math.cos(te + 2*math.pi/3)
    rows.append((th, T))
    print(" %9.3f | %+.3f %+.3f %+.3f | %+10.3f" % (th, iA, iB, iC, T), flush=True)

# locate zero crossings + slope
print()
ths = np.array([r[0] for r in rows]); Ts = np.array([r[1] for r in rows])
for i in range(len(ths) - 1):
    if Ts[i] == 0 or (Ts[i] < 0) != (Ts[i+1] < 0):
        # linear-interpolate the crossing
        t0, t1 = ths[i], ths[i+1]; y0, y1 = Ts[i], Ts[i+1]
        zc = t0 - y0 * (t1 - t0) / (y1 - y0) if y1 != y0 else t0
        slope = (y1 - y0) / (t1 - t0)
        kind = "STABLE (d-axis aligned ← physical zero)" if slope < 0 else "unstable (anti-aligned / q-axis)"
        print("  zero crossing at rotor = %7.3f deg mech  (slope %+.2f Nm/deg) -> %s"
              % (zc, slope, kind))
