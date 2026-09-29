# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) MOTRES d.o.o. and contributors
"""gmsh backend for the geometry-driven mesher's constrained triangulation.

`geo_mesh.py` builds every half-mesh from a planar straight-line graph (PSLG)
that it constructs itself: resampled gap-facing arcs on the slip grid, 1 um
snapped coordinates, clone-identical radial cuts, densified outlines, the
structured skin/wire patches left as holes.  Only ONE call in that chain was
Triangle: `_triangulate(V, S, ...)`, the quality CDT of the PSLG.  This module
is its drop-in replacement on gmsh, so every other feature of the geometry
mesher (skin layers, per-part sizes, wire cells, cusp/sliver guards,
slot/pole tiling, exact tagging, moving-band rings, mesh budget) is shared
between the two backends by construction.

Contract, the one the callers relied on with Triangle `-Y`:

* the returned vertex array STARTS with the input vertices, in input order
  (unused ones included; `geo_mesh_halves` prunes them);
* no point is inserted on a DOMAIN-BOUNDARY segment (the cut rays, the gap
  rings, the rims of the structured skin/wire patches, hole outlines): that is
  what keeps the radial cuts clone-identical and every node-identity weld and
  patch stitch hanging-node free.  INTERIOR segments (material interfaces
  between two meshed faces) may be subdivided, as Triangle's refinement did;
* holes are flooded from their marker to the enclosing segment loops; region
  seeds set a per-region maximum cell area (the last seed inside a face wins,
  the `_seeds` order); a face without a seed has no area cap (Triangle "Aa");
* triangles are counter-clockwise.

Sizing is gmsh's own field machinery, driven by the geometry only:

* every input segment carries a local size h = min(its length, K x its local
  feature size), the feature size being the distance to the nearest segment it
  does not touch (a thin air pocket, a bridge, a wire gap) — the scale the PSLG
  producer resolved on physical grounds (skin depth, wire height, slip grid,
  fillet chords);
* segments are grouped in x1.5 size classes; each class is a Distance field on
  its curves feeding h0 + g * distance (MathEval), i.e. a growth-limited
  grading away from every feature;
* each face is capped by a Constant field at its region's target edge;
* the background mesh is the Min of all of them.

The build runs on one gmsh thread and is therefore deterministic: the same
PSLG gives the same mesh, node for node (tests/test_geo_mesh_gmsh.py).
"""
from __future__ import annotations

import logging
import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# equilateral relation, the one geo_mesh._cell_area uses: area = 0.433 L^2
_EQ = 0.4330
# Feature-size factor: an element may span a gap g with an edge up to K*g.
# 2.5 keeps a 20-degree minimum angle on the isosceles across the gap (the
# quality Triangle's q20 guaranteed).
_LFS_K = 2.5
# Growth slope of the element size away from a feature (mm per mm).  2.0 is
# close to the grading Triangle's q20 produced from the same boundary spacing
# (40 mm 12s/14p at 1 mm: 1.7x Triangle's element count; 1.0 gives 2.3x,
# 3.0 barely fewer than 2.0 because the region caps then bind).
_GROWTH = 2.0
# Size classes (ratio between consecutive classes).
_CLASS_RATIO = 1.5


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return v if v > 0 else default


class GmshCDTError(RuntimeError):
    """The gmsh backend could not triangulate the PSLG (loud, never silent)."""


def _edge_of_area(a: float) -> float:
    return math.sqrt(max(float(a), 1e-12) / _EQ)


def _faces(V: np.ndarray, S: np.ndarray):
    """Bounded faces of the PSLG as shapely polygons."""
    import shapely
    from shapely.geometry import LineString
    lines = [LineString([V[a], V[b]]) for a, b in S]
    polys, _cuts, _dangles, invalid = shapely.polygonize_full(lines)
    if len(getattr(invalid, "geoms", [])):
        raise GmshCDTError("PSLG has %d invalid ring(s) — the segment graph "
                           "self-intersects" % len(invalid.geoms))
    return list(getattr(polys, "geoms", []))


