"""Geometry-driven FEM mesh: triangulate the REAL CadQuery polygons (every
fillet) with a constrained Delaunay (Shewchuk's Triangle) instead of building
an idealised tensor template and patching it to the geometry.

The boundaries fed to Triangle ARE the real geometry, so the mesh conforms to
every fillet (magnet corners, tooth-tip r1, V-notch apex, OD / slot-bottom
roundings) by construction; per-triangle domain tags are then an EXACT
centroid point-in-polygon test (no staircase).

Proven recipe (see the sliver investigation, 2026-07-15):
  1. resample every gap-/shaft-facing arc onto the UNIFORM slip grid
     (fem_solver_2d._resample_ring_arcs) so the iron's ring points and the
     bore/OD/shaft circle points coincide bit-for-bit — kills the "double arc"
     slivers and lets the belt weld by node identity;
  2. HOMOGENISE the winding: one clean copper block per slot (area = the slot's
     copper area) instead of 336 sub-micron wire rectangles + insulation — the
     solver's J_z is uniform per slot anyway (I·n_wires/slot_area), so the wire
     detail carries no physics and only spawns slivers;
  3. magnets fed as-is (clean fillet polygons, disjoint from steel);
  4. snap all coords to 1um, node with shapely.unary_union, triangulate with
     `pq<angle>a<area>` — with (1)+(2) the quality flag no longer explodes and
     gives AR_max < ~5 (stator) / ~100 (rotor bridge) at ~50-60k tris/half.

Deterministic: Triangle is deterministic for a fixed PSLG + options, and the
PSLG is derived deterministically from the polygons.
"""
from __future__ import annotations
import logging
import math
import os
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

import numpy as np

# DOM_* tags — must match iron_template / fem_solver_2d.
DOM_AIR, DOM_STATOR, DOM_ROTOR, DOM_SHAFT, DOM_OUTER = 0, 1, 5, 6, 8
DOM_MAG_BASE, DOM_COIL_BASE = 100, 200

_SNAP = 1e-3          # snap coords to 1 um (kills sub-um CAD slivers)
# Slot/pole cell tiling (periodic iron mesh): default ON, SB_GEO_TILE=0 reverts
# to the whole-wedge CDT (two unique seams → broadband torque noise).
_SB_GEO_TILE = os.environ.get("SB_GEO_TILE", "1").lower() not in ("0", "false")
_Q = 20               # Triangle min-angle quality (deg) — 20 keeps clean AR
                      # without the aggressive corner over-refinement q28 caused
                      # (dense fans at fillets/bridges); also safer on thin coils.
_ROTOR_STEINER_CAP = 6000  # cap Triangle's Steiner points on the rotor so a sharp
                      # magnet corner near the OD bridge can't blow q up to 1 M tris
_ROTOR_AR_GATE = 200
_ROTOR_AR_BULK = 25
_ROTOR_DEFEATURE_MM = 0.06  # morphological-opening radius (mm) for the rotor
                            # iron: trims knife-edge slivers thinner than ~2eps
                            # (e.g. a 0-fillet magnet corner meeting the OD) that
                            # make Triangle's q-refinement explode.  0.06 mm is
                            # 5x below the smallest element (min 0.3 mm) and far
                            # below any flux-relevant feature; the trimmed sliver
                            # becomes air.  Standard CAE defeaturing.   # p99.5 aspect of the capped-q rotor mesh at/below this →
                      # the BULK is stator-grade and only the unavoidable
                      # knife-edge corner slivers broke the max-AR gate: keep
                      # the quality mesh + smooth the slivers instead of
                      # remeshing the whole rotor area-only (rough texture)  # if the capped-q rotor mesh is worse than this max aspect
                      # ratio, it's a genuinely singular knife-edge → area-only
_DEFAULT_N_SLIP = 1008


# ── low-level helpers ────────────────────────────────────────────────────────
def _snap_ring(coords) -> np.ndarray:
    """Round a ring to the snap grid and drop consecutive duplicates."""
    a = np.round(np.asarray(coords, float) / _SNAP) * _SNAP
    keep = [0] + [i for i in range(1, len(a)) if not np.allclose(a[i], a[i - 1])]
    return a[keep]


def _densify(coords, max_edge: float) -> np.ndarray:
    """Insert points along each segment so no edge exceeds `max_edge` (mm) — the
    input vertices (corners) are kept EXACTLY, so geometry is unchanged.

    A boundary edge must be ~ the mesh size, else CDT stitches a long chord (a
    magnet's flat top) to the fine slip-grid OD ring across the 1.3 mm iron
    bridge with sliver triangles that wreck the near-gap field (macro ripple
    12→25 % at a sharp corner).  Densifying the magnet outline makes the bridge a
    clean structured strip for ANY corner radius, with no geometry fudge."""
    pts = np.asarray(coords, float)
    if len(pts) < 2 or max_edge <= 0:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(d / max_edge)))
        for k in range(1, n):
            out.append(a + (b - a) * (k / n))
        out.append(b)
    return np.asarray(out, float)


def _grid_circle(r: float, n: int) -> np.ndarray:
    """Closed circle polyline on the UNIFORM angular grid 2*pi*k/n (k=0..n-1)
    — identical grid the sliding band uses, so nodes coincide with the belt."""
    t = np.arange(n) * (2.0 * math.pi / n)
    c = np.c_[r * np.cos(t), r * np.sin(t)]
    return np.vstack([c, c[:1]])


def _radius_span(geom) -> Tuple[float, float]:
    """(min, max) vertex radius over a (Multi)Polygon's exteriors."""
    rmin, rmax = math.inf, 0.0
    for gg in getattr(geom, "geoms", [geom]):
        if getattr(gg, "area", 0.0) < 1e-9:
            continue
        xy = np.asarray(gg.exterior.coords)
        r = np.hypot(xy[:, 0], xy[:, 1])
        rmin = min(rmin, float(r.min())); rmax = max(rmax, float(r.max()))
    return rmin, rmax


def _resample(geom, r_ring: float, n_grid: int):
    """Snap every boundary run on r≈r_ring onto the uniform n_grid — reuses the
    solver's own routine so the geo mesh and the belt agree bit-for-bit."""
    from motor_ai_sim.simulation.fem_solver_2d import _resample_ring_arcs
    return _resample_ring_arcs(geom, r_ring, n_grid)


def _build_pslg(lines, merge_tol: float = 0.006) -> Tuple[np.ndarray, np.ndarray]:
    """Node a set of LineStrings into a valid PSLG (unique vertices, unique
    segments, T-junctions resolved by shapely).  Returns (V n*2, S m*2).

    Sub-`merge_tol` (mm) vertex pairs are welded: sharp geometry corners can land
    a fraction of a micron from a resampled node, leaving a ~0.5 µm segment that
    Triangle's angle (`q`) refinement then subdivides toward infinity (1.1 M tris
    on a magnet with no corner fillet).  The 6 µm weld kills those degenerate
    stubs; real features (slip ring ~157 µm pitch, 130 µm wire gaps, fillet arc
    segments ~200 µm) are far larger, so nothing physical — and no gap-ring node —
    is disturbed."""
    from shapely.ops import unary_union
    noded = unary_union(lines)
    vmap: Dict[Tuple[float, float], int] = {}
    verts: List[List[float]] = []
    segs = set()

    def vid(x, y):
        k = (round(x, 4), round(y, 4))
        i = vmap.get(k)
        if i is None:
            i = len(verts); vmap[k] = i; verts.append([x, y])
        return i

    for ls in getattr(noded, "geoms", [noded]):
        xy = np.asarray(ls.coords)
        idx = [vid(x, y) for x, y in xy]
        for a, b in zip(idx[:-1], idx[1:]):
            if a != b:
                segs.add((min(a, b), max(a, b)))
    V = np.array(verts, float)
    S = np.array(sorted(segs), np.int64)
    if len(V) > 1 and len(S):
        from scipy.spatial import cKDTree
        pairs = cKDTree(V).query_pairs(merge_tol)
        if pairs:                                   # union-find weld toward min id
            parent = list(range(len(V)))
            def _find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]; i = parent[i]
                return i
            for a, b in pairs:
                ra, rb = _find(int(a)), _find(int(b))
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
            roots = np.array([_find(i) for i in range(len(V))])
            uniq, inv = np.unique(roots, return_inverse=True)   # inv[old] = new id
            V = V[uniq]                                         # keep min-id rep
            S = inv[S]                                          # remap segments
            S = S[S[:, 0] != S[:, 1]]                           # drop collapsed
            if len(S):
                S = np.array(sorted({(min(int(a), int(b)), max(int(a), int(b)))
                                     for a, b in S}), np.int64)
    return V, S


