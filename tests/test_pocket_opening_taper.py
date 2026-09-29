"""rotor_hole < 1: the pocket OPENING must never leave the magnet outline.

Owner, 2026-09-25 («проверь геометрию, там с ротором косяки»): the Ø12
12s10p rotor imported from his Fusion model came out as 31 pieces of iron.
Two things stacked up; this file pins the builder half.

The opening above each magnet is a rectangle of half-width
w = rotor_or·sin(pole·fill_up·rotor_hole/2) cut from the OD down
``magnet_up_gap + 2 mm`` (clamped to the magnet height).  That fixed 2 mm was
written for magnets tens of mm tall.  On a 2 mm magnet it runs the 0.88 mm
opening down to the magnet's 0.72 mm-wide inner end: the rectangle's corners
stand 0.08 mm outside the magnet on both sides, i.e. in the 0.13 mm iron web
between two neighbouring pockets, and every spoke comes off the hub.  That is
the 2026-09-05 "косяк внизу магнитов" again, which the 2 mm overlap was meant
to have fixed.  `_pocket_cut_depth` now also stops where the magnet's side
becomes narrower than the opening; where it never does (every machine built
before) the depth is exactly what it was.

The other half was magnet_down_height: Fusion's ``mag_down_h`` = 2 mm is NOT
our radial foot (the owner's picture has a plain trapezoid, i.e. a 0 foot), so
0 must be an admissible value.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

import motor_ai_sim.cadquery_geometry as cg
from motor_ai_sim.cadquery_geometry import CadQueryMotor, _circle_points

# The owner's Fusion Ø12 12s10p (CIANO14 12_40 Ø12 12s10p / L10), pinned field
# by field — never from config/motor_config.yaml, which the user edits.
GEO_12 = {
    "stator_diameter": 12.0, "slot_height": 1.842, "core_thickness": 0.7,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 5,
    "air_gap": 0.1, "tooth_width": 1.0, "tooth2_width": 0.5, "cut_width": 1.0,
    "insulation_thickness": 0.05, "wire_width": 0.8, "wire_height": 0.2,
    "wire_spacing_x": 0.05, "wire_spacing_y": 0.05, "num_wires_per_slot": 3,
    "wire_split": 1, "wire_parallel": 1, "slot_hs": 0.267,
    "magnet_height": 2.0, "rotor_house_height": 0.3, "shaft_height": 1.0,
    "magnet_fill_down": 0.85, "magnet_fill_up": 0.6, "magnet_fill_radius": 0.2,
    "magnet_up_gap": 0.1, "rotor_hole": 0.7, "magnet_down_height": 0.0,
    "magnet_lamination": 0, "stator_fillet_r": 0.5, "stator_fillet_r1": 0.05,
    "rotor_fill_r": 0.2, "motor_length": 40.0, "sleeve_thickness": 0,
}


def _build(geo, angle=0.0):
    m = CadQueryMotor()
    m.set_parameters(dict(geo))
    return m, m.get_2d_polygons(rotor_angle_deg=angle)


def _parts(g):
    if g is None or g.is_empty:
        return []
    return list(g.geoms) if isinstance(g, MultiPolygon) else [g]


def _old_depth(p):
    """HEAD before 2026-09-25, verbatim."""
    mag_h = float(p.get("magnet_height", 0.0) or 0.0)
    gap = float(p.get("magnet_up_gap", 0.0) or 0.0)
    if cg._extended_pocket(p):
        return 0.0
    return max(0.0, min(mag_h, gap + 2.0))


@pytest.mark.parametrize("angle", [0.0, 7.3, 18.0])
def test_owner_fusion_rotor_is_one_body(angle):
    """Magnet foot 0 (the owner's trapezoid) — the core is ONE piece, and
    it was eleven (hub + ten loose spokes) with the fixed 2 mm cut."""
    motor, polys = _build(GEO_12, angle)
    rp = [q for q in _parts(polys["rotor"]) if q.area > 1e-6]
    assert len(rp) == 1, [round(q.area, 5) for q in rp]


def test_pocket_air_only_above_the_magnet_top():
    """Every bit of pocket AIR (rotor disk − iron − magnets) sits in the
    opening band next to the OD — none beside the magnet's inner end."""
    motor, polys = _build(GEO_12)
    p = motor.parameters
    r_or, r_ir = float(p["rotor_outer_radius"]), float(p["rotor_inner_radius"])
    disk = Polygon(_circle_points(r_or), [_circle_points(r_ir)])
    air = disk.difference(unary_union([polys["rotor"]] + [m for m, _ in polys["magnets"]]))
    r_top = r_or - float(p["magnet_up_gap"])
    lowest = r_top - float(p["magnet_fill_radius"]) - 0.05
    bad = []
    for q in _parts(air):
        if q.area < 1e-6:
            continue
        r_min = min(math.hypot(x, y) for x, y in q.exterior.coords)
        if r_min < lowest:
            bad.append((round(q.area, 5), round(r_min, 3)))
    assert not bad, f"pocket air below the magnet top (area, r_min): {bad}"


def test_every_pocket_is_the_same_shape():
    """Rotation invariance: ten pockets, ten equal areas — up to the OD
    polyline's phase.  The rim is the 256-gon `_circle_points` and the magnet
    top arc is spliced from its GLOBAL stations (25.6 per pole here), so each
    pocket meets the rim at a different station phase: ≈ opening width ×
    sagitta = 0.88 mm × 2.5e-4 mm ≈ 2e-4 mm² per pocket, the same spread the
    magnets themselves carry (4.2e-4 mm² at 3°) with or without this fix.
    A per-pole difference in the clamp itself would be w × Δdepth, orders
    larger."""
    motor, polys = _build(GEO_12, 3.0)
    p = motor.parameters
    r_or, r_ir = float(p["rotor_outer_radius"]), float(p["rotor_inner_radius"])
    disk = Polygon(_circle_points(r_or), [_circle_points(r_ir)])
    holes = disk.difference(polys["rotor"])
    areas = sorted(q.area for q in _parts(holes) if q.area > 1e-4)
    assert len(areas) == 10
    sag = r_or * (1.0 - math.cos(math.pi / 256))
    assert areas[-1] - areas[0] < 4.0 * 0.9 * sag, areas


def test_as_imported_foot_keeps_spokes_on_the_hub():
    """With the literal Fusion value (foot 2 mm = the whole magnet) the
    magnet's radial side reaches the OD, so the iron above its slanted top
    face is cut off — 20 lip islands, which is a PARAMETER consequence.  But
    the spokes must stay on the hub: the core's big body carries hub + all
    ten spokes (it was hub alone before the clamp)."""
    motor, polys = _build(dict(GEO_12, magnet_down_height=2.0))
    p = motor.parameters
    r_or = float(p["rotor_outer_radius"])
    rp = sorted(_parts(polys["rotor"]), key=lambda q: -q.area)
    # one body carrying hub + all ten spokes (mid-spoke points between the
    # magnet centroids); everything else is a lip at the OD
    from shapely.geometry import Point
    body = rp[0]
    angs = sorted(math.atan2(m.centroid.y, m.centroid.x) for m, _ in polys["magnets"])
    r_mid = 0.5 * (r_or + float(p["rotor_inner_radius"]))
    for i, a in enumerate(angs):
        b = angs[(i + 1) % len(angs)] + (2 * math.pi if i == len(angs) - 1 else 0.0)
        s = 0.5 * (a + b)
        assert body.buffer(1e-9).contains(Point(r_mid * math.cos(s), r_mid * math.sin(s))), \
            f"spoke at {math.degrees(s):.1f} deg is not on the hub"
    for q in rp[1:]:
        r_min = min(math.hypot(x, y) for x, y in q.exterior.coords)
        assert r_min > r_or - 0.3, (round(q.area, 5), round(r_min, 3))


def test_depth_unchanged_where_the_magnet_is_wide_enough():
    """Every die in config/dies: where the old cut never left the magnet the
    new depth is bit-identical, so no existing machine moves."""
    root = Path(__file__).resolve().parents[1] / "config" / "dies"
    checked = 0
    for f in sorted(root.glob("*/die.yaml")):
        g = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("geometry")
        if not g or float(g.get("rotor_hole", 1.0) or 0.0) >= 1.0:
            continue
        m = CadQueryMotor()
        m.set_parameters(dict(g))
        p = m.parameters
        lim = cg._pocket_cut_depth_limit(p)
        old = _old_depth(p)
        if lim is None or lim >= old:
            assert cg._pocket_cut_depth(p) == old, f.parent.name
            checked += 1
    assert checked >= 1


def test_depth_clamp_on_the_owner_rotor():
    m = CadQueryMotor()
    m.set_parameters(dict(GEO_12))
    p = m.parameters
    assert _old_depth(p) == pytest.approx(2.0)
    d = cg._pocket_cut_depth(p)
    assert d < 2.0
    # the corner of the cut sits exactly on the magnet's slanted side
    n = int(p["num_poles"]); pole = 2 * math.pi / n
    w = p["rotor_outer_radius"] * math.sin(pole * p["magnet_fill_up"] * p["rotor_hole"] / 2)
    r0 = p["rotor_inner_radius"] + p["rotor_house_height"]
    a_dn = pole * p["magnet_fill_down"] / 2; a_up = pole * p["magnet_fill_up"] / 2
    x0, y0 = r0 * math.sin(a_dn), r0 * math.cos(a_dn)
    rt = p["rotor_outer_radius"] - p["magnet_up_gap"]
    x1, y1 = rt * math.sin(a_up), rt * math.cos(a_up)
    y_corner = p["rotor_outer_radius"] - d
    x_side = x0 + (y_corner - y0) * (x1 - x0) / (y1 - y0)
    assert x_side == pytest.approx(w, abs=1e-12)


def test_magnet_down_height_zero_is_admissible():
    from motor_ai_sim.geometry_validation import validate_parameter_values
    errs = [e for e in validate_parameter_values(dict(GEO_12))
            if e.get("field") == "magnet_down_height"]
    assert errs == []
    errs = [e for e in validate_parameter_values(dict(GEO_12, magnet_down_height=-0.1))
            if e.get("field") == "magnet_down_height"]
    assert errs, "a negative foot must still be refused"
