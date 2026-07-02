import _use40  # noqa
import numpy as np, math, logging
logging.basicConfig(level=logging.ERROR)
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
m=CadQueryMotor()
polys=F._simplify_polys(m.get_2d_polygons(0.0),tol_mm=0.005,stator_fillet_mm=0.0)
# Mesh-tab full-disk path (n_sectors=1) with structured_slot
mesh,ct,cf=F._build_full_disk_from_halves(polys,0.0,4.0,0.3,1.3,False,0.4,
    m.parameters,{"stator":2.0},normal_deviation_deg=6.0,aspect_ratio=10.0,
    gap_layers=3.0,structured_slot=True)
P=np.asarray(mesh.p)*1000.0;T=np.asarray(mesh.t);ct=np.asarray(ct)
a=P[:,T[0]];b=P[:,T[1]];c=P[:,T[2]]
L0=np.hypot(*(b-a));L1=np.hypot(*(c-b));L2=np.hypot(*(a-c))
def ang(la,lb,lc):
    v=(lb**2+lc**2-la**2)/(2*lb*lc+1e-30);return np.degrees(np.arccos(np.clip(v,-1,1)))
mn=np.minimum(np.minimum(ang(L1,L0,L2),ang(L2,L0,L1)),ang(L0,L1,L2))
blk=(ct==9)|(ct==10)|(ct>=200)
mb=mn[blk]
print(f"Mesh-tab full-disk structured_slot: tris={T.shape[1]} "
      f"insulation tags: enamel={int((ct==9).sum())} liner={int((ct==10).sum())} "
      f"block min_ang={mb.min():.2f} <15={int((mb<15).sum())}")
