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
import os as _os_gm
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

# Per-part element size (UI "Per-part element size (mm)") keys this mesher can
# honour.  Solid parts become their own CDT region seed with their own target
# cell area; "outer"/"air" is the coarse air size (air_mesh_mm).  A key OUTSIDE
# this set cannot be applied on the geometry-driven path and MUST NOT be
# silently dropped — the caller raises and falls back to the gmsh build, which
# honours every component key through its per-surface size fields.
#
# "shaft" is NOT here on purpose: the rotor cell's shaft core is a pie slice
# bounded by the two radial cut chains, and those chains are frozen by Triangle's
# -Y flag (clone-identical seams for the anti-periodic weld), so its area
# constraint cannot be met and is silently dropped by Triangle (measured: a
# 0.0043 mm^2 shaft target gives the SAME 374-tri cell as 3.9 mm^2 under
# `pq20AaS6000Y`, and 1647 tris without the Y).  Same for "airgap" — the gap is
# owned by the structured belt / harmonic macro, not by this CDT.  Both are
# routed to the gmsh mesher instead, which does honour them.
# "coil_rel" is NOT a size — see COIL_REL_KEY below.  It travels in the same
# component_mesh dict (one persisted UI block, one cache key) and is listed here
# so _check_part_mesh_supported does not route the build to gmsh over it.
GEO_PART_KEYS = frozenset(("stator", "rotor", "magnet", "coil", "outer", "air",
                           "coil_rel"))
# solid-part keys that map onto a CDT region seed (air is handled by air_mm).
# coil_rel is deliberately absent: _part_areas turns these into mm^2 targets and
# a FACTOR must never be squared into an area.
GEO_REGION_KEYS = ("stator", "rotor", "magnet", "coil")

# ── "Wire cell" — the copper cell size as a FACTOR of the wire's own height ──
# UI: ½h / 1h / 2h, h = the wire's short side.  A factor, not a mm size, because
# the mm value is tied to the wire it was chosen for: a 0.6 mm request saved
# against a 0.6 mm wire silently becomes a 2h cell after the user halves
# wire_height, whereas `coil_rel` re-reads h at build time and stays ½h/1h/2h.
#
# Only the three UI values carry meaning, so anything else is SNAPPED to the
# nearest of them rather than rejected: the key is a discretisation preference,
# not physics (copper cell size 0.2-0.6 mm moves torque by <0.01 %), and a duty
# file written by an older/hand-edited client must still open instead of 400ing
# a whole simulation over a mesh cosmetic.  An explicit `coil` size in mm WINS —
# a user who typed an absolute size asked for that size.
COIL_REL_KEY = "coil_rel"
COIL_REL_CHOICES = (0.5, 1.0, 2.0)


def snap_coil_rel(v) -> float:
    """The requested wire-cell factor snapped to the nearest allowed choice, or
    0.0 when it is absent / unparseable / non-positive (→ the 1h default path,
    which is bit-identical to no key at all)."""
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return 0.0
    if not (fv > 0.0) or not math.isfinite(fv):
        return 0.0
    return min(COIL_REL_CHOICES, key=lambda c: abs(math.log(fv / c)))


def _coil_rel_of(part_mesh_mm: Optional[Dict]) -> float:
    """The wire-cell factor carried alongside the per-part element SIZES."""
    if not part_mesh_mm:
        return 0.0
    return snap_coil_rel((part_mesh_mm or {}).get(COIL_REL_KEY))


# ── optimizer mesh budget (process-scoped, OFF by default) ───────────────────
# The optimizer explores the whitelist without a range box, so a candidate can
# carry a derived thin feature (a slot opening squeezed to um, a tooth pared to
# a knife edge) that is perfectly buildable yet makes the STATOR cell's q20
# refinement cascade — the rotor cell already caps itself (_ROTOR_STEINER_CAP),
# the stator cell had no cap and was measured at 0.5-1.1 M tris on such input.
# Those cascades are Triangle refinement ARTIFACTS: no area/feature-width
# arithmetic on the polygons predicts them (a knife edge contributes O(1) tris
# per size scale analytically and 10^6 in practice), so the only faithful
# pre-solve predictor is Triangle itself with a Steiner cap — capped, it
# terminates promptly instead of cascading.
#
# `set_tri_budget` arms that cap for THIS PROCESS.  It is called only by the
# optimizer's one-candidate eval subprocess (optimization/refine_proc), where
# process scope == candidate scope; the API server never arms it, so
# interactive meshing (Simulation / Mesh tabs) is bit-identical to before.
# While armed, a mesh that stays under the budget is ALSO bit-identical to the
# unarmed build — Triangle only inserts the Steiner points refinement asks for,
# so a cap that is not reached changes nothing.  Hitting the cap raises
# MeshBudgetExceeded, whose message starts with "mesh budget:" — the string the
# optimizer's reject classifier keys on.
_TRI_BUDGET: Dict[str, Optional[int]] = {"v": None}


class MeshBudgetExceeded(RuntimeError):
    """The armed triangle budget was hit — the candidate meshes pathologically.

    Raised instead of letting the refinement cascade: an eval this size could
    not have finished inside the optimizer's subprocess timeout anyway, so the
    candidate is rejected in seconds, counted, and named — not silently burned
    as a 300 s timeout."""


def set_tri_budget(n: Optional[int]) -> None:
    """Arm (int) or disarm (None/0) the process-scoped triangle budget."""
    _TRI_BUDGET["v"] = int(n) if n else None


def tri_budget() -> Optional[int]:
    return _TRI_BUDGET["v"]


# A Triangle run that the -S cap actually TRUNCATED has spent most of that cap.
# Measured on the cascading 10x10 square (the case the fence was written for):
# 377/500, 847/1000, 2989/5000, 4551/7921 points added — i.e. 57-85 % of the
# cap, never a sliver of it.  0.25 sits far below that floor and far above the
# consumption of a run that simply finished (measured on the 40 mm 24s/28p
# stator cell: 1580 points added against a 200 000 cap = 0.8 %).
_CAP_BINDING_FRAC = 0.25


def _steiner_cap_truncated(A: Dict, out: Dict, area: float,
                           cap: Optional[int] = None) -> bool:
    """Did a Steiner-CAPPED Triangle run stop BEFORE meeting its constraints?

    TWO conditions must hold, because either one alone gives a false verdict.

    1. The cap must be able to have BOUND at all.  Triangle spends its -S budget
       on REJECTED insertions too (a circumcentre that encroaches on a segment
       is discarded and the segment split instead), so a truncated run holds
       fewer added points than the cap — but it still holds MOST of it (see
       `_CAP_BINDING_FRAC`).  A run that added 0.8 % of its allowance stopped
       because its own constraints were met, not because it ran out.

    2. The mesh must miss its own max-area constraint GROSSLY.  A finished run
       is expected to satisfy the area target it was meshed under — the
       per-region column 4 when the caller passed `regions` (`Aa`), else the
       global `area` (`a<area>`); a truncated one leaves triangles far above it
       (the cascading square: max element area 25 mm^2 against a 0.01 mm^2
       target).  Triangles in no declared region, or in a region asking for
       area <= 0, are unconstrained and are skipped.

    Condition 2 was the whole test until it was measured against a CONVERGED
    mesh: with -Y (no Steiner points on the input boundary) Triangle cannot
    always split the last triangle down to its region target, and it legitimately
    finishes leaving one 1.2x over.  That is a miss by PERCENT; truncation misses
    by orders of magnitude, and only after eating the cap.  Requiring both
    conditions keeps the fence firing on real cascades and stops it rejecting
    every healthy candidate.
    """
    T = np.asarray(out.get("triangles"), np.int64)
    if T.size == 0:
        return False
    if cap:
        # Points Triangle ADDED to the input PSLG.  Its output always begins
        # with the input vertices, so the difference is exactly the insertions.
        _added = len(np.asarray(out.get("vertices"), float)) - \
            len(np.asarray(A.get("vertices"), float))
        if _added < _CAP_BINDING_FRAC * float(cap):
            return False                      # the cap never bound → it finished
    V = np.asarray(out.get("vertices"), float)
    p = V[T]
    a = 0.5 * np.abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                     - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
    regions = A.get("regions")
    if regions is None or not len(regions):
        lim = np.full(len(T), float(area))
    else:
        attrs = out.get("triangle_attributes")
        if attrs is None:
            return False                      # cannot tell → never false-reject
        marker = np.asarray(attrs, float)[:, 0]
        # marker -> its target area; a marker seeded twice with different areas
        # takes the LOOSEST, so an ambiguity can only ever spare a candidate.
        tgt: Dict[float, float] = {}
        for row in np.asarray(regions, float):
            m, ta = float(row[2]), float(row[3])
            tgt[m] = max(tgt.get(m, 0.0), ta)
        lim = np.array([tgt.get(float(m), 0.0) for m in marker], float)
        free = lim <= 0.0                     # unconstrained region
        lim[free] = np.inf
    # 2 % slack absorbs the float arithmetic; a truncated mesh misses by orders
    # of magnitude, never by percent.
    return bool(np.any(a > lim * 1.02))


