"""Your EXACT Simulation config at 60 vs 120 steps -> full metric set.

Uses the persisted last-transient params (n_sectors=1 / gap_layers=1 / pole_copy
on / end_winding 1.51 / outer_air 1.2 / gamma 28 / I 100), fresh=true (force
recompute). Prints torque, filtered+raw ripple, losses AND the derived efficiency
so we can see exactly which displayed numbers move between 60 and 120.
"""
import json, urllib.request, urllib.parse

BASE = "http://127.0.0.1:8001/api/simulation/physics/fem_transient"

def params(steps):
    return {
        "restore": "false", "n_steps_per_period": str(steps), "n_periods": "1",
        "gamma_deg": "28", "I_phase_rms": "100",
        "mesh_size_mm": "4.0", "min_size_mm": "0.3", "outer_air_factor": "1.2",
        "motion_band": "true", "band_thickness_mm": "0.4", "gap_layers": "1",
        "n_sectors": "1", "stator_fillet_mm": "0", "sliding_band": "true",
        "rotor_eddy": "true", "demag": "false", "torque_filter": "true",
        "pole_copy": "true", "coil_temp_c": "120", "end_winding_factor": "1.51",
        "component_mesh": "{}", "include_frames": "true", "n_frames": str(steps),
        "run_id": f"udiag_{steps}", "fresh": "true",
    }

def call(steps):
    url = BASE + "?" + urllib.parse.urlencode(params(steps))
    with urllib.request.urlopen(url, timeout=600) as r:
        d = json.loads(r.read().decode())
    pin = d.get("P_elec_in_W", 0) or 0
    pmech = d.get("P_mech_avg_W", 0) or 0
    eff = (pmech / pin * 100) if pin else 0
    return {
        "req": steps, "backend_n_steps": d.get("n_steps_per_period"),
        "n_slip_nodes": d.get("n_slip_nodes"),
        "T_avg_Nm": round(d.get("T_avg_Nm", 0), 4),
        "ripple_filt_%": round(d.get("T_ripple_filt_pct", 0), 4),
        "ripple_raw_%": round(d.get("T_ripple_raw_pct", 0), 4),
        "eff_%": round(eff, 4),
        "P_elec_in_W": round(pin, 2), "P_mech_W": round(pmech, 2),
        "P_loss_W": round(d.get("P_loss_total_avg_W", 0), 2),
        "P_cu_W": round(d.get("P_cu_total_solve_W", d.get("P_cu_dc_W", 0)), 2),
    }

if __name__ == "__main__":
    for s in (60, 120):
        print(json.dumps(call(s)))
