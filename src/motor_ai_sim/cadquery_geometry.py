# CadQuery Geometry Engine
"""
Native Python parametric motor geometry engine using CadQuery.

This module provides:
1. Parametric Stator: Ring with radial slots using polarArray()
2. Parametric Rotor: Hub with magnet cavities
3. Coils: Wound in slots
4. STL Export: High-resolution export for NVIDIA Modulus
5. Fast Rebuilds: < 1 second regeneration
"""

from __future__ import annotations
import os
import json
import logging
import hashlib
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Any
from math import sin, cos, tan, radians, degrees, pi, acos, atan2, hypot, ceil
#import math

log = logging.getLogger(__name__)


def _safe_union(a, b):
    """Binary shapely union robust to GEOS 'side location conflict' — thrown when
    a valid polygon is unioned with a TINY feature (e.g. a 0.2 mm slot-mouth fillet
    circle on a 40 mm stator) whose edges land within float noise of the base.
    Retries with buffer(0) then snap-rounded (grid_size) inputs, which resolve the
    degenerate noding without changing the shape meaningfully (grid ≤ 1 um)."""
    try:
        return a.union(b)
    except Exception:
        pass
    try:
        return a.buffer(0).union(b.buffer(0))
    except Exception:
        pass
    import shapely
    for gs in (1e-6, 1e-5, 1e-4):
        try:
            return shapely.union(a, b, grid_size=gs)
        except Exception:
            continue
    return a.buffer(1e-9).union(b.buffer(1e-9))    # last resort

# CadQuery imports - try to import lazily
HAS_CADQUERY = False

def _import_cadquery():
    """Lazy import of CadQuery."""
    global HAS_CADQUERY, cq, exporters
    if HAS_CADQUERY:
        return True
    
    try:
        import cadquery as cq
        from cadquery import exporters
        HAS_CADQUERY = True
        return True
    except ImportError:
        print("Warning: CadQuery not available")
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  Arc discretisation — ONE shared helper for EVERY arc/fillet in this module
# ═══════════════════════════════════════════════════════════════════════════
# Root cause of the "fan of microscopic triangles" artefact: every fillet used a
# FIXED point count (n_arc = 8 / 12 / 16, slot-mouth circles 32/64-gons), so the
# chord length scaled with the RADIUS.  A 0.2 mm rotor-tip fillet was cut into 8
# chords of 0.0196 mm while the ring it lives on has 0.30 mm edges — gmsh honours
# every boundary point, so those 15x-too-fine chords seeded a fan of degenerate
# elements.  The fix is sagitta-based: choose the angular step from the geometric
# error we are willing to accept, not from a magic count.
#
#   sagitta  s = r*(1 - cos(dtheta/2))   (max chord-to-arc deviation)
#   tol      = min(machine_diameter/8000, r/50)
#
# The absolute term ties the tolerance to the machine scale: 0.019 mm on a 150 mm
# machine, 0.005 mm on a 40 mm one.  It is deliberately calibrated so the rule
# NEVER refines anything relative to the fixed counts it replaces — the old
# 32-gon slot-mouth circle on the 30 mm machine already accepted a 0.0034 mm
# chord error, so a scale-proportional tolerance at that level only ever removes
# points.  A geometry fix must not silently make every mesh denser.
# The r/50 term is what saves the tiny fillets: with tol = r/50 the step is a
# CONSTANT 0.4 rad however small r gets, so a 0.2 mm fillet gets 2-3 chords
# instead of 8 microscopic ones.
_ARC_SAG_SCALE = 8000.0    # absolute sagitta tolerance = machine diameter / this
_ARC_SAG_REL   = 50.0      # ... but never finer than r / this
_ARC_MIN_SEGS  = 2         # a fillet is never less than 2 chords (still an arc)
_ARC_PTS_FULL  = 8         # >= 8 points on a full 360 deg arc
_ARC_MAX_SEGS  = 256       # cap: one arc can never explode a ring
_DEFAULT_SCALE_MM = 150.0  # fallback machine diameter when a caller has none


