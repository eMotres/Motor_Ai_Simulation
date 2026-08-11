"""Stage C, part 1 — does the 3D model converge to 2D as the stack grows?

The end effect is a DEFICIT that lives at the two stack ends: it does not scale
with the stack, so its share of the flux must fall like 1/L and k_flux must walk
to 1 as L grows.  A model that failed that would not be measuring an end effect
at all — it would be measuring its own mesh or its own boundary box.

The passport already carries an `l_stack_curve`, but it is STALE by its own note:
isotropic iron and the pre-convergence 2D leg.  The inductive form of the same
test (stage_b.long_stack_honesty_test) passed on the CURRENT model and fitted a
textbook 1/L to 2e-5 uH/mm, which is why this run is worth doing on flux: if the
flux deficit obeys the same law with the same corrected model, the two agree
about where the end effect lives, and Stage C's first claim is measured rather
than argued.

Run:  python scripts/stage_c_long_stack.py [--factors 1,2,4] [--out PATH]

Writes a JSON with one row per stack length plus the a + b/L fit and a verdict.
It does NOT touch config/end_effect_3d.json — folding the result into the
passport is a separate, deliberate step.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))


def _fit_a_plus_b_over_L(L, y):
    """Least squares y = a + b/L on three-plus rows; returns (a, b, residuals)."""
    import numpy as np
    L = np.asarray(L, float)
    y = np.asarray(y, float)
    A = np.column_stack([np.ones_like(L), 1.0 / L])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    a, b = float(coef[0]), float(coef[1])
    resid = (y - (a + b / L)).tolist()
    return a, b, resid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--factors", default="1,2,4",
                    help="stack multipliers of the pinned 12 mm stack")
    ap.add_argument("--max-iter", type=int, default=45,
                    help="Picard cap; the 12 mm row did not converge at 45")
    ap.add_argument("--out", default=str(_ROOT / "config" / "stage_c_long_stack.json"))
    args = ap.parse_args()
    factors = tuple(float(x) for x in args.factors.split(",") if x.strip())

    passport = json.loads((_ROOT / "config" / "end_effect_3d.json").read_text(encoding="utf-8"))
    geo = dict(passport["pinned_geometry"]["geometry"])

    from motor_ai_sim.simulation.static3d.end_effect import run_stage_a

    print("Stage C / long stack — factors %s of the pinned %s mm stack"
          % (list(factors), geo.get("motor_length")), flush=True)
    t0 = time.perf_counter()
    # The SAME model Stage A now quotes: laminated iron, converged 2D leg, P2.
    # do_bracket is off — the box-size bracket is Stage A's question, not this
    # one, and it doubles the cost.
    res = run_stage_a(geo_override=geo, l_factors=factors, laminated_iron=True,
                      do_2d=True, do_bracket=False, max_iter=args.max_iter,
                      verbose=True)
    wall = time.perf_counter() - t0

    curve = res.get("l_stack_curve") or []
    rows = [{"stack_mm": float(r["stack_mm"]),
             "k_flux": float(r["k_flux"]),
             "k_flux_self": float(r["k_flux_self"]),
             "B1_mid_T": float(r["B1_mid_T"]),
             "picard_converged": bool(r.get("picard_converged")),
             "wall_s": float(r.get("wall_s") or 0.0)} for r in curve]

    out = {
        "version": "static3d-stageC-longstack-1",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "geometry_fingerprint": passport.get("geometry_fingerprint"),
        "model": {"laminated_iron": True, "two_d_leg": "converged Picard (ref2d)",
                  "element_order": 2, "note": "same model Stage A quotes k_flux from"},
        "rows": rows,
        "wall_s": round(wall, 1),
    }

    if len(rows) >= 3:
        L = [r["stack_mm"] for r in rows]
        for key in ("k_flux", "k_flux_self"):
            a, b, resid = _fit_a_plus_b_over_L(L, [r[key] for r in rows])
            out[f"fit_{key}"] = {
                "a_L_to_infinity": a, "b_end_effect": b, "residuals": resid,
                "_doc": "k(L) = a + b/L.  a is what the 3D model says an infinitely "
                        "long machine is worth against the same 2D reference, so "
                        "a == 1 is the convergence claim; b is the end effect in "
                        "k-units-times-mm and must be NEGATIVE (a deficit).",
            }
        # THE CONVERGENCE CLAIM IS ABOUT k_flux.  k_flux_self is the gap
        # fundamental's droop along the stack normalised by its OWN mid-plane
        # value — it never touches the 2D reference, so extrapolating it says
        # nothing about 2D-vs-3D agreement (an error made once already, in the
        # first reading of this very run).  k_flux = k_flux_self * B1_mid/B1_2D
        # is the ratio that has to walk to 1.
        a = out["fit_k_flux"]["a_L_to_infinity"]
        a_self = out["fit_k_flux_self"]["a_L_to_infinity"]
        all_conv = all(r["picard_converged"] for r in rows)
        out["verdict"] = {
            "a_k_flux": a,
            "a_k_flux_self": a_self,
            "all_rows_converged": all_conv,
            "converges_to_2d": bool(all_conv and abs(a - 1.0) < 0.01),
            "reading": ("k_flux extrapolates to %.4f at L -> infinity (%.2f %% from 1); "
                        "every row converged: %s.  k_flux_self -> %.4f is the pure "
                        "droop and is NOT a statement about 2D."
                        % (a, 100 * abs(a - 1.0), all_conv, a_self)),
        }

    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\nwrote %s (%.0f s)" % (args.out, wall), flush=True)
    for r in rows:
        print("  L=%6.1f mm  k_flux=%.4f  k_flux_self=%.4f  converged=%s"
              % (r["stack_mm"], r["k_flux"], r["k_flux_self"], r["picard_converged"]), flush=True)
    if "verdict" in out:
        print("\n" + out["verdict"]["reading"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
