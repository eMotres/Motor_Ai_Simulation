"""Render the structured gap mesh at gap_layers=1/2/3 (full-disk halves stitched)
and save PNGs.  Verifies: NO thin filler strips at either boundary, exactly
2/4/6 uniform rings, radial levels printed.
"""
import _use40  # noqa
import sys, math
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import logging
logging.basicConfig(level=logging.WARNING)
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor

OUT = r"C:/Users/vadim/AppData/Local/Temp/claude/C--Users-vadim-Projects-Motor-Optimization-AI/4baf3446-3813-46b8-803e-79b9c27f2cf3/scratchpad"


def build_full(gap_layers):
    m = CadQueryMotor()
    polys = F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005,
                              stator_fillet_mm=0.0, n_slip=1008,
                              gap_layers=gap_layers, structured_gap=True,
                              band_mode="merged")
    ms, ts, cs, mr, tr, cr = F._build_sliding_band_meshes(
        polys, 0.0, 1.5, min_size_mm=0.25, outer_air_factor=1.3,
        band_thickness_mm=0.4, n_sectors=-1, geo_cfg=m.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0, gap_layers=gap_layers,
        component_mesh_mm=None, full_ring=True, pole_copy=False)
    # combine both halves for the picture
    Ps = np.asarray(ms.p) * 1000.0
    Pr = np.asarray(mr.p) * 1000.0
    Ts = np.asarray(ms.t)
    Tr = np.asarray(mr.t)
    return (Ps, Ts, ts), (Pr, Tr, tr)


def radial_levels(P, lo, hi):
    r = np.hypot(P[0], P[1])
    band = r[(r > lo) & (r < hi)]
    lv = []
    for v in np.sort(np.unique(np.round(band, 4))):
        if not lv or v - lv[-1] > 2e-3:
            lv.append(round(float(v), 4))
    return lv


for gl in [1, 2, 3]:
    (Ps, Ts, ts), (Pr, Tr, tr) = build_full(gl)
    lv_all = radial_levels(np.hstack([Ps, Pr]), 12.05, 12.35)
    print(f"gap_layers={gl}: gap radial levels in [12.05,12.35] = {len(lv_all)} -> {lv_all}", flush=True)

    fig, ax = plt.subplots(figsize=(9, 9))
    for (P, T, col) in ((Ps, Ts, "#1f77b4"), (Pr, Tr, "#d62728")):
        tri = mtri.Triangulation(P[0], P[1], T.T)
        ax.triplot(tri, color=col, linewidth=0.3, alpha=0.8)
    # zoom to a slice of the gap so rings are visible
    ax.set_xlim(-2.5, 2.5)
    ax.set_ylim(11.4, 12.9)
    ax.set_aspect("equal")
    for rr in (12.1, 12.2, 12.3):
        th = np.linspace(-0.3, 0.3, 100)
        ax.plot(rr * np.sin(th), rr * np.cos(th), "k--", lw=0.5, alpha=0.4)
    ax.set_title(f"structured gap, gap_layers={gl}  (blue=stator half, red=rotor half)\n"
                 f"levels={lv_all}")
    p = f"{OUT}/gap_mesh_gl{gl}.png"
    fig.savefig(p, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {p}", flush=True)

    # full-disk overview too (first gl only)
    if gl == 2:
        fig, ax = plt.subplots(figsize=(9, 9))
        for (P, T, col) in ((Ps, Ts, "#1f77b4"), (Pr, Tr, "#d62728")):
            tri = mtri.Triangulation(P[0], P[1], T.T)
            ax.triplot(tri, color=col, linewidth=0.15, alpha=0.7)
        ax.set_aspect("equal")
        ax.set_title(f"full disk, gap_layers={gl}")
        fig.savefig(f"{OUT}/full_disk_gl{gl}.png", dpi=110, bbox_inches="tight")
        plt.close(fig)
print("RENDER DONE", flush=True)
