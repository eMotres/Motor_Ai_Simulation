"""A sequence of runs of one ledger point in ONE process (sandbox only, read-only).

Each leg: a tag, an optional magnet temperature, and environment overrides set
for that leg only.  The process keeps its warm state between legs exactly as
the API serving a coupled loop or repeated runs would.

  python seq_bench.py point.json out_prefix legs.json

``legs.json``: [{"tag": "cold130", "magnet_temp_c": 130, "env": {"SB_NO_WARM_CACHE": "1"}}, ...]
Each leg writes <out_prefix>_<tag>.json (fem_speed_identity's scalars plus the
demag / warm-start blocks) and prints one DONE line.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fem_speed_identity as F                              # noqa: E402


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s: %(message)s")
    point, out, legs_f = sys.argv[1], sys.argv[2], sys.argv[3]
    pt = json.load(open(point))
    params = (F._params_from_ledger(pt["ledger"]) if "ledger" in pt
              else dict(pt["params"]))
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import simulation as S
    ws = W.workspace_for_identity(pt.get("identity", "vadim@motresres.com"))
    for leg in json.load(open(legs_f)):
        p = dict(params)
        if leg.get("magnet_temp_c") is not None:
            p["magnet_temp_c"] = float(leg["magnet_temp_c"])
        old = {}
        for k, v in (leg.get("env") or {}).items():
            old[k] = os.environ.get(k)
            os.environ[k] = str(v)
        try:
            with W.use_workspace(ws):
                t0 = time.time()
                r = S.get_fem_transient(**p, fresh=True, ledger=False)
                wall = time.time() - t0
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        if hasattr(r, "body"):
            r = json.loads(r.body)
        res = {"leg": leg, "wall_s": wall, "scalars": F._scalars(r),
               "solve_wall_s": r.get("solve_wall_s"),
               "n_frames_solved": r.get("n_frames_solved"),
               "linear_solver": r.get("linear_solver"),
               "picard_iters_mean": r.get("picard_iters_mean"),
               "demag_seeded": r.get("demag_seeded"),
               "demag_seed_from": r.get("demag_seed_from"),
               "demag_settle": r.get("demag_settle"),
               "demag_prepass_frames": r.get("demag_prepass_frames"),
               "eddy_warmup_frames": r.get("eddy_warmup_frames"),
               "warm_start": r.get("warm_start"),
               "demag_summary": r.get("demag_summary")}
        json.dump(res, open("%s_%s.json" % (out, leg["tag"]), "w"), indent=1,
                  default=str)
        ds = r.get("demag_summary") or {}
        print("DONE %s wall %.1f s, solve %s s, frames %s (warm-up %s, pre-pass %s), "
              "T %.6g, Br kept %s %%, demag_seeded %s, linear %s" % (
                  leg["tag"], wall, res["solve_wall_s"], res["n_frames_solved"],
                  res["eddy_warmup_frames"], res["demag_prepass_frames"],
                  float(r.get("T_avg_Nm") or 0.0), ds.get("br_kept_vol_pct"),
                  res["demag_seeded"], res["linear_solver"]), flush=True)


if __name__ == "__main__":
    main()
