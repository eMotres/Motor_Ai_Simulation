"""Parity test: ns=1 vs ns=4 with the SAME gap-band mode.

structured_gap=True forces the merged/structured band for BOTH the full ring
and the sector wedge — if the ns=1 vs ns=4 mismatch disappears here, the root
cause is the band-mode asymmetry (_full_ring forces 'moving', sector 'merged').
Usage: python _cmp_band.py [steps]
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from motor_ai_sim.routes.optimization import _subprocess_eval

CUR, GAMMA = 100.0, 28.0
STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
COMMON = dict(coil_temp_c=120.0, n_periods=1.0, gamma_deg=GAMMA,
              mesh_size_mm=4.0, min_size_mm=0.3, pole_copy=True,
              torque_filter=True, iron_template=False, geo_mesh=False,
              structured_gap=True)


def run(ns):
    out = _subprocess_eval({}, CUR, STEPS, n_sectors=ns, _log=False, **COMMON)
    if not out or not out.get("ok"):
        return {"n_sectors": ns, "ok": False, "err": (out or {}).get("error", "?")}
    r = out.get("res", {})
    return {"n_sectors": ns, "ok": True,
            "T_Nm": round(r.get("T_em_Nm", 0), 3),
            "eff_%": round(100 * r.get("efficiency", 0), 2),
            "ripple_%": round(r.get("T_ripple_pct", 0), 2),
            "Vpk": round(r.get("V_peak", 0), 1)}


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as ex:
        res = list(ex.map(run, [1, 4]))
    print("RESULT_JSON " + json.dumps(res))
    for r in res:
        print(r)
