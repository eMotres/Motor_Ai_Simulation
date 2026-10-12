"""Compare two solids dumps (cad/fc_scripts/solids_dump.py): every solid's volume, area, bounding box, centre of mass.

  python -m motor_ai_sim.cad.compare A.json B.json [--tol 1e-6] [--out result.json]

Reports the largest absolute and relative difference per quantity.  Exit code 0 when every bounding-box coordinate and
centre of mass is within --tol mm and every volume / area within --tol relative (and the solid names match).
"""
import argparse
import json
import sys


def compare(a, b):
    sa, sb = a["solids"], b["solids"]
    out = dict(a=a.get("model"), b=b.get("model"), freecad_a=a.get("freecad_version"), freecad_b=b.get("freecad_version"),
               names_equal=sorted(sa) == sorted(sb), only_a=sorted(set(sa) - set(sb)), only_b=sorted(set(sb) - set(sa)),
               n_solids=len(sa), total_volume_a=a.get("total_volume"), total_volume_b=b.get("total_volume"))
    mv = ma = mbb = mcom = 0.0
    mv_abs = 0.0
    worst = {}
    for k in sorted(set(sa) & set(sb)):
        x, y = sa[k], sb[k]
        rv = abs(x["volume"] - y["volume"]) / max(abs(x["volume"]), 1e-12)
        ra = abs(x["area"] - y["area"]) / max(abs(x["area"]), 1e-12)
        dbb = max(abs(p - q) for p, q in zip(x["bbox"], y["bbox"]))
        dc = max(abs(p - q) for p, q in zip(x["com"], y["com"]))
        mv_abs = max(mv_abs, abs(x["volume"] - y["volume"]))
        for name, val in (("volume_rel", rv), ("area_rel", ra), ("bbox_mm", dbb), ("com_mm", dc)):
            if val >= worst.get(name, (0, None))[0]:
                worst[name] = (val, k)
        mv, ma, mbb, mcom = max(mv, rv), max(ma, ra), max(mbb, dbb), max(mcom, dc)
    out.update(max_volume_rel=mv, max_volume_abs_mm3=mv_abs, max_area_rel=ma, max_bbox_mm=mbb, max_com_mm=mcom,
               worst={k: dict(value=v[0], solid=v[1]) for k, v in worst.items()},
               total_volume_rel=abs(a["total_volume"] - b["total_volume"]) / max(abs(a["total_volume"]), 1e-12))
    return out


def passes(r, tol=1e-6):
    return (r["names_equal"] and r["max_volume_rel"] <= tol and r["max_area_rel"] <= tol and r["max_bbox_mm"] <= tol
            and r["max_com_mm"] <= tol)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--tol", type=float, default=1e-6)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    r = compare(json.load(open(a.a, encoding="utf-8")), json.load(open(a.b, encoding="utf-8")))
    r["tol"] = a.tol
    r["pass"] = passes(r, a.tol)
    if a.out:
        json.dump(r, open(a.out, "w", encoding="utf-8"), indent=1)
    print(json.dumps(r, indent=1))
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
