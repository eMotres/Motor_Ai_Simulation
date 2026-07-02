"""Slot-interior mesh QUALITY diagnostic + render.

Builds the production full-ring STATOR half (the coils live there) at 40 mm
(and a scaled 450 mm), measures triangle quality (min interior angle, max
aspect ratio) over the whole mesh AND restricted to the slot-interior region,
and renders a slot close-up.  This is the BEFORE/AFTER instrument for the
structured-slot work.

Usage:  python _slot_quality.py [40|450] [free|struct]
"""
import _use40  # noqa  (points config at the stable 40 mm cfg)
import sys, math, json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import logging
logging.basicConfig(level=logging.WARNING)

import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor

OUT = (r"C:/Users/vadim/AppData/Local/Temp/claude/"
       r"C--Users-vadim-Projects-Motor-Optimization-AI/"
       r"4baf3446-3813-46b8-803e-79b9c27f2cf3/scratchpad")

SCALE = sys.argv[1] if len(sys.argv) > 1 else "40"
MODE = sys.argv[2] if len(sys.argv) > 2 else "free"


def _scale_450(m):
    """Scale the 40 mm config to a ~450 mm stator OD, keeping 12s/14p topology
    and proportional slot / tooth / wire / insulation dimensions."""
    p = m.parameters
    f = 450.0 / float(p["stator_diameter"])
    KEYS = ["stator_diameter", "stator_outer_radius", "stator_inner_radius",
            "rotor_outer_radius", "rotor_inner_radius", "shaft_inner_radius",
            "core_thickness", "tooth_width", "tooth2_width", "cut_width",
            "slot_height", "wire_width", "wire_height", "wire_spacing_x",
            "wire_spacing_y", "insulation_thickness", "magnet_height",
            "rotor_house_height", "magnet_down_height", "motor_length"]
    for k in KEYS:
        if k in p and isinstance(p[k], (int, float)):
            p[k] = float(p[k]) * f
    return m


def build_stator_half(scale, mode):
    m = CadQueryMotor()
    if scale == "450":
        m = _scale_450(m)
    gl = 2
    polys = F._simplify_polys(
        m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005,
        stator_fillet_mm=0.0, n_slip=1008, gap_layers=gl,
        structured_gap=(mode == "struct"), band_mode="merged")
    # feature-relative min size: ~1/3 of the smallest thin insulation feature
    ins = float(m.parameters["insulation_thickness"])
    dy = float(m.parameters["wire_spacing_y"])
    min_sz = max(0.02, min(ins, dy) / 2.0)
    ms, ts, cs, mr, tr, cr = F._build_sliding_band_meshes(
        polys, 0.0, 1.5, min_size_mm=min_sz, outer_air_factor=1.3,
        band_thickness_mm=0.4, n_sectors=-1, geo_cfg=m.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0, gap_layers=gl,
        component_mesh_mm=None, full_ring=True, pole_copy=False)
    return m, (np.asarray(ms.p) * 1000.0, np.asarray(ms.t), np.asarray(ts))


def tri_quality(P, T):
    """Return per-triangle (min interior angle deg, aspect ratio=longest/shortest edge)."""
    a = P[:, T[0]]; b = P[:, T[1]]; c = P[:, T[2]]
    e0 = b - a; e1 = c - b; e2 = a - c
    L0 = np.hypot(*e0); L1 = np.hypot(*e1); L2 = np.hypot(*e2)
    # angle at each vertex via law of cosines
    def ang(la, lb, lc):  # angle opposite la
        v = (lb**2 + lc**2 - la**2) / (2 * lb * lc + 1e-30)
        return np.degrees(np.arccos(np.clip(v, -1, 1)))
    A0 = ang(L1, L2, L0)   # at vertex a? careful: angle opposite edge L1 (b-c) is at a
    # Angle at vertex a is between edges (a->b)=e0? actually opposite to edge bc=L1
    Aa = ang(L1, L0, L2)   # opposite L1 -> at a  (edges L0=ab, L2=ca meet at a)
    Ab = ang(L2, L0, L1)   # at b
    Ac = ang(L0, L1, L2)   # at c
    min_ang = np.minimum(np.minimum(Aa, Ab), Ac)
    Lmax = np.maximum(np.maximum(L0, L1), L2)
    Lmin = np.minimum(np.minimum(L0, L1), L2)
    aspect = Lmax / (Lmin + 1e-30)
    return min_ang, aspect


def slot_mask(P, T, m):
    """Triangles whose centroid lies in the slot-interior annulus (between the
    yoke inner radius and the stator bore, i.e. where wires + insulation live)."""
    p = m.parameters
    r_si = float(p["stator_inner_radius"])
    core = float(p["core_thickness"])
    r_yoke = float(p["stator_outer_radius"]) - core
    cx = P[0, T].mean(0); cy = P[1, T].mean(0)
    r = np.hypot(cx, cy)
    return (r > r_si) & (r < r_yoke)


def main():
    m, (P, T, tags) = build_stator_half(SCALE, MODE)
    min_ang, aspect = tri_quality(P, T)
    sm = slot_mask(P, T, m)

    def stats(mask, label):
        if mask.sum() == 0:
            print(f"  {label}: NO triangles"); return
        ma = min_ang[mask]; asp = aspect[mask]
        print(f"  {label}: n={mask.sum()}  min_angle: min={ma.min():.2f} "
              f"p1={np.percentile(ma,1):.2f} median={np.median(ma):.2f}   "
              f"aspect: max={asp.max():.1f} p99={np.percentile(asp,99):.1f}")
        # count near-degenerate
        print(f"      slivers <5deg: {(ma<5).sum()}  <10deg: {(ma<10).sum()}  "
              f"<15deg: {(ma<15).sum()}")

    print(f"=== SCALE={SCALE}mm  MODE={MODE}  (stator half, full ring) ===")
    print(f"  total tris: {T.shape[1]}")
    stats(np.ones(T.shape[1], bool), "WHOLE MESH")
    stats(sm, "SLOT INTERIOR")

    # render slot close-up: pick the slot near +Y (angle ~90 deg)
    fig, ax = plt.subplots(1, 2, figsize=(15, 8))
    tri = mtri.Triangulation(P[0], P[1], T.T)
    # color by min angle
    cvals = min_ang
    for a, (title, zoom) in zip(ax, [("whole", None), ("slot close-up", True)]):
        tp = a.tripcolor(tri, cvals, shading="flat", cmap="RdYlGn",
                         vmin=0, vmax=60, edgecolors="k", linewidth=0.15)
        a.set_aspect("equal")
        a.set_title(f"{SCALE}mm {MODE} — {title} (color=min angle)")
        if zoom:
            # locate a slot: yoke-inner..bore, angle near 90deg
            r_si = float(m.parameters["stator_inner_radius"])
            r_yk = float(m.parameters["stator_outer_radius"]) - float(m.parameters["core_thickness"])
            rc = 0.5 * (r_si + r_yk)
            # slot pitch
            half = int(m.parameters["num_slots"]) // 2
            # center on a slot gap around 90deg; window sized to the slot
            w = 3.0 * float(m.parameters["tooth_width"])
            a.set_xlim(-w, w)
            a.set_ylim(rc - w, rc + w)
    fig.colorbar(tp, ax=ax[1], label="min interior angle (deg)")
    p = f"{OUT}/slot_quality_{SCALE}_{MODE}.png"
    fig.savefig(p, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {p}", flush=True)


if __name__ == "__main__":
    main()
