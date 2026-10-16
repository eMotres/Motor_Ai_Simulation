"""Conducting bodies BISECTED by the sector boundary — the exact constraint.

A sector model solves the wedge 0 ≤ θ ≤ φ = 2π/NS with the (anti)periodic
condition A(θ + φ) = s·A(θ) (s = −1 anti-periodic, +1 periodic).  A magnet
that straddles the cut at θ = 0 appears in the wedge TWICE, as two meshed
bodies:

* its 0⁺ piece, just above the ray θ = 0 — the physical piece;
* a piece just below the ray θ = φ — the image of its OTHER physical piece
  (the one at θ < 0), whose field is s·A of the model's.

Both pieces belong to ONE physical conductor with ONE axial voltage gradient U.
The image piece's physical current density is s·J_model with the model's own
voltage s·U there (the image magnet is the same magnet one sector on, whose
field — and therefore whose U — is s times this one's).  So the physical
magnet's net current is

    ∫_{0⁺} σ(−∂A/∂t + U) dΩ  +  s·∫_{φ⁻} σ(−∂A/∂t + s·U) dΩ  =  I (= 0)

which is ONE bordered row with ONE unknown U, the constraint column
g = g_{0⁺} + s·g_{φ⁻} and S = S_{0⁺} + S_{φ⁻} (s² = 1): the system stays
symmetric and has exactly the shape of any other body's row.  The earlier
treatment (U ≡ 0 on both pieces, no row) is NOT this: it fixes U instead of
the net current, and is exact only for a closed RING (shaft, sleeve), whose
single piece is its own image and whose U must vanish by the symmetry.

No machine in the catalogue has a bisected magnet today ("0 edge halves" on
every duty); tests/test_cut_bodies.py proves the pairing on a synthetic ring
against the full-ring solve.  docs/EDDY_TIME_INTEGRATION_2026-09-25.md §4.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def _ray_dist(p: np.ndarray, ang: float) -> np.ndarray:
    """Distance of each node (2, N) [m] from the ray at angle ``ang`` through
    the origin (points behind the origin count as far)."""
    c, s = math.cos(ang), math.sin(ang)
    along = c * p[0] + s * p[1]
    perp = np.abs(-s * p[0] + c * p[1])
    return np.where(along > 0.0, perp, np.inf)


def body_ray_radii(p: np.ndarray, t: np.ndarray, elems: np.ndarray,
                   ang: float, tol: float) -> np.ndarray:
    """Sorted radii of a body's nodes that lie on an element EDGE on the ray
    at ``ang`` (a body merely touching the ray at one vertex is not cut)."""
    tri = np.asarray(t, int)[:, np.asarray(elems, int)]
    on = _ray_dist(p, ang) < tol
    rr = []
    for i, j in ((0, 1), (1, 2), (2, 0)):
        a, b = tri[i], tri[j]
        m = on[a] & on[b]
        if np.any(m):
            rr.append(np.hypot(p[0, a[m]], p[1, a[m]]))
            rr.append(np.hypot(p[0, b[m]], p[1, b[m]]))
    if not rr:
        return np.zeros(0)
    return np.unique(np.round(np.concatenate(rr), 12))


def pair_bisected_bodies(p: np.ndarray, t: np.ndarray,
                         bodies: Sequence[np.ndarray], n_sectors: int,
                         tol: float,
                         is_partial: Optional[Sequence[bool]] = None
                         ) -> Tuple[List[Tuple[int, int]], Dict]:
    """Pair the two pieces of every conductor bisected by the sector cut.

    ``bodies``: element-index arrays of the candidate bodies (magnets), on the
    wedge 0 ≤ θ ≤ 2π/n_sectors whose nodes are ``p`` (2, N) [m], elements
    ``t`` (3, E).  ``is_partial[b]`` (optional) says whether body b is LESS
    than its CAD outline (the mesh holds only part of it); without it every
    body with an edge on a cut ray is a candidate.

    A body with an edge on the ray θ = 0 pairs with a body with an edge on the
    ray θ = φ when their on-ray node radii coincide within ``tol`` — the cut
    is meshed clone-identically on both rays, so the two faces of one cut
    magnet share their radii exactly.  Returns ``(pairs, info)`` with pairs
    ``(i_on_0, j_on_phi)``; ``info`` lists touching-but-unpaired bodies.
    """
    ns = int(n_sectors)
    info: Dict = {"n_candidates": 0, "unpaired": []}
    if ns <= 1 or not len(bodies):
        return [], info
    phi = 2.0 * math.pi / ns
    r0 = [body_ray_radii(p, t, e, 0.0, tol) for e in bodies]
    r1 = [body_ray_radii(p, t, e, phi, tol) for e in bodies]
    part = (list(is_partial) if is_partial is not None
            else [True] * len(bodies))
    c0 = [i for i in range(len(bodies)) if r0[i].size >= 2 and part[i]]
    c1 = [j for j in range(len(bodies)) if r1[j].size >= 2 and part[j]]
    info["n_candidates"] = len(set(c0) | set(c1))
    pairs: List[Tuple[int, int]] = []
    used = set()
    for i in c0:
        best = None
        for j in c1:
            if j in used or j == i or r1[j].size != r0[i].size:
                continue
            d = float(np.max(np.abs(r1[j] - r0[i])))
            if d < tol and (best is None or d < best[0]):
                best = (d, j)
        if best is not None:
            pairs.append((i, best[1]))
            used.add(i); used.add(best[1])
    info["unpaired"] = sorted((set(c0) | set(c1)) - used)
    return pairs, info


def match_outlines(p: np.ndarray, t: np.ndarray,
                   bodies: Sequence[np.ndarray], outlines_mm: Sequence,
                   tags: Optional[Sequence[int]] = None,
                   tag_base: int = 0) -> List:
    """The CAD outline (a shapely Polygon, mm) each meshed body came from.

    A body is matched to the outline that CONTAINS its area centroid; the
    outline at index ``tag − tag_base`` is tried first (the mesher tags
    magnet j as ``DOM_MAG_BASE + j``).  A body cut by the sector boundary
    gets its WHOLE outline — the physical block.  Unmatched → None."""
    from shapely.geometry import Point
    P = np.asarray(p, float); T = np.asarray(t, int)
    x, y = P[0][T], P[1][T]
    area = 0.5 * np.abs((x[1] - x[0]) * (y[2] - y[0])
                        - (x[2] - x[0]) * (y[1] - y[0]))
    out = []
    ol = list(outlines_mm or [])
    for bi, e in enumerate(bodies):
        e = np.asarray(e, int)
        w = area[e]
        cx = float((x[:, e].mean(axis=0) * w).sum() / max(w.sum(), 1e-300))
        cy = float((y[:, e].mean(axis=0) * w).sum() / max(w.sum(), 1e-300))
        pt = Point(cx * 1e3, cy * 1e3)
        j0 = (int(tags[bi]) - int(tag_base)) if tags is not None else -1
        cand = ([j0] if 0 <= j0 < len(ol) else []) + [
            j for j in range(len(ol)) if j != j0]
        hit = None
        for j in cand:
            g = ol[j]
            if g is None:
                continue
            for part in getattr(g, "geoms", [g]):
                if part.buffer(1e-6).contains(pt):
                    hit = part
                    break
            if hit is not None:
                break
        out.append(hit)
    return out


def meshed_area_m2(p: np.ndarray, t: np.ndarray, elems: np.ndarray) -> float:
    P = np.asarray(p, float); T = np.asarray(t, int)[:, np.asarray(elems, int)]
    x, y = P[0][T], P[1][T]
    return float(np.sum(0.5 * np.abs((x[1] - x[0]) * (y[2] - y[0])
                                     - (x[2] - x[0]) * (y[1] - y[0]))))


def merge_pair_constraint(g0: np.ndarray, S0: float, g1: np.ndarray,
                          S1: float, bc_sign: int) -> Tuple[np.ndarray, float]:
    """The ONE bordered row of a bisected conductor (see the module doc):
    g = g_{0⁺} + s·g_{φ⁻},  S = S_{0⁺} + S_{φ⁻}."""
    s = -1.0 if int(bc_sign) < 0 else 1.0
    return np.asarray(g0, float) + s * np.asarray(g1, float), float(S0) + float(S1)
