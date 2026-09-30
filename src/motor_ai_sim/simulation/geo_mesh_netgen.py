# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) MOTRES d.o.o. and contributors
"""Netgen backend for the geometry-driven mesher's constrained triangulation.

Netgen (NGSolve project, LGPL-2.1, pip package `netgen-mesher`) is the third
CDT backend next to Triangle and gmsh (docs/MESHER_NETGEN_2026-09-30.md).  Being
LGPL it can run in-process next to Intel MKL/PARDISO under any project licence.
Like `geo_mesh_gmsh.triangulate_gmsh` it replaces exactly ONE call of the
geometry mesher, `geo_mesh._triangulate`: the quality triangulation of the
planar straight-line graph (PSLG) that `geo_mesh.py` builds itself.  Every
other feature (skin layers, per-part sizes, wire cells, cusp/sliver guards,
tiling, tagging, moving-band rings, mesh budget) is shared code.

Contract (the same as the gmsh backend and Triangle `-Y`):

* the returned vertex array STARTS with the input vertices, in input order
  (unused ones included; `geo_mesh_halves` prunes them), bit-exact;
* no point is inserted on a DOMAIN-BOUNDARY segment (cut rays, gap rings, rims
  of the structured skin/wire patches, hole outlines, embedded dangling
  segments): each is an OCC edge with an EMPTY netgen partition, so its two
  end vertices are its only mesh nodes.  INTERIOR segments (material
  interfaces between two meshed faces) get a fixed uniform partition at their
  target size;
* free PSLG vertices inside a face (wire lattices, the iron grading next to
  the shaft skin) are mesh nodes: they are glued into the face as internal
  vertices, and so are dangling segments;
* holes are the faces holding a hole marker; region seeds set a per-region
  maximum cell edge (last seed inside a face wins); a face without a seed has
  no cap;
* triangles are counter-clockwise.

Why OCC and not `netgen.geom2d`: the spline geometry has no way to keep a
segment whole.  Its 1-D partition re-divides every spline by the local mesh
size, which a finer neighbour or a domain cap drives below the segment length
(measured on the 40 mm stator cell: 54 of 278 boundary segments split at the
default grading, 36 still at grading 10).  The OCC geometry takes an explicit
per-edge partition (`Edge.partition`, normalized curve parameters in (0, 1)),
which netgen honours exactly.

Sizing: the 1-D mesh is fixed as above; netgen's 2-D advancing front then
grades the interior from that boundary with `grading` (h may grow by about
`grading` x the distance) under the per-face caps (`Face.maxh`).  The
per-segment target of an interior interface is the gmsh backend's
(`geo_mesh_gmsh._segment_sizes`: min(length, 2.5 x local feature size), never
finer than a frozen segment facing it), capped by the adjacent faces' targets.

Determinism: one thread (`parallel_meshing=False`), a fixed build order, and a
process lock; the same PSLG gives the same mesh node for node.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

#: The netgen-mesher release this backend was validated on
#: (docs/MESHER_NETGEN_2026-09-30.md).  Another release still runs; provenance
#: flags it.
NETGEN_VALIDATED = "6.2.2607"
# Netgen grading (h grows by about GRADING x distance from the boundary).
# Measured on the production builds of L12, L13 @ 0.61 mm and L155
# (docs/MESHER_NETGEN_2026-09-30.md): 0.3 (netgen's default) gives 1.15-1.35x
# gmsh's element count, 0.5 the same count as gmsh (0.97-1.03x) with a better
# worst angle, 1.0 fewer elements but rotor slivers (0.9 deg on L12).
_GRADING = 0.5
_LOCK = threading.Lock()


class NetgenCDTError(RuntimeError):
    """The netgen backend could not triangulate the PSLG (loud, never silent)."""


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return v if v > 0 else default


def netgen_version() -> Optional[str]:
    try:
        import netgen
        return str(getattr(netgen, "__version__", "?"))
    except Exception:  # noqa: BLE001
        return None


def triangulate_netgen(V, S, area: float, hole_pts=None, regions=None,
                       budget: Optional[int] = None
                       ) -> Tuple[np.ndarray, np.ndarray]:
    """Constrained quality triangulation of the PSLG (V mm, S index pairs).

    See the module docstring for the contract.  `budget` (triangles) arms the
    optimizer fence: a predicted or actual count above it raises
    geo_mesh.MeshBudgetExceeded before/after meshing."""
    import netgen.meshing as ngm
    import netgen.occ as occ
    from shapely.geometry import Point
    from shapely.geometry.polygon import orient
    from shapely.prepared import prep
    from motor_ai_sim.simulation.geo_mesh import MeshBudgetExceeded
    # The PSLG analysis is the gmsh backend's (pure shapely/numpy; gmsh itself
    # is imported only inside triangulate_gmsh).
    from motor_ai_sim.simulation.geo_mesh_gmsh import (
        _EQ, _LFS_K, _edge_of_area, _faces, _feature_sizes, _ring_use_counts,
        _segment_sizes)

    V = np.asarray(V, float).reshape(-1, 2)
    S = np.asarray(S, np.int64).reshape(-1, 2)
    S = S[S[:, 0] != S[:, 1]]
    S = np.unique(np.sort(S, axis=1), axis=0)
    nV = len(V)
    holes = np.zeros((0, 2)) if hole_pts is None else \
        np.asarray(hole_pts, float).reshape(-1, 2)
    grading = _env_float("SB_NETGEN_CDT_GRADING", _GRADING)
    K = _env_float("SB_GMSH_CDT_LFS_K", _LFS_K)

    try:
        faces = _faces(V, S)
    except Exception as e:  # noqa: BLE001 — GmshCDTError from the shared helper
        raise NetgenCDTError(str(e)) from e
    if not faces:
        raise NetgenCDTError("PSLG encloses no face")
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

    # budget PREFLIGHT, before any netgen work (the gmsh backend's rule)
    pred = sum(faces[i].area / (_EQ * tgt[i] ** 2)
               for i in range(len(faces)) if keep[i] and tgt[i])
    _t0 = time.time()
    if budget and pred > 2.0 * budget:
        raise MeshBudgetExceeded(
            "mesh budget: this cross-section needs ~{:.0f} triangles at its "
            "target sizes (budget {}); rejected before meshing."
            .format(pred, int(budget)))

    key = {(float(x), float(y)): i for i, (x, y) in
           reversed(list(enumerate(V)))}          # first index wins on dups
    seg_id = {(int(a), int(b)): k for k, (a, b) in enumerate(S)}
    L = np.linalg.norm(V[S[:, 0]] - V[S[:, 1]], axis=1)
    lfs, near = _feature_sizes(V, S)
    uses = _ring_use_counts(S, faces, keep, key, seg_id)
    frozen = uses != 2
    h_seg = _segment_sizes(L, lfs, near, frozen, K)

    def _vid(c):
        i = key.get((float(c[0]), float(c[1])))
        if i is None:
            raise NetgenCDTError("face ring vertex (%.6f, %.6f) is not a PSLG "
                                 "vertex" % (c[0], c[1]))
        return i

    with _LOCK:
        try:
            ngm.SetMessageImportance(0)
            vx: Dict[int, object] = {}
            ed: Dict[int, object] = {}
            side_tgt: Dict[int, List[float]] = {}

            def _VX(i):
                v = vx.get(i)
                if v is None:
                    v = occ.Vertex(occ.gp_Pnt(float(V[i, 0]), float(V[i, 1]), 0.0))
                    vx[i] = v
                return v

            def _ED(k):
                e = ed.get(k)
                if e is None:
                    e = occ.Edge(_VX(int(S[k, 0])), _VX(int(S[k, 1])))
                    ed[k] = e
                return e

            occ_faces = []
            kept = []
            for i, f in enumerate(faces):
                if not keep[i]:
                    continue
                fo = orient(f, 1.0)          # exterior CCW, interiors CW
                wires = []
                for ring in [fo.exterior] + list(fo.interiors):
                    c = list(ring.coords)
                    es = []
                    for j in range(len(c) - 1):
                        a, b = _vid(c[j]), _vid(c[j + 1])
                        k = seg_id.get((min(a, b), max(a, b)))
                        if k is None:
                            raise NetgenCDTError("face ring edge %d-%d is not a "
                                                 "PSLG segment" % (a, b))
                        if tgt[i]:
                            side_tgt.setdefault(k, []).append(float(tgt[i]))
                        e = _ED(k)
                        es.append(e if a < b else e.Reversed())
                    wires.append(occ.Wire(es))
                F = occ.Face(wires[0])
                if len(wires) > 1:
                    F0 = F
                    F = occ.Face(F0, wires)
                    if abs(F.mass - f.area) > 1e-6 * f.area:
                        F = occ.Face(F0, [wires[0]] + [w.Reversed() for w in wires[1:]])
                if abs(F.mass - f.area) > 1e-6 * f.area:
                    raise NetgenCDTError("face %d: the OCC face (area %.6g) does "
                                         "not match the PSLG face (area %.6g)"
                                         % (i, F.mass, f.area))
                F.name = "f%d" % i
                if tgt[i]:
                    F.maxh = float(tgt[i])
                occ_faces.append(F)
                kept.append(i)
            # 1-D mesh fixed here: boundary segments whole, interfaces uniform
            npiece = 0
            for k, e in ed.items():
                n = 1
                if not frozen[k]:
                    h = min([float(h_seg[k])] + side_tgt.get(k, []))
                    n = max(1, int(math.ceil(L[k] / h - 1e-9)))
                e.partition = np.array([j / n for j in range(1, n)], float)
                npiece += n
            # dangling segments and free vertices inside a kept face: glued
            # in as internal edges/vertices (Triangle and gmsh keep them too)
            extra = []
            n_dangle = 0
            for k in range(len(S)):
                if k in ed:
                    continue
                m = Point(0.5 * (V[S[k, 0]] + V[S[k, 1]]))
                if any(pf[i].contains(m) for i in kept):
                    e = _ED(k)
                    e.partition = np.array([], float)
                    extra.append(e)
                    n_dangle += 1
            on_seg = np.zeros(nV, bool)
            on_seg[S.ravel()] = True
            n_free = 0
            free_in = []
            for v in np.where(~on_seg)[0]:
                pt = Point(V[v])
                if any(pf[i].contains(pt) for i in kept):
                    extra.append(_VX(int(v)))
                    free_in.append(int(v))
                    n_free += 1
            shape = occ.Compound(occ_faces)
            if extra:
                shape = occ.Glue([shape] + extra)
            geo = occ.OCCGeometry(shape, dim=2)
            try:
                mesh = geo.GenerateMesh(maxh=1e6, grading=grading,
                                        parallel_meshing=False)
            except Exception as e:  # noqa: BLE001 — re-raised loudly
                raise NetgenCDTError("netgen failed to mesh the PSLG (%d vertices, "
                                     "%d segments, %d faces): %s"
                                     % (nV, len(S), len(occ_faces), e)) from e
            P = np.array([[p[0], p[1]] for p in mesh.Points()], float)
            els = mesh.Elements2D()
            T = np.empty((len(els), 3), np.int64)
            fidx = np.empty(len(els), np.int64)
            for n_, el in enumerate(els):
                vs = el.vertices
                if len(vs) != 3:
                    raise NetgenCDTError("netgen produced a non-triangle element "
                                         "(%d nodes)" % len(vs))
                T[n_] = (vs[0].nr - 1, vs[1].nr - 1, vs[2].nr - 1)
                fidx[n_] = el.index
            mat = {int(ix): mesh.GetMaterial(int(ix)) for ix in np.unique(fidx)}
        finally:
            try:
                occ.ResetGlobalShapeProperties()
            except Exception:  # noqa: BLE001
                pass

    if not len(T):
        raise NetgenCDTError("netgen produced no triangle")
    # every kept face meshed in full (netgen reports a failed face on stdout
    # only, so the areas are the check)
    p = P[T]
    sa = 0.5 * ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
    got: Dict[int, float] = {}
    for ix, nm in mat.items():
        try:
            fi = int(str(nm).lstrip("f"))
        except ValueError:
            raise NetgenCDTError("netgen returned an unknown face %r" % nm)
        got[fi] = got.get(fi, 0.0) + float(np.abs(sa[fidx == ix]).sum())
    for i in kept:
        want = faces[i].area
        if abs(got.get(i, 0.0) - want) > 1e-7 * max(want, 1e-12) + 1e-12:
            raise NetgenCDTError("netgen left face %d incompletely meshed "
                                 "(meshed %.6g of %.6g mm^2)"
                                 % (i, got.get(i, 0.0), want))

    # input vertices first, bit-exact: map each netgen node back to its input
    # vertex (netgen may return a vertex 1 ulp off after the OCC round trip)
    from scipy.spatial import cKDTree
    scale = float(np.abs(V).max()) if nV else 1.0
    tol = 1e-9 * max(scale, 1.0)
    d, j = cKDTree(P).query(V)
    out_idx = np.full(len(P), -1, np.int64)
    for i in range(nV):                      # first input index wins on dups
        if d[i] <= tol and out_idx[j[i]] < 0:
            out_idx[j[i]] = i
    need = np.zeros(nV, bool)                # vertices that must be nodes
    for k in ed:
        need[S[k]] = True
    need[free_in] = True
    miss = np.where(need & (d > tol))[0]
    if len(miss):
        raise NetgenCDTError("%d input vertices are not mesh nodes (first at "
                             "(%.6f, %.6f))" % (len(miss), V[miss[0], 0], V[miss[0], 1]))
    rest = np.where(out_idx < 0)[0]
    out_idx[rest] = nV + np.arange(len(rest))
    Vo = np.vstack([V, P[rest]])
    To = out_idx[T]
    # the contract: every boundary segment is a mesh edge (never split)
    E = np.sort(np.vstack([To[:, [0, 1]], To[:, [1, 2]], To[:, [0, 2]]]), axis=1)
    Es = set(map(tuple, E.tolist()))
    split = [k for k in ed if frozen[k] and (int(S[k, 0]), int(S[k, 1])) not in Es]
    if split:
        raise NetgenCDTError("netgen split %d boundary segment(s) (first %d-%d)"
                             % (len(split), S[split[0], 0], S[split[0], 1]))
    q = Vo[To]
    sa = ((q[:, 1, 0] - q[:, 0, 0]) * (q[:, 2, 1] - q[:, 0, 1])
          - (q[:, 2, 0] - q[:, 0, 0]) * (q[:, 1, 1] - q[:, 0, 1]))
    flip = sa < 0
    To[flip] = To[flip][:, [0, 2, 1]]
    if np.any(np.abs(sa) <= 1e-14):
        raise NetgenCDTError("netgen produced %d zero-area triangle(s)"
                             % int(np.sum(np.abs(sa) <= 1e-14)))
    log.info("netgen CDT: %d faces, %d segments (%d pieces, %d embedded, %d free "
             "pts), predicted >= %.0f tris, built %d in %.2f s", len(kept), len(S),
             npiece, n_dangle, n_free, pred, len(To), time.time() - _t0)
    if budget and len(To) > budget:
        raise MeshBudgetExceeded(
            "mesh budget: {} triangles in one cell exceed the {}-triangle "
            "budget; rejected before any FEM time is spent."
            .format(len(To), int(budget)))
    return Vo, To