def _repair_slivers(V, T, n_fixed: int, n_iter: int = 20,
                    in_V=None, in_S=None):
    """Raise triangle quality of an area-only CDT by Laplacian-smoothing the
    FREE interior nodes (guarded against inversion).

    Area-only meshing (no `q`) always builds — even for a razor-sharp magnet
    corner at the OD bridge that would explode `q` refinement — but leaves a few
    slivers.  We repair them the right way (move nodes, don't touch geometry):
    a node is FREE only if it is (a) a Triangle-inserted interior Steiner point
    (index ≥ n_fixed), (b) not on a mesh boundary edge, AND (c) not ON any input
    PSLG segment (pass in_V/in_S).  (c) is what keeps MATERIAL INTERFACES exact:
    a Steiner point that Triangle inserted ON a magnet/iron outline is shared by
    two domains — the outer-boundary test alone missed those, the smoother
    dragged them off the outline and the per-centroid tagging then rendered the
    magnets as jagged staircases.  With the segment pin, only nodes strictly
    inside one material relax.  Each move is rolled back if it would flip an
    incident triangle, so the mesh stays valid and conforming for ANY magnet
    corner radius."""
    V = np.asarray(V, float).copy()
    nV = len(V)
    if nV <= n_fixed or len(T) == 0:
        return V, T
    from collections import defaultdict
    edge_cnt = defaultdict(int)
    nbr = defaultdict(set)
    inc = defaultdict(list)
    for ti, (a, b, c) in enumerate(T):
        for u, v in ((a, b), (b, c), (a, c)):
            edge_cnt[(min(u, v), max(u, v))] += 1
        nbr[a].update((b, c)); nbr[b].update((a, c)); nbr[c].update((a, b))
        inc[a].append(ti); inc[b].append(ti); inc[c].append(ti)
    on_bnd = np.zeros(nV, bool)
    for (u, v), n in edge_cnt.items():
        if n == 1:
            on_bnd[u] = on_bnd[v] = True
    if in_V is not None and in_S is not None and len(in_S):
        # pin Steiner nodes sitting ON an input segment (material interfaces)
        iv = np.asarray(in_V, float)
        A = iv[np.asarray(in_S)[:, 0]]          # (m,2) segment starts
        B = iv[np.asarray(in_S)[:, 1]]          # (m,2) segment ends
        AB = B - A
        L2 = np.maximum((AB * AB).sum(axis=1), 1e-18)
        cand = np.arange(n_fixed, nV)
        for i in cand:
            if on_bnd[i]:
                continue
            P = V[i]
            t = np.clip(((P - A) * AB).sum(axis=1) / L2, 0.0, 1.0)
            proj = A + t[:, None] * AB
            d2 = ((P - proj) ** 2).sum(axis=1)
            if d2.min() < (1e-3) ** 2:          # within 1 µm of a segment → pin
                on_bnd[i] = True
    free = [i for i in range(n_fixed, nV) if not on_bnd[i] and nbr[i]]
    if not free:
        return V, T

    def _neg(i):                      # any incident triangle non-positive?
        for t in inc[i]:
            a, b, c = T[t]
            if ((V[b, 0]-V[a, 0])*(V[c, 1]-V[a, 1])
                    - (V[c, 0]-V[a, 0])*(V[b, 1]-V[a, 1])) <= 1e-11:
                return True
        return False

    nbr_arr = {i: np.fromiter(nbr[i], int) for i in free}
    for _ in range(n_iter):
        for i in free:
            old0, old1 = V[i, 0], V[i, 1]
            c = V[nbr_arr[i]].mean(axis=0)
            V[i, 0], V[i, 1] = c[0], c[1]
            if _neg(i):
                V[i, 0], V[i, 1] = old0, old1
    return V, T


def _triangulate(V, S, area: float, quality: int = _Q, hole: bool = True,
                 regions=None, hole_pts=None, no_bnd_steiner: bool = False,
                 rotor_bridge: bool = False):
    """CDT of the region.  `hole=True` puts a marker at the origin so the inner
    disk is emptied (stator half: the bore opens onto the rotor space);
    `hole=False` meshes solid to the centre (rotor half: the shaft is a real
    DOM_SHAFT region, exactly as the tensor template treats it).  `hole_pts`
    (N*2) overrides with explicit hole markers — a sector uses one inside the
    removed inner disk, at the wedge mid-angle rather than the origin.

    `regions` (N*4 [x, y, marker, max_area]) enables PER-REGION target areas —
    iron/coils/magnets stay fine while the open air (outer air, shaft, slot
    pockets) is coarsened to its own size.  Every region MUST be seeded (the
    'Aa' flags impose no global cap), so callers derive the seeds from the real
    polygons.  Without regions, a single global max-area is used."""
    import triangle as _tri
    A = dict(vertices=V, segments=S)
    if hole_pts is not None:
        A["holes"] = np.asarray(hole_pts, float)
    elif hole:
        A["holes"] = np.array([[0.0, 0.0]])
    # -Y (no_bnd_steiner): forbid Steiner points ON the input boundary/segments.
    # Sector meshes seed the two radial cuts with an IDENTICAL node set; without
    # -Y, Triangle re-splits each cut independently → they diverge → broken
    # anti-periodic weld.  Interior points are still inserted to meet area/quality.
    _Y = "Y" if no_bnd_steiner else ""
    if regions is not None and len(regions):
        A["regions"] = np.asarray(regions, float)
        _tail = "Aa"                          # per-region areas from column 4
    else:
        _tail = f"a{area:.4f}"

    if rotor_bridge:
        # The rotor magnet corner may be ANY radius, 0 (razor-sharp) included, and
        # the mesh MUST build.  A sharp corner 1.3 mm from the OD is a knife-edge
        # iron wedge — a near-0° input angle that Triangle's `q` refinement blows
        # up on (0.5–1.1 M tris).  Strategy that is correct, not a fudge:
        #   1. try `q` with a Steiner CAP (bounds the blow-up) — on a real fillet
        #      it converges to a high-quality mesh → accurate near-gap field →
        #      honest, step-independent macro ripple (area-only slivers do NOT:
        #      they inject spurious field harmonics, ripple 12→19 % vs 15→16 %);
        #   2. if the capped result is still sliver-ridden (AR over the gate — a
        #      genuinely singular knife-edge), fall back to area-only + smoothing:
        #      fewer tris, the SAME unavoidable corner sliver, always valid.
        # Area-only baseline first (cheap, always builds) — its count is the
        # honest size target; a q-mesh may only be accepted within a bounded
        # BUDGET of it, else the knife-edge corner cascades tiny triangles
        # through the magnets (measured: 420 → 25 000 magnet tris).
        out0 = _tri.triangulate(A, f"p{_tail}{_Y}")
        V0 = np.asarray(out0["vertices"], float)
        T0 = np.asarray(out0["triangles"], np.int64)
        _budget = len(T0) + 2 * _ROTOR_STEINER_CAP + 500
        _q0 = int(quality) if quality is not None else _Q
        for _qq in (_q0,):        # q20 only — q10/q5 'pass' but with skinny bulk
            out = _tri.triangulate(
                A, f"pq{_qq}{_tail}S{_ROTOR_STEINER_CAP}{_Y}")
            Vo = np.asarray(out["vertices"], float)
            To = np.asarray(out["triangles"], np.int64)
            _ar, _ = _aspect_arr(Vo, To)
            log.info("rotor q%d: %d tris (budget %d, base %d) ARmax=%.0f p99.5=%.1f",
                     _qq, len(To), _budget, len(T0), float(_ar.max()),
                     float(np.percentile(_ar, 99.5)))
            if len(To) > _budget:              # refinement cascade — too costly
                continue
            if float(_ar.max()) <= _ROTOR_AR_GATE:
                return Vo, To                  # q converged clean → honest field
            # Max-AR broken only by the unavoidable knife-edge slivers while
            # the BULK is stator-grade → take the quality mesh AS IS (rotor
            # teeth then match the stator's q-mesh look).  Do NOT Laplace-smooth
            # it: q-placed nodes are already optimal, and smoothing across the
            # fine→coarse size gradient drags them and RUINS the shapes
            # (measured: median AR 2.9 → 7.6).
            if float(np.percentile(_ar, 99.5)) <= _ROTOR_AR_BULK:
                return Vo, To
        return _repair_slivers(V0, T0, n_fixed=len(V), in_V=V, in_S=S, n_iter=60)

    _q = "" if quality is None else f"q{int(quality)}"
    out = _tri.triangulate(A, f"p{_q}{_tail}{_Y}")
    return (np.asarray(out["vertices"], float),
            np.asarray(out["triangles"], np.int64))


