"""Compare n_sectors=4 (sector) vs 1 (full disk): outer Dirichlet boundary
radius, presence of far-field air, and per-magnet magnetization polarity
pattern by angle. Pure numeric diagnostic (no plots)."""
import math, numpy as np
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, build_materials, _simplify_polys,
    DOM_MAG_BASE, _outer_boundary_nodes)
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
from motor_ai_sim.cadquery_geometry import CadQueryMotor

p = params_from_config(); d = MotorDomains2D(p)
slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
print("r_stator_out=%.2f  r_stator_in=%.2f  r_rotor_out=%.2f mm"
      % (p.r_stator_out * 1000, p.r_stator_in * 1000, p.r_rotor_out * 1000))

for ns in (4, 1):
    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0)
    mesh, ct, cf = build_mesh_from_polygons(
        polys, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3,
        motion_band=True, band_thickness_mm=0.4, n_sectors=ns,
        geo_cfg=motor.parameters)
    pm = getattr(cf, "polys", polys)
    mats = build_materials({"A": 0., "B": 0., "C": 0.}, d.winding_layout,
                           pm, 0.0, slot_area, 15)
    rv = np.hypot(mesh.p[0], mesh.p[1]); ob = _outer_boundary_nodes(mesh)
    magtags = sorted(t for t in mats if t >= DOM_MAG_BASE)
    mlist = pm.get("magnets", [])
    print("--- n_sectors=%d ---" % ns)
    print("  mesh r_max=%.2f mm   outer-BC r=[%.2f..%.2f] mm  n=%d"
          % (rv.max() * 1000, rv[ob].min() * 1000, rv[ob].max() * 1000, len(ob)))
    print("  far-field air_outer present: %s   air_gap present: %s   #magnets(polys)=%d  #magnet-tags=%d"
          % ("air_outer" in pm, "air_gap" in pm, len(mlist), len(magtags)))
    rows = []
    for t in magtags:
        i = t - DOM_MAG_BASE
        if i >= len(mlist):
            continue
        m = mats[t]; mag = mlist[i][0]
        if mag is None or mag.is_empty:
            continue
        cx, cy = mag.centroid.x, mag.centroid.y; cr = math.hypot(cx, cy)
        if cr < 1e-9:
            continue
        ang = math.degrees(math.atan2(cy, cx)) % 360
        tx, ty = -cy / cr, cx / cr
        s = (m.Mx * tx + m.My * ty) / (math.hypot(m.Mx, m.My) + 1e-30)
        rows.append((ang, i, s))
    rows.sort()
    patt = "".join("+" if s > 0 else "-" for _, _, s in rows)
    print("  polarity (M . CCW-tangent) sorted by angle:  %s" % patt)
