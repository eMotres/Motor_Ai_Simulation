"""Proof: build the ROTOR gap-facing boundary as circle arcs COINCIDENT with the
transfinite gap cells' inner arc → occ.fragment merges the shared curve → each
cell keeps 4 corners → transfinite → EXACT uniform rings, NO retract, NO filler.

Mirrors the real solver's full-disk half build (n_sectors=2 wedge), using the
real 40 mm rotor polygon (fuzzy OD ~12.1) but replacing its OD run with arcs.
"""
import sys, math, numpy as np
sys.path.insert(0, "src")
import gmsh
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import motor_ai_sim.simulation.fem_solver_2d as F

# --- real 40 mm rotor polygon, simplified + clipped to a 180 deg wedge ---
m = CadQueryMotor()
polys = F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005)
clip = F._clip_polys_to_sector(dict(polys), n_sectors=2)
rg = clip["rotor"]
if hasattr(rg, "geoms"):
    rg = max(rg.geoms, key=lambda s: s.area)
ext = list(rg.exterior.coords)[:-1]

r_ro = 12.1
mid = 12.2
K = 2
NS = 2                      # n_sectors (wedge = pi)
n_slip = 1008
S, M = F._structured_gap_sm(n_slip, NS)   # (36, 14)
Phi = 2 * math.pi / NS

gmsh.initialize([], interruptible=False)
gmsh.option.setNumber("General.Terminal", 0)
gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-3)
gmsh.model.add("proof")
occ = gmsh.model.occ
c = occ.addPoint(0, 0, 0)


def P(r, a):
    return occ.addPoint(r * math.cos(a), r * math.sin(a), 0)


# ---- build ROTOR surface: OD run replaced by seam-aligned circle arcs -------
# Walk the exterior; classify vertices on the OD ring (r ~ r_ro).
TOL = 0.03
rr = np.hypot([p[0] for p in ext], [p[1] for p in ext])
on = rr > r_ro - TOL
# Find the maximal contiguous on-ring run (single OD arc in a wedge).
# Rotate list so run is contiguous (not wrapped).
idx_on = np.where(on)[0]
# In the wedge the OD run is contiguous already (θ 0..180). Build point tags.
# Endpoints of the run:
run_start = idx_on[0]
run_end = idx_on[-1]
# angles of run endpoints -> snap to seam grid
a0 = math.atan2(ext[run_start][1], ext[run_start][0])
a1 = math.atan2(ext[run_end][1], ext[run_end][0])
if a0 < 0:
    a0 += 2 * math.pi
if a1 < 0:
    a1 += 2 * math.pi
# seam angles across [min,max]; ensure we cover [a_lo..a_hi]
a_lo, a_hi = min(a0, a1), max(a0, a1)
seam_k0 = int(round(a_lo / (Phi / S)))
seam_k1 = int(round(a_hi / (Phi / S)))
seam_angs = [Phi * k / S for k in range(seam_k0, seam_k1 + 1)]
arc_pts = [P(r_ro, a) for a in seam_angs]

# Non-ring boundary vertices (radial cuts + shaft arc): keep as polyline.
# Build the full loop: [arc_pts...] then the off-ring vertices back to start.
off = [ext[i] for i in range(len(ext)) if not on[i]]
off_pts = [occ.addPoint(x, y, 0) for x, y in off]

loop_pts = arc_pts + off_pts     # order: OD arc (seam0..seamN) then the rest
lines = []
# arcs along the OD
for i in range(len(arc_pts) - 1):
    lines.append(occ.addCircleArc(arc_pts[i], c, arc_pts[i + 1]))
# straight edges for the remainder (close the loop)
seq = arc_pts[-1:] + off_pts + arc_pts[:1]
for i in range(len(seq) - 1):
    if seq[i] != seq[i + 1]:
        lines.append(occ.addLine(seq[i], seq[i + 1]))
wire = occ.addCurveLoop(lines)
rotor_surf = occ.addPlaneSurface([wire])

# ---- gap cells r_ro -> mid, K radial x S angular ---------------------------
radii = np.linspace(r_ro, mid, K + 1)
cells = []
for ir in range(K):
    ra, rb = radii[ir], radii[ir + 1]
    for s in range(S):
        a1s = Phi * s / S
        a2s = Phi * (s + 1) / S
        p1, p2, p3, p4 = P(ra, a1s), P(ra, a2s), P(rb, a2s), P(rb, a1s)
        ain = occ.addCircleArc(p1, c, p2)
        aout = occ.addCircleArc(p4, c, p3)
        l2 = occ.addLine(p2, p3)
        l1 = occ.addLine(p1, p4)
        cells.append(occ.addPlaneSurface([occ.addCurveLoop([ain, l2, -aout, -l1])]))

alls = [(2, rotor_surf)] + [(2, x) for x in cells]
occ.fragment(alls, [])
occ.synchronize()

# ---- set gap cells transfinite ---------------------------------------------
ncell = 0
nbad = 0
for (d, surf) in gmsh.model.getEntities(2):
    com = occ.getCenterOfMass(2, surf)
    rrc = math.hypot(com[0], com[1])
    if r_ro + 1e-4 < rrc < mid - 1e-4:
        cvs = gmsh.model.getBoundary([(d, surf)], oriented=False)
        if len(cvs) != 4:
            nbad += 1
            continue
        ncell += 1
        for (cd, cv) in cvs:
            b = gmsh.model.getBoundary([(cd, cv)], oriented=False)
            rs = [math.hypot(*gmsh.model.getValue(0, pt, [])[:2]) for (pdim, pt) in b]
            gmsh.model.mesh.setTransfiniteCurve(cv, M + 1 if abs(rs[0] - rs[1]) < 1e-4 else 2)
        try:
            gmsh.model.mesh.setTransfiniteSurface(surf)
        except Exception as e:
            print("TF surf fail:", e)
print(f"gap cells 4-corner={ncell}  NON-4corner(subdivided)={nbad}  (want {K*S} clean, 0 bad)", flush=True)

gmsh.model.mesh.generate(2)
nt, nc, _ = gmsh.model.mesh.getNodes()
xyz = nc.reshape(-1, 3)
r = np.hypot(xyz[:, 0], xyz[:, 1])
gap = r[(r > r_ro - 1e-3) & (r < mid + 1e-3)]
lv = []
for v in np.sort(np.unique(np.round(gap, 4))):
    if not lv or v - lv[-1] > 1e-3:
        lv.append(round(float(v), 4))
print(f"gap radial levels={len(lv)} (want {K+1}): {lv}", flush=True)
et, en = gmsh.model.mesh.getElementsByType(2)
print(f"total tris={len(en)//3}, nodes={len(nt)}  -> ONE conforming mesh", flush=True)
gmsh.finalize()
print("PROOF DONE", flush=True)
