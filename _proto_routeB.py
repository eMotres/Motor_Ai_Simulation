"""PROTOTYPE step 3 (Route B): full rotor-half mesh with a SEPARATE transfinite
gap annulus welded to the OCC iron mesh by node identity.

Rotor half (n_sectors=2 -> 180deg wedge):
  - iron mesh: rotor iron + magnets + shaft + in_band air, BUT with the uniform
    gap annulus (r_ro..mid) CUT OUT so the air stops at the r_ro circle.  Force a
    uniform N-node transfinite ring at r_ro (weld seam) and at mid is NOT present
    on this mesh anymore (mid belongs to the gap).  Wait: mid is the slip ring and
    must exist for coupling -> the gap mesh provides mid.  The iron mesh's outer
    air boundary becomes r_ro.
  - gap mesh: transfinite annulus r_ro..mid, K layers, uniform N rings at r_ro & mid.
  - weld the gap's inner ring (r_ro) to the iron's r_ro boundary ring by node id.
    The gap's outer ring (mid) is the slip ring, left free for coupling.

Verify: (1) conforming (no dangling/duplicate away from seam), (2) gap exactly K
uniform layers, (3) slip ring uniform on the grid, (4) mesh valid (positive areas).
"""
import math, numpy as np, gmsh
from shapely.geometry import Polygon as SPoly, MultiPolygon as SMPoly
from shapely.ops import unary_union
from motor_ai_sim.cadquery_geometry import CadQueryMotor

# ---------- geometry ----------
g = CadQueryMotor()
polys = g.get_2d_polygons(rotor_angle_deg=0.0)
mid = float(polys["mid_r_mm"])                    # 12.2
# rotor OD (clean circle)
rot = polys["rotor"]
r_ro = max(math.hypot(x, y) for p in ([rot] if not hasattr(rot,"geoms") else rot.geoms)
           for x, y in p.exterior.coords)          # ~12.1
print(f"mid={mid}  r_ro={r_ro:.5f}  half-gap(rotor)={mid-r_ro:.5f}")

sector_deg = 180.0
N_slip = 1176                                       # pole_pairs=7 * 168; half => 588 intervals
K = 3

# ---------- transfinite gap annulus (skfem-ready P,T) ----------
def build_tf_gap(r_in, r_out, K, N_slip, sector_deg, seg_sectors):
    step = 360.0 / N_slip
    M_total = int(round(sector_deg / step)); assert abs(M_total*step-sector_deg)<1e-9
    assert M_total % seg_sectors == 0
    M = M_total // seg_sectors
    gmsh.model.add("gapann")
    geo = gmsh.model.geo
    c = geo.addPoint(0, 0, 0)
    seam = [sector_deg*s/seg_sectors for s in range(seg_sectors+1)]
    def _pt(r, ad): a=math.radians(ad); return geo.addPoint(r*math.cos(a), r*math.sin(a), 0)
    inn = [_pt(r_in, a) for a in seam]; out = [_pt(r_out, a) for a in seam]
    for s in range(seg_sectors):
        ia=geo.addCircleArc(inn[s],c,inn[s+1]); oa=geo.addCircleArc(out[s],c,out[s+1])
        r1=geo.addLine(inn[s],out[s]); r2=geo.addLine(inn[s+1],out[s+1])
        sf=geo.addPlaneSurface([geo.addCurveLoop([ia,r2,-oa,-r1])])
        geo.mesh.setTransfiniteCurve(ia,M+1); geo.mesh.setTransfiniteCurve(oa,M+1)
        geo.mesh.setTransfiniteCurve(r1,K+1); geo.mesh.setTransfiniteCurve(r2,K+1)
        geo.mesh.setTransfiniteSurface(sf)
    geo.synchronize(); gmsh.model.mesh.generate(2)
    ntags,ncoords,_ = gmsh.model.mesh.getNodes()
    tag2idx={int(t):i for i,t in enumerate(ntags)}
    _,etn = gmsh.model.mesh.getElementsByType(2)
    T=np.array([tag2idx[int(t)] for t in etn],int).reshape(-1,3).T
    P=ncoords.reshape(-1,3)[:,:2].T.copy()
    gmsh.model.remove()
    # drop unused nodes (the origin center point)
    used=np.unique(T); remap=-np.ones(P.shape[1],int); remap[used]=np.arange(used.size)
    return P[:,used], remap[T]

gmsh.initialize(); gmsh.option.setNumber("General.Terminal", 0)
seg = 84
Pg, Tg = build_tf_gap(r_ro, mid, K, N_slip, sector_deg, seg)
rg = np.hypot(Pg[0], Pg[1])
levels=[]
for v in np.sort(np.unique(np.round(rg,4))):
    if not levels or v-levels[-1]>1e-3: levels.append(v)
print(f"gap mesh: {Pg.shape[1]} nodes, {Tg.shape[1]} tris, radial levels={[round(x,4) for x in levels]} "
      f"(want {K+1}) {'OK' if len(levels)==K+1 else 'FAIL'}")
# area positivity
def areas(P,T):
    return 0.5*((P[0,T[1]]-P[0,T[0]])*(P[1,T[2]]-P[1,T[0]])-(P[0,T[2]]-P[0,T[0]])*(P[1,T[1]]-P[1,T[0]]))
a=areas(Pg,Tg); print(f"gap tri areas: min={a.min():.3e} max={a.max():.3e} negative={int((a<=0).sum())}")
gmsh.finalize()
print("DONE step3a (gap only)")
