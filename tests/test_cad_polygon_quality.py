"""Ring quality of the polygons the CAD builder hands to the mesher.

This is the survey that found the "fan of microscopic triangles on one side of a
tooth" bug, turned into a gate.  On the 150 mm 24s/28p machine the builder used
to emit:

* stator exterior: 676 points with **12 exact zero-length segments** (duplicate
  consecutive points, e.g. at (15.5755, -60.6056) and (44.3178, 43.9640)) plus
  slivers of 0.0055 / 0.0790 mm against a 0.399 mm median,
* rotor exterior: **456 of 1348** segments micro — the pole-tip fillets ran at
  0.0196 mm chords because every fillet was cut into a FIXED number of arc
  points regardless of its radius,
* a second rotor "polygon" that was three coincident points at
  (-39.8101, -39.8101) — zero area, pure junk, straight into gmsh.

gmsh honours every boundary point it is given, so each of those seeded a cluster
of degenerate elements.  The fixes live in ``cadquery_geometry``: sagitta-based
arc discretisation through one shared helper, tangent-arc fillets that consume
the vertices they swallow, and a final sanitize pass over every ring.

What is asserted, and why the obvious "min segment >= 5 % of the ring median"
rule is NOT the criterion:

1. **No point closer to its neighbour than the weld tolerance** (machine
   diameter / 4000).  This is the hard, absolute gate and it is the one that
   catches the original bug on the original machine — the zero-length segments
   trivially, and the 0.0196 mm rotor-tip chords too (weld is 0.0375 mm at
   150 mm).  It holds by construction after the sanitize pass, so a failure here
   means a code path started bypassing it.
2. **No degenerate ring** — every ring has >= 3 distinct points and an area
   above (1e-4 mm)^2, and no domain lost a body.
3. **Arc quality, measured against the LOCAL discretisation.**  A ring median is
   the wrong yardstick: the stator slot liner is a legitimate 0.15 mm x 7.81 mm
   band, so its shortest edge is 1.9 % of its own median and always will be —
   the geometry demands it.  Likewise the 40 mm bore ring legitimately carries a
   0.15 mm fillet (0.044 mm chords) next to a 12.1 mm bore (0.297 mm chords).
   What actually predicts a bad mesh is a segment that is short *compared with
   its neighbours*, so the bound is applied to the median of a +/-10 segment
   window and only to rings dense enough for that to mean anything (>= 64
   points).  Bound = 4 %; the worst real case across the five machines below is
   4.67 % (the 40 mm bore, where GEOS nodes the slot wedge against the bore
   circle 0.0139 mm from a bore vertex).  Before the fix the same measurement
   was 0 % on every machine (exact duplicates).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.cadquery_geometry import (
    _ARC_SAG_REL,
    _ARC_SAG_SCALE,
    _DEGEN_AREA_MM2,
    _WELD_DIV,
    CadQueryMotor,
    _arc_n_segments,
    _arc_points,
    _sanitize_ring,
)

_ROOT = Path(__file__).resolve().parents[1]
_PRESETS = json.loads((_ROOT / "config" / "motor_presets.json").read_text(encoding="utf-8"))

# The machine the bug was reported on, pinned field by field.  It must NOT come
# from config/motor_config.yaml: the user edits that constantly (rotor_fill_r
# moved 0.2 -> 0.5 while this fix was being written, which changes the pole-tip
# fillet and therefore the very numbers this file is about).  Same reasoning as
# tests/test_physics_regression.py.
GEO_150MM_REPORTED = {
    "stator_diameter": 150.0, "slot_height": 14.0, "core_thickness": 4.2,
    "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.5, "tooth_width": 9.2, "tooth2_width": 5.5, "cut_width": 6.0,
    "insulation_thickness": 0.15, "wire_width": 5.0, "wire_height": 0.6,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.13, "num_wires_per_slot": 14,
    "wire_split": 1, "slot_hs": 0.2, "magnet_height": 16.0,
    "rotor_house_height": 1.2, "shaft_height": 3.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.44, "magnet_fill_radius": 2.5, "magnet_up_gap": 2.0,
    "rotor_hole": 0.6, "magnet_down_height": 1.8, "magnet_lamination": 0,
    "stator_fillet_r": 3.5, "stator_fillet_r1": 1.2,
    "rotor_fill_r": 0.2,      # the value the defect was measured at
    "motor_length": 35.0,
}

# Rings with fewer points than this are not discretised curves (an 8-point slot
# liner, a 4-point wire rectangle) — the local-window arc rule does not apply.
_DENSE_RING_MIN_PTS = 64
_LOCAL_WINDOW = 10          # +/- segments used for the local median
_LOCAL_MIN_RATIO = 0.04     # see the module docstring for the justification


def _cases():
    """(name, geometry-override-or-None).  Covers the machine that showed the
    bug, the 40 mm preset, the shipped default config, and two more sizes so a
    scale-dependent tolerance cannot pass on one diameter and fail on another."""
    return [
        ("150 mm 24s28p as reported", GEO_150MM_REPORTED),
        ("default config (CadQueryMotor with no override)", None),
        ("preset my_40mm_last", _PRESETS["my_40mm_last"]["geometry"]),
        ("preset ciano14_30_10", _PRESETS["ciano14_30_10"]["geometry"]),
        ("preset motor_100mm", _PRESETS["motor_100mm"]["geometry"]),
        ("preset m200_20kw_base", _PRESETS["m200_20kw_base"]["geometry"]),
    ]


def _rings(geom, tag):
    """[(label, open coordinate array)] for every ring of a (Multi)Polygon."""
    if geom is None or getattr(geom, "is_empty", True):
        return []
    if hasattr(geom, "geoms"):
        out = []
        for i, sub in enumerate(geom.geoms):
            out += _rings(sub, f"{tag}[{i}]")
        return out
    if not hasattr(geom, "exterior"):
        pytest.fail(f"{tag}: non-areal geometry {geom.geom_type} reached the output")
    out = [(f"{tag}.ext", np.asarray(geom.exterior.coords, float))]
    for i, hole in enumerate(geom.interiors):
        out.append((f"{tag}.int{i}", np.asarray(hole.coords, float)))
    return [(lbl, P[:-1] if len(P) > 1 and np.allclose(P[0], P[-1]) else P)
            for lbl, P in out]


def _all_rings(polys):
    out = []
    for key in ("stator", "rotor", "shaft", "air_gap", "in_band", "out_band"):
        out += _rings(polys.get(key), key)
    for i, (mp, _pol) in enumerate(polys.get("magnets", [])):
        out += _rings(mp, f"magnet[{i}]")
    for key in ("coils", "wire_insulation", "slot_insulation"):
        for i, g in enumerate(polys.get(key) or []):
            out += _rings(g, f"{key}[{i}]")
    return out


def _build(geo):
    motor = CadQueryMotor()
    if geo:
        motor.set_parameters(geo)
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    return polys, 2.0 * float(motor.parameters["stator_outer_radius"])


def _segments(P):
    return np.hypot(*(np.roll(P, -1, axis=0) - P).T)


def _shoelace(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


# ─────────────────────────── the shared arc helper ────────────────────────────

def test_arc_discretisation_is_sagitta_based_not_a_fixed_count():
    """The root cause, tested directly: the chord count must follow the radius.

    A fixed n_arc is what put 8 chords of 0.0196 mm on a 0.2 mm rotor-tip fillet
    while the ring around it ran at 0.30 mm.
    """
    scale = 150.0
    quarter = np.pi / 2.0
    counts = {r: _arc_n_segments(r, quarter, scale) for r in (0.1, 0.2, 1.0, 3.5, 20.0)}
    assert len(set(counts.values())) > 1, (
        f"chord count is radius-independent — that IS the bug: {counts}")
    assert counts[0.2] < counts[3.5] < counts[20.0], counts

    min_chord = scale / 2000.0
    for r in (0.05, 0.1, 0.2, 0.5, 1.0, 2.5, 3.5, 10.0, 40.0, 75.0):
        for sweep in (0.3, 1.0, quarter, np.pi, 2 * np.pi):
            n = _arc_n_segments(r, sweep, scale)
            assert n >= 2
            tol = min(scale / _ARC_SAG_SCALE, r / _ARC_SAG_REL)
            sag = r * (1.0 - np.cos(sweep / n / 2.0))
            chord = 2.0 * r * np.sin(sweep / n / 2.0)
            # Three rules in strict precedence, and every arc must satisfy the
            # first one that can be satisfied:
            #   1. keep at least 2 chords (8 on a full turn) — shape floor,
            #   2. never emit a chord below min_chord — mesh sanity,
            #   3. keep the sagitta under tol — CAD accuracy.
            # A big arc meets all three; a 0.2 mm fillet trades 3 for 2 (that IS
            # the fix); an arc shorter than 2 x min_chord trades both for 1.
            n_floor = max(2, int(np.ceil(sweep / (2 * np.pi) * 8)))
            assert n >= n_floor, f"r={r} sweep={sweep}: n={n} below floor {n_floor}"
            assert (sag <= tol * 1.0000001 or chord >= min_chord or n == n_floor), (
                f"r={r} sweep={sweep}: n={n} gives sagitta {sag:.3e} (tol {tol:.3e}) "
                f"AND chord {chord:.5f} (floor {min_chord:.5f}) — neither rule met")


def test_arc_points_hit_the_exact_endpoints():
    pts = _arc_points(1.0, -2.0, 3.0, 0.25, 1.1, 150.0)
    assert len(pts) >= 3
    for want, got in ((0.25, pts[0]), (0.25 + 1.1, pts[-1])):
        assert got[0] == pytest.approx(1.0 + 3.0 * np.cos(want), abs=1e-12)
        assert got[1] == pytest.approx(-2.0 + 3.0 * np.sin(want), abs=1e-12)


def test_arc_helper_survives_a_degenerate_radius():
    """Infeasible designs push a NEGATIVE slot-mouth radius through here; the
    builder must still produce a ring so the VALIDATOR reports the violation
    instead of shapely raising."""
    assert _arc_n_segments(-0.7, 2 * np.pi, 40.0) >= 8
    assert _arc_n_segments(0.0, 2 * np.pi, 40.0) >= 8
    assert len(_arc_points(0.0, 0.0, -0.7, 0.0, 2 * np.pi, 40.0)) >= 9


# ───────────────────── the weld must not depend on traversal ──────────────────

def test_weld_is_order_and_direction_independent():
    """The subtle one, and the reason the weld is run-based rather than a
    "keep the first, drop the follower" sweep.

    Adjacent domains carry their OWN copy of a shared boundary — out_band's hole
    is the stator ring, in_band's hole is the rotor — and shapely hands them back
    starting at a different vertex and wound the other way.  A sweep resolves a
    3-point cluster differently depending on where it starts, so the two copies
    of the same edge would end up microns apart and OCC's fragment would turn
    the mismatch into precisely the sliver faces this whole fix removes.
    """
    eps = 0.02
    # a ring with a 3-point cluster (a-b-c all within eps of the next) plus a
    # lone duplicate pair, so both the chain and the simple case are covered
    base = [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0),
            (2.0, 5.0), (2.0 + 0.9 * eps, 5.0), (2.0 + 1.8 * eps, 5.0),
            (0.0, 5.0), (0.0, 2.0), (0.0 + 0.5 * eps, 2.0)]
    def closed(ring):                     # shapely always repeats the first point
        return list(ring) + [ring[0]]

    ref = _sanitize_ring(closed(base), eps, "ref")
    assert ref is not None
    ref_set = {(round(x, 12), round(y, 12)) for x, y in ref}

    for shift in range(len(base)):
        rot = base[shift:] + base[:shift]
        for rev in (False, True):
            ring = list(reversed(rot)) if rev else rot
            got = _sanitize_ring(closed(ring), eps, "perm")
            assert got is not None
            assert {(round(x, 12), round(y, 12)) for x, y in got} == ref_set, (
                f"weld result depends on traversal (shift={shift}, "
                f"reversed={rev}): {got} != {ref}")


@pytest.mark.parametrize("name,geo", _cases(), ids=[c[0] for c in _cases()])
def test_shared_boundaries_stay_point_identical(name, geo):
    """Every stator boundary point must still be a point of out_band.

    out_band is the annulus MINUS the stator, so the two share that boundary
    exactly — they did before this fix (0 of 1497 stator points missing on the
    150 mm machine) and they must after, or gmsh's OCC fragment sees two
    almost-coincident curves instead of one.
    """
    polys, _diameter = _build(geo)
    stator = {(round(x, 9), round(y, 9))
              for _l, P in _rings(polys["stator"], "s") for x, y in P}
    out_band = {(round(x, 9), round(y, 9))
                for _l, P in _rings(polys["out_band"], "o") for x, y in P}
    missing = stator - out_band
    assert not missing, (
        f"{name}: {len(missing)} of {len(stator)} stator boundary points are not "
        f"in out_band — the shared boundary drifted; first "
        f"{sorted(missing)[:3]}")


# ───────────────────────────── the ring survey ────────────────────────────────

@pytest.mark.parametrize("name,geo", _cases(), ids=[c[0] for c in _cases()])
def test_no_coincident_points_and_no_degenerate_rings(name, geo):
    polys, diameter = _build(geo)
    weld = diameter / _WELD_DIV
    rings = _all_rings(polys)
    assert rings, f"{name}: builder returned no rings at all"

    problems = []
    for label, P in rings:
        if len(P) < 3:
            problems.append(f"{label}: degenerate ring, only {len(P)} distinct points "
                            f"{[tuple(np.round(q, 4)) for q in P]}")
            continue
        area = abs(_shoelace(P))
        if area < _DEGEN_AREA_MM2:
            problems.append(f"{label}: degenerate ring, area {area:.3e} mm^2 "
                            f"at {tuple(np.round(P[0], 4))}")
            continue
        seg = _segments(P)
        bad = np.flatnonzero(seg < weld)
        if bad.size:
            problems.append(
                f"{label}: {bad.size} segment(s) below the {weld:.5f} mm weld "
                f"tolerance (min {seg.min():.3e} mm) — first at "
                f"{tuple(np.round(P[bad[0]], 4))}")
    assert not problems, f"{name}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("name,geo", _cases(), ids=[c[0] for c in _cases()])
def test_arc_chords_are_not_micro_against_their_neighbours(name, geo):
    """No segment may be a small fraction of the LOCAL discretisation.

    See the module docstring for why this is the criterion and not a fraction of
    the ring median.
    """
    polys, _diameter = _build(geo)
    problems = []
    for label, P in _all_rings(polys):
        seg = _segments(P)
        n = len(seg)
        if n < _DENSE_RING_MIN_PTS or n <= 2 * _LOCAL_WINDOW + 1:
            continue                      # not a discretised curve
        idx = (np.arange(n)[:, None]
               + np.arange(-_LOCAL_WINDOW, _LOCAL_WINDOW + 1)[None, :]) % n
        local = np.median(seg[idx], axis=1)
        ratio = seg / np.maximum(local, 1e-12)
        j = int(np.argmin(ratio))
        if ratio[j] < _LOCAL_MIN_RATIO:
            problems.append(
                f"{label}: segment {j} is {seg[j]:.5f} mm = {100*ratio[j]:.2f} % of "
                f"the local median {local[j]:.5f} mm, at "
                f"{tuple(np.round(P[j], 4))} -> {tuple(np.round(P[(j+1) % n], 4))}")
    assert not problems, f"{name}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("name,geo", _cases(), ids=[c[0] for c in _cases()])
def test_every_domain_survives_the_sanitize_pass(name, geo):
    """Sanitising must never silently delete a body: the machine still has its
    stator, rotor, shaft, bands, every magnet and every coil, all with area."""
    polys, _diameter = _build(geo)
    for key in ("stator", "rotor", "shaft", "air_gap", "in_band", "out_band"):
        g = polys[key]
        assert g is not None and not g.is_empty, f"{name}: {key} disappeared"
        assert g.area > 1.0, f"{name}: {key} area collapsed to {g.area:.4g} mm^2"
        assert g.is_valid, f"{name}: {key} is not a valid shapely geometry"
    assert polys["magnets"], f"{name}: no magnets"
    for i, (mp, pol) in enumerate(polys["magnets"]):
        assert mp is not None and not mp.is_empty and mp.area > 0, \
            f"{name}: magnet {i} vanished"
        assert pol in (+1, -1)
    assert polys["coils"], f"{name}: no coils"
    for i, cp in enumerate(polys["coils"]):
        assert cp.area > 0, f"{name}: coil {i} vanished"


def test_the_reported_150mm_defects_are_gone():
    """Regression on the exact coordinates from the bug report."""
    polys, diameter = _build(GEO_150MM_REPORTED)
    weld = diameter / _WELD_DIV

    rotor_pieces = (list(polys["rotor"].geoms)
                    if hasattr(polys["rotor"], "geoms") else [polys["rotor"]])
    for piece in rotor_pieces:
        assert piece.area > _DEGEN_AREA_MM2, (
            "the 3-coincident-point rotor sliver at (-39.8101, -39.8101) is back")

    for label, P in _rings(polys["stator"], "stator"):
        seg = _segments(P)
        assert seg.min() > 0.0, f"{label}: zero-length segment (duplicate point)"
        assert seg.min() >= weld, (
            f"{label}: shortest segment {seg.min():.5f} mm is below the "
            f"{weld:.5f} mm weld tolerance")

    # The rotor pole-tip fillets (rotor_fill_r = 0.2 mm) were the worst offender:
    # a fixed 8 arc points gave 0.0196 mm chords, 456 of the ring's 1348 segments.
    # They now land at ~0.079 mm — the arc helper's min-chord floor (D/2000).
    # Half that floor is the gate: it passes the corrected geometry with room and
    # fails the old 0.0196 mm chords by 2x.
    floor = 0.5 * diameter / 2000.0
    for label, P in _rings(polys["rotor"], "rotor"):
        seg = _segments(P)
        assert seg.min() >= floor, (
            f"{label}: shortest segment {seg.min():.5f} mm < {floor:.5f} mm — the "
            f"fillet arcs are being cut finer than their radius warrants, at "
            f"{tuple(np.round(P[int(np.argmin(seg))], 4))}")
