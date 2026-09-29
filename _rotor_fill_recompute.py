"""'пересчитаем все' — recompute the FEM transient with the NEW rounded rotor
(rotor_fill_r=1.6) vs the OLD sharp rotor (rotor_fill_r=0), 72 steps, sliding
band, identical settings.  Reports T_avg, ripple, and losses so we can see if
rounding the rotor pole-tip corners reduced the torque ripple."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band


def run(rfr):
    t0 = time.time()
    r = fem_transient_sliding_band(
        n_steps_per_period=72, n_periods=1.0, gamma_deg=0.0,
        I_phase_rms=85.0, mesh_size_mm=2.0, min_size_mm=0.1,
        n_sectors=4, nonlinear_iterations=14, coil_temp_c=120.0,
        geo_override={"rotor_fill_r": float(rfr)},
    )
    dt = time.time() - t0
    T = r.get("T_em_Nm") or r.get("torque_Nm") or r.get("T_Nm")
    import numpy as np
    T = np.asarray(T, float)
    Tavg = float(T.mean()); Tmin = float(T.min()); Tmax = float(T.max())
    ripple = 100.0 * (Tmax - Tmin) / Tavg if Tavg else float("nan")
    def g(*keys):
        for k in keys:
            v = r.get(k)
            if v is not None:
                try: return float(np.mean(v))
                except Exception: return float(v)
        return None
    P_cu = g("P_copper_W", "P_cu_W", "copper_loss_W", "P_cu")
    P_fe = g("P_iron_W", "P_fe_W", "iron_loss_W", "P_fe")
    P_mg = g("P_magnet_W", "P_mag_W", "magnet_loss_W", "P_mag")
    print(f"\n=== rotor_fill_r = {rfr}  ({dt:.1f}s) ===")
    print(f"  T_avg  = {Tavg:7.3f} N.m")
    print(f"  T_min  = {Tmin:7.3f}   T_max = {Tmax:7.3f}")
    print(f"  ripple = {ripple:6.2f} %  (peak-peak / avg)")
    print(f"  losses: Cu={P_cu}  Fe={P_fe}  Mg={P_mg}")
    return dict(rfr=rfr, Tavg=Tavg, Tmin=Tmin, Tmax=Tmax, ripple=ripple,
                P_cu=P_cu, P_fe=P_fe, P_mg=P_mg, dt=dt, T=T.tolist())


sharp = run(0.0)
rounded = run(1.6)

print("\n" + "=" * 56)
print(" ROTOR-FILLET EFFECT  (sharp -> rounded 1.6 mm, 72 steps)")
print("=" * 56)
print(f"  T_avg : {sharp['Tavg']:7.3f} -> {rounded['Tavg']:7.3f} N.m"
      f"  ({100*(rounded['Tavg']-sharp['Tavg'])/sharp['Tavg']:+.2f} %)")
print(f"  ripple: {sharp['ripple']:6.2f} -> {rounded['ripple']:6.2f} %"
      f"  ({rounded['ripple']-sharp['ripple']:+.2f} pp)")
if sharp['P_fe'] and rounded['P_fe']:
    print(f"  P_fe  : {sharp['P_fe']:7.1f} -> {rounded['P_fe']:7.1f} W"
          f"  ({100*(rounded['P_fe']-sharp['P_fe'])/sharp['P_fe']:+.2f} %)")

import json
with open(os.path.join(os.path.dirname(__file__), "rotor_fill_recompute.json"), "w") as f:
    json.dump({"sharp": sharp, "rounded": rounded}, f, indent=2)
print("\nsaved rotor_fill_recompute.json")
