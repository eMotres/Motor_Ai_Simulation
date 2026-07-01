"""PROTOTYPE step 4 (Route B, full): build the rotor half with the OCC path but
air_gap CUT at r_ro (+ transfinite N-ring at r_ro), then WELD a separate
transfinite gap annulus (r_ro..mid) into it.  Verify conforming + exact rings +
uniform slip ring.
"""
import math, numpy as np, gmsh
from shapely.geometry import Polygon as SPoly, MultiPolygon as SMPoly, Point as SPt
from shapely.ops import unary_union
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import motor_ai_sim.simulation.fem_solver_2d as fs

g = CadQueryMotor()
polys = g.get_2d_polygons(rotor_angle_deg=0.0)
mid = float(polys["mid_r_mm"])
rot = polys["rotor"]
r_ro = max(math.hypot(x, y) for p in ([rot] if not hasattr(rot,"geoms") else rot.geoms)
           for x, y in p.exterior.coords)

sector_deg = 180.0        # n_sectors=2 (half-disk build clips to 180)
N_slip = 1176
K = 3
seg = 84

def _ring_pts(r, N):
    return [(r*math.cos(2*math.pi*i/N), r*math.sin(2*math.pi*i/N)) for i in range(N)]

# ---- cut the uniform gap annulus (r_ro..mid) out of in_band: keep air BELOW r_ro
# and make its outer boundary the EXACT N-gon ring at r_ro (weld seam) ----
in_band = polys["in_band"]
disk_ro = SPoly(_ring_pts(r_ro, N_slip))          # N-gon disk up to r_ro
in_band_cut = in_band.intersection(disk_ro)
if not in_band_cut.is_valid: in_band_cut = in_band_cut.buffer(0)
print(f"in_band cut at r_ro: type={in_band_cut.geom_type}, area={in_band_cut.area:.4f} "
      f"(orig {in_band.area:.4f})")

polys_r = {
    "shaft": polys.get("shaft"),
    "rotor": polys.get("rotor"),
    "magnets": polys.get("magnets"),
    "air_gap": in_band_cut,        # NOTE: gap annulus removed; air stops at r_ro
    "mid_r_mm": mid,
}

# Build the iron half via the real OCC path. Force transfinite N-ring at r_ro.
mesh_r, tags_r, cls_r = fs.build_mesh_from_polygons(
    polys_r, n_sectors=2, rotor_angle_deg=0.0,
    mesh_size_mm=0.6, min_size_mm=0.3, normal_deviation_deg=6.0, aspect_ratio=10.0,
    outer_air_factor=1.0, motion_band=False, add_background_air=False,
    slip_transfinite_r=None,                       # NOT mid — gap provides mid
    extra_transfinite_radii=[r_ro],                # weld seam ring
    geo_cfg=None,
)
Pi = np.asarray(mesh_r.p, float)*1e3   # back to mm for comparison
Ti = np.asarray(mesh_r.t, int)
print(f"iron half: {Pi.shape[1]} nodes, {Ti.shape[1]} tris")
# count nodes on the r_ro ring of the iron mesh
ri = np.hypot(Pi[0], Pi[1])
ir_ring = np.where(np.abs(ri - r_ro) < 1e-4)[0]
ang_ir = np.degrees(np.arctan2(Pi[1,ir_ring], Pi[0,ir_ring])) % 360.0
print(f"iron r_ro ring nodes: {ir_ring.size} (expect ~{int(N_slip*sector_deg/360)+1} on 180deg)")

# ---- separate transfinite gap annulus ----
def build_tf_gap(r_in, r_out, K, N_slip, sector_deg, seg_sectors):
    step = 360.0/N_slip; M_total=int(round(sector_deg/step)); M=M_total//seg_sectors
    gmsh.initialize(); gmsh.option.setNumber("General.Terminal",0)
    gmsh.model.add("gapann"); geo=gmsh.model.geo
    c=geo.addPoint(0,0,0); seam=[sector_deg*s/seg_sectors for s in range(seg_sectors+1)]
    def _pt(r,ad): a=math.radians(ad); return geo.addPoint(r*math.cos(a),r*math.sin(a),0)
    inn=[_pt(r_in,a) for a in seam]; out=[_pt(r_out,a) for a in seam]
    for s in range(seg_sectors):
        ia=geo.addCircleArc(inn[s],c,inn[s+1]); oa=geo.addCircleArc(out[s],c,out[s+1])
        r1=geo.addLine(inn[s],out[s]); r2=geo.addLine(inn[s+1],out[s+1])
        sf=geo.addPlaneSurface([geo.addCurveLoop([ia,r2,-oa,-r1])])
        geo.mesh.setTransfiniteCurve(ia,M+1); geo.mesh.setTransfiniteCurve(oa,M+1)
        geo.mesh.setTransfiniteCurve(r1,K+1); geo.mesh.setTransfiniteCurve(r2,K+1)
        geo.mesh.setTransfiniteSurface(sf)
    geo.synchronize(); gmsh.model.mesh.generate(2)
    ntags,ncoords,_=gmsh.model.mesh.getNodes(); tag2idx={int(t):i for i,t in enumerate(ntags)}
    _,etn=gmsh.model.mesh.getElementsByType(2)
    T=np.array([tag2idx[int(t)] for t in etn],int).reshape(-1,3).T
    P=ncoords.reshape(-1,3)[:,:2].T.copy(); gmsh.finalize()
    used=np.unique(T); remap=-np.ones(P.shape[1],int); remap[used]=np.arange(used.size)
    return P[:,used], remap[T]

