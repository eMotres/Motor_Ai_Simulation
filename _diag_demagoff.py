"""Decisive test: does disabling demag make full disk (1) match sector (4)?
Strips magnet BH-curve (→ demag check skipped) and compares gap|B| at open
circuit. Pure numeric (no plots)."""
import math, dataclasses, numpy as np
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, build_materials, _simplify_polys,
    solve_magnetostatics, _per_triangle_B, DOM_MAG_BASE)
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
from motor_ai_sim.cadquery_geometry import CadQueryMotor

p = params_from_config(); d = MotorDomains2D(p)
r_ro, r_si = p.r_rotor_out, p.r_stator_in
slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor


def gap_stats(mesh, A):
    Bx, By = _per_triangle_B(mesh, A); Bm = np.hypot(Bx, By)
    c = mesh.p[:, mesh.t].mean(axis=1); rc = np.hypot(c[0], c[1])
    m = (rc >= r_ro) & (rc <= r_si)
    return float(Bm[m].mean()), float(Bm.max())


def strip_demag(mats):
    for t in list(mats):
        if t >= DOM_MAG_BASE:
            try:
                mats[t].bh_curve = None
            except Exception:
                mats[t] = dataclasses.replace(mats[t], bh_curve=None)
    return mats


for demag in (True, False):
    print("=== demag %s ===" % ("ON" if demag else "OFF"), flush=True)
    res = {}
    for ns in (4, 1):
        motor = CadQueryMotor()
        polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
        polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0)
        mesh, ct, cf = build_mesh_from_polygons(
            polys, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3,
            motion_band=True, band_thickness_mm=0.4, n_sectors=ns,
            geo_cfg=motor.parameters)
        ct = ct.astype(np.int16)
        pm = getattr(cf, "polys", polys)
        mats = build_materials({"A": 0., "B": 0., "C": 0.}, d.winding_layout,
                               pm, 0.0, slot_area, 15)
        if not demag:
            mats = strip_demag(mats)
        poles_per = p.num_poles // max(ns, 1); anti = (poles_per % 2 == 1)
        A = solve_magnetostatics(mesh, ct, mats, n_sectors=ns,
                                 pole_pairs_per_sector_is_half_integer=anti)
        gmean, gmax = gap_stats(mesh, A)
        res[ns] = gmean
        print("  n_sectors=%d: gap|B|_mean=%.3f T   |B|_max=%.1f T" % (ns, gmean, gmax), flush=True)
    ratio = res[1] / res[4] if res[4] else 0.0
    print("  --> full/sector gap|B| ratio = %.2f  (1.00 = MATCH)" % ratio, flush=True)
