"""DEBUG: zoomed flux lines (A_z contours) + |B| over rotor+gap+stator for a
few poles, ns=4 vs ns=1, converged open-circuit solve. See where full-disk flux goes."""
import math, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, build_materials, _simplify_polys,
    solve_magnetostatics, _per_triangle_B, DOM_MAG_BASE, DOM_ROTOR, DOM_STATOR, DOM_SHAFT)
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
from motor_ai_sim.cadquery_geometry import CadQueryMotor

p = params_from_config(); d = MotorDomains2D(p)
sa = p.slot_width_m * p.slot_height_m * p.fill_factor

fig, axes = plt.subplots(1, 2, figsize=(17, 9))
for ax, ns in zip(axes, (4, 1)):
    m = CadQueryMotor(); po = m.get_2d_polygons(rotor_angle_deg=0.0)
    po = _simplify_polys(po, tol_mm=0.005, stator_fillet_mm=0.0)
    mesh, ct, cf = build_mesh_from_polygons(
        po, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3,
        motion_band=True, band_thickness_mm=0.4, n_sectors=ns, geo_cfg=m.parameters)
    ct = ct.astype(np.int32); pm = getattr(cf, "polys", po)
    mats = build_materials({"A": 0., "B": 0., "C": 0.}, d.winding_layout, pm, 0.0, sa, 15)
    pp = p.num_poles // max(ns, 1); anti = (pp % 2 == 1)
    A = solve_magnetostatics(mesh, ct, mats, n_sectors=ns,
                             pole_pairs_per_sector_is_half_integer=anti)
    Bx, By = _per_triangle_B(mesh, A); Bm = np.hypot(Bx, By)
    X = mesh.p[0] * 1000; Y = mesh.p[1] * 1000
    tri = mtri.Triangulation(X, Y, mesh.t.T)
    # color |B| (clip 2T), shade nothing; overlay flux lines
    tpc = ax.tripcolor(tri, facecolors=np.clip(Bm, 0, 2.0), cmap="inferno", vmin=0, vmax=2.0)
    try:
        lv = np.linspace(A.min(), A.max(), 60)
        ax.tricontour(tri, A, levels=lv, colors="cyan", linewidths=0.35, alpha=0.6)
    except Exception:
        pass
    # zoom: 50..105 mm radius, angular 60..120 deg (top region, ~4 poles)
    a0, a1 = 60, 120
    xs = [r * math.cos(math.radians(a)) for r in (48, 105) for a in (a0, a1)]
    ys = [r * math.sin(math.radians(a)) for r in (48, 105) for a in (a0, a1)]
    ax.set_xlim(min(xs) - 5, max(xs) + 5); ax.set_ylim(min(ys) - 2, max(ys) + 8)
    ax.set_aspect("equal")
    ax.set_title("n_sectors=%d  (|B| 0-2T, flux lines cyan)" % ns, fontsize=12)
    plt.colorbar(tpc, ax=ax, fraction=0.04, label="|B| (T)")

fig.suptitle("Zoom rotor+gap+stator (top ~4 poles): WHERE does full-disk flux go?", fontsize=13, weight="bold")
fig.tight_layout()
out = r"C:\Users\vadim\Projects\motor_ai_sim\_flux_zoom.png"
fig.savefig(out, dpi=120)
print("SAVED", out, flush=True)
