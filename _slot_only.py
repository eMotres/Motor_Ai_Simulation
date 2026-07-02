"""Build ONLY the structured slot cells in a bare gmsh model (no iron, no air)
and mesh them transfinite — isolates the cell subdivision from fragment.
Prints triangle quality; if THIS is clean, the slivers come from fragment
interaction, not the cell mesher."""
import _use40  # noqa
import math
import numpy as np
import gmsh
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor

m = CadQueryMotor()
gmsh.initialize([], interruptible=False)
gmsh.option.setNumber("General.Terminal", 0)
gmsh.model.add("slotonly")
occ = gmsh.model.occ
cells = F._build_structured_slot_cells(occ, m.parameters, n_sectors=1, getP=None)
occ.synchronize()
frag_tags = set(int(s) for s, d in cells)
# transfinite: reuse the SAME logic as build_mesh_from_polygons
_SS_ASPECT = 3.0; _SS_MAXDIV = 24
def _endpoints(cv):
    bp = gmsh.model.getBoundary([(1, cv)], oriented=False)
    p = [int(pt) for _, pt in bp]
    return p if len(p) == 2 else None
def _pxy(pt):
    v = gmsh.model.getValue(0, pt, []); return (v[0], v[1])
nn = 0; sk = 0
for (d, surf) in gmsh.model.getEntities(2):
    if int(surf) not in frag_tags:
        continue
    cvs = gmsh.model.getBoundary([(d, surf)], oriented=False)
    if len(cvs) != 4:
        sk += 1; continue
    edges = {}
    ok = True
    for (_cd, cv) in cvs:
        ep = _endpoints(cv)
        if ep is None: ok = False; break
        edges[int(cv)] = (ep[0], ep[1])
    if not ok: sk += 1; continue
    cl = list(edges.keys()); oc = [cl[0]]; op = [edges[cl[0]][0], edges[cl[0]][1]]
    rem = set(cl[1:])
    while rem and len(oc) < 4:
        tail = op[-1]; nx = None
        for c in rem:
            a, b = edges[c]
            if a == tail: nx = (c, b); break
            if b == tail: nx = (c, a); break
        if nx is None: ok = False; break
        oc.append(nx[0]); op.append(nx[1]); rem.discard(nx[0])
    if not ok or len(oc) != 4 or len(set(op[:4])) != 4:
        sk += 1; continue
    corners = op[:4]; cxy = [_pxy(p) for p in corners]
    def dist(a, b): return math.hypot(cxy[a][0]-cxy[b][0], cxy[a][1]-cxy[b][1])
    LA = 0.5*(dist(0,1)+dist(2,3)); LB = 0.5*(dist(1,2)+dist(3,0))
    short = max(1e-9, min(LA, LB))
    def ndf(L):
        return max(2, min(_SS_MAXDIV, max(1, math.ceil(L/(_SS_ASPECT*short)-1e-9))+1))
    nA = ndf(LA); nB = ndf(LB)
    gmsh.model.mesh.setTransfiniteCurve(oc[0], nA)
    gmsh.model.mesh.setTransfiniteCurve(oc[2], nA)
    gmsh.model.mesh.setTransfiniteCurve(oc[1], nB)
    gmsh.model.mesh.setTransfiniteCurve(oc[3], nB)
    try:
        gmsh.model.mesh.setTransfiniteSurface(surf, "Left", corners)
        nn += 1
    except Exception as e:
        sk += 1
gmsh.option.setNumber("Mesh.MeshSizeMin", 0.001)
gmsh.option.setNumber("Mesh.MeshSizeMax", 100.0)
gmsh.model.mesh.generate(2)
# collect all triangles
nt, ntt, _ = gmsh.model.mesh.getElements(2)
pts_tags, coords, _ = gmsh.model.mesh.getNodes()
coord = {int(t): (coords[3*i], coords[3*i+1]) for i, t in enumerate(pts_tags)}
tris = []
for et, ets, ens in zip(nt, ntt, gmsh.model.mesh.getElements(2)[2]):
    if int(et) != 2: continue
    conn = ens.reshape(-1, 3)
    for tr in conn:
        tris.append([coord[int(tr[0])], coord[int(tr[1])], coord[int(tr[2])]])
gmsh.finalize()
print(f"transfinite {nn} skip {sk}; {len(tris)} triangles")
ang_min = []
for (a, b, c) in tris:
    a = np.array(a); b = np.array(b); c = np.array(c)
    L0 = np.linalg.norm(b-a); L1 = np.linalg.norm(c-b); L2 = np.linalg.norm(a-c)
    def ang(la, lb, lc):
        v = (lb**2+lc**2-la**2)/(2*lb*lc+1e-30); return math.degrees(math.acos(max(-1, min(1, v))))
    ang_min.append(min(ang(L1, L0, L2), ang(L2, L0, L1), ang(L0, L1, L2)))
ang_min = np.array(ang_min)
print(f"min angle: min={ang_min.min():.2f} median={np.median(ang_min):.1f} "
      f"<5={int((ang_min<5).sum())} <10={int((ang_min<10).sum())} <15={int((ang_min<15).sum())}")
