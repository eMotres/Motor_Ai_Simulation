#!/usr/bin/env python
"""How far the exact line-to-circle fillet (2026-10-30) moves every stored machine.  Report only.

WHY.  The builder's corner fillet drew a corner between a straight edge and a
circle about the axis (stator OD / bore, rotor pole tips, a closed magnet top)
flatter than its radius and could leave one boundary vertex behind the arc.  On
G2-L40 against Fusion v41: pole tips 0.053 mm outside Fusion's arc, a 0.063 mm
spike at the tooth tips (both in the 0.65 mm air gap).  The fix changes no stored
value, so there is nothing to migrate; the machines' outlines move slightly
and their stored results describe the old outline.  This script says by how much.

WHAT IT DOES.  For every die, configuration (die geometry + its overrides) and
motor_config.yaml under the roots it builds the 2-D polygons twice: as before
(``MOTOR_AI_SIM_FILLET_LEGACY=1``, a replay of the old fillet) and as now, and
prints per part the largest boundary deviation [mm] and the area change [%].
Nothing is written.  ~1-3 s of CPU per machine.

    python scripts/report_fillet_change.py <root> [<root> ...] [--json out.json]
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mdg = _load_sibling("migrate_derived_geometry")


def _polys(geo: dict, legacy: bool):
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    old = os.environ.get("MOTOR_AI_SIM_FILLET_LEGACY")
    os.environ["MOTOR_AI_SIM_FILLET_LEGACY"] = "1" if legacy else "0"
    try:
        m = CadQueryMotor()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            m.set_parameters(dict(geo))
            return m.get_2d_polygons()
    finally:
        if old is None:
            os.environ.pop("MOTOR_AI_SIM_FILLET_LEGACY", None)
        else:
            os.environ["MOTOR_AI_SIM_FILLET_LEGACY"] = old


def compare(geo: dict) -> dict:
    """``{part: {"dev_mm", "area_pct"}}`` for stator, rotor and the magnets."""
    from shapely.ops import unary_union
    a, b = _polys(geo, True), _polys(geo, False)
    out = {}
    for part in ("stator", "rotor", "magnets"):
        ga, gb = a.get(part), b.get(part)
        if part == "magnets":
            ga = unary_union([p for p, _ in ga or []])
            gb = unary_union([p for p, _ in gb or []])
        if ga is None or gb is None or ga.is_empty or gb.is_empty:
            continue
        import shapely
        dev = shapely.hausdorff_distance(ga.boundary, gb.boundary, densify=0.25)
        out[part] = {"dev_mm": round(float(dev), 5),
                     "area_pct": round(100.0 * (gb.area / ga.area - 1.0), 4)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--json", help="also write the table here")
    a = ap.parse_args(argv)
    rows = []
    print("== report_fillet_change (nothing is written)")
    print(f"{'machine':60s} {'stator mm':>9} {'%':>8} {'rotor mm':>9} {'%':>8} {'magnet mm':>9} {'%':>8}")
    for p, kind in mdg.iter_targets(a.roots):
        try:
            doc = mdg.load_yaml(p)
            if kind == "configuration":
                die = mdg.load_yaml(p.parent / "die.yaml")
                geo = mdg.payload_geo(die, doc)
                label = f"{p.parent.name}/{p.stem}"
            else:
                geo = dict(doc.get("geometry") or {})
                label = f"{p.parent.name}/die" if kind == "die" else str(p)
            if not geo:
                continue
            res = compare(geo)
        except Exception as e:                                 # noqa: BLE001
            print(f"!! {p}: {e}")
            continue
        rows.append({"machine": label, "file": str(p), **res})

        def f(k):
            r = res.get(k)
            return (f"{r['dev_mm']:9.4f} {r['area_pct']:+8.3f}" if r else f"{'-':>9} {'-':>8}")
        print(f"{label[:60]:60s} {f('stator')} {f('rotor')} {f('magnets')}")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"== {len(rows)} machine(s); a deviation above ~0.005 mm at the air gap means its "
          f"stored results describe the old outline (rerun)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
