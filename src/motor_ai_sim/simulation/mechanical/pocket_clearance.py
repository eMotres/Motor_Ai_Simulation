"""Magnets in pockets that are DRAWN with a clearance — the mechanical model.

Written 2026-11-25 (owner: mechanics must accept ``magnet_pocket_clearance``
above the rotor mesher's welding tolerance).

THE PROBLEM
-----------
``geometry.magnet_pocket_clearance`` [mm per side] cuts the rotor pocket as the
magnet outline offset outward by the clearance (``cadquery_geometry.
_pocket_clearance``).  The magnetic model wants exactly that: the ring between
magnet and pocket wall is air.  The structural model cannot mesh it as drawn:
its contact is node-to-node on a CONFORMING mesh (``mechanical.contact``), and a
magnet that shares no boundary with the iron is a separate island.  Below the
mesher's node weld (``rotor_stress.build_rotor_mesh(weld_tol_mm=0.02)``) the
weld closes the ring and the magnet is meshed as touching its pocket — every
die stored so far is in that range or at 0.  Above it (a real 0.035 mm Fusion
clearance) the rotor solids came out in pieces and rotor stress, modes and
critical speeds were refused.

THE MODEL (one rule for every mechanical solve)
-----------------------------------------------
The IRON keeps the real pocket: the rotor polygon is not touched, so thin walls
and bridges are exactly the drawn ones and nothing in the clearance is iron.

The MAGNET is meshed as the body that fills the AIR OF ITS POCKET — the
connected air region around the drawn magnet inside the rotor's outline (its
convex hull, so a pocket opening to the gap is closed by the chord between the
two lips), limited to the reach a magnet may be seated across
(``contact.SEAT_TRAVEL_FRAC`` of the rotor radius, plus the clearance).  So
magnet and pocket share their boundary again and the ordinary contact split
applies — along the clearance ring AND across any air DRAWN above the magnet
(``magnet_up_gap``): measured on a 14-pole spoke rotor with 0.035 mm of
clearance, the flanks taper 2.5 deg, so a magnet travelling outward would close
the flank clearance only after ~0.8 mm, while the lips over its corners stand
~0.14 mm above it — the lips are what retains it, and a fill limited to the
clearance ring left them with no contact pair at all (the solve was refused as a
runaway).  Then:

  * rotor stress (``separation`` contact, the default): every magnet/iron and
    magnet/sleeve pair carries a STRESS-FREE GAP ``g0`` equal to the drawn air
    between the magnet and that point of the pocket (the distance from the pair
    to the drawn magnet, ``pair_gaps``) and starts OPEN.  Under load the magnet travels across the
    clearance and seats against the pocket — the contact solve's own seating
    step (``contact.solve_contact``, "SEATING A LOOSE PART"): it is moved as a
    rigid body onto the faces its load points at, and only the pairs it lands
    on carry pressure.  A magnet with nothing to land on (an open-top pocket,
    nothing outward) is still ``free_parts`` and the solve is still refused —
    the clearance cannot invent a retaining surface.
  * a ``bonded`` magnet/iron joint is the glued magnet: the adhesive fills the
    pocket, so the bonded fill is the glued body and ``g0`` does not enter.
  * modes and critical speeds: the magnet is BONDED at its seated position,
    i.e. the pocket-filling body tied to the iron — the same bonded model those
    two solves use at clearance 0.  The seated travel is a fraction of a
    millimetre, so the seated position and the drawn one are the same
    cross-section for a mode shape.

MASS.  The fill is larger than the drawn magnet by the pocket air (a few per
cent).  Its density is scaled by ``density_scale = A_drawn / A_fill`` so every
magnet carries exactly the drawn magnet's mass, centrifugal load and inertia
(``scaled_mechs``).  The stiffness of that air is the magnet's — a few per cent
more magnet section, on the part that carries least.

OPT-IN, BIT FOR BIT.  ``mechanical_polys`` returns the SAME polygons object
when the clearance is 0, unset, or at/below the welding tolerance (the range
every stored die and result is in), and every hook in the solvers reads the
marker key it alone adds — so nothing about any existing answer moves.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np

#: The rotor mesher's node weld, mm (``rotor_stress.build_rotor_mesh``'s
#: ``weld_tol_mm`` default).  A drawn clearance at or below it is closed by the
#: weld and meshed as the touching pocket it has always been meshed as.
WELD_TOL_MM = 0.02

#: The key ``mechanical_polys`` adds to a polygons dict.  Its presence is the
#: whole switch: every solver hook reads it and does nothing without it.
POCKET_KEY = "mech_pocket_clearance"

#: The share of the clearance ring (the magnet offset by 90 % of the clearance)
#: that may lie in drawn iron before the geometry is refused.  The builder cuts
#: the pocket as the magnet offset by the clearance, so only the polygon
#: sanitiser's micron-scale vertex merges reach in; a pocket that is NOT that
#: offset is not the machine the clearance describes.
MAX_IRON_OVERLAP_FRAC = 0.02

RULE_STRESS = ("magnet meshed to fill its pocket's air; separation pairs start "
               "open with the drawn air (clearance, up gap) as their stress-free "
               "gap, and the magnet seats against the pocket under load; iron "
               "keeps the real pocket")
RULE_MODAL = ("magnet bonded at its seated position (the pocket-filling body "
              "tied to the iron), density scaled to the drawn magnet's mass")


def clearance_of(params: Optional[Dict[str, Any]]) -> float:
    """``magnet_pocket_clearance`` [mm per side] read the way the builder reads
    it: absent / None / '' / non-numeric / <= 0 / NaN = 0."""
    if not params:
        return 0.0
    try:
        v = float(params.get("magnet_pocket_clearance", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return v if (v == v and math.isfinite(v) and v > 0.0) else 0.0


def applies(clearance_mm: float) -> bool:
    """True when the clearance is wider than the mesher's weld closes."""
    return float(clearance_mm) > WELD_TOL_MM