Pg, Tg = build_tf_gap(r_ro, mid, K, N_slip, sector_deg, seg)   # mm
# fix winding to CCW (positive area) for skfem
def areas(P,T):
    return 0.5*((P[0,T[1]]-P[0,T[0]])*(P[1,T[2]]-P[1,T[0]])-(P[0,T[2]]-P[0,T[0]])*(P[1,T[1]]-P[1,T[0]]))
if np.median(areas(Pg,Tg))<0: Tg=Tg[[0,2,1],:]
print(f"gap: {Pg.shape[1]} nodes, {Tg.shape[1]} tris, min area {areas(Pg,Tg).min():.2e}")

# ---- weld: concatenate iron + gap, merge coincident nodes ----
from skfem import MeshTri
Pall = np.hstack([Pi, Pg])*1e-3            # metres
Tall = np.hstack([Ti, Tg + Pi.shape[1]])
tags_all = np.concatenate([np.asarray(tags_r,np.int16),
                           np.full(Tg.shape[1], fs.DOM_AIRGAP, np.int16)])
mesh_all = MeshTri(Pall.astype(float), Tall.astype(np.int64))
mesh_w, tags_w = fs._weld_coincident_nodes(mesh_all, tags_all, tol_m=2e-6)
Pw = np.asarray(mesh_w.p,float)*1e3; Tw=np.asarray(mesh_w.t,int)
print(f"welded: {Pall.shape[1]} -> {Pw.shape[1]} nodes, {Tw.shape[1]} tris")

# ---- verify: exact rings in the gap band ----
rw = np.hypot(Pw[0], Pw[1])
gap_lv=[]
for v in np.sort(np.unique(np.round(rw[(rw>=r_ro-1e-4)&(rw<=mid+1e-4)],4))):
    if not gap_lv or v-gap_lv[-1]>1e-3: gap_lv.append(v)
print(f"GAP radial levels [{r_ro:.3f}..{mid:.3f}]: {[round(x,4) for x in gap_lv]} "
      f"(want {K+1}) {'OK' if len(gap_lv)==K+1 else 'FAIL'}")

# ---- verify: slip ring uniform on grid ----
step=360.0/N_slip
mid_ring=np.where(np.abs(rw-mid)<1e-5)[0]
ang=np.degrees(np.arctan2(Pw[1,mid_ring],Pw[0,mid_ring]))%360.0
ongrid=np.all(np.abs(ang-np.round(ang/step)*step)<0.02*step)
print(f"slip ring @mid: {mid_ring.size} nodes, on-grid={ongrid} "
      f"(expect {int(N_slip*sector_deg/360)+1})")

# ---- verify: conforming (Euler / boundary edges only on true boundary) ----
def boundary_edges(T):
    from collections import Counter
    cnt=Counter()
    for tri in T.T:
        for a,b in ((tri[0],tri[1]),(tri[1],tri[2]),(tri[2],tri[0])):
            cnt[(min(a,b),max(a,b))]+=1
    interior=sum(1 for v in cnt.values() if v==2)
    bnd=[e for e,v in cnt.items() if v==1]
    over=sum(1 for v in cnt.values() if v>2)
    return interior, bnd, over
inte, bnd, over = boundary_edges(Tw)
# boundary nodes radius histogram
bnd_nodes=np.unique(np.array(bnd).ravel()) if bnd else np.array([],int)
br = rw[bnd_nodes] if bnd_nodes.size else np.array([])
print(f"edges: interior(shared by 2)={inte}, boundary(1)={len(bnd)}, over(>2)={over} "
      f"{'CONFORMING' if over==0 else 'NON-CONFORMING'}")
if br.size:
    # boundary should be only at outer far-field, sector cuts, shaft bore, and mid (slip, open)
    import numpy as _np
    print(f"  boundary node radii: min={br.min():.3f} max={br.max():.3f}; "
          f"count near mid={int((_np.abs(br-mid)<1e-3).sum())} near r_ro={int((_np.abs(br-r_ro)<1e-3).sum())}")
# min triangle area over whole welded mesh
aw=areas(Pw,Tw); print(f"welded tri area: min={aw.min():.3e} negatives={int((aw<=0).sum())}")
print("DONE step4 weld")
