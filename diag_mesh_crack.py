"""Check for a non-conforming mesh crack at the mid_r slip circle in the full
disk (n_sectors=1) vs a sector.  Two coincident nodes at the same (x,y) that the
solver treats as separate = a crack that blocks flux.  Count near-duplicate
nodes overall and near mid_r."""
import math, numpy as np
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.fem_solver_2d import build_mesh_from_polygons, _simplify_polys
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.simulation.geometry_2d import params_from_config

p = params_from_config()
mid_r = 0.5*(p.r_rotor_out + p.r_stator_in)   # slip circle [m]
print("mid_r = %.4f m (%.2f mm), gap %.2f-%.2f mm" %
      (mid_r, mid_r*1e3, p.r_rotor_out*1e3, p.r_stator_in*1e3))

def dup_count(P, rband=None):
    # round to 1e-7 m (0.1 micron) and count coincident node groups with >1 node
    key = np.round(P/1e-7).astype(np.int64)
    if rband is not None:
        r = np.hypot(P[:,0],P[:,1]); m=(r>=rband[0])&(r<=rband[1])
        key = key[m]
    from collections import Counter
    c = Counter(map(tuple, key))
    dups = sum(v-1 for v in c.values() if v>1)
    return dups, len(key)

for ns in (4, 1):
    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0)
    mesh, cell_tags, classify = build_mesh_from_polygons(
        polys, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3,
        motion_band=True, band_thickness_mm=0.4, n_sectors=ns, geo_cfg=motor.parameters)
    P = mesh.p.T   # (n,2) metres
    dtot, ntot = dup_count(P)
    dmid, nmid = dup_count(P, rband=(mid_r-0.001, mid_r+0.001))   # +/-1mm around mid_r
    dgap, ngap = dup_count(P, rband=(p.r_rotor_out-1e-4, p.r_stator_in+1e-4))
    print(" n_sectors=%d : nodes=%d  dup_total=%d  dup@mid_r(+/-1mm)=%d/%d  dup@gap=%d/%d"
          % (ns, P.shape[0], dtot, dmid, nmid, dgap, ngap), flush=True)
