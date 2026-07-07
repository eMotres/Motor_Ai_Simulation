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

# Single source of truth for the d-axis phase offset: the electrical angle added
# to (rotor_angle·pole_pairs + γ) so that γ=0 lands on the q-axis.  MUST be
# identical across every solve path (transient currents, static field, eddy) —
# otherwise the field/torque would be at a different phase per path.
#
# = ideal 90° d→q rotation  +  the motor's rotor-d-axis-vs-phase-A GEOMETRIC
# offset.  That offset is TOPOLOGY-dependent (pole/slot/winding) and must be
# calibrated per motor: a hardcoded 90 left γ=0 ~18° off the q-axis for the
# 20-pole/24-slot motor, so torque peaked at γ≈+38° and "kept rising with γ"
# instead of peaking at a small-+ MTPA.
# Calibrated 2026-06-16 for the 20p/24s topology via a no-load (I=0) run:
# psi_A(θ) peaks at θ*=34.2° mech (342° el) ⇒ DAXIS = (90 − 342) ≡ 108° (mod 360).
# Now γ=0 = q-axis and MTPA sits at a small + γ (~+20° for this motor).
# RECALIBRATE only if the pole/slot/winding TOPOLOGY changes (dimension sweeps
# don't move it): run fem_transient_sliding_band(I_phase_rms=0); θ* = rotor angle
# of max psi_A_Wb; DAXIS_SHIFT_DEG = (90 − θ*·pole_pairs) mod 360.
DAXIS_SHIFT_DEG = 108.0

# Number of equally-spaced nodes on the sliding-band slip circle (r = mid_r).
# Shared by in_band (exterior) and out_band (hole) so the two half-meshes get
# IDENTICAL matching nodes there.  This count drives the angular resolution of
# the rotor-rotation merge, so lowering it raises torque ripple — keep it high.
# The VISUAL band width is controlled by the radial air-gap size field, not this.
#
# NB: the TRANSIENT solver does NOT use this fixed value — it computes an
# adaptive n_slip_eff = pole_pairs·per_period (per_period a multiple of 24, ≥120)
# so the slip ring is divisible by the pole-pair count for ANY motor and the
# electrical period tiles EXACTLY.  1008 = 14·72 tiled cleanly only for 14
# pole-pairs (28 poles); a 10-pp / 20-pole motor got 1008/10 = 100.8 → a coarse,
# 24-skipping step grid that under-resolved ripple & efficiency.  This constant
# stays the fallback for the static/eddy paths (_simplify_polys default) that
# don't know pole_pairs.
_N_SLIP = 1008

# Structured slip strips (explicit offset rings at mid±δ).  Tested as a fix for
# the order-6 sliding artifact: strips came out perfectly regular, but ord6 was
# UNCHANGED (1.94 vs 1.79) and the per-frame local stress balance got WORSE
# (frame-0 total −4.2 vs −0.1 N·m) → OFF by default; kept for experiments.
_SB_STRUCTURED_STRIPS = False

# Moving-band strip half-width as a fraction of the FULL gap (δ = frac·gap).
# 0.25 → strip spans half the gap (one closed-form row).  Smaller → thinner
# strip + more free rows per side (better radial resolution of the gap).
_SB_BAND_DELTA_FRAC = 0.25

# Rotational curve-mesh periodicity (pole->pole / slot->slot setPeriodic) in
# the SB half-builds — diagnostic gate.
_SB_ROT_PERIODICITY = True

# Template-copy each pole (rotor) / slot (stator): mesh ONE period, then rotate-
# copy + weld it so every period has a BIT-IDENTICAL interior, not just matched
# boundaries (setPeriodic).  Kills the pole-to-pole mesh-discretisation variance
# that leaves a ~1.2-1.6x residual on the loss waveform.  Per-half opt-in so the
# rotor can be validated before the (winding-phase-aware) stator.
import os as _os_sb
_SB_POLE_COPY_ROTOR  = _os_sb.environ.get("SB_POLE_COPY_ROTOR",  "0") == "1"
_SB_POLE_COPY_STATOR = _os_sb.environ.get("SB_POLE_COPY_STATOR", "0") == "1"

# True moving band (two uniform rings + closed-form re-stitched strip) vs the
# legacy merged single ring.  Diagnostic result: ord6 identical in both (the
# artifact is NOT the coupling); the one-row strip biases frame-local torque
# (frame0 -2.8 vs -0.1 N*m at I=0), so MERGED stays the default until the
# two-mesh gap bias is resolved.
_SB_MOVING_BAND = False

# Harmonic air-gap macroelement (Davat): replace the single-layer moving-band strip
# with the ANALYTIC Laplace solution of the gap annulus (block-circulant coupling,
# DFT-diagonal 2×2 per-harmonic blocks; rotor rotation = smooth phase e^{ikφ}).
# Implemented + validated (per-harmonic stiffness vs FEM annulus; mean torque matches
# the band).  RESULT (2026-06-30, #141): it does NOT reduce torque ripple — measured
# WORSE than the band at the same mesh (raw 25.6 % vs 17.6 %), because the dominant
# ripple is the FEM half-mesh teeth/slot discretisation (present in the field, seen by
# every torque contour), NOT the gap coupling; the band's coarse strip happens to
# low-pass it while the macroelement faithfully captures + (via virtual work) amplifies
# it.  Kept behind this flag (default OFF → production uses the band) for reference.
_SB_AIRGAP_MACRO = False
# Structured (concentric-ring) air gap: partition EACH half-gap (rotor OD->R1 and
# R2->stator bore) into `gap_layers` thin annular rows bounded by uniform N-gon rings
# on the slip angular grid -> the gap meshes as an ANSYS-style structured band
# (concentric circles + near-radial spokes) instead of free Delaunay triangles.  The
# slip band R1..R2 is untouched.  EXPERIMENTAL (default OFF, env SB_STRUCTURED_GAP=1):
# the dominant torque-ripple source is the tooth/slot half-mesh discretisation, NOT the
# gap coupling (see _SB_AIRGAP_MACRO / #141), so this mainly regularises the gap mesh
# and the slip re-pairing noise -- MEASURE before trusting it to move ripple.
_SB_STRUCTURED_GAP = _os_sb.environ.get("SB_STRUCTURED_GAP", "0") == "1"
# STRUCTURED gap ε retract (mm): how far the gap-facing iron is pulled off the
# transfinite cell arcs so its fuzzy polygon vertices do not subdivide them.
# None → the code default (0.01).  Diagnostic override hook (torque/ε studies).
_SG_EPS_OVERRIDE = None
_SLIP_PER_PERIOD_OVERRIDE = 0   # 0 = adaptive formula; >0 forces slip nodes/period (ring density)

# ── Torque-band diagnostic (off by default; set ['on']=True before a solve to
# collect the per-frame Arkkio torque over radial sub-bands of the gap, to
# localise where parasitic ripple comes from — e.g. the slip-ring interface).
_TORQUE_DIAG = {"on": False, "full": [], "rotor": [], "stator": [],
                "iface": [], "rinner": [], "router": [],
                # per-frame angular profile of the Arkkio integrand (PHYSICAL
                # angle bins; rotor-half elements shifted by +θ_eff) — to see
                # WHERE around the gap the parasitic torque is generated.
                "ang_bins": 36, "ang_prof": []}


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
    sigma: float = 0.0   # [S/m]   electrical conductivity — solid-conductor eddy
                         #         currents (copper, magnet, shaft).  0 = no eddy
                         #         (air / laminated iron treated as σ=0).
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
                     n_slip: Optional[int] = None,
                     band_mode: str = "merged",
                     gap_layers: float = 2.0,
                     structured_gap: bool = False) -> dict:
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

    def _drop_slivers(g, min_area_mm2: float = 1e-2):
        # CadQuery/shapely boolean noise can leave near-zero-area sliver parts
        # (e.g. a 4-point ribbon ON the rotor surface).  OCC turns those into
        # degenerate faces/edges in the air gap → locally shredded mesh.
        try:
            from shapely.geometry import MultiPolygon as _MP
            if hasattr(g, "geoms"):
                parts = [p for p in g.geoms if p.area >= min_area_mm2]
                if not parts:
                    return g
                return parts[0] if len(parts) == 1 else _MP(parts)
            return g
        except Exception:
            return g

    for k in SIMPLIFY_KEYS:
        if polys.get(k) is not None:
            try:
                g = polys[k].simplify(tol_mm, preserve_topology=True)
                g = _decimate_poly_by_angle(g, normal_dev_deg)
                out[k] = _drop_slivers(g)
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
            from shapely.geometry import Polygon as _SPoly2, MultiPolygon as _SMulti2
            _N = int(n_slip) if n_slip and n_slip > 0 else _N_SLIP
            def _ring_pts(_r):
                return [(_r * math.cos(2*math.pi*i/_N),
                         _r * math.sin(2*math.pi*i/_N)) for i in range(_N)]
            mid_ring  = _ring_pts(mid_r)
            rout_ring = _ring_pts(r_outer)

            # ── STRUCTURED slip strips ────────────────────────────────────
            # Free meshing leaves the band next to the slip ring RAGGED (no
            # clean node rows) and its frozen irregular pattern beats against
            # the slip alignment as the rotor slides → parasitic low-order
            # torque.  Bound a thin annulus strip on EACH side of the slip
            # ring by explicit offset rings on the SAME angular grid — the
            # strip then meshes as a regular, pole/slot-agnostic row.
            _r_ro_est = 0.0
            for _g in rotor_solids:
                _xy = (_g.exterior.coords if hasattr(_g, "exterior")
                       else _g.geoms[0].exterior.coords)
                _r_ro_est = max(_r_ro_est,
                                max(math.hypot(x, y) for x, y in _xy))
            _r_si_est = float("inf")
            if out.get("stator") is not None:
                _sg = (out["stator"].geoms[0] if hasattr(out["stator"], "geoms")
                       else out["stator"])
                for _intr in _sg.interiors:
                    _r_si_est = min(_r_si_est,
                                    min(math.hypot(x, y) for x, y in _intr.coords))
            _gap_est = (_r_si_est - _r_ro_est) \
                if (_r_si_est < float("inf") and _r_ro_est > 0) else 0.0
            _delta = min(max(_SB_BAND_DELTA_FRAC * _gap_est, 0.04), 0.4) if _gap_est > 0.05 else 0.0
            # STRUCTURED gap: per-request param OR the global env flag.
            _do_struct = bool(structured_gap) or _SB_STRUCTURED_GAP

            def _parts(_g):
                return list(_g.geoms) if hasattr(_g, "geoms") else [_g]

            if band_mode == "moving" and _delta > 0 \
                    and (mid_r - _delta) > _r_ro_est + 0.02 \
                    and (mid_r + _delta) < _r_si_est - 0.02:
                # ── TRUE MOVING BAND geometry ────────────────────────────
                # The rotor half's air stops at a uniform N-gon ring R1
                # (r = mid−δ, rotates rigidly with the rotor); the stator
                # half's air starts at ring R2 (r = mid+δ, stationary).
                # The annulus R1..R2 belongs to NEITHER mesh — the solver
                # re-stitches it EVERY frame in closed form between the two
                # UNIFORM rings, so the m-dependent part of the operator is
                # pattern-invariant (the node-merge formulation's frozen
                # irregular fans were the order-6 ripple source).
                _r1 = mid_r - _delta
                _r2 = mid_r + _delta
                if _do_struct:
                    # STRUCTURED gap: partition each half-gap into `gap_layers`
                    # concentric annular rows bounded by uniform N-gon rings on the
                    # slip angular grid → the gap meshes as ANSYS-style concentric
                    # circles + near-radial spokes.  The slip band R1..R2 is left
                    # intact; rows sit strictly inside the clean gap (a thin
                    # transition sliver to the iron stays free-meshed).
                    _K = max(1, int(round(float(gap_layers))))
                    _lo = _r_ro_est + 0.02          # just above the rotor OD
                    _hi = _r_si_est - 0.02          # just below the stator tooth tips
                    _radii_in  = sorted({_lo + (_r1 - _lo) * i / _K for i in range(_K)}
                                        | {_r1})                                   # _lo .. R1
                    _radii_out = sorted({_r2}
                                        | {_r2 + (_hi - _r2) * i / _K for i in range(1, _K + 1)})  # R2 .. _hi
                    _rings_in  = [_ring_pts(r) for r in _radii_in]
                    _rings_out = [_ring_pts(r) for r in _radii_out]
                    # rotor-side in_band: inner core (disk(radii_in[0]) − solids) + rows up to R1
                    _core = _SPoly2(_rings_in[0])
                    if rotor_solids:
                        _core = _core.difference(_uu(rotor_solids))
                    if not _core.is_valid: _core = _core.buffer(0)
                    _in_parts = _parts(_core)
                    for _i in range(len(_rings_in) - 1):
                        _in_parts.append(_SPoly2(_rings_in[_i + 1], [_rings_in[_i]]))
                    out["in_band"] = _SMulti2(_in_parts)
                    # stator-side out_band: rows from R2 + outer shell (r_outer − stator)
                    _out_parts = []
                    for _i in range(len(_rings_out) - 1):
                        _out_parts.append(_SPoly2(_rings_out[_i + 1], [_rings_out[_i]]))
                    _shell = _SPoly2(rout_ring, [_rings_out[-1]])
                    if out.get("stator") is not None:
                        _shell = _shell.difference(out["stator"])
                    if not _shell.is_valid: _shell = _shell.buffer(0)
                    _out_parts += _parts(_shell)
                    out["out_band"] = _SMulti2(_out_parts)
                    out["band_radii_mm"] = [_r1, _r2]
                    out["transfinite_ring_radii_mm"] = sorted(set(_radii_in + _radii_out))
                else:
                    in_band = _SPoly2(_ring_pts(_r1))
                    if rotor_solids:
                        in_band = in_band.difference(_uu(rotor_solids))
                    if not in_band.is_valid: in_band = in_band.buffer(0)
                    out["in_band"] = in_band

                    out_band = _SPoly2(rout_ring, [_ring_pts(_r2)])
                    if out.get("stator") is not None:
                        out_band = out_band.difference(out["stator"])
                    if not out_band.is_valid: out_band = out_band.buffer(0)
                    out["out_band"] = out_band
                    out["band_radii_mm"] = [_r1, _r2]
                    out["transfinite_ring_radii_mm"] = [_r1, _r2]
            elif _SB_STRUCTURED_STRIPS and _delta > 0 \
                    and (mid_r - _delta) > _r_ro_est + 0.02 \
                    and (mid_r + _delta) < _r_si_est - 0.02:
                ring_in  = _ring_pts(mid_r - _delta)
                ring_out = _ring_pts(mid_r + _delta)
                strip_r = _SPoly2(mid_ring, [ring_in])     # rotor-side strip
                inner = _SPoly2(ring_in)
                if rotor_solids:
                    inner = inner.difference(_uu(rotor_solids))
                if not inner.is_valid: inner = inner.buffer(0)
                out["in_band"] = _SMulti2([strip_r] + _parts(inner))

                strip_s = _SPoly2(ring_out, [mid_ring])    # stator-side strip
                outer = _SPoly2(rout_ring, [ring_out])
                if out.get("stator") is not None:
                    outer = outer.difference(out["stator"])
                if not outer.is_valid: outer = outer.buffer(0)
                out["out_band"] = _SMulti2([strip_s] + _parts(outer))
                # The offset rings must keep EXACTLY their seeded vertices
                # (transfinite 2/edge) or gmsh re-subdivides them and the
                # strip alignment is lost.  The mesh builder reads this.
                out["transfinite_ring_radii_mm"] = [mid_r - _delta, mid_r + _delta]
            elif _do_struct and _r_ro_est > 0 and _r_si_est < float("inf") and _gap_est > 0.05:
                # ── STRUCTURED (mapped) gap — ROUTE A ─────────────────────────
                # DO NOT partition the gap into thin shapely annuli here (that is
                # the route-B approach that fails: OCC merges sub-tolerance rings
                # away and the survivors come back with ragged node counts).
                # Instead: EXCLUDE the gap annulus from in_band / out_band and
                # hand the mesh builder a spec so it builds the gap as concentric
                # cylinder-sector CELLS (2K radial × S angular OCC surfaces) IN
                # THE SAME occ.fragment as the iron, then sets each cell
                # transfinite → EXACTLY 2K uniform rings, one conforming mesh.
                #   rotor half owns r_ro→mid_r (K rings), stator half owns
                #   mid_r→r_si (K rings) → 2K total.  The slip ring at mid_r is
                #   built on the uniform S·M = n_slip grid so the sliding coupling
                #   (_ring node identification) is untouched.
                _K = max(1, int(round(float(gap_layers))))
                # ── CLEAN arc-boundary route A (no ε retract, no filler) ──────
                # The gap cells only mesh transfinite if NO foreign vertex lands
                # inside a cell arc.  Rather than pull the iron ε OFF the arcs and
                # bridge the void with a free-meshed filler ring (the OLD approach
                # — it left two ugly rings of sliver triangles AND blunted the
                # tooth tips → torque deficit), we now keep the iron at its TRUE
                # gap radius and rebuild its gap-facing ring as circle arcs
                # COINCIDENT with the cells' arc (snapped to the seam grid) in
                # build_mesh_from_polygons::_iron_arc_ring_occ.  So ε = 0 here:
                # no retract clip, no filler, no post-mesh ε-ring reclassify.
                # (_SG_EPS_OVERRIDE still forces the legacy retract for debugging.)
                _eps = float(_SG_EPS_OVERRIDE) if _SG_EPS_OVERRIDE else 0.0
                _ro_c = _r_ro_est - _eps      # = r_ro when ε=0 (no retract)
                _si_c = _r_si_est + _eps      # = r_si when ε=0

                def _drop_tiny(g, amin=0.01):
                    # Clipping the annulus off a polygonal (chorded) OD leaves a
                    # crescent SLIVER between every chord and the true circle.
                    # These are ~0 area — drop them so only the real pockets /
                    # shaft / outer shell remain (else OCC shreds the gap).
                    ps = list(g.geoms) if hasattr(g, "geoms") else [g]
                    ps = [q for q in ps
                          if q.geom_type == "Polygon" and q.area >= amin]
                    if not ps:
                        return None
                    return ps[0] if len(ps) == 1 else _SMulti2(ps)

                # Legacy ε retract of the iron — ONLY when _SG_EPS_OVERRIDE forces
                # ε>0 (debugging the old path).  With the default ε=0 the iron
                # keeps its true OD/bore and the arc-ring OCC build handles
                # conformity, so we skip the retract clip entirely.
                if _eps > 0.0:
                    if out.get("rotor") is not None:
                        _rc = out["rotor"].intersection(_SPoly2(_ring_pts(_ro_c)))
                        if not _rc.is_empty:
                            out["rotor"] = (_rc.buffer(0) if not _rc.is_valid else _rc)
                    if out.get("stator") is not None:
                        _sc = out["stator"].difference(_SPoly2(_ring_pts(_si_c)))
                        if not _sc.is_empty:
                            out["stator"] = (_sc.buffer(0) if not _sc.is_valid else _sc)
                # in_band = free inner air (disk(mid_r) − solids) with the pure
                # gap RING r_ro→mid_r SUBTRACTED (a clean annulus polygon), so the
                # transfinite cells own that ring.  We SUBTRACT an annulus rather
                # than INTERSECT disk(r_ro): the pockets lie fully below r_ro so
                # their boundaries are untouched (no disk-arc points injected →
                # the OCC converter stays happy).  Then clip the air to r_ro−ε too
                # (so no air boundary lands on the cell arcs either) and drop
                # slivers.  The µm iron→cell ring is welded post-mesh.
                _gap_ring_in = _SPoly2(mid_ring, [_ring_pts(_r_ro_est)])
                in_band = _SPoly2(mid_ring)
                if rotor_solids:
                    in_band = in_band.difference(_uu(rotor_solids))
                in_band = in_band.difference(_gap_ring_in)
                # Legacy ε retract only: clip the air a further ε below r_ro so no
                # air vertex sits on the cell arc.  With ε=0 the gap-ring
                # subtraction already bounds the air at r_ro; the arc iron owns
                # that circle, so skip the extra clip.
                if _eps > 0.0:
                    in_band = in_band.intersection(_SPoly2(_ring_pts(_ro_c)))
                if not in_band.is_valid: in_band = in_band.buffer(0)
                in_band = _drop_tiny(in_band)
                if in_band is not None:
                    # 10 µm simplify collapses near-duplicate points the boolean
                    # ops leave (else the OCC loop-closure fails after the sector
                    # clip). 10 µm ≪ the 200 µm gap.
                    in_band = in_band.simplify(0.01, preserve_topology=True)
                    out["in_band"] = in_band
                # out_band = free outer air (annulus mid_r→r_out − stator) with the
                # gap ring mid_r→r_si SUBTRACTED, so the cells own it, then clipped
                # to start at r_si+ε.  Slot openings sit above r_si (untouched).
                _gap_ring_out = _SPoly2(_ring_pts(_r_si_est), [mid_ring])
                out_band = _SPoly2(rout_ring, [mid_ring])
                if out.get("stator") is not None:
                    out_band = out_band.difference(out["stator"])
                out_band = out_band.difference(_gap_ring_out)
                # Legacy ε retract only (see in_band above).  With ε=0 the arc
                # iron owns the r_si circle, so skip the extra clip.
                if _eps > 0.0:
                    out_band = out_band.difference(_SPoly2(_ring_pts(_si_c)))
                if not out_band.is_valid: out_band = out_band.buffer(0)
                out_band = _drop_tiny(out_band)
                if out_band is not None:
                    out_band = out_band.simplify(0.01, preserve_topology=True)
                    out["out_band"] = out_band
                # Spec consumed by build_mesh_from_polygons (per half).  It knows
                # its own n_sectors and picks S (sectors/wedge) + M (arc divisions)
                # so S·M·n_sectors = n_slip → mid ring lands on the global grid.
                out["structured_gap_spec"] = {
                    "r_ro": float(_r_ro_est), "mid": float(mid_r),
                    "r_si": float(_r_si_est), "K": int(_K), "n_slip": int(_N),
                    "eps": float(_eps),
                }
                # No transfinite_ring_radii_mm here — the cells carry their own
                # transfinite seeding; the old ring-radii path is route B.
            else:
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
            parts = list(g.geoms) if hasattr(g, "geoms") else [g]
            if len(parts) == 1:
                return parts[0].intersection(wedge)
            # Clip TOUCHING parts one by one — a single overlay on the whole
            # MultiPolygon node-merges the parts and DISSOLVES their shared
            # boundaries (e.g. the structured slip strips' offset rings),
            # erasing the explicit ring seeding from the model.
            clipped = []
            for p in parts:
                c = p.intersection(wedge)
                if c is None or c.is_empty:
                    continue
                clipped.extend(list(c.geoms) if hasattr(c, "geoms") else [c])
            clipped = [c for c in clipped
                       if c.geom_type == "Polygon" and c.area > 1e-9]
            if not clipped:
                return None
            return clipped[0] if len(clipped) == 1 else _SMPoly(clipped)
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
    # Structured-gap (route-A) spec: give each half its OWN gap band range so the
    # mesh builder fills only that half's slice with transfinite cells.
    #   rotor half:  r_ro → mid   (K rings, inner half of the gap)
    #   stator half: mid  → r_si  (K rings, outer half of the gap)
    _sg = polys.get("structured_gap_spec")
    if _sg is not None:
        _base = {"K": int(_sg["K"]), "n_slip": int(_sg["n_slip"]),
                 "eps": float(_sg.get("eps", 0.0))}
        polys_r["structured_gap_spec"] = dict(
            _base, r_lo=float(_sg["r_ro"]), r_hi=float(_sg["mid"]), half="rotor")
        polys_s["structured_gap_spec"] = dict(
            _base, r_lo=float(_sg["mid"]), r_hi=float(_sg["r_si"]), half="stator")
    return polys_s, polys_r


