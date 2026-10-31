"""Warm start of a repeated run, measured (sandbox only, read-only).

In ONE process, like the API serving re-runs and coupled passes of one machine:

  1. the point, cold                         -> <out>_cold.json
  2. the same point again (seeded by run 1)  -> <out>_same.json
  3. the point with the magnet +dT K (seeded by run 2, as a coupled pass)
                                             -> <out>_hot.json

  python warm_seed_bench.py point.json out [dT=3] [ENV=VAL ...]

Each line printed: wall, solver time, frames solved, warm-up and pre-pass frames.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fem_speed_identity as F                              # noqa: E402


def _one(params, ws, tag, out):
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import simulation as S
    with W.use_workspace(ws):
        t0 = time.time()
        r = S.get_fem_transient(**params, fresh=True, ledger=False)
        wall = time.time() - t0
    if hasattr(r, "body"):
        r = json.loads(r.body)
    res = {"wall_s": wall, "scalars": F._scalars(r),
           "solve_wall_s": r.get("solve_wall_s"),
           "n_frames_solved": r.get("n_frames_solved"),
           "eddy_warmup_frames": r.get("eddy_warmup_frames"),
           "demag_prepass_frames": r.get("demag_prepass_frames"),
           "warm_seeded": r.get("warm_seeded"),
           "warm_start": r.get("warm_start")}
    json.dump(res, open("%s_%s.json" % (out, tag), "w"), indent=1, default=str)
    print("DONE %s wall %.1f s, solve %s s, frames %s (warm-up %s, pre-pass %s), "
          "seeded %s" % (tag, wall, res["solve_wall_s"], res["n_frames_solved"],
                         res["eddy_warmup_frames"], res["demag_prepass_frames"],
                         res["warm_seeded"]), flush=True)


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s: %(message)s")
    point, out = sys.argv[1], sys.argv[2]
    dT = 3.0
    for kv in sys.argv[3:]:
        k, v = kv.split("=", 1)
        if k == "dT":
            dT = float(v)
        else:
            os.environ[k] = v
    pt = json.load(open(point))
    params = (F._params_from_ledger(pt["ledger"]) if "ledger" in pt
              else dict(pt["params"]))
    from motor_ai_sim import workspace as W
    ws = W.workspace_for_identity(pt.get("identity", "vadim@motresres.com"))
    _one(params, ws, "cold", out)
    _one(params, ws, "same", out)
    _one(dict(params, magnet_temp_c=float(params["magnet_temp_c"]) + dT), ws,
         "hot", out)


if __name__ == "__main__":
    main()