def _valid(g):
    return g if g.is_valid else g.buffer(0)


def _largest(g):
    if g.geom_type == "Polygon":
        return g
    parts = [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
    return max(parts, key=lambda p: p.area) if parts else g


def _r_outer_mm(polys: dict) -> float:
    from shapely.geometry import MultiPolygon
    r = 0.0
    for name in ("rotor", "shaft"):
        g = polys.get(name)
        if g is None or g.is_empty:
            continue
        for p in (g.geoms if isinstance(g, MultiPolygon) else [g]):
            xy = np.asarray(p.exterior.coords)
            r = max(r, float(np.hypot(xy[:, 0], xy[:, 1]).max()))
    for mp, _pol in polys.get("magnets") or []:
        xy = np.asarray(_largest(mp).exterior.coords)
        r = max(r, float(np.hypot(xy[:, 0], xy[:, 1]).max()))
    return r


def mechanical_polys(polys: dict, params: Optional[Dict[str, Any]]) -> dict:
    """The polygons the mechanical solvers mesh.

    ``polys`` itself (the same object, untouched) unless the machine's
    ``magnet_pocket_clearance`` is wider than ``WELD_TOL_MM``; then a shallow
    copy whose ``magnets`` are the pocket fills (see the module docstring) and
    which carries ``POCKET_KEY`` with the drawn magnets and the mass scale.
    The fill never overlaps iron (it is the pocket's air).  Raises
    ``ValueError`` when the iron reaches into the clearance ring, i.e. the drawn
    pocket is not the magnet offset by the clearance.
    """
    c = clearance_of(params)
    mags = list(polys.get("magnets") or [])
    if not applies(c) or not mags:
        return polys
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union
    from motor_ai_sim.simulation.mechanical.contact import SEAT_TRAVEL_FRAC

    rotor = polys.get("rotor")
    if rotor is None or rotor.is_empty:
        return polys
    rotor = _valid(rotor)
    # The rotor's outline: the convex hull of its iron (and of the magnets, for
    # a magnet flush with the OD).  Its edges ARE the OD stations, so no sliver
    # of air opens between it and the iron along the OD, and a pocket opening to
    # the gap is closed by the chord between the lips.
    outline = unary_union([rotor] + [_valid(mp) for mp, _ in mags]).convex_hull
    reach = SEAT_TRAVEL_FRAC * _r_outer_mm(polys) + c
    drawn = [_valid(mp) for mp, _pol in mags]
    solid = unary_union([rotor] + [_valid(g) for g in (polys.get("shaft"),
                                                       polys.get("sleeve"))
                                   if g is not None and not g.is_empty])
    fills = []
    a_drawn = a_fill = 0.0
    for i, ((mp, pol), mv) in enumerate(zip(mags, drawn)):
        ring = mv.buffer(0.9 * c, join_style=2).difference(mv)
        over = float(ring.intersection(rotor).area)
        if ring.area > 0.0 and over > MAX_IRON_OVERLAP_FRAC * ring.area:
            raise ValueError(
                f"magnet_pocket_clearance {c:g} mm: the drawn pocket of magnet "
                f"{i + 1} is not the magnet offset by the clearance "
                f"({100.0 * over / ring.area:.1f} % of the clearance ring lies "
                "in rotor iron) — the mechanical model cannot place the magnet "
                "in this pocket")
        others = [d for j, d in enumerate(drawn) if j != i]
        air = outline.intersection(mv.buffer(reach, join_style=2))
        air = air.difference(unary_union([solid] + others) if others else solid)
        parts = [g for g in (air.geoms if isinstance(air, MultiPolygon)
                             or air.geom_type == "GeometryCollection" else [air])
                 if g.geom_type == "Polygon" and g.intersects(mv)]
        fill = _largest(_valid(unary_union(parts + [mv])))
        fills.append((fill, pol))
        a_drawn += float(mv.area)
        a_fill += float(fill.area)
    out = dict(polys)
    out["magnets"] = fills
    out[POCKET_KEY] = {
        "clearance_mm": float(c),
        "weld_tol_mm": WELD_TOL_MM,
        "magnets_drawn": [(d, pol) for d, (_mp, pol) in zip(drawn, mags)],
        "magnet_area_drawn_mm2": a_drawn,
        "magnet_area_meshed_mm2": a_fill,
        "density_scale": (a_drawn / a_fill) if a_fill > 0.0 else 1.0,
    }
    return out


def info(polys: Optional[dict]) -> Optional[Dict[str, Any]]:
    """The marker ``mechanical_polys`` left, or None (every other machine)."""
    if not polys:
        return None
    pk = polys.get(POCKET_KEY)
    return pk if isinstance(pk, dict) else None


def scaled_mechs(mech: Dict[str, Any], polys: Optional[dict]) -> Dict[str, Any]:
    """``mech`` (``rotor_stress.resolve_part_materials``) with the magnet's
    density scaled so the pocket-filling body weighs what the drawn magnet does.
    The same dict object when there is no clearance marker."""
    pk = info(polys)
    if pk is None or "magnet" not in mech or mech["magnet"] is None:
        return mech
    import dataclasses
    out = dict(mech)
    out["magnet"] = dataclasses.replace(
        mech["magnet"],
        density=float(mech["magnet"].density) * float(pk["density_scale"]))
    return out


def summary(polys: Optional[dict], rule: str) -> Optional[Dict[str, Any]]:
    """The block a result carries to say this model was used; None otherwise."""
    pk = info(polys)
    if pk is None:
        return None
    return {"clearance_mm": pk["clearance_mm"], "weld_tol_mm": pk["weld_tol_mm"],
            "magnet_area_drawn_mm2": round(pk["magnet_area_drawn_mm2"], 4),
            "magnet_area_meshed_mm2": round(pk["magnet_area_meshed_mm2"], 4),
            "density_scale": round(pk["density_scale"], 6), "rule": rule}


def pair_gaps(cs, polys: Optional[dict]) -> Optional[np.ndarray]:
    """The stress-free gap of every contact pair, metres: the distance from the
    pair to the DRAWN magnet for every SEPARATION pair with a magnet side, 0
    elsewhere.
    None when there is no clearance marker (the pair table stays at its zero
    gaps, i.e. the conforming mesh as always)."""
    pk = info(polys)
    if pk is None or not cs.n_pairs:
        return None
    from shapely.geometry import Point
    from shapely.ops import unary_union
    from shapely.prepared import prep
    from motor_ai_sim.simulation.mechanical.contact import PART_MAGNET

    mag_iface = np.array([PART_MAGNET in (it.part_a, it.part_b)
                          for it in cs.interfaces], dtype=bool)
    on_mag = mag_iface[np.asarray(cs.pair_iface, dtype=np.int64)] \
        if mag_iface.size else np.zeros(cs.n_pairs, dtype=bool)
    # Only a SEPARATION pair can open: a bonded / sliding magnet joint is the
    # glued magnet, whose adhesive fills the ring, so it keeps the zero gap.
    uni = np.asarray(cs.pair_unilateral, dtype=bool)
    if uni.size == on_mag.size:
        on_mag = on_mag & uni
    g0 = np.zeros(cs.n_pairs)
    if not on_mag.any():
        return g0
    body = unary_union([d for d, _pol in pk["magnets_drawn"]])
    inside = prep(body)
    pos_mm = np.asarray(cs.pair_pos, dtype=float) * 1e3
    for i in np.nonzero(on_mag)[0]:
        q = Point(float(pos_mm[i, 0]), float(pos_mm[i, 1]))
        g0[i] = 0.0 if inside.contains(q) else float(body.distance(q)) * 1e-3
    # Where the drawn magnet itself is the meshed boundary (a face clipped at
    # the rotor OD, or a side the clearance does not open) the distance is the
    # polygon sampling error, not a gap: those pairs touch as before.
    g0[g0 < 1e-3 * float(pk["clearance_mm"]) * 1e-3] = 0.0
    return g0
