import sys, time
print('importing...', flush=True); t=time.time()
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import motor_ai_sim.simulation.fem_solver_2d as fs
import numpy as np
print(f'imported {time.time()-t:.1f}s', flush=True)
m=CadQueryMotor(); p0=m.get_2d_polygons(0.0)
polys=fs._simplify_polys(p0, tol_mm=0.005, stator_fillet_mm=1.0, n_slip=168, gap_layers=1, structured_gap=True, band_mode='merged')
print('spec set:', polys.get('mapped_gap_spec') is not None, flush=True)
t=time.time()
ms,ts,cs,mr,tr,cr=fs._build_sliding_band_meshes(polys,0.0,0.6,min_size_mm=0.3,outer_air_factor=1.2,band_thickness_mm=0.4,n_sectors=2,geo_cfg=m.parameters,normal_deviation_deg=8.0,aspect_ratio=10.0,gap_layers=1.0,component_mesh_mm=None,full_ring=False,pole_copy=False)
print(f'BUILD {time.time()-t:.1f}s: stator {ms.p.shape[1]}n/{ms.t.shape[1]}t rotor {mr.p.shape[1]}n/{mr.t.shape[1]}t', flush=True)
# verify rings
P=np.hstack([ms.p,mr.p])*1e3; r=np.hypot(P[0],P[1])
spec=polys['mapped_gap_spec']; r_ro,mid,r_si=spec['r_ro'],spec['mid_r'],spec['r_si']
lv=[]
for v in np.sort(np.unique(np.round(r[(r>=r_ro-2e-4)&(r<=r_si+2e-4)],4))):
    if not lv or v-lv[-1]>2e-3: lv.append(v)
print(f'gap levels K=1: {[round(x,4) for x in lv]} (want 3)', flush=True)
