"""Dump the per-magnet M source for n_sectors=4 vs n_sectors=1.

Replicates fem_solve_for_sim's mesh+materials build, then for each magnet tag
prints centroid angle, polarity, |M|, and the angle of M.  Also assembles the
magnetisation RHS vector and reports its norm.  If the full-disk magnet sources
are weaker / mis-signed / cancelling, it shows up here directly."""
import math, numpy as np
import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, build_materials, _simplify_polys, DOM_MAG_BASE, MU0)
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D

p = params_from_config()
d = MotorDomains2D(p)
slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
n_wires = 15

for ns in (4, 1):
    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=0.0)
    mesh, cell_tags, classify_fn = build_mesh_from_polygons(
        polys, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3,
        motion_band=True, band_thickness_mm=0.4, n_sectors=ns,
        geo_cfg=motor.parameters)
    polys_meshed = getattr(classify_fn, "polys", polys)
    I_ph = {'A':0.0,'B':0.0,'C':0.0}
    mats = build_materials(I_ph, d.winding_layout, polys_meshed, 0.0, slot_area, n_wires)
    mag_tags = sorted([t for t in mats if t >= DOM_MAG_BASE])
    # per-magnet area from mesh
    tri_area = None
    from skfem import MeshTri  # noqa
    rows = []
    Mtot = 0.0
    for t in mag_tags:
        m = mats[t]
        idx = np.where(cell_tags == t)[0]
        # triangle areas for this tag
        P = mesh.p; T = mesh.t[:, idx]
        x1,x2,x3 = P[0,T[0]],P[0,T[1]],P[0,T[2]]; y1,y2,y3=P[1,T[0]],P[1,T[1]],P[1,T[2]]
        area = np.abs((x2-x1)*(y3-y1)-(x3-x1)*(y2-y1))/2.0
        Atag = float(area.sum())
        Mmag = math.hypot(m.Mx, m.My)
        Mang = math.degrees(math.atan2(m.My, m.Mx))
        # magnet centroid angle
        cidx = mesh.p[:, np.unique(T)]
        cang = math.degrees(math.atan2(cidx[1].mean(), cidx[0].mean()))
        rows.append((t-DOM_MAG_BASE, round(cang,1), round(Mang,1), round(Mmag/1e6,3), round(Atag*1e6,1)))
        Mtot += Mmag*Atag
    print("\n=== n_sectors=%d : %d magnets, ntris=%d, sum|M|*area=%.4e ==="
          % (ns, len(mag_tags), mesh.t.shape[1], Mtot))
    print(" idx  centroid°  M_dir°   |M|MA/m  area_mm2")
    for r in rows[:10]:
        print("  %2d   %7.1f  %7.1f  %7.3f  %7.1f" % r)
