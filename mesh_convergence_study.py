"""Mesh-convergence study: same operating point, different per-component mesh
sizes → compare torque + ALL losses.  Uses the exact solver the web UI calls
(fem_transient_sliding_band)."""
import time, json
import numpy as np
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band as F

STEPS = 36          # frames per electrical period
NPER  = 1.0
I_RMS = 120.0
TEMP  = 120.0

# (label, component_mesh_mm, mesh_size_mm)
CONFIGS = [
    ("baseline (global 3mm)",        None,                                         3.0),
    ("iron+mag = 1.0mm",             {"stator":1.0,"rotor":1.0,"magnet":1.0},      3.0),
    ("all parts = 0.6mm",            {"stator":0.6,"rotor":0.6,"magnet":0.6,
                                      "coil":0.6,"shaft":1.0},                     3.0),
]

def mean(x):
    a = np.asarray(x or [0.0], float)
    return float(a.mean()) if a.size else 0.0

rows = []
for label, cm, ms in CONFIGS:
    t = time.time()
    d = F(n_steps_per_period=STEPS, n_periods=NPER, I_phase_rms=I_RMS,
          n_sectors=4, coil_temp_c=TEMP, mesh_size_mm=ms, component_mesh_mm=cm)
    rpm   = float(d.get("rpm", 0.0))
    Tavg  = float(d["T_avg_Nm"]); Trip = float(d["T_ripple_pct"])
    Pcu   = mean(d.get("P_cu_W")); Pfe = mean(d.get("P_fe_W"))
    Pmag  = mean(d.get("P_mag_eddy_W")); Psh = mean(d.get("P_shaft_eddy_W"))
    Pcudc = float(d.get("P_cu_dc_W", 0.0)); Pcuac = mean(d.get("P_cu_ac_W"))
    Ptot  = Pcu + Pfe + Pmag + Psh
    Pmech = Tavg * 2*np.pi*rpm/60.0
    eff   = Pmech/(Pmech+Ptot) if Pmech > 0 else 0.0
    dt = time.time()-t
    rows.append(dict(label=label, rpm=rpm, T=Tavg, ripple=Trip,
                     Pcu=Pcu, Pcu_dc=Pcudc, Pcu_ac=Pcuac, Pfe=Pfe, Pmag=Pmag,
                     Psh=Psh, Ptot=Ptot, Pmech=Pmech, eff=eff*100, wall=dt))
    print(f"[done] {label:24s} {dt:5.0f}s  T={Tavg:6.2f}  Ptot={Ptot:7.0f}W  eff={eff*100:5.2f}%", flush=True)

print("\n================ MESH-CONVERGENCE: torque + losses ================")
hdr = (f"{'config':24s} | {'T(Nm)':>7} {'rip%':>5} | {'Cu':>6} {'Fe':>6} "
       f"{'Mag':>5} {'Shaft':>5} | {'Ptot(W)':>7} {'eff%':>6}")
print(hdr); print("-"*len(hdr))
for r in rows:
    print(f"{r['label']:24s} | {r['T']:7.2f} {r['ripple']:5.1f} | "
          f"{r['Pcu']:6.0f} {r['Pfe']:6.0f} {r['Pmag']:5.0f} {r['Psh']:5.0f} | "
          f"{r['Ptot']:7.0f} {r['eff']:6.2f}")
print("\n(Cu = DC I^2R + slab AC proximity; rpm=%.0f, I=%.0fArms, steps=%d)" % (
    rows[0]['rpm'], I_RMS, STEPS))
json.dump(rows, open("mesh_convergence_results.json","w"), indent=2)
print("saved -> mesh_convergence_results.json")