def _cell_area(edge_mm: float) -> float:
    """Target CDT cell area (mm^2) for a requested triangle EDGE length (mm):
    the equilateral relation area = 0.433*L^2.

    NO 0.12 mm^2 floor here — that floor guards the GLOBAL size (it is applied
    to a slider the user drags, and 0.12 mm^2 ~ 0.53 mm edge everywhere is
    already a huge mesh).  A PER-PART size is an explicit, local request
    ("mesh the magnets at 0.15 mm"): clamping it to the global floor silently
    substituted a coarser size — 0.3 mm and 0.15 mm both collapsed to 0.12 mm^2
    and produced the SAME mesh, which is exactly the no-op the knob was
    reported for."""
    return max(1e-6, 0.4330 * float(edge_mm) ** 2)


def _seeds(air: List, solid: List) -> List:
    """The region-seed list, in the ONE order every mesher here must use.

    Triangle floods each region seed outward to the enclosing segment loop.  When
    TWO seeds land in the same flood region the LAST one in the list wins.  The
    air seeds come from `_air_parts` = annulus - raw steel - magnets, computed on
    the RAW CadQuery polygons, while the PSLG is built from the resampled +
    defeatured iron chain — so along the shaft/OD circles that difference leaves
    hairline "air" slivers whose representative points actually sit INSIDE the
    meshed iron.  With the iron seed first, such a stray air seed silently
    replaced the iron's target area with the coarse AIR area: measured on the
    30 mm rotor, seed #0 (steel) had literally NO effect on the mesh — 190 tris
    whether its area was 0.62 or 0.039 mm^2 — which is why a per-part "rotor"
    element size looked like a no-op.

    Listing air/shaft FIRST and the solid parts (iron, coils, magnets) LAST makes
    the solid part win its own region.  The DEFAULT mesh is unchanged (the solid
    target area is not binding there — the quality refinement is already finer),
    verified by an identical node-coordinate hash.
    """
    return list(air) + list(solid)


def _part_areas(part_mesh_mm: Optional[Dict]) -> Dict[str, float]:
    """{part: target cell area mm^2} for the per-part element sizes actually
    requested.  Parts absent here keep the global/air size, so an empty request
    reproduces the previous mesh bit-for-bit.

    Only GEO_REGION_KEYS are converted — COIL_REL_KEY shares the dict but is a
    dimensionless factor, so it is filtered out here and read separately by
    _coil_rel_of().  (Squaring 2.0 into a 1.73 mm^2 coil target would silently
    request a mm size nobody asked for.)"""
    out: Dict[str, float] = {}
    for k, v in (part_mesh_mm or {}).items():
        kk = str(k).lower()
        if kk not in GEO_REGION_KEYS:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0.0:
            out[kk] = _cell_area(fv)
    return out


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


# ── structured (template) mesh in the copper ─────────────────────────────────
# The winding is N COPIES OF ONE RECTANGLE (7 x 0.6 mm strands, 0.13 mm apart).
# Fed to the CDT as bare outlines they were filled independently, so nominally
# identical wires got different, ragged triangulations (CILN28: coil edge p10
# 0.29 / median 0.42 / p90 0.59 mm) — the per-wire eddy solve then sees N
# slightly different discrete conductors where the machine has N identical ones.
# So: mesh ONE wire like a template and stamp it into all of them — boundary
# densified at pitch s + a regular interior lattice at pitch s, both built in
# the wire's own frame, so a rotated slot gets the same stamp.  Default pitch is
# the WIRE HEIGHT (the short side): square-ish cells one wire high, the coarsest
# grid that still resolves the strand.  SB_NO_WIRE_GRID=1 restores the old fill.
#
# THREE TIERS (SB_NO_WIRE_GRID=1 → off; SB_WIRE_PATCH=0 → the lattice HINT above;
# default → the structured PATCH below).
#
# The lattice hint alone does NOT deliver square cells: it only *offers* Triangle
# the nodes.  Triangle then bisects the long-side segments anyway, because the
# 0.13 mm inter-wire air slivers cannot meet the global q20 bound — a sliver
# triangle spanning a 0.6 mm step has min angle atan(0.13/0.6) ≈ 12°, so quality
# refinement plus segment encroachment split the template back to h/2 x h.
# Coarser anisotropic requests are simply refused (2.0 mm produced MORE tris).
#
# So the winding stack is built OUTRIGHT — no Triangle inside it: one structured
# quad grid per slot covering the wires AND the slivers between them, with the
# sliver rows sharing the wire faces' node columns (conformity by construction).
# The grid's outer rectangle is handed to Triangle as input segments plus a hole
# marker, so the surrounding CDT conforms to it; the patch is then stitched in by
# node identity.  Thin sliver cells (aspect ≈ 4.6) are accepted: they are air,
# P2, and the structured air-gap belt already lives with controlled-aspect cells.
_WIRE_GRID = os.environ.get("SB_NO_WIRE_GRID", "0").lower() in ("0", "false", "")
_WIRE_PATCH = os.environ.get("SB_WIRE_PATCH", "1").lower() not in ("0", "false")
# Boundary conformity — measured, not assumed.  Two ways the surrounding CDT can
# stay conforming to the patch:
#   (a) GRADE the patch's outer faces to f·(clearance to the nearest geometry) so
#       the ~0.1-0.25 mm slot air outside meets q20 on its own and no patch
#       segment is encroached (a segment of length L is encroached by a point d
#       away when L > 2d, so f < 2);
#   (b) let Triangle split what it wants and ABSORB every split by fanning the
#       patch triangle behind it (_stitch_patches) — conforming by construction,
#       whatever Triangle decides.
# (b) is the default (f = 0), measured on both machines:
#   * the tiled slot-pair cell (the normal path) triangulates with -Y, which
#     already forbids Steiner points on input segments — it splits NOTHING, so
#     grading can only add triangles;
#   * the whole-ring CDT fallback (SB_GEO_TILE=0) asked for 14 splits over 504
#     wires on CILN28 and 233 over 84 on CIANO14 (0.11 mm to the tooth) — no
#     cascade, because the 0.13 mm slivers that used to drive it now live INSIDE
#     the patch;
#   * grading instead is worse on BOTH counts — CILN28 whole ring 49 501 →
#     53 409 tris with copper AR_max 4.3 → 5.3, CIANO14 10 750 → 11 612 tris with
#     copper AR_max 8.3 → 17.8 (f = 1.0: 14 066 tris, AR_max 38.6) — because a
#     transition fan lands on EVERY wire and destroys the h x h square it was
#     meant to protect.
# SB_WIRE_PATCH_BND_F>0 re-enables (a) as a study knob.
try:
    _WIRE_PATCH_BND_F = float(os.environ.get("SB_WIRE_PATCH_BND_F", "0"))
except ValueError:
    _WIRE_PATCH_BND_F = 0.0


class _PatchError(RuntimeError):
    """The structured winding patch could not be stitched into the CDT.

    Raised only from the stitch step (after Triangle already carved the patch
    holes), where dropping a patch would leave a real hole in the mesh.  The
    stator mesher catches it and rebuilds the WHOLE half without patches, so a
    build never fails because of the patch."""


