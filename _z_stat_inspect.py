import _use40  # noqa
import math, numpy as np
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import motor_ai_sim.simulation.fem_solver_2d as F
m = CadQueryMotor()
polys = F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005)
clip = F._clip_polys_to_sector(dict(polys), n_sectors=2)
sg = clip["stator"]
print("stator:", sg.geom_type, "parts:", len(list(sg.geoms)) if hasattr(sg, "geoms") else 1)
parts = list(sg.geoms) if hasattr(sg, "geoms") else [sg]
for pi, pp in enumerate(parts):
    ext = list(pp.exterior.coords)[:-1]
    r = np.hypot([x for x, y in ext], [y for x, y in ext])
    print(f"  part{pi}: area={pp.area:.2f} n={len(ext)} rmin={r.min():.3f} rmax={r.max():.3f} interiors={len(list(pp.interiors))}")
sg = max(parts, key=lambda s: s.area)
ext = list(sg.exterior.coords)[:-1]
r = np.hypot([x for x, y in ext], [y for x, y in ext])
th = np.degrees(np.arctan2([y for x, y in ext], [x for x, y in ext])) % 360
on = r < 12.35
prev = None
runs = []
for i in range(len(ext)):
    b = "BORE" if on[i] else "off"
    if b != prev:
        runs.append([b, th[i], th[i], 1]); prev = b
    else:
        runs[-1][2] = th[i]; runs[-1][3] += 1
nb = sum(1 for x in runs if x[0] == "BORE")
print(f"\nlargest part: {len(runs)} runs, {nb} BORE(tooth-tip) runs:")
for rr in runs:
    if rr[0] == "BORE":
        print(f"   BORE th {rr[1]:7.2f}->{rr[2]:7.2f} n={rr[3]}")
