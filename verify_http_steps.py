"""Reproduce the EXACT Simulation-tab HTTP request at 60 vs 120 steps.

Hits the live backend (:8001) on /api/simulation/physics/fem_transient with the
same query the frontend builds (sliding_band, include_frames, etc.), fresh=false
(mimics the user pressing Re-run). Prints the backend's SNAPPED n_steps_per_period
plus the summary metrics for each, so we can see if 60 and 120 truly collapse to
the same result through the real route (cache + restore logic included).
"""
import json, urllib.request, urllib.parse

BASE = "http://127.0.0.1:8001/api/simulation/physics/fem_transient"

def common(steps, fresh):
    return {
        "restore": "false", "n_steps_per_period": str(steps), "n_periods": "1",
        "gamma_deg": "28", "I_phase_rms": "100",
        "mesh_size_mm": "4.0", "min_size_mm": "0.3", "outer_air_factor": "1.3",
        "motion_band": "true", "band_thickness_mm": "0.4", "gap_layers": "2",
        "n_sectors": "4", "stator_fillet_mm": "0", "sliding_band": "true",
        "rotor_eddy": "true", "demag": "false", "torque_filter": "true",
        "pole_copy": "false", "coil_temp_c": "120", "end_winding_factor": "0",
        "component_mesh": "{}", "include_frames": "true", "n_frames": str(steps),
        "run_id": f"diag_{steps}", "fresh": fresh,
    }

def call(steps, fresh):
    url = BASE + "?" + urllib.parse.urlencode(common(steps, fresh))
    with urllib.request.urlopen(url, timeout=600) as r:
        d = json.loads(r.read().decode())
    return {
        "req_steps": steps, "fresh": fresh,
        "backend_n_steps": d.get("n_steps_per_period"),
        "n_slip_nodes": d.get("n_slip_nodes"),
        "restored": d.get("restored"), "stale": d.get("stale"),
        "T_avg_Nm": round(d.get("T_avg_Nm", 0), 4),
        "ripple_filt_%": round(d.get("T_ripple_filt_pct", d.get("T_ripple_pct", 0)), 4),
        "ripple_raw_%": round(d.get("T_ripple_raw_pct", 0), 4),
        "P_loss_W": round(d.get("P_loss_total_avg_W", 0), 3),
        "P_mech_W": round(d.get("P_mech_avg_W", 0), 3),
    }

if __name__ == "__main__":
    # fresh=false mimics the user exactly (cache may serve)
    for steps in (60, 120):
        print(json.dumps(call(steps, "false")))
    # fresh=true forces a real recompute, bypassing the cache
    for steps in (60, 120):
        print(json.dumps(call(steps, "true")))