def _air_parts(annulus_poly, iron, embedded):
    """The air sub-regions of `annulus_poly` = annulus minus iron minus the
    embedded solids (coils or magnets) — one shapely part per connected air
    pocket, so each can be seeded with its own (coarse) target area."""
    from shapely.ops import unary_union
    air = annulus_poly.difference(iron)
    if embedded:
        air = air.difference(unary_union(embedded))
    return [g for g in getattr(air, "geoms", [air])
            if getattr(g, "area", 0.0) > 1e-9]


def _aspect_arr(V, T):
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    a = np.linalg.norm(p1 - p0, axis=1)
    b = np.linalg.norm(p2 - p1, axis=1)
    c = np.linalg.norm(p0 - p2, axis=1)
    s = 0.5 * (a + b + c)
    area = np.sqrt(np.maximum(s * (s - a) * (s - b) * (s - c), 0.0))
    longest = np.maximum.reduce([a, b, c])
    ar = longest / np.maximum(2 * area / longest, 1e-12)
    return ar, area


def _aspect_stats(V, T):
    ar, area = _aspect_arr(V, T)
    return float(ar.max()), float(area.min())


def _weld_outline(g, target, tol: float = 0.01):
    """Snap polygon `g` onto `target`'s boundary, then PROJECT any residual
    vertex that still sits 0 < d < tol off the boundary onto it.  shapely's
    snap only moves vertices onto the other chain's VERTICES (within tol) —
    a vertex 4-5 um off a long iron SEGMENT survives it, and that point-to-
    segment offset is exactly the um-zipper that forces a micro-triangle
    cluster (seen at the magnet's TOP corner where the pocket wall meets the
    resampled OD run).  After projection the vertex lies ON the segment, so
    the PSLG noding splits the segment there — one conforming chain."""
    from shapely.geometry import Point, Polygon as _P
    from shapely.ops import snap as _s
    g = _s(g, target, tol)
    bnd = target.boundary if hasattr(target, "boundary") else target
    out = []
    changed = False
    for x, y in list(g.exterior.coords)[:-1]:
        pt = Point(x, y)
        d = bnd.distance(pt)
        if 1e-9 < d < tol:
            pr = bnd.interpolate(bnd.project(pt))
            # prefer an EXISTING target vertex when the projection lands within
            # the PSLG weld tolerance of one — a projected point a few um from
            # a vertex creates a micro-segment after noding (degenerate tris)
            best = None
            bd = 0.008
            for ls in getattr(bnd, "geoms", [bnd]):
                for vx, vy in ls.coords:
                    dv = ((vx - pr.x) ** 2 + (vy - pr.y) ** 2) ** 0.5
                    if dv < bd:
                        bd = dv
                        best = (vx, vy)
            out.append(best if best is not None else (pr.x, pr.y))
            changed = True
        else:
            out.append((x, y))
    if not changed:
        return g
    gg = _P(out, [list(h.coords) for h in g.interiors])
    return gg if gg.is_valid and not gg.is_empty else g


def _air_facing_runs(g, target, tol: float = 0.01):
    """Runs of a (welded) magnet outline that do NOT coincide with the iron
    boundary.  The shared pocket walls already exist in the iron chain with
    ONE sampling; adding the magnet's independently-discretised copy of the
    same wall interleaves two point sets -> 10-30 um noded segments -> ~2 300
    micro-triangles per corner under q20.  Only the magnet's air-facing edges
    are added; the walls are delimited by the iron chain alone."""
    from shapely.geometry import Point
    bnd = target.boundary if hasattr(target, "boundary") else target
    pts = list(g.exterior.coords)
    runs, cur = [], []
    for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
        mid = Point(0.5 * (x1 + x2), 0.5 * (y1 + y2))
        if bnd.distance(mid) > tol:
            if not cur:
                cur = [(x1, y1)]
            cur.append((x2, y2))
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def _defeature_iron(steel):
    """Chord-clip razor corners of the rotor iron: any ring vertex with an
    interior angle < ~15 deg (e.g. a 0-fillet magnet corner meeting the OD)
    is replaced by TWO points 0.25 mm along its edges — one straight cut, no
    arcs, no near-coincident chains (a morphological opening produced those
    and they meshed into zero-area slivers against the magnet outline).  The
    trimmed tip becomes air; 0.25 mm is the smallest honest feature at the
    0.3 mm minimum element.  Runs BEFORE the slip-grid resample."""
    from shapely.geometry import Polygon as _P, MultiPolygon as _MP
    import numpy as _np

    def _clip_ring(coords):
        pts = _np.asarray(coords[:-1], float)      # drop closing dup
        n = len(pts)
        if n < 4:
            return coords
        out = []
        for i in range(n):
            p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
            a = p0 - p1
            b = p2 - p1
            la = float(_np.hypot(*a)); lb = float(_np.hypot(*b))
            if la < 1e-9 or lb < 1e-9:
                out.append(p1); continue
            cosang = float(_np.clip(_np.dot(a, b) / (la * lb), -1.0, 1.0))
            ang = _np.degrees(_np.arccos(cosang))
            if ang < 15.0:                          # razor tip -> chord cut
                d = min(0.25, 0.45 * la, 0.45 * lb)
                out.append(p1 + a / la * d)
                out.append(p1 + b / lb * d)
            else:
                out.append(p1)
        return [tuple(q) for q in out] + [tuple(out[0])]

    try:
        parts = list(getattr(steel, "geoms", [steel]))
        clipped = []
        for g in parts:
            ext = _clip_ring(list(g.exterior.coords))
            ints = [_clip_ring(list(h.coords)) for h in g.interiors]
            gg = _P(ext, ints)
            if not gg.is_valid:
                gg = gg.buffer(0)
            if gg.is_empty:
                return steel
            clipped.append(gg)
        outp = _MP(clipped) if len(clipped) > 1 else clipped[0]
        loss = 1.0 - outp.area / max(steel.area, 1e-12)
        if 0.0 <= loss < 0.005:
            return outp
    except Exception:
        pass
    return steel


