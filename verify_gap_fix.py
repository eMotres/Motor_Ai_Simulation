"""Verify the scan eval (_subprocess_eval) now honors gap_layers.
Same design (current config) + op (I=110,g=28,steps120,n_sectors=4) at:
  gap_layers=1 -> expect ~Simulation (T80.25 ripple13.25)
  gap_layers=3 -> expect ~old sweep  (T81.1  ripple9.0)
"""
import json
from concurrent.futures import ThreadPoolExecutor
from motor_ai_sim.routes.optimization import _subprocess_eval

def run(gap):
    out = _subprocess_eval({}, 110.0, 120, 120.0, n_periods=1.0, gamma_deg=28.0,
                           mesh_size_mm=4.0, min_size_mm=0.3, n_sectors=4, _log=False,
                           pole_copy=True, torque_filter=True, gap_layers=gap)
    if not out or not out.get("ok"):
        return {"gap": gap, "ok": False, "err": (out or {}).get("error")}
    r = out["res"]
    return {"gap": gap, "T": round(r.get("T_em_Nm", 0), 2),
            "ripple": round(r.get("T_ripple_pct", 0), 2),
            "eff": round(100 * r.get("efficiency", 0), 2),
            "P_loss": round(r.get("P_loss_total_W", 0), 0)}

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as ex:
        res = list(ex.map(run, [1.0, 3.0]))
    for r in res:
        print(json.dumps(r), flush=True)
