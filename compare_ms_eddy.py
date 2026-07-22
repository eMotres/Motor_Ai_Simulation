"""Run magnetostatic vs eddy-current solve at the SAME operating point + mesh,
print ALL output parameters side by side.  Torque filtering is OFF."""
import time
import numpy as np
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band as F

KW = dict(n_steps_per_period=24, n_periods=2.0, I_phase_rms=120.0,
          n_sectors=4, coil_temp_c=120.0, mesh_size_mm=3.0, min_size_mm=0.3)

def run(eddy):
    t = time.time()
    d = F(eddy=eddy, **KW)
    d["_wall_s"] = time.time() - t
    return d

print("running magnetostatic…", flush=True); ms = run(False)
print("running eddy…",          flush=True); ed = run(True)

def mean(d, k):
    s = d.get(k)
    if isinstance(s, (list, tuple)) and len(s):
        return float(np.mean(np.asarray(s, float)))
    if isinstance(s, (int, float)):
        return float(s)
    return None

def params(d):
    rpm = float(d.get("rpm", 0.0)); omega = 2 * np.pi * rpm / 60.0
    Tavg = float(d.get("T_avg_Nm", 0.0)); Pmech = Tavg * omega
    Pcu = mean(d, "P_cu_W") or 0.0; Pfe = mean(d, "P_fe_W") or 0.0
    Pmag = mean(d, "P_mag_eddy_W") or 0.0; Psh = mean(d, "P_shaft_eddy_W") or 0.0
    ploss = Pcu + Pfe + Pmag + Psh
    eff = 100.0 * Pmech / (Pmech + ploss) if Pmech > 0 else 0.0
    return [
        ("T_avg [N·m]",            Tavg),
        ("T_ripple RAW [%]",       d.get("T_ripple_pct")),
        ("V_peak [V]",             d.get("V_peak")),
        ("--- COPPER ---",         None),
        ("P_cu DC (I²R) [W]",      d.get("P_cu_dc_W")),
        ("P_cu AC slab (mean) [W]",mean(d, "P_cu_ac_W")),
        ("P_cu total slab [W]",    Pcu),
        ("P_cu total SOLVE [W]",   d.get("P_cu_total_solve_W")),
        ("P_cu AC SOLVE [W]",      d.get("P_cu_ac_solve_W")),
        ("--- OTHER LOSSES ---",   None),
        ("P_fe iron (mean) [W]",   Pfe),
        ("P_mag eddy (mean) [W]",  Pmag),
        ("P_shaft eddy (mean) [W]",Psh),
        ("P_loss total [W]",       ploss),
        ("--- POWER ---",          None),
        ("P_mech [W]",             Pmech),
        ("efficiency [%]",         eff),
        ("--- ELECTRICAL ---",     None),
        ("R_phase [Ω]",            d.get("R_phase_ohm")),
        ("coil_temp [°C]",         d.get("coil_temp_C")),
        ("k_end",                  d.get("end_winding_factor")),
        ("rpm",                    rpm),
        ("f_elec [Hz]",            d.get("f_elec_Hz")),
        ("--- MESH/SOLVE ---",     None),
        ("n_steps",                d.get("n_steps")),
        ("n_slip_nodes",           d.get("n_slip_nodes")),
        ("wall [s]",               round(d.get("_wall_s", 0.0), 1)),
    ]

pm, pe = params(ms), params(ed)
print("\n================== MAGNETOSTATIC vs EDDY-CURRENT solve ==================")
print("(I=120 Arms, %d steps × %.0f periods, mesh %.1f mm, 1/4 sector — torque UNFILTERED)"
      % (KW["n_steps_per_period"], KW["n_periods"], KW["mesh_size_mm"]))
print("%-26s | %14s | %14s" % ("parameter", "magnetostatic", "eddy-current"))
print("-" * 60)
for (k, vm), (_, ve) in zip(pm, pe):
    if vm is None and ve is None:
        print(k); continue
    def f(v):
        return "—" if v is None else (f"{v:14.3f}" if abs(v) < 1e4 else f"{v:14.1f}")
    print("%-26s | %s | %s" % (k, f(vm), f(ve)))