def _feature_sizes(V: np.ndarray, S: np.ndarray):
    """Per segment: (distance to the nearest segment sharing no vertex with it,
    index of that segment or -1)."""
    import shapely
    from shapely.geometry import LineString
    lines = [LineString([V[a], V[b]]) for a, b in S]
    tree = shapely.STRtree(lines)
    L = np.linalg.norm(V[S[:, 0]] - V[S[:, 1]], axis=1)
    out = np.full(len(S), np.inf)
    near = np.full(len(S), -1, np.int64)
    for k, seg in enumerate(lines):
        a, b = int(S[k, 0]), int(S[k, 1])
        for c in tree.query(seg.buffer(max(float(L[k]), 1e-6))):
            if c == k:
                continue
            if S[c, 0] in (a, b) or S[c, 1] in (a, b):
                continue
            d = float(seg.distance(lines[c]))
            if d < out[k]:
                out[k] = d
                near[k] = int(c)
    return out, near


def _segment_sizes(L, lfs, near, frozen, K):
    """Target edge per segment.

    * frozen (domain-boundary) segments cannot be split: their size IS their
      length, and it is what the size field must grade from;
    * an interior interface is cut to min(its length, K x its feature size)
      so a thin feature between two interfaces never forms a sliver ...
    * ... but never finer than a FROZEN segment facing it across that thin
      feature: a 0.02 mm recess under a 0.074 mm slip-grid ring meshes as one
      row spanning the recess only if both sides carry the same spacing
      (measured: aspect 51 with the interface cut to 2.5 x 0.02 mm, the
      cell-row layout with the frozen spacing)."""
    h = np.minimum(L, K * lfs)
    face_frozen = (near >= 0) & frozen[np.maximum(near, 0)]
    h = np.where(face_frozen, np.minimum(L, np.maximum(h, L[np.maximum(near, 0)])), h)
    h = np.where(frozen, L, h)
    return np.maximum(h, 1e-4)


def _ring_use_counts(S, faces, keep, key, seg_id):
    """How many kept faces use each segment on their rings (2 = interior)."""
    cnt = np.zeros(len(S), int)
    for i, f in enumerate(faces):
        if not keep[i]:
            continue
        for ring in [f.exterior] + list(f.interiors):
            c = list(ring.coords)
            for j in range(len(c) - 1):
                a = key.get((float(c[j][0]), float(c[j][1])))
                b = key.get((float(c[j + 1][0]), float(c[j + 1][1])))
                k = seg_id.get((min(a, b), max(a, b))) if a is not None and b is not None else None
                if k is not None:
                    cnt[k] += 1
    return cnt


