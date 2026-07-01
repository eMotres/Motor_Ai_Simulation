"""Inspect the gap-facing boundary of rotor iron (r~12.1) and stator bore (r~12.3)
to know whether they are clean constant-radius circles (easy conformity) or wavy."""
import math, numpy as np
from motor_ai_sim.cadquery_geometry import CadQueryMotor

g = CadQueryMotor()
polys = g.get_2d_polygons(rotor_angle_deg=0.0)
mid_r = polys["mid_r_mm"]

def boundary_at_radius(geom, r_target, tol=0.05, which="ext"):
    """Collect (angle_deg, radius) of vertices near r_target."""
    parts = list(geom.geoms) if hasattr(geom,"geoms") else [geom]
    pts=[]
    for p in parts:
        rings = [p.exterior] if which=="ext" else list(p.interiors)
        for ring in rings:
            for x,y in ring.coords:
                r=math.hypot(x,y)
                if abs(r-r_target)<tol:
                    pts.append((math.degrees(math.atan2(y,x))%360.0, r))
    pts.sort()
    return pts

# rotor outer edge near 12.1
rp = boundary_at_radius(polys["rotor"], 12.1, tol=0.15, which="ext")
print(f"rotor ext near 12.1: {len(rp)} pts")
if rp:
    rs=[r for _,r in rp]
    print(f"  radius range [{min(rs):.4f}, {max(rs):.4f}]  (spread {max(rs)-min(rs):.4f} mm)")
    print(f"  first 12 (ang,r): {[(round(a,1),round(r,4)) for a,r in rp[:12]]}")

# in_band inner boundary (facing rotor) — this is what actually bounds the gap
ib = polys["in_band"]
parts = list(ib.geoms) if hasattr(ib,"geoms") else [ib]
print(f"\nin_band: {len(parts)} parts")
for i,p in enumerate(parts):
    er=[math.hypot(x,y) for x,y in p.exterior.coords]
    print(f"  part{i} ext r[{min(er):.4f},{max(er):.4f}] ({len(er)} pts), {len(p.interiors)} holes")
    for j,h in enumerate(p.interiors):
        hr=[math.hypot(x,y) for x,y in h.coords]
        print(f"    hole{j} r[{min(hr):.4f},{max(hr):.4f}] ({len(hr)} pts)")

# stator bore near 12.3
sp = boundary_at_radius(polys["stator"], 12.3, tol=0.15, which="int")
print(f"\nstator int(bore) near 12.3: {len(sp)} pts")
if sp:
    rs=[r for _,r in sp]
    print(f"  radius range [{min(rs):.4f}, {max(rs):.4f}]  (spread {max(rs)-min(rs):.4f} mm)")
    print(f"  first 12 (ang,r): {[(round(a,1),round(r,4)) for a,r in sp[:12]]}")

# out_band inner hole (facing stator/gap)
ob = polys["out_band"]
parts = list(ob.geoms) if hasattr(ob,"geoms") else [ob]
print(f"\nout_band: {len(parts)} parts")
for i,p in enumerate(parts):
    er=[math.hypot(x,y) for x,y in p.exterior.coords]
    print(f"  part{i} ext r[{min(er):.4f},{max(er):.4f}] ({len(er)} pts), {len(p.interiors)} holes")
    for j,h in enumerate(p.interiors):
        hr=[math.hypot(x,y) for x,y in h.coords]
        print(f"    hole{j} r[{min(hr):.4f},{max(hr):.4f}] ({len(hr)} pts)")
