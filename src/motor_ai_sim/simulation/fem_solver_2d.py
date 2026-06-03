"""2-D finite-element magnetostatics solver — pure Python (scikit-fem + gmsh).

Solves   ∇·(ν ∇A_z) = -J_z + ∇·(M × ẑ)    on a triangle mesh of the motor
cross-section.  Used as a real-FEM alternative to the analytical Green's
function solver in routes/simulation.py.

Domain-specific data (matches the CadQuery polygon classes):
    air-gap        ν = 1/μ₀                     (free space)
    stator steel   ν = 1/(μ₀·5000)              (silicon steel, linear)
    rotor steel    ν = 1/(μ₀·5000)
    shaft          ν = 1/(μ₀·1000)              (aluminium-ish)
    magnets        ν = 1/(μ₀·1.05),  M = ±Br·φ̂   (tangential, alternating)
    coils          ν = 1/μ₀,  J_z = direction · I_phase · n_wires / area

Boundary: A_z = 0 on the outer stator circle.

Method: linear FE on a P1-triangle mesh; gmsh builds a conforming mesh from
the real CadQuery exterior+interior contours so the same geometry the
canvas displays is the geometry we solve on.

Time budget for one solve at the default mesh density (~25k triangles):
roughly 1-2 s on a modern CPU.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np

log = logging.getLogger(__name__)

# Global lock around gmsh usage.  gmsh exposes a single C library
# instance per process, so two threads cannot concurrently call
# initialize()/model.add()/mesh.generate()/finalize() — the second
# call sees "Gmsh has not been initialized" if it lands between the
# first thread's init and finalize.  FastAPI runs sync 'def' endpoints
# (fem_field2d, fem_transient) on the threadpool, and the browser
# fires 2-4 of them in parallel on Simulation-tab mount, so without
# a lock the second request crashes the gmsh state.
_GMSH_LOCK = threading.RLock()

MU0 = 4e-7 * math.pi

# Number of equally-spaced nodes on the sliding-band slip circle (r = mid_r).
# Shared by in_band (exterior) and out_band (hole) so the two half-meshes get
# IDENTICAL matching nodes there.  Multiple of 14 (pole pairs) so the rotor
# step aligns to whole nodes for n_steps ∈ {12,24,36,72,...}.
# 1008 = 14·72 → 252 nodes per 90° sector ≈ 0.46 mm tangential spacing on the
# slip ring.  This count drives the angular resolution of the rotor-rotation
# merge, so lowering it raises torque ripple — keep it high.  The VISUAL band
# width is controlled by the radial air-gap size field, not this.
_N_SLIP = 1008


def _snap_steps_to_nodes(n_steps: int, nodes_per_period: int) -> int:
    """Snap the requested steps/period to the nearest DIVISOR of the slip-ring
    node count per electrical period, so the rotor advances a whole number of
    nodes each time step (uniform → strictly PERIODIC torque, not the chaotic
    jitter from round(θ/spacing) landing between nodes).

    Keeping the slip-node count FIXED (instead of scaling it with n_steps) means
    the mesh — and therefore the torque magnitude — does NOT drift with the time
    resolution.  Effective resolution is capped at nodes_per_period."""
    ns = max(int(n_steps), 1)
    if nodes_per_period % ns == 0:
        return ns
    divs = [d for d in range(1, nodes_per_period + 1) if nodes_per_period % d == 0]
    # nearest divisor; on a tie prefer the finer (larger) one
    return min(divs, key=lambda d: (abs(d - ns), -d))

# Domain ids (must match _DOMAIN_ID in the API rasterisation for consistency)
DOM_AIR     = 0
DOM_STATOR  = 1
DOM_COIL    = 2
DOM_AIRGAP  = 3
DOM_MAG_N   = 4   # N pole (generic, used for visualisation tag)
DOM_ROTOR   = 5
DOM_SHAFT   = 6
DOM_BAND    = 7   # motion / slip band inside the air gap (transient solver)
DOM_OUTER   = 8   # outer air ring (far-field boundary, beyond stator OD)
DOM_MAG_S   = 44  # S pole (generic, used for visualisation tag)

# Per-magnet domain IDs are allocated in [DOM_MAG_BASE, DOM_MAG_BASE + N_MAG).
# Each magnet gets its own tag so the FEM source term can apply that magnet's
# specific tangential M direction (M ∥ that magnet's bottom edge in the world
# frame, with polarity ±).  The high IDs stay clear of the small fixed-domain
# range above.
DOM_MAG_BASE  = 100
# Per-coil ids — each physical slot gets its own tag so the FEM assembly
# applies the correct (phase, direction) current density.  Without this
# every coil shared the same averaged J_z, which is ZERO for a balanced
# three-phase load → stator currents had no effect on the field.
DOM_COIL_BASE = 200


@dataclass
class FEMMaterial:
    name:  str
    mu_r:  float
    J_z:   float = 0.0   # [A/m²]  external current density
    Mx:    float = 0.0   # [A/m]   magnetization x-component
    My:    float = 0.0   # [A/m]   magnetization y-component
    # Optional measured B-H curve (list of (H_A_per_m, B_T) pairs).
    # When set, the non-linear Picard iteration uses it to derive μ_r(|B|)
    # at each iteration instead of the analytic Fröhlich roll-off.
    bh_curve: Optional[List[Tuple[float, float]]] = None


@dataclass
class FEMResult:
    """Sampled result on a regular Cartesian grid."""
    grid_size:   int
    extent:      Tuple[float, float, float, float]   # xmin xmax ymin ymax [m]
    A_z:         np.ndarray   # (gs, gs)
    B_x:         np.ndarray
    B_y:         np.ndarray
    B_mag:       np.ndarray
    J_z:         np.ndarray   # source J_z on grid
    domain:      np.ndarray   # int8, domain ids on grid
    n_triangles: int
    n_nodes:     int
    solve_time_s: float


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Build a triangle mesh from CadQuery Shapely polygons (using gmsh)
# ─────────────────────────────────────────────────────────────────────────────

def _fillet_polygon(poly, r_convex: float = 0.6, r_concave: float = 0.6):
    """Round sharp corners of a Shapely (Multi)Polygon with two buffer passes.

    Algorithm:
      1.  poly.buffer(+r_convex, join_style=round).buffer(-r_convex, join_style=round)
          → rounds the CONVEX corners of the exterior (and concave corners of
          interiors, which are convex from inside the iron).
      2.  poly.buffer(-r_concave, join_style=round).buffer(+r_concave, join_style=round)
          → rounds the CONCAVE corners of the exterior (slot-mouth inside
          corners, the ones the user circled in red).

    Equivalent to a Minkowski-sum + erosion pipeline that produces visually
    indistinguishable fillets up to the chord tolerance.
    """
    try:
        # join_style 1 = round (Shapely 2.x; some versions use the
        # enum JOIN_STYLE.round = 1).  Cap style is irrelevant for closed
        # polygons.
        p1 = poly.buffer(+r_convex,  join_style=1, mitre_limit=4).buffer(
                          -r_convex, join_style=1, mitre_limit=4)
        p2 = p1.buffer(  -r_concave, join_style=1, mitre_limit=4).buffer(
                         +r_concave, join_style=1, mitre_limit=4)
        if p2.is_valid and not p2.is_empty:
            return p2
    except Exception as e:
        log.warning("_fillet_polygon failed (%s) — keeping original", e)
    return poly


def _decimate_ring_by_angle(coords: list, min_turn_deg: float) -> list:
    """Remove vertices whose turn-angle is below `min_turn_deg` (near
    collinear), guarding against removing two adjacent vertices in the
    same pass so a fillet arc thins out evenly instead of collapsing.

    This is the polygon equivalent of Ansys "Normal Deviation": a larger
    angle keeps fewer points per arc → coarser mesh on bends; a smaller
    angle keeps every arc point → fine mesh on bends.
    """
    pts = list(coords)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n < 6 or min_turn_deg <= 0.0:
        return coords
    keep = [True] * n
    just_removed = False
    for i in range(n):
        if just_removed:                       # never drop adjacent pair
            just_removed = False
            continue
        p0 = pts[(i - 1) % n]; p1 = pts[i]; p2 = pts[(i + 1) % n]
        ax, ay = p1[0] - p0[0], p1[1] - p0[1]
        bx, by = p2[0] - p1[0], p2[1] - p1[1]
        la = math.hypot(ax, ay); lb = math.hypot(bx, by)
        if la < 1e-12 or lb < 1e-12:
            continue
        cos_t = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
        turn = math.degrees(math.acos(cos_t))   # 0° = perfectly straight
        if turn < min_turn_deg:
            keep[i] = False
            just_removed = True
    new = [pts[i] for i in range(n) if keep[i]]
    if len(new) < 3:
        return coords
    new.append(new[0])
    return new


def _decimate_poly_by_angle(geom, min_turn_deg: float):
    """Apply _decimate_ring_by_angle to every ring of a (Multi)Polygon."""
    from shapely.geometry import Polygon as _P, MultiPolygon as _MP
    if geom is None or geom.is_empty or min_turn_deg <= 0.0:
        return geom
    try:
        def _do(g):
            ext = _decimate_ring_by_angle(list(g.exterior.coords), min_turn_deg)
            ints = [_decimate_ring_by_angle(list(h.coords), min_turn_deg)
                    for h in g.interiors]
            q = _P(ext, ints)
            return q if q.is_valid else q.buffer(0)
        if hasattr(geom, "geoms"):
            return _MP([_do(g) for g in geom.geoms])
        return _do(geom)
    except Exception:
        return geom


def _simplify_polys(polys: dict, tol_mm: float = 0.005,
                     stator_fillet_mm: float = 0.8,
                     normal_dev_deg: float = 0.0,
                     n_slip: Optional[int] = None) -> dict:
    """Drop near-collinear vertices below chord tolerance `tol_mm`.

    Default 0.005 mm matches Ansys Maxwell's "Surface Deviation = 0.01 mm"
    — small enough that every fillet arc point (chord deviation ~0.01-0.05
    mm depending on radius) survives the cleanup. Long straight runs still
    collapse to two endpoints, so the mesh density is driven entirely by
    point clustering on curved boundaries.

    Parameters
    ----------
    tol_mm : float
        Chord-error (Surface Deviation) tolerance — distance-based
        Douglas-Peucker simplification.  Larger → coarser bends.
    stator_fillet_mm : float, default 0.8
        Round all sharp corners on the stator polygon with this radius
        (a Shapely buffer-out/in pipeline).  The CadQuery slot cutter only
        rounds ONE corner per slot pair; this post-pass adds fillets at the
        other slot-mouth corners (top-of-wedge + slot-bottom) so the mesh
        boundary matches the physical iron lamination.
    normal_dev_deg : float, default 0.0
        Angle-based (Normal Deviation) decimation threshold in degrees.
        Vertices whose turn-angle is below this are dropped, thinning out
        fillet arcs.  0 disables it (keeps all arc points).
    """
    # NOTE: deliberately do NOT simplify in_band / out_band here — they are
    # recomputed below from the FINAL (simplified + filleted) solids so
    # their shared boundaries match exactly.  Simplifying them independently
    # offset their edges from the rotor / stator edges and OCC fragment
    # turned the gaps into degenerate sliver "fan" triangles.
    SIMPLIFY_KEYS = ("stator", "rotor", "shaft", "air_gap")
    out = dict(polys)
    for k in SIMPLIFY_KEYS:
        if polys.get(k) is not None:
            try:
                g = polys[k].simplify(tol_mm, preserve_topology=True)
                g = _decimate_poly_by_angle(g, normal_dev_deg)
                out[k] = g
            except Exception:
                out[k] = polys[k]
    # ── Round all sharp slot-mouth corners on the stator ──
    if out.get("stator") is not None and stator_fillet_mm > 0:
        out["stator"] = _fillet_polygon(out["stator"],
                                         r_convex=stator_fillet_mm,
                                         r_concave=stator_fillet_mm)
    out["magnets"] = [
        ((_decimate_poly_by_angle(
              m.simplify(tol_mm, preserve_topology=True), normal_dev_deg)
          if m is not None else m), p)
        for m, p in polys.get("magnets", [])
    ]
    out["coils"] = list(polys.get("coils", []))

    # ── Rebuild in_band / out_band from the FINAL solids ──────────────────
    # in_band  = disk(mid_r)            − (rotor ∪ shaft ∪ magnets)
    # out_band = annulus(mid_r..r_out)  − stator
    # Sharing the exact simplified+filleted solid boundaries means OCC
    # fragment produces conforming edges with NO sliver triangles.
    if polys.get("in_band") is not None or polys.get("out_band") is not None:
        try:
            from shapely.geometry import Point as _Pt
            from shapely.ops import unary_union as _uu
            mid_r   = float(polys.get("mid_r_mm", 56.55))
            r_outer = float(polys.get("r_outer_boundary_mm",
                                      1.3 * max(math.hypot(x, y)
                                                for x, y in (out["stator"].exterior.coords
                                                if hasattr(out["stator"], "exterior")
                                                else out["stator"].geoms[0].exterior.coords))))
            rotor_solids = []
            if out.get("rotor") is not None: rotor_solids.append(out["rotor"])
            if out.get("shaft") is not None: rotor_solids.append(out["shaft"])
            rotor_solids += [m for m, _p in out["magnets"] if m is not None]
            # Build the mid_r slip circle from ONE explicit equally-spaced point
            # ring shared by BOTH in_band (exterior) and out_band (hole), so the
            # sliding-band transfinite mesh gets identical, matching nodes there
            # (rotor & stator differences don't touch mid_r, so it stays intact).
            from shapely.geometry import Polygon as _SPoly2
            _N = int(n_slip) if n_slip and n_slip > 0 else _N_SLIP
            mid_ring = [(mid_r * math.cos(2*math.pi*i/_N),
                         mid_r * math.sin(2*math.pi*i/_N)) for i in range(_N)]
            rout_ring = [(r_outer * math.cos(2*math.pi*i/_N),
                          r_outer * math.sin(2*math.pi*i/_N)) for i in range(_N)]
            in_band = _SPoly2(mid_ring)
            if rotor_solids:
                in_band = in_band.difference(_uu(rotor_solids))
            if not in_band.is_valid: in_band = in_band.buffer(0)
            out["in_band"] = in_band

            out_band = _SPoly2(rout_ring, [mid_ring])
            if out.get("stator") is not None:
                out_band = out_band.difference(out["stator"])
            if not out_band.is_valid: out_band = out_band.buffer(0)
            out["out_band"] = out_band
        except Exception as e:
            log.warning("in/out band rebuild after simplify failed: %s", e)
    return out


def _add_background_air(polys: dict, outer_air_factor: float = 1.0) -> dict:
    """Compute the explicit "background air" polygon and add it to `polys`.

    Background air  =  disk(r = stator_outer)  −  ∪(all material polygons).

    This catches every region NOT covered by an explicit material:
      • the thin rectangle in each rotor pocket between magnet top and rotor
        outer radius (was a hole in the rotor polygon, not filled by any
        magnet → previously meshed as "rotor" or left bare),
      • the air inside each stator slot around the wire bundle (slot was a
        hole in the stator polygon, only partly filled by the coil bundle
        → previously left bare or covered by overlay templates).

    With this air polygon registered as a separate gmsh OCC surface, the
    fragment operation produces a clean, NON-OVERLAPPING partition of the
    entire motor cross-section.  Every triangle then maps to exactly one
    source polygon, eliminating the "two meshes overlapping" artefacts.

    Parameters
    ----------
    outer_air_factor : float, default 1.0
        Multiplier for the outer disk radius.  Values > 1 add an outer air
        ring beyond the stator OD — this is where the Dirichlet A_z = 0
        far-field boundary condition is applied, instead of directly on
        the stator iron (which would suppress flux at the iron→air boundary).
        Tagged with DOM_OUTER so it can be rendered in a distinct color.
    """
    from shapely.geometry import Point as _Pt
    from shapely.ops import unary_union as _uu

    out = dict(polys)
    stator = polys.get("stator")
    if stator is None:
        return out

    try:
        # Outer envelope radius
        if hasattr(stator, "exterior"):
            r_max = max(math.hypot(x, y) for x, y in list(stator.exterior.coords))
        else:
            # MultiPolygon
            r_max = max(math.hypot(x, y)
                        for g in stator.geoms
                        for x, y in list(g.exterior.coords))
    except Exception:
        return out

    inner_disk = _Pt(0.0, 0.0).buffer(r_max + 1e-4, resolution=256)

    materials: list = []
    for k in ("stator", "rotor", "shaft", "air_gap", "airgap_band"):
        g = polys.get(k)
        if g is not None and not g.is_empty:
            materials.append(g)
    for mp, _pol in polys.get("magnets", []):
        if mp is not None and not mp.is_empty:
            materials.append(mp)
    for cp in polys.get("coils", []):
        if cp is not None and not cp.is_empty:
            materials.append(cp)

    if materials:
        try:
            mat_union = _uu(materials)
            air = inner_disk.difference(mat_union)
            if not air.is_empty:
                out["air_background"] = air
        except Exception as e:
            log.warning("background air computation failed: %s — falling back to disk", e)
            out["air_background"] = inner_disk
    else:
        out["air_background"] = inner_disk

    # ── Outer air ring (far-field boundary) ──────────────────────────────
    # Adds disk(R_far) − disk(R_stator) as DOM_OUTER, so the Dirichlet
    # A_z = 0 condition is applied on the artificial far-field boundary,
    # not on the iron.  Typical R_far / R_stator = 1.3–1.5 (Ansys default
    # is "Region Padding" ~25% which corresponds to factor 1.25).
    if outer_air_factor > 1.001:
        try:
            r_far = r_max * float(outer_air_factor)
            outer_disk = _Pt(0.0, 0.0).buffer(r_far, resolution=256)
            outer_ring = outer_disk.difference(inner_disk)
            if not outer_ring.is_empty:
                out["air_outer"] = outer_ring
        except Exception as e:
            log.warning("outer air ring construction failed: %s", e)

    return out


def _add_motion_band(polys: dict, motion_band: bool, band_thickness_mm: float = 0.4
                      ) -> dict:
    """Add a thin annular "motion band" inside the air gap.

    The band is a slip surface that lets the rotor sweep through angles
    without re-meshing the surrounding regions — the rotor-side air-gap
    rotates with the rotor; the band itself re-meshes cheaply per step;
    the stator-side air-gap is stationary.

    For static FEM the band is just an extra air ring; its real value
    surfaces in the transient solver (eddy-current / cogging vs time).

    The band is centred at the air-gap midline (r = (r_rotor_out +
    r_stator_in) / 2) with the requested thickness.  The original
    air_gap polygon is split into THREE rings:
        airgap_rotor_side  : rotor_out → band_inner
        airgap_band        : band_inner → band_outer      ← DOM_BAND
        airgap_stator_side : band_outer → stator_in
    """
    if not motion_band:
        return polys
    from shapely.geometry import Point as _Pt

    airgap = polys.get("air_gap")
    rotor  = polys.get("rotor")
    stator = polys.get("stator")
    if airgap is None or airgap.is_empty:
        return polys

    try:
        # Air-gap inner/outer radii
        ag_inner = min(math.hypot(x, y) for x, y in list(airgap.exterior.coords))
        ag_outer = max(math.hypot(x, y) for x, y in list(airgap.exterior.coords))
        for h in airgap.interiors:
            ag_inner = min(ag_inner, min(math.hypot(x, y) for x, y in list(h.coords)))
        # air_gap = annulus from rotor_out (= ag_inner via hole) to stator_in (= ag_outer via exterior)
        # In the cadquery construction air_gap = SPoly(_circle(inner_r), [_circle(rotor_or)]),
        # so exterior r ≈ stator_in (outer), interior r ≈ rotor_out (inner).
        r_band_center = 0.5 * (ag_inner + ag_outer)
        r_band_in  = r_band_center - 0.5 * band_thickness_mm
        r_band_out = r_band_center + 0.5 * band_thickness_mm
        band = _Pt(0, 0).buffer(r_band_out, resolution=256).difference(
               _Pt(0, 0).buffer(r_band_in,  resolution=256))
        if band.is_empty:
            return polys
        # Slice air_gap into THREE separate rings.  Previously we unioned
        # the rotor-side + stator-side back into a single 'air_gap'
        # polygon (so gmsh would treat them as one DoF region); for the
        # sliding-band solver they MUST be separate so we can rotate the
        # rotor-side as a rigid body without disturbing the stator-side
        # discretisation.
        out = dict(polys)
        out["airgap_band"] = band
        inner_band_disk = _Pt(0, 0).buffer(r_band_in, resolution=256)
        outer_band_disk = _Pt(0, 0).buffer(r_band_out, resolution=256)
        rotor_side  = airgap.intersection(inner_band_disk)   # ring rotor_out → band_in
        stator_side = airgap.difference(outer_band_disk)     # ring band_out → stator_in
        out["air_gap"]            = rotor_side.union(stator_side) \
            if (not rotor_side.is_empty and not stator_side.is_empty) \
            else (rotor_side if not rotor_side.is_empty else stator_side)
        # Separate refs the sliding-band path consumes (kept ADDITIONALLY
        # to 'air_gap' so the existing rebuild-per-frame solver still
        # works exactly as before — no behaviour change on main yet).
        out["air_gap_rotor_side"]  = rotor_side
        out["air_gap_stator_side"] = stator_side
        out["r_band_in"]           = r_band_in
        out["r_band_out"]          = r_band_out
        return out
    except Exception as e:
        log.warning("motion band construction failed: %s", e)
        return polys


def _clip_polys_to_sector(polys: dict, n_sectors: int) -> dict:
    """Clip every polygon to a 360°/n_sectors wedge starting at θ = 0.

    Used for symmetry reduction:  e.g.  n_sectors=4 with 24 slots + 28 poles
    gives  6 slots + 7 poles per sector  (since GCD(24, 28) = 4).
    Anti-periodic BC on the two radial cuts must be applied by the solver
    when the # of pole pairs per sector is fractional (7 poles → 3.5 pp).
    """
    if n_sectors <= 1:
        return polys
    from shapely.geometry import Polygon as _SPoly
    from shapely.geometry import MultiPolygon as _SMPoly

    sector_angle = 2 * math.pi / n_sectors
    # Big-enough wedge: extends to 10x the stator radius (always overshoots).
    R = 10_000.0
    n_arc = max(64, int(360 / n_sectors))
    pts = [(0.0, 0.0)]
    for i in range(n_arc + 1):
        a = sector_angle * i / n_arc
        pts.append((R * math.cos(a), R * math.sin(a)))
    wedge = _SPoly(pts)

    def _clip(g):
        if g is None or g.is_empty:
            return g
        try:
            return g.intersection(wedge)
        except Exception:
            return g

    out = dict(polys)
    for k in ("stator", "rotor", "shaft", "air_gap", "airgap_band",
              "air_background", "air_outer"):
        if polys.get(k) is not None:
            out[k] = _clip(polys[k])
    out["magnets"] = []
    for mp, pol in polys.get("magnets", []):
        clipped = _clip(mp)
        if clipped is not None and not clipped.is_empty and clipped.area > 1e-6:
            # An intersection may be a MultiPolygon: keep all parts with same polarity
            if isinstance(clipped, _SMPoly):
                for g in clipped.geoms:
                    if g.area > 1e-6:
                        out["magnets"].append((g, pol))
            else:
                out["magnets"].append((clipped, pol))
    out["coils"] = []
    for cp in polys.get("coils", []):
        clipped = _clip(cp)
        if clipped is not None and not clipped.is_empty and clipped.area > 1e-6:
            if isinstance(clipped, _SMPoly):
                for g in clipped.geoms:
                    if g.area > 1e-6:
                        out["coils"].append(g)
            else:
                out["coils"].append(clipped)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Periodic coil meshing — one wire → all wires → all coils
# ─────────────────────────────────────────────────────────────────────────────

def _split_polys_for_sliding_band(polys: dict) -> Tuple[dict, dict]:
    """Split a polys dict into stator-side and rotor-side halves using the
    first-class in_band / out_band air domains from get_2d_polygons.

    Sliding-band FEM meshes each half ONCE; the rotor half is then
    rotated rigidly per transient frame, and the master-slave coupling
    at the slip surface (the shared mid_r circle) glues them together.

    Rotor side  (rigidly rotates with the rotor):
        shaft, rotor, magnets, in_band
        in_band = full disk r=0..mid_r MINUS rotor + magnets + shaft —
        captures the shaft bore, inter-magnet flux-barrier pockets and
        the inner half of the air gap.
    Stator side (stationary in the lab frame):
        stator, coils, out_band
        out_band = annulus mid_r..r_outer MINUS stator + coils —
        captures the outer half of the air gap, the slot-opening air and
        the outer ambient air up to the far-field boundary r_outer.

    The two halves meet at the slip surface r=mid_r.  Together with the
    iron / copper / magnet domains they tile the whole cross-section, so
    NO background-air or motion-band post-processing is needed.
    """
    STATOR_KEYS = ("stator", "coils", "out_band")
    ROTOR_KEYS  = ("shaft", "rotor", "magnets", "in_band")

    polys_s: dict = {}
    polys_r: dict = {}
    for k in STATOR_KEYS:
        v = polys.get(k)
        if v is not None:
            polys_s[k] = v
    for k in ROTOR_KEYS:
        v = polys.get(k)
        if v is not None:
            polys_r[k] = v
    # Carry the slip-surface radius through to both halves.
    if polys.get("mid_r_mm") is not None:
        polys_s["mid_r_mm"] = polys["mid_r_mm"]
        polys_r["mid_r_mm"] = polys["mid_r_mm"]
    return polys_s, polys_r


def _build_sliding_band_meshes(
        polys: dict,
        rotor_angle_deg: float,
        mesh_size_mm: float,
        min_size_mm: float,
        outer_air_factor: float,
        band_thickness_mm: float,
        n_sectors: int,
        geo_cfg: dict,
        normal_deviation_deg: float = 6.0,
        aspect_ratio: float = 10.0,
):
    """Build the stator-half and rotor-half meshes for the sliding-band solver.

    Strategy:
      1) Split the polys dict into stator + rotor halves (SB-5a).
      2) Build each half as an independent gmsh mesh (single gmsh call
         per half — uses the existing build_mesh_from_polygons path).
      3) Rotate the ROTOR half's node coordinates by rotor_angle_deg as
         a rigid body — the rotor mesh's topology (cells) stays the
         same across all transient frames.

    Returns
    -------
    (mesh_s, tags_s, classify_s, mesh_r, tags_r, classify_r) where
    mesh_* are skfem MeshTri objects, tags_* are per-cell domain ids,
    classify_* are the helper returned by build_mesh_from_polygons.
    """
    polys_s, polys_r = _split_polys_for_sliding_band(polys)

    # Slip-ring radius (mm) — force BOTH halves to keep an identical,
    # equally-spaced node ring there (transfinite) so they merge by node
    # identity when the rotor is rotated by an integer node step.
    _slip_r = polys.get("mid_r_mm")
    if _slip_r is None and geo_cfg:
        _ro = float(geo_cfg.get("rotor_outer_radius", 0.0))
        _si = float(geo_cfg.get("stator_inner_radius", 0.0))
        _slip_r = 0.5 * (_ro + _si) if (_ro > 0 and _si > _ro) else None

    # Map the air domains onto the keys the mesh builder recognises:
    #   rotor half:  in_band  → "air_gap"   (DOM_AIRGAP)
    #   stator half: out_band → "air_outer" (DOM_OUTER, far-field air)
    # The far-field Dirichlet boundary lives on out_band's outer edge.
    # No motion_band / background-air post-processing — in_band and
    # out_band already tile every air region, so we pass
    # add_background_air=False to keep the OCC partition clean.
    polys_s_for_mesh = dict(polys_s)
    polys_r_for_mesh = dict(polys_r)
    if "in_band" in polys_r_for_mesh:
        polys_r_for_mesh["air_gap"] = polys_r_for_mesh.pop("in_band")
    if "out_band" in polys_s_for_mesh:
        polys_s_for_mesh["air_outer"] = polys_s_for_mesh.pop("out_band")

    # Build stator half at the FIXED lab position (rotor_angle_deg ignored
    # for stator-side polygons, which are stationary).  Stator stays
    # sector-clipped to the requested n_sectors — it never rotates, so
    # the sector wedge accurately represents the symmetry-reduced domain.
    mesh_s, tags_s, classify_s = build_mesh_from_polygons(
        polys_s_for_mesh, rotor_angle_deg=0.0,
        mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
        normal_deviation_deg=normal_deviation_deg, aspect_ratio=aspect_ratio,
        outer_air_factor=outer_air_factor,
        motion_band=False, band_thickness_mm=band_thickness_mm,
        n_sectors=n_sectors, geo_cfg=geo_cfg,
        add_background_air=False, slip_transfinite_r=_slip_r,
    )

    # Build rotor half with the SAME sector clip as the stator
    # (n_sectors).  The rotor mesh covers ONE sector (1/n_sectors of the
    # disk) at the un-rotated zero position; rigid rotation by
    # rotor_angle_deg slides this wedge inside the stator sector.  The
    # rotor body + magnets + shaft + in_band air all live in ONE mesh so
    # they rotate together as a single rigid unit per transient frame.
    # Past the sector edge the wedge wraps via anti-periodic BC (handled
    # later by the solver / master-slave pair).
    mesh_r, tags_r, classify_r = build_mesh_from_polygons(
        polys_r_for_mesh, rotor_angle_deg=0.0,
        mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
        normal_deviation_deg=normal_deviation_deg, aspect_ratio=aspect_ratio,
        outer_air_factor=outer_air_factor,
        motion_band=False, band_thickness_mm=band_thickness_mm,
        n_sectors=n_sectors, geo_cfg=geo_cfg,
        add_background_air=False, slip_transfinite_r=_slip_r,
    )

    # Apply rotor rotation as a rigid body — node coords only, topology
    # unchanged.  This is the heart of sliding-band: every frame just
    # transforms the rotor mesh's points instead of remeshing.
    if abs(rotor_angle_deg) > 1e-9:
        mesh_r = type(mesh_r)(_rotate_mesh_points(mesh_r.p, rotor_angle_deg),
                               mesh_r.t)

    return mesh_s, tags_s, classify_s, mesh_r, tags_r, classify_r


def _find_ring_nodes(mesh, r_target_m: float, tol_m: float = 1e-5
                      ) -> np.ndarray:
    """Return indices of mesh nodes that lie within `tol_m` of radius
    r_target_m (in metres).  Used by sliding-band to extract the band-
    interface nodes that need master-slave coupling.

    The mesh.p array is in METRES (skfem convention), so r_target_m
    should also be in metres.
    """
    r = np.hypot(mesh.p[0], mesh.p[1])
    idx = np.where(np.abs(r - r_target_m) < tol_m)[0]
    return idx


def _band_master_slave_pairing(
        band_node_angles_deg: np.ndarray,
        master_node_angles_deg: np.ndarray,
        rotor_angle_deg: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build the linear-interpolation master-slave map for one side of
    the sliding band.

    Parameters
    ----------
    band_node_angles_deg :
        Angular positions (in DEGREES, mod 360) of the band-side nodes
        that will be constrained.
    master_node_angles_deg :
        Angular positions of the OTHER mesh's interface nodes — these
        are the masters whose A_z values drive the band slaves.
        Must be SORTED in ascending order (we wrap with mod 360).
    rotor_angle_deg :
        Δθ applied to the master ring before pairing.  When this is the
        rotor↔band edge: pass the current rotor_angle so that the
        rotor's outer-edge nodes are seen at their CURRENT lab angle.
        When this is the stator↔band edge: pass 0.

    Returns
    -------
    master_j        : (n_band,) index into master_node_angles_deg of the
                       LEFT neighbour for each band node
    master_jp1      : (n_band,) index of the RIGHT neighbour (wraps)
    weights         : (n_band,) interpolation weight α so that
                       A_z[band_i] = (1-α) · A_z[master_j] + α · A_z[master_jp1]

    Notes
    -----
    • angle wrapping is handled in [0, 360°) — pairing across the 0/360
      seam uses the standard modulo trick.
    • when band and master node counts AND positions match exactly
      (degenerate case at rotor_angle = 0 with identical n_arc), α
      degenerates to 0 and every band node snaps to a unique master.
    """
    band   = np.mod(np.asarray(band_node_angles_deg,   dtype=float), 360.0)
    master = np.mod(np.asarray(master_node_angles_deg, dtype=float) + rotor_angle_deg, 360.0)
    n_m = master.size
    # Sort master once + ALSO carry the original index so we can map
    # back into the master_node_angles array after np.searchsorted.
    sort_order = np.argsort(master)
    master_sorted = master[sort_order]
    # For each band angle, find which segment of the sorted master ring
    # it falls in (we use np.searchsorted with side='right' so equal-
    # angle ties prefer the LEFT master, matching the (1-α) convention).
    pos = np.searchsorted(master_sorted, band, side='right') - 1
    # Wrap: if band lies in the gap that crosses 0°/360°, pos == -1 → use
    # the LAST master as the left neighbour.
    pos[pos < 0] = n_m - 1
    j_left  = sort_order[pos]
    j_right = sort_order[(pos + 1) % n_m]
    # Interpolation weight α along the arc from left → right master
    a_l = master_sorted[pos]
    a_r = master_sorted[(pos + 1) % n_m]
    # Compute the angular gap from left → band and left → right, both
    # taken in the CCW direction (i.e. the positive arc length mod 360).
    d_lb = (band - a_l) % 360.0
    d_lr = (a_r  - a_l) % 360.0
    # Guard against zero-length segments (both masters at same angle)
    alpha = np.where(d_lr > 1e-9, d_lb / d_lr, 0.0)
    return j_left, j_right, alpha


