"""Final: scan eval (_subprocess_eval) at the Simulation's FULL settings must
reproduce the Simulation. Target: T=80.25 ripple=13.25 eff=95.75 P_loss=1738.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from motor_ai_sim.routes.optimization import _subprocess_eval

def run(gap, ew, re):
    out = _subprocess_eval({}, 110.0, 120, 120.0, n_periods=1.0, gamma_deg=28.0,
                           mesh_size_mm=4.0, min_size_mm=0.3, n_sectors=4, _log=False,
                           pole_copy=True, torque_filter=True, gap_layers=gap,
                           end_winding_factor=ew, rotor_eddy=re)
    if not out or not out.get("ok"):
        return {"cfg": f"gap{gap},ew{ew},re{re}", "ok": False, "err": (out or {}).get("error")}
    r = out["res"]
    return {"cfg": f"gap{gap},ew{ew},re{re}", "T": round(r.get("T_em_Nm", 0), 2),
            "ripple": round(r.get("T_ripple_pct", 0), 2),
            "eff": round(100 * r.get("efficiency", 0), 2),
            "P_loss": round(r.get("P_loss_total_W", 0), 0)}

if __name__ == "__main__":
    # Sim settings (gap1/ew1.51/rotor_eddy True) vs old sweep (gap3/ew0/rotor_eddy False)
    with ThreadPoolExecutor(max_workers=2) as ex:
        res = list(ex.map(lambda a: run(*a), [(1.0, 1.51, True), (3.0, 0.0, False)]))
    print("TARGET Simulation: T=80.25 ripple=13.25 eff=95.75 P_loss=1738")
    for r in res:
        print(json.dumps(r), flush=True)