def _collapse_slivers(V, T, keep_r=(), area_tol=1e-10, r_guard=0.05):
    """Weld ZERO-AREA sliver triangles by collapsing their shortest edge.

    Near-coincident boundary chains (defeatured iron wall vs the exact magnet
    outline) mesh into zero-area slivers; skfem then assembles 1/area -> inf
    (the 'array must not contain infs or NaNs' crash).  Deleting such a tri
    would leave an unwelded crack — collapsing its shortest edge (merge one
    node into the other) removes the sliver AND welds the chains.  Nodes
    within r_guard (mm) of any radius in keep_r (slip/shaft grid rings, welded
    later BY IDENTITY) are never moved.
    """
    V = np.asarray(V, float).copy()
    T = np.asarray(T, np.int64).copy()
    keep_r = tuple(float(r) for r in keep_r)
    for _ in range(6):
        p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        ar2 = np.abs((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
                     - (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1]))
        bad = np.where(ar2 < area_tol)[0]
        if bad.size == 0:
            break
        rad = np.hypot(V[:, 0], V[:, 1])
        pinned = np.zeros(len(V), bool)
        for r in keep_r:
            pinned |= np.abs(rad - r) < r_guard
        remap = np.arange(len(V))
        for ti in bad:
            a, b, c = T[ti]
            e = [(np.hypot(*(V[u] - V[v])), u, v)
                 for u, v in ((a, b), (b, c), (a, c))]
            e.sort()
            for _, u, v in e:
                u, v = int(remap[u]), int(remap[v])
                if u == v:
                    break                     # already collapsed this round
                if pinned[v] and pinned[u]:
                    continue                  # both protected — try next edge
                if pinned[v]:
                    u, v = v, u               # merge the free node into pinned
                remap[remap == v] = u
                break
        T = remap[T]
        good = ~((T[:, 0] == T[:, 1]) | (T[:, 1] == T[:, 2]) | (T[:, 0] == T[:, 2]))
        T = T[good]
    return V, T


# ── half meshers ─────────────────────────────────────────────────────────────
def _mesh_stator_half(polys: Dict, r_bore: float, r_out_iron: float,
                      r_outer: float, n_slip: int, area: float, air_mm: float,
                      quality: int, r2_band: float = 0.0):
    """(V mm, T) for the stator annulus [r_bore, r_outer].

    The winding is meshed as the REAL CadQuery conductors — the actual
    per-wire rectangles, NOT a synthetic block: the boundaries fed to the CDT
    are the true copper outlines, so the mesh conforms to the winding geometry
    exactly (the slot-air gaps between wires stay air).  The ~1um enamel/liner
    is skipped for the magnetic solve (it is mu_0 = air); the 0.13mm inter-wire
    gaps mesh cleanly, so `q` does not blow up (only the sub-um insulation did).

    Iron and conductors mesh at `area`; every AIR pocket (slot air + the outer
    air ring) gets its own coarse `air_area` seed so the far field isn't meshed
    as finely as the iron."""
    from shapely.geometry import LineString, Polygon
    iron = _resample(polys["stator"], r_bore, n_slip)      # bore → slip grid
    coils = [w for w in (polys.get("coils") or []) if w is not None and not w.is_empty]

    lines = []

    def add(coords):
        r = _snap_ring(coords)
        if len(r) >= 3:
            lines.append(LineString(r))

    for gg in getattr(iron, "geoms", [iron]):
        if getattr(gg, "area", 0.0) < 1e-9:
            continue
        add(gg.exterior.coords)
        for hole in gg.interiors:
            add(hole.coords)
    for w in coils:                                        # REAL conductors
        add(w.exterior.coords)
    air_area = max(area, 0.4330 * air_mm * air_mm)          # coarse air cell
    add(_grid_circle(r_bore, n_slip))                       # bore (slip grid)
    if 0.0 < r2_band < r_bore - 1e-6:
        add(_grid_circle(r2_band, n_slip))                  # moving-band R2
    # the FAR-FIELD outer circle is not a belt boundary, so discretise it at the
    # air size — otherwise a fine outer ring caps how coarse the air can get.
    add(_grid_circle(r_outer, max(48, int(2 * math.pi * r_outer / max(1.0, air_mm)))))
    V, S = _build_pslg(lines)

    # region seeds: iron + each conductor fine; each air pocket coarse
    reg = [[*iron.representative_point().coords[0], 1, area]]
    reg += [[w.centroid.x, w.centroid.y, 2, area] for w in coils]
    ann = Polygon(_grid_circle(r_outer, 360)[:-1]).difference(
          Polygon(_grid_circle(r_bore, n_slip)[:-1]))
    reg += [[*a.representative_point().coords[0], 3, air_area]
            for a in _air_parts(ann, iron, coils)]
    if 0.0 < r2_band < r_bore - 1e-6:
        # gap-air annulus [R2, bore] — FINE (it carries the gap field)
        reg += [[0.5 * (r2_band + r_bore), 0.0, 4, area]]
    V, T = _triangulate(V, S, area, quality, regions=reg)
    return V, T


def _mesh_rotor_half(polys: Dict, r_od: float, r_shaft: float,
                     n_slip: int, area: float, air_mm: float, quality: int,
                     r1_band: float = 0.0):
    """(V mm, T) for the rotor disk [0, r_od].  Steel and magnets mesh at
    `area`; the solid shaft core (r < r_shaft) and the flux-barrier air pockets
    get the coarse air size — the rotor centre carries little flux.

    r1_band > r_od extends the half with the gap-air annulus [r_od, r1_band]
    ending on the UNIFORM slip-grid ring R1 — the moving-band/harmonic-macro
    boundary (the macro couples R1↔R2 analytically, no node-merge belt)."""
    from shapely.geometry import LineString, MultiPolygon, Polygon
    air_area = max(area, 0.4330 * air_mm * air_mm)              # coarse air cell
    parts = [g for g in getattr(polys["rotor"], "geoms", [polys["rotor"]])
             if getattr(g, "area", 0.0) > 1e-6]                 # drop degenerate
    steel = MultiPolygon(parts) if len(parts) > 1 else parts[0]
    steel = _defeature_iron(steel)            # trim knife-edge slivers (pre-grid)
    iron = _resample(steel, r_od, n_slip)                       # OD → slip grid
    # shaft seam is internal (not a belt boundary) → discretise at the air size
    n_sh = max(48, int(2 * math.pi * r_shaft / max(0.35, air_mm)))
    iron = _resample(iron, r_shaft, n_sh)                       # shaft → own grid
    mags = [mg for mg, _pol in (polys.get("magnets") or [])]
    mags = [_weld_outline(mg, iron, 0.01) for mg in mags]  # см. sector (zipper)

    lines = []

    def add(coords):
        r = _snap_ring(coords)
        if len(r) >= 3:
            lines.append(LineString(r))

    for gg in getattr(iron, "geoms", [iron]):
        if getattr(gg, "area", 0.0) < 1e-9:
            continue
        add(gg.exterior.coords)
        for hole in gg.interiors:
            add(hole.coords)
    for mg in mags:
        for run in _air_facing_runs(mg, iron):
            add(run)             # shared walls come from the iron chain
    add(_grid_circle(r_od, n_slip))                             # gap ring
    if r1_band > r_od + 1e-6:
        add(_grid_circle(r1_band, n_slip))                      # moving-band R1
    add(_grid_circle(r_shaft, n_sh))                            # iron|shaft seam
    V, S = _build_pslg(lines)

    # region seeds: steel + each magnet fine; shaft core + flux barriers coarse
    reg = [[*steel.representative_point().coords[0], 5, area]]
    reg += [[mg.centroid.x, mg.centroid.y, 6, area] for mg in mags]
    reg += [[r_shaft * 0.5, 0.0, 7, air_area]]                  # solid shaft core
    ann = Polygon(_grid_circle(r_od, n_slip)[:-1]).difference(
          Polygon(_grid_circle(r_shaft, n_sh)[:-1]))
    reg += [[*a.representative_point().coords[0], 8, air_area]
            for a in _air_parts(ann, steel, mags)]
    if r1_band > r_od + 1e-6:
        # gap-air annulus [r_od, R1] — FINE (it carries the gap field)
        reg += [[0.5 * (r_od + r1_band), 0.0, 9, area]]
    V, T = _triangulate(V, S, area, quality, hole=False, regions=reg,
                        rotor_bridge=True)  # shaft solid
    return V, T


