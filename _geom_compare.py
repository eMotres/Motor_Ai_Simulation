"""Compare the geometry the Modulus PINN actually samples (MotorDomains2D —
simple annular sectors + rotated rectangles) against the REAL motor geometry the
FEM uses (CadQueryMotor.get_2d_polygons — trapezoid magnets, T-teeth, fillets).

Run on the Windows Python (has shapely + the geometry modules):
  python _geom_compare.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

MM = 1000.0  # PINN domains are in metres; real polys are in mm

# ── REAL geometry (CadQuery / Shapely, mm) ───────────────────────────────────
from motor_ai_sim.cadquery_geometry import CadQueryMotor
motor = CadQueryMotor()
polys = motor.get_2d_polygons(rotor_angle_deg=0.0)

# ── PINN geometry (MotorDomains2D, metres) ───────────────────────────────────
from motor_ai_sim.simulation.geometry_2d import MotorDomains2D, params_from_config
gp = params_from_config()
geo = MotorDomains2D(gp)

fig, (axL, axR) = plt.subplots(1, 2, figsize=(18, 9))


def scat(ax, dom, n, c, label=None):
    pts = dom.sample_interior(n)
    x = np.asarray(pts["x"]).ravel() * MM
    y = np.asarray(pts["y"]).ravel() * MM
    ax.scatter(x, y, s=3, c=c, label=label, linewidths=0)


# LEFT — what the PINN samples
scat(axL, geo["shaft"],       600, "#777", "shaft")
scat(axL, geo["rotor_core"],  600, "#444", "rotor iron")
scat(axL, geo["stator_core"], 1600, "#aaa", "stator iron")
scat(axL, geo["air_gap"],     300, "#7db", "air gap")
first_slot = first_magN = first_magS = True
for name, dom, (ph, dr) in geo.slot_domains():
    scat(axL, dom, 250, "orange", "slot (coil)" if first_slot else None); first_slot = False
for name, dom, pol in geo.magnet_domains():
    if pol > 0:
        scat(axL, dom, 250, "red",  "magnet N" if first_magN else None); first_magN = False
    else:
        scat(axL, dom, 250, "blue", "magnet S" if first_magS else None); first_magS = False
axL.set_title("MODULUS / PINN geometry  (MotorDomains2D)\nannular-sector magnets + rotated-rectangle slots", fontsize=12)
axL.legend(loc="upper right", fontsize=8, markerscale=3)


# RIGHT — the real geometry the FEM uses
def fill_poly(ax, poly, **kw):
    if poly is None:
        return
    geoms = list(getattr(poly, "geoms", [poly]))
    for g in geoms:
        if getattr(g, "is_empty", True):
            continue
        try:
            xy = np.array(g.exterior.coords)
        except Exception:
            continue
        ax.fill(xy[:, 0], xy[:, 1], **kw)
        for hole in getattr(g, "interiors", []):
            hxy = np.array(hole.coords)
            ax.fill(hxy[:, 0], hxy[:, 1], color="white")


fill_poly(axR, polys.get("stator"), color="#aaa", label="stator")
fill_poly(axR, polys.get("rotor"),  color="#444")
fill_poly(axR, polys.get("shaft"),  color="#777")
fl_N = fl_S = fl_c = True
for mp, pol in polys.get("magnets", []):
    fill_poly(axR, mp, color="red" if pol > 0 else "blue",
              label=("magnet N" if (pol > 0 and fl_N) else ("magnet S" if (pol <= 0 and fl_S) else None)))
    if pol > 0: fl_N = False
    else:       fl_S = False
for c in polys.get("coils", []):
    fill_poly(axR, c, color="orange", label="coil" if fl_c else None); fl_c = False
axR.set_title("REAL / FEM geometry  (CadQueryMotor.get_2d_polygons)\ntrapezoid magnets + T-teeth + fillets", fontsize=12)
axR.legend(loc="upper right", fontsize=8)

R = gp.r_stator_out * MM * 1.05
for ax in (axL, axR):
    ax.set_aspect("equal"); ax.set_xlim(-R, R); ax.set_ylim(-R, R)
    ax.set_xlabel("x [mm]"); ax.set_ylabel("y [mm]")
    ax.grid(alpha=0.15)

plt.suptitle("Geometry the Modulus PINN sees  vs  the REAL motor (FEM)", fontsize=14)
plt.tight_layout()
out = os.path.join(os.path.dirname(__file__), "geom_compare.png")
plt.savefig(out, dpi=90)
print("saved", out)

# ── numeric comparison ───────────────────────────────────────────────────────
print("\n--- PINN (MotorDomains2D) ---")
print(f"radii [mm]: shaft_in={gp.r_shaft_in*MM:.2f}  rotor_in={gp.r_rotor_in*MM:.2f} "
      f"rotor_out={gp.r_rotor_out*MM:.2f}  stator_in={gp.r_stator_in*MM:.2f}  stator_out={gp.r_stator_out*MM:.2f}")
print(f"slots={gp.num_slots}  poles={gp.num_poles}  magnet_fill={gp.magnet_fill_fraction}")
print(f"magnet domain radial span: [{gp.r_rotor_in*MM:.2f}, {gp.r_rotor_out*MM:.2f}] mm "
      f"(thickness {(gp.r_rotor_out-gp.r_rotor_in)*MM:.2f} mm), arc-sector shape")
print("\n--- REAL (CadQuery) ---")
print(f"magnets={len(polys.get('magnets', []))}  coils={len(polys.get('coils', []))}")
for i, (mp, pol) in enumerate(polys.get("magnets", [])[:3]):
    b = mp.bounds
    print(f"  magnet[{i}] polarity={pol:+d}  bounds(mm)=({b[0]:.1f},{b[1]:.1f})..({b[2]:.1f},{b[3]:.1f})  area={mp.area:.1f}mm²")
print("real param keys:", sorted(motor.parameters.keys())[:20])
