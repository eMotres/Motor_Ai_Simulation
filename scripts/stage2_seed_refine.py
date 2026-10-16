"""Stage 2 of the two-stage method — refine LOCALLY from a stage-1 seed.

The method (user, 2026-08-14): stage 1 searches free, with the ripple gate
effectively off, so the population is not crippled from the first generation by
a constraint the starting design itself breaks.  Stage 2 then goes backwards:
take the points of stage 1's cloud that sit closest to the objective under a
ripple you would actually build, and descend from each of them.

This script is the mechanical half of stage 2.  It:

  1. reads the live run state (/api/optimization/descent/progress),
  2. scores EVERY published point with the run's own baseline line — the same
     perpendicular distance F the optimizer maximises, recomputed here because
     runs started before the F stamp do not carry it,
  3. ranks under a ripple ceiling you choose,
  4. and — with --apply N — writes that seed's geometry into the design and
     launches a SCREENING descent from it (mode="screen": ±delta on every
     variable, then descend the influential ones — local, and it starts paying
     after 2N evals instead of O(N^2)).

Nothing here invents an operating point: the seed's geometry is applied and the
plan re-reads current / rpm / gamma / winding / k_end from the Simulation tab,
exactly as every other run does.

  python scripts/stage2_seed_refine.py --max-ripple 5            # just look
  python scripts/stage2_seed_refine.py --max-ripple 5 --apply 3  # seed #3, run
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

API = "http://localhost:8001"


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)


def _post(path: str, body: dict, method: str = "POST"):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def score(points, bline):
    """Signed perpendicular distance above the current-only baseline line."""
    td_a, eff_a = bline["td_a"], bline["eff_a"]
    w_td, w_eff, norm = bline["w_td"], bline["w_eff"], bline["norm"]
    out = []
    for i, p in enumerate(points, 1):
        td, eff, rip = p.get("td"), p.get("eff"), p.get("ripple")
        if td is None or eff is None or rip is None:
            continue
        # the same display fence the chart uses: hide broken numbers, not bad
        # designs (a persisted td = -7.7e9 once stretched the axis by 1e8)
        if not (0 < eff <= 1 and 0 < td < 5e3):
            continue
        out.append({
            "n": i, "F": (w_td * (td - td_a) + w_eff * (eff - eff_a)) / norm,
            "T": p.get("torque"), "eff": 100 * eff, "ripple": rip, "td": td,
            "mass": p.get("mass"), "overrides": p.get("overrides") or {},
            "current_a": p.get("current_a"),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-ripple", type=float, default=5.0,
                    help="ripple ceiling for CHOOSING seeds (%)")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--apply", type=int, default=0,
                    help="rank (1-based, in the printed list) to seed a run from")
    ap.add_argument("--gate", type=float, default=None,
                    help="ripple gate for the stage-2 run (default: --max-ripple)")
    ap.add_argument("--mode", default="screen", choices=("screen", "cmaes"))
    ap.add_argument("--name", default="")
    args = ap.parse_args()

    st = _get("/api/optimization/descent/progress")
    if st.get("running"):
        print("A RUN IS STILL IN FLIGHT (%s, %s evals).  Seeding now would make "
              "the two searches share the same workers — stop it first."
              % (st.get("phase"), st.get("n_evals")), file=sys.stderr)
        if args.apply:
            return 2
    bline = st.get("baseline_line")
    if not bline:
        print("no baseline line in the run state — nothing to score against",
              file=sys.stderr)
        return 1

    rows = score(st.get("points") or [], bline)
    under = sorted([r for r in rows if r["ripple"] <= args.max_ripple],
                   key=lambda r: -r["F"])[:args.top]
    print("%d points scored | %d under %.2f %% ripple\n"
          % (len(rows), len(under), args.max_ripple))
    print(" rank  pt#      F        T Nm    eta %%  ripple%%  Nm/kg   mass")
    for k, r in enumerate(under, 1):
        print("  %2d   %4d  %+.4f  %8.1f  %6.2f  %6.2f  %6.2f  %5.2f"
              % (k, r["n"], r["F"], r["T"] or 0, r["eff"], r["ripple"],
                 r["td"], r["mass"] or 0))
    if not args.apply:
        print("\n(--apply <rank> to write that geometry into the design and "
              "launch the stage-2 descent from it)")
        return 0

    if not 1 <= args.apply <= len(under):
        print("rank %d is outside the printed list" % args.apply, file=sys.stderr)
        return 1
    seed = under[args.apply - 1]
    gate = args.gate if args.gate is not None else args.max_ripple
    name = args.name or ("stage2_pt%d_r%02d" % (seed["n"], round(gate)))

    print("\nseeding from point #%d (F %+.4f, T %.1f Nm, ripple %.2f %%)"
          % (seed["n"], seed["F"], seed["T"] or 0, seed["ripple"]))
    print("  geometry: %s" % json.dumps(seed["overrides"]))
    _post("/api/geometry", seed["overrides"], method="PUT")
    # The seed was measured at its own solved current — carry it, exactly as
    # the chart's click-to-apply does, so the descent starts at the operating
    # point the seed's numbers belong to.
    if isinstance(seed.get("current_a"), (int, float)):
        _post("/api/simulation/config", {"max_current": float(seed["current_a"])},
              method="PATCH")
        print("  operating current: %.2f A" % seed["current_a"])

    out = _post("/api/optimization/auto",
                {"max_ripple_pct": gate, "mode": args.mode, "point_name": name})
    plan = out.get("plan") or {}
    print("\nstarted %s | mode %s | gate %.2f %% | budget %d evals | ~%.1f h"
          % (name, args.mode, gate, plan.get("budget_evals", 0),
             (plan.get("cost") or {}).get("est_wall_seconds", 0) / 3600.0))
    print("op: %s" % json.dumps(plan.get("operating_point")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