# ── sector meshers (1/N wedge) ───────────────────────────────────────────────
# The CDT is not periodic, but the solver's sector anti-periodic BC pairs the
# two radial-cut node sets by NEAREST radius within 1 mm (handles unequal
# counts).  So a wedge only needs the two cuts to carry ~matching radial node
# sets — pinning the SAME graded {r_k} on both (the sector spans a whole number
# of slot/pole pitches, so both cuts traverse identical geometry) gives an
# exact pairing with a freely-triangulated, high-quality interior (no `Y`).
def _wedge(a0, a1, r0, r1, n=400):
    from shapely.geometry import Polygon
    t = np.linspace(a0, a1, n)
    return Polygon(np.vstack([np.c_[r1 * np.cos(t), r1 * np.sin(t)],
                              np.c_[r0 * np.cos(t[::-1]), r0 * np.sin(t[::-1])]]))


def _grid_arc(r, n_slip, span):
    """Slip-grid arc from angle 0 to span (endpoints land on the grid)."""
    step = 2.0 * math.pi / n_slip
    ks = [k for k in range(n_slip + 1) if -1e-9 <= k * step <= span + 1e-9]
    a = np.array(ks) * step
    return np.c_[r * np.cos(a), r * np.sin(a)]


def _cut_pts(ang, rk):
    rk = np.asarray(rk, float)
    return np.c_[rk * np.cos(ang), rk * np.sin(ang)]


def _lin_arc(r, n, span):
    """Arc 0..span with n+1 EVENLY spaced points, endpoints EXACTLY on the two
    rays.  For non-slip circles in a tiled cell (far field, shaft) — the global
    _grid_arc endpoints generally miss the rays, which leaves ragged corners
    that break the copy-to-copy weld."""
    a = np.linspace(0.0, span, max(2, int(n)) + 1)
    return np.c_[r * np.cos(a), r * np.sin(a)]


def _tile_cells(Vc, Tc, span, n_copies, weld_tol=1e-3):
    """Rotate-copy one meshed cell (V mm, T) n_copies times about the origin and
    weld the coincident seam nodes.  The cell's two radial cut chains are
    clone-identical (_symmetrize_cuts), so copy k's θ=0 chain lands EXACTLY on
    copy k−1's θ=span chain; welding is a pure rounded-coordinate merge.
    n_copies·span == 2π closes the ring (no cuts remain); fewer copies leave an
    open wedge whose two outer chains are clones — the sector cut pairing keys
    on them as before.

    WHY: a CDT wedge welded to itself has DIFFERENT triangles on the two sides
    of the seam — the discrete operator is not rotationally smooth there, and
    that seam defect (replicated by the model symmetry) sprays torque noise on
    non-physical orders (S=2: all orders incl. 1; S=4: even orders — measured).
    Tiling makes EVERY junction the SAME junction (cell-right ↔ cell-left), so
    the residual mesh error is exactly slot/pole-periodic: its torque signature
    lands ONLY on the physical cogging orders, and a 1/S sector is a bit-exact
    subset of the full ring — sector == full by construction."""
    Vs = []; Ts = []; off = 0
    for k in range(int(n_copies)):
        a = k * span
        c, s = math.cos(a), math.sin(a)
        R = np.array([[c, -s], [s, c]])
        Vs.append(np.asarray(Vc, float) @ R.T)
        Ts.append(np.asarray(Tc, np.int64) + off)
        off += len(Vc)
    V = np.vstack(Vs); T = np.vstack(Ts)
    key = np.round(V / weld_tol).astype(np.int64)
    _uniq, first, inv = np.unique(key, axis=0, return_index=True,
                                  return_inverse=True)
    order = np.argsort(first)                    # keep original node order
    rank = np.empty_like(order); rank[order] = np.arange(len(order))
    V2 = V[first[order]]
    T2 = rank[inv][T]
    # drop degenerate triangles (all-3-welded cannot happen geometrically, but a
    # duplicated seam sliver would be caught here)
    ok = ((T2[:, 0] != T2[:, 1]) & (T2[:, 1] != T2[:, 2]) & (T2[:, 0] != T2[:, 2]))
    return V2, T2[ok]


def _graded_radii(segs):
    """Concatenate graded radial samples: segs = [(r0, r1, step), ...]."""
    out = []
    for r0, r1, step in segs:
        n = max(1, int(round((r1 - r0) / max(step, 1e-6))))
        out.extend(np.linspace(r0, r1, n + 1))
    return np.array(sorted(set(np.round(out, 4))))


def _symmetrize_cuts(V, S, span, tol_r=0.06):
    """Force the two radial cut rays (θ=0 and θ=span) to carry an IDENTICAL
    node set — same radii, same count — so the sector anti-periodic pairing
    welds by exact radius.

    The geometry is rotationally periodic across the wedge (span = whole pole
    pitches), so the two cuts SHOULD be clones; independent shapely clipping of
    the two sides leaves numerically-offset / unequal node sets (the arcs are
    line-sampled, the pole cells differ), and the solver's 1 mm nearest-radius
    pairing then mis-welds (offset pairs + a handful of unpaired nodes) → a
    spurious once-per-wedge field seam → order-1 torque ripple.

    Merges both cuts' radii into one clustered set R (within tol_r mm), snaps
    every existing cut node onto R IN PLACE (attached polygon edges follow), and
    inserts the missing R radii on whichever cut lacks them.  Angles in radians;
    V in mm.  Coincident nodes are deduped afterwards.  Caller must triangulate
    with the -Y flag so Triangle does not re-split these (now clone) segments."""
    V = np.asarray(V, float).copy()
    r = np.hypot(V[:, 0], V[:, 1])
    ang = np.arctan2(V[:, 1], V[:, 0])

    def _on_ray(theta):
        # nodes ON the ray θ: SCALE-INDEPENDENT angular test (a fixed perp
        # distance fails — the wedge clip runs to ±1e-3 rad past the cut, so the
        # off-cut boundary nodes sit r·1e-3 away, i.e. 75 µm at r=75 but 0.5 µm
        # at r=0.5).  Wrap-safe via atan2(sin,cos) so span=π (atan2 ±π flip)
        # still matches both edges.  0.17° tol >> the 0.057° clip slop but <<
        # the ~1° interior spacing, so only genuine cut nodes are caught.
        d = np.abs(np.arctan2(np.sin(ang - theta), np.cos(ang - theta)))
        return np.where((d < 3e-3) & (r > 1e-3))[0]

    on0 = _on_ray(0.0)
    onS = _on_ray(span)
    if on0.size < 2 or onS.size < 2:
        return V, S
    # merged, clustered radius set (one representative per cluster ≤ tol_r wide)
    allr = np.sort(np.concatenate([r[on0], r[onS]]))
    reps, cur = [], [allr[0]]
    for rr in allr[1:]:
        if rr - cur[0] <= tol_r:
            cur.append(rr)
        else:
            reps.append(float(np.mean(cur))); cur = [rr]
    reps.append(float(np.mean(cur)))
    R = np.array(reps)
    set0 = set(on0.tolist()); setS = set(onS.tolist())
    new_segs = []
    for theta, idxs in ((0.0, on0), (span, onS)):
        ct, st = math.cos(theta), math.sin(theta)
        slot = {}                                  # cluster k → vertex id
        for i in idxs:                             # snap existing nodes onto R
            k = int(np.argmin(np.abs(R - r[i])))
            V[i] = (R[k] * ct, R[k] * st)
            slot.setdefault(k, int(i))             # first wins; dup deduped later
        for k in range(R.size):                    # insert missing radii
            if k not in slot:
                V = np.vstack([V, (R[k] * ct, R[k] * st)])
                slot[k] = V.shape[0] - 1
        chain = [slot[k] for k in range(R.size)]   # ascending-radius chain
        new_segs += list(zip(chain[:-1], chain[1:]))
    # drop the OLD cut-chain segments (both endpoints on the same cut), keep the
    # rest (polygon edges, gap/shaft arcs, edges crossing INTO a cut), add new.
    keep = [(int(a), int(b)) for a, b in S
            if not ((a in set0 and b in set0) or (a in setS and b in setS))]
    keep += new_segs
    S2 = np.array(sorted({(min(a, b), max(a, b)) for a, b in keep}), np.int64)
    # dedupe coincident vertices (snapping may collide two nodes onto one R)
    _, uniq = np.unique(np.round(V, 4), axis=0, return_index=True)
    V2 = V[np.sort(uniq)]
    keymap = {tuple(np.round(V2[j], 4)): j for j in range(V2.shape[0])}
    remap = np.array([keymap[tuple(np.round(V[i], 4))] for i in range(V.shape[0])],
                     np.int64)
    S3 = remap[S2]
    S3 = S3[S3[:, 0] != S3[:, 1]]
    S3 = np.array(sorted({(min(int(a), int(b)), max(int(a), int(b)))
                          for a, b in S3}), np.int64)
    return V2, S3


