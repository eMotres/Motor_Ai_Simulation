"""Render the REAL full-disk (n_sectors=1) mesh for inspection, with the
defective (overlapping, edge-in->2-triangles) cells highlighted RED.
Full overview + zoom on the gap/magnet region. Also the clean ns=4 sector."""
import math, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from collections import Counter
from motor_ai_sim.simulation.fem_solver_2d import build_mesh_from_polygons, _simplify_polys
from motor_ai_sim.cadquery_geometry import CadQueryMotor

def build(ns):
    m = CadQueryMotor(); po = m.get_2d_polygons(rotor_angle_deg=0.0)
    po = _simplify_polys(po, tol_mm=0.005, stator_fillet_mm=0.0)
    mesh, ct, cf = build_mesh_from_polygons(
        po, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3,
        motion_band=True, band_thickness_mm=0.4, n_sectors=ns, geo_cfg=m.parameters)
    return mesh, ct.astype(np.int32)

def overlap_mask(T):
    ec = Counter()
    for tr in T:
        for e in ((tr[0],tr[1]),(tr[1],tr[2]),(tr[2],tr[0])): ec[tuple(sorted(e))]+=1
    bad = {e for e,v in ec.items() if v>2}
    mask = np.zeros(len(T), bool)
    for i,tr in enumerate(T):
        for e in ((tr[0],tr[1]),(tr[1],tr[2]),(tr[2],tr[0])):
            if tuple(sorted(e)) in bad: mask[i]=True; break
    return mask

fig, axes = plt.subplots(1, 3, figsize=(22, 8))

# --- full disk overview ---
mesh1, ct1 = build(1); T1 = mesh1.t.T; X1=mesh1.p[0]*1000; Y1=mesh1.p[1]*1000
bad1 = overlap_mask(T1)
ax = axes[0]
ax.triplot(X1, Y1, T1, color="0.6", lw=0.15)
ax.tripcolor(X1, Y1, T1[bad1], facecolors=np.ones(bad1.sum()), cmap="autumn", vmin=0, vmax=1)
ax.set_aspect("equal"); ax.set_title("FULL DISK n_sectors=1\n%d tris, %d defective(red, edge in >2 tris)" % (len(T1), bad1.sum()), fontsize=11)
ax.set_xlim(-135,135); ax.set_ylim(-135,135)

# --- full disk ZOOM on top gap/magnet region ---
ax = axes[1]
ax.triplot(X1, Y1, T1, color="0.55", lw=0.3)
if bad1.any(): ax.tripcolor(X1, Y1, T1[bad1], facecolors=np.ones(bad1.sum()), cmap="autumn", vmin=0, vmax=1)
ax.set_aspect("equal"); ax.set_title("FULL DISK — ZOOM gap/magnets (red=overlap)", fontsize=11)
ax.set_xlim(-22,22); ax.set_ylim(68,86)

# --- sector ns=4 zoom (clean reference) ---
mesh4, ct4 = build(4); T4=mesh4.t.T; X4=mesh4.p[0]*1000; Y4=mesh4.p[1]*1000
bad4 = overlap_mask(T4)
ax = axes[2]
ax.triplot(X4, Y4, T4, color="0.55", lw=0.3)
ax.set_aspect("equal"); ax.set_title("SECTOR n_sectors=4 (REFERENCE)\n%d tris, %d defective" % (len(T4), bad4.sum()), fontsize=11)
# zoom same physical region (around 60-90 deg)
ax.set_xlim(-2,42); ax.set_ylim(60,86)

fig.suptitle("Full-disk mesh vs sector — defective overlapping cells highlighted", fontsize=13, weight="bold")
fig.tight_layout()
out = r"C:\Users\vadim\Projects\motor_ai_sim\_mesh_fulldisk.png"
fig.savefig(out, dpi=130); print("SAVED", out, "| full bad tris=%d/%d | sector bad=%d/%d"%(bad1.sum(),len(T1),bad4.sum(),len(T4)), flush=True)