def _wire_template(poly, s_req: float = 0.0, rel: float = 0.0):
    """(boundary ring, free interior points, target cell area) for ONE wire, or
    None when the polygon is not a rectangle (a wedge-clipped wire) — those keep
    the plain CDT fill.

    The target area is HALF a lattice cell plus 10 %: the stamped quad splits
    into two triangles of exactly that half-cell, so anything tighter would make
    Triangle refine straight through the template it was handed."""
    if poly is None or getattr(poly, "is_empty", True) or poly.geom_type != "Polygon":
        return None
    if poly.interiors:                        # a wire with a hole is not a strand
        return None
    try:
        mrr = poly.minimum_rotated_rectangle
    except Exception:
        return None
    if mrr is None or mrr.geom_type != "Polygon":
        return None
    c = np.asarray(mrr.exterior.coords, float)[:4]
    if len(c) < 4:
        return None
    a_vec, b_vec = c[1] - c[0], c[3] - c[0]
    La, Lb = float(np.hypot(*a_vec)), float(np.hypot(*b_vec))
    if min(La, Lb) < 1e-4 or mrr.area <= 0.0:
        return None
    if poly.area < 0.98 * mrr.area:           # clipped/oblique → not a rectangle
        return None
    # default: the wire HEIGHT; `rel` scales it (½h / 1h / 2h), an explicit mm
    # request wins over both.  Best effort only on this tier — Triangle's quality
    # bound still bisects a >h cell here (that is exactly why the patch exists).
    _h = min(La, Lb)
    s = float(s_req) if s_req > 0 else (_h * rel if rel > 0 else _h)
    s = max(s, 1e-3)
    _eps = 1.0 + 1e-9                         # a side exactly s stays ONE step
    na = max(1, int(math.ceil(La / (s * _eps))))
    nb = max(1, int(math.ceil(Lb / (s * _eps))))
    da, db = La / na, Lb / nb
    ua, ub = a_vec / La, b_vec / Lb
    # the boundary is the REAL outline (corners exact), subdivided by the same
    # rule, so its nodes land on the lattice for a true rectangle
    ring = _densify(np.asarray(poly.exterior.coords, float), s * _eps)
    if na < 2 or nb < 2:
        pts = np.zeros((0, 2), float)         # one cell thick: boundary is all
    else:
        i, j = np.meshgrid(np.arange(1, na), np.arange(1, nb), indexing="ij")
        pts = (c[0] + np.outer(i.ravel() * da, ua) + np.outer(j.ravel() * db, ub))
        # the lattice is laid out on the ORIENTED BOX, which the outline is only
        # allowed to match to 2 % — drop anything the real wire does not contain
        # (a free vertex in the inter-wire air would seed a sliver there)
        from shapely import contains_xy
        pts = pts[np.asarray(contains_xy(poly, pts[:, 0], pts[:, 1]), bool)]
    return ring, pts, 0.55 * da * db


# ── structured winding PATCH (built outright, stitched into the CDT) ─────────
def _rect_axes(poly):
    """Orthonormal frame of a RECTANGULAR wire: dict(u, v, u0, u1, v0, v1) with
    u the long-side direction (canonically signed) and v = u rotated +90° (so
    (u, v) is right-handed and a +i,+j quad is CCW).  None when the polygon is
    not a rectangle — a wedge-clipped wire, a hole, an oblique offcut.

    The patch TILES the polygon exactly, so the rectangle test is 0.1 % here,
    ten times tighter than the 2 % the lattice hint tolerates."""
    if poly is None or getattr(poly, "is_empty", True) or poly.geom_type != "Polygon":
        return None
    if poly.interiors:
        return None
    try:
        mrr = poly.minimum_rotated_rectangle
    except Exception:
        return None
    if mrr is None or mrr.geom_type != "Polygon":
        return None
    c = np.asarray(mrr.exterior.coords, float)[:4]
    if len(c) < 4:
        return None
    a, b = c[1] - c[0], c[3] - c[0]
    La, Lb = float(np.hypot(*a)), float(np.hypot(*b))
    if min(La, Lb) < 1e-4 or mrr.area <= 0.0:
        return None
    if poly.area < 0.999 * mrr.area:
        return None
    e = (a / La) if La >= Lb else (b / Lb)
    if e[0] < -1e-12 or (abs(e[0]) <= 1e-12 and e[1] < 0.0):
        e = -e                                    # canonical sense
    u = e
    v = np.array([-u[1], u[0]])
    q = np.asarray(poly.exterior.coords, float)[:-1]
    pu, pv = q @ u, q @ v
    return dict(u=u, v=v, u0=float(pu.min()), u1=float(pu.max()),
                v0=float(pv.min()), v1=float(pv.max()))


def _flip_axes(r):
    """The same rectangle read with the roles of the two axes swapped (aligned
    along v, stacked along u), still right-handed: u' = v, v' = -u."""
    return dict(u=r["v"], v=-r["u"], u0=r["v0"], u1=r["v1"],
                v0=-r["u1"], v1=-r["u0"])


def _stack_key(r, snap: float = 1e-3):
    """Wires of one stack are translates along v: same u direction, same extent
    along u.  That pair is the grouping key."""
    return (round(float(r["u"][0]), 6), round(float(r["u"][1]), 6),
            round(r["u0"] / snap), round(r["u1"] / snap))


def _wire_stacks(rects: Dict[int, Dict], gap_max: float):
    """[[(wire index, frame), ...], ...] — aligned, non-overlapping stacks sorted
    along v.  A run is broken wherever the air gap exceeds `gap_max`, so the
    patch never swallows a wide pocket of slot air."""
    groups: Dict[Tuple, List] = {}
    for i, r in rects.items():
        groups.setdefault(_stack_key(r), []).append((i, r))
    singles = [m for g in groups.values() if len(g) < 2 for m in g]
    runs = [g for g in groups.values() if len(g) >= 2]
    if singles:                                   # try the other orientation
        g2: Dict[Tuple, List] = {}
        for i, r in singles:
            rf = _flip_axes(r)
            g2.setdefault(_stack_key(rf), []).append((i, rf))
        runs += list(g2.values())
    out = []
    for g in runs:
        g.sort(key=lambda m: m[1]["v0"])
        cur = [g[0]]
        for i, r in g[1:]:
            prev = cur[-1][1]
            gap = r["v0"] - prev["v1"]
            if gap < -1e-6 or gap > gap_max:      # overlap / wide pocket → break
                out.append(cur); cur = [(i, r)]
            else:
                cur.append((i, r))
        out.append(cur)
    return out


def _stack_patch(members, s: float, bnd_pitch: float = 0.0):
    """Structured mesh of ONE winding stack (wires + the slivers between them).

    Returns dict(V, T, ring, hole, rect, wires, n_u, du, rows) or None.
    `V` are mm node coords snapped to the 1 µm grid (so the boundary polyline
    handed to the PSLG is bit-identical to what _snap_ring would produce),
    `ring` the CCW outer-boundary node ids, `rows` the per-row wire index
    (None = an air sliver row).

    Columns and rows are BOTH at pitch ≈ s: every copper row is exactly one
    wire height / an integer division of it, and the column count is the nearest
    integer division of the wire width — so a copper cell is s x s to within the
    width's remainder, and a wire's cell count is identical for every wire.

    A band is always split into at least ONE row (`max(1, ...)`), so the VERTICAL
    pitch can never exceed the band's own height whatever s is asked for: s = 2h
    gives one row of 2h-long x h-high cells, not a cell straddling two wires."""
    from shapely.geometry import Polygon
    u = np.asarray(members[0][1]["u"], float)
    v = np.asarray(members[0][1]["v"], float)
    u0 = min(r["u0"] for _, r in members)
    u1 = max(r["u1"] for _, r in members)
    U = u1 - u0
    if U <= 1e-6:
        return None
    n_u = max(1, int(round(U / max(s, 1e-6))))
    du = U / n_u
    ulines = u0 + du * np.arange(n_u + 1)

    bands = []                                     # (v_lo, v_hi, wire | None)
    prev = None
    for i, r in members:
        if prev is not None and r["v0"] - prev > 1e-6:
            bands.append((prev, r["v0"], None))
        bands.append((r["v0"], r["v1"], i))
        prev = r["v1"]
    vlines = [bands[0][0]]
    rows: List[Optional[int]] = []
    for lo, hi, i in bands:
        t = hi - lo
        n = max(1, int(round(t / max(s, 1e-6))))
        for j in range(1, n + 1):
            vlines.append(lo + t * (j / n))
            rows.append(i)
    vlines = np.asarray(vlines, float)
    R = len(rows)
    if R < 1:
        return None

    nu1, nv1 = n_u + 1, R + 1
    Vp = (ulines[:, None, None] * u[None, None, :]
          + vlines[None, :, None] * v[None, None, :]).reshape(-1, 2)
    Vp = np.round(Vp / _SNAP) * _SNAP

    def nid(i, j):
        return i * nv1 + j

    tri = []
    for i in range(n_u):
        for j in range(R):
            a, b = nid(i, j), nid(i + 1, j)
            c, d = nid(i + 1, j + 1), nid(i, j + 1)
            if (i + j) % 2 == 0:                  # alternate the diagonal
                tri.append((a, b, c)); tri.append((a, c, d))
            else:
                tri.append((a, b, d)); tri.append((b, c, d))
    Tp = np.asarray(tri, np.int64)
    ring = ([nid(i, 0) for i in range(nu1)]
            + [nid(n_u, j) for j in range(1, nv1)]
            + [nid(i, R) for i in range(n_u - 1, -1, -1)]
            + [nid(0, j) for j in range(R - 1, 0, -1)])
    if bnd_pitch > 0.0:
        Vp, Tp, ring = _patch_insert_bnd(Vp, Tp, ring, bnd_pitch)
    corners = np.array([ulines[0] * u + vlines[0] * v,
                        ulines[-1] * u + vlines[0] * v,
                        ulines[-1] * u + vlines[-1] * v,
                        ulines[0] * u + vlines[-1] * v])
    return dict(V=Vp, T=Tp, ring=np.asarray(ring, np.int64),
                hole=corners.mean(axis=0), rect=Polygon(corners),
                wires=[i for i, _ in members], n_u=n_u, du=du,
                rows=rows, u=u, v=v)


