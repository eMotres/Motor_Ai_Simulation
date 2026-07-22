"""Does requesting more steps change the Simulation result?

Calls the SAME solver the Simulation tab uses (fem_transient_sliding_band) at
several step counts for the current config geometry, at the Simulation's
operating point (100 A / gamma 28 / 4000 rpm), n_sectors=4 (Mesh default).
Reports the SNAPPED step count + scalar metrics so we can see which requests
collapse to the same compute (cap = nodes_per_period = 120 for this 20-pole motor).
"""
import json
from concurrent.futures import ThreadPoolExecutor
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

GAMMA, CUR, NS = 28.0, 100.0, 4
REQ = [60, 120, 240]   # 60 -> 60 solves; 120 & 240 -> both capped to 120

def run(req_steps):
    d = fem_transient_sliding_band(
        n_steps_per_period=req_steps, n_periods=1.0,
        gamma_deg=GAMMA, I_phase_rms=CUR, n_sectors=NS,
        mesh_size_mm=4.0, min_size_mm=0.3, gap_layers=3.0,
        coil_temp_c=120.0, torque_filter=True, pole_copy=True,
    )
    # keep only scalar (non-list/dict) fields for a clean compare
    scal = {k: v for k, v in d.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {"req": req_steps, "snapped": d.get("n_steps_per_period"),
            "T_mean_Nm": round(scal.get("T_em_mean_Nm", scal.get("T_em_Nm", 0)), 4),
            "ripple_pct": round(scal.get("T_ripple_pct", 0), 4),
            "eff_pct": round(100 * scal.get("efficiency", 0), 4),
            "P_cu": round(scal.get("P_cu_W", 0), 3),
            "P_fe": round(scal.get("P_fe_W", 0), 3),
            "scalar_keys": sorted(scal.keys())}

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(run, REQ))
    print("RESULT_JSON " + json.dumps(res))
    for r in res:
        print({k: r[k] for k in ("req", "snapped", "T_mean_Nm", "ripple_pct", "eff_pct", "P_cu", "P_fe")})
    print("scalar_keys:", res[0]["scalar_keys"])
