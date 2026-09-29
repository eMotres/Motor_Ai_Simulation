"""Verify rotor_fill_r rounds EVERY sharp rotor corner (not just 2).
Renders the rotor boundary sharp vs rounded, full + zoomed on several poles,
and reports how many corners the fillet actually modified."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from motor_ai_sim.cadquery_geometry import CadQueryMotor

RFR = float(sys.argv[1]) if len(sys.argv) > 1 else 0.8


def get_polys(rfr):
    m = CadQueryMotor()
    m.parameters["rotor_fill_r"] = rfr
    return m.get_2d_polygons(rotor_angle_deg=0.0)


p0 = get_polys(0.0); p1 = get_polys(RFR)
r0, r1 = p0["rotor"], p1["rotor"]


def nverts(poly):
    n = 0
    for g in getattr(poly, "geoms", [poly]):
        n += len(g.exterior.coords) + sum(len(h.coords) for h in g.interiors)
    return n


print(f"rotor_fill_r=0.0 : area={r0.area:.1f} mm2  valid={r0.is_valid}  verts={nverts(r0)}  type={r0.geom_type}")
print(f"rotor_fill_r={RFR} : area={r1.area:.1f} mm2  valid={r1.is_valid}  verts={nverts(r1)}  type={r1.geom_type}")
print(f"area kept: {100*r1.area/r0.area:.1f}%   (verts {nverts(r0)} -> {nverts(r1)}; more verts = corners got arcs)")


def outline(ax, poly, color, lw=1.0):
    for g in getattr(poly, "geoms", [poly]):
        if g.is_empty:
            continue
        xy = np.array(g.exterior.coords); ax.plot(xy[:, 0], xy[:, 1], color=color, lw=lw)
        for h in g.interiors:
            hh = np.array(h.coords); ax.plot(hh[:, 0], hh[:, 1], color=color, lw=lw)


def fillrotor(ax, polys):
    rp = polys["rotor"]
    for g in getattr(rp, "geoms", [rp]):
        if g.is_empty:
            continue
        ax.fill(*np.array(g.exterior.coords).T, fc="#9ca3af", ec="none")
        for h in g.interiors:
            ax.fill(*np.array(h.coords).T, fc="white", ec="none")
    for mp, pol in polys.get("magnets", []):
        for g in getattr(mp, "geoms", [mp]):
            ax.fill(*np.array(g.exterior.coords).T, fc="#ef4444" if pol > 0 else "#3b82f6", ec="none")


fig, ax = plt.subplots(1, 3, figsize=(21, 7))
# (1) full rotor
fillrotor(ax[0], p1); ax[0].set_aspect("equal")
ax[0].set_title(f"Full rotor — rotor_fill_r={RFR} mm", fontsize=11)
# (2) zoom on top ~5 poles, OUTLINES sharp (red) vs rounded (green)
for a in (ax[1], ax[2]):
    outline(a, r0, "#ef4444", 1.0); outline(a, r1, "#10b981", 1.6)
    a.set_aspect("equal")
ax[1].set_xlim(-30, 30); ax[1].set_ylim(48, 58)
ax[1].set_title("Top poles — sharp (red) vs rounded (green)", fontsize=11)
ax[2].set_xlim(-12, 12); ax[2].set_ylim(52, 57.2)
ax[2].set_title("Close zoom — every corner should be green-rounded", fontsize=11)
plt.suptitle(f"rotor_fill_r — every rotor corner rounded (r={RFR} mm)", fontsize=13)
plt.tight_layout()
out = os.path.join(os.path.dirname(__file__), "rotor_fill_test.png")
plt.savefig(out, dpi=110)
print("saved", out)