def _mesh_stator_sector(polys, r_bore, r_out_iron, r_outer, n_slip, span,
                        area, air_mm, quality, r2_band: float = 0.0,
                        cell: bool = False):
    """(V mm, T) for a stator WEDGE [0, span] × [r_bore, r_outer].
    r2_band < r_bore extends the wedge inward with the gap-air annulus ending
    on the uniform moving-band ring R2 (harmonic-macro boundary).
    cell=True → the wedge is ONE slot-pitch cell to be rotate-copied by
    _tile_cells: the far-field arc uses _lin_arc so its endpoints land EXACTLY
    on the two rays (a global-grid arc misses them → ragged corners → broken
    copy weld)."""
    from shapely.geometry import LineString, Polygon
    iron_edge = math.sqrt(max(area, 1e-6) / 0.4330)
    air_area = max(area, 0.4330 * air_mm * air_mm)
    _rin = r2_band if 0.0 < r2_band < r_bore - 1e-6 else r_bore
    W = _wedge(-1e-3, span + 1e-3, _rin - 3.0, r_outer + 3.0)
    iron = _resample(polys["stator"], r_bore, n_slip).intersection(W)
    coils = [c.intersection(W) for c in (polys.get("coils") or [])
             if c is not None and not c.is_empty and c.intersects(W)]
    coils = [c for c in coils if c.geom_type == "Polygon" and c.area > 1e-6]
    # iron portion of the cut seeded FINE (0.5·iron_edge): -Y freezes the seam,
    # so its radial density is fixed here — a fine flux-carrying seam sharpens the
    # anti-periodic weld (coarse seam left a ~6 pp ripple residual vs full ring).
    _rk_segs = [(r_bore, r_out_iron, 0.5 * iron_edge),
                (r_out_iron, r_outer, air_mm)]
    if 0.0 < r2_band < r_bore - 1e-6:
        _rk_segs.insert(0, (r2_band, r_bore, max(r_bore - r2_band, 1e-3)))
    rk = _graded_radii(_rk_segs)
    n_out = max(8, int(2 * math.pi * r_outer / max(1.0, air_mm)))

    lines = []

    def add(coords):
        r = _snap_ring(coords)
        if len(r) >= 2:
            lines.append(LineString(r))

    for gg in getattr(iron, "geoms", [iron]):
        if getattr(gg, "area", 0.0) < 1e-9:
            continue
        add(gg.exterior.coords)
        for h in gg.interiors:
            add(h.coords)
    for c in coils:
        add(c.exterior.coords)
    add(_grid_arc(r_bore, n_slip, span))
    if 0.0 < r2_band < r_bore - 1e-6:
        add(_grid_arc(r2_band, n_slip, span))           # moving-band R2
    if cell:
        add(_lin_arc(r_outer, max(2, int(round(n_out * span / (2 * math.pi)))),
                     span))                              # endpoints ON the rays
    else:
        add(_grid_arc(r_outer, n_out, span))
    add(_cut_pts(0.0, rk)); add(_cut_pts(span, rk))     # identical {r_k}
    V, S = _build_pslg(lines)
    V, S = _symmetrize_cuts(V, S, span)                 # clone-identical seam

    reg = [[*iron.representative_point().coords[0], 1, area]]
    reg += [[c.centroid.x, c.centroid.y, 2, area] for c in coils]
    ann = W.intersection(Polygon(_grid_circle(r_outer, 360)[:-1]).difference(
                         Polygon(_grid_circle(r_bore, n_slip)[:-1])))
    reg += [[*a.representative_point().coords[0], 3, air_area]
            for a in _air_parts(ann, iron, coils)]
    if 0.0 < r2_band < r_bore - 1e-6:
        _rm = 0.5 * (r2_band + r_bore)
        reg += [[_rm * math.cos(span / 2), _rm * math.sin(span / 2), 4, area]]
    hp = [[(_rin - 1.5) * math.cos(span / 2), (_rin - 1.5) * math.sin(span / 2)]]
    V, T = _triangulate(V, S, area, quality, regions=reg, hole_pts=hp,
                        no_bnd_steiner=True)
    return V, T


