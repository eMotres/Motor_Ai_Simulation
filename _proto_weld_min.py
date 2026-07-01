"""Minimal weld test: free-meshed inner disk with an N-gon boundary at r_ro
(forced transfinite => exactly N boundary nodes at grid angles), welded to the
separate transfinite gap annulus.  Isolates the WELD + seam-node-identity."""
import math, numpy as np, gmsh
from skfem import MeshTri
import motor_ai_sim.simulation.fem_solver_2d as fs

r_ro, mid = 12.1, 12.2
sector_deg = 180.0; N_slip = 1176; K = 3; seg = 84
step = 360.0/N_slip
M_wedge = int(round(sector_deg/step))     # angular intervals over 180deg => nodes = M_wedge+1

def _ring_pts_wedge(r, N, sector_deg):
    """N-gon vertices from angle 0..sector_deg (inclusive) on the 2pi j/N grid."""
    kmax = int(round(sector_deg/(360.0/N)))
    return [(r*math.cos(2*math.pi*i/N), r*math.sin(2*math.pi*i/N)) for i in range(kmax+1)]

def build_inner_disk_wedge(r_ro, N_slip, sector_deg):
    """Wedge of disk(r_ro): center + arc(0..sector) as N-gon chords, free-meshed,
    with the arc forced transfinite (M_wedge+1 nodes)."""
    gmsh.initialize(); gmsh.option.setNumber("General.Terminal",0)
    gmsh.model.add("innerdisk"); geo=gmsh.model.geo
    gmsh.option.setNumber("Mesh.MeshSizeMax",1.0); gmsh.option.setNumber("Mesh.MeshSizeMin",0.1)
    verts=_ring_pts_wedge(r_ro, N_slip, sector_deg)   # arc nodes (N-gon)
    c=geo.addPoint(0,0,0)
    pts=[geo.addPoint(x,y,0) for (x,y) in verts]
    # radial edges center->first, center->last
    l_start=geo.addLine(c, pts[0]); l_end=geo.addLine(pts[-1], c)
    arc_lines=[geo.addLine(pts[i],pts[i+1]) for i in range(len(pts)-1)]
    loop=geo.addCurveLoop([l_start]+arc_lines+[l_end])
    sf=geo.addPlaneSurface([loop])
    geo.synchronize()
    # force the arc chords transfinite (2 nodes each => preserves exactly the N-gon verts)
    for al in arc_lines: geo.mesh.setTransfiniteCurve(al,2)
    gmsh.model.mesh.generate(2)
    ntags,ncoords,_=gmsh.model.mesh.getNodes(); tag2idx={int(t):i for i,t in enumerate(ntags)}
    _,etn=gmsh.model.mesh.getElementsByType(2)
    T=np.array([tag2idx[int(t)] for t in etn],int).reshape(-1,3).T
    P=ncoords.reshape(-1,3)[:,:2].T.copy(); gmsh.finalize()
    used=np.unique(T); remap=-np.ones(P.shape[1],int); remap[used]=np.arange(used.size)
    return P[:,used], remap[T]

def build_tf_gap(r_in,r_out,K,N_slip,sector_deg,seg_sectors):
    step=360.0/N_slip; M_total=int(round(sector_deg/step)); M=M_total//seg_sectors
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

def areas(P,T):
    return 0.5*((P[0,T[1]]-P[0,T[0]])*(P[1,T[2]]-P[1,T[0]])-(P[0,T[2]]-P[0,T[0]])*(P[1,T[1]]-P[1,T[0]]))

Pi,Ti = build_inner_disk_wedge(r_ro, N_slip, sector_deg)
ri=np.hypot(Pi[0],Pi[1]); ring_i=np.where(np.abs(ri-r_ro)<1e-5)[0]
angi=np.sort(np.degrees(np.arctan2(Pi[1,ring_i],Pi[0,ring_i]))%360.0)
print(f"inner disk r_ro ring: {ring_i.size} nodes (want {M_wedge+1}), "
      f"grid-aligned={np.all(np.abs(angi-np.round(angi/step)*step)<1e-4)}")
if np.median(areas(Pi,Ti))<0: Ti=Ti[[0,2,1],:]

Pg,Tg = build_tf_gap(r_ro,mid,K,N_slip,sector_deg,seg)
rg=np.hypot(Pg[0],Pg[1]); ring_g=np.where(np.abs(rg-r_ro)<1e-5)[0]
print(f"gap inner r_ro ring: {ring_g.size} nodes")
if np.median(areas(Pg,Tg))<0: Tg=Tg[[0,2,1],:]

# weld
Pall=np.hstack([Pi,Pg])*1e-3; Tall=np.hstack([Ti,Tg+Pi.shape[1]])
tags=np.concatenate([np.zeros(Ti.shape[1],np.int16), np.full(Tg.shape[1],3,np.int16)])
mesh=MeshTri(Pall.astype(float),Tall.astype(np.int64))
mw,tw=fs._weld_coincident_nodes(mesh,tags,tol_m=2e-6)
Pw=np.asarray(mw.p,float)*1e3; Tw=np.asarray(mw.t,int)
print(f"weld: {Pall.shape[1]} -> {Pw.shape[1]} nodes ({Pall.shape[1]-Pw.shape[1]} merged), {Tw.shape[1]} tris")

# conformity
from collections import Counter
cnt=Counter()
for tri in Tw.T:
    for a,b in ((tri[0],tri[1]),(tri[1],tri[2]),(tri[2],tri[0])): cnt[(min(a,b),max(a,b))]+=1
over=sum(1 for v in cnt.values() if v>2); bnd=[e for e,v in cnt.items() if v==1]
rw=np.hypot(Pw[0],Pw[1]); bnd_n=np.unique(np.array(bnd).ravel()); br=rw[bnd_n]
print(f"conformity: over(>2 tris)={over} {'CONFORMING' if over==0 else 'NON-CONFORMING'}; "
      f"boundary nodes at r_ro seam={int((np.abs(br-r_ro)<1e-3).sum())} (want 0 if welded)")
gap_lv=[]
for v in np.sort(np.unique(np.round(rw[(rw>=r_ro-1e-4)&(rw<=mid+1e-4)],4))):
    if not gap_lv or v-gap_lv[-1]>1e-3: gap_lv.append(v)
print(f"gap levels: {[round(x,4) for x in gap_lv]} (want {K+1}) {'OK' if len(gap_lv)==K+1 else 'FAIL'}")
aw=areas(Pw,Tw); print(f"welded min area={aw.min():.2e} neg={int((aw<=0).sum())}")
print("DONE weld_min")
