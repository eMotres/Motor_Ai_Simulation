"""Dump the real config-motor polygon geometry so the transfinite-gap prototype
knows exactly what boundaries it must conform to."""
import math, numpy as np
from motor_ai_sim.cadquery_geometry import CadQueryMotor

g = CadQueryMotor()
polys = g.get_2d_polygons(rotor_angle_deg=0.0)

print("keys:", sorted(polys.keys()))
mid_r = polys.get("mid_r_mm")
print("mid_r_mm =", mid_r)

def rrange(geom, name):
    if geom is None:
        print(f"  {name}: None"); return
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    ext_r = []
    int_r = []
    for p in parts:
        ext_r += [math.hypot(x, y) for x, y in p.exterior.coords]
        for h in p.interiors:
            int_r += [math.hypot(x, y) for x, y in h.coords]
    er = f"ext[{min(ext_r):.4f},{max(ext_r):.4f}]" if ext_r else "ext[]"
    ir = f"int[{min(int_r):.4f},{max(int_r):.4f}]" if int_r else "int[]"
    print(f"  {name}: {len(parts)} part(s)  {er}  {ir}")

for k in ("in_band","out_band","rotor","stator","shaft"):
    rrange(polys.get(k), k)
mags = polys.get("magnets") or []
print("  magnets:", len(mags))
for i,(m,pol) in enumerate(mags[:2]):
    rrange(m, f"    mag{i}(pol={pol})")

# rotor outer radius / stator inner radius
def outer_max(geom):
    parts = list(geom.geoms) if hasattr(geom,"geoms") else [geom]
    return max(math.hypot(x,y) for p in parts for x,y in p.exterior.coords)
def inner_min(geom):
    parts = list(geom.geoms) if hasattr(geom,"geoms") else [geom]
    vals=[math.hypot(x,y) for p in parts for h in p.interiors for x,y in h.coords]
    return min(vals) if vals else None

r_ro = outer_max(polys["rotor"]) if polys.get("rotor") is not None else None
r_si = inner_min(polys["stator"]) if polys.get("stator") is not None else None
print(f"\nr_ro (rotor OD) = {r_ro}")
print(f"r_si (stator bore) = {r_si}")
print(f"gap = {r_si - r_ro if (r_ro and r_si) else None}")
print(f"mid_r = {mid_r}  (should be ~{(r_ro+r_si)/2 if (r_ro and r_si) else None})")