def _mesh_rotor_sector(polys, r_od, r_shaft, n_slip, span, area, air_mm, quality,
                       r1_band: float = 0.0, cell_copies: int = 0):
    """(V mm, T) for a rotor WEDGE [0, span] × [0, r_od] (shaft solid to centre).
    r1_band > r_od extends the wedge with the gap-air annulus ending on the
    uniform moving-band ring R1 (harmonic-macro boundary).
    cell_copies > 0 → this wedge is ONE pole-pitch cell for _tile_cells: the
    iron|shaft seam circle count is rounded UP to a multiple of the copy count
    so its grid nodes land EXACTLY on the rays (else the copies' shaft rings
    misalign at the seams by up to half a step → sliver holes in the iron)."""
    from shapely.geometry import LineString, MultiPolygon, Polygon
    iron_edge = math.sqrt(max(area, 1e-6) / 0.4330)
    air_area = max(area, 0.4330 * air_mm * air_mm)
    parts = [g for g in getattr(polys["rotor"], "geoms", [polys["rotor"]])
             if getattr(g, "area", 0.0) > 1e-6]
    steel = MultiPolygon(parts) if len(parts) > 1 else parts[0]
    steel = _defeature_iron(steel)            # trim knife-edge slivers (pre-grid)
    n_sh = max(48, int(2 * math.pi * r_shaft / max(0.35, air_mm)))
    if cell_copies > 0:                       # grid nodes exactly on the rays
        n_sh = int(math.ceil(n_sh / cell_copies)) * cell_copies
    iron = _resample(steel, r_od, n_slip)
    iron = _resample(iron, r_shaft, n_sh)
    _rout = r1_band if r1_band > r_od + 1e-6 else r_od
    W = _wedge(-1e-3, span + 1e-3, 0.0, _rout + 3.0)
    iron = iron.intersection(W)
    mags = [g.intersection(W) for g, _pol in (polys.get("magnets") or [])
            if g.intersects(W)]
    mags = [g for g in mags if g.geom_type == "Polygon" and g.area > 1e-6]
    # SNAP the magnet outlines onto the iron chain (10 um): CadQuery discretises
    # the shared pocket boundary INDEPENDENTLY for the iron and the magnet, so
    # with a corner fillet the two arc polylines land 2-4 um apart — a
    # point-to-SEGMENT offset the PSLG vertex weld cannot see.  Triangle then
    # bridges the um-wide strip with a fringe of micro triangles along the
    # whole arc.  Snapping makes the magnet follow the iron chain exactly, so
    # the noding merges them into ONE conforming chain.
    mags = [_weld_outline(g, iron, 0.01) for g in mags]
    _rk_segs = [(r_shaft, r_od, 0.5 * iron_edge)]            # fine flux-carrying seam
    if r1_band > r_od + 1e-6:
        _rk_segs.append((r_od, r1_band, max(r1_band - r_od, 1e-3)))
    rk = _graded_radii(_rk_segs)
    # shaft-core cut nodes from the ORIGIN out (the pie tip must close at r=0,
    # else near-centre nodes are left isolated → singular matrix).
    rk_sh = _graded_radii([(0.0, r_shaft, air_mm)])
    rk_all = np.array(sorted(set(np.round(np.concatenate([rk_sh, rk]), 4))))

    lines = []

    def add(coords):
        r = _snap_ring(coords)
        if len(r) >= 2:
            lines.append(LineString(r))

    for gg in getattr(iron, "geoms", [iron]):
        if getattr(gg, "area", 0.0) < 1e-9:
            continue
        add(gg.exterior.coords)
        for h in gg.interiors:
            add(h.coords)
    for g in mags:
        for run in _air_facing_runs(g, iron):
            add(run)             # shared pocket walls come from the IRON chain
    add(_grid_arc(r_od, n_slip, span))
    if r1_band > r_od + 1e-6:
        add(_grid_arc(r1_band, n_slip, span))           # moving-band R1
    add(_cut_pts(0.0, rk_all)); add(_cut_pts(span, rk_all))
    V, S = _build_pslg(lines)
    V, S = _symmetrize_cuts(V, S, span)                 # clone-identical seam

    reg = [[*steel.intersection(W).representative_point().coords[0], 5, area]]
    reg += [[g.centroid.x, g.centroid.y, 6, area] for g in mags]
    reg += [[0.5 * r_shaft * math.cos(span / 2),
             0.5 * r_shaft * math.sin(span / 2), 7, air_area]]   # shaft core
    ann = W.intersection(Polygon(_grid_circle(r_od, n_slip)[:-1]).difference(
                         Polygon(_grid_circle(r_shaft, n_sh)[:-1])))
    reg += [[*a.representative_point().coords[0], 8, air_area]
            for a in _air_parts(ann, steel, mags)]
    if r1_band > r_od + 1e-6:
        _rm = 0.5 * (r_od + r1_band)
        reg += [[_rm * math.cos(span / 2), _rm * math.sin(span / 2), 9, area]]
    V, T = _triangulate(V, S, area, quality, hole=False, regions=reg,
                        no_bnd_steiner=True, rotor_bridge=True)  # shaft solid
    return V, T


# ── tagging ──────────────────────────────────────────────────────────────────
def _tag_stator(V, T, polys, r_out_iron):
    """int16 per-triangle DOM tag for the stator half."""
    from shapely import contains_xy
    from shapely.ops import unary_union
    from scipy.spatial import cKDTree
    C = (V[T[:, 0]] + V[T[:, 1]] + V[T[:, 2]]) / 3.0
    cx, cy = C[:, 0], C[:, 1]
    tags = np.full(len(T), DOM_AIR, np.int16)
    in_iron = contains_xy(polys["stator"], cx, cy)
    tags[in_iron] = DOM_STATOR
    # outer air ring (beyond the iron OD)
    tags[(~in_iron) & (np.hypot(cx, cy) > r_out_iron + 1e-6)] = DOM_OUTER
    # coils: cells whose centroid sits inside a REAL conductor → that wire's j
    # (the mesh conforms to the wire outlines, so a cell is wholly in one wire
    # or in the slot-air between them; the gaps stay DOM_AIR).
    coils = polys.get("coils") or []
    if coils:
        in_cu = (~in_iron) & contains_xy(unary_union(coils), cx, cy)
        idx = np.where(in_cu)[0]
        if len(idx):
            ref = np.array([[c.centroid.x, c.centroid.y] for c in coils])
            _, j = cKDTree(ref).query(C[idx])
            tags[idx] = (DOM_COIL_BASE + j).astype(np.int16)
    return tags


def _tag_rotor(V, T, polys, r_shaft):
    """int16 per-triangle DOM tag for the rotor half."""
    from shapely import contains_xy
    from scipy.spatial import cKDTree
    parts = [g for g in getattr(polys["rotor"], "geoms", [polys["rotor"]])
             if getattr(g, "area", 0.0) > 1e-6]
    from shapely.geometry import MultiPolygon
    steel = MultiPolygon(parts) if len(parts) > 1 else parts[0]
    C = (V[T[:, 0]] + V[T[:, 1]] + V[T[:, 2]]) / 3.0
    cx, cy = C[:, 0], C[:, 1]
    tags = np.full(len(T), DOM_AIR, np.int16)
    tags[contains_xy(steel, cx, cy)] = DOM_ROTOR
    tags[np.hypot(cx, cy) < r_shaft - 1e-6] = DOM_SHAFT       # (hole; usually empty)
    mags = polys.get("magnets") or []
    if mags:
        in_mag = np.zeros(len(T), bool)
        for mg, _pol in mags:
            in_mag |= contains_xy(mg, cx, cy)
        idx = np.where(in_mag)[0]
        if len(idx):
            ref = np.array([[mg.centroid.x, mg.centroid.y] for mg, _ in mags])
            _, j = cKDTree(ref).query(C[idx])
            tags[idx] = (DOM_MAG_BASE + j).astype(np.int16)
    return tags