def _replicate_periodic_half(polys_half, period_deg, n_copies, common_kw, kind):
    """Mesh ONE pole/slot wedge, then rotate-copy + weld it into ``n_copies``
    so every period has a BIT-IDENTICAL interior (not just matched boundary
    curves as setPeriodic gives).  This removes the pole/slot-to-pole mesh-
    discretisation variance that leaves a residual on the loss waveform.

    ``kind`` ∈ {"rotor","stator"}:
      • rotor  — one magnet per period; polarity alternates ±1 per pole.
      • stator — coils per slot; the PHASE/current is NOT geometry-periodic,
                 it comes from the winding layout via the per-slot tag, so we
                 only need to renumber DOM_COIL_BASE+i in slot order.

    Returns (skfem MeshTri, per-cell tags, classify-like namespace with .polys)
    matching build_mesh_from_polygons' interface.  Raises on any inconsistency
    so the caller can fall back to the standard sector build.

    How the seam weld is made node-exact (the hard part):
      • mesh ONE period with the radial cuts set TRANSFINITE (equal node count
        on both edges — build_mesh_from_polygons(transfinite_radial_cuts=True)),
      • SNAP the right edge onto the EXACT rotation of the left edge (gmsh still
        leaves a ~10 µm offset; the shaft-centre apex is shared),
      • rotate-copy and weld at a tight 0.1 µm tol.
    Validated: 1-period vs 2-period ground truth edge residual 1.34/1.17 → 1.00,
    back-EMF & Pmag converge to the standard mesh at fine resolution — i.e. the
    pole-to-pole mesh variance is fully removed, physics unchanged.  Off by
    default (env SB_POLE_COPY_ROTOR/STATOR); the standard build is untouched.
    """
    import types
    from shapely.affinity import rotate as _srot
    from scipy.spatial import cKDTree
    from skfem import MeshTri

    _ns_one = int(round(360.0 / period_deg))
    # Mesh ONE period.  Its two radial cuts are made periodic by the sector
    # machinery (n_sectors>1), so the right edge == the left edge rotated by
    # the period → rotated copies coincide at the seam to floating precision.
    mesh1, tags1, cls1 = build_mesh_from_polygons(
        polys_half, n_sectors=_ns_one, rotational_period_deg=None,
        transfinite_radial_cuts=True, **common_kw)
    P1 = np.asarray(mesh1.p, float).copy()   # (2, nN) metres
    T1 = np.asarray(mesh1.t, int)            # (3, nE)
    tags1 = np.asarray(tags1, int)
    polys1 = getattr(cls1, "polys", polys_half)
    nN = P1.shape[1]

    # Snap the canonical's RIGHT radial edge onto the EXACT rotation of its LEFT
    # edge.  Transfinite gives both edges the same node count + radii to ~10 µm,
    # but not bit-exact — so rotated copies miss the weld at 0.1 µm tol.  After
    # this snap the right edge == rotate(left, period) exactly → every copy's
    # left edge coincides with the previous copy's right edge to machine
    # precision and they weld cleanly (shaft-centre apex is shared by all).
    _ang1 = np.degrees(np.arctan2(P1[1], P1[0])) % 360.0
    _r1 = np.hypot(P1[0], P1[1])
    _Li = np.where((_ang1 < 1e-3) & (_r1 > 1e-9))[0]              # left edge (no apex)
    _Ri = np.where(np.abs(_ang1 - period_deg) < 1e-3)[0]         # right edge
    _nsnap = 0
    if _Li.size and _Ri.size:
        _ca, _sa = math.cos(math.radians(period_deg)), math.sin(math.radians(period_deg))
        _ideal = np.vstack([_ca * P1[0, _Li] - _sa * P1[1, _Li],
                            _sa * P1[0, _Li] + _ca * P1[1, _Li]])  # rotate(left)
        _d, _j = cKDTree(_ideal.T).query(P1[:, _Ri].T)
        _ok = _d < 5e-5                                          # ≤ 50 µm ≪ node gap
        if _ok.any():
            P1[:, _Ri[_ok]] = _ideal[:, _j[_ok]]
        _nsnap = int(_ok.sum())
        log.info("pole-copy %s: snapped %d/%d right-edge nodes onto rotate(left)",
                 kind, _nsnap, _Ri.size)

    # Weld-quality gate.  Only the SNAPPED seam nodes (right == rotate(left) to
    # machine precision) weld when the copies are stacked; an un-snapped node
    # stays at its meshed position and leaves a hairline CRACK at every seam.
    # This requires the wedge's two radial cuts to carry the SAME node
    # distribution — true when the two cuts traverse identical geometry (the
    # rotor pole wedge: 39/39).  It FAILS when OCC splits the two cuts
    # differently (the stator slot wedge: the right cut lumps yoke+outer-air
    # into one coarse segment and runs fine along the slot, the left runs
    # through clean tooth iron — 7/24).  A rank-forced snap would drag seam
    # nodes >10 mm and invert triangles, so instead we BAIL and let the caller
    # fall back to the standard (stitched / sector) build, which is crack-free
    # and high quality — just not a bit-identical copy.  The rotor copy, which
    # is what actually removes the moving-part loss ripple, is unaffected.
    if _Li.size != _Ri.size or _nsnap < _Ri.size - 1:
        raise ValueError(
            f"{kind} wedge radial cuts not periodic-weldable "
            f"(left {_Li.size} vs right {_Ri.size} nodes, {_nsnap} aligned) — "
            f"OCC split the two cuts differently; standard build instead")

    if kind == "rotor":
        feat_lo, feat_hi, base, feat_key = DOM_MAG_BASE, DOM_COIL_BASE, DOM_MAG_BASE, "magnets"
    else:
        feat_lo, feat_hi, base, feat_key = DOM_COIL_BASE, 10**9, DOM_COIL_BASE, "coils"
    canon_feat = sorted(int(t) for t in np.unique(tags1) if feat_lo <= int(t) < feat_hi)
    canon_polys = list(polys1.get(feat_key, []))
    nfeat = len(canon_feat)
    if nfeat == 0 or len(canon_polys) != nfeat:
        raise ValueError(f"{kind} period has {nfeat} feature tags but "
                         f"{len(canon_polys)} {feat_key} polys (straddled cut?)")

    allP, allT, allTags, feat_polys = [], [], [], []
    for k in range(n_copies):
        a = math.radians(k * period_deg)
        c, s = math.cos(a), math.sin(a)
        allP.append(np.vstack([c * P1[0] - s * P1[1], s * P1[0] + c * P1[1]]))
        allT.append(T1 + k * nN)
        tk = tags1.copy()
        for j, ft in enumerate(canon_feat):
            tk[tags1 == ft] = base + k * nfeat + j      # unique per-copy feature id
        allTags.append(tk)
        for item in canon_polys:
            if kind == "rotor":                          # magnets: (poly, polarity)
                poly, meta = item
                feat_polys.append((_srot(poly, k * period_deg, origin=(0.0, 0.0)),
                                   meta * ((-1) ** k)))   # spoke polarity alternates
            else:                                         # coils: bare polygon;
                feat_polys.append(_srot(item, k * period_deg, origin=(0.0, 0.0)))
                                                          # phase comes from the layout

    P = np.hstack(allP); T = np.hstack(allT); tags = np.concatenate(allTags)

    # Weld coincident nodes at the rotated seams (now node-exact after the snap).  The seam nodes coincide to
    # rotation-arithmetic precision (~1e-12 m), and every DISTINCT node is ≥
    # min_size (~0.1 mm) away, so a TIGHT absolute tol welds only true twins —
    # a loose (element-scaled) tol over-merged 26 % of nodes into collapsed
    # slivers and corrupted the field.
    _tolw = 1e-7                                  # 0.1 µm
    pairs = cKDTree(P.T).query_pairs(_tolw, output_type="ndarray")
    parent = np.arange(P.shape[1])
    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for u, v in pairs:
        ru, rv = _find(int(u)), _find(int(v))
        if ru != rv:
            parent[max(ru, rv)] = min(ru, rv)
    roots = np.array([_find(i) for i in range(P.shape[1])])
    uniq, inv = np.unique(roots, return_inverse=True)
    Pw = P[:, uniq]; Tw = inv[T].astype(np.int64)
    # Drop any triangle that collapsed during the weld (two corners merged —
    # happens where many pie-slice copies converge, e.g. a solid-shaft hub).
    _ar = 0.5 * np.abs(
        (Pw[0, Tw[1]] - Pw[0, Tw[0]]) * (Pw[1, Tw[2]] - Pw[1, Tw[0]])
        - (Pw[0, Tw[2]] - Pw[0, Tw[0]]) * (Pw[1, Tw[1]] - Pw[1, Tw[0]]))
    _degen = ((Tw[0] == Tw[1]) | (Tw[1] == Tw[2]) | (Tw[0] == Tw[2])
              | (_ar < 1e-12))
    if _degen.any():
        log.warning("pole-copy %s: %d/%d collapsed tris at weld (min area %.2e) — "
                    "dropping", kind, int(_degen.sum()), Tw.shape[1], float(_ar.min()))
        Tw = Tw[:, ~_degen]; tags = tags[~_degen]
    mesh = MeshTri(Pw.copy(), Tw)
    full_polys = dict(polys1); full_polys[feat_key] = feat_polys
    log.info("pole-copy %s: 1 period (%d nodes, %d feats) × %d → %d nodes, %d tris",
             kind, nN, nfeat, n_copies, mesh.p.shape[1], mesh.t.shape[1])
    return mesh, tags, types.SimpleNamespace(polys=full_polys)


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
        gap_layers: float = 3.0,            # element layers across the air gap
        component_mesh_mm: Optional[dict] = None,
        full_ring: bool = False,            # TRUE 360°: stitch 2×180° per half
        pole_copy: Optional[bool] = None,   # template-copy poles/slots; None=env default
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

    # ── Iron/magnet quality floor: default the iron + magnet element size to
    # the global element size, which the CALLERS already clamp to smallest-
    # feature/4 (fem_transient_sliding_band's feature-refine + the Mesh route's
    # _eff_mesh) — that ÷4 IS the single quality floor (≥4 elements across the
    # smallest tooth/slot; torque quality plateaus there).
    #
    # The old extra `min(global, 2 mm)` absolute cap is REMOVED (per Vadim
    # 2026-07-02 — "auto quality-floor, slider honest"): on LARGE motors where
    # feature/4 > 2 mm it silently over-refined the iron to 2 mm AND made the
    # Max-element-size slider a no-op above 2 mm (e.g. 450 mm: slider 4/8/12 all
    # collapsed to iron 2 mm).  Small motors are unaffected — their feature/4 is
    # already < 2 mm, so the 2 mm cap never bound there (the 150 mm ripple fix it
    # was added for is now carried by feature/4 = slot/4 ≈ 1.4 mm < 2 mm).
    # The user can still override any part explicitly in the Mesh tab.
    component_mesh_mm = dict(component_mesh_mm or {})
    _iron_cap = float(mesh_size_mm)
    for _part in ("stator", "rotor", "magnet"):
        _v = component_mesh_mm.get(_part)
        if _v is None or not (_v > 0):
            component_mesh_mm[_part] = _iron_cap

    # Rotational mesh periodicity: pole pitch on the rotor half, slot pitch on
    # the stator half — identical pockets/teeth get IDENTICAL meshes, killing
    # the pole-to-pole mesh-asymmetry torque artifact.
    _slot_period = _pole_period = None
    try:
        if not _SB_ROT_PERIODICITY:
            raise RuntimeError("rotational periodicity disabled (diagnostic)")
        _ns_ = int(round(float((geo_cfg or {}).get("num_seg", 0))))
        _sl_ = int(round(float((geo_cfg or {}).get("num_slots_per_segment", 0))))
        _pl_ = int(round(float((geo_cfg or {}).get("num_poles_per_segment", 0))))
        if _ns_ > 0 and _sl_ > 0:
            # The stator polygon repeats every TWO slot pitches, not one: slots
            # are built in PAIRS (half_slots loop in get_2d_polygons) — a full
            # tooth on the pair boundary, two coil pockets, and a SHORT centre
            # tooth (tooth2) carrying the slot opening between them.  A one-
            # slot-pitch wedge is therefore asymmetric by construction (left cut
            # bisects the full tooth, right cut bisects the short tooth → OCC
            # splits the two radial cuts differently, "not periodic-weldable
            # 29 vs 29, 3 aligned"), which silently disabled the stator
            # template-copy AND mis-grouped curves in the rotational-periodicity
            # signature.  The TRUE period is the slot-pair pitch.
            _slot_period = 2.0 * 360.0 / (_ns_ * _sl_)
        if _ns_ > 0 and _pl_ > 0:
            _pole_period = 360.0 / (_ns_ * _pl_)
    except Exception:
        pass

    # Slip-ring radius (mm) — force BOTH halves to keep an identical,
    # equally-spaced node ring there (transfinite) so they merge by node
    # identity when the rotor is rotated by an integer node step.
    _slip_r = polys.get("mid_r_mm")
    if _slip_r is None and geo_cfg:
        _ro = float(geo_cfg.get("rotor_outer_radius", 0.0))
        _si = float(geo_cfg.get("stator_inner_radius", 0.0))
        _slip_r = 0.5 * (_ro + _si) if (_ro > 0 and _si > _ro) else None
    # Structured-strip offset rings (mid±δ): keep their seeded vertices too.
    _extra_tf = polys.get("transfinite_ring_radii_mm") or []

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

    _common_kw = dict(
        rotor_angle_deg=0.0,
        mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
        normal_deviation_deg=normal_deviation_deg, aspect_ratio=aspect_ratio,
        outer_air_factor=outer_air_factor,
        motion_band=False, band_thickness_mm=band_thickness_mm,
        gap_layers=gap_layers, geo_cfg=geo_cfg,
        add_background_air=False, slip_transfinite_r=_slip_r,
        extra_transfinite_radii=_extra_tf,
        component_mesh_mm=component_mesh_mm,
    )

    if full_ring:
        # TRUE 360°: each half stitched from two clean 180° builds (direct
        # closed-360 OCC double-meshes → dead field).  No sector cuts exist
        # anywhere afterwards — the artifact-free configuration.
        #
        # pole_copy on the full ring: mesh ONE slot/pole wedge and rotate-copy
        # it the full (360/period) times.  The seam weld closes the circle
        # automatically — the last copy's right edge coincides with the first
        # copy's left edge — and 24 slots / 28 poles divide 360° exactly, so
        # the wrap is PERIODIC (the 28-pole magnet polarity (-1)^k matches at
        # k=0 and k=28, no anti-periodic sign needed like the sector build).
        # This is literally the "mesh one magnet + one rotor tooth and copy it
        # around the circle" view — every pocket/tooth is bit-identical.
        _pc_rotor  = _SB_POLE_COPY_ROTOR  if pole_copy is None else bool(pole_copy)
        _pc_stator = _SB_POLE_COPY_STATOR if pole_copy is None else bool(pole_copy)
        mesh_s = tags_s = classify_s = None
        mesh_r = tags_r = classify_r = None
        if _pc_stator and _slot_period and _slot_period > 0:
            _ncs = round(360.0 / _slot_period)
            if _ncs >= 1 and abs(_ncs * _slot_period - 360.0) < 1e-6:
                try:
                    mesh_s, tags_s, classify_s = _replicate_periodic_half(
                        polys_s_for_mesh, _slot_period, _ncs, _common_kw, "stator")
                except Exception as _se:
                    log.warning("full-ring stator slot-copy failed (%s) — stitched build", _se)
                    mesh_s = None
        if mesh_s is None:
            mesh_s, tags_s, classify_s = _stitch_full_half(
                polys_s_for_mesh, DOM_OUTER,
                dict(_common_kw, rotational_period_deg=_slot_period))
        if _pc_rotor and _pole_period and _pole_period > 0:
            _ncp = round(360.0 / _pole_period)
            if _ncp >= 1 and abs(_ncp * _pole_period - 360.0) < 1e-6:
                try:
                    mesh_r, tags_r, classify_r = _replicate_periodic_half(
                        polys_r_for_mesh, _pole_period, _ncp, _common_kw, "rotor")
                except Exception as _re:
                    log.warning("full-ring rotor pole-copy failed (%s) — stitched build", _re)
                    mesh_r = None
        if mesh_r is None:
            mesh_r, tags_r, classify_r = _stitch_full_half(
                polys_r_for_mesh, DOM_AIRGAP,
                dict(_common_kw, rotational_period_deg=_pole_period))
        if abs(rotor_angle_deg) > 1e-9:
            mesh_r = type(mesh_r)(_rotate_mesh_points(mesh_r.p, rotor_angle_deg),
                                   mesh_r.t)
        return mesh_s, tags_s, classify_s, mesh_r, tags_r, classify_r

    # Build stator half at the FIXED lab position (rotor_angle_deg ignored
    # for stator-side polygons, which are stationary).  Stator stays
    # sector-clipped to the requested n_sectors — it never rotates, so
    # the sector wedge accurately represents the symmetry-reduced domain.
    # Per-request override of the env-gated template-copy flags (one toggle
    # drives both halves when pole_copy is given).
    _pc_rotor  = _SB_POLE_COPY_ROTOR  if pole_copy is None else bool(pole_copy)
    _pc_stator = _SB_POLE_COPY_STATOR if pole_copy is None else bool(pole_copy)

    mesh_s = tags_s = classify_s = None
    if (_pc_stator and _slot_period and _slot_period > 0):
        _ncs = round((360.0 / n_sectors) / _slot_period)
        if _ncs >= 1 and abs(_ncs * _slot_period - 360.0 / n_sectors) < 1e-6:
            try:
                mesh_s, tags_s, classify_s = _replicate_periodic_half(
                    polys_s_for_mesh, _slot_period, _ncs, _common_kw, "stator")
            except Exception as _se:
                log.warning("stator slot-copy failed (%s) — standard sector build", _se)
                mesh_s = None
    if mesh_s is None:
        mesh_s, tags_s, classify_s = build_mesh_from_polygons(
            polys_s_for_mesh, n_sectors=n_sectors,
            rotational_period_deg=_slot_period, **_common_kw)

    # Build rotor half with the SAME sector clip as the stator
    # (n_sectors).  The rotor mesh covers ONE sector (1/n_sectors of the
    # disk) at the un-rotated zero position; rigid rotation by
    # rotor_angle_deg slides this wedge inside the stator sector.  The
    # rotor body + magnets + shaft + in_band air all live in ONE mesh so
    # they rotate together as a single rigid unit per transient frame.
    # Past the sector edge the wedge wraps via anti-periodic BC (handled
    # later by the solver / master-slave pair).
    mesh_r = tags_r = classify_r = None
    if (_pc_rotor and _pole_period and _pole_period > 0):
        _ncp = round((360.0 / n_sectors) / _pole_period)
        if _ncp >= 1 and abs(_ncp * _pole_period - 360.0 / n_sectors) < 1e-6:
            try:
                mesh_r, tags_r, classify_r = _replicate_periodic_half(
                    polys_r_for_mesh, _pole_period, _ncp, _common_kw, "rotor")
            except Exception as _re:
                log.warning("rotor pole-copy failed (%s) — standard sector build", _re)
                mesh_r = None
    if mesh_r is None:
        mesh_r, tags_r, classify_r = build_mesh_from_polygons(
            polys_r_for_mesh, n_sectors=n_sectors,
            rotational_period_deg=_pole_period, **_common_kw)

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
    # ── Feasibility clamp (single source: geometry_constraints.wire_height_fits_slot)
    # Without this the copper mesh overflows the stator bore, across the air gap,
    # onto the rotor — overlapping meshes and current injected in the gap (physically
    # invalid).  MUST match cadquery_geometry.get_2d_polygons so the copper current
    # mesh and the material-domain polygons agree wire-for-wire.
    _slot_h = float(geo_cfg.get("slot_height", 0.0))
    _wh_max = (_slot_h - 2.0 * ins_w) / max(1, n_wires) - wire_dy
    if _wh_max > 1e-3 and wire_h > _wh_max:
        wire_h = _wh_max
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


def _structured_gap_sm(n_slip: int, n_sectors: int,
                       m_target: int = 14) -> Tuple[int, int]:
    """Pick (S, M) for the structured-gap cells of ONE wedge (route A).

    S = number of angular cells in the [0, 2π/n_sectors] wedge, M = arc
    divisions per cell.  Requirement: S·M = n_slip / n_sectors (so the mid
    ring lands EXACTLY on the global slip grid 2πj/n_slip and the sliding
    coupling's _ring() finds a uniform ring).  Among the (S, M) factorings
    of slip_wedge, prefer M near ``m_target`` (~14, like the proven proto)
    to keep cell aspect reasonable and the surface count modest.
    """
    slip_wedge = int(n_slip) // int(n_sectors)
    if slip_wedge <= 0:
        return 1, max(1, int(n_slip))
    # candidate M = every divisor of slip_wedge; pick the one closest to target
    divs = [d for d in range(1, slip_wedge + 1) if slip_wedge % d == 0]
    M = min(divs, key=lambda d: (abs(d - m_target), d))
    S = slip_wedge // M
    return S, M


def _iron_arc_ring_occ(occ, center_pt: int, geom, r_ring: float,
                       n_sectors: int, S: int, getP, dedupe_fn,
                       tol_mm: float = 0.03) -> List[int]:
    """Build OCC plane surfaces for an iron (Multi)Polygon whose gap-facing ring
    (vertices at r ≈ ``r_ring``) is replaced by circle arcs COINCIDENT with the
    structured-gap cells' arc at that radius (route A, clean — no ε retract).

    Why: the cells only mesh transfinite if NO foreign vertex lands INSIDE a cell
    arc (which would give the cell a 5th+ corner).  The raw CadQuery iron boundary
    is a fuzzy polyline with hundreds of vertices at r_ring; converting it as
    lines subdivides every cell arc.  Instead we emit, for each maximal run of
    on-ring vertices, a chain of circle arcs whose endpoints are SNAPPED to the
    cells' uniform seam grid (angles 2π·k/(S·n_sectors)).  Because the arc share
    the exact circle + the exact seam endpoints as the cell arcs, occ.fragment
    merges them → each gap cell keeps its 4 corners.  Off-ring boundary (yoke
    outer edge, slot-mouth walls, radial sector cuts, shaft arc) stays polyline.

    Slot mouths (stator) stay OPEN to the gap: between two snapped tooth-tip arcs
    the boundary lifts radially into the slot as lines — no arc seals the mouth,
    so the mouth cell's outer arc is a free gap↔slot interface (flux crosses).

    ``getP(x, y)`` must be a caller-provided memoized point-adder so iron arc
    endpoints share the SAME OCC point tags as the cells' seam points (exact
    coincidence).  Returns the list of surface tags created.
    """
    import numpy as _np
    ns = max(1, int(n_sectors))
    step = (2.0 * math.pi / ns) / max(1, int(S))     # seam angular spacing

    def _ring_curveloop(coords):
        """One curve loop: on-ring runs → seam-snapped arcs, else lines."""
        pts = dedupe_fn(coords)
        if len(pts) < 3:
            return None
        n = len(pts)
        r = _np.hypot([p[0] for p in pts], [p[1] for p in pts])
        # "On the gap ring" = radius within tol of r_ring.  Works for both the
        # rotor OD (iron below the ring) and the stator bore (iron above it):
        # only the vertices sitting AT r_ring must snap to the cell arc.
        on = _np.abs(r - r_ring) < tol_mm
        # Build an ordered node list: replace each on-ring run by the seam nodes
        # spanning its (snapped) angular extent; keep off-ring vertices as-is.
        loop: List[Tuple[str, int]] = []
        i = 0
        while i < n:
            if on[i]:
                j = i
                while j < n and on[j]:
                    j += 1
                a_s = math.atan2(pts[i][1], pts[i][0])
                a_e = math.atan2(pts[j - 1][1], pts[j - 1][0])
                # Take the SHORT arc between the run's endpoints (unwrap a_e so
                # |a_e−a_s| ≤ π) so a run straddling θ=0 does not wind the long
                # way round the circle.  In the stitched build (180° wedges) runs
                # never cross the seam, but this keeps the helper correct for a
                # single-model full disk (n_sectors=1) too.
                while a_e - a_s > math.pi:
                    a_e -= 2.0 * math.pi
                while a_e - a_s < -math.pi:
                    a_e += 2.0 * math.pi
                k_s = int(round(a_s / step))
                k_e = int(round(a_e / step))
                rng = (range(k_s, k_e + 1) if k_e >= k_s
                       else range(k_s, k_e - 1, -1))
                for k in rng:
                    a = step * k
                    loop.append(("ARC", getP(r_ring * math.cos(a),
                                             r_ring * math.sin(a))))
                i = j
            else:
                loop.append(("PT", getP(pts[i][0], pts[i][1])))
                i += 1
        # Emit curves: consecutive ARC-ARC → circle arc (shares the cell arc);
        # any run touching an off-ring PT → straight line.
        curves: List[int] = []
        L = len(loop)
        for a in range(L):
            (ta, pa) = loop[a]
            (tb, pb) = loop[(a + 1) % L]
            if pa == pb:
                continue
            if ta == "ARC" and tb == "ARC":
                try:
                    curves.append(occ.addCircleArc(pa, center_pt, pb))
                    continue
                except Exception:
                    pass
            try:
                curves.append(occ.addLine(pa, pb))
            except Exception:
                continue
        if len(curves) < 3:
            return None
        try:
            return occ.addCurveLoop(curves)
        except Exception:
            return None

    def _polys_only(gm):
        if gm is None or gm.is_empty:
            return []
        if gm.geom_type == "Polygon":
            return [gm]
        if hasattr(gm, "geoms"):
            out: List = []
            for sub in gm.geoms:
                out.extend(_polys_only(sub))
            return out
        return []

    surfs: List[int] = []
    for g in _polys_only(geom):
        if g.is_empty or g.area < 1e-6:
            continue
        outer = _ring_curveloop(list(g.exterior.coords)[:-1])
        if outer is None:
            continue
        holes = []
        for h in g.interiors:
            hw = _ring_curveloop(list(h.coords)[:-1])
            if hw is not None:
                holes.append(hw)
        try:
            surfs.append(occ.addPlaneSurface([outer, *holes]))
        except Exception as _e:
            log.warning("iron arc-ring surface failed: %s", _e)
    return surfs


