"""Side-by-side PINN vs FEM (Windows). Real FEM field via fem_solve_for_sim
(the path the web uses). Shows |B| both ways + the air-gap torque integrand
B_r·B_φ + a capability table → why the PINN torque is low and what's missing.
Writes pinn_vs_fem.png."""
import os, sys, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
from scipy.interpolate import griddata

ROOT = os.path.dirname(__file__); MMc = 1000.0
P = np.load(os.path.join(ROOT, "pinn_bgrid.npz"))
Pb, PX, PY = P["B"], P["X"] * MMc, P["Y"] * MMc
rg = float(P["rg"]); th = P["th"]; P_Br, P_Bphi = P["B_r"], P["B_phi"]
R = float(P["R"]); r_shaft = float(P["r_shaft"])

# ── REAL FEM solve (same path as the web) ────────────────────────────────────
from motor_ai_sim.simulation.fem_solver_2d import fem_solve_for_sim
fem = fem_solve_for_sim(rotor_angle_deg=0.0, gamma_deg=0.0, mesh_size_mm=2.5,
                        min_size_mm=0.35, n_sectors=4)
V = np.array(fem["vertices"]); T = np.array(fem["triangles"])
Bt = np.array(fem["Bmag_per_tri"]); A = np.array(fem["A_z_per_node"])
T_fem = float(fem.get("T_em_Nm", 24.87))
vx, vy = V[:, 0], V[:, 1]
# B per triangle from linear A_z gradient → centroids
x1, x2, x3 = vx[T[:, 0]], vx[T[:, 1]], vx[T[:, 2]]
y1, y2, y3 = vy[T[:, 0]], vy[T[:, 1]], vy[T[:, 2]]
a1, a2, a3 = A[T[:, 0]], A[T[:, 1]], A[T[:, 2]]
det = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
det = np.where(np.abs(det) < 1e-18, 1e-18, det)
dAdx = ((y2 - y3) * (a1 - a3) + (y3 - y1) * (a2 - a3)) / det
dAdy = ((x3 - x2) * (a1 - a3) + (x1 - x3) * (a2 - a3)) / det
Bx_t, By_t = dAdy, -dAdx
cx = (x1 + x2 + x3) / 3.0; cy = (y1 + y2 + y3) / 3.0
gx, gy = rg * np.cos(th), rg * np.sin(th)
F_Bx = griddata((cx, cy), Bx_t, (gx, gy), method="linear", fill_value=0.0)
F_By = griddata((cx, cy), By_t, (gx, gy), method="linear", fill_value=0.0)
F_Br = F_Bx * np.cos(th) + F_By * np.sin(th)
F_Bphi = -F_Bx * np.sin(th) + F_By * np.cos(th)

def mdisk(X, Y):
    rr = np.sqrt((X / MMc) ** 2 + (Y / MMc) ** 2); return (rr > R) | (rr < r_shaft)
Pbm = np.ma.masked_where(mdisk(PX, PY), Pb)
VMAX = 2.0
tri = mtri.Triangulation(vx * MMc, vy * MMc, T)

fig, ax = plt.subplots(2, 3, figsize=(17, 10.6), facecolor="#0f172a")
def style(a):
    a.set_facecolor("#0f172a"); a.tick_params(colors="#94a3b8", labelsize=7)
    [s.set_color("#334155") for s in a.spines.values()]

im = ax[0, 0].contourf(PX, PY, Pbm, np.linspace(0, VMAX, 41), cmap="jet", extend="max")
ax[0, 0].set_title(f"PINN |B|  (max {Pbm.max():.2f} T) — LINEAR", color="#4ade80", fontsize=11)
ax[0, 0].set_aspect("equal"); style(ax[0, 0]); plt.colorbar(im, ax=ax[0, 0], fraction=0.046)

tp = ax[0, 1].tripcolor(tri, facecolors=np.clip(Bt, 0, VMAX), cmap="jet", vmin=0, vmax=VMAX)
ax[0, 1].set_title(f"FEM |B|  (max {Bt.max():.2f} T) — BH/saturation", color="#60a5fa", fontsize=11)
ax[0, 1].set_aspect("equal"); ax[0, 1].set_xlim(-R*MMc, R*MMc); ax[0, 1].set_ylim(-R*MMc, R*MMc)
style(ax[0, 1]); plt.colorbar(tp, ax=ax[0, 1], fraction=0.046)

ax[0, 2].axis("off")
rows = [("physics", "FEM", "PINN"), ("magnetostatics A_z", "yes", "yes"),
        ("real geometry", "yes", "yes"), ("magnet M", "yes", "yes (=FEM)"),
        ("BH / saturation", "YES", "NO"), ("demagnetisation", "YES", "NO"),
        ("iron loss (f, lam.)", "YES", "NO"), ("copper/magnet loss", "YES", "NO"),
        (f"|B|max [T]", f"{Bt.max():.2f}", f"{Pbm.max():.2f}"),
        ("torque [N·m]", f"{T_fem:.1f}", "2.68")]
tb = ax[0, 2].table(cellText=rows, loc="center", cellLoc="center")
tb.auto_set_font_size(False); tb.set_fontsize(10); tb.scale(1, 1.7)
for (r, c), cell in tb.get_celld().items():
    cell.set_edgecolor("#334155")
    if r == 0:
        cell.set_facecolor("#1e293b"); cell.set_text_props(color="#e2e8f0", weight="bold")
    else:
        miss = rows[r][2].upper() == "NO"
        cell.set_facecolor("#3a1010" if miss else "#0f172a")
        cell.set_text_props(color="#fca5a5" if miss else "#cbd5e1")
ax[0, 2].set_title("What each solver models", color="#e2e8f0", fontsize=11)

deg = np.degrees(th)
for a, yp, yf, t in ((ax[1, 0], P_Br, F_Br, "Air-gap B_r (radial)"),
                     (ax[1, 1], P_Bphi, F_Bphi, "Air-gap B_φ (tangential)"),
                     (ax[1, 2], P_Br * P_Bphi, F_Br * F_Bphi, "B_r·B_φ  (torque integrand)")):
    a.plot(deg, yf, color="#60a5fa", lw=1.2, label="FEM")
    a.plot(deg, yp, color="#4ade80", lw=1.2, label="PINN")
    a.set_title(t, color="#e2e8f0", fontsize=11); a.set_xlim(0, 360)
    a.set_xlabel("φ [deg]", color="#94a3b8", fontsize=8); a.legend(fontsize=8); a.grid(alpha=0.25); style(a)

fig.suptitle("Modulus PINN  vs  FEM  —  same geometry; PINN is LINEAR (no BH / saturation / demag / loss)",
             color="#f1f5f9", fontsize=13)
fig.tight_layout()
fig.savefig(os.path.join(ROOT, "pinn_vs_fem.png"), dpi=85, facecolor="#0f172a")
print("saved pinn_vs_fem.png")
print(f"PINN |B|max={Pbm.max():.2f} torque~2.68   FEM |B|max={Bt.max():.2f} torque={T_fem:.2f}")
print(f"B_phi p-p:  PINN={P_Bphi.max()-P_Bphi.min():.3f}  FEM={F_Bphi.max()-F_Bphi.min():.3f}")
