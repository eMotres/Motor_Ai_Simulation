"""Run the eddy-current solve and visualise its fields: A_z, |B|, and the
eddy current density J in the copper (the new field the eddy solve produces)."""
import time
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.tri import Triangulation
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band as F

t0 = time.time()
d = F(n_steps_per_period=12, n_periods=2.0, I_phase_rms=120.0, n_sectors=4,
      coil_temp_c=120.0, eddy=True, return_field=True)
fld = d["field"]
print("solve %.0fs  T=%.1f  Cu_total(solve)=%.0fW  Cu_AC(solve)=%.0fW"
      % (time.time() - t0, d["T_avg_Nm"], d["P_cu_total_solve_W"], d["P_cu_ac_solve_W"]))

P = fld["P_mm"]; T = fld["T"]; A = fld["A"]
Bx = fld["Bx"]; By = fld["By"]; J = fld["Jeddy"]; tags = fld["tags"]
x, y = P[0], P[1]
tri = Triangulation(x, y, T.T)
Bmag = np.sqrt(Bx**2 + By**2)

# copper element mask (per-element) for the J panel framing
DOM_COIL = 2; DOM_COIL_BASE = 200
cu_elem = (tags == DOM_COIL) | (tags >= DOM_COIL_BASE)

fig, ax = plt.subplots(1, 3, figsize=(21, 7))

# 1) A_z (nodal)
tpc = ax[0].tripcolor(tri, A, shading="gouraud", cmap="RdBu_r")
ax[0].tricontour(tri, A, levels=22, colors="k", linewidths=0.3, alpha=0.5)
fig.colorbar(tpc, ax=ax[0], shrink=0.8, label="A_z [Wb/m]")
ax[0].set_title("A_z — vector potential (flux lines)")

# 2) |B| (per-element)
tpc = ax[1].tripcolor(tri, facecolors=np.clip(Bmag, 0, 2.2), cmap="inferno")
fig.colorbar(tpc, ax=ax[1], shrink=0.8, label="|B| [T]")
ax[1].set_title("|B| — flux density (clipped 2.2 T)")

# 3) Eddy current density J in copper (nodal, signed)
jlim = np.percentile(np.abs(J[J != 0]), 99) if np.any(J != 0) else 1.0
tpc = ax[2].tripcolor(tri, J, shading="gouraud", cmap="seismic",
                      vmin=-jlim, vmax=jlim)
fig.colorbar(tpc, ax=ax[2], shrink=0.8, label="J_eddy [A/m²]")
ax[2].set_title("J = σ(−∂A/∂t + U)  eddy current density in Cu")

for a in ax:
    a.set_aspect("equal"); a.set_xlabel("x [mm]"); a.set_ylabel("y [mm]")

plt.tight_layout()
plt.savefig("eddy_field_view.png", dpi=110, bbox_inches="tight")
print("saved -> eddy_field_view.png")

# extra: zoom on the slot region to see the per-wire eddy pattern
cu_nodes = np.unique(T[:, cu_elem]) if cu_elem.any() else np.array([], int)
if cu_nodes.size:
    cx, cy = x[cu_nodes], y[cu_nodes]
    fig2, ax2 = plt.subplots(figsize=(9, 9))
    tpc = ax2.tripcolor(tri, J, shading="gouraud", cmap="seismic", vmin=-jlim, vmax=jlim)
    fig2.colorbar(tpc, ax=ax2, label="J_eddy [A/m²]")
    # zoom to a couple of slots near the gap
    rmid = np.hypot(cx, cy)
    ax2.set_xlim(cx.min(), cx.min() + 0.45 * (cx.max() - cx.min()))
    ax2.set_ylim(cy.max() - 0.45 * (cy.max() - cy.min()), cy.max())
    ax2.set_aspect("equal"); ax2.set_title("J_eddy — zoom on wires");
    ax2.set_xlabel("x [mm]"); ax2.set_ylabel("y [mm]")
    plt.tight_layout(); plt.savefig("eddy_field_zoom.png", dpi=120, bbox_inches="tight")
    print("saved -> eddy_field_zoom.png")
