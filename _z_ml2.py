import _use40  # noqa
import math, numpy as np, logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s|%(message)s")
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
m=CadQueryMotor()
polys=F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005, stator_fillet_mm=0.0, n_slip=1008, gap_layers=2, structured_gap=True, band_mode="merged")
ps,pr=F._split_polys_for_sliding_band(polys)
kw=dict(mesh_size_mm=1.5,min_size_mm=0.25,normal_deviation_deg=8.0,aspect_ratio=10.0,gap_layers=2,slip_transfinite_r=12.2)
for name,ph in (("ROTOR",pr),("STATOR",ps)):
    mesh,tags,clf=F.build_mesh_from_polygons(ph,n_sectors=2,**kw)
    P=np.asarray(mesh.p)*1000.0; r=np.hypot(P[0],P[1])
    band=r[(r>12.05)&(r<12.35)]
    lv=[]
    for v in np.sort(np.unique(np.round(band,4))):
        if not lv or v-lv[-1]>2e-3: lv.append(round(float(v),4))
    print(f"{name}: gap-band radial levels: {lv}")
    mid=12.2; sel=np.abs(r-mid)<2e-3
    ang=np.degrees(np.arctan2(P[1,sel],P[0,sel]))%360.0
    step=360.0/1008; kg=np.round(ang/step); og=np.abs(ang-kg*step)<0.05*step
    print(f"   nodes at mid={mid}: {int(sel.sum())}, on 1008-grid: {int(og.sum())}")
