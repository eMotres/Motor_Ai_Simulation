import gmsh, math, numpy as np
gmsh.initialize(); gmsh.option.setNumber("General.Terminal", 0)
def build(r_in, r_out, S, M, K):
    gmsh.model.add(f"a{K}")
    geo = gmsh.model.geo
    c = geo.addPoint(0,0,0)
    inner=[geo.addPoint(r_in*math.cos(2*math.pi*s/S), r_in*math.sin(2*math.pi*s/S),0) for s in range(S)]
    outer=[geo.addPoint(r_out*math.cos(2*math.pi*s/S), r_out*math.sin(2*math.pi*s/S),0) for s in range(S)]
    for s in range(S):
        s2=(s+1)%S
        ia=geo.addCircleArc(inner[s], c, inner[s2]); oa=geo.addCircleArc(outer[s], c, outer[s2])
        r1=geo.addLine(inner[s], outer[s]); r2=geo.addLine(inner[s2], outer[s2])
        cl=geo.addCurveLoop([ia, r2, -oa, -r1]); surf=geo.addPlaneSurface([cl])
        geo.mesh.setTransfiniteCurve(ia, M+1); geo.mesh.setTransfiniteCurve(oa, M+1)
        geo.mesh.setTransfiniteCurve(r1, K+1); geo.mesh.setTransfiniteCurve(r2, K+1)
        geo.mesh.setTransfiniteSurface(surf)
    geo.synchronize(); gmsh.model.mesh.generate(2)
    _, nc, _ = gmsh.model.mesh.getNodes(); xyz=nc.reshape(-1,3)
    r=np.hypot(xyz[:,0], xyz[:,1])
    lv=[]; 
    for v in np.sort(np.unique(np.round(r,5))):
        if not lv or v-lv[-1]>1e-4: lv.append(v)
    et,ent=gmsh.model.mesh.getElementsByType(2)  # triangles
    gmsh.model.remove()
    return len(lv), [round(x,4) for x in lv], len(ent)//3
for K in (1,2,3):
    n,lv,ntri = build(12.1, 12.3, 12, 20, K)
    print(f"K={K}: radial levels={n} (want {K+1})  {lv}  | {ntri} tris (want {12*20*K*2})", flush=True)
gmsh.finalize(); print("DONE", flush=True)
