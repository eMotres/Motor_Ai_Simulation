"""Proof: the SHIPPED _iron_arc_ring_occ builds the STATOR bore (tooth-tip arcs +
open slot mouths) coincident with the gap cells' outer arc at r_si, so every gap
cell stays 4-corner → transfinite → EXACT uniform rings, with eps=0 (no retract,
no filler).  Uses the real 40 mm stator half (n_sectors=2 wedge, 3-part MultiPolygon).
"""
import _use40  # noqa
import math, numpy as np
import gmsh
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import motor_ai_sim.simulation.fem_solver_2d as F

m = CadQueryMotor()
polys = F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005,
                          stator_fillet_mm=0.0, n_slip=1008, gap_layers=2,
                          structured_gap=True, band_mode="merged")
clip = F._clip_polys_to_sector(dict(polys), n_sectors=2)
sg = clip["stator"]

r_si = 12.3; mid = 12.2; K = 2; NS = 2; n_slip = 1008
S, M = F._structured_gap_sm(n_slip, NS)
Phi = 2.0 * math.pi / NS

gmsh.initialize([], interruptible=False)
gmsh.option.setNumber("General.Terminal", 0)
gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-3)
gmsh.model.add("proofS")
occ = gmsh.model.occ
center = occ.addPoint(0, 0, 0)
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


# The shipped helper builds the stator bore (r_hi=r_si) as seam-snapped arcs.
stator_surfs = F._iron_arc_ring_occ(occ, center, sg, r_si, NS, S, getP, dedupe)

# gap cells mid -> r_si, sharing getP so seam points coincide with the arcs.
cells, filler, _, _, Mret = F._build_structured_gap_cells(
    occ, {"r_lo": mid, "r_hi": r_si, "K": K, "n_slip": n_slip, "half": "stator"},
    NS, center, eps=0.0, getP=getP)
print(f"stator surfs={len(stator_surfs)}  cells={len(cells)}  filler={len(filler)} (want filler=0)", flush=True)

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
gmsh.finalize()
print("STATOR PROOF DONE", flush=True)
