"""Test the FULL sliding-band mesh (rotor half + stator half) with structured_gap,
measuring gap ring counts across the WHOLE gap (r_ro..r_si) and the mid slip ring."""
import math, numpy as np
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.simulation import fem_solver_2d as F

def gap_levels(P, r_ro, r_si):
    r = np.hypot(P[0], P[1])
    gap = r[(r > r_ro*1e-3 - 1e-6) & (r < r_si*1e-3 + 1e-6)] * 1e3  # to mm
    lv = []
    for v in np.sort(np.unique(np.round(gap, 4))):
        if not lv or v - lv[-1] > 1e-3:
            lv.append(round(float(v), 4))
    return lv

def mid_ring_check(P, mid_mm, n_slip):
    r = np.hypot(P[0], P[1]) * 1e3
    ang = np.degrees(np.arctan2(P[1], P[0])) % 360.0
    m = np.abs(r - mid_mm) < 1e-3
    a = ang[m]
    step = 360.0 / n_slip
    kg = np.round(a / step)
    on = np.abs(a - kg*step) < 0.05*step
    return int(m.sum()), int(on.sum())

def run(structured, gap_layers, full_ring=True, n_sectors=1):
    m = CadQueryMotor()
    p = m.parameters
    r_ro = float(p['rotor_outer_radius']); r_si = float(p['stator_inner_radius'])
    mid = 0.5*(r_ro+r_si)
    pp = int(p['num_poles'])//2
    _slip_base = int(round(1008.0*(max(1.0,float(gap_layers))+2.0)/3.0))
    _spp = 24*max(5, math.ceil(_slip_base/(24*pp)))
    n_slip_eff = pp*_spp
    polys = m.get_2d_polygons(rotor_angle_deg=0.0)
    # structured_gap → merged (route A owns r_ro->mid / mid->r_si w/ shared mid ring)
    _bm = "merged" if structured else ("moving" if full_ring else "merged")
    polys = F._simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0,
                              n_slip=n_slip_eff, gap_layers=gap_layers,
                              structured_gap=structured,
                              band_mode=_bm)
    ms, ts, cs, mr, tr, cr = F._build_sliding_band_meshes(
        polys, 0.0, 3.0, min_size_mm=0.3, outer_air_factor=1.3,
        band_thickness_mm=0.4, n_sectors=(1 if full_ring else n_sectors),
        geo_cfg=m.parameters, normal_deviation_deg=8.0, aspect_ratio=10.0,
        gap_layers=gap_layers, component_mesh_mm={}, full_ring=full_ring,
        pole_copy=False)
    # combined
    Pall = np.hstack([ms.p, mr.p])
    lv = gap_levels(Pall, r_ro, r_si)
    # mid ring on the ROTOR half (that's what _ring uses at mid)
    n_mid, n_on = mid_ring_check(mr.p, mid, n_slip_eff)
    n_mid_s, n_on_s = mid_ring_check(ms.p, mid, n_slip_eff)
    print(f"structured={structured} gap_layers={gap_layers} full_ring={full_ring}: "
          f"GAP LEVELS={len(lv)} {lv}")
    print(f"    n_slip_eff={n_slip_eff}  rotor mid ring: {n_mid} nodes ({n_on} on grid); "
          f"stator mid ring: {n_mid_s} ({n_on_s} on grid)")
    print(f"    tris: stator={ms.t.shape[1]} rotor={mr.t.shape[1]}")
    return len(lv)

if __name__=="__main__":
    print("### FREE MODE (baseline) ###")
    run(False, 3)
    print("\n### STRUCTURED, full_ring (production default) ###")
    for K in (1,2,3):
        run(True, K)
    print("\n### STRUCTURED, sector n_sectors=4 (viewer/alt path) ###")
    for K in (1,2,3):
        run(True, K, full_ring=False, n_sectors=4)
