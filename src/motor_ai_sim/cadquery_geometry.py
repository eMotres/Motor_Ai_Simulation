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
import threading
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Any
from math import sin, cos, tan, radians, degrees, pi, acos, atan2, hypot, ceil, floor, sqrt
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

# ═══════════════════════════════════════════════════════════════════════════
#  The ONE CadQuery probe — attempted at most once per process
# ═══════════════════════════════════════════════════════════════════════════
# This used to cache only SUCCESS (`if HAS_CADQUERY: return True`), so on a
# machine where the import FAILS every caller re-ran the whole failing import.
# That cost nothing while the failure mode was "cadquery isn't installed"
# (ImportError in microseconds).  On 2026-09-14 Windows Application Control
# started blocking OCP's DLL, and the same probe turned into a multi-second,
# process-global, SERIALISED operation — and hung the server:
#
#   * `import cadquery` takes ~0.5-3 s to fail (the loader evaluates the App
#     Control policy and writes an event log entry for every attempt);
#   * importlib holds a per-module lock for the whole attempt, so every other
#     thread importing it parks in `importlib._bootstrap.acquire` — a convoy,
#     not parallel work;
#   * every request of the 3-D viewer (`/api/geometry/mesh`) and of the new
#     FreeCAD export probed again, so the convoy grew faster than it drained,
#     filled all 40 anyio worker threads, and every SYNC endpoint — which is
#     nearly all of them, `/api/me` included — queued forever behind it.
#     Live incident: the API served one request at 10:44 and nothing until the
#     11:19 restart, 54 threads, 51 s of CPU (everyone blocked on a lock).
#
#   * worse, the probe LIED under concurrency: while thread A runs
#     `cadquery/__init__.py`, a partially initialised `cadquery` sits in
#     sys.modules, so thread B's `import cadquery` takes importlib's
#     already-in-sys.modules path (`_lock_unlock_module`) and returns that
#     half-built module with NO exception.  `ocp_available()` then answered
#     "yes" and the export skipped its own fallback and 500'd in `build_solids`.
#
# So: ONE attempt per process, guarded by OUR lock (waiters queue here, where
# waiting is free, instead of inside importlib), the FAILURE cached as firmly
# as the success, and the module checked for a real attribute so a half-built
# module can never pass as a working kernel.  Installing/unblocking a CAD
# kernel therefore needs an API restart — which is already true of every other
# Python-level change on this deployment (uvicorn runs without --reload).
HAS_CADQUERY = False
cq = None                     # type: Any  — the resolved module, or None
exporters = None              # type: Any

_CQ_PROBE_LOCK = threading.Lock()
#: None = never attempted; else (ok, reason) — reason is "" when ok.
_CQ_PROBE: Optional[Tuple[bool, str]] = None


def cadquery_probe() -> Tuple[bool, str]:
    """(is the CadQuery/OCP kernel usable here, why not).  Memoised — the
    import is attempted at most ONCE per process; see the note above."""
    global HAS_CADQUERY, cq, exporters, _CQ_PROBE
    probe = _CQ_PROBE
    if probe is not None:                     # fast path, no lock, no importlib
        return probe
    with _CQ_PROBE_LOCK:
        if _CQ_PROBE is not None:             # another thread just did it
            return _CQ_PROBE
        try:
            import cadquery as _cq_mod
            # A half-initialised module handed back by importlib's
            # already-in-sys.modules path has no Workplane yet: refuse it
            # rather than promise solids we cannot build.  Checked BEFORE the
            # submodule import so the reason names the real problem.
            if not hasattr(_cq_mod, "Workplane"):
                raise ImportError("cadquery imported but is not initialised "
                                  "(no Workplane) — partial module")
            from cadquery import exporters as _exporters
        except Exception as e:                # noqa: BLE001 — ANY failure is final
            _CQ_PROBE = (False, f"{type(e).__name__}: {e}")
            log.warning("CadQuery/OCP unavailable in this process (%s) — "
                        "solids are off until the API is restarted", _CQ_PROBE[1])
            return _CQ_PROBE
        cq = _cq_mod
        exporters = _exporters
        HAS_CADQUERY = True
        _CQ_PROBE = (True, "")
        return _CQ_PROBE


def reset_cadquery_probe() -> None:
    """Forget the memoised answer (tests; a kernel installed at runtime)."""
    global _CQ_PROBE, HAS_CADQUERY
    with _CQ_PROBE_LOCK:
        _CQ_PROBE = None
        HAS_CADQUERY = False


def _import_cadquery() -> bool:
    """Lazy import of CadQuery — True when the kernel is usable."""
    return cadquery_probe()[0]


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


def _resolve_node_map(raw: Dict) -> Dict:
    """Path-compress a ``{dropped point: representative}`` weld map.

    A weld can be recorded in two steps — round 1 merges A into B, round 2 (with
    a new neighbour in the ring) merges B into C — and a domain that only ever
    saw A must still land on C.  Chains are two or three long in practice; the
    visited set is there so a cycle (impossible by construction, since every
    representative is the lexicographic minimum of its run) can never hang the
    builder."""
    out = {}
    for k in raw:
        seen = {k}
        v = raw[k]
        while v in raw and v not in seen:
            seen.add(v)
            v = raw[v]
        if v != k:
            out[k] = v
    return out


