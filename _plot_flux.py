"""DEBUG (internal): flux-line plot (A_z iso-contours) for ns=4 vs ns=1,
open circuit, converged solve. Reveals WHERE the full-disk flux goes."""
import math, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, build_materials, _simplify_polys,
    solve_magnetostatics, _per_triangle_B, DOM_MAG_BASE, DOM_ROTOR, DOM_STATOR)
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
from motor_ai_sim.cadquery_geometry import CadQueryMotor

p = params_from_config(); d = MotorDomains2D(p)
r_ro, r_si = p.r_rotor_out, p.r_stator_in
sa = p.slot_width_m * p.slot_height_m * p.fill_factor

fig, axes = plt.subplots(1, 2, figsize=(16, 8))
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
    cc = mesh.p[:, mesh.t].mean(axis=1); rc = np.hypot(cc[0], cc[1])
    gap = float(Bm[(rc >= r_ro) & (rc <= r_si)].mean())
    X = mesh.p[0] * 1000; Y = mesh.p[1] * 1000
    tri = mtri.Triangulation(X, Y, mesh.t.T)
    # flux lines = A_z iso-contours
    lv = np.linspace(A.min(), A.max(), 40)
    ax.tricontour(tri, A, levels=lv, colors="k", linewidths=0.4)
    # shade iron
    irontri = np.isin(ct, [DOM_ROTOR, DOM_STATOR])
    ax.tripcolor(tri, facecolors=np.where(irontri, 1.0, 0.0),
                 cmap="Blues", alpha=0.25, vmin=0, vmax=1)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title("n_sectors=%d   gap|B|=%.3f T   A_z range=[%.2f, %.2f] mWb/m"
                 % (ns, gap, A.min() * 1000, A.max() * 1000), fontsize=11)

fig.suptitle("Flux lines (A_z iso-contours), open circuit — sector vs full disk",
             fontsize=13, weight="bold")
fig.tight_layout()
out = r"C:\Users\vadim\Projects\motor_ai_sim\_flux_compare.png"
fig.savefig(out, dpi=95)
print("SAVED", out, flush=True)
