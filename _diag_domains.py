"""Per-domain mean|B| (open circuit, demag OFF): localize WHERE the full-disk
flux is lost vs the sector. Pure numeric."""
import math, dataclasses, numpy as np
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, build_materials, _simplify_polys,
    solve_magnetostatics, _per_triangle_B,
    DOM_MAG_BASE, DOM_ROTOR, DOM_STATOR, DOM_SHAFT, DOM_AIRGAP, DOM_OUTER)
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
from motor_ai_sim.cadquery_geometry import CadQueryMotor

p = params_from_config(); d = MotorDomains2D(p)
slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor


def meanB_by(ct, Bm, tagset):
    idx = np.isin(ct, list(tagset))
    return float(Bm[idx].mean()) if idx.any() else 0.0, int(idx.sum())


for ns in (4, 1):
    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0)
    mesh, ct, cf = build_mesh_from_polygons(
        polys, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3,
        motion_band=True, band_thickness_mm=0.4, n_sectors=ns,
        geo_cfg=motor.parameters)
    ct = ct.astype(np.int32)
    pm = getattr(cf, "polys", polys)
    mats = build_materials({"A": 0., "B": 0., "C": 0.}, d.winding_layout,
                           pm, 0.0, slot_area, 15)
    for t in list(mats):            # demag OFF
        if t >= DOM_MAG_BASE:
            try: mats[t].bh_curve = None
            except Exception: mats[t] = dataclasses.replace(mats[t], bh_curve=None)
    poles_per = p.num_poles // max(ns, 1); anti = (poles_per % 2 == 1)
    A = solve_magnetostatics(mesh, ct, mats, n_sectors=ns,
                             pole_pairs_per_sector_is_half_integer=anti)
    Bx, By = _per_triangle_B(mesh, A); Bm = np.hypot(Bx, By)
    magtags = set(int(t) for t in np.unique(ct) if t >= DOM_MAG_BASE)
    print("=== n_sectors=%d  (total tris=%d) ===" % (ns, ct.size), flush=True)
    for name, tags in [("magnets", magtags), ("rotor", {DOM_ROTOR}),
                       ("stator", {DOM_STATOR}), ("shaft", {DOM_SHAFT}),
                       ("airgap", {DOM_AIRGAP}), ("outer", {DOM_OUTER})]:
        mb, n = meanB_by(ct, Bm, tags)
        print("    %-8s mean|B|=%.3f T   (%d tris)" % (name, mb, n), flush=True)