def _sanitize_ring(coords, eps: float, label: str = "",
                   node_map: Optional[Dict] = None,
                   merges: Optional[Dict] = None):
    """Clean ONE closed ring.  Returns the open coordinate list, or None if the
    ring is degenerate (< 3 distinct points, or area below (1e-4 mm)^2).

    ``node_map`` / ``merges`` are the SHARED-BOUNDARY channel (see
    `_weld_group_geoms`, 2026-09-06): points are rewritten through `node_map`
    before anything else, and every weld this ring performs is recorded into
    `merges` so the neighbouring domains can be replayed with it.  Both default
    to None, which is exactly the single-domain behaviour this function has
    always had.

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
    if node_map:
        # Replay the welds the neighbouring domains already decided (see
        # `_weld_group_geoms`).  Done BEFORE the closing-repeat trim so a mapped
        # first/last point still collapses correctly.
        P = [node_map.get(q, q) for q in P]
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
            if merges is not None:
                for q in P:
                    if q != merged[0]:
                        merges[q] = merged[0]
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
                if merges is not None:
                    for q in run:
                        if q != rep:
                            merges[q] = rep
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


def _sanitize_geom(geom, scale_mm: float, label: str = "",
                   node_map: Optional[Dict] = None,
                   merges: Optional[Dict] = None):
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
        ext = _sanitize_ring(list(g.exterior.coords), eps, tag, node_map, merges)
        if ext is None:
            return []
        holes = []
        for j, h in enumerate(g.interiors):
            hr = _sanitize_ring(list(h.coords), eps, f"{tag}.hole{j}",
                                node_map, merges)
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


#: How many times `_weld_group_geoms` may replay the group.  A hard stop, NOT a
#: convergence guarantee: over the 100-build Ø200 sweep of 2026-09-06 the map
#: reached a fixed point in 1 round 6 times, 2 rounds 66, 3 rounds 23 — and 3
#: builds never did, because a weld keeps exposing a second-order one and the
#: pair alternates.  Raising the budget to 12 changed not one measured number on
#: that sweep, so those 3 oscillate rather than converge slowly; 4 it is.
_WELD_GROUP_ROUNDS = 4


def _weld_group_geoms(items, scale_mm: float, node_map: Optional[Dict] = None,
                      derive=None):
    """Sanitise a set of ADJACENT domains with ONE shared weld decision.

    WHY (measured 2026-09-06 on the Ø200 12s/10p sleeved fixture, sweeping
    magnet_up_gap ∈ {0, 0.5, 1, 1.5, 2}, rotor_fill_r ∈ {0, 0.2} and rotor angle
    ∈ {0 … 18°}):

    the polygons that come OUT of the boolean build are clean — magnet∩iron,
    air∩iron, air∩magnet and un-owned void all measure ≤ 4e-15 mm², i.e. exact.
    Everything the sweep found was created afterwards, by `_sanitize_geom`
    welding each domain ON ITS OWN.  Two domains carry their own copy of a
    boundary they share, but not the same NEIGHBOURS along it: the rotor's copy
    of the magnet's top arc also carries the pocket-opening rectangle's crossing
    points, so a run of three points welds there while the magnet's copy of the
    same arc — the identical vertices, 1.5 mm apart — welds nothing.  The two
    copies then sit up to one weld tolerance (diameter/4000 = 0.05 mm here)
    apart and the domains overlap or leave a hole:

        worst over the sweep          before        after
        magnet ∩ rotor iron, hole 0.7   0.105 mm²   1.6e-15 mm²
        magnet ∩ rotor iron, hole 1.0   3.6e-13     3.6e-13
        in_band ∩ magnet, hole 0.7      6.2e-4      3.8e-15
        un-owned void, hole 0.7         0.43        0.057
        un-owned void, hole 1.0         0.069       0.0020

    (0.105 mm² is 2× the 0.048 mm² of magnet∩iron the static-3D validator
    refused on 2026-08-24, and it is the same defect.)

    So the weld decision has to be taken ONCE for the whole set.  Each round
    sanitises every member from its ORIGINAL rings with the accumulated map
    applied and collects the welds it performed; the map is then the union of
    all of them, path-compressed, and the round is replayed.  A point that any
    member welds is therefore welded the same way by every member that has it,
    and a shared boundary can only move as one.  Usually the round that adds
    nothing new comes second or third; a few builds never reach a fixed point
    (see `_WELD_GROUP_ROUNDS`) and are cut off, which leaves the members welded
    against the SAME map but free to have taken one private decision each — the
    0.057 mm² of un-owned void still in the table above is that residue.

    Every round starts from the ORIGINAL rings — never from the previous
    round's output, and a caller must not chain two of these calls on the same
    domain.  Sanitising twice is not idempotent: the first pass drops the magnet
    nodes `_side_nodes_from_magnet` splices onto the pocket wall (with no weld
    they are exactly collinear, which is the point — they cost nothing when they
    are not needed), and on the stripped ring the wall is one 25 mm segment
    again, so a later 0.045 mm move of its OD corner sweeps 0.47 mm² of magnet
    into the iron.  That is measured, not feared: it is what an earlier
    two-stage version of this function did to the Ø200 at magnet_up_gap 0.5,
    rotor_fill_r 0.2, 7.5°.

    Deliberately NOT a global spatial clustering of every point in the machine:
    that would also merge two points that are within eps but on OPPOSITE walls
    of a thin feature (a slot neck, an inter-magnet bridge) and pinch the domain
    shut.  Only welds that a ring actually decided — i.e. between CONSECUTIVE
    points of some boundary — are propagated.

    Parameters
    ----------
    items : list[(label, geom)]
        The adjacent domains.  `None` entries are passed through untouched so a
        caller can keep a fixed slot for an optional domain (the sleeve).
    node_map : dict, optional
        Welds already agreed elsewhere.
    derive : callable(list[geom]) -> list[(label, geom)], optional
        DERIVED domains, rebuilt from the round's welded members before they are
        sanitised themselves — the air band, which is the disk minus the solids.
        It has to be rebuilt inside the loop, not after it: cut from the solids
        as they were BEFORE the weld it inherits a boundary they no longer have,
        and the sub-weld slivers that difference strands are then deleted by its
        own sanitise pass (0.43 mm² of un-owned rotor annulus, 2026-09-06).  Its
        welds join the map like anyone else's, so where the air band has to weld
        a corner away the solid that shares the corner follows and absorbs the
        area instead of leaving a hole.

    Returns
    -------
    (list[geom], list[geom], node_map)
        The welded members, the welded derived domains (empty when `derive` is
        None), and the map they all agreed on.
    """
    nm = dict(node_map or {})
    out = [g for _lab, g in items]
    der: List = []

    def _pass(collect):
        merges: Dict = {} if collect else None
        o = [None if g is None
             else _sanitize_geom(g, scale_mm, lab, nm or None, merges)
             for lab, g in items]
        d = []
        if derive is not None:
            d = [None if g is None
                 else _sanitize_geom(g, scale_mm, lab, nm or None, merges)
                 for lab, g in derive(o)]
        return o, d, merges

    for _ in range(_WELD_GROUP_ROUNDS):
        out, der, merges = _pass(True)
        if not merges:
            break                       # nothing new — `out` matches `nm`
        merged_into = dict(nm)
        merged_into.update(merges)
        nm2 = _resolve_node_map(merged_into)
        if nm2 == nm:
            break                       # map is a fixed point — so is `out`
        nm = nm2
    else:
        # Round budget exhausted: `out` was built with the map of the previous
        # round, so rebuild it against the one we are handing back rather than
        # returning a pair that disagrees.
        out, der, _ = _pass(False)
    return out, der, nm


def _pocket_cut_depth(p: Dict) -> float:
    """Radial depth of the rectangular pocket-opening cut above each magnet.

    ONLY the rotor_hole < 1 path uses this.  At rotor_hole >= 1 there is no
    rectangle at all any more: the pocket is the magnet outline with its two
    SIDE edges extended straight to the rotor OD — see `_extended_pocket` /
    `_extended_pocket_pts` — and this returns 0 there so a stale caller cannot
    re-introduce one.

    For rotor_hole < 1 the opening runs from the rotor OD down past the magnet's
    top edge (magnet_up_gap) with 2 mm of overlap into the magnet, so the two
    polygons always fuse without a seam; it never reaches deeper, because below
    the top edge the magnet polygon is the pocket.  A constant-width rectangle
    run the full magnet height protruded past the magnet's inner corners
    whenever the magnet narrows toward the hub (measured 2026-09-05 on the
    optimised Ø200: 2 × 0.13 mm² air slivers beside the magnet's inner end).
    Clamped to the magnet height for a magnet shorter than the overlap.

    ALSO clamped so the rectangle never reaches below the height where the
    magnet's side becomes narrower than the opening (2026-09-25, owner's Ø12
    12s10p Fusion rotor, magnet 2 mm tall): the fixed 2 mm overlap ran the
    0.88 mm opening down to the magnet's 0.72 mm-wide inner end, its corners
    cut 0.08 mm into both 0.13 mm iron webs between neighbouring pockets and
    every iron spoke came off the hub — the rotor core in 11 pieces.  That is
    the 2026-09-05 "косяк внизу магнитов" again, back on any machine whose
    magnet is shorter than ~2 mm + the opening's own taper.  Where the magnet
    is at least as wide as the opening all the way down to the fixed overlap
    (every machine built before this) the depth is exactly what it was."""
    try:
        mag_h = float(p.get('magnet_height', 0.0) or 0.0)
        gap = float(p.get('magnet_up_gap', 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if _extended_pocket(p):
        return 0.0
    depth = max(0.0, min(mag_h, gap + 2.0))
    limit = _pocket_cut_depth_limit(p)
    if limit is not None and limit < depth:
        depth = limit
    return depth


def _pocket_cut_depth_limit(p: Dict) -> Optional[float]:
    """The deepest the rotor_hole < 1 opening may go (measured, like the cut
    itself, along the pole axis from y = rotor_or) without its corners
    leaving the magnet outline.

    Pole-local frame (pole on +y), the same one `rect_local` and `mag_local`
    are built in: the magnet's +x side is the polyline mp1 → mp2 → mp3 (radial
    foot of magnet_down_height, then the slanted face to the top corner).  The
    opening's half-width is w = rotor_or·sin(pole·fill_up·rotor_hole/2).  The
    cut is safe down to the lowest y from which the side stays at x ≥ w all
    the way up to mp3; below that the rectangle's corner is in rotor iron.

    None when the parameters needed are missing (a partial dict), or when the
    magnet is narrower than the opening even at its top corner (mp3.x < w) —
    then no depth keeps the corners inside it, the opening is a wider slot
    than the magnet and the depth stays what it was (the tabs over the magnet
    corners are then zero-thickness, which `rotor_hole_gap_error` owns)."""
    try:
        rotor_or = float(p['rotor_outer_radius'])
        magnet_r = float(p['rotor_inner_radius']) + float(p['rotor_house_height'])
        n_poles = int(p['num_poles'])
        fd = float(p['magnet_fill_down'])
        fu = float(p['magnet_fill_up'])
        hole = float(p['rotor_hole'])
        down_h = float(p.get('magnet_down_height', 0.0) or 0.0)
        gap = float(p.get('magnet_up_gap', 0.0) or 0.0)
    except (KeyError, TypeError, ValueError):
        return None
    if n_poles <= 0 or rotor_or <= 0.0:
        return None
    pole = 2.0 * pi / n_poles
    a_dn = pole * fd / 2.0
    a_up = pole * fu / 2.0
    w = rotor_or * sin(pole * fu * hole / 2.0)
    # the +x side, bottom to top — the SAME expressions as mag_local
    side = [(magnet_r * sin(a_dn), magnet_r * cos(a_dn)),
            ((magnet_r + down_h) * sin(a_dn), (magnet_r + down_h) * cos(a_dn)),
            ((rotor_or - gap) * sin(a_up), (rotor_or - gap) * cos(a_up))]
    if side[2][0] < w:
        return None
    # walk down from the top corner; the first segment whose lower end is
    # narrower than w holds the lowest admissible y
    y_ok = side[2][1]
    for (x0, y0), (x1, y1) in ((side[1], side[2]), (side[0], side[1])):
        if x0 >= w:
            y_ok = min(y_ok, y0)
            continue
        if x1 > x0:
            y_ok = min(y_ok, y0 + (w - x0) * (y1 - y0) / (x1 - x0))
        break
    else:
        return None          # the whole side is at least as wide as the opening
    return max(0.0, rotor_or - y_ok)


# ═══════════════════════════════════════════════════════════════════════════
#  rotor_hole >= 1: the pocket is the magnet outline with STRAIGHT sides
#  run out to the rotor OD — no rectangle, no fillet, no wedge
# ═══════════════════════════════════════════════════════════════════════════
#
# WHY (user, 2026-09-06, two zoomed pictures of the Ø200 pocket's top corner):
#
#   * with magnet_up_gap = 1 the rectangular opening had VERTICAL sides while
#     the magnet's side edge (mp2→mp3) is slanted, so where the cut's side met
#     the magnet's rounded corner the iron showed a small step — "артефакт";
#   * with magnet_up_gap = 0 the pocket equalled the FILLETED magnet, so iron
#     filled the magnet's 2.5 mm corner fillet and ended in a sharp wedge
#     between the fillet arc and the sleeve bore — "кусок ротора, который будет
#     давать опять жуткие перегрузки и не нужен совсем; нужно сделать грань
#     ротора прямой".
#
# Both are the same defect: the iron face beside a magnet was not one straight
# line.  It is now.  The pocket boundary is the UNFILLETED magnet outline
# mp1→mp2→E3 … E4→mp5→mp6, where E3/E4 are the magnet's own side lines extended
# beyond mp3/mp4 to the circle r = rotor_or, closed across the top along the OD
# itself.  So the iron face runs mp2 → OD without a single intermediate vertex
# and meets the OD at one corner: no tooth, no wedge.  Everything between the
# magnet (arc top + corner fillets, unchanged) and this boundary is pocket AIR,
# which the in_band builders pick up for free — they are `disk − rotor − magnets
# − shaft − sleeve`, so any area that is in neither solid IS rotor-side air.


def _extended_pocket(p: Dict) -> bool:
    """True when the rotor pocket is built with straight sides run to the OD.

    The switch is ``rotor_hole >= 1`` — at rotor_hole < 1 the user is asking for
    a NARROWER opening than the magnet, which is what the rectangle expresses,
    and nothing about that path changes."""
    try:
        return float(p.get('rotor_hole', 0.0) or 0.0) >= 1.0 - 1e-9
    except (TypeError, ValueError):
        return False


def _pocket_side_to_od(p_low, p_top, rotor_or: float):
    """Where the magnet's side edge ``p_low → p_top``, extended BEYOND p_top,
    crosses the circle r = rotor_or.

    At magnet_up_gap = 0 the top corner is already on that circle and this
    returns it unchanged (t = 0 exactly), so the zero-gap pocket is the magnet's
    own outline and nothing is invented."""
    dx = p_top[0] - p_low[0]
    dy = p_top[1] - p_low[1]
    n = hypot(dx, dy)
    if n <= 1e-12:
        return (float(p_top[0]), float(p_top[1]))
    dx /= n
    dy /= n
    b = p_top[0] * dx + p_top[1] * dy
    c = p_top[0] ** 2 + p_top[1] ** 2 - rotor_or ** 2
    disc = b * b - c
    if disc < 0.0:                       # the line misses the OD — cannot happen
        return (float(p_top[0]), float(p_top[1]))
    t = -b + sqrt(disc)
    if not (t > 0.0):                    # corner already at / past the OD
        return (float(p_top[0]), float(p_top[1]))
    return (p_top[0] + t * dx, p_top[1] + t * dy)


#: Angular stations of the rotor OD — `_circle_points`' own count.  The pocket's
#: top MUST be drawn on these and only these.
_OD_STATIONS = 256


def _od_station(r: float, k: int, n: int = _OD_STATIONS):
    """Vertex k of the OD/sleeve-bore ring, in the SAME expression
    `_circle_points` uses — so the value is bit-identical to that ring's."""
    th = 2 * pi * (k % n) / n
    return (r * cos(th), r * sin(th))


