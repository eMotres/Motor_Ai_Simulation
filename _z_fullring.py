import _use40  # noqa
import math, numpy as np, logging
logging.basicConfig(level=logging.WARNING)
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
m=CadQueryMotor()
polys=m.get_2d_polygons(rotor_angle_deg=0.0)
polys=F._simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0, n_slip=1008, gap_layers=2, structured_gap=True, band_mode="merged")
ms,ts,cs,mr,tr,cr=F._build_sliding_band_meshes(
    polys, 0.0, 1.5, min_size_mm=0.25, outer_air_factor=1.3, band_thickness_mm=0.4,
    n_sectors=-1, geo_cfg=m.parameters, normal_deviation_deg=8.0, aspect_ratio=10.0,
    gap_layers=2, component_mesh_mm=None, full_ring=True, pole_copy=False)
for nm,mesh in (("STATOR",ms),("ROTOR",mr)):
    P=np.asarray(mesh.p)*1000.0; r=np.hypot(P[0],P[1])
    for mid in (12.2,):
        sel=np.abs(r-mid)<1e-3
        print(f"{nm}: nodes within 1um*1000 of mid={mid}: {int((np.abs(r-mid)<1e-3).sum())}; within 1e-6mm: {int((np.abs(r-mid)<1e-6).sum())}")
        if sel.sum():
            print(f"   exact r values near mid (unique, rounded 6): {sorted(set(np.round(r[sel],6)))[:6]}")
    band=r[(r>12.05)&(r<12.35)]; lv=[]
    for v in np.sort(np.unique(np.round(band,4))):
        if not lv or v-lv[-1]>2e-3: lv.append(round(float(v),4))
    print(f"   {nm} gap levels: {lv}")
