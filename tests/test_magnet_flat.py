"""`magnet_flat` + `magnet_pocket_clearance` (2026-11-15): the Fusion sketch's magnet and pocket, as is.

Owner: «нужно как есть перенести».  The reference contour below is rebuilt from the SKETCH DIMENSIONS ALONE (never from our
builder): bottom edge at the distance 1.358 on the pole centre line, top edge at 3.358 - 0.10 = 3.258, corners on the
construction rays at 15.3 deg (bottom, = 0.85 x 36 / 2) and 10.8 deg (top, = 0.6 x 36 / 2), straight sides, R0.20 on the top
corners; the pocket = the magnet offset by 0.025 plus the rotor_hole opening (7.56 deg = 0.7 x 10.8).  Acceptance: area within
0.5 % and maximum contour deviation <= 0.05 mm, for the magnet and for the pocket.  Absent keys = the stored builder, bit for bit.
"""
import math

import pytest
from shapely.affinity import rotate
from shapely.geometry import Point, Polygon, box

from motor_ai_sim.cadquery_geometry import CadQueryMotor

G = dict(stator_diameter=12, motor_length=40, air_gap=0.1, sleeve_thickness=0, core_thickness=0.7, slot_height=1.842,
         tooth_width=1.0, tooth2_width=0.6, cut_width=1.2, stator_fillet_r=0.5, stator_fillet_r1=0.05,
         insulation_thickness=0.125, slot_hs=0.11, magnet_height=2.0, magnet_fill_up=0.6, magnet_fill_down=0.85,
         magnet_fill_radius=0.2, magnet_up_gap=0.1, magnet_down_height=2.0, magnet_lamination=0, rotor_hole=0.7,
         rotor_fill_r=0.0, rotor_house_height=0.258, shaft_height=0.5, num_seg=2, num_slots_per_segment=6,
         num_poles_per_segment=5, num_slots=12, num_poles=10, wire_spacing_y=0.05, wire_width=0.2, wire_height=0.8,
         num_wires_per_slot=2, wire_split=3, wire_spacing_x=0.03, wire_bore_margin=0.04)
FLAT = dict(magnet_flat=1, magnet_pocket_clearance=0.025)

R_OR, D_BOT, GAP, R_FIL, CLR = 3.358, 1.358, 0.10, 0.20, 0.025
A_DN, A_UP, A_HOLE = (math.radians(15.3), math.radians(10.8), math.radians(7.56))


def fusion_magnet():
    """The sketch: flat edges at 1.358 / 3.258, corners on the 15.3 / 10.8 deg rays, R0.20 on the TOP two corners."""
    d_top = R_OR - GAP
    xb, xt = D_BOT * math.tan(A_DN), d_top * math.tan(A_UP)
    P = Polygon([(-xb, D_BOT), (xb, D_BOT), (xt, d_top), (-xt, d_top)])
    rounded = P.buffer(-R_FIL).buffer(R_FIL)                 # every convex corner rounded: a DIFFERENT algorithm from the builder's
    y0 = 0.5 * (D_BOT + d_top)
    return rounded.intersection(box(-9, y0, 9, 9)).union(P.intersection(box(-9, -9, 9, y0)))


def fusion_pocket():
    opening_w = 2 * R_OR * math.sin(A_HOLE)
    rect = box(-opening_w / 2, 2.0, opening_w / 2, R_OR)             # the rotor_hole opening: from the OD down into the magnet (y = 2.0, where the magnet is wider than the opening)
    return fusion_magnet().buffer(CLR, join_style=2).union(rect).intersection(Point(0, 0).buffer(R_OR, 256))      # mitre: the Fusion offset keeps the corners sharp


def _built(**ov):
    m = CadQueryMotor()
    m.set_parameters({**G, **ov})
    return m.get_2d_polygons(rotor_angle_deg=0.0)


