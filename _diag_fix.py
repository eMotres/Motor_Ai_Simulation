"""Validate the proposed fix: for n_sectors=1, drop the closed in_band/out_band
slip-ring and use the legacy CONTINUOUS background-air path (motion_band off).
Compare gap|B| + shaft tris + total tris across:
  (A) n_sectors=4  in/out-band   (REFERENCE, correct)
  (B) n_sectors=1  in/out-band   (current, BROKEN)
  (C) n_sectors=1  legacy air    (PROPOSED FIX)
Open circuit, demag OFF (isolate the field). Pure numeric."""
import math, dataclasses, numpy as np
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, build_materials, _simplify_polys,
    solve_magnetostatics, _per_triangle_B,
    DOM_MAG_BASE, DOM_SHAFT)
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
from motor_ai_sim.cadquery_geometry import CadQueryMotor

p = params_from_config(); d = MotorDomains2D(p)
r_ro, r_si = p.r_rotor_out, p.r_stator_in
slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor


def solve(ns, legacy):
    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0)
    if legacy:                       # drop closed slip-ring → legacy continuous air
        polys = dict(polys)
        for k in ("in_band", "out_band"):
            polys.pop(k, None)
    mesh, ct, cf = build_mesh_from_polygons(
        polys, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3,
        motion_band=(not legacy), band_thickness_mm=0.4, n_sectors=ns,
        geo_cfg=motor.parameters)
    ct = ct.astype(np.int32)
    pm = getattr(cf, "polys", polys)
    mats = build_materials({"A": 0., "B": 0., "C": 0.}, d.winding_layout,
                           pm, 0.0, slot_area, 15)
    for t in list(mats):             # demag OFF
        if t >= DOM_MAG_BASE:
            try: mats[t].bh_curve = None
            except Exception: mats[t] = dataclasses.replace(mats[t], bh_curve=None)
    poles_per = p.num_poles // max(ns, 1); anti = (poles_per % 2 == 1)
    A = solve_magnetostatics(mesh, ct, mats, n_sectors=ns,
                             pole_pairs_per_sector_is_half_integer=anti)
    Bx, By = _per_triangle_B(mesh, A); Bm = np.hypot(Bx, By)
    c = mesh.p[:, mesh.t].mean(axis=1); rc = np.hypot(c[0], c[1])
    gap = float(Bm[(rc >= r_ro) & (rc <= r_si)].mean())
    shaft = int((ct == DOM_SHAFT).sum())
    return gap, shaft, ct.size


for label, ns, legacy in [("A) ns=4  in/out-band (REF) ", 4, False),
                          ("B) ns=1  in/out-band (NOW) ", 1, False),
                          ("C) ns=1  legacy air  (FIX) ", 1, True)]:
    try:
        gap, shaft, ntri = solve(ns, legacy)
        print("%s  gap|B|=%.3f T   shaft_tris=%d   total_tris=%d" % (label, gap, shaft, ntri), flush=True)
    except Exception as e:
        print("%s  ERROR: %r" % (label, e), flush=True)