def _patch_fan(V, T, ring, inserts):
    """Insert extra nodes into the patch's outer boundary and FAN the single
    triangle behind each split edge, so the patch stays a valid triangulation
    with the new nodes on its boundary.

    `inserts` maps a ring-edge index k (edge ring[k] → ring[k+1]) to an ordered
    array of new point coordinates.  The incidence map is kept up to date as
    triangles are replaced, so a corner triangle carrying TWO boundary edges is
    fanned twice, correctly.  Returns (V, T, ring)."""
    pts: List[np.ndarray] = [np.asarray(x, float) for x in np.asarray(V, float)]
    tris: List[Optional[Tuple[int, int, int]]] = [
        (int(a), int(b), int(c)) for a, b, c in np.asarray(T, np.int64)]
    emap: Dict[Tuple[int, int], List[int]] = {}

    def _key(x, y):
        return (min(x, y), max(x, y))

    def _reg(ti):
        a, b, c = tris[ti]
        for x, y in ((a, b), (b, c), (a, c)):
            emap.setdefault(_key(x, y), []).append(ti)

    for ti in range(len(tris)):
        _reg(ti)

    M = len(ring)
    new_ring: List[int] = []
    changed = False
    for k in range(M):
        p, q = int(ring[k]), int(ring[(k + 1) % M])
        new_ring.append(p)
        ins = inserts.get(k)
        if ins is None or not len(ins):
            continue
        owners = [t for t in emap.get(_key(p, q), []) if tris[t] is not None]
        if len(owners) != 1:
            raise _PatchError(
                "patch boundary edge has {} incident triangles".format(len(owners)))
        t = owners[0]
        a, b, c = tris[t]
        rest = {a, b, c} - {p, q}
        if len(rest) != 1:
            raise _PatchError("degenerate patch boundary triangle")
        o = rest.pop()
        ids = []
        for xy in ins:
            pts.append(np.asarray(xy, float))
            ids.append(len(pts) - 1)
        for x, y in ((a, b), (b, c), (a, c)):        # retire the fanned triangle
            emap[_key(x, y)].remove(t)
        tris[t] = None
        chain = [p] + ids + [q]
        for x, y in zip(chain[:-1], chain[1:]):
            tris.append((x, y, o))
            _reg(len(tris) - 1)
        new_ring.extend(ids)
        changed = True
    if not changed:
        return np.asarray(V, float), np.asarray(T, np.int64), list(ring)
    V2 = np.asarray(pts, float)
    T2 = np.asarray([t for t in tris if t is not None], np.int64)
    p0, p1, p2 = V2[T2[:, 0]], V2[T2[:, 1]], V2[T2[:, 2]]
    neg = ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
           - (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1])) < 0.0
    if neg.any():                                    # keep every triangle CCW
        T2[neg] = T2[neg][:, [0, 2, 1]]
    return V2, T2, new_ring


def _patch_insert_bnd(V, T, ring, pitch: float):
    """Grade the patch's OUTER faces to `pitch` (interior grid untouched)."""
    V = np.asarray(V, float)
    M = len(ring)
    inserts = {}
    for k in range(M):
        A, B = V[ring[k]], V[ring[(k + 1) % M]]
        L = float(np.hypot(*(B - A)))
        n = int(math.ceil(L / max(pitch, 1e-6) - 1e-9))
        if n > 1:
            # snapped like every other patch node: _snap_ring rounds the ring
            # handed to the PSLG to 1 µm, and a boundary node that moves by half
            # a micron there stops being the SAME node at stitch time.
            q = A + np.outer(np.arange(1, n) / n, B - A)
            inserts[k] = np.round(q / _SNAP) * _SNAP
    if not inserts:
        return V, np.asarray(T, np.int64), list(ring)
    V2, T2, r2 = _patch_fan(V, T, ring, inserts)
    return V2, T2, r2


def _stitch_patches(V, T, patches, tol: float = 6e-3):
    """Merge the structured patches into the CDT output by node identity.

    Triangle received each patch's boundary polyline as input segments and its
    centre as a hole marker, so the CDT stops exactly on the patch boundary and
    every patch boundary node is already a CDT node.  Any node Triangle
    ADDITIONALLY placed on a patch segment (quality/encroachment split) is
    absorbed by fanning the patch triangle behind it, so conformity holds
    whatever Triangle decided.  Returns (V, T, stats)."""
    from scipy.spatial import cKDTree
    if not patches:
        return V, T, {"n_patch": 0, "n_split": 0, "n_tri": 0}
    V = np.asarray(V, float)
    T = np.asarray(T, np.int64)
    kd = cKDTree(V)
    Vout = [V]
    Tout = [T]
    off = len(V)
    n_split = 0
    n_tri = 0
    for p in patches:
        Vp = np.asarray(p["V"], float)
        Tp = np.asarray(p["T"], np.int64)
        ring = [int(i) for i in p["ring"]]
        M = len(ring)
        inserts = {}
        for k in range(M):
            A, B = Vp[ring[k]], Vp[ring[(k + 1) % M]]
            AB = B - A
            L = float(np.hypot(*AB))
            if L <= 1e-9:
                continue
            hits = []
            for ci in kd.query_ball_point(0.5 * (A + B), 0.5 * L + tol):
                w = V[ci] - A
                if float(w @ w) <= tol * tol:            # this IS ring[k]
                    continue
                wb = V[ci] - B
                if float(wb @ wb) <= tol * tol:          # this IS ring[k+1]
                    continue
                t = float(w @ AB) / (L * L)
                if t <= 1e-6 or t >= 1.0 - 1e-6:
                    continue
                if abs(float(AB[0] * w[1] - AB[1] * w[0])) / L <= tol:
                    hits.append((t, V[ci]))
            if hits:
                hits.sort(key=lambda h: h[0])
                inserts[k] = np.array([h[1] for h in hits])
        if inserts:
            n_split += int(sum(len(x) for x in inserts.values()))
            Vp, Tp, ring = _patch_fan(Vp, Tp, ring, inserts)
        d, j = kd.query(Vp[np.asarray(ring, np.int64)])
        if float(np.max(d)) > tol:
            raise _PatchError(
                "patch boundary node {:.4g} mm from any CDT node".format(
                    float(np.max(d))))
        if len(set(j.tolist())) != len(j):
            raise _PatchError("two patch boundary nodes welded onto one CDT node")
        gid = np.full(len(Vp), -1, np.int64)
        gid[np.asarray(ring, np.int64)] = j
        free = np.where(gid < 0)[0]
        gid[free] = off + np.arange(len(free))
        off += len(free)
        Vout.append(Vp[free])
        Tout.append(gid[Tp])
        n_tri += len(Tp)
    return (np.vstack(Vout), np.vstack(Tout),
            {"n_patch": len(patches), "n_split": n_split, "n_tri": n_tri})


