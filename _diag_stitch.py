"""Build the FULL disk by stitching TWO clean 1/2 (n_sectors=2) meshes:
copy the half, rotate 180deg, weld coincident seam nodes, reclassify cells by
centroid against the full polygons, solve as a genuine full disk (no periodic
BC). Validate: 0 overlaps + field/torque match the sector."""
import math, dataclasses, numpy as np
from collections import Counter
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
import shapely
from shapely import STRtree
from skfem import MeshTri
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, build_materials, _simplify_polys,
    solve_magnetostatics, _per_triangle_B,
    DOM_MAG_BASE, DOM_COIL_BASE, DOM_SHAFT, DOM_ROTOR, DOM_STATOR,
    DOM_AIRGAP, DOM_AIR)
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
from motor_ai_sim.cadquery_geometry import CadQueryMotor

p = params_from_config(); d = MotorDomains2D(p)
r_ro, r_si = p.r_rotor_out, p.r_stator_in
sa = p.slot_width_m * p.slot_height_m * p.fill_factor


def edge2(T):
    ec = Counter()
    for tr in T:
        for e in ((tr[0], tr[1]), (tr[1], tr[2]), (tr[2], tr[0])):
            ec[tuple(sorted(e))] += 1
    return sum(1 for v in ec.values() if v > 2)


# 1) clean half mesh (n_sectors=2)
m = CadQueryMotor(); po2 = m.get_2d_polygons(rotor_angle_deg=0.0)
po2 = _simplify_polys(po2, tol_mm=0.005, stator_fillet_mm=0.0)
mesh2, ct2, cf2 = build_mesh_from_polygons(
    po2, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3, motion_band=True,
    band_thickness_mm=0.4, n_sectors=2, geo_cfg=m.parameters)
V = mesh2.p.T.copy(); T = mesh2.t.T.copy(); N = len(V)
print("half (n=2): nodes=%d tris=%d  >2edges=%d" % (N, len(T), edge2(T)), flush=True)

# 2) stitch: original + 180deg-rotated copy, then weld coincident nodes
V_full = np.vstack([V, -V])               # 180deg rotation = (x,y)->(-x,-y)
T_full = np.vstack([T, T + N])
n2 = len(V_full)
pairs = cKDTree(V_full).query_pairs(r=1e-7)
if pairs:
    ij = np.array(list(pairs)).T
    g = sp.coo_matrix((np.ones(ij.shape[1]), (ij[0], ij[1])), shape=(n2, n2))
    _, lab = connected_components(g + g.T, directed=False)
else:
    lab = np.arange(n2)
uniq, inv = np.unique(lab, return_inverse=True)
Vw = np.zeros((len(uniq), 2))
np.add.at(Vw, inv, V_full)
cnt = np.bincount(inv); Vw /= cnt[:, None]
Tw = inv[T_full]
# drop degenerate triangles (any two welded nodes equal)
good = (Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2])
Tw = Tw[good]
meshF = MeshTri(Vw.T, Tw.T.copy())
print("stitched full: nodes=%d tris=%d  welded_pairs=%d  >2edges=%d"
      % (len(Vw), len(Tw), len(pairs), edge2(Tw)), flush=True)

# 3) full polygons (no clip) for materials + classification
poF = m.get_2d_polygons(rotor_angle_deg=0.0)
poF = _simplify_polys(poF, tol_mm=0.005, stator_fillet_mm=0.0)

# 4) classify each TRIANGLE by centroid (triangles are convex → centroid inside)
cen = Vw[Tw].mean(axis=1)
clf = []
for i, (mp, _pl) in enumerate(poF.get("magnets", [])):
    if mp is not None and not mp.is_empty: clf.append((mp, DOM_MAG_BASE + i))
for i, cp in enumerate(poF.get("coils", [])):
    if cp is not None and not cp.is_empty: clf.append((cp, DOM_COIL_BASE + i))
for k, dm in (("shaft", DOM_SHAFT), ("rotor", DOM_ROTOR), ("stator", DOM_STATOR)):
    gg = poF.get(k)
    if gg is not None and not gg.is_empty: clf.append((gg, dm))
ctF = np.full(len(Tw), DOM_AIR, dtype=np.int32)
rr = np.hypot(cen[:, 0], cen[:, 1])
ctF[(rr >= r_ro) & (rr <= r_si)] = DOM_AIRGAP
# UNITS: mesh is in METRES, polygons in MM → convert centroids to mm.
cen_mm = cen * 1000.0
# Apply LEAST-specific first so MOST-specific (magnets/coils, front of clf)
# overwrites last → wins.  contains_xy is vectorized point-in-polygon.
for g, tag in reversed(clf):
    mask = shapely.contains_xy(g, cen_mm[:, 0], cen_mm[:, 1])
    ctF[mask] = tag
print("classified: magnets=%d coils=%d rotor=%d stator=%d shaft=%d airgap=%d"
      % (int(((ctF >= DOM_MAG_BASE) & (ctF < DOM_COIL_BASE)).sum()),
         int((ctF >= DOM_COIL_BASE).sum()), int((ctF == DOM_ROTOR).sum()),
         int((ctF == DOM_STATOR).sum()), int((ctF == DOM_SHAFT).sum()),
         int((ctF == DOM_AIRGAP).sum())), flush=True)

# 5) materials on full polys (demag OFF for clean field compare)
mats = build_materials({"A": 0., "B": 0., "C": 0.}, d.winding_layout, poF, 0.0, sa, 15)
for t in list(mats):
    if t >= DOM_MAG_BASE:
        try: mats[t].bh_curve = None
        except Exception: mats[t] = dataclasses.replace(mats[t], bh_curve=None)

# 6) solve genuine full disk (no periodic BC)
A = solve_magnetostatics(meshF, ctF, mats, n_sectors=1,
                         pole_pairs_per_sector_is_half_integer=False)
Bx, By = _per_triangle_B(meshF, A); Bm = np.hypot(Bx, By)
c = meshF.p[:, meshF.t].mean(axis=1); rc = np.hypot(c[0], c[1])
gap = float(Bm[(rc >= r_ro) & (rc <= r_si)].mean())
print("STITCHED full-disk: gap|B|=%.3f T   (sector reference = 1.316)" % gap, flush=True)
