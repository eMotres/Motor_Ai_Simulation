"""Side-by-side |B| field: sector(4) vs full(1), open circuit (magnets only).
Produces _field_compare.png for the user AND prints a mesh-weld diagnostic
(coincident node count on the mid_r slip circle)."""
import math, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
from scipy.spatial import cKDTree
import motor_ai_sim.simulation.fem_solver_2d as fs
from motor_ai_sim.simulation.geometry_2d import params_from_config
from motor_ai_sim.cadquery_geometry import CadQueryMotor

p = params_from_config()
r_ro, r_si = p.r_rotor_out, p.r_stator_in
motor = CadQueryMotor()
polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
mid_r = float(polys.get("mid_r_mm", (r_ro + r_si) * 500.0)) / 1000.0
print(f"GEOM: r_rotor_out={r_ro*1000:.2f}mm  r_stator_in={r_si*1000:.2f}mm  "
      f"mid_r={mid_r*1000:.2f}mm  gap={(r_si-r_ro)*1000:.2f}mm", flush=True)


def tri_B(V, T, A):
    x = V[:, 0]; y = V[:, 1]
    x1, x2, x3 = x[T[:, 0]], x[T[:, 1]], x[T[:, 2]]
    y1, y2, y3 = y[T[:, 0]], y[T[:, 1]], y[T[:, 2]]
    a1, a2, a3 = A[T[:, 0]], A[T[:, 1]], A[T[:, 2]]
    det = (x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)
    det = np.where(np.abs(det) < 1e-30, 1e-30, det)
    Bx = ((x2 - x1) * (a3 - a1) - (x3 - x1) * (a2 - a1)) / det
    By = -((y3 - y1) * (a2 - a1) - (y2 - y1) * (a3 - a1)) / det
    return Bx, By


fig, axes = plt.subplots(1, 2, figsize=(15, 7.5))
for ax, (label, ns) in zip(axes, [("Sector  n_sectors=4  (REFERENCE)", 4),
                                   ("Full disk  n_sectors=1", 1)]):
    r = fs.fem_solve_for_sim(rotor_angle_deg=0.0, gamma_deg=0.0,
                             mesh_size_mm=4.0, n_sectors=ns, I_phase_rms=0.0)
    V = np.asarray(r["vertices"]); T = np.asarray(r["triangles"])
    A = np.asarray(r["A_z_per_node"])
    Bx, By = tri_B(V, T, A); Bmag = np.hypot(Bx, By)
    cx = V[T].mean(axis=1)[:, 0]; cy = V[T].mean(axis=1)[:, 1]
    rc = np.hypot(cx, cy)
    gapB = float(Bmag[(rc >= r_ro) & (rc <= r_si)].mean())
    # coincident-node diagnostic on the mid_r slip circle
    rv = np.hypot(V[:, 0], V[:, 1])
    band = V[np.abs(rv - mid_r) < 1.5e-3]
    ndup = 0
    if len(band) > 1:
        ndup = len(cKDTree(band).query_pairs(r=1e-7))
    print(f"{label:35s}: GAP|B|={gapB:.3f}T  T={r['T_em_Nm']:+7.2f} N·m  "
          f"nodes_on_mid_r={len(band):4d}  coincident_pairs={ndup}", flush=True)

    triang = mtri.Triangulation(V[:, 0] * 1000, V[:, 1] * 1000, T)
    tpc = ax.tripcolor(triang, facecolors=np.clip(Bmag, 0, 2.0),
                       cmap="inferno", vmin=0, vmax=2.0, shading="flat")
    th = np.linspace(0, 2 * np.pi, 240)
    ax.plot(mid_r * 1000 * np.cos(th), mid_r * 1000 * np.sin(th),
            "c--", lw=0.8, alpha=0.7)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title(f"{label}\nGAP |B| = {gapB:.3f} T     T = {r['T_em_Nm']:+.2f} N·m",
                 fontsize=11)
    plt.colorbar(tpc, ax=ax, fraction=0.046, pad=0.02, label="|B| (T)")

fig.suptitle("Open circuit (magnets only) — both panels SHOULD be identical",
             fontsize=13, weight="bold")
fig.tight_layout()
out = r"C:\Users\vadim\Projects\motor_ai_sim\_field_compare.png"
fig.savefig(out, dpi=110)
print("SAVED", out, flush=True)
