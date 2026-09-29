"""Drop-in replacement for ``mapbox_earcut.triangulate_float64`` when the native
extension cannot load.

Measured 2026-09-02 on the user's workstation: Windows Application Control
blocked ``mapbox_earcut/_core.cp311-win_amd64.pyd`` and the whole 3-D view went
blank.  The triangulation itself is not exotic: a constrained Delaunay of a
polygon with holes.  Since 2026-09-29 it is done with shapely's
``constrained_delaunay_triangles`` (GEOS, LGPL-2.1, AGPL-compatible); the former
``triangle`` backend was removed because its licence forbids commercial use.

Contract (from mapbox_earcut's .pyi): ``triangulate_float64(vertices (N,2)
float64, ring_end_indices (R,) uint32) -> flat uint32 triangle indices`` into
the SAME vertex array — outer ring first, holes after, each entry the index
one past that ring's last vertex.  GEOS's polygon CDT adds no Steiner points,
so every output corner is an input vertex and is mapped back by coordinate.
"""
from __future__ import annotations

import numpy as np


def triangulate_float64(vertices, ring_ends):
    import shapely
    from shapely.geometry import Polygon as _SPoly

    V = np.ascontiguousarray(np.asarray(vertices, dtype=np.float64)).reshape(-1, 2)
    ends = np.asarray(ring_ends, dtype=np.int64).ravel()
    if V.shape[0] < 3 or ends.size == 0:
        return np.zeros(0, dtype=np.uint32)
    rings, start = [], 0
    for end in [int(e) for e in ends]:
        if end - start >= 3:
            rings.append(V[start:end])
        start = end
    if not rings:
        return np.zeros(0, dtype=np.uint32)
    poly = _SPoly(rings[0], rings[1:])
    if not poly.is_valid:
        poly = shapely.make_valid(poly)
    tris = shapely.constrained_delaunay_triangles(poly)
    lookup = {(float(x), float(y)): k for k, (x, y) in enumerate(V)}
    out = []
    for t in getattr(tris, "geoms", []):
        c = np.asarray(t.exterior.coords)[:3]
        try:
            idx = [lookup[(float(x), float(y))] for x, y in c]
        except KeyError:            # a vertex not in the input (invalid ring)
            continue
        out.extend(idx)
    return np.asarray(out, dtype=np.uint32)