def _arc_n_segments(r: float, sweep: float, scale_mm: float = _DEFAULT_SCALE_MM) -> int:
    """Chord count for an arc of radius `r` spanning |`sweep`| radians.

    Sagitta-based (see the block comment above).  Additionally no chord may be
    shorter than `scale_mm/2000` (0.075 mm at 150 mm, 0.02 mm at 40 mm) unless
    the WHOLE arc is shorter than that — that floor is what forbids the micro
    segments the mesher chokes on, and it can only ever bind on tiny radii where
    the sagitta is already far below tolerance anyway.
    """
    sweep = abs(float(sweep))
    # A NEGATIVE radius reaches here from infeasible designs (fill_r2 goes < 0
    # when the slot mouth cannot be rounded) — the caller then draws a mirrored
    # circle of |r| and the validator reports the violation.  Size it off |r| so
    # the ring is still a ring and validation, not shapely, does the complaining.
    r = abs(float(r))
    # floor: >= _ARC_PTS_FULL chords on a full turn, >= _ARC_MIN_SEGS on any arc
    n_floor = max(_ARC_MIN_SEGS, int(ceil(sweep / (2.0 * pi) * _ARC_PTS_FULL)))
    if r <= 0.0 or sweep <= 0.0:
        return n_floor
    scale_mm = float(scale_mm) if scale_mm and scale_mm > 0 else _DEFAULT_SCALE_MM
    tol = min(scale_mm / _ARC_SAG_SCALE, r / _ARC_SAG_REL)
    ratio = max(-1.0, min(1.0, 1.0 - tol / r))
    dtheta = 2.0 * acos(ratio)                       # step meeting the sagitta tol
    n = max(int(ceil(sweep / dtheta)) if dtheta > 0 else n_floor, n_floor)
    # min-chord floor — kills micro chords at their source
    min_chord = scale_mm / 2000.0
    n_chord_cap = int((sweep * r) // min_chord)
    if n_chord_cap >= n_floor:
        n = min(n, n_chord_cap)
    else:
        n = n_floor
    return int(max(_ARC_MIN_SEGS, min(n, _ARC_MAX_SEGS)))


def _arc_points(cx: float, cy: float, r: float, a0: float, sweep: float,
                scale_mm: float = _DEFAULT_SCALE_MM, include_start: bool = True):
    """Points along an arc, sagitta-discretised.  Start and end are exact."""
    n = _arc_n_segments(r, sweep, scale_mm)
    k0 = 0 if include_start else 1
    return [(float(cx + r * cos(a0 + sweep * k / n)),
             float(cy + r * sin(a0 + sweep * k / n))) for k in range(k0, n + 1)]


def _circle_points(r: float, n: int = 256):
    """Full-circle polygon.  Deliberately a FIXED 256-gon, NOT sagitta-driven:
    these are the air-gap / OD / bore domain boundaries and their density is a
    SOLVER requirement (torque accuracy across the sliding band), not a CAD
    tolerance.  They are uniform and produce no micro chords, so they are not
    part of the defect this helper set fixes — sagitta rules would only coarsen
    the air gap and move the pinned physics."""
    return [(r * cos(2 * pi * i / n), r * sin(2 * pi * i / n)) for i in range(n)]


# ═══════════════════════════════════════════════════════════════════════════
#  Ring sanitising — no duplicate points, no degenerate rings, ever
# ═══════════════════════════════════════════════════════════════════════════
_DEGEN_AREA_MM2 = 1e-8      # (1e-4 mm)^2 — below this a ring is not geometry

# Weld (point-merge) tolerance = machine_diameter / _WELD_DIV.
#   150 mm machine -> 0.0375 mm ;  40 mm machine -> 0.010 mm
#
# Why this and not "1e-6 of the diameter" (which only catches EXACT duplicates):
# after the arc discretisation is fixed, every remaining short edge in the output
# is a GEOS artefact of a tangency, not geometry —
#   * shapely's union/difference nodes the slot-mouth circle against the slot
#     wedge it is tangent to and emits both an exact duplicate AND a node a few
#     microns off an existing vertex,
#   * a fillet's tangent point lands at an arbitrary arc distance along the
#     boundary, so the leftover stub to the next retained vertex is a uniformly
#     distributed remainder that can be arbitrarily small.
# Neither is something this builder ever DECIDED to emit: the arc helper's
# min-chord floor is diameter/2000, so _WELD_DIV = 4000 is exactly HALF the
# shortest chord we ever produce deliberately — a merge can never eat a real arc
# point, and it moves a boundary by at most 0.037 mm on a 150 mm machine (the
# solver's own mesher already runs a 0.3 mm Douglas-Peucker on these rings, i.e.
# 8x looser).  Every merge is logged with coordinates.
_WELD_DIV = 4000.0


def _ring_signed_area(P) -> float:
    a = 0.0
    n = len(P)
    for i in range(n):
        x1, y1 = P[i]
        x2, y2 = P[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _sanitize_ring(coords, eps: float, label: str = ""):
    """Clean ONE closed ring.  Returns the open coordinate list, or None if the
    ring is degenerate (< 3 distinct points, or area below (1e-4 mm)^2).

    * merges consecutive points closer than `eps` (the weld tolerance — see
      `_WELD_DIV`; shapely booleans emit exact duplicates at noded slot-mouth
      corners, and those zero-length segments are what made gmsh build a fan of
      microscopic triangles),
    * drops a vertex ONLY when it is EXACTLY collinear with (and between) its
      neighbours — tolerance eps/10000, i.e. ~4e-6 mm, so real geometry is never
      simplified away,
    * logs every drop with coordinates.

    The merge is deliberately ORDER-INDEPENDENT.  Adjacent domains carry their
    own copy of a shared boundary (out_band's hole IS the stator ring, in_band's
    hole IS the rotor), traversed from a different vertex and in the opposite
    direction.  A "keep the first, drop the follower" sweep resolves a 3-point
    cluster differently depending on where the traversal starts, which would
    leave the two copies of the same edge a few microns apart — and OCC's
    fragment turns a mismatched shared boundary into exactly the sliver faces
    this fix exists to remove.  So each maximal run of within-eps consecutive
    points (a rotation- and direction-invariant set) collapses to its
    lexicographically smallest member, which both copies agree on.
    """
    P = [(float(c[0]), float(c[1])) for c in coords]
    if len(P) > 1 and hypot(P[0][0] - P[-1][0], P[0][1] - P[-1][1]) <= eps:
        P = P[:-1]                                     # shapely's closing repeat

    n = len(P)
    dropped_dups = []
    if n >= 2:
        close = [hypot(P[i][0] - P[(i + 1) % n][0],
                       P[i][1] - P[(i + 1) % n][1]) <= eps for i in range(n)]
        if all(close):
            dropped_dups = list(P[1:])
            merged = [min(P)]
        else:
            merged = []
            for s in range(n):
                if close[(s - 1) % n]:
                    continue                       # not the start of a run
                run = [P[s]]
                k = s
                while close[k]:
                    k = (k + 1) % n
                    if k == s:
                        break
                    run.append(P[k])
                rep = min(run)                     # order-independent representative
                merged.append(rep)
                dropped_dups.extend(q for q in run if q != rep)
    else:
        merged = list(P)
    if dropped_dups:
        log.warning("geometry sanitize [%s]: merged %d coincident point(s) "
                    "(gap <= %.3g mm weld tol), first at (%.4f, %.4f)",
                    label, len(dropped_dups), eps,
                    dropped_dups[0][0], dropped_dups[0][1])

    # Exactly-collinear interior points (never a simplification: eps_col is float
    # noise, ~4e-6 mm on a 150 mm machine).  Like the weld above this is a PURE
    # LOCAL predicate on the ORIGINAL neighbours — never on a running "last kept"
    # cursor — so it gives the same answer whichever vertex a copy of this
    # boundary happens to start at and whichever way it is wound.  Collinearity
    # of (a, b, c) is symmetric, so reversal is covered too.
    eps_col = eps * 1e-4
    if len(merged) > 3:
        n = len(merged)
        drop = [False] * n
        for i in range(n):
            a, b, c = merged[(i - 1) % n], merged[i], merged[(i + 1) % n]
            ux, uy = c[0] - a[0], c[1] - a[1]
            ul = hypot(ux, uy)
            if ul <= eps:
                continue
            t = ((b[0] - a[0]) * ux + (b[1] - a[1]) * uy) / (ul * ul)
            perp = abs((b[0] - a[0]) * uy - (b[1] - a[1]) * ux) / ul
            drop[i] = perp <= eps_col and 0.0 <= t <= 1.0
        if any(drop) and n - sum(drop) >= 3:
            log.warning("geometry sanitize [%s]: dropped %d exactly-collinear "
                        "point(s)", label, sum(drop))
            merged = [q for q, d in zip(merged, drop) if not d]

    if len(merged) < 3:
        log.warning("geometry sanitize [%s]: DROPPED degenerate ring — only %d "
                    "distinct point(s): %s", label, len(merged),
                    [(round(x, 4), round(y, 4)) for x, y in merged])
        return None
    if abs(_ring_signed_area(merged)) < _DEGEN_AREA_MM2:
        log.warning("geometry sanitize [%s]: DROPPED degenerate ring — %d points, "
                    "area %.3g mm^2 < %.3g, first point (%.4f, %.4f)",
                    label, len(merged), abs(_ring_signed_area(merged)),
                    _DEGEN_AREA_MM2, merged[0][0], merged[0][1])
        return None
    return merged


def _sanitize_geom(geom, scale_mm: float, label: str = ""):
    """Apply `_sanitize_ring` to EVERY ring of a shapely (Multi)Polygon.

    Degenerate sub-polygons (the 3-coincident-point sliver shapely's rotor
    difference leaves behind) and degenerate holes are dropped, loudly.  If
    sanitising would delete a body that actually had area, the ORIGINAL is kept
    and the problem is logged as an error — we never silently lose a domain.
    """
    if geom is None:
        return geom
    try:
        from shapely.geometry import Polygon as _SP, MultiPolygon as _SMP
    except ImportError:
        return geom
    if getattr(geom, "is_empty", False):
        return geom
    _s = float(scale_mm) if scale_mm and scale_mm > 0 else _DEFAULT_SCALE_MM
    eps = _s / _WELD_DIV

    def _one(g, tag):
        ext = _sanitize_ring(list(g.exterior.coords), eps, tag)
        if ext is None:
            return []
        holes = []
        for j, h in enumerate(g.interiors):
            hr = _sanitize_ring(list(h.coords), eps, f"{tag}.hole{j}")
            if hr is not None:
                holes.append(hr)
        q = _SP(ext, holes)
        if not q.is_valid:
            q = q.buffer(0)
        if q.is_empty or q.area < _DEGEN_AREA_MM2:
            return []
        # buffer(0) on a self-touching ring can hand back a MultiPolygon; those
        # must be FLATTENED, never nested — MultiPolygon([MultiPolygon]) raises
        # "Sequences of multi-polygons are not valid arguments".
        if hasattr(q, "geoms"):
            return [g for g in q.geoms
                    if hasattr(g, "exterior") and g.area >= _DEGEN_AREA_MM2]
        return [q]

    subs = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    out = []
    for i, g in enumerate(subs):
        if not hasattr(g, "exterior"):        # LineString/Point debris
            if getattr(g, "length", 0.0) > 0 or not getattr(g, "is_empty", True):
                log.warning("geometry sanitize [%s]: DROPPED non-areal fragment %s",
                            label, g.geom_type)
            continue
        out.extend(_one(g, f"{label}[{i}]" if len(subs) > 1 else label))
    if not out:
        if getattr(geom, "area", 0.0) > _DEGEN_AREA_MM2:
            log.error("geometry sanitize [%s]: refusing to drop a body with area "
                      "%.6g mm^2 — keeping the ORIGINAL rings", label, geom.area)
            return geom
        return geom
    return out[0] if len(out) == 1 else _SMP(out)


def _sanitize_polys_dict(polys: Dict, scale_mm: float) -> Dict:
    """Sanitize every ring of every domain in a get_2d_polygons()-shaped dict."""
    for key in ('stator', 'rotor', 'shaft', 'air_gap', 'in_band', 'out_band'):
        if key in polys and polys[key] is not None:
            polys[key] = _sanitize_geom(polys[key], scale_mm, key)
    if 'magnets' in polys:
        polys['magnets'] = [(_sanitize_geom(mp, scale_mm, f"magnet[{i}]"), pol)
                            for i, (mp, pol) in enumerate(polys['magnets'])]
    for key in ('coils', 'wire_insulation', 'slot_insulation'):
        if key in polys and polys[key]:
            polys[key] = [_sanitize_geom(g, scale_mm, f"{key}[{i}]")
                          for i, g in enumerate(polys[key])]
    return polys


def _fillet_ring_corners(poly, r, select, ang_min_deg=8.0, ang_max_deg=168.0,
                         scale_mm=_DEFAULT_SCALE_MM):
    """THE vertex-fillet core — every rounded corner in this module goes through it.

    Rounds the SHARP corners of a Shapely (Multi)Polygon that `select` accepts,
    with a true tangent-arc fillet of radius r — UNIFORMLY.

    The tangent length needed for radius r is t = r/tan(half-angle).  Earlier this
    was clamped to 45 % of the single ADJACENT edge, so on the 256-gon rotor
    surface (≈1.4 mm segments) the radius collapsed to ~0.6 mm, and unevenly
    (each corner's neighbour segment differs) → the "one tip rounder than the
    others" artdefact.

    Here t is measured ALONG the boundary across the smooth (sub-corner) arc up to
    the next REAL corner, and the tangent points are placed on the actual boundary
    at that arc-distance (so a discretised arc is handled exactly).  t is only
    capped at 49 % of the run to the next corner so two fillets sharing an edge
    can't overlap.  Result: every corner with room gets the SAME radius r; only a
    genuinely tight neck (e.g. a thin bridge) auto-reduces.  Topology preserved.

    Crucially the vertices the fillet SWALLOWS are removed (`consumed`): a naive
    "replace the corner vertex by an arc" leaves the swallowed discretisation
    points in place right next to the arc's tangent point, which is what produced
    the 0.005 mm slivers on the stator OD.  The arc itself is emitted through the
    shared sagitta helper, so its chords scale with its radius.

    Parameters
    ----------
    select : callable(radii: ndarray, P: list) -> bool ndarray
        Per-vertex mask of which corners are candidates (radius band, target
        radius, ...).  Called once per ring.
    """
    import numpy as _np
    import math as _m
    from shapely.geometry import Polygon as _SP, MultiPolygon as _SMP

    a_lo, a_hi = _m.radians(ang_min_deg), _m.radians(ang_max_deg)

    def _ring(coords):
        P = [_np.asarray(c, float) for c in coords]
        if len(P) > 1 and _np.allclose(P[0], P[-1]):
            P = P[:-1]
        n = len(P)
        if n < 3:
            return coords
        seg = _np.array([float(_np.hypot(*(P[(i + 1) % n] - P[i]))) for i in range(n)])  # seg[i]=|P[i]→P[i+1]|
        ang = _np.empty(n)
        for i in range(n):
            d1, d2 = P[i - 1] - P[i], P[(i + 1) % n] - P[i]
            l1, l2 = float(_np.hypot(*d1)), float(_np.hypot(*d2))
            ang[i] = _m.pi if (l1 < 1e-12 or l2 < 1e-12) else \
                _m.acos(max(-1.0, min(1.0, float(_np.dot(d1 / l1, d2 / l2)))))
        is_corner = (ang >= a_lo) & (ang <= a_hi)        # candidate to be filleted
        bound = ang < a_hi                                # any real turn bounds a run

        radii = _np.array([float(_np.hypot(P[i][0], P[i][1])) for i in range(n)])
        at_surface = _np.asarray(select(radii, P), bool)

        def run(i, step):
            L = 0.0; k = i
            for _ in range(n):
                L += seg[k] if step > 0 else seg[(k - 1) % n]
                k = (k + step) % n
                if k == i or bound[k]:
                    break
            return L

        def walk(i, step, d):
            """Point on the boundary at arc-distance d from vertex i (direction step)."""
            k = i; rem = d
            for _ in range(n + 1):
                e = k if step > 0 else (k - 1) % n
                s = seg[e]
                nxt = (k + 1) % n if step > 0 else (k - 1) % n
                if s >= rem:
                    u = P[nxt] - P[k]; ul = float(_np.hypot(*u))
                    return (P[k] + u / ul * rem) if ul > 1e-12 else P[k].copy()
                rem -= s; k = nxt
            return P[k].copy()

        consumed = _np.zeros(n, bool)
        out = []
        for i in range(n):
            if not (is_corner[i] and at_surface[i]):      # skip non-corners + deeper rows
                if not consumed[i]:
                    out.append(tuple(P[i]))
                continue
            half = ang[i] / 2.0
            th = _m.tan(half)
            want = r / th if th > 1e-9 else 1e9
            t = min(want, 0.49 * run(i, -1), 0.49 * run(i, 1))   # uniform r, capped vs neighbour
            if t < 1e-6:
                out.append(tuple(P[i])); continue
            V = P[i]; reff = t * th
            Pa, Pb = walk(i, -1, t), walk(i, 1, t)
            # Mark the smooth vertices the fillet swallows on each side.
            #
            # The tangent point lands at an ARBITRARY arc distance along the
            # discretised boundary, so the stub from it to the next surviving
            # vertex is a uniformly distributed remainder — it can be arbitrarily
            # short (that is where the 0.014 mm edges on the 40 mm bore came
            # from).  If the tangent point ate more than _STUB_KEEP of the chord
            # it landed in, swallow that vertex too: the boundary then runs
            # straight from the tangent point to the vertex AFTER it, which on a
            # smooth run costs ~4x the (already sub-micron) local sagitta.
            _STUB_KEEP = 0.6
            for step in (-1, 1):
                k = i; rem = t
                for _ in range(n):
                    e = k if step > 0 else (k - 1) % n
                    if seg[e] >= rem:
                        if rem > _STUB_KEEP * seg[e]:      # stub too short — snap
                            nxt = (k + step) % n
                            if nxt != i and not bound[nxt]:
                                consumed[nxt] = True
                        break
                    rem -= seg[e]; k = (k + step) % n
                    if k != i:
                        consumed[k] = True
            uA = Pa - V; la = float(_np.hypot(*uA))
            uB = Pb - V; lb = float(_np.hypot(*uB))
            if la < 1e-9 or lb < 1e-9:
                out.append(tuple(V)); continue
            uA /= la; uB /= lb
            bis = uA + uB; bl = float(_np.hypot(*bis))
            if bl < 1e-9:
                out.append(tuple(V)); continue
            bis /= bl
            C = V + bis * (reff / _m.sin(half))
            a0 = _m.atan2(Pa[1] - C[1], Pa[0] - C[0])
            a1 = _m.atan2(Pb[1] - C[1], Pb[0] - C[0])
            d = a1 - a0
            while d > _m.pi:  d -= 2 * _m.pi
            while d < -_m.pi: d += 2 * _m.pi
            # sagitta-based arc — chord count follows reff, NOT a magic constant
            out.extend(_arc_points(C[0], C[1], reff, a0, d, scale_mm))
        return out

    def _one(p):
        q = _SP(_ring(list(p.exterior.coords)),
                [_ring(list(h.coords)) for h in p.interiors])
        return q if q.is_valid else q.buffer(0)

    def _assemble(parts):
        """Flatten to Polygon / MultiPolygon.  `buffer(0)` on a self-touching
        filleted ring can itself return a MultiPolygon, and feeding those to
        MultiPolygon() raises 'Sequences of multi-polygons are not valid
        arguments' — which the callers only see as "keeping sharp rotor" while
        an optimiser subprocess reports it as a geometry verdict."""
        flat = []
        for q in parts:
            if q is None or getattr(q, "is_empty", True):
                continue
            if hasattr(q, "geoms"):
                flat.extend(g for g in q.geoms
                            if hasattr(g, "exterior") and g.area > 0.0)
            elif hasattr(q, "exterior") and q.area > 0.0:
                flat.append(q)
        if not flat:
            return poly
        return flat[0] if len(flat) == 1 else _SMP(flat)

    if poly.geom_type == "MultiPolygon":
        return _assemble([_one(g) for g in poly.geoms])
    return _assemble([_one(poly)])


def _round_corners_vertex(poly, r, ang_min_deg=8.0, ang_max_deg=168.0,
                          surface_band=1.5, scale_mm=_DEFAULT_SCALE_MM):
    """Rotor pole-tip rounding: fillet only the corners within `surface_band` of
    the ring's max radius (the air-gap tips).  The deeper rows (shoulders, inner
    corners, magnet-side pocket corners) stay SHARP.  The rotor is centred at the
    origin, so |vertex| is its air-gap distance."""
    def _sel(radii, P):
        if not surface_band:
            import numpy as _np
            return _np.ones(len(radii), bool)
        return radii >= (float(radii.max()) - surface_band)
    return _fillet_ring_corners(poly, r, _sel, ang_min_deg, ang_max_deg, scale_mm)


def _round_corners_at_radius(poly, target_r, r_tol, r_fillet, scale_mm,
                             min_angle_deg=20.0):
    """Stator slot-corner rounding: fillet the sharp corners that sit within
    `r_tol` of `target_r` (the OD band for stator_fillet_r, the bore band for
    stator_fillet_r1) — the 2-D mirror of the 3-D `.fillet()` edge selectors.

    Replaces the old per-corner `_fillet_corner` walk, which placed the tangent
    point at t = r/tan(half) along the STRAIGHT line to the immediately preceding
    vertex.  On a discretised boundary that tangent length routinely exceeded the
    neighbouring chord, so the arc start jumped BACKWARDS past a vertex that was
    then left in the ring — a spike that `buffer(0)` repaired into 0.005-0.08 mm
    sliver edges on the stator OD.  Walking the real boundary and consuming the
    swallowed vertices removes the defect at its source."""
    import numpy as _np

    def _sel(radii, P):
        return _np.abs(radii - float(target_r)) < float(r_tol)
    return _fillet_ring_corners(poly, r_fillet, _sel,
                                ang_min_deg=0.0,
                                ang_max_deg=180.0 - float(min_angle_deg),
                                scale_mm=scale_mm)


class CadQueryMotor:
    """Parametric motor geometry engine using CadQuery."""
    
    def __init__(self):
        self.parameters: Dict = {}
        self.parts: Dict = {}
        self.assembly = None
        self._load_defaults_from_config()
    
    def _load_defaults_from_config(self) -> None:
        """Load default parameters from motor_config.yaml."""
        try:
            from motor_ai_sim.config import get_geometry_params
            params = get_geometry_params()
            # Convert MotorGeometryParams to dict with proper mapping
            self.parameters = self._map_api_to_cadquery(params.to_dict())
        except Exception as e:
            print(f"Warning: Could not load config: {e}")
            # Fall back to hardcoded defaults
            self.parameters = self._get_hardcoded_defaults()
    
    def _map_api_to_cadquery(self, api_params: Dict) -> Dict:
        """Map API parameter names to CadQuery parameter names.
        
        Uses derived_params from config/motor_config.yaml to compute values.
        All parameters should come from config - api_params are overrides.
        """
        # Get geometry params from config - this is the single source of truth
        try:
            from motor_ai_sim.config import get_geometry_params
            config_params = get_geometry_params().to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to load config: {e}")
        
        # Start with config params as defaults
        mapped = config_params.copy()
        
        # Override with any API params that are provided
        # This allows runtime overrides while keeping config as source of truth
        for key, value in api_params.items():
            if value is not None:
                mapped[key] = value
        
        # Pole/slot COUNT is defined by the geometry's magnets/slots: num_poles and
        # num_slots are AUTHORITATIVE.  The segment view (num_seg × *_per_segment) is
        # the winding-periodicity representation DERIVED from them (num_seg = the
        # symmetry = gcd(slots, poles)).  Only fall back to the segment product when
        # the explicit counts are absent — otherwise a STALE num_seg left over from a
        # different motor (e.g. a 24/28 design) would silently rebuild the wrong pole
        # count (24 slots / 28 poles) for a 12/14 motor.
        import math as _math
        # Priority: an OVERRIDE's explicit counts > the OVERRIDE's segment form >
        # the config's counts.  Read the segment form from api_params (the caller's
        # override), NOT from `mapped` — `mapped` is seeded from the config, which
        # ALWAYS carries num_slots/num_poles, so a preset that only supplies the
        # segment view (num_seg × *_per_segment, e.g. the 40 mm 2×6/2×7 = 12s/14p)
        # would otherwise be silently overridden by the config's 24 s / 20 p and mesh
        # the wrong winding onto the wrong geometry (T ~= 0, singular sector solve).
        _P = api_params.get('num_poles'); _S = api_params.get('num_slots')
        if _P is None and api_params.get('num_seg') and api_params.get('num_poles_per_segment'):
            _P = api_params['num_seg'] * api_params['num_poles_per_segment']
        if _S is None and api_params.get('num_seg') and api_params.get('num_slots_per_segment'):
            _S = api_params['num_seg'] * api_params['num_slots_per_segment']
        if _P is None:
            _P = mapped.get('num_poles')
        if _S is None:
            _S = mapped.get('num_slots')
        if _P is not None and _S is not None:
            _P = int(round(_P)); _S = int(round(_S))
            mapped['num_poles'] = _P
            mapped['num_slots'] = _S
            _seg = _math.gcd(_S, _P) or 1            # winding periodicity / mesh symmetry
            mapped['num_seg'] = _seg
            mapped['num_poles_per_segment'] = _P // _seg
            mapped['num_slots_per_segment'] = _S // _seg
        
        # Derived fields, from the MERGED primaries — the ONE derivation shared
        # with MotorGeometryParams._compute_derived and merge_geo_override.  It
        # runs AFTER the count resolution above so angle_slot/angle_pole/
        # slot_pitch/pole_pitch land on the counts this motor is actually built
        # with, and it replaces the four hand-written radius lines that used to
        # sit above the count block (same formulas, one copy).
        #
        # `mapped` is seeded from the SHARED CONFIG, so every derived name the
        # config stores arrives here describing the CONFIG's motor; an api_params
        # override supplies primaries only.  The radii were already recomputed —
        # `slot_width` was NOT, and the Mesh tab sizes its element from
        # `motor.parameters['slot_width']` (routes/simulation.py) to show the mesh
        # the solver builds.  It therefore drew a DIFFERENT mesh whenever the
        # config held a different design than the request.  Same leak as the
        # solver's own (see derived_geometry), same fix.
        from motor_ai_sim.geometry.motor_geometry import derived_geometry
        mapped.update(derived_geometry(mapped))

        # shaft_radius is NOT part of that shared derivation: CadQuery means
        # "the radius of the shaft hole under the rotor bore" (rotor_inner_radius
        # − shaft_height) while MotorGeometryParams.shaft_radius means the bore
        # itself.  Two different quantities under one name; unifying them moves
        # CAD geometry and is not this fix.
        if 'rotor_inner_radius' in mapped and 'shaft_height' in mapped:
            mapped['shaft_radius'] = mapped['rotor_inner_radius'] - mapped['shaft_height']

        if 'rotor_inner_radius' in mapped and 'shaft_height' in mapped:
            mapped['shaft_inner_radius'] = mapped['rotor_inner_radius'] - mapped['shaft_height']

        # Ensure magnet parameters exist
        for key in ['magnet_fill_down', 'magnet_fill_up', 'magnet_fill_radius', 'magnet_up_gap', 'magnet_down_height',
                    'magnet_lamination',        # AXIAL slice length, mm (loss factor in solver); 0 = solid
                    'magnet_lamination_tan']:   # in-plane segment size, mm (geometry split); 0 = off
            if key not in mapped:
                mapped[key] = config_params.get(key, 0.0)
        
        return mapped
    
    def _get_hardcoded_defaults(self) -> Dict:
        """Get default parameters from config/motor_config.yaml.
        
        This method loads all parameters from the config file, ensuring
        a single source of truth for all motor parameters.
        """
        try:
            from motor_ai_sim.config import get_geometry_params
            params = get_geometry_params()
            # Map API params to CadQuery internal parameters
            return self._map_api_to_cadquery(params.to_dict())
        except Exception as e:
            print(f"Warning: Could not load config: {e}")
            # Fallback - but this should never happen if config is valid
            raise RuntimeError(
                "Failed to load config/motor_config.yaml. "
                "All parameters must be defined in the config file."
            )
    
    def set_parameters(self, params: Dict) -> None:
        """Set motor geometry parameters (updates defaults from config)."""
        # Start with current parameters (from config)
        updated = self.parameters.copy() if self.parameters else self._get_hardcoded_defaults()
        
        # Map API params to CadQuery params first
        mapped_params = self._map_api_to_cadquery(params)
        
        # Update with mapped params
        updated.update(mapped_params)
        self.parameters = updated
        
    def get_parameter_hash(self) -> str:
        """Get hash of current parameters for caching."""
        param_str = json.dumps(self.parameters, sort_keys=True)
        return hashlib.sha256(param_str.encode()).hexdigest()[:16]
    
    def build_all(self) -> Dict:
        """
        Build all components. 
        Rotor has cavities, magnets are separate, and coils are separate per slot.
        """
        if not _import_cadquery():
            raise RuntimeError("CadQuery not found")
        
        import cadquery as cq
        
        # 1. Stator and Shaft
        self.parts['stator_core'] = self._create_stator(cq)
        self.parts['shaft'] = self._create_shaft(cq)

        # 1b. Sliding-band air rings — first-class components with material Air.
        # in_band  rotates with the rotor (rotor_outer..mid_radius)
        # out_band stays with the stator (mid_radius..stator_inner)
        try:
            self.parts['in_band']  = self._create_in_band(cq)
            self.parts['out_band'] = self._create_out_band(cq)
        except Exception as e:
            print(f"Failed to build in_band / out_band: {e}")
        
        # 2. Magnets and Rotor Core with Cavities
        magnets_list = self._create_magnets(cq)
        rotor_solid = self._create_rotor(cq)
        
        for i, magnet in enumerate(magnets_list):
            if magnet is not None:
                rotor_solid = rotor_solid.cut(magnet) # Cut hole in rotor
                self.parts[f'magnet_{i}'] = magnet    # Keep magnet separate

        # NOTE on rotor_fill_r: the rotor corners are rounded in the 2D physics
        # geometry (get_2d_polygons / get_2d_mesh_data, per-edge clamped vertex
        # fillet).  A CadQuery BRep fillet on THIS 3D solid can't take the full
        # radius near the ~1.2 mm bridges (StdFail_NotDone) and is slow, so the
        # /api/geometry/mesh route instead swaps rotor_core for the extruded
        # filleted 2D mesh (get_extruded_mesh_data) — exact radius, fast,
        # identical to the physics.  The solid here stays sharp on purpose.
        self.parts['rotor_core'] = rotor_solid
        
        # 3. Individual Coils (one object per slot)
        try:
            coils_list = self._create_coils(cq)
            for i, coil_stack in enumerate(coils_list):
                self.parts[f'coil_{i}'] = coil_stack
        except Exception as e:
            print(f"Failed to build coils: {e}")
            
        return self.parts
        
    def _create_stator(self, cq) -> Any:
        """Create stator with radial slots/teeth."""
        import math
        p = self.parameters

        outer_r = p['stator_outer_radius']
        inner_r = p['stator_inner_radius']
        core_h     = p['core_thickness']
        slot_height = p['slot_height']
        stator_w   = p['motor_length']   # axial stack length (single source)
        num_slots  = int(p['num_slots'])
        tooth_width  = p['tooth_width']
        tooth2_width = p.get('tooth2_width', 4.5)
        cut_width    = p.get('cut_width', 2.0)
        wire_w     = p['wire_width']
        ins_w      = p['insulation_thickness']
        wire_d_x   = p['wire_spacing_x']
        # Stator corner fillets (WANTED on every motor):
        #   stator_fillet_r  → rounds OUTER-ring corners (stator OD profile)
        #   stator_fillet_r1 → rounds INNER / air-gap-side corners (tooth tips, slot mouths)
        # The deep slot-POCKET corners (where the coil sits) stay SHARP automatically:
        # the fillet is radius-gated to outer_r / inner_r, and the pocket is at mid-radius.
        # (See [[slot_fillet_root_cause]] — earlier I wrongly killed these entirely.)
        slot_fillet_r  = p.get('stator_fillet_r',  0.0)
        slot_fillet_r1 = p.get('stator_fillet_r1', 0.0)

        slot_w  = wire_w + ins_w*2 + wire_d_x
        slot_h  = slot_height
        slot_x  = tooth_width / 2
        slot_y  = outer_r - core_h
        half_slots  = num_slots // 2
        slot_angle  = 360.0 / half_slots

        # ── Compound-cutter geometry ──────────────────────────────────────────
        # All cuts are unioned into one solid then cut in a single boolean.
        # This is ~70× faster than sequential cuts.
        cut_x  = tooth_width/2 + ins_w*2 + wire_w + wire_d_x*2 + tooth2_width
        fill_r = ((inner_r + cut_width) * sin(radians(slot_angle/2)) - cut_x) \
                 / (1 - sin(radians(slot_angle/2)))
        rr   = inner_r + cut_width + fill_r
        ext  = outer_r * 2
        p1   = (cut_x, ext)
        p2   = (cut_x, rr * cos(radians(slot_angle/2)))
        p3   = (cut_x + fill_r, rr * cos(radians(slot_angle/2)))
        p4   = (ext * tan(radians(slot_angle/2)), ext)

        # Create stator as a solid ring
        stator = (
            cq.Workplane("XY")
            .circle(outer_r)
            .circle(inner_r)
            .extrude(stator_w)
        )

        cutters = []
        for i in range(half_slots):
            angle = i * slot_angle
            # Trapezoid wedge (+X)
            cutters.append(
                cq.Workplane("XY")
                .moveTo(p1[0], p1[1]).lineTo(p2[0], p2[1])
                .lineTo(p3[0], p3[1]).lineTo(p4[0], p4[1])
                .close().extrude(stator_w + 1)
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Trapezoid wedge (-X mirror)
            cutters.append(
                cq.Workplane("XY")
                .moveTo(-p1[0], p1[1]).lineTo(-p2[0], p2[1])
                .lineTo(-p3[0], p3[1]).lineTo(-p4[0], p4[1])
                .close().extrude(stator_w + 1)
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Fillet cylinder at p3 (+X)
            cutters.append(
                cq.Workplane("XY").circle(fill_r).extrude(stator_w + 1)
                .translate((p3[0], p3[1], 0))
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Fillet cylinder at p3 (-X)
            cutters.append(
                cq.Workplane("XY").circle(fill_r).extrude(stator_w + 1)
                .translate((-p3[0], p3[1], 0))
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Slot rectangle (+X)
            cutters.append(
                cq.Workplane("XY")
                .rect(slot_w, -slot_h*2, centered=(False, False))
                .extrude(stator_w + 1)
                .translate((slot_x, slot_y, 0))
                .rotate((0,0,0),(0,0,1), angle)
            )
            # Slot rectangle (-X)
            cutters.append(
                cq.Workplane("XY")
                .rect(-slot_w, -slot_h*2, centered=(False, False))
                .extrude(stator_w + 1)
                .translate((-slot_x, slot_y, 0))
                .rotate((0,0,0),(0,0,1), angle)
            )

        # Single boolean cut
        tool = cutters[0]
        for c in cutters[1:]:
            tool = tool.union(c)
        stator = stator.cut(tool)

        import cadquery as _cq

        # ── Fillet: OUTER RADIUS corners ─────────────────────────────────────
        # |Z edges where trapezoid walls meet the outer cylinder (r ≈ outer_r)
        if slot_fillet_r > 0:
            _r_lo = outer_r - 0.5
            _r_hi = outer_r + 0.2

            class _OuterRingSelector(_cq.selectors.Selector):
                def filter(self_, obj_list):
                    return [e for e in obj_list
                            if _r_lo < (e.Center().x**2 + e.Center().y**2)**0.5 < _r_hi]

            try:
                stator = stator.edges("|Z").edges(_OuterRingSelector()).fillet(slot_fillet_r)
            except Exception as ex:
                print(f"[stator] outer-ring fillet failed (r={slot_fillet_r}): {ex}")

        # ── Fillet: INNER RADIUS corners ─────────────────────────────────────
        # |Z edges where slot walls and trapezoid walls meet the inner cylinder
        # (r ≈ inner_r). These are the corners visible in the red circle.
        if slot_fillet_r1 > 0:
            _r_lo1 = inner_r - 0.8
            _r_hi1 = inner_r + 0.8

            class _InnerRingSelector(_cq.selectors.Selector):
                def filter(self_, obj_list):
                    return [e for e in obj_list
                            if _r_lo1 < (e.Center().x**2 + e.Center().y**2)**0.5 < _r_hi1]

            try:
                stator = stator.edges("|Z").edges(_InnerRingSelector()).fillet(slot_fillet_r1)
            except Exception as ex:
                print(f"[stator] inner-ring fillet failed (r1={slot_fillet_r1}): {ex}")

        return stator
        
    def _create_shaft(self, cq) -> Any:
        """Create motor shaft."""
        p = self.parameters

        shaft_r = p['rotor_inner_radius']
        shaft_in = p['shaft_inner_radius']
        length = p['motor_length']

        # Print shaft parameters for debugging
        print(f"[DEBUG] _create_shaft: shaft_r={shaft_r}, shaft_in={shaft_in}")

        shaft = (
            cq.Workplane("XY")
            .circle(shaft_r)
            .circle(shaft_in)
            .extrude(length)
        )

        return shaft

    def _create_in_band(self, cq) -> Any:
        """Inner air domain: full DISK r=0..mid_r minus rotor+magnets+shaft.

        Sliding-band FEM rotates this rigidly with the rotor.  Material
        is Air; the disk captures every internal air pocket (shaft bore,
        inter-magnet wedges, inner half of the air gap).
        """
        p = self.parameters
        rotor_or = p['rotor_outer_radius']
        inner_r  = p['stator_inner_radius']
        length   = p['motor_length']
        mid_r    = 0.5 * (rotor_or + inner_r)
        return (
            cq.Workplane("XY")
            .circle(mid_r)
            .extrude(length)
        )

    def _create_out_band(self, cq) -> Any:
        """Outer air domain: ANNULUS mid_r..r_outer minus stator+coils.

        The outer edge (r_outer) is the far-field Dirichlet boundary for
        the magnetic vector potential A_z.  Stationary in the lab frame.
        """
        p = self.parameters
        outer_r  = p['stator_outer_radius']
        rotor_or = p['rotor_outer_radius']
        inner_r  = p['stator_inner_radius']
        length   = p['motor_length']
        mid_r    = 0.5 * (rotor_or + inner_r)
        # Default outer_air_factor=1.3 if not set
        r_outer  = float(p.get('outer_air_factor', 1.3)) * outer_r
        return (
            cq.Workplane("XY")
            .circle(r_outer)
            .circle(mid_r)
            .extrude(length)
        )
    
    def _create_magnets(self, cq) -> List[Any]:
        """Create rotor magnets."""
        p = self.parameters
        rotor_inner_r = p['rotor_inner_radius']
        rotor_outer_r = p['rotor_outer_radius']
        num_poles = int(p['num_poles'])
        width = p['motor_length']

        mag_h = p['magnet_height']                  # magnet height
        rotor_house_h = p['rotor_house_height']     # rotor housing thickness
        mag_fill_down = p['magnet_fill_down']       # down fill ratio of the magnet 
        mag_fill_up = p['magnet_fill_up']           # up fill ratio of the magnet 
        mag_fill_r = p['magnet_fill_radius']   # magnet fillet radius 
        mag_up_gap = p['magnet_up_gap']             # magnet cut up gap
        mag_down_h = p['magnet_down_height']        # magnet down height 
        pole_angle = 360.0 / num_poles
        
        # Print magnet parameters for debugging
        print(f"[DEBUG] _create_magnets: mag_fill_down={mag_fill_down}, pole_angle={pole_angle}, num_poles={num_poles}")
        magnet_r = rotor_inner_r + rotor_house_h
        print(f"[DEBUG] _create_magnets: rotor_inner_r={rotor_inner_r}, magnet_r={magnet_r}")
        
        magnets = []
        
        # Calculate angles in radians for math functions
        angle_down = radians(pole_angle * mag_fill_down / 2)
        angle_up = radians(pole_angle * mag_fill_up / 2)
        
        p1 = (magnet_r * sin(angle_down), magnet_r * cos(angle_down))      
        p2 = ((magnet_r + mag_down_h) * sin(angle_down), (magnet_r + mag_down_h) * cos(angle_down))      
        p3 = ((rotor_outer_r - mag_up_gap) * sin(angle_up), (rotor_outer_r - mag_up_gap) * cos(angle_up))    
        p4 = (-(rotor_outer_r - mag_up_gap) * sin(angle_up), (rotor_outer_r - mag_up_gap) * cos(angle_up))           
        p5 = (-(magnet_r + mag_down_h) * sin(angle_down), (magnet_r + mag_down_h) * cos(angle_down))       
        p6 = (-magnet_r * sin(angle_down), magnet_r * cos(angle_down))      
        
        for i in range(num_poles):
            angle = i * pole_angle
            
            # Create magnet at origin then rotate/translate
            magnet = (
                cq.Workplane("XY")
                .polyline([p1, p2, p3, p4, p5, p6])
                .close()        
                .extrude(width)
            )
            
            if mag_fill_r > 0:
                try:
                    magnet = magnet.edges(">Y and |Z").fillet(mag_fill_r)
                except Exception as e:
                    print(f"Warning: Could not apply fillet to magnet: {e}")
            
            # Rotate to final position
            magnet = magnet.rotate((0, 0, 0), (0, 0, 1), angle)

            magnets.append(magnet)
            
        return magnets
    def _create_rotor(self, cq) -> Any:
        """Create rotor hub."""
        p = self.parameters
        
        rotor_outer_r = p['rotor_outer_radius']
        rotor_inner_r = p['rotor_inner_radius']
        width = p['motor_length']
        num_poles = int(p['num_poles'])
        magnet_hole = p['rotor_hole']
        pole_angle = 360.0 / num_poles
        mag_fill_up = p['magnet_fill_up']
        mag_h = p['magnet_height']
        width = p['motor_length']

        mag_angle_up = radians(pole_angle * mag_fill_up*magnet_hole / 2)
        rec_w = 2*rotor_outer_r * sin(mag_angle_up)
        
        rotor = (
            cq.Workplane("XY")
            .circle(rotor_outer_r)
            .circle(rotor_inner_r)
            .extrude(width)
        )

        for i in range(num_poles):
            angle = i * pole_angle
            # Create positive side slot 
            cut_up = (
                cq.Workplane("XY")
                .rect(rec_w, -mag_h, centered=(False, False))
                .extrude(width + 1)
                .translate((-rec_w/2, rotor_outer_r, 0))
                .rotate((0, 0, 0), (0, 0, 1), angle)
            )
            rotor = rotor.cut(cut_up)
        
        return rotor
   
    def _create_coils(self, cq) -> List[Any]:
        """Create hairpin coils wound in stator slots - high-fidelity spiral windings.
        
        Hairpin winding structure:
        - Straight legs passing through stator slots
        - Crown (U-turn) on FRONT side connecting the two legs
        - Leads (S-bend exit) on BACK side for connection to next layer
        """
        import math
        p = self.parameters
        
        # Core parameters
        outer_r = p['stator_outer_radius']
        inner_r = p['stator_inner_radius']
        core_h = p['core_thickness']
        stator_w = p['motor_length']
        num_slots = int(p['num_slots'])
        tooth_width = p['tooth_width']
        
        # Wire parameters
        wire_w = p['wire_width']         # 4.0 mm
        wire_h = p['wire_height']        # 0.6 mm
        wire_d_x = p['wire_spacing_x']     # 0.1 mm
        wire_d_y = p['wire_spacing_y']     # 0.13 mm
        ins_w  = p['insulation_thickness']
        num_wires = int(p['num_wires_per_slot'])
        # Feasibility clamp (match get_2d_polygons / geometry_constraints): the
        # winding must fit the slot or coils overflow the bore onto the rotor.
        _wh_max = (float(p.get('slot_height', 0.0)) - 2.0 * ins_w) / max(1, num_wires) - wire_d_y
        if _wh_max > 1e-3 and wire_h > _wh_max:
            wire_h = _wh_max

        # Calculate slot dimensions
        half_slots = num_slots // 2
        slot_angle = 360.0 / half_slots
        slot_radial_depth = outer_r - inner_r
        available_width = tooth_width - 2 * ins_w
        
        # Crown and S-bend parameters
        crown_radius = wire_w * 1.5
        sbend_height = wire_h * 2
        sbend_offset = wire_w * 0.8
        
    # Top starting position (X is the vertical axis in the slot)
    # Calculation: Start from inner radius + insulation + full height of the stack
        top_y = outer_r - core_h - ins_w - wire_d_y/2
    
    # Horizontal Y positions for the two columns (centered around Y=0)
        right_x = tooth_width / 2 + ins_w + wire_d_x/2
    
        coils = [] # Renamed from final_coils
    
        for i in range(half_slots):
            angle = i * slot_angle
            wires = [] # Renamed from slot_wires
        
            for step_y in range(num_wires):
            # Calculate current Y position for this layer (stacking DOWNWARDS)
                current_y = top_y - step_y *(wire_h+wire_d_y) 
            
                # Define Right Wire Polygon coordinates
                right_pts = [
                    (right_x, current_y ),          
                    (right_x + wire_w, current_y ),   
                    (right_x + wire_w, current_y - wire_h),            
                    (right_x, current_y - wire_h)                    
                ]
            
                # Define Left Wire Polygon coordinates
                left_pts = [
                    (-right_x, current_y ),          
                    (-right_x - wire_w, current_y ),   
                    (-right_x - wire_w, current_y - wire_h),            
                    (-right_x, current_y - wire_h)                    
                ]
            
                # Create 3D geometry via extrusion along Z axis
                # .translate centers the coil along the motor length
                right_wire = (cq.Workplane("XY").polyline(right_pts).close().extrude(stator_w))
                left_wire = (cq.Workplane("XY").polyline(left_pts).close().extrude(stator_w))
            
                # Rotate and store individual wires
                wires.append(right_wire.rotate((0,0,0), (0,0,1), angle))
                wires.append(left_wire.rotate((0,0,0), (0,0,1), angle))
            
        # Instead of slow O(N^2) boolean union, create a Compound for fast export
            if wires:
                valid_wires = [w for w in wires if w is not None]
                if valid_wires:
                    # Use Compound to group wires without expensive boolean operations
                    compound = cq.Compound.makeCompound([w.val() for w in valid_wires])
                    coils.append(compound)
        
        return coils    
    
    def export_stl(self, output_dir: str, tolerance: float = 0.1) -> Dict[str, str]:
        """Export all components to STL files."""
        if not _import_cadquery():
            raise RuntimeError("CadQuery is not available")
            
        from cadquery import exporters
        
        os.makedirs(output_dir, exist_ok=True)
        stl_files = {}
        
        if not self.parts:
            self.build_all()
            
        for name, part in self.parts.items():
            stl_path = os.path.join(output_dir, f"{name}.stl")
            try:
                # Use the newer CadQuery export API with exportType string
                exporters.export(part, stl_path, exportType='STL', tolerance=tolerance)
                stl_files[name] = stl_path
                print(f"Exported {name} to {stl_path}")
            except Exception as e:
                print(f"Error exporting {name}: {e}")
                
        return stl_files
    
    def get_mesh_data(self, component: str) -> Optional[Dict]:
        """Get mesh data for a component."""
        if not _import_cadquery():
            return None
            
        if not self.parts:
            self.build_all()
            
        if component not in self.parts:
            return None
            
        try:
            shape = self.parts[component]
            # Use OCP's direct tessellation for massive speedup (no temp file IO)
            if hasattr(shape, 'val'):
                solid = shape.val()
            else:
                solid = shape
                
            vertices, faces = solid.tessellate(0.1)
            
            # Format to basic lists
            vertices_list = [[v.x, v.y, v.z] for v in vertices]
            
            return {
                'vertices': vertices_list,
                'faces': faces,
                'vertex_count': len(vertices_list),
                'face_count': len(faces),
            }
        except Exception as e:
            print(f"Error tessellating {component}: {e}")
            return None
    
    def get_all_mesh_data(self) -> Dict[str, Dict]:
        """Get mesh data for all components."""
        mesh_data = {}
        
        if not self.parts:
            self.build_all()
            
        for name in self.parts:
            data = self.get_mesh_data(name)
            if data:
                mesh_data[name] = data
                
        return mesh_data
    
    def _build_insulation_polys(self):
        """(wire_insulation_polys, slot_insulation_polys) — shapely Polygons in mm.

        SINGLE SOURCE used by get_2d_polygons (FEM/cost) AND get_2d_mesh_data (3D
        viewer / tree), so the two never drift apart.  Per slot column:
          • wire enamel  = wire-column envelope (grown wire_spacing_x/2 in X,
            wire_spacing_y/2 in Y) MINUS the copper wires.  polyimide.
          • slot liner   = a U-band of thickness insulation_thickness on the THREE
            iron-facing sides (two tooth walls + the yoke); open at the air-gap side.
        """
        from math import radians  # noqa: F401 (kept for parity with callers)
        from shapely.geometry import Polygon as SPoly
        from shapely.ops import unary_union
        from shapely.affinity import rotate as _affine_rotate
        p = self.parameters
        outer_r = p['stator_outer_radius']; inner_r = p['stator_inner_radius']
        core_h = p['core_thickness']; tooth_w = p['tooth_width']
        wire_w = p['wire_width']; ins_w = p['insulation_thickness']
        wire_dx = p['wire_spacing_x']; wire_dy = p['wire_spacing_y']; wire_h = p['wire_height']
        num_wires = int(p['num_wires_per_slot']); num_slots = int(p['num_slots'])
        _wh_max = (float(p.get('slot_height', 0.0)) - 2.0 * ins_w) / max(1, num_wires) - wire_dy
        if _wh_max > 1e-3 and wire_h > _wh_max:
            wire_h = _wh_max
        half_slots = num_slots // 2
        slot_angle_deg = 360.0 / half_slots
        right_x = tooth_w / 2 + ins_w + wire_dx / 2
        top_y_c = (outer_r - core_h) - ins_w - wire_dy / 2
        min_wire_r = inner_r + ins_w
        n_fit = 0
        for step in range(num_wires):
            if top_y_c - step * (wire_h + wire_dy) - wire_h < min_wire_r:
                break
            n_fit += 1
        wpolys = []; spolys = []
        if n_fit > 0:
            for i in range(half_slots):
                ang = i * slot_angle_deg
                for sx0 in (right_x, -(right_x + wire_w)):
                    lw = []
                    for s in range(n_fit):
                        cy = top_y_c - s * (wire_h + wire_dy)
                        lw.append(SPoly([(sx0, cy), (sx0 + wire_w, cy),
                                         (sx0 + wire_w, cy - wire_h), (sx0, cy - wire_h)]))
                    copper = unary_union(lw)
                    y_top = top_y_c; y_bot = top_y_c - (n_fit - 1) * (wire_h + wire_dy) - wire_h
                    el = sx0 - wire_dx / 2; er = sx0 + wire_w + wire_dx / 2
                    et = y_top + wire_dy / 2; eb = y_bot - wire_dy / 2
                    env = SPoly([(el, et), (er, et), (er, eb), (el, eb)])
                    wi = env.difference(copper)
                    if not wi.is_valid: wi = wi.buffer(0)
                    if (not wi.is_empty) and wi.area > 1e-9:
                        wpolys.append(_affine_rotate(wi, ang, origin=(0, 0)))
                    outer = SPoly([(el - ins_w, et + ins_w), (er + ins_w, et + ins_w),
                                   (er + ins_w, eb), (el - ins_w, eb)])
                    liner = outer.difference(env)
                    if not liner.is_valid: liner = liner.buffer(0)
                    if (not liner.is_empty) and liner.area > 1e-9:
                        spolys.append(_affine_rotate(liner, ang, origin=(0, 0)))
        return wpolys, spolys

    def get_2d_mesh_data(self) -> Dict[str, Dict]:
        """
        Build flat 2D cross-section meshes for all motor components.
        All triangles lie in the z=0 plane; each component gets a tiny
        z-offset (0…5 mm) so Three.js depth-sorts them correctly.
        No CadQuery / OCCT required – pure shapely + earcut.
        """
        from math import pi, sin, cos, tan, radians, sqrt
        try:
            from shapely.geometry import Polygon as SPoly, MultiPolygon as SMPoly
            from shapely.ops import unary_union
            import numpy as np
            import mapbox_earcut as earcut
        except ImportError as exc:
            print(f"[2d] missing dependency: {exc}")
            return {}

        p = self.parameters

        # ── radii ──────────────────────────────────────────────────────────
        outer_r   = p['stator_outer_radius']
        inner_r   = p['stator_inner_radius']       # stator bore / air-gap inner
        rotor_or  = p['rotor_outer_radius']
        rotor_ir  = p['rotor_inner_radius']
        shaft_r   = p['shaft_inner_radius']

        # ── slot / tooth params ────────────────────────────────────────────
        num_slots   = int(p['num_slots'])
        core_h      = p['core_thickness']
        tooth_w     = p['tooth_width']
        tooth2_w    = p.get('tooth2_width', 4.5)
        cut_w       = p.get('cut_width', 2.0)
        wire_w      = p['wire_width']
        ins_w       = p['insulation_thickness']
        wire_dx     = p['wire_spacing_x']
        wire_dy     = p['wire_spacing_y']
        wire_h      = p['wire_height']
        num_wires   = int(p['num_wires_per_slot'])
        # ── Feasibility clamp (match get_2d_polygons / geometry_constraints) ──
        # The winding must fit the slot, else coils overflow the bore across the
        # air gap onto the rotor (overlapping meshes / invalid cross-section).
        _wh_max = (float(p.get('slot_height', 0.0)) - 2.0 * ins_w) / max(1, num_wires) - wire_dy
        if _wh_max > 1e-3 and wire_h > _wh_max:
            wire_h = _wh_max

        # ── magnet params ──────────────────────────────────────────────────
        num_poles   = int(p['num_poles'])
        mag_h       = p['magnet_height']
        rotor_hh    = p['rotor_house_height']
        mag_fd      = p['magnet_fill_down']
        mag_fu      = p['magnet_fill_up']
        mag_up_gap  = p['magnet_up_gap']
        mag_down_h  = p['magnet_down_height']
        mag_fill_r  = p['magnet_fill_radius']
        magnet_r    = rotor_ir + rotor_hh

        # ── rotor pocket params ────────────────────────────────────────────
        magnet_hole = p['rotor_hole']

        # Machine scale — every arc tolerance in this build is tied to it.
        scale_mm = 2.0 * outer_r

        # helper: circle polygon (see _circle_points for why this stays a 256-gon)
        _circle = _circle_points

        # helper: rotate 2-D point
        def _rot(x, y, a_rad):
            c, s = cos(a_rad), sin(a_rad)
            return x*c - y*s, x*s + y*c

        # helper: triangulate a shapely (Multi)Polygon → dict
        def _tri(poly, z: float) -> Optional[Dict]:
            if poly is None or poly.is_empty:
                return None
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                return None
            geoms = list(poly.geoms) if isinstance(poly, SMPoly) else [poly]
            all_verts: list = []
            all_faces: list = []
            base = 0
            for g in geoms:
                ext = np.array(g.exterior.coords[:-1], dtype=np.float64)
                holes_raw = [np.array(h.coords[:-1], dtype=np.float64)
                             for h in g.interiors]
                verts = np.vstack([ext] + holes_raw) if holes_raw else ext
                # earcut needs cumulative end-indices, not lengths
                lengths = [len(ext)] + [len(h) for h in holes_raw]
                rings_u32 = np.cumsum(lengths, dtype=np.uint32)
                tris  = earcut.triangulate_float64(verts.astype(np.float64), rings_u32)
                if tris is None or len(tris) == 0:
                    continue
                tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
                # flip winding so normals point +Z
                tris = tris[:, ::-1]
                all_verts.append(verts)
                all_faces.append(tris + base)
                base += len(verts)
            if not all_verts:
                return None
            V = np.vstack(all_verts)
            F = np.vstack(all_faces)
            V3 = np.column_stack([V, np.full(len(V), z)])
            return {
                'vertices':     V3.tolist(),
                'faces':        F.tolist(),
                'vertex_count': len(V3),
                'face_count':   len(F),
            }

        result: Dict[str, Dict] = {}

        # Z-offsets: tiny (0.1 mm steps) so all layers look coplanar from
        # front/top but never z-fight each other.
        Z_SHAFT  = 0.0
        Z_ROTOR  = 0.1
        Z_MAG    = 0.2   # magnets sit on top of rotor surface
        Z_STATOR = 0.1   # same level as rotor (non-overlapping regions)
        Z_COIL   = 0.2   # coils sit in stator slots (non-overlapping with magnets)

        # Magnet trapezoid shape (local, pointing +Y at pole angle=0)
        half_slots   = num_slots // 2
        slot_angle_r = 2*pi / half_slots
        pole_angle_r = 2*pi / num_poles
        angle_down   = pole_angle_r * mag_fd / 2
        angle_up_m   = pole_angle_r * mag_fu / 2
        mp1 = ( magnet_r * sin(angle_down),                  magnet_r * cos(angle_down))
        mp2 = ((magnet_r + mag_down_h) * sin(angle_down),   (magnet_r + mag_down_h) * cos(angle_down))
        mp3 = ((rotor_or - mag_up_gap) * sin(angle_up_m),   (rotor_or - mag_up_gap) * cos(angle_up_m))
        mp4 = (-(rotor_or - mag_up_gap) * sin(angle_up_m),  (rotor_or - mag_up_gap) * cos(angle_up_m))
        mp5 = (-(magnet_r + mag_down_h) * sin(angle_down),  (magnet_r + mag_down_h) * cos(angle_down))
        mp6 = (-magnet_r * sin(angle_down),                  magnet_r * cos(angle_down))
        mag_local = [mp1, mp2, mp3, mp4, mp5, mp6]

        # ── 1. SHAFT (hollow ring: shaft_inner_radius → rotor_inner_radius) ──
        shaft_outer_r = rotor_ir          # outer edge of shaft tube
        shaft_inner_r = shaft_r           # inner bore of shaft
        shaft_poly = SPoly(_circle(shaft_outer_r), [_circle(shaft_inner_r)])
        if not shaft_poly.is_valid:
            shaft_poly = shaft_poly.buffer(0)
        r = _tri(shaft_poly, z=Z_SHAFT)
        if r: result['shaft'] = r

        # ── 2+3. MAGNETS + ROTOR CORE ─────────────────────────────────────
        # Round ONLY the two top corners (mp3/mp4, outer/air-gap edge) to match
        # CadQuery's edges(">Y and |Z").fillet(mag_fill_r).
        # The same rounded polygon is used for BOTH the rotor pocket holes and
        # the magnet so there is no dark gap at the corners.

        def _fillet_corner(p_prev, p_corner, p_next, r):
            """Arc points (T1→T2 inclusive) for filleting one convex corner
            between two LONG straight edges (magnet hexagon)."""
            V  = np.array(p_corner, float)
            dA = np.array(p_prev,  float) - V;  dA /= np.linalg.norm(dA)
            dB = np.array(p_next,  float) - V;  dB /= np.linalg.norm(dB)
            cos_t  = float(np.clip(np.dot(dA, dB), -1.0, 1.0))
            half_a = acos(cos_t) / 2            # correct interior half-angle φ/2
            if sin(half_a) < 1e-9:              # ~180° corner → no fillet
                return [tuple(p_corner)]
            tan_len = r / tan(half_a)
            bis     = dA + dB;  bis /= np.linalg.norm(bis)
            center  = V + (r / sin(half_a)) * bis
            T1      = V + tan_len * dA
            T2      = V + tan_len * dB
            a1 = atan2(T1[1] - center[1], T1[0] - center[0])
            a2 = atan2(T2[1] - center[1], T2[0] - center[0])
            da = a2 - a1
            if da >  pi: da -= 2 * pi
            elif da < -pi: da += 2 * pi
            return _arc_points(center[0], center[1], r, a1, da, scale_mm)

        def _build_mag_poly(pts, fillet_r):
            """Hexagon with only the two top corners (indices 2,3) filleted."""
            if fillet_r <= 0:
                return SPoly(pts)
            try:
                new_pts = (pts[:2]
                           + _fillet_corner(pts[1], pts[2], pts[3], fillet_r)
                           + _fillet_corner(pts[2], pts[3], pts[4], fillet_r)
                           + pts[4:])
                return SPoly(new_pts)
            except Exception:
                return SPoly(pts)

        # Rectangular cut above each magnet — matches 3D _create_rotor cut_up:
        #   rect(rec_w, -mag_h).translate((-rec_w/2, rotor_outer_r)).rotate(angle)
        mag_angle_up_hole = pole_angle_r * mag_fu * magnet_hole / 2  # radians
        rec_w = 2 * rotor_or * sin(mag_angle_up_hole)
        rect_local = [
            (-rec_w / 2, rotor_or),
            ( rec_w / 2, rotor_or),
            ( rec_w / 2, rotor_or - mag_h),
            (-rec_w / 2, rotor_or - mag_h),
        ]

        rotor_outer_pts = _circle(rotor_or)
        rotor_inner_pts = _circle(rotor_ir)
        # Build the rotor body as a clean annulus first, then subtract
        # holes via shapely difference.  The previous approach passed
        # raw hole coordinate lists to SPoly(); when the rect_local
        # rectangle's top corners landed at r=sqrt((rec_w/2)^2 +
        # rotor_or^2) > rotor_or (i.e. just outside the rotor disk), the
        # hole crossed the exterior boundary, produced an invalid
        # polygon, and buffer(0) repaired it by dropping the bridges
        # between adjacent magnets.  Doing a proper shapely difference
        # avoids that.
        rotor_disk = SPoly(rotor_outer_pts, [rotor_inner_pts])
        if not rotor_disk.is_valid:
            rotor_disk = rotor_disk.buffer(0)

        mag_rot_polys = []
        hole_polys    = []
        for i in range(num_poles):
            a   = i * pole_angle_r
            pts = [_rot(x, y, a) for x, y in mag_local]
            mp  = _build_mag_poly(pts, mag_fill_r)
            if not mp.is_valid:
                mp = mp.buffer(0)

            rect_pts  = [_rot(x, y, a) for x, y in rect_local]
            rect_poly = SPoly(rect_pts)
            if not rect_poly.is_valid:
                rect_poly = rect_poly.buffer(0)

            hole = _safe_union(mp, rect_poly)
            if not hole.is_valid:
                hole = hole.buffer(0)
            hole_polys.append(hole)
            mag_rot_polys.append(mp)

        # One difference call with the union of all holes — preserves
        # bridges between adjacent magnets and clips any sliver that
        # would stick past rotor_or.
        rotor_poly = rotor_disk.difference(unary_union(hole_polys))
        if not rotor_poly.is_valid:
            rotor_poly = rotor_poly.buffer(0)
        # Round the rotor pole-tip sharp corners (rotor_fill_r) — same guarded
        # vertex fillet as get_2d_polygons, so the Mesh tab shows the rounded
        # rotor too.  Keep only if valid, >=85% area, same piece count (else
        # keep sharp — never break the thin inter-magnet bridges).
        _rfr = float(self.parameters.get('rotor_fill_r', 0.0) or 0.0)
        if _rfr > 1e-4:
            def _npoly(g): return len(g.geoms) if g.geom_type == 'MultiPolygon' else (0 if g.is_empty else 1)
            try:
                # sub-bridge surface band: round the air-gap tips only, keep the
                # magnet-side pole corners sharp (see get_2d_polygons for why).
                _band = min(1.5, 0.6 * float(self.parameters.get('magnet_up_gap', 1.5) or 1.5))
                _f = _round_corners_vertex(rotor_poly, _rfr, surface_band=_band,
                                           scale_mm=scale_mm)
                if not _f.is_valid: _f = _f.buffer(0)
                if (_f.is_valid and not _f.is_empty
                        and _f.area >= 0.85 * rotor_poly.area
                        and _npoly(_f) == _npoly(rotor_poly)):
                    rotor_poly = _f
            except Exception as _e:
                print(f"rotor_fill_r (mesh) failed ({_e}) -- keeping sharp rotor")
        rotor_poly = _sanitize_geom(rotor_poly, scale_mm, "mesh.rotor")
        r = _tri(rotor_poly, z=Z_ROTOR)
        if r: result['rotor_core'] = r

        for i, poly in enumerate(mag_rot_polys):
            r = _tri(poly, z=Z_MAG)
            if r: result[f'magnet_{i}'] = r

        # ── 4. STATOR CORE (ring with slot cutouts) z=1 ────────────────
        slot_angle_deg = 360.0 / half_slots
        cut_x  = tooth_w/2 + ins_w*2 + wire_w + wire_dx*2 + tooth2_w
        fill_r = ((inner_r + cut_w) * sin(radians(slot_angle_deg/2)) - cut_x) \
                 / (1 - sin(radians(slot_angle_deg/2)))
        rr  = inner_r + cut_w + fill_r
        ext = outer_r * 2

        p1s = (cut_x,  ext)
        p2s = (cut_x,  rr * cos(radians(slot_angle_deg/2)))
        p3s = (cut_x + fill_r, rr * cos(radians(slot_angle_deg/2)))
        p4s = (ext * tan(radians(slot_angle_deg/2)), ext)

        # base ring
        stator_poly = SPoly(_circle(outer_r), [_circle(inner_r)])

        cutters = []
        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            # +X trapezoid pre-merged with fill_r fillet circle at p3s
            # (circle overlaps trap → clean Polygon union, no tangency issues)
            trap_p = SPoly([_rot(*p1s, a), _rot(*p2s, a),
                             _rot(*p3s, a), _rot(*p4s, a)])
            cx, cy = _rot(p3s[0], p3s[1], a)
            circ_p = SPoly(_arc_points(cx, cy, fill_r, 0.0, 2*pi, scale_mm)[:-1])
            m_p = _safe_union(trap_p, circ_p)
            cutters.append(m_p if m_p.is_valid else m_p.buffer(0))

            # -X trapezoid pre-merged with fill_r fillet circle
            mp1n = (-p1s[0], p1s[1]); mp2n = (-p2s[0], p2s[1])
            mp3n = (-p3s[0], p3s[1]); mp4n = (-p4s[0], p4s[1])
            trap_n = SPoly([_rot(*mp1n, a), _rot(*mp2n, a),
                             _rot(*mp3n, a), _rot(*mp4n, a)])
            cxn, cyn = _rot(-p3s[0], p3s[1], a)
            circ_n = SPoly(_arc_points(cxn, cyn, fill_r, 0.0, 2*pi, scale_mm)[:-1])
            m_n = _safe_union(trap_n, circ_n)
            cutters.append(m_n if m_n.is_valid else m_n.buffer(0))

            # slot rectangles
            slot_w  = wire_w + ins_w*2 + wire_dx
            slot_h  = p['slot_height']
            slot_x  = tooth_w / 2
            slot_y  = outer_r - core_h
            # +X rect
            rx0, ry0 = slot_x, slot_y
            rect_pts_p = [(rx0, ry0), (rx0 + slot_w, ry0),
                          (rx0 + slot_w, ry0 - slot_h*2), (rx0, ry0 - slot_h*2)]
            cutters.append(SPoly([_rot(*pt, a) for pt in rect_pts_p]))
            # -X rect
            rect_pts_n = [(-rx0, ry0), (-rx0 - slot_w, ry0),
                          (-rx0 - slot_w, ry0 - slot_h*2), (-rx0, ry0 - slot_h*2)]
            cutters.append(SPoly([_rot(*pt, a) for pt in rect_pts_n]))

        tool = unary_union(cutters)
        stator_poly = stator_poly.difference(tool)
        # Filter any zero-area ghost fragments produced by Shapely difference
        # when tool boundaries are tangent to the stator ring boundary
        if isinstance(stator_poly, SMPoly):
            parts = [g for g in stator_poly.geoms if g.area > 0.1]
            stator_poly = parts[0] if len(parts) == 1 else SMPoly(parts)
        if not stator_poly.is_valid:
            stator_poly = stator_poly.buffer(0)

        # ── Stator corner rounding (outer-ring via fillet_r, air-gap side via fillet_r1) ──
        # Pocket corners stay sharp (radius-gated; pocket at mid-radius). [[slot_fillet_root_cause]]
        fillet_r  = p.get('stator_fillet_r',  0.0)
        fillet_r1 = p.get('stator_fillet_r1', 0.0)

        # Outer band = 0.45·core_thickness (cap 1.5) so it can't reach the slot-bottom
        # corners on small motors (see [[slot_fillet_root_cause]]).
        # Same shared corner-fillet core as get_2d_polygons — one implementation.
        _out_tol = min(1.5, 0.45 * core_h)
        if fillet_r > 0 and hasattr(stator_poly, 'exterior'):
            stator_poly = _round_corners_at_radius(stator_poly, outer_r, _out_tol,
                                                   fillet_r, scale_mm)
        if fillet_r1 > 0 and hasattr(stator_poly, 'exterior'):
            stator_poly = _round_corners_at_radius(stator_poly, inner_r, 1.0,
                                                   fillet_r1, scale_mm)
        stator_poly = _sanitize_geom(stator_poly, scale_mm, "mesh.stator")

        r = _tri(stator_poly, z=Z_STATOR)
        if r: result['stator_core'] = r

        # ── 5. COILS (rectangles in slots) ─────────────────────────────
        right_x = tooth_w / 2 + ins_w + wire_dx/2
        slot_y  = outer_r - core_h
        top_y_c = slot_y - ins_w - wire_dy/2

        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            for step in range(num_wires):
                cy = top_y_c - step * (wire_h + wire_dy)
                for side, sx in ((1, right_x), (-1, -(right_x + wire_w))):
                    pts_local = [(sx, cy), (sx + wire_w, cy),
                                 (sx + wire_w, cy - wire_h), (sx, cy - wire_h)]
                    pts = [_rot(*pt, a) for pt in pts_local]
                    poly = SPoly(pts)
                    r = _tri(poly, z=Z_COIL)
                    if r:
                        key = f'coil_{i}'
                        if key not in result:
                            result[key] = r
                        else:
                            # merge into existing coil entry
                            result[key]['vertices'] += r['vertices']
                            result[key]['faces']    += [
                                [f + result[key]['vertex_count'] for f in face]
                                for face in r['faces']
                            ]
                            result[key]['vertex_count'] += r['vertex_count']
                            result[key]['face_count']   += r['face_count']

        # ── 5b. SLOT INSULATION (wire enamel + slot liner) ────────────────
        # Shared geometry with get_2d_polygons (single source). Each merged into
        # ONE part so the 3D tree shows a single "Wire enamel" / "Slot liner" item.
        def _merge_part(key, r):
            if not r:
                return
            if key not in result:
                result[key] = r
            else:
                base = result[key]['vertex_count']
                result[key]['vertices'] += r['vertices']
                result[key]['faces']    += [[f + base for f in face] for face in r['faces']]
                result[key]['vertex_count'] += r['vertex_count']
                result[key]['face_count']   += r['face_count']
        try:
            _wpolys, _spolys = self._build_insulation_polys()
            for _wp in _wpolys:
                _merge_part('wire_insulation', _tri(_wp, z=Z_COIL))
            for _sp in _spolys:
                _merge_part('slot_insulation', _tri(_sp, z=Z_COIL))
        except Exception as _e:
            print(f"[2d] Failed to build insulation meshes: {_e}")

        # ── 6. SLIDING-BAND AIR DOMAINS ───────────────────────────────────
        # in_band  = full DISK r=0..mid_r  MINUS rotor + magnets + shaft.
        #            Captures every air pocket inside the rotor region.
        # out_band = ANNULUS mid_r..r_outer MINUS stator + coils.
        #            Outer boundary is where the Dirichlet BC will be set.
        Z_IN_BAND  = -0.05  # behind everything so they don't occlude
        Z_OUT_BAND = -0.05
        mid_r   = 0.5 * (rotor_or + inner_r)
        r_outer = float(p.get('outer_air_factor', 1.3)) * outer_r
        try:
            in_band_poly  = SPoly(_circle(mid_r))
            out_band_poly = SPoly(_circle(r_outer), [_circle(mid_r)])

            # Subtract rotor solids from in_band.  CRITICAL: subtract the
            # SAME filleted magnet polygons (mag_rot_polys) that are
            # rendered and that were used to carve the rotor holes.  An
            # earlier version subtracted the UNFILLETED hexagon
            # (mag_local) here, which is larger than the rendered
            # filleted magnet — that mismatch left a thin uncovered band
            # between the filleted magnet's rounded top and the
            # unfilleted hexagon's flat top (inside the cut_up width),
            # rendering as a black notch above each magnet.  Using
            # mag_rot_polys keeps in_band's lower boundary exactly on the
            # magnet's rendered top edge.
            shaft_solid = SPoly(_circle(rotor_ir), [_circle(shaft_r)])
            try:
                in_band_poly = in_band_poly.difference(
                    unary_union([shaft_solid, rotor_poly] + list(mag_rot_polys)))
                if not in_band_poly.is_valid:
                    in_band_poly = in_band_poly.buffer(0)
            except Exception:
                pass

            # Subtract stator iron from out_band.  (Coils sit inside slot
            # cutouts that are already part of the stator's exterior — no
            # extra cut needed.)
            try:
                out_band_poly = out_band_poly.difference(stator_poly)
                if not out_band_poly.is_valid:
                    out_band_poly = out_band_poly.buffer(0)
            except Exception:
                pass

            r_in  = _tri(in_band_poly,  z=Z_IN_BAND)
            r_out = _tri(out_band_poly, z=Z_OUT_BAND)
            if r_in:  result['in_band']  = r_in
            if r_out: result['out_band'] = r_out
        except Exception as e:
            print(f"[2d] Failed to build in_band/out_band: {e}")

        return result

    @staticmethod
    def _laminate_magnets(mag_polys, seg_mm: float, num_poles: int):
        """Magnet lamination — split each magnet polygon into ≈``seg_mm``-sized
        insulated pieces along its LONGEST in-plane dimension (2-D segmentation).

        radial extent ≥ tangential → concentric-circle cuts (radial slices);
        otherwise                  → radial-ray cuts   (tangential slices).

        The cuts are ZERO-width: pieces TOUCH, so the mesh conforms with shared
        nodes and the magnetic field is IDENTICAL to the solid magnet.  The
        electrical isolation comes from the per-body treatment downstream —
        every returned piece gets its own DOM_MAG tag, hence its own floating
        conductor (∮J = 0) in BOTH eddy-loss paths (history post-process
        ``_mag_groups`` and the honest coupled solve) — which is exactly what
        physical segmentation does: smaller loops → quadratically less loss.
        Each piece also gets its own tangential M from its OWN centroid in
        build_materials, i.e. arc-true magnetisation for free.

        Piece count per magnet is capped so the machine stays inside the
        DOM_MAG tag budget [DOM_MAG_BASE .. DOM_COIL_BASE) = 100 ids even on a
        full-disk build.  Fail-safe: if slicing loses area (>2 %), that magnet
        stays solid.  Works for ANY magnet shape (universal — operates on the
        final polygon, after fillets).  Returns [(piece, polarity), ...]."""
        import math as _m
        from shapely.geometry import Polygon as _P, MultiPolygon as _MP, Point as _Pt

        out = []
        cap = max(1, 96 // max(1, int(num_poles)))       # tag-budget cap / pole
        _N_ARC = 512                                      # circle facets (sag ≪ mesh tol)
        _origin = _Pt(0.0, 0.0)
        for mp, pol in mag_polys:
            if mp is None or mp.is_empty or seg_mm <= 0.1:
                out.append((mp, pol)); continue
            try:
                xy = list(mp.exterior.coords)
                rr = [_m.hypot(x, y) for x, y in xy]
                # r_hi: max distance is always attained at a vertex.  r_lo must be
                # the TRUE min distance to the region — a straight bottom edge
                # between two vertices at radius R sags INSIDE the R-circle
                # (chord sagitta: the 450's magnet bottom dips 123.3 → 122.1 mm),
                # so min-over-vertices missed a 28 mm² lens under the innermost
                # annulus cut.  shapely distance(origin→polygon) is exact.
                r_lo, r_hi = float(mp.distance(_origin)), max(rr)
                c = mp.centroid
                ca = _m.atan2(c.y, c.x)
                # unwrap angles around the centroid direction (seam-safe)
                angs = [(_m.atan2(y, x) - ca + _m.pi) % (2.0 * _m.pi) - _m.pi
                        for x, y in xy]
                a_lo, a_hi = min(angs), max(angs)
                rad_ext = r_hi - r_lo
                tan_ext = (a_hi - a_lo) * 0.5 * (r_lo + r_hi)
                n = int(_m.ceil(max(rad_ext, tan_ext) / float(seg_mm)))
                n = max(1, min(n, cap))
                if n == 1:
                    out.append((mp, pol)); continue
                pieces = []
                if rad_ext >= tan_ext:
                    # radial slices: cut with concentric annuli
                    edges = [r_lo + rad_ext * k / n for k in range(n + 1)]
                    edges[0] = max(edges[0] - 1e-6, 1e-9)
                    edges[-1] += 1e-6
                    def _ring(r):
                        return [(r * _m.cos(2 * _m.pi * j / _N_ARC),
                                 r * _m.sin(2 * _m.pi * j / _N_ARC))
                                for j in range(_N_ARC)]
                    for k in range(n):
                        annulus = _P(_ring(edges[k + 1]), [_ring(edges[k])])
                        pieces.append(mp.intersection(annulus))
                else:
                    # tangential slices: cut with radial-ray wedges
                    R = 10.0 * r_hi
                    for k in range(n):
                        b0 = ca + a_lo + (a_hi - a_lo) * k / n
                        b1 = ca + a_lo + (a_hi - a_lo) * (k + 1) / n
                        if k == 0:      b0 -= 1e-7
                        if k == n - 1:  b1 += 1e-7
                        bm = 0.5 * (b0 + b1)
                        wedge = _P([(0.0, 0.0),
                                    (R * _m.cos(b0), R * _m.sin(b0)),
                                    (R * _m.cos(bm), R * _m.sin(bm)),
                                    (R * _m.cos(b1), R * _m.sin(b1))])
                        pieces.append(mp.intersection(wedge))
                a_min = 1e-4 * mp.area
                got = []
                for pc in pieces:
                    if pc is None or pc.is_empty:
                        continue
                    if not pc.is_valid:
                        pc = pc.buffer(0)
                    for g in (list(pc.geoms) if isinstance(pc, _MP) else [pc]):
                        if g.area > a_min:
                            got.append((g, pol))
                # fail-safe: keep the solid magnet if slicing lost area
                if got and abs(sum(g.area for g, _p in got) - mp.area) < 0.02 * mp.area:
                    out.extend(got)
                else:
                    out.append((mp, pol))
            except Exception:
                out.append((mp, pol))                     # any hiccup → solid
        return out

    def get_2d_polygons(self, rotor_angle_deg: float = 0.0) -> Dict[str, Any]:
        """Return raw Shapely polygon objects for each motor domain.

        Same geometry as get_2d_mesh_data() but returns Shapely objects
        (not triangulated meshes).  Used for 2D field-map domain classification.

        Coordinates are in mm (same as get_2d_mesh_data).

        Parameters
        ----------
        rotor_angle_deg : float
            Rotor rotation angle in degrees (rotates magnets + rotor).

        Returns
        -------
        Dict with keys:
            'stator'   : Shapely Polygon/MultiPolygon  (stator steel with slots)
            'magnets'  : list of (Shapely Polygon, polarity:int +1/-1)
            'rotor'    : Shapely Polygon               (rotor back-iron)
            'shaft'    : Shapely Polygon               (shaft ring)
            'coils'    : list of Shapely Polygon       (one per coil side, in mm)
            'air_gap'  : Shapely Polygon               (air gap ring)
        """
        from math import pi, sin, cos, tan, radians, degrees, acos, atan2, sqrt
        try:
            from shapely.geometry import Polygon as SPoly, MultiPolygon as SMPoly
            from shapely.ops import unary_union
        except ImportError:
            raise ImportError("shapely required — pip install shapely")

        import numpy as np
        p = self.parameters

        outer_r   = p['stator_outer_radius']
        inner_r   = p['stator_inner_radius']
        rotor_or  = p['rotor_outer_radius']
        rotor_ir  = p['rotor_inner_radius']
        shaft_r   = p['shaft_inner_radius']

        num_slots   = int(p['num_slots'])
        core_h      = p['core_thickness']
        tooth_w     = p['tooth_width']
        cut_w       = p.get('cut_width', 2.0)
        wire_w      = p['wire_width']
        ins_w       = p['insulation_thickness']
        wire_dx     = p['wire_spacing_x']
        wire_dy     = p['wire_spacing_y']
        wire_h      = p['wire_height']
        num_wires   = int(p['num_wires_per_slot'])
        # ── Feasibility clamp (see geometry_constraints.py) ───────────────────
        # The winding must fit the slot, else the coils overflow across the air
        # gap onto the rotor and the FEM solves an invalid cross-section.
        #   wire_height ≤ (slot_height − 2·insulation)/num_wires − wire_spacing_y
        _wh_max = (float(p.get('slot_height', 0.0)) - 2.0 * ins_w) / max(1, num_wires) - wire_dy
        if _wh_max > 1e-3 and wire_h > _wh_max:
            wire_h = _wh_max

        num_poles   = int(p['num_poles'])
        mag_h       = p['magnet_height']
        rotor_hh    = p['rotor_house_height']
        mag_fd      = p['magnet_fill_down']
        mag_fu      = p['magnet_fill_up']
        mag_up_gap  = p['magnet_up_gap']
        mag_down_h  = p['magnet_down_height']
        mag_fill_r  = p['magnet_fill_radius']
        magnet_r    = rotor_ir + rotor_hh
        magnet_hole = p['rotor_hole']
        # default 0 = param-driven (no landmine); re-read at the fillet application below
        fillet_r    = p.get('stator_fillet_r',  0.0)
        fillet_r1   = p.get('stator_fillet_r1', 0.0)

        # ── Zero-position alignment ──────────────────────────────────────
        # At rotor_angle_deg = 0 the rotor IRON TOOTH between mag[6] (N)
        # and mag[7] (S) — i.e. an effective N-pole of the rotor in the
        # SPOKE-PM topology — sits at math 90° (+Y axis), aligned with
        # the first stator tooth (also at math 90°).  This is the
        # convention shown in the user's Ansys reference image:  rotor
        # d-axis pole at +Y, magnets distributed in the upper arc of the
        # rotor.  Cadquery's native magnet origin is the +Y axis; we add
        # a small −(90° − half_pole_pitch) shift so the 7 magnets of the
        # 1/4 sector all fit INSIDE the wedge with centers at math 6.43°
        # through 83.57°.
        _pole_pitch_deg  = 360.0 / num_poles
        # Mechanical alignment offset kept at 0 — the d-axis↔phase-A alignment is
        # handled entirely by the electrical phase shift (DAXIS_SHIFT_DEG) instead,
        # which is the single, simpler knob.  (No artificial rotor rotation.)
        _DAXIS_ALIGN_MECH = 0.0
        ZERO_OFFSET_DEG  = -(90.0 - _pole_pitch_deg * 0.5) + _DAXIS_ALIGN_MECH
        theta_r = radians(rotor_angle_deg + ZERO_OFFSET_DEG)

        # Machine scale — every arc tolerance in this build is tied to it.
        scale_mm = 2.0 * outer_r

        _circle = _circle_points

        def _rot(x, y, a):
            c, s = cos(a), sin(a)
            return x*c - y*s, x*s + y*c

        def _fillet_corner(p_prev, p_corner, p_next, r):
            """Tangent-arc fillet of ONE convex corner between two LONG straight
            edges (magnet hexagon).  Safe here because tan_len is far shorter than
            the adjacent edges; discretised through the shared sagitta helper."""
            V  = np.array(p_corner, float)
            dA = np.array(p_prev, float) - V; dA /= max(np.linalg.norm(dA), 1e-9)
            dB = np.array(p_next, float) - V; dB /= max(np.linalg.norm(dB), 1e-9)
            cos_t = float(np.clip(np.dot(dA, dB), -1.0, 1.0))
            half_a = acos(cos_t) / 2
            if sin(half_a) < 1e-9:
                return [tuple(p_corner)]
            tan_len = r / tan(half_a)
            bis = dA + dB; bis /= np.linalg.norm(bis)
            center = V + (r / sin(half_a)) * bis
            T1 = V + tan_len * dA; T2 = V + tan_len * dB
            a1 = atan2(T1[1]-center[1], T1[0]-center[0])
            a2 = atan2(T2[1]-center[1], T2[0]-center[0])
            da = a2 - a1
            if da >  pi: da -= 2*pi
            elif da < -pi: da += 2*pi
            return _arc_points(center[0], center[1], r, a1, da, scale_mm)

        # ── Magnet local polygon ──────────────────────────────────────────────
        pole_angle_r = 2*pi / num_poles
        angle_down   = pole_angle_r * mag_fd / 2
        angle_up_m   = pole_angle_r * mag_fu / 2
        mp1 = ( magnet_r*sin(angle_down),               magnet_r*cos(angle_down))
        mp2 = ((magnet_r+mag_down_h)*sin(angle_down),  (magnet_r+mag_down_h)*cos(angle_down))
        mp3 = ((rotor_or-mag_up_gap)*sin(angle_up_m),  (rotor_or-mag_up_gap)*cos(angle_up_m))
        mp4 = (-(rotor_or-mag_up_gap)*sin(angle_up_m), (rotor_or-mag_up_gap)*cos(angle_up_m))
        mp5 = (-(magnet_r+mag_down_h)*sin(angle_down), (magnet_r+mag_down_h)*cos(angle_down))
        mp6 = (-magnet_r*sin(angle_down),               magnet_r*cos(angle_down))
        mag_local = [mp1, mp2, mp3, mp4, mp5, mp6]

        def _build_mag_poly(pts, fr):
            if fr <= 0: return SPoly(pts)
            try:
                new_pts = (pts[:2]
                           + _fillet_corner(pts[1], pts[2], pts[3], fr)
                           + _fillet_corner(pts[2], pts[3], pts[4], fr)
                           + pts[4:])
                return SPoly(new_pts)
            except Exception:
                return SPoly(pts)

        # Rectangular cut above each magnet
        mag_angle_up_hole = pole_angle_r * mag_fu * magnet_hole / 2
        rec_w = 2 * rotor_or * sin(mag_angle_up_hole)
        rect_local = [(-rec_w/2, rotor_or), (rec_w/2, rotor_or),
                      (rec_w/2, rotor_or-mag_h), (-rec_w/2, rotor_or-mag_h)]

        mag_polys = []   # (poly, polarity)
        hole_polys = []  # list of shapely (magnet + cut_up) polygons

        for i in range(num_poles):
            a   = i * pole_angle_r + theta_r
            pts = [_rot(x, y, a) for x, y in mag_local]
            mp  = _build_mag_poly(pts, mag_fill_r)
            if not mp.is_valid: mp = mp.buffer(0)

            rect_pts = [_rot(x, y, a) for x, y in rect_local]
            rect_poly = SPoly(rect_pts)
            if not rect_poly.is_valid: rect_poly = rect_poly.buffer(0)
            hole = _safe_union(mp, rect_poly)
            if not hole.is_valid: hole = hole.buffer(0)
            hole_polys.append(hole)
            # Magnet polarity FLIPPED (N↔S vs the old i%2 convention): the rotor
            # field then points the right way, so γ=0 gives positive torque with a
            # SMALL d-axis phase shift (≈0 ± a few deg) instead of ~270°.
            polarity = -1 if i % 2 == 0 else +1
            mag_polys.append((mp, polarity))

        # ── In-plane magnet segmentation (magnet_lamination_tan, mm; 0 = off) ──
        # Split every magnet into ≈seg-sized insulated pieces IN THE CROSS-
        # SECTION (see the helper for the physics).  NOTE: the user-facing
        # `magnet_lamination` parameter means AXIAL slicing (along the stack,
        # e.g. 180 mm / 10 mm = 18 slices) — that cannot be meshed in 2-D and is
        # applied as an analytic eddy-loss factor in the solver instead.  This
        # in-plane splitter stays available under its own key for cross-section
        # segmentation studies.  MUST happen after the pole loop and BEFORE any
        # consumer: the rotor POCKETS above (hole_polys) keep the FULL magnet
        # outline, and the air bands below subtract the union of the pieces,
        # which equals the solid magnet region (zero-width cuts).
        _seg_mm = float(p.get('magnet_lamination_tan', 0.0) or 0.0)
        if _seg_mm > 0.1:
            mag_polys = self._laminate_magnets(mag_polys, _seg_mm, num_poles)

        # Rotor = annulus rotor_or..rotor_ir minus union(all holes).
        # Using shapely difference avoids the invalid-polygon problem
        # caused by passing raw hole coords whose top corners lie at
        # r = sqrt((rec_w/2)^2 + rotor_or^2) > rotor_or — these
        # apparent-but-tiny excursions outside the rotor disk used to
        # cross the exterior boundary, invalidate the polygon, and let
        # buffer(0) silently drop the rotor-iron bridges between
        # adjacent magnets.
        rotor_disk = SPoly(_circle(rotor_or), [_circle(rotor_ir)])
        if not rotor_disk.is_valid: rotor_disk = rotor_disk.buffer(0)
        rotor_poly = rotor_disk.difference(unary_union(hole_polys))
        if not rotor_poly.is_valid: rotor_poly = rotor_poly.buffer(0)

        # ── Round the rotor pole-tip sharp corners (rotor_fill_r) ─────────────
        # Sharp iron corners at the air-gap surface concentrate flux → feed
        # cogging/ripple + iron loss.  Use a VERTEX fillet (tangent arc per corner)
        # — it rounds corners WITHOUT eroding edges, so the thin inter-magnet
        # bridges (rotor_house ~1.2 mm) survive (the fillet auto-clamps to the
        # local feature size).  GUARDED: keep only if valid, ≥85 % area, same #
        # pieces — else keep the sharp rotor.  ("не ломай геометрию")
        _rfr = float(p.get('rotor_fill_r', 0.0) or 0.0)
        if _rfr > 1e-4:
            def _npoly(g):
                return len(g.geoms) if g.geom_type == 'MultiPolygon' else (0 if g.is_empty else 1)
            try:
                # surface band must be NARROWER than the magnet-top bridge
                # (mag_up_gap): the pocket-top corners sit only up_gap below the
                # OD, so the default 1.5 mm band swallowed them too and rounded
                # the pole iron NEXT TO THE MAGNETS (dense mesh fans there).
                # Air-gap-side tips are AT max radius → a sub-bridge band keeps
                # them rounded while every magnet-side corner stays sharp.
                _band = min(1.5, 0.6 * float(p.get('magnet_up_gap', 1.5) or 1.5))
                _f = _round_corners_vertex(rotor_poly, _rfr, surface_band=_band,
                                           scale_mm=scale_mm)
                if not _f.is_valid:
                    _f = _f.buffer(0)
                if (_f.is_valid and not _f.is_empty
                        and _f.area >= 0.85 * rotor_poly.area
                        and _npoly(_f) == _npoly(rotor_poly)):
                    rotor_poly = _f
                else:
                    print(f"rotor_fill_r={_rfr:.2f} rejected: valid={_f.is_valid} empty={_f.is_empty} "
                          f"area={100*_f.area/max(rotor_poly.area,1e-9):.1f}% "
                          f"pieces {_npoly(rotor_poly)}->{_npoly(_f)} -- keeping sharp rotor")
            except Exception as _e:
                print(f"rotor_fill_r failed ({_e}) -- keeping sharp rotor")

        # Shaft
        shaft_poly = SPoly(_circle(rotor_ir), [_circle(shaft_r)])
        if not shaft_poly.is_valid: shaft_poly = shaft_poly.buffer(0)

        # Air gap ring
        airgap_poly = SPoly(_circle(inner_r), [_circle(rotor_or)])
        if not airgap_poly.is_valid: airgap_poly = airgap_poly.buffer(0)

        # ── Stator with real slot cutouts ────────────────────────────────────
        half_slots     = num_slots // 2
        slot_angle_deg = 360.0 / half_slots
        cut_x  = tooth_w/2 + ins_w*2 + wire_w + wire_dx*2 + p.get('tooth2_width', 4.5)
        fill_r2 = ((inner_r + cut_w) * sin(radians(slot_angle_deg/2)) - cut_x) \
                  / (1 - sin(radians(slot_angle_deg/2)))
        rr  = inner_r + cut_w + fill_r2
        ext = outer_r * 2

        p1s = (cut_x,  ext)
        p2s = (cut_x,  rr * cos(radians(slot_angle_deg/2)))
        p3s = (cut_x + fill_r2, rr * cos(radians(slot_angle_deg/2)))
        p4s = (ext * tan(radians(slot_angle_deg/2)), ext)

        stator_poly_base = SPoly(_circle(outer_r), [_circle(inner_r)])
        cutters = []
        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            trap_p = SPoly([_rot(*p1s,a), _rot(*p2s,a), _rot(*p3s,a), _rot(*p4s,a)])
            cx, cy = _rot(p3s[0], p3s[1], a)
            # slot-mouth rounding circle — sagitta-discretised like every other arc
            circ_p = SPoly(_arc_points(cx, cy, fill_r2, 0.0, 2*pi, scale_mm)[:-1])
            m_p = _safe_union(trap_p, circ_p)
            cutters.append(m_p if m_p.is_valid else m_p.buffer(0))

            mp1n=(-p1s[0],p1s[1]); mp2n=(-p2s[0],p2s[1])
            mp3n=(-p3s[0],p3s[1]); mp4n=(-p4s[0],p4s[1])
            trap_n = SPoly([_rot(*mp1n,a), _rot(*mp2n,a), _rot(*mp3n,a), _rot(*mp4n,a)])
            cxn,cyn = _rot(-p3s[0],p3s[1],a)
            circ_n = SPoly(_arc_points(cxn, cyn, fill_r2, 0.0, 2*pi, scale_mm)[:-1])
            m_n = _safe_union(trap_n, circ_n)
            cutters.append(m_n if m_n.is_valid else m_n.buffer(0))

            slot_w_c = wire_w + ins_w*2 + wire_dx
            slot_h_c = p['slot_height']
            slot_x   = tooth_w / 2
            slot_y   = outer_r - core_h
            rx0, ry0 = slot_x, slot_y
            cutters.append(SPoly([_rot(*pt, a) for pt in
                [(rx0,ry0),(rx0+slot_w_c,ry0),(rx0+slot_w_c,ry0-slot_h_c*2),(rx0,ry0-slot_h_c*2)]]))
            cutters.append(SPoly([_rot(*pt, a) for pt in
                [(-rx0,ry0),(-rx0-slot_w_c,ry0),(-rx0-slot_w_c,ry0-slot_h_c*2),(-rx0,ry0-slot_h_c*2)]]))

        tool = unary_union(cutters)
        stator_poly = stator_poly_base.difference(tool)
        if isinstance(stator_poly, SMPoly):
            parts = [g for g in stator_poly.geoms if g.area > 0.1]
            stator_poly = parts[0] if len(parts)==1 else SMPoly(parts)
        if not stator_poly.is_valid: stator_poly = stator_poly.buffer(0)

        # ── Stator slot-corner fillets — same radii as 3D CadQuery + Geometry tab ──
        # The 3D model applies `.fillet(stator_fillet_r)` to outer-ring edges and
        # `.fillet(stator_fillet_r1)` to inner-ring edges via _OuterRingSelector /
        # _InnerRingSelector.  We mirror that on the 2-D polygon here so the
        # Mesh tab (which only sees this dict) renders the SAME corners.
        # Outer-ring fillet (fillet_r) + air-gap-side fillet (fillet_r1). [[slot_fillet_root_cause]]
        # The outer band must stay in the OUTER part of the back-iron so it can NEVER
        # reach the slot-bottom (yoke-side) corners — on small motors those sit only
        # ~1.4 mm from the OD and a fixed 1.5 mm band wrongly rounded them (the bug
        # that only showed on small diameters).  Band = 0.45·core_thickness (cap 1.5).
        fillet_r  = p.get('stator_fillet_r',  0.0)
        fillet_r1 = p.get('stator_fillet_r1', 0.0)
        _out_tol = min(1.5, 0.45 * core_h)
        if fillet_r > 0 and hasattr(stator_poly, 'exterior'):
            stator_poly = _round_corners_at_radius(stator_poly, outer_r, _out_tol,
                                                   fillet_r, scale_mm)
        if fillet_r1 > 0 and hasattr(stator_poly, 'exterior'):
            stator_poly = _round_corners_at_radius(stator_poly, inner_r, 1.0,
                                                   fillet_r1, scale_mm)

        # ── Coils (winding rectangles in slots) ──────────────────────────────
        # The cadquery iteration places wires on BOTH sides of each central
        # tooth (positive-x = "right slot", negative-x = "left slot"), so
        # we emit TWO separate polygons per iteration — one per physical
        # slot — labelled with the matching winding-layout slot index.
        # This makes per-coil J_z assignment straightforward downstream.
        right_x  = tooth_w/2 + ins_w + wire_dx/2
        slot_y_c = outer_r - core_h
        top_y_c  = slot_y_c - ins_w - wire_dy/2
        # Wires must stay INSIDE the slot — never cross the stator inner radius
        # into the air gap / rotor.  Stop stacking once a wire would overflow.
        # (Without this, a too-large num_wires·wire_height pushed coils across the
        #  gap onto the rotor → J_z applied in the air gap → invalid FEM.)
        min_wire_r = inner_r + ins_w
        n_fit = 0
        for step in range(num_wires):
            if top_y_c - step*(wire_h + wire_dy) - wire_h < min_wire_r:
                break
            n_fit += 1
        self._coils_overflow = bool(n_fit < num_wires)
        self._n_wires_fit = int(n_fit)
        # PER-WIRE conductors: every single wire is emitted as its OWN polygon
        # (NOT unioned into a per-slot bar).  The winding is N turns in series,
        # so each wire is an independent solid conductor carrying the branch
        # current — this is what lets the eddy-current solver compute the real
        # per-wire skin + proximity loss instead of one shorted slot bar.
        # `coil_polys` order = slot-by-slot, +x side then −x side, wire-by-wire;
        # each entry's centroid still lands in its slot so the (phase, direction)
        # lookup downstream is unchanged.
        coil_polys = []
        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            for sx0 in (right_x, -(right_x + wire_w)):     # +x side, then −x side
                for step in range(n_fit):
                    cy = top_y_c - step*(wire_h + wire_dy)
                    local = [(sx0, cy), (sx0 + wire_w, cy),
                             (sx0 + wire_w, cy - wire_h), (sx0, cy - wire_h)]
                    wp = SPoly([_rot(*pt, a) for pt in local])
                    if wp.is_valid and wp.area > 0:
                        coil_polys.append(wp)              # ONE polygon per wire

        # ── Slot insulation objects (thermal + mass/cost; EM-inert) ───────────
        # wire enamel (polyimide) + slot liner (Nomex/ceramic), via the SHARED
        # _build_insulation_polys() so the 3D viewer (get_2d_mesh_data) shows the
        # exact same geometry.  Enamel = envelope−copper; liner = ins_w U-band on the
        # 3 iron-facing sides.
        wire_ins_polys, slot_ins_polys = self._build_insulation_polys()

        # ── Sliding-band air domains: in_band + out_band ──────────────────
        # in_band  = full DISK r=0..mid_r  MINUS rotor + magnets + shaft.
        #            Captures every bit of air inside the moving (rotor)
        #            region — including the shaft bore, inter-magnet pockets,
        #            and the inner half of the air gap.  Rotates rigidly
        #            with the rotor in the sliding-band transient solver.
        #
        # out_band = ANNULUS mid_r..r_outer MINUS stator + coils.
        #            r_outer = outer_air_factor × stator_outer_radius is the
        #            far-field boundary where the Dirichlet BC A_z = 0
        #            (magnetic potential clamp) will be applied.  Captures
        #            slot-opening air, outer ambient air and the outer half
        #            of the air gap — stationary in the lab frame.
        #
        # The shared circle r=mid_r is the slip surface.
        mid_r   = 0.5 * (rotor_or + inner_r)
        # Outer boundary for the FE domain — pulled in from motor_config
        # if available; defaults to 1.3× the stator OD.
        r_outer = float(p.get('outer_air_factor', 1.3)) * outer_r

        in_band_poly  = SPoly(_circle(mid_r))                # full disk to mid_r
        out_band_poly = SPoly(_circle(r_outer), [_circle(mid_r)])  # annulus mid_r..r_outer
        if not in_band_poly.is_valid:  in_band_poly  = in_band_poly.buffer(0)
        if not out_band_poly.is_valid: out_band_poly = out_band_poly.buffer(0)

        # Subtract rotor solids from in_band (shaft + rotor + every magnet).
        try:
            rotor_solids = [rotor_poly, shaft_poly] + [mp for mp, _pol in mag_polys]
            in_band_poly = in_band_poly.difference(unary_union(rotor_solids))
            if not in_band_poly.is_valid: in_band_poly = in_band_poly.buffer(0)
        except Exception:
            pass

        # Subtract stator + coils from out_band.
        try:
            stator_solids = [stator_poly] + list(coil_polys)
            out_band_poly = out_band_poly.difference(unary_union(stator_solids))
            if not out_band_poly.is_valid: out_band_poly = out_band_poly.buffer(0)
        except Exception:
            pass

        out = {
            'stator':   stator_poly,      # Shapely Polygon in mm
            'magnets':  mag_polys,        # list of (Polygon, polarity)
            'rotor':    rotor_poly,       # Shapely Polygon in mm
            'shaft':    shaft_poly,       # Shapely Polygon in mm
            'air_gap':  airgap_poly,      # Shapely Polygon in mm — kept for back-compat
            'in_band':  in_band_poly,     # Air disk r=0..mid_r minus rotor bodies (rotates)
            'out_band': out_band_poly,    # Air annulus mid_r..r_outer minus stator (stationary)
            'mid_r_mm': mid_r,            # slip-surface radius (mm)
            'r_outer_boundary_mm': r_outer,  # outer Dirichlet BC radius (mm)
            'coils':    coil_polys,       # list of Shapely Polygon in mm
            'wire_insulation': wire_ins_polys,  # list[Polygon] — wire enamel (polyimide); thermal+display (cost in wire)
            'slot_insulation': slot_ins_polys,  # list[Polygon] — slot liner (Nomex/ceramic); thermal+cost+display
            # Winding fit: True if the requested num_wires_per_slot did NOT fit in
            # the slot (stack clamped to n_wires_fit so coils stay out of the gap).
            'coils_overflow': bool(getattr(self, '_coils_overflow', False)),
            'n_wires_fit':    int(getattr(self, '_n_wires_fit', num_wires)),
            'n_wires_requested': int(num_wires),
        }

        # ── Final ring sanitize — NOTHING defective leaves this builder ───────
        # Shapely's boolean noding emits exact duplicate points at slot-mouth
        # corners and can leave a 3-coincident-point sliver "polygon" behind the
        # rotor difference.  Both are junk the mesher must never see: gmsh
        # honours every boundary point, so a zero-length edge becomes a fan of
        # microscopic triangles.  Every drop is logged with coordinates.
        return _sanitize_polys_dict(out, scale_mm)

    def get_extruded_mesh_data(self, depth: float = None) -> Dict[str, Dict]:
        """
        Extrude flat 2D cross-section meshes into 3D solid meshes.

        Takes the output of get_2d_mesh_data() and for each component:
          1. Duplicates vertices at z=0 (top) and z=-depth (bottom)
          2. Keeps top faces with original winding
          3. Adds bottom faces with reversed winding
          4. Finds boundary edges and builds side-wall quads

        No CadQuery / OCCT required — pure NumPy.

        Parameters
        ----------
        depth : float, optional
            Axial extrusion depth in mm.  Defaults to motor_length parameter
            or 30 mm if not set.

        Returns
        -------
        Dict mapping component name → same mesh dict format as get_2d_mesh_data /
        get_all_mesh_data, with z spanning [0, -depth].
        """
        import numpy as np

        if depth is None:
            depth = float(self.parameters.get('motor_length', 30.0))

        flat = self.get_2d_mesh_data()
        extruded: Dict[str, Dict] = {}

        for name, comp in flat.items():
            verts_2d = np.array(comp['vertices'], dtype=float)  # (N, 3) z≈0
            faces_2d = np.array(comp['faces'],    dtype=int)    # (M, 3)
            N = len(verts_2d)

            # ── top & bottom vertices ────────────────────────────────────────
            top_v    = verts_2d.copy()
            top_v[:, 2] = 0.0
            bot_v    = verts_2d.copy()
            bot_v[:, 2] = -depth

            vertices = np.vstack([top_v, bot_v])  # (2N, 3)

            # ── top faces (original winding) ─────────────────────────────────
            top_f = faces_2d.copy()

            # ── bottom faces (reversed winding so normals point down) ────────
            bot_f = faces_2d[:, ::-1] + N

            # ── side walls (vectorised) ──────────────────────────────────────
            # Build directed edge array: for each face [a,b,c] → edges a→b, b→c, c→a
            M = len(faces_2d)
            # directed_edges shape (3M, 2): each row is [from, to]
            directed = np.concatenate([
                faces_2d[:, [0, 1]],
                faces_2d[:, [1, 2]],
                faces_2d[:, [2, 0]],
            ], axis=0)  # (3M, 2)

            # Canonical (sorted) edge for counting duplicates
            canonical = np.sort(directed, axis=1)  # (3M, 2)
            # Encode as a single int64 for fast uniqueness check (N < 2**31)
            MAX_IDX = N + 1
            codes   = canonical[:, 0].astype(np.int64) * MAX_IDX + canonical[:, 1].astype(np.int64)
            unique_codes, counts = np.unique(codes, return_counts=True)
            boundary_codes = unique_codes[counts == 1]
            boundary_set   = set(boundary_codes.tolist())

            # Among directed edges, keep those whose canonical code is a boundary
            dir_codes  = directed[:, 0].astype(np.int64) * MAX_IDX + directed[:, 1].astype(np.int64)
            can_codes  = np.sort(directed, axis=1)
            can_codes2 = can_codes[:, 0].astype(np.int64) * MAX_IDX + can_codes[:, 1].astype(np.int64)
            mask       = np.isin(can_codes2, list(boundary_set))
            boundary_directed = directed[mask]  # each row [a, b] in correct CCW winding

            if len(boundary_directed):
                a_col = boundary_directed[:, 0]
                b_col = boundary_directed[:, 1]
                # For a CCW-wound boundary edge a→b (solid to the left), the outward
                # normal of the side-wall quad must point to the RIGHT of a→b.
                # Cross-product analysis shows (a, b+N, b) and (a, a+N, b+N) give
                # normals = depth*(dy, -dx, 0) which is 90° CW from (dx,dy) = outward.
                tri1 = np.stack([a_col,        b_col + N,    b_col      ], axis=1)
                tri2 = np.stack([a_col,        a_col + N,    b_col + N  ], axis=1)
                side_f = np.vstack([tri1, tri2])
            else:
                side_f = np.empty((0, 3), dtype=int)

            all_faces = np.vstack([top_f, bot_f, side_f])

            extruded[name] = {
                'vertices':     vertices.tolist(),
                'faces':        all_faces.tolist(),
                'vertex_count': len(vertices),
                'face_count':   len(all_faces),
            }

        return extruded

    def validate_sdf(self, n_points: int = 50000) -> Dict:
        """Validate geometry by computing SDF."""
        mesh_data = self.get_all_mesh_data()
        
        if not mesh_data:
            return {'valid': False, 'error': 'No mesh data'}
            
        import numpy as np
        
        all_vertices = []
        for comp, data in mesh_data.items():
            all_vertices.extend(data['vertices'])
            
        vertices = np.array(all_vertices)
        bounds_min = vertices.min(axis=0)
        bounds_max = vertices.max(axis=0)
        
        size = bounds_max - bounds_min
        volume = np.prod(size)
        
        valid = volume > 0 and len(mesh_data) > 0
        
        return {
            'valid': valid,
            'bounding_box': {
                'min': bounds_min.tolist(),
                'max': bounds_max.tolist(),
            },
            'approximate_volume': float(volume),
            'components': list(mesh_data.keys()),
            'n_components': len(mesh_data),
        }


class CadQueryCache:
    """Cache for CadQuery-generated geometry."""
    
    def __init__(self, cache_dir: str = "./cadquery_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
    def get_cache_path(self, param_hash: str) -> Path:
        return self.cache_dir / param_hash
        
    def exists(self, param_hash: str) -> bool:
        cache_path = self.get_cache_path(param_hash)
        return cache_path.exists() and any(cache_path.glob("*.stl"))
        
    def save(self, param_hash: str, stl_files: Dict[str, str]) -> str:
        import shutil
        cache_path = self.get_cache_path(param_hash)
        cache_path.mkdir(exist_ok=True)
        
        for comp_name, src_path in stl_files.items():
            dst_path = cache_path / f"{comp_name}.stl"
            shutil.copy2(src_path, dst_path)
            
        return str(cache_path)
        
    def load(self, param_hash: str) -> Optional[Dict[str, str]]:
        cache_path = self.get_cache_path(param_hash)
        
        if not self.exists(param_hash):
            return None
            
        stl_files = {}
        for stl_file in cache_path.glob("*.stl"):
            stl_files[stl_file.stem] = str(stl_file)
            
        return stl_files
    
    def clear_all(self):
        """Clear all cached geometry."""
        import shutil
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(exist_ok=True)
    
    def clear_hash(self, param_hash: str):
        """Clear a specific cached geometry by hash."""
        import shutil
        cache_path = self.get_cache_path(param_hash)
        if cache_path.exists():
            shutil.rmtree(cache_path)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='CadQuery Motor Geometry Generator')
    parser.add_argument('--stator_outer_radius', type=float, default=100.0)
    parser.add_argument('--num_slots', type=int, default=36)
    parser.add_argument('--num_poles', type=int, default=12)
    parser.add_argument('--output', type=str, default='./stl_output')
    parser.add_argument('--validate', action='store_true')
    
    args = parser.parse_args()
    
    motor = CadQueryMotor()
    motor.set_parameters(vars(args))
    
    if args.validate:
        motor.build_all()
        result = motor.validate_sdf()
        print(f"Validation result: {result}")
    else:
        stl_files = motor.export_stl(args.output)
        print(f"Generated {len(stl_files)} STL files")
