"""Direct build of the STATOR sector via build_mesh_from_polygons with
structured_slot on/off — isolates the new slot mesher (no sliding-band
plumbing yet).  Prints slot-interior triangle quality + renders."""
import _use40  # noqa
import sys, math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor

OUT = (r"C:/Users/vadim/AppData/Local/Temp/claude/"
       r"C--Users-vadim-Projects-Motor-Optimization-AI/"
       r"4baf3446-3813-46b8-803e-79b9c27f2cf3/scratchpad")

MODE = sys.argv[1] if len(sys.argv) > 1 else "struct"   # struct | free


def tri_quality(P, T):
    a = P[:, T[0]]; b = P[:, T[1]]; c = P[:, T[2]]
    L0 = np.hypot(*(b - a)); L1 = np.hypot(*(c - b)); L2 = np.hypot(*(a - c))
    def ang(la, lb, lc):
        v = (lb**2 + lc**2 - la**2) / (2 * lb * lc + 1e-30)
        return np.degrees(np.arccos(np.clip(v, -1, 1)))
    Aa = ang(L1, L0, L2); Ab = ang(L2, L0, L1); Ac = ang(L0, L1, L2)
    min_ang = np.minimum(np.minimum(Aa, Ab), Ac)
    aspect = np.maximum(np.maximum(L0, L1), L2) / (np.minimum(np.minimum(L0, L1), L2) + 1e-30)
    return min_ang, aspect


m = CadQueryMotor()
polys = F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005,
                          stator_fillet_mm=0.0, band_mode="merged")
# stator sector at n_sectors=6 (keeps it small + fast for the isolation test)
ins = float(m.parameters["insulation_thickness"]); dy = float(m.parameters["wire_spacing_y"])
min_sz = max(0.02, min(ins, dy) / 2.0)
polys_s = dict(polys)
if "out_band" in polys_s:
    polys_s["air_outer"] = polys_s.pop("out_band")
mesh, tags, classify = F.build_mesh_from_polygons(
    polys_s, 0.0, 1.5, min_size_mm=min_sz, normal_deviation_deg=8.0,
    aspect_ratio=10.0, geo_cfg=m.parameters, outer_air_factor=1.3,
    motion_band=False, gap_layers=2.0, n_sectors=6, add_background_air=False,
    structured_slot=(MODE == "struct"))
P = np.asarray(mesh.p) * 1000.0
T = np.asarray(mesh.t)
tags = np.asarray(tags)
min_ang, aspect = tri_quality(P, T)

# slot interior mask
p = m.parameters
r_si = float(p["stator_inner_radius"]); core = float(p["core_thickness"])
r_yoke = float(p["stator_outer_radius"]) - core
cx = P[0, T].mean(0); cy = P[1, T].mean(0); r = np.hypot(cx, cy)
sm = (r > r_si) & (r < r_yoke)

print(f"=== build_mesh_from_polygons stator sector, MODE={MODE} ===")
print(f"total tris {T.shape[1]}, unique tags {sorted(set(tags.tolist()))}")
DOM_WIRE_INS, DOM_SLOT_INS = F.DOM_WIRE_INS, F.DOM_SLOT_INS
print(f"  wire_ins cells={int((tags==DOM_WIRE_INS).sum())}  "
      f"slot_ins cells={int((tags==DOM_SLOT_INS).sum())}  "
      f"coil(base+) cells={int((tags>=F.DOM_COIL_BASE).sum())}")
for label, mask in [("WHOLE", np.ones(T.shape[1], bool)), ("SLOT", sm)]:
    ma = min_ang[mask]; asp = aspect[mask]
    print(f"  {label}: n={mask.sum()} min_ang min={ma.min():.2f} "
          f"median={np.median(ma):.1f}  aspect max={asp.max():.1f}  "
          f"slivers<5={int((ma<5).sum())} <10={int((ma<10).sum())} <15={int((ma<15).sum())}")

# Per-tag breakdown of the <10deg slivers: WHICH material owns the bad tris?
print("  --- <10deg sliver owners (tag: count) ---")
bad = min_ang < 10.0
def _dname(t):
    t = int(t)
    if t >= F.DOM_COIL_BASE: return "COPPER"
    if t >= F.DOM_MAG_BASE: return "magnet"
    return {0:"AIR",1:"stator",3:"airgap",5:"rotor",6:"shaft",8:"OUTER",
            9:"WIRE_INS",10:"SLOT_INS"}.get(t, f"dom{t}")
from collections import Counter
cc = Counter(_dname(t) for t in tags[bad])
for k, v in sorted(cc.items(), key=lambda kv: -kv[1]):
    print(f"      {k}: {v}")
# insulation-only quality (the acceptance target)
ins_mask = (tags == DOM_WIRE_INS) | (tags == DOM_SLOT_INS) | (tags >= F.DOM_COIL_BASE)
if ins_mask.sum():
    mi = min_ang[ins_mask]
    print(f"  SLOT-BLOCK ONLY (cu+enamel+liner): n={ins_mask.sum()} "
          f"min_ang min={mi.min():.2f} median={np.median(mi):.1f} "
          f"<5={int((mi<5).sum())} <10={int((mi<10).sum())} <15={int((mi<15).sum())}")

# render slot close-up (angle ~90 deg is in the sector for n_sectors=6? sector=60deg
# so pick a slot inside [0,60]; use ~30 deg)
fig, ax = plt.subplots(figsize=(9, 9))
tri = mtri.Triangulation(P[0], P[1], T.T)
tp = ax.tripcolor(tri, min_ang, shading="flat", cmap="RdYlGn", vmin=0, vmax=60,
                  edgecolors="k", linewidth=0.2)
ax.set_aspect("equal")
th = math.radians(30); rc = 0.5 * (r_si + r_yoke)
w = 2.2 * float(p["tooth_width"])
ax.set_xlim(rc * math.cos(th) - w, rc * math.cos(th) + w)
ax.set_ylim(rc * math.sin(th) - w, rc * math.sin(th) + w)
ax.set_title(f"stator sector {MODE} — slot close-up (color=min angle)")
fig.colorbar(tp, label="min interior angle (deg)")
pth = f"{OUT}/slot_build_{MODE}.png"
fig.savefig(pth, dpi=140, bbox_inches="tight")
plt.close(fig)
print(f"  saved {pth}")
