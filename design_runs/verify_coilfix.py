"""Verify the coil-overflow fix: in EVERY geometry path the innermost coil vertex
must stay at or above the stator bore (stator_inner_radius). Before the fix the
mesh/FEM-copper paths placed unclamped wires that crossed the bore onto the rotor.

Usage:  PYTHONPATH=src python design_runs/verify_coilfix.py FINAL.yaml
"""
from __future__ import annotations
import sys, math
from pathlib import Path
import yaml
import numpy as np
from motor_ai_sim.cadquery_geometry import CadQueryMotor

FINAL = sys.argv[1] if len(sys.argv) > 1 else "motor_40mm_final.yaml"
ROOT = Path(__file__).parent.parent
geo = yaml.safe_load((ROOT / "design_runs" / FINAL).read_text(encoding="utf-8"))["geometry"]

m = CadQueryMotor(); m.set_parameters(geo)
p = m.parameters
inner_r = float(p["stator_inner_radius"])
print(f"{FINAL}: stator_inner_radius (bore) = {inner_r:.3f} mm  "
      f"(slot_h={geo['slot_height']} wire_h={geo['wire_height']} turns={geo['num_wires_per_slot']})")


def minr_polys(polys):
    mn = 1e9
    for poly in polys:
        for x, y in poly.exterior.coords:
            mn = min(mn, math.hypot(x, y))
    return mn

# 1) domain polygons (get_2d_polygons — was already clamped)
P = m.get_2d_polygons()
r_poly = minr_polys(P["coils"])

# 2) 2D mesh (get_2d_mesh_data — fixed)
md = m.get_2d_mesh_data()
mn_mesh = 1e9
for k, comp in md.items():
    if not k.startswith("coil"):
        continue
    verts = comp.get("vertices") or comp.get("positions") or []
    arr = np.asarray(verts, dtype=float).reshape(-1, 3) if verts else np.empty((0, 3))
    if len(arr):
        mn_mesh = min(mn_mesh, float(np.hypot(arr[:, 0], arr[:, 1]).min()))

# 3) FEM copper mesh (build_periodic_coil_mesh — fixed)
try:
    from motor_ai_sim.simulation.fem_solver_2d import build_periodic_coil_mesh
    v, *_ = build_periodic_coil_mesh(dict(geo), int(p["num_slots"]), float(p["stator_outer_radius"]))
    v = np.asarray(v)
    rr = np.hypot(v[0], v[1])
    if rr.max() < 1.0:   # metres -> mm
        rr = rr * 1000.0
    mn_fem = float(rr.min())
except Exception as e:
    mn_fem = None
    print("  build_periodic_coil_mesh:", str(e)[:80])

def verdict(mn):
    if mn is None or mn > 1e8:
        return "n/a"
    return "OK (fits)" if mn >= inner_r - 1e-6 else f"OVERFLOW by {inner_r - mn:.3f} mm"

print(f"  get_2d_polygons   coils min r = {r_poly:.3f}  -> {verdict(r_poly)}")
print(f"  get_2d_mesh_data  coils min r = {mn_mesh:.3f}  -> {verdict(mn_mesh)}")
if mn_fem is not None:
    print(f"  build_periodic_coil_mesh min r = {mn_fem:.3f}  -> {verdict(mn_fem)}")