# ── public entry point ───────────────────────────────────────────────────────
def geo_mesh_halves(p: Dict, polys: Dict, outer_air_factor: float = 1.2,
                    density: float = 1.2, n_sectors: int = 1,
                    n_slip: int = _DEFAULT_N_SLIP,
                    r_si: float = 0.0, r_ro: float = 0.0,
                    air_mesh_mm: float = 0.0,
                    r1_band: float = 0.0, r2_band: float = 0.0,
                    mesh_edge_mm: float = 0.0):
    """Solver-ready halves in DOM_* tags, geometry-driven CDT:
    (mesh_s, tags_s, cls_s, mesh_r, tags_r, cls_r) — same signature as
    iron_template.template_solver_halves.  Full ring only for now (n_sectors
    handled by the caller's fallback until the sector clone lands).

    r_si / r_ro (belt spec radii, mm) pin the gap rings so the belt welds by
    node identity.  They are REQUIRED to come from a source that matches the
    belt — the stator bore lives on an INTERIOR ring (the polygon exterior min
    is the yoke, not the bore), so _radius_span would grid the wrong circle."""
    from skfem import MeshTri

    # gap-facing radii: belt spec first, else the geometry params (NEVER the
    # polygon span — the stator bore is an interior ring).
    r_bore = float(r_si) or float(p.get("stator_inner_radius") or 0.0)
    r_od = float(r_ro) or float(p.get("rotor_outer_radius") or 0.0)
    r_out_iron = _radius_span(polys["stator"])[1]           # yoke OD (exterior)
    r_sh = float(p.get("rotor_inner_radius") or _radius_span(polys["rotor"])[0])
    r_outer = r_out_iron * float(outer_air_factor)
    # Cell area (mm²).  mesh_edge_mm is the UI "Max element size": honour it as
    # the actual TRIANGLE EDGE (area = 0.433·L² for an equilateral), so the
    # rotor/stator iron interior meshes at the size the user asked for.  The
    # legacy density mapping (0.6/density = 0.3·mesh, i.e. LINEAR in size →
    # edges ~2× finer than requested) stays as fallback for callers that don't
    # pass an explicit edge.  Fillet arcs still refine locally via the boundary
    # densification — only the interior sizing changes.
    if float(mesh_edge_mm or 0.0) > 0:
        area = max(0.12, 0.4330 * float(mesh_edge_mm) ** 2)
    else:
        area = max(0.12, 0.6 / max(0.3, float(density)))
    # air EDGE size (mm): the requested air size, else a coarse default that is
    # not tied to the (possibly very fine) iron cell — the open air / shaft /
    # far-field carry little flux, so ~3 mm keeps them cheap.  Used for both the
    # coarse region area AND the far-field circle discretisation.
    iron_edge = math.sqrt(max(area, 1e-6) / 0.4330)
    air_mm = (float(air_mesh_mm) if float(air_mesh_mm or 0.0) > 0
              else max(3.0, 2.0 * iron_edge))

    _ns = max(1, int(n_sectors))

    # ── SLOT/POLE CELL TILING (default; SB_GEO_TILE=0 opts out) ─────────────
    # Mesh ONE slot-pitch (stator) / pole-pitch (rotor) cell with clone-identical
    # radial chains and rotate-copy it.  Every junction is then the SAME
    # junction, so the CDT discretisation error is exactly slot/pole-periodic:
    # its torque signature lands ONLY on the physical cogging orders, and a 1/S
    # sector is a bit-exact subset of the full ring (sector == full by
    # construction).  A whole-wedge CDT instead leaves TWO unique seams whose
    # neighbourhoods differ → a static defect the model symmetry replicates →
    # broadband torque noise (measured: S=4 even orders, S=2 all orders).
    _n_slots = int(round(float(p.get("num_slots") or 0)))
    _n_poles = int(round(float(p.get("num_poles") or 0)))
    # STATOR cell = a PAIR of slots (2 slot pitches).  The CQ builder creates
    # slots in MIRRORED pairs about each pair ray — a single-slot rotational
    # copy reproduces the SAME chirality everywhere, so every ODD slot's mesh
    # missed the true (mirrored) geometry by ~6% of the coil area (measured
    # coverage 1.00/0.94 alternating; wires visibly off the outlines).  The
    # slot SET is invariant only under rotation by TWO pitches — tile that.
    # ROTOR pole cells are mirror-symmetric about their own centreline
    # (coverage 1.00 on every pole), so single-pole tiling stays exact.
    _n_pairs = _n_slots // 2
    _tile = (_SB_GEO_TILE and _n_slots >= 4 and _n_slots % 2 == 0
             and _n_poles >= 2
             and n_slip % _n_pairs == 0 and n_slip % _n_poles == 0
             and _n_pairs % _ns == 0 and _n_poles % _ns == 0)
    if _tile:
        # a coil crossing a cell ray would be sliced by every copy — the cut
        # must pass mid-tooth.  Pair-periodic geometry ⇒ checking ray θ=0 is
        # enough for all rays.
        from shapely.geometry import LineString as _LS
        _ray0 = _LS([(max(r_bore - 1.0, 0.1), 0.0), (r_out_iron + 1.0, 0.0)])
        if any(c is not None and not c.is_empty and c.intersects(_ray0)
               for c in (polys.get("coils") or [])):
            log.info("geo tile: coil crosses the θ=0 ray — whole-wedge fallback")
            _tile = False
    Vs = None
    if _tile:
        try:
            _span_s = 2.0 * math.pi / _n_pairs           # 2 slot pitches
            _span_r = 2.0 * math.pi / _n_poles
            Vc, Tc = _mesh_stator_sector(polys, r_bore, r_out_iron, r_outer,
                                         n_slip, _span_s, area, air_mm, _Q,
                                         r2_band=r2_band, cell=True)
            Vs, Ts = _tile_cells(Vc, Tc, _span_s, _n_pairs // _ns)
            Vcr, Tcr = _mesh_rotor_sector(polys, r_od, r_sh, n_slip, _span_r,
                                          area, air_mm, _Q, r1_band=r1_band,
                                          cell_copies=_n_poles)
            Vr, Tr = _tile_cells(Vcr, Tcr, _span_r, _n_poles // _ns)
            log.info("geo tile: stator %d x pair-cell(%dtri) = %dtri, rotor "
                     "%d x cell(%dtri) = %dtri (1/%d)", _n_pairs // _ns,
                     len(Tc), len(Ts), _n_poles // _ns, len(Tcr), len(Tr), _ns)
        except Exception as _te:
            log.warning("geo tile failed (%s) — whole-wedge fallback", _te)
            Vs = None
    if Vs is None:
        if _ns > 1:                                    # 1/N wedge
            span = 2.0 * math.pi / _ns
            Vs, Ts = _mesh_stator_sector(polys, r_bore, r_out_iron, r_outer,
                                         n_slip, span, area, air_mm, _Q,
                                         r2_band=r2_band)
            Vr, Tr = _mesh_rotor_sector(polys, r_od, r_sh, n_slip, span,
                                        area, air_mm, _Q, r1_band=r1_band)
        else:                                          # full ring
            Vs, Ts = _mesh_stator_half(polys, r_bore, r_out_iron,
                                       r_outer, n_slip, area, air_mm, _Q,
                                       r2_band=r2_band)
            Vr, Tr = _mesh_rotor_half(polys, r_od, r_sh, n_slip, area, air_mm, _Q,
                                      r1_band=r1_band)
    # Prune UNREFERENCED vertices (Triangle keeps every input point in the output
    # even when no triangle uses it — seen on the sector cut chains in the outer
    # air).  An unreferenced vertex is a zero stiffness row; on the 200 mm they
    # happened to be killed by the outer-circle Dirichlet or merged into a live
    # cut partner, but on the 40/100 mm at least one stayed free → singular
    # matrix ("failed to factorize").  Dropping them is exact: they carry no FEM
    # meaning, and the belt weld / cut pairing / Dirichlet all key on coordinates.
    def _prune(V, T):
        used = np.unique(T)
        if used.size == len(V):
            return V, T
        remap = np.full(len(V), -1, np.int64)
        remap[used] = np.arange(used.size)
        return V[used], remap[T]
    Vs, Ts = _prune(Vs, Ts)
    Vr, Tr = _prune(Vr, Tr)
    # zero-area slivers (defeatured-iron vs magnet-outline chains) crash the
    # FEM assembly — collapse them, protecting the slip/shaft grid rings that
    # the belt welds BY node identity.
    Vs, Ts = _collapse_slivers(Vs, Ts, keep_r=(r_bore,))
    Vr, Tr = _collapse_slivers(Vr, Tr, keep_r=(r_od, r_sh))
    Vs, Ts = _prune(Vs, Ts)
    Vr, Tr = _prune(Vr, Tr)

    tags_s = _tag_stator(Vs, Ts, polys, r_out_iron)
    tags_r = _tag_rotor(Vr, Tr, polys, r_sh)

    mesh_s = MeshTri(np.ascontiguousarray(Vs.T) * 1e-3, np.ascontiguousarray(Ts.T))
    mesh_r = MeshTri(np.ascontiguousarray(Vr.T) * 1e-3, np.ascontiguousarray(Tr.T))

    def _cls_s(x, y):
        return DOM_STATOR
    def _cls_r(x, y):
        return DOM_ROTOR
    _cls_s.polys = polys
    _cls_r.polys = polys
    return mesh_s, tags_s, _cls_s, mesh_r, tags_r, _cls_r
