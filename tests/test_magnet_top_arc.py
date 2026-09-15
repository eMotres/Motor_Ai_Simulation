"""The magnet's outer edge is the ARC on r = rotor_or − magnet_up_gap.

Why (user, 2026-09-06): "я убрал всё, но у нас геометрия всё равно не
соединяется со сливом" — with ``magnet_up_gap = 0`` and ``rotor_hole = 1`` the
magnet STILL did not touch the retaining sleeve, because its top was the
straight CHORD between the two top corners.  On the Ø200 machine that chord sits
63.2 mm × (1 − cos 8.1°) ≈ 0.63 mm below the OD at the pole centre, and the only
two points that did touch — the corners — are then filleted away by
``magnet_fill_radius``.  The user's goal that day: the centrifugal load of the
magnets must press DIRECTLY on the carbon sleeve.

The chord was briefly a `magnet_top: flat | arc` choice.  The user closed it the
same day — "давай по умолчанию сделаем только arc и уберём прямую вообще" — so
there is one magnet top now and no knob.  What is asserted here:

1. **The arc lands on the sleeve bore's own vertices.**  Not "close to the OD":
   the top vertices must BE vertices of ``_circle_points(rotor_or)``, the fixed
   256-gon that draws the rotor OD and the sleeve bore.  Anything else leaves
   micron slivers of air in the contact (the very air the user is removing) and
   needle triangles for gmsh.
2. **The contact is real** — between the two corner fillets the magnet, not air,
   is what the sleeve bore sits on.
3. **The fillets come AFTER the arc** and are tangent to it, taking material
   only from the magnet.
4. **The area is the chord's plus the circular segment**, checked against
   r²/2·(θ − sin θ) — the segment measured against an explicitly built chord,
   not against a second code path.
5. **The mesher and the geometry validator accept it**, on every preset.
6. **The knob is gone**, and a stale ``magnet_top`` key left in an old die,
   preset or catalog entry is ignored in silence — never an error, never a
   different polygon, never written back.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from motor_ai_sim.cadquery_geometry import CadQueryMotor, _circle_points

_ROOT = Path(__file__).resolve().parents[1]
_PRESETS = json.loads((_ROOT / "config" / "motor_presets.json").read_text(encoding="utf-8"))

# The Ø200 12s/10p sleeved machine the complaint was made on, pinned field by
# field.  It must NOT come from config/motor_config.yaml — the user edits that
# constantly (same reasoning as tests/test_cad_polygon_quality.py).  The two
# values that matter for the complaint are magnet_up_gap = 0 (the user "removed
# everything") and rotor_hole = 1 (the pocket opening as wide as the magnet).
GEO_200_SLEEVED = {
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

#: A vertex "is a vertex of the sleeve bore ring" at this tolerance.  The arc
#: stations are computed with the SAME expression `_circle_points` uses, so the
#: real agreement is a few ulp; 1e-6 mm is the task's bound, four orders looser.
_ON_RING_MM = 1e-6

_PRESET_CASES = [
    ("200 mm sleeved (the machine the complaint was made on)", GEO_200_SLEEVED),
    ("preset my_40mm_last", _PRESETS["my_40mm_last"]["geometry"]),
    ("preset ciano14_30_10", _PRESETS["ciano14_30_10"]["geometry"]),
    ("preset motor_100mm", _PRESETS["motor_100mm"]["geometry"]),
    ("preset m200_20kw_base", _PRESETS["m200_20kw_base"]["geometry"]),
]


def _build(geo, angle=0.0):
    motor = CadQueryMotor()
    motor.set_parameters(dict(geo))
    return motor, motor.get_2d_polygons(rotor_angle_deg=angle)


def _top_vertices(mag_poly, r_top):
    """The magnet vertices sitting on the top circle."""
    P = np.asarray(mag_poly.exterior.coords[:-1], float)
    r = np.hypot(P[:, 0], P[:, 1])
    return P[np.abs(r - r_top) < _ON_RING_MM]


# ─────────────── 1. the arc IS the sleeve bore (up_gap = 0, hole = 1) ─────────

def test_arc_top_vertices_are_vertices_of_the_sleeve_bore_ring():
    motor, polys = _build(GEO_200_SLEEVED)
    rotor_or = float(motor.parameters["rotor_outer_radius"])
    ring = np.asarray(_circle_points(rotor_or), float)

    assert polys.get("sleeve") is not None, "this fixture has a sleeve"
    n_on = 0
    for i, (mp, _pol) in enumerate(polys["magnets"]):
        P = np.asarray(mp.exterior.coords[:-1], float)
        r = np.hypot(P[:, 0], P[:, 1])
        # nothing may poke THROUGH the bore into the sleeve
        assert r.max() <= rotor_or + 1e-9, (
            f"magnet {i} reaches r={r.max():.9f} mm past the rotor OD "
            f"{rotor_or:.9f} mm — it is inside the sleeve")
        top = P[np.abs(r - rotor_or) < _ON_RING_MM]
        assert len(top) >= 3, (
            f"magnet {i} touches the OD at only {len(top)} vertices — with "
            f"magnet_up_gap = 0 the whole top edge must lie on it")
        n_on += len(top)
        # every one of them is a vertex of the 256-gon the bore is drawn with
        for pt in top:
            d = np.hypot(ring[:, 0] - pt[0], ring[:, 1] - pt[1]).min()
            assert d < _ON_RING_MM, (
                f"magnet {i}: top vertex {pt} is {d:.3e} mm off the nearest "
                f"sleeve-bore vertex — the two curves do not share nodes")
    assert n_on >= 3 * len(polys["magnets"])


def test_arc_top_leaves_no_air_between_the_magnet_and_the_sleeve():
    """Between the two corner fillets the sleeve bore rests on MAGNET, not air.

    Sampled just inside and just outside the bore: inside must be magnet, and
    outside must be sleeve, for every angle of the contact span.
    """
    motor, polys = _build(GEO_200_SLEEVED)
    rotor_or = float(motor.parameters["rotor_outer_radius"])
    sleeve = polys["sleeve"]
    solids = unary_union([polys["rotor"], sleeve]
                         + [mp for mp, _ in polys["magnets"]])

    # Probe depth.  Both curves are the SAME 256-gon, so "just inside the
    # circle" is not the test: near a chord's midpoint the polygon runs a
    # sagitta below the true circle (62.1 mm × (1 − cos 0.703°) = 4.7 µm here).
    # 10 µm clears that and is still 60x finer than the feature being checked.
    eps = 0.01
    for i, (mp, _pol) in enumerate(polys["magnets"]):
        top = _top_vertices(mp, rotor_or)
        ang = np.sort(np.arctan2(top[:, 1], top[:, 0]))
        # the contact span is between the OUTERMOST stations the fillets left
        for t in np.linspace(0.02, 0.98, 25):
            th = ang[0] + t * (ang[-1] - ang[0])
            p_in = Point((rotor_or - eps) * math.cos(th),
                         (rotor_or - eps) * math.sin(th))
            p_out = Point((rotor_or + eps) * math.cos(th),
                          (rotor_or + eps) * math.sin(th))
            assert mp.covers(p_in), (
                f"magnet {i}: air (not magnet) just under the bore at "
                f"θ={math.degrees(th):.3f}° — the load does not reach the sleeve")
            assert sleeve.covers(p_out), (
                f"magnet {i}: the sleeve does not cover θ="
                f"{math.degrees(th):.3f}° just outside the bore")
            assert solids.covers(p_in) and solids.covers(p_out)

        # and no air BODY survives in that span: the rotor-side air region must
        # not reach the bore anywhere between the fillet corners
        in_band = polys.get("in_band")
        if in_band is not None and not in_band.is_empty:
            for t in np.linspace(0.02, 0.98, 25):
                th = ang[0] + t * (ang[-1] - ang[0])
                q = Point((rotor_or - eps) * math.cos(th),
                          (rotor_or - eps) * math.sin(th))
                assert not in_band.covers(q), (
                    f"magnet {i}: an air polygon touches the OD at "
                    f"θ={math.degrees(th):.3f}°")


def test_arc_is_concentric_below_the_od_when_the_gap_is_not_zero():
    """magnet_up_gap = 0.5 → the arc is the circle at rotor_or − 0.5, not the OD."""
    gap = 0.5
    motor, polys = _build(dict(GEO_200_SLEEVED, magnet_up_gap=gap))
    rotor_or = float(motor.parameters["rotor_outer_radius"])
    r_top = rotor_or - gap
    for i, (mp, _pol) in enumerate(polys["magnets"]):
        P = np.asarray(mp.exterior.coords[:-1], float)
        r = np.hypot(P[:, 0], P[:, 1])
        assert r.max() <= r_top + 1e-9, (
            f"magnet {i} reaches {r.max():.9f} mm, above rotor_or − gap "
            f"= {r_top:.9f} mm")
        top = P[np.abs(r - r_top) < _ON_RING_MM]
        assert len(top) >= 3
        assert np.allclose(np.hypot(top[:, 0], top[:, 1]), r_top, atol=_ON_RING_MM)
        # concentric with the OD, i.e. the SAME angular stations
        k = np.degrees(np.arctan2(top[:, 1], top[:, 0])) / (360.0 / 256.0)
        assert np.abs(k - np.round(k)).max() < 1e-9, (
            "the arc left the OD ring's angular stations once the gap is > 0")


# ─────────────────── 2. the fillets, applied AFTER the arc ───────────────────

def test_corner_fillets_are_tangent_to_the_arc_and_only_remove_material():
    """The two top corners are rounded on the finished arc outline.

    Order matters: filleting a chord and then bending it onto the circle would
    put the rounding somewhere else entirely.  Tangency is measured as
    |d(centre) − (r_top − r_fillet)| — the fillet lands in the top CHORD it
    meets rather than on the ideal circle, so the bound is 0.02 mm, not zero.
    """
    r_f = float(GEO_200_SLEEVED["magnet_fill_radius"])
    # Measured with the magnet 0.5 mm BELOW the sleeve bore: a magnet that
    # seats on the bore (up_gap 0) deliberately ends its fillets with a short
    # chord at >= 12 deg to the bore (`_open_fillet_at_top`, 2026-09-07), so
    # tangency to the top arc is the contract only when the top is free air.
    _gap = 0.5
    motor, polys = _build(dict(GEO_200_SLEEVED, magnet_up_gap=_gap))
    rotor_or = float(motor.parameters["rotor_outer_radius"]) - _gap
    # the same outline with no fillet: the fillet may only take material away
    _m0, polys0 = _build(dict(GEO_200_SLEEVED, magnet_up_gap=_gap, magnet_fill_radius=0.0))

    worst = 0.0
    for i, ((mp, _p), (mp0, _p0)) in enumerate(
            zip(polys["magnets"], polys0["magnets"])):
        assert mp.area < mp0.area, f"magnet {i}: the fillet added material"
        assert mp0.buffer(1e-9).covers(mp), (
            f"magnet {i}: the rounded outline leaves the raw one — a fillet "
            f"that bulges out puts the magnet inside the rotor iron "
            f"(incident 2026-08-24)")
        # the rounded corner: vertices off the top circle and off the straight
        # side walls, within a fillet radius of the arc's end
        top = _top_vertices(mp, rotor_or)
        assert len(top) >= 3, f"magnet {i}: the fillets ate the whole arc"
        ends = top[np.argsort(np.arctan2(top[:, 1], top[:, 0]))][[0, -1]]
        P = np.asarray(mp.exterior.coords[:-1], float)
        for end in ends:
            near = P[np.hypot(P[:, 0] - end[0], P[:, 1] - end[1]) < 2.5 * r_f]
            arc_pts = near[np.abs(np.hypot(near[:, 0], near[:, 1]) - rotor_or)
                           > _ON_RING_MM]
            if len(arc_pts) < 3:
                continue
            # least-squares circle through the rounding
            A = np.c_[2 * arc_pts[:, 0], 2 * arc_pts[:, 1], np.ones(len(arc_pts))]
            (cx, cy, c0), *_ = np.linalg.lstsq(A, (arc_pts ** 2).sum(1), rcond=None)
            d = math.hypot(cx, cy)
            worst = max(worst, abs(d - (rotor_or - math.sqrt(c0 + cx * cx + cy * cy))))
    assert worst < 0.02, (
        f"the fillet centre sits {worst:.4f} mm off the (r_top − r_fillet) "
        f"circle — it is not tangent to the arc")


# ───────────────────────── 3. the area is the segment ────────────────────────

def test_the_magnet_top_adds_exactly_the_circular_segment_over_the_chord():
    """Area check with the corner fillets OFF, against an EXPLICIT chord.

    The old flat build is gone, so the reference is constructed here — the
    hexagon mp1..mp6 — and the delta must be the circular segment,
    A_seg = r²/2 · (θ − sin θ),  θ = 2 · half-angle.
    """
    base = dict(GEO_200_SLEEVED, magnet_fill_radius=0.0)
    motor, polys = _build(base)
    rotor_or = float(motor.parameters["rotor_outer_radius"])
    n_poles = int(motor.parameters["num_poles"])
    a_up = math.radians(360.0 / n_poles * base["magnet_fill_up"] / 2.0)
    theta = 2.0 * a_up
    want = n_poles * rotor_or ** 2 / 2.0 * (theta - math.sin(theta))

    # the chord-topped reference, built from the same landmarks the CAD uses
    p = motor.parameters
    magnet_r = float(p["rotor_inner_radius"]) + float(p["rotor_house_height"])
    dn = float(p["magnet_down_height"])
    a_dn = math.radians(360.0 / n_poles * base["magnet_fill_down"] / 2.0)
    r_top = rotor_or - float(base["magnet_up_gap"])
    hexa = Polygon([
        (magnet_r * math.sin(a_dn), magnet_r * math.cos(a_dn)),
        ((magnet_r + dn) * math.sin(a_dn), (magnet_r + dn) * math.cos(a_dn)),
        (r_top * math.sin(a_up), r_top * math.cos(a_up)),
        (-r_top * math.sin(a_up), r_top * math.cos(a_up)),
        (-(magnet_r + dn) * math.sin(a_dn), (magnet_r + dn) * math.cos(a_dn)),
        (-magnet_r * math.sin(a_dn), magnet_r * math.cos(a_dn)),
    ])
    got = sum(mp.area for mp, _ in polys["magnets"]) - n_poles * hexa.area
    assert got == pytest.approx(want, rel=0.01), (
        f"the magnet top carries {got:.4f} mm² more than the chord, the "
        f"circular segment is {want:.4f} mm² ({100 * (got / want - 1):+.2f} %)")

    # Where the segment comes FROM: at rotor_hole = 1 the pocket opening is
    # already cut all the way to the OD across the magnet's full width, so that
    # crescent was AIR, not iron — which is exactly the user's complaint.  The
    # rotor-side air must therefore be short by the same crescent: the pocket
    # air is bounded below by the magnet, so growing the magnet shrinks it.
    air_over_chord = unary_union(
        [Polygon(np.asarray(mp.exterior.coords)) for mp, _ in polys["magnets"]]
    ).area - n_poles * hexa.area
    assert air_over_chord == pytest.approx(want, rel=0.01)
    assert polys["in_band"].intersection(
        unary_union([mp for mp, _ in polys["magnets"]])).area < 1e-6, (
        "an air polygon overlaps a magnet — the crescent was taken twice")

    from motor_ai_sim.masses import cad_areas_m2

    a = cad_areas_m2(base)
    assert a and a["magnet"] > 0.0
    assert a["magnet"] == pytest.approx(
        sum(mp.area for mp, _ in polys["magnets"]) * 1e-6, rel=1e-9), (
        "cad_areas_m2 does not measure the polygons the mesher gets")


def test_the_rotor_iron_gives_up_nothing_to_the_magnet_top():
    """The crescent under the sleeve is pocket AIR at rotor_hole = 1, so the
    magnet may take it — but it must not eat rotor iron to get it."""
    base = dict(GEO_200_SLEEVED, magnet_fill_radius=0.0)
    _m, polys = _build(base)
    rotor = polys["rotor"]
    for i, (mp, _pol) in enumerate(polys["magnets"]):
        overlap = rotor.intersection(mp).area
        assert overlap < 1e-6, (
            f"magnet {i} overlaps the rotor iron by {overlap:.6f} mm²")


# ─────────────────────── 4. the mesher and the validator ─────────────────────

@pytest.mark.parametrize("name,geo", _PRESET_CASES, ids=[c[0] for c in _PRESET_CASES])
@pytest.mark.parametrize("angle", [0.0, 7.5])
def test_validator_accepts_every_preset_with_the_arc_top(name, geo, angle):
    """Every machine in the presets file is now arc-topped, whether it was saved
    before 2026-09-06 or after.  None of them may become unbuildable."""
    from motor_ai_sim.geometry_validation import validate_geometry

    res = validate_geometry(dict(geo))
    errs = [v for v in res.violations if v.severity == "error"]
    assert not errs, f"{name}: " + "\n".join(v.message for v in errs)
    # and the polygons themselves build at both rotor angles
    _motor, polys = _build(geo, angle)
    assert polys.get("magnets"), f"{name} @ {angle}°: no magnets"
    for mp, _pol in polys["magnets"]:
        assert mp.is_valid and not mp.is_empty and mp.area > 0.0


@pytest.mark.slow
def test_fem_mesh_builds_on_the_arc_topped_magnet():
    from motor_ai_sim.simulation.mesher import build_mesh_from_polygons

    motor, polys = _build(GEO_200_SLEEVED)
    mesh, _tags = build_mesh_from_polygons(
        polys, rotor_angle_deg=0.0, mesh_size_mm=4.0, min_size_mm=0.3,
        normal_deviation_deg=8.0, geo_cfg=motor.parameters,
        outer_air_factor=1.2, gap_layers=1.0, n_sectors=1)[:2]
    assert mesh.t.shape[1] > 1000
    assert np.isfinite(mesh.p).all()


# ───────────────────────── 5. the knob is gone ───────────────────────────────

def test_the_knob_is_not_in_the_served_schema_any_more():
    """The user removed the choice on 2026-09-06 ("уберём прямую вообще"), so
    the Geometry tab must not offer it."""
    from motor_ai_sim.routes._validation import (
        SCHEMA_FALLBACK,
        geometry_schema_meta,
        known_geometry_keys,
    )

    assert "magnet_top" not in SCHEMA_FALLBACK
    assert "magnet_top" not in geometry_schema_meta()
    assert "magnet_top" not in known_geometry_keys()


def test_a_stale_magnet_top_key_is_ignored_in_silence():
    """A die, preset or catalog entry saved during the few hours the choice
    existed still carries the key.  It must not error, must not change a single
    polygon, and must not come back out as a geometry field."""
    from motor_ai_sim.geometry_validation import validate_parameter_values
    from motor_ai_sim.routes._validation import check_unknown_geometry_keys

    ref = _build(GEO_200_SLEEVED)[1]
    for stale in ("flat", "arc", " Flat ", "nonsense", None, 0):
        geo = dict(GEO_200_SLEEVED, magnet_top=stale)
        # no field error, no unknown-field 422
        assert validate_parameter_values(geo) == [], f"magnet_top={stale!r}"
        assert check_unknown_geometry_keys({"magnet_top": stale}) == [], (
            f"magnet_top={stale!r} would 422 a machine saved on 2026-09-06")
        # and the geometry is bit-identical to the one without the key
        got = _build(geo)[1]
        assert [mp.wkt for mp, _ in got["magnets"]] == \
               [mp.wkt for mp, _ in ref["magnets"]], (
            f"magnet_top={stale!r} moved a magnet — the key must be inert")
        assert got["rotor"].wkt == ref["rotor"].wkt


def test_a_real_typo_is_still_rejected():
    """Retiring the knob must not have opened the door to any word: an unknown
    geometry key is still a 422 with the nearest real name."""
    from motor_ai_sim.routes._validation import check_unknown_geometry_keys

    bad = check_unknown_geometry_keys({"magnet_topp": "arc"})
    assert len(bad) == 1 and bad[0]["field"] == "magnet_topp"


def test_on_sleeve_fillets_leave_the_bore_at_a_finite_angle():
    """A magnet seated on the sleeve (up_gap 0) must not meet the bore
    tangentially: the air lens between the fillet and the bore ended in a 0 deg
    cusp that Triangle filled with a fan of micro-elements (user 2026-09-07,
    mesh view: "обрати внимание на углы магнитов, что-то тут не так").  The
    fillet now ends with a chord at >= 12 deg to the bore; a magnet with air
    above it (up_gap 0.5) keeps the tangent fillet."""
    def junction_angles(mp, r_top):
        P = np.asarray(mp.exterior.coords[:-1], float)
        n = len(P)
        r = np.hypot(P[:, 0], P[:, 1])
        top = np.abs(r - r_top) < 0.06          # weld tolerance, as in the builder
        out = []
        for i in range(n):
            if not top[i]:
                continue
            for s in (1, -1):
                j, b = (i + s) % n, (i - s) % n
                if top[j] or not top[b]:
                    continue
                t, v = P[i] - P[b], P[j] - P[i]
                out.append(math.degrees(math.acos(max(-1.0, min(1.0, float(t @ v) / (np.linalg.norm(t) * np.linalg.norm(v)))))))
        return out
    motor, polys = _build(dict(GEO_200_SLEEVED, magnet_up_gap=0.0))
    r_top = float(motor.parameters["rotor_outer_radius"])
    angs = [a for mp, _p in polys["magnets"] for a in junction_angles(mp, r_top)]
    assert angs and min(angs) >= 12.0, f"on-sleeve fillet meets the bore at {min(angs):.2f} deg"
    motor, polys = _build(dict(GEO_200_SLEEVED, magnet_up_gap=0.5))
    r_top = float(motor.parameters["rotor_outer_radius"]) - 0.5
    angs = [a for mp, _p in polys["magnets"] for a in junction_angles(mp, r_top)]
    # a tangent fillet arrives at half its chord angle (~8 deg on a 2.5 mm
    # fillet drawn with 16 deg chords); the on-sleeve chord is >= 12 deg
    assert angs and max(angs) < 10.0, f"free-top fillet is no longer tangent ({max(angs):.2f} deg)"