def _rotate_mesh_points(points: np.ndarray, angle_deg: float,
                          cx: float = 0.0, cy: float = 0.0) -> np.ndarray:
    """Rotate a (2, N) array of 2-D points by angle_deg around (cx, cy).

    Used by the sliding-band solver to take the rotor mesh through one
    time step: the topology (mesh.t) stays the same — only node
    coordinates update.  This keeps the FEM matrix sparse pattern
    intact and lets us re-use cached stator + rotor stiffness blocks
    while only rebuilding the band coupling each step.
    """
    c = math.cos(math.radians(angle_deg))
    s = math.sin(math.radians(angle_deg))
    R = np.array([[c, -s], [s, c]], dtype=points.dtype)
    return R @ (points - np.array([[cx], [cy]])) + np.array([[cx], [cy]])


def _structured_band_mesh(r_in_mm: float, r_out_mm: float,
                            n_arc: int,
                            theta_start: float = 0.0,
                            theta_end:   float = 2 * math.pi,
                          ) -> Tuple[np.ndarray, np.ndarray,
                                     np.ndarray, np.ndarray]:
    """Structured triangular mesh of the thin annulus  r_in ≤ r ≤ r_out.

    Returns
    -------
    verts          : (2, 2·n_arc)   inner ring nodes first, then outer ring
    tris           : (3, 2·(n_arc-1))
    inner_ring_idx : (n_arc,) node indices on the inner circle (r = r_in)
    outer_ring_idx : (n_arc,) node indices on the outer circle (r = r_out)

    The sliding-band master-slave constraint links inner_ring_idx (which
    moves with the rotor) to the rotor mesh's outer boundary, and
    outer_ring_idx (stationary) to the stator mesh's inner boundary.
    Per transient time step the rotor mesh node coordinates are updated
    via a rigid rotation; the band topology stays untouched, so the
    master-slave pairing only needs to be re-evaluated at the band's
    interfaces — not the whole problem.

    `n_arc` is the number of vertices along the arc; the band sweep is
    `theta_end − theta_start` in radians (full circle by default; pass a
    sector arc for symmetry-reduced runs).
    """
    n = max(8, int(n_arc))
    thetas = np.linspace(theta_start, theta_end, n)
    # Inner ring vertices first (indices 0..n-1), outer ring next (n..2n-1).
    inner_x = r_in_mm  * np.cos(thetas)
    inner_y = r_in_mm  * np.sin(thetas)
    outer_x = r_out_mm * np.cos(thetas)
    outer_y = r_out_mm * np.sin(thetas)
    verts = np.column_stack([
        np.concatenate([inner_x, outer_x]),
        np.concatenate([inner_y, outer_y]),
    ]).T
    # Triangulate each quad (i, i+1, n+i+1, n+i) into two triangles.
    tris: List[List[int]] = []
    for i in range(n - 1):
        a, b = i,     i + 1
        c, d = n + i, n + i + 1
        # alternating diagonal for nicer aspect ratio
        if i % 2 == 0:
            tris.append([a, b, d]); tris.append([a, d, c])
        else:
            tris.append([a, b, c]); tris.append([b, d, c])
    inner_ring_idx = np.arange(0, n)
    outer_ring_idx = np.arange(n, 2 * n)
    return verts, np.array(tris, dtype=np.int64).T, inner_ring_idx, outer_ring_idx


def _structured_rect_mesh(w_mm: float, h_mm: float, target_mm: float
                          ) -> Tuple[np.ndarray, np.ndarray]:
    """Structured triangular mesh of (0,0)→(w,h). Returns (verts (2,N), tris (3,M))."""
    nx = max(2, int(round(w_mm / max(target_mm, 0.05))) + 1)
    ny = max(2, int(round(h_mm / max(target_mm, 0.05))) + 1)
    xs = np.linspace(0.0, w_mm, nx)
    ys = np.linspace(0.0, h_mm, ny)
    XX, YY = np.meshgrid(xs, ys)
    verts = np.column_stack([XX.ravel(), YY.ravel()]).T   # (2, nx*ny)
    tris: List[List[int]] = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i
            b = a + 1
            c = a + nx
            d = c + 1
            # Two triangles per cell, alternating diagonal for nicer quality
            if (i + j) % 2 == 0:
                tris.append([a, b, d]); tris.append([a, d, c])
            else:
                tris.append([a, b, c]); tris.append([b, d, c])
    return verts, np.array(tris, dtype=np.int64).T


