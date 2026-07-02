import _use40  # noqa
import numpy as np, math
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import gmsh
# monkeypatch to intercept out_map: wrap occ.fragment via a flag in the module?
# Simpler: replicate the front half of build_mesh_from_polygons minimally.
m = CadQueryMotor()
polys = F._simplify_polys(m.get_2d_polygons(0.0), tol_mm=0.005, stator_fillet_mm=0.0, band_mode="merged")
ps = dict(polys); ps["air_outer"] = ps.pop("out_band")
# carve
fp = F._slot_block_footprint(m.parameters).buffer(1e-4)
ps["air_outer"] = ps["air_outer"].difference(fp).buffer(0)
if ps.get("air_gap") is not None:
    ps["air_gap"] = ps["air_gap"].difference(fp).buffer(0)
ps = F._clip_polys_to_sector(ps, n_sectors=6)

gmsh.initialize([], interruptible=False); gmsh.option.setNumber("General.Terminal", 0)
gmsh.model.add("t"); occ = gmsh.model.occ
gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-3)
from shapely.geometry import Polygon as SP
def s2occ(g):
    if g is None or g.is_empty: return []
    gs = [g] if g.geom_type=="Polygon" else list(getattr(g,"geoms",[]))
    out=[]
    for gg in gs:
        if gg.geom_type!="Polygon" or gg.area<1e-6: continue
        ext=list(gg.exterior.coords)[:-1]
        pts=[occ.addPoint(x,y,0) for x,y in ext]
        ls=[occ.addLine(pts[i],pts[(i+1)%len(pts)]) for i in range(len(pts)) if pts[i]!=pts[(i+1)%len(pts)]]
        if len(ls)<3: continue
        out.append(occ.addPlaneSurface([occ.addCurveLoop(ls)]))
    return out
ds=[]
for surf in s2occ(ps.get("air_outer")): ds.append((surf,8))
for surf in s2occ(ps.get("stator")): ds.append((surf,1))
ptc={}
def getP(x,y):
    k=(round(x,6),round(y,6)); t=ptc.get(k)
    if t is None: t=occ.addPoint(x,y,0); ptc[k]=t
    return t
cells=F._build_structured_slot_cells(occ, m.parameters, 6, getP=getP)
ss_in=[]
for (cs,dom) in cells:
    ss_in.append(len(ds)); ds.append((cs,dom))
occ.synchronize()
dt=[(2,s) for s,_ in ds]
fo, om = occ.fragment(dt, [])
occ.synchronize()
# how many outputs per slot input?
multi=0; toone=0
for ii in ss_in:
    ol=[t for (d,t) in om[ii] if d==2]
    if len(ol)>1: multi+=1
    else: toone+=1
print(f"slot inputs: {len(ss_in)}  ->1 output: {toone}  ->many: {multi}")
# for the many ones, print
cnt=0
for ii in ss_in:
    ol=[t for (d,t) in om[ii] if d==2]
    if len(ol)>1 and cnt<5:
        cnt+=1
        for t in ol:
            b=gmsh.model.getBoundary([(2,t)],oriented=False)
            print(f"  input {ii} -> face {t} with {len(b)} curves")
gmsh.finalize()
