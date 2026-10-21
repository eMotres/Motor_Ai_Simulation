"""Synthetic, public-data regression for spoke magnet pole flanks."""
from __future__ import annotations

import math

import numpy as np
import pytest
from shapely.geometry import LineString

from motor_ai_sim.simulation.geo_mesh import _build_pslg, _triangulate, set_cdt_backend
from motor_ai_sim.simulation.geo_mesh_gmsh import _EQ


def _spoke_pslg(angle_deg: float):
    outer = [(-10, -10), (10, -10), (10, 10), (-10, 10), (-10, -10)]
    magnets = [
        [(1, -3), (2, -3), (2, 3), (1, 3), (1, -3)],
        [(-2, -3), (-1, -3), (-1, 3), (-2, 3), (-2, -3)],
    ]
    theta = math.radians(angle_deg)
    c, s = math.cos(theta), math.sin(theta)
    rot = np.array([[c, -s], [s, c]])

    def line(points):
        return LineString((rot @ np.asarray(points, float).T).T)

    vertices, segments = _build_pslg(
        [line(outer), *(line(ring) for ring in magnets)])
    regions = np.array([
        *(np.r_[rot @ point, 5, _EQ * 1.0] for point in [(0, 0)]),
        *(np.r_[rot @ point, 6, _EQ * 1.0] for point in [(1.5, 0), (-1.5, 0)]),
    ], dtype=float)
    return vertices, segments, regions, rot


def _mesh_flanks(mesh_size_mm: float, angle_deg: float, backend: str):
    vertices, segments, regions, rot = _spoke_pslg(angle_deg)
    area = _EQ * mesh_size_mm**2
    regions[:, 3] = area
    set_cdt_backend(backend)
    try:
        points, triangles = _triangulate(
            vertices, segments, area, hole=False, regions=regions)
    finally:
        set_cdt_backend(None)

    inverse = np.linalg.inv(rot)
    local = (inverse @ points.T).T
    owners = {}
    for tri_i, tri in enumerate(triangles):
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            owners.setdefault(tuple(sorted((int(a), int(b)))), []).append(tri_i)

    flank_edges = []
    adjacent = set()
    for edge, tris in owners.items():
        a, b = edge
        pa, pb = local[a], local[b]
        if abs(pa[0] - pb[0]) > 1e-7:
            continue
        if not any(abs(pa[0] - x) < 1e-7 for x in (1, -1)):
            continue
        if min(pa[1], pb[1]) < -3 - 1e-7 or max(pa[1], pb[1]) > 3 + 1e-7:
            continue
        flank_edges.append(float(np.linalg.norm(points[a] - points[b])))
        adjacent.update(tris)

    q = points[np.asarray(triangles, int)]
    angles = []
    for i in range(3):
        a = q[:, (i + 1) % 3] - q[:, i]
        b = q[:, (i + 2) % 3] - q[:, i]
        cosine = np.sum(a * b, axis=1) / np.maximum(
            np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1), 1e-30)
        angles.append(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))
    angle_array = np.min(np.stack(angles, axis=1), axis=1)
    return {
        "median_spacing_mm": float(np.median(flank_edges)),
        "max_spacing_mm": float(np.max(flank_edges)),
        "n_flank_edges": len(flank_edges),
        "adjacent_min_angle_deg": float(angle_array[sorted(adjacent)].min()),
    }


@pytest.mark.parametrize("backend", ["gmsh", "netgen"])
def test_spoke_pole_flank_spacing_tracks_mesh_size_without_fan_slivers(backend):
    if backend == "netgen":
        pytest.importorskip("netgen.occ")
    results = {
        (angle, size): _mesh_flanks(size, angle, backend)
        for angle in (0.0, 37.0)
        for size in (0.8, 1.6)
    }
    for angle in (0.0, 37.0):
        fine = results[(angle, 0.8)]
        coarse = results[(angle, 1.6)]
        assert fine["n_flank_edges"] >= 8
        assert coarse["n_flank_edges"] >= 4
        assert 1.6 < coarse["median_spacing_mm"] / fine["median_spacing_mm"] < 2.4
    for result in results.values():
        assert result["adjacent_min_angle_deg"] > 10.0
        assert result["max_spacing_mm"] < 2.1
    for size in (0.8, 1.6):
        a, b = results[(0.0, size)], results[(37.0, size)]
        assert abs(a["median_spacing_mm"] - b["median_spacing_mm"]) <= 0.15 * a["median_spacing_mm"]
