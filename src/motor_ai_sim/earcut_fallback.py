"""Drop-in replacement for ``mapbox_earcut.triangulate_float64`` when the native
extension cannot load.

Measured 2026-09-02 on the user's workstation: Windows Application Control
blocked ``mapbox_earcut/_core.cp311-win_amd64.pyd`` ("An Application Control
policy has blocked this file") and the whole 3-D view went blank — the 2-D
cross-section builder swallowed the ImportError and returned ``{}``, the
extruded route cached that empty payload, and nothing on screen said why.
The triangulation itself is not exotic: a constrained Delaunay of a polygon
with holes, done here with shapely (GEOS).  The ``triangle`` package that used
to do it was removed on 2026-10-03 (non-commercial licence).

Contract (from mapbox_earcut's .pyi): ``triangulate_float64(vertices (N,2)
float64, ring_end_indices (R,) uint32) -> flat uint32 triangle indices`` into
the SAME vertex array — outer ring first, holes after, each entry the index
one past that ring's last vertex.  GEOS adds no Steiner points, so the index
contract holds; a plain polygon never needs them.
"""
from __future__ import annotations

import numpy as np


def _rings(vertices: np.ndarray, ring_ends: np.ndarray):
    start = 0
    for end in [int(e) for e in ring_ends]:
        yield vertices[start:end], start, end
        start = end


def triangulate_float64(vertices, ring_ends):
    return _triangulate_shapely(vertices, ring_ends)


def triangulate_float32(vertices, ring_ends):
    return triangulate_float64(np.asarray(vertices, dtype=np.float64), ring_ends)


__all__ = ["triangulate_float64", "triangulate_float32"]


def _triangulate_shapely(vertices, ring_ends):
    """Same contract without `triangle`: shapely's (GEOS, LGPL-2.1) constrained
    Delaunay of the polygon.  GEOS adds no Steiner points, so every triangle
    corner is an input vertex and is mapped back by coordinate."""
    import shapely
    from shapely.geometry import Polygon as _SPoly
    V = np.ascontiguousarray(np.asarray(vertices, dtype=np.float64)).reshape(-1, 2)
    ends = np.asarray(ring_ends, dtype=np.int64).ravel()
    rings = [ring for ring, s, e in _rings(V, ends) if e - s >= 3]
    if V.shape[0] < 3 or not rings:
        return np.zeros(0, dtype=np.uint32)
    poly = _SPoly(rings[0], rings[1:])
    if not poly.is_valid:
        poly = shapely.make_valid(poly)
    lookup = {(float(x), float(y)): k for k, (x, y) in enumerate(V)}
    out = []
    for t in getattr(shapely.constrained_delaunay_triangles(poly), "geoms", []):
        try:
            out.extend(lookup[(float(x), float(y))]
                       for x, y in np.asarray(t.exterior.coords)[:3])
        except KeyError:            # a vertex not in the input (invalid ring)
            continue
    return np.asarray(out, dtype=np.uint32)
