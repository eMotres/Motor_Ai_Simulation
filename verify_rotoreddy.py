"""Confirm rotor_eddy (field vs slab magnet eddy) is the efficiency driver.
Same design/op (gap=1, ew=1.51, n_sectors=4, steps120, I=110, g=28) at:
  rotor_eddy=True  (field, like Simulation) -> expect ~Sim eff 95.75 / P_loss 1738
  rotor_eddy=False (slab, scan default)     -> expect ~94.4 / 2003
"""
import json
from concurrent.futures import ThreadPoolExecutor
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

def run(re):
    d = fem_transient_sliding_band(
        n_steps_per_period=120, n_periods=1.0, gamma_deg=28.0, I_phase_rms=110.0,
        mesh_size_mm=4.0, min_size_mm=0.3, outer_air_factor=1.2, n_sectors=4,
        gap_layers=1.0, coil_temp_c=120.0, end_winding_factor=1.51,
        rotor_eddy=re, torque_filter=True, pole_copy=True)
    su = d.get("summary", {})
    return {"rotor_eddy": re, "T": round(d.get("T_avg_Nm", 0), 2),
            "ripple": round(d.get("T_ripple_filt_pct", 0), 2),
            "eff": round(100 * su.get("efficiency", 0), 2),
            "P_loss": round(su.get("P_loss_total_W", 0), 0),
            "mag": round(su.get("P_solid_W", 0), 0)}

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as ex:
        res = list(ex.map(run, [True, False]))
    print("TARGET Simulation: T=80.25 ripple=13.25 eff=95.75 P_loss=1738")
    for r in res:
        print(json.dumps(r), flush=True)
