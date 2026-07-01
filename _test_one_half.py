"""Direct test: build_mesh_from_polygons on ONE half (rotor) at n_sectors=2,
structured_gap. Measure ring levels + mid ring. Logging ON to see cell counts."""
import math, numpy as np, logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
# silence noisy libs
for n in ("matplotlib","PIL","numba","shapely"):
    logging.getLogger(n).setLevel(logging.WARNING)
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.simulation import fem_solver_2d as F

def one_half(gl, n_sectors, which="rotor"):
    m=CadQueryMotor(); p=m.parameters
    r_ro=float(p['rotor_outer_radius']); r_si=float(p['stator_inner_radius']); mid=0.5*(r_ro+r_si)
    pp=int(p['num_poles'])//2
    _sb=int(round(1008.0*(gl+2.0)/3.0)); _spp=24*max(5,math.ceil(_sb/(24*pp))); n_slip=pp*_spp
    polys=m.get_2d_polygons(rotor_angle_deg=0.0)
    polys=F._simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0, n_slip=n_slip,
                            gap_layers=gl, structured_gap=True, band_mode="merged")
    ps,pr=F._split_polys_for_sliding_band(polys)
    half = dict(pr if which=="rotor" else ps)
    if which=="rotor" and "in_band" in half: half["air_gap"]=half.pop("in_band")
    if which=="stator" and "out_band" in half: half["air_outer"]=half.pop("out_band")
    print(f"\n--- {which} half  gap_layers={gl}  n_sectors={n_sectors}  n_slip={n_slip} ---")
    mesh,tags,cf=F.build_mesh_from_polygons(
        half, n_sectors=n_sectors, mesh_size_mm=3.0, min_size_mm=0.3,
        normal_deviation_deg=8.0, aspect_ratio=10.0, outer_air_factor=1.3,
        gap_layers=gl, add_background_air=False, slip_transfinite_r=mid,
        geo_cfg=m.parameters)
    P=mesh.p
    r=np.hypot(P[0],P[1])*1e3
    lo = r_ro if which=="rotor" else mid
    hi = mid if which=="rotor" else r_si
    band=r[(r>lo-1e-3)&(r<hi+1e-3)]; lv=[]
    for v in np.sort(np.unique(np.round(band,4))):
        if not lv or v-lv[-1]>1e-3: lv.append(round(float(v),4))
    ang=np.degrees(np.arctan2(P[1],P[0]))%360.0
    mm=np.abs(r-mid)<1e-3; a=ang[mm]; step=360.0/n_slip; kg=np.round(a/step)
    on=np.abs(a-kg*step)<0.05*step
    print(f"  {which} band radial LEVELS={len(lv)} (want K+1={gl+1}): {lv}")
    print(f"  mid ring: {int(mm.sum())} nodes, {int(on.sum())} on global grid (want {n_slip//n_sectors}+1)")
    print(f"  tris={mesh.t.shape[1]}")
    return len(lv)

if __name__=="__main__":
    for gl in (1,2,3):
        one_half(gl, 2, "rotor")
    for gl in (1,2,3):
        one_half(gl, 2, "stator")