def _pocket_corner_on_od(p_low, p_top, rotor_or: float, n: int = _OD_STATIONS):
    """Where the magnet's side edge ``p_low → p_top`` (extended) meets the rotor
    OD **polyline**, plus the index of the station just counter-clockwise of it.

    Not the ideal circle — the POLYLINE, i.e. the 256-gon `_circle_points` draws
    the OD with.  The circle intersection sits up to one sagitta (4.8 µm on the
    Ø200) outside that polygon, and a corner outside the disk means the pocket's
    end chord crosses the OD boundary somewhere else, leaving a needle of iron
    between the two.  Measured 2026-09-06 on the Ø200 before this snap: four
    0.0025 mm² islands, and the rotor came out of the difference as FIVE pieces
    instead of one.  Landing the corner on the polyline makes every pocket-top
    segment lie exactly on the OD boundary, so the difference removes it cleanly
    and the rotor stays one body.
    """
    e = _pocket_side_to_od(p_low, p_top, rotor_or)      # ideal-circle crossing
    step = 2.0 * pi / n
    k = int(floor(atan2(e[1], e[0]) / step))            # chord [k, k+1] holds it
    a = _od_station(rotor_or, k, n)
    b = _od_station(rotor_or, k + 1, n)
    dx, dy = p_top[0] - p_low[0], p_top[1] - p_low[1]
    ex, ey = b[0] - a[0], b[1] - a[1]
    den = dx * ey - dy * ex
    if abs(den) > 1e-15:
        t = ((a[0] - p_low[0]) * ey - (a[1] - p_low[1]) * ex) / den
        px, py = p_low[0] + t * dx, p_low[1] + t * dy
        u = ((px - a[0]) * ex + (py - a[1]) * ey) / (ex * ex + ey * ey)
        if -1e-9 <= u <= 1.0 + 1e-9:
            return (px, py), k, u
    return e, k, 0.5            # degenerate — keep the circle point


def _extended_pocket_pts(mag_local, rotor_or: float, a_global: float,
                         n: int = _OD_STATIONS):
    """The pocket hole for ONE pole, in global coordinates.

    ``[mp1, mp2, E3, <OD stations between E3 and E4>, E4, mp5, mp6]``.  The two
    OD corners are found per side in GLOBAL coordinates (the station grid is
    global, so the pocket is not mirror-symmetric about the pole axis unless the
    pole happens to sit on a station) and the stations in between are emitted
    with `_od_station`, so every one of them IS a vertex of the OD ring — no
    slivers where the pocket meets the rim.

    Deliberately NOT `_magnet_top_arc_global`: that helper drops the stations
    within 0.25 step of its corners (`_MAG_ARC_CORNER_SKIP`) to protect the
    MAGNET's top corners from the weld pass.  Here a dropped station is a
    stranded vertex of the rotor rim sitting outside the pocket, which is the
    island the docstring of `_pocket_corner_on_od` measures.  Every station
    inside the span is emitted.

    A station CAN still land within the weld tolerance of a corner and be merged
    into it by the ring sanitiser.  That is not harmless — it tilts the segment
    the corner ends — but it is contained where it matters: the 2-D builders go
    through `_extended_pocket_poly`, which splices the magnet's own nodes onto
    the two side edges so the tilt cannot reach past the magnet's corner fillet.
    """
    c, s = cos(a_global), sin(a_global)
    g = [(x * c - y * s, x * s + y * c) for x, y in mag_local]
    e3, k3, u3 = _pocket_corner_on_od(g[1], g[2], rotor_or, n)   # +side, mp2→mp3
    e4, k4, u4 = _pocket_corner_on_od(g[4], g[3], rotor_or, n)   # −side, mp5→mp4
    while k4 < k3:              # atan2 wrapped between the two corners
        k4 += n
    lo = k3 + (2 if u3 >= 1.0 - 1e-9 else 1)     # first station strictly ccw of E3
    hi = k4 + (-1 if u4 <= 1e-9 else 0)          # last station strictly cw of E4
    arc = [_od_station(rotor_or, k, n) for k in range(lo, hi + 1)]
    return [g[0], g[1], e3] + arc + [e4, g[4], g[5]]


def _od_ring_with_pocket_corners(rotor_or: float, hole_polys, n: int = _OD_STATIONS):
    """The rotor OD ring (`_circle_points` stations) with every pocket's OD
    corner (E3 / E4) spliced in as a SHARED vertex.

    WHY (2026-09-06): E3/E4 come from a line/chord intersection, so they sit
    ~1e-15 mm off the chord they were computed on.  `rotor_disk.difference(
    hole)` then does not recognise the pocket's top edge (E4 → station) as the
    rim's own edge (station → E4): the rim keeps the station and walks back
    along the chord to E4 — a zero-width IRON NEEDLE ~1.2 mm long at every
    pocket corner (measured: an interior angle of 0.0° at θ = 97.03° on the
    Ø200, right next to the pocket corner at 98.10°).  Shapely still calls the
    polygon valid, gmsh gets a sliver, and the pole-tip fillet cannot find the
    corner.  With the corner present in BOTH rings as the same float pair the
    overlay is exact and the difference removes the pocket top cleanly.

    Corners are recognised as hole vertices within 0.02 mm of the OD that are
    not station points; they are inserted in angular order."""
    ring = list(_circle_points(rotor_or))
    step = 2.0 * pi / n
    extra = []
    for h in hole_polys:
        geoms = list(h.geoms) if h.geom_type == 'MultiPolygon' else [h]
        for g in geoms:
            for x, y in g.exterior.coords[:-1]:
                if hypot(x, y) < rotor_or - 0.02:
                    continue
                th = atan2(y, x) % (2.0 * pi)
                frac = th / step
                if abs(frac - round(frac)) < 1e-7:      # a station itself
                    continue
                extra.append((th, (float(x), float(y))))
    if not extra:
        return ring
    keyed = [((atan2(y, x) % (2.0 * pi)), (x, y)) for x, y in ring]
    keyed.extend(extra)
    keyed.sort(key=lambda t: t[0])
    out = []
    for _, pt in keyed:
        if out and abs(out[-1][0] - pt[0]) < 1e-12 and abs(out[-1][1] - pt[1]) < 1e-12:
            continue
        out.append(pt)
    return out


def _side_nodes_from_magnet(p_low, e, magnet_poly, tol: float):
    """The MAGNET's own vertices that sit on the pocket's side segment
    ``p_low → e``, ordered from p_low outwards.

    WHY they belong in the pocket outline (measured 2026-09-06 on the Ø200):
    the ring sanitiser welds each domain on its own, and the pocket's OD corner
    can land within the weld tolerance (diameter/4000 = 0.05 mm here) of a rim
    station.  Merging the two rotates the segment the corner ends — and with a
    bare ``[mp2, E3]`` outline that segment is the WHOLE 25 mm iron face, so it
    sweeps across the magnet's side edge and buries up to 0.86 mm² of magnet in
    iron: 18 × the 0.048 mm² the static-3D validator refused on 2026-08-24.

    Splicing the magnet's own nodes in pins the face to the magnet: a weld can
    then only rotate the stub ABOVE the last shared node, which is where the
    magnet has already curved away into its corner fillet, so nothing overlaps.
    The nodes are exactly collinear with the segment, so when no weld fires the
    sanitiser drops them again and the ring is what it always was.
    """
    if magnet_poly is None or getattr(magnet_poly, 'is_empty', True):
        return []
    if getattr(magnet_poly, 'geom_type', '') != 'Polygon':
        return []
    dx, dy = e[0] - p_low[0], e[1] - p_low[1]
    L = hypot(dx, dy)
    if L <= tol:
        return []
    ux, uy = dx / L, dy / L
    out = []
    for x, y in list(magnet_poly.exterior.coords)[:-1]:
        rx, ry = x - p_low[0], y - p_low[1]
        t = rx * ux + ry * uy
        if not (tol < t < L - tol):
            continue                        # p_low itself, the corner, or beyond
        if abs(rx * (-uy) + ry * ux) > tol:
            continue                        # not on the side line
        out.append((t, (float(x), float(y))))
    out.sort()
    return [q for _t, q in out]