def _coil_pslg(coils, a_coil_req: Optional[float], area: float,
               patch: bool = True, iron=None, rays=(), coil_rel: float = 0.0):
    """(boundary rings, free interior points, region seeds, patches) for the
    winding — the one place both stator meshers get their conductors from, so
    the full ring and the slot cell build the identical structure.

    A slot whose wires form a clean aligned stack of rectangles becomes ONE
    structured patch (rings carry only its outer rectangle, no interior points,
    no per-wire region seed); anything else — a wedge-clipped wire, a
    non-rectangle, a stack that is not cleanly aligned, a stack too close to a
    sector cut ray — falls back to the lattice-hint template, and that in turn
    falls back to the plain CDT fill.

    `coil_rel` (½ / 1 / 2, 0 = absent) scales the default pitch by the wire's OWN
    height; an explicit `a_coil_req` (from the mm "Windings" size) wins over it.
    coil_rel == 1 reproduces the no-key mesh exactly — including the stack
    GROUPING, which keeps using the geometric wire height for its gap_max so a
    ½h/2h request re-cuts cells without re-grouping wires."""
    s_req = math.sqrt(float(a_coil_req) / 0.4330) if a_coil_req else 0.0
    a_def = float(a_coil_req) if a_coil_req else float(area)
    rings: List[np.ndarray] = []
    pts: List[np.ndarray] = []
    seeds: List[List[float]] = []
    patches: List[Dict] = []
    done = set()

    if _WIRE_GRID and patch and _WIRE_PATCH and coils:
        rects = {}
        for i, w in enumerate(coils):
            fr = _rect_axes(w)
            if fr is not None:
                rects[i] = fr
        # default pitch = the wire HEIGHT (true squares); an explicit per-part
        # coil size wins and gives squares of the requested size.
        def _h(rs):
            return float(np.median([min(r["u1"] - r["u0"], r["v1"] - r["v0"])
                                    for r in rs])) if len(rs) else 0.0

        # gap_max is a GEOMETRIC break rule (how wide a pocket may be bridged),
        # so it reads the wire height, NOT the requested cell pitch — otherwise
        # ½h/2h would silently regroup the wires instead of just re-cutting them.
        s_glob = s_req if s_req > 0 else _h(list(rects.values()))
        cand = []
        for mem in _wire_stacks(rects, gap_max=1.5 * max(s_glob, 1e-6)):
            _hm = _h([r for _, r in mem])
            s = s_req if s_req > 0 else (_hm * coil_rel if coil_rel > 0 else _hm)
            try:
                p = _stack_patch(mem, s, bnd_pitch=_patch_bnd_pitch(mem, s, iron))
            except _PatchError:
                p = None
            if p is None or not _patch_clear_of_rays(p, rays):
                continue
            cand.append(p)
        patches = _patch_keep_disjoint(cand, coils, iron)
        for p in patches:
            done.update(p["wires"])

    n_tpl = 0
    for i, w in enumerate(coils):
        if i in done:
            continue
        tpl = _wire_template(w, s_req, coil_rel) if _WIRE_GRID else None
        if tpl is None:
            rings.append(np.asarray(w.exterior.coords, float))
            seeds.append([w.centroid.x, w.centroid.y, 2, a_def])
            continue
        ring, ipts, a_cell = tpl
        rings.append(ring)
        if len(ipts):
            pts.append(ipts)
        seeds.append([w.centroid.x, w.centroid.y, 2, a_cell])
        n_tpl += 1
    for p in patches:                       # the patch outline IS the PSLG input
        rings.append(np.vstack([p["V"][p["ring"]], p["V"][p["ring"][:1]]]))
    if coils:
        log.info("coil mesh: %d patches covering %d/%d wires (%d tris), "
                 "%d lattice-stamped, %d plain CDT",
                 len(patches), len(done), len(coils),
                 sum(len(p["T"]) for p in patches), n_tpl,
                 len(coils) - len(done) - n_tpl)
    P = np.vstack(pts) if pts else np.zeros((0, 2), float)
    return rings, P, seeds, patches


def _patch_keep_disjoint(cands, coils, iron):
    """Drop any patch whose rectangle would SWALLOW geometry it does not own.

    The stack is bridged across the air gaps between its wires, so a wire the
    wedge clipped (rejected as a non-rectangle, hence not a member) sitting
    BETWEEN two intact wires would end up inside the patch rectangle — its
    outline would then be a segment loop inside Triangle's hole, leaving a
    filled island overlapping the patch.  Same for iron poking into the stack's
    bounding box, and for two patches that overlap.  Any of those → that stack
    keeps the lattice/CDT path."""
    if not cands:
        return []
    keep: List[Dict] = []
    try:
        from shapely import STRtree
        tree = STRtree(coils) if coils else None
    except Exception:
        tree = None
    for p in cands:
        rect = p["rect"]
        own = set(p["wires"])
        bad = False
        if iron is not None:
            try:
                bad = rect.intersection(iron).area > 1e-9
            except Exception:
                bad = True
        if not bad and tree is not None:
            for j in np.atleast_1d(tree.query(rect)).tolist():
                if int(j) in own:
                    continue
                if rect.intersection(coils[int(j)]).area > 1e-9:
                    bad = True
                    break
        if not bad:
            for q in keep:
                if rect.intersection(q["rect"]).area > 1e-9:
                    bad = True
                    break
        if bad:
            log.info("winding patch: stack of %d wires overlaps foreign "
                     "geometry — lattice path for it", len(own))
            continue
        keep.append(p)
    return keep


def _patch_bnd_pitch(members, s: float, iron) -> float:
    """Grading pitch for the patch's outer faces (0 = none).

    The slot air between the stack and the tooth flank is only ~0.1–0.25 mm
    thick.  A patch boundary segment of length L is ENCROACHED by a point d
    away when L > 2d, and Triangle answers encroachment by splitting the
    segment — which the stitch would then have to fan.  Subdividing the outer
    faces at 1.5·d up front keeps the split count at zero and, unlike letting
    Triangle decide, makes the transition IDENTICAL for every slot."""
    if _WIRE_PATCH_BND_F <= 0.0 or iron is None:
        return 0.0
    try:
        from shapely.geometry import Polygon
        u = np.asarray(members[0][1]["u"], float)
        v = np.asarray(members[0][1]["v"], float)
        u0 = min(r["u0"] for _, r in members); u1 = max(r["u1"] for _, r in members)
        v0 = min(r["v0"] for _, r in members); v1 = max(r["v1"] for _, r in members)
        rect = Polygon([u0 * u + v0 * v, u1 * u + v0 * v,
                        u1 * u + v1 * v, u0 * u + v1 * v])
        d = float(rect.exterior.distance(iron))
    except Exception:
        return 0.0
    if not (d > 1e-6):
        return 0.0
    return min(float(s), _WIRE_PATCH_BND_F * d)


def _patch_clear_of_rays(p, rays) -> bool:
    """A patch must stay clear of the sector cut rays: _symmetrize_cuts snaps
    every node within 3e-3 rad of a ray onto the shared radius chain, which
    would drag a patch boundary node off the patch."""
    if not len(rays):
        return True
    xy = np.asarray(p["V"][p["ring"]], float)
    ang = np.arctan2(xy[:, 1], xy[:, 0])
    for th in rays:
        d = np.abs(np.arctan2(np.sin(ang - th), np.cos(ang - th)))
        if float(d.min()) < 6e-3:
            return False
    return True


