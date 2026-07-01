import sys, time, math
import numpy as np
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import motor_ai_sim.simulation.fem_solver_2d as fs
side = sys.argv[1] if len(sys.argv)>1 else "rotor"
m=CadQueryMotor(); p0=m.get_2d_polygons(0.0)
polys=fs._simplify_polys(p0, tol_mm=0.005, stator_fillet_mm=1.0, n_slip=168, gap_layers=1, structured_gap=True, band_mode='merged')
ps,pr=fs._split_polys_for_sliding_band(polys)
spec=polys['mapped_gap_spec']
print('spec:',spec, flush=True)
common=dict(rotor_angle_deg=0.0, mesh_size_mm=0.6, min_size_mm=0.3, normal_deviation_deg=8.0, aspect_ratio=10.0, outer_air_factor=1.2, motion_band=False, band_thickness_mm=0.4, gap_layers=1.0, geo_cfg=m.parameters, add_background_air=False, slip_transfinite_r=spec['mid_r'], extra_transfinite_radii=[], component_mesh_mm={'stator':2.0,'rotor':2.0,'magnet':2.0})
if side=="stator":
    d=dict(ps); d['air_outer']=d.pop('out_band'); gs=dict(spec,side='stator')
    print('building STATOR half...', flush=True); t=time.time()
    mesh,tags,cls=fs.build_mesh_from_polygons(d, n_sectors=2, rotational_period_deg=None, mapped_gap_spec=gs, **common)
else:
    d=dict(pr); d['air_gap']=d.pop('in_band'); gs=dict(spec,side='rotor')
    print('building ROTOR half...', flush=True); t=time.time()
    mesh,tags,cls=fs.build_mesh_from_polygons(d, n_sectors=2, rotational_period_deg=None, mapped_gap_spec=gs, **common)
print(f'{side} done {time.time()-t:.1f}s: {mesh.p.shape[1]}n/{mesh.t.shape[1]}t', flush=True)
P=np.asarray(mesh.p)*1e3; r=np.hypot(P[0],P[1])
ri,ro=fs._mapped_gap_seam_r(gs)
lv=[]
for v in np.sort(np.unique(np.round(r[(r>=min(ri,ro)-2e-4)&(r<=max(ri,ro)+2e-4)],4))):
    if not lv or v-lv[-1]>2e-3: lv.append(v)
print(f'{side} gap band levels [{ri:.3f}..{ro:.3f}]: {[round(x,4) for x in lv]} (want 2 for K=1)', flush=True)
