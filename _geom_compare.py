"""Geometry diagnostic: LEFT = the collocation points the PINN now samples INSIDE
the real polygons (from pinn_geom.json, the same shapes the FEM uses);
RIGHT = the real FEM polygons filled.  After the geometry-unification they MATCH.

Run on Windows (writes geom_compare.png):  python _geom_compare.py
"""
import os, sys, json, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath

ROOT = os.path.dirname(__file__)
MM = 1000.0

# regenerate the export if missing
if not os.path.exists(os.path.join(ROOT, "pinn_geom.json")):
    import subprocess
    subprocess.run([sys.executable, os.path.join(ROOT, "_export_geom.py")], cwd=ROOT)

G = json.load(open(os.path.join(ROOT, "pinn_geom.json")))
from motor_ai_sim.routes.simulation import _get_motor_geom
polys = _get_motor_geom(0.0)   # real shapely polys (mm) for the RIGHT panel


def rings_paths(rings):
    return [(MplPath(np.asarray(r["ext"], float)),
             [MplPath(np.asarray(h, float)) for h in r.get("holes", [])]) for r in rings]


def sample_in(rings, n, exclude=None):
    paths = rings_paths(rings)
    allext = np.vstack([np.asarray(r["ext"], float) for r in rings])
    lo, hi = allext.min(0), allext.max(0)
    gx, gy = [], []; have = 0; t = 0
    while have < n and t < 60:
        t += 1
        bx = np.random.uniform(lo[0], hi[0], n * 4); by = np.random.uniform(lo[1], hi[1], n * 4)
        P = np.column_stack([bx, by]); ins = np.zeros(len(P), bool)
        for ext, holes in paths:
            m = ext.contains_points(P)
            for h in holes:
                m &= ~h.contains_points(P)
            ins |= m
        if exclude:
            ex = np.zeros(len(P), bool)
            for ext, holes in exclude:
                em = ext.contains_points(P)
                for h in holes:
                    em &= ~h.contains_points(P)
                ex |= em
            ins &= ~ex
        gx.append(bx[ins]); gy.append(by[ins]); have += int(ins.sum())
    return np.concatenate(gx)[:n] * MM, np.concatenate(gy)[:n] * MM


fig, (axL, axR) = plt.subplots(1, 2, figsize=(13, 6.6), facecolor="#0f172a")

# ── LEFT: PINN collocation sampled INSIDE the real polygons ──
mag_excl = [p for m in G["magnets"] for p in rings_paths(m["rings"])]
axL.scatter(*sample_in(G["stator"], 2600), s=3, c="#aaa", label="stator iron", linewidths=0)
axL.scatter(*sample_in(G["rotor"], 1500, exclude=mag_excl), s=3, c="#555", label="rotor iron", linewidths=0)
axL.scatter(*sample_in(G["shaft"], 700), s=3, c="#777", label="shaft", linewidths=0)
axL.scatter(*sample_in(G["air_gap"], 700), s=3, c="#7db", label="air gap", linewidths=0)
fc = fN = fS = True
for c in G["coils"]:
    x, y = sample_in(c["rings"], 130)
    axL.scatter(x, y, s=4, c="orange", label="coil" if fc else None, linewidths=0); fc = False
for m in G["magnets"]:
    x, y = sample_in(m["rings"], 130)
    col = "#ef4444" if m["polarity"] > 0 else "#3b82f6"
    lab = ("magnet N" if (m["polarity"] > 0 and fN) else ("magnet S" if (m["polarity"] <= 0 and fS) else None))
    axL.scatter(x, y, s=4, c=col, label=lab, linewidths=0)
    if m["polarity"] > 0: fN = False
    else: fS = False
axL.set_title("MODULUS / PINN  — now samples the REAL polygons\n(point-in-polygon, from pinn_geom.json)",
              fontsize=11, color="#4ade80")
axL.legend(loc="upper right", fontsize=7, markerscale=3, facecolor="#1e293b", labelcolor="#e2e8f0")


# ── RIGHT: real FEM polygons (filled) ──
def fill_poly(ax, poly, **kw):
    if poly is None:
        return
    for g in list(getattr(poly, "geoms", [poly])):
        if getattr(g, "is_empty", True):
            continue
        try:
            xy = np.array(g.exterior.coords)
        except Exception:
            continue
        ax.fill(xy[:, 0], xy[:, 1], **kw)
        for h in getattr(g, "interiors", []):
            ax.fill(*np.array(h.coords).T, color="#0f172a")


fill_poly(axR, polys.get("stator"), color="#aaa", label="stator")
fill_poly(axR, polys.get("rotor"), color="#555")
fill_poly(axR, polys.get("shaft"), color="#777")
fN = fS = fc = True
for mp, pol in polys.get("magnets", []):
    lab = ("magnet N" if (pol > 0 and fN) else ("magnet S" if (pol <= 0 and fS) else None))
    fill_poly(axR, mp, color="#ef4444" if pol > 0 else "#3b82f6", label=lab)
    if pol > 0: fN = False
    else: fS = False
for c in polys.get("coils", []):
    fill_poly(axR, c, color="orange", label="coil" if fc else None); fc = False
axR.set_title("REAL / FEM  (CadQueryMotor.get_2d_polygons)\ntrapezoid spoke magnets + T-teeth + coils",
              fontsize=11, color="#60a5fa")
axR.legend(loc="upper right", fontsize=7, facecolor="#1e293b", labelcolor="#e2e8f0")

R = G["meta"]["r_stator_out"] * MM * 1.05
for ax in (axL, axR):
    ax.set_aspect("equal"); ax.set_xlim(-R, R); ax.set_ylim(-R, R)
    ax.set_facecolor("#0f172a"); ax.tick_params(colors="#94a3b8", labelsize=7)
    for s in ax.spines.values():
        s.set_color("#334155")
    ax.set_xlabel("x [mm]", color="#94a3b8", fontsize=8)
fig.suptitle("Modulus PINN now samples the SAME geometry the FEM solves — UNIFIED",
             fontsize=12.5, color="#4ade80")
fig.tight_layout()
fig.savefig(os.path.join(ROOT, "geom_compare.png"), dpi=95, facecolor="#0f172a")
print("saved geom_compare.png")
