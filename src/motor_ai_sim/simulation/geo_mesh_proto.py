"""Prototype: geometry-driven stator-half mesh via triangle (CDT on real CQ
polygons).  Boundaries = real geometry (all fillets) → conforming mesh →
exact centroid tags."""
import sys, numpy as np, collections, time
sys.path.insert(0, r"C:/Users/vadim/Projects/motor_ai_sim/src")
import triangle as tr
from shapely import contains_xy
from shapely.geometry import LineString, MultiLineString
from shapely.ops import unary_union
from motor_ai_sim.cadquery_geometry import CadQueryMotor

m = CadQueryMotor(); p = m.parameters
P = m.get_2d_polygons(rotor_angle_deg=0.0)
R_bore = float(p["stator_inner_radius"])      # 54.6
R_o = float(p["stator_outer_radius"])         # 75
r_out = R_o * 1.2

# collect ALL region boundaries as LineStrings, then NODE them with shapely
lines = []
g = P["stator"]
for gg in getattr(g, "geoms", [g]):
    lines.append(LineString(gg.exterior.coords))
    for hole in gg.interiors:
        lines.append(LineString(hole.coords))
for c in P["coils"]:
    lines.append(LineString(c.exterior.coords))
for c in P.get("slot_insulation") or []:
    lines.append(LineString(c.exterior.coords))
for c in P.get("wire_insulation") or []:
    lines.append(LineString(c.exterior.coords))
n_bore = 720
ang = np.linspace(0, 2*np.pi, n_bore, endpoint=False)
bore = np.c_[R_bore*np.cos(ang), R_bore*np.sin(ang)]
lines.append(LineString(np.vstack([bore, bore[:1]])))
n_out = 240
ao = np.linspace(0, 2*np.pi, n_out, endpoint=False)
oc = np.c_[r_out*np.cos(ao), r_out*np.sin(ao)]
lines.append(LineString(np.vstack([oc, oc[:1]])))

t_node = time.time()
noded = unary_union(lines)          # splits at every intersection, merges dups
print(f"noded arrangement in {time.time()-t_node:.2f}s: {noded.geom_type}")

# extract unique vertices + segments from the noded planar graph
vmap = {}; verts = []; segs = []
def vid(x, y):
    k = (round(x, 6), round(y, 6))
    if k not in vmap:
        vmap[k] = len(verts); verts.append([x, y])
    return vmap[k]
for ls in getattr(noded, "geoms", [noded]):
    xy = np.asarray(ls.coords)
    idx = [vid(x, y) for x, y in xy]
    for i in range(len(idx)-1):
        if idx[i] != idx[i+1]:
            segs.append((idx[i], idx[i+1]))
S = np.array(sorted({(min(a, b), max(a, b)) for a, b in segs}))
V = np.array(verts)
print(f"PSLG: {len(V)} verts, {len(S)} segments")
A = dict(vertices=V, segments=S, holes=np.array([[0.0, 0.0]]))
t0 = time.time()
# q30 = min angle 30deg, a = max area (mm^2); p = planar straight line graph
out = tr.triangulate(A, f"pq28a0.35")
dt = time.time() - t0
TV = out["vertices"]; TT = out["triangles"]
print(f"triangle: {len(TV)} verts, {len(TT)} tris, {dt:.2f}s")

# tag by centroid
C = TV[TT].mean(1)
iron = contains_xy(P["stator"], C[:,0], C[:,1])
print(f"iron tris: {int(iron.sum())}, air/coil: {int((~iron).sum())}")
# render
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
fig,ax=plt.subplots(figsize=(11,10))
col=np.where(iron,"#57666f","#8ca6c7")
# coils
inco=np.zeros(len(TT),bool)
for c in P["coils"]: inco|=contains_xy(c,C[:,0],C[:,1])
col=np.where(inco,"#f9bf24",col)
ax.add_collection(PolyCollection(TV[TT],facecolors=col,edgecolors="#111",linewidths=0.15))
ax.set_xlim(-2,56); ax.set_ylim(-2,56); ax.set_aspect("equal"); ax.set_facecolor("#0b0b0b")
ax.set_title(f"GEO-DRIVEN stator half (triangle CDT on real polygons) {len(TT)} tris")
plt.tight_layout(); plt.savefig(r"C:/Users/vadim/AppData/Local/Temp/claude/C--Users-vadim-Projects-Motor-Optimization-AI/4baf3446-3813-46b8-803e-79b9c27f2cf3/scratchpad/geo_stator.png",dpi=110)
print("saved geo_stator.png")
# quality
p0,p1,p2=TV[TT[:,0]],TV[TT[:,1]],TV[TT[:,2]]
a=np.linalg.norm(p1-p0,axis=1);b=np.linalg.norm(p2-p1,axis=1);cc=np.linalg.norm(p0-p2,axis=1)
s=0.5*(a+b+cc);area=np.sqrt(np.maximum(s*(s-a)*(s-b)*(s-cc),0))
AR=np.maximum(np.maximum(a,b),cc)/np.maximum(2*area/np.maximum(np.maximum(a,b),cc),1e-12)
print(f"AR max {AR.max():.1f}, AR>10 {int((AR>10).sum())} (CDT quality mesh)")
