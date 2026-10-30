# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The stator outline is one piece, stays on its outer circle and is the same in
every sector (owner report 2026-10-15: an arc "lobe" with a round notch at the
top of the CIANO14 30_10 stator in Configure's cross-section).

The report could not be reproduced on the catalog geometry (the 2-D FEM polygon,
the Configure mesh and the stored thumbnail agree), so this pins the invariants a
lobe would break, for every catalog stator family (num_seg 2 and 4, fillet r1 = 0
and > 0) so a regression in the slot-cutter / fillet construction is caught
without a browser:

* one piece, valid;
* no vertex beyond the outer circle by more than 0.3 % (fillet arcs overshoot by
  up to 0.15 % today);
* the 60/30-degree sectors (one slot-pair unit each) have the same area to 0.5 %
  (today at most 0.12 %; a lost cutter lobe is > 1 %);
* the display mesh triangles cover exactly the FEM polygon.

Geometry only (shapely): no solve, no CAD kernel.  Runs in about a second.
"""
from __future__ import annotations

import io
import contextlib
import logging
import math

import pytest

shapely = pytest.importorskip("shapely")
from shapely.geometry import Polygon  # noqa: E402

from motor_ai_sim.cadquery_geometry import CadQueryMotor  # noqa: E402

# CIANO14 30_10 (the reported die), complete.
_BASE = {
    "air_gap": 0.2, "core_thickness": 1.9, "cut_width": 1.8,
    "insulation_thickness": 0.05, "magnet_down_height": 1,
    "magnet_fill_down": 0.7618, "magnet_fill_radius": 0.3,
    "magnet_fill_up": 0.4123, "magnet_height": 4, "magnet_lamination": 0,
    "magnet_up_gap": 0.1, "motor_length": 10, "num_poles": 14,
    "num_poles_per_segment": 7, "num_seg": 2, "num_slots": 12,
    "num_slots_per_segment": 6, "num_wires_per_slot": 6,
    "rotor_fill_r": 0.3, "rotor_hole": 0.6927, "rotor_house_height": 0.8,
    "rotor_inner_radius": 3.9, "rotor_outer_radius": 8.7,
    "shaft_diameter": 5, "shaft_height": 2, "slot_height": 4.2,
    "slot_hs": 0.267, "stator_diameter": 30, "stator_fillet_r": 1.6,
    "stator_fillet_r1": 0, "stator_inner_radius": 8.9,
    # tooth2_width 1.7 = the die's stored 1.6 in the Fusion definition (leg
    # width without the extra wire_spacing_x, 2026-10-30) - the same outline.
    "stator_outer_radius": 15, "tooth2_width": 1.7, "tooth_width": 2.3,
    "wire_height": 0.5, "wire_spacing_x": 0.1, "wire_spacing_y": 0.1,
    "wire_split": 1, "wire_width": 2,
}

# stator-relevant differences of the other catalog dies (values from the shared
# catalog 2026-10-15); each is a feasible slot (fill radius > 0).
_VARIANTS = {
    "30_10 (num_seg 2, r1 = 0)": {},
    "40 new (num_seg 2, r1 = 0.15)": dict(
        stator_diameter=40, stator_outer_radius=20, stator_inner_radius=12.0,
        slot_height=6.1, tooth_width=3.3, tooth2_width=1.7, cut_width=1,
        stator_fillet_r=1.1, stator_fillet_r1=0.15, wire_width=2.5,
        insulation_thickness=0.06, air_gap=0.25, rotor_outer_radius=11.75,
        rotor_inner_radius=4.95, magnet_height=5.7),
    # The die as the owner had it live on 2026-10-15 (insulation 0.07, L20): on the
    # server the cascaded union of the slot cutters DROPPED one cutter for exactly
    # this input and one 60-degree sector came out +23 % (a lobe with a round
    # notch).  Fixed in cadquery_geometry._union_cutters; see
    # tests/test_stator_cutter_union.py for the deterministic replay.
    "40 new L20 ins 0.07 (live 2026-10-15)": dict(
        stator_diameter=40, stator_outer_radius=20, stator_inner_radius=12.0,
        slot_height=6.1, tooth_width=3.3, tooth2_width=1.7, cut_width=1,
        stator_fillet_r=1.1, stator_fillet_r1=0.15, wire_width=2.5,
        insulation_thickness=0.07, air_gap=0.25, rotor_outer_radius=11.75,
        rotor_inner_radius=4.95, magnet_height=5.7, motor_length=20,
        num_wires_per_slot=7, wire_height=0.6),
    "85 mm (num_seg 4, r1 = 0.1)": dict(
        stator_diameter=85, stator_outer_radius=42.5, stator_inner_radius=33.1,
        num_seg=4, num_slots=24, num_poles=28, slot_height=7, core_thickness=2.4,
        tooth_width=5.8, tooth2_width=2.2, cut_width=1.6, stator_fillet_r=1.2,
        stator_fillet_r1=0.1, slot_hs=0.13, wire_width=3, air_gap=0.3,
        rotor_outer_radius=32.8, rotor_inner_radius=25.4, magnet_height=6.6),
    "200 mm (num_seg 2, r1 = 0.2)": dict(
        stator_diameter=200, stator_outer_radius=100, stator_inner_radius=62.8,
        num_poles=10, num_poles_per_segment=5, slot_height=26,
        core_thickness=11.2, tooth_width=18.2, tooth2_width=12.9, cut_width=12,
        stator_fillet_r=5, stator_fillet_r1=0.2, slot_hs=0.2, wire_width=9,
        insulation_thickness=0.25, air_gap=1.6, rotor_outer_radius=61.2,
        rotor_inner_radius=28.2, shaft_height=3, magnet_height=30),
    "CILN28 (num_seg 4, r1 = 0.4)": dict(
        stator_diameter=200, stator_outer_radius=100, stator_inner_radius=74.0,
        num_seg=4, num_slots=24, num_poles=28, slot_height=20.4,
        core_thickness=5.6, tooth_width=11.3, tooth2_width=5.9, cut_width=4.7,
        stator_fillet_r=3.9, stator_fillet_r1=0.4, slot_hs=0.2, wire_width=7,
        insulation_thickness=0.2, air_gap=0.65, rotor_outer_radius=73.35,
        rotor_inner_radius=52.95, shaft_height=3, magnet_height=18.7),
}


def _build(overrides):
    g = dict(_BASE)
    g.update(overrides)
    m = CadQueryMotor()
    m.set_parameters(g)
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            poly = m.get_2d_polygons()["stator"]
            mesh = m.get_2d_mesh_data()["stator_core"]
    finally:
        logging.disable(logging.NOTSET)
    return m.parameters, poly, mesh


def _sector_areas(poly, R, half):
    """Area of the stator inside each slot-pair unit's wedge (unit 0 at the top)."""
    reach = 3.0 * R
    areas = []
    for i in range(half):
        a0 = math.radians(90 + i * 360 / half - 180 / half)
        a1 = a0 + 2 * math.pi / half
        am = 0.5 * (a0 + a1)
        wedge = Polygon([(0, 0),
                         (reach * math.cos(a0), reach * math.sin(a0)),
                         (reach * math.cos(am), reach * math.sin(am)),
                         (reach * math.cos(a1), reach * math.sin(a1))])
        areas.append(poly.intersection(wedge).area)
    return areas


@pytest.mark.parametrize("name", list(_VARIANTS))
def test_stator_outline_has_no_lobe(name):
    p, poly, mesh = _build(_VARIANTS[name])
    R = float(p["stator_outer_radius"])
    half = int(p["num_slots"]) // 2

    # one piece, valid
    assert poly.is_valid and poly.geom_type == "Polygon", poly.geom_type

    # never outside the outer circle (fillet arcs may touch it, not a lobe)
    rmax = max(math.hypot(x, y) for x, y in poly.exterior.coords)
    assert rmax <= R * 1.003, f"{name}: vertex at r = {rmax:.4f} > R = {R}"

    # every slot-pair unit has the same area
    areas = _sector_areas(poly, R, half)
    spread = (max(areas) - min(areas)) / max(areas)
    assert spread < 0.005, f"{name}: sector areas differ by {spread:.2%}: {areas}"

    # the display triangles cover exactly the FEM polygon
    tri = 0.0
    V, F = mesh["vertices"], mesh["faces"]
    for a, b, c in F:
        tri += 0.5 * abs((V[b][0] - V[a][0]) * (V[c][1] - V[a][1])
                         - (V[c][0] - V[a][0]) * (V[b][1] - V[a][1]))
    assert tri == pytest.approx(poly.area, rel=1e-6), (tri, poly.area)
    assert max(math.hypot(v[0], v[1]) for v in V) <= R * 1.003


# ── sweep: every case is either symmetric or refused BY NAME ─────────────────
# Nothing in between: a lobe must never be drawn.  The 40 new die over the
# insulation and wire widths the owner can reach from Configure (2.8 mm is past
# the slot mouth for insulation >= 0.075, so the refusal branch runs too).

_NEW40 = _VARIANTS["40 new L20 ins 0.07 (live 2026-10-15)"]
_INS = [0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]
_WW = [2.0, 2.2, 2.4, 2.5, 2.6, 2.8]


@pytest.mark.parametrize("ins", _INS)
def test_40_new_sweep_symmetric_or_refused_by_name(ins):
    from motor_ai_sim.geometry_validation import (
        slot_mouth_infeasibility, stator_outline_violation)
    seen = {"drawn": 0, "refused": 0}
    for ww in _WW:
        g = {**_BASE, **_NEW40, "insulation_thickness": ins, "wire_width": ww}
        refusal = slot_mouth_infeasibility(g)
        if refusal is not None:                       # refused by name, with the maximum
            assert refusal["code"] == "wire_too_wide_for_slot"
            assert refusal["message"].startswith("wire too wide for this slot: max wire width")
            seen["refused"] += 1
            continue
        p, poly, mesh = _build({"insulation_thickness": ins, "wire_width": ww, **{
            k: v for k, v in _NEW40.items() if k not in ("insulation_thickness", "wire_width")}})
        half = int(p["num_slots"]) // 2
        assert poly.is_valid and poly.geom_type == "Polygon", (ins, ww, poly.geom_type)
        areas = _sector_areas(poly, float(p["stator_outer_radius"]), half)
        spread = (max(areas) - min(areas)) / max(areas)
        assert spread < 0.005, f"ins {ins} ww {ww}: sectors differ by {spread:.2%}: {areas}"
        assert stator_outline_violation(poly, p) is None
        seen["drawn"] += 1
    assert seen["drawn"] >= 5
