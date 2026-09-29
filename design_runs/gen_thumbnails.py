"""Generate REAL-geometry cross-section thumbnails for every catalog motor.

For each catalog motor we build its actual CadQuery geometry from the preset and
pull the true 2-D Shapely polygons (get_2d_polygons) — stator steel with slots,
rotor back-iron, coils, magnets (coloured by polarity), shaft — then write a
themed SVG to web/public/motor-thumbs/<motor_id>.svg. These are the real
cross-sections, not a schematic.

Run:  PYTHONPATH=src python design_runs/gen_thumbnails.py
"""
from __future__ import annotations
import json
from pathlib import Path
from motor_ai_sim.cadquery_geometry import CadQueryMotor

ROOT = Path(__file__).parent.parent
PRESETS = json.loads((ROOT / "config" / "motor_presets.json").read_text(encoding="utf-8"))
CATALOG = json.loads((ROOT / "config" / "motor_catalog.json").read_text(encoding="utf-8"))
OUT = ROOT / "web" / "public" / "motor-thumbs"
OUT.mkdir(parents=True, exist_ok=True)

C = 60.0; VIEW = 120.0; MARGIN = 4.0
COL = dict(stator="#26344a", rotor="#314158", shaft="#4a5a73",
           coil="#c6822f", magN="#e0556a", magS="#5b8def", edge="#46597a")


def _path(geom, s: float) -> str:
    """Shapely Polygon/MultiPolygon -> SVG path 'd' (mm->view, y flipped)."""
    if geom is None or geom.is_empty:
        return ""
    geoms = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    parts = []
    for g in geoms:
        if g.is_empty:
            continue
        for ring in [g.exterior, *g.interiors]:
            pts = list(ring.coords)
            if len(pts) < 3:
                continue
            d = " L ".join(f"{C + x * s:.2f} {C - y * s:.2f}" for x, y in pts)
            parts.append("M " + d + " Z")
    return " ".join(parts)


def gen(geo: dict) -> str:
    m = CadQueryMotor(); m.set_parameters(geo)
    P = m.get_2d_polygons()
    R = float(m.parameters["stator_outer_radius"])
    s = (VIEW / 2 - MARGIN) / R
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEW:.0f} {VIEW:.0f}">']
    out.append(f'<path d="{_path(P["stator"], s)}" fill="{COL["stator"]}" fill-rule="evenodd" '
               f'stroke="{COL["edge"]}" stroke-width="0.7"/>')
    out.append(f'<path d="{_path(P["rotor"], s)}" fill="{COL["rotor"]}" fill-rule="evenodd"/>')
    for c in P.get("coils", []):
        out.append(f'<path d="{_path(c, s)}" fill="{COL["coil"]}"/>')
    for poly, pol in P.get("magnets", []):
        out.append(f'<path d="{_path(poly, s)}" fill="{COL["magN"] if pol > 0 else COL["magS"]}"/>')
    out.append(f'<path d="{_path(P["shaft"], s)}" fill="{COL["shaft"]}"/>')
    out.append("</svg>")
    return "\n".join(out)


n_ok = 0
for mtr in CATALOG.get("motors", []):
    pid = mtr.get("preset")
    pr = PRESETS.get(pid) if pid else None
    if not pr or not pr.get("geometry"):
        print(f"  skip {mtr['id']} (no preset geometry: {pid})")
        continue
    try:
        (OUT / f"{mtr['id']}.svg").write_text(gen(pr["geometry"]), encoding="utf-8")
        n_ok += 1
        print(f"  ok  {mtr['id']}.svg  (Ø{mtr.get('diameter_mm')} {mtr.get('slots')}s/{mtr.get('poles')}p)")
    except Exception as e:
        print(f"  FAIL {mtr['id']}: {type(e).__name__}: {str(e)[:80]}")
print(f"wrote {n_ok} thumbnails -> {OUT}")