def _build_structured_gap_cells(occ, spec: dict, n_sectors: int, center_pt: int,
                                eps: float = 0.0, getP=None
                                ) -> Tuple[List[int], List[int], float, float, int]:
    """Build the route-A gap cells (concentric cylinder-sectors) for one half
    as OCC plane surfaces, over the [0, 2π/n_sectors] wedge.

    ``spec`` = {r_lo, r_hi, K, n_slip, half}.  Returns
    (transfinite_cell_tags, filler_tags, r_lo, r_hi, M):
      • transfinite cells: K radial layers r_lo→r_hi → K+1 uniform radial levels;
        the caller sets each transfinite (arcs → M+1, radial → 2).
      • filler cells: ONE thin free-meshed layer bridging the ε retract gap to
        the iron (rotor: r_ro−ε→r_ro on the r_lo side; stator: r_si→r_si+ε on the
        r_hi side).  It shares the transfinite cells' clean arc (r_lo or r_hi) so
        it conforms above, and meets the fuzzy iron below/above (free-meshed, so
        the iron's subdividing vertices are harmless).  This closes the void the
        ε retract opens → the mesh is CONFORMING (flux crosses; torque ≠ 0).
        Returned SEPARATELY so it is NOT set transfinite.
    """
    r_lo = float(spec["r_lo"]); r_hi = float(spec["r_hi"])
    K = max(1, int(spec["K"]))
    n_slip = int(spec["n_slip"])
    half = str(spec.get("half", ""))
    ns = max(1, int(n_sectors))
    Phi = 2.0 * math.pi / ns
    S, M = _structured_gap_sm(n_slip, ns)

    # When a shared memoized point-adder is passed (route-A clean arc iron), reuse
    # it so the cells' arc endpoints at r_lo/r_hi share the SAME OCC point tags as
    # the iron arc boundary → occ.fragment sees one coincident curve per seam.
    if getP is not None:
        def _P(r, a):
            return getP(r * math.cos(a), r * math.sin(a))
    else:
        def _P(r, a):
            return occ.addPoint(r * math.cos(a), r * math.sin(a), 0)

    def _sector_layer(ra, rb):
        """S plane-surface cells between radii ra<rb over the wedge."""
        out = []
        for s in range(S):
            a1 = Phi * s / S
            a2 = Phi * (s + 1) / S
            p1, p2 = _P(ra, a1), _P(ra, a2)
            p3, p4 = _P(rb, a2), _P(rb, a1)
            ain = occ.addCircleArc(p1, center_pt, p2)
            aout = occ.addCircleArc(p4, center_pt, p3)
            l2 = occ.addLine(p2, p3)
            l1 = occ.addLine(p1, p4)
            out.append(occ.addPlaneSurface(
                [occ.addCurveLoop([ain, l2, -aout, -l1])]))
        return out

    radii = np.linspace(r_lo, r_hi, K + 1)
    cells: List[int] = []
    for ir in range(K):
        cells += _sector_layer(float(radii[ir]), float(radii[ir + 1]))

    # ε bridge filler on the IRON side of this half (free-meshed).  Rotor iron is
    # capped at r_ro−ε (= r_lo−ε here); stator iron starts at r_si+ε (= r_hi+ε).
    filler: List[int] = []
    if eps > 0.0:
        if half == "stator":
            filler = _sector_layer(r_hi, r_hi + eps)     # r_si → r_si+ε
        else:                                             # rotor (default)
            filler = _sector_layer(r_lo - eps, r_lo)     # r_ro−ε → r_ro
    return cells, filler, r_lo, r_hi, M


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
                             gap_layers: float = 3.0,   # element layers across the air gap
                             n_sectors: int = 1,
                             add_background_air: bool = True,
                             slip_transfinite_r: Optional[float] = None,
                             component_mesh_mm: Optional[dict] = None,
                             rotational_period_deg: Optional[float] = None,
                             extra_transfinite_radii: Optional[List[float]] = None,
                             transfinite_radial_cuts: bool = False,
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
        gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-2)  # 10µm: weld CadQuery ~3µm cross-polygon slivers (was 1e-3)
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

        # ── STRUCTURED gap (route A) — CLEAN arc iron boundary (no ε retract) ──
        # When a structured_gap_spec is present we build the gap-facing iron of
        # THIS half with its ring edge (rotor OD at r_lo=r_ro, or stator bore at
        # r_hi=r_si) emitted as circle arcs COINCIDENT with the gap cells' arc,
        # snapped to the uniform seam grid.  occ.fragment then merges the shared
        # arc so every gap cell keeps 4 corners → transfinite → EXACT uniform
        # rings, with NO ε retract and NO bridge filler (the old ugly sliver
        # strips).  The iron keeps its true gap radius (rotor OD=r_ro, stator
        # bore=r_si); slot mouths stay open to the gap.
        _sg_spec0 = polys.get("structured_gap_spec")
        _sg_arc_half = None       # "rotor" | "stator" | None
        _sg_arc_r = 0.0
        _sg_arc_S = 0
        if _sg_spec0 is not None:
            try:
                _sg_arc_half = str(_sg_spec0.get("half", "")) or None
                _sg_arc_S, _ = _structured_gap_sm(
                    int(_sg_spec0["n_slip"]), max(1, int(n_sectors)))
                # rotor half: gap ring is the OD at r_lo (=r_ro).
                # stator half: gap ring is the bore at r_hi (=r_si).
                _sg_arc_r = (float(_sg_spec0["r_hi"]) if _sg_arc_half == "stator"
                             else float(_sg_spec0["r_lo"]))
            except Exception:
                _sg_arc_half = None
        # Shared memoized point-adder: iron arc endpoints reuse the SAME OCC point
        # tags the gap cells will place at each seam (exact coincidence).
        _sg_center_pt = occ.addPoint(0.0, 0.0, 0.0) if _sg_arc_half else None
        _sg_ptcache: Dict[Tuple[float, float], int] = {}

        def _sg_getP(x, y):
            _key = (round(x, 6), round(y, 6))
            _t = _sg_ptcache.get(_key)
            if _t is None:
                _t = occ.addPoint(x, y, 0)
                _sg_ptcache[_key] = _t
            return _t

        def _gap_edge_occ(geom, on_gap_edge: bool):
            """Build surface(s) for a domain that borders the structured gap:
            when it touches THIS half's gap ring (rotor OD=r_lo / stator
            bore=r_hi) its ring run is emitted as arcs coincident with the cell
            arc (so no foreign vertex subdivides a cell).  Applies to the
            gap-facing iron AND to any AIR that reaches the ring (rotor pocket
            air at r_ro, stator slot-mouth air at r_si) — otherwise their fuzzy
            1008-gon ring boundary would subdivide the mouth/pocket cells.
            Non-gap-edge domains use the plain polyline converter."""
            if (on_gap_edge and _sg_arc_half is not None
                    and geom is not None and not geom.is_empty):
                s = _iron_arc_ring_occ(
                    occ, _sg_center_pt, geom, _sg_arc_r,
                    n_sectors, _sg_arc_S, _sg_getP, _dedupe)
                if s:
                    return s
                log.warning("structured gap: arc edge build empty (%s) — "
                            "polyline fallback", _sg_arc_half)
            return _shapely_to_occ(geom)

        # air_outer borders the gap on the STATOR half (slot mouths at r_si);
        # air_gap (inner air) borders it on the ROTOR half (pockets at r_ro).
        for surf in _gap_edge_occ(polys.get("air_outer"),
                                  _sg_arc_half == "stator"):
            domain_surfaces.append((surf, DOM_OUTER))
        for surf in _shapely_to_occ(polys.get("air_background")):
            domain_surfaces.append((surf, DOM_AIR))
        for surf in _gap_edge_occ(polys.get("stator"), _sg_arc_half == "stator"):
            domain_surfaces.append((surf, DOM_STATOR))
        for surf in _gap_edge_occ(polys.get("rotor"), _sg_arc_half == "rotor"):
            domain_surfaces.append((surf, DOM_ROTOR))
        for surf in _shapely_to_occ(polys.get("shaft")):
            domain_surfaces.append((surf, DOM_SHAFT))
        # air_gap = inner air (pockets etc.); on the ROTOR half it reaches r_ro.
        for surf in _gap_edge_occ(polys.get("air_gap"),
                                  _sg_arc_half == "rotor"):
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

        # ── STRUCTURED (mapped) air gap — ROUTE A ─────────────────────────────
        # Build the gap for THIS half as concentric cylinder-sector cells and
        # add them to the SAME fragment as the iron.  in_band/out_band were built
        # (in _simplify_polys) to STOP at the rotor OD / start at the stator bore,
        # so these cells own the gap slice exclusively.  Conformity is automatic
        # (one fragmented model); the transfinite seeding (below, post-fragment)
        # forces EXACTLY K+1 uniform radial levels in this half.
        _sg_spec = polys.get("structured_gap_spec")
        _sg_cells: List[int] = []
        _sg_M = 0
        _sg_rlo = _sg_rhi = 0.0
        _sg_input_idx: List[int] = []      # indices into domain_surfaces of TF cells
        _sg_filler_idx: List[int] = []     # indices into domain_surfaces of filler
        _sg_filler_half = "rotor"
        _sg_eps = 0.0
        if _sg_spec is not None:
            try:
                # Reuse the shared center point + memoized point-adder from the
                # arc iron build so the cells' r_lo/r_hi seam points COINCIDE with
                # the iron arc endpoints (route A clean).  Falls back to a fresh
                # center when the arc iron path is inactive (eps>0 legacy retract).
                _sg_center = (_sg_center_pt if _sg_center_pt is not None
                              else occ.addPoint(0.0, 0.0, 0.0))
                _sg_getP2 = _sg_getP if _sg_arc_half is not None else None
                _sg_eps = float(_sg_spec.get("eps", 0.0))
                _sg_cells, _sg_filler, _sg_rlo, _sg_rhi, _sg_M = \
                    _build_structured_gap_cells(
                        occ, _sg_spec, n_sectors, _sg_center, eps=_sg_eps,
                        getP=_sg_getP2)
                for _cs in _sg_cells:
                    _sg_input_idx.append(len(domain_surfaces))
                    domain_surfaces.append((_cs, DOM_AIRGAP))
                # Filler ring (ε bridge to the iron) — FREE-meshed (not tracked in
                # _sg_input_idx so never set transfinite).  Each thin filler cell
                # must carry the SAME material as the iron/air DIRECTLY behind it:
                # iron under a tooth / between poles, air in a slot mouth / pole
                # gap.  A blanket iron tag SHORTS the slot openings (iron bridges
                # adjacent teeth) → ~25 % of the gap flux leaks tangentially →
                # torque collapses.  Classify each filler cell below (post-frag)
                # by centroid vs the retracted iron; tag AIR provisionally here.
                _sg_filler_half = str(_sg_spec.get("half", "rotor"))
                for _fs in _sg_filler:
                    _sg_filler_idx.append(len(domain_surfaces))
                    domain_surfaces.append((_fs, DOM_AIRGAP))
                log.info("structured gap (route A): %s half, %d cells + %d filler "
                         "r=%.3f→%.3f (K=%d, M=%d/arc, ε=%.4f, n_sectors=%d)",
                         _sg_spec.get("half", "?"), len(_sg_cells), len(_sg_filler),
                         _sg_rlo, _sg_rhi, int(_sg_spec["K"]), _sg_M, _sg_eps,
                         n_sectors)
            except Exception as _sge:
                log.warning("structured gap cell build failed (%s) — free gap", _sge)
                _sg_cells = []

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

        # STRUCTURED gap: collect the EXACT fragment tags that came from the gap
        # cell inputs (via out_map), so the transfinite pass targets ONLY those
        # cells — not a big iron/air surface whose centroid happens to fall in
        # the gap band (that would try to make a 17-corner surface transfinite).
        _sg_cell_frag_tags: set = set()
        if _sg_input_idx:
            for _ii in _sg_input_idx:
                _ol = out_map[_ii] if _ii < len(out_map) else []
                for (_od, _ot) in _ol:
                    if _od == 2:
                        _sg_cell_frag_tags.add(int(_ot))

        # STRUCTURED gap ε-filler: decide each filler cell's material by the iron
        # DIRECTLY behind it.  Rotor filler (r_ro−ε→r_ro): iron if the point just
        # inside the retracted rotor OD is in the rotor (or a magnet); else air.
        # Stator filler (r_si→r_si+ε): iron if the point just inside the retracted
        # stator bore is in the stator; else air (slot mouth).  This stops the
        # blanket-iron slot short that collapses the gap flux.
        _sg_filler_dom: Dict[int, int] = {}   # filler fragment tag → domain id
        if _sg_filler_idx:
            from shapely.geometry import Point as _PtF
            _st_poly = polys.get("stator")
            _ro_poly = polys.get("rotor")
            _mag_polys = [mp for mp, _pl in polys.get("magnets", []) if mp is not None]
            _probe = max(1e-4, 0.4 * float(_sg_eps))   # radial probe depth into iron
            for _ii in _sg_filler_idx:
                _ol = out_map[_ii] if _ii < len(out_map) else []
                for (_od, _ot) in _ol:
                    if _od != 2:
                        continue
                    try:
                        _com = occ.getCenterOfMass(2, int(_ot))
                    except Exception:
                        _sg_filler_dom[int(_ot)] = DOM_AIRGAP
                        continue
                    _rr = math.hypot(_com[0], _com[1])
                    _th = math.atan2(_com[1], _com[0])
                    if _sg_filler_half == "stator":
                        _pr = _rr + _probe            # probe outward (into stator)
                        _pt = _PtF(_pr * math.cos(_th), _pr * math.sin(_th))
                        _dom = (DOM_STATOR if (_st_poly is not None
                                              and _st_poly.contains(_pt))
                                else DOM_AIRGAP)
                    else:
                        _pr = _rr - _probe            # probe inward (into rotor)
                        _pt = _PtF(_pr * math.cos(_th), _pr * math.sin(_th))
                        _dom = DOM_AIRGAP
                        if _ro_poly is not None and _ro_poly.contains(_pt):
                            _dom = DOM_ROTOR
                        else:
                            for _mi, _mp in enumerate(_mag_polys):
                                if _mp.contains(_pt):
                                    _dom = DOM_MAG_BASE + _mi
                                    break
                    _sg_filler_dom[int(_ot)] = _dom

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
            if int(tag) in _sg_filler_dom:
                # ε-filler cell: material decided by the iron behind it (above).
                dom_id = _sg_filler_dom[int(tag)]
            else:
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
        # All background size fields (air-gap + per-component) are collected in
        # _bg_fields and combined with a Min field at the very end.
        _bg_fields: List[int] = []
        try:
            _gc = geo_cfg or {}
            _r_ro = float(_gc.get("rotor_outer_radius", 0.0))
            _r_si = float(_gc.get("stator_inner_radius", 0.0))
            if _r_ro > 0.0 and _r_si > _r_ro:
                _r_ag = 0.5 * (_r_ro + _r_si)
                _gap  = _r_si - _r_ro
                # gap_layers = element layers PER HALF gap (slip-midline → iron).
                # The sliding-band slip ring bisects the gap, so each side (stator
                # half-mesh, rotor half-mesh) spans gap/2; element size =
                # (gap/2)/layers gives exactly `layers` rows on each side — which
                # is what the user counts in the Mesh view.  ~2 per half is the
                # mesh-independent sweet spot for Maxwell-stress torque; 3 is
                # plenty (4+ adds cost without accuracy).
                _nl   = max(1.0, float(gap_layers))
                _ag_h = max(0.04, (_gap / 2.0) / _nl)
                # Let the gap be finer than the global Min size so "Air-gap
                # layers" actually changes the gap density (otherwise N≥~2 is
                # floored at min_size and 2× vs 3× look identical).
                gmsh.option.setNumber("Mesh.MeshSizeMin", min(min_size_mm, _ag_h))
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
                _ms = float(mesh_size_mm)
                _base = (f"min({_ms}, {_ag_h}+"
                         f"({_ms}-{_ag_h})*"
                         f"max(0,(fabs(sqrt(x*x+y*y)-{_r_ag})-{_half})/{_trans}))")
                # ── Far-field coarsening ─────────────────────────────────────
                # Beyond the stator OD the air ring only carries the A_z→0
                # Dirichlet decay — there is no field detail to resolve, so
                # refining it is wasted work. Let elements grow up to COARSE_FAR×
                # the global size from the stator OD out to the far boundary.
                _r_so = float(_gc.get("stator_outer_radius", 0.0))
                _oaf = float(outer_air_factor)
                if _r_so > 0.0 and _oaf > 1.001:
                    _r_far = _r_so * _oaf
                    _cf = 3.0
                    _farmult = (f"(1+{_cf - 1.0}*min(1,max(0,"
                                f"(sqrt(x*x+y*y)-{_r_so})/{max(1e-3, _r_far - _r_so)})))")
                    _formula = f"({_base})*{_farmult}"
                    # allow the coarse far elements (MeshSizeMax was = mesh_size)
                    gmsh.option.setNumber("Mesh.MeshSizeMax", _ms * _cf)
                else:
                    _formula = _base
                _fid = gmsh.model.mesh.field.add("MathEval")
                gmsh.model.mesh.field.setString(_fid, "F", _formula)
                _bg_fields.append(_fid)
                # Let the background field own the size in the gap; keep curvature
                # for fillets (gmsh takes the min of the two).
                gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        except Exception as _e:
            log.warning("air-gap size field skipped: %s", _e)

        # ── Per-component mesh size (study mesh-density effect on results) ────
        # The UI can request a target element size for each motor PART type.
        # For each requested component we build one gmsh "Constant" size field
        # (size VIn inside that component's surfaces, ≈∞ outside) using the
        # post-fragment surface tags grouped by domain.  Components left unset
        # keep the global / curvature / air-gap size.  Everything is combined by
        # a Min field below so the FINEST requested size wins at any point.
        try:
            _cm = {str(k): float(v) for k, v in (component_mesh_mm or {}).items()
                   if v is not None and float(v) > 0.0}
            if _cm:
                def _comp_of(_d: int):
                    if _d >= DOM_COIL_BASE:
                        return "coil"
                    if _d >= DOM_MAG_BASE:
                        return "magnet"
                    return {DOM_STATOR: "stator", DOM_ROTOR: "rotor",
                            DOM_SHAFT: "shaft", DOM_AIRGAP: "airgap",
                            DOM_BAND: "airgap", DOM_AIR: "outer",
                            DOM_OUTER: "outer"}.get(int(_d))
                _surf_by_comp: Dict[str, List[int]] = defaultdict(list)
                for _tag, _dom in frag_surfaces:
                    _ck = _comp_of(int(_dom))
                    if _ck is not None:
                        _surf_by_comp[_ck].append(int(_tag))
                _applied = []
                for _ckey, _csize in _cm.items():
                    _surfs = _surf_by_comp.get(_ckey, [])
                    if not _surfs:
                        continue
                    _cf2 = gmsh.model.mesh.field.add("Constant")
                    gmsh.model.mesh.field.setNumbers(_cf2, "SurfacesList", _surfs)
                    gmsh.model.mesh.field.setNumber(_cf2, "VIn", float(_csize))
                    gmsh.model.mesh.field.setNumber(_cf2, "VOut", 1e22)
                    _bg_fields.append(_cf2)
                    _applied.append((_ckey, _csize, len(_surfs)))
                if _applied:
                    # lower the floor so a requested fine size isn't clamped up.
                    # NEVER RAISE it: the air-gap field above may have already
                    # dropped MeshSizeMin below min_size_mm so the gap fits its
                    # (gap/2)/layers rows — overwriting that with a coarser
                    # component size clamps the gap up and shreds the layering
                    # (ragged gap mesh → noisy Arkkio torque, fake low-order
                    # "cogging" — 96 % ripple on the 150 mm motor).
                    _smallest = min(s for _, s, _ in _applied)
                    _cur_min = gmsh.option.getNumber("Mesh.MeshSizeMin")
                    if _cur_min <= 0:
                        _cur_min = min_size_mm
                    gmsh.option.setNumber("Mesh.MeshSizeMin",
                                          min(_cur_min, min_size_mm, _smallest))
                    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
                    log.info("per-component mesh sizes: %s",
                             ", ".join(f"{c}={s}mm({n} surf)"
                                       for c, s, n in _applied))
        except Exception as _e:
            log.warning("per-component size fields skipped: %s", _e)

        # ── Combine all background size fields via Min (finest wins) ─────────
        if len(_bg_fields) == 1:
            gmsh.model.mesh.field.setAsBackgroundMesh(_bg_fields[0])
        elif len(_bg_fields) > 1:
            _minf = gmsh.model.mesh.field.add("Min")
            gmsh.model.mesh.field.setNumbers(_minf, "FieldsList", _bg_fields)
            gmsh.model.mesh.field.setAsBackgroundMesh(_minf)

        # ── STRUCTURED gap cells: set each gap cell TRANSFINITE (route A) ──────
        # For every cell surface (centroid radius in this half's gap band): arcs
        # → M+1 nodes, radial edges → 2 nodes, then setTransfiniteSurface.  gmsh
        # then fills the cell with EXACTLY 2 uniform triangle rows between two
        # arcs → K cells stacked radially give K+1 uniform radial levels, and the
        # arc at mid_r carries the uniform S·M = n_slip/n_sectors slip grid.
        # This REPLACES the 2-node slip-ring seeding for the mid ring (below),
        # which is skipped when the cells own it (else it would fight M+1 vs 2).
        _sg_active = bool(_sg_cell_frag_tags) and _sg_M > 0
        if _sg_active:
            try:
                _sg_n = 0; _sg_skip = 0
                for (_d, _surf) in gmsh.model.getEntities(2):
                    if int(_surf) not in _sg_cell_frag_tags:
                        continue
                    _cvs = gmsh.model.getBoundary([(_d, _surf)], oriented=False)
                    # A clean cell is a 4-sided quad (2 arcs + 2 radial edges).
                    # If OCC merged this cell into a bigger region (>4 sides), we
                    # CANNOT make it transfinite (gmsh needs 3/4 corners) — skip
                    # it (it meshes free; a merged cell is rare and only softens
                    # one row locally).
                    if len(_cvs) != 4:
                        _sg_skip += 1
                        continue
                    for (_cd, _cv) in _cvs:
                        _bpts = gmsh.model.getBoundary(
                            [(_cd, _cv)], oriented=False)
                        _prs = [math.hypot(*gmsh.model.getValue(0, _pt, [])[:2])
                                for (_pdim, _pt) in _bpts]
                        if len(_prs) != 2:
                            continue
                        # arc (both endpoints same radius) → M+1; radial → 2
                        _is_arc = abs(_prs[0] - _prs[1]) < 1e-4
                        gmsh.model.mesh.setTransfiniteCurve(
                            _cv, (_sg_M + 1) if _is_arc else 2)
                    try:
                        gmsh.model.mesh.setTransfiniteSurface(_surf)
                        _sg_n += 1
                    except Exception as _tse:
                        log.warning("structured gap TF surface failed: %s", _tse)
                log.info("structured gap: %d cells set transfinite, %d skipped "
                         "(merged) (M=%d/arc → uniform rings)",
                         _sg_n, _sg_skip, _sg_M)
            except Exception as _sge2:
                log.warning("structured gap transfinite skipped: %s", _sge2)
                _sg_active = False

        # ── Sliding-band slip ring: force the mid_r boundary to keep EXACTLY
        # its polygon vertices (transfinite, 2 nodes/edge) so both half-meshes
        # share an identical, equally-spaced node ring at r = slip_transfinite_r.
        # Matching nodes let the two halves MERGE by node identity (a shared
        # DOF) when the rotor is rotated by an integer node step — no
        # interpolation, no flux-coupling error.
        #
        # STRUCTURED gap: the transfinite cells already seed the mid_r ring on
        # the uniform S·M grid — do NOT re-seed it here (2 nodes/edge would
        # collide with the cells' M+1).  Only seed OTHER extra radii, and only
        # those NOT inside a gap band the cells own.
        if _sg_active and slip_transfinite_r is not None:
            _tf_radii = [float(r) for r in (extra_transfinite_radii or [])
                         if not (_sg_rlo - 1e-3 <= float(r) <= _sg_rhi + 1e-3)]
        else:
            _tf_radii = ([float(slip_transfinite_r)] if slip_transfinite_r is not None else []) \
                        + [float(r) for r in (extra_transfinite_radii or [])]
        if _tf_radii:
            try:
                _counts = [0] * len(_tf_radii)
                for (_d, _ct) in gmsh.model.getEntities(1):
                    _bp = gmsh.model.getBoundary([(1, _ct)], oriented=False,
                                                 combined=False)
                    _rr = []
                    for (_pd, _pt) in _bp:
                        _xyz = gmsh.model.getValue(0, _pt, [])
                        _rr.append(math.hypot(_xyz[0], _xyz[1]))
                    if not _rr:
                        continue
                    for _ri, _rs in enumerate(_tf_radii):
                        if all(abs(_r - _rs) < 1e-3 for _r in _rr):
                            gmsh.model.mesh.setTransfiniteCurve(_ct, 2)
                            _counts[_ri] += 1
                            break
                log.info("transfinite rings: " + ", ".join(
                    f"r={_rs:.3f}mm×{_c}" for _rs, _c in zip(_tf_radii, _counts)))
            except Exception as _e:
                log.warning("slip transfinite skipped: %s", _e)

        # ── Sector-cut periodicity: mesh cut B as the ROTATED COPY of cut A ──
        # gmsh meshes the two radial cut lines independently, so their node
        # distributions differ (different counts / positions, mismatch up to
        # ~0.8 mm).  The (anti-)periodic BC then merges NON-coincident DOFs —
        # a hard constraint between points up to a millimetre apart — which
        # injects a systematic field error at the cuts.  As the poles slide
        # past the cut this error modulates with rotor position → parasitic
        # low-order (1–4/period) torque ripple, ~25 N·m pk-pk at I=0 on the
        # 150 mm motor.  setPeriodic forces gmsh to COPY the master-cut mesh
        # onto the slave cut (rotated) → node-matched cuts, exact BC pairing.
        if n_sectors and int(n_sectors) > 1:
            try:
                _phi = 2.0 * math.pi / int(n_sectors)
                _cph, _sph = math.cos(_phi), math.sin(_phi)
                _tolc = 1e-4          # mm — OCC noise ≪ this ≪ any feature
                def _on_ray(pts, ux, uy):
                    for (px, py) in pts:
                        if abs(px * uy - py * ux) > _tolc:   # distance to line
                            return False
                        if (px * ux + py * uy) < -_tolc:     # wrong half-line
                            return False
                    return True
                _cutA, _cutB = [], []          # (radial midpoint, curve tag)
                for (_d, _ct) in gmsh.model.getEntities(1):
                    _lo, _hi = gmsh.model.getParametrizationBounds(1, _ct)
                    _pts = []
                    for _f in (0.0, 0.5, 1.0):
                        _tv = _lo[0] + _f * (_hi[0] - _lo[0])
                        _xyz = gmsh.model.getValue(1, _ct, [_tv])
                        _pts.append((_xyz[0], _xyz[1]))
                    _rm = math.hypot(_pts[1][0], _pts[1][1])
                    # STRUCTURED gap: the cells' seam radial edges are already
                    # transfinite(2) and node-exact — leave them out of the sector
                    # cut pairing (their periodic copies coincide by construction;
                    # re-setting them here would double-constrain the curve).
                    if _sg_active and (_sg_rlo - 1e-4 <= _rm <= _sg_rhi + 1e-4):
                        continue
                    if _on_ray(_pts, 1.0, 0.0):
                        _cutA.append((_rm, _ct))
                    elif _on_ray(_pts, _cph, _sph):
                        _cutB.append((_rm, _ct))
                _cutA.sort(); _cutB.sort()
                if _cutA and len(_cutA) == len(_cutB) and transfinite_radial_cuts:
                    # NODE-EXACT radial edges for pole/slot template-copy: force
                    # each paired cut segment TRANSFINITE with the SAME node
                    # count on both edges, so the two radial edges have identical
                    # distributions and rotated copies WELD exactly (setPeriodic
                    # alone left a 25-vs-26 mismatch → only ~12 nodes welded).
                    _ncut = 0
                    for (_rmA, _ctA), (_rmB, _ctB) in zip(_cutA, _cutB):
                        # node count from the segment's radial length / a fine
                        # target (cap at the iron size); both segments share it.
                        _bpA = gmsh.model.getBoundary([(1, _ctA)], oriented=False,
                                                      combined=False)
                        _rr = []
                        for (_pd, _pt) in _bpA:
                            _xyz = gmsh.model.getValue(0, _pt, [])
                            _rr.append(math.hypot(_xyz[0], _xyz[1]))
                        _seglen = (max(_rr) - min(_rr)) if len(_rr) >= 2 else 0.0
                        _tgt = max(min(float(mesh_size_mm), 1.5), float(min_size_mm))
                        _N = max(2, int(round(_seglen / _tgt)) + 1)
                        gmsh.model.mesh.setTransfiniteCurve(_ctA, _N)
                        gmsh.model.mesh.setTransfiniteCurve(_ctB, _N)
                        _ncut += 1
                    log.info("sector cuts: %d segment pairs set TRANSFINITE "
                             "(node-exact edges for template-copy)", _ncut)
                elif _cutA and len(_cutA) == len(_cutB):
                    _aff = [_cph, -_sph, 0.0, 0.0,
                            _sph,  _cph, 0.0, 0.0,
                            0.0,   0.0,  1.0, 0.0,
                            0.0,   0.0,  0.0, 1.0]
                    gmsh.model.mesh.setPeriodic(
                        1, [t for _, t in _cutB], [t for _, t in _cutA], _aff)
                    log.info("sector cuts: %d curve pairs set periodic "
                             "(rot %.1f°) → node-matched cut meshes",
                             len(_cutA), math.degrees(_phi))
                else:
                    log.warning("sector cuts NOT matched (%d vs %d curves) — "
                                "cut meshes may disagree node-wise",
                                len(_cutA), len(_cutB))
            except Exception as _e:
                log.warning("sector-cut periodicity skipped: %s", _e)

        # ── Rotational mesh periodicity: identical features → identical mesh ──
        # Free meshing gives every pole pocket / tooth a slightly DIFFERENT
        # node pattern; that pole-to-pole asymmetry shows up as parasitic
        # torque (the N/S half-cogging order, ±2 N·m at I=0).  Group every
        # 1-D curve by its rotation-canonical signature (period = pole pitch
        # for the rotor half / slot pitch for the stator half) and force each
        # group to be meshed as the ROTATED COPY of its first member.  Curves
        # on the sector cuts (own periodicity) and the slip ring (transfinite)
        # are excluded; non-matching curves (e.g. a non-periodic iron chain)
        # simply stay free — no harm.
        if rotational_period_deg and rotational_period_deg > 0:
            try:
                _per = math.radians(float(rotational_period_deg))
                _tolp = 1e-3            # mm — canonical-coordinate match
                _phi_cut = (2.0 * math.pi / int(n_sectors)
                            if n_sectors and int(n_sectors) > 1 else None)
                _slip_rr = (float(slip_transfinite_r)
                            if slip_transfinite_r is not None else None)
                _groups: Dict[tuple, list] = {}
                for (_d, _ct) in gmsh.model.getEntities(1):
                    _lo, _hi = gmsh.model.getParametrizationBounds(1, _ct)
                    _pts = []
                    for _f in (0.0, 0.5, 1.0):
                        _tv = _lo[0] + _f * (_hi[0] - _lo[0])
                        _xyz = gmsh.model.getValue(1, _ct, [_tv])
                        _pts.append((_xyz[0], _xyz[1]))
                    if _slip_rr is not None and all(
                            abs(math.hypot(px, py) - _slip_rr) < 1e-3
                            for px, py in _pts):
                        continue                      # transfinite slip ring
                    # STRUCTURED gap: every cell curve (arcs + radial edges) is
                    # already transfinite; its rotated copies coincide on the
                    # uniform grid by construction — exclude from periodicity so
                    # gmsh does not double-constrain a transfinite curve.
                    if _sg_active and all(
                            _sg_rlo - 1e-3 <= math.hypot(px, py) <= _sg_rhi + 1e-3
                            for px, py in _pts):
                        continue
                    if _phi_cut is not None:
                        def _on_ray(ux, uy):
                            return all(abs(px*uy - py*ux) <= 1e-4
                                       and (px*ux + py*uy) >= -1e-4
                                       for px, py in _pts)
                        if _on_ray(1.0, 0.0) or _on_ray(math.cos(_phi_cut),
                                                        math.sin(_phi_cut)):
                            continue                  # sector-cut curves
                    _mx, _my = _pts[1]
                    _th = math.atan2(_my, _mx)
                    if _th < 0:
                        _th += 2.0 * math.pi
                    _kf = _th / _per
                    _k = int(math.floor(_kf + 1e-9))
                    if _kf - _k > 1.0 - 1e-6:
                        _k += 1
                    _ck, _sk = math.cos(-_k * _per), math.sin(-_k * _per)
                    _sig = [(round(_ck*px - _sk*py, 3),
                             round(_sk*px + _ck*py, 3)) for px, py in _pts]
                    _key = (_sig[1], tuple(sorted((_sig[0], _sig[2]))))
                    _groups.setdefault(_key, []).append((_k, _ct))
                _bydk: Dict[int, Tuple[list, list]] = {}
                _npairs = 0
                for _members in _groups.values():
                    if len(_members) < 2:
                        continue
                    _members.sort()
                    _k0, _master = _members[0]
                    for _k, _ct in _members[1:]:
                        _dk = _k - _k0
                        if _dk <= 0:
                            continue
                        _sl, _ma = _bydk.setdefault(_dk, ([], []))
                        _sl.append(_ct); _ma.append(_master)
                        _npairs += 1
                _nset = 0
                for _dk, (_sl, _ma) in sorted(_bydk.items()):
                    _a = _dk * _per
                    _ca, _sa = math.cos(_a), math.sin(_a)
                    _aff = [_ca, -_sa, 0.0, 0.0,
                            _sa,  _ca, 0.0, 0.0,
                            0.0,  0.0, 1.0, 0.0,
                            0.0,  0.0, 0.0, 1.0]
                    try:
                        gmsh.model.mesh.setPeriodic(1, _sl, _ma, _aff)
                        _nset += len(_sl)
                    except Exception as _pe:
                        log.warning("rotational periodicity dk=%d failed: %s",
                                    _dk, _pe)
                log.info("rotational periodicity: %d/%d curve pairs set "
                         "(period %.3f deg)", _nset, _npairs,
                         float(rotational_period_deg))
            except Exception as _e:
                log.warning("rotational periodicity skipped: %s", _e)

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

    # ── Weld coincident-but-separate nodes ───────────────────────────────
    # gmsh's OCC boolean fragment can leave DUPLICATE nodes on a curve shared
    # by two surfaces (e.g. the full mid_r slip circle where in_band meets
    # out_band on the FULL disk, n_sectors=1).  Those duplicates form a
    # NON-CONFORMING crack: the FEM treats the two sides as disconnected, so
    # flux can't cross → the field collapses (~3.5× too weak, only on the full
    # disk; the sector's radial cuts give gmsh clean arc endpoints → no dupes).
    # Welding coincident nodes makes the mesh conforming.  No-op when there are
    # no duplicates (every sector model), so it's safe to always apply.
    mesh, cell_tags = _weld_coincident_nodes(mesh, cell_tags)

    # Attach the (possibly modified) polys dict to the classify_fn so the API
    # can render outlines that match the actual meshed geometry.
    try:
        _classify.polys = polys                  # type: ignore[attr-defined]
    except Exception:
        pass

    return mesh, cell_tags, _classify


def _weld_coincident_nodes(mesh, cell_tags: np.ndarray, tol_m: float = 2e-6):
    """Merge nodes that share the same (x, y) within tol_m into one node.

    Fixes non-conforming cracks left by gmsh's boolean fragment on shared
    curves (the full mid_r slip circle on the n_sectors=1 full disk).  tol_m
    (2 µm) is far below the smallest real node spacing (≳10 µm) and the air gap
    (0.65 mm), so genuinely distinct nodes are never merged.  Returns the mesh
    unchanged when there are no duplicates.
    """
    from skfem import MeshTri
    P = mesh.p                                   # (2, n) metres
    n = P.shape[1]
    try:
        from scipy.spatial import cKDTree
        pairs = cKDTree(P.T).query_pairs(r=tol_m, output_type="ndarray")
    except Exception:
        return mesh, cell_tags
    if len(pairs) == 0:
        return mesh, cell_tags
    # Union-find: group all mutually-coincident nodes onto their lowest id.
    parent = np.arange(n)
    def _find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b in pairs:
        ra, rb = _find(int(a)), _find(int(b))
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([_find(i) for i in range(n)], dtype=np.int64)
    uniq_roots, new_id = np.unique(roots, return_inverse=True)
    new_p = P[:, uniq_roots]                      # representative coords
    new_t = new_id[mesh.t]                        # remap triangle connectivity
    # Drop any triangle that collapsed (two node ids equal) — shouldn't happen
    # for cross-crack dupes, but guard against it so MeshTri stays valid.
    good = ~((new_t[0] == new_t[1]) | (new_t[1] == new_t[2]) | (new_t[0] == new_t[2]))
    n_welded = n - uniq_roots.size
    n_dropped = int((~good).sum())
    log.info("FEM: welded %d coincident nodes (%d→%d); dropped %d degenerate tris",
             n_welded, n, uniq_roots.size, n_dropped)
    mesh2 = MeshTri(doflocs=new_p.astype(np.float64),
                    t=new_t[:, good].astype(np.int64))
    return mesh2, cell_tags[good]


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

    from skfem import ElementTriP0
    basis = Basis(mesh, ElementTriP1())

    @BilinearForm
    def stiffness(u, v, w):
        return dot(grad(u), grad(v))

    @BilinearForm
    def stiffness_nu(u, v, w):            # per-element reluctivity ν(x)
        return w["nu"] * dot(grad(u), grad(v))

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
    tag_basis: Dict[int, "Basis"] = {}

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
        tag_basis[int(tag)] = sub_basis
        if mat.J_z != 0.0:
            f_current += asm(rhs_unit, sub_basis) * mat.J_z
        if abs(mat.Mx) > 0:
            tag_fMx[int(tag)] = asm(rhs_dvdy, sub_basis)
        if abs(mat.My) > 0:
            tag_fMy[int(tag)] = asm(rhs_dvdx, sub_basis)

    SATURABLE_TAGS = {DOM_STATOR, DOM_ROTOR, DOM_SHAFT}
    mu_r_eff: Dict[int, float] = {tag: tag_mat[tag].mu_r for tag in tag_mat}
    # ── PER-ELEMENT saturation state for the iron domains ────────────────
    # A per-DOMAIN μ from the B p90 NEVER saturates a spoke rotor's bridges
    # (they are a tiny fraction of the rotor area), so the magnets short
    # through the unsaturated bridges and the gap field collapses ~10×
    # (0.11 T instead of ~1 T — the "chaotic iso-lines / dead cogging"
    # symptom).  Mirror the transient: every iron triangle gets its own
    # ν(|B|), updated by damped Picard.
    sat_basis: Dict[int, "Basis"] = {}
    sat_b0:    Dict[int, "Basis"] = {}
    nu_el:     Dict[int, np.ndarray] = {}
    for tag in SATURABLE_TAGS:
        if tag in tag_cells:
            sat_basis[tag] = tag_basis[tag]
            sat_b0[tag] = tag_basis[tag].with_element(ElementTriP0())
            nu_el[tag] = np.full(tag_cells[tag].size,
                                 1.0 / (MU0 * max(tag_mat[tag].mu_r, 1.0)))
    # Br factor — starts at 1.0 (full strength) per magnet; the demag
    # iteration drops it below 1.0 when the operating point crosses the knee.
    br_factor: Dict[int, float] = {
        tag: 1.0 for tag in tag_mat if tag >= DOM_MAG_BASE}

    def _assemble_K() -> "csr_matrix":
        K = csr_matrix((n, n))
        for tag, K_dom in tag_K.items():
            if tag in sat_basis:
                continue                       # assembled per element below
            K = K + K_dom * (1.0 / (MU0 * mu_r_eff[tag]))
        for tag, sb in sat_basis.items():
            b0 = sat_b0[tag]
            nf = b0.zeros()
            nf[tag_cells[tag]] = nu_el[tag]    # P0 dof == global element id
            K = K + asm(stiffness_nu, sb, nu=b0.interpolate(nf))
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
        # Per-ELEMENT ν(|B|) update in every saturable (iron) domain — each
        # triangle saturates on its own, so thin features (spoke bridges)
        # saturate correctly even though the domain average stays low.
        Bx_tri, By_tri = _per_triangle_B(mesh, A)
        Bmag_tri = np.sqrt(Bx_tri ** 2 + By_tri ** 2)
        changed = False
        for tag, sb in sat_basis.items():
            idx = tag_cells[tag]
            Bm = Bmag_tri[idx]
            mat_t = tag_mat[tag]
            if mat_t.bh_curve and len(mat_t.bh_curve) >= 2:
                mu_new = _mu_r_from_bh_vec(mat_t.bh_curve, Bm)
            else:
                ratio = np.where(Bm > 1.8, (1.8 / np.maximum(Bm, 1e-9)) ** 3, 1.0)
                mu_new = mat_t.mu_r * ratio + 5.0 * (1.0 - ratio)
            nu_new = 1.0 / (MU0 * np.maximum(mu_new, 1.0))
            nu_upd = 0.5 * nu_el[tag] + 0.5 * nu_new
            rel = float(np.max(np.abs(nu_upd - nu_el[tag])
                               / np.maximum(nu_el[tag], 1e-30)))
            if rel > 0.02:
                changed = True
            nu_el[tag] = nu_upd
            log.info("FEM iter %d: tag=%d B_max=%.2fT μ_r median %.0f (per-elem)",
                     it, tag, float(Bm.max()) if Bm.size else 0.0,
                     float(np.median(1.0 / (MU0 * nu_el[tag]))))

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

    # ── Per-request material override (multi-user, Stage 2b) ─────────────
    # The signed-in user's own assignment + resolved props (mine / global),
    # sent with the request. The override WINS over the shared config; when
    # absent, everything below behaves EXACTLY as before (built-in / global
    # via mat_lib, which already resolves the admin global layer itself).
    try:
        from motor_ai_sim.material_context import get_request_materials
        _ov = get_request_materials() or {}
    except Exception:
        _ov = {}
    _ov_assign = _ov.get("assignment") or {}
    _ov_mats = _ov.get("materials") or {}
    if _ov_assign:
        assignments = {**assignments, **{k: v for k, v in _ov_assign.items() if v}}

    def _resolve_mat(category: str, name: str):
        """Material dataclass: per-request override props first, else the library."""
        from motor_ai_sim import materials as _ml
        if name and name in _ov_mats:
            try:
                cat = (_ov_mats[name] or {}).get("category") or category
                return _ml.material_from_dict(cat, name, _ov_mats[name])
            except Exception:
                pass
        return _ml.get_material(category, name)

    def _bh_for(part_key: str, category: str = "steel"):
        name = assignments.get(part_key)
        if not name:
            return None
        try:
            m = _resolve_mat(category, name)
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
        if name in _ov_mats:                     # per-request override (known category)
            try:
                cat = (_ov_mats[name] or {}).get("category") or "steel"
                mu = getattr(_resolve_mat(cat, name), "mu_r", None)
                return float(mu) if (mu is not None and float(mu) > 1.0) else 1.0
            except Exception:
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
            mat_mag = _resolve_mat("magnet", mag_name)
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
        # Solid (non-laminated) conductors carry σ for the eddy-current solve.
        DOM_SHAFT:  FEMMaterial("shaft",  mu_r=shaft_mu_r, bh_curve=bh_shaft,
                                sigma=SIGMA_SHAFT),
        DOM_COIL:   FEMMaterial("coil",   mu_r=1.0, sigma=SIGMA_CU_20),
        DOM_MAG_N:  FEMMaterial("mag_N",  mu_r=mu_rec, sigma=SIGMA_NDFEB),
        DOM_MAG_S:  FEMMaterial("mag_S",  mu_r=mu_rec, sigma=SIGMA_NDFEB),
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


