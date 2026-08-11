"""Stage C, part 2 — where does the 0.68 % that does NOT go away come from?

The long-stack run answered the first Stage C question: the flux deficit falls
with the stack (3.60 % at 12 mm, 2.59 % at 24, 1.18 % at 48) and a + b/L
extrapolates to a = 0.9932.  It should extrapolate to 1: at infinite length the
3D model and the 2D reference are the same machine.  0.68 % is small, but it is
a SYSTEMATIC offset, and an unattributed offset in the denominator of every
k-factor is exactly the kind of thing this passport exists to not have.

Two suspects, one solve each, at the longest stack (48 mm — the row where the
ends matter least, so anything left is not an end effect):

  * the FAR FIELD.  The 3D box is 4x the stator OD.  If the offset is the
    boundary pulling flux, a 6x box moves k towards 1 and a 8x box moves it less
    — a converging sequence.  If the box is innocent, all three agree.
  * the AXIAL DISCRETISATION.  n_stack layers over the stack.  Doubling them
    must not move k if the axial mesh is resolved.

Neither is a fit and neither is an argument: each is a number that either moves
or does not.

Run:  python scripts/stage_c_offset_probe.py [--stack-mm 48] [--out PATH]
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
    ap.add_argument("--stack-mm", type=float, default=48.0)
    ap.add_argument("--out", default=str(_ROOT / "config" / "stage_c_offset_probe.json"))
    args = ap.parse_args()

    passport = json.loads((_ROOT / "config" / "end_effect_3d.json").read_text(encoding="utf-8"))
    geo = dict(passport["pinned_geometry"]["geometry"])
    base_stack = float(geo.get("motor_length") or 12.0)
    factor = args.stack_mm / base_stack

    from motor_ai_sim.simulation.static3d.end_effect import run_stage_a

    cases = [
        ("box 4x (as quoted)", dict(box_factor=4.0, n_stack=8)),
        ("box 6x", dict(box_factor=6.0, n_stack=8)),
        ("axial layers x2", dict(box_factor=4.0, n_stack=16)),
    ]
    out = {
        "version": "static3d-stageC-offset-1",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stack_mm": args.stack_mm,
        "_doc": __doc__.strip(),
        "cases": [],
    }
    for label, kw in cases:
        t0 = time.perf_counter()
        print("\n=== %s ===" % label, flush=True)
        res = run_stage_a(geo_override=geo, l_factors=(factor,), laminated_iron=True,
                          do_2d=True, do_bracket=False, verbose=True, **kw)
        row = (res.get("l_stack_curve") or [{}])[0]
        rec = {"case": label, **kw,
               "k_flux": row.get("k_flux"), "k_flux_self": row.get("k_flux_self"),
               "B1_mid_T": row.get("B1_mid_T"),
               "picard_converged": row.get("picard_converged"),
               "wall_s": round(time.perf_counter() - t0, 1)}
        out["cases"].append(rec)
        print("  -> k_flux_self=%.5f  converged=%s  (%.0f s)"
              % (rec["k_flux_self"] or 0, rec["picard_converged"], rec["wall_s"]), flush=True)

    ks = [c["k_flux_self"] for c in out["cases"] if c["k_flux_self"]]
    if len(ks) == 3:
        out["verdict"] = {
            "d_box_6x": ks[1] - ks[0],
            "d_axial_x2": ks[2] - ks[0],
            "reading": ("box 4x -> 6x moves k_flux_self by %+.5f, doubling the axial "
                        "layers by %+.5f.  The offset belongs to whichever moves it; "
                        "if neither does, it is neither." % (ks[1] - ks[0], ks[2] - ks[0])),
        }
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("\nwrote %s" % args.out, flush=True)
    if "verdict" in out:
        print(out["verdict"]["reading"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
