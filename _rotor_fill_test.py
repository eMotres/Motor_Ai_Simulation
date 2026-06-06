"""Verify rotor_fill_r: render the rotor polygon with the fillet OFF vs ON,
zoomed on the pole-tip corners, and report area/validity (geometry not broken)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from motor_ai_sim.cadquery_geometry import CadQueryMotor


def get_polys(rfr):
    m = CadQueryMotor()
    m.parameters["rotor_fill_r"] = rfr
    return m.get_2d_polygons(rotor_angle_deg=0.0)


p0 = get_polys(0.0); p1 = get_polys(1.6)
r0, r1 = p0["rotor"], p1["rotor"]
print(f"rotor_fill_r=0.0 : area={r0.area:.1f} mm²  valid={r0.is_valid}  parts={getattr(r0,'geom_type','?')}")
print(f"rotor_fill_r=1.6 : area={r1.area:.1f} mm²  valid={r1.is_valid}  parts={getattr(r1,'geom_type','?')}")
print(f"area kept: {100*r1.area/r0.area:.1f}%")


def draw(ax, polys, title, zoom=None):
    def fill(poly, **kw):
        for g in getattr(poly, "geoms", [poly]):
            if g.is_empty:
                continue
            xy = np.array(g.exterior.coords); ax.fill(xy[:, 0], xy[:, 1], **kw)
            for h in g.interiors:
                ax.fill(*np.array(h.coords).T, color="white")
    fill(polys.get("rotor"), fc="#6b7280", ec="k", lw=0.6)
    for mp, pol in polys.get("magnets", []):
        fill(mp, fc="#ef4444" if pol > 0 else "#3b82f6", ec="none")
    ax.set_aspect("equal"); ax.set_title(title, fontsize=11)
    if zoom:
        ax.set_xlim(zoom[0], zoom[1]); ax.set_ylim(zoom[2], zoom[3])


fig, ax = plt.subplots(1, 2, figsize=(15, 7.5))
Z = (-22, 22, 40, 59)   # zoom on the top pole-tip surface
draw(ax[0], p0, "rotor_fill_r = 0  (sharp corners)", Z)
draw(ax[1], p1, "rotor_fill_r = 1.6 mm  (rounded)", Z)
plt.suptitle("Rotor pole-tip corners — sharp vs rounded", fontsize=13)
plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), "rotor_fill_test.png"), dpi=100)
print("saved rotor_fill_test.png")
