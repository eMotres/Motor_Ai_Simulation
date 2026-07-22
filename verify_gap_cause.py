"""Confirm the sweep-vs-Simulation mismatch is gap_layers + end_winding.
Run the CURRENT config geometry through the solver at:
  (A) Simulation settings: gap_layers=1, end_winding=1.51  -> expect ~Sim (T80.25 eff95.75 ripple13.25)
  (B) Sweep settings:      gap_layers=3, end_winding=0(auto) -> expect ~Sweep (T81.10 eff94.49 ripple9.06)
Same op: I=110, gamma=28, steps=120, n_sectors=4, pole_copy, torque_filter.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

def run(tag, gap, ew):
    d = fem_transient_sliding_band(
        n_steps_per_period=120, n_periods=1.0, gamma_deg=28.0, I_phase_rms=110.0,
        mesh_size_mm=4.0, min_size_mm=0.3, outer_air_factor=1.2, n_sectors=4,
        gap_layers=gap, coil_temp_c=120.0, end_winding_factor=ew,
        torque_filter=True, pole_copy=True)
    return {"cfg": tag, "gap": gap, "ew": ew,
            "T": round(d.get("T_avg_Nm", 0), 2),
            "eff": round(100 * (d.get("summary", {}).get("efficiency", 0)), 2),
            "ripple": round(d.get("T_ripple_filt_pct", 0), 2),
            "P_loss": round(d.get("summary", {}).get("P_loss_total_W", 0), 0),
            "mag": round(d.get("summary", {}).get("P_solid_W", 0), 0)}

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as ex:
        res = list(ex.map(lambda a: run(*a), [("A_sim", 1.0, 1.51), ("B_sweep", 3.0, 0.0)]))
    for r in res:
        print(json.dumps(r), flush=True)
