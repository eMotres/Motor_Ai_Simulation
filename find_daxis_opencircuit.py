"""Locate the EFFECTIVE rotor d-axis for the tangential (spoke) magnetisation.

Physical definition, no assumptions about geometry: the rotor d-axis is aligned
with phase A at the rotor angle where the OPEN-CIRCUIT (current = 0) phase-A
flux linkage psi_A is MAXIMUM (N-pole flux fully linking phase A).

For a spoke/flux-concentration rotor the pole is in the IRON between magnets, so
this is the only honest way to find it — we let the magnet field itself tell us
where the pole points.  Run a current-free transient over one electrical period
and read off argmax(psi_A)."""
import numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs

cfg = C.get_config(); cfg.setdefault("winding", {})
cfg["winding"]["layers"] = 1
cfg["winding"]["layout"] = "A|a|c|C|B|b|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"

pole_pairs = C.get_config()["geometry"].get("num_poles", 28) // 2
elec_period_mech = 360.0 / pole_pairs
print("pole_pairs =", pole_pairs, " elec period =", round(elec_period_mech, 4), "deg mech")

d = fs.fem_transient_sliding_band(
    eddy=False, n_steps_per_period=24, n_periods=1.0, I_phase_rms=0.0,
    n_sectors=4, coil_temp_c=120.0, mesh_size_mm=4.0, gamma_deg=0.0)

th = np.asarray(d["rotor_angle_deg"], float)
pA = np.asarray(d["psi_A_Wb"], float)
pB = np.asarray(d["psi_B_Wb"], float)
pC = np.asarray(d["psi_C_Wb"], float)

print("\n rotor_mech |  psi_A     psi_B     psi_C   [Wb]")
for i in range(len(th)):
    mark = "  <-- psi_A MAX" if i == int(np.argmax(pA)) else ""
    print("  %8.3f | %+8.5f %+8.5f %+8.5f%s" % (th[i], pA[i], pB[i], pC[i], mark), flush=True)

iA = int(np.argmax(pA))
print("\n=> phase-A open-circuit flux linkage is MAX at rotor = %.3f deg mech"
      " (= %.2f deg elec)" % (th[iA], th[iA]*pole_pairs))
print("   => the rotor d-axis (N pole in iron) points at phase A at THIS rotor angle.")
print("   psi_A(rotor=0) = %+.5f   psi_A(max) = %+.5f   ratio = %.3f"
      % (pA[0], pA[iA], pA[0]/pA[iA] if pA[iA] else 0.0))
