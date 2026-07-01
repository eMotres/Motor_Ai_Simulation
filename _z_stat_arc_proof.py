"""Measure: can the STATOR bore be built as arcs (eps=0) with only a bounded
number of subdivided edge-cells, and do rings stay uniform?  Handles the
3-part MultiPolygon stator wedge.  Uniform seams (slip ring needs them) so
tooth-edge cells that straddle a seam boundary may subdivide (skip TF)."""
import _use40  # noqa
import math, numpy as np
import gmsh
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import motor_ai_sim.simulation.fem_solver_2d as F

m = CadQueryMotor()
polys = F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005)
clip = F._clip_polys_to_sector(dict(polys), n_sectors=2)
sg = clip["stator"]
parts = list(sg.geoms) if hasattr(sg, "geoms") else [sg]

r_si = 12.3; mid = 12.2; K = 2; NS = 2; n_slip = 1008
S, M = F._structured_gap_sm(n_slip, NS)
Phi = 2 * math.pi / NS
step = Phi / S

gmsh.initialize([], interruptible=False)
gmsh.option.setNumber("General.Terminal", 0)
gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-3)
gmsh.model.add("proofS2")
occ = gmsh.model.occ
c = occ.addPoint(0, 0, 0)
_ptcache = {}


def getP(x, y):
    key = (round(x, 6), round(y, 6))
    if key not in _ptcache:
        _ptcache[key] = occ.addPoint(x, y, 0)
    return _ptcache[key]


def dedupe(pts, tol=1e-3):
    out = []
    for p in pts:
        if not out or (abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol):
            out.append(p)
    if len(out) >= 2 and abs(out[0][0] - out[-1][0]) < tol and abs(out[0][1] - out[-1][1]) < tol:
        out.pop()
    return out


def ring_wire(coords, r_ring, on_test):
    """Build a curve loop replacing on-ring runs with seam-snapped arcs."""
    pts = dedupe(coords)
    n = len(pts)
    r = np.hypot([p[0] for p in pts], [p[1] for p in pts])
    on = on_test(r)
    loop = []
    i = 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            a_s = math.atan2(pts[i][1], pts[i][0])
            a_e = math.atan2(pts[j - 1][1], pts[j - 1][0])
            k_s = round(((a_s % (2 * math.pi)) / step))
            k_e = round(((a_e % (2 * math.pi)) / step))
            rng = range(k_s, k_e + 1) if k_e >= k_s else range(k_s, k_e - 1, -1)
            for k in rng:
                a = step * k
                loop.append(("ARC", getP(r_ring * math.cos(a), r_ring * math.sin(a))))
            i = j
        else:
            loop.append(("PT", getP(pts[i][0], pts[i][1])))
            i += 1
    curves = []
    L = len(loop)
    for a in range(L):
        (ta, pa) = loop[a]
        (tb, pb) = loop[(a + 1) % L]
        if pa == pb:
            continue
        if ta == "ARC" and tb == "ARC":
            try:
                curves.append(occ.addCircleArc(pa, c, pb))
            except Exception:
                curves.append(occ.addLine(pa, pb))
        else:
            curves.append(occ.addLine(pa, pb))
    return occ.addCurveLoop(curves)


stator_surfs = []
for pp in parts:
    w = ring_wire(list(pp.exterior.coords)[:-1], r_si, lambda r: r < r_si + 0.03)
    hole_wires = [ring_wire(list(h.coords)[:-1], r_si, lambda r: r < r_si + 0.03)
                  for h in pp.interiors]
    stator_surfs.append(occ.addPlaneSurface([w, *hole_wires]))

# gap cells mid -> r_si
radii = np.linspace(mid, r_si, K + 1)
cells = []
for ir in range(K):
    ra, rb = radii[ir], radii[ir + 1]
    for s in range(S):
        a1s = Phi * s / S; a2s = Phi * (s + 1) / S
        p1, p2, p3, p4 = getP(ra*math.cos(a1s), ra*math.sin(a1s)), getP(ra*math.cos(a2s), ra*math.sin(a2s)), getP(rb*math.cos(a2s), rb*math.sin(a2s)), getP(rb*math.cos(a1s), rb*math.sin(a1s))
        ain = occ.addCircleArc(p1, c, p2); aout = occ.addCircleArc(p4, c, p3)
        l2 = occ.addLine(p2, p3); l1 = occ.addLine(p1, p4)
        cells.append(occ.addPlaneSurface([occ.addCurveLoop([ain, l2, -aout, -l1])]))

alls = [(2, x) for x in stator_surfs] + [(2, x) for x in cells]
occ.fragment(alls, [])
occ.synchronize()

ncell = 0; nbad = 0
for (d, surf) in gmsh.model.getEntities(2):
    com = occ.getCenterOfMass(2, surf); rrc = math.hypot(com[0], com[1])
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
        except Exception:
            pass
print(f"STATOR gap cells 4-corner={ncell} subdivided={nbad} (of {K*S})", flush=True)
gmsh.model.mesh.generate(2)
nt, nc, _ = gmsh.model.mesh.getNodes()
xyz = nc.reshape(-1, 3); r = np.hypot(xyz[:, 0], xyz[:, 1])
gap = r[(r > mid - 1e-3) & (r < r_si + 1e-3)]
lv = []
for v in np.sort(np.unique(np.round(gap, 4))):
    if not lv or v - lv[-1] > 1e-3:
        lv.append(round(float(v), 4))
print(f"gap radial levels={len(lv)} (want {K+1}): {lv[:12]}", flush=True)
et, en = gmsh.model.mesh.getElementsByType(2)
print(f"total tris={len(en)//3}, nodes={len(nt)}", flush=True)
gmsh.finalize()
print("DONE", flush=True)
