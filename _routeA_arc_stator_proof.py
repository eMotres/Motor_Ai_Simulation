"""Proof/experiment: build the STATOR bore boundary as circle arcs (tooth tips)
+ lines (slot mouths), eps=0, coincident with the gap cells' outer arc at r_si.

The stator-half cells have uniform seams (the mid slip ring needs them), so the
r_si arc also has uniform seams. Tooth-edge/mouth corners generally land
mid-cell → those few cells subdivide (skip TF, mesh free). Count how many, and
check rings stay uniform.  Uses the real 40 mm stator half (n_sectors=2 wedge).
"""
import sys, math, numpy as np
sys.path.insert(0, "src")
import gmsh
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import motor_ai_sim.simulation.fem_solver_2d as F

m = CadQueryMotor()
polys = F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005)
clip = F._clip_polys_to_sector(dict(polys), n_sectors=2)
sg = clip["stator"]
if hasattr(sg, "geoms"):
    sg = max(sg.geoms, key=lambda s: s.area)

r_si = 12.3
mid = 12.2
K = 2
NS = 2
n_slip = 1008
S, M = F._structured_gap_sm(n_slip, NS)
Phi = 2 * math.pi / NS

gmsh.initialize([], interruptible=False)
gmsh.option.setNumber("General.Terminal", 0)
gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-3)
gmsh.model.add("proofS")
occ = gmsh.model.occ
c = occ.addPoint(0, 0, 0)


def P(r, a):
    return occ.addPoint(r * math.cos(a), r * math.sin(a), 0)


def dedupe(pts, tol=1e-3):
    out = []
    for p in pts:
        if not out or (abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol):
            out.append(p)
    if len(out) >= 2 and abs(out[0][0] - out[-1][0]) < tol and abs(out[0][1] - out[-1][1]) < tol:
        out.pop()
    return out


# ---- STATOR: exterior = outer disk boundary; interior[0] = bore -------------
# Build exterior as polyline (far from gap, fuzzy is fine).
ext = dedupe(list(sg.exterior.coords)[:-1])
ext_pts = [occ.addPoint(x, y, 0) for x, y in ext]
ext_lines = [occ.addLine(ext_pts[i], ext_pts[(i + 1) % len(ext_pts)])
             for i in range(len(ext_pts))]
ext_wire = occ.addCurveLoop(ext_lines)

# Build the bore hole with arcs on tooth-tip runs.
bore = dedupe(list(sg.interiors[0].coords)[:-1])
n = len(bore)
rr = np.hypot([p[0] for p in bore], [p[1] for p in bore])
TOL = 0.03
on = rr < r_si + TOL          # on the tooth-tip ring
# Emit a mixed loop: arc across on-ring runs (snap endpoints to nearest seam),
# line across off-ring (slot-mouth) runs.
hole_curves = []
# Build point tags: on-ring endpoints snap to seam grid; interior on-ring
# vertices are dropped (replaced by seam-subdivided arc); off-ring kept.
step = Phi / S


def snap_ang(a):
    if a < 0:
        a += 2 * math.pi
    k = round(a / step)
    return k, k * step


# Walk runs.
i = 0
seq_pts = []   # list of ('arc', p_from, p_to) via seam list OR ('line', ptA, ptB)
# Precompute point tags lazily with a cache keyed by rounded coords.
_ptcache = {}


def getP(x, y):
    key = (round(x, 6), round(y, 6))
    if key not in _ptcache:
        _ptcache[key] = occ.addPoint(x, y, 0)
    return _ptcache[key]


# Represent the whole loop as an ordered list of point tags, but where a run of
# on-ring vertices is replaced by seam-grid points spanning that run's angles.
loop = []
i = 0
while i < n:
    if on[i]:
        j = i
        while j < n and on[j]:
            j += 1
        # run [i, j-1] on ring; snap endpoints to seams, fill seams between
        a_s = math.atan2(bore[i][1], bore[i][0])
        a_e = math.atan2(bore[j - 1][1], bore[j - 1][0])
        k_s, _ = snap_ang(a_s)
        k_e, _ = snap_ang(a_e)
        # arc direction: bore interior is CW or CCW? just span inclusive between k_s,k_e
        ks, ke = (k_s, k_e)
        rng = range(ks, ke + 1) if ke >= ks else range(ks, ke - 1, -1)
        for k in rng:
            a = step * k
            loop.append(("ARC", getP(r_si * math.cos(a), r_si * math.sin(a))))
        i = j
    else:
        loop.append(("PT", getP(bore[i][0], bore[i][1])))
        i += 1

# Build curves along the loop: consecutive ARC-ARC → circle arc; else line.
hole_lines = []
Lp = len(loop)
for a in range(Lp):
    (ta, pa) = loop[a]
    (tb, pb) = loop[(a + 1) % Lp]
    if pa == pb:
        continue
    if ta == "ARC" and tb == "ARC":
        try:
            hole_lines.append(occ.addCircleArc(pa, c, pb))
        except Exception:
            hole_lines.append(occ.addLine(pa, pb))
    else:
        hole_lines.append(occ.addLine(pa, pb))
hole_wire = occ.addCurveLoop(hole_lines)
stator_surf = occ.addPlaneSurface([ext_wire, hole_wire])

# ---- gap cells mid -> r_si -------------------------------------------------
radii = np.linspace(mid, r_si, K + 1)
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

alls = [(2, stator_surf)] + [(2, x) for x in cells]
occ.fragment(alls, [])
occ.synchronize()

ncell = 0
nbad = 0
for (d, surf) in gmsh.model.getEntities(2):
    com = occ.getCenterOfMass(2, surf)
    rrc = math.hypot(com[0], com[1])
    if mid + 1e-4 < rrc < r_si - 1e-4:
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
            pass
print(f"stator gap cells 4-corner={ncell}  subdivided={nbad}  (of {K*S}={K*S})", flush=True)

gmsh.model.mesh.generate(2)
nt, nc, _ = gmsh.model.mesh.getNodes()
xyz = nc.reshape(-1, 3)
r = np.hypot(xyz[:, 0], xyz[:, 1])
gap = r[(r > mid - 1e-3) & (r < r_si + 1e-3)]
lv = []
for v in np.sort(np.unique(np.round(gap, 4))):
    if not lv or v - lv[-1] > 1e-3:
        lv.append(round(float(v), 4))
print(f"gap radial levels={len(lv)} (want {K+1}): {lv[:12]}", flush=True)
et, en = gmsh.model.mesh.getElementsByType(2)
print(f"total tris={len(en)//3}, nodes={len(nt)}", flush=True)
gmsh.finalize()
print("STATOR PROOF DONE", flush=True)
