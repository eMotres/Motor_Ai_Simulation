"""Gap-layer study table.  usage: python gaptable.py out/e_d40_gl1.json ..."""
import json
import sys

import numpy as np


def avg(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    a = np.asarray(v, float)
    return float(a.mean()) if a.size else None


def row(fn):
    r = json.load(open(fn))
    c = np.asarray(r["T_coulomb_series"], float)
    mx = np.asarray(r["T_em_maxwell_Nm"], float)
    sc = r["coulomb_torque"]["layer_self_check"]
    m = r["meshes"][-1] if r.get("meshes") else {}
    out = dict(
        run=fn.split("/")[-1][:-5], gl=r["args"]["gl"], ring=r.get("slip_nodes_per_period"),
        steps=r.get("n_steps_per_period"), n_elem=m.get("n_elem"), n_dof=m.get("n_dof_p2"),
        T_coul=float(c.mean()), T_mx=float(mx.mean()), T_rep=r.get("T_avg_Nm"),
        pp_coul=float(np.ptp(c)), pp_pct=100 * float(np.ptp(c)) / abs(float(c.mean())) if c.mean() else None,
        pp_mx=float(np.ptp(mx)), selfcheck_pct=100 * sc["rel_to_pp"] if sc.get("rel_to_pp") is not None else None,
        selfcheck_mean_pct=100 * sc["rel_to_mean"] if sc.get("rel_to_mean") is not None else None,
        P_fe=avg(r.get("P_fe_avg_W", r.get("P_fe_W"))), P_mag=avg(r.get("P_mag_solve_W", r.get("P_mag_eddy_W"))),
        P_shaft=avg(r.get("P_shaft_solve_W", r.get("P_shaft_eddy_W"))),
        P_sleeve=avg(r.get("P_sleeve_solve_W", r.get("P_sleeve_eddy_W"))),
        P_cu_dc=avg(r.get("P_cu_dc_W")), P_cu_ac=avg(r.get("P_cu_ac_solve_W")),
        P_cu=avg(r.get("P_cu_W")), P_total=avg(r.get("P_loss_total_avg_W", r.get("P_loss_total_W"))),
        frames_solved=r.get("n_frames_solved"), warmup=r.get("eddy_warmup_frames"),
        settled=r.get("eddy_settled"), wall_s=r.get("wall_s"), solve_wall=r.get("solve_wall_s"),
        coul_ms=1e3 * r["coulomb_eval"]["seconds"] / max(1, r["coulomb_eval"]["calls"]),
    )
    return out


rows = [row(f) for f in sys.argv[1:]]
keys = list(rows[0].keys())
for r in rows:
    print(json.dumps({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()}))