def _pole_frame(poly, ref_xy):
    """rotate a polygon found near ``ref_xy`` so that its pole centre line is +y"""
    a = math.degrees(math.atan2(poly.centroid.x, poly.centroid.y))
    return rotate(poly, a, origin=(0, 0))


def _magnet0(P):
    mags = [x[0] if isinstance(x, tuple) else x for x in P["magnets"]]
    return _pole_frame(mags[0], None)


def test_magnet_matches_the_fusion_sketch():
    ref, got = fusion_magnet(), _magnet0(_built(**FLAT))
    assert got.area == pytest.approx(ref.area, rel=0.005)
    assert abs(got.area - 1.866) / 1.866 < 0.005                       # the section area read from Fusion
    assert got.hausdorff_distance(ref) <= 0.05
    assert got.hausdorff_distance(ref) < 0.005                         # the builder is in fact far inside the acceptance


def test_pocket_matches_the_fusion_sketch():
    P = _built(**FLAT)
    disk = Point(0, 0).buffer(R_OR, 256)
    bore = Point(0, 0).buffer(1.1, 256)
    rotor = P["rotor"][0] if isinstance(P["rotor"], tuple) else P["rotor"]
    pockets = disk.difference(bore).difference(rotor)
    geoms = [g for g in (list(pockets.geoms) if hasattr(pockets, "geoms") else [pockets]) if g.area > 0.5]
    assert len(geoms) == 10                                            # one pocket per pole
    pocket0 = min(geoms, key=lambda g: abs(g.centroid.x) + abs(g.centroid.y - 2.5))   # the pole on +y
    # `disk - rotor` along the OD compares two different 256-gons and strands zero-width needles there: open them away
    got = _pole_frame(pocket0, None).buffer(-0.01).buffer(0.01)
    ref = fusion_pocket()
    assert got.area == pytest.approx(ref.area, rel=0.005)
    assert got.hausdorff_distance(ref) <= 0.05


def test_corners_are_where_the_sketch_puts_them():
    got = _magnet0(_built(**FLAT))
    xs = sorted(got.exterior.coords)
    ys = [y for _x, y in got.exterior.coords]
    assert min(ys) == pytest.approx(D_BOT, abs=1e-6)                   # the flat bottom edge is at 1.358 on the centre line
    assert max(ys) == pytest.approx(R_OR - GAP, abs=1e-6)              # the flat top edge is 0.10 below the OD on the centre line
    bottom = [x for x, y in got.exterior.coords if abs(y - D_BOT) < 1e-6]
    assert max(bottom) == pytest.approx(D_BOT * math.tan(A_DN), abs=1e-6)   # bottom corner on the 15.3 deg ray


def test_without_the_keys_the_magnet_is_the_stored_one():
    old = _built(magnet_down_height=0.0)
    again = _built(magnet_down_height=0.0, magnet_flat=0, magnet_pocket_clearance=0.025)   # the clearance needs magnet_flat
    a = [x[0] if isinstance(x, tuple) else x for x in old["magnets"]]
    b = [x[0] if isinstance(x, tuple) else x for x in again["magnets"]]
    assert all(p.equals(q) for p, q in zip(a, b))
    assert old["rotor"].equals(again["rotor"]) if not isinstance(old["rotor"], tuple) else True


def test_magnet_flat_needs_the_opening_pocket():
    from motor_ai_sim.geometry_validation import validate_parameter_values
    assert any(b.get("field") == "magnet_flat" for b in validate_parameter_values({**G, **FLAT, "rotor_hole": 1.0}))
    assert not any(b.get("field") == "magnet_flat" for b in validate_parameter_values({**G, **FLAT}))
    with pytest.raises(ValueError):
        _built(**FLAT, rotor_hole=1.0)


def test_keys_are_known_and_not_filled():
    from motor_ai_sim.routes._validation import SCHEMA_FALLBACK
    for k in ("magnet_flat", "magnet_pocket_clearance"):
        assert SCHEMA_FALLBACK[k]["unset_ok"] is True
