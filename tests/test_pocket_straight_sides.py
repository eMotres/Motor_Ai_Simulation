"""With ``rotor_hole = 1`` the rotor iron face beside a magnet is ONE straight line.

Why (user, 2026-09-06, two zoomed pictures of the Ø200 pocket's top corner):

    "при Rotor Hole = 1 грань ротора у кармана должна быть всегда прямой"

and, on the zero-gap picture, "кусок ротора, который будет давать опять жуткие
перегрузки; нужно сделать грань ротора прямой".  The two defects he showed:

  * ``magnet_up_gap = 1`` — the rectangular opening cut had VERTICAL sides while
    the magnet's side edge is slanted, so where the cut met the magnet's rounded
    corner the iron kept a small step / tooth;
  * ``magnet_up_gap = 0`` — the pocket equalled the FILLETED magnet, so iron
    filled the magnet's 2.5 mm corner fillet and ended in a sharp wedge between
    the fillet arc and the sleeve bore.

Both are the same defect, and the requirement covers BOTH gaps.  The build
answers it by making the pocket the UNFILLETED magnet outline:

  * ``magnet_up_gap = 0`` — the magnet reaches the rim, so the two side edges
    run straight on to the rotor OD,

        [mp1, mp2, E3, <OD ring stations strictly between E3 and E4>, E4, mp5, mp6]

    and the iron face runs mp2 → OD with no intermediate vertex at all;
  * ``magnet_up_gap > 0`` — the pocket STOPS at the magnet's top arc and the
    ``magnet_up_gap`` of iron above it is left in place as one continuous
    bridge joined to both pole pieces (user 2026-09-21, section (h) below).
    The face is then the magnet's own side edge, mp2 → mp3, which is the same
    single straight line by construction.

The needle-island pitfall this file also pins (measured 2026-09-06 before the
fix: four 0.0025 mm² islands and a rotor that came out of the difference as FIVE
pieces): the rotor annulus's own rim is the 256-gon `_circle_points` polyline,
whose chords lie a sagitta INSIDE the true circle.  E3/E4 taken on the analytic
circle therefore sit OUTSIDE the rim polygon, and the pocket's end chord then
crosses the rim somewhere else, stranding slivers of iron.  `_pocket_corner_on_od`
lands the corners on the POLYLINE instead, and every station inside the span is
emitted (no corner-skip guard, unlike the magnet's own top arc).

``rotor_hole < 1`` is a different request — a NARROWER opening than the magnet —
and is untouched: the rectangle path is pinned here byte for byte.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pytest
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

import motor_ai_sim.cadquery_geometry as cg
from motor_ai_sim.cadquery_geometry import CadQueryMotor, _circle_points

# The Ø200 12s/10p sleeved machine the complaint was made on, pinned field by
# field — it must NOT come from config/motor_config.yaml, which the user edits
# constantly (same reasoning as tests/test_magnet_top_arc.py).
GEO_200 = {
    "stator_diameter": 200.0, "slot_height": 25.1, "core_thickness": 11.2,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 5,
    "air_gap": 1.6, "tooth_width": 23.3, "tooth2_width": 12.9, "cut_width": 10.0,
    "insulation_thickness": 0.25, "wire_width": 9.0, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.13, "num_wires_per_slot": 27,
    "wire_split": 1, "wire_parallel": 3, "slot_hs": 0.2,
    "magnet_height": 34.0, "rotor_house_height": 6.0, "shaft_height": 3.0,
    "magnet_fill_down": 0.9, "magnet_fill_up": 0.4502, "magnet_fill_radius": 2.5,
    "magnet_up_gap": 0.0, "rotor_hole": 1.0, "magnet_down_height": 9.2,
    "magnet_lamination": 5, "stator_fillet_r": 5.0, "stator_fillet_r1": 0.2,
    "rotor_fill_r": 0.2, "motor_length": 160.0, "sleeve_thickness": 1.0,
}

#: The two gaps the user showed pictures of.
GAPS = [0.0, 1.0]

#: …split by what the pocket DOES at that gap (user 2026-09-21, after the Ø50
#: CIANO14 50 / L15 refused to solve: «мне нужно сделать запас
#: magnet_up_gap = 0.1, чтобы магниты не выскочили наружу … эта пара должна
#: работать при любом значении magnet_up_gap»).
#:
#:   * ``magnet_up_gap = 0`` — the magnet reaches the rim, so the pocket is run
#:     OUT to it and is open at the OD.  Everything in sections (a)-(d) below is
#:     about THAT pocket and is parametrised on `OPEN_GAPS`.
#:   * ``magnet_up_gap > 0`` — the pocket stops at the magnet's top arc and an
#:     iron BRIDGE of that thickness closes it, joined to both pole pieces
#:     (`cadquery_geometry._pocket_bridge`).  Section (h) is about that one.
OPEN_GAPS = [0.0]
BRIDGE_GAPS = [0.05, 1.0]

#: Point-merge tolerance of the ring sanitiser: machine diameter / 4000 = 0.05 mm
#: here.  An OD station landing this close to a pocket corner is welded into it,
#: which moves the corner along the RIM by up to that much — the face stays one
#: straight segment, it just ends 50 µm to the side.  Anything the tests match by
#: identity against a freshly computed corner has to allow it.
def _weld_tol(motor) -> float:
    return 2.0 * float(motor.parameters["stator_outer_radius"]) / cg._WELD_DIV


def _build(geo, angle=0.0):
    motor = CadQueryMotor()
    motor.set_parameters(dict(geo))
    return motor, motor.get_2d_polygons(rotor_angle_deg=angle)


def _pole_geometry(motor, gap, angle):
    """``(mag_local, rotor_or, num_poles, pole_angle_rad, theta_of_pole_0)``.

    Rebuilt here from the mapped parameters rather than imported from the
    builder, so the test measures the shape against the DEFINITION (the six
    magnet landmarks) and not against the same expression twice.
    """
    p = motor.parameters
    n = int(p["num_poles"])
    rotor_or = float(p["rotor_outer_radius"])
    rotor_ir = float(p["rotor_inner_radius"])
    pole = 2.0 * math.pi / n
    a_dn = pole * float(p["magnet_fill_down"]) / 2.0
    a_up = pole * float(p["magnet_fill_up"]) / 2.0
    magnet_r = rotor_ir + float(p["rotor_house_height"])
    dn = float(p["magnet_down_height"])
    r_top = rotor_or - gap
    mag_local = [
        (magnet_r * math.sin(a_dn), magnet_r * math.cos(a_dn)),                  # mp1
        ((magnet_r + dn) * math.sin(a_dn), (magnet_r + dn) * math.cos(a_dn)),    # mp2
        (r_top * math.sin(a_up), r_top * math.cos(a_up)),                        # mp3
        (-r_top * math.sin(a_up), r_top * math.cos(a_up)),                       # mp4
        (-(magnet_r + dn) * math.sin(a_dn), (magnet_r + dn) * math.cos(a_dn)),   # mp5
        (-magnet_r * math.sin(a_dn), magnet_r * math.cos(a_dn)),                 # mp6
    ]
    # get_2d_polygons' own zero-position convention (ZERO_OFFSET_DEG + the
    # requested rotor angle).
    theta0 = math.radians(-(90.0 - (360.0 / n) * 0.5) + angle)
    return mag_local, rotor_or, n, pole, theta0


def _rot(x, y, a):
    c, s = math.cos(a), math.sin(a)
    return (x * c - y * s, x * s + y * c)


def _faces(motor, polys, gap, angle):
    """Yield ``(pole, side, mp_low, corner_on_od)`` for all 2·num_poles faces."""
    mag_local, rotor_or, n, pole, theta0 = _pole_geometry(motor, gap, angle)
    for i in range(n):
        a = i * pole + theta0
        pts = cg._extended_pocket_pts(mag_local, rotor_or, a)
        g = [_rot(x, y, a) for x, y in mag_local]
        yield i, 0, np.array(g[1]), np.array(pts[2])     # +side: mp2 → E3
        yield i, 1, np.array(g[4]), np.array(pts[-3])    # −side: mp5 → E4


def _rings(poly):
    geoms = list(poly.geoms) if poly.geom_type == "MultiPolygon" else [poly]
    return [np.asarray(g.exterior.coords[:-1], float) for g in geoms]


def _walk_face(ring, mp_low, corner):
    """The boundary path from ``mp_low`` to the rotor vertex nearest ``corner``.

    Walks the ring in BOTH directions and keeps the shorter approach, so what
    comes back is the actual iron face — not a chord through the polygon, and not
    the long way round the rotor.
    """
    d_low = np.hypot(ring[:, 0] - mp_low[0], ring[:, 1] - mp_low[1])
    i0 = int(d_low.argmin())
    if d_low[i0] > 1e-9:
        return None
    N = len(ring)
    best = None
    for step in (+1, -1):
        for k in range(1, 16):
            q = ring[(i0 + step * k) % N]
            d = math.hypot(q[0] - corner[0], q[1] - corner[1])
            if best is None or d < best[0]:
                best = (d, step, k)
    d_end, step, k = best
    return d_end, np.asarray([ring[(i0 + step * j) % N] for j in range(k + 1)])


# ══════════════════════════════════════════════════════════════════════════
#  (a) the iron face is one straight line — the user's actual requirement
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("gap", OPEN_GAPS)
@pytest.mark.parametrize("angle", [0.0, 3.3, 7.5])
def test_the_pocket_wall_is_drawn_as_one_straight_line(gap, angle):
    """The DEFINITION: every point of the pocket outline between the magnet's
    lower corner and the OD lies on the line between them, to 1e-6 · rotor_or.

    This is the user's requirement stated on the shape the builder draws
    (`_extended_pocket_pts` plus the magnet's own nodes spliced onto the side —
    see `_side_nodes_from_magnet`), before shapely and the ring sanitiser touch
    anything: no step, no wedge, no rounded corner, no kink of any kind.
    """
    motor, _polys = _build(dict(GEO_200, magnet_up_gap=gap), angle)
    mag_local, rotor_or, n, pole, theta0 = _pole_geometry(motor, gap, angle)
    lim = 1e-6 * rotor_or
    _m2, polys0 = _build(dict(GEO_200, magnet_up_gap=gap, rotor_fill_r=0.0), angle)
    worst = 0.0
    for i in range(n):
        a = i * pole + theta0
        base = cg._extended_pocket_pts(mag_local, rotor_or, a)
        mp = polys0["magnets"][i][0]
        for low, corner in ((np.array(base[1]), np.array(base[2])),
                            (np.array(base[-2]), np.array(base[-3]))):
            v = corner - low
            u = v / np.hypot(*v)
            nodes = cg._side_nodes_from_magnet(low, corner, mp,
                                               2.0 * rotor_or * 1e-9)
            for q in nodes:
                rel = np.array(q) - low
                worst = max(worst, abs(rel[0] * (-u[1]) + rel[1] * u[0]))
    assert worst < lim, (
        f"the pocket wall bends {worst:.3e} mm away from the straight line "
        f"(limit {lim:.3e} mm)")


@pytest.mark.parametrize("gap", OPEN_GAPS)
@pytest.mark.parametrize("angle", [0.0, 3.3, 7.5])
def test_the_built_iron_face_is_straight_to_the_builders_own_tolerance(gap, angle):
    """And the BUILT rotor: mp2 → OD with (almost always) nothing in between.

    "Almost": the ring sanitiser's point-weld (machine diameter / 4000 = 0.05 mm
    here) can merge the pocket's OD corner with the rim station next to it, which
    tilts the last stretch of the face.  That stretch is short — the magnet's own
    nodes are spliced into the pocket wall precisely so the tilt cannot reach
    further down than the magnet's corner fillet — and the whole face stays
    inside the weld tolerance of the ideal line.  Two assertions, both needed:
    the face is ONE segment on the poles the weld does not touch (so this is not
    a test that would pass on a face full of little steps), and NO vertex on any
    face is further off the line than the tolerance the builder is allowed
    anywhere.

    rotor_fill_r = 0 here: the deliberate pole-tip rounding has its own test.
    """
    geo = dict(GEO_200, magnet_up_gap=gap, rotor_fill_r=0.0)
    motor, polys = _build(geo, angle)
    rings = _rings(polys["rotor"])
    tol = _weld_tol(motor)
    seen = exact = 0
    worst = 0.0
    for i, side, mp_low, corner in _faces(motor, polys, gap, angle):
        hit = None
        for ring in rings:
            hit = _walk_face(ring, mp_low, corner)
            if hit is not None:
                break
        assert hit is not None, (
            f"pole {i} side {side}: the magnet's lower corner "
            f"mp{2 if side == 0 else 5} is not a vertex of the rotor at all")
        d_end, path = hit
        assert d_end <= tol, (
            f"pole {i} side {side}: the face ends {d_end:.6f} mm from the OD "
            f"corner E{3 if side == 0 else 4} — more than the {tol:.4f} mm weld "
            f"tolerance, so the pocket does not reach the rim on the side line")
        v = corner - mp_low
        u = v / np.hypot(*v)
        rel = path - mp_low
        perp = np.abs(rel[:, 0] * (-u[1]) + rel[:, 1] * u[0])
        assert perp.max() <= tol, (
            f"pole {i} side {side}: a face vertex sits {perp.max():.6f} mm off "
            f"the straight line — that is a step, not a weld (user 2026-09-06)")
        worst = max(worst, float(perp.max()))
        exact += len(path) == 2
        seen += 1
    assert seen == 2 * int(motor.parameters["num_poles"])
    assert exact >= 0.7 * seen, (
        f"only {exact} of {seen} iron faces are a single segment — the pocket "
        f"wall is being rebuilt, not welded (worst offset {worst:.6f} mm)")


@pytest.mark.parametrize("gap", OPEN_GAPS)
def test_rotor_fill_r_rounds_only_the_pole_tip_not_the_face(gap):
    """``rotor_fill_r`` is the user's own air-gap tip rounding and stays.

    Two properties, and no guess about how far a tangent arc reaches (the fillet
    core measures the tangent length ALONG the boundary, so a sharp pole tip
    legitimately eats a couple of mm of a 25 mm face):

      * nothing leaves the line by more than rotor_fill_r, and
      * whatever does leave it is ONE run of vertices ending at the OD.

    A bump in the middle of the face, with straight line on both sides of it,
    would be the step the user complained about coming back under another name.
    """
    rfr = float(GEO_200["rotor_fill_r"])
    geo = dict(GEO_200, magnet_up_gap=gap)
    motor, polys = _build(geo)
    rings = _rings(polys["rotor"])
    lim = 1e-6 * float(motor.parameters["rotor_outer_radius"])
    for i, side, mp_low, corner in _faces(motor, polys, gap, 0.0):
        for ring in rings:
            hit = _walk_face(ring, mp_low, corner)
            if hit is None:
                continue
            _d, path = hit
            v = corner - mp_low
            L = float(np.hypot(*v))
            u = v / L
            rel = path - mp_low
            perp = np.abs(rel[:, 0] * (-u[1]) + rel[:, 1] * u[0])
            off = [q > lim for q in perp[1:-1]]
            assert max(perp[1:-1], default=0.0) <= rfr + 1e-9, (
                f"pole {i} side {side}: a face vertex is "
                f"{max(perp[1:-1]):.4f} mm off the line, more than "
                f"rotor_fill_r = {rfr} mm")
            if any(off):
                first = off.index(True)
                assert all(off[first:]), (
                    f"pole {i} side {side}: the face leaves the line, comes "
                    f"back to it and leaves again — that is a step, not a "
                    f"rounded pole tip: {np.round(perp, 5).tolist()}")
            break


# ══════════════════════════════════════════════════════════════════════════
#  (b) no needle islands — the pitfall that killed the circle-intersection try
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("gap", OPEN_GAPS)
@pytest.mark.parametrize("angle", [0.0, 3.3, 7.5, 18.0])
def test_the_rotor_comes_out_of_the_difference_as_one_clean_body(gap, angle):
    """One Polygon, one interior (the bore), and no crumbs.

    With the corners taken on the analytic circle instead of the rim POLYLINE
    this was five pieces with four 0.0025 mm² islands (2026-09-06).
    """
    motor, polys = _build(dict(GEO_200, magnet_up_gap=gap), angle)
    rotor = polys["rotor"]
    pieces = list(rotor.geoms) if rotor.geom_type == "MultiPolygon" else [rotor]
    tiny = [pc.area for pc in pieces if pc.area < 0.01]
    assert not tiny, f"needle/island pieces in the rotor: {tiny} mm²"
    assert rotor.geom_type == "Polygon", (
        f"the pockets split the rotor into {len(pieces)} pieces")
    assert len(pieces[0].interiors) == 1, (
        f"the rotor has {len(pieces[0].interiors)} interior rings — exactly one "
        f"(the shaft bore) is expected; an extra one is a trapped void")
    assert rotor.is_valid


@pytest.mark.parametrize("gap", GAPS)
def test_no_magnet_pokes_into_the_rotor_iron(gap):
    """The pocket contains the magnet by construction; assert it (2026-08-24)."""
    _motor, polys = _build(dict(GEO_200, magnet_up_gap=gap))
    mags = unary_union([mp for mp, _ in polys["magnets"]])
    assert polys["rotor"].intersection(mags).area < 1e-6


@pytest.mark.parametrize("gap", [0.0, 0.5, 1.0, 2.0])
@pytest.mark.parametrize("angle", [0.0, 1.0, 5.0, 18.0])
def test_no_magnet_is_buried_in_the_iron_at_any_rotor_angle(gap, angle):
    """The pocket wall and the magnet's side edge stay the same line — swept.

    Found 2026-09-06 while pinning this pocket, and the reason
    `_side_nodes_from_magnet` exists: the ring sanitiser welds each domain on its
    own, and the pocket's OD corner can land within the weld tolerance (0.05 mm
    here) of a rim station.  Merging those two rotates the segment the corner
    ends — and with a bare ``[mp2, E3]`` outline that is the WHOLE 25 mm iron
    face, so it sweeps across the magnet's side edge.  Measured before the fix:
    0.856 mm² of magnet inside the iron at magnet_up_gap = 0, rotor angle 1°,
    i.e. 18 × the 0.048 mm² the static-3D validator refused on 2026-08-24.

    Swept over rotor angles on purpose: the corner's position against the 256-gon
    station grid changes with the angle, so the defect hides at 0° and appears at
    1°.  The rotor-side air is checked at the same time — a domain the weld
    shrinks out from under is just as wrong as one it grows.
    """
    _motor, polys = _build(dict(GEO_200, magnet_up_gap=gap), angle)
    mags = unary_union([mp for mp, _ in polys["magnets"]])
    assert polys["rotor"].intersection(mags).area < 1e-9, (
        "a magnet is buried in the rotor iron")
    assert polys["in_band"].intersection(polys["rotor"]).area < 1e-9
    assert polys["in_band"].intersection(mags).area < 1e-9
    assert polys["shaft"].intersection(polys["rotor"]).area < 1e-9


# ══════════════════════════════════════════════════════════════════════════
#  (c) the pocket top runs on the rotor OD ring's own stations
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("gap", OPEN_GAPS)
def test_the_pocket_top_is_the_od_ring_and_skips_no_station(gap):
    """Every point of the hole's top is either a `_circle_points` vertex or one
    of the two corners, the corners are ON the rim polyline (not outside it), and
    no station inside the span is dropped — a dropped one is the island."""
    motor, _polys = _build(dict(GEO_200, magnet_up_gap=gap))
    mag_local, rotor_or, n, pole, theta0 = _pole_geometry(motor, gap, 0.0)
    ring = np.asarray(_circle_points(rotor_or), float)
    rim = Polygon(_circle_points(rotor_or))
    step = 2.0 * math.pi / cg._OD_STATIONS
    for i in range(n):
        a = i * pole + theta0
        pts = np.asarray(cg._extended_pocket_pts(mag_local, rotor_or, a), float)
        e3, e4 = pts[2], pts[-3]
        arc = pts[3:-3]
        # the corners are ON the polyline the rim is drawn with, to the µm — the
        # analytic circle would put them a sagitta (4.8 µm here) outside it
        for name, e in (("E3", e3), ("E4", e4)):
            d = rim.exterior.distance(Point(e))
            assert d < 1e-9, f"pole {i}: {name} is {d:.3e} mm off the rim polyline"
            assert rim.buffer(1e-9).covers(Point(e)), (
                f"pole {i}: {name} lies OUTSIDE the rotor rim polygon — the "
                f"pocket's end chord will cross the rim and strand a needle")
        # every intermediate vertex IS a rim vertex
        for q in arc:
            d = np.hypot(ring[:, 0] - q[0], ring[:, 1] - q[1]).min()
            assert d < 1e-9, f"pole {i}: pocket-top vertex {q} is not a rim vertex"
        # and none inside the span is missing
        k3 = math.atan2(e3[1], e3[0]) / step
        k4 = math.atan2(e4[1], e4[0]) / step
        while k4 < k3:
            k4 += cg._OD_STATIONS
        want = [k for k in range(int(math.floor(k3)) + 1, int(math.ceil(k4)))]
        assert len(arc) == len(want), (
            f"pole {i}: the pocket top carries {len(arc)} stations, the span "
            f"({k3:.3f}, {k4:.3f}) holds {len(want)} — a skipped station leaves "
            f"a rim vertex stranded outside the pocket")


# ══════════════════════════════════════════════════════════════════════════
#  (d) what the pocket holds beyond the magnet, and that it is AIR
# ══════════════════════════════════════════════════════════════════════════

def _hole_and_magnets(motor, polys, gap, angle=0.0):
    mag_local, rotor_or, n, pole, theta0 = _pole_geometry(motor, gap, angle)
    holes, unfilleted = [], []
    a_up = pole * float(motor.parameters["magnet_fill_up"]) / 2.0
    for i in range(n):
        a = i * pole + theta0
        holes.append(Polygon(cg._extended_pocket_pts(mag_local, rotor_or, a)))
        g = [_rot(x, y, a) for x, y in mag_local]
        unfilleted.append(Polygon(
            g[:3] + cg._magnet_top_arc_global(rotor_or - gap, a_up, a) + g[3:]))
    return holes, unfilleted


@pytest.mark.parametrize("gap", OPEN_GAPS)
def test_the_pocket_is_the_magnet_plus_the_fillet_corners_plus_the_crescent(gap):
    """area(hole) − area(magnet) = the two corner fillets + the up_gap crescent.

    The fillet term is the EXACT unfilleted-minus-filleted area (the closed form
    2·(1 − π/4)·r_f² does not apply: the core caps the tangent length, so the
    realised radius is 2.36 mm rather than the nominal 2.5).  The crescent term
    is measured independently, as the part of the hole outside the circle
    r = rotor_or − magnet_up_gap.
    """
    geo = dict(GEO_200, magnet_up_gap=gap, rotor_fill_r=0.0)
    motor, polys = _build(geo)
    rotor_or = float(motor.parameters["rotor_outer_radius"])
    holes, unfilleted = _hole_and_magnets(motor, polys, gap)
    top_disk = Polygon(_circle_points(rotor_or - gap)) if gap > 0 else None

    a_hole = sum(h.area for h in holes)
    a_mag = sum(mp.area for mp, _ in polys["magnets"])
    a_unf = sum(u.area for u in unfilleted)
    a_fillets = a_unf - a_mag
    assert a_fillets > 0.0, "the corner fillets took no material"

    if top_disk is None:
        # magnet_up_gap = 0: there is no crescent, the pocket beyond the magnet
        # is the two corner fillets and nothing else
        assert abs((a_hole - a_mag) - a_fillets) < 0.01 * a_fillets, (
            f"hole − magnet = {a_hole - a_mag:.4f} mm², the corner fillets are "
            f"{a_fillets:.4f} mm² and at zero gap there is nothing else")
    else:
        a_cresc = sum(h.difference(top_disk).area for h in holes)
        assert a_cresc > 0.0
        want = a_fillets + a_cresc
        assert (a_hole - a_mag) == pytest.approx(want, rel=0.01), (
            f"hole − magnet = {a_hole - a_mag:.4f} mm², fillets "
            f"{a_fillets:.4f} + crescent {a_cresc:.4f} = {want:.4f} mm²")


@pytest.mark.parametrize("gap", OPEN_GAPS)
def test_everything_between_the_magnet_and_the_pocket_wall_is_air(gap):
    """The pocket remainder must be neither iron nor a void.

    ``in_band`` is ``disk − rotor − magnets − shaft − sleeve``, so it picks the
    remainder up for free — this asserts it actually does, to 1 %, and that the
    fillet corners in particular are covered by air and by nothing else.
    """
    geo = dict(GEO_200, magnet_up_gap=gap, rotor_fill_r=0.0)
    motor, polys = _build(geo)
    rotor_or = float(motor.parameters["rotor_outer_radius"])
    rotor_ir = float(motor.parameters["rotor_inner_radius"])
    holes, _unf = _hole_and_magnets(motor, polys, gap)
    annulus = Polygon(_circle_points(rotor_or), [_circle_points(rotor_ir)])

    mags = unary_union([mp for mp, _ in polys["magnets"]])
    air = polys["in_band"]
    pocket_air = air.intersection(annulus)
    want = sum(h.area for h in holes) - mags.area
    assert pocket_air.area == pytest.approx(want, rel=0.01), (
        f"the rotor-side air inside the annulus is {pocket_air.area:.4f} mm², "
        f"the pocket remainder is {want:.4f} mm² — some of it became iron or a "
        f"void")
    assert air.intersection(polys["rotor"]).area < 1e-6, "air overlaps the iron"
    assert air.intersection(mags).area < 1e-6, "air overlaps a magnet"

    # and the corner region itself, where the user saw the wedge of iron: every
    # piece of (pocket − magnet) is air, not iron and not a void
    # Eroded by 0.02 mm first: what survives is a REGION, not one of the micron
    # slivers the per-domain point-weld leaves along a shared edge (those are
    # measured and reported separately — worst 0.08 mm² here, and worse on the
    # rectangle path, which is the sanitiser's own business, not the pocket's).
    probed = 0
    for i, h in enumerate(holes):
        rest = h.difference(mags).buffer(-0.02)
        if rest.is_empty:
            continue
        for piece in (rest.geoms if rest.geom_type == "MultiPolygon" else [rest]):
            if piece.is_empty or piece.area < 1e-4:
                continue
            q = piece.representative_point()
            assert not polys["rotor"].covers(q), (
                f"pole {i}: iron at {q.x:.3f},{q.y:.3f} inside the pocket — the "
                f"wedge the user pointed at (2026-09-06)")
            assert air.covers(q), (
                f"pole {i}: the point {q.x:.3f},{q.y:.3f} in the pocket belongs "
                f"to no body at all — a void, not air")
            probed += 1
    assert probed >= len(holes), (
        f"only {probed} pocket regions probed for {len(holes)} poles")


# ══════════════════════════════════════════════════════════════════════════
#  (e) rotor_hole < 1 — the rectangle path, untouched
# ══════════════════════════════════════════════════════════════════════════

#: sha1 of the rotor polygon's rings, coordinates rounded to 1e-9 mm.
#: rotor_hole = 0.7 is a different request — a NARROWER opening than the magnet —
#: and the rectangle that expresses it is untouched by the straight-sided pocket.
#: Regenerate ONLY together with a deliberate change to the rotor_hole < 1 path;
#: a diff here after a shapely/GEOS upgrade is worth reading before it is blessed.
#:
#: Both were taken from the build of 2026-09-06 and are the values it produced
#: BEFORE the straight-sided pocket existed: the rectangle path came through the
#: change untouched, which is the point of pinning it.
_RECT_SNAPSHOT = {
    # gap 0 re-pinned 2026-09-07: a magnet that SEATS ON THE SLEEVE now ends its
    # corner fillets with a short chord at ≥ 12° to the bore instead of a
    # tangent (cadquery_geometry._open_fillet_at_top — the 0° air cusp at the
    # tangent point made Triangle fan micro-elements there; user: "обрати
    # внимание на углы магнитов").  The rectangle path itself is untouched: the
    # pocket hole is built from the same magnet polygon, so its hash follows
    # the magnet.  Rotor area 3997.148409 mm², gap 1 unchanged.
    # Same value after the opening learned to judge the chord with the ring
    # sanitiser's weld slack and to skip duplicate junction vertices (later the
    # same day): one vertex per corner is dropped here, as before.
    0.0: "5265023dc55a2e61ab62bd89960bdf06ce51b3eb",
    1.0: "9ebbd0aa08b9eb1c7a31a43181ca3ed5a6a24b24",
}


def _coord_hash(poly):
    def rnd(seq):
        return [(round(x, 9), round(y, 9)) for x, y in seq]
    rings = [rnd(poly.exterior.coords)] + [rnd(r.coords) for r in poly.interiors]
    return hashlib.sha1(json.dumps(rings).encode()).hexdigest()


@pytest.mark.parametrize("gap", GAPS)
def test_rotor_hole_below_one_is_the_untouched_rectangle_path(gap):
    geo = dict(GEO_200, rotor_hole=0.7, magnet_up_gap=gap)
    motor, polys = _build(geo)
    assert not cg._extended_pocket(motor.parameters), (
        "rotor_hole = 0.7 must NOT take the straight-sided pocket")
    # the opening depth rule is the pre-existing one: gap + 2 mm of overlap,
    # clamped to the magnet height
    assert cg._pocket_cut_depth(motor.parameters) == pytest.approx(
        min(float(geo["magnet_height"]), gap + 2.0))
    assert _coord_hash(polys["rotor"]) == _RECT_SNAPSHOT[gap], (
        f"the rotor_hole = 0.7 rotor changed (area {polys['rotor'].area:.9f} "
        f"mm²) — the rectangle path must be bit-identical")


def test_the_switch_is_rotor_hole_one():
    for v, want in ((0.0, False), (0.7, False), (0.999, False),
                    (1.0, True), (1.5, True), (None, False), ("", False)):
        assert cg._extended_pocket({"rotor_hole": v}) is want, v
    # and the rectangle depth is zeroed on the extended path so a stale caller
    # cannot re-introduce an opening cut
    assert cg._pocket_cut_depth(
        {"rotor_hole": 1.0, "magnet_height": 34.0, "magnet_up_gap": 1.0}) == 0.0


def test_the_second_switch_is_magnet_up_gap():
    """Inside ``rotor_hole >= 1``, ``magnet_up_gap`` chooses bridge or open rim.

    `_pocket_bridge` is the bridge THICKNESS (so a caller can draw it) and
    `_pocket_open_to_od` is the "run the sides out to the rim" behaviour every
    consumer actually asks for.  `rotor_hole < 1` is neither: it keeps its
    rectangle and has no bridge of its own to report."""
    for hole, gap, bridge, open_od in (
            (1.0, 0.0, 0.0, True),      # the Ø200 recipe — unchanged
            (1.0, 1e-12, 0.0, True),    # numerically zero is zero
            (1.0, 0.05, 0.05, False),
            (1.0, 0.1, 0.1, False),
            (1.5, 1.0, 1.0, False),
            (0.9, 0.1, 0.0, False),     # rectangle path: no bridge, no rim run
            (0.9, 0.0, 0.0, False),
            (1.0, None, 0.0, True),
            (1.0, "", 0.0, True)):
        p = {"rotor_hole": hole, "magnet_up_gap": gap}
        assert cg._pocket_bridge(p) == pytest.approx(bridge), p
        assert cg._pocket_open_to_od(p) is open_od, p


# ══════════════════════════════════════════════════════════════════════════
#  (f) + (g) the consumers: validator, mesher, and the 3-D solid
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("gap", GAPS)
def test_the_validator_accepts_the_straight_sided_pocket(gap):
    from motor_ai_sim.geometry_validation import validate_geometry

    res = validate_geometry(dict(GEO_200, magnet_up_gap=gap))
    errs = [v for v in res.violations if v.severity == "error"]
    assert not errs, "\n".join(v.message for v in errs)


@pytest.mark.parametrize("gap", GAPS)
@pytest.mark.slow
def test_the_mesher_builds_on_the_straight_sided_pocket(gap):
    from motor_ai_sim.simulation.mesher import build_mesh_from_polygons

    motor, polys = _build(dict(GEO_200, magnet_up_gap=gap))
    mesh, _tags = build_mesh_from_polygons(
        polys, rotor_angle_deg=0.0, mesh_size_mm=4.0, min_size_mm=0.3,
        normal_deviation_deg=8.0, geo_cfg=motor.parameters,
        outer_air_factor=1.2, gap_layers=1.0, n_sectors=1)[:2]
    assert mesh.t.shape[1] > 1000
    assert np.isfinite(mesh.p).all()


@pytest.mark.parametrize("gap", GAPS)
def test_the_three_d_rotor_is_the_same_solid_as_the_two_d_one(gap):
    """The 3-D rotor's volume is the 2-D iron area × the stack length.

    At ``magnet_up_gap = 0`` `_create_rotor` cuts the whole extended pocket
    itself; with a BRIDGE it cuts nothing and the magnets are removed by
    `build_all`'s ``rotor.cut(magnet)`` — the same two steps `rotor_hole < 1`
    has always taken.  Either way the finished solid is the 2-D cross-section,
    and a rectangle left in one of the paths shows up here as a percent-level
    gap."""
    cq = pytest.importorskip("cadquery")
    motor, polys = _build(dict(GEO_200, magnet_up_gap=gap))
    rotor = motor._create_rotor(cq)
    if cg._pocket_bridge(motor.parameters) > 0.0:
        for magnet in motor._create_magnets(cq):
            rotor = rotor.cut(magnet)
    volume = rotor.val().Volume()
    want = polys["rotor"].area * float(motor.parameters["motor_length"])
    assert volume == pytest.approx(want, rel=0.005), (
        f"3-D rotor {volume:.1f} mm³ vs 2-D area × length {want:.1f} mm³ "
        f"({100 * (volume / want - 1):+.3f} %)")


@pytest.mark.parametrize("gap", GAPS)
def test_the_mesh_tab_rotor_is_the_same_body_as_the_solver_rotor(gap):
    """get_2d_mesh_data is the third build site; it must cut the same pocket."""
    motor, polys = _build(dict(GEO_200, magnet_up_gap=gap))
    data = motor.get_2d_mesh_data()
    assert "rotor_core" in data
    verts = np.asarray(data["rotor_core"]["vertices"], float)[:, :2]
    faces = np.asarray(data["rotor_core"]["faces"], int).reshape(-1, 3)
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    u, v = b - a, c - a
    area = 0.5 * np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]).sum()
    assert area == pytest.approx(polys["rotor"].area, rel=0.005), (
        f"the Mesh tab rotor is {area:.3f} mm², the solver rotor "
        f"{polys['rotor'].area:.3f} mm² — the two build sites disagree")


# ─────────────────────────────────────────────────────────────────────────────
# 2026-09-06, later the same day.  Two defects found on the live Ø200 after the
# straight-sided pocket landed:
#   * a zero-width IRON NEEDLE ~1.2 mm long at every pocket corner (interior
#     angle 0.0° at θ = 97.03°): the corner E3/E4 sits ~1e-15 mm off the OD
#     chord, so `rotor_disk.difference(hole)` kept the station and walked back
#     to the corner along the same chord.  Fixed by splicing the corners into
#     the rim ring itself (`_od_ring_with_pocket_corners`).
#   * the pole-tip fillet (rotor_fill_r) was NOT applied at magnet_up_gap = 0:
#     the search band was scaled by up_gap and collapsed to 0 (user: "ты забыл
#     применить это на углы ротора").  Fixed band on the straight-sided path.
# ─────────────────────────────────────────────────────────────────────────────

def _od_band_interior_angles(rotor, band_mm=0.5):
    cs = list(rotor.exterior.coords)[:-1]
    n = len(cs)
    od = max(math.hypot(x, y) for x, y in cs)
    out = []
    for i, (x, y) in enumerate(cs):
        if math.hypot(x, y) < od - band_mm:
            continue
        ax, ay = cs[i - 1]
        bx, by = cs[(i + 1) % n]
        v1 = (ax - x, ay - y)
        v2 = (bx - x, by - y)
        d = (v1[0] * v2[0] + v1[1] * v2[1]) / (math.hypot(*v1) * math.hypot(*v2) + 1e-300)
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, d)))))
    return out


@pytest.mark.parametrize("gap", OPEN_GAPS)
@pytest.mark.parametrize("angle", [0.0, 1.0, 3.3, 7.5])
def test_no_needle_at_the_pocket_corners(gap, angle):
    geo = dict(GEO_200, magnet_up_gap=gap)
    _, polys = _build(geo, angle)
    rotor = polys["rotor"]
    assert rotor.geom_type == "Polygon" and len(rotor.interiors) == 1
    angs = _od_band_interior_angles(rotor)
    assert angs, "no rim vertices found"
    # A needle is a vertex whose two edges leave in the same direction.
    assert min(angs) > 5.0, f"needle on the rim: min interior angle {min(angs):.2f}°"


@pytest.mark.parametrize("gap", OPEN_GAPS)
def test_the_pole_tips_are_rounded_by_rotor_fill_r(gap):
    sharp = _build(dict(GEO_200, magnet_up_gap=gap, rotor_fill_r=0.0))[1]["rotor"]
    round_ = _build(dict(GEO_200, magnet_up_gap=gap, rotor_fill_r=0.2))[1]["rotor"]
    n_sharp = sum(a < 120.0 for a in _od_band_interior_angles(sharp))
    n_round = sum(a < 120.0 for a in _od_band_interior_angles(round_))
    n_poles = 2 * GEO_200["num_seg"] * GEO_200["num_poles_per_segment"] // 2
    # Without the fillet every pocket corner is a sharp tip (one per side);
    # with it none is.
    assert n_sharp >= 2 * n_poles - 2, (n_sharp, n_poles)
    assert n_round == 0, f"{n_round} sharp pole tips survived rotor_fill_r"
    assert round_.area < sharp.area                # the fillet removes iron


# ══════════════════════════════════════════════════════════════════════════
#  (h) magnet_up_gap > 0 on a straight-sided pocket: the IRON BRIDGE
# ══════════════════════════════════════════════════════════════════════════
#
# User 2026-09-21, on CIANO14 50 edited / L15 (Ø50, 14 poles, rotor_hole 1,
# magnet_up_gap 0.1): «мне нужно сделать запас magnet_up_gap = 0.1, чтобы
# магниты не выскочили наружу, я должен проверить деформации» and «эта пара
# должна работать при любом значении magnet_up_gap».
#
# What it used to do: the pocket's sides were run out to the OD whatever the
# gap, so the retaining iron was cut away and the magnet's top edge stopped
# `magnet_up_gap` short of a rim the pocket had already opened.  That crescent
# of air ends in a ZERO-angle wedge at each side.  On the Ø50 at 0.1 mm the
# mechanical mesh came out with 22 triangles of 6e-19 mm² and a 0.00° minimum
# angle (6e-4 mm² / 3.70° at gap 0); the magnet locked in its pocket at
# ±105 MPa with every contact pair closed to 2e-16 µm, the rotor stopped
# deforming (0.05 µm against 6.23) and the bore reacted 0.5 % of the applied
# torque — `rotor_stress.RotorRanAway`, which is what the owner hit in
# production.
#
# What it does now: the pocket IS the magnet outline, so the `magnet_up_gap` of
# iron over the magnet stays where it was drawn — one continuous bridge per
# pole, joined to both pole pieces.  The sides are still straight (they are the
# magnet's own side edges) and the rim is a plain ring again.

#: The Ø50 the defect was found on: CIANO14 50 edited / L15, pinned field by
#: field for the same reason GEO_200 is.
GEO_50 = {
    "stator_diameter": 50.0, "slot_height": 7.5, "core_thickness": 2.4,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.25, "tooth_width": 4.2, "tooth2_width": 2.1, "cut_width": 1.2,
    "insulation_thickness": 0.06, "wire_width": 3.0, "wire_height": 0.5,
    "wire_spacing_x": 0.07, "wire_spacing_y": 0.1, "num_wires_per_slot": 11,
    "wire_split": 1, "wire_parallel": 1, "slot_hs": 0.267,
    "magnet_height": 7.0, "rotor_house_height": 1.3, "shaft_height": 2.0,
    "magnet_fill_down": 0.87, "magnet_fill_up": 0.34, "magnet_fill_radius": 0.2,
    "magnet_up_gap": 0.1, "rotor_hole": 1.0, "magnet_down_height": 1.5,
    "magnet_lamination": 0, "stator_fillet_r": 1.5, "stator_fillet_r1": 0.1,
    "rotor_fill_r": 0.2, "motor_length": 15.0, "sleeve_thickness": 0.0,
    "rotor_outer_radius": 14.85, "rotor_inner_radius": 6.55,
}

#: sha1 of the Ø200 rotor's rings at magnet_up_gap = 0 (`_coord_hash`).  This is
#: the `CIANO10 200 opt` recipe — rotor_hole 1, magnet_up_gap 0, sleeve 2.5 —
#: and the bridge must not move a single coordinate of it.  Taken from the build
#: of 2026-09-21 and checked against `git show HEAD:` before the change landed.
_OPEN_POCKET_SNAPSHOT = "cdd109485f291410f4aff29ce4b6f89310590966"


def test_the_zero_gap_pocket_is_bit_identical_to_the_open_build():
    """Constraint 1 of the bridge change: the Ø200 recipe does not move."""
    _motor, polys = _build(dict(GEO_200, magnet_up_gap=0.0))
    assert _coord_hash(polys["rotor"]) == _OPEN_POCKET_SNAPSHOT, (
        f"the magnet_up_gap = 0 rotor changed (area {polys['rotor'].area:.9f} "
        f"mm²) — the Ø200 straight-sided pocket must be bit-identical")


def _bridge_thickness(polys, rotor_or, n_poles):
    """(min, max) radial thickness of the iron over each magnet, in mm."""
    out = []
    for mp, _pol in polys["magnets"]:
        r_top = max(math.hypot(x, y) for x, y in mp.exterior.coords)
        out.append(rotor_or - r_top)
    assert len(out) == n_poles
    return min(out), max(out)


@pytest.mark.parametrize("geo,name", [(GEO_200, "200"), (GEO_50, "50")])
@pytest.mark.parametrize("gap", [0.05, 0.1, 0.3, 1.0])
def test_the_straight_pocket_is_closed_by_a_bridge_of_iron(geo, name, gap):
    """One rotor body, one closed pocket per pole, `gap` of iron over each.

    The bridge is asserted three ways, because "there is iron up there" is not
    enough — it has to be CONNECTED: the rotor is a single polygon (so the two
    pole pieces and the bridge are one body), the pocket is an interior ring of
    it (so the bridge closes over the magnet rather than leaving a slot), and a
    point at mid-bridge height on the pole axis is iron.
    """
    motor, polys = _build(dict(geo, magnet_up_gap=gap))
    p = motor.parameters
    rotor_or = float(p["rotor_outer_radius"])
    n = int(p["num_poles"])
    assert cg._pocket_bridge(p) == pytest.approx(gap)
    assert not cg._pocket_open_to_od(p)

    rotor = polys["rotor"]
    assert rotor.geom_type == "Polygon", (
        f"the bridged pockets split the rotor into "
        f"{len(rotor.geoms)} pieces — the bridge is not joined to the poles")
    assert rotor.is_valid
    assert len(rotor.interiors) == n + 1, (
        f"the rotor has {len(rotor.interiors)} interior rings — the shaft bore "
        f"plus one closed pocket per pole ({n + 1}) is what a bridge means; "
        f"fewer means a pocket still opens onto the rim")

    lo, hi = _bridge_thickness(polys, rotor_or, n)
    assert lo == pytest.approx(gap, abs=1e-6) and hi == pytest.approx(gap, abs=1e-6), (
        f"the iron over the magnets is {lo:.4f}…{hi:.4f} mm, not {gap} mm")

    # and it is IRON, at mid-thickness on every pole axis
    pole = 2.0 * math.pi / n
    theta0 = math.radians(-(90.0 - (360.0 / n) * 0.5))
    r_mid = rotor_or - 0.5 * gap
    for i in range(n):
        a = i * pole + theta0 + math.pi / 2.0
        q = Point(r_mid * math.cos(a), r_mid * math.sin(a))
        assert rotor.covers(q), (
            f"pole {i}: the middle of the bridge at r = {r_mid:.3f} mm is not "
            f"rotor iron — the pocket was still cut through to the rim")


@pytest.mark.parametrize("gap", BRIDGE_GAPS)
@pytest.mark.parametrize("angle", [0.0, 1.0, 3.3, 7.5])
def test_the_bridged_rim_is_a_plain_ring_with_no_needle(gap, angle):
    """No pocket reaches the rim, so the rim keeps its own stations and nothing
    sharper than a rounded pole tip is left on it."""
    _motor, polys = _build(dict(GEO_200, magnet_up_gap=gap), angle)
    rotor = polys["rotor"]
    angs = _od_band_interior_angles(rotor)
    assert angs, "no rim vertices found"
    assert min(angs) > 90.0, (
        f"a corner of {min(angs):.2f}° on the rim of a bridged rotor — the rim "
        f"should be the plain 256-gon")


@pytest.mark.parametrize("gap", BRIDGE_GAPS)
def test_the_iron_face_beside_a_bridged_magnet_is_still_one_straight_line(gap):
    """The 2026-09-06 requirement survives the bridge.

    With no rectangle and no extension the face IS the magnet's own side edge,
    mp2 → mp3, so it is straight by construction — asserted on the BUILT rotor
    (rotor_fill_r = 0, which has its own test) so a later sanitiser change
    cannot quietly put a step back.
    """
    motor, polys = _build(dict(GEO_200, magnet_up_gap=gap, rotor_fill_r=0.0))
    mag_local, rotor_or, n, pole, theta0 = _pole_geometry(motor, gap, 0.0)
    tol = _weld_tol(motor)
    mag_fill_r = float(motor.parameters["magnet_fill_radius"])
    rings = _rings(polys["rotor"]) + [
        np.asarray(r.coords[:-1], float) for r in polys["rotor"].interiors]
    seen = 0
    for i in range(n):
        a = i * pole + theta0
        g = [_rot(x, y, a) for x, y in mag_local]
        for low, top in ((np.array(g[1]), np.array(g[2])),
                         (np.array(g[4]), np.array(g[3]))):
            hit = None
            for ring in rings:
                hit = _walk_face(ring, low, top)
                if hit is not None:
                    break
            assert hit is not None, (
                f"pole {i}: the magnet's lower corner is not a vertex of the "
                f"rotor — the pocket is not the magnet outline")
            d_end, path = hit
            # The straight face ends where the magnet's CORNER FILLET begins,
            # so it stops short of mp3 by up to the realised fillet radius —
            # never further, or something else has taken over the face.
            assert d_end <= mag_fill_r + tol, (
                f"pole {i}: the face ends {d_end:.6f} mm from the magnet's top "
                f"corner, further than the {mag_fill_r} mm corner fillet")
            assert len(path) >= 2
            v = top - low
            u = v / np.hypot(*v)
            rel = path - low
            perp = np.abs(rel[:, 0] * (-u[1]) + rel[:, 1] * u[0])
            # Straight, then the corner fillet — and in that order.  Nothing
            # leaves the line by more than the fillet radius, and whatever does
            # leave it never comes back: a bump with straight line on both
            # sides of it is the step the user complained about (2026-09-06).
            assert perp.max() <= mag_fill_r + tol, (
                f"pole {i}: a face vertex sits {perp.max():.6f} mm off the "
                f"straight line, further than the {mag_fill_r} mm fillet")
            off = [q > tol for q in perp]
            if any(off):
                first = off.index(True)
                assert all(off[first:]), (
                    f"pole {i}: the face leaves the straight line, comes back "
                    f"and leaves again — that is a step, not a corner fillet: "
                    f"{np.round(perp, 6).tolist()}")
                assert first >= 2, (
                    f"pole {i}: the face bends at its second vertex "
                    f"({np.round(perp, 6).tolist()}) — there is no straight "
                    f"iron face beside the magnet at all")
            seen += 1
    assert seen == 2 * n


#: ``rotor_hole`` × ``magnet_up_gap`` — the matrix the owner asked for
#: (2026-09-21: «эта пара должна работать при любом значении magnet_up_gap»).
#:
#: ``(0.9, 0.0)`` is marked: an opening NARROWER than a magnet that is flush
#: with the rotor OD leaves retaining tabs of zero thickness, and the sanitiser
#: welds 5.594e-4 mm² of the Ø50's magnets into the iron there.  Measured
#: identical on HEAD (2401d16) and after the bridge change — it is the
#: rectangle path's own degenerate corner, not this one's, and it is listed
#: here rather than hidden so the next person meets it.
HOLE_GAP_MATRIX = [
    pytest.param(0.9, 0.0, marks=pytest.mark.xfail(
        strict=True, reason="rotor_hole < 1 with the magnet flush at the OD: "
                            "zero-thickness tabs, 5.6e-4 mm² of magnet welded "
                            "into the iron — pre-existing, see AGENTS notes")),
    (0.9, 0.05), (0.9, 0.1), (0.9, 0.3),
    (1.0, 0.0), (1.0, 0.05), (1.0, 0.1), (1.0, 0.3),
]


@pytest.mark.parametrize("hole,gap", HOLE_GAP_MATRIX)
def test_every_hole_gap_pair_builds_one_rotor_with_no_sliver(hole, gap):
    """The matrix the owner asked for: ANY magnet_up_gap on either path.

    One rotor body, no crumb pieces, no magnet in the iron, and no ring of the
    rotor with two vertices closer together than a micron — the needle that
    turns into a zero-area element in the mesh.
    """
    _motor, polys = _build(dict(GEO_50, rotor_hole=hole, magnet_up_gap=gap))
    rotor = polys["rotor"]
    pieces = list(rotor.geoms) if rotor.geom_type == "MultiPolygon" else [rotor]
    assert rotor.geom_type == "Polygon", (
        f"the rotor came out as {len(pieces)} pieces "
        f"{[round(q.area, 6) for q in pieces]}")
    assert rotor.is_valid and rotor.area > 0.0
    mags = unary_union([mp for mp, _ in polys["magnets"]])
    assert rotor.intersection(mags).area < 1e-9, "a magnet is buried in the iron"
    for ring in [rotor.exterior] + list(rotor.interiors):
        q = np.asarray(ring.coords[:-1], float)
        d = np.hypot(*(np.roll(q, -1, axis=0) - q).T)
        assert d.min() > 1e-6, (
            f"two rotor vertices are {d.min():.3e} mm apart — a needle")