def _add_free_points(V: np.ndarray, P: np.ndarray,
                     merge_tol: float = 0.006) -> np.ndarray:
    """Append PSLG-free vertices (the wire lattices).  Triangle keeps every input
    vertex, so these become mesh nodes as-is; points landing on an existing node
    are dropped — a duplicate vertex would leave an unreferenced row."""
    P = np.asarray(P, float)
    if not len(P):
        return V
    from scipy.spatial import cKDTree
    keep = cKDTree(V).query(P, distance_upper_bound=merge_tol)[0] > merge_tol
    return np.vstack([V, P[keep]]) if keep.any() else V


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
                 rotor_bridge: bool = False, extra_holes=None):
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
    if extra_holes is not None and len(extra_holes):
        # the structured winding patches: Triangle must stop ON their boundary
        # segments and leave the interior to the patch mesh
        _eh = np.asarray(extra_holes, float).reshape(-1, 2)
        A["holes"] = (np.vstack([A["holes"], _eh]) if "holes" in A else _eh)
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
    _cap = _TRI_BUDGET["v"]
    if _cap and _q:
        # Armed (optimizer eval subprocess): run the SAME quality refinement
        # under a Steiner cap so a cascade terminates promptly instead of
        # building 10^6 triangles.  ~2 triangles per point → capping the added
        # points at budget/2 caps the triangle count at ~the budget.  A mesh
        # that needs fewer points than the cap is bit-identical to the unarmed
        # run; one that hits the cap was truncated mid-refinement — it would
        # have blown past the budget, so reject it rather than solve on a
        # half-refined mesh that matches nothing the Simulation tab would build.
        _s = max(1000, int(_cap) // 2)
        out = _tri.triangulate(A, f"p{_q}{_tail}S{_s}{_Y}")
        Vo = np.asarray(out["vertices"], float)
        To = np.asarray(out["triangles"], np.int64)
        if _steiner_cap_truncated(A, out, area, cap=_s):
            raise MeshBudgetExceeded(
                "mesh budget: quality meshing of this cross-section hit the "
                "{}-point Steiner cap ({} triangles and still refining; budget "
                "{} triangles for the whole mesh). A feature of this candidate "
                "is too thin for the mesher to resolve economically — an eval "
                "this size cannot finish inside the optimizer's per-candidate "
                "time cap, so it is rejected before any FEM time is spent."
                .format(_s, len(To), int(_cap)))
        return Vo, To
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
                      quality: int, r2_band: float = 0.0,
                      part_area: Optional[Dict] = None, coil_rel: float = 0.0):
    """Stator half — structured winding patches first, plain lattice on retry."""
    try:
        return _stator_half_impl(polys, r_bore, r_out_iron, r_outer, n_slip,
                                 area, air_mm, quality, r2_band, part_area,
                                 patch=True, coil_rel=coil_rel)
    except _PatchError as e:
        log.warning("winding patch not stitchable (%s) — lattice fallback", e)
        return _stator_half_impl(polys, r_bore, r_out_iron, r_outer, n_slip,
                                 area, air_mm, quality, r2_band, part_area,
                                 patch=False, coil_rel=coil_rel)


def _stator_half_impl(polys: Dict, r_bore: float, r_out_iron: float,
                      r_outer: float, n_slip: int, area: float, air_mm: float,
                      quality: int, r2_band: float = 0.0,
                      part_area: Optional[Dict] = None, patch: bool = True,
                      coil_rel: float = 0.0):
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
    _pa = part_area or {}
    a_iron = float(_pa.get("stator", area))                # per-part override
    iron = _resample(polys["stator"], r_bore, n_slip)      # bore → slip grid
    coils = [w for w in (polys.get("coils") or []) if w is not None and not w.is_empty]
    _c_rings, _c_pts, _c_seeds, _c_patch = _coil_pslg(
        coils, _pa.get("coil"), area, patch=patch, iron=iron,
        coil_rel=coil_rel)

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
    for r in _c_rings:                                     # REAL conductors
        add(r)
    air_area = max(area, 0.4330 * air_mm * air_mm)          # coarse air cell
    add(_grid_circle(r_bore, n_slip))                       # bore (slip grid)
    if 0.0 < r2_band < r_bore - 1e-6:
        add(_grid_circle(r2_band, n_slip))                  # moving-band R2
    # the FAR-FIELD outer circle is not a belt boundary, so discretise it at the
    # air size — otherwise a fine outer ring caps how coarse the air can get.
    add(_grid_circle(r_outer, max(48, int(2 * math.pi * r_outer / max(1.0, air_mm)))))
    V, S = _build_pslg(lines)
    V = _add_free_points(V, _c_pts)                          # wire lattices

    # region seeds: iron + each conductor fine; each air pocket coarse
    ann = Polygon(_grid_circle(r_outer, 360)[:-1]).difference(
          Polygon(_grid_circle(r_bore, n_slip)[:-1]))
    # the patch rectangles are holes for the CDT, so the air pockets must be
    # taken AROUND them — else a pocket's representative point lands inside a
    # patch and the real remaining slot air is left unseeded (no area cap).
    _emb = coils + [p["rect"] for p in _c_patch]
    _air_reg = [[*a.representative_point().coords[0], 3, air_area]
                for a in _air_parts(ann, iron, _emb)]
    if 0.0 < r2_band < r_bore - 1e-6:
        # gap-air annulus [R2, bore] — FINE (it carries the gap field)
        _air_reg += [[0.5 * (r2_band + r_bore), 0.0, 4, area]]
    reg = _seeds(_air_reg,
                 [[*iron.representative_point().coords[0], 1, a_iron]] + _c_seeds)
    V, T = _triangulate(V, S, area, quality, regions=reg,
                        extra_holes=[p["hole"] for p in _c_patch])
    V, T, _st = _stitch_patches(V, T, _c_patch)
    if _c_patch:
        log.info("winding patch: %d stitched, %d Triangle segment splits fanned",
                 _st["n_patch"], _st["n_split"])
    return V, T


def _shaft_bore_r(polys: Dict, r_shaft: float) -> float:
    """Inner radius (mm) of the CadQuery shaft TUBE — 0.0 for a solid shaft.

    The shaft CadQuery builds is a hollow tube (rotor_inner_radius −
    shaft_height .. rotor_inner_radius); the bore inside it is AIR.  Meshing the
    whole inner disk as ONE DOM_SHAFT region handed the eddy solve ~7x the
    conductive section the machine has (150 mm: 5425 mm² of "aluminium" for a
    756 mm² tube), so the shaft eddy tile billed metal that does not exist.

    The bore is read off the polygon rather than off `shaft_inner_radius` so the
    meshed conductor is exactly the section the mass model bills (masses.py
    measures the same polygon)."""
    g = (polys or {}).get("shaft")
    if g is None or getattr(g, "is_empty", True):
        return 0.0
    r_in = 0.0
    for sub in getattr(g, "geoms", [g]):
        for h in getattr(sub, "interiors", []):
            c = np.asarray(h.coords, float)
            if len(c):
                r_in = max(r_in, float(np.hypot(c[:, 0], c[:, 1]).max()))
    return r_in if 1e-6 < r_in < r_shaft - 1e-3 else 0.0


