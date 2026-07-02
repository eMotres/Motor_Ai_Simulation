"""Proof renders for the structured-slot work: FREE vs STRUCTURED slot close-up
at 40 mm AND 450 mm, plus a whole-cross-section.  Colours by min interior angle
(red=sliver, green=well-shaped) so the insulation slivers (free) vs clean block
(structured) are directly visible."""
import _use40  # noqa
import sys, math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import logging
logging.basicConfig(level=logging.ERROR)
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor

OUT = (r"C:/Users/vadim/AppData/Local/Temp/claude/"
       r"C--Users-vadim-Projects-Motor-Optimization-AI/"
       r"4baf3446-3813-46b8-803e-79b9c27f2cf3/scratchpad")


def scale450(m):
    p = m.parameters; f = 450.0 / float(p["stator_diameter"])
    for k in ["stator_diameter", "stator_outer_radius", "stator_inner_radius",
              "rotor_outer_radius", "rotor_inner_radius", "shaft_inner_radius",
              "core_thickness", "tooth_width", "tooth2_width", "cut_width",
              "slot_height", "wire_width", "wire_height", "wire_spacing_x",
              "wire_spacing_y", "insulation_thickness", "magnet_height",
              "rotor_house_height", "magnet_down_height", "motor_length"]:
        if k in p and isinstance(p[k], (int, float)):
            p[k] = float(p[k]) * f
    return m


def build(scale, structured):
    m = CadQueryMotor()
    if scale == 450:
        m = scale450(m)
    polys = F._simplify_polys(m.get_2d_polygons(0.0), tol_mm=0.005,
                              stator_fillet_mm=0.0, band_mode="merged",
                              structured_gap=False)
    ins = float(m.parameters["insulation_thickness"]); dy = float(m.parameters["wire_spacing_y"])
    ms, ts, cs, mr, tr, cr = F._build_sliding_band_meshes(
        polys, 0.0, 1.5, min_size_mm=max(0.02, min(ins, dy) / 2), outer_air_factor=1.3,
        band_thickness_mm=0.4, n_sectors=-1, geo_cfg=m.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0, gap_layers=2.0,
        component_mesh_mm={"stator": min(2.0, 0.05 * float(m.parameters["stator_diameter"]))},
        full_ring=True, pole_copy=False, structured_slot=structured)
    return m, np.asarray(ms.p) * 1000.0, np.asarray(ms.t), np.asarray(ts)


def minang(P, T):
    a = P[:, T[0]]; b = P[:, T[1]]; c = P[:, T[2]]
    L0 = np.hypot(*(b - a)); L1 = np.hypot(*(c - b)); L2 = np.hypot(*(a - c))
    def ang(la, lb, lc):
        v = (lb**2 + lc**2 - la**2) / (2 * lb * lc + 1e-30)
        return np.degrees(np.arccos(np.clip(v, -1, 1)))
    return np.minimum(np.minimum(ang(L1, L0, L2), ang(L2, L0, L1)), ang(L0, L1, L2))


for scale in (40, 450):
    fig, axes = plt.subplots(1, 2, figsize=(15, 8))
    for ax, structured in zip(axes, (False, True)):
        m, P, T, tags = build(scale, structured)
        ma = minang(P, T)
        tri = mtri.Triangulation(P[0], P[1], T.T)
        tp = ax.tripcolor(tri, ma, shading="flat", cmap="RdYlGn", vmin=0, vmax=45,
                          edgecolors="k", linewidth=0.15)
        # zoom to one slot near +Y
        p = m.parameters
        r_si = float(p["stator_inner_radius"]); core = float(p["core_thickness"])
        r_yk = float(p["stator_outer_radius"]) - core
        rc = 0.5 * (r_si + r_yk); w = 2.6 * float(p["tooth_width"])
        ax.set_xlim(-w, w); ax.set_ylim(rc - w, rc + w); ax.set_aspect("equal")
        # block-only quality number
        blk = (tags == F.DOM_WIRE_INS) | (tags == F.DOM_SLOT_INS) | (tags >= F.DOM_COIL_BASE)
        lbl = "STRUCTURED" if structured else "FREE"
        if structured and blk.any():
            mb = ma[blk]
            ax.set_title(f"{scale}mm {lbl} slot\nINSULATION block: min angle {mb.min():.1f}°, "
                         f"{int((mb<15).sum())} slivers<15°")
        else:
            # free: measure slivers in the slot-interior annulus
            cx = P[0, T].mean(0); cy = P[1, T].mean(0); r = np.hypot(cx, cy)
            sm = (r > r_si) & (r < r_yk)
            ms2 = ma[sm]
            ax.set_title(f"{scale}mm {lbl} slot\nslot interior: min angle {ms2.min():.1f}°, "
                         f"{int((ms2<5).sum())} slivers<5°")
    fig.colorbar(tp, ax=axes, label="min interior angle (deg)  red=sliver green=good",
                 shrink=0.8)
    pth = f"{OUT}/slot_proof_{scale}.png"
    fig.savefig(pth, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {pth}", flush=True)

# whole cross-section (structured, 40mm)
m, P, T, tags = build(40, True)
ma = minang(P, T)
fig, ax = plt.subplots(figsize=(10, 10))
tri = mtri.Triangulation(P[0], P[1], T.T)
ax.tripcolor(tri, ma, shading="flat", cmap="RdYlGn", vmin=0, vmax=45,
             edgecolors="k", linewidth=0.08)
ax.set_aspect("equal")
ax.set_title("40mm STRUCTURED slot — whole stator cross-section (color=min angle)")
pth = f"{OUT}/slot_proof_whole_40.png"
fig.savefig(pth, dpi=130, bbox_inches="tight")
plt.close(fig)
print(f"saved {pth}", flush=True)
print("PROOF RENDERS DONE", flush=True)