def triangulate_gmsh(V, S, area: float, hole_pts=None, regions=None,
                     budget: Optional[int] = None
                     ) -> Tuple[np.ndarray, np.ndarray]:
    """Constrained quality triangulation of the PSLG (V mm, S index pairs).

    See the module docstring for the contract.  `budget` (triangles) arms the
    optimizer fence: a predicted or actual count above it raises
    geo_mesh.MeshBudgetExceeded before/after meshing."""
    import gmsh
    from shapely.geometry import Point
    from shapely.prepared import prep
    from motor_ai_sim.simulation.sb_domains import _GMSH_LOCK
    from motor_ai_sim.simulation.geo_mesh import MeshBudgetExceeded

    V = np.asarray(V, float).reshape(-1, 2)
    S = np.asarray(S, np.int64).reshape(-1, 2)
    S = S[S[:, 0] != S[:, 1]]
    S = np.unique(np.sort(S, axis=1), axis=0)
    nV = len(V)
    holes = np.zeros((0, 2)) if hole_pts is None else \
        np.asarray(hole_pts, float).reshape(-1, 2)
    K = _env_float("SB_GMSH_CDT_LFS_K", _LFS_K)
    g = _env_float("SB_GMSH_CDT_GROWTH", _GROWTH)

    faces = _faces(V, S)
    if not faces:
        raise GmshCDTError("PSLG encloses no face")
    pf = [prep(f) for f in faces]
    keep = [not any(pf[i].contains(Point(h)) for h in holes)
            for i in range(len(faces))]
    tgt: List[Optional[float]] = [None] * len(faces)
    if regions is not None and len(regions):
        for row in np.asarray(regions, float):
            pt = Point(row[0], row[1])
            for i in range(len(faces)):
                if keep[i] and pf[i].contains(pt):
                    tgt[i] = _edge_of_area(row[3]) if row[3] > 0 else None
                    break
    elif area and area > 0:
        tgt = [_edge_of_area(area)] * len(faces)

    if budget:
        pred = sum(faces[i].area / (_EQ * tgt[i] ** 2)
                   for i in range(len(faces)) if keep[i] and tgt[i])
        if pred > 2.0 * budget:
            raise MeshBudgetExceeded(
                "mesh budget: this cross-section needs ~{:.0f} triangles at "
                "its target sizes (budget {}); rejected before meshing."
                .format(pred, int(budget)))

    key = {(float(x), float(y)): i for i, (x, y) in
           reversed(list(enumerate(V)))}          # first index wins on dups
    seg_id = {(int(a), int(b)): k for k, (a, b) in enumerate(S)}

    L = np.linalg.norm(V[S[:, 0]] - V[S[:, 1]], axis=1)
    lfs, near = _feature_sizes(V, S)
    frozen = _ring_use_counts(S, faces, keep, key, seg_id) != 2
    h_seg = _segment_sizes(L, lfs, near, frozen, K)

    def _vid(c):
        i = key.get((float(c[0]), float(c[1])))
        if i is None:
            raise GmshCDTError("face ring vertex (%.6f, %.6f) is not a PSLG "
                               "vertex" % (c[0], c[1]))
        return i

    _GMSH_LOCK.acquire()
    own = not gmsh.isInitialized()
    if own:
        try:
            gmsh.initialize([], interruptible=False)
        except TypeError:
            gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.option.setNumber("Mesh.MaxNumThreads1D", 1)
        gmsh.option.setNumber("Mesh.MaxNumThreads2D", 1)
        gmsh.option.setNumber("Mesh.Algorithm", int(_env_float("SB_GMSH_CDT_ALGO", 6)))
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.MeshSizeMin", 0.0)
        gmsh.option.setNumber("Mesh.MeshSizeMax", 1e22)
        gmsh.option.setNumber("Mesh.Optimize", 0)
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        gmsh.model.add("geo_cdt")
        geo = gmsh.model.geo
        ptag: Dict[int, int] = {}

        def _pt(i):
            t = ptag.get(i)
            if t is None:
                t = geo.addPoint(float(V[i, 0]), float(V[i, 1]), 0.0)
                ptag[i] = t
            return t

        ltag: Dict[int, int] = {}
        uses = np.zeros(len(S), int)

        def _ln(a, b):
            k = seg_id.get((min(a, b), max(a, b)))
            if k is None:
                raise GmshCDTError("face ring edge %d-%d is not a PSLG "
                                   "segment" % (a, b))
            uses[k] += 1
            t = ltag.get(k)
            if t is None:
                t = geo.addLine(_pt(int(S[k, 0])), _pt(int(S[k, 1])))
                ltag[k] = t
            return t if a < b else -t          # S rows are sorted (min, max)

        def _loop(coords):
            ids = [_vid(c) for c in list(coords)[:-1]]
            curves = [_ln(ids[j], ids[(j + 1) % len(ids)]) for j in range(len(ids))]
            return geo.addCurveLoop(curves, reorient=False)

        surf_of_face: Dict[int, int] = {}
        for i, f in enumerate(faces):
            if not keep[i]:
                continue
            loops = [_loop(f.exterior.coords)] + [_loop(r.coords) for r in f.interiors]
            surf_of_face[i] = geo.addPlaneSurface(loops)
        # segments on no kept face ring (dangles inside a face) and free
        # vertices: Triangle keeps them in the mesh, so they are embedded here
        on_ring = set(ltag)
        on_seg = np.zeros(nV, bool)
        on_seg[S.ravel()] = True
        emb_l: Dict[int, List[int]] = {}
        emb_p: Dict[int, List[int]] = {}
        for k in range(len(S)):
            if k in on_ring:
                continue
            a, b = S[k]
            m = Point(0.5 * (V[a] + V[b]))
            for i, s in surf_of_face.items():
                if pf[i].contains(m):
                    t = geo.addLine(_pt(int(a)), _pt(int(b)))
                    ltag[k] = t
                    emb_l.setdefault(s, []).append(t)
                    break
        for v in np.where(~on_seg)[0]:
            pt = Point(V[v])
            for i, s in surf_of_face.items():
                if pf[i].contains(pt):
                    emb_p.setdefault(s, []).append(_pt(int(v)))
                    break
        geo.synchronize()
        # boundary (one kept face) and embedded segments are frozen; interior
        # interfaces (two kept faces) are subdivided by the size field
        for k, t in ltag.items():
            if uses[k] != 2:
                gmsh.model.mesh.setTransfiniteCurve(abs(t), 2)
        for s, ls in emb_l.items():
            gmsh.model.mesh.embed(1, ls, 2, s)
        for s, ps in emb_p.items():
            gmsh.model.mesh.embed(0, ps, 2, s)

        F = gmsh.model.mesh.field
        fids = []
        cls: Dict[int, List[int]] = {}
        cls_h: Dict[int, float] = {}
        cls_L: Dict[int, float] = {}
        for k, t in ltag.items():
            c = int(math.floor(math.log(h_seg[k]) / math.log(_CLASS_RATIO)))
            cls.setdefault(c, []).append(abs(t))
            cls_h[c] = min(cls_h.get(c, math.inf), float(h_seg[k]))
            cls_L[c] = max(cls_L.get(c, 0.0), float(L[k]))
        for c in sorted(cls):
            h0 = cls_h[c]
            dfi = F.add("Distance")
            F.setNumbers(dfi, "CurvesList", sorted(cls[c]))
            F.setNumber(dfi, "Sampling",
                        int(min(200, max(2, math.ceil(cls_L[c] / h0) + 1))))
            mfi = F.add("MathEval")
            F.setString(mfi, "F", "%.9g + %.9g*F%d" % (h0, g, dfi))
            fids.append(mfi)
        by_size: Dict[float, List[int]] = {}
        for i, s in surf_of_face.items():
            if tgt[i]:
                by_size.setdefault(round(float(tgt[i]), 9), []).append(s)
        for h, surfs in sorted(by_size.items()):
            fid = F.add("Constant")
            F.setNumbers(fid, "SurfacesList", surfs)
            F.setNumber(fid, "VIn", float(h))
            F.setNumber(fid, "VOut", 1e22)
            F.setNumber(fid, "IncludeBoundary", 1)
            fids.append(fid)
        mf = F.add("Min")
        F.setNumbers(mf, "FieldsList", fids)
        F.setAsBackgroundMesh(mf)
        try:
            gmsh.model.mesh.generate(2)
        except Exception as e:  # noqa: BLE001 — re-raised loudly
            raise GmshCDTError("gmsh failed to mesh the PSLG (%d vertices, %d "
                               "segments, %d faces): %s"
                               % (nV, len(S), len(surf_of_face), e)) from e

        # nodes: every geometric point is an input vertex (index known); the
        # rest follow in node-tag order (deterministic)
        out_idx: Dict[int, int] = {}
        for i, t in ptag.items():
            nt, _, _ = gmsh.model.mesh.getNodes(0, t)
            if len(nt):
                out_idx[int(nt[0])] = i
        tags_all, xyz, _ = gmsh.model.mesh.getNodes()
        tags_all = np.asarray(tags_all, np.int64)
        xyz = np.asarray(xyz, float).reshape(-1, 3)[:, :2]
        extra = []
        for j in np.argsort(tags_all, kind="stable"):
            t = int(tags_all[j])
            if t not in out_idx:
                out_idx[t] = nV + len(extra)
                extra.append(xyz[j])
        tris = []
        for i, s in surf_of_face.items():
            et, _, en = gmsh.model.mesh.getElements(2, s)
            got = 0
            for ty, nodes in zip(et, en):
                if int(ty) != 2:
                    raise GmshCDTError("gmsh produced non-triangle elements "
                                       "(type %d)" % int(ty))
                tris.append(np.asarray(nodes, np.int64).reshape(-1, 3))
                got += len(nodes) // 3
            if got == 0:
                raise GmshCDTError("face %d (area %.4g mm^2) meshed to no "
                                   "triangle" % (i, faces[i].area))
    finally:
        try:
            gmsh.model.remove()
        except Exception:  # noqa: BLE001
            pass
        if own:
            try:
                gmsh.finalize()
            except Exception:  # noqa: BLE001
                pass
        _GMSH_LOCK.release()

    Vo = np.vstack([V] + ([np.asarray(extra, float)] if extra else []))
    T = np.concatenate(tris)
    lut = np.vectorize(out_idx.__getitem__, otypes=[np.int64])
    T = lut(T)
    p = Vo[T]
    sa = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
          - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
    flip = sa < 0
    T[flip] = T[flip][:, [0, 2, 1]]
    if np.any(np.abs(sa) <= 1e-14):
        raise GmshCDTError("gmsh produced %d zero-area triangle(s)"
                           % int(np.sum(np.abs(sa) <= 1e-14)))
    if budget and len(T) > budget:
        raise MeshBudgetExceeded(
            "mesh budget: {} triangles in one cell exceed the {}-triangle "
            "budget; rejected before any FEM time is spent."
            .format(len(T), int(budget)))
    return Vo, T