def _mesh_rotor_half(polys: Dict, r_od: float, r_shaft: float,
                     n_slip: int, area: float, air_mm: float, quality: int,
                     r1_band: float = 0.0, part_area: Optional[Dict] = None):
    """(V mm, T) for the rotor disk [0, r_od].  Steel and magnets mesh at
    `area`; the solid shaft core (r < r_shaft) and the flux-barrier air pockets
    get the coarse air size — the rotor centre carries little flux.

    r1_band > r_od extends the half with the gap-air annulus [r_od, r1_band]
    ending on the UNIFORM slip-grid ring R1 — the moving-band/harmonic-macro
    boundary (the macro couples R1↔R2 analytically, no node-merge belt)."""
    from shapely.geometry import LineString, MultiPolygon, Polygon
    air_area = max(area, 0.4330 * air_mm * air_mm)              # coarse air cell
    _pa = part_area or {}
    a_steel = float(_pa.get("rotor", area))                     # per-part override
    a_mag = float(_pa.get("magnet", area))
    # NOTE: no per-part "shaft" size here — see GEO_PART_KEYS (the -Y cut
    # chains make the core's area constraint unsatisfiable; the request is
    # routed to the gmsh mesher instead of being silently dropped).
    parts = [g for g in getattr(polys["rotor"], "geoms", [polys["rotor"]])
             if getattr(g, "area", 0.0) > 1e-6]                 # drop degenerate
    steel = MultiPolygon(parts) if len(parts) > 1 else parts[0]
    steel = _defeature_iron(steel)            # trim knife-edge slivers (pre-grid)
    iron = _resample(steel, r_od, n_slip)                       # OD → slip grid
    # shaft seam is internal (not a belt boundary) → discretise at the air size
    n_sh = max(48, int(2 * math.pi * r_shaft / max(0.35, air_mm)))
    iron = _resample(iron, r_shaft, n_sh)                       # shaft → own grid
    # HOLLOW shaft (see _shaft_bore_r): mesh the tube wall as its own region and
    # leave the bore inside it to the coarse air size — _tag_rotor then tags the
    # bore DOM_AIR, so sigma = 0 there.
    r_bore = _shaft_bore_r(polys, r_shaft)
    a_tube = air_area
    n_bore = 0
    if r_bore > 0.0:
        t_tube = r_shaft - r_bore
        a_tube = max(1e-3, min(air_area, 0.4330 * (0.5 * t_tube) ** 2))
        n_bore = max(48, int(2 * math.pi * r_bore / max(0.35, 0.5 * t_tube)))
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
    if r_bore > 0.0:
        add(_grid_circle(r_bore, n_bore))                       # shaft tube bore
    V, S = _build_pslg(lines)

    # region seeds: steel + each magnet fine; shaft bore + flux barriers coarse
    _core_r = 0.5 * (r_bore if r_bore > 0.0 else r_shaft)
    _air_reg = [[_core_r, 0.0, 7, air_area]]                   # inside the tube
    if r_bore > 0.0:                                           # the tube wall
        _air_reg += [[0.5 * (r_bore + r_shaft), 0.0, 10, a_tube]]
    ann = Polygon(_grid_circle(r_od, n_slip)[:-1]).difference(
          Polygon(_grid_circle(r_shaft, n_sh)[:-1]))
    _air_reg += [[*a.representative_point().coords[0], 8, air_area]
                 for a in _air_parts(ann, steel, mags)]
    if r1_band > r_od + 1e-6:
        # gap-air annulus [r_od, R1] — FINE (it carries the gap field)
        _air_reg += [[0.5 * (r_od + r1_band), 0.0, 9, area]]
    reg = _seeds(_air_reg,
                 [[*steel.representative_point().coords[0], 5, a_steel]]
                 + [[mg.centroid.x, mg.centroid.y, 6, a_mag] for mg in mags])
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
                        cell: bool = False, part_area: Optional[Dict] = None,
                        coil_rel: float = 0.0):
    """Stator wedge — structured winding patches first, plain lattice on retry."""
    try:
        return _stator_sector_impl(polys, r_bore, r_out_iron, r_outer, n_slip,
                                   span, area, air_mm, quality, r2_band, cell,
                                   part_area, patch=True, coil_rel=coil_rel)
    except _PatchError as e:
        log.warning("winding patch not stitchable (%s) — lattice fallback", e)
        return _stator_sector_impl(polys, r_bore, r_out_iron, r_outer, n_slip,
                                   span, area, air_mm, quality, r2_band, cell,
                                   part_area, patch=False, coil_rel=coil_rel)


def _stator_sector_impl(polys, r_bore, r_out_iron, r_outer, n_slip, span,
                        area, air_mm, quality, r2_band: float = 0.0,
                        cell: bool = False, part_area: Optional[Dict] = None,
                        patch: bool = True, coil_rel: float = 0.0):
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
    _pa = part_area or {}
    a_iron = float(_pa.get("stator", area))                # per-part override
    _rin = r2_band if 0.0 < r2_band < r_bore - 1e-6 else r_bore
    W = _wedge(-1e-3, span + 1e-3, _rin - 3.0, r_outer + 3.0)
    iron = _resample(polys["stator"], r_bore, n_slip).intersection(W)
    coils = [c.intersection(W) for c in (polys.get("coils") or [])
             if c is not None and not c.is_empty and c.intersects(W)]
    coils = [c for c in coils if c.geom_type == "Polygon" and c.area > 1e-6]
    _c_rings, _c_pts, _c_seeds, _c_patch = _coil_pslg(
        coils, _pa.get("coil"), area, patch=patch, iron=iron,
        rays=(0.0, float(span)), coil_rel=coil_rel)
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
    for r in _c_rings:
        add(r)
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
    # wire lattices carry NO segments, so the cut symmetrisation above never
    # sees them and -Y still freezes exactly the real boundary
    V = _add_free_points(V, _c_pts)

    ann = W.intersection(Polygon(_grid_circle(r_outer, 360)[:-1]).difference(
                         Polygon(_grid_circle(r_bore, n_slip)[:-1])))
    _emb = coils + [p["rect"] for p in _c_patch]     # see _mesh_stator_half
    _air_reg = [[*a.representative_point().coords[0], 3, air_area]
                for a in _air_parts(ann, iron, _emb)]
    if 0.0 < r2_band < r_bore - 1e-6:
        _rm = 0.5 * (r2_band + r_bore)
        _air_reg += [[_rm * math.cos(span / 2), _rm * math.sin(span / 2), 4, area]]
    reg = _seeds(_air_reg,
                 [[*iron.representative_point().coords[0], 1, a_iron]] + _c_seeds)
    hp = [[(_rin - 1.5) * math.cos(span / 2), (_rin - 1.5) * math.sin(span / 2)]]
    V, T = _triangulate(V, S, area, quality, regions=reg, hole_pts=hp,
                        no_bnd_steiner=True,
                        extra_holes=[p["hole"] for p in _c_patch])
    V, T, _st = _stitch_patches(V, T, _c_patch)
    if _c_patch:
        log.info("winding patch: %d stitched, %d Triangle segment splits fanned",
                 _st["n_patch"], _st["n_split"])
    return V, T


