import gmsh, math, numpy as np
gmsh.initialize(); gmsh.option.setNumber("General.Terminal",0)
gmsh.model.add("routeA")
occ=gmsh.model.occ
r_ro,r_si,r_out=12.1,12.3,20.0
K=2; S=24; M=7            # 2K=4 rings, S*M=168 angular
# --- iron as OCC (like the real shapely->OCC path): rotor disk + stator annulus ---
rotor=occ.addDisk(0,0,0,r_ro,r_ro)
so=occ.addDisk(0,0,0,r_out,r_out); si=occ.addDisk(0,0,0,r_si,r_si)
stator=occ.cut([(2,so)],[(2,si)],removeTool=True)[0]
# --- gap cylinder-sectors: 2K radial x S angular cells (r_ro..r_si) ---
radii=np.linspace(r_ro,r_si,2*K+1); c=occ.addPoint(0,0,0); cells=[]
def P(r,a): return occ.addPoint(r*math.cos(a),r*math.sin(a),0)
for ir in range(2*K):
    ra,rb=radii[ir],radii[ir+1]
    for s in range(S):
        a1=2*math.pi*s/S; a2=2*math.pi*(s+1)/S
        p1,p2,p3,p4=P(ra,a1),P(ra,a2),P(rb,a2),P(rb,a1)
        ain=occ.addCircleArc(p1,c,p2); aout=occ.addCircleArc(p4,c,p3)
        l2=occ.addLine(p2,p3); l1=occ.addLine(p1,p4)
        cells.append(occ.addPlaneSurface([occ.addCurveLoop([ain,l2,-aout,-l1])]))
alls=[(2,rotor)]+[(2,x) for _,x in stator]+[(2,x) for x in cells]
occ.fragment(alls,[]); occ.synchronize()
# --- set gap cells transfinite (1 radial layer each; M angular) ---
ncell=0
for (d,surf) in gmsh.model.getEntities(2):
    com=occ.getCenterOfMass(2,surf); rr=math.hypot(com[0],com[1])
    if r_ro+1e-4<rr<r_si-1e-4:
        ncell+=1
        for (cd,cv) in gmsh.model.getBoundary([(d,surf)],oriented=False):
            b=gmsh.model.getBoundary([(cd,cv)],oriented=False)
            rs=[math.hypot(*gmsh.model.getValue(0,pt,[])[:2]) for (pdim,pt) in b]
            gmsh.model.mesh.setTransfiniteCurve(cv, M+1 if abs(rs[0]-rs[1])<1e-4 else 2)
        try: gmsh.model.mesh.setTransfiniteSurface(surf)
        except Exception as e: print("TF surf fail:",e)
print(f"gap cells found={ncell} (want {2*K*S})",flush=True)
gmsh.model.mesh.generate(2)
# --- verify: gap radial levels + conformity (one mesh = auto-conform) ---
nt,nc,_=gmsh.model.mesh.getNodes(); xyz=nc.reshape(-1,3); r=np.hypot(xyz[:,0],xyz[:,1])
gap=r[(r>r_ro-1e-3)&(r<r_si+1e-3)]; lv=[]
for v in np.sort(np.unique(np.round(gap,4))):
    if not lv or v-lv[-1]>1e-3: lv.append(round(float(v),4))
et,en=gmsh.model.mesh.getElementsByType(2)
print(f"gap radial levels={len(lv)} (want {2*K+1}): {lv}",flush=True)
print(f"total tris={len(en)//3}, total nodes={len(nt)}  -> ONE conforming mesh (gmsh fragment)",flush=True)
gmsh.finalize(); print("DONE",flush=True)