def _field2d_static_inputs(
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    I_phase_rms: Optional[float] = None,
):
    """Config-derived inputs for the magnetostatic field solve.

    SHARED by fem_field2d (which builds its own mesh) and solve_field2d_on_mesh
    (which consumes a prebuilt mesh from the `mesh` module). Single source for the
    operating point + per-domain materials, so the self-meshing path and the
    mesh -> solver handoff path can never drift apart.

    Returns (polys_simplified, materials, params).
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
    from motor_ai_sim.config import get_config

    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)

    # ── Operating-point currents (γ=0 → q-axis, +π/2 shift) ──────────────
    if I_phase_rms is None:
        I_phase_rms = sim.get("max_current", 85.0)
    n_parallel   = wind.get("n_parallel", 2)
    n_wires      = geo.get("num_wires_per_slot", 14)
    pole_pairs   = p.num_poles // 2
    I_coil_peak  = I_phase_rms / n_parallel * math.sqrt(2)
    theta_e      = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + DAXIS_SHIFT_DEG)
    I_ph = {
        'A': I_coil_peak * math.cos(theta_e),
        'B': I_coil_peak * math.cos(theta_e - 2 * math.pi / 3),
        'C': I_coil_peak * math.cos(theta_e + 2 * math.pi / 3),
    }

    # ── Real CadQuery polygons at this rotor angle (simplified to match mesh) ──
    motor = CadQueryMotor()
    polys = _simplify_polys(motor.get_2d_polygons(rotor_angle_deg=rotor_angle_deg), tol_mm=0.3)

    slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
    mats = build_materials(I_ph, d.winding_layout, polys, rotor_angle_deg, slot_area, n_wires)
    return polys, mats, p


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

    t_start = _t.time()
    polys, mats, p = _field2d_static_inputs(rotor_angle_deg, gamma_deg)

    log.info("FEM: building triangle mesh (h=%.2f mm)…", mesh_size_mm)
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


def solve_field2d_on_mesh(
    mesh,
    cell_tags: np.ndarray,
    *,
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    I_phase_rms: Optional[float] = None,
) -> Dict[str, Any]:
    """Magnetostatic field solve on a PREBUILT mesh — the end-to-end
    mesh -> solver handoff.

    The static solver consumes exactly the discretization the `mesh` module
    produced (its MeshIR vertices/triangles/cell_tags), instead of meshing
    again. The operating point + per-domain materials come from
    _field2d_static_inputs, SHARED with the self-meshing fem_field2d, so the
    physics is identical — only the mesh provenance differs.

    Returns a JSON-friendly field summary (no per-node arrays); callers that
    need the full field still use fem_field2d / the field route.
    """
    import time as _t
    t0 = _t.time()
    _polys, mats, _p = _field2d_static_inputs(rotor_angle_deg, gamma_deg, I_phase_rms)
    cell_tags = np.asarray(cell_tags).astype(int)

    A = solve_magnetostatics(mesh, cell_tags, mats)
    Bx, By = _per_triangle_B(mesh, A)
    Bmag = np.sqrt(Bx ** 2 + By ** 2)
    return {
        "n_nodes":     int(mesh.p.shape[1]),
        "n_cells":     int(mesh.t.shape[1]),
        "A_z_min":     float(A.min()),
        "A_z_max":     float(A.max()),
        "B_mag_max_T": float(Bmag.max()),
        "B_mag_mean_T": float(Bmag.mean()),
        "solve_time_s": round(_t.time() - t0, 3),
    }


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


def band_limit_torque(T_series, n_steps_per_period, n_periods):
    """Reconstruct T(t) from the electrical orders a BALANCED three-phase machine
    can physically produce — DC + EVERY 6·k order (6, 12, 18, 24, …): the
    6th/12th… torque ripple and the order-12 cogging of a 24/28 machine — and
    discard everything else.

    Both transient pipelines inject NON-physical torque ripple at forbidden
    orders: the sliding band steps the rotor across discrete slip nodes, and the
    remesh-per-frame path gives every frame a slightly different mesh.  Both errors
    spread broadband over orders a 3-phase drive cannot make (1,2,4,5,7,…) and
    NEITHER converges with mesh refinement → they are numerical, not real.  So we
    simply KEEP THE MULTIPLES OF 6 and drop the rest — no amplitude threshold, no
    special-casing.  The MEAN (calibrated average torque) is preserved exactly.

    Returns (T_phys_list, ripple_phys_pct, ripple_raw_pct)."""
    x = np.asarray(T_series, float); n = x.size
    if n == 0:
        return [], 0.0, 0.0
    avg = float(x.mean())
    def _pp(arr):
        return (100.0 * (float(arr.max()) - float(arr.min())) / abs(avg)
                if abs(avg) > 1e-9 else 0.0)
    raw_rip = _pp(x)
    nper = max(1, int(round(n_periods)))
    step = 6 * nper                                  # electrical order 6 → bin 6·nper
    if n < 2 * step:                                 # too few frames to resolve order 6
        return x.tolist(), raw_rip, raw_rip
    F = np.fft.rfft(x - avg)
    G = np.zeros_like(F)
    G[step:F.size:step] = F[step:F.size:step]        # keep DC + every 6·k harmonic
    xf = np.fft.irfft(G, n=n) + avg
    return xf.tolist(), _pp(xf), raw_rip


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

# Conductivities of the SOLID (non-laminated) conductor regions for the
# eddy-current (magnetodynamic) solver [S/m].  σ=0 ⇒ no eddy (air, laminated
# iron).  These move the eddy loss INTO the field solve (J = −σ ∂A/∂t).
SIGMA_CU_20  = 1.0 / RHO_CU_20   # ≈ 5.80e7  (temperature-corrected at use)
SIGMA_NDFEB  = 6.7e5             # sintered NdFeB
SIGMA_SHAFT  = 4.5e6             # carbon-steel shaft


def end_winding_factor_geom(p, geo_cfg) -> float:
    """Estimate the end-winding length factor k_end = (active + end-turn) /
    active from the geometry.  A 2-D solve only resolves the in-slot (active,
    = stack-length) copper; the end-turns that loop outside the iron stack add
    series length the 2-D model can't see.  Per conductor the path length is
    L_stack + 2·L_endturn, so k_end = 1 + 2·L_endturn/L_stack.

    This machine is a 24-slot / 28-pole FRACTIONAL-SLOT CONCENTRATED winding
    (q = slots/(phases·poles) = 0.29 < 1) → tooth coils, each wound around ONE
    tooth.  Its end-turns are SHORT (they just arc over the tooth).

    SINGLE SOURCE: delegates to motor_ai_sim.masses.end_winding_factor so the copper
    MASS (compute_masses), phase RESISTANCE and LOSS (copper_loss_W) all scale by the
    exact same k_end."""
    from motor_ai_sim.masses import end_winding_factor
    return end_winding_factor(p, geo_cfg)


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


def _params_from_geo_dict(g: dict):
    """Build MotorDomainParams from a geometry dict (mirror of
    geometry_2d.params_from_config, but from an in-memory dict so a candidate
    design can be evaluated WITHOUT touching the global config file)."""
    from motor_ai_sim.simulation.geometry_2d import MotorDomainParams
    mm = 1e-3
    r_so = g["stator_diameter"] / 2 * mm
    r_si = r_so - g["core_thickness"] * mm - g["slot_height"] * mm
    r_ro = r_si - g["air_gap"] * mm
    r_ri = r_ro - g["magnet_height"] * mm - g["rotor_house_height"] * mm
    r_sh = r_ri - g["shaft_height"] * mm
    # Pole/slot COUNT is defined by the geometry (magnets/slots) — num_poles/num_slots
    # are authoritative; the segment product is only a fallback (a stale num_seg from a
    # different motor must not override the real count).
    num_slots = int(g.get("num_slots") or round(g["num_seg"] * g["num_slots_per_segment"]))
    num_poles = int(g.get("num_poles") or round(g["num_seg"] * g["num_poles_per_segment"]))
    slot_width_m = (g["wire_width"] + 2 * g["wire_spacing_x"]
                    + 2 * g["insulation_thickness"]) * mm
    return MotorDomainParams(
        r_stator_out=r_so, r_stator_in=r_si, r_rotor_out=r_ro, r_rotor_in=r_ri,
        r_shaft_in=r_sh, r_air_out=r_si, r_air_in=r_ro,
        num_poles=num_poles, num_slots=num_slots,
        stack_length=g.get("motor_length", 30) * mm,
        magnet_fill_fraction=g.get("magnet_fill_down", 0.9),
        slot_width_m=slot_width_m, slot_height_m=g["slot_height"] * mm,
        wire_width_m=g["wire_width"] * mm, wire_height_m=g["wire_height"] * mm,
        num_wires_per_slot=int(g["num_wires_per_slot"]))


def fem_transient_sliding_band(
    n_steps_per_period: int = 12,
    n_periods: float = 1.0,
    gamma_deg: float = 0.0,
    I_phase_rms: float = 85.0,
    mesh_size_mm: float = 3.0,
    min_size_mm: float = 0.3,
    outer_air_factor: float = 1.3,
    gap_layers: float = 3.0,     # element layers across the air gap (Mesh-tab slider)
    n_sectors: int = 4,
    stator_fillet_mm: float = 0.0,
    nonlinear_iterations: int = 14,
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    geo_override: dict = None,
    eddy: bool = False,          # opt-in: time-coupled σ·∂A/∂t eddy-current solve
    rotor_eddy: bool = False,    # field-based magnet/shaft eddy losses (stranded coils)
    demag: bool = False,         # opt-in: per-element irreversible demagnetisation
    component_mesh_mm: dict = None,  # per-part target element size {comp: mm}
    return_field: bool = False,  # also return a field snapshot for the viewer
    field_first: bool = False,   # snapshot the FIRST frame (rotor at angle0) instead
                                 # of the last — used by the magnetostatic field view
                                 # so the picture matches the requested rotor angle
    torque_filter: bool = True,  # band-limit T(t) to the physical 6·k orders
                                 # (False = raw per-frame Maxwell-stress torque)
    pole_copy: Optional[bool] = None,  # bit-identical pole/slot mesh; None=env default
    progress_cb=None,            # optional callback(done:int, total:int) per frame
    magnet_scale: float = 1.0,   # scale ALL magnet Br (0 → PMs off = reluctance torque)
    rotor_angle0_deg: float = 0.0,   # DIAGNOSTIC: build the rotor PHYSICALLY rotated
                                     # in CAD (magnets+pockets rotated before meshing);
                                     # with 1 step this is a true static solve at that
                                     # angle using the SB machinery minus the sliding.
    hi_fidelity: bool = False,       # "High-fidelity torque": 2× slip-ring nodes + finer
                                     # feature mesh (÷8 not ÷4) → pushes the numerical
                                     # picket-fence torque hash to higher orders + halves
                                     # the over-resolved cogging.  ~2× slower; mean torque
                                     # unchanged.  Off by default (speed).
    honest_eddy: bool = False,       # ADDITIVE diagnostic: ALSO compute the coupled
                                     # (reaction-included) rotor eddy via eddy_solver_2d
                                     # for comparison vs the resistance-limited post-
                                     # process.  Captures rotor-node A history; fail-safe
                                     # (any error leaves the production numbers intact).
    structured_gap: bool = False,    # ANSYS-style concentric-ring air-gap mesh (experimental
                                     # Mesh-tab toggle; default off = free gmsh gap).
    drive: str = "current",          # "current" = imposed sinusoidal phase currents (default);
                                     # "voltage" = imposed sinusoidal phase VOLTAGE — the phase
                                     # currents become circuit STATE solved from V = R·i + dψ/dt
                                     # each frame, so non-sinusoidal back-EMF drives REAL
                                     # parasitic harmonic currents (FOC-drive verification mode).
    v_phase_peak: float = 0.0,       # voltage drive: phase-voltage amplitude [V, peak]
    v_delta_deg: float = 0.0,        # voltage drive: voltage angle [°el] in the SAME frame as γ
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
    # Mesh density is driven ENTIRELY by the Mesh-tab sliders now (mesh_size,
    # min_size, gap_layers, normal_deviation) — no hidden clamp.  Earlier this
    # path hard-clamped iron to 2 mm and the gap floor to 0.1 mm "for smooth
    # T(t)", but that silently overrode the sliders (they looked dead).  The
    # air-gap is resolved by gap_layers (element size = gap/gap_layers, applied
    # under min_size in build_mesh_from_polygons), so torque accuracy is the
    # user's choice: finer mesh + more gap layers = smoother T(t), coarser =
    # faster.  Defaults (mesh 4 mm clamped→… no: now literally 4 mm; gap_layers
    # 3) reproduce the previous behaviour closely; drag to mesh≈2 mm / gap≈3-4
    # for the cleanest torque.
    mesh_size_mm = float(mesh_size_mm)
    min_size_mm = float(min_size_mm)
    cfg = get_config(); sim = cfg.get("simulation", {})
    geo = dict(cfg.get("geometry", {})); wind = cfg.get("winding", {})
    # Candidate-design evaluation (optimization refine): overlay a geometry
    # override in-memory so the global config / Simulation state is untouched.
    if geo_override:
        geo.update({k: v for k, v in geo_override.items()})
        p = _params_from_geo_dict(geo)
    else:
        p = params_from_config()
    dom = MotorDomains2D(p)
    # ── Feature-relative mesh refinement (real element sizing, not a fudge) ──
    # mesh_size_mm is an ABSOLUTE target.  On a small motor the UI default
    # (4 mm) leaves ~1 element across a 2.8 mm slot → the field, torque and
    # back-EMF are grossly under-resolved AND mesh-dependent.  Convergence study
    # (12s/14p 40 mm, I=38, γ=−32): at mesh 1.5 mm the result is garbage
    # (T 0.52, KV 484, 175 % ripple); it only plateaus at mesh ≲ slot_width/3
    # (T≈0.565, KV≈585, 11 % ripple from 1.0→0.5 mm).  So clamp the target to
    # resolve the smallest in-plane feature (slot or tooth) with ≥4 elements.
    # This only ever REFINES (min) — motors with large features (e.g. 200 mm)
    # keep their coarser mesh.  Radial air-gap resolution is separate
    # (gap_layers).  No operating-point tuning — pure geometric element sizing.
    try:
        _feat_mm = min(float(geo.get("slot_width", 1e9) or 1e9),
                       float(geo.get("tooth_width", 1e9) or 1e9))
        if 0.0 < _feat_mm < 1e8:
            _elem_per_feat = 4.0 if hi_fidelity else 2.0   # normal: 2 elem/feature ceiling (÷4 hi-fi)
            _mesh_feat = max(float(min_size_mm), _feat_mm / _elem_per_feat)
            if _mesh_feat < mesh_size_mm - 1e-9:
                log.info("mesh auto-refined %.2f → %.2f mm (smallest feature "
                         "%.2f mm ÷ %g) — small-motor resolution",
                         mesh_size_mm, _mesh_feat, _feat_mm, _elem_per_feat)
                mesh_size_mm = _mesh_feat
    except Exception as _e:
        log.warning("mesh feature-refine skipped: %s", _e)
    # n_sectors == -1: DIAGNOSTIC full ring — no sector cuts at all (the moving
    # band makes a closed 360° pair of halves feasible: the halves are open
    # annuli, not the historically OCC-double-meshed full cross-section).
    # n_sectors ≤ 1 → FULL RING (NS=1).  -1 was the historical "full ring" flag;
    # 1 ("Full" from the UI) must mean the same — NOT fall through to NS=4, which is
    # an invalid 90° wedge for any motor whose pole count is not a multiple of 4
    # (e.g. 14 poles → 3.5/sector → corrupt anti-periodic BC → spurious torque/ripple).
    _full_ring = (int(n_sectors) <= 1)
    NS = 1 if _full_ring else int(n_sectors)
    sector_deg = 360.0 / NS
    pole_pairs = p.num_poles // 2
    # Sector boundary sign: ANTI-periodic (−1) only when the sector spans an
    # ODD number of poles (e.g. NS=4 → 7 poles); PERIODIC (+1) for an EVEN pole
    # count (NS=2 → 14 poles).  Mirrors the static solve's `anti_periodic =
    # (poles_per_sector % 2 == 1)`.  Hard-coding −1 here corrupted the 1/2-sector
    # field → 40 %-unbalanced phase-A flux linkage + 70 % torque ripple.
    _poles_per_sector = p.num_poles // NS
    _bc_sign = -1 if (_poles_per_sector % 2 == 1) else 1
    n_parallel = wind.get("n_parallel", 2)
    n_wires = int(geo.get("num_wires_per_slot", 14))
    # Physical copper loss: ρ_Cu(coil_temp)·J²·V_cu·k_end (end-winding the 2-D
    # field never sees).  R_phase is derived from it so the R·I voltage drop is
    # temperature-consistent — no hard-coded resistance.
    P_cu, _k_end_used, R_phase = copper_loss_W(
        p, geo, float(I_phase_rms), n_parallel,
        coil_temp_c=coil_temp_c, end_winding_factor=end_winding_factor)
    # Synchronous machine: rpm and f_elec are LOCKED (f = rpm·pp/60).  The
    # config can carry a stale pair (preset-apply wrote rpm but not frequency)
    # — and using the mismatched rpm in ω_mech scaled dB/dt (→ iron/magnet
    # losses) by the wrong speed (×4 at 3950-vs-2000).  rpm is the master
    # (it's what presets/UI write); the frequency is DERIVED, never read.
    rpm = float(sim.get("rpm", 3950))
    _f_cfg = float(sim.get("frequency", 0.0) or 0.0)
    f_elec = rpm * (p.num_poles // 2) / 60.0
    if _f_cfg > 0 and abs(_f_cfg - f_elec) / max(f_elec, 1e-9) > 0.01:
        log.warning("config frequency=%.2f Hz inconsistent with rpm=%.0f "
                    "(→ %.2f Hz); using the rpm-derived frequency",
                    _f_cfg, rpm, f_elec)
    slot_area_m2 = p.slot_width_m * p.slot_height_m * p.fill_factor
    mid = 0.5 * (p.r_rotor_out + p.r_stator_in)

    def _currents(rotor_angle_deg):
        Ipk = float(I_phase_rms) / n_parallel * math.sqrt(2)
        # d-axis phase offset so γ=0 = q-axis — shared module constant so the
        # transient, static and eddy paths all use the SAME phase shift.
        te = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + DAXIS_SHIFT_DEG)
        return {'A': Ipk * math.cos(te),
                'B': Ipk * math.cos(te - 2 * math.pi / 3),
                'C': Ipk * math.cos(te + 2 * math.pi / 3)}

    # Voltage drive: imposed sinusoidal PHASE voltage in the same electrical
    # frame as the currents (v_delta_deg is directly comparable to γ), so a
    # clean back-EMF yields near-sinusoidal currents and a distorted one shows
    # its real parasitic harmonic currents + their losses.
    _vdrive = str(drive or "current").strip().lower().startswith("v")
    if _vdrive and rotor_eddy:
        # Voltage drive + conducting-rotor dynamics is NOT implemented: the
        # eddy path imposes coil currents via integral constraints while the
        # voltage circuit needs them as unknowns.  Before this guard the frame
        # loop silently took the no-eddy branch — the vdrive orbit then solved
        # DIFFERENT physics (no magnet-eddy screening, ψ off by ~5 %) than the
        # rotor_eddy current-drive it was compared against, skewing the
        # round-trip fundamental ~10 %/19° and ΔP_harm by the whole P_mag.
        # Explicitly drop eddy on BOTH (the route mirrors this in harm_ref) so
        # voltage runs and their references always share the same physics.
        log.warning("voltage drive: rotor_eddy not supported yet — running "
                    "without conducting-rotor dynamics (magnet/shaft eddy "
                    "losses excluded; ΔP_harm = copper+iron harmonic cost)")
        rotor_eddy = False

    def _voltages(rotor_angle_deg):
        vpk = float(v_phase_peak)
        te = math.radians(rotor_angle_deg * pole_pairs + v_delta_deg + DAXIS_SHIFT_DEG)
        return {'A': vpk * math.cos(te),
                'B': vpk * math.cos(te - 2 * math.pi / 3),
                'C': vpk * math.cos(te + 2 * math.pi / 3)}

    # ── High-fidelity = genuinely higher resolution EVERYWHERE the noise lives ─
    # The raw torque ripple of the sliding-band transient carries a BROADBAND
    # numerical floor at the non-6·k orders a balanced 3-φ machine cannot produce.
    # Measured (40 mm 12s/14p) it is a FIELD-level artifact: IDENTICAL on every
    # torque contour (band strip / rotor-surface / stator-surface / whole gap) and
    # NOT removed by any single knob — gap_layers ALONE even makes it worse
    # (20.8 %→25.5 %), steps don't move it (→21.8 %), pole_copy doesn't (→20.3 %).
    # Only RAISING ALL THREE TOGETHER pulls the floor down: tangential band
    # density (slip nodes), radial gap density (gap_layers) and the global mesh.
    # So hi_fidelity bundles all three (mesh ÷8 vs ÷4 above; slip 2× below;
    # gap_layers≥4 here) → measured raw 20.8 %→~14 %, RMS 4.7 %→3.0 %.  This is the
    # honest "spend compute for accuracy" mode, NOT a filter — the real DC torque
    # is unchanged and the 6·k physical ripple already matches Ansys.  gap_layers
    # is bumped ONLY inside this bundle (it is counter-productive on its own).
    if hi_fidelity:
        gap_layers = max(float(gap_layers), 4.0)
    # ── Slip-ring resolution (ADAPTIVE to pole count) ─────────────────────
    # Nodes per electrical period = a multiple of 24 (so 24/30/40/60/120 are all
    # valid step counts) and ≥120, scaled so the full-ring node count stays
    # ≥~1008 (fine tangential spacing → accurate ripple).  n_slip_eff =
    # pole_pairs·per_period is divisible by pole_pairs BY CONSTRUCTION → the rotor
    # advances a whole number of nodes each step (strictly periodic torque) and
    # the electrical period tiles EXACTLY (vs the old fixed 1008 → 100.8/period).
    # Air-gap layers is the SINGLE fidelity regulator (the UI "Air-gap layer" slider):
    # more layers -> more tangential slip nodes -> less node-identification jitter in the
    # eddy loss (the same knob also sets the radial gap density above).  Calibrated so
    # gap_layers=1 -> 1008 (fast) and gap_layers=4 -> 2016 (= the retired hi-fidelity slip);
    # the _slip_per_period rounding below keeps the count pole-pair-divisible for any motor.
    _slip_base = int(round(1008.0 * (max(1.0, float(gap_layers)) + 2.0) / 3.0))
    _slip_per_period = 24 * max(5, math.ceil(_slip_base / (24 * pole_pairs)))
    n_slip_eff = pole_pairs * _slip_per_period
    if bool(_SB_AIRGAP_MACRO) and _full_ring:
        # The harmonic macroelement is ANALYTIC between ring nodes, so a COARSE ring
        # is enough — and its coupling is a DENSE N×N block, so a small N is wanted.
        # 48 nodes/period resolves angular harmonics to 24·pole_pairs (≫ the
        # significant slot/pole orders); the node-identification band needed the
        # fine ≥120/period purely to keep the re-pairing quiet — the macroelement
        # does not.  This is the efficiency lever that makes the dense block cheap.
        _slip_per_period = 48
        n_slip_eff = pole_pairs * _slip_per_period
    if _SLIP_PER_PERIOD_OVERRIDE and _full_ring:        # advanced: force ring density
        _slip_per_period = int(_SLIP_PER_PERIOD_OVERRIDE)
        n_slip_eff = pole_pairs * _slip_per_period

    # ── Snap steps/period so the rotor lands on whole slip nodes ──────────
    # For a uniform (periodic, non-chaotic) rotor advance, n_steps must divide
    # the nodes-per-period.
    _nodes_per_period = _slip_per_period
    _req_steps = int(n_steps_per_period)
    n_steps_per_period = _snap_steps_to_nodes(_req_steps, _nodes_per_period)
    if n_steps_per_period != _req_steps:
        log.info("SB: snapped steps/period %d → %d (divisor of %d slip nodes/"
                 "period → whole-node rotor steps, periodic torque)",
                 _req_steps, n_steps_per_period, _nodes_per_period)

    # ── Build the two halves ONCE ────────────────────────────────────────
    motor = CadQueryMotor()
    if geo_override:
        motor.set_parameters(geo_override)   # in-memory candidate geometry
    polys = motor.get_2d_polygons(rotor_angle_deg=float(rotor_angle0_deg))
    # STRUCTURED (mapped) gap uses the MERGED band: the route-A cells own the
    # whole gap r_ro→mid→r_si with the SINGLE shared slip ring at mid_r (uniform
    # S·M grid).  The moving-band split (mid±δ, empty re-stitched strip) is
    # incompatible with the cells, so force merged when structured_gap is on.
    _band_mode = ("merged" if structured_gap
                  else ("moving" if (_SB_MOVING_BAND or _full_ring) else "merged"))
    polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=stator_fillet_mm,
                            n_slip=n_slip_eff, gap_layers=gap_layers,
                            structured_gap=structured_gap,
                            band_mode=_band_mode)
    ms, ts, cs, mr, tr, cr = _build_sliding_band_meshes(
        polys, 0.0, mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, band_thickness_mm=0.4,
        n_sectors=NS, geo_cfg=motor.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0,
        gap_layers=gap_layers,
        component_mesh_mm=component_mesh_mm,
        full_ring=_full_ring, pole_copy=pole_copy)
    Ps, Tts = ms.p.copy(), ms.t.copy(); Pr, Ttr = mr.p.copy(), mr.t.copy()
    nsn = Ps.shape[1]
    Pall = np.hstack([Ps, Pr]); Tall = np.hstack([Tts, Ttr + nsn])
    n = Pall.shape[1]
    mesh_all = MeshTri(Pall, Tall)

    def _ring(P, r_at):
        # Select the SEEDED ring nodes only: radius window + snap-to-grid in
        # angle.  A bare radius window also sweeps in foreign free-mesh nodes
        # that happen to sit within microns of the ring radius (the stator
        # free row is only ~0.13 mm thick) — those polluted the pairing.
        r = np.hypot(P[0], P[1])
        idx = np.where(np.abs(r - r_at) < 1e-6)[0]
        ang = np.degrees(np.arctan2(P[1, idx], P[0, idx])) % 360.0
        step = 360.0 / n_slip_eff
        kg = np.round(ang / step)
        on_grid = np.abs(ang - kg * step) < (0.05 * step)
        idx, ang, kg = idx[on_grid], ang[on_grid], kg[on_grid].astype(int) % n_slip_eff
        # one node per grid slot (keep the angularly-closest if duplicated)
        if kg.size:
            order = np.lexsort((np.abs(ang - np.round(ang / step) * step), kg))
            idx, kg = idx[order], kg[order]
            keep = np.concatenate([[True], np.diff(kg) != 0])
            idx, kg = idx[keep], kg[keep]
            o = np.argsort(kg)
            idx = idx[o]
        return idx
    # MOVING BAND: the halves end at two DIFFERENT uniform rings — rotor at
    # R1 = mid−δ (rotates rigidly with the rotor mesh), stator at R2 = mid+δ
    # (stationary).  The annulus between them is re-stitched every frame in
    # closed form.  Legacy (merged single ring at mid) kept as fallback.
    _band_radii = polys.get("band_radii_mm")
    _moving = bool(_band_radii) and len(_band_radii) == 2
    if _moving:
        _r1_m = float(_band_radii[0]) * 1e-3   # metres
        _r2_m = float(_band_radii[1]) * 1e-3
        rring = _ring(Pr, _r1_m)
        sring = _ring(Ps, _r2_m)
    else:
        rring = _ring(Pr, mid)
        sring = _ring(Ps, mid)
    Nring = min(sring.size, rring.size)
    if sring.size != rring.size:
        log.warning("band ring node counts differ: stator=%d rotor=%d — "
                    "truncating to %d", sring.size, rring.size, Nring)
    sring = sring[:Nring]; rring = rring[:Nring]
    if _full_ring:
        spacing = 360.0 / Nring          # CLOSED ring: N nodes, N intervals
    else:
        spacing = sector_deg / (Nring - 1)

    # Constant radial-cut anti-periodic pairs on the combined mesh.
    if _full_ring:
        Mn, Sn = np.array([], int), np.array([], int)   # no cuts at all
    else:
        Mn, Sn = _pair_sector_cut_nodes(mesh_all, NS)

    # Forms
    @BilinearForm
    def _stiff(u, v, w): return _dot(_grad(u), _grad(v))
    @BilinearForm
    def _stiff_nu(u, v, w):            # per-element reluctivity ν(x)
        return w["nu"] * _dot(_grad(u), _grad(v))
    @BilinearForm
    def _massform(u, v, w):            # ∫ u·v  — for the σ·∂A/∂t eddy term
        return u * v
    @LinearForm
    def _f1(v, w): return 1.0 * v
    @LinearForm
    def _fdy(v, w): return _grad(v)[1]
    @LinearForm
    def _fdx(v, w): return _grad(v)[0]
    @LinearForm
    def _msrc(v, w):            # magnet source with PER-ELEMENT M (P0 fields):
        return w["mx"] * _grad(v)[1] - w["my"] * _grad(v)[0]   # ∫(Mx·∂v/∂y − My·∂v/∂x)

    # ── Pre-assemble per-tag stiffness K0 + constant magnet source ───────
    matr0 = build_materials(_currents(0.0), dom.winding_layout,
                            getattr(cr, "polys", polys), 0.0, slot_area_m2, n_wires)
    # unit-current stator sources (per phase), magnet source is in rotor half
    # σ per domain tag for the eddy-current mass matrix (temperature-corrected
    # copper).  Solid conductors only — air / laminated iron stay σ=0.
    _sig_cu_T = SIGMA_CU_20 / (1.0 + ALPHA_CU * (float(coil_temp_c) - 20.0))

    # ── Assigned materials (library) — REAL σ / BH / loss curves ──────────
    # Fetched BEFORE the σ-mass assembly so the eddy solve uses the σ of the
    # materials actually assigned in the UI (e.g. an ALUMINIUM shaft is 2.6e7
    # S/m — 5.7× the hardcoded carbon-steel value).  Falls back to the generic
    # constants when a lookup fails.
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

    def _lookup_sigma(name: str) -> float:
        for _cat in ("conductor", "steel", "magnet"):
            try:
                return float(getattr(_mat_lib.get_material(_cat, name), "sigma", 0.0) or 0.0)
            except Exception:
                continue
        return 0.0

    _sigma_mag_lib = (float(getattr(_magnet_mat, "sigma", 0.0) or 0.0)
                      if _magnet_mat else 0.0) or SIGMA_NDFEB
    _sigma_shaft_lib = _lookup_sigma(str(_ma.get("shaft", ""))) or SIGMA_SHAFT

    def _sigma_of_tag(t: int) -> float:
        t = int(t)
        if t >= DOM_COIL_BASE or t == DOM_COIL:      return _sig_cu_T
        if t >= DOM_MAG_BASE or t in (DOM_MAG_N, DOM_MAG_S): return _sigma_mag_lib
        if t == DOM_SHAFT:                           return _sigma_shaft_lib
        return 0.0

    half = {}
    for name, (P, T, tags, mats) in (
        ("s", (Ps, Tts, ts, None)), ("r", (Pr, Ttr, tr, matr0))):
        mesh = MeshTri(P, T); b = Basis(mesh, ElementTriP1()); nh = b.N
        K0 = {}; cells = {}; mu0 = {}
        Msig = _csr((nh, nh))            # σ-weighted mass (eddy term), 0 in air/iron
        for tag in np.unique(tags):
            idx = np.where(tags == tag)[0]; cells[int(tag)] = idx
            sb = Basis(mesh, ElementTriP1(), elements=idx)
            K0[int(tag)] = asm(_stiff, sb)
            _sig = _sigma_of_tag(int(tag))
            if _sig > 0.0:
                Msig = Msig + asm(_massform, sb) * _sig
        half[name] = dict(mesh=mesh, b=b, n=nh, K0=K0, cells=cells,
                          Msig=Msig.tocsr())
    # ── magnet source (rotor half, constant — magnets fixed at angle 0) ──
    # Built from a PER-ELEMENT magnetisation field (P0) so it can be de-rated
    # element-by-element by the demag pass below (_br_glob, 1.0 = full Br).
    # With _br_glob ≡ 1 this is numerically identical to the old per-tag sum.
    _rb  = Basis(half["r"]["mesh"], ElementTriP1())
    _rb0 = _rb.with_element(ElementTriP0())     # P0: dof == rotor element id
    _nt_r = half["r"]["mesh"].t.shape[1]
    _Mx_glob = np.zeros(_nt_r); _My_glob = np.zeros(_nt_r)
    for tag, idx in half["r"]["cells"].items():
        m = matr0.get(int(tag))
        if m is None or (abs(m.Mx) + abs(m.My)) <= 0:
            continue
        _Mx_glob[idx] = m.Mx; _My_glob[idx] = m.My
    def _build_fmag(_br):
        return asm(_msrc, _rb,
                   mx=_rb0.interpolate(_Mx_glob * _br),
                   my=_rb0.interpolate(_My_glob * _br))
    # magnet_scale lets the torque decomposition turn the PMs OFF (=0 →
    # reluctance-only torque) or weaken them, without touching geometry.
    _br_glob = np.full(_nt_r, float(magnet_scale))   # per-element Br factor (demag de-rating × magnet_scale)
    f_mag = _build_fmag(_br_glob)
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

    # ── Stage 2: solid-copper current-constrained eddy data ──────────────────
    # Each coil is a SOLID bar: J = σ(−∂A/∂t + U_c) with ∫J dA = I_c imposed.
    # Per coil store: g_c (σ-lumped load, full DOF space), S_c = ∫σ dA, and the
    # imposed-current coefficient I_c_unit = dir·n_wires·(area/slot_area) so that
    # I_c = Ist[phase]·I_c_unit exactly matches the magnetostatic ampere-turns.
    _coil_con = []
    if eddy:
        _ones_s = np.ones(half["s"]["n"])
        _nr0 = half["r"]["n"]
        for tag, idx in half["s"]["cells"].items():
            if int(tag) < DOM_COIL_BASE:
                continue
            sb = Basis(half["s"]["mesh"], ElementTriP1(), elements=idx)
            g_s = np.asarray((asm(_massform, sb) * _sig_cu_T) @ _ones_s)
            nm = (mats_full.get(int(tag)) or FEMMaterial("x")).name
            ph = nm[-2] if nm.endswith(("+", "-")) else "A"
            dr = 1.0 if nm.endswith("+") else -1.0
            area_c = float(areas_s[idx].sum())
            _coil_con.append({
                "g": np.concatenate([g_s, np.zeros(_nr0)]),
                "S": float(g_s.sum()),
                "Iunit": dr * n_wires * area_c / max(slot_area_m2, 1e-12),
                "phase": ph,
                "nodes": np.unique(half["s"]["mesh"].t[:, idx]),   # stator-local node ids
            })

    # ── Rotor-eddy stage: FIELD-BASED magnet eddy losses ─────────────────────
    # The rotor mesh is the rotor's MATERIAL frame (rotation lives in the slip
    # pairing), so dA/dt at a rotor node IS the material ∂A/∂t — no convective
    # term.  Each isolated magnet carries J = σ(−∂A/∂t + U_m) with ∫J dA = 0;
    # U_m is the per-magnet area-mean of ∂A/∂t (uniform σ).  Magnet halves
    # bisected by the sector cut take U = 0 — their (anti)periodic image
    # cancels the net axial current by symmetry.
    #
    # IMPLEMENTATION: post-processing on the magnetostatic A(t) histories with
    # the SAME smoothed angle-derivative the B-field losses use.  An in-loop
    # coupled solve was tried first and rejected: the raw frame-to-frame ∂A/∂t
    # rides on the slip-ring node-merge jitter, and the σ|∂A/∂t|² integral
    # AMPLIFIES that noise with the step count (P_mag tripled going 24→72
    # steps).  _angle_ddt_2d low-pass-filters A(θ) over the unique slip-node
    # positions before differentiating — physics (slot ripple, 1–2 cycles per
    # period) passes, merge jitter dies.  The resistance-limited approximation
    # (no eddy-reaction skin effect) is good for the magnets: skin depth at the
    # slot-passing frequency ≈ 12 mm vs ~14 mm magnet width, and neglecting the
    # reaction errs conservative (slightly over-reports the loss).
    # σ comes from the ASSIGNED magnet material (library), not a constant.
    _rot_con = []            # bordered ∫J=0 rows — only for the eddy J-VIEW mode
    _rot_sig_nodes = []      # (nodes_global, σ) per rotor group — J snapshot
    _mag_groups = []         # per magnet: element triplets/areas for the loss
    _magnode_glob = np.array([], int)   # global DOF ids of all magnet nodes
    _shaft_group = None                 # field-based shaft eddy group (rotor frame)
    _shaftnode_glob = np.array([], int) # global DOF ids of the shaft nodes
    if rotor_eddy:
        _ones_r = np.ones(half["r"]["n"])
        _areas_r_re = _triangle_areas(half["r"]["mesh"])
        _mag_tags = [int(t) for t in half["r"]["cells"]
                     if (matr0.get(int(t)) is not None
                         and (abs(matr0[int(t)].Mx) + abs(matr0[int(t)].My)) > 0)]
        _mag_area = {t: float(_areas_r_re[half["r"]["cells"][t]].sum())
                     for t in _mag_tags}
        _med_area = float(np.median(list(_mag_area.values()))) if _mag_area else 0.0
        _magnode_loc = (np.unique(np.concatenate(
            [half["r"]["mesh"].t[:, half["r"]["cells"][t]].ravel()
             for t in _mag_tags])) if _mag_tags else np.array([], int))
        _magnode_glob = _magnode_loc + nsn
        _n_interior = _n_halves = 0
        for t in _mag_tags:
            idx = np.asarray(half["r"]["cells"][t], int)
            tri = half["r"]["mesh"].t[:, idx]                  # (3, E) rotor-local
            is_half = bool(_med_area > 0 and _mag_area[t] < 0.6 * _med_area)
            _mag_groups.append({
                "tri": np.searchsorted(_magnode_loc, tri),     # → magnet-node idx
                "areas": _areas_r_re[idx].astype(float),
                "half": is_half,
            })
            nds = np.unique(tri)
            _rot_sig_nodes.append((nds + nsn, _sigma_mag_lib))
            if is_half:
                _n_halves += 1               # edge half-magnet → U = 0
                continue
            _n_interior += 1
            # ∫J=0 constraint row — used only by the coupled eddy J-VIEW mode.
            sb = Basis(half["r"]["mesh"], ElementTriP1(), elements=idx)
            g_r = np.asarray((asm(_massform, sb) * _sigma_mag_lib) @ _ones_r)
            _rot_con.append({"g": np.concatenate([np.zeros(nsn), g_r]),
                             "S": float(g_r.sum()),
                             "nodes": nds + nsn})
        _sh_idx = np.asarray(half["r"]["cells"].get(int(DOM_SHAFT),
                                                    np.array([], int)), int)
        if _sh_idx.size:
            _sh_tri = half["r"]["mesh"].t[:, _sh_idx]            # (3, E) rotor-local
            _shaftnode_loc = np.unique(_sh_tri)
            _shaftnode_glob = _shaftnode_loc + nsn
            _rot_sig_nodes.append((_shaftnode_glob, _sigma_shaft_lib))
            # Field-based shaft eddy group (rotor frame, ∫J=0): the shaft co-rotates
            # with the magnets, so the magnet field is DC in its frame → no loss;
            # only the AC coil-current / slot-ripple field dissipates.  Same
            # treatment as the magnets (replaces the lab-frame slab estimate).
            _shaft_group = {
                "tri":   np.searchsorted(_shaftnode_loc, _sh_tri),
                "areas": _areas_r_re[_sh_idx].astype(float),
            }
        log.info("rotor-eddy: %d interior magnets (∫J=0), %d edge halves (U=0) | "
                 "σ_mag=%.3g σ_shaft=%.3g S/m (library)",
                 _n_interior, _n_halves, _sigma_mag_lib, _sigma_shaft_lib)

    # ── EXACT edge data for the magnet A-histories (pole-shift symmetry) ─────
    # The loss window spans whole electrical periods, but the ROTOR-frame
    # signal is NOT periodic over it (the stator structure passes a non-integer
    # number of times), so any wrap at the window edge is wrong.  The missing
    # samples beyond the edges exist EXACTLY inside the window: after one
    # electrical period the whole solution repeats with the rotor advanced two
    # pole pitches, so  A(node n, t±T) = A(node n∓, t)  where n∓ is the node
    # rotated ∓2 pole pitches in the (pole-periodic) rotor mesh — a pure node
    # permutation, no approximation.  Crossing a sector cut multiplies A by the
    # anti-periodic sign.  Built here once; used to pad the magnet histories so
    # the loss derivative has REAL data at both window ends.
    # The pole meshes share IDENTICAL boundaries (setPeriodic) but gmsh meshes
    # each pole INTERIOR independently (measured node mismatch ≈ 0.9 mm), so a
    # pure node permutation does not exist.  The identity is continuous though:
    # the value at the rotated POINT exists in the same solve — so the map is a
    # P1 barycentric INTERPOLATION matrix over the magnet triangles (the same
    # accuracy class as the FEM field itself).
    _pp2 = None                       # (W_fwd, sign_fwd, W_bwd, sign_bwd)
    if rotor_eddy and _magnode_loc.size:
        try:
            from scipy.spatial import Delaunay as _Del, cKDTree as _KD
            _theta2 = math.radians(2.0 * 360.0 / max(1, p.num_poles))  # 2 pole pitches
            _Pn = half["r"]["mesh"].p[:, _magnode_loc]    # (2, Nn) node coords [m]
            _Nn = _Pn.shape[1]
            _sec_rad = math.radians(sector_deg)
            _dt2 = _Del(_Pn.T)
            _kd2 = _KD(_Pn.T)

            def _pole_map(_dir):
                c, s = math.cos(_dir * _theta2), math.sin(_dir * _theta2)
                x = c * _Pn[0] - s * _Pn[1]; y = s * _Pn[0] + c * _Pn[1]
                sg = np.ones(_Nn)
                if not _full_ring:
                    ang = np.mod(np.arctan2(y, x), 2.0 * math.pi)
                    # wrap rotated points back into the wedge; every cut crossing
                    # flips A by the (anti-)periodic boundary sign.  k and k−NS
                    # wraps give the same sign because _bc_sign**NS == +1.
                    for _ in range(int(NS)):
                        _ov = ang > _sec_rad + 1e-9
                        if not _ov.any():
                            break
                        ang = np.where(_ov, ang - _sec_rad, ang)
                        sg = np.where(_ov, sg * _bc_sign, sg)
                    r = np.hypot(x, y)
                    x = r * np.cos(ang); y = r * np.sin(ang)
                _tgt = np.column_stack([x, y])
                _sx = _dt2.find_simplex(_tgt)
                _out = _sx < 0
                _d, _near = _kd2.query(_tgt)
                if _out.any() and float(np.max(_d[_out])) > 0.5 * float(min_size_mm) * 1e-3:
                    raise ValueError(
                        f"{int(_out.sum())} targets {float(np.max(_d[_out]))*1e3:.3f} mm "
                        "outside the magnet hull")
                return _tgt, sg, _near.astype(int)
            _tgF, _sgF, _nrF = _pole_map(+1.0)   # n's position one period LATER
            _tgB, _sgB, _nrB = _pole_map(-1.0)   # … one period EARLIER
            _pp2 = (_dt2, _tgF, _sgF, _nrF, _tgB, _sgB, _nrB)
            log.info("magnet-history edge pads: 2-pole-pitch C1 interpolation map OK "
                     "(%d nodes)", _Nn)
        except Exception as _pe:
            log.warning("magnet-history edge pads unavailable (%s) — "
                        "falling back to C0-detrend edges", _pe)
            _pp2 = None

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
    # The Bertotti coefficients (kh,kc,ke) are FITTED to the material's measured
    # loss-vs-frequency curves at runtime (materials.effective_bertotti), so this
    # IS the real frequency-dependent loss model.  (Steel/magnet materials were
    # already fetched above, before the σ-mass assembly.)
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
    # Non-laminated solid conductors that ALSO carry rotating-field eddy losses
    # (in addition to magnets): the COILS (solid copper bars, stator side) and
    # the SHAFT (solid steel, rotor side).
    _coil_parts = [np.asarray(_i, int) for _t, _i in half["s"]["cells"].items()
                   if int(_t) >= DOM_COIL_BASE or int(_t) == int(DOM_COIL)]
    _coil_idx = np.concatenate(_coil_parts) if _coil_parts else np.array([], int)
    _shaft_idx = np.asarray(half["r"]["cells"].get(int(DOM_SHAFT),
                                                   np.array([], int)), int)
    # Per-frame B histories for the loss elements only (keeps memory small).
    _hist_sx = []; _hist_sy = []; _hist_rx = []; _hist_ry = []
    _hist_mx = []; _hist_my = []; _mshift_hist = []
    _hist_cx = []; _hist_cy = []; _hist_shx = []; _hist_shy = []

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

    # ── Moving-band machinery ─────────────────────────────────────────────
    # The annulus R1..R2 is re-stitched EVERY frame; see _simplify_polys: each
    # ring is a UNIFORM N-gon, so the stitch pattern is IDENTICAL at every
    # shift m — two congruent triangle shapes whose local stiffness (air) and
    # torque vectors are computed ONCE; per frame only the index mapping
    # (rotor k ↔ stator k+m, anti-periodic sign on wrap) changes.  This
    # replaces the node-merge slip coupling whose frozen irregular fans
    # produced the order-6 parasitic cogging.
    if _moving:
        _gR1 = rring.astype(int) + nsn          # rotor-ring DOFs (global ids)
        _gR2 = sring.astype(int)                # stator-ring DOFs
        _dphi_b = math.radians(spacing)

        # ── Harmonic air-gap macroelement (Davat) — analytic gap, smooth torque ──
        # Couple the two uniform rings by the EXACT Laplace solution of the gap
        # annulus instead of the single-layer triangle strip.  Both rings are
        # uniform N-gons ⇒ the coupling is block-circulant and DFT-diagonalises into
        # 2×2 per-harmonic blocks; rotor rotation by m nodes is a smooth phase
        # e^{i·k·φ} (no node re-pairing) → the broadband sliding-band ripple is gone
        # at the source.  Per-harmonic stiffness + nodal assembly validated standalone
        # (energy == analytic == FEM annulus; m-shift == circulant shift).  Full-ring
        # only for now (sector anti-periodic harmonics deferred).
        _use_macro = bool(_SB_AIRGAP_MACRO) and _full_ring
        if _use_macro:
            _Nm = int(Nring)
            _r1M, _r2M, _stkM = float(_r1_m), float(_r2_m), float(p.stack_length)

            def _Qk_gap(k):
                # per-harmonic 2×2 [[Q11(rotor),Q12],[Q12,Q22(stator)]] for the gap
                # energy u^T Q u with u=(A@r1, A@r2); r1=rotor ring, r2=stator ring.
                # Closed form in ρ=r1/r2<1 (the raw r^{±2k} form overflows for the
                # large k reached at N~1008): Q11=Q22=k(1+ρ^2k)/(1−ρ^2k),
                # Q12=−2k·ρ^k/(1−ρ^2k).  ρ^k→0 for high k → self-stiffness ~k, no
                # cross-coupling (the gap low-passes the surface field), as expected.
                if k == 0:
                    c = 1.0 / math.log(_r2M / _r1M)
                    return c, -c, c
                rhok = (_r1M / _r2M) ** k
                rho2k = rhok * rhok
                den = 1.0 - rho2k
                q11 = k * (1.0 + rho2k) / den
                return q11, -2.0 * k * rhok / den, q11

            # PER-UNIT-LENGTH normalisation: the half-mesh K_const carries no stack
            # length (2D solve; L is applied later in the torque/loss post-processing,
            # exactly like _T_band).  So the gap coupling must also be per-unit — the
            # stack length _stkM is applied only in _T_macro below.  (Baking L in here
            # made the gap ~1/L weaker than the iron → decoupled, garbage field.)
            _Gm = 2.0 * math.pi / (MU0 * _Nm)             # DFT energy normalisation (per-unit)
            _mu_rr = np.empty(_Nm); _mu_rs = np.empty(_Nm); _mu_ss = np.empty(_Nm)
            for _j in range(_Nm):
                _q11, _q12, _q22 = _Qk_gap(min(_j, _Nm - _j))   # phys order = min(j,N−j)
                _mu_rr[_j], _mu_rs[_j], _mu_ss[_j] = _Gm*_q11, _Gm*_q12, _Gm*_q22
            for _j in (0, _Nm // 2):                       # unpaired bins counted once
                _mu_rr[_j] *= 0.5; _mu_rs[_j] *= 0.5; _mu_ss[_j] *= 0.5
            _jfreq = np.arange(_Nm, dtype=float)
            _jfreq[_jfreq > _Nm/2] -= _Nm                  # signed frequency (for ∂/∂φ)
            _ii_m = (np.arange(_Nm)[:, None] - np.arange(_Nm)[None, :]) % _Nm

            def _circ_of(mu):                              # circulant C[i,j]=col[(i−j)%N]
                return np.fft.ifft(mu).real[_ii_m]
            _Krr_blk = _circ_of(_mu_rr)                    # rotor-rotor  (m-independent)
            _Kss_blk = _circ_of(_mu_ss)                    # stator-stator(m-independent)
            _Rg1, _Cg1 = np.meshgrid(_gR1, _gR1, indexing="ij")
            _Rg2, _Cg2 = np.meshgrid(_gR2, _gR2, indexing="ij")
            _Rg12, _Cg12 = np.meshgrid(_gR1, _gR2, indexing="ij")

            def _K_gap_macro(m):
                # rotor↔stator block at rotor shift m: phase e^{i·2π·jfreq·m/N}
                _krs = np.fft.ifft(_mu_rs *
                                   np.exp(1j*2*np.pi*_jfreq*int(m)/_Nm)).real[_ii_m]
                # forward block  K[gR1[a],gR2[b]] = _krs[a,b]  and its symmetric
                # transpose K[gR2[b],gR1[a]] = _krs[a,b] (NOT _krs[b,a] — _krs is
                # asymmetric for m≠0, so a literal .T here made the global matrix
                # non-symmetric → garbage solve at non-integer-pole shifts).
                rows = np.concatenate([_Rg1.ravel(), _Rg2.ravel(),
                                       _Rg12.ravel(), _Cg12.ravel()])
                cols = np.concatenate([_Cg1.ravel(), _Cg2.ravel(),
                                       _Cg12.ravel(), _Rg12.ravel()])
                data = np.concatenate([_Krr_blk.ravel(), _Kss_blk.ravel(),
                                       _krs.ravel(), _krs.ravel()])
                return _coo((data, (rows, cols)), shape=(n, n)).tocsr()

            def _T_macro(m, Avec):
                # virtual work: T = −∂(L·w_gap)/∂φ ; only the rotor↔stator term depends
                # on φ.  w_rs(per-unit) = (1/N) Σ μ_rs(j) e^{i·jfreq·φ} conj(Ûr) Ûs;
                # the real torque scales by the stack length _stkM (= L).
                Ur = np.fft.fft(Avec[_gR1]); Us = np.fft.fft(Avec[_gR2])
                ph = np.exp(1j*2*np.pi*_jfreq*int(m)/_Nm)
                return float(-(_stkM/_Nm) * np.sum(
                    (1j*_jfreq) * _mu_rs * ph * np.conj(Ur) * Us).real)

        def _tri_template(P3):
            (x1, y1), (x2, y2), (x3, y3) = P3
            bb = np.array([y2 - y3, y3 - y1, y1 - y2])
            cc = np.array([x3 - x2, x1 - x3, x2 - x1])
            area = 0.5 * abs(cc[2] * bb[1] - cc[1] * bb[2])
            Kl = (np.outer(bb, bb) + np.outer(cc, cc)) / (4.0 * area * MU0)
            cxl = (x1 + x2 + x3) / 3.0; cyl = (y1 + y2 + y3) / 3.0
            rcl = math.hypot(cxl, cyl); cp, sp = cxl / rcl, cyl / rcl
            # B = (∂A/∂y, −∂A/∂x);  Br = u·A,  Bφ = v·A  (template frame —
            # rotationally invariant, so valid for every quad of the ring)
            u = ( cc * cp - bb * sp) / (2.0 * area)
            v = (-cc * sp - bb * cp) / (2.0 * area)
            return Kl, u, v, area, rcl

        def _pol(r_, a_):
            return (r_ * math.cos(a_), r_ * math.sin(a_))
        _Ka, _ua, _va, _ArA, _rcA = _tri_template(
            [_pol(_r1_m, 0.0), _pol(_r2_m, 0.0), _pol(_r2_m, _dphi_b)])
        _Kb, _ub, _vb, _ArB, _rcB = _tri_template(
            [_pol(_r1_m, 0.0), _pol(_r2_m, _dphi_b), _pol(_r1_m, _dphi_b)])
        if _full_ring:
            _kk_b  = np.arange(Nring)               # closed: N quads
            _kk1_b = (np.arange(Nring) + 1) % Nring
        else:
            _kk_b  = np.arange(Nring - 1)           # open sector: N−1 quads
            _kk1_b = _kk_b + 1
        _ones_b = np.ones(len(_kk_b))

        def _band_idx(m):
            if _full_ring:
                j  = (_kk_b + int(m)) % Nring        # periodic, no sign
                j1 = (_kk_b + int(m) + 1) % Nring
                return j.astype(int), j1.astype(int), _ones_b, _ones_b
            j = _kk_b + int(m); j1 = j + 1
            sj = np.ones(Nring - 1); sj1 = np.ones(Nring - 1)
            while np.any(j > Nring - 1):
                w = j > Nring - 1
                j = np.where(w, j - (Nring - 1), j)
                sj = np.where(w, sj * _bc_sign, sj)
            while np.any(j1 > Nring - 1):
                w = j1 > Nring - 1
                j1 = np.where(w, j1 - (Nring - 1), j1)
                sj1 = np.where(w, sj1 * _bc_sign, sj1)
            return j.astype(int), j1.astype(int), sj, sj1

        def _K_band(m):
            j, j1, sj, sj1 = _band_idx(m)
            rows = []; cols = []; data = []
            for Kl, dofs, sgs in (
                (_Ka, (_gR1[_kk_b], _gR2[j],  _gR2[j1]),
                       (_ones_b, sj, sj1)),
                (_Kb, (_gR1[_kk_b], _gR2[j1], _gR1[_kk1_b]),
                       (_ones_b, sj1, _ones_b)),
            ):
                for pq in range(9):
                    pp, qq = divmod(pq, 3)
                    rows.append(dofs[pp]); cols.append(dofs[qq])
                    data.append(Kl[pp, qq] * sgs[pp] * sgs[qq])
            return _coo((np.concatenate(data),
                         (np.concatenate(rows), np.concatenate(cols))),
                        shape=(n, n)).tocsr()

        def _T_band(m, Avec):
            j, j1, sj, sj1 = _band_idx(m)
            Aa = np.vstack([Avec[_gR1[_kk_b]],
                            Avec[_gR2[j]] * sj, Avec[_gR2[j1]] * sj1])
            Ab = np.vstack([Avec[_gR1[_kk_b]],
                            Avec[_gR2[j1]] * sj1, Avec[_gR1[_kk1_b]]])
            s = (_ArA * _rcA * (_ua @ Aa) * (_va @ Aa)
                 + _ArB * _rcB * (_ub @ Ab) * (_vb @ Ab))
            # Arkkio over the STRIP alone — normalise by the strip's radial
            # width (r2−r1).  The strip is the consistently-COUPLED region
            # (rotor ring ↔ stator ring), so its stress is artifact-free; the
            # half-mesh gap fields are sheared (rotor at θ=0 vs coupled stator)
            # and carry a spurious DC torque, so they are NOT used.
            return float(np.sum(s)) * p.stack_length / (MU0 * (_r2_m - _r1_m))

        # Cut pairing is m-INDEPENDENT now (no slip merge) → constant Pro.
        _suf0 = _SignedUF(n)
        for a, b in zip(Mn, Sn):
            _suf0.union(int(b), int(a), _bc_sign)
        _roots0 = [_suf0.find(i) for i in range(n)]
        _rid0 = np.array([r for r, _ in _roots0])
        _rsg0 = np.array([s for _, s in _roots0], float)
        _uniq0, _inv0 = np.unique(_rid0, return_inverse=True)
        Pro_const = _coo((_rsg0, (np.arange(n), _inv0)),
                         shape=(n, _uniq0.size)).tocsr()
        outer_red_const = np.unique(_inv0[outer_nodes])
        log.info("moving band: %d quads (%s), r1=%.3f r2=%.3f mm, Δφ=%.4f°",
                 len(_kk_b), "full ring" if _full_ring else "sector",
                 _r1_m * 1e3, _r2_m * 1e3, spacing)

    # ── Frame loop ───────────────────────────────────────────────────────
    n_total = max(1, int(round(n_steps_per_period * n_periods)))
    # Voltage drive: the currents are STATE (start at 0), so the run has an
    # electrical start-up transient.  Run ONE extra settling period and discard
    # its frames after the loop — every reported series/metric is steady-state.
    # dt = T_elec·n_periods/n_total is invariant under the dual bump.
    _vskip = 0
    _v_nspp = int(round(n_steps_per_period))
    if _vdrive:
        # TEN settling periods with ITERATED Aitken.  The electrical time
        # constant L/R spans many periods on a low-R machine, so a marched DC
        # start-up decays too slowly to shed by brute force.  Instead: the
        # phasor init lands near the orbit, then the period-boundary flux
        # (which converges GEOMETRICALLY) is Δ²-extrapolated to its limit at
        # every 3rd boundary (anchors at periods 3, 6, 9 — each application
        # cuts the residual DC ~3×), and the final settling period runs after
        # the last anchor so the reported window starts on a clean orbit.
        # Settling frames use a REDUCED Picard depth (the DC dynamics only
        # need L roughly right); the last settling period + the reported
        # window run at full depth.
        _v_settle_periods = 10
        _vskip = _v_settle_periods * max(2, _v_nspp)
        n_periods = float(n_periods) + float(_v_settle_periods)
        n_total += _vskip
    period_mech = 360.0 / pole_pairs                      # one electrical period [deg mech]
    T_series = []; psiA = []; psiB = []; psiC = []
    IA = []; IB = []; IC = []; tt = []
    dt = (1.0 / max(f_elec, 1e-9)) * n_periods / n_total
    # Eddy-current (magnetodynamic) coupling: backward-Euler adds (Msig/dt) to the
    # stiffness and (Msig/dt)·A_prev to the RHS.  Msig is 0 outside solid
    # conductors, so air/iron are unaffected.  A_prev follows the material points
    # (rotor mesh rotates rigidly), so it IS the previous-step field per DOF.
    if eddy:
        # Bordered magnetodynamic system (the coupled eddy J-VIEW mode).
        #   stator copper → SOLID bars with imposed current (∫J dA = I_c);
        #   + rotor magnets/shaft σ when rotor_eddy (∫J = 0 per interior
        #     magnet; shaft + cut halves U = 0 by symmetry) so the J view
        #     shows the rotor eddy currents too.
        # The TRANSIENT loss path does NOT use this solve — magnet losses come
        # from smoothed post-processing (see the rotor-eddy stage above).
        from scipy.sparse import bmat as _bmat, diags as _diags
        from scipy.sparse.linalg import spsolve as _spsolve
        _Ms_s = half["s"]["Msig"]
        _Ms_r = half["r"]["Msig"] if rotor_eddy else _csr(half["r"]["Msig"].shape)
        _Minv_dt = _bd([_Ms_s, _Ms_r]).tocsr() * (1.0 / dt)
        A_prev = np.zeros(n)
        _eddy_P = []          # field-based dissipation ∫σ(∂A/∂t)² per frame [W, sector]
        _cons = _coil_con + (_rot_con if rotor_eddy else [])
        _Gfull = _csr(np.column_stack([c["g"] for c in _cons])) if _cons else _csr((n, 0))
        _Sdt   = np.array([c["S"] for c in _cons]) * dt
        _n_coil_con = len(_coil_con)
        _Iunit = np.array([c["Iunit"] for c in _coil_con])
        _phase = [c["phase"] for c in _coil_con]

        def _solve_eddy_constrained(Keff, rhs_field, Pro, outer_red, I_vec, A_prv):
            m = Pro.shape[1]
            KK = (Pro.T @ Keff @ Pro).tocsr()
            rf = np.asarray(Pro.T @ rhs_field).ravel()      # m
            free = np.setdiff1d(np.arange(m), outer_red)
            KKff = KK[np.ix_(free, free)].tocsr()
            if _Gfull.shape[1] == 0:                  # no conductors constrained
                sol = _spsolve(KKff, rf[free])
                A_red = np.zeros(m); A_red[free] = sol
                return Pro @ A_red, np.zeros(0)
            Bred = (Pro.T @ _Gfull).tocsr()                 # m × nc
            Bf = Bred[free, :].tocsr()
            cr = dt * I_vec - np.asarray(_Gfull.T @ A_prv).ravel()   # nc
            Mb = _bmat([[KKff, -Bf], [-Bf.T, _diags(_Sdt)]]).tocsr()
            sol = _spsolve(Mb, np.concatenate([rf[free], cr]))
            A_red = np.zeros(m); A_red[free] = sol[:free.size]
            return Pro @ A_red, sol[free.size:]      # A , per-conductor voltages U_c

    # ── Demagnetisation pre-pass (opt-in) ────────────────────────────────
    # Sweep the rotor over the WHOLE period at full Br, tracking the worst
    # (most negative) demagnetising field H·M̂ at EVERY magnet element.  Any
    # element whose worst H crosses the material BH-curve knee is de-rated
    # along the recoil line (irreversible) → _br_glob.  The measurement loop
    # below then runs with the weakened magnets, so the reported torque /
    # back-EMF / losses carry the demag penalty — Ansys-style, per element.
    _demag_coef = None
    _demag_field = None
    _demag_report = []
    if demag and _mag_idx.size:
        _dm = []
        for _tag, _idx in half["r"]["cells"].items():
            _m = matr0.get(int(_tag))
            if _m is None or (abs(_m.Mx) + abs(_m.My)) <= 0:
                continue
            _bh = getattr(_m, "bh_curve", None)
            if not _bh or len(_bh) < 2:
                continue
            _Mm = math.hypot(_m.Mx, _m.My)
            _knee = _bh[1][0] if _bh[0][1] <= 0 else _bh[0][0]
            _hs = np.array([pt[0] for pt in _bh], float)
            _bs = np.array([pt[1] for pt in _bh], float)
            _o = np.argsort(_hs)
            _dm.append(dict(idx=np.asarray(_idx, int), Mx=_m.Mx, My=_m.My,
                            Mm=_Mm, knee=float(_knee), mu_r=float(_m.mu_r),
                            hs=_hs[_o], bs=_bs[_o], Br0=_Mm * MU0, tag=int(_tag)))
        _Hmin = np.full(_nt_r, np.inf)
        for k in range(n_total):
            if progress_cb is not None:
                try: progress_cb(k, 2 * n_total)
                except Exception: pass
            theta = (k / n_total) * period_mech * n_periods
            m_shift = int(round(theta / spacing))
            Ist = _currents(m_shift * spacing)
            f = np.concatenate([(Ist['A'] * f_coil['A'] + Ist['B'] * f_coil['B']
                                 + Ist['C'] * f_coil['C']), f_mag])
            if _moving:
                Pro = Pro_const
                outer_red = outer_red_const
                _Kband_p = _K_gap_macro(m_shift) if _use_macro else _K_band(m_shift)
            else:
                suf = _SignedUF(n)
                for a, b in zip(Mn, Sn):
                    suf.union(int(b), int(a), _bc_sign)
                for kk in range(Nring):
                    j = kk + m_shift; sg = 1
                    while j > Nring - 1: j -= (Nring - 1); sg *= _bc_sign
                    while j < 0:         j += (Nring - 1); sg *= _bc_sign
                    suf.union(int(rring[kk] + nsn), int(sring[j]), sg)
                roots = [suf.find(i) for i in range(n)]
                rid = np.array([r for r, _ in roots]); rsg = np.array([s for _, s in roots], float)
                uniq, inv = np.unique(rid, return_inverse=True)
                Pro = _coo((rsg, (np.arange(n), inv)), shape=(n, uniq.size)).tocsr()
                outer_red = np.unique(inv[outer_nodes])
                _Kband_p = None
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
                        nf[h["cells"][tag]] = nu_el[hn][tag]
                        K = K + asm(_stiff_nu, _sbi, nu=b0.interpolate(nf))
                    blocks.append(K)
                K = _bd(blocks).tocsr()
                if _Kband_p is not None:
                    K = (K + _Kband_p).tocsr()
                A = Pro @ _sksolve(*condense((Pro.T @ K @ Pro).tocsr(),
                                             Pro.T @ f, D=outer_red))
                for hn, off in (("s", 0), ("r", nsn)):
                    h = half[hn]
                    Bx, By = _per_triangle_B(h["mesh"], A[off:off + h["n"]])
                    Bm = np.sqrt(Bx ** 2 + By ** 2)
                    for tag, curve in sat_bh[hn].items():
                        idx = h["cells"][tag]
                        if idx.size == 0: continue
                        mu_new = _mu_r_from_bh_vec(curve, Bm[idx])
                        nu_new = 1.0 / (MU0 * np.maximum(mu_new, 1.0))
                        nu_el[hn][tag] = 0.5 * nu_el[hn][tag] + 0.5 * nu_new
            _Bxr, _Byr = _per_triangle_B(half["r"]["mesh"], A[nsn:])
            for _d in _dm:
                _ix = _d["idx"]
                _BdotM = _Bxr[_ix] * _d["Mx"] + _Byr[_ix] * _d["My"]
                _H = _BdotM / (MU0 * _d["Mm"]) - _d["Mm"]     # full strength
                _Hmin[_ix] = np.minimum(_Hmin[_ix], _H)
        for _d in _dm:
            _ix = _d["idx"]; _H = _Hmin[_ix]
            _bad = _H < _d["knee"]
            _wi = int(np.argmin(_H)); _hmin = float(_H[_wi])
            _prox = _hmin / _d["knee"] if _d["knee"] < 0 else 0.0
            if np.any(_bad):
                _Bop = np.interp(_H[_bad], _d["hs"], _d["bs"])
                _Brn = _Bop - _d["mu_r"] * MU0 * _H[_bad]
                _ratio = np.clip(_Brn / max(_d["Br0"], 1e-12), 0.0, 1.0)
                _br_glob[_ix[_bad]] = np.minimum(_br_glob[_ix[_bad]], _ratio)
            if _prox > 0.85:
                _demag_report.append({
                    "magnet_index": int(_d["tag"] - DOM_MAG_BASE),
                    "H_min_kA_per_m": round(_hmin * 1e-3, 1),
                    "H_knee_kA_per_m": round(_d["knee"] * 1e-3, 1),
                    "knee_proximity": round(_prox, 2),
                    "demagnetised": bool(_prox > 1.0),
                    "Br_factor": round(float(np.min(_br_glob[_ix])), 3),
                })
        f_mag = _build_fmag(_br_glob)     # weakened magnets for the measurement pass
        _nst = int(Tts.shape[1])
        _demag_coef = np.ones(int(mesh_all.t.shape[1]))
        _demag_coef[_nst:] = _br_glob
        # Field-map payload (full stitched mesh + per-element Br factor) so the
        # UI can render the demagnetisation %-map (% demag = (1 − Br_factor)·100)
        # in the same FemFieldChart 'Demag' mode used by the static field view.
        _pmm = mesh_all.p * 1e3
        _demag_field = {
            "vertices":           _pmm.T.tolist(),
            "triangles":          mesh_all.t.T.astype(int).tolist(),
            "domain_per_tri":     np.concatenate([np.asarray(ts), np.asarray(tr)]).astype(int).tolist(),
            "demag_coef_per_tri": _demag_coef.tolist(),
            "mag_domains":        sorted({int(_d["tag"]) for _d in _dm}),  # which tags are magnets
            "extent": [float(_pmm[0].min()), float(_pmm[0].max()),
                       float(_pmm[1].min()), float(_pmm[1].max())],
        }
        log.warning("demag pre-pass: %d/%d magnet elems de-rated, min Br_factor %.3f",
                    int(np.sum(_br_glob < 0.999)), int(_mag_idx.size), float(_br_glob.min()))

    _field_snap = None       # eddy last-frame field snapshot (if return_field)
    _hist_Am = []            # per-frame A on magnet nodes (loss post-processing)
    _hist_Ash = []           # per-frame A on shaft nodes (field-based shaft loss)
    _hist_A_rotor = []       # per-frame A on ALL rotor nodes (honest coupled eddy)
    # When the demag pre-pass ran, the measurement pass is the SECOND half of
    # the work — continue the progress counter so the UI bar doesn't reset.
    _prog_off = n_total if (demag and _mag_idx.size) else 0
    _prog_tot = 2 * n_total if (demag and _mag_idx.size) else n_total

    # Per-phase flux linkage of a solution — used INSIDE the voltage-drive
    # circuit iteration and for the ψ series (single implementation).
    sc_psi = p.stack_length * NS / float(n_parallel)

    def _psi_of(Avec):
        As_ = Avec[:nsn]
        A_tri_ = (As_[Tts[0]] + As_[Tts[1]] + As_[Tts[2]]) / 3.0
        pa_ = pb_ = pc_ = 0.0
        for idx_, ar_, dir_, ph_ in coil_info:
            sa_ = float(np.sum(ar_))
            if sa_ <= 0:
                continue
            val_ = dir_ * float(np.sum(A_tri_[idx_] * ar_)) / sa_
            if ph_ == 'A':
                pa_ += val_
            elif ph_ == 'B':
                pb_ += val_
            else:
                pc_ += val_
        return pa_, pb_, pc_

    # Voltage-drive circuit state: the phase currents are UNKNOWNS solved with
    # the field (strong field↔circuit coupling — see the Picard loop).  Warm-
    # start from the previous frame + the previous-step flux for backward-Euler.
    from scipy.sparse.linalg import splu as _splu
    _iv_state = {'A': 0.0, 'B': 0.0, 'C': 0.0}
    _psi_prev = None
    _th_eff_prev = None     # previous frame's SNAPPED rotor angle (rotor-time dt)
    _dt_k = dt              # per-frame rotor-time step (uniform when nodes align)
    _v_diag = {"iters": [], "resid": []}   # circuit convergence stats per frame
    _v_bpsi = []            # period-boundary flux samples (psiA, psiB) for Aitken
    _v_aitken_done = False

    if _vdrive:
        # ── Phasor steady-state initialiser ──────────────────────────────────
        # The electrical time constant tau = L/R spans ~20 electrical periods on
        # a low-R machine, so a marched start-up would need ~100 periods to shed
        # its DC transient — impractical.  Instead measure the PM flux + the dq
        # inductances once at theta=0 and place the current DIRECTLY on the
        # periodic orbit (i(0), psi(-dt)); the march then only has to develop the
        # small saturation/slotting harmonics on top of a DC-free fundamental.
        import math as _m
        _pp = pole_pairs
        # m_shift = 0 periodicity setup
        if _moving:
            _Pro0, _out0 = Pro_const, outer_red_const
            _Kb0 = _K_gap_macro(0) if _use_macro else _K_band(0)
        else:
            _suf = _SignedUF(n)
            for _a, _b in zip(Mn, Sn):
                _suf.union(int(_b), int(_a), _bc_sign)
            for _kk in range(Nring):
                _suf.union(int(rring[_kk] + nsn), int(sring[_kk]), 1)
            _rt = [_suf.find(_ii) for _ii in range(n)]
            _rid = np.array([_r for _r, _ in _rt]); _rsg = np.array([_s for _, _s in _rt], float)
            _uq, _iv = np.unique(_rid, return_inverse=True)
            _Pro0 = _coo((_rsg, (np.arange(n), _iv)), shape=(n, _uq.size)).tocsr()
            _out0 = np.unique(_iv[outer_nodes]); _Kb0 = None
        _Pmag0 = np.concatenate([np.zeros(nsn), f_mag])
        _Pa0 = np.concatenate([f_coil['A'] - f_coil['C'], np.zeros(half["r"]["n"])])
        _Pb0 = np.concatenate([f_coil['B'] - f_coil['C'], np.zeros(half["r"]["n"])])
        for _hn in ("s", "r"):
            for _tg in sb_sat[_hn]:
                nu_el[_hn][_tg][:] = 1.0 / (MU0 * max(mu0[_hn].get(_tg, 1.0), 1.0))

        def _assemble0():
            _bl = []
            for _hn in ("s", "r"):
                _h = half[_hn]; _K = K_const[_hn].copy()
                for _tg, _sbi in sb_sat[_hn].items():
                    _b0 = b0_sat[_hn][_tg]; _nf = _b0.zeros()
                    _nf[_h["cells"][_tg]] = nu_el[_hn][_tg]
                    _K = _K + asm(_stiff_nu, _sbi, nu=_b0.interpolate(_nf))
                _bl.append(_K)
            _K = _bd(_bl).tocsr()
            if _Kb0 is not None:
                _K = (_K + _Kb0).tocsr()
            return _K

        def _fac0(_K):
            _Kg = (_Pro0.T @ _K @ _Pro0).tocsr()
            _mk = np.ones(_Kg.shape[0], bool); _mk[_out0] = False
            _fr = np.flatnonzero(_mk)
            _lu = _splu(_Kg[_fr][:, _fr].tocsc())
            _N = _Kg.shape[0]

            def _bs(_ff):
                _r = (_Pro0.T @ _ff)[_fr]
                _x = np.zeros(_N); _x[_fr] = _lu.solve(_r)
                return _Pro0 @ _x
            return _bs

        _the0 = _m.radians(0.0 * _pp + DAXIS_SHIFT_DEG)  # theta_eff=0 electrical

        def _park(_xa_, _xb_, _xc_, _th):
            return ((2.0 / 3.0) * (_xa_ * _m.cos(_th) + _xb_ * _m.cos(_th - 2.094395102393195)
                                   + _xc_ * _m.cos(_th + 2.094395102393195)),
                    -(2.0 / 3.0) * (_xa_ * _m.sin(_th) + _xb_ * _m.sin(_th - 2.094395102393195)
                                    + _xc_ * _m.sin(_th + 2.094395102393195)))

        def _ipark(_d, _q, _th):
            return (_d * _m.cos(_th) - _q * _m.sin(_th),
                    _d * _m.cos(_th - 2.094395102393195) - _q * _m.sin(_th - 2.094395102393195),
                    _d * _m.cos(_th + 2.094395102393195) - _q * _m.sin(_th + 2.094395102393195))

        _w = 2.0 * _m.pi * f_elec
        _V0 = _voltages(0.0)
        # Coupled phasor Picard: solve the dq STEADY-STATE circuit and the
        # saturated field TOGETHER at theta=0, so the inductances used for the
        # operating point are measured AT that operating point (Lq changes ~5x
        # between i=0 and full load -> an i=0 estimate leaves a large DC).
        _id0 = _iq0 = 0.0; _thal = _the0; _align = 0.0
        _psi_pm_d = 0.0; _Ldd = _Lqq = _Ldq = _Lqd = 1e-6
        for _it in range(nonlinear_iterations):
            _K0 = _assemble0(); _bs = _fac0(_K0)
            _A0 = _bs(_Pmag0); _xa = _bs(_Pa0); _xb = _bs(_Pb0)
            _pm = _psi_of(_A0); _qa = _psi_of(_xa); _qb = _psi_of(_xb)
            _ppmA, _ppmB, _ppmC = _pm[0] * sc_psi, _pm[1] * sc_psi, _pm[2] * sc_psi
            # align d on the PM flux, measure the dq inductances there
            _pd0, _pq0 = _park(_ppmA, _ppmB, _ppmC, _the0)
            _align = _m.atan2(_pq0, _pd0); _thal = _the0 + _align
            _psi_pm_d = _m.hypot(_pd0, _pq0)
            _Laa, _Lba = _qa[0] * sc_psi, _qa[1] * sc_psi
            _Lab, _Lbb = _qb[0] * sc_psi, _qb[1] * sc_psi
            _idA, _idB, _idC = _ipark(1.0, 0.0, _thal)
            _iqA, _iqB, _iqC = _ipark(0.0, 1.0, _thal)
            _Ldd, _Lqd = _park(_Laa * _idA + _Lab * _idB, _Lba * _idA + _Lbb * _idB,
                               -((_Laa + _Lba) * _idA + (_Lab + _Lbb) * _idB), _thal)
            _Ldq, _Lqq = _park(_Laa * _iqA + _Lab * _iqB, _Lba * _iqA + _Lbb * _iqB,
                               -((_Laa + _Lba) * _iqA + (_Lab + _Lbb) * _iqB), _thal)
            # dq steady state: V_d = R i_d - w psi_q ; V_q = R i_q + w psi_d
            _Vd, _Vq = _park(_V0['A'], _V0['B'], _V0['C'], _thal)
            _M = np.array([[R_phase - _w * _Lqd, -_w * _Lqq],
                           [_w * _Ldd, R_phase + _w * _Ldq]])
            try:
                _idq = np.linalg.solve(_M, np.array([_Vd, _Vq - _w * _psi_pm_d]))
            except np.linalg.LinAlgError:
                _idq = np.array([0.0, 0.0])
            _id0, _iq0 = float(_idq[0]), float(_idq[1])
            # operating-point field: A = A_pm + iA*xa + iB*xb (i_C folded in),
            # then update the iron saturation from it for the next iterate.
            _iA0, _iB0, _iC0 = _ipark(_id0, _iq0, _thal)
            _Aop = _A0 + _iA0 * _xa + _iB0 * _xb
            for _hn, _off in (("s", 0), ("r", nsn)):
                _h = half[_hn]
                _Bx, _By = _per_triangle_B(_h["mesh"], _Aop[_off:_off + _h["n"]])
                _Bm = np.sqrt(_Bx ** 2 + _By ** 2)
                for _tg, _cv in sat_bh[_hn].items():
                    _ix = _h["cells"][_tg]
                    if _ix.size == 0:
                        continue
                    _mn = _mu_r_from_bh_vec(_cv, _Bm[_ix])
                    nu_el[_hn][_tg] = 0.5 * nu_el[_hn][_tg] + 0.5 / (MU0 * np.maximum(_mn, 1.0))
        _iA0, _iB0, _iC0 = _ipark(_id0, _iq0, _thal)
        _iv_state = {'A': _iA0, 'B': _iB0, 'C': _iC0}
        # psi at t=-dt on the orbit: dq are constant in steady state, so the
        # previous-step flux is just the SAME dq vector mapped one FRAME back —
        # i.e. the park angle rotated by the per-frame electrical step w*dt (NOT
        # one slip-node; a frame spans many slip nodes).  Getting this wrong
        # injects a spurious rotational EMF at frame 0 -> a decaying DC current.
        _psd = _psi_pm_d + _Ldd * _id0 + _Ldq * _iq0
        _psq = _Lqd * _id0 + _Lqq * _iq0
        _thal_m1 = _thal - _w * dt
        _psi_prev = dict(zip(('A', 'B', 'C'), _ipark(_psd, _psq, _thal_m1)))
        log.info("vdrive phasor init: Ld=%.4g Lq=%.4g H |psi_pm|=%.4g Wb "
                 "i_dq=(%.1f, %.1f) A i0=(%.1f, %.1f, %.1f)",
                 _Ldd, _Lqq, _psi_pm_d, _id0, _iq0, _iA0, _iB0, _iC0)

    for k in range(n_total):
        if progress_cb is not None:
            try:
                progress_cb(_prog_off + k, _prog_tot)
            except Exception:
                pass
        theta = (k / n_total) * period_mech * n_periods
        m_shift = int(round(theta / spacing))
        theta_eff = m_shift * spacing
        # Voltage drive uses a Crank–Nicolson circuit: (ψ_k − ψ_{k−1})/dt is the
        # EXACT centred derivative at the mid-step time, so V must be sampled
        # there too and R split between i_k and i_{k−1} — this removes the
        # backward-Euler phase lag (ωΔt/2, 15°el at 12 steps/period) that
        # otherwise skews the whole operating point when |V| ≈ |E|.
        #
        # CRITICAL: the field only exists at SNAPPED slip-node angles θ_eff, so
        # the circuit must live in "rotor time": Δt_k = Δθ_eff/ω with V sampled
        # at the midpoint of the ACTUAL motion.  Dividing the snapped-rotor Δψ
        # by the UNIFORM dt instead modulates dψ/dt by the node-quantisation
        # sawtooth (±33 % at 48 steps vs 72 nodes/period — fake volts ≫ |V−E|)
        # and Crank–Nicolson rings undamped at Nyquist → monster harmonic
        # currents (THD_I ~110 % observed).  Rotor-time stepping removes the
        # artifact exactly; over a period Σ Δt_k = the nominal period.
        _dth_frame = period_mech * n_periods / n_total     # mech deg per frame
        if _vdrive:
            if _th_eff_prev is None:            # very first frame: nominal step
                _th_eff_prev = theta_eff - _dth_frame
            _dth_eff = theta_eff - _th_eff_prev
            _dt_k = dt * (_dth_eff / _dth_frame) if _dth_eff > 1e-12 else dt
            _Vt = _voltages(0.5 * (theta_eff + _th_eff_prev))
            _th_eff_prev = theta_eff
        else:
            _Vt = None
        _iv_prev = dict(_iv_state) if _vdrive else None    # i_{k−1} for the R/2 term
        if not _vdrive:
            Ist = _currents(theta_eff)
        else:
            Ist = dict(_iv_state)   # warm start (only seeds the saturation Picard)
        if _moving:
            # Moving band: the rotor<->stator coupling is the closed-form strip
            # stiffness K_band(m) (added to K below); the only node-pairing
            # constraints left are the m-INDEPENDENT sector cuts -> constant Pro.
            Pro = Pro_const
            outer_red = outer_red_const
            _Kband_f = _K_gap_macro(m_shift) if _use_macro else _K_band(m_shift)
        else:
            # legacy: signed union-find merges ring nodes (slip) + cut pairs.
            suf = _SignedUF(n)
            for a, b in zip(Mn, Sn):
                suf.union(int(b), int(a), _bc_sign)
            for kk in range(Nring):
                j = kk + m_shift; sg = 1
                while j > Nring - 1: j -= (Nring - 1); sg *= _bc_sign
                while j < 0:         j += (Nring - 1); sg *= _bc_sign
                suf.union(int(rring[kk] + nsn), int(sring[j]), sg)
            roots = [suf.find(i) for i in range(n)]
            rid = np.array([r for r, _ in roots]); rsg = np.array([s for _, s in roots], float)
            uniq, inv = np.unique(rid, return_inverse=True)
            Pro = _coo((rsg, (np.arange(n), inv)), shape=(n, uniq.size)).tocsr()
            outer_red = np.unique(inv[outer_nodes])
            _Kband_f = None
        # Source vectors.  Current drive: one fixed load vector f (Ist known).
        # Voltage drive: the winding "unit-current" columns so the field can be
        # written A = A_pm + i_A*xa + i_B*xb with i_C = -i_A-i_B (coil C folded
        # in), and the currents solved from the circuit.
        _n_rot = half["r"]["n"]
        if not _vdrive:
            f_cur_s = (Ist['A'] * f_coil['A'] + Ist['B'] * f_coil['B']
                       + Ist['C'] * f_coil['C'])
            f = np.concatenate([f_cur_s, f_mag])
        else:
            _Pmag = np.concatenate([np.zeros(nsn), f_mag])
            _Pa = np.concatenate([f_coil['A'] - f_coil['C'], np.zeros(_n_rot)])
            _Pb = np.concatenate([f_coil['B'] - f_coil['C'], np.zeros(_n_rot)])
        # Reset the per-element iron reluctivity to the unsaturated base each
        # frame so the saturation solution is a pure function of rotor position
        # (no history dependence) -> the torque ripple is strictly PERIODIC.
        # NB: warm-starting nu_el was tried and REVERTED -- the damped Picard
        # doesn't converge tightly enough for start-independence, so warm-start
        # left each frame at a slightly different convergence level and INCREASED
        # the ripple.  The per-frame reset is load-bearing (torque_ripple_root_cause).
        for hn in ("s", "r"):
            for tag in sb_sat[hn]:
                nu_el[hn][tag][:] = 1.0 / (MU0 * max(mu0[hn].get(tag, 1.0), 1.0))
        A = np.zeros(n)
        # Voltage drive changes the current every Picard step, so the saturation
        # state moves more than at fixed current -> a few extra iterations.
        # SETTLING frames (all but the last discarded period) only need the DC
        # trajectory roughly right -> a shallow Picard is ~3× cheaper; the last
        # settling period + the whole reported window run at full depth.
        if _vdrive and _vskip and k < (_vskip - _v_nspp):
            _n_pic = max(6, nonlinear_iterations // 2)
        else:
            _n_pic = nonlinear_iterations + (6 if _vdrive else 0)
        for it in range(_n_pic):
            blocks = []
            for hn in ("s", "r"):
                h = half[hn]; K = K_const[hn].copy()
                for tag, _sbi in sb_sat[hn].items():
                    b0 = b0_sat[hn][tag]; nf = b0.zeros()
                    nf[h["cells"][tag]] = nu_el[hn][tag]   # P0 dof = global elem id
                    K = K + asm(_stiff_nu, _sbi, nu=b0.interpolate(nf))
                blocks.append(K)
            K = _bd(blocks).tocsr()
            if _Kband_f is not None:
                K = (K + _Kband_f).tocsr()   # moving-band strip coupling
            if eddy:
                Keff = (K + _Minv_dt).tocsr()
                # Solid-bar coils: current imposed via the integral J=I constraint,
                # NOT a source -- the RHS carries magnets + eddy history.
                rhs_field = (np.concatenate([np.zeros(nsn), f_mag])
                             + _Minv_dt @ A_prev)
                I_vec = np.concatenate([
                    np.array([Ist[ph] for ph in _phase]) * _Iunit,
                    np.zeros(len(_cons) - _n_coil_con)])
                A, U_cons = _solve_eddy_constrained(Keff, rhs_field, Pro,
                                                    outer_red, I_vec, A_prev)
            elif _vdrive:
                # ---- STRONG field + circuit coupling ------------------------
                # The phase currents are unknowns solved WITH the field on the
                # EXACT inductance of the current saturation state:
                #   A = A_pm + i_A*xa + i_B*xb   (i_C = -i_A - i_B), where
                #     A_pm = K^-1 * f_mag                 (PM flux, i = 0)
                #     xa   = K^-1 * (coilA - coilC),  xb = K^-1 * (coilB - coilC)
                # so K*A reproduces the imposed winding source exactly.  The
                # per-phase flux linkages give the PM flux + the 2x2 inductance
                # L; the circuit (backward Euler)
                #   (R*I2 + L/dt) * i = V - (psi_pm - psi_prev)/dt
                # is solved directly for i.  No outer iteration, no frozen
                # Jacobian -- L is re-measured EVERY Picard step, so saturation
                # and the circuit converge together.  Factor K once, reuse the
                # LU for all three back-solves.
                Kg = (Pro.T @ K @ Pro).tocsr()
                _msk = np.ones(Kg.shape[0], bool); _msk[outer_red] = False
                _free = np.flatnonzero(_msk)
                _lu = _splu(Kg[_free][:, _free].tocsc())

                def _bsolve(_ffull, _lu=_lu, _free=_free, _Ncol=Kg.shape[0]):
                    _fr = (Pro.T @ _ffull)[_free]
                    _xr = np.zeros(_Ncol); _xr[_free] = _lu.solve(_fr)
                    return Pro @ _xr

                A_pm = _bsolve(_Pmag); xa = _bsolve(_Pa); xb = _bsolve(_Pb)
                _pm = _psi_of(A_pm); _qa = _psi_of(xa); _qb = _psi_of(xb)
                _psi_pmA = _pm[0] * sc_psi; _psi_pmB = _pm[1] * sc_psi
                _psi_pmC = _pm[2] * sc_psi
                _Laa = _qa[0] * sc_psi; _Lba = _qa[1] * sc_psi; _Lca = _qa[2] * sc_psi
                _Lab = _qb[0] * sc_psi; _Lbb = _qb[1] * sc_psi; _Lcb = _qb[2] * sc_psi
                if _psi_prev is None:   # first (discarded settling) frame bootstrap
                    _psi_prev = {'A': _psi_pmA, 'B': _psi_pmB, 'C': _psi_pmC}
                # Crank–Nicolson circuit at the mid-step time of the ACTUAL
                # rotor motion (Δt_k = Δθ_eff/ω — see the frame-top comment),
                # in the LINE-TO-LINE (floating-neutral wye) formulation:
                #   V_AB = R·(Δi_k + Δi_{k−1})/2 + (Δψ_k − Δψ_{k−1})/Δt_k  (Δ = A−B)
                #   V_BC likewise (B−C), with i_C = −i_A − i_B.
                # A real FOC inverter drives an ISOLATED-neutral machine: the
                # zero-sequence back-EMF (triplen harmonics — large on this
                # concentrated winding) falls on the floating neutral and drives
                # NO current.  Applying phase voltages to the phase equations
                # directly pins the machine's neutral to the source's and shorts
                # that zero-sequence EMF through the tiny zero-seq inductance →
                # monster fake triplen currents (measured h3 ≈ 43 % of I₁,
                # THD_I ≈ 110 %).  Line-to-line differences kill the zero
                # sequence exactly — as the physical isolated neutral does.
                _dpmA = _psi_pmA - _psi_prev['A']
                _dpmB = _psi_pmB - _psi_prev['B']
                _dpmC = _psi_pmC - _psi_prev['C']
                _Mc = np.array([
                    [0.5 * R_phase + (_Laa - _Lba) / _dt_k,
                     -0.5 * R_phase + (_Lab - _Lbb) / _dt_k],
                    [0.5 * R_phase + (_Lba - _Lca) / _dt_k,
                     1.0 * R_phase + (_Lbb - _Lcb) / _dt_k]])
                _bc = np.array([
                    (_Vt['A'] - _Vt['B']) - (_dpmA - _dpmB) / _dt_k
                    - 0.5 * R_phase * (_iv_prev['A'] - _iv_prev['B']),
                    (_Vt['B'] - _Vt['C']) - (_dpmB - _dpmC) / _dt_k
                    - 0.5 * R_phase * (_iv_prev['B'] - _iv_prev['C'])])
                _iab = np.linalg.solve(_Mc, _bc)
                _iA = float(_iab[0]); _iB = float(_iab[1])
                Ist = {'A': _iA, 'B': _iB, 'C': -_iA - _iB}
                A = A_pm + _iA * xa + _iB * xb
            else:
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
                    # mu(|B|) from the B-H curve.  Damped (Picard) update of the
                    # element-wise nu field used by the saturable-tag assembly.
                    mu_new = _mu_r_from_bh_vec(curve, Bm[idx])
                    nu_new = 1.0 / (MU0 * np.maximum(mu_new, 1.0))
                    nu_el[hn][tag] = 0.5 * nu_el[hn][tag] + 0.5 * nu_new
        # per-phase flux linkage of the converged solution (also used below).
        pa, pb, pc = _psi_of(A)
        if _vdrive:
            # circuit residual on the CONVERGED solution (health check: ~0 when
            # the coupled Picard converged).
            _psiA_c = pa * sc_psi; _psiB_c = pb * sc_psi; _psiC_c = pc * sc_psi
            # line-to-line residuals (the phase-A equation alone is legitimately
            # nonzero by the zero-sequence EMF the floating neutral absorbs)
            _resA = ((_Vt['A'] - _Vt['B'])
                     - 0.5 * R_phase * (Ist['A'] - Ist['B']
                                        + _iv_prev['A'] - _iv_prev['B'])
                     - ((_psiA_c - _psiB_c)
                        - (_psi_prev['A'] - _psi_prev['B'])) / _dt_k)
            _resB = ((_Vt['B'] - _Vt['C'])
                     - 0.5 * R_phase * (Ist['B'] - Ist['C']
                                        + _iv_prev['B'] - _iv_prev['C'])
                     - ((_psiB_c - _psiC_c)
                        - (_psi_prev['B'] - _psi_prev['C'])) / _dt_k)
            _v_diag["iters"].append(int(_n_pic))
            _v_diag["resid"].append(float(max(abs(_resA), abs(_resB))))
            _psi_prev = {'A': _psiA_c, 'B': _psiB_c, 'C': pc * sc_psi}
            _iv_state = dict(Ist)
            # ITERATED Aitken DC-mode removal: the period-boundary flux converges
            # geometrically toward the steady orbit; sample it at each period
            # end and Δ²-extrapolate the limit whenever 3 fresh samples exist
            # since the last anchor (anchors at periods 3, 6, 9 within the
            # settling window — each application cuts the residual DC ~3×).
            # Samples must share the cycle phase (period spacing) so the
            # periodic flux content cancels exactly in the differences.
            if _v_nspp > 0 and ((k + 1) % _v_nspp == 0) and (k + 1) < _vskip:
                _v_bpsi.append((_psiA_c, _psiB_c))
                if len(_v_bpsi) >= 3:
                    _p0, _p1, _p2 = _v_bpsi[-3:]
                    _new = {}
                    for _ci, _ky in enumerate(('A', 'B')):
                        _x0, _x1, _x2 = _p0[_ci], _p1[_ci], _p2[_ci]
                        _d1 = _x1 - _x0; _d2 = _x2 - _x1; _dd = _d2 - _d1
                        _new[_ky] = (_x2 - _d2 * _d2 / _dd) if abs(_dd) > 1e-15 else _x2
                    _new['C'] = -(_new['A'] + _new['B'])
                    _corr = max(abs(_new['A'] - _psiA_c), abs(_new['B'] - _psiB_c))
                    _drift = max(abs(_p2[0] - _p1[0]), abs(_p2[1] - _p1[1]))
                    # Guarded anchor: once the boundary drift is below noise the
                    # Δ² quotient divides noise by noise and the "correction"
                    # EXPLODES — skip when already converged (drift < 0.05 % of
                    # the PM flux) or when the extrapolation is unstable
                    # (|corr| ≫ drift).  Better to keep marching than to kick a
                    # converged orbit right before the reported window.
                    _flux_scale = max(abs(_psiA_c), abs(_psiB_c), 1e-6)
                    if _drift < 5e-4 * _flux_scale or _corr > 5.0 * _drift:
                        log.info("vdrive Aitken anchor SKIPPED at period %d "
                                 "(drift %.3g, corr %.3g Wb — converged/unstable)",
                                 (k + 1) // _v_nspp, _drift, _corr)
                        _v_bpsi.clear()
                    else:
                        _psi_prev = _new
                        _v_bpsi.clear()   # fresh samples only after the re-anchor
                        log.info("vdrive Aitken anchor at period %d: psiA %.4g -> "
                                 "%.4g (|corr| %.3g Wb)", (k + 1) // _v_nspp,
                                 _psiA_c, _new['A'], _corr)
        if eddy:
            # Joule loss = ∫σ J²/σ² = ∫σ(−∂A/∂t + U_c)² over the conductors.
            # The conductor voltage U_c cancels the large inductive −∂A/∂t,
            # leaving the real dissipation.  Constrained conductors get their
            # solved U_c; U=0 conductors (shaft, cut magnet halves) are pure
            # J = −σ∂A/∂t by symmetry.
            Uvec = np.zeros(n)
            for _ci, _c in enumerate(_cons):
                Uvec[_c["nodes"]] = U_cons[_ci]
            Ffld = -(A - A_prev) / dt + Uvec
            _eddy_P.append(float(Ffld @ (_Minv_dt @ Ffld)) * dt)   # ∫σ F² [W, sector]
            A_prev = A.copy()        # previous-step field for the σ·∂A/∂t term
        if rotor_eddy and _magnode_glob.size:
            # Magnet-node A history → smoothed post-processed eddy loss below.
            _hist_Am.append(A[_magnode_glob].copy())
        if rotor_eddy and _shaftnode_glob.size:
            # Shaft-node A history → field-based (rotor-frame) shaft eddy loss.
            _hist_Ash.append(A[_shaftnode_glob].copy())
        if honest_eddy or rotor_eddy:
            # ALL rotor-node A history → coupled (reaction-included) rotor eddy.
            # With rotor_eddy this is now the PRODUCTION magnet/shaft loss (the
            # history-based post-process is jitter-dominated for screened bodies
            # — see the honest-swap block below); honest_eddy alone keeps the
            # old diagnostic behaviour.  ~N·n_rotor_nodes·8 B ≈ 10-20 MB.
            _hist_A_rotor.append(A[nsn:nsn + half["r"]["n"]].copy())
        # capture the converged per-element B for the loss integrals
        _Bxs, _Bys = _per_triangle_B(half["s"]["mesh"], A[:nsn])
        _Bxr, _Byr = _per_triangle_B(half["r"]["mesh"], A[nsn:])
        if return_field and ((field_first and k == 0)
                             or (not field_first and k == n_total - 1)):
            # Field snapshot for the viewer: A_z, per-element B, and a current
            # density J.  eddy=True → the EDDY density J = σ(−∂A/∂t + U_c) on the
            # solid conductors (the genuinely-new field the eddy solve produces).
            # eddy=False (magnetostatic field view, run at 1 step + rotor_angle0)
            # → the per-element SOURCE current density so the J view still shows
            # the applied winding currents at this rotor position.
            _sig_node = np.zeros(n)
            if eddy:
                for _c in _coil_con:
                    _sig_node[_c["nodes"]] = _sig_cu_T
            for _nds, _sg in _rot_sig_nodes:
                _sig_node[_nds] = _sg
            _Jnodal = _sig_node * (Ffld if eddy else 0.0)
            _field_snap = {
                "P_mm": (mesh_all.p * 1e3).copy(),                 # node coords [mm]
                "T":    mesh_all.t.copy(),                         # triangles (3,nel)
                "A":    A.copy(),                                  # nodal A_z [Wb/m]
                "Bx":   np.concatenate([_Bxs, _Bxr]),              # per-elem B [T]
                "By":   np.concatenate([_Bys, _Byr]),
                "Jeddy": _Jnodal,                          # nodal eddy J [A/m^2]
                "tags": np.concatenate([np.asarray(ts), np.asarray(tr)]).astype(int),
                "nsn":  int(nsn),
            }
            if not eddy:
                # per-element source current density J_z = dir·I_phase·n_wires/area
                # (coil elements live in the stator half = first block of the mesh)
                _Js = np.zeros(_Bxs.size + _Bxr.size)
                for _ix, _ar, _dir, _ph in coil_info:
                    _Js[_ix] = _dir * Ist[_ph] * n_wires / max(slot_area_m2, 1e-12)
                _field_snap["Jtri_src"] = _Js
        _hist_sx.append(_Bxs[_iron_s_idx]); _hist_sy.append(_Bys[_iron_s_idx])
        _hist_rx.append(_Bxr[_iron_r_idx]); _hist_ry.append(_Byr[_iron_r_idx])
        _hist_mx.append(_Bxr[_mag_idx]);    _hist_my.append(_Byr[_mag_idx])
        if _coil_idx.size:
            _hist_cx.append(_Bxs[_coil_idx]); _hist_cy.append(_Bys[_coil_idx])
        if _shaft_idx.size:
            _hist_shx.append(_Bxr[_shaft_idx]); _hist_shy.append(_Byr[_shaft_idx])
        _mshift_hist.append(m_shift)
        # torque: sector → Arkkio over the whole gap.  Moving band → Arkkio over
        # the coupled STRIP only (the half-mesh gap fields are sheared and carry
        # a spurious DC torque; the strip is the consistent rotor↔stator join).
        if _moving:
            Tq_sec = _T_macro(m_shift, A) if _use_macro else _T_band(m_shift, A)
        else:
            Tq_sec = _arkkio_torque(mesh_all, A, p.r_rotor_out, p.r_stator_in,
                                    p.stack_length)
        Tq = Tq_sec * NS
        if _TORQUE_DIAG["on"]:
            _mr = 0.5 * (p.r_rotor_out + p.r_stator_in)
            _gw = (p.r_stator_in - p.r_rotor_out)
            _ak = lambda a, b: _arkkio_torque(mesh_all, A, a, b, p.stack_length) * NS
            _TORQUE_DIAG["full"].append(Tq)                                 # reported torque (strip for moving)
            _TORQUE_DIAG.setdefault("arkkio_full", []).append(_ak(p.r_rotor_out, p.r_stator_in))  # sheared half-mesh Arkkio
            _TORQUE_DIAG.setdefault("tband", []).append(_T_band(m_shift, A) * NS if _moving else 0.0)  # strip Arkkio
            _TORQUE_DIAG["rotor"].append(_ak(p.r_rotor_out, _mr))          # whole rotor half
            _TORQUE_DIAG["stator"].append(_ak(_mr, p.r_stator_in))         # whole stator half
            _TORQUE_DIAG["iface"].append(_ak(_mr - 0.25 * _gw, _mr + 0.25 * _gw))  # straddles slip ring
            _TORQUE_DIAG["rinner"].append(_ak(p.r_rotor_out, p.r_rotor_out + 0.3 * _gw))   # rotor surface, far from ring
            _TORQUE_DIAG["router"].append(_ak(p.r_stator_in - 0.3 * _gw, p.r_stator_in))   # stator surface, far from ring
            # angular profile of the torque integrand over the gap band
            _Bx, _By = _per_triangle_B(mesh_all, A)
            _Pq = mesh_all.p; _Tq2 = mesh_all.t
            _cx = (_Pq[0, _Tq2[0]] + _Pq[0, _Tq2[1]] + _Pq[0, _Tq2[2]]) / 3.0
            _cy = (_Pq[1, _Tq2[0]] + _Pq[1, _Tq2[1]] + _Pq[1, _Tq2[2]]) / 3.0
            _rc = np.hypot(_cx, _cy)
            _msk = (_rc >= p.r_rotor_out) & (_rc <= p.r_stator_in)
            _ar = _triangle_areas(mesh_all)
            _cp = _cx / _rc; _sp = _cy / _rc
            _Brq = _Bx * _cp + _By * _sp
            _Bpq = -_Bx * _sp + _By * _cp
            _itg = (_ar * _rc * _Brq * _Bpq) * (p.stack_length / (MU0 * (p.r_stator_in - p.r_rotor_out))) * NS
            _phi = np.degrees(np.arctan2(_cy, _cx)) % 360.0
            _nst_tris = Tts.shape[1]
            _is_rot = np.arange(_Tq2.shape[1]) >= _nst_tris
            _phi_phys = np.where(_is_rot, (_phi + theta_eff) % 360.0, _phi)   # theta_eff is in degrees
            if _TORQUE_DIAG.get("capture_A") is not None:
                _TORQUE_DIAG["capture_A"].append(
                    dict(A=A.copy(), m=int(m_shift), Mn=Mn.copy(), Sn=Sn.copy(),
                         nsn=int(nsn)))
            _nb = int(_TORQUE_DIAG["ang_bins"])
            _prof = np.zeros(_nb)
            _sec = 360.0 / NS
            # wrap rotated rotor elements past the sector edge back into the
            # sector (Br·Bφ is invariant under the anti-periodic map)
            _bi = np.clip(((_phi_phys[_msk] % _sec) / _sec * _nb).astype(int), 0, _nb - 1)
            np.add.at(_prof, _bi, _itg[_msk])
            _TORQUE_DIAG["ang_prof"].append(_prof)
        # flux linkage: pa/pb/pc were computed by _psi_of(A) inside the frame
        # solve (single implementation — the voltage drive needs them in-loop).
        T_series.append(float(Tq))
        psiA.append(pa * sc_psi); psiB.append(pb * sc_psi); psiC.append(pc * sc_psi)
        IA.append(Ist['A']); IB.append(Ist['B']); IC.append(Ist['C'])
        tt.append(k * dt)

    # ── Voltage drive: drop the settling period — every series below is
    #    steady-state.  n_total/n_periods return to the REQUESTED window so all
    #    per-period post-processing (spectra, band-limit, summaries) is unchanged.
    if _vdrive and _vskip:
        n_total -= _vskip
        n_periods = float(n_periods) - float(_v_settle_periods)
        _slice_lists = [T_series, psiA, psiB, psiC, IA, IB, IC, tt, _mshift_hist,
                        _hist_sx, _hist_sy, _hist_rx, _hist_ry, _hist_mx, _hist_my,
                        _hist_cx, _hist_cy, _hist_shx, _hist_shy,
                        _hist_Am, _hist_Ash, _hist_A_rotor]
        try:
            _slice_lists.append(_eddy_P)   # exists only when eddy=True
        except NameError:
            pass
        for _lst in _slice_lists:
            if len(_lst) > _vskip:
                del _lst[:_vskip]
        _t0_new = tt[0] if tt else 0.0
        tt[:] = [_t - _t0_new for _t in tt]
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

    def _angle_ddt_2d(X, quasi_period_rad=None, pre=None, post=None):
        """Smoothed dX/dt on the unique slip-node grid, mapped back to frames.

        Raw node-to-node derivatives amplify slip-merge jitter with the step
        count, so X(θ) is savgol-low-passed before differentiating.  Two
        defects were found by a 1-period vs 2-period ground-truth comparison
        and are handled here:
        (1) ROTOR-frame histories (magnets, rotor iron) are NOT periodic over
            the window — the stator slotting sweeps past at the stator-
            structure period (2 slot pitches: alternating wide/narrow teeth),
            a non-integer count per electrical period — so a forced periodic
            wrap put a LEVEL JUMP at the seam that the smoother bent into the
            neighbours: P_mag humped ~2.5× on the first/last frames.  Fixed by
            a C0 wrap-detrend (see below) — exact for the derivative, no
            assumption about the signal's true period, no-op when already
            periodic (stator-frame histories).
        (2) the savgol window was set in SAMPLES (U//8), so the physical
            smoothing width depended on the run length — a 2-period run
            smoothed the genuine 15° slot ripple away (P_mag halved on
            identical physics).  Fixed: the width is a constant ANGLE.
        When `pre`/`post` are given they are EXACT samples just before/after
        the window (from the pole-shift symmetry — see the magnet-history pad
        block); the derivative then uses real data at both ends and needs no
        wrap assumption at all.
        (quasi_period_rad accepted for compatibility; unused.)
        """
        N = X.shape[0]
        if N < 3:
            return np.zeros_like(X)
        uniq, first = np.unique(_m_arr, return_index=True)   # sorted unique m
        if uniq.size < 3:
            return (np.roll(X, -1, 0) - np.roll(X, 1, 0)) / (2 * dt)
        Bu = X[first]                                        # (U, E)
        theta_u = uniq * _spacing_rad                        # (U,)
        U = uniq.size
        _W = math.radians(period_mech * n_periods)           # window span
        if float(theta_u[-1]) >= _W - 1e-9:
            # degenerate: last node is the periodic image of the first — drop it
            uniq = uniq[:-1]; Bu = Bu[:-1]; theta_u = theta_u[:-1]; U -= 1
        # savgol at a FIXED PHYSICAL width (≈1/3 slot pitch).  Two past bugs:
        # (a) a window set in SAMPLES (the old U//8) made the width depend on
        #     the run length — a 2-period run smoothed the genuine 15° slot
        #     ripple away (P_mag halved on identical physics);
        # (b) the angle→samples conversion divided by the slip-NODE spacing
        #     (_spacing_rad) instead of the SAMPLE spacing, so the effective
        #     width was 5°×(nodes advanced per step): 60° at 12 steps, 30° at
        #     24, 10° at 72 — the filter NARROWED as the step count grew, so
        #     the "converged" rotor eddy loss GREW with steps (450 mm shaft:
        #     0.8→12.7→25.6 kW at 12/24/72) instead of converging.  Fixed:
        #     divide by the actual theta_u sample spacing.
        _w_ang = math.radians(5.0)                            # smoothing width
        _samp_rad = (float(np.median(np.diff(theta_u))) if U >= 2
                     else _spacing_rad)
        w = int(round(_w_ang / max(_samp_rad, 1e-12)))
        w = max(5, w | 1)                                     # odd, ≥5
        _exact = (pre is not None and post is not None
                  and U == N and np.array_equal(uniq, _m_arr))
        if _exact:
            # EXACT pads: real samples beyond both window ends — no wrap, no
            # detrend; the seam simply does not exist.
            _need = w // 2 + 2
            pre_u = pre[-min(_need, pre.shape[0]):]
            post_u = post[:min(_need, post.shape[0])]
            thL = theta_u[0] - _spacing_rad * np.arange(pre_u.shape[0], 0, -1)
            thR = theta_u[-1] + _spacing_rad * np.arange(1, post_u.shape[0] + 1)
            th_ext = np.concatenate([thL, theta_u, thR])
            Bu_ext = np.concatenate([pre_u, Bu, post_u], axis=0)
            i0 = pre_u.shape[0]
            _c = np.zeros((1, Bu.shape[1]))
        else:
            # C0 detrend: per column, remove the linear ramp that makes the two
            # window ends MEET, so the periodic extension has no level jump at
            # the seam; the ramp's constant slope is added back to the
            # derivative exactly.  (A harmonic-regression replacement was tried
            # and rejected: the slot-structure lines sit ~0.86 cycles apart —
            # under the Rayleigh limit of a 1-period window — so the design
            # matrix is near-singular and the fit explodes.)
            _span = float(theta_u[-1] - theta_u[0])
            if _span > 1e-12:
                _c = (Bu[-1] - Bu[0])[None, :] / _span       # (1, E) dB/dθ
                Bu = Bu - _c * (theta_u - theta_u[0])[:, None]
            else:
                _c = np.zeros((1, Bu.shape[1]))
            th_ext = np.concatenate([theta_u - _W, theta_u, theta_u + _W])
            Bu_ext = np.concatenate([Bu, Bu, Bu], axis=0)
            i0 = U
        if U >= 7 and Bu_ext.shape[0] >= w:
            from scipy.signal import savgol_filter as _sg
            Bu_ext = _sg(Bu_ext, w, 3, axis=0, mode="interp")
        dBdt_u = ((np.gradient(Bu_ext, th_ext, axis=0)[i0:i0 + U] + _c)
                  * _omega_mech)
        pos = np.clip(np.searchsorted(uniq, _m_arr), 0, U - 1)   # frame → unique idx
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

    # ── Band-limit the torque to the physical 6·k electrical orders ──────────
    # The sliding band steps the rotor across DISCRETE slip nodes, injecting
    # broadband torque ripple at orders a balanced 3-φ machine CANNOT produce
    # (1,2,3,4,5,7,…) that does NOT converge with mesh refinement → purely
    # numerical.  Measured at no-load: the real order-6 cogging dominates, but
    # ~41 % of the ripple ENERGY sits in those forbidden orders (raw pk-pk
    # 10.0 → 6·k-only 4.8 N·m).  Keep DC + every 6·k order (real cogging + load
    # ripple) and drop the rest; the mean is preserved exactly.  torque_filter
    # (UI toggle, default ON) switches back to the raw per-frame torque for
    # inspecting the unfiltered solve.
    # Always compute BOTH the raw per-frame torque and the band-limited (6·k)
    # reconstruction, and return both — band-limiting is pure post-processing
    # (FFT → keep DC + 6·k → inverse), so the UI can toggle between them
    # INSTANTLY without a 30 s re-solve.  band_limit_torque preserves the mean
    # exactly, so T_avg is identical for raw and filtered.
    _T_raw = list(T_series)
    _T_filt, Trip_filt, Trip_raw = band_limit_torque(
        T_series, n_steps_per_period, n_periods)
    # T_em_Nm follows the toggle for back-compat (saved sims + server summary);
    # the UI uses the explicit T_em_raw_Nm / T_em_filt_Nm fields below to flip
    # client-side without re-running.
    if torque_filter:
        T_series = list(_T_filt); Trip = Trip_filt
    else:
        T_series = list(_T_raw);  Trip = Trip_raw
    Tavg = float(np.mean(_T_raw)) if _T_raw else Tavg
    Vpk = float(max(max(map(abs, VA)), max(map(abs, VB)), max(map(abs, VC)))) if VA else 0.0
    # P_cu already computed physically (ρ(T)·J²·V·k_end) near the top.

    # ── Torque harmonic spectrum over ONE electrical period ──────────────────
    # The single most telling diagnostic for "is this periodic or chaotic": a
    # clean ripple shows a few DISCRETE peaks (the cogging / 6·k 3-phase orders);
    # broadband noise spreads across all orders.  Orders are multiples of the
    # ELECTRICAL fundamental; amplitude is the single-sided FFT magnitude [N·m].
    # Spectrum is ALWAYS the RAW per-frame torque (not the band-limited series),
    # so the UI shows every order and the user can SEE which bars the 6·k filter
    # keeps (orange) vs drops (the broadband slip-node noise).
    T_harm_order = []; T_harm_amp = []
    if _T_raw:
        _per = max(1, int(round(n_steps_per_period)))
        _Tp = np.asarray(_T_raw[:_per], float)
        if _Tp.size >= 4:
            _F = np.abs(np.fft.rfft(_Tp - _Tp.mean())) / _Tp.size * 2.0
            _nh = min(_F.size - 1, 36)
            T_harm_order = list(range(1, _nh + 1))
            T_harm_amp = [round(float(_F[k]), 4) for k in range(1, _nh + 1)]

    # ── Losses from the captured B(t) — PER-FRAME instantaneous series ────────
    # iron(t)  = hysteresis baseline (per-cycle quantity, flat) + classical
    #            eddy from the smooth |dB/dt|²(t) → ripples as the teeth pass.
    # magnet(t)= σ·d²/12·|dB/dt|²(t)  → ripples likewise.
    def _iron_series(hx, hy, idx, areas_half, mat, qp=None):
        if mat is None or idx.size == 0 or not hx or np.asarray(hx[0]).size == 0:
            return np.zeros(n_total), 0.0
        X = np.asarray(hx); Y = np.asarray(hy)            # (N, E)
        # Maxwell-style coefficients: fitted from the material's MEASURED loss
        # curves when present (relative-error-weighted NNLS over every (f,B)
        # point), falling back to the YAML kh/kc/ke.  Real curves → real loss.
        kh, kc, ke = _mat_lib.effective_bertotti(mat)
        sf = float(getattr(mat, "stacking_factor", 0.95))
        vol = areas_half[idx] * p.stack_length * sf       # (E,)
        dX = _angle_ddt_2d(X, qp); dY = _angle_ddt_2d(Y, qp)
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

    if rotor_eddy and _hist_Am and _mag_groups:
        # FIELD-BASED magnet eddy from the A(t) DISTRIBUTION (post-processed):
        #   J_e = σ(−dA/dt|material + U_m),  U_m = per-magnet area-mean (∫J=0),
        #   U = 0 for sector-cut halves (symmetry).  dA/dt uses the SAME
        # smoothed angle-derivative as the B-field losses (_angle_ddt_2d), so
        # the slip-merge jitter is filtered and the loss CONVERGES with step
        # count — the raw in-loop derivative tripled going 24→72 steps.
        # P(t) = Σ_magnets σ Σ_e (dA/dt_e − U_m)²·area_e × stack × NS.
        _Am = np.asarray(_hist_Am, float)            # (N, n_magnodes)
        # Exact edge pads from the pole-shift symmetry:  A(n, m±M_per) =
        # A(n∓, m), n∓ = the node 2 pole pitches away (see the _pp2 block).
        # Requires the electrical period to be an integer number of slip nodes
        # and frames to map 1:1 onto consecutive nodes — both true for the
        # standard runs; otherwise the C0-detrend edges are used.
        _pads = (None, None)
        _M_per = period_mech / spacing               # slip nodes per elec. period
        if (_pp2 is not None and abs(_M_per - round(_M_per)) < 1e-6):
            _Mp = int(round(_M_per))
            if (_Am.shape[0] >= _Mp and _m_arr.size == _Am.shape[0]
                    and np.array_equal(_m_arr,
                                       np.arange(_m_arr[0], _m_arr[0] + _m_arr.size))):
                _dt2c, _tgF, _sgF, _nrF, _tgB, _sgB, _nrB = _pp2
                _K = min(24, _Mp - 1)

                def _ct_rows(_rows, _tg, _sg, _nr):
                    # C1 (Clough–Tocher) interpolation of each frame's A at the
                    # ±2-pole-pitch image points; nearest-node fallback for
                    # boundary-rounding stragglers outside the hull.
                    from scipy.interpolate import CloughTocher2DInterpolator as _CTI
                    _o = np.empty((_rows.shape[0], _tg.shape[0]))
                    for _i in range(_rows.shape[0]):
                        _w = np.asarray(_CTI(_dt2c, _rows[_i])(_tg), float)
                        _bad = ~np.isfinite(_w)
                        if _bad.any():
                            _w[_bad] = _rows[_i][_nr[_bad]]
                        _o[_i] = _w * _sg
                    return _o
                # post (m = m_max+1 … m_max+K): rows N−M_per … N−M_per+K−1,
                # values at each node's +2-pole-pitch image; pre mirrors it.
                _post = _ct_rows(_Am[_Am.shape[0] - _Mp:_Am.shape[0] - _Mp + _K],
                                 _tgF, _sgF, _nrF)
                _pre = _ct_rows(_Am[_Mp - _K:_Mp], _tgB, _sgB, _nrB)
                _pads = (_pre, _post)
        _dAm = _angle_ddt_2d(_Am, pre=_pads[0], post=_pads[1])   # material dA/dt
        _Pt = np.zeros(n_total)
        for _mg in _mag_groups:
            _dA_e = _dAm[:, _mg["tri"]].mean(axis=1)        # (N, E) elem-mean
            _ar = _mg["areas"]
            if _mg["half"]:
                _F = _dA_e                                   # U = 0 (symmetry)
            else:
                _w = _ar / max(_ar.sum(), 1e-30)
                _F = _dA_e - (_dA_e * _w[None, :]).sum(axis=1, keepdims=True)
            _Pt += _sigma_mag_lib * np.sum(_F ** 2 * _ar[None, :], axis=1)
        _P_mag_t = _declip(_Pt * p.stack_length * NS)
        P_mag_series = _P_mag_t.tolist()
        P_mag_avg = float(np.mean(_P_mag_t))
    elif (_sigma_mag > 0.0 and _mag_idx.size and _hist_mx
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

    # ── AC eddy / proximity losses in the SOLID (non-laminated) conductors ────
    # Same classical slab loss as the magnets, σ·(d²/12)·⟨(dB/dt)²⟩, applied to
    # the COILS (solid copper bars) and the SHAFT (solid steel).  d is the
    # conductor dimension capped at twice the skin depth (for d≫δ the field is
    # surface-limited, so the d² slab law alone would over-count).
    def _slab_eddy(hx, hy, idx, areas_half, sigma, d_m, qp=None):
        if sigma <= 0.0 or idx.size == 0 or not hx or np.asarray(hx[0]).size == 0:
            return [0.0] * n_total, 0.0
        X = np.asarray(hx); Y = np.asarray(hy)
        dX = _angle_ddt_2d(X, qp); dY = _angle_ddt_2d(Y, qp)
        vol = areas_half[idx] * p.stack_length
        Pt = _declip(sigma * (d_m ** 2 / 12.0)
                     * np.sum((dX ** 2 + dY ** 2) * vol[None, :], axis=1) * NS)
        return Pt.tolist(), float(np.mean(Pt))

    def _prox_eddy_split(hx, hy, idx, cen, areas_half, sigma, d_for_Br, d_for_Bt):
        # Proximity loss with the field resolved into RADIAL and TANGENTIAL
        # components, each paired with the conductor dimension PERPENDICULAR to
        # it: B_r ↔ tangential width, B_θ (slot leakage) ↔ radial height.  This
        # avoids the single-d slab over-count (a tall-thin bar barely sees the
        # tangential slot-leakage field).
        if sigma <= 0.0 or idx.size == 0 or not hx or np.asarray(hx[0]).size == 0:
            return [0.0] * n_total, 0.0
        X = np.asarray(hx); Y = np.asarray(hy)                 # (N, E)
        r = np.hypot(cen[0], cen[1]); r = np.where(r < 1e-9, 1e-9, r)
        ux = (cen[0] / r)[None, :]; uy = (cen[1] / r)[None, :]  # r_hat
        Br = X * ux + Y * uy                                   # radial component
        Bt = -X * uy + Y * ux                                  # tangential component
        dBr = _angle_ddt_2d(Br); dBt = _angle_ddt_2d(Bt)
        vol = areas_half[idx] * p.stack_length
        Pt = _declip((sigma / 12.0) * np.sum(
            (d_for_Br ** 2 * dBr ** 2 + d_for_Bt ** 2 * dBt ** 2)
            * vol[None, :], axis=1) * NS)
        return Pt.tolist(), float(np.mean(Pt))

    _omega_e = 2.0 * math.pi * max(1e-6, f_elec)
    # Copper winding bar (SOLID, one strand): proximity loss from the rotating
    # field, split into radial/tangential and each capped at 2·skin-depth.
    _rho_cu   = RHO_CU_20 * (1.0 + ALPHA_CU * (float(coil_temp_c) - 20.0))
    _sigma_cu = 1.0 / _rho_cu
    _delta_cu = math.sqrt(2.0 * _rho_cu / (_omega_e * MU0))
    # wire_split (per Vadim, matches his ANSYS practice): the wide flat bar is
    # wound as N parallel strips across its WIDTH (insulated + transposed), so
    # the width-direction proximity loops see w/N, cutting that loss term ∝N².
    # Assumes ideal transposition (no circulating currents between strips).
    # The 2·δ skin cap still applies on top (whichever is smaller governs).
    _n_wsplit = max(1, int(round(float(geo.get("wire_split", 1) or 1))))
    _w_cu = min(float(geo.get("wire_width",  5.0)) * 1e-3 / _n_wsplit,
                2.0 * _delta_cu)                                             # ↔ B_radial
    _h_cu = min(float(geo.get("wire_height", 0.8)) * 1e-3, 2.0 * _delta_cu)  # ↔ B_tangential
    _sm = half["s"]["mesh"]
    _coil_cen = ((_sm.p[:, _sm.t].mean(axis=1))[:, _coil_idx]
                 if _coil_idx.size else np.zeros((2, 0)))
    P_cu_ac_series, P_cu_ac_avg = _prox_eddy_split(
        _hist_cx, _hist_cy, _coil_idx, _coil_cen, areas_s, _sigma_cu, _w_cu, _h_cu)
    # Shaft eddy (any SOLID conductor, e.g. aluminium).  Computed the SAME geometry-
    # exact field way as the magnets — NO slab/cylinder shape factor, NO skin-depth
    # cap, NO fudge:
    #     P = σ · ∫ (∂A/∂t − ⟨∂A/∂t⟩_body)² dA · L · NS
    # integrated over the REAL shaft element areas, with the area-mean ∂A/∂t removed so
    # the net axial current ∮J dA = 0 over the single connected shaft body.  Because it
    # integrates the actual E = −∂A/∂t over the actual geometry, it reproduces the exact
    # solid-cylinder loss with no shape correction.  The co-rotating shaft sees the
    # magnet field as DC → only the AC slot-ripple / armature reaction dissipates; ∂A/∂t
    # is the slip-jitter-smoothed material derivative (same _angle_ddt_2d as the
    # magnets).  UNIVERSAL: every σ>0 domain (magnet, shaft, …) uses this identical
    # formula; laminated iron (σ=0) contributes nothing here (its loss is in CoreLoss).
    _sigma_shaft = _sigma_shaft_lib
    if (_shaft_group is not None and _hist_Ash
            and np.asarray(_hist_Ash[0]).size and _sigma_shaft > 0.0):
        _Ash  = np.asarray(_hist_Ash, float)                      # (N, n_shaftnodes)
        _dAsh = _angle_ddt_2d(_Ash)                               # material ∂A/∂t = −E
        _dA_e = _dAsh[:, _shaft_group["tri"]].mean(axis=1)        # (N, E) per element
        _ar_sh = _shaft_group["areas"]
        _w_sh  = _ar_sh / max(_ar_sh.sum(), 1e-30)
        _F_sh  = _dA_e - (_dA_e * _w_sh[None, :]).sum(axis=1, keepdims=True)   # ∮J=0
        _Psh_t = _declip(_sigma_shaft
                         * np.sum(_F_sh ** 2 * _ar_sh[None, :], axis=1)
                         * p.stack_length * NS)
        P_shaft_series = _Psh_t.tolist()
        P_shaft_avg    = float(np.mean(_Psh_t))
    else:
        P_shaft_series = [0.0] * n_total
        P_shaft_avg    = 0.0

    # ── HONEST (coupled) rotor eddy — PRODUCTION magnet/shaft loss when rotor_eddy ──
    # Frequency-domain multi-body solve on the REAL rotor mesh (eddy_solver_2d),
    # driven by the captured rotor-node A history: per-harmonic screening + skin
    # reaction are SOLVED, and the fixed harmonic ceiling (k ≤ 16) band-limits the
    # drive to the physical rotor-frame orders.  The history post-process above
    # squares ∂A/∂t, so the slip-band node-identification jitter — which does NOT
    # decay with depth the way physical field harmonics do — dominates SCREENED
    # bodies: the 450 mm shaft (under 50 mm magnet + 5 mm back-iron; physical
    # reach ~e⁻⁹) read 32→45 kW of pure jitter (GROWING with step count) vs a
    # stable 0.5-0.6 kW here.  So with rotor_eddy the honest values REPLACE the
    # history-based magnet/shaft averages: the magnet series keeps its (physical
    # slot-ripple) shape rescaled to the honest mean; the shaft series — whose
    # shape is jitter, not physics — is flattened to the honest mean.
    # honest_eddy alone keeps the old additive-diagnostic behaviour.
    # Fail-safe: any error → the history-based values stand (as before).
    P_mag_honest = P_shaft_honest = 0.0
    P_mag_hist_avg = float(P_mag_avg); P_shaft_hist_avg = float(P_shaft_avg)
    _honest_ok = False
    if (honest_eddy or rotor_eddy) and _hist_A_rotor:
        try:
            from motor_ai_sim.simulation.eddy_solver_2d import honest_rotor_eddy as _hre
            _rm = half["r"]["mesh"]
            _tags_r = np.zeros(_rm.t.shape[1], int)
            for _tg, _els in half["r"]["cells"].items():
                _tags_r[np.asarray(_els, int)] = int(_tg)
            _mag_tags_h = [int(tg) for tg in np.unique(_tags_r) if int(tg) >= DOM_MAG_BASE]

            def _muf(tg):
                tg = int(tg)
                if tg >= DOM_MAG_BASE:                 # magnet (NdFeB recoil ~1.05)
                    return 1.05
                if tg == DOM_ROTOR:                    # rotor back-iron: converged mu_r
                    try:
                        _n = nu_el.get("r", {}).get(DOM_ROTOR)
                        if _n is not None and np.size(_n):
                            return 1.0 / (MU0 * float(np.mean(_n)))
                    except Exception:
                        pass
                    return 1000.0
                return 1.0                             # shaft (Al) / air = non-magnetic
            P_mag_honest, P_shaft_honest, _hfreqs = _hre(
                np.asarray(_rm.p, float), np.asarray(_rm.t, int), _tags_r,
                _muf, _sigma_of_tag, _mag_tags_h, DOM_SHAFT,
                np.asarray(_hist_A_rotor, float), float(n_total) * dt,
                float(p.stack_length), float(NS))
            _honest_ok = True
            log.info("HONEST rotor eddy: mag=%.3f shaft=%.3f W (%d harmonics) | "
                     "resistance-limited mag=%.3f shaft=%.3f W",
                     P_mag_honest, P_shaft_honest, len(_hfreqs), P_mag_avg, P_shaft_avg)
        except Exception as _e:
            log.warning("honest rotor eddy failed (history-based values stand): %s", _e)
            P_mag_honest = P_shaft_honest = 0.0
    if rotor_eddy and _honest_ok:
        _mh = float(P_mag_honest)
        if P_mag_avg > 1e-9:
            _kh = _mh / P_mag_avg
            P_mag_series = [v * _kh for v in P_mag_series]
        else:
            P_mag_series = [_mh] * n_total
        P_mag_avg = _mh
        P_shaft_series = [float(P_shaft_honest)] * n_total
        P_shaft_avg = float(P_shaft_honest)

    # ── AXIAL magnet lamination (magnet_lamination, mm; 0 = solid) ──────────
    # Slicing the magnets ALONG THE STACK (per Vadim: the 450's 180 mm magnets
    # laminated at 10 mm = 18 slices) cannot be meshed in this 2-D model — the
    # J_z formulation assumes infinitely long conductors (loss ∝ w_eff², the
    # in-plane loop width, with free loop closure at z = ±∞).  A finite axial
    # slice of length l forces the loop to close within the slice, adding the
    # return-path resistance; in the resistance-limited regime (NdFeB skin
    # depth ≈ 16 mm @ 1.8 kHz > l = 10 mm) the classical rectangular-plate
    # result rescales the eddy loss by
    #     k_ax = l² / (l² + w_eff²),   w_eff = magnet area / longest extent
    # (limits: l→∞ → 1 = the 2-D value; l ≪ w → (l/w)², loops close axially).
    # Field / torque / mass are untouched — insulated cuts do not change the
    # magnetostatics.  Applied to the PRODUCTION magnet numbers (series + avg +
    # the reported honest value); P_mag_hist_W stays the raw-2D diagnostic.
    # lam=0 keeps k=1 (pure 2-D, back-compat); a real 180 mm solid magnet is
    # itself k(180) ≈ 0.97 — negligible.  Shaft: not affected by this param.
    try:
        _lam_mm = float((geo or {}).get("magnet_lamination", 0.0) or 0.0)
    except Exception:
        _lam_mm = 0.0
    if _lam_mm > 0.1 and P_mag_avg > 0.0:
        try:
            _mp0 = (polys.get("magnets") or [(None, 0)])[0][0]
            _xy0 = list(_mp0.exterior.coords)
            _rr0 = [math.hypot(_x, _y) for _x, _y in _xy0]
            _c0 = _mp0.centroid
            _ca0 = math.atan2(_c0.y, _c0.x)
            _an0 = [((math.atan2(_y, _x) - _ca0 + math.pi) % (2.0 * math.pi)) - math.pi
                    for _x, _y in _xy0]
            _rad0 = max(_rr0) - min(_rr0)
            _tan0 = (max(_an0) - min(_an0)) * 0.5 * (max(_rr0) + min(_rr0))
            _w_eff = _mp0.area / max(max(_rad0, _tan0), 1e-9)
            _l_ax = min(_lam_mm, float(p.stack_length) * 1e3)   # can't exceed the stack
            _k_ax = (_l_ax * _l_ax) / (_l_ax * _l_ax + _w_eff * _w_eff)
            P_mag_series = [v * _k_ax for v in P_mag_series]
            P_mag_avg = float(P_mag_avg) * _k_ax
            P_mag_honest = float(P_mag_honest) * _k_ax
            log.info("magnet AXIAL lamination: l=%.1f mm, w_eff=%.1f mm -> k_ax=%.4f, "
                     "P_mag=%.0f W", _l_ax, _w_eff, _k_ax, P_mag_avg)
        except Exception as _e:
            log.warning("magnet lamination factor skipped: %s", _e)

    # ── Field-based rotor eddy loss from the magnetodynamic solve (Stage 1) ──
    # ∫σ(∂A/∂t)² straight from the eddy field — NO slab/d/cap.  Compare against
    # the slab estimate above.  Skip the first electrical period (eddy warmup).
    P_cu_total_solve_W = 0.0; P_cu_ac_solve_W = 0.0
    if eddy and '_eddy_P' in dir() and len(_eddy_P) > 1:
        _warm = max(1, len(_eddy_P) // 2)
        # _eddy_P entries are ∫σF² dA over the 2-D sector mesh [W per metre of
        # stack] — × stack_length for watts (was missing → reported 22× high,
        # which is why the UI note called this value "inflated").
        P_cu_total_solve_W = float(np.mean(_eddy_P[_warm:]) * NS * p.stack_length)
        P_cu_ac_solve_W = P_cu_total_solve_W - float(P_cu)    # total − DC I²R
        log.info("EDDY-SOLVE copper total=%.1f W (DC=%.0f + AC=%.1f) vs slab DC+AC=%.1f W",
                 P_cu_total_solve_W, float(P_cu), P_cu_ac_solve_W, float(P_cu) + P_cu_ac_avg)

    log.info("SB transient: %d frames, %d slip nodes, P_fe=%.1f P_mag=%.1f "
             "P_cuDC=%.1f P_cuAC=%.1f P_shaft=%.1f, %.1fs",
             n_total, Nring, P_fe_avg, P_mag_avg, float(P_cu), P_cu_ac_avg,
             P_shaft_avg, _t.time() - t0)
    # Copper total = DC I²R (flat) + AC eddy/proximity (rotor-position dependent).
    P_cu_dc = float(P_cu)
    P_cu_series = [P_cu_dc + ac for ac in P_cu_ac_series]
    P_tot_series = [c + f + m + s for c, f, m, s
                    in zip(P_cu_series, P_fe_series, P_mag_series, P_shaft_series)]
    # ── Mechanical/shaft power from GLOBAL energy conservation ───────────────
    # P_elec_in = ⟨Σ v·i⟩ over the period (EXACTLY 0 at no-load, I=0).  Energy
    # balance P_in = P_mech + P_loss gives the physically-correct shaft power
    #     P_mech = P_elec_in − P_loss_total
    # — at no-load this equals −P_loss (the drive overcomes every loss), and it
    # never relies on the numerically-noisy cogging-mean torque (T_avg·ω gave a
    # spurious −620 W at I=0 against 325 W of loss, violating conservation).
    #
    # ⚠ PER-BRANCH → TOTAL:  VA/VB/VC are the PER-BRANCH terminal voltages (ψ was
    # divided by n_parallel to get one branch's linkage — see the ψ scaling and
    # the legacy-path comment) and IA/IB/IC are the PER-BRANCH conductor currents
    # (I_phase ÷ n_parallel, see `_currents`).  So ⟨Σ v·i⟩ is the power of ONE
    # parallel branch per phase.  The phase has n_parallel such branches in
    # parallel (same terminal V, currents add), so the machine's TOTAL electrical
    # input is n_parallel × ⟨Σ v·i⟩.  WITHOUT this factor P_elec_in — and hence
    # the energy-balance shaft power and efficiency — came out ÷n_parallel too
    # small (129 kW / η 92.8 % vs the 557 kW / η 98 % the airgap torque T·ω=563 kW
    # implies at n_parallel=4).  Losses (P_cu via copper_loss_W, iron/magnet from
    # the ×NS field integrals) are already whole-machine totals, so only the ⟨v·i⟩
    # terminal power carried the per-branch scale.
    _omega_m = 2.0 * math.pi * rpm / 60.0
    P_elec_in = (float(np.mean(np.asarray(VA) * np.asarray(IA)
                               + np.asarray(VB) * np.asarray(IB)
                               + np.asarray(VC) * np.asarray(IC)))
                 * float(n_parallel)
                 if IA else 0.0)
    P_loss_total_avg = float(np.mean(P_tot_series)) if P_tot_series else 0.0
    P_airgap_avg = float(Tavg * _omega_m)        # electromagnetic (Arkkio) power
    P_mech_avg = P_elec_in - P_loss_total_avg     # energy-conserving shaft power

    # ── Per-element loss DENSITY (W/m³) for the Ansys-style spatial map ──────
    # Same per-element loss math as the totals above, kept per-element instead
    # of summed, then each component NORMALISED so its volume-integral equals
    # the reported (physically-trusted) component loss — the map both shows the
    # spatial distribution AND integrates back to the sidebar numbers.  Element
    # order matches the field snapshot: [stator-half | rotor-half].
    if _field_snap is not None:
        _nst_e = int(_Bxs.size)
        _dens = np.zeros(int(_Bxs.size + _Bxr.size))

        def _mean_sq_ddt(hx, hy, qp=None):              # time-avg |dB/dt|² per elem
            dX = _angle_ddt_2d(np.asarray(hx), qp)
            dY = _angle_ddt_2d(np.asarray(hy), qp)
            return np.mean(dX ** 2 + dY ** 2, axis=0)

        def _bac2(hx, hy):                              # (½ peak-peak)² per elem
            X = np.asarray(hx); Y = np.asarray(hy)
            return (((X.max(0) - X.min(0)) * 0.5) ** 2
                    + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)

        def _norm_into(local_idx, shape_e, areas_half, base, P_target_W):
            if local_idx.size == 0 or shape_e.size == 0 or P_target_W <= 0:
                return
            integ = float(np.sum(shape_e * areas_half[local_idx])) * p.stack_length * NS
            if integ > 1e-30:
                _dens[base + local_idx] += shape_e * (P_target_W / integ)

        # Iron — stator + rotor share one Bertotti total (P_fe_avg).
        def _iron_shape(hx, hy, idx, mat, qp=None):
            if mat is None or idx.size == 0 or not hx or np.asarray(hx[0]).size == 0:
                return np.zeros(idx.size)
            kh, kc, ke = _mat_lib.effective_bertotti(mat)
            b2 = _bac2(hx, hy)
            return (kh * f_elec * b2
                    + ke * f_elec ** 1.5 * np.power(np.maximum(b2, 0.0), 0.75)
                    + (kc / _two_pi2) * _mean_sq_ddt(hx, hy, qp))
        _sh_is = _iron_shape(_hist_sx, _hist_sy, _iron_s_idx, _steel_s)
        _sh_ir = _iron_shape(_hist_rx, _hist_ry, _iron_r_idx, _steel_r)
        _integ_fe = ((float(np.sum(_sh_is * areas_s[_iron_s_idx])) if _iron_s_idx.size else 0.0)
                     + (float(np.sum(_sh_ir * areas_r[_iron_r_idx])) if _iron_r_idx.size else 0.0)
                     ) * p.stack_length * NS
        if _integ_fe > 1e-30 and P_fe_avg > 0:
            _kfe = P_fe_avg / _integ_fe
            if _iron_s_idx.size: _dens[_iron_s_idx] += _sh_is * _kfe
            if _iron_r_idx.size: _dens[_nst_e + _iron_r_idx] += _sh_ir * _kfe

        # Magnets — slab |dB/dt|² shape, normalised to P_mag_avg.
        if _mag_idx.size and _hist_mx and np.asarray(_hist_mx[0]).size:
            _norm_into(_mag_idx, _mean_sq_ddt(_hist_mx, _hist_my),
                       areas_r, _nst_e, P_mag_avg)

        # Copper — uniform DC ohmic + crowded AC proximity (radial/tangential).
        if _coil_idx.size:
            _vol_cu = float(np.sum(areas_s[_coil_idx])) * p.stack_length * NS
            if _vol_cu > 1e-30 and P_cu_dc > 0:
                _dens[_coil_idx] += P_cu_dc / _vol_cu
            if _hist_cx and np.asarray(_hist_cx[0]).size and P_cu_ac_avg > 0:
                _Xc = np.asarray(_hist_cx); _Yc = np.asarray(_hist_cy)
                _rc = np.hypot(_coil_cen[0], _coil_cen[1])
                _rc = np.where(_rc < 1e-9, 1e-9, _rc)
                _uxc = (_coil_cen[0] / _rc)[None, :]; _uyc = (_coil_cen[1] / _rc)[None, :]
                _dBrc = _angle_ddt_2d(_Xc * _uxc + _Yc * _uyc)
                _dBtc = _angle_ddt_2d(-_Xc * _uyc + _Yc * _uxc)
                _sh_cu = (_sigma_cu / 12.0) * np.mean(
                    _w_cu ** 2 * _dBrc ** 2 + _h_cu ** 2 * _dBtc ** 2, axis=0)
                _norm_into(_coil_idx, _sh_cu, areas_s, 0, P_cu_ac_avg)

        _field_snap["loss_dens"] = _dens.tolist()

    # (The honest coupled rotor-eddy solve moved ABOVE the loss-series assembly —
    # it is now the production magnet/shaft loss when rotor_eddy is on.)

    return {
        "method": "sliding_band",
        # 'field+honest' = magnet/shaft loss from the coupled frequency-domain
        # rotor solve (screening + skin reaction, k≤16 physical band) — the
        # production model with rotor_eddy; 'field' = its history-based σ·∂A/∂t
        # fallback; 'slab' = classical d²/12 estimate.
        "loss_model": ("field+honest" if (rotor_eddy and _honest_ok)
                        else ("field" if rotor_eddy else "slab")),
        "n_steps": n_total, "n_steps_per_period": int(n_steps_per_period),
        "n_periods": float(n_periods), "rpm": rpm, "f_elec_Hz": f_elec,
        "dt_s": dt, "T_period_s": (1.0 / f_elec if f_elec > 1e-9 else 0.0),
        "time_s": tt, "rotor_angle_deg": [
            (k / n_total) * period_mech * n_periods for k in range(n_total)],
        "T_em_Nm": T_series, "T_avg_Nm": Tavg, "T_ripple_pct": Trip,
        "T_ripple_raw_pct": Trip_raw, "T_ripple_filt_pct": Trip_filt,
        # Both reconstructions — the UI toggles between them client-side (no
        # re-solve) when the "Torque filter" checkbox is flipped.
        "T_em_raw_Nm": _T_raw, "T_em_filt_Nm": _T_filt,
        "psi_A_Wb": psiA, "psi_B_Wb": psiB, "psi_C_Wb": psiC,
        "V_A": VA, "V_B": VB, "V_C": VC, "V_peak": Vpk,
        "I_A": IA, "I_B": IB, "I_C": IC,
        "P_cu_W": P_cu_series, "P_fe_W": P_fe_series,
        "P_mag_eddy_W": P_mag_series, "P_loss_total_W": P_tot_series,
        "P_cu_dc_W": P_cu_dc, "P_cu_ac_W": P_cu_ac_series,
        "P_shaft_eddy_W": P_shaft_series,
        "P_mag_honest_W": round(float(P_mag_honest), 3),    # coupled (reaction) eddy — production w/ rotor_eddy
        "P_shaft_honest_W": round(float(P_shaft_honest), 3),
        "P_mag_hist_W": round(float(P_mag_hist_avg), 3),    # pre-swap history-based avgs (diagnostic:
        "P_shaft_hist_W": round(float(P_shaft_hist_avg), 3),  # jitter-dominated for screened bodies)
        "P_cu_ac_solve_W": round(P_cu_ac_solve_W, 1),       # field-based copper AC (eddy solve)
        "P_cu_total_solve_W": round(P_cu_total_solve_W, 1),  # field-based copper total
        "P_mech_avg_W": P_mech_avg,                          # energy-conserving shaft power
        "P_elec_in_W": P_elec_in,                            # ⟨Σ v·i⟩ (0 at no-load)
        "P_airgap_W": P_airgap_avg,                          # electromagnetic T_avg·ω
        "P_loss_total_avg_W": P_loss_total_avg,
        "R_phase_ohm": R_phase, "n_slip_nodes": int(Nring),
        "n_parallel": int(n_parallel),
        "coil_temp_C": float(coil_temp_c),
        "end_winding_factor": float(_k_end_used),
        # Drive mode: "current" (imposed sinusoidal I) or "voltage" (imposed
        # sinusoidal V — the currents above are the machine's own response).
        "drive": ("voltage" if _vdrive else "current"),
        "v_phase_peak_V": float(v_phase_peak) if _vdrive else None,
        "v_delta_deg": float(v_delta_deg) if _vdrive else None,
        # circuit-iteration convergence stats (per frame, incl. settling) + the
        # honest steady-state quality gauge: mean phase current over the
        # REPORTED window (≈0 A on a converged periodic orbit).
        "v_drive_diag": (_v_diag if _vdrive else None),
        "v_dc_residual_A": (round(float(np.mean(np.asarray(IA, float))), 3)
                            if (_vdrive and IA) else None),
        "T_harm_order": T_harm_order, "T_harm_amp": T_harm_amp,
        "field": _field_snap,
        # Demagnetisation (populated only when demag=True): per-element Br
        # factor over the FULL stitched mesh (1.0 = full strength), plus the
        # per-magnet worst-cell report consumed by the UI panel/% map.
        "demag_coef_per_tri": (_demag_coef.tolist() if _demag_coef is not None else None),
        "demag_report": _demag_report,
        "demag_field": _demag_field,     # full mesh + per-element Br factor for the %-map
    }


def _stitch_full_half(polys_half: dict, default_dom: int,
                      build_kwargs: dict):
    """Build a FULL-360° sliding-band HALF (rotor disk or stator annulus) by
    stitching two 180° sector builds — the direct closed-360 OCC build
    double-meshes / mis-classifies (dead field).  Same trick as the static
    full disk: clean n_sectors=2 build → mirror by point negation (exact
    180° rotation) → weld coincident seam nodes → reclassify every triangle
    by centroid against the FULL half polygons.

    Returns (MeshTri, tags int16, classify_fn with .polys = polys_half)."""
    import numpy as _np
    import scipy.sparse as _sp
    from scipy.sparse.csgraph import connected_components as _cc
    from scipy.spatial import cKDTree as _KD
    import shapely as _sh
    from skfem import MeshTri as _MT

    mesh2, _t2, _c2 = build_mesh_from_polygons(
        polys_half, n_sectors=2, **build_kwargs)
    V = mesh2.p.T; T = mesh2.t.T; N = len(V)

    Vf = _np.vstack([V, -V])              # 180° rotation = point negation
    Tf = _np.vstack([T, T + N]); n2 = len(Vf)
    pairs = _KD(Vf).query_pairs(r=1e-7)
    if pairs:
        ij = _np.array(list(pairs)).T
        g = _sp.coo_matrix((_np.ones(ij.shape[1]), (ij[0], ij[1])),
                           shape=(n2, n2))
        _, lab = _cc(g + g.T, directed=False)
    else:
        lab = _np.arange(n2)
    uniq, inv = _np.unique(lab, return_inverse=True)
    Vw = _np.zeros((len(uniq), 2)); _np.add.at(Vw, inv, Vf)
    Vw /= _np.bincount(inv)[:, None]
    Tw = inv[Tf]
    good = ((Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2])
            & (Tw[:, 0] != Tw[:, 2]))
    Tw = Tw[good]
    meshF = _MT(Vw.T, Tw.T.copy())

    cen = Vw[Tw].mean(axis=1) * 1000.0    # metres → polygon mm
    ct = _np.full(len(Tw), default_dom, dtype=_np.int32)
    clf = []
    for i, (mp, _pl) in enumerate(polys_half.get("magnets", []) or []):
        if mp is not None and not mp.is_empty:
            clf.append((mp, DOM_MAG_BASE + i))
    for i, cp in enumerate(polys_half.get("coils", []) or []):
        if cp is not None and not cp.is_empty:
            clf.append((cp, DOM_COIL_BASE + i))
    for k, dm in (("shaft", DOM_SHAFT), ("rotor", DOM_ROTOR),
                  ("stator", DOM_STATOR)):
        gg = polys_half.get(k)
        if gg is not None and not gg.is_empty:
            clf.append((gg, dm))
    for gg, tag in reversed(clf):     # solids last → most specific wins
        try:
            ct[_sh.contains_xy(gg, cen[:, 0], cen[:, 1])] = tag
        except Exception:
            pass

    # STRUCTURED gap: the ε retract pulled the iron OFF the cell arcs for the OCC
    # build, so the thin ε ring (rotor r_ro−ε→r_ro, stator r_si→r_si+ε) is NOT
    # inside the retracted iron polygon → the pass above left it as air, WIDENING
    # the magnetic gap by 2ε and collapsing torque.  Restore its material: a tri
    # in the ε ring is iron where the retracted iron sits DIRECTLY behind it (probe
    # ε inward for the rotor / outward for the stator).
    _sg = polys_half.get("structured_gap_spec")
    if _sg is not None and float(_sg.get("eps", 0.0)) > 0.0:
        _eps = float(_sg["eps"])
        _rr = _np.hypot(cen[:, 0], cen[:, 1]); _th = _np.arctan2(cen[:, 1], cen[:, 0])
        _probe = max(1e-4, 0.4 * _eps)
        _ro_poly = polys_half.get("rotor"); _st_poly = polys_half.get("stator")
        _mags = [mp for mp, _pl in polys_half.get("magnets", []) if mp is not None]
        if str(_sg.get("half")) == "stator":
            # stator ε ring is on the r_hi (= r_si) side
            _rsi = float(_sg["r_hi"])
            if _st_poly is not None:
                _m = (_rr >= _rsi - 1e-6) & (_rr <= _rsi + _eps + 1e-6)
                if _m.any():
                    _px = (_rr[_m] + _probe) * _np.cos(_th[_m])
                    _py = (_rr[_m] + _probe) * _np.sin(_th[_m])
                    _iron = _sh.contains_xy(_st_poly, _px, _py)
                    _idx = _np.where(_m)[0]
                    ct[_idx[_iron]] = DOM_STATOR
        else:
            # rotor ε ring is on the r_lo (= r_ro) side
            _rro = float(_sg["r_lo"])
            if _ro_poly is not None:
                _m = (_rr >= _rro - _eps - 1e-6) & (_rr <= _rro + 1e-6)
                if _m.any():
                    _px = (_rr[_m] - _probe) * _np.cos(_th[_m])
                    _py = (_rr[_m] - _probe) * _np.sin(_th[_m])
                    _iron = _sh.contains_xy(_ro_poly, _px, _py)
                    _idx = _np.where(_m)[0]
                    ct[_idx[_iron]] = DOM_ROTOR
                    for _mi, _mp in enumerate(_mags):
                        _inm = _sh.contains_xy(_mp, _px, _py)
                        ct[_idx[_inm]] = DOM_MAG_BASE + _mi

    class _CF:
        pass
    cf = _CF(); cf.polys = polys_half
    log.info("SB full-ring half stitched: %d nodes, %d tris (default dom %d)",
             len(Vw), len(Tw), default_dom)
    return meshF, ct.astype(_np.int16), cf


def _build_full_disk_from_halves(polys, rotor_angle_deg, mesh_size_mm,
                                 min_size_mm, outer_air_factor, motion_band,
                                 band_thickness_mm, geo_cfg, component_mesh_mm,
                                 normal_deviation_deg=6.0, aspect_ratio=10.0,
                                 gap_layers=3.0):
    """Build a CLEAN full-disk (n_sectors=1) mesh by stitching TWO 1/2 sector
    meshes (the half is meshed cleanly by OCC; the full 360° is NOT).

    Steps: build the clean half (n_sectors=2) → duplicate it rotated 180° →
    weld the coincident seam nodes → reclassify every triangle by its centroid
    against the FULL (un-clipped) polygons.  Result is a manifold (no
    overlapping/double-meshed iron) full disk that the magnetostatics solver
    handles as the genuine 360° motor (no periodic BC).

    Returns (MeshTri, cell_tags int16, classify_fn) — same contract as
    build_mesh_from_polygons.  classify_fn.polys = the full polys so
    build_materials assigns per-magnet/per-coil materials correctly.
    """
    import numpy as _np
    import scipy.sparse as _sp
    from scipy.sparse.csgraph import connected_components as _cc
    from scipy.spatial import cKDTree as _KD
    import shapely as _sh
    from skfem import MeshTri as _MT

    # 1) clean half (n_sectors=2 → OCC meshes the open wedge without overlaps)
    mesh2, _ct2, _cf2 = build_mesh_from_polygons(
        polys, rotor_angle_deg, mesh_size_mm, min_size_mm=min_size_mm,
        normal_deviation_deg=normal_deviation_deg, aspect_ratio=aspect_ratio,
        outer_air_factor=outer_air_factor, motion_band=motion_band,
        band_thickness_mm=band_thickness_mm, gap_layers=gap_layers, n_sectors=2,
        geo_cfg=geo_cfg, component_mesh_mm=component_mesh_mm)
    V = mesh2.p.T; T = mesh2.t.T; N = len(V)

    # 2) stitch: half + 180°-rotated copy, then weld coincident seam nodes
    Vf = _np.vstack([V, -V])              # 180° rotation = (x,y)->(-x,-y)
    Tf = _np.vstack([T, T + N]); n2 = len(Vf)
    pairs = _KD(Vf).query_pairs(r=1e-7)
    if pairs:
        ij = _np.array(list(pairs)).T
        g = _sp.coo_matrix((_np.ones(ij.shape[1]), (ij[0], ij[1])), shape=(n2, n2))
        _, lab = _cc(g + g.T, directed=False)
    else:
        lab = _np.arange(n2)
    uniq, inv = _np.unique(lab, return_inverse=True)
    Vw = _np.zeros((len(uniq), 2)); _np.add.at(Vw, inv, Vf)
    Vw /= _np.bincount(inv)[:, None]
    Tw = inv[Tf]
    good = ((Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2]))
    Tw = Tw[good]
    meshF = _MT(Vw.T, Tw.T.copy())

    # 3) classify each triangle by centroid against the FULL (un-clipped) polys
    cen = Vw[Tw].mean(axis=1) * 1000.0    # mesh metres → polygon mm
    rr = _np.hypot(cen[:, 0], cen[:, 1])
    _gc = geo_cfg or {}
    r_ro = float(_gc.get("rotor_outer_radius", 0.0))
    r_si = float(_gc.get("stator_inner_radius", 0.0))
    ct = _np.full(len(Tw), DOM_AIR, dtype=_np.int32)
    if r_ro > 0.0 and r_si > r_ro:
        ct[(rr >= r_ro) & (rr <= r_si)] = DOM_AIRGAP
    clf = []
    for i, (mp, _pl) in enumerate(polys.get("magnets", [])):
        if mp is not None and not mp.is_empty:
            clf.append((mp, DOM_MAG_BASE + i))
    for i, cp in enumerate(polys.get("coils", [])):
        if cp is not None and not cp.is_empty:
            clf.append((cp, DOM_COIL_BASE + i))
    for k, dm in (("shaft", DOM_SHAFT), ("rotor", DOM_ROTOR), ("stator", DOM_STATOR)):
        gg = polys.get(k)
        if gg is not None and not gg.is_empty:
            clf.append((gg, dm))
    # least-specific first so magnets/coils (front of clf) overwrite last → win
    for gg, tag in reversed(clf):
        try:
            ct[_sh.contains_xy(gg, cen[:, 0], cen[:, 1])] = tag
        except Exception:
            pass

    class _CF:
        pass
    cf = _CF(); cf.polys = polys
    log.info("FEM-sim: stitched full disk from 2 halves — %d nodes, %d tris",
             len(Vw), len(Tw))
    return meshF, ct.astype(_np.int16), cf


def em_transient_eval(
    *,
    n_steps_per_period: int,
    n_periods: float,
    gamma_deg: float,
    I_phase_rms: float,
    mesh_size_mm: float = 4.0,
    min_size_mm: float = 0.3,
    outer_air_factor: float = 1.3,
    gap_layers: float = 3.0,
    n_sectors: int = -1,
    stator_fillet_mm: float = 0.0,
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    rotor_eddy: bool = False,
    demag: bool = False,
    torque_filter: bool = True,
    pole_copy=None,
    component_mesh_mm=None,
    geo_override=None,
    progress_cb=None,
    hi_fidelity: bool = False,
    structured_gap: bool = False,
    drive: str = "current",
    v_phase_peak: float = 0.0,
    v_delta_deg: float = 0.0,
) -> Dict:
    """THE single canonical sliding-band transient invocation.

    Every consumer that needs a 2-D transient solve funnels through here — the
    Simulation route (get_fem_transient), the optimizer (refine_proc.run_one) and
    the solver.em_transient module — so the optimizer's physics can NEVER drift
    from what the Simulation tab shows. Pure: no caching, no global progress, no
    disk-save (those UI concerns stay in the route, which wraps this). Returns the
    raw sliding-band result dict (sbres).
    """
    return fem_transient_sliding_band(
        n_steps_per_period=int(n_steps_per_period), n_periods=float(n_periods),
        gamma_deg=float(gamma_deg), I_phase_rms=float(I_phase_rms),
        mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
        outer_air_factor=float(outer_air_factor), gap_layers=float(gap_layers),
        n_sectors=int(n_sectors) if int(n_sectors) > 1 else -1,
        stator_fillet_mm=float(stator_fillet_mm),
        coil_temp_c=float(coil_temp_c), end_winding_factor=float(end_winding_factor),
        rotor_eddy=bool(rotor_eddy), demag=bool(demag),
        torque_filter=bool(torque_filter), pole_copy=pole_copy,
        component_mesh_mm=(component_mesh_mm or {}), geo_override=geo_override,
        progress_cb=progress_cb, hi_fidelity=bool(hi_fidelity),
        structured_gap=bool(structured_gap),
        drive=str(drive or "current"), v_phase_peak=float(v_phase_peak),
        v_delta_deg=float(v_delta_deg))


def fem_quasistatic_transient(
    n_steps_per_period: int = 24,
    n_periods: float = 1.0,
    gamma_deg: float = 0.0,
    I_phase_rms: float = 85.0,
    mesh_size_mm: float = 4.0,
    min_size_mm: float = 0.3,
    outer_air_factor: float = 1.3,
    motion_band: bool = True,
    band_thickness_mm: float = 0.4,
    n_sectors: int = 4,
    stator_fillet_mm: float = 0.0,
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    component_mesh_mm: dict = None,
) -> dict:
    """GENUINE quasi-static transient — ONE algorithm for every symmetry.

    Sweeps ``fem_solve_for_sim`` over one electrical period at the REQUESTED
    symmetry: Full (n_sectors=1) uses the real stitched 360° disk, 1/2 & 1/4 use
    the clipped sectors with the correct (anti)periodic BC.  NOTHING is forced to
    1/4 — so the full disk shows its own honest result.  Each frame is a real
    magnetostatic solve; back-EMF = R·I + dψ/dt (central differences); per-frame
    losses (Bertotti core + I²R copper + slab magnet eddy) already carry the
    n_sectors multiplier.  Returns the same dict shape the transient endpoint and
    summary builder expect (so the frontend is unchanged).
    """
    import time as _t
    import numpy as _np
    import math as _math
    from motor_ai_sim.simulation.geometry_2d import params_from_config
    from motor_ai_sim.config import get_config

    t0 = _t.time()
    cfg = get_config(); sim = cfg.get("simulation", {}); geo = dict(cfg.get("geometry", {}))
    wind = cfg.get("winding", {})
    p = params_from_config()
    pole_pairs = p.num_poles // 2
    n_parallel = wind.get("n_parallel", 2)
    # rpm is the master; the electrical frequency is DERIVED (see the
    # sliding-band path for why — stale config pairs scaled losses wrong).
    rpm = float(sim.get("rpm", 3950))
    f_elec = rpm * pole_pairs / 60.0
    n_total = max(2, int(round(n_steps_per_period * n_periods)))
    period_mech = 360.0 / pole_pairs                       # one electrical period [deg mech]
    dt = (1.0 / max(f_elec, 1e-9)) * n_periods / n_total
    Ipk = float(I_phase_rms) / n_parallel * _math.sqrt(2)
    # Temperature-consistent phase resistance for the R·I voltage drop.
    _P_cu_dc, _k_end_used, R_phase = copper_loss_W(
        p, geo, float(I_phase_rms), n_parallel,
        coil_temp_c=coil_temp_c, end_winding_factor=end_winding_factor)

    T = []; psiA = []; psiB = []; psiC = []
    Pcu = []; Pfe = []; Pmag = []; IA = []; IB = []; IC = []; tt = []; ang_list = []
    for k in range(n_total):
        ang = (k / n_total) * period_mech * n_periods
        r = fem_solve_for_sim(
            rotor_angle_deg=float(ang), gamma_deg=float(gamma_deg),
            mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
            outer_air_factor=float(outer_air_factor), motion_band=motion_band,
            band_thickness_mm=float(band_thickness_mm), n_sectors=int(n_sectors),
            stator_fillet_mm=float(stator_fillet_mm), I_phase_rms=float(I_phase_rms),
            component_mesh_mm=component_mesh_mm)
        T.append(float(r.get("T_em_Nm", 0.0)))
        psiA.append(float(r.get("psi_A_Wb", 0.0)))
        psiB.append(float(r.get("psi_B_Wb", 0.0)))
        psiC.append(float(r.get("psi_C_Wb", 0.0)))
        Pcu.append(float(r.get("P_cu_W", 0.0)))
        Pfe.append(float(r.get("P_fe_W", 0.0)))
        Pmag.append(float(r.get("P_mag_eddy_W", 0.0)))
        te = _math.radians(ang * pole_pairs + gamma_deg + DAXIS_SHIFT_DEG)
        IA.append(Ipk * _math.cos(te))
        IB.append(Ipk * _math.cos(te - 2 * _math.pi / 3))
        IC.append(Ipk * _math.cos(te + 2 * _math.pi / 3))
        tt.append(k * dt); ang_list.append(ang)
        log.info("QS transient: frame %d/%d ang=%.2f T=%.2f", k + 1, n_total, ang, T[-1])

    # Back-EMF e = dψ/dt via a SPECTRAL derivative (keep the fundamental + a few
    # low harmonics).  Each frame is meshed independently, so ψ(t) carries a tiny
    # frame-to-frame remesh jitter; a raw finite difference amplifies that into a
    # spurious V-peak (and differently per symmetry).  Differentiating the low
    # harmonics of the periodic ψ removes the jitter and gives the genuine,
    # symmetry-consistent back-EMF.  (Same denoising the sliding-band path used.)
    _Kv = max(1, min(6, n_total // 2 - 1))
    def _ddt(arr):
        a = _np.asarray(arr, float); N = a.size
        if N < 4:
            return _np.array([(a[(i + 1) % N] - a[(i - 1) % N]) / (2 * dt) for i in range(N)])
        Fc = _np.fft.rfft(a)
        if _Kv + 1 < Fc.size:
            Fc[_Kv + 1:] = 0.0
        return _np.fft.irfft(Fc * (1j * 2 * _np.pi * _np.fft.rfftfreq(N, d=dt)), n=N)
    VA = [R_phase * i + e for i, e in zip(IA, _ddt(psiA).tolist())]
    VB = [R_phase * i + e for i, e in zip(IB, _ddt(psiB).tolist())]
    VC = [R_phase * i + e for i, e in zip(IC, _ddt(psiC).tolist())]
    Vpk = float(max(max(map(abs, VA)), max(map(abs, VB)), max(map(abs, VC)))) if VA else 0.0
    Ta = _np.asarray(T, float)
    Tavg = float(Ta.mean()) if Ta.size else 0.0
    Tpp = float(Ta.max() - Ta.min()) if Ta.size else 0.0
    Trip = float(100.0 * Tpp / abs(Tavg)) if Tavg else 0.0
    # torque spectrum (orders = × electrical frequency) for the harmonics bar chart
    T_harm_order = []; T_harm_amp = []
    if Ta.size >= 4:
        F = _np.fft.rfft(Ta - Ta.mean())
        scale = 2.0 / Ta.size
        for kk in range(1, len(F)):
            T_harm_order.append(round(kk / max(n_periods, 1e-9), 2))
            T_harm_amp.append(float(abs(F[kk]) * scale))
    Ptot = [c + f + m for c, f, m in zip(Pcu, Pfe, Pmag)]
    Pmech = float(Tavg * 2.0 * _math.pi * rpm / 60.0)
    log.info("QS transient DONE: n=%d sectors=%d T_avg=%.2f ripple=%.1f%% V_peak=%.1f (%.1fs)",
             n_total, int(n_sectors), Tavg, Trip, Vpk, _t.time() - t0)
    return {
        "method": "quasistatic",
        "n_steps": n_total, "n_steps_per_period": int(n_steps_per_period),
        "n_periods": float(n_periods), "rpm": rpm, "f_elec_Hz": f_elec, "dt_s": dt,
        "T_period_s": (1.0 / f_elec if f_elec > 1e-9 else 0.0),
        "time_s": tt, "rotor_angle_deg": ang_list,
        "T_em_Nm": T, "T_avg_Nm": Tavg, "T_ripple_pct": round(Trip, 2),
        "T_ripple_raw_pct": round(Trip, 2),
        "psi_A_Wb": psiA, "psi_B_Wb": psiB, "psi_C_Wb": psiC,
        "V_A": VA, "V_B": VB, "V_C": VC, "V_peak": Vpk,
        "I_A": IA, "I_B": IB, "I_C": IC,
        "P_cu_W": Pcu, "P_fe_W": Pfe, "P_mag_eddy_W": Pmag, "P_loss_total_W": Ptot,
        "P_mech_avg_W": Pmech, "R_phase_ohm": R_phase, "coil_temp_C": float(coil_temp_c),
        "end_winding_factor": float(_k_end_used),
        "T_harm_order": T_harm_order, "T_harm_amp": T_harm_amp, "field": None,
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
    component_mesh_mm: Optional[dict] = None,
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
    # Shared module constant so every solve path uses the SAME phase shift.
    SPOKE_PM_DAXIS_SHIFT_DEG = DAXIS_SHIFT_DEG
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
    if int(n_sectors) == 1:
        # FULL DISK: OCC fragment can't cleanly mesh the closed 360° geometry
        # (it double-meshes the iron).  Build it from two clean 1/2 sector
        # meshes stitched together instead.
        mesh, cell_tags, classify_fn = _build_full_disk_from_halves(
            polys, rotor_angle_deg, mesh_size_mm, min_size_mm, outer_air_factor,
            motion_band, band_thickness_mm, motor.parameters, component_mesh_mm)
    else:
        mesh, cell_tags, classify_fn = build_mesh_from_polygons(
            polys, rotor_angle_deg, mesh_size_mm,
            min_size_mm=min_size_mm,
            outer_air_factor=outer_air_factor,
            motion_band=motion_band,
            band_thickness_mm=band_thickness_mm,
            n_sectors=n_sectors,
            geo_cfg=motor.parameters,
            component_mesh_mm=component_mesh_mm,
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
        # Maxwell-style: coefficients fitted from the material's MEASURED loss
        # curves when present (see materials.fit_bertotti_from_curves), else YAML.
        kh, kc, ke = _mat_lib.effective_bertotti(mat)
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
