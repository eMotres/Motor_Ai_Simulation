"""High-fidelity authoritative eval of the CURRENT config at one operating point.
Prints torque, ripple, losses, efficiency, voltage. Use to lock a final design.

Usage:  PYTHONPATH=src python design_runs/final_eval.py I rpm gamma [n_steps] [mesh] [n_sectors]
"""
import sys, math
from statistics import mean
from motor_ai_sim.routes.simulation import get_fem_transient

I      = float(sys.argv[1]) if len(sys.argv) > 1 else 35.0
RPM    = float(sys.argv[2]) if len(sys.argv) > 2 else 12000.0
GAMMA  = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
NSTEPS = int(sys.argv[4]) if len(sys.argv) > 4 else 16
MESH   = float(sys.argv[5]) if len(sys.argv) > 5 else 0.6
NSEC   = int(sys.argv[6]) if len(sys.argv) > 6 else 2

res = get_fem_transient(n_steps_per_period=NSTEPS, n_periods=1.0, gamma_deg=GAMMA,
                        I_phase_rms=I, mesh_size_mm=MESH, n_sectors=NSEC,
                        sliding_band=True, rotor_eddy=True)
sa = res.get("summary") or {}

def m(x):
    return mean(x) if isinstance(x, list) and x else (x or 0.0)

T   = float(res.get("T_avg_Nm") or 0.0)
omega = RPM * 2 * math.pi / 60.0
Pm  = T * omega
Pcu = m(res.get("P_cu_W")); Pfe = m(res.get("P_fe_W"))
Pme = m(res.get("P_mag_eddy_W")); Pse = m(res.get("P_shaft_eddy_W"))
Ploss = Pcu + Pfe + Pme + Pse
eff = sa.get("efficiency", res.get("efficiency"))
rip = sa.get("ripple_pct", res.get("ripple_pct"))
Vpk = res.get("V_phase_peak_V") or res.get("V_peak")
mass = sa.get("mass_kg") or res.get("mass_kg")
jdens = sa.get("current_density_A_mm2") or res.get("current_density_A_mm2")

print(f"=== FINAL EVAL  I={I:.0f}A  rpm={RPM:.0f}  gamma={GAMMA:.0f}  (steps={NSTEPS}, mesh={MESH}, sec={NSEC}) ===")
print(f"  T_avg      = {T:.4f} N*m")
print(f"  P_mech     = {Pm:.1f} W")
print(f"  ripple     = {rip}")
print(f"  efficiency = {eff}")
print(f"  P_cu       = {Pcu:.2f} W")
print(f"  P_fe       = {Pfe:.2f} W")
print(f"  P_mag_eddy = {Pme:.3f} W   P_shaft_eddy = {Pse:.3f} W")
print(f"  P_loss_tot = {Ploss:.2f} W")
print(f"  V_ph_peak  = {Vpk} V")
print(f"  mass       = {mass} kg   J = {jdens} A/mm2")
