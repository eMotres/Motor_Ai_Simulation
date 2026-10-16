"""Reverse-engineer the running sweep's n_sectors & steps.

Re-run ONE sweep point's exact geometry+op through the SAME _subprocess_eval the
scan uses, at candidate (n_sectors, steps) combos. Whichever reproduces the
sweep's reported eff/ripple/torque/losses is what the scan is actually using.

Sweep point to match (I=100, gamma=28):
  eff 0.94124, ripple 13.29, T 72.7, P_loss 1901.3, P_fe 436.2, P_mag 389.4
"""
import json
from motor_ai_sim.routes.optimization import _subprocess_eval

OV = {"slot_height": 19.6, "tooth_width": 11.2, "tooth2_width": 7.3, "magnet_fill_radius": 3.0}
I, G = 100.0, 28.0
# scan-path params (scan reads mesh from localStorage; defaults + earlier scan_params)
COMMON = dict(coil_temp_c=120.0, n_periods=1.0, gamma_deg=G,
              mesh_size_mm=4.0, min_size_mm=0.3, pole_copy=True, torque_filter=True, _log=False)

def run(ns, steps):
    out = _subprocess_eval(OV, I, steps, n_sectors=ns, **COMMON)
    if not out or not out.get("ok"):
        return {"cfg": f"n={ns},st={steps}", "ok": False, "err": (out or {}).get("error")}
    r = out["res"]
    return {"cfg": f"n={ns},st={steps}", "eff": round(r.get("efficiency", 0), 5),
            "ripple": round(r.get("T_ripple_pct", 0), 2), "T": round(r.get("T_em_Nm", 0), 2),
            "P_loss": round(r.get("P_loss_total_W", 0), 1), "P_fe": round(r.get("P_fe_W", 0), 1),
            "P_mag": round(r.get("P_mag_W", 0), 1)}

if __name__ == "__main__":
    print("TARGET (sweep): eff=0.94124 ripple=13.29 T=72.7 P_loss=1901.3 P_fe=436.2 P_mag=389.4", flush=True)
    for ns, st in [(4, 120), (4, 60), (1, 120)]:
        print(json.dumps(run(ns, st)), flush=True)
