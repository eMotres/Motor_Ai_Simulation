"""Render the REAL get_2d_mesh_data triangles (the exact geometry the Mesh/field
view uses) to an SVG, with the stator bore drawn as a red dashed circle. If any
coil triangle crosses the bore the overflow is visible; after the fix all copper
stays outside it. Also shows whether slot corners are filleted.

Usage:  PYTHONPATH=src python design_runs/debug_mesh_svg.py FINAL.yaml OUT.svg
"""
from __future__ import annotations
import sys
from pathlib import Path
import yaml
import numpy as np
from motor_ai_sim.cadquery_geometry import CadQueryMotor

FINAL = sys.argv[1] if len(sys.argv) > 1 else "motor_40mm_final.yaml"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("design_runs/_debug_mesh.svg")
ROOT = Path(__file__).parent.parent
geo = yaml.safe_load((ROOT / "design_runs" / FINAL).read_text(encoding="utf-8"))["geometry"]

m = CadQueryMotor(); m.set_parameters(geo)
md = m.get_2d_mesh_data()
R = float(m.parameters["stator_outer_radius"])
ir = float(m.parameters["stator_inner_radius"])
C = 300.0; VW = 600.0; MARGIN = 12.0
s = (VW / 2 - MARGIN) / R


def col(k: str):
    if k.startswith("coil"):
        return "#c6822f"
    if k.startswith("magnet"):
        return "#e0556a" if int(k.split("_")[1]) % 2 == 0 else "#5b8def"
    return {"stator_core": "#26344a", "rotor_core": "#314158", "shaft": "#4a5a73"}.get(k)


out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VW:.0f} {VW:.0f}">',
       f'<rect width="{VW:.0f}" height="{VW:.0f}" fill="#0a0f1a"/>']
order = (["stator_core", "rotor_core"]
         + [k for k in md if k.startswith("coil")]
         + [k for k in md if k.startswith("magnet")] + ["shaft"])
for k in order:
    comp = md.get(k)
    c = col(k)
    if not comp or not c:
        continue
    V = np.asarray(comp["vertices"], float).reshape(-1, 3)
    F = np.asarray(comp["faces"], int).reshape(-1, 3)
    for tri in F:
        pts = " ".join(f"{C + V[i,0]*s:.1f},{C - V[i,1]*s:.1f}" for i in tri)
        out.append(f'<polygon points="{pts}" fill="{c}" stroke="{c}" stroke-width="0.3"/>')
# stator bore (air-gap line): coils must NOT cross inside this
out.append(f'<circle cx="{C}" cy="{C}" r="{ir*s:.1f}" fill="none" stroke="#ff3b3b" '
           f'stroke-width="1.3" stroke-dasharray="5 4"/>')
out.append('</svg>')
OUT.write_text("\n".join(out), encoding="utf-8")
print(f"wrote {OUT.name}  (bore r={ir:.2f} mm shown as red dashed circle)")
