"""Verify n_sectors unification: same geometry/op, two build modes.

Runs the CURRENT config geometry (the min-ripple design) at the Simulation's
operating point (100 A, gamma=28, 4000 rpm) through the SAME _subprocess_eval the
optimizer/scan API uses, for n_sectors=1 (Simulation's stitch build) and
n_sectors=-1 (old optimizer diagnostic-ring build). If ripple differs by ~4 pp,
that confirms the build mode was the source of the Simulation-vs-optimizer
ripple mismatch — and that forcing n_sectors=mesh.nSectors makes the
optimizer/sweep/DOE report the SAME ripple the Simulation shows.

Metrics live under out["res"] (then _point_from_eval spreads {**r}).
The two builds are independent -> run them concurrently to halve wall time.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from motor_ai_sim.routes.optimization import _subprocess_eval

CUR, GAMMA, STEPS = 100.0, 28.0, 120
COMMON = dict(coil_temp_c=120.0, n_periods=1.0, gamma_deg=GAMMA,
              mesh_size_mm=4.0, min_size_mm=0.3, pole_copy=True, torque_filter=True)

def run(ns):
    out = _subprocess_eval({}, CUR, STEPS, n_sectors=ns, _log=False, **COMMON)
    if not out or not out.get("ok"):
        return {"n_sectors": ns, "ok": False, "err": (out or {}).get("error", "?")}
    r = out.get("res", {})
    return {"n_sectors": ns, "ok": True,
            "T_Nm": round(r.get("T_em_Nm", 0), 2),
            "eff_%": round(100 * r.get("efficiency", 0), 2),
            "ripple_%": round(r.get("T_ripple_pct", 0), 2),
            "Vpk": round(r.get("V_peak", 0), 1)}

if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as ex:
        res = list(ex.map(run, [1, -1]))
    print("RESULT_JSON " + json.dumps(res))
    for r in res:
        print(r)
