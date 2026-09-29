"""How small can the air box be before the answer moves?

The user's reading of the 3D view — "too much air, 20 % of the motor should do"
— is worth taking seriously for two reasons, and they pull in opposite
directions.  FOR: the mesh is 69 % air by element count (53 460 of 77 166 tets),
so the box is most of the cost of every 3D solve, and shrinking it would make
the whole programme cheaper.  AGAINST: the box is where the field is truncated,
and truncating it inside the region where the field still lives changes the
answer rather than the runtime.

So this is measured, not argued, and it is measured by the SELF-CHECK the code
already has rather than by comparing against an even bigger box: at each size
the same machine is solved twice, once with the far boundary held at phi = 0
(Dirichlet — no flux may leave) and once with dphi/dn = 0 (Neumann — no flux may
cross).  The truth is BETWEEN them: one boundary condition pushes flux back in,
the other lets it slide out.  A box that is big enough shows the two conditions
agreeing; a box that is too small shows them apart, and the gap IS the
truncation error, with no reference solve needed.

Run:  python scripts/stage_c_box_size.py [--factors 1.2,1.5,2,4]

`box_factor` here is `run_stage_a`'s own: the box radius in multiples of the
STATOR OUTER DIAMETER, so 1.2 is a box 1.2 x 40 mm = 48 mm across against a
40 mm machine — i.e. 20 % clearance, exactly what was asked about.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--factors", default="1.2,1.5,2,4")
    ap.add_argument("--out", default=str(_ROOT / "config" / "stage_c_box_size.json"))
    args = ap.parse_args()
    factors = [float(x) for x in args.factors.split(",") if x.strip()]

    passport = json.loads((_ROOT / "config" / "end_effect_3d.json").read_text(encoding="utf-8"))
    geo = dict(passport["pinned_geometry"]["geometry"])

    from motor_ai_sim.simulation.static3d.end_effect import run_stage_a

    out = {"version": "static3d-boxsize-1",
           "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "_doc": __doc__.strip(), "rows": []}

    for bf in factors:
        t0 = time.perf_counter()
        print("\n=== box_factor = %.2f ===" % bf, flush=True)
        # do_bracket=True is the whole point: it solves the SAME mesh with both
        # outer boundary conditions and reports the spread.
        res = run_stage_a(geo_override=geo, l_factors=(1.0,), laminated_iron=True,
                          do_2d=True, do_bracket=True, box_factor=bf, verbose=True)
        row = (res.get("l_stack_curve") or [{}])[0]
        br = res.get("bracket") or {}
        rec = {
            "box_factor": bf,
            "box_r_mm": (res.get("model") or {}).get("box_r_mm"),
            "k_flux": row.get("k_flux"),
            "k_flux_self": row.get("k_flux_self"),
            "B1_mid_T": row.get("B1_mid_T"),
            "bracket_rel_spread": br.get("rel_spread_k_flux"),
            "dirichlet": br.get("dirichlet"),
            "neumann": br.get("neumann"),
            "elements": (res.get("model") or {}).get("elements"),
            "wall_s": round(time.perf_counter() - t0, 1),
        }
        out["rows"].append(rec)
        print("  -> k_flux_self=%.5f  bracket spread=%.3f %%  (%.0f s)"
              % (rec["k_flux_self"] or 0, 100 * (rec["bracket_rel_spread"] or 0),
                 rec["wall_s"]), flush=True)

    ok = [r for r in out["rows"] if (r["bracket_rel_spread"] or 1) < 0.002]
    if ok:
        best = min(ok, key=lambda r: r["box_factor"])
        out["verdict"] = {
            "smallest_box_within_0p2pct": best["box_factor"],
            "reading": ("the Dirichlet/Neumann bracket closes to under 0.2 %% at "
                        "box_factor %.2f; anything smaller is truncating the field, "
                        "not saving time." % best["box_factor"]),
        }
    else:
        out["verdict"] = {"smallest_box_within_0p2pct": None,
                          "reading": "no tested box closed the bracket to 0.2 %"}
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\nwrote %s" % args.out, flush=True)
    print(out["verdict"]["reading"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
