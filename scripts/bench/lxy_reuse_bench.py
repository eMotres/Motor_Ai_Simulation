"""Item 1 of the standard-transient speed plan, measured like a coupled loop.

Sandbox only, read-only (``get_fem_transient(fresh=True, ledger=False)`` in the
sandbox's own workspace copy, nothing saved into the catalog).  In ONE process,
like the API serving a coupled loop:

  1. the point, cold                      -> <out>_1.json
  2. the point with the magnet +dT K, the six-phase L_xy probe offered from
     run 1 (``routes.simulation._LXY_REUSE_CTX``, what the coupled loop sets)
                                          -> <out>_2reuse.json
  3. the same as 2 with the probe solved  -> <out>_3probe.json   (the reuse's
     own error: L_xy of 3 against 2's reused one)

  python lxy_reuse_bench.py point.json out [dT=3] [ENV=VAL ...]

``point.json`` as for fem_speed_identity.py.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fem_speed_identity as F                              # noqa: E402


def _one(params, ws, tag, out, lxy=None):
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import simulation as S
    tok = S._LXY_REUSE_CTX.set(lxy) if lxy is not None else None
    try:
        with W.use_workspace(ws):
            t0 = time.time()
            r = S.get_fem_transient(**params, fresh=True, ledger=False)
            wall = time.time() - t0
    finally:
        if tok is not None:
            S._LXY_REUSE_CTX.reset(tok)
    if hasattr(r, "body"):
        r = json.loads(r.body)
    six = (r.get("summary") or {}).get("six_phase") or r.get("six_phase") or {}
    res = {"wall_s": wall, "scalars": F._scalars(r),
           "solve_wall_s": r.get("solve_wall_s"),
           "n_frames_solved": r.get("n_frames_solved"),
           "six_phase": {k: six.get(k) for k in ("inductances", "probe_op",
                                                  "inductances_reused")}}
    json.dump(res, open("%s_%s.json" % (out, tag), "w"), indent=1, default=str)
    print("DONE %s wall %.1f s, solve %s s, frames %s, Lxy %s, reused %s" % (
        tag, wall, res["solve_wall_s"], res["n_frames_solved"],
        (six.get("inductances") or {}).get("Lxy_mH"),
        bool(six.get("inductances_reused"))), flush=True)
    return r, six


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
    _, six1 = _one(params, ws, "1", out)
    hot = dict(params, magnet_temp_c=float(params["magnet_temp_c"]) + dT)
    lxy = ({"mode": "reuse", "six_phase": six1, "source": "bench run 1"}
           if six1.get("inductances") else None)
    _one(hot, ws, "2reuse", out, lxy=lxy)
    _one(hot, ws, "3probe", out)


if __name__ == "__main__":
    main()
