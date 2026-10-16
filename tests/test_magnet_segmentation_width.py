"""The magnet segmentation loop width is a property of the CAD, not the mesh.

docs/CONDUCTIVE_BODY_MESH_CONVERGENCE_2026-09-24.md §7.3: the width read off
the mesh-node cloud moved 32.548 → 32.724 mm when Triangle re-planned the L155
magnet cells, i.e. the reported magnet loss moved −0.96 % while the solved
2-D loss moved +0.03 %.  docs/EDDY_TIME_INTEGRATION_2026-09-25.md §2.
"""
from __future__ import annotations

import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.simulation.losses import (  # noqa: E402
    char_width_m, magnet_segmentation, polygon_char_width_m)


def _bread_loaf(n_arc):
    """A surface bread-loaf magnet (m): flat base, arc top, rotated 17°."""
    th = np.linspace(math.radians(-15), math.radians(15), n_arc)
    top = np.vstack([0.060 * np.cos(th), 0.060 * np.sin(th)])
    base = np.array([[0.052, 0.052], [0.060 * math.sin(math.radians(15)),
                                       -0.060 * math.sin(math.radians(15))]])
    P = np.hstack([top, base])
    a = math.radians(17.0)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return R @ P


def _node_cloud(P, n_fill, seed):
    """An interior 'mesh' cloud: the outline plus random interior points,
    denser on one side (what a graded mesh does)."""
    rng = np.random.default_rng(seed)
    lo, hi = P.min(axis=1), P.max(axis=1)
    from matplotlib.path import Path as MPath
    path = MPath(P.T)
    pts = []
    while len(pts) < n_fill:
        q = lo + (hi - lo) * rng.random(2) ** np.array([1.0, 3.0])
        if path.contains_point(q):
            pts.append(q)
    return np.hstack([P, np.array(pts).T])


def test_polygon_width_is_invariant_to_orientation_and_outline_density():
    w1 = polygon_char_width_m(_bread_loaf(41))
    w2 = polygon_char_width_m(_bread_loaf(41)[:, ::-1])
    assert abs(w1 - w2) < 1e-12
    # a finer CAD arc changes the outline by its chord sag only (µm)
    assert abs(polygon_char_width_m(_bread_loaf(161)) - w1) < 2e-6


def test_node_clouds_disagree_but_the_reported_width_does_not():
    P = _bread_loaf(41)
    c1, c2 = _node_cloud(P, 400, 1), _node_cloud(P, 1500, 7)
    # the old estimator reads the node density
    assert abs(char_width_m(c1) - char_width_m(c2)) > 1e-7
    geo = {"magnet_lamination": 5.0}
    k1, r1 = magnet_segmentation(geo, [c1], 0.155, polygons_by_body=[P])
    k2, r2 = magnet_segmentation(geo, [c2], 0.155, polygons_by_body=[P])
    assert r1["width_source"] == r2["width_source"] == "cad_polygon"
    assert k1 == k2 and r1["width_mm"] == r2["width_mm"]
    # no outline → the node cloud, said so
    _k3, r3 = magnet_segmentation(geo, [c1], 0.155, polygons_by_body=[None])
    assert r3["width_source"] == "mesh_nodes"
