"""Stage C, part 3 — hold the axial ELEMENT SIZE fixed, not the layer count.

The four-length sweep proved the end effect is real: the droop k_flux_self falls
to 0.9987 at a 96 mm stack, i.e. the field stops sagging once the ends are far
away.  What it did NOT prove is the 2D comparison: k_flux extrapolated to 0.986
and its per-row 2D ratio B1_mid/B1_2D wandered 0.987 / 1.003 / 1.001 / 0.998 —
±0.8 % with no trend.  A systematic error has a trend; that is scatter.

Its source is in how the sweep was set up rather than in the physics: `n_stack`
was held at 8 layers while the stack grew 8x, so the axial element stretched
from 1.5 mm to 12 mm.  Every row therefore sat on a DIFFERENT axial resolution,
and the earlier probe measured that sensitivity directly — doubling the layers
at 48 mm moved k_flux_self by 0.32 %.

So this run holds the axial element at the 12 mm row's own 1.5 mm — n_stack
scales WITH the stack — and asks one question: does the ±0.8 % wobble in
B1_mid/B1_2D collapse?  If it does, the 1.4 % was discretisation and k_flux is
free to converge; if it survives at constant element size, it is physics and
belongs in the passport as a real disagreement.

Run:  python scripts/stage_c_axial_resolution.py [--lengths 12,24,48]
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
    ap.add_argument("--lengths", default="12,24,48", help="stack lengths in mm")
    ap.add_argument("--n-stack-12mm", type=int, default=8,
                    help="axial layers for the 12 mm row; longer rows scale with L")
    ap.add_argument("--max-iter", type=int, default=90)
    ap.add_argument("--out", default=str(_ROOT / "config" / "stage_c_axial_resolution.json"))
    args = ap.parse_args()
    lengths = [float(x) for x in args.lengths.split(",") if x.strip()]

    passport = json.loads((_ROOT / "config" / "end_effect_3d.json").read_text(encoding="utf-8"))
    geo = dict(passport["pinned_geometry"]["geometry"])
    base = float(geo.get("motor_length") or 12.0)

    from motor_ai_sim.simulation.static3d.end_effect import run_stage_a

    out = {
        "version": "static3d-stageC-axial-1",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "_doc": __doc__.strip(),
        "axial_element_mm": base / args.n_stack_12mm,
        "rows": [],
    }
    print("axial element held at %.3f mm" % out["axial_element_mm"], flush=True)

    for L in lengths:
        n_stack = max(2, int(round(args.n_stack_12mm * L / base)))
        t0 = time.perf_counter()
        print("\n=== L = %.1f mm, n_stack = %d ===" % (L, n_stack), flush=True)
        res = run_stage_a(geo_override=geo, l_factors=(L / base,), laminated_iron=True,
                          do_2d=True, do_bracket=False, n_stack=n_stack,
                          max_iter=args.max_iter, verbose=True)
        row = (res.get("l_stack_curve") or [{}])[0]
        b2d = ((res.get("two_d") or {}).get("B1_T")) or 0.0
        rec = {"stack_mm": L, "n_stack": n_stack,
               "k_flux": row.get("k_flux"), "k_flux_self": row.get("k_flux_self"),
               "B1_mid_T": row.get("B1_mid_T"), "B1_2D_T": b2d,
               "mid_over_2d": (row.get("B1_mid_T") / b2d) if b2d else None,
               "picard_converged": row.get("picard_converged"),
               "wall_s": round(time.perf_counter() - t0, 1)}
        out["rows"].append(rec)
        # Written after EVERY row, not at the end: this run is hours long and the
        # last attempt at it was killed with nothing on disk.  A partial curve is
        # worth something; an empty file after three hours is worth nothing.
        Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print("  -> k_flux=%.5f  k_self=%.5f  mid/2D=%.5f  conv=%s  (%.0f s)"
              % (rec["k_flux"] or 0, rec["k_flux_self"] or 0, rec["mid_over_2d"] or 0,
                 rec["picard_converged"], rec["wall_s"]), flush=True)

    ratios = [r["mid_over_2d"] for r in out["rows"] if r["mid_over_2d"]]
    if len(ratios) >= 2:
        spread = (max(ratios) - min(ratios)) / min(ratios)
        out["verdict"] = {
            "mid_over_2d_spread_pct": 100 * spread,
            "was_at_fixed_layer_count_pct": 1.6,   # 0.987 .. 1.003 from the v2 sweep
            "collapsed": bool(spread < 0.005),
            "reading": ("B1_mid/B1_2D spans %.2f %% across the stack lengths at a "
                        "CONSTANT axial element, against 1.6 %% when the layer count "
                        "was constant.  %s"
                        % (100 * spread,
                           "The wobble was discretisation." if spread < 0.005
                           else "The wobble survives — it is not the axial mesh.")),
        }
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\nwrote %s" % args.out, flush=True)
    if "verdict" in out:
        print(out["verdict"]["reading"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
