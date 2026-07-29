"""Geometry → mesh: everything gmsh/OCC and the sliding-band mesh topology.

Lifted verbatim out of ``fem_solver_2d``, which had grown to 9600 lines with the
mesher and the physics interleaved. Nothing here knows about materials, fields
or torque — it turns polygons into a tagged ``MeshTri`` plus the slip-ring node
pairing the transient needs, and stops there.

The split is by dependency, not by taste: this module imports only
``sb_domains``, so the solver depends on the mesher and never the reverse. Every
public name is re-exported from ``fem_solver_2d`` so existing imports
(``routes.simulation``, ``modules.mesh``, ``geo_mesh``) keep working unchanged.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

from motor_ai_sim.simulation.sb_domains import (
    DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_COIL, DOM_COIL_BASE, DOM_MAG_BASE,
    DOM_MAG_N, DOM_MAG_S, DOM_OUTER, DOM_ROTOR, DOM_SHAFT, DOM_STATOR,
    _GMSH_LOCK, _N_SLIP, _SB_BAND_DELTA_FRAC, _SB_BELT, _SB_GEO_MESH,
    _SB_GEO_SECTOR, _SB_IRON_RESAMPLE, _SB_IRON_TEMPLATE, _SB_POLE_COPY_ROTOR,
    _SB_POLE_COPY_STATOR, _SB_ROT_PERIODICITY, _SB_STRUCTURED_GAP,
    _SB_STRUCTURED_STRIPS, _SG_EPS_OVERRIDE, _SG_M_TARGET,
)

log = logging.getLogger(__name__)

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
            # Uniform-chord resampling of the iron's gap surfaces (experimental,
            # SB_IRON_RESAMPLE=1): rotor OD + stator bore arcs re-sampled on the
            # slip grid, mouth/pocket corners preserved exactly.  Re-collect
            # rotor_solids afterwards so the band construction below uses the
            # resampled iron.
            _belt_mode = _SB_BELT and (bool(structured_gap) or _SB_STRUCTURED_GAP)
            if (_SB_IRON_RESAMPLE or _belt_mode) and _gap_est > 0.05:
                # (forced in belt mode: the belt welds by node IDENTITY, so the
                # iron boundary must sit exactly on the slip grid)
                if out.get("rotor") is not None:
                    out["rotor"] = _resample_ring_arcs(out["rotor"], _r_ro_est, _N)
                if out.get("stator") is not None:
                    out["stator"] = _resample_ring_arcs(out["stator"], _r_si_est, _N)
                rotor_solids = []
                if out.get("rotor") is not None: rotor_solids.append(out["rotor"])
                if out.get("shaft") is not None: rotor_solids.append(out["shaft"])
                rotor_solids += [m for m, _p in out["magnets"] if m is not None]
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
                    # spec for the geo/template iron meshers in MOVING mode too:
                    # carries the band radii so the halves can end on the uniform
                    # R1/R2 rings the harmonic macroelement couples analytically.
                    out["structured_gap_spec"] = {
                        "r_ro": float(_r_ro_est), "mid": float(mid_r),
                        "r_si": float(_r_si_est),
                        "K": max(1, int(round(float(gap_layers)))),
                        "n_slip": int(_N), "eps": 0.0,
                        "r1": float(_r1), "r2": float(_r2),
                    }
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
                    out["structured_gap_spec"] = {
                        "r_ro": float(_r_ro_est), "mid": float(mid_r),
                        "r_si": float(_r_si_est),
                        "K": max(1, int(round(float(gap_layers)))),
                        "n_slip": int(_N), "eps": 0.0,
                        "r1": float(_r1), "r2": float(_r2),
                    }
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
                    # BELT welds by node identity: the ring-subtraction vertices
                    # (exact slip-grid points) MUST survive.  10 µm simplify
                    # collapses the 0.4 µm-sagitta slip arcs into ~1.4° chords
                    # (measured — the route-A pocket-hole story); 0.1 µm still
                    # kills true boolean duplicates but keeps every grid vertex.
                    in_band = in_band.simplify(
                        (1e-4 if _belt_mode else 0.01), preserve_topology=True)
                    if _belt_mode:
                        # ring subtraction can leave PINCHED (self-touching)
                        # polygons — split them or the OCC loop fails
                        in_band = _split_geom_pinches(in_band)
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
                    out_band = out_band.simplify(
                        (1e-4 if _belt_mode else 0.01), preserve_topology=True)
                    if _belt_mode:
                        out_band = _split_geom_pinches(out_band)
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


def _check_part_mesh_supported(user_cm: dict, use_geo: bool) -> None:
    """Raise if a REQUESTED per-part element size cannot be applied on the
    template/geo build path.

    The raise is caught by the caller's `except` around the template build and
    drops the half-build to the gmsh path, which DOES honour every component
    key (one Constant size field per component surface group).  So a per-part
    request is always either applied by the geo mesher or handed to a mesher
    that applies it — it is NEVER silently ignored."""
    if not user_cm:
        return
    if not use_geo:
        raise ValueError(
            "tensor-template iron has one global density and cannot honour "
            f"per-part element sizes {sorted(user_cm)}")
    from motor_ai_sim.simulation.geo_mesh import GEO_PART_KEYS
    _bad = sorted(set(user_cm) - set(GEO_PART_KEYS))
    if _bad:
        raise ValueError("geometry-driven mesh cannot honour per-part element "
                         f"size for {_bad}")


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
        iron_template: Optional[bool] = None,  # deterministic template iron; None=env default
        geo_mesh: Optional[bool] = None,    # geometry-driven CDT mesh; None=env default
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

    # What the USER actually asked for, captured BEFORE the auto-fill below —
    # the template/geo path must know the difference between "the caller
    # requested magnet = 0.15 mm" and "we defaulted magnet to the global size"
    # (the auto-fill would otherwise make EVERY run look like a per-part request).
    _user_cm = {}
    for _k, _v in dict(component_mesh_mm or {}).items():
        try:
            _fv = float(_v)
        except (TypeError, ValueError):
            continue
        if _fv > 0.0:
            _user_cm[str(_k).lower()] = _fv

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
    _extra_tf = list(polys.get("transfinite_ring_radii_mm") or [])
    # BELT: pin the gap-facing boundary circles (rotor OD r_ro, stator bore
    # r_si) as transfinite rings — every boundary Line keeps EXACTLY its two
    # endpoint vertices as mesh nodes.  Without this gmsh re-samples the
    # boundary at its own density and the belt's node-identity weld finds
    # nothing to weld to (measured: nodes at arbitrary angles, r off by the
    # chord sagitta).  Same machinery that keeps the mid slip ring exact.
    if _SB_BELT:
        _bsp = polys.get("structured_gap_spec")
        if _bsp:
            _extra_tf = _extra_tf + [float(_bsp["r_ro"]), float(_bsp["r_si"])]

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

    _use_tpl = _SB_IRON_TEMPLATE if iron_template is None else bool(iron_template)
    _use_geo = _SB_GEO_MESH if geo_mesh is None else bool(geo_mesh)
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
        if _use_tpl:
            try:
                from motor_ai_sim.simulation.iron_template import template_solver_halves
                from motor_ai_sim.cadquery_geometry import CadQueryMotor as _CQM
                # geometry params for the template/geo meshers: the CALLER's
                # geometry (geo_cfg — carries any geo_override), NOT a fresh
                # CadQueryMotor() = the GLOBAL config.  A preset/candidate motor
                # meshed with the config's radii built a CHIMERA (40 mm polys +
                # 200 mm shaft/bore rings → magnetically disconnected halves,
                # back-EMF ≡ 0, torque ~0 — the '40/100 mm weak flux' mystery).
                _p_geo = dict(geo_cfg) if geo_cfg else _CQM().parameters
                _sgspec = polys.get("structured_gap_spec") or {}
                if not _sgspec:
                    # template halves end ON the iron circles — without the
                    # structured-gap belt NOTHING meshes the air gap (a bare
                    # black ring in the viewer, a broken solve).  Fall back.
                    raise ValueError("template iron needs the structured-gap "
                                     "belt (Air-gap mesh: Structured rings)")
                if _sgspec:            # iron circles must sit EXACTLY on the
                    _p_geo = dict(_p_geo)   # belt-spec radii (4 um off = no weld)
                    _p_geo["stator_inner_radius"] = float(_sgspec["r_si"])
                    _p_geo["rotor_outer_radius"] = float(_sgspec["r_ro"])
                _density = max(0.3, 2.0 / max(mesh_size_mm, 0.5))
                _check_part_mesh_supported(_user_cm, _use_geo)
                if _use_geo:
                    from motor_ai_sim.simulation.geo_mesh import geo_mesh_halves
                    _cm = component_mesh_mm or {}
                    # UI "Per-part element size → Outer air" uses key "outer";
                    # geo applies it as ONE coarse size for all air + shaft.
                    _air_mm = float(_cm.get("outer") or _cm.get("air") or 0.0)
                    (mesh_s, tags_s, classify_s,
                     mesh_r, tags_r, classify_r) = geo_mesh_halves(
                        _p_geo, polys, outer_air_factor=outer_air_factor,
                        density=_density, n_sectors=1,
                        mesh_edge_mm=float(mesh_size_mm),
                        n_slip=int(_sgspec.get("n_slip", 1008)),
                        r_si=float(_sgspec.get("r_si", 0.0)),
                        r_ro=float(_sgspec.get("r_ro", 0.0)),
                        air_mesh_mm=_air_mm,
                        # per-part element sizes the USER asked for (solid
                        # parts → their own CDT region seed); the auto-filled
                        # iron/magnet defaults are NOT passed, so an empty
                        # request reproduces the previous mesh bit-for-bit.
                        part_mesh_mm=_user_cm,
                        # MOVING-band spec (harmonic macro): halves end on the
                        # uniform R1/R2 rings; merged spec has no r1/r2 → 0.
                        r1_band=float(_sgspec.get("r1", 0.0)),
                        r2_band=float(_sgspec.get("r2", 0.0)))
                    log.info("geo-driven halves: stator %d tris, rotor %d tris",
                             mesh_s.t.shape[1], mesh_r.t.shape[1])
                elif "r1" in _sgspec:
                    # tensor template halves end ON the iron circles and know
                    # nothing about the moving-band R1/R2 rings → the macro's
                    # ring extraction would find nothing.  gmsh meshes the
                    # in_band/out_band polys up to R1/R2 correctly — fall back.
                    raise ValueError("template iron does not support the "
                                     "moving-band macro (use geo mesh)")
                else:
                    (mesh_s, tags_s, classify_s,
                     mesh_r, tags_r, classify_r) = template_solver_halves(
                        _p_geo, polys, outer_air_factor=outer_air_factor,
                        density=_density)
                    log.info("iron template halves: stator %d tris, rotor %d tris",
                             mesh_s.t.shape[1], mesh_r.t.shape[1])
            except Exception as _te:
                log.warning("iron template failed (%s) — gmsh build", _te)
                mesh_s = tags_s = classify_s = None
                mesh_r = tags_r = classify_r = None
        if mesh_s is None and _pc_stator and _slot_period and _slot_period > 0:
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
        if mesh_r is None and _pc_rotor and _pole_period and _pole_period > 0:
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
        # BELT: weld each half's numpy-built gap annulus in (node identity —
        # boundaries were resampled onto the slip grid).  BEFORE the rotor
        # rotation: the rotor's belt slice rotates rigidly with it.
        if _SB_BELT:
            _bs = polys_s.get("structured_gap_spec")
            _br = polys_r.get("structured_gap_spec")
            # a MOVING-band spec (has r1/r2) carries no belt cells — the gap is
            # coupled analytically between the R1/R2 rings, nothing to weld.
            if _bs and "r1" not in _bs:
                mesh_s, tags_s = _weld_belt_into_half(mesh_s, tags_s, _bs, "stator", 1)
            if _br and "r1" not in _br:
                mesh_r, tags_r = _weld_belt_into_half(mesh_r, tags_r, _br, "rotor", 1)
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
    mesh_r = tags_r = classify_r = None
    if _use_tpl:
        # deterministic template wedge: whole units only (radial cuts land on
        # tooth axes / inter-pole axes with clone-identical node sets), so the
        # sector master-slave pairing matches nodes 1:1 by radius.
        try:
            from motor_ai_sim.simulation.iron_template import template_solver_halves
            from motor_ai_sim.cadquery_geometry import CadQueryMotor as _CQM
            # caller's geometry (geo_override-aware), NOT the global config —
            # see the full-ring branch above (chimera-mesh bug).
            _p_geo = dict(geo_cfg) if geo_cfg else _CQM().parameters
            _ns_i = max(1, int(n_sectors))
            if (int(_p_geo["num_slots"]) // 2) % _ns_i or int(_p_geo["num_poles"]) % _ns_i:
                raise ValueError(f"sector {_ns_i} not unit-aligned "
                                 f"({_p_geo['num_slots']}s/{_p_geo['num_poles']}p)")
            _sgspec = polys.get("structured_gap_spec") or {}
            if not _sgspec:
                raise ValueError("template iron needs the structured-gap "
                                 "belt (Air-gap mesh: Structured rings)")
            if _sgspec:            # iron circles must sit EXACTLY on the
                _p_geo = dict(_p_geo)   # belt-spec radii (4 um off = no weld)
                _p_geo["stator_inner_radius"] = float(_sgspec["r_si"])
                _p_geo["rotor_outer_radius"] = float(_sgspec["r_ro"])
            _density = max(0.3, 2.0 / max(mesh_size_mm, 0.5))
            _check_part_mesh_supported(_user_cm, _use_geo)
            if _use_geo:
                from motor_ai_sim.simulation.geo_mesh import geo_mesh_halves
                _cm = component_mesh_mm or {}
                _air_mm = float(_cm.get("outer") or _cm.get("air") or 0.0)
                (mesh_s, tags_s, classify_s,
                 mesh_r, tags_r, classify_r) = geo_mesh_halves(
                    _p_geo, polys, outer_air_factor=outer_air_factor,
                    density=_density, n_sectors=_ns_i,
                    mesh_edge_mm=float(mesh_size_mm),
                    n_slip=int(_sgspec.get("n_slip", 1008)),
                    r_si=float(_sgspec.get("r_si", 0.0)),
                    r_ro=float(_sgspec.get("r_ro", 0.0)), air_mesh_mm=_air_mm,
                    part_mesh_mm=_user_cm,   # per-part sizes (see full-ring)
                    r1_band=float(_sgspec.get("r1", 0.0)),
                    r2_band=float(_sgspec.get("r2", 0.0)))
                log.info("geo-driven wedge 1/%d: stator %d tris, rotor %d tris",
                         _ns_i, mesh_s.t.shape[1], mesh_r.t.shape[1])
            elif "r1" in _sgspec:
                raise ValueError("template iron does not support the "
                                 "moving-band macro (use geo mesh)")
            else:
                (mesh_s, tags_s, classify_s,
                 mesh_r, tags_r, classify_r) = template_solver_halves(
                    _p_geo, polys, outer_air_factor=outer_air_factor,
                    density=_density, n_sectors=_ns_i)
                log.info("iron template wedge 1/%d: stator %d tris, rotor %d tris",
                         _ns_i, mesh_s.t.shape[1], mesh_r.t.shape[1])
        except Exception as _te:
            log.warning("iron template wedge failed (%s) — gmsh build", _te)
            mesh_s = tags_s = classify_s = None
            mesh_r = tags_r = classify_r = None
    if mesh_s is None and (_pc_stator and _slot_period and _slot_period > 0):
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
    if mesh_r is None and (_pc_rotor and _pole_period and _pole_period > 0):
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

    # BELT on the sector build: the open belt slice's cut columns land exactly
    # on the radial cuts (grid angles), so the sector master–slave pairing
    # picks them up like any other cut node.
    if _SB_BELT:
        _bs = polys_s.get("structured_gap_spec")
        _br = polys_r.get("structured_gap_spec")
        # moving-band spec (r1/r2) → analytic gap, no belt cells to weld
        if _bs and "r1" not in _bs:
            mesh_s, tags_s = _weld_belt_into_half(mesh_s, tags_s, _bs, "stator", n_sectors)
        if _br and "r1" not in _br:
            mesh_r, tags_r = _weld_belt_into_half(mesh_r, tags_r, _br, "rotor", n_sectors)

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

def _split_ring_pinches(ring):
    """A ring visiting the same point twice (non-consecutively) is a PINCH —
    two loops touching at a node.  Shapely tolerates it; OCC rejects the curve
    loop ('Curve loop is not closed', a figure-eight).  Split into separate
    loops at every pinch (recursively).  ``ring`` = list of (x, y), open."""
    ring = list(ring)
    seen = {}
    for idx, p in enumerate(ring):
        k = (round(p[0], 7), round(p[1], 7))
        if k in seen:
            a = seen[k]
            inner = ring[a:idx]
            outer = ring[:a] + ring[idx:]
            out = []
            if len(inner) >= 3:
                out += _split_ring_pinches(inner)
            if len(outer) >= 3:
                out += _split_ring_pinches(outer)
            return out
        seen[k] = idx
    return [ring]

def _split_geom_pinches(geom):
    """Apply _split_ring_pinches to every polygon of a (Multi)Polygon; holes
    are re-attached to whichever part contains them."""
    from shapely.geometry import Polygon as _P, MultiPolygon as _MP
    if geom is None or getattr(geom, "is_empty", True):
        return geom
    def _one(p):
        try:
            rings = _split_ring_pinches(list(p.exterior.coords)[:-1])
            if len(rings) == 1:
                return [p]
            holes = [list(h.coords)[:-1] for h in p.interiors]
            parts = []
            for ring in rings:
                q = _P(ring)
                if not q.is_valid:
                    q = q.buffer(0)
                if q is None or q.is_empty:
                    continue
                own = [h for h in holes
                       if len(h) >= 3 and q.contains(_P(h).representative_point())]
                if own:
                    q = _P(ring, own)
                    if not q.is_valid:
                        q = q.buffer(0)
                for gg in (q.geoms if hasattr(q, "geoms") else [q]):
                    if gg.geom_type == "Polygon" and not gg.is_empty:
                        parts.append(gg)
            return parts if parts else [p]
        except Exception:
            return [p]
    geoms = geom.geoms if hasattr(geom, "geoms") else [geom]
    parts = []
    for g in geoms:
        if g.geom_type == "Polygon":
            parts.extend(_one(g))
    if not parts:
        return geom
    return _MP(parts) if len(parts) > 1 else parts[0]

def _resample_ring_arcs(geom, r_ring: float, n_grid: int, tol_mm: float = 0.02):
    """Return `geom` with every boundary run lying ON the circle r≈r_ring
    resampled to the UNIFORM angular grid (n_grid points per revolution),
    keeping each run's END vertices exactly (slot-mouth / pocket corner angles
    untouched).  Kills the random chord-sagitta roughness of the iron's
    gap-facing surface without re-geometrising the openings."""
    import numpy as _np
    from shapely.geometry import Polygon as _P, MultiPolygon as _MP

    step = 2.0 * math.pi / max(8, int(n_grid))

    def _ring(coords):
        # VERTEX-INDEPENDENT run detection.  Deciding "on the ring" per VERTEX
        # (|r−r_ring|<tol) made tooth/pocket corners flip in or out of a run
        # depending on where the CadQuery fillet polygonisation happened to put
        # a vertex — DIFFERENT per tooth → per-tooth snap differences → broken
        # slot symmetry (forbidden torque orders) + inflated cogging.  Instead:
        # find the CONTINUOUS angles where the boundary crosses the circle
        # r = r_ring − δ (linear interpolation along segments — identical for
        # every tooth up to the µm polygonisation), and replace everything
        # above that circle with grid nodes k ∈ [ceil(θin/step), floor(θout/step)].
        pts = [(float(x), float(y)) for x, y in coords]
        if len(pts) > 1 and math.hypot(pts[0][0]-pts[-1][0], pts[0][1]-pts[-1][1]) < 1e-12:
            pts = pts[:-1]
        n = len(pts)
        if n < 3:
            return coords
        r = _np.hypot([p[0] for p in pts], [p[1] for p in pts])
        on = _np.abs(r - r_ring) < tol_mm
        if not on.any():
            return coords
        if bool(on.all()):
            return [(r_ring*math.cos(step*k), r_ring*math.sin(step*k))
                    for k in range(max(8, int(n_grid)))]
        # rotate so index 0 is OFF-ring → every run is contiguous (wrap-around
        # runs split across the array boundary caused the pocket-hole bug)
        if bool(on[0]):
            k0 = int(_np.argmin(on))
            pts = pts[k0:] + pts[:k0]
            r = _np.roll(r, -k0)
            on = _np.roll(on, -k0)

        delta = min(tol_mm * 0.2, 0.005)

        def _cross_angle(i_on, i_off):
            """CONTINUOUS run-end angle: where the boundary leaves the ring,
            interpolated on the segment on→off at r_ring ± δ (the off side's
            own side).  Vertex-placement independent — a fillet vertex landing
            just in/out of the tol window moves this by µm, not by a segment,
            so every tooth/pocket snaps IDENTICALLY."""
            pa, ra = pts[i_on % n], r[i_on % n]
            pb, rb = pts[i_off % n], r[i_off % n]
            r_c = r_ring + (delta if rb > r_ring else -delta)
            t = (r_c - ra) / (rb - ra) if abs(rb - ra) > 1e-12 else 0.0
            t = min(1.0, max(0.0, t))
            x = pa[0] + t * (pb[0] - pa[0]); y = pa[1] + t * (pb[1] - pa[1])
            return math.atan2(y, x)

        out = []
        def _push(p):
            if not out or math.hypot(p[0]-out[-1][0], p[1]-out[-1][1]) > 1e-9:
                out.append(p)
        i = 0
        while i < n:
            if not on[i]:
                _push(pts[i]); i += 1
                continue
            j = i
            while j < n and on[j]:
                j += 1
            a_in = _cross_angle(i, i - 1)
            a_out = _cross_angle(j - 1, j % n)
            a_mid = math.atan2(pts[(i + j - 1) // 2][1], pts[(i + j - 1) // 2][0])
            while a_in - a_mid > math.pi:  a_in -= 2*math.pi
            while a_in - a_mid < -math.pi: a_in += 2*math.pi
            while a_out - a_mid > math.pi:  a_out -= 2*math.pi
            while a_out - a_mid < -math.pi: a_out += 2*math.pi
            lo, hi = (a_in, a_out) if a_out >= a_in else (a_out, a_in)
            # ROUND (not ceil/floor) both ends: the iron run and the adjacent
            # pocket/mouth run share the SAME physical crossing angle, so
            # rounding makes them meet at the SAME grid node — ceil/floor made
            # each retreat inward, leaving an uncovered node between domains
            # (belt weld deficit, one gap node per pocket edge).
            k_lo = int(round(lo / step))
            k_hi = int(round(hi / step))
            ks = list(range(k_lo, k_hi + 1))
            if a_out < a_in:
                ks = ks[::-1]
            if not ks:
                ks = [int(round(0.5 * (lo + hi) / step))]
            for k in ks:
                _push((r_ring*math.cos(step*k), r_ring*math.sin(step*k)))
            i = j
        # De-spike: two runs separated by a tiny off-ring dip can snap to the
        # SAME grid node → ... K, dip, K ... = a zero-area bow-tie that breaks
        # the OCC curve loop ("Curve loop is not closed", seen at gap_layers=2
        # where the slip grid is coarser).  Collapse A,B,A → A repeatedly.
        changed = True
        while changed and len(out) >= 3:
            changed = False
            m = len(out)
            for a in range(m):
                if (math.hypot(out[a][0]-out[(a+2) % m][0],
                               out[a][1]-out[(a+2) % m][1]) < 1e-9):
                    hi_i, lo_i = sorted(((a+1) % m, (a+2) % m), reverse=True)
                    del out[hi_i]; del out[lo_i]
                    changed = True
                    break
        return out

    _split_pinches = _split_ring_pinches

    def _poly(p):
        try:
            rings = _split_pinches(_ring(p.exterior.coords))
            holes = [_ring(h.coords) for h in p.interiors]
            parts = []
            for ring in rings:
                q = _P(ring)
                if not q.is_valid:
                    q = q.buffer(0)
                if q is None or q.is_empty:
                    continue
                # attach each hole to the part that contains it
                own = [h for h in holes
                       if len(h) >= 3 and q.contains(_P(h).representative_point())]
                if own:
                    q = _P(ring, own)
                    if not q.is_valid:
                        q = q.buffer(0)
                for gg in (q.geoms if hasattr(q, "geoms") else [q]):
                    if gg.geom_type == "Polygon" and not gg.is_empty:
                        parts.append(gg)
            return parts if parts else [p]
        except Exception:
            return [p]

    if geom is None or geom.is_empty:
        return geom
    if geom.geom_type == "Polygon":
        parts = _poly(geom)
        return _MP(parts) if len(parts) > 1 else parts[0]
    if hasattr(geom, "geoms"):
        parts = []
        for g in geom.geoms:
            if g.geom_type == "Polygon":
                parts.extend(_poly(g))
        return _MP(parts) if len(parts) > 1 else (parts[0] if parts else geom)
    return geom

def _weld_belt_into_half(mesh, tags, spec: dict, half: str, n_sectors: int):
    """BELT: build this half's gap slice (r_lo→r_hi from ``spec``) directly in
    numpy — K uniform radial rings × the n_slip angular grid — and weld it into
    the gmsh half-mesh by EXACT node identity (the iron / pocket / mouth
    boundaries were resampled onto the same grid, so the belt's inner boundary
    nodes coincide bit-for-bit with existing mesh nodes).

    Returns (mesh, tags) with the belt merged in; raises on a weld deficit
    (missing coincident nodes = the boundary was NOT on the grid → would leave
    a crack; better to fail loudly than solve a torn field)."""
    from scipy.spatial import cKDTree as _KD

    N = int(spec["n_slip"])
    K = max(1, int(spec["K"]))
    r_lo = float(spec["r_lo"]) * 1e-3      # mm → m (mesh coords are metres)
    r_hi = float(spec["r_hi"]) * 1e-3
    ns = max(1, int(n_sectors))
    full = (ns == 1)
    cols = N if full else (N // ns + 1)    # open sector keeps both cut columns
    ang0 = 2.0 * math.pi / N
    span = (2.0 * math.pi) if full else (2.0 * math.pi / ns)

    P = np.asarray(mesh.p, float); T = np.asarray(mesh.t, np.int64)
    n0 = P.shape[1]

    # ── The belt's IRON-side row is the mesh's OWN boundary ring ────────────
    # Take the ACTUAL half-mesh nodes sitting on the iron-side circle (r_lo
    # for the rotor half, r_hi for the stator half) and stitch the first belt
    # row onto them with a two-pointer seam triangulation.  Conformity is then
    # guaranteed BY CONSTRUCTION for any boundary the mesher produced — no
    # node-identity assumption, no weld deficit (pocket corners, segment gaps
    # and other boundary irregularities are absorbed by the seam row).
    r_iron = r_lo if half == "rotor" else r_hi
    rP = np.hypot(P[0], P[1])
    ring_idx = np.where(np.abs(rP - r_iron) < 2e-6)[0]        # ±2 µm
    if ring_idx.size < 8:
        raise ValueError(f"belt[{half}]: only {ring_idx.size} mesh nodes on the "
                         f"iron circle r={r_iron*1e3:.4f}mm — boundary not on the ring")
    # SAME angular convention as the belt rows (0..2π) — sorting the iron by
    # raw atan2 (−π..π] paired the wrong halves in the seam (field welded with
    # a half-turn twist: psi collapsed 14.7 → 5.4 mWb).
    angP = np.mod(np.arctan2(P[1, ring_idx], P[0, ring_idx]), 2.0 * math.pi)
    if full:
        order = np.argsort(angP)
        iron = ring_idx[order]; iron_a = angP[order]
    else:
        sel = (angP > -1e-9) & (angP < span + 1e-9)
        order = np.argsort(angP[sel])
        iron = ring_idx[sel][order]; iron_a = angP[sel][order]

    # uniform rows: seam row at the iron radius offset is not needed — rows
    # start at the FIRST uniform ring (one layer in) and go to the slip side.
    rows_r = (np.linspace(r_lo, r_hi, K + 1)[1:] if half == "rotor"
              else np.linspace(r_lo, r_hi, K + 1)[:-1][::-1])
    # rows_r[0] is the ring adjacent to the iron; rows_r[-1] is the slip ring
    aa = np.arange(cols) * ang0
    bp_rows = []
    for r_ in rows_r:
        bp_rows.append(np.vstack([r_ * np.cos(aa), r_ * np.sin(aa)]))
    bp = np.hstack(bp_rows)                                   # (2, K*cols)
    bdom = DOM_AIRGAP if half == "rotor" else DOM_OUTER

    def _uid(j, i):        # uniform-grid node id (global, after concat)
        return n0 + j * cols + (i % cols if full else min(i, cols - 1))

    tri = []
    # ── seam: iron boundary nodes ↔ first uniform ring (two-pointer merge) ──
    m = iron.size
    ia = 0; ib = 0
    a_ext = np.concatenate([iron_a, iron_a[:1] + (2.0 * math.pi if full else 0.0)])
    ncell = cols if full else cols - 1
    b_ext = np.concatenate([aa, [aa[-1] + ang0] if full else [aa[-1]]])
    total_a = m if full else m - 1
    total_b = ncell
    while ia < total_a or ib < total_b:
        adv_a = (ia < total_a) and (ib >= total_b or a_ext[ia + 1] <= b_ext[ib + 1])
        if adv_a:
            tri.append((int(iron[ia % m]), int(iron[(ia + 1) % m]), _uid(0, ib)))
            ia += 1
        else:
            tri.append((int(iron[ia % m]), _uid(0, ib + 1), _uid(0, ib)))
            ib += 1
    # ── uniform quads between successive rings ──
    for j in range(len(rows_r) - 1):
        for i in range(ncell):
            a = _uid(j, i); b = _uid(j, i + 1)
            c = _uid(j + 1, i); d = _uid(j + 1, i + 1)
            tri.append((a, b, d)); tri.append((a, d, c))
    bt = np.asarray(tri, dtype=np.int64).T

    Pall = np.hstack([P, bp])
    Tall = np.hstack([T, bt])
    tags_all = np.concatenate([np.asarray(tags),
                               np.full(bt.shape[1], bdom, dtype=np.asarray(tags).dtype)])
    # drop degenerate tris (duplicate iron nodes etc.)
    good = ((Tall[0] != Tall[1]) & (Tall[1] != Tall[2]) & (Tall[0] != Tall[2]))
    mesh_out = type(mesh)(Pall, np.ascontiguousarray(Tall[:, good]))
    tags_out = tags_all[good]
    log.info("belt[%s]: %d iron boundary nodes, %d uniform rings x %d, %d tris",
             half, m, len(rows_r), ncell, bt.shape[1])
    return mesh_out, tags_out

def _structured_gap_sm(n_slip: int, n_sectors: int,
                       m_target: int = None) -> Tuple[int, int]:
    """Pick (S, M) for the structured-gap cells of ONE wedge (route A).

    S = number of angular cells in the [0, 2π/n_sectors] wedge, M = arc
    divisions per cell.  Requirement: S·M = n_slip / n_sectors (so the mid
    ring lands EXACTLY on the global slip grid 2πj/n_slip and the sliding
    coupling's _ring() finds a uniform ring).  Among the (S, M) factorings
    of slip_wedge, prefer M near ``m_target`` (~14, like the proven proto)
    to keep cell aspect reasonable and the surface count modest.
    """
    if m_target is None:
        m_target = _SG_M_TARGET
    slip_wedge = int(n_slip) // int(n_sectors)
    if slip_wedge <= 0:
        return 1, max(1, int(n_slip))
    # candidate M = every divisor of slip_wedge; pick the one closest to target.
    # HARD PREFERENCE: EVEN S.  The stator wedge always spans 2 slot pitches
    # (slot_period = a slot PAIR), so an odd S puts a half-integer number of
    # seams per slot pitch — the seam grid PHASE then alternates between the
    # wedge's two slots, their mouth corners snap DIFFERENTLY, and the slot
    # symmetry breaks (measured on 24s20p: gl=5 → S=49 odd → h6 tripled +
    # forbidden orders).  An even S keeps every slot's snap identical.
    divs = [d for d in range(1, slip_wedge + 1) if slip_wedge % d == 0]
    even = [d for d in divs if (slip_wedge // d) % 2 == 0]
    pool = even if even else divs
    M = min(pool, key=lambda d: (abs(d - m_target), d))
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
        # A polygon whose FIRST vertex sits inside an on-ring run splits that
        # run across the array boundary: the linear scan below then snaps the
        # "tail" and "head" pieces separately, their seam ranges OVERLAP (round
        # widens each to the nearest seam), and the loop walks the same arc
        # twice → self-intersecting curve loop → OCC rejects it → the polygon
        # was silently DROPPED (a HOLE: 20 pocket holes = −13 % flux on the
        # 24s20p rotor half).  Rotate the vertex list so index 0 is OFF-ring —
        # every run is then contiguous.  (All-on-ring polygons keep as-is: one
        # single run covering the full loop is already contiguous.)
        if bool(on[0]) and not bool(on.all()):
            k0 = int(_np.argmin(on))          # first off-ring vertex
            pts = pts[k0:] + pts[:k0]
            r = _np.roll(r, -k0)
            on = _np.roll(on, -k0)
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
                except Exception as _e:
                    log.warning("arc-ring: addCircleArc failed (%s) pts %d->%d — "
                                "line fallback", _e, pa, pb)
            try:
                curves.append(occ.addLine(pa, pb))
            except Exception as _e:
                # A dropped segment BREAKS the loop → the whole polygon would be
                # dropped → a HOLE in the mesh (measured: −13 % flux on 24s20p).
                log.warning("arc-ring: addLine failed (%s) pts %d->%d — SEGMENT "
                            "LOST, loop will not close", _e, pa, pb)
                continue
        if len(curves) < 3:
            log.warning("arc-ring: curve loop degenerate (%d curves from %d pts, "
                        "r_ring=%.4f) — polygon will be DROPPED", len(curves), n, r_ring)
            return None
        try:
            return occ.addCurveLoop(curves)
        except Exception as _e:
            log.warning("arc-ring: addCurveLoop failed (%s; %d curves, r_ring=%.4f)"
                        " — polygon will be DROPPED", _e, len(curves), r_ring)
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
            # NOTE: no polyline fallback here — an on-ring polyline coincident
            # with the cell arcs makes occ.fragment intersect every chord with
            # every arc (combinatorial blow-up, measured: build hangs >10 min).
            # A dropped polygon is a HOLE in the mesh (dead flux) — the loud
            # warning below must be treated as a build FAILURE to investigate.
            log.warning("arc-ring: polygon SKIPPED (area %.3f mm2, r_ring=%.4f)"
                        " — a HOLE is left in the mesh here", g.area, r_ring)
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

        # Memoised point adder: identical coordinates (to 0.1 nm) reuse ONE OCC
        # point tag, so near-coincident polygon vertices produce a==b segments
        # (skipped) instead of ~zero-length lines OCC rejects — which used to
        # leave the curve loop OPEN ("Curve loop is not closed" build failure).
        _occ_ptcache: Dict[Tuple[float, float], int] = {}

        def _occ_pt(x: float, y: float) -> int:
            _k = (round(x, 7), round(y, 7))
            _t = _occ_ptcache.get(_k)
            if _t is None:
                _t = occ.addPoint(x, y, 0)
                _occ_ptcache[_k] = _t
            return _t

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
            # Split PINCHED (self-touching) rings — Shapely tolerates a ring
            # that visits the same point twice, OCC rejects the loop.  Pinches
            # arise from ring subtractions and half/wedge clips; the converter
            # is the one place every polygon passes through.
            _gs2 = []
            from shapely.geometry import Polygon as _SP3
            for g in geoms:
                _ext0 = _dedupe(list(g.exterior.coords)[:-1])
                rings = _split_ring_pinches(_ext0)
                # NOTE: even a single returned ring may be CLEANED (tiny
                # sub-loops < 3 pts dropped) — always rebuild from the rings,
                # never fall back to the original pinched polygon.
                if len(rings) == 1 and len(rings[0]) == len(_ext0):
                    _gs2.append(g)
                    continue
                holes = [list(h.coords)[:-1] for h in g.interiors]
                for ring in rings:
                    if len(ring) < 3:
                        continue
                    q = _SP3(ring)
                    if not q.is_valid:
                        q = q.buffer(0)
                    own = [h for h in holes if len(h) >= 3 and
                           q.contains(_SP3(h).representative_point())]
                    if own:
                        q = _SP3(ring, own)
                        if not q.is_valid:
                            q = q.buffer(0)
                    _gs2.extend(_polys_only(q))
            geoms = _gs2
            tags: List[int] = []
            for g in geoms:
                if g.is_empty or g.area < 1e-6:
                    continue
                ext = _dedupe(list(g.exterior.coords)[:-1])
                if len(ext) < 3:
                    continue
                pt_tags = [_occ_pt(x, y) for x, y in ext]
                line_tags = []
                # CHAIN build: a failed segment (OCC rejects ~zero-length lines
                # between coincident-but-distinct points) must NOT break the
                # loop — bridge from the last successful endpoint instead
                # ("Curve loop is not closed" took the whole build down).
                tail = pt_tags[0]
                for i in range(1, len(pt_tags) + 1):
                    b = pt_tags[i % len(pt_tags)]
                    if tail == b:
                        continue
                    try:
                        line_tags.append(occ.addLine(tail, b))
                        tail = b
                    except Exception:
                        continue          # keep tail → bridge over the bad point
                if len(line_tags) < 3:
                    continue
                try:
                    outer_wire = occ.addCurveLoop(line_tags)
                except Exception as _e:
                    _c = g.centroid
                    log.warning("polygon loop failed (%s): area=%.4f mm2 at "
                                "(%.2f, %.2f) r=%.3f — polygon SKIPPED",
                                _e, g.area, _c.x, _c.y, math.hypot(_c.x, _c.y))
                    continue
                hole_wires: List[int] = []
                for hole in g.interiors:
                    hext = _dedupe(list(hole.coords)[:-1])
                    if len(hext) < 3:
                        continue
                    hpts = [_occ_pt(x, y) for x, y in hext]
                    hlines = []
                    htail = hpts[0]
                    for i in range(1, len(hpts) + 1):
                        b = hpts[i % len(hpts)]
                        if htail == b:
                            continue
                        try:
                            hlines.append(occ.addLine(htail, b))
                            htail = b
                        except Exception:
                            continue      # bridge over the bad point (see exterior)
                    if len(hlines) >= 3:
                        try:
                            hole_wires.append(occ.addCurveLoop(hlines))
                        except Exception as _e:
                            log.warning("hole loop skipped (%s)", _e)
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
        # BELT mode: the gap is built OUTSIDE gmsh (numpy annulus welded by node
        # identity in _build_sliding_band_meshes) — skip the whole OCC route-A
        # machinery (arc-ring iron + transfinite cells).  The polygons still
        # carry the ring cut-outs + resampled boundaries the belt welds onto.
        _sg_spec0 = None if _SB_BELT else polys.get("structured_gap_spec")
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
        _sg_spec = None if _SB_BELT else polys.get("structured_gap_spec")
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
