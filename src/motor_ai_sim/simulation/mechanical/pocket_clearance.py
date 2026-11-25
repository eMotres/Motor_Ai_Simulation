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

The MAGNET is meshed as a body that fills pocket air, so magnet and pocket
share their boundary again and the ordinary contact split applies.  Two fills,
both clipped to the radial span of the IRON (an inner rotor's fill stops at its
OD, an outer rotor's at its bore — never in the air gap):

  * the SEATING fill, for a ``separation`` magnet joint (the default): the
    pocket's air within ``clearance + magnet_up_gap + 2 x WELD_TOL_MM`` of the
    drawn magnet.  It reaches the clearance ring AND the drawn air above the
    magnet: measured on a 14-pole spoke rotor with 0.035 mm of clearance, the
    flanks taper 2.5 deg, so a magnet travelling outward would close the flank
    clearance only after ~0.8 mm, while the lips over its corners stand
    0.06-0.14 mm above it across the drawn up gap — the lips are what retains
    it, and a fill limited to the ring left them with no contact pair at all.
    Every separation pair with a magnet side carries a STRESS-FREE GAP ``g0``:
    the distance from the pair, along its normal, to the drawn magnet
    (``pair_gaps``).  A pair whose normal misses the magnet — the walls of an
    opening over it — gets a gap beyond the seating cap and never closes, so
    the fill in such an opening ties nothing.  All of those pairs start OPEN,
    and under load the magnet travels across the air and seats against the
    pocket — the contact solve's own seating step (``contact.solve_contact``,
    "SEATING A LOOSE PART").  A magnet with nothing to land on (an open-top
    pocket, nothing outward) is still ``free_parts`` and the solve is still
    refused — the clearance cannot invent a retaining surface.
  * the BONDED fill (``bonded_variant``), for every solve in which the magnet
    is tied to the iron — a ``bonded`` / ``sliding`` magnet joint (the glued
    magnet), the ring modes and the critical speeds: the clearance ring only
    (the builder's own offset).  Drawn air beyond it stays air, exactly as at
    clearance 0, so an opening over the magnet is never a plug of magnet
    gluing two pole pieces together.

Refused (a 422 at the route), never guessed: a pocket the iron reaches into
(not the magnet offset by the clearance), a seating fill holding more air than
the clearance and the up gap explain, two magnets sharing pocket air, and
magnets whose drawn / meshed areas differ by more than 0.5 % (one density scale
must carry them all).

MASS.  The fill is larger than the drawn magnet by the pocket air (a few per
cent).  Its density is scaled by ``density_scale = A_drawn / A_fill`` so every
magnet carries exactly the drawn magnet's mass, centrifugal load and inertia
(``scaled_mechs``).  The stiffness of that air is the magnet's — a few per cent
more magnet section, on the part that carries least — and the magnet's own
stresses and safety factor are read inside the drawn magnet only
(``drawn_mask``).

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
RULE_MODAL = ("magnet bonded to its pocket across the clearance ring (the ring "
              "filled, any drawn air beyond it left open), density scaled to "
              "the drawn magnet's mass")


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


def _iron_span_mm(rotor) -> tuple:
    """(r_lo, r_hi): the radial span the IRON occupies, mm.

    ``r_hi`` is the outer radius less the chord sag of the iron's own OD
    stations, so the annulus it bounds never reaches into the sliver of air
    between a true circle and the polygonal OD (which would connect every
    pocket along the surface); ``r_lo`` is the smallest iron radius — the chords
    of a bore bulge INTO the bore, so the annulus stays on iron or pocket air
    there without a correction.  An inner rotor's fill therefore stops at its
    OD and an outer rotor's at its bore: never in the air gap."""
    from shapely.geometry import MultiPolygon
    rings = []
    for p in (rotor.geoms if isinstance(rotor, MultiPolygon) else [rotor]):
        rings.append(np.asarray(p.exterior.coords, dtype=float))
        rings += [np.asarray(h.coords, dtype=float) for h in p.interiors]
    r_all = np.concatenate([np.hypot(x[:, 0], x[:, 1]) for x in rings])
    r_max, r_lo = float(r_all.max()), float(r_all.min())
    sag = 0.0
    for xy in rings:
        r = np.hypot(xy[:, 0], xy[:, 1])
        on = (r[:-1] > r_max - 1e-4) & (r[1:] > r_max - 1e-4)
        if on.any():
            mid = 0.5 * (xy[:-1][on] + xy[1:][on])
            sag = max(sag, float(r_max - np.hypot(mid[:, 0], mid[:, 1]).min()))
    return r_lo, r_max - sag - 1e-6


def _up_gap_of(params: Optional[Dict[str, Any]]) -> float:
    try:
        v = float((params or {}).get("magnet_up_gap", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return v if (v == v and math.isfinite(v) and v > 0.0) else 0.0


def _width_mm(mv) -> float:
    """The magnet's width across its own radial axis (its pole frame), mm."""
    c = mv.centroid
    a = math.atan2(c.y, c.x)
    xy = np.asarray(mv.exterior.coords, dtype=float)
    y = -xy[:, 0] * math.sin(a) + xy[:, 1] * math.cos(a)
    return float(y.max() - y.min())


def _polys_of(g) -> list:
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]


def mechanical_polys(polys: dict, params: Optional[Dict[str, Any]]) -> dict:
    """The polygons the mechanical solvers mesh.

    ``polys`` itself (the same object, untouched) unless the machine's
    ``magnet_pocket_clearance`` is wider than ``WELD_TOL_MM``; then a shallow
    copy whose ``magnets`` are the SEATING fills (see the module docstring) and
    which carries ``POCKET_KEY`` with the drawn magnets, the BONDED fills
    (``bonded_variant``) and the two mass scales.  No fill overlaps iron by
    more than the sanitiser's micron merges.  Raises ``ValueError`` (a 422 at
    the route) when the pocket is not the magnet offset by the clearance, when
    a fill holds more air than the clearance and the up gap explain, when two
    magnets share pocket air, or when the magnets' mass scales differ.
    """
    c = clearance_of(params)
    mags = list(polys.get("magnets") or [])
    if not applies(c) or not mags:
        return polys
    from shapely.geometry import Point
    from shapely.ops import unary_union
    from shapely.strtree import STRtree

    rotor = polys.get("rotor")
    if rotor is None or rotor.is_empty:
        return polys
    rotor = _valid(rotor)
    up = _up_gap_of(params)
    reach = c + up + 2.0 * WELD_TOL_MM
    r_lo, r_hi = _iron_span_mm(rotor)
    span = Point(0.0, 0.0).buffer(r_hi, quad_segs=1024)
    if r_lo > 0.0:
        span = span.difference(Point(0.0, 0.0).buffer(r_lo, quad_segs=1024))
    drawn = [_valid(mp) for mp, _pol in mags]
    tree = STRtree(drawn)
    blockers = [_valid(g) for g in (polys.get("shaft"), polys.get("sleeve"))
                if g is not None and not g.is_empty]
    seat, bond = [], []
    for i, ((mp, pol), mv) in enumerate(zip(mags, drawn)):
        ring9 = mv.buffer(0.9 * c, join_style=2).difference(mv)
        over = float(ring9.intersection(rotor).area)
        if ring9.area > 0.0 and over > MAX_IRON_OVERLAP_FRAC * ring9.area:
            raise ValueError(
                f"magnet_pocket_clearance {c:g} mm: the drawn pocket of magnet "
                f"{i + 1} is not the magnet offset by the clearance "
                f"({100.0 * over / ring9.area:.1f} % of the clearance ring lies "
                "in rotor iron) — the mechanical model cannot place the magnet "
                "in this pocket")
        zone = mv.buffer(reach, join_style=2)
        near = [drawn[int(j)] for j in tree.query(zone) if int(j) != i]
        cut = unary_union(blockers + near) if (blockers or near) else None
        # SEATING fill: the pocket's air within the reach of the drawn air
        air = zone.intersection(span).difference(rotor)
        if cut is not None:
            air = air.difference(cut)
        parts = [g for g in _polys_of(air) if g.intersects(mv)]
        fs = _largest(_valid(unary_union(parts + [mv])))
        # BONDED fill: the clearance ring only (the builder's own offset), so
        # drawn air beyond it — an up gap, an opening to the gap — stays air
        rb = mv.buffer(c, join_style=2).intersection(span)
        if cut is not None:
            rb = rb.difference(cut)
        fb = _largest(_valid(unary_union([mv, rb])))
        extra = float(fs.area - mv.area)
        allowed = (float(mv.buffer(c + 2.0 * WELD_TOL_MM, join_style=2).area
                         - mv.area) + up * _width_mm(mv))
        if extra > 1.02 * allowed + 1e-9:
            raise ValueError(
                f"magnet_pocket_clearance {c:g} mm: the pocket of magnet {i + 1} "
                f"holds {extra:.3f} mm2 of air around the magnet, more than the "
                f"clearance and the up gap explain ({allowed:.3f} mm2) — the "
                "mechanical model cannot tell which of it the magnet may move "
                "into")
        seat.append((fs, pol))
        bond.append((fb, pol))

    def _check(fills, label):
        geoms = [f for f, _ in fills]
        t = STRtree(geoms)
        for i, g in enumerate(geoms):
            for j in t.query(g):
                j = int(j)
                if j > i and g.intersection(geoms[j]).area > 1e-9:
                    raise ValueError(
                        f"magnet_pocket_clearance {c:g} mm: magnets {i + 1} and "
                        f"{j + 1} share pocket air ({label} fill) — one pocket "
                        "per magnet is what this model can place")
        ratios = np.array([d.area / g.area for d, g in zip(drawn, geoms)])
        if ratios.size and (ratios.max() - ratios.min()) > 0.005 * ratios.mean():
            raise ValueError(
                f"magnet_pocket_clearance {c:g} mm: the magnets' pockets differ "
                f"(drawn / meshed area {ratios.min():.4f} … {ratios.max():.4f}, "
                "more than 0.5 %) — one density scale cannot carry every "
                "magnet's mass")
        return float(sum(d.area for d in drawn)), float(sum(g.area for g in geoms))

    a_drawn, a_seat = _check(seat, "seating")
    _a, a_bond = _check(bond, "bonded")
    out = dict(polys)
    out["magnets"] = seat
    out[POCKET_KEY] = {
        "clearance_mm": float(c),
        "up_gap_mm": float(up),
        "weld_tol_mm": WELD_TOL_MM,
        "reach_mm": float(reach),
        "fill": "seating",
        "magnets_drawn": [(d, pol) for d, (_mp, pol) in zip(drawn, mags)],
        "magnets_seating": seat,
        "magnets_bonded": bond,
        "magnet_area_drawn_mm2": a_drawn,
        "magnet_area_meshed_mm2": a_seat,
        "magnet_area_bonded_mm2": a_bond,
        "density_scale": (a_drawn / a_seat) if a_seat > 0.0 else 1.0,
        "density_scale_bonded": (a_drawn / a_bond) if a_bond > 0.0 else 1.0,
    }
    return out


def bonded_variant(polys: Optional[dict]) -> Optional[dict]:
    """``polys`` with the BONDED fills (the clearance ring only) in place of the
    seating ones, for every solve in which the magnet is tied to the iron —
    modes, critical speeds and a bonded / sliding rotor-stress joint — so a
    drawn opening over the magnet is never a plug of magnet tying two pole
    pieces together.  The same object when there is no marker or it is already
    the bonded variant."""
    pk = info(polys)
    if pk is None or pk.get("fill") == "bonded":
        return polys
    out = dict(polys)
    out["magnets"] = list(pk["magnets_bonded"])
    out[POCKET_KEY] = dict(pk, fill="bonded",
                           magnet_area_meshed_mm2=pk["magnet_area_bonded_mm2"],
                           density_scale=pk["density_scale_bonded"])
    return out


def drawn_mask(mesh, part_tri: np.ndarray, polys: Optional[dict],
               part_magnet: int) -> Optional[np.ndarray]:
    """Per element: False for a MAGNET element outside the drawn magnet (the
    clearance / up-gap layer of the fill), True everywhere else.  None when
    there is no marker."""
    pk = info(polys)
    if pk is None:
        return None
    import shapely
    from shapely.ops import unary_union
    body = unary_union([d for d, _pol in pk["magnets_drawn"]])
    p = mesh.p * 1e3
    cen = (p[:, mesh.t[0]] + p[:, mesh.t[1]] + p[:, mesh.t[2]]) / 3.0
    mask = np.ones(mesh.t.shape[1], dtype=bool)
    mm = np.asarray(part_tri) == part_magnet
    if mm.any():
        mask[mm] = shapely.contains_xy(body, cen[0, mm], cen[1, mm])
    return mask


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
            "up_gap_mm": pk.get("up_gap_mm"), "fill": pk.get("fill"),
            "magnet_area_drawn_mm2": round(pk["magnet_area_drawn_mm2"], 4),
            "magnet_area_meshed_mm2": round(pk["magnet_area_meshed_mm2"], 4),
            "density_scale": round(pk["density_scale"], 6), "rule": rule}


def pair_gaps(cs, polys: Optional[dict]) -> Optional[np.ndarray]:
    """The stress-free gap of every contact pair, metres.

    For every SEPARATION pair with a magnet side: the distance from the pair,
    ALONG ITS NORMAL towards the magnet side, to the DRAWN magnet — the travel
    that shuts that pair.  A ray that misses the drawn magnet within the
    fill's reach (the walls of an opening over the magnet, which the magnet
    cannot reach by moving across them) gets the reach plus the rotor's seating
    cap, i.e. it never closes.  0 elsewhere.  None when there is no clearance
    marker (the pair table keeps the conforming mesh's zero gaps).  A pair
    table without a matching ``pair_unilateral`` is refused, never guessed.
    """
    pk = info(polys)
    if pk is None or not cs.n_pairs:
        return None
    import shapely
    from shapely.ops import unary_union
    from motor_ai_sim.simulation.mechanical.contact import (PART_MAGNET,
                                                           SEAT_TRAVEL_FRAC)

    uni = np.asarray(getattr(cs, "pair_unilateral", np.zeros(0)), dtype=bool)
    if uni.size != cs.n_pairs:
        raise ValueError(
            f"pocket clearance: the contact system has {cs.n_pairs} pairs but "
            f"{uni.size} unilateral flags — the stress-free gaps cannot be set")
    ia = np.array([it.part_a == PART_MAGNET for it in cs.interfaces], dtype=bool)
    ib = np.array([it.part_b == PART_MAGNET for it in cs.interfaces], dtype=bool)
    iface = np.asarray(cs.pair_iface, dtype=np.int64)
    a_mag = ia[iface] if ia.size else np.zeros(cs.n_pairs, dtype=bool)
    b_mag = ib[iface] if ib.size else np.zeros(cs.n_pairs, dtype=bool)
    # Only a SEPARATION pair can open: a bonded / sliding magnet joint is the
    # glued magnet, whose adhesive fills the pocket, so it keeps the zero gap.
    on_mag = (a_mag | b_mag) & uni
    g0 = np.zeros(cs.n_pairs)
    if not on_mag.any():
        return g0
    body = unary_union([d for d, _pol in pk["magnets_drawn"]])
    bnd = body.boundary
    sel = np.nonzero(on_mag)[0]
    pos = np.asarray(cs.pair_pos, dtype=float)[sel] * 1e3          # mm
    n = np.asarray(cs.pair_n, dtype=float)[sel]
    # the normal points A -> B; the magnet is on the A side for magnet_rotor
    d = np.where(a_mag[sel][:, None], -n, n)
    L = float(pk.get("reach_mm") or 0.0) + 1e-3
    lines = shapely.linestrings(np.stack([pos, pos + L * d], axis=1))
    hit = shapely.intersection(lines, bnd)
    dist = shapely.distance(shapely.points(pos), hit)
    r_out = float(np.hypot(cs.mesh.p[0], cs.mesh.p[1]).max()) * 1e3
    far = L + SEAT_TRAVEL_FRAC * r_out
    miss = shapely.is_empty(hit) | ~np.isfinite(dist)
    g = np.where(miss, far, dist)
    g[shapely.contains_xy(body, pos[:, 0], pos[:, 1])] = 0.0
    # Where the drawn magnet itself is the meshed boundary (a face clipped at
    # the iron's span, a side the clearance does not open) the distance is the
    # polygon sampling error, not a gap: those pairs touch as before.
    g[g < 1e-3 * float(pk["clearance_mm"])] = 0.0
    g0[sel] = g * 1e-3
    return g0


def magnet_equilibrium(cs, sol, basis, f: np.ndarray,
                       magnet_id_tri: np.ndarray) -> Optional[Dict[str, Any]]:
    """Per magnet: ``|F_body + sum of the contact forces on it| / |F_body|``.

    A magnet in a drawn clearance is held by nothing but its contact pairs, so
    the vector sum of the load on its own dofs (``f``: spin, pull, eigenstrain —
    the torque is on the iron) and of the forces its pairs put on it must
    vanish.  The contact forces are the solve's own (``lam_n`` along the pair
    normal, ``force_t`` along its tangent), taken on the magnet's side of each
    pair.  None when no magnet carries a load (standstill)."""
    from motor_ai_sim.simulation.mechanical.contact import PART_MAGNET
    mid = np.asarray(magnet_id_tri)
    t = cs.mesh.t
    nv = cs.mesh.p.shape[1]
    vmag = -np.ones(nv, dtype=np.int64)
    for j in np.unique(mid[mid >= 0]):
        vmag[np.unique(t[:, mid == j])] = int(j)
    n = np.asarray(cs.pair_n, dtype=float)
    tang = np.stack([-n[:, 1], n[:, 0]], axis=1)
    fa = np.asarray(sol.lam_n, dtype=float)[:, None] * n
    if np.any(sol.force_t):
        fa = fa + np.asarray(sol.force_t, dtype=float)[:, None] * tang
    ia = np.array([it.part_a == PART_MAGNET for it in cs.interfaces], dtype=bool)
    ib = np.array([it.part_b == PART_MAGNET for it in cs.interfaces], dtype=bool)
    iface = np.asarray(cs.pair_iface, dtype=np.int64)
    rows = []
    for j in np.unique(mid[mid >= 0]):
        dofs = np.unique(basis.element_dofs[:, mid == j].ravel())
        fb = np.array([float(f[dofs[dofs % 2 == 0]].sum()),
                       float(f[dofs[dofs % 2 == 1]].sum())])
        on_a = ia[iface] & (vmag[cs.pair_va] == j)
        on_b = ib[iface] & (vmag[cs.pair_vb] == j)
        fc = fa[on_a].sum(axis=0) - fa[on_b].sum(axis=0)
        load = float(np.hypot(*fb))
        if load <= 0.0:
            continue
        rows.append(float(np.hypot(*(fb + fc))) / load)
    if not rows:
        return None
    return {"max_rel": max(rows), "per_magnet_rel": rows}