def _extended_pocket_poly(spoly, mag_local, rotor_or: float, a_global: float,
                          magnet_poly, node_tol: float = 1e-9):
    """`_extended_pocket_pts` as a shapely polygon, guarded.

    The two side edges also carry the magnet's own nodes (see
    `_side_nodes_from_magnet` — they keep a point-weld at the OD corner from
    sweeping the whole face across the magnet).  They are collinear, so the
    shape is unchanged.

    The pocket MUST contain the magnet — a magnet poking into rotor iron is the
    one defect this whole area keeps producing (incident 2026-08-24).  It does
    by construction (the magnet is the same outline, filleted, under a top arc
    that never leaves the pocket's angular span), so the guard should never
    fire; if it ever does, the magnet is unioned back in rather than left
    embedded in iron."""
    pts = _extended_pocket_pts(mag_local, rotor_or, a_global)
    # pts = [mp1, mp2, E3, <arc>, E4, mp5, mp6] — splice on the two side edges
    plus = _side_nodes_from_magnet(pts[1], pts[2], magnet_poly, node_tol)
    minus = _side_nodes_from_magnet(pts[-2], pts[-3], magnet_poly, node_tol)
    pts = pts[:2] + plus + pts[2:-2] + minus[::-1] + pts[-2:]
    poly = spoly(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.area <= 0.0:
        log.warning("extended rotor pocket degenerate at %.3f rad — falling "
                    "back to the magnet outline", a_global)
        return magnet_poly
    if not poly.buffer(1e-9).covers(magnet_poly):
        log.warning("extended rotor pocket does not cover the magnet at "
                    "%.3f rad — unioning the magnet back in", a_global)
        poly = _safe_union(poly, magnet_poly)
        if not poly.is_valid:
            poly = poly.buffer(0)
    return poly


# ═══════════════════════════════════════════════════════════════════════════
#  Magnet top edge: ALWAYS the arc on the circle r = rotor_or − magnet_up_gap
# ═══════════════════════════════════════════════════════════════════════════
#
# WHY (user, 2026-09-06: "я убрал всё, но у нас геометрия всё равно не
# соединяется со сливом" — with magnet_up_gap = 0 and rotor_hole = 1 the magnet
# STILL did not touch the retaining sleeve):
#
#   the magnet's top edge used to be the straight CHORD between mp3 and mp4,
#   both on the circle r = rotor_or − magnet_up_gap.  At the pole centre that
#   chord sits below the circle by the sagitta — on the Ø200 machine 63.2 mm ×
#   (1 − cos 8.1°) ≈ 0.63 mm — and the only two points that DID touch, the
#   corners, are then filleted away by magnet_fill_radius.  So even at zero gap
#   there was a crescent of air between the magnet and the sleeve bore, and the
#   magnets' centrifugal load went into the rotor iron instead of into the
#   carbon band.
#
# The chord was briefly a choice (`magnet_top: flat | arc`).  The user closed it
# the same day — "давай по умолчанию сделаем только arc и уберём прямую вообще"
# (2026-09-06) — so there is now ONE magnet top: the arc.  A machine, die,
# preset or catalog entry that still carries a `magnet_top` key is read as if it
# did not: the key is ignored, never errors and is never written back.

#: The magnet arc is sampled at the SAME angular stations `_circle_points` lays
#: on the rotor OD / sleeve bore, but a station within this fraction of one step
#: from a top corner is dropped: it would leave a micro chord that the ring
#: sanitiser (weld = diameter/4000) would then merge — moving the corner itself.
#: 0.25 of a step is 0.39 mm on the Ø200, 8x the weld tolerance there.
_MAG_ARC_CORNER_SKIP = 0.25


def _magnet_top_arc_global(r_top: float, angle_up: float, a_global: float,
                           n_circle: int = 256):
    """The INTERIOR points of an arc-topped magnet's outer edge, in GLOBAL
    coordinates, running from the +side top corner (mp3) to the −side one (mp4)
    of a pole rotated by ``a_global``.

    Discretisation — the whole point of this helper (user 2026-09-06, the
    magnets must press on the sleeve): the stations are EXACTLY the ones
    `_circle_points(r, 256)` uses, i.e. θ_k = 2πk/256, and the coordinates are
    computed with the SAME expression, so at magnet_up_gap = 0 every vertex of
    the magnet's top is bit-identical to a vertex of the rotor OD ring and of
    the sleeve bore ring.  A private sagitta arc (what `_arc_points` would give)
    would land BETWEEN those stations and leave micron slivers of air along the
    contact — exactly the air the user is trying to remove — plus needle
    triangles for gmsh at every crossing.

    The corners themselves (mp3/mp4) are NOT emitted here: they are the magnet's
    own landmarks and the caller already has them.
    """
    step = 2.0 * pi / int(n_circle)
    lo = (pi / 2.0 - angle_up) + a_global          # global angle of mp3
    hi = (pi / 2.0 + angle_up) + a_global          # global angle of mp4
    if not (hi > lo):
        return []
    skip = _MAG_ARC_CORNER_SKIP * step
    k0 = int(ceil((lo + skip) / step))
    k1 = int(floor((hi - skip) / step))
    if k1 < k0:
        # The corner guard ate the whole span.  Try the bare span first; if even
        # that holds no station the magnet is narrower than one 256-gon step
        # (< 1.41°), and its "arc" and its chord differ by under 5 µm on a Ø200
        # rotor — the top then stays the chord, which is the honest shape at
        # that width and still ends on the ring at both corners.
        k0 = int(ceil(lo / step + 1e-9))
        k1 = int(floor(hi / step - 1e-9))
    out = []
    for k in range(k0, k1 + 1):
        th = 2.0 * pi * k / n_circle               # same form as _circle_points
        out.append((r_top * cos(th), r_top * sin(th)))
    return out


def _open_fillet_at_top(poly, r_top: float, min_deg: float = 12.0,
                        open_top: bool = True, open_wall: bool = False,
                        weld_tol: float = 0.0):
    """End every corner fillet with a chord that meets its neighbour at a
    finite angle instead of tangentially.

    WHY (user 2026-09-07, mesh view: "обрати внимание на углы магнитов, что-то
    тут не так"): a fillet arriving tangentially leaves a 0°…8° wedge of pocket
    air between the arc and the surface it meets — the sleeve bore on top
    (magnet_up_gap = 0) and the straight iron pocket wall on the side
    (rotor_hole = 1).  Triangle cannot mesh such a wedge and answers with a fan
    of micro-elements at its tip (609 triangles under 0.002 mm² at the wall
    junction of the live Ø200, 27 143 in the half-rotor; and on some sweep
    points an access violation — see geo_mesh._blunt_cusps, the mesh-side
    safety net).  Dropping the fillet's first vertices next to the junction
    replaces the tangent arrival by one chord at ≥ `min_deg` to the surface:
    the magnet loses a sliver of at most one chord's sagitta (≈ 0.02 mm on a
    2.5 mm fillet, −0.02 % of its area) and the wedge gets a proper tip.
    `open_top` handles junctions with the top circle (only meaningful when the
    magnet seats on the sleeve), `open_wall` junctions where a LONG straight
    edge runs into the arc (only meaningful when the pocket wall is straight).
    Everything else keeps its outline bit for bit.

    `weld_tol` is the ring sanitiser's point-merge distance (diameter/4000):
    the junction vertex may still be welded onto a rim station up to that far
    along the surface AFTER this pass, which flattens the first chord (measured
    on the Ø200 fixture: a 12.5° chord became 9.3° after a 0.05 mm weld).  The
    chord is therefore judged with the junction slid `weld_tol` away from the
    arc — the angle it can at worst end up with."""
    import numpy as _np
    try:
        if poly.geom_type != 'Polygon' or not (open_top or open_wall):
            return poly
        P = _np.asarray(poly.exterior.coords[:-1], float)
        n = len(P)
        if n < 8:
            return poly
        r = _np.hypot(P[:, 0], P[:, 1])
        # 'on the circle' must tolerate the ring sanitiser's welds (up to
        # diameter/4000 = `weld_tol`): a welded station read as an off-circle
        # point looked like a 1.4 deg 'junction' and confused the chain walk.
        #
        # 1.2 x THAT, not a constant.  It used to be a flat 0.06 mm, which is
        # 1.2 x the Ø200's own 0.05 mm weld and was therefore right on the
        # machine it was measured on — and five times too generous on a Ø50,
        # where the weld is 0.0125 mm.  On the owner's CIANO14 50 / L15
        # (magnet_fill_radius 0.2 mm, fillet chords 0.069 mm) that band swallowed
        # the fillet's own vertices: the first vertex past the wall junction sat
        # 0.046 mm from the top circle, `_walk` read it as "on the top arc",
        # broke out before dropping anything, and the fillet kept arriving at the
        # pocket wall TANGENTIALLY.  The 4 µm cusp that leaves meshed into 22
        # triangles of 0.00 deg in the mechanical mesh, which froze the contact
        # solve and refused the machine (2026-09-21).  Scaled to the weld the
        # band means what it says and the Ø200's value is unchanged to the bit
        # (1.2 x 200/4000 = 0.06).
        _top_band = max(1e-6 * max(1.0, float(r_top)), 1.2 * float(weld_tol))
        on_top = _np.abs(r - float(r_top)) < _top_band
        keep = _np.ones(n, bool)

        def _ang(u, w):
            nu, nw = _np.linalg.norm(u), _np.linalg.norm(w)
            if nu < 1e-12 or nw < 1e-12:
                return 180.0
            return degrees(acos(max(-1.0, min(1.0, float(u @ w) / (nu * nw)))))

        def _chord_deg(tangent, i, k):
            """Angle of chord P[i]→P[k] to `tangent`, with the junction slid
            `weld_tol` along the tangent away from the chord (worst-case weld)."""
            t = tangent / max(_np.linalg.norm(tangent), 1e-12)
            v = P[k] - P[i]
            along = float(v @ t) + float(weld_tol)
            across = abs(float(v[0] * t[1] - v[1] * t[0]))
            return degrees(atan2(across, along))

        def _walk(i, step, tangent):
            """Drop arc vertices after junction i (direction `step`) until the
            chord from P[i] leaves `tangent` by ≥ min_deg.  Never walks into a
            top station or past a long (wall) edge."""
            j = _distinct(i, step)
            first_len = _np.linalg.norm(P[j] - P[i])
            dropped = 0
            while dropped < 12:
                nxt = _distinct(j, step)
                if on_top[nxt] or _np.linalg.norm(P[nxt] - P[j]) > 4.0 * max(first_len, 1e-9):
                    break
                if _chord_deg(tangent, i, nxt) >= min_deg:
                    keep[j] = False
                    break
                keep[j] = False
                dropped += 1
                j = nxt

        def _distinct(i, step):
            """Next vertex from i in direction `step` that is not a duplicate
            of P[i] (the corner clip can leave an exact double at the junction;
            a zero-length 'edge' there would read as a 0° chord and make the
            walk eat the whole fillet)."""
            k = (i + step) % n
            while k != i and _np.linalg.norm(P[k] - P[i]) < 1e-9:
                k = (k + step) % n
            return k

        for i in range(n):
            for step in (+1, -1):
                j = _distinct(i, step)
                back = _distinct(i, -step)
                if j == i or back == i:
                    continue
                e_in = P[i] - P[back]                   # the edge arriving at i
                e_out = P[j] - P[i]                     # the first edge leaving i
                if open_top and on_top[i] and (not on_top[j]) and on_top[back]:
                    # top junction: the bore direction is the arriving chord
                    if _chord_deg(e_in, i, j) < min_deg:
                        _walk(i, step, e_in)
                    continue
                if open_wall and (not on_top[i]) and (not on_top[j]):
                    # wall junction: a LONG straight edge (≥ 4 × the first arc
                    # chord) meets a short chord with a small turn
                    lin, lout = _np.linalg.norm(e_in), _np.linalg.norm(e_out)
                    if lin >= 4.0 * lout and 0.0 < _ang(e_in, e_out) and _chord_deg(e_in, i, j) < min_deg:
                        _walk(i, step, e_in)
        if keep.all():
            return poly
        from shapely.geometry import Polygon as _SP
        out = _SP(P[keep])
        if not out.is_valid:
            out = out.buffer(0)
        if (out.is_valid and not out.is_empty and out.geom_type == 'Polygon'
                and abs(out.area - poly.area) <= 0.005 * poly.area):
            return out
        return poly
    except Exception:                             # noqa: BLE001 — never fail a build here
        return poly


def _fillet_magnet_top_arc(poly, fillet_r: float, r_top: float, scale_mm: float,
                           open_top: bool = False, open_wall: bool = False):
    """Round the two TOP corners of the magnet (magnet_fill_radius).

    Applied AFTER the arc, never before: the fillet has to eat into the real
    outline, and the two corners it rounds are the ends of the arc.

    Goes through `_fillet_ring_corners`, not the local `_fillet_corner` twins:
    that core measures the tangent length ALONG the real boundary, so the side
    that runs into the discretised top arc is handled without the arc's chords
    being mistaken for the corner's straight leg.  The fillet is therefore
    tangent to the top CHORD it lands in rather than to the ideal circle — an
    approximation, measured on the live Ø200 with magnet_fill_radius = 2.5 mm as
    |d(centre) − (r_top − r_fillet)| ≤ 0.011 mm over all 20 corners (mean
    0.0026 mm), i.e. inside 0.02 mm of true tangency.  The realised radius comes
    out slightly under the nominal (2.36 vs 2.50 mm) because the core caps the
    tangent length at 49 % of the run to the next real corner.

    Selection is by RADIUS: only vertices sitting on the top circle are
    candidates, and of those the interior arc stations are rejected by the
    core's own corner test (their interior angle is 180° − 360/256 = 178.6°,
    above ang_max_deg = 168°).  So exactly mp3 and mp4 are rounded — the same
    two corners the retired flat build rounded with its index pair.

    Clipped to the raw outline — and here it is not belt-and-braces, it is
    required.  The core takes the
    corner's half-angle from the IMMEDIATE neighbours but places the tangent
    points at arc-distance t along the boundary; on the top arc those are two
    different directions (0.3° apart at the 256-gon's spacing), so the fillet's
    arc starts a few microns OUTSIDE the polyline — measured 2.7 µm and 12 µm on
    the Ø200 poles, i.e. a magnet poking through the sleeve bore, which is the
    one thing this whole mode must not do.  Clipping puts the boundary back on
    the magnet's own outline; the stations themselves are untouched by it (they
    are not near the clip) and stay bit-identical to the OD ring's.
    """
    import numpy as _np
    base = poly if poly.is_valid else poly.buffer(0)
    if fillet_r <= 0 or base.is_empty or base.geom_type != 'Polygon':
        return base

    def _sel(radii, P):
        return _np.abs(radii - float(r_top)) < 1e-6

    try:
        f = _fillet_ring_corners(base, float(fillet_r), _sel, scale_mm=scale_mm)
        if not f.is_valid:
            f = f.buffer(0)
        f = f.intersection(base)
        if not f.is_valid:
            f = f.buffer(0)
        if (f.is_valid and not f.is_empty and f.geom_type == 'Polygon'
                and f.area <= base.area * (1.0 + 1e-9)
                and f.area >= 0.5 * base.area):
            return (_open_fillet_at_top(f, r_top, open_top=open_top, open_wall=open_wall,
                                        weld_tol=float(scale_mm) / _WELD_DIV)
                    if (open_top or open_wall) else f)
    except Exception as _e:                       # noqa: BLE001
        log.warning("magnet top arc: corner fillet failed (%s) — keeping sharp "
                    "magnet corners", _e)
    return base


def _sleeve_thickness(p: Dict) -> float:
    """``sleeve_thickness`` [mm] out of a geometry/parameter dict, or 0.0.

    ONE reader, because "is there a sleeve?" is asked from the polygon builder,
    the 3-D mesh builder, the masses and the validator, and a machine that
    predates the knob (every one but the Ø200) must answer 0.0 identically in
    all four — a missing key, a None, a string from a hand-edited YAML and a
    negative number all mean "no sleeve", never a crash and never a ring.
    """
    from math import isfinite as _isfinite
    try:
        t = float(p.get('sleeve_thickness', 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return t if (_isfinite(t) and t > 0.0) else 0.0


def _wire_split(p: Dict) -> int:
    """``wire_split`` [-] out of a geometry/parameter dict, or 1.

    ONE reader, for the same reason ``_sleeve_thickness`` is one: the strips are
    drawn by the 3-D coil builder, the 2-D viewer mesh, the FEM polygons AND the
    enamel envelope, and a missing key, a None, a string out of a hand-edited
    YAML and a 0 must all mean "one solid wire" in every one of them.
    """
    from math import isfinite as _isfinite
    try:
        n = float(p.get('wire_split', 1) or 1)
    except (TypeError, ValueError):
        return 1
    if not _isfinite(n):
        return 1
    n = int(round(n))
    return n if n >= 1 else 1


def _strip_columns(x0: float, wire_w: float, n_split: int, spacing_x: float):
    """[(x_left, width)] for the strips of ONE turn, left to right.

    ``wire_w`` is ONE STRIP — the user halves the wire himself when he splits a
    bar in two (2026-09-08: *"сделай ширину провода 4,5 мм, слот станет чуть
    больше, я бы гап между проводами сделал 2·Wire Spacing X"*).  So ``n_split``
    strips of ``wire_w`` are laid side by side starting at ``x0``, separated by
    ``STRIP_GAP_FACTOR × spacing_x`` of insulation, and the row spans
    ``_strip_span``.

    ``spacing_x`` is the RAW ``wire_spacing_x``; the ×2 lives in
    ``winding.STRIP_GAP_FACTOR`` so the polygon builder, the slot cutter, the
    validator and the thermal template cannot drift apart on it.

    ``n_split <= 1`` returns exactly ``[(x0, wire_w)]``, so an unsplit machine
    walks the identical single-rectangle path it always has.
    """
    from motor_ai_sim.winding import STRIP_GAP_FACTOR as _GF
    n = max(1, int(n_split))
    if n == 1:
        return [(x0, wire_w)]
    gap = _GF * float(spacing_x)
    return [(x0 + i * (wire_w + gap), wire_w) for i in range(n)]


def _strip_span(wire_w: float, n_split: int, spacing_x: float) -> float:
    """Tangential space one turn's strips OCCUPY [mm] — copper AND gaps.

    ``n·wire_w + (n−1)·STRIP_GAP_FACTOR·spacing_x``.  Everything that places
    something against the far edge of a wire (the slot cutter, the enamel
    envelope, the −x column's origin, the thermal template's coil column) has
    to use THIS, or the outermost strip is drawn inside the iron.
    """
    from motor_ai_sim.winding import STRIP_GAP_FACTOR as _GF
    n = max(1, int(n_split))
    if n == 1:
        return wire_w
    return n * wire_w + (n - 1) * _GF * float(spacing_x)


def _sanitize_polys_dict(polys: Dict, scale_mm: float,
                         node_map: Optional[Dict] = None,
                         done=()) -> Dict:
    """Sanitize every ring of every domain in a get_2d_polygons()-shaped dict.

    `done` names the keys a `_weld_group_geoms` call already covered.  Those are
    skipped, NOT re-run: sanitising an already-sanitised ring a second time is
    not a no-op — the first pass drops the exactly-collinear nodes that pin the
    pocket wall to the magnet, and a weld on the stripped ring rotates the whole
    iron face (0.47 mm² of magnet buried, measured 2026-09-06).

    `node_map` replays an agreed weld on a domain OUTSIDE that group.  No caller
    uses it today (see the call in `get_2d_polygons` for why the rotor-side map
    is not pushed onto `air_gap`); it is the channel a second group would need."""
    for key in ('stator', 'rotor', 'sleeve', 'shaft', 'air_gap',
                'in_band', 'out_band'):
        if key in done:
            continue
        if key in polys and polys[key] is not None:
            polys[key] = _sanitize_geom(polys[key], scale_mm, key, node_map)
    if 'magnets' in polys and 'magnets' not in done:
        polys['magnets'] = [(_sanitize_geom(mp, scale_mm, f"magnet[{i}]", node_map), pol)
                            for i, (mp, pol) in enumerate(polys['magnets'])]
    for key in ('coils', 'wire_insulation', 'slot_insulation'):
        if key in done:
            continue
        if key in polys and polys[key]:
            polys[key] = [_sanitize_geom(g, scale_mm, f"{key}[{i}]", node_map)
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
            # A near-zero corner angle (a spike vertex, e.g. an absurd
            # tooth_width driving the slot width negative) has sin(half) → 0
            # with reff → 0 too: the centre went 0/0 = NaN and the NaN then
            # crashed the arc-point count with "cannot convert float NaN to
            # integer" — an unreadable failure for what is simply an unfillable
            # corner.  Keep the vertex sharp; the region validator downstream
            # is the one that names WHY the geometry is impossible.
            _sh = _m.sin(half)
            if abs(_sh) < 1e-9 or not _m.isfinite(reff / _sh):
                out.append(tuple(V)); continue
            C = V + bis * (reff / _sh)
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
                    'magnet_lamination_tan',    # in-plane segment size, mm (geometry split); 0 = off
                    'sleeve_thickness']:        # carbon-fibre retaining ring on the rotor OD, mm; 0 = none
            if key not in mapped:
                mapped[key] = config_params.get(key, 0.0)

        # A stale `magnet_top` from a machine saved on 2026-09-06, while the
        # flat/arc choice briefly existed, rides along untouched: nothing reads
        # it any more (the top is always the arc — user, same day: "уберём
        # прямую вообще"), so it cannot change a polygon, and dropping it here
        # would only make the dict differ from the one the caller passed in.

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
        # 1a. Retaining sleeve (carbon-fibre ring on the rotor OD) — a solid of
        # its own so the 3-D viewer and its component tree list it; only when
        # the geometry has one (user 2026-09-04: the ring showed in the 2-D
        # cross-section but not in "Motor Assembly").
        try:
            if _sleeve_thickness(self.parameters) > 0.0:
                self.parts['sleeve'] = self._create_sleeve(cq)
        except Exception as e:
            print(f"Failed to build sleeve: {e}")

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

        # The wire COLUMN, not one strip's width: wire_split = N lays N strips
        # of wire_width side by side with (N−1) gaps of 2·wire_spacing_x between
        # them (see _strip_span), and the pocket that holds them has to be that
        # much wider or the outermost strip is drawn inside the iron.
        # N = 1 → wire_col_w == wire_w.
        wire_col_w = _strip_span(wire_w, _wire_split(p), wire_d_x)
        slot_w  = wire_col_w + ins_w*2 + wire_d_x
        slot_h  = slot_height
        slot_x  = tooth_width / 2
        slot_y  = outer_r - core_h
        half_slots  = num_slots // 2
        slot_angle  = 360.0 / half_slots

        # ── Compound-cutter geometry ──────────────────────────────────────────
        # All cuts are unioned into one solid then cut in a single boolean.
        # This is ~70× faster than sequential cuts.
        cut_x  = tooth_width/2 + ins_w*2 + wire_col_w + wire_d_x*2 + tooth2_width
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

    def _create_sleeve(self, cq) -> Any:
        """Retaining sleeve: a plain ring rotor_or .. rotor_or + sleeve_thickness,
        the full stack length, rotating with the rotor."""
        p = self.parameters
        rotor_or = float(p['rotor_outer_radius'])
        t = _sleeve_thickness(p)
        length = p['motor_length']
        return (
            cq.Workplane("XY")
            .circle(rotor_or + t)
            .circle(rotor_or)
            .extrude(length)
        )

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

        # The magnet top is the ARC on r = rotor_or − magnet_up_gap (user
        # 2026-09-06: the magnets must press straight on the carbon sleeve, and
        # later that day "уберём прямую вообще" — the chord is gone).  The
        # outline is therefore not a hexagon, so `edges(">Y and |Z")` — which
        # picks the vertical edges at max Y — would grab the single station at
        # the pole centre instead of the two corners.  Build the profile as the
        # SAME 2-D polygon the mesher and the validator get (arc stations +
        # corner fillets already in it) and extrude that, so the 3-D view cannot
        # show a different magnet from the one solved.  The top is faceted at
        # the OD ring's 256-gon, which is what the 2-D build is.
        arc_pts = ([p1, p2, p3]
                   + _magnet_top_arc_global(rotor_outer_r - mag_up_gap,
                                            angle_up, 0.0)
                   + [p4, p5, p6])
        profile = arc_pts
        _scale_mm = 2.0 * float(p['stator_outer_radius'])
        try:
            from shapely.geometry import Polygon as _SPoly
            _mp = _fillet_magnet_top_arc(_SPoly(arc_pts), mag_fill_r,
                                         rotor_outer_r - mag_up_gap,
                                         _scale_mm,
                                         open_top=(float(mag_up_gap) <= 1e-9),
                                          open_wall=_extended_pocket(p))
            profile = [(float(x), float(y))
                       for x, y in list(_mp.exterior.coords)[:-1]]
        except Exception as e:                       # noqa: BLE001
            # The fillet is cosmetic for the 3-D view; the ARC is not.  Fall
            # back to the unfilleted arc, never to a chord — a straight top here
            # would show a magnet floating a sagitta below the sleeve, which is
            # the exact shape the user removed.
            print(f"Warning: magnet top corner fillet failed in 3-D ({e}) — "
                  f"extruding the arc profile with sharp corners")
        # The SAME weld the 2-D build applies (`_sanitize_ring`, diameter /
        # _WELD_DIV).  The shapely fillet leaves a float-noise duplicate where
        # the corner arc meets the top arc (measured 2026-09-13 on the Ø200 at
        # magnet 31 / fillet 4: two points 9e-15 mm apart at (-5.774, 62.330));
        # the mesher welds it, but handed raw to `polyline` it became a
        # zero-length edge and `close()` died in OCC with
        # "BRepAdaptor_Curve::No geometry" — the 3-D tab blank, the boot-time
        # warm-up "skipped", and nothing naming the magnet.  A profile the weld
        # cannot make a polygon of is refused BY NAME, never extruded.
        _welded = _sanitize_ring(profile, _scale_mm / _WELD_DIV,
                                 label="magnet (3-D profile)")
        if _welded is None:
            raise ValueError(
                "magnet: the 3-D profile is degenerate after welding — "
                f"{len(profile)} points from magnet_height={mag_h}, "
                f"magnet_fill_up={mag_fill_up}, magnet_fill_down={mag_fill_down}, "
                f"magnet_fill_radius={mag_fill_r}, magnet_down_height={mag_down_h}, "
                f"magnet_up_gap={mag_up_gap} leave no polygon to extrude")
        profile = [(float(x), float(y)) for x, y in _welded]

        for i in range(num_poles):
            angle = i * pole_angle

            # Create magnet at origin then rotate/translate
            magnet = (
                cq.Workplane("XY")
                .polyline(profile)
                .close()
                .extrude(width)
            )

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
        # The cut is the pocket OPENING above the magnet: from the OD down past
        # the magnet's top edge, no deeper.  It used to run the full magnet
        # height as a constant-width rectangle, and where the magnet narrows
        # toward the hub (magnet_fill_down < the opening's width in mm) its
        # bottom corners stuck out past the magnet's inner corners — two air
        # slivers beside the magnet's inner end (user 2026-09-05: "косяк внизу
        # магнитов").  Below the top edge the magnet polygon itself defines
        # the pocket.  Same rule in get_2d_mesh_data / get_2d_polygons.
        rect_depth = _pocket_cut_depth(p)

        rotor = (
            cq.Workplane("XY")
            .circle(rotor_outer_r)
            .circle(rotor_inner_r)
            .extrude(width)
        )

        # rotor_hole >= 1 — the WHOLE pocket, straight sides run to the OD (see
        # `_extended_pocket`).  Not an "opening" any more: the cut IS the magnet
        # outline with its side edges extended, so `build_all`'s later
        # `rotor.cut(magnet)` finds nothing left to remove and the 3-D solid is
        # the same shape the mesher solves.  User 2026-09-06: "нужно сделать
        # грань ротора прямой".
        if _extended_pocket(p):
            rotor_house_h = p['rotor_house_height']
            mag_down_h = p['magnet_down_height']
            mag_fill_down = p['magnet_fill_down']
            mag_up_gap = p['magnet_up_gap']
            magnet_r = rotor_inner_r + rotor_house_h
            a_dn = radians(pole_angle * mag_fill_down / 2)
            a_up = radians(pole_angle * mag_fill_up / 2)
            r_top = rotor_outer_r - mag_up_gap
            mag_local = [
                ( magnet_r * sin(a_dn),                 magnet_r * cos(a_dn)),
                ((magnet_r + mag_down_h) * sin(a_dn),  (magnet_r + mag_down_h) * cos(a_dn)),
                ( r_top * sin(a_up),                    r_top * cos(a_up)),
                (-r_top * sin(a_up),                    r_top * cos(a_up)),
                (-(magnet_r + mag_down_h) * sin(a_dn), (magnet_r + mag_down_h) * cos(a_dn)),
                (-magnet_r * sin(a_dn),                 magnet_r * cos(a_dn)),
            ]
            for i in range(num_poles):
                pts = _extended_pocket_pts(mag_local, rotor_outer_r,
                                           radians(i * pole_angle))
                cut_up = (
                    cq.Workplane("XY")
                    .polyline([(float(x), float(y)) for x, y in pts])
                    .close()
                    .extrude(width + 1)
                )
                rotor = rotor.cut(cut_up)
            return rotor

        for i in range(num_poles):
            angle = i * pole_angle
            if rect_depth <= 0.0:
                break            # nothing to open (see _pocket_cut_depth)
            # Create positive side slot
            cut_up = (
                cq.Workplane("XY")
                .rect(rec_w, -rect_depth, centered=(False, False))
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
        # Strips per wire row (wire_split): each wire row is drawn as N solids of
        # wire_w with 2·wire_spacing_x between them.
        n_split = _wire_split(p)
        wire_col_w = _strip_span(wire_w, n_split, wire_d_x)
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

                # One solid per STRIP on each side.  n_split = 1 gives back the
                # single full-width rectangle this loop always built.
                for _x0 in (right_x, -(right_x + wire_col_w)):
                    for sxs, sw in _strip_columns(_x0, wire_w, n_split, wire_d_x):
                        pts = [
                            (sxs,      current_y),
                            (sxs + sw, current_y),
                            (sxs + sw, current_y - wire_h),
                            (sxs,      current_y - wire_h),
                        ]
                        solid = (cq.Workplane("XY").polyline(pts).close()
                                 .extrude(stator_w))
                        wires.append(solid.rotate((0, 0, 0), (0, 0, 1), angle))
            
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
          • insulation   = a U-band of thickness insulation_thickness on the THREE
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
        # Strips per wire row (wire_split) — the enamel is the envelope MINUS the
        # copper, so the (N−1) gaps of 2·wire_spacing_x between a turn's strips
        # come out as enamel, which is exactly what they are (and what the
        # thermal domains 61-63 need them to be: coating, not slot air).
        n_split = _wire_split(p)
        wire_col_w = _strip_span(wire_w, n_split, wire_dx)
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
                for sx0 in (right_x, -(right_x + wire_col_w)):
                    lw = []
                    for s in range(n_fit):
                        cy = top_y_c - s * (wire_h + wire_dy)
                        for sxs, sw in _strip_columns(sx0, wire_w, n_split, wire_dx):
                            lw.append(SPoly([(sxs, cy), (sxs + sw, cy),
                                             (sxs + sw, cy - wire_h), (sxs, cy - wire_h)]))
                    copper = unary_union(lw)
                    y_top = top_y_c; y_bot = top_y_c - (n_fit - 1) * (wire_h + wire_dy) - wire_h
                    el = sx0 - wire_dx / 2; er = sx0 + wire_col_w + wire_dx / 2
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
        except ImportError as exc:
            print(f"[2d] missing dependency: {exc}")
            return {}
        try:
            import mapbox_earcut as earcut
        except ImportError as exc:
            # The native earcut can be BLOCKED by the OS (Windows Application
            # Control refused mapbox_earcut/_core.pyd on 2026-09-02) — that
            # used to return {} here and the whole 3-D view went blank with
            # no message.  A constrained Delaunay via `triangle` is the same
            # triangulation of the same rings; only the speed differs.
            from motor_ai_sim import earcut_fallback as earcut
            log.warning("2-D mesh: mapbox_earcut unavailable (%s) — using the "
                        "`triangle` fallback triangulator", exc)

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
        # Strips per wire — same split the FEM polygons are built with, so the
        # 3-D view shows the conductors the solver actually solved.
        n_split     = _wire_split(p)
        wire_col_w  = _strip_span(wire_w, n_split, wire_dx)
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
        # The magnet's top edge is the ARC on r = rotor_or − magnet_up_gap, and
        # nothing else (user 2026-09-06 — "уберём прямую вообще").
        mag_r_top = rotor_or - mag_up_gap

        # ── 1. SHAFT (hollow ring: shaft_inner_radius → rotor_inner_radius) ──
        shaft_outer_r = rotor_ir          # outer edge of shaft tube
        shaft_inner_r = shaft_r           # inner bore of shaft
        shaft_poly = SPoly(_circle(shaft_outer_r), [_circle(shaft_inner_r)])
        if not shaft_poly.is_valid:
            shaft_poly = shaft_poly.buffer(0)
        r = _tri(shaft_poly, z=Z_SHAFT)
        if r: result['shaft'] = r

        # ── 1b. RETAINING SLEEVE (ring on the rotor OD, inside the air gap) ──
        # Same Z plane as the rotor: it is bonded to the rotor and the extruded
        # viewer (get_extruded_mesh_data) then gives it the SAME stack length as
        # every other part, which is what it physically has.
        sleeve_t  = _sleeve_thickness(p)
        sleeve_or = rotor_or + sleeve_t
        if sleeve_t > 0.0:
            sleeve_poly = SPoly(_circle(sleeve_or), [_circle(rotor_or)])
            if not sleeve_poly.is_valid:
                sleeve_poly = sleeve_poly.buffer(0)
            r = _tri(sleeve_poly, z=Z_ROTOR)
            if r: result['sleeve'] = r

        # ── 2+3. MAGNETS + ROTOR CORE ─────────────────────────────────────
        # Round ONLY the two top corners (mp3/mp4, outer/air-gap edge), the ends
        # of the top arc.  The same rounded polygon is used for BOTH the rotor
        # pocket holes and the magnet so there is no dark gap at the corners.

        def _build_mag_poly(pts, fillet_r):
            """The magnet outline with its two TOP corners rounded.

            The outline is not a hexagon (the top edge carries the OD ring's own
            stations), so the rounding goes through the shared boundary-walking
            core, which finds the corners by radius instead of by index."""
            return _fillet_magnet_top_arc(SPoly(pts), fillet_r,
                                          mag_r_top, scale_mm,
                                          open_top=(float(mag_up_gap) <= 1e-9),
                                          open_wall=_extended_pocket(p))

        # rotor_hole >= 1: no rectangle at all — the pocket is the magnet
        # outline with its side edges extended straight to the OD (see
        # `_extended_pocket`, user 2026-09-06).  Below, only for rotor_hole < 1:
        # the rectangular cut above each magnet — matches 3D _create_rotor:
        #   rect(rec_w, -mag_h).translate((-rec_w/2, rotor_outer_r)).rotate(angle)
        _ext_pocket = _extended_pocket(p)
        mag_angle_up_hole = pole_angle_r * mag_fu * magnet_hole / 2  # radians
        rec_w = 2 * rotor_or * sin(mag_angle_up_hole)
        _rd = _pocket_cut_depth(p)          # see _create_rotor: opening only
        rect_local = [
            (-rec_w / 2, rotor_or),
            ( rec_w / 2, rotor_or),
            ( rec_w / 2, rotor_or - _rd),
            (-rec_w / 2, rotor_or - _rd),
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
            # The arc stations are GLOBAL (they have to coincide with the
            # OD/sleeve-bore 256-gon), so they are spliced in after the
            # rotation, between mp3 (index 2) and mp4 (index 3).
            pts = (pts[:3]
                   + _magnet_top_arc_global(mag_r_top, angle_up_m, a)
                   + pts[3:])
            mp  = _build_mag_poly(pts, mag_fill_r)
            if not mp.is_valid:
                mp = mp.buffer(0)

            if _ext_pocket:
                hole = _extended_pocket_poly(SPoly, mag_local, rotor_or, a, mp,
                                             node_tol=scale_mm * 1e-9)
            elif _rd <= 0.0:
                hole = mp        # no opening cut (see _pocket_cut_depth)
            else:
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
        if _extended_pocket(p):
            # Share the pocket OD corners with the rim (see
            # _od_ring_with_pocket_corners): no needle at the corners.
            rotor_disk = SPoly(_od_ring_with_pocket_corners(rotor_or, hole_polys),
                               [rotor_inner_pts])
            if not rotor_disk.is_valid:
                rotor_disk = rotor_disk.buffer(0)
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
                # Straight-sided pockets (rotor_hole ≥ 1): the only sharp
                # corners near the OD ARE the pole tips, so the band is fixed —
                # the up_gap-scaled band was 0 at up_gap 0 and the tips stayed
                # sharp (user 2026-09-06: "ты забыл применить это на углы ротора").
                _band = (1.5 if _extended_pocket(self.parameters)
                         else min(1.5, 0.6 * float(self.parameters.get('magnet_up_gap', 1.5) or 1.5)))
                _f = _round_corners_vertex(rotor_poly, _rfr, surface_band=_band,
                                           scale_mm=scale_mm)
                if not _f.is_valid: _f = _f.buffer(0)
                if (_f.is_valid and not _f.is_empty
                        and _f.area >= 0.85 * rotor_poly.area
                        and _npoly(_f) == _npoly(rotor_poly)):
                    # Void-aware fillet: re-cut the pockets so the corner arcs
                    # never grow iron into a magnet (same fix as
                    # get_2d_polygons; incident 2026-08-24).
                    _f = _f.difference(unary_union(hole_polys))
                    if not _f.is_valid: _f = _f.buffer(0)
                    if not _f.is_empty and _npoly(_f) == _npoly(rotor_poly):
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
        # `wire_col_w` (see get_2d_polygons): N strips plus their (N−1) gaps of
        # 2·wire_spacing_x widen the column, and the slot widens with it.
        cut_x  = tooth_w/2 + ins_w*2 + wire_col_w + wire_dx*2 + tooth2_w
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

            # slot rectangles — sized on the wire COLUMN (wire_split widens it)
            slot_w  = wire_col_w + ins_w*2 + wire_dx
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

        # One rectangle per CONDUCTOR: with wire_split = N each wire row is
        # N strips of wire_w side by side, so the view shows what the mesher
        # was given (they are all merged into the slot's `coil_i` part, exactly
        # as the wires of a stack always were).
        _strip_xy = [(sxs, sw)
                     for _sd, sx in ((1, right_x), (-1, -(right_x + wire_col_w)))
                     for sxs, sw in _strip_columns(sx, wire_w, n_split, wire_dx)]
        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            for step in range(num_wires):
                cy = top_y_c - step * (wire_h + wire_dy)
                for sxs, sw in _strip_xy:
                    pts_local = [(sxs, cy), (sxs + sw, cy),
                                 (sxs + sw, cy - wire_h), (sxs, cy - wire_h)]
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

        # ── 5b. SLOT INSULATION (wire enamel + insulation) ────────────────
        # Shared geometry with get_2d_polygons (single source). Each merged into
        # ONE part so the 3D tree shows a single "Wire enamel" / "Insulation" item.
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
        # Slip surface = the middle of the MECHANICAL gap (sleeve OD .. bore);
        # sleeve_or == rotor_or on a machine with no sleeve.  Same expression
        # get_2d_polygons uses, so the two views cannot disagree.
        mid_r   = 0.5 * (sleeve_or + inner_r)
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
            _rot_solids = [shaft_solid, rotor_poly] + list(mag_rot_polys)
            if sleeve_t > 0.0:
                # The sleeve rotates with the rotor, so it is a rotor solid the
                # inner band has to be cut around — otherwise the air region
                # would be drawn straight through the ring.
                _sl = SPoly(_circle(sleeve_or), [_circle(rotor_or)])
                _rot_solids.append(_sl if _sl.is_valid else _sl.buffer(0))
            try:
                in_band_poly = in_band_poly.difference(unary_union(_rot_solids))
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

        # ── Retaining SLEEVE (carbon fibre, hoop-wound, ON the rotor OD) ─────
        # A ring in the air gap that turns WITH the rotor.  0 (the default and
        # what every machine but the Ø200 carries) must change nothing at all:
        # `sleeve_or` collapses onto `rotor_or`, the sleeve polygon is None, and
        # every expression below is the one that was there before.
        sleeve_t  = _sleeve_thickness(p)
        sleeve_or = rotor_or + sleeve_t

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
        # ── Horizontal split: each turn is N strips of wire_w side by side ────
        # The column they sit in is N·wire_w + (N−1)·2·wire_spacing_x wide, so
        # `wire_col_w` — not `wire_w` — is what the slot cutter, the −x column
        # origin and the enamel envelope must be built on.  N = 1 makes the two
        # identical and every expression below is the one that was always there.
        n_split     = _wire_split(p)
        wire_col_w  = _strip_span(wire_w, n_split, wire_dx)
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
        # The magnet's top edge is the ARC on r = rotor_or − magnet_up_gap and
        # nothing else (user 2026-09-06 — "уберём прямую вообще").  This is THE
        # build the mesher and the validator read, so it is the one that decides
        # whether the magnet actually touches the sleeve bore.
        mag_r_top = rotor_or - mag_up_gap

        def _build_mag_poly(pts, fr):
            # Not a hexagon (the top edge carries the OD ring's own stations) —
            # round the two top corners through the shared boundary-walking
            # core, which finds them by radius instead of by index.  The core
            # clips to the raw outline, so a fillet can still only REMOVE
            # material (a near-straight top corner used to make the arc sweep
            # OUTSIDE the outline and the magnet bulged into the rotor iron —
            # 0.048 mm² magnet∩iron, static-3D validator, 2026-08-24).
            return _fillet_magnet_top_arc(SPoly(pts), fr, mag_r_top, scale_mm,
                                          open_top=(float(mag_up_gap) <= 1e-9),
                                          open_wall=_extended_pocket(p))

        # rotor_hole >= 1: no rectangle at all — the pocket is the magnet
        # outline with its side edges extended straight to the OD (see
        # `_extended_pocket`, user 2026-09-06).  Rectangular cut above each
        # magnet, rotor_hole < 1 only:
        _ext_pocket = _extended_pocket(p)
        mag_angle_up_hole = pole_angle_r * mag_fu * magnet_hole / 2
        rec_w = 2 * rotor_or * sin(mag_angle_up_hole)
        _rd = _pocket_cut_depth(p)          # see _create_rotor: opening only
        rect_local = [(-rec_w/2, rotor_or), (rec_w/2, rotor_or),
                      (rec_w/2, rotor_or-_rd), (-rec_w/2, rotor_or-_rd)]

        mag_polys = []   # (poly, polarity)
        hole_polys = []  # list of shapely (magnet + cut_up) polygons

        for i in range(num_poles):
            a   = i * pole_angle_r + theta_r
            pts = [_rot(x, y, a) for x, y in mag_local]
            # The arc stations are GLOBAL (they have to coincide with the
            # OD/sleeve-bore 256-gon at THIS rotor angle), so they are spliced
            # in after the rotation, between mp3 and mp4.
            pts = (pts[:3]
                   + _magnet_top_arc_global(mag_r_top, angle_up_m, a)
                   + pts[3:])
            mp  = _build_mag_poly(pts, mag_fill_r)
            if not mp.is_valid: mp = mp.buffer(0)

            if _ext_pocket:
                hole = _extended_pocket_poly(SPoly, mag_local, rotor_or, a, mp,
                                             node_tol=scale_mm * 1e-9)
            elif _rd <= 0.0:
                hole = mp        # no opening cut (see _pocket_cut_depth)
            else:
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
        # Straight-sided pockets: the OD corners must be vertices of the rim
        # too, or the difference leaves a zero-width needle (see helper).
        _od_ring = (_od_ring_with_pocket_corners(rotor_or, hole_polys)
                    if _ext_pocket else _circle(rotor_or))
        rotor_disk = SPoly(_od_ring, [_circle(rotor_ir)])
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
                # Straight-sided pockets: fixed band — the up_gap-scaled band
                # was 0 at up_gap 0 and left the tips sharp (user 2026-09-06:
                # "ты забыл применить это на углы ротора").
                _band = (1.5 if _ext_pocket
                         else min(1.5, 0.6 * float(p.get('magnet_up_gap', 1.5) or 1.5)))
                _f = _round_corners_vertex(rotor_poly, _rfr, surface_band=_band,
                                           scale_mm=scale_mm)
                if not _f.is_valid:
                    _f = _f.buffer(0)
                if (_f.is_valid and not _f.is_empty
                        and _f.area >= 0.85 * rotor_poly.area
                        and _npoly(_f) == _npoly(rotor_poly)):
                    # The fillet arcs ADD iron in pocket corners, and iron must
                    # never grow into a magnet: at magnet_fill_up ≈ 0.32 the
                    # magnet's slanted side ran exactly through a corner arc and
                    # the cross-section stopped being buildable (0.048 mm² of
                    # magnet∩iron, found by the static-3D validator 2026-08-24).
                    # Re-cut the pockets so the fillet is void-aware by
                    # construction.
                    _f = _f.difference(unary_union(hole_polys))
                    if not _f.is_valid:
                        _f = _f.buffer(0)
                    # NOT clipped back to `rotor_disk` here, though it is
                    # tempting: the fillet's tangent points sit at an arc
                    # distance along the boundary, so on the OD — a 256-gon, not
                    # a straight edge — the chord to the tangent point dips
                    # inside the rim and the arc tangent to THAT chord pokes back
                    # out through it by up to 2.8e-4 mm, which is 2.7e-3 mm² of
                    # rotor iron standing inside the retaining sleeve's bore
                    # (measured 2026-09-06 on the Ø200, only ever with
                    # rotor_fill_r > 0).  Clipping does remove it, but it also
                    # trims the 2e-4 mm² needles this difference already strands
                    # at magnet_up_gap 0 into 1e-4 mm² ones that then survive the
                    # weld instead of being dropped as degenerate — a needle in
                    # the mesh is worse than the overlap.  The real fix is in
                    # `_fillet_ring_corners`, which must not place an arc outside
                    # the boundary it is rounding; left for that.
                    if not _f.is_empty and _npoly(_f) == _npoly(rotor_poly):
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

        # Retaining sleeve — a plain ring rotor_or .. rotor_or + sleeve_thickness.
        # It belongs to the ROTOR half of the sliding band (it is bonded to the
        # rotor and turns with it), so everything below that unions "the rotor
        # solids" includes it.
        sleeve_poly = None
        if sleeve_t > 0.0:
            sleeve_poly = SPoly(_circle(sleeve_or), [_circle(rotor_or)])
            if not sleeve_poly.is_valid:
                sleeve_poly = sleeve_poly.buffer(0)

        # Air gap ring — the MECHANICAL gap, i.e. what is left outside the
        # sleeve.  With no sleeve sleeve_or == rotor_or and this is the ring it
        # has always been.
        airgap_poly = SPoly(_circle(inner_r), [_circle(sleeve_or)])
        if not airgap_poly.is_valid: airgap_poly = airgap_poly.buffer(0)

        # ── Stator with real slot cutouts ────────────────────────────────────
        half_slots     = num_slots // 2
        slot_angle_deg = 360.0 / half_slots
        # `wire_col_w`, not `wire_w`: with wire_split = N the column is N strips
        # plus (N−1) gaps of 2·wire_spacing_x wide, and a slot cut on ONE
        # strip's width would run the outermost strip into the tooth.
        cut_x  = tooth_w/2 + ins_w*2 + wire_col_w + wire_dx*2 + p.get('tooth2_width', 4.5)
        # The slot's outer wall has to stay INSIDE the half-sector wedge at the
        # slot-bottom radius, or two neighbouring cutters meet and the tooth
        # between them is gone (fill_r2 turns negative and the stator polygon
        # stops describing a machine).  Measured here and carried out in the
        # polys dict so the validator can name WHICH knob overflowed — with a
        # split it is almost always `wire_split`, whose extra strips and their
        # gaps are the only thing that just moved.
        self._slot_cut_x_mm     = float(cut_x)
        self._slot_cut_x_max_mm = float((inner_r + cut_w)
                                        * sin(radians(slot_angle_deg/2)))
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

            # the wire COLUMN, so a split's extra strips and their (N−1) gaps of
            # 2·wire_spacing_x get real pocket to sit in
            slot_w_c = wire_col_w + ins_w*2 + wire_dx
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
        # PER-CONDUCTOR polygons: every single conductor is emitted as its OWN
        # polygon (NOT unioned into a per-slot bar).  The winding is N turns in
        # series, so each conductor is an independent solid body carrying the
        # branch current — this is what lets the eddy-current solver compute the
        # real per-conductor skin + proximity loss instead of one shorted slot
        # bar.  With `wire_split` = S each wire ROW IS S conductors: S strips of
        # wire_w side by side with 2·wire_spacing_x of enamel between them, so
        # the slot holds num_wires·S conductors and the row gets S polygons
        # where it had one.  Those S strips are consecutive SERIES turns (see
        # winding.turns_per_coil), which changes no polygon here — a strip is a
        # body either way.
        # `coil_polys` order = slot-by-slot, +x side then −x side, wire-by-wire,
        # strip-by-strip; each entry's centroid still lands in its slot so the
        # (phase, direction) lookup downstream is unchanged.
        coil_polys = []
        for i in range(half_slots):
            a = i * radians(slot_angle_deg)
            for sx0 in (right_x, -(right_x + wire_col_w)):  # +x side, then −x side
                for step in range(n_fit):
                    cy = top_y_c - step*(wire_h + wire_dy)
                    for sxs, sw in _strip_columns(sx0, wire_w, n_split, wire_dx):
                        local = [(sxs, cy), (sxs + sw, cy),
                                 (sxs + sw, cy - wire_h), (sxs, cy - wire_h)]
                        wp = SPoly([_rot(*pt, a) for pt in local])
                        if wp.is_valid and wp.area > 0:
                            coil_polys.append(wp)          # ONE polygon per strip

        # ── Slot insulation objects (thermal + mass/cost; EM-inert) ───────────
        # wire enamel (polyimide) + insulation (Nomex/ceramic), via the SHARED
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
        # The shared circle r=mid_r is the slip surface.  It is the middle of
        # the MECHANICAL gap — between the outermost ROTATING surface (the
        # sleeve OD when there is a sleeve, the rotor OD otherwise) and the
        # stator bore.  Anchoring it on rotor_or instead would put the slip
        # surface INSIDE the sleeve on any ring thicker than half the air gap,
        # and the sliding band would shear the ring every step.
        mid_r   = 0.5 * (sleeve_or + inner_r)
        # Outer boundary for the FE domain — pulled in from motor_config
        # if available; defaults to 1.3× the stator OD.
        r_outer = float(p.get('outer_air_factor', 1.3)) * outer_r

        in_band_poly  = SPoly(_circle(mid_r))                # full disk to mid_r
        out_band_poly = SPoly(_circle(r_outer), [_circle(mid_r)])  # annulus mid_r..r_outer
        if not in_band_poly.is_valid:  in_band_poly  = in_band_poly.buffer(0)
        if not out_band_poly.is_valid: out_band_poly = out_band_poly.buffer(0)

        # ── Weld the whole rotor SIDE as ONE set ─────────────────────────────
        # The rotor, the magnets, the shaft, the sleeve and the air that fills
        # what is left of the disk all share boundaries, and sanitising each of
        # them on its own is what buried magnets in iron and left 0.43 mm² of
        # the rotor annulus owned by nobody (measured 2026-09-06 — the full
        # sweep is in `_weld_group_geoms`).  ONE call, so every ring is
        # sanitised exactly once and from its ORIGINAL points: re-sanitising an
        # already-sanitised rotor is NOT safe, because the first pass drops the
        # magnet nodes `_side_nodes_from_magnet` splices onto the pocket wall
        # (with no weld they are exactly collinear), and on the stripped ring a
        # later 0.045 mm move of the OD corner rotates the whole 25 mm iron face
        # and sweeps 0.47 mm² of magnet into the iron — measured on this fixture
        # at gap 0.5 / rotor_fill_r 0.2 / 7.5°, which is how this is known.
        #
        # in_band is the DERIVED member: each round cuts it fresh out of that
        # round's welded solids, so it never carries a boundary they have since
        # left behind, and its own welds go into the same map — where it has to
        # weld a pocket corner away, the iron lip that shares that corner moves
        # with it and takes the area, instead of the area becoming a hole.
        _solid_items = ([('rotor', rotor_poly), ('shaft', shaft_poly),
                         ('sleeve', sleeve_poly)]
                        + [(f'magnet[{i}]', mp) for i, (mp, _p) in enumerate(mag_polys)])
        _in_band_disk = in_band_poly

        def _derive_in_band(welded):
            band = _in_band_disk
            try:
                solids = [g for g in welded if g is not None]
                band = band.difference(unary_union(solids))
                if not band.is_valid:
                    band = band.buffer(0)
            except Exception:
                pass
            return [('in_band', band)]

        # The map itself is dropped on purpose — see the sanitize call at the
        # end of this method for why it is not replayed on the other domains.
        _welded, _derived, _ = _weld_group_geoms(
            _solid_items, scale_mm, derive=_derive_in_band)
        rotor_poly, shaft_poly, sleeve_poly = _welded[0], _welded[1], _welded[2]
        mag_polys = [(w, pol) for w, (_mp, pol) in zip(_welded[3:], mag_polys)]
        in_band_poly = _derived[0]

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
            # Retaining sleeve — None when sleeve_thickness = 0, so every
            # consumer's `polys.get("sleeve")` is falsy on a machine without one
            # and no code path has to know the knob exists.
            'sleeve':   sleeve_poly,      # Shapely Polygon (ring) in mm, or None
            'sleeve_r_mm': ([rotor_or, sleeve_or] if sleeve_poly is not None
                            else None),   # [ID, OD] of the ring, mm
            'air_gap':  airgap_poly,      # Shapely Polygon in mm — kept for back-compat
            'in_band':  in_band_poly,     # Air disk r=0..mid_r minus rotor bodies (rotates)
            'out_band': out_band_poly,    # Air annulus mid_r..r_outer minus stator (stationary)
            'mid_r_mm': mid_r,            # slip-surface radius (mm)
            'r_outer_boundary_mm': r_outer,  # outer Dirichlet BC radius (mm)
            'coils':    coil_polys,       # list of Shapely Polygon in mm
            'wire_insulation': wire_ins_polys,  # list[Polygon] — wire enamel (polyimide); thermal+display (cost in wire)
            'slot_insulation': slot_ins_polys,  # list[Polygon] — insulation (Nomex/ceramic); thermal+cost+display
            # Winding fit: True if the requested num_wires_per_slot did NOT fit in
            # the slot (stack clamped to n_wires_fit so coils stay out of the gap).
            'coils_overflow': bool(getattr(self, '_coils_overflow', False)),
            'n_wires_fit':    int(getattr(self, '_n_wires_fit', num_wires)),
            'n_wires_requested': int(num_wires),
            # ── Horizontal split ────────────────────────────────────────────
            # How many strips each turn is, the width one strip was drawn at
            # (= wire_width — the split does NOT subdivide it), and the column
            # they occupy.  The validator needs all three: the nominal conductor
            # section is strip_width × wire_height, and "does the winding still
            # fit across the slot" is a question about the COLUMN.
            'wire_split':      int(n_split),
            'strip_width_mm':  float(wire_w),
            'wire_column_mm':  float(wire_col_w),
            'slot_cut_x_mm':     float(getattr(self, '_slot_cut_x_mm', 0.0)),
            'slot_cut_x_max_mm': float(getattr(self, '_slot_cut_x_max_mm', 0.0)),
        }

        # ── Final ring sanitize — NOTHING defective leaves this builder ───────
        # Shapely's boolean noding emits exact duplicate points at slot-mouth
        # corners and can leave a 3-coincident-point sliver "polygon" behind the
        # rotor difference.  Both are junk the mesher must never see: gmsh
        # honours every boundary point, so a zero-length edge becomes a fan of
        # microscopic triangles.  Every drop is logged with coordinates.
        #
        # The rotor-side domains came through `_weld_group_geoms` above and are
        # NOT re-run here (see `_sanitize_polys_dict`).  The group's weld map is
        # deliberately NOT replayed on the rest: `air_gap`'s bore is the SAME
        # 256-gon as the rotor OD, but it is the ring on the far side of the
        # sliding surface and is not built from the rotor — replaying a rotor
        # weld on it dragged its bore inward onto the iron (7.4e-5 mm² of
        # air_gap∩rotor on the 30 mm regression fixture, measured 2026-09-06)
        # and moved a fixture that had nothing wrong with it.
        return _sanitize_polys_dict(
            out, scale_mm,
            done=('rotor', 'shaft', 'sleeve', 'in_band', 'magnets'))

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
        # Constructing the cache must never be the reason a route 500s: the
        # cache is an OPTIMISATION.  An unwritable parent (container: /app is
        # root-owned, the process is uid 10001) leaves it simply unusable —
        # `exists()` stays False and every read misses.
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        
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
        """Clear all cached geometry — the CONTENTS, never the directory itself.

        Removing and re-creating ``cache_dir`` needs write permission on its
        PARENT, which the container does not have: the API runs as uid 10001
        with WORKDIR /app, /app is root-owned and only /app/cadquery_cache is
        chowned to the service account (deploy/Dockerfile.api).  ``rmtree`` of
        the directory therefore raised

            PermissionError: [Errno 13] Permission denied: 'cadquery_cache'

        out of every ``PUT /api/geometry`` on production (2026-09-16) — the
        route calls this before it writes, so no geometry edit could be saved
        at all.  Deleting only the CHILDREN needs write permission on the cache
        directory itself, which is exactly what we are given.

        A missing directory is not an error: nothing is cached, so the cache is
        already clear.  It is re-created so the next ``save`` has somewhere to
        go, and even that is tolerated if the parent refuses.
        """
        import shutil
        if not self.cache_dir.exists():
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass        # unwritable parent — nothing was cached anyway
            return
        for child in self.cache_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    
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