def _mesh_single_polygon(poly, mesh_size_mm: float, min_size_mm: float
                          ) -> Tuple[np.ndarray, np.ndarray]:
    """Mesh ONE Shapely polygon via gmsh. Returns (verts (2,N) in mm, tris (3,M))."""
    import gmsh

    # gmsh is process-global and NOT thread-safe — serialise all of it
    # under a single RLock to avoid 'Gmsh has not been initialized' errors
    # when the FastAPI threadpool runs two FEM solves in parallel.
    _GMSH_LOCK.acquire()
    try:
        gmsh.initialize([], interruptible=False)
    except TypeError:
        gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.MeshSizeMin", min_size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 60)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 1)
        gmsh.option.setNumber("Mesh.Algorithm", 6)
        gmsh.model.add("single_poly")
        occ = gmsh.model.occ

        ext = list(poly.exterior.coords)[:-1]
        # Drop consecutive near-duplicate points
        clean: List[Tuple[float, float]] = []
        for x, y in ext:
            if not clean or (abs(x - clean[-1][0]) > 1e-3 or abs(y - clean[-1][1]) > 1e-3):
                clean.append((x, y))
        if len(clean) >= 2 and abs(clean[0][0] - clean[-1][0]) < 1e-3 \
                and abs(clean[0][1] - clean[-1][1]) < 1e-3:
            clean.pop()
        if len(clean) < 3:
            return np.empty((2, 0)), np.empty((3, 0), dtype=np.int64)

        pt_tags = [occ.addPoint(x, y, 0) for x, y in clean]
        line_tags = []
        for i in range(len(pt_tags)):
            a = pt_tags[i]; b = pt_tags[(i + 1) % len(pt_tags)]
            if a != b:
                line_tags.append(occ.addLine(a, b))
        loop = occ.addCurveLoop(line_tags)
        surf = occ.addPlaneSurface([loop])
        occ.synchronize()
        gmsh.model.mesh.generate(2)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        node_id_to_idx = {int(t): i for i, t in enumerate(node_tags)}
        points = np.array(node_coords, dtype=float).reshape(-1, 3)[:, :2]

        el_types, _, node_lists = gmsh.model.mesh.getElements(2, surf)
        tris: List[Tuple[int, int, int]] = []
        for et_idx, et in enumerate(el_types):
            if int(et) != 2:
                continue
            flat = node_lists[et_idx]
            for k in range(len(flat) // 3):
                a = node_id_to_idx[int(flat[3 * k + 0])]
                b = node_id_to_idx[int(flat[3 * k + 1])]
                c = node_id_to_idx[int(flat[3 * k + 2])]
                tris.append((a, b, c))
    finally:
        try:
            gmsh.finalize()
        except Exception:
            pass
        _GMSH_LOCK.release()

    return points.T, np.array(tris, dtype=np.int64).T


def build_periodic_magnet_mesh(polys: dict, geo_cfg: dict, rotor_angle_deg: float,
                                 mesh_size_mm: float, min_size_mm: float,
                                 ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build deterministic mesh for ALL magnets by template rotation.

    Strategy:
      1. Take magnet #0 polygon (already at its position) and de-rotate to
         angle = 0  (canonical center on +X axis)
      2. Mesh it ONCE with gmsh
      3. Rotate the resulting (verts, tris) to each of `num_poles` positions
      4. Tag every triangle with DOM_MAG_N or DOM_MAG_S by polarity

    Returns (verts_m (2,N), tris (3,M), cell_tags (M,))
    """
    from shapely.affinity import rotate as _shapely_rotate

    magnet_list = polys.get("magnets", [])
    num_poles = len(magnet_list)
    if num_poles == 0:
        return np.empty((2, 0)), np.empty((3, 0), dtype=np.int64), np.array([], dtype=np.int16)

    pole_pitch_deg = 360.0 / num_poles
    # Magnet #0 is centered at (0 + 0.5) * pole_pitch_deg + rotor_angle_deg
    first_angle_deg = 0.5 * pole_pitch_deg + rotor_angle_deg
    first_poly, _ = magnet_list[0]

    # De-rotate to canonical angle 0. Inflate slightly so the rendered template
    # always covers the gmsh-rotor's discretisation of the same boundary (the
    # two independent gmsh runs produce slightly different boundary vertices,
    # leaving thin "cracks" without this buffer).
    canonical_exact = _shapely_rotate(first_poly, -first_angle_deg, origin=(0.0, 0.0))
    try:
        canonical = canonical_exact.buffer(0.05, join_style=2)  # 0.05 mm outward
    except Exception:
        canonical = canonical_exact

    v_tpl_mm, t_tpl = _mesh_single_polygon(canonical, mesh_size_mm, min_size_mm)
    if v_tpl_mm.shape[1] == 0:
        return np.empty((2, 0)), np.empty((3, 0), dtype=np.int64), np.array([], dtype=np.int16)

    n_per = v_tpl_mm.shape[1]
    all_v: List[np.ndarray] = []
    all_t: List[np.ndarray] = []
    all_tags: List[int] = []
    offset = 0
    for i, (_, polarity) in enumerate(magnet_list):
        ang = math.radians((i + 0.5) * pole_pitch_deg + rotor_angle_deg)
        c, s = math.cos(ang), math.sin(ang)
        rx = v_tpl_mm[0] * c - v_tpl_mm[1] * s
        ry = v_tpl_mm[0] * s + v_tpl_mm[1] * c
        all_v.append(np.vstack([rx, ry]))
        all_t.append(t_tpl + offset)
        dom = DOM_MAG_N if polarity > 0 else DOM_MAG_S
        all_tags.extend([dom] * t_tpl.shape[1])
        offset += n_per

    verts_mm = np.hstack(all_v)
    tris = np.hstack(all_t)
    tags = np.array(all_tags, dtype=np.int16)
    return verts_mm * 1e-3, tris, tags


def build_periodic_coil_mesh(geo_cfg: dict, num_slots: int,
                              outer_r_mm: float, wire_target_mm: float = 0.2,
                              ) -> Tuple[np.ndarray, np.ndarray]:
    """Build a deterministic mesh of ALL copper wires by template replication.

    Strategy (user-requested):
      1. Mesh ONE wire (template) at a canonical position
      2. Translate to every wire position inside ONE slot pair (14 × 2 = 28 wires)
      3. Rotate that slot's wire block to every slot angular position
      4. Concatenate everything → deterministic, identical mesh for every wire

    Returns (verts_m (2,N), tris (3,M)) — coordinates in metres ready for
    appending to the global mesh.
    """
    wire_w  = float(geo_cfg.get("wire_width", 5.0))
    wire_h  = float(geo_cfg.get("wire_height", 0.6))
    wire_dx = float(geo_cfg.get("wire_spacing_x", 0.1))
    wire_dy = float(geo_cfg.get("wire_spacing_y", 0.13))
    n_wires = int(geo_cfg.get("num_wires_per_slot", 14))
    ins_w   = float(geo_cfg.get("insulation_thickness", 0.15))
    tooth_w = float(geo_cfg.get("tooth_width", 9.2))
    core_h  = float(geo_cfg.get("core_thickness", 4.2))

    # Match cadquery_geometry._create_coils geometry exactly
    right_x = tooth_w / 2.0 + ins_w + wire_dx / 2.0
    top_y   = outer_r_mm - core_h - ins_w - wire_dy / 2.0
    half_slots = num_slots // 2
    slot_pitch = 2 * math.pi / half_slots

    # Slight buffer (0.03 mm on all sides) so the wire rectangle visually
    # overlaps the surrounding slot-air mesh, covering any boundary cracks
    # between the template's discretisation and gmsh's discretisation.
    BUF = 0.03
    tpl_v, tpl_t = _structured_rect_mesh(wire_w + 2 * BUF, wire_h + 2 * BUF, wire_target_mm)
    tpl_v -= np.array([[BUF], [BUF]])    # shift so origin still at (0,0)
    n_per_wire = tpl_v.shape[1]

    # Each wire's (origin x, origin y) inside the slot at angle=0
    wire_origins: List[Tuple[float, float]] = []
    for step in range(n_wires):
        y_top = top_y - step * (wire_h + wire_dy)
        y_bot = y_top - wire_h
        # right side of slot
        wire_origins.append((right_x, y_bot))
        # left side of slot (mirror)
        wire_origins.append((-right_x - wire_w, y_bot))

    all_v_chunks: List[np.ndarray] = []
    all_t_chunks: List[np.ndarray] = []
    offset = 0
    for slot_i in range(half_slots):
        ang = slot_i * slot_pitch
        c_ang, s_ang = math.cos(ang), math.sin(ang)
        for ox, oy in wire_origins:
            # Translate template
            vx = tpl_v[0] + ox
            vy = tpl_v[1] + oy
            # Rotate by slot angle
            rx = vx * c_ang - vy * s_ang
            ry = vx * s_ang + vy * c_ang
            all_v_chunks.append(np.vstack([rx, ry]))
            all_t_chunks.append(tpl_t + offset)
            offset += n_per_wire

    verts_mm = np.hstack(all_v_chunks)
    tris     = np.hstack(all_t_chunks)
    return verts_mm * 1e-3, tris   # mm → m


def build_mesh_from_polygons(polys: dict,
                             rotor_angle_deg: float = 0.0,
                             mesh_size_mm: float = 1.5,
                             min_size_mm: float = 0.3,
                             normal_deviation_deg: float = 6.0,
                             aspect_ratio: float = 10.0,
                             periodic_coils: bool = False,
                             geo_cfg: Optional[dict] = None,
                             outer_air_factor: float = 1.0,
                             motion_band: bool = False,
                             band_thickness_mm: float = 0.4,
                             n_sectors: int = 1,
                             add_background_air: bool = True,
                             slip_transfinite_r: Optional[float] = None,
                             ) -> Tuple["MeshTri", np.ndarray]:
    """Construct a conforming triangle mesh from the CadQuery polygon dict.

    Uses gmsh OCC kernel + boolean fragments so that overlapping surfaces
    (magnets sitting in rotor pockets, coils in stator slots) get clean
    conforming interfaces automatically.

    Returns
    -------
    mesh        : scikit-fem MeshTri
    cell_tags   : (n_triangles,) int8 array of domain ids
    """
    import gmsh
    from skfem.io.meshio import from_meshio
    import meshio as _mio
    from shapely.geometry import Polygon as SPoly, MultiPolygon as SMPoly

    # interruptible=False skips signal handler install (only main-thread-safe)
    _GMSH_LOCK.acquire()
    try:
        gmsh.initialize([], interruptible=False)
    except TypeError:
        gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        # Ansys-style "Curved Surface Meshing":
        #   - polygon vertex density is the refinement signal (dense points on
        #     fillet arcs → fine mesh there, sparse on straight runs → coarse)
        #   - MeshSizeFromCurvature ≈ 60 means ~6° per segment (= Ansys Normal
        #     Deviation = 3° doubled to keep triangle count reasonable)
        #   - MeshSizeMin bounds the minimum element so a 0.5 mm fillet doesn't
        #     spawn thousands of triangles
        gmsh.option.setNumber("Mesh.MeshSizeMin", min_size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)
        # 360° / normal_deviation = points per 2π
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature",
                               max(8, 360.0 / max(0.5, normal_deviation_deg)))
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 1)
        gmsh.option.setNumber("Mesh.AnisoMax", aspect_ratio)
        gmsh.option.setNumber("Mesh.CharacteristicLengthFactor", 1.0)
        gmsh.option.setNumber("Mesh.Algorithm", 6)
        gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
        gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-3)
        gmsh.model.add("motor2d")
        occ = gmsh.model.occ

        def _dedupe(pts: List[Tuple[float, float]], tol: float = 1e-3) -> List[Tuple[float, float]]:
            """Drop consecutive points closer than `tol` (mm). Keeps the loop closed implicitly."""
            out: List[Tuple[float, float]] = []
            for p in pts:
                if not out or (abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol):
                    out.append(p)
            # Also check closing edge
            if len(out) >= 2 and abs(out[0][0] - out[-1][0]) < tol and abs(out[0][1] - out[-1][1]) < tol:
                out.pop()
            return out

        def _shapely_to_occ(geom) -> List[int]:
            """Build OCC plane surfaces from a Shapely (Multi)Polygon.
            Uses addPolyline-like construction via dedup'd point loops.
            Returns the list of OCC surface tags created."""
            if geom is None or geom.is_empty:
                return []
            # A too-aggressive simplify / buffer can degenerate a thin
            # polygon into a GeometryCollection (polygons + stray
            # LineStrings/Points).  Flatten ANY geometry down to its polygon
            # parts only — the leftover 1-D bits are not surfaces and have no
            # `.exterior` (the crash that blanked the Mesh view).
            def _polys_only(gm) -> List:
                if gm is None or gm.is_empty:
                    return []
                if gm.geom_type == "Polygon":
                    return [gm]
                if hasattr(gm, "geoms"):  # Multi* / GeometryCollection
                    out: List = []
                    for sub in gm.geoms:
                        out.extend(_polys_only(sub))
                    return out
                return []  # LineString / Point → not a surface
            geoms = _polys_only(geom)
            tags: List[int] = []
            for g in geoms:
                if g.is_empty or g.area < 1e-6:
                    continue
                ext = _dedupe(list(g.exterior.coords)[:-1])
                if len(ext) < 3:
                    continue
                pt_tags = [occ.addPoint(x, y, 0) for x, y in ext]
                line_tags = []
                for i in range(len(pt_tags)):
                    a = pt_tags[i]; b = pt_tags[(i + 1) % len(pt_tags)]
                    if a == b:
                        continue
                    try:
                        line_tags.append(occ.addLine(a, b))
                    except Exception:
                        # Two points at identical location — skip
                        continue
                if len(line_tags) < 3:
                    continue
                outer_wire = occ.addCurveLoop(line_tags)
                hole_wires: List[int] = []
                for hole in g.interiors:
                    hext = _dedupe(list(hole.coords)[:-1])
                    if len(hext) < 3:
                        continue
                    hpts = [occ.addPoint(x, y, 0) for x, y in hext]
                    hlines = []
                    for i in range(len(hpts)):
                        a = hpts[i]; b = hpts[(i + 1) % len(hpts)]
                        if a == b:
                            continue
                        try:
                            hlines.append(occ.addLine(a, b))
                        except Exception:
                            continue
                    if len(hlines) >= 3:
                        hole_wires.append(occ.addCurveLoop(hlines))
                try:
                    surf = occ.addPlaneSurface([outer_wire, *hole_wires])
                    tags.append(surf)
                except Exception as e:
                    log.warning("addPlaneSurface failed: %s", e)
            return tags

        # 1) Air domains.  Prefer the first-class in_band / out_band air
        #    domains from get_2d_polygons — they tile EVERY air region
        #    cleanly (inner pockets + full gap + outer far-field) so the
        #    whole motor uses ONE geometry in the Mesh tab and the
        #    Simulation field view.  The legacy motion-band path split the
        #    air gap with a 0.4 mm sliver ring that gmsh meshed into
        #    degenerate fan triangles ("trash") — avoided entirely here.
        if polys.get("in_band") is not None and polys.get("out_band") is not None:
            polys = dict(polys)
            polys["air_gap"]   = polys.pop("in_band")    # full inner air → DOM_AIRGAP
            polys["air_outer"] = polys.pop("out_band")   # outer air to far-field → DOM_OUTER
            polys.pop("airgap_band", None)
            polys.pop("air_background", None)
        else:
            # Legacy path (no in/out bands available).
            polys = _add_motion_band(polys, motion_band=motion_band,
                                      band_thickness_mm=band_thickness_mm)
            if add_background_air:
                polys = _add_background_air(polys, outer_air_factor=outer_air_factor)
        # 3) Symmetry: clip ALL polygons to a 360°/n_sectors wedge
        if n_sectors > 1:
            polys = _clip_polys_to_sector(polys, n_sectors=n_sectors)

        # Build OCC surfaces, keeping track of which dom each surface represents.
        # We list them in order from outer to inner; OCC `fragment` will produce
        # the boolean partition where smaller surfaces "punch out" the larger
        # ones automatically.
        domain_surfaces: List[Tuple[int, int]] = []   # (surf_tag, domain_id)

        for surf in _shapely_to_occ(polys.get("air_outer")):
            domain_surfaces.append((surf, DOM_OUTER))
        for surf in _shapely_to_occ(polys.get("air_background")):
            domain_surfaces.append((surf, DOM_AIR))
        for surf in _shapely_to_occ(polys.get("stator")):
            domain_surfaces.append((surf, DOM_STATOR))
        for surf in _shapely_to_occ(polys.get("rotor")):
            domain_surfaces.append((surf, DOM_ROTOR))
        for surf in _shapely_to_occ(polys.get("shaft")):
            domain_surfaces.append((surf, DOM_SHAFT))
        for surf in _shapely_to_occ(polys.get("air_gap")):
            domain_surfaces.append((surf, DOM_AIRGAP))
        for surf in _shapely_to_occ(polys.get("airgap_band")):
            domain_surfaces.append((surf, DOM_BAND))
        # Each magnet gets a UNIQUE domain id (DOM_MAG_BASE + i).  Polarity
        # is recovered by the materials builder via polys["magnets"][i][1].
        for i, (mag_poly, _polarity) in enumerate(polys.get("magnets", [])):
            for surf in _shapely_to_occ(mag_poly):
                domain_surfaces.append((surf, DOM_MAG_BASE + i))
        # Each coil polygon gets a UNIQUE per-slot id (DOM_COIL_BASE + i),
        # so build_materials can assign the right (phase, sign) current.
        for i, coil_poly in enumerate(polys.get("coils", [])):
            for surf in _shapely_to_occ(coil_poly):
                domain_surfaces.append((surf, DOM_COIL_BASE + i))

        occ.synchronize()

        # Capture each surface's centroid BEFORE fragmenting so we can re-classify
        # fragments by point-in-polygon vs the originals.
        original_centroids: List[Tuple[Tuple[float, float], int]] = []
        for surf, dom_id in domain_surfaces:
            try:
                com = occ.getCenterOfMass(2, surf)
                original_centroids.append(((com[0], com[1]), dom_id))
            except Exception:
                pass

        # Boolean fragment — produces a clean non-overlapping partition.
        # outDimTagsMap[i] = list of output (dim,tag) created from input i.
        dim_tags = [(2, s) for s, _ in domain_surfaces]
        try:
            fragment_out, out_map = occ.fragment(dim_tags, [])
        except Exception as e:
            log.warning("OCC fragment failed (%s) — falling back to original surfaces", e)
            fragment_out = dim_tags
            out_map = [[dt] for dt in dim_tags]

        occ.synchronize()

        # Classify each fragment: first by polygon membership for the small
        # features (coils, magnets), then fall back to radial annulus for the
        # large bulk (shaft, rotor, airgap, stator). This avoids issues with
        # the rotor/stator polygons having very wavy exteriors after slot/
        # magnet-pocket subtraction.
        from shapely.geometry import Point as _Pt
        import math as _m

        # Radial bounds (mm — same units as polygon coords). Handles both
        # single Polygon and MultiPolygon (after sector clip).
        def _iter_geoms(g):
            # Polygon parts only — a GeometryCollection from an aggressive
            # simplify can hold LineStrings/Points that have no `.exterior`.
            if g is None or g.is_empty:
                return []
            if hasattr(g, "geoms"):  # Multi* / GeometryCollection
                return [sub for sub in g.geoms
                        if not sub.is_empty and sub.geom_type == "Polygon"]
            return [g] if g.geom_type == "Polygon" else []

        def _all_ext_r(g):
            return [_m.hypot(x, y) for sub in _iter_geoms(g)
                    for x, y in list(sub.exterior.coords)]

        def _all_int_r(g):
            out = []
            for sub in _iter_geoms(g):
                for h in sub.interiors:
                    out.extend(_m.hypot(x, y) for x, y in list(h.coords))
            return out

        def _bounds_radial():
            r_shaft_in = 0.0
            r_shaft_out = 0.0
            r_rotor_out = 0.0
            r_stator_in = 0.0
            r_stator_out = 0.0
            if polys.get("shaft") is not None:
                ext_r = _all_ext_r(polys["shaft"])
                if ext_r:
                    r_shaft_out = max(ext_r)
                int_r = _all_int_r(polys["shaft"])
                if int_r:
                    r_shaft_in = min(int_r)
            if polys.get("rotor") is not None:
                ext_r = _all_ext_r(polys["rotor"])
                if ext_r:
                    r_rotor_out = max(ext_r)
            if polys.get("stator") is not None:
                ext_r = _all_ext_r(polys["stator"])
                if ext_r:
                    r_stator_out = max(ext_r)
                int_r = _all_int_r(polys["stator"])
                if int_r:
                    r_stator_in = min(int_r)
            if r_stator_in == 0 and polys.get("air_gap") is not None:
                ext_r = _all_ext_r(polys["air_gap"])
                if ext_r:
                    r_stator_in = max(ext_r)
            return r_shaft_in, r_shaft_out, r_rotor_out, r_stator_in, r_stator_out

        r_shaft_in, r_shaft_out, r_rotor_out, r_stator_in, r_stator_out = _bounds_radial()
        log.info("FEM radial bounds (mm): shaft_in=%.2f shaft_out=%.2f rotor_out=%.2f stator_in=%.2f stator_out=%.2f",
                 r_shaft_in, r_shaft_out, r_rotor_out, r_stator_in, r_stator_out)

        # Small-feature polygons (high priority)
        small_polys: List[Tuple[object, int]] = []
        for coil_poly in polys.get("coils", []):
            if coil_poly is not None:
                small_polys.append((coil_poly, DOM_COIL))
        for mag_poly, polarity in polys.get("magnets", []):
            if mag_poly is not None:
                small_polys.append((mag_poly, DOM_MAG_N if polarity > 0 else DOM_MAG_S))

        def _classify(x: float, y: float) -> int:
            p = _Pt(x, y)
            # 1) Small feature override (coils, magnets)
            for poly, d in small_polys:
                try:
                    if poly.contains(p):
                        return d
                except Exception:
                    continue
            # 2) Radial annulus for the bulk regions
            r = _m.hypot(x, y)
            if r < r_shaft_in:
                return DOM_AIR
            if r < r_shaft_out:
                return DOM_SHAFT
            if r < r_rotor_out:
                return DOM_ROTOR
            if r < r_stator_in:
                return DOM_AIRGAP
            if r <= r_stator_out + 0.1:
                return DOM_STATOR
            return DOM_AIR

        # Domain "specificity" — when a fragment came from multiple input
        # surfaces (e.g. an OCC overlap), pick the most specific domain
        # (coil > magnet > airgap > shaft > rotor > stator > air).
        # Air is the lowest priority: if anything else overlaps a sliver of
        # background-air, that material wins (prevents material→air bleed at
        # OCC tolerance boundaries).
        specificity = {
            DOM_COIL:    9,
            # DOM_MAG_BASE..DOM_MAG_BASE+N_MAG handled separately below
            DOM_BAND:    7,
            DOM_AIRGAP:  6,
            DOM_SHAFT:   5,
            DOM_ROTOR:   4,
            DOM_STATOR:  3,
            DOM_AIR:     2,
            DOM_OUTER:   1,   # outer ring loses to everything else
        }
        def _spec(dom_id: int) -> int:
            if dom_id >= DOM_COIL_BASE:
                return 10    # any per-coil tag — highest specificity
            if dom_id >= DOM_MAG_BASE:
                return 8     # any per-magnet tag — beats every bulk material
            return specificity.get(dom_id, 0)

        # Map each output fragment tag → set of source domain ids (via out_map)
        frag_to_doms: Dict[int, List[int]] = {}
        for in_idx, in_dim_tag in enumerate(dim_tags):
            _, dom_id = domain_surfaces[in_idx]
            out_list = out_map[in_idx] if in_idx < len(out_map) else [in_dim_tag]
            for out_dim, out_tag in out_list:
                if out_dim != 2:
                    continue
                frag_to_doms.setdefault(int(out_tag), []).append(int(dom_id))

        # Classify each output fragment by its most-specific input source.
        frag_surfaces: List[Tuple[int, int]] = []
        for dim, tag in fragment_out:
            if dim != 2:
                continue
            sources = frag_to_doms.get(int(tag), [])
            if sources:
                dom_id = max(sources, key=_spec)
            else:
                # Fallback: centroid-based radial classifier (rare path)
                try:
                    com = occ.getCenterOfMass(2, tag)
                    dom_id = _classify(com[0], com[1])
                except Exception:
                    dom_id = DOM_AIR
            frag_surfaces.append((int(tag), int(dom_id)))

        # Group surfaces by domain id — one physical group per domain
        from collections import defaultdict
        by_dom: Dict[int, List[int]] = defaultdict(list)
        for tag, dom_id in frag_surfaces:
            by_dom[dom_id].append(tag)
        phys_to_dom: Dict[int, int] = {}
        for dom_id, tags in by_dom.items():
            phys_tag = int(dom_id) + 1   # avoid the reserved 0
            gmsh.model.addPhysicalGroup(2, tags, tag=phys_tag)
            gmsh.model.setPhysicalName(2, phys_tag, f"dom_{dom_id}")
            phys_to_dom[phys_tag] = int(dom_id)

        # Refinement is driven by:
        #  (a) polygon vertex density (MeshSizeFromPoints = 1)
        #  (b) curvature inferred from the same vertex chain (MeshSizeFromCurvature)
        #  (c) a RADIAL AIR-GAP field (below) — the global size (≈4 mm) is 8×
        #      the 0.5 mm gap, so without this the gap had <1 element across it
        #      and the Maxwell-stress torque was grossly under-resolved and
        #      mesh-dependent (23→37 N·m).  Force ~3 element layers ACROSS THE
        #      GAP ONLY, with a quick ramp back to the global size just outside
        #      it — so the rotor/stator iron and the rotating ring stay coarse
        #      and only the gap itself is fine (Ansys does the same).
        try:
            _gc = geo_cfg or {}
            _r_ro = float(_gc.get("rotor_outer_radius", 0.0))
            _r_si = float(_gc.get("stator_inner_radius", 0.0))
            if _r_ro > 0.0 and _r_si > _r_ro:
                _r_ag = 0.5 * (_r_ro + _r_si)
                _gap  = _r_si - _r_ro
                _ag_h = max(0.06, min(min_size_mm, _gap / 3.0))   # ~3 layers in gap
                # Fine core = the gap + a thin sliver of the tooth tips on each
                # side (the tooth-tip field sets the gap B, so the torque needs
                # it — pure gap-only refinement makes the torque mesh-dependent
                # again).  The OLD problem was the 4 mm transition that spread
                # semi-fine mesh deep into the rotor/ring; the SHORT ramp below
                # keeps the surrounding iron and the rotating ring coarse.
                # Two knobs, decoupled:
                #   _half  = half-width of the UNIFORMLY-fine core (the visually
                #            "dense" strip).  Shrink it to the gap so only the
                #            air-gap itself is densely meshed.
                #   _trans = length of the GRADUAL ramp back to the global size.
                #            Keep it gentle — a short/steep ramp makes high
                #            aspect-ratio elements at the gap edge and the
                #            average torque sags (~8 %).  A gentle ramp keeps the
                #            gap-edge element quality (and the torque) while the
                #            triangles still coarsen quickly away from the gap.
                _half = _gap * 0.6                 # dense core ≈ the gap (±0.3 mm)
                _trans = _gap * 3.0                # gentle ramp (keeps torque)
                _formula = (f"min({float(mesh_size_mm)}, {_ag_h}+"
                            f"({float(mesh_size_mm)}-{_ag_h})*"
                            f"max(0,(fabs(sqrt(x*x+y*y)-{_r_ag})-{_half})/{_trans}))")
                _fid = gmsh.model.mesh.field.add("MathEval")
                gmsh.model.mesh.field.setString(_fid, "F", _formula)
                gmsh.model.mesh.field.setAsBackgroundMesh(_fid)
                # Let the background field own the size in the gap; keep curvature
                # for fillets (gmsh takes the min of the two).
                gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        except Exception as _e:
            log.warning("air-gap size field skipped: %s", _e)

        # ── Sliding-band slip ring: force the mid_r boundary to keep EXACTLY
        # its polygon vertices (transfinite, 2 nodes/edge) so both half-meshes
        # share an identical, equally-spaced node ring at r = slip_transfinite_r.
        # Matching nodes let the two halves MERGE by node identity (a shared
        # DOF) when the rotor is rotated by an integer node step — no
        # interpolation, no flux-coupling error.
        if slip_transfinite_r is not None:
            try:
                _rs = float(slip_transfinite_r)
                _n_ring = 0
                for (_d, _ct) in gmsh.model.getEntities(1):
                    _bp = gmsh.model.getBoundary([(1, _ct)], oriented=False,
                                                 combined=False)
                    _rr = []
                    for (_pd, _pt) in _bp:
                        _xyz = gmsh.model.getValue(0, _pt, [])
                        _rr.append(math.hypot(_xyz[0], _xyz[1]))
                    if _rr and all(abs(_r - _rs) < 1e-3 for _r in _rr):
                        gmsh.model.mesh.setTransfiniteCurve(_ct, 2)
                        _n_ring += 1
                log.info("slip ring: %d transfinite edges at r=%.3f mm",
                         _n_ring, _rs)
            except Exception as _e:
                log.warning("slip transfinite skipped: %s", _e)

        gmsh.model.mesh.generate(2)

        # Pre-compute the (gmsh_element_tag → domain id) map using the physical
        # groups — this lets us recover correct cell tags AFTER the meshio
        # round-trip even though meshio drops tag metadata.
        elem_tag_to_dom: Dict[int, int] = {}
        for phys_tag, dom_id in phys_to_dom.items():
            ent_tags = gmsh.model.getEntitiesForPhysicalGroup(2, phys_tag)
            for surf_tag in ent_tags:
                el_types, el_tags_per_type, _ = gmsh.model.mesh.getElements(2, int(surf_tag))
                for et_idx, et in enumerate(el_types):
                    if int(et) != 2:
                        continue
                    for elem_tag in el_tags_per_type[et_idx]:
                        elem_tag_to_dom[int(elem_tag)] = int(dom_id)

        # Export through meshio (the known-working path) and read tags back via
        # the gmsh-element-tag map we just built.
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix=".msh", delete=False)
        tmp.close()
        gmsh.write(tmp.name)
        mesh_io = _mio.read(tmp.name)
        os.unlink(tmp.name)

        # Read meshio's gmsh:geometrical to recover the per-cell element tag,
        # then look up the domain via our pre-built map.
        cell_tags_list: List[int] = []
        cell_data = mesh_io.cell_data or {}
        # meshio puts gmsh element ids in 'gmsh:physical' (if there's a phys group)
        # OR they can be in 'gmsh:dim_tags'. We use phys-IDs (= dom_id + 1):
        for idx, cell_block in enumerate(mesh_io.cells):
            if cell_block.type != "triangle":
                continue
            n_here = len(cell_block.data)
            phys_per = cell_data.get("gmsh:physical", [])
            if idx < len(phys_per) and len(phys_per[idx]) == n_here:
                for t in phys_per[idx]:
                    cell_tags_list.append(max(0, int(t) - 1))
            else:
                cell_tags_list.extend([DOM_AIR] * n_here)
    finally:
        try:
            gmsh.finalize()
        except Exception:
            pass
        _GMSH_LOCK.release()

    # Convert mm → m
    mesh_io.points = mesh_io.points * 1e-3
    mesh = from_meshio(mesh_io)
    cell_tags = np.array(cell_tags_list, dtype=np.int16)

    # ── PERIODIC COIL SUBSTITUTION ───────────────────────────────────────
    if periodic_coils and geo_cfg is not None:
        try:
            num_slots = sum(
                1 for _ in polys.get("coils", [])
            ) * 2 or int(geo_cfg.get("num_slots", 24))
            outer_r_mm = float(geo_cfg.get("stator_outer_radius",
                                            geo_cfg.get("stator_diameter", 150) / 2))
            wire_target_mm = max(0.15, min_size_mm)
            v_coil_m, t_coil = build_periodic_coil_mesh(
                geo_cfg, num_slots, outer_r_mm, wire_target_mm,
            )
            keep = cell_tags != DOM_COIL
            kept_t = mesh.t[:, keep]
            kept_tags = cell_tags[keep]
            n_old = mesh.p.shape[1]
            new_p = np.hstack([mesh.p, v_coil_m])
            new_t = np.hstack([kept_t, t_coil + n_old])
            new_tags = np.concatenate([
                kept_tags,
                np.full(t_coil.shape[1], DOM_COIL, dtype=np.int16),
            ])
            from skfem import MeshTri
            mesh = MeshTri(doflocs=new_p.astype(np.float64),
                           t=new_t.astype(np.int64))
            cell_tags = new_tags
            log.info("FEM: periodic coil substitution — added %d wire tris",
                     t_coil.shape[1])
        except Exception as e:
            log.warning("periodic coil substitution failed: %s", e)

    # NOTE: periodic-magnet template overlay (build_periodic_magnet_mesh) was
    # intentionally REMOVED.  The disjoint air_background polygon now ensures
    # gmsh's single fragment pass meshes every region (including the rotor-
    # pocket air above each magnet) without overlap, so no overlay is needed.

    # Attach the (possibly modified) polys dict to the classify_fn so the API
    # can render outlines that match the actual meshed geometry.
    try:
        _classify.polys = polys                  # type: ignore[attr-defined]
    except Exception:
        pass

    return mesh, cell_tags, _classify


def _read_cell_tags_by_dom(mesh_io) -> np.ndarray:
    """Map meshio physical-group ids → domain ids per triangle.

    With our new convention each physical group's tag equals the domain id
    plus one (to avoid the reserved 0).  Returns one int8 per triangle.
    """
    all_tags: List[int] = []
    phys_data = mesh_io.cell_data.get("gmsh:physical", [])
    for idx, cell_block in enumerate(mesh_io.cells):
        if cell_block.type != "triangle":
            continue
        n_tri_block = len(cell_block.data)
        if idx < len(phys_data) and len(phys_data[idx]) == n_tri_block:
            for t in phys_data[idx]:
                # phys group tag = dom_id + 1
                all_tags.append(max(0, int(t) - 1))
        else:
            all_tags.extend([DOM_AIR] * n_tri_block)
    return np.array(all_tags, dtype=np.int8)


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Assemble + solve the linear magnetostatics problem
# ─────────────────────────────────────────────────────────────────────────────

def _pair_sector_cut_nodes(mesh, n_sectors: int, tol_m: float = 1e-5
                             ) -> Tuple[np.ndarray, np.ndarray]:
    """Pair nodes on the two radial cut lines of a sector model.

    Returns (master_ids, slave_ids) — nodes on cut θ=0 paired by radius
    with nodes on cut θ = 2π/n_sectors.  Both arrays have the same length
    and same index ordering.  Origin (r=0) is skipped from both sides.
    """
    p = mesh.p
    x, y = p[0], p[1]
    r = np.sqrt(x ** 2 + y ** 2)

    # Cut 1 = +X axis (θ=0):  y ≈ 0, x ≥ 0
    cut1 = np.where((np.abs(y) < tol_m) & (x > tol_m) & (r > tol_m))[0]

    # Cut 2 = rotated +X by 2π/n_sectors
    a = 2.0 * math.pi / n_sectors
    ca, sa = math.cos(a), math.sin(a)
    # Distance from line θ=a (line through origin with direction (ca, sa)):
    # signed distance = x·sin(a) − y·cos(a)
    dist2 = np.abs(x * sa - y * ca)
    # Only the half-line in the +direction of (ca, sa)
    half2 = (x * ca + y * sa) > tol_m
    cut2 = np.where((dist2 < tol_m) & half2 & (r > tol_m))[0]

    if cut1.size == 0 or cut2.size == 0:
        return np.array([], dtype=int), np.array([], dtype=int)

    # Pair by NEAREST radius (handles unequal node counts after fragment).
    r1 = r[cut1]
    r2 = r[cut2]
    order1 = np.argsort(r1)
    order2 = np.argsort(r2)
    cut1_s = cut1[order1]
    cut2_s = cut2[order2]
    r1_s   = r1[order1]
    r2_s   = r2[order2]

    # Greedy: walk both sorted lists, pair items whose radii match within tol.
    masters, slaves = [], []
    i = j = 0
    while i < len(cut1_s) and j < len(cut2_s):
        dr = r1_s[i] - r2_s[j]
        if abs(dr) < tol_m * 100:    # 1 mm radial tolerance for matching
            masters.append(int(cut1_s[i]))
            slaves.append(int(cut2_s[j]))
            i += 1; j += 1
        elif dr < 0:
            i += 1
        else:
            j += 1
    return np.array(masters, dtype=int), np.array(slaves, dtype=int)


def _apply_anti_periodic(K, f, masters: np.ndarray, slaves: np.ndarray,
                          sign: float = -1.0):
    """Eliminate slave DoFs via A_slave = sign · A_master.

    Builds elimination matrix T (n × n_red) so that A_full = T · A_red:
      - T[i, k] = 1 for any free or master node i mapped to reduced index k
      - T[s, master_red_idx] = sign for each slave node s
    Returns (K_red, f_red, T) ready for the normal solve + back-projection.
    """
    from scipy.sparse import csr_matrix, lil_matrix, eye as sp_eye

    n = K.shape[0]
    is_slave = np.zeros(n, dtype=bool)
    is_slave[slaves] = True
    free_ids = np.where(~is_slave)[0]              # everything that survives
    n_red = free_ids.size

    # Map full → reduced index for the surviving nodes
    full2red = -np.ones(n, dtype=int)
    full2red[free_ids] = np.arange(n_red)

    # Build T (n × n_red).  Start with identity on the free rows.
    T = lil_matrix((n, n_red), dtype=float)
    for k, fi in enumerate(free_ids):
        T[fi, k] = 1.0
    # Slave rows pick up sign·master entry
    for m, s in zip(masters, slaves):
        T[s, full2red[m]] = sign
    T = T.tocsr()

    K_red = T.T @ K @ T
    f_red = T.T @ f
    return K_red, f_red, T


def _build_magnet_bh_curve_payload(mats: Dict[int, "FEMMaterial"]) -> List[dict]:
    """Pull the assigned magnet's BH curve from any per-magnet material entry
    (they all share the same curve) for plotting on the frontend."""
    for tag in sorted(mats):
        if tag < DOM_MAG_BASE:
            continue
        mat = mats[tag]
        if mat.bh_curve and len(mat.bh_curve) >= 2:
            return [{"H_kA_per_m": round(h * 1e-3, 1), "B_T": round(b, 4)}
                    for (h, b) in mat.bh_curve]
    return []


def _b_from_bh_at_H(bh_curve: List[Tuple[float, float]], H: float) -> float:
    """Interpolate B at given H from a BH curve sorted by H ascending."""
    if not bh_curve:
        return 0.0
    hs = [pt[0] for pt in bh_curve]
    bs = [pt[1] for pt in bh_curve]
    if H <= hs[0]:
        # Linear extrapolation in H (negative side)
        slope = (bs[1] - bs[0]) / max(hs[1] - hs[0], 1e-12)
        return bs[0] + slope * (H - hs[0])
    if H >= hs[-1]:
        slope = (bs[-1] - bs[-2]) / max(hs[-1] - hs[-2], 1e-12)
        return bs[-1] + slope * (H - hs[-1])
    lo, hi = 0, len(hs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if hs[mid] < H: lo = mid
        else:           hi = mid
    f = (H - hs[lo]) / max(hs[hi] - hs[lo], 1e-12)
    return bs[lo] + f * (bs[hi] - bs[lo])


def _mu_r_from_bh(bh_curve: List[Tuple[float, float]], B_mag: float
                   ) -> float:
    """Effective μ_r at flux density |B| read from a measured B-H curve.

    The curve is a list of (H [A/m], B [T]) sample pairs sorted by H.  We
    invert it by linear interpolation in B to find H(B), then μ_r = B / (μ₀·H).

    Beyond the last tabulated point the curve is extrapolated with the
    incremental slope dB/dH ≈ μ₀ (deep saturation), so the iron behaves
    asymptotically like air.  Below the first point the initial slope is
    used.
    """
    if not bh_curve or len(bh_curve) < 2 or B_mag <= 1e-12:
        return 1.0
    # Curve is monotonically increasing in B as H increases.
    bs = [pt[1] for pt in bh_curve]
    hs = [pt[0] for pt in bh_curve]
    if B_mag <= bs[0]:
        H = hs[0] + (hs[1] - hs[0]) * (B_mag - bs[0]) / max(bs[1] - bs[0], 1e-12)
    elif B_mag >= bs[-1]:
        # Extrapolate above the last sample with the differential μ₀ slope.
        H = hs[-1] + (B_mag - bs[-1]) / MU0
    else:
        # Binary search for the segment
        lo, hi = 0, len(bs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if bs[mid] < B_mag:
                lo = mid
            else:
                hi = mid
        f = (B_mag - bs[lo]) / max(bs[hi] - bs[lo], 1e-12)
        H = hs[lo] + f * (hs[hi] - hs[lo])
    if H <= 1e-9:
        return 1.0e6                              # virtually infinite μ_r
    return B_mag / (MU0 * H)


def _mu_r_from_bh_vec(bh_curve, B_arr):
    """Vectorised μ_r(|B|) from a (H,B) curve — one value per element.

    H(B) by linear interpolation in B; above the last sample the differential
    μ₀ slope (deep saturation); μ_r = B/(μ₀·H), clamped to ≥ 1."""
    B = np.asarray(B_arr, dtype=float)
    if not bh_curve or len(bh_curve) < 2:
        return np.ones_like(B)
    hs = np.array([pt[0] for pt in bh_curve], float)
    bs = np.array([pt[1] for pt in bh_curve], float)
    H = np.interp(B, bs, hs)                       # clamps at the ends
    above = B >= bs[-1]
    H = np.where(above, hs[-1] + (B - bs[-1]) / MU0, H)
    H = np.maximum(H, 1e-9)
    mu = np.where(B <= 1e-12, 1.0, B / (MU0 * H))
    return np.maximum(mu, 1.0)


def solve_magnetostatics(
    mesh,
    cell_tags: np.ndarray,
    materials: Dict[int, FEMMaterial],
    n_sectors: int = 1,
    pole_pairs_per_sector_is_half_integer: bool = True,
    nonlinear_iterations: int = 8,
) -> np.ndarray:
    """Linear 2-D magnetostatics solve.

    Returns the nodal A_z vector (shape n_nodes,) for the P1 basis.

    Equation:   ∫ ν ∇A_z·∇v  dΩ  =  ∫ J_z v dΩ  +  ∫ (Mx ∂v/∂y − My ∂v/∂x) dΩ

    When `n_sectors > 1`, anti-periodic master-slave boundary conditions are
    enforced on the two radial cuts of the sector:
        A_z(r, θ=0) = -A_z(r, θ=2π/n_sectors)
    The sign flips because each sector covers an ODD number of poles for
    the 24-slot / 28-pole motor (7 poles per quarter = 3.5 pole pairs).
    """
    import time as _t
    from skfem import (
        Basis, ElementTriP1, BilinearForm, LinearForm,
        asm, condense, solve,
    )
    from skfem.helpers import dot, grad

    basis = Basis(mesh, ElementTriP1())

    @BilinearForm
    def stiffness(u, v, w):
        return dot(grad(u), grad(v))

    @LinearForm
    def rhs_unit(v, w):
        return 1.0 * v

    @LinearForm
    def rhs_dvdy(v, w):
        return grad(v)[1]   # ∂v/∂y

    @LinearForm
    def rhs_dvdx(v, w):
        return grad(v)[0]   # ∂v/∂x

    t0 = _t.time()
    n = basis.N
    from scipy.sparse import csr_matrix

    # Pre-compute the per-tag stiffness factors so the Picard iteration can
    # cheaply re-scale them when μ_r is updated.
    unique_tags = np.unique(cell_tags)
    tag_K: Dict[int, "csr_matrix"] = {}
    tag_mat: Dict[int, FEMMaterial] = {}
    tag_cells: Dict[int, np.ndarray] = {}

    # Pre-assemble per-tag CURRENT and MAGNETISATION source vectors so the
    # Picard loop can re-scale each magnet's contribution when its Br_eff
    # drops due to demagnetisation, without re-running asm() every step.
    f_current = np.zeros(n)                  # J_z contribution (independent of M)
    tag_fMx: Dict[int, np.ndarray] = {}      # per-magnet ∫ ∂v/∂y dΩ
    tag_fMy: Dict[int, np.ndarray] = {}      # per-magnet ∫ ∂v/∂x dΩ
    for tag in unique_tags:
        mat = materials.get(int(tag))
        if mat is None:
            continue
        cells_idx = np.where(cell_tags == tag)[0]
        if cells_idx.size == 0:
            continue
        sub_basis = Basis(mesh, ElementTriP1(), elements=cells_idx)
        tag_K[int(tag)]    = asm(stiffness, sub_basis)
        tag_mat[int(tag)]  = mat
        tag_cells[int(tag)] = cells_idx
        if mat.J_z != 0.0:
            f_current += asm(rhs_unit, sub_basis) * mat.J_z
        if abs(mat.Mx) > 0:
            tag_fMx[int(tag)] = asm(rhs_dvdy, sub_basis)
        if abs(mat.My) > 0:
            tag_fMy[int(tag)] = asm(rhs_dvdx, sub_basis)

    SATURABLE_TAGS = {DOM_STATOR, DOM_ROTOR, DOM_SHAFT}
    mu_r_eff: Dict[int, float] = {tag: tag_mat[tag].mu_r for tag in tag_mat}
    # Br factor — starts at 1.0 (full strength) per magnet; the demag
    # iteration drops it below 1.0 when the operating point crosses the knee.
    br_factor: Dict[int, float] = {
        tag: 1.0 for tag in tag_mat if tag >= DOM_MAG_BASE}

    def _assemble_K() -> "csr_matrix":
        K = csr_matrix((n, n))
        for tag, K_dom in tag_K.items():
            K = K + K_dom * (1.0 / (MU0 * mu_r_eff[tag]))
        return K

    def _assemble_f() -> np.ndarray:
        f = f_current.copy()
        for tag, fMx in tag_fMx.items():
            scale = br_factor.get(tag, 1.0)
            f += fMx * (tag_mat[tag].Mx * scale)
        for tag, fMy in tag_fMy.items():
            scale = br_factor.get(tag, 1.0)
            f -= fMy * (tag_mat[tag].My * scale)
        return f

    f_const = _assemble_f()              # initial source, also referenced below

    f_total = f_const                                       # for compatibility

    # ── Picard iteration for iron saturation ─────────────────────────────
    # Linear iron (μ_r=5000 everywhere) lets the rotor back-iron act as a
    # short-circuit and absorb all the magnet flux instead of pushing it
    # through the air gap into the stator.  Real iron saturates at ~1.8 T,
    # so we iterate: solve linearly → check mean |B| in each iron domain →
    # roll μ_r down for over-saturated domains → resolve.  3–4 iterations
    # converge to a self-consistent saturated picture.
    A = np.zeros(n)
    outer_nodes = _outer_boundary_nodes(mesh)

    for it in range(max(1, nonlinear_iterations)):
        K_csr = _assemble_K().tocsr()
        f_iter = _assemble_f()           # picks up updated br_factor
        A = _solve_with_bc(K_csr, f_iter, outer_nodes, mesh, n_sectors,
                            pole_pairs_per_sector_is_half_integer)
        # Compute |B| per triangle, then mean |B| in each saturable domain.
        Bx_tri, By_tri = _per_triangle_B(mesh, A)
        Bmag_tri = np.sqrt(Bx_tri ** 2 + By_tri ** 2)
        changed = False
        for tag in SATURABLE_TAGS:
            if tag not in tag_cells:
                continue
            idx = tag_cells[tag]
            B_p90 = float(np.percentile(Bmag_tri[idx], 90)) if idx.size else 0.0
            mat_t = tag_mat[tag]
            # Prefer the measured B-H curve if the assigned material has one;
            # otherwise fall back to the analytic Fröhlich roll-off.
            if mat_t.bh_curve and len(mat_t.bh_curve) >= 2:
                new_mu = _mu_r_from_bh(mat_t.bh_curve, B_p90)
                src = "BH"
            else:
                from math import inf as _inf
                ratio = (1.8 / max(B_p90, 1e-9)) ** 3 if B_p90 > 1.8 else 1.0
                new_mu = mat_t.mu_r * ratio + 5.0 * (1 - ratio)
                src = "Fröhlich"
            new_mu = 0.5 * (mu_r_eff[tag] + new_mu)
            if abs(new_mu - mu_r_eff[tag]) / max(mu_r_eff[tag], 1.0) > 0.02:
                changed = True
            log.info("FEM iter %d: tag=%d B_p90=%.2fT μ_r %.0f→%.0f (%s)",
                     it, tag, B_p90, mu_r_eff[tag], new_mu, src)
            mu_r_eff[tag] = new_mu

        # ── Self-consistent demagnetisation update ────────────────────
        # When a magnet's operating point falls BELOW its BH-curve knee,
        # the effective Br drops to the value defined by the recoil line
        # passing through the operating point.  We reduce br_factor so
        # the next iteration's source term reflects the lost magnetisation
        # — and the reported torque/losses include the demag penalty.
        for tag in [t for t in tag_mat if t >= DOM_MAG_BASE]:
            mat_t = tag_mat[tag]
            if not mat_t.bh_curve or len(mat_t.bh_curve) < 2:
                continue
            Mmag = math.hypot(mat_t.Mx, mat_t.My)
            if Mmag < 1e-9:
                continue
            idx = tag_cells.get(tag)
            if idx is None or idx.size == 0:
                continue
            # Per-cell H projected onto +M̂  (along magnetisation direction).
            B_dot_M = (Bx_tri[idx] * mat_t.Mx + By_tri[idx] * mat_t.My)
            H_along_M = B_dot_M / (MU0 * Mmag) - Mmag * br_factor[tag]
            H_worst = float(np.min(H_along_M))
            H_knee = mat_t.bh_curve[1][0] if mat_t.bh_curve[0][1] <= 0 \
                       else mat_t.bh_curve[0][0]
            if H_worst < H_knee:
                # On the BH curve at H_worst, B is below the recoil line
                # → effective Br must drop.  New Br = B_op - μ_rec·μ₀·H_op
                # where (H_op, B_op) is read from the measured curve.
                B_op = _b_from_bh_at_H(mat_t.bh_curve, H_worst)
                Br_new = B_op - mat_t.mu_r * MU0 * H_worst
                Br_orig = Mmag * MU0      # current full-strength Br
                ratio = max(0.0, min(1.0, Br_new / max(Br_orig, 1e-12)))
                new_factor = 0.5 * (br_factor[tag] + ratio)   # damped
                if abs(new_factor - br_factor[tag]) > 0.01:
                    changed = True
                log.warning("FEM iter %d: magnet tag=%d demag — "
                             "H_min=%.0f A/m, H_knee=%.0f A/m, Br_factor %.3f→%.3f",
                             it, tag, H_worst, H_knee,
                             br_factor[tag], new_factor)
                br_factor[tag] = new_factor
        if not changed:
            break
    log.info("FEM solve: %d nodes, %d triangles, %d Picard iters, %.2fs",
             basis.N, mesh.t.shape[1], it + 1, _t.time() - t0)
    return A


def _solve_with_bc(K_csr, f, outer_nodes, mesh, n_sectors,
                    pole_pairs_per_sector_is_half_integer):
    """Apply Dirichlet outer BC + optional anti-periodic sector BC, then solve.
    Returns the nodal A_z vector at FULL mesh resolution."""
    from skfem import condense, solve

    # ── Anti-periodic master-slave BC on the radial cuts (sector mode) ──
    if n_sectors > 1:
        masters, slaves = _pair_sector_cut_nodes(mesh, n_sectors)
        if masters.size:
            sign = -1.0 if pole_pairs_per_sector_is_half_integer else +1.0
            K_red, f_red, T = _apply_anti_periodic(K_csr, f,
                                                     masters, slaves, sign)
            n_full = mesh.p.shape[1]
            is_slave = np.zeros(n_full, dtype=bool); is_slave[slaves] = True
            free_ids = np.where(~is_slave)[0]
            full2red = -np.ones(n_full, dtype=int)
            full2red[free_ids] = np.arange(free_ids.size)
            outer_red = full2red[outer_nodes]
            outer_red = outer_red[outer_red >= 0]
            A_red = solve(*condense(K_red, f_red, D=outer_red))
            return (T @ A_red).A.ravel() if hasattr(T @ A_red, 'A') \
                else np.asarray(T @ A_red).ravel()

    return solve(*condense(K_csr, f, D=outer_nodes))


def _outer_boundary_nodes(mesh) -> np.ndarray:
    """Return node ids on the outermost circular boundary (highest r)."""
    coords = mesh.p
    r = np.sqrt(coords[0] ** 2 + coords[1] ** 2)
    r_max = r.max()
    # Outer boundary = nodes within 0.5 mm of r_max
    return np.where(r >= r_max - 5e-4)[0]


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Sample A_z and derived B onto a regular grid (for canvas rendering)
# ─────────────────────────────────────────────────────────────────────────────

def sample_to_grid(
    mesh,
    A_nodal: np.ndarray,
    cell_tags: np.ndarray,
    materials: Dict[int, FEMMaterial],
    grid_size: int,
    extent_m: Tuple[float, float, float, float],
    classify_fn=None,   # optional (x_mm, y_mm) → domain_id
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate (A_z) onto a regular gs×gs grid; derive B = curl(A);
    reclassify domain on grid directly via classify_fn (recommended) or
    nearest-neighbour from mesh cells."""
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

    xmin, xmax, ymin, ymax = extent_m
    xs = np.linspace(xmin, xmax, grid_size)
    ys = np.linspace(ymin, ymax, grid_size)
    XX, YY = np.meshgrid(xs, ys)

    pts = mesh.p.T              # (n_nodes, 2)
    interp_A = LinearNDInterpolator(pts, A_nodal, fill_value=0.0)
    A_z = interp_A(XX, YY)

    dy = ys[1] - ys[0]
    dx = xs[1] - xs[0]
    B_x =  np.gradient(A_z, dy, axis=0)
    B_y = -np.gradient(A_z, dx, axis=1)
    B_mag = np.sqrt(B_x ** 2 + B_y ** 2)

    if classify_fn is not None:
        # Grid-level classification (mm coordinates)
        domain = np.zeros((grid_size, grid_size), dtype=np.int8)
        # Vectorise: vectorize the per-pixel classifier
        flat_x = (XX * 1e3).ravel()    # m → mm
        flat_y = (YY * 1e3).ravel()
        out = np.array([classify_fn(fx, fy) for fx, fy in zip(flat_x, flat_y)],
                       dtype=np.int8)
        domain = out.reshape(grid_size, grid_size)
    else:
        cell_centroids = mesh.p[:, mesh.t].mean(axis=1).T
        dom_interp = NearestNDInterpolator(cell_centroids, cell_tags.astype(np.int32))
        domain = dom_interp(XX, YY).astype(np.int8)

    # J_z grid: directly from the material map by domain id
    J_z = np.zeros_like(A_z)
    for d in np.unique(domain):
        if int(d) in materials:
            J_z[domain == d] = materials[int(d)].J_z

    return A_z, B_x, B_y, B_mag, J_z, domain


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Top-level convenience: build materials dict, solve, sample
# ─────────────────────────────────────────────────────────────────────────────

def build_materials(
    I_ph: Dict[str, float],
    winding_layout: List[Tuple[str, int]],
    polys: dict,
    rotor_angle_deg: float,
    slot_area_m2: float,
    n_wires: int,
    Br: float = 1.19,
    mu_r_steel: float = 5000.0,
) -> Dict[int, FEMMaterial]:
    """Build the per-domain material map for the FEM solve.

    Each magnet (DOM_MAG_BASE+i) and each coil (DOM_COIL_BASE+i) gets its
    own material entry with the polygon-specific source term.  Bulk
    materials (air, iron, etc.) share fixed ids.
    """
    n_slot = len(winding_layout)
    # Tangential M magnitude (alternating per pole)
    M_mag = Br / MU0

    # ── Resolve material assignments from motor_config.yaml ─────────────
    # Each motor part may be linked to a library material (with a BH curve);
    # if not, we keep the analytic μ_r above.
    try:
        from motor_ai_sim.config import get_material_assignments
        from motor_ai_sim import materials as mat_lib
        assignments = get_material_assignments() or {}
    except Exception:
        assignments = {}

    def _bh_for(part_key: str, category: str = "steel"):
        name = assignments.get(part_key)
        if not name:
            return None
        try:
            m = mat_lib.get_material(category, name)
            bh = getattr(m, "bh_curve", None)
            if bh and len(bh) >= 2:
                return [(float(h), float(b)) for (h, b) in bh]
        except Exception:
            # Silently — shafts are often Aluminium (not in steel) which is fine
            pass
        return None

    def _mu_r_for(part_key: str, default: float = 1.0) -> float:
        """Resolve a part's relative permeability from its linked material,
        searching all library categories.  A non-magnetic material (e.g.
        Aluminium_6061, whose mu_r is None) returns 1.0 — NOT the old 1000
        steel default that turned the aluminium shaft into a spurious flux
        path (it showed ~2.4 T; aluminium is non-magnetic, μ_r≈1)."""
        name = assignments.get(part_key)
        if not name:
            return default
        for cat in ("steel", "metal", "conductor", "magnet", "other", "custom"):
            try:
                m = mat_lib.get_material(cat, name)
            except Exception:
                continue
            mu = getattr(m, "mu_r", None)
            if mu is not None and float(mu) > 1.0:
                return float(mu)
            return 1.0          # found, but non-magnetic
        return default

    bh_stator = _bh_for("stator_core", "steel")
    bh_rotor  = _bh_for("rotor_core",  "steel")
    # Shaft is typically aluminium (conductor) or steel — try steel silently;
    # missing entry means no BH curve, which is fine for the air-like Al case.
    try:
        bh_shaft = _bh_for("shaft", "steel")
    except Exception:
        bh_shaft = None
    # μ_r for the shaft: a measured steel BH curve → mu_r_steel; otherwise the
    # linked material's own μ_r (Aluminium → 1.0), never a hard-coded 1000.
    shaft_mu_r = mu_r_steel if bh_shaft is not None else _mu_r_for("shaft", 1.0)
    # Magnet recoil μ_r, Br and BH curve (2nd-quadrant demag curve) from
    # the linked magnet material.
    mag_name = assignments.get("magnet")
    bh_magnet: Optional[List[Tuple[float, float]]] = None
    if mag_name:
        try:
            mat_mag = mat_lib.get_material("magnet", mag_name)
            Br      = float(getattr(mat_mag, "Br",     Br))
            mu_rec  = float(getattr(mat_mag, "mu_rec", 1.05))
            M_mag   = Br / MU0
            bh = getattr(mat_mag, "bh_curve", None)
            if bh and len(bh) >= 2:
                bh_magnet = [(float(h), float(b)) for (h, b) in bh]
        except Exception as e:
            log.warning("Magnet material '%s' lookup failed: %s", mag_name, e)
            mu_rec = 1.05
    else:
        mu_rec = 1.05

    mats: Dict[int, FEMMaterial] = {
        DOM_AIR:    FEMMaterial("air",    mu_r=1.0),
        DOM_AIRGAP: FEMMaterial("airgap", mu_r=1.0),
        DOM_BAND:   FEMMaterial("band",   mu_r=1.0),
        DOM_OUTER:  FEMMaterial("outer",  mu_r=1.0),
        DOM_STATOR: FEMMaterial("stator", mu_r=mu_r_steel, bh_curve=bh_stator),
        DOM_ROTOR:  FEMMaterial("rotor",  mu_r=mu_r_steel, bh_curve=bh_rotor),
        DOM_SHAFT:  FEMMaterial("shaft",  mu_r=shaft_mu_r, bh_curve=bh_shaft),
        DOM_COIL:   FEMMaterial("coil",   mu_r=1.0),
        DOM_MAG_N:  FEMMaterial("mag_N",  mu_r=mu_rec),
        DOM_MAG_S:  FEMMaterial("mag_S",  mu_r=mu_rec),
    }

    # ── Per-magnet tangential magnetization (SPOKE-PM topology) ──────────
    # M is tangent to the rotor at each magnet's angular position; sign
    # alternates per pole.  The iron tooth between adjacent magnets
    # becomes a virtual pole — flux concentrates there and exits radially
    # into the air gap, then closes through the STATOR YOKE.
    #
    # Convention (CCW tangent):
    #   tangent_CCW(centroid) = (-c_y, +c_x) / |centroid|     in WORLD frame
    #   N magnet (pol = +1):  M = +M_mag · tangent_CCW
    #   S magnet (pol = -1):  M = −M_mag · tangent_CCW
    for i, (mp, polarity) in enumerate(polys.get("magnets", [])):
        if mp is None or mp.is_empty:
            continue
        try:
            cx, cy = mp.centroid.x, mp.centroid.y
            cr = math.hypot(cx, cy)
            if cr < 1e-9:
                continue
            tx, ty = -cy / cr, cx / cr     # CCW tangent
        except Exception:
            continue
        sign = +1.0 if polarity > 0 else -1.0
        # Per-magnet material uses the assigned magnet's recoil permeability
        # and full demagnetisation BH curve so the Picard iteration can
        # detect under-knee operation and warn / reduce Br_eff.
        mats[DOM_MAG_BASE + i] = FEMMaterial(
            name=f"mag_{i}_{('N' if polarity>0 else 'S')}",
            mu_r=mu_rec,
            Mx= sign * M_mag * tx,
            My= sign * M_mag * ty,
            bh_curve=bh_magnet,                # full 2nd-quadrant demag curve
        )

    # ── Per-coil current density ─────────────────────────────────────────
    # cadquery_geometry now emits 24 coil polygons (one per slot, alternating
    # +x / -x side of each tooth).  We look up the slot's (phase, direction)
    # from winding_layout via centroid angle so the indexing is robust to
    # any clipping / re-ordering done downstream.
    coil_list = polys.get("coils", [])
    if coil_list and n_slot > 0:
        slot_pitch_deg = 360.0 / n_slot
        # The cadquery layout places TWO coil polygons per wide tooth — one
        # on either side of the tooth (e.g. at math 83.64° and 96.36° for
        # the tooth at math 90°).  Slot CENTRES sit at the half-pitch
        # offset (math 7.5°, 22.5°, 37.5°, …) so the two halves map to
        # ADJACENT slot indices: 5 and 6 for the example above.  Those two
        # neighbouring slot_idx values carry OPPOSITE direction signs in
        # the winding layout — which gives the go / return current pattern
        # of the concentrated coil (one side red = +J, the other blue = −J,
        # matching the Ansys reference).  Rounding to slot_pitch directly
        # (without the half-pitch offset) collapses both halves onto the
        # SAME slot_idx and forces them to carry the same sign — that's
        # the bug the user spotted in the field render.
        half_pitch_deg = slot_pitch_deg * 0.5
        for i, cp in enumerate(coil_list):
            if cp is None or cp.is_empty:
                continue
            try:
                cx, cy = cp.centroid.x, cp.centroid.y
            except Exception:
                continue
            ang = math.degrees(math.atan2(cy, cx))
            if ang < 0: ang += 360.0
            slot_idx = int((ang - half_pitch_deg) / slot_pitch_deg + 0.5) % n_slot
            phase, direction = winding_layout[slot_idx]
            # J_z = direction · I_phase_peak · n_wires_per_slot / slot_area
            J_z = float(direction) * I_ph[phase] * n_wires / max(slot_area_m2, 1e-12)
            mats[DOM_COIL_BASE + i] = FEMMaterial(
                name=f"coil_{i}_slot{slot_idx}_{phase}{'+' if direction>0 else '-'}",
                mu_r=1.0, J_z=J_z,
            )
    return mats


def fem_field2d(
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    grid_size: int = 150,
    mesh_size_mm: float = 1.6,
) -> FEMResult:
    """Top-level: build mesh + assemble + solve + sample.

    Mirrors the signature of routes/simulation.py::get_field2d so the FEM
    endpoint can drop in as a swap.
    """
    import time as _t
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import (
        params_from_config, MotorDomains2D,
    )
    from motor_ai_sim.config import get_config

    t_start = _t.time()
    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)

    # ── Operating-point currents (γ=0 → q-axis, +π/2 shift) ──────────────
    I_phase_rms  = sim.get("max_current", 85.0)
    n_parallel   = wind.get("n_parallel", 2)
    n_wires      = geo.get("num_wires_per_slot", 14)
    pole_pairs   = p.num_poles // 2
    I_coil_peak  = I_phase_rms / n_parallel * math.sqrt(2)
    theta_e      = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + 90.0)
    I_ph = {
        'A': I_coil_peak * math.cos(theta_e),
        'B': I_coil_peak * math.cos(theta_e - 2 * math.pi / 3),
        'C': I_coil_peak * math.cos(theta_e + 2 * math.pi / 3),
    }

    # ── Real CadQuery polygons at this rotor angle ────────────────────────
    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=rotor_angle_deg)

    log.info("FEM: building triangle mesh (h=%.2f mm)…", mesh_size_mm)
    polys = _simplify_polys(polys, tol_mm=0.3)
    mesh, cell_tags, classify_fn = build_mesh_from_polygons(polys, rotor_angle_deg, mesh_size_mm)

    # Reclassify each triangle by its centroid (mm) — robust against gmsh tag loss
    tri_centroids_m = mesh.p[:, mesh.t].mean(axis=1)   # (2, n_tri) in metres
    cell_tags = np.array(
        [classify_fn(tri_centroids_m[0, i] * 1e3, tri_centroids_m[1, i] * 1e3)
         for i in range(tri_centroids_m.shape[1])],
        dtype=np.int8,
    )
    log.info("FEM: reclassified cells — %s", dict(zip(*np.unique(cell_tags, return_counts=True))))
    log.info("FEM: mesh has %d nodes, %d triangles", mesh.p.shape[1], mesh.t.shape[1])

    slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
    mats = build_materials(I_ph, d.winding_layout, polys, rotor_angle_deg,
                           slot_area, n_wires)

    # Solve
    A = solve_magnetostatics(mesh, cell_tags, mats)

    # Sample onto regular grid for canvas
    R = p.r_stator_out * 1.02
    extent = (-R, R, -R, R)
    A_g, Bx_g, By_g, Bmag_g, Jz_g, dom_g = sample_to_grid(
        mesh, A, cell_tags, mats, grid_size, extent, classify_fn=classify_fn,
    )

    return FEMResult(
        grid_size=grid_size,
        extent=extent,
        A_z=A_g, B_x=Bx_g, B_y=By_g, B_mag=Bmag_g,
        J_z=Jz_g, domain=dom_g,
        n_triangles=mesh.t.shape[1],
        n_nodes=mesh.p.shape[1],
        solve_time_s=_t.time() - t_start,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Full FEM pipeline with torque + losses (Simulation tab endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def _per_triangle_B(mesh, A_nodal: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Compute B = (B_x, B_y) per triangle from P1 nodal A_z.
    Returns (B_x_per_tri, B_y_per_tri) — both shape (n_tri,).

    For a P1 element with vertices p0, p1, p2 and nodal A values A0, A1, A2:
        ∇A = Σ_i A_i ∇φ_i
        where ∇φ_0 = (y1 - y2, x2 - x1) / (2·area)  (and cyclic)
    Then B_x =  ∂A/∂y, B_y = -∂A/∂x  (2-D out-of-plane A_z convention).
    """
    p = mesh.p          # (2, n_nodes)
    t = mesh.t          # (3, n_tri)
    x0 = p[0, t[0]]; y0 = p[1, t[0]]
    x1 = p[0, t[1]]; y1 = p[1, t[1]]
    x2 = p[0, t[2]]; y2 = p[1, t[2]]
    A0 = A_nodal[t[0]]
    A1 = A_nodal[t[1]]
    A2 = A_nodal[t[2]]
    two_area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    safe = np.where(np.abs(two_area) > 1e-18, two_area, 1e-18)
    dA_dx = (A0 * (y1 - y2) + A1 * (y2 - y0) + A2 * (y0 - y1)) / safe
    dA_dy = (A0 * (x2 - x1) + A1 * (x0 - x2) + A2 * (x1 - x0)) / safe
    return dA_dy, -dA_dx           # B_x, B_y


def _triangle_areas(mesh) -> np.ndarray:
    p = mesh.p; t = mesh.t
    x0 = p[0, t[0]]; y0 = p[1, t[0]]
    x1 = p[0, t[1]]; y1 = p[1, t[1]]
    x2 = p[0, t[2]]; y2 = p[1, t[2]]
    return 0.5 * np.abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0))


def _maxwell_stress_torque(mesh, A_nodal: np.ndarray, r_ag_m: float,
                            stack_length_m: float,
                            theta_start: float = 0.0,
                            theta_end: float   = 2 * math.pi,
                            n_samples: int     = 720) -> float:
    """Maxwell stress tensor torque integrated on an air-gap circle arc.

    T = (L / μ₀) · r² · ∫ B_r · B_φ dφ   (over [theta_start, theta_end])

    Samples B at n_samples points on the arc r = r_ag, finds the host
    triangle for each, evaluates the constant-gradient B.  Returns torque
    in N·m for the integrated arc — caller multiplies by n_sectors when
    only a sector is meshed.
    """
    from matplotlib.tri import Triangulation
    tri = Triangulation(mesh.p[0], mesh.p[1], mesh.t.T)
    finder = tri.get_trifinder()

    phis = np.linspace(theta_start, theta_end, n_samples, endpoint=False)
    xs = r_ag_m * np.cos(phis)
    ys = r_ag_m * np.sin(phis)
    tri_idx = finder(xs, ys)

    Bx_per_tri, By_per_tri = _per_triangle_B(mesh, A_nodal)

    valid = tri_idx >= 0
    Bx = np.where(valid, Bx_per_tri[np.clip(tri_idx, 0, None)], 0.0)
    By = np.where(valid, By_per_tri[np.clip(tri_idx, 0, None)], 0.0)
    # Polar components at each sample point
    cos_p = np.cos(phis); sin_p = np.sin(phis)
    B_r   = Bx * cos_p + By * sin_p
    B_phi = -Bx * sin_p + By * cos_p

    dphi = (theta_end - theta_start) / n_samples
    return (stack_length_m / MU0) * r_ag_m ** 2 * float(np.sum(B_r * B_phi)) * dphi


def _arkkio_torque(mesh, A_nodal: np.ndarray, r_in_m: float, r_out_m: float,
                   stack_length_m: float) -> float:
    """Arkkio torque — averages the Maxwell stress over the WHOLE air-gap
    annulus instead of a single circle:

        T = (L / (μ₀·(r_out−r_in))) · ∫∫_annulus  r · B_r · B_φ  dA

    Integrating over every air-gap element (area-weighted) makes the result
    far less sensitive to mesh density and sampling noise than the
    single-contour stress integral — provided the gap is actually resolved
    (see the radial size field in build_mesh_from_polygons).  Returns the
    SECTOR torque; the caller multiplies by n_sectors.

    mesh.p is in METRES, so r_in_m / r_out_m must be in metres too.
    """
    Bx, By = _per_triangle_B(mesh, A_nodal)
    P = mesh.p; T = mesh.t
    cx = (P[0, T[0]] + P[0, T[1]] + P[0, T[2]]) / 3.0
    cy = (P[1, T[0]] + P[1, T[1]] + P[1, T[2]]) / 3.0
    rc = np.hypot(cx, cy)
    mask = (rc >= r_in_m) & (rc <= r_out_m)
    if not np.any(mask):
        return 0.0
    areas = _triangle_areas(mesh)                      # m²
    cosp = cx[mask] / rc[mask]; sinp = cy[mask] / rc[mask]
    Br  =  Bx[mask] * cosp + By[mask] * sinp
    Bph = -Bx[mask] * sinp + By[mask] * cosp
    integrand = areas[mask] * rc[mask] * Br * Bph
    return (stack_length_m / (MU0 * (r_out_m - r_in_m))) * float(np.sum(integrand))


class _SignedUF:
    """Signed union-find for combining anti-periodic + slip master-slave
    constraints.  union(a,b,s) means dof_a == s·dof_b; find returns (root, sign)."""
    __slots__ = ("par", "sgn")

    def __init__(self, n):
        self.par = list(range(n)); self.sgn = [1] * n

    def find(self, x):
        p = self.par[x]
        if p == x:
            return x, 1
        r, s = self.find(p)
        self.par[x] = r; self.sgn[x] *= s
        return r, self.sgn[x]

    def union(self, a, b, sign):
        ra, sa = self.find(a); rb, sb = self.find(b)
        if ra == rb:
            return
        self.par[ra] = rb; self.sgn[ra] = sign * sa * sb


# Copper electrical properties (annealed Cu, IEC 60028).
RHO_CU_20 = 1.724e-8     # Ω·m at 20 °C
ALPHA_CU  = 0.00393      # temperature coefficient [1/°C]


def end_winding_factor_geom(p, geo_cfg) -> float:
    """Estimate the end-winding length factor k_end = (active + end-turn) /
    active from the geometry.  A 2-D solve only resolves the in-slot (active,
    = stack-length) copper; the end-turns that loop outside the iron stack add
    series length the 2-D model can't see.  Per conductor the path length is
    L_stack + 2·L_endturn, so k_end = 1 + 2·L_endturn/L_stack.

    This machine is a 24-slot / 28-pole FRACTIONAL-SLOT CONCENTRATED winding
    (q = slots/(phases·poles) = 0.29 < 1) → tooth coils, each wound around ONE
    tooth.  Its end-turns are SHORT (they just arc over the tooth), so the
    end-turn per side ≈ a half-loop over the tooth width, NOT the long
    distributed-winding half-pole-pitch span.  (The old 'π·τ/2 + slot_depth'
    estimate was a distributed-winding formula → over-counted k_end at 3.3 and
    the copper loss by ~1.7×.)"""
    L = float(p.stack_length)
    if L <= 0:
        return 1.0
    r_mid = p.r_stator_in + p.slot_height_m * 0.5
    tau = 2.0 * math.pi * r_mid / max(p.num_slots, 1)        # slot pitch
    q = p.num_slots / (3.0 * max(p.num_poles, 1))            # slots/pole/phase
    if q < 0.75:                                            # concentrated tooth coil
        tooth_w = max(tau - p.slot_width_m, 0.3 * tau)      # tooth the coil wraps
        L_end = (math.pi / 2.0) * tooth_w                   # half-loop over the tooth
    else:                                                   # distributed winding
        L_end = math.pi * tau / 2.0 + p.slot_height_m
    return 1.0 + (2.0 * L_end) / L


def copper_loss_W(p, geo_cfg, I_phase_rms, n_parallel,
                  coil_temp_c=120.0, end_winding_factor=0.0):
    """Physical 3-phase copper (stranded) loss = ρ_Cu(T)·J²·V_cu·k_end.

    ρ_Cu(T) rises with coil temperature; J is the conductor current density
    (branch current / strand area); V_cu is the ACTIVE in-slot copper volume;
    k_end scales it up for the end-turns the 2-D field never sees.  Returns
    (P_cu_total_W, k_end_used, R_phase_eff_ohm)."""
    mm = 1e-3
    n_wires = float(geo_cfg.get("num_wires_per_slot", 14))
    wire_area = (float(geo_cfg.get("wire_width", 5.0)) * mm
                 * float(geo_cfg.get("wire_height", 0.6)) * mm)
    n_par = max(float(n_parallel), 1.0)
    if wire_area <= 0 or I_phase_rms <= 0:
        return 0.0, 1.0, 0.0
    V_cu_slot = p.num_slots * wire_area * n_wires * float(p.stack_length)
    k_end = (float(end_winding_factor) if end_winding_factor and end_winding_factor > 0
             else end_winding_factor_geom(p, geo_cfg))
    rho = RHO_CU_20 * (1.0 + ALPHA_CU * (float(coil_temp_c) - 20.0))
    I_coil = float(I_phase_rms) / n_par                 # branch current
    J = I_coil / wire_area                              # conductor current density
    P = rho * J * J * V_cu_slot * k_end
    R_phase_eff = P / (3.0 * float(I_phase_rms) ** 2)
    return float(P), float(k_end), float(R_phase_eff)


def fem_transient_sliding_band(
    n_steps_per_period: int = 12,
    n_periods: float = 1.0,
    gamma_deg: float = 0.0,
    I_phase_rms: float = 85.0,
    mesh_size_mm: float = 3.0,
    min_size_mm: float = 0.3,
    outer_air_factor: float = 1.3,
    n_sectors: int = 4,
    stator_fillet_mm: float = 0.0,
    nonlinear_iterations: int = 14,
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
) -> dict:
    """Sliding-band transient: mesh the stator + rotor halves ONCE, then sweep
    the rotor by shifting the slip-ring node pairing (no remeshing) so the
    mesh topology is IDENTICAL every frame.  That removes the per-frame
    remesh noise → smooth T(t) and clean back-EMF V(t) = R·I + dψ/dt.

    Fixed-mesh formulation: both halves stay in the [0, 360/n_sectors] wedge;
    the rotor rotation θ = m·slip_spacing is encoded ONLY in the slip pairing
    (shift by m nodes, sign −1 on every wrap past the sector edge — anti-
    periodic).  A signed union-find merges the slip pairing with the radial-cut
    anti-periodic BC.  Iron saturation via per-domain Picard.

    Returns the same dict shape as the parallel transient endpoint expects.
    """
    import time as _t
    from skfem import (Basis, ElementTriP1, ElementTriP0, BilinearForm,
                       LinearForm, asm, condense, solve as _sksolve, MeshTri)
    from skfem.helpers import dot as _dot, grad as _grad
    from scipy.sparse import csr_matrix as _csr, coo_matrix as _coo, block_diag as _bd
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
    from motor_ai_sim.config import get_config

    t0 = _t.time()
    # A coarse bulk mesh (e.g. 4 mm) makes the per-domain 90th-percentile
    # saturation jump between frames and occasionally produces a degenerate
    # gap slice → a single torque-spike frame (e.g. 36 N·m amid ~24) that
    # inflates ripple to ~75 %.  The sliding-band promise is a SMOOTH T(t),
    # so clamp the bulk element size for this path; the air-gap is refined
    # separately by the MathEval size field regardless of this value.
    _req_mesh = float(mesh_size_mm)
    # Clamp the iron mesh to 2 mm / 0.2 mm: a finer, more rotationally-
    # consistent iron mesh cuts the absolute parasitic (non-6·k) torque ripple
    # from the unstructured-mesh asymmetry by ~36 % (1.6→1.0 N·m, verified at
    # 24 & 72 steps) and is closer to mesh-converged.  A ~1 N·m floor remains
    # (sector anti-periodic formulation / physical FSCW sub-harmonics).
    mesh_size_mm = min(_req_mesh, 2.0)
    min_size_mm = min(float(min_size_mm), 0.2)
    cfg = get_config(); sim = cfg.get("simulation", {})
    geo = cfg.get("geometry", {}); wind = cfg.get("winding", {})
    p = params_from_config(); dom = MotorDomains2D(p)
    NS = int(n_sectors) if n_sectors and n_sectors > 1 else 4
    sector_deg = 360.0 / NS
    pole_pairs = p.num_poles // 2
    n_parallel = wind.get("n_parallel", 2)
    n_wires = int(geo.get("num_wires_per_slot", 14))
    # Physical copper loss: ρ_Cu(coil_temp)·J²·V_cu·k_end (end-winding the 2-D
    # field never sees).  R_phase is derived from it so the R·I voltage drop is
    # temperature-consistent — no hard-coded resistance.
    P_cu, _k_end_used, R_phase = copper_loss_W(
        p, geo, float(I_phase_rms), n_parallel,
        coil_temp_c=coil_temp_c, end_winding_factor=end_winding_factor)
    rpm = float(sim.get("rpm", 3950)); f_elec = float(sim.get("frequency", 921.67))
    slot_area_m2 = p.slot_width_m * p.slot_height_m * p.fill_factor
    mid = 0.5 * (p.r_rotor_out + p.r_stator_in)

    def _currents(rotor_angle_deg):
        Ipk = float(I_phase_rms) / n_parallel * math.sqrt(2)
        te = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + 285.0)
        return {'A': Ipk * math.cos(te),
                'B': Ipk * math.cos(te - 2 * math.pi / 3),
                'C': Ipk * math.cos(te + 2 * math.pi / 3)}

    # ── Snap steps/period so the rotor lands on whole slip nodes ──────────
    # The slip ring has _N_SLIP/pole_pairs nodes per electrical period; for a
    # uniform (periodic, non-chaotic) rotor advance, n_steps must divide that.
    _nodes_per_period = _N_SLIP // pole_pairs
    _req_steps = int(n_steps_per_period)
    n_steps_per_period = _snap_steps_to_nodes(_req_steps, _nodes_per_period)
    if n_steps_per_period != _req_steps:
        log.info("SB: snapped steps/period %d → %d (divisor of %d slip nodes/"
                 "period → whole-node rotor steps, periodic torque)",
                 _req_steps, n_steps_per_period, _nodes_per_period)

    # ── Build the two halves ONCE ────────────────────────────────────────
    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=stator_fillet_mm)
    ms, ts, cs, mr, tr, cr = _build_sliding_band_meshes(
        polys, 0.0, mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, band_thickness_mm=0.4,
        n_sectors=NS, geo_cfg=motor.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0)
    Ps, Tts = ms.p.copy(), ms.t.copy(); Pr, Ttr = mr.p.copy(), mr.t.copy()
    nsn = Ps.shape[1]
    Pall = np.hstack([Ps, Pr]); Tall = np.hstack([Tts, Ttr + nsn])
    n = Pall.shape[1]
    mesh_all = MeshTri(Pall, Tall)

    def _ring(P):
        r = np.hypot(P[0], P[1]); idx = np.where(np.abs(r - mid) < 1e-6)[0]
        ang = np.degrees(np.arctan2(P[1, idx], P[0, idx])) % 360.0
        o = np.argsort(ang); return idx[o]
    sring = _ring(Ps); rring = _ring(Pr)
    Nring = min(sring.size, rring.size)
    sring = sring[:Nring]; rring = rring[:Nring]
    spacing = sector_deg / (Nring - 1)

    # Constant radial-cut anti-periodic pairs on the combined mesh.
    Mn, Sn = _pair_sector_cut_nodes(mesh_all, NS)

    # Forms
    @BilinearForm
    def _stiff(u, v, w): return _dot(_grad(u), _grad(v))
    @BilinearForm
    def _stiff_nu(u, v, w):            # per-element reluctivity ν(x)
        return w["nu"] * _dot(_grad(u), _grad(v))
    @LinearForm
    def _f1(v, w): return 1.0 * v
    @LinearForm
    def _fdy(v, w): return _grad(v)[1]
    @LinearForm
    def _fdx(v, w): return _grad(v)[0]

    # ── Pre-assemble per-tag stiffness K0 + constant magnet source ───────
    matr0 = build_materials(_currents(0.0), dom.winding_layout,
                            getattr(cr, "polys", polys), 0.0, slot_area_m2, n_wires)
    # unit-current stator sources (per phase), magnet source is in rotor half
    half = {}
    for name, (P, T, tags, mats) in (
        ("s", (Ps, Tts, ts, None)), ("r", (Pr, Ttr, tr, matr0))):
        mesh = MeshTri(P, T); b = Basis(mesh, ElementTriP1()); nh = b.N
        K0 = {}; cells = {}; mu0 = {}
        for tag in np.unique(tags):
            idx = np.where(tags == tag)[0]; cells[int(tag)] = idx
            sb = Basis(mesh, ElementTriP1(), elements=idx)
            K0[int(tag)] = asm(_stiff, sb)
        half[name] = dict(mesh=mesh, b=b, n=nh, K0=K0, cells=cells)
    # magnet source (rotor half, constant — magnets fixed at angle 0)
    f_mag = np.zeros(half["r"]["n"])
    for tag, idx in half["r"]["cells"].items():
        m = matr0.get(int(tag))
        if m is None or (abs(m.Mx) + abs(m.My)) <= 0:
            continue
        sb = Basis(half["r"]["mesh"], ElementTriP1(), elements=idx)
        f_mag += asm(_fdy, sb) * m.Mx - asm(_fdx, sb) * m.My
    # per-phase unit-current stator source vectors
    f_coil = {'A': np.zeros(half["s"]["n"]), 'B': np.zeros(half["s"]["n"]),
              'C': np.zeros(half["s"]["n"])}
    coil_info = []   # (idx, areas, dir, phase) for ψ
    areas_s = _triangle_areas(half["s"]["mesh"])
    for ph in ('A', 'B', 'C'):
        Iunit = {'A': 0.0, 'B': 0.0, 'C': 0.0}; Iunit[ph] = 1.0
        mats_u = build_materials(Iunit, dom.winding_layout,
                                 getattr(cs, "polys", polys), 0.0, slot_area_m2, n_wires)
        for tag, idx in half["s"]["cells"].items():
            mu = mats_u.get(int(tag))
            if mu is None or mu.J_z == 0.0:
                continue
            sb = Basis(half["s"]["mesh"], ElementTriP1(), elements=idx)
            f_coil[ph] += asm(_f1, sb) * mu.J_z
    # ψ coil map (phase, dir) per coil tag — from a full-current material build
    mats_full = build_materials(_currents(0.0), dom.winding_layout,
                                getattr(cs, "polys", polys), 0.0, slot_area_m2, n_wires)
    for tag, idx in half["s"]["cells"].items():
        nm = (mats_full.get(int(tag)) or FEMMaterial("x")).name
        if not nm.startswith("coil_"):
            continue
        # name = "coil_<i>_slot<j>_<phase><+|->"  → phase is the char before +/-
        ph = nm[-2] if nm[-1] in "+-" else nm[-1]
        direction = 1.0 if nm.endswith("+") else -1.0
        if ph in "ABC":
            coil_info.append((idx, areas_s[idx], direction, ph))

    # ── Loss bookkeeping — iron Bertotti + magnet eddy from the ACTUAL B(t) ──
    # The sliding-band run gives a clean B(t) per element over a full electrical
    # period, so instead of the remesh path's single-snapshot Bertotti we use
    # the genuine time-derivative of the field:
    #   • classical eddy  ∝ ⟨(dB/dt)²⟩  (frequency-correct for ALL harmonics —
    #     slot ripple included — because faster flux ⇒ larger dB/dt ⇒ ∝ f²)
    #   • hysteresis      ∝ f·B_ac²     (B_ac = AC excursion, so a DC-biased
    #     rotor tooth contributes only its ripple, not its standing flux)
    #   • magnet eddy     = σ·d²/12·⟨(dB/dt)²⟩  (honest slab loss, no empirical
    #     ripple-fraction fudge)
    # The 20SW1200 Bertotti coefficients (kh,kc,ke) are fitted to the measured
    # loss-vs-frequency curves, so this IS the frequency-dependent loss model.
    from motor_ai_sim import materials as _mat_lib
    from motor_ai_sim.config import get_material_assignments as _gma
    _ma = _gma() or {}
    try:
        _steel_s = _mat_lib.get_steel(_ma.get("stator_core", "20SW1200"))
        _steel_r = _mat_lib.get_steel(_ma.get("rotor_core",  "20SW1200"))
    except Exception:
        _steel_s = _steel_r = None
    try:
        _magnet_mat = _mat_lib.get_magnet(_ma.get("magnet")) if _ma.get("magnet") else None
    except Exception:
        _magnet_mat = None
    _sigma_mag = float(getattr(_magnet_mat, "sigma", 0.0)) if _magnet_mat else 0.0
    # Magnet eddy slab dimension d: the AC field the magnet sees is the SLOT
    # RIPPLE, which varies TANGENTIALLY, so the eddy-current loop is limited by
    # the magnet's TANGENTIAL WIDTH (pole-pitch × fill) — NOT its radial
    # thickness.  P_eddy ∝ d², so using the (smaller) tangential width instead
    # of the 16 mm radial height drops the loss ~3× into the physical range
    # (the radial-thickness slab over-counted the un-segmented eddy).
    _r_mag_mid = 0.5 * (p.r_rotor_in + p.r_rotor_out)
    _mag_frac = float(getattr(p, "magnet_fill_fraction", 0.85) or 0.85)
    _d_mag_m = max(1e-3, (2.0 * math.pi * _r_mag_mid
                          / max(p.num_poles, 1)) * _mag_frac)
    areas_r = _triangle_areas(half["r"]["mesh"])
    _iron_s_idx = np.asarray(half["s"]["cells"].get(int(DOM_STATOR), np.array([], int)), int)
    _iron_r_idx = np.asarray(half["r"]["cells"].get(int(DOM_ROTOR),  np.array([], int)), int)
    _mag_parts = []
    for _tag, _idx in half["r"]["cells"].items():
        _m = matr0.get(int(_tag))
        if _m is not None and (abs(_m.Mx) + abs(_m.My)) > 0:
            _mag_parts.append(np.asarray(_idx, int))
    _mag_idx = np.concatenate(_mag_parts) if _mag_parts else np.array([], int)
    # Per-frame B histories for the loss elements only (keeps memory small).
    _hist_sx = []; _hist_sy = []; _hist_rx = []; _hist_ry = []
    _hist_mx = []; _hist_my = []; _mshift_hist = []

    SAT = {DOM_STATOR, DOM_ROTOR, DOM_SHAFT}
    # Per-tag base μ_r (air=1, coil=1, magnet=μ_rec, iron=μ_steel) + BH curves
    # for the saturable iron tags only.
    mu0 = {"s": {}, "r": {}}
    sat_bh = {"s": {}, "r": {}}
    for hn, md in (("s", mats_full), ("r", matr0)):
        for tag in half[hn]["cells"]:
            m = md.get(int(tag))
            mu0[hn][int(tag)] = max(float(m.mu_r), 1.0) if m else 1.0
            if (tag in SAT) and m and m.bh_curve and len(m.bh_curve) >= 2:
                sat_bh[hn][int(tag)] = m.bh_curve

    # Per-element saturation: the CONSTANT (non-iron) stiffness is pre-summed
    # once; the saturable iron tags are re-assembled each Picard iteration with
    # an element-wise reluctivity ν(x) so every triangle gets its own μ(|B|)
    # from the B-H curve — no single lumped μ that over- or under-saturates the
    # whole domain.
    K_const = {}; sb_sat = {"s": {}, "r": {}}
    b0_sat = {"s": {}, "r": {}}; nu_el = {"s": {}, "r": {}}
    for hn in ("s", "r"):
        h = half[hn]
        Kc = _csr((h["n"], h["n"]))
        for tag, Kd in h["K0"].items():
            if tag in sat_bh[hn]:
                idx = h["cells"][tag]
                _sbi = Basis(h["mesh"], ElementTriP1(), elements=idx)
                sb_sat[hn][tag] = _sbi
                b0_sat[hn][tag] = _sbi.with_element(ElementTriP0())
                nu_el[hn][tag] = np.full(
                    idx.size, 1.0 / (MU0 * max(mu0[hn].get(tag, 1.0), 1.0)))
            else:
                Kc = Kc + Kd * (1.0 / (MU0 * max(mu0[hn].get(tag, 1.0), 1.0)))
        K_const[hn] = Kc.tocsr()

    r_all = np.hypot(Pall[0], Pall[1])
    outer_nodes = np.where(r_all >= r_all.max() - 5e-4)[0]

    # ── Frame loop ───────────────────────────────────────────────────────
    n_total = max(1, int(round(n_steps_per_period * n_periods)))
    period_mech = 360.0 / pole_pairs                      # one electrical period [deg mech]
    T_series = []; psiA = []; psiB = []; psiC = []
    IA = []; IB = []; IC = []; tt = []
    dt = (1.0 / max(f_elec, 1e-9)) * n_periods / n_total
    for k in range(n_total):
        theta = (k / n_total) * period_mech * n_periods
        m_shift = int(round(theta / spacing))
        theta_eff = m_shift * spacing
        Ist = _currents(theta_eff)
        f_cur_s = (Ist['A'] * f_coil['A'] + Ist['B'] * f_coil['B']
                   + Ist['C'] * f_coil['C'])
        f = np.concatenate([f_cur_s, f_mag])
        # signed union-find: anti-periodic + slip-shift merge
        suf = _SignedUF(n)
        for a, b in zip(Mn, Sn):
            suf.union(int(b), int(a), -1)
        for kk in range(Nring):
            j = kk + m_shift; sg = 1
            while j > Nring - 1: j -= (Nring - 1); sg = -sg
            while j < 0:         j += (Nring - 1); sg = -sg
            suf.union(int(rring[kk] + nsn), int(sring[j]), sg)
        roots = [suf.find(i) for i in range(n)]
        rid = np.array([r for r, _ in roots]); rsg = np.array([s for _, s in roots], float)
        uniq, inv = np.unique(rid, return_inverse=True)
        Pro = _coo((rsg, (np.arange(n), inv)), shape=(n, uniq.size)).tocsr()
        outer_red = np.unique(inv[outer_nodes])
        # Reset the per-element iron reluctivity to the unsaturated base each
        # frame so the saturation solution is a pure function of rotor position
        # (no history dependence) → the torque ripple is strictly PERIODIC.
        for hn in ("s", "r"):
            for tag in sb_sat[hn]:
                nu_el[hn][tag][:] = 1.0 / (MU0 * max(mu0[hn].get(tag, 1.0), 1.0))
        A = np.zeros(n)
        for it in range(nonlinear_iterations):
            blocks = []
            for hn in ("s", "r"):
                h = half[hn]; K = K_const[hn].copy()
                for tag, _sbi in sb_sat[hn].items():
                    b0 = b0_sat[hn][tag]; nf = b0.zeros()
                    nf[h["cells"][tag]] = nu_el[hn][tag]   # P0 dof = global elem id
                    K = K + asm(_stiff_nu, _sbi, nu=b0.interpolate(nf))
                blocks.append(K)
            K = _bd(blocks).tocsr()
            A = Pro @ _sksolve(*condense((Pro.T @ K @ Pro).tocsr(),
                                          Pro.T @ f, D=outer_red))
            for hn, off in (("s", 0), ("r", nsn)):
                h = half[hn]
                Bx, By = _per_triangle_B(h["mesh"], A[off:off + h["n"]])
                Bm = np.sqrt(Bx ** 2 + By ** 2)
                for tag, curve in sat_bh[hn].items():
                    idx = h["cells"][tag]
                    if idx.size == 0:
                        continue
                    # PER-ELEMENT reluctivity: each iron triangle gets its own
                    # μ(|B|) from the B-H curve.  Damped (Picard) update of the
                    # element-wise ν field used by the saturable-tag assembly.
                    mu_new = _mu_r_from_bh_vec(curve, Bm[idx])
                    nu_new = 1.0 / (MU0 * np.maximum(mu_new, 1.0))
                    nu_el[hn][tag] = 0.5 * nu_el[hn][tag] + 0.5 * nu_new
        # capture the converged per-element B for the loss integrals
        _Bxs, _Bys = _per_triangle_B(half["s"]["mesh"], A[:nsn])
        _Bxr, _Byr = _per_triangle_B(half["r"]["mesh"], A[nsn:])
        _hist_sx.append(_Bxs[_iron_s_idx]); _hist_sy.append(_Bys[_iron_s_idx])
        _hist_rx.append(_Bxr[_iron_r_idx]); _hist_ry.append(_Byr[_iron_r_idx])
        _hist_mx.append(_Bxr[_mag_idx]);    _hist_my.append(_Byr[_mag_idx])
        _mshift_hist.append(m_shift)
        # torque (Arkkio over the gap)
        Tq = _arkkio_torque(mesh_all, A, p.r_rotor_out, p.r_stator_in,
                            p.stack_length) * NS
        # flux linkage (stator half)
        As = A[:nsn]; A_tri = (As[Tts[0]] + As[Tts[1]] + As[Tts[2]]) / 3.0
        pa = pb = pc = 0.0
        for idx, ar, direction, ph in coil_info:
            sa = float(np.sum(ar))
            if sa <= 0: continue
            mAz = float(np.sum(A_tri[idx] * ar)) / sa
            val = direction * mAz
            if ph == 'A': pa += val
            elif ph == 'B': pb += val
            else: pc += val
        sc = p.stack_length * NS / float(n_parallel)
        T_series.append(float(Tq))
        psiA.append(pa * sc); psiB.append(pb * sc); psiC.append(pc * sc)
        IA.append(Ist['A']); IB.append(Ist['B']); IC.append(Ist['C'])
        tt.append(k * dt)
    # ── Spectral periodic time-derivative (truncated to K harmonics) ─────────
    # The rotor advances in DISCRETE slip-node steps, so ψ(t) and B(t) carry a
    # tiny frame-to-frame quantisation jitter.  A raw finite-difference dψ/dt
    # amplifies that jitter into a jagged back-EMF (worse at small dt → the 24-
    # step run looked torn).  Reconstruct the derivative from the LOW harmonics
    # only: that keeps the genuine fundamental + slot-ripple content but drops
    # the quantisation noise floor near Nyquist, giving a clean V(t) and a
    # physically-rippling (not noisy, not flat) loss(t).
    _two_pi2 = 2.0 * math.pi ** 2

    def _spectral_ddt(x, kmax):
        x = np.asarray(x, float); N = x.size
        if N < 4:
            return np.array([(x[(i + 1) % N] - x[(i - 1) % N]) / (2 * dt)
                             for i in range(N)])
        F = np.fft.rfft(x)
        if kmax + 1 < F.size:
            F[kmax + 1:] = 0.0
        return np.fft.irfft(F * (1j * 2 * np.pi * np.fft.rfftfreq(N, d=dt)), n=N)

    # The rotor can only sit on DISCRETE slip nodes (≈ N_slip/4 ≈ 72 positions
    # per electrical period), so B depends only on the quantised angle m_shift.
    # When n_steps > that node count the rotor advances <1 node/step and
    # STUTTERS (m_shift jumps 0,1,1,0,1…); a frame-to-frame dB/dt of that
    # stutter is meaningless noise.  So differentiate B against the UNIQUE
    # rotor node-positions (smooth, ~72 pts) and map the result back onto the
    # time frames — gives a clean dB/dt at any n_steps.
    _m_arr = np.asarray(_mshift_hist, int)
    _spacing_rad = math.radians(spacing)
    _omega_mech = 2.0 * math.pi * rpm / 60.0

    def _angle_ddt_2d(X):
        N = X.shape[0]
        if N < 3:
            return np.zeros_like(X)
        uniq, first = np.unique(_m_arr, return_index=True)   # sorted unique m
        if uniq.size < 3:
            return (np.roll(X, -1, 0) - np.roll(X, 1, 0)) / (2 * dt)
        Bu = X[first]                                        # (U, E)
        theta_u = uniq * _spacing_rad                        # (U,)
        # node-to-node slip-merge noise is high-frequency vs the physical slot
        # ripple (~1–2 cycles per electrical period); low-pass B(θ) on the node
        # grid before differentiating so dB/dθ shows ripple, not merge jitter.
        U = uniq.size
        if U >= 7:
            from scipy.signal import savgol_filter as _sg
            w = min(max(5, (U // 8) * 2 + 1), U if U % 2 == 1 else U - 1)
            if w >= 5:
                Bu = _sg(Bu, w, 3, axis=0, mode="interp")
        dBdt_u = np.gradient(Bu, theta_u, axis=0) * _omega_mech
        pos = np.searchsorted(uniq, _m_arr)                  # frame → unique idx
        return dBdt_u[pos]

    def _declip(a):
        # Safety net: clip any residual single-frame outlier to median±5·MAD.
        a = np.asarray(a, float)
        if a.size < 5:
            return a
        med = float(np.median(a)); mad = float(np.median(np.abs(a - med)))
        if mad <= 0:
            return a
        return np.clip(a, max(0.0, med - 5 * mad), med + 5 * mad)

    _Kv = max(1, min(5, (n_total // 2) - 1))     # back-EMF: keep it smooth

    # voltage V = R·I + dψ/dt  (spectrally smoothed back-EMF)
    eA = _spectral_ddt(psiA, _Kv); eB = _spectral_ddt(psiB, _Kv)
    eC = _spectral_ddt(psiC, _Kv)
    VA = [R_phase * i + e for i, e in zip(IA, eA.tolist())]
    VB = [R_phase * i + e for i, e in zip(IB, eB.tolist())]
    VC = [R_phase * i + e for i, e in zip(IC, eC.tolist())]
    Tavg = float(np.mean(T_series)) if T_series else 0.0
    Trip = (100.0 * (max(T_series) - min(T_series)) / abs(Tavg)
            if T_series and abs(Tavg) > 1e-9 else 0.0)
    Vpk = float(max(max(map(abs, VA)), max(map(abs, VB)), max(map(abs, VC)))) if VA else 0.0
    # P_cu already computed physically (ρ(T)·J²·V·k_end) near the top.

    # ── Torque harmonic spectrum over ONE electrical period ──────────────────
    # The single most telling diagnostic for "is this periodic or chaotic": a
    # clean ripple shows a few DISCRETE peaks (the cogging / 6·k 3-phase orders);
    # broadband noise spreads across all orders.  Orders are multiples of the
    # ELECTRICAL fundamental; amplitude is the single-sided FFT magnitude [N·m].
    T_harm_order = []; T_harm_amp = []
    if T_series:
        _per = max(1, int(round(n_steps_per_period)))
        _Tp = np.asarray(T_series[:_per], float)
        if _Tp.size >= 4:
            _F = np.abs(np.fft.rfft(_Tp - _Tp.mean())) / _Tp.size * 2.0
            _nh = min(_F.size - 1, 36)
            T_harm_order = list(range(1, _nh + 1))
            T_harm_amp = [round(float(_F[k]), 4) for k in range(1, _nh + 1)]

    # ── Losses from the captured B(t) — PER-FRAME instantaneous series ────────
    # iron(t)  = hysteresis baseline (per-cycle quantity, flat) + classical
    #            eddy from the smooth |dB/dt|²(t) → ripples as the teeth pass.
    # magnet(t)= σ·d²/12·|dB/dt|²(t)  → ripples likewise.
    def _iron_series(hx, hy, idx, areas_half, mat):
        if mat is None or idx.size == 0 or not hx or np.asarray(hx[0]).size == 0:
            return np.zeros(n_total), 0.0
        X = np.asarray(hx); Y = np.asarray(hy)            # (N, E)
        kh = float(getattr(mat, "core_loss_kh", 0.0))
        kc = float(getattr(mat, "core_loss_kc", 0.0))
        ke = float(getattr(mat, "core_loss_ke", 0.0))
        sf = float(getattr(mat, "stacking_factor", 0.95))
        vol = areas_half[idx] * p.stack_length * sf       # (E,)
        dX = _angle_ddt_2d(X); dY = _angle_ddt_2d(Y)
        pcl_t = (kc / _two_pi2) * np.sum((dX ** 2 + dY ** 2) * vol[None, :], axis=1)
        Bac2 = (((X.max(0) - X.min(0)) * 0.5) ** 2
                + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)
        phys = float(np.sum((kh * f_elec * Bac2
                             + ke * f_elec ** 1.5
                               * np.power(np.maximum(Bac2, 0.0), 0.75)) * vol))
        return pcl_t, phys

    _pcl_s, _ph_s = _iron_series(_hist_sx, _hist_sy, _iron_s_idx, areas_s, _steel_s)
    _pcl_r, _ph_r = _iron_series(_hist_rx, _hist_ry, _iron_r_idx, areas_r, _steel_r)
    _P_hyst = (_ph_s + _ph_r) * NS
    _P_fe_t = _declip((_pcl_s + _pcl_r) * NS + _P_hyst)  # classical ripple + flat hyst
    P_fe_series = _P_fe_t.tolist()
    P_fe_avg = float(np.mean(_P_fe_t))

    if (_sigma_mag > 0.0 and _mag_idx.size and _hist_mx
            and np.asarray(_hist_mx[0]).size):
        Xm = np.asarray(_hist_mx); Ym = np.asarray(_hist_my)
        dXm = _angle_ddt_2d(Xm); dYm = _angle_ddt_2d(Ym)
        vol_m = areas_r[_mag_idx] * p.stack_length
        _P_mag_t = _declip(_sigma_mag * (_d_mag_m ** 2 / 12.0)
                    * np.sum((dXm ** 2 + dYm ** 2) * vol_m[None, :], axis=1) * NS)
        P_mag_series = _P_mag_t.tolist()
        P_mag_avg = float(np.mean(_P_mag_t))
    else:
        P_mag_series = [0.0] * n_total; P_mag_avg = 0.0

    log.info("SB transient: %d frames, %d slip nodes, P_fe=%.1f P_mag=%.1f, %.1fs",
             n_total, Nring, P_fe_avg, P_mag_avg, _t.time() - t0)
    P_cu_series = [P_cu] * n_total
    P_tot_series = [c + f + e for c, f, e in zip(P_cu_series, P_fe_series, P_mag_series)]
    P_mech_avg = float(Tavg * 2.0 * math.pi * rpm / 60.0)
    return {
        "method": "sliding_band",
        "n_steps": n_total, "n_steps_per_period": int(n_steps_per_period),
        "n_periods": float(n_periods), "rpm": rpm, "f_elec_Hz": f_elec,
        "dt_s": dt, "T_period_s": (1.0 / f_elec if f_elec > 1e-9 else 0.0),
        "time_s": tt, "rotor_angle_deg": [
            (k / n_total) * period_mech * n_periods for k in range(n_total)],
        "T_em_Nm": T_series, "T_avg_Nm": Tavg, "T_ripple_pct": Trip,
        "psi_A_Wb": psiA, "psi_B_Wb": psiB, "psi_C_Wb": psiC,
        "V_A": VA, "V_B": VB, "V_C": VC, "V_peak": Vpk,
        "I_A": IA, "I_B": IB, "I_C": IC,
        "P_cu_W": P_cu_series, "P_fe_W": P_fe_series,
        "P_mag_eddy_W": P_mag_series, "P_loss_total_W": P_tot_series,
        "P_mech_avg_W": P_mech_avg,
        "R_phase_ohm": R_phase, "n_slip_nodes": int(Nring),
        "coil_temp_C": float(coil_temp_c),
        "end_winding_factor": float(_k_end_used),
        "T_harm_order": T_harm_order, "T_harm_amp": T_harm_amp,
    }


def fem_solve_for_sim(
    rotor_angle_deg: float = 0.0,
    gamma_deg:       float = 0.0,
    mesh_size_mm:    float = 3.0,
    min_size_mm:     float = 0.3,
    outer_air_factor:float = 1.3,
    motion_band:     bool  = True,
    band_thickness_mm: float = 0.4,
    n_sectors:       int   = 4,
    stator_fillet_mm:float = 0.0,
    I_phase_rms:     Optional[float] = None,
) -> dict:
    """End-to-end FEM solve: build mesh on (possibly clipped) geometry,
    solve magnetostatics, compute Maxwell-stress torque and Steinmetz iron
    losses + I²R copper losses, return everything the Simulation tab needs.

    Multiplies INTEGRAL quantities (torque + iron + magnet eddy losses)
    by n_sectors so the values represent the full motor.  Copper loss is
    derived from phase currents directly (no mesh integration) so it's
    already a full-motor number.
    """
    import time as _t
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import (
        params_from_config, MotorDomains2D,
    )
    from motor_ai_sim.config import get_config

    t_start = _t.time()
    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)
    pole_pairs   = p.num_poles // 2
    # I_phase_rms = 0 must be honoured (zero-current solve = magnet field only).
    # `None` means "use whatever the operating-point config says".
    if I_phase_rms is None:
        I_phase_rms  = sim.get("max_current", 85.0)
    I_phase_rms = float(I_phase_rms)
    n_parallel   = wind.get("n_parallel", 2)
    n_wires      = int(geo.get("num_wires_per_slot", 14))
    I_coil_peak  = I_phase_rms / n_parallel * math.sqrt(2)
    # d-axis convention for SPOKE-PM:
    #   The effective N pole of the rotor sits at the CENTRE OF THE IRON
    #   TOOTH between two adjacent magnets — half a pole pitch (= 90° elec)
    #   offset from the magnet centre.  Empirical γ-sweep with the actual
    #   mesh + nonlinear iron + corrected (half-pitch) slot_idx mapping
    #   determines this constant so γ = 0 lands on the q-axis (max torque).
    # Geometry: rotor d-axis tooth at math 90° (+Y axis), aligned with the
    #   first stator tooth (also at math 90°), exactly as in the Ansys
    #   reference image.
    SPOKE_PM_DAXIS_SHIFT_DEG = 285.0
    theta_e      = math.radians(rotor_angle_deg * pole_pairs
                                 + gamma_deg + SPOKE_PM_DAXIS_SHIFT_DEG)
    I_ph = {
        'A': I_coil_peak * math.cos(theta_e),
        'B': I_coil_peak * math.cos(theta_e - 2 * math.pi / 3),
        'C': I_coil_peak * math.cos(theta_e + 2 * math.pi / 3),
    }

    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=rotor_angle_deg)
    polys = _simplify_polys(polys, tol_mm=0.005,
                             stator_fillet_mm=stator_fillet_mm)

    log.info("FEM-sim: building mesh (h=%.2f, n_sectors=%d, outer×%.2f, band=%s)",
             mesh_size_mm, n_sectors, outer_air_factor, motion_band)
    mesh, cell_tags, classify_fn = build_mesh_from_polygons(
        polys, rotor_angle_deg, mesh_size_mm,
        min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor,
        motion_band=motion_band,
        band_thickness_mm=band_thickness_mm,
        n_sectors=n_sectors,
        geo_cfg=motor.parameters,
    )
    # int16 — per-magnet tags reach DOM_MAG_BASE + 27 = 127, well within int16
    # but right at the edge of int8.  Stay in int16 to be safe.
    cell_tags = cell_tags.astype(np.int16)

    slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
    # build_mesh_from_polygons attached the FINAL (post-clip + air-injected)
    # polys to classify_fn.  Per-magnet material indices must match the
    # mesh's per-magnet tags, so we build materials from that same dict.
    polys_meshed = getattr(classify_fn, "polys", polys)
    mats = build_materials(I_ph, d.winding_layout, polys_meshed,
                            rotor_angle_deg, slot_area, n_wires)

    t_solve_start = _t.time()
    poles_per_sector = p.num_poles // max(int(n_sectors), 1)
    anti_periodic = (poles_per_sector % 2 == 1)
    A = solve_magnetostatics(mesh, cell_tags, mats,
                              n_sectors=int(n_sectors),
                              pole_pairs_per_sector_is_half_integer=anti_periodic)
    t_solve = _t.time() - t_solve_start

    # ── Demagnetisation post-check (after the converged solve) ───────────
    Bx_post, By_post = _per_triangle_B(mesh, A)
    demag_report: List[dict] = []
    magnet_op_points: List[dict] = []
    # PER-TRIANGLE demagnetisation coefficient (0..1) for the field map —
    # all triangles default to 1.0 (no demag); magnet cells get the actual
    # ratio of remaining B / Br at their operating point.
    demag_coef_per_tri = np.ones(mesh.t.shape[1], dtype=np.float32)
    for tag in sorted([t for t in mats if t >= DOM_MAG_BASE]):
        mat_t = mats[tag]
        if abs(mat_t.Mx) + abs(mat_t.My) < 1e-9:
            continue
        idx = np.where(cell_tags == tag)[0]
        if idx.size == 0:
            continue
        Mmag = math.hypot(mat_t.Mx, mat_t.My)
        # H projected on +M̂, accounting for the iteration's br_factor.
        H_M = (Bx_post[idx] * mat_t.Mx + By_post[idx] * mat_t.My) \
                / (MU0 * Mmag + 1e-30) - Mmag
        # B projected on +M̂
        B_M = (Bx_post[idx] * mat_t.Mx + By_post[idx] * mat_t.My) / Mmag
        H_min = float(np.min(H_M))
        H_mean = float(np.mean(H_M))
        B_at_min = float(B_M[int(np.argmin(H_M))])
        magnet_op_points.append({
            "magnet_index": int(tag - DOM_MAG_BASE),
            "H_op_kA_per_m":  round(H_min  * 1e-3, 1),
            "H_mean_kA_per_m": round(H_mean * 1e-3, 1),
            "B_op_T":         round(B_at_min, 4),
        })
        if not mat_t.bh_curve or len(mat_t.bh_curve) < 2:
            continue
        H_knee = mat_t.bh_curve[1][0] if mat_t.bh_curve[0][1] <= 0 \
                   else mat_t.bh_curve[0][0]
        # Per-cell demag coefficient.  Above the knee (H ≥ H_knee, i.e.
        # less negative) the magnet operates linearly → DC = 1.  Below
        # the knee the coefficient drops linearly with H, hitting 0 at
        # 2·H_knee (a deeply demagnetised cell).
        for j, c in enumerate(idx):
            h = H_M[j]
            if h >= H_knee:
                dc = 1.0
            else:
                dc = 1.0 - (H_knee - h) / abs(H_knee)
            demag_coef_per_tri[c] = max(0.0, min(1.0, float(dc)))
        ratio = H_min / H_knee if H_knee < 0 else 0.0
        if ratio > 0.85:
            demag_report.append({
                "tag": int(tag),
                "magnet_index": int(tag - DOM_MAG_BASE),
                "H_min_kA_per_m": round(H_min * 1e-3, 1),
                "H_knee_kA_per_m": round(H_knee * 1e-3, 1),
                "knee_proximity": round(ratio, 2),
                "demagnetised": bool(ratio > 1.0),
            })
    # Sanitize any NaN/Inf so the response stays JSON-compliant.  Bad nodes
    # become zero — they show up as background-coloured spots in the canvas
    # but don't crash the whole render.
    A = np.nan_to_num(A, nan=0.0, posinf=0.0, neginf=0.0)
    n_bad = int(np.sum(~np.isfinite(A))) if A.size else 0
    if n_bad:
        log.warning("FEM: %d non-finite A values clamped to 0", n_bad)

    # ── Per-triangle B, |B| ───────────────────────────────────────────────
    Bx_tri, By_tri = _per_triangle_B(mesh, A)
    Bmag_tri = np.sqrt(Bx_tri ** 2 + By_tri ** 2)
    Bx_tri = np.nan_to_num(Bx_tri, nan=0.0, posinf=0.0, neginf=0.0)
    By_tri = np.nan_to_num(By_tri, nan=0.0, posinf=0.0, neginf=0.0)
    Bmag_tri = np.nan_to_num(Bmag_tri, nan=0.0, posinf=0.0, neginf=0.0)
    areas    = _triangle_areas(mesh)               # m² for unit stack

    # ── Torque via Arkkio (air-gap annulus average) — mesh-robust ─────────
    # The old single-circle Maxwell stress was wildly mesh-dependent (23→37 N·m)
    # because the gap was under-meshed; with the air-gap size field the gap is
    # now resolved and Arkkio (averaging the stress over the whole annulus)
    # converges to ~26-27 N·m.  Single-circle kept only for the debug log.
    r_ag_m = 0.5 * (p.r_rotor_out + p.r_stator_in)      # mid-air-gap
    theta_end = 2 * math.pi if n_sectors <= 1 else (2 * math.pi / n_sectors)
    T_sector = _arkkio_torque(mesh, A, p.r_rotor_out, p.r_stator_in, p.stack_length)
    T_em_Nm = T_sector * (n_sectors if n_sectors > 1 else 1)
    try:
        T_circle = _maxwell_stress_torque(mesh, A, r_ag_m, p.stack_length,
                                          0.0, theta_end, 720) \
                   * (n_sectors if n_sectors > 1 else 1)
        log.info("torque: Arkkio=%.2f N·m  (single-circle=%.2f N·m)",
                 T_em_Nm, T_circle)
    except Exception:
        pass

    # ── Per-phase flux linkage ψ_A, ψ_B, ψ_C ─────────────────────────────
    # ψ_per_slot = N_turns · L_stack · ⟨A_z⟩_slot  (signed by winding dir).
    # ⟨A_z⟩_slot is the AREA-WEIGHTED mean A_z over the slot's triangles
    # (linear P1 element, so per-tri mean = nodal mean).  Summing the
    # signed per-slot contributions over all slots belonging to a phase
    # gives the phase flux linkage in Wb.  Multiplied by the symmetry
    # multiplier to recover the full-motor value.
    psi_A = psi_B = psi_C = 0.0
    coil_polys_clipped = polys_meshed.get("coils", [])
    n_slot_layout = len(d.winding_layout)
    if n_slot_layout > 0 and coil_polys_clipped:
        slot_pitch_deg_layout = 360.0 / n_slot_layout
        # Same half-pitch offset fix as build_materials: slot CENTRES sit
        # midway between adjacent coil polygons, so the two halves of each
        # wide tooth land on ADJACENT slot_idx values with OPPOSITE direction
        # signs.  Without this offset both halves collapse onto the same
        # slot_idx and inherit the SAME direction → ψ_phase becomes
        # ⟨A_z_left⟩ + ⟨A_z_right⟩ ≈ 2⟨A_z_tooth⟩ instead of the proper
        # ⟨A_z_left⟩ − ⟨A_z_right⟩ = flux LINKED by the coil loop, which
        # over-estimates the back-EMF voltage by 10–50× (user-reported
        # V_peak ≈ 5 kV vs. expected ≈ 140 V).
        half_pitch_layout = slot_pitch_deg_layout * 0.5
        A_tri_mean = (A[mesh.t[0]] + A[mesh.t[1]] + A[mesh.t[2]]) / 3.0
        for i, cp in enumerate(coil_polys_clipped):
            if cp is None or cp.is_empty:
                continue
            try:
                cx, cy = cp.centroid.x, cp.centroid.y
            except Exception:
                continue
            ang = math.degrees(math.atan2(cy, cx))
            if ang < 0: ang += 360.0
            slot_idx = int((ang - half_pitch_layout)
                            / slot_pitch_deg_layout + 0.5) % n_slot_layout
            phase, direction = d.winding_layout[slot_idx]
            tag = DOM_COIL_BASE + i
            idx = np.where(cell_tags == tag)[0]
            if idx.size == 0:
                continue
            slot_area = float(np.sum(areas[idx]))
            if slot_area <= 0:
                continue
            mean_Az = float(np.sum(A_tri_mean[idx] * areas[idx])) / slot_area
            psi_slot = direction * mean_Az      # signed Wb/m  per turn
            if   phase == 'A': psi_A += psi_slot
            elif phase == 'B': psi_B += psi_slot
            elif phase == 'C': psi_C += psi_slot
        # ⟨A_z⟩ has units Wb/m.  Critical scaling note:
        #   _clip_polys_to_sector unpacks each slot's MultiPolygon
        #   (= union of N_wires disjoint wire rectangles) into N_wires
        #   INDIVIDUAL polygons in `coils`.  The loop above therefore
        #   already SUMS direction × ⟨A_z⟩ over all N_wires wires per
        #   slot, so we MUST NOT multiply by N_wires again — that
        #   would over-count flux linkage by N_wires (= 14 for this
        #   motor, producing the ~5 kV phase-voltage artefact the user
        #   pointed out).  Divide by n_parallel to convert the SUMMED
        #   phase flux linkage into PER-BRANCH ψ, which is what the
        #   phase-terminal voltage equation uses.
        sym = (n_sectors if n_sectors > 1 else 1)
        scale = p.stack_length * sym / float(n_parallel)
        psi_A *= scale
        psi_B *= scale
        psi_C *= scale

    # ── Losses ────────────────────────────────────────────────────────────
    # Iron loss: per-cell Bertotti formula using the material's actual
    # kh / kc / ke coefficients from materials_library.yaml.  The Bertotti
    # model splits hysteresis, classical eddy and excess losses and
    # encodes BOTH frequency and B dependence per material grade:
    #
    #   P/V  =  k_h · f · B²   +   k_c · f² · B²   +   k_e · f^1.5 · B^1.5
    #
    # Lamination stacking_factor (≈0.97) discounts the geometric volume
    # to account for the inter-laminate insulation thickness.
    #
    # Copper: 3-phase I²R from R_phase in config.
    freq = sim.get("rpm", 3950) / 60 * pole_pairs       # electrical Hz

    # Pull material objects directly from the library to get Bertotti
    # coefficients, density and stacking factor.
    try:
        from motor_ai_sim import materials as _mat_lib
        from motor_ai_sim.config import get_material_assignments as _gma
        _ma = _gma() or {}
        _stator_mat = _mat_lib.get_steel(_ma.get("stator_core", "20SW1200"))
        _rotor_mat  = _mat_lib.get_steel(_ma.get("rotor_core",  "20SW1200"))
    except Exception as e:
        log.warning("Steel material lookup failed (%s) — falling back to hardcoded Bertotti", e)
        _stator_mat = _rotor_mat = None

    # Per-triangle volumes [m³].  Stacking factor applied at loss step.
    vol = areas * p.stack_length

    def _domain_iron_loss_bertotti(tag: int, mat) -> float:
        """Per-cell Bertotti core loss summed over a domain."""
        mask = cell_tags == tag
        idx = np.where(mask)[0]
        if idx.size == 0 or mat is None:
            return 0.0
        kh = float(getattr(mat, "core_loss_kh", 0.0))
        kc = float(getattr(mat, "core_loss_kc", 0.0))
        ke = float(getattr(mat, "core_loss_ke", 0.0))
        sf = float(getattr(mat, "stacking_factor", 0.97))
        f  = freq
        B  = Bmag_tri[idx]
        # W/m³ per cell
        p_dens = kh * f * B**2 + kc * f**2 * B**2 + ke * f**1.5 * B**1.5
        # Apply lamination stacking factor to the geometric volume
        return float(np.sum(p_dens * vol[idx] * sf))

    P_fe_stator = _domain_iron_loss_bertotti(DOM_STATOR, _stator_mat)
    P_fe_rotor  = _domain_iron_loss_bertotti(DOM_ROTOR,  _rotor_mat)

    # ── Magnet eddy losses — slot-ripple slab model ──────────────────────
    # In a SYNCHRONOUS machine the fundamental armature reaction rotates
    # with the rotor, so in the magnet's frame it is DC — no eddy loss.
    # The dominant AC field a magnet sees is the SLOT RIPPLE, at frequency
    #     f_slot = num_slots × n_mech     [Hz]
    # whose amplitude inside the magnet is a few percent of the local B.
    # The classical conducting-slab result for losses is
    #     P/V = σ · (2π f_slot)² · (η·B)² · d² / 12      [W/m³]
    # with η ≈ 0.10 the empirical "ripple fraction" for unsegmented
    # rotors in concentrated-winding machines (Bianchi & Fornasiero,
    # IEEE TIA 2009).  Segmenting axially reduces this by N_seg²
    # (out of scope for this 2-D solver).
    #
    # Naively plugging the FULL B and electrical f into the slab formula
    # (as a static FEM might suggest) over-estimates the loss by ~3-4
    # orders of magnitude — see the rotor-frame argument above.
    #
    # An exact "finite-difference of A_z over transient frames" turns
    # out to be poisoned by gauge / sector-clipping noise (A_z FLIPS sign
    # by anti-periodicity once per pole pitch even though B stays the
    # same), so for a robust unattended answer we use the slot-ripple
    # slab model.  Per-snapshot A_z mean / per-magnet volume / σ are
    # still surfaced in the response for downstream tooling that does a
    # gauge-aware finite difference (e.g. a full-motor moving-band run).
    try:
        mag_name = _ma.get("magnet")
        _magnet_mat = _mat_lib.get_magnet(mag_name) if mag_name else None
    except Exception:
        _magnet_mat = None
    rho_magnet = float(getattr(_magnet_mat, "density", 7500.0)) if _magnet_mat else 7500.0
    sigma_mag  = float(getattr(_magnet_mat, "sigma",   0.0))    if _magnet_mat else 0.0

    # Per-magnet mean A_z, volume, and ROTOR-FRAME centroid angle for the
    # downstream time-differentiation in /fem_transient.  The rotor-frame
    # angle (= lab centroid angle minus the current rotor_angle_deg) is a
    # stable identifier — the SAME physical magnet has the SAME rotor-frame
    # angle across all transient frames, even though sector clipping may
    # reorder it in the per-frame magnet list.
    A_z_mean_per_magnet:  List[float] = []
    Bmag_mean_per_magnet: List[float] = []
    vol_per_magnet:       List[float] = []
    mag_rotor_angle_deg:  List[float] = []
    A_tri_mean_all = (A[mesh.t[0]] + A[mesh.t[1]] + A[mesh.t[2]]) / 3.0
    for i, (mp, _pol) in enumerate(polys_meshed.get("magnets", [])):
        tag = DOM_MAG_BASE + i
        idx = np.where(cell_tags == tag)[0]
        try:
            lab_ang = math.degrees(math.atan2(mp.centroid.y, mp.centroid.x))
        except Exception:
            lab_ang = 0.0
        rotor_frame_ang = (lab_ang - rotor_angle_deg) % 360.0
        if idx.size == 0:
            A_z_mean_per_magnet.append(0.0)
            Bmag_mean_per_magnet.append(0.0)
            vol_per_magnet.append(0.0)
            mag_rotor_angle_deg.append(rotor_frame_ang)
            continue
        a_w = float(np.sum(areas[idx]))
        A_z_mean_per_magnet.append(
            float(np.sum(A_tri_mean_all[idx] * areas[idx])) / max(a_w, 1e-30))
        # |B|² area-weighted mean — gauge-INVARIANT, unlike ⟨A_z⟩
        Bmag_mean_per_magnet.append(
            float(np.sum(Bmag_tri[idx] * areas[idx])) / max(a_w, 1e-30))
        vol_per_magnet.append(a_w * p.stack_length)
        mag_rotor_angle_deg.append(rotor_frame_ang)

    # ── Magnet eddy losses — slot-ripple slab model on local B ───────────
    #
    # The honest computation would be a sliding-band moving-mesh FEM
    # (rotor mesh rigidly rotates, stator mesh stays fixed, anti-periodic
    # BC on the radial cuts handles the wedge — exactly what Ansys does
    # with its "Dependent Boundary / Bdep = −Bind" master-slave pairing).
    # In our pipeline the mesh is rebuilt from scratch every frame, so
    # neither ∂A_z/∂t (gauge-ambiguous) nor ∂|B|/∂t (mesh-noise
    # dominated) of the per-frame ⟨...⟩-over-magnet gives a stable
    # answer — 24 frames → 1.5 kW, 48 frames → 25 kW peak.  See git
    # commit history for the gauge-and-noise analysis.
    #
    # Pending the sliding-band rewrite we use the classical conducting-
    # slab formula on the LOCAL per-cell B with a small empirical
    # ripple fraction η:
    #     P/V = σ · (2π f_slot)² · (η · B_local)² · d² / 12   [W/m³]
    # with
    #     f_slot = num_slots × n_mech      (slot-ripple in rotor frame)
    #     η      = 0.03                    (typical 24-slot/14-pp FSCW
    #                                       SPMSM — 3 % of local B
    #                                       varies at slot frequency)
    #     B_local = per-cell |B|           (gauge-stable)
    #     d      = magnet radial thickness
    #
    # This is calibrated to match the typical 1–3 % of P_in published
    # value for unsegmented NdFeB in FSCW machines (Bianchi & Fornasiero,
    # IEEE TIA 2009).
    n_mech_solver = sim.get("rpm", 3950) / 60.0
    f_slot_solver = float(p.num_slots) * n_mech_solver
    omega_slot    = 2.0 * math.pi * f_slot_solver
    d_mag_m       = float(p.r_rotor_out - p.r_rotor_in - 0.0012) \
                    if (p.r_rotor_out - p.r_rotor_in) > 0.002 else 0.016
    RIPPLE_FRACTION = 0.03

    P_mag_eddy = 0.0
    for i, _ in enumerate(polys_meshed.get("magnets", [])):
        tag = DOM_MAG_BASE + i
        idx = np.where(cell_tags == tag)[0]
        if idx.size == 0:
            continue
        B_cells = Bmag_tri[idx]
        p_dens = (sigma_mag * omega_slot**2
                  * (RIPPLE_FRACTION * B_cells) ** 2
                  * d_mag_m ** 2 / 12.0)
        P_mag_eddy += float(np.sum(p_dens * vol[idx]))

    mult = n_sectors if n_sectors > 1 else 1
    P_fe_total = (P_fe_stator + P_fe_rotor) * mult
    P_mag_total = P_mag_eddy * mult

    # Copper loss — phase currents × R_phase  (×3 phases).  Uses the
    # I_phase_rms passed in (NOT the config value) so the simulation
    # actually reflects user-set current changes.
    R_phase = float(wind.get("phase_resistance_ohm", 0.018))
    P_cu = 3 * I_phase_rms ** 2 * R_phase

    P_loss_total = P_fe_total + P_mag_total + P_cu
    rpm = sim.get("rpm", 3950)
    P_mech = T_em_Nm * 2 * math.pi * rpm / 60
    eff = P_mech / max(P_mech + P_loss_total, 1e-6) if P_mech > 0 else 0.0

    # ── Outlines (for the renderer; matches /mesh/build2d format) ─────────
    polys_for_outlines = getattr(classify_fn, "polys", polys)

    # Remap per-magnet + per-coil tags back to the visualisation ids
    # (DOM_MAG_N / DOM_MAG_S / DOM_COIL) that the frontend already knows
    # how to colour.
    polarities = [pol for _mp, pol in polys_meshed.get("magnets", [])]
    cell_tags_vis = cell_tags.copy()
    mask_coil = cell_tags_vis >= DOM_COIL_BASE
    if np.any(mask_coil):
        cell_tags_vis[mask_coil] = DOM_COIL
    mask = (cell_tags_vis >= DOM_MAG_BASE) & (cell_tags_vis < DOM_COIL_BASE)
    if np.any(mask):
        idx = (cell_tags_vis[mask] - DOM_MAG_BASE).astype(int)
        cell_tags_vis[mask] = np.array(
            [DOM_MAG_N if (j < len(polarities) and polarities[j] > 0) else DOM_MAG_S
             for j in idx], dtype=cell_tags_vis.dtype)

    # ── Per-triangle J_z [A/m²] for the field renderer (J mode) ───────────
    # Each coil cell carries the J_z of its per-coil material entry;
    # everything else is zero.  Used by FemFieldChart's "J" mode to draw
    # the Ansys-style red/blue/green current-density map.
    J_z_per_tri = np.zeros(cell_tags.shape[0], dtype=np.float32)
    coil_tags = np.where(cell_tags >= DOM_COIL_BASE)[0]
    if coil_tags.size:
        for tag in np.unique(cell_tags[coil_tags]):
            mat_t = mats.get(int(tag))
            if mat_t is None:
                continue
            J_z_per_tri[cell_tags == tag] = float(mat_t.J_z)

    return {
        "ok": True,
        "rotor_angle_deg": rotor_angle_deg,
        "gamma_deg":       gamma_deg,
        "n_sectors":       n_sectors,
        "symmetry_mult":   mult,
        "n_vertices":      int(mesh.p.shape[1]),
        "n_triangles":     int(mesh.t.shape[1]),
        "vertices":        mesh.p.T.tolist(),       # metres
        "triangles":       mesh.t.T.tolist(),
        "domain_per_tri":  cell_tags_vis.tolist(),
        "A_z_per_node":    A.tolist(),               # Wb/m
        "Bmag_per_tri":    Bmag_tri.tolist(),
        "J_z_per_tri":     J_z_per_tri.tolist(),     # A/m² — coils only
        # ── Per-magnet bulk quantities for transient-mode honest eddy ─────
        "A_z_mean_per_magnet":   A_z_mean_per_magnet,   # Wb/m per magnet
        "Bmag_mean_per_magnet":  Bmag_mean_per_magnet,  # T per magnet (gauge-invariant)
        "vol_per_magnet":        vol_per_magnet,        # m³ per magnet
        "mag_rotor_angle_deg":   mag_rotor_angle_deg,   # rotor-frame ID (deg)
        "sigma_magnet":          float(sigma_mag),      # S/m
        "extent": [
            float(mesh.p[0].min()), float(mesh.p[0].max()),
            float(mesh.p[1].min()), float(mesh.p[1].max()),
        ],
        "polys_for_outlines": polys_for_outlines,
        # ── Physics quantities (with n_sectors multiplier already applied) ──
        "T_em_Nm":       round(T_em_Nm, 4),
        "P_cu_W":        round(P_cu, 1),
        "P_fe_W":        round(P_fe_total, 1),
        "P_mag_eddy_W":  round(P_mag_total, 1),
        "P_loss_total_W":round(P_loss_total, 1),
        "P_mech_W":      round(P_mech, 1),
        "efficiency":    round(eff, 4),
        "freq_Hz":       round(freq, 2),
        "rpm":           rpm,
        "solve_time_s":  round(t_solve, 2),
        "total_time_s":  round(_t.time() - t_start, 2),
        # Demagnetisation report — each entry is a magnet whose worst-cell
        # H came within 15 % of the BH-curve knee.  demagnetised=True means
        # the magnet has crossed the knee and is irreversibly weakened.
        "demag_report":      demag_report,
        "demag_coef_per_tri": demag_coef_per_tri.tolist(),
        # Per-phase flux linkages [Wb].  Used by the transient endpoint
        # to derive V_phase(t) = R·I + dψ/dt across the period.
        "psi_A_Wb":          float(psi_A),
        "psi_B_Wb":          float(psi_B),
        "psi_C_Wb":          float(psi_C),
    }
