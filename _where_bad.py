import _use40  # noqa
import numpy as np, math
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
m = CadQueryMotor()
cols, ins = F._slot_grid_columns(m.parameters)
c0 = cols[0]
print("column0 xs:", [round(v,4) for v in c0["xs"]])
print("column0 ys (bottom..top):", [round(v,4) for v in c0["ys"]])
print("eb (block bottom, min r on that col at x-center):", round(min(c0['ys']),4))
# now build integrated and locate bad tris by (r, local slot frame)
polys = F._simplify_polys(m.get_2d_polygons(0.0), tol_mm=0.005, stator_fillet_mm=0.0, band_mode="merged")
ps = dict(polys); ps["air_outer"] = ps.pop("out_band")
mesh, tags, _ = F.build_mesh_from_polygons(ps, 0.0, 1.5, min_size_mm=max(0.02, min(ins,float(m.parameters['wire_spacing_y']))/2),
    normal_deviation_deg=8.0, aspect_ratio=10.0, geo_cfg=m.parameters, outer_air_factor=1.3,
    motion_band=False, gap_layers=2.0, n_sectors=6, add_background_air=False, structured_slot=True)
P=np.asarray(mesh.p)*1000.0; T=np.asarray(mesh.t); tags=np.asarray(tags)
a=P[:,T[0]];b=P[:,T[1]];c=P[:,T[2]]
L0=np.hypot(*(b-a));L1=np.hypot(*(c-b));L2=np.hypot(*(a-c))
def ang(la,lb,lc):
    v=(lb**2+lc**2-la**2)/(2*lb*lc+1e-30);return np.degrees(np.arccos(np.clip(v,-1,1)))
mn=np.minimum(np.minimum(ang(L1,L0,L2),ang(L2,L0,L1)),ang(L0,L1,L2))
blk=(tags==9)|(tags==10)|(tags>=200)
bad=blk&(mn<10)
idx=np.where(bad)[0]
# rotate each centroid back to the un-rotated slot frame (nearest slot angle)
slot_ang = 360.0/(int(m.parameters['num_slots'])//2)
for i in idx[:12]:
    cx=P[0,T[:,i]].mean(); cy=P[1,T[:,i]].mean()
    th=math.degrees(math.atan2(cy,cx))%slot_ang
    r=math.hypot(cx,cy)
    # unrotate by nearest multiple
    k=round(math.degrees(math.atan2(cy,cx))/slot_ang)
    aa=math.radians(-k*slot_ang)
    lx=cx*math.cos(aa)-cy*math.sin(aa); ly=cx*math.sin(aa)+cy*math.cos(aa)
    print(f"minang={mn[i]:.2f} r={r:.3f} local=({lx:.3f},{ly:.3f}) tag={int(tags[i])}")
