"""Drop-in replacement for ``mapbox_earcut.triangulate_float64`` when the native
extension cannot load.

Measured 2026-09-02 on the user's workstation: Windows Application Control
blocked ``mapbox_earcut/_core.cp311-win_amd64.pyd`` ("An Application Control
policy has blocked this file") and the whole 3-D view went blank — the 2-D
cross-section builder swallowed the ImportError and returned ``{}``, the
extruded route cached that empty payload, and nothing on screen said why.
The triangulation itself is not exotic: a constrained Delaunay of a polygon
with holes, which the ``triangle`` package (pure C, unaffected) does exactly.

Contract (from mapbox_earcut's .pyi): ``triangulate_float64(vertices (N,2)
float64, ring_end_indices (R,) uint32) -> flat uint32 triangle indices`` into
the SAME vertex array — outer ring first, holes after, each entry the index
one past that ring's last vertex.  ``S0`` forbids Steiner points so the index
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
    import triangle as _tri
    from shapely.geometry import Polygon as _SPoly

    V = np.ascontiguousarray(np.asarray(vertices, dtype=np.float64)).reshape(-1, 2)
    ends = np.asarray(ring_ends, dtype=np.int64).ravel()
    if V.shape[0] < 3 or ends.size == 0:
        return np.zeros(0, dtype=np.uint32)
    segs, holes = [], []
    for ring, s, e in _rings(V, ends):
        n = e - s
        if n < 3:
            continue
        segs.extend([[s + i, s + (i + 1) % n] for i in range(n)])
        if s > 0:                                  # a hole ring: mark its inside
            try:
                holes.append(list(_SPoly(ring).representative_point().coords[0]))
            except Exception:                      # noqa: BLE001 — degenerate ring
                pass
    if not segs:
        return np.zeros(0, dtype=np.uint32)
    pslg = {"vertices": V, "segments": np.asarray(segs, dtype=np.int32)}
    if holes:
        pslg["holes"] = np.asarray(holes, dtype=np.float64)
    out = _tri.triangulate(pslg, "pS0")          # PSLG, no Steiner points
    tris = np.asarray(out.get("triangles", np.zeros((0, 3))), dtype=np.int64)
    # 'p' without 'S0' could add vertices; with S0 the vertex array is ours,
    # but guard anyway: drop any triangle referencing a vertex we do not have.
    tris = tris[(tris < V.shape[0]).all(axis=1)]
    if tris.size:
        # Match earcut's orientation: its caller flips every triangle to get
        # +Z normals, i.e. it expects CLOCKWISE input.  `triangle` emits CCW —
        # hand back CW so the flip lands where it did with the native library.
        a, b, c = V[tris[:, 0]], V[tris[:, 1]], V[tris[:, 2]]
        signed = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
        ccw = signed > 0
        tris[ccw] = tris[ccw][:, ::-1]
    return tris.astype(np.uint32).ravel()


def triangulate_float32(vertices, ring_ends):
    return triangulate_float64(np.asarray(vertices, dtype=np.float64), ring_ends)


__all__ = ["triangulate_float64", "triangulate_float32"]
