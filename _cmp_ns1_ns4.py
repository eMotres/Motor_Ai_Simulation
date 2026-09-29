"""Compare n_sectors=1 (full ring) vs n_sectors=4 (1/4 wedge, anti-periodic).

Same geometry (current config), same operating point, same eval path as the
optimizer (_subprocess_eval). Goal: T_avg and ripple must MATCH.
Runs BOTH mesh pipelines:
  geo   — iron_template=True, geo_mesh=True   (current default / WIP CDT path)
  gmsh  — iron_template=False, geo_mesh=False (validated gmsh+belt path)
Usage:  python _cmp_ns1_ns4.py [steps] [geo|gmsh|both]
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from motor_ai_sim.routes.optimization import _subprocess_eval

CUR, GAMMA = 100.0, 28.0
STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
MODE = sys.argv[2] if len(sys.argv) > 2 else "both"
COMMON = dict(coil_temp_c=120.0, n_periods=1.0, gamma_deg=GAMMA,
              mesh_size_mm=4.0, min_size_mm=0.3, pole_copy=True, torque_filter=True)


def run(args):
    ns, tpl = args
    out = _subprocess_eval({}, CUR, STEPS, n_sectors=ns,
                           iron_template=tpl, geo_mesh=tpl, _log=False, **COMMON)
    if not out or not out.get("ok"):
        return {"pipeline": "geo" if tpl else "gmsh", "n_sectors": ns,
                "ok": False, "err": (out or {}).get("error", "?")}
    r = out.get("res", {})
    return {"pipeline": "geo" if tpl else "gmsh", "n_sectors": ns, "ok": True,
            "T_Nm": round(r.get("T_em_Nm", 0), 3),
            "eff_%": round(100 * r.get("efficiency", 0), 2),
            "ripple_%": round(r.get("T_ripple_pct", 0), 2),
            "ripple_raw_%": round(r.get("T_ripple_raw_pct", 0), 2),
            "Vpk": round(r.get("V_peak", 0), 1)}


if __name__ == "__main__":
    jobs = []
    if MODE in ("geo", "both"):
        jobs += [(1, True), (4, True)]
    if MODE in ("gmsh", "both"):
        jobs += [(1, False), (4, False)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(run, jobs))
    print("RESULT_JSON " + json.dumps(res))
    for r in res:
        print(r)
