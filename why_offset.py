"""Why is the physical zero offset from the CAD zero?

Hypothesis: it is NOT a force.  It is a static geometric mismatch — the magnet
d-axis and the phase-A winding axis live on DIFFERENT angular grids in this
24-slot / 28-pole fractional machine (slot pitch 15deg, pole pitch 12.857deg),
so no magnet sits on the phase-A axis at the arbitrary CAD zero.

Prints:
  1. magnet N-pole centres vs phase-A (+/-) slot centres  -> different grids
  2. ideal phase-A MMF fundamental axis (p pole pairs)
  3. cogging torque T(theta) at I=0  -> shows slotting is a SMALL perturbation
"""
import cmath, math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.geometry_2d import build_winding_layout

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"

g = C.get_config()["geometry"]
num_slots = g.get("num_slots", 24)
num_poles = g.get("num_poles", 28)
pp = num_poles // 2
slot_pitch = 360.0 / num_slots
pole_pitch = 360.0 / num_poles

# 1. magnet N-pole centres (even index = +1 polarity) and phase-A slot centres
mag_centres = [(i*pole_pitch + pole_pitch/2.0, +1 if i % 2 == 0 else -1)
               for i in range(num_poles)]
N_centres = [round(a, 3) for a, pol in mag_centres if pol == +1]
print("slot pitch = %.4f deg   pole pitch = %.4f deg" % (slot_pitch, pole_pitch))
print("magnet N-pole centres [deg mech]:", N_centres)

layout = build_winding_layout(num_slots, pp, single_layer=True,
                              layout_str=cfg["winding"]["layout"])
A_plus  = [i*slot_pitch for i,(ph,d) in enumerate(layout) if ph == 'A' and d > 0]
A_minus = [i*slot_pitch for i,(ph,d) in enumerate(layout) if ph == 'A' and d < 0]
print("phase-A  +slots [deg mech]:", [round(x,1) for x in A_plus])
print("phase-A  -slots [deg mech]:", [round(x,1) for x in A_minus])

# 2. ideal phase-A MMF fundamental axis: S = sum d_k * exp(j*p*alpha_k)
S = 0j
for i,(ph,d) in enumerate(layout):
    if ph == 'A':
        S += d * cmath.exp(1j*math.radians(pp*(i*slot_pitch)))
axis_elec = math.degrees(cmath.phase(S))            # electrical deg
axis_mech = axis_elec / pp
print("phase-A MMF fundamental axis: %.2f deg elec  = %.3f deg mech" % (axis_elec, axis_mech))
# nearest N-pole to that axis
near = min(N_centres, key=lambda a: min(abs(a-axis_mech), abs(a-axis_mech-25.714), abs(a-axis_mech+25.714)))
print("nearest N-pole centre: %.3f deg mech  ->  gap = %.3f deg mech (%.1f deg elec)"
      % (near, near-axis_mech, (near-axis_mech)*pp))

# 3. cogging torque (I=0) over one electrical period
print("\nCOGGING torque (current = 0), one electrical period:")
print(" rotor_mech |  T_cog [Nm]")
for k in range(9):
    th = (360.0/pp) * k / 8.0
    r = fs.fem_solve_for_sim(rotor_angle_deg=float(th), gamma_deg=0.0,
                             mesh_size_mm=4.0, n_sectors=4, I_phase_rms=0.0)
    print("  %8.3f | %+8.3f" % (th, float(r["T_em_Nm"])), flush=True)