def _mesh_rotor_sector(polys, r_od, r_shaft, n_slip, span, area, air_mm, quality,
                       r1_band: float = 0.0, cell_copies: int = 0,
                       part_area: Optional[Dict] = None):
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
    _pa = part_area or {}
    a_steel = float(_pa.get("rotor", area))                # per-part override
    a_mag = float(_pa.get("magnet", area))
    # NOTE: no per-part "shaft" size here — see GEO_PART_KEYS (the -Y cut
    # chains make the core's area constraint unsatisfiable; the request is
    # routed to the gmsh mesher instead of being silently dropped).
    parts = [g for g in getattr(polys["rotor"], "geoms", [polys["rotor"]])
             if getattr(g, "area", 0.0) > 1e-6]
    steel = MultiPolygon(parts) if len(parts) > 1 else parts[0]
    steel = _defeature_iron(steel)            # trim knife-edge slivers (pre-grid)
    n_sh = max(48, int(2 * math.pi * r_shaft / max(0.35, air_mm)))
    if cell_copies > 0:                       # grid nodes exactly on the rays
        n_sh = int(math.ceil(n_sh / cell_copies)) * cell_copies
    # HOLLOW shaft (see _shaft_bore_r): the tube wall is a region of its own,
    # the bore inside it stays coarse air.
    r_bore = _shaft_bore_r(polys, r_shaft)
    a_tube = air_area
    n_bore = 0
    if r_bore > 0.0:
        t_tube = r_shaft - r_bore
        a_tube = max(1e-3, min(air_area, 0.4330 * (0.5 * t_tube) ** 2))
        n_bore = max(48, int(2 * math.pi * r_bore / max(0.35, 0.5 * t_tube)))
        if cell_copies > 0:
            n_bore = int(math.ceil(n_bore / cell_copies)) * cell_copies
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
    rk_sh = _graded_radii(
        [(0.0, r_bore, air_mm), (r_bore, r_shaft, 0.5 * (r_shaft - r_bore))]
        if r_bore > 0.0 else [(0.0, r_shaft, air_mm)])
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
    if r_bore > 0.0:                                    # shaft tube bore
        # _lin_arc, not _grid_arc: the bore is not a slip circle, so its
        # endpoints must land EXACTLY on the two cut rays or the tiled copies
        # leave a ragged seam.
        add(_lin_arc(r_bore, max(2, int(round(n_bore * span / (2.0 * math.pi)))),
                     span))
    add(_cut_pts(0.0, rk_all)); add(_cut_pts(span, rk_all))
    V, S = _build_pslg(lines)
    V, S = _symmetrize_cuts(V, S, span)                 # clone-identical seam

    _core_r = 0.5 * (r_bore if r_bore > 0.0 else r_shaft)
    _air_reg = [[_core_r * math.cos(span / 2),
                 _core_r * math.sin(span / 2), 7, air_area]]   # inside the tube
    if r_bore > 0.0:                                           # the tube wall
        _rt = 0.5 * (r_bore + r_shaft)
        _air_reg += [[_rt * math.cos(span / 2), _rt * math.sin(span / 2),
                      10, a_tube]]
    ann = W.intersection(Polygon(_grid_circle(r_od, n_slip)[:-1]).difference(
                         Polygon(_grid_circle(r_shaft, n_sh)[:-1])))
    _air_reg += [[*a.representative_point().coords[0], 8, air_area]
                 for a in _air_parts(ann, steel, mags)]
    if r1_band > r_od + 1e-6:
        _rm = 0.5 * (r_od + r1_band)
        _air_reg += [[_rm * math.cos(span / 2), _rm * math.sin(span / 2), 9, area]]
    reg = _seeds(
        _air_reg,
        [[*steel.intersection(W).representative_point().coords[0], 5, a_steel]]
        + [[g.centroid.x, g.centroid.y, 6, a_mag] for g in mags])
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
    _rr = np.hypot(cx, cy)
    tags[_rr < r_shaft - 1e-6] = DOM_SHAFT
    # HOLLOW shaft: only the tube WALL is metal.  The bore inside it is air —
    # tagging the whole inner disk DOM_SHAFT gave _sigma_of_tag() the shaft's
    # sigma over ~7x the CAD section, inflating the shaft eddy tile (and the
    # free-run decomposition's computed shaft leg) by the same factor.
    _r_bore = _shaft_bore_r(polys, r_shaft)
    if _r_bore > 0.0:
        tags[_rr < _r_bore - 1e-6] = DOM_AIR
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
                    mesh_edge_mm: float = 0.0,
                    part_mesh_mm: Optional[Dict] = None):
    """Solver-ready halves in DOM_* tags, geometry-driven CDT:
    (mesh_s, tags_s, cls_s, mesh_r, tags_r, cls_r) — same signature as
    iron_template.template_solver_halves.  Full ring only for now (n_sectors
    handled by the caller's fallback until the sector clone lands).

    r_si / r_ro (belt spec radii, mm) pin the gap rings so the belt welds by
    node identity.  They are REQUIRED to come from a source that matches the
    belt — the stator bore lives on an INTERIOR ring (the polygon exterior min
    is the yoke, not the bore), so _radius_span would grid the wrong circle."""
    from skfem import MeshTri

    # ── SB_WIRE_SIMPLIFY_MM (study knob, default off): Douglas-Peucker the
    # wire outlines before meshing.  Keeps every true corner (rectangles stay
    # rectangles), drops fillet-arc points within the tolerance — the wire
    # boundary is what pins the mesh density floor (measured: max element
    # size saturates at ~4 mm because of it).  Physics topology untouched:
    # every strand keeps its own region, only its outline gets fewer chords.
    _wsimp = 0.0
    try:
        _wsimp = float(_os_gm.environ.get("SB_WIRE_SIMPLIFY_MM", "0") or 0.0)
    except ValueError:
        _wsimp = 0.0
    if _wsimp > 0 and polys.get("coils"):
        polys = dict(polys)
        _n0 = sum(len(w.exterior.coords) for w in polys["coils"]
                  if w is not None and not w.is_empty)
        polys["coils"] = [
            (w.simplify(_wsimp, preserve_topology=True)
             if w is not None and not w.is_empty else w)
            for w in polys["coils"]]
        _n1 = sum(len(w.exterior.coords) for w in polys["coils"]
                  if w is not None and not w.is_empty)
        log.info("SB_WIRE_SIMPLIFY_MM=%.3g: coil outline points %d -> %d",
                 _wsimp, _n0, _n1)

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
    # PER-PART element size: every solid part already owns a CDT region seed
    # (stator iron / each coil / rotor steel / each magnet), so a requested part
    # size is simply that seed's target cell area.  Parts not requested keep the
    # global `area`, so an empty request is bit-identical to the previous mesh.
    part_area = _part_areas(part_mesh_mm)
    if part_area:
        log.info("geo per-part element size: %s (global %.3f mm)",
                 ", ".join(f"{k}={math.sqrt(v / 0.4330):.3f}mm"
                           for k, v in sorted(part_area.items())), iron_edge)
    # "Wire cell" factor — travels in the same dict but is NOT a size, so it is
    # read out separately and handed to the stator meshers only (the winding is
    # the only thing it touches).
    coil_rel = _coil_rel_of(part_mesh_mm)
    if coil_rel and "coil" in part_area:
        log.info("wire cell: coil_rel=%.3g ignored — explicit coil size wins",
                 coil_rel)
    elif coil_rel:
        log.info("wire cell: coil_rel=%.3g (x the wire height)", coil_rel)

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
                                         r2_band=r2_band, cell=True,
                                         part_area=part_area,
                                         coil_rel=coil_rel)
            Vs, Ts = _tile_cells(Vc, Tc, _span_s, _n_pairs // _ns)
            Vcr, Tcr = _mesh_rotor_sector(polys, r_od, r_sh, n_slip, _span_r,
                                          area, air_mm, _Q, r1_band=r1_band,
                                          cell_copies=_n_poles,
                                          part_area=part_area)
            Vr, Tr = _tile_cells(Vcr, Tcr, _span_r, _n_poles // _ns)
            log.info("geo tile: stator %d x pair-cell(%dtri) = %dtri, rotor "
                     "%d x cell(%dtri) = %dtri (1/%d)", _n_pairs // _ns,
                     len(Tc), len(Ts), _n_poles // _ns, len(Tcr), len(Tr), _ns)
        except MeshBudgetExceeded:
            # The whole-wedge fallback re-runs the SAME refinement on the same
            # geometry — it would hit the same cap after burning another capped
            # run.  The budget verdict is about the geometry, not the tiling.
            raise
        except Exception as _te:
            log.warning("geo tile failed (%s) — whole-wedge fallback", _te)
            Vs = None
    if Vs is None:
        if _ns > 1:                                    # 1/N wedge
            span = 2.0 * math.pi / _ns
            Vs, Ts = _mesh_stator_sector(polys, r_bore, r_out_iron, r_outer,
                                         n_slip, span, area, air_mm, _Q,
                                         r2_band=r2_band, part_area=part_area,
                                         coil_rel=coil_rel)
            Vr, Tr = _mesh_rotor_sector(polys, r_od, r_sh, n_slip, span,
                                        area, air_mm, _Q, r1_band=r1_band,
                                        part_area=part_area)
        else:                                          # full ring
            Vs, Ts = _mesh_stator_half(polys, r_bore, r_out_iron,
                                       r_outer, n_slip, area, air_mm, _Q,
                                       r2_band=r2_band, part_area=part_area,
                                       coil_rel=coil_rel)
            Vr, Tr = _mesh_rotor_half(polys, r_od, r_sh, n_slip, area, air_mm, _Q,
                                      r1_band=r1_band, part_area=part_area)
    # Armed budget, second gate: the per-cell Steiner cap bounds each Triangle
    # RUN, but tiling multiplies a cell by its copy count and the two halves
    # add — the number the FEM will actually assemble is checked here, before
    # tagging/solving pays for it.
    _cap = _TRI_BUDGET["v"]
    if _cap and len(Ts) + len(Tr) > int(_cap):
        raise MeshBudgetExceeded(
            "mesh budget: {} stator + {} rotor triangles exceed the {}-triangle "
            "budget. A mesh this size cannot finish inside the optimizer's "
            "per-candidate time cap, so the candidate is rejected before any "
            "FEM time is spent.".format(len(Ts), len(Tr), int(_cap)))
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
    _r_bore = _shaft_bore_r(polys, r_sh)
    Vr, Tr = _collapse_slivers(Vr, Tr,
                               keep_r=((r_od, r_sh, _r_bore) if _r_bore > 0.0
                                       else (r_od, r_sh)))
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
