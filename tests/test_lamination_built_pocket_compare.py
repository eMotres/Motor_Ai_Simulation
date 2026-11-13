"""``die_shape_diffs`` compares the coil pocket the builders really CUT, not ``derived_geometry()["slot_width"]``.

The pocket is ``column + 2 x insulation + 1 x wire_spacing_x`` (``cadquery_geometry.slot_w``,
``iron_template.coil_w``); the derived number carries ``2 x wire_spacing_x``.  Two configurations with the same
pocket but another ``wire_spacing_x`` / ``wire_width`` split are the same lamination, and the reverse.  Pure.
"""
from __future__ import annotations

from motor_ai_sim.geometry.motor_geometry import derived_geometry
from motor_ai_sim.routes import family as fam

BASE = {"stator_diameter": 80.0, "num_seg": 1, "num_slots_per_segment": 12, "num_poles_per_segment": 14,
        "core_thickness": 3.0, "slot_height": 9.0, "air_gap": 0.6, "magnet_height": 4.0, "rotor_house_height": 2.0,
        "magnet_down": 1.0, "tooth_width": 4.0, "wire_height": 0.5, "num_wires_per_slot": 12, "motor_length": 12}


def _geo(ww, wsx, ins, split=None):
    g = dict(BASE, wire_width=ww, wire_spacing_x=wsx, insulation_thickness=ins)
    if split:
        g["wire_split"] = split
    return g


def _slot_diffs(a, b):
    return [d for d in fam.die_shape_diffs(a, b) if d["key"] == "slot_width"]


def test_same_built_pocket_with_another_spacing_split_is_the_same_lamination():
    a, b = _geo(4.0, 0.1, 0.1), _geo(3.9, 0.2, 0.1)          # 4.0 + 0.2 + 0.1 == 3.9 + 0.2 + 0.2 == 4.3
    assert abs(fam._built_slot_width(a) - fam._built_slot_width(b)) < 1e-9
    assert abs(derived_geometry(dict(a))["slot_width"] - derived_geometry(dict(b))["slot_width"]) > 0.05   # the old verdict differed
    assert _slot_diffs(a, b) == []
    assert not [d for d in fam.die_shape_diffs(a, b) if d["key"] not in fam.FREE_GEO_KEYS]


def test_same_built_pocket_with_a_split_wire_is_the_same_lamination():
    # n strips: n*ww + (n-1)*2*wsx + 2*ins + wsx
    a = _geo(1.9, 0.1, 0.1, split=2)                          # 3.8 + 0.2 + 0.2 + 0.1 = 4.3
    b = _geo(4.0, 0.1, 0.1)                                   # 4.0 + 0.2 + 0.1 = 4.3
    assert abs(fam._built_slot_width(a) - 4.3) < 1e-9 and abs(fam._built_slot_width(b) - 4.3) < 1e-9
    assert _slot_diffs(a, b) == []


def test_different_built_pockets_are_different_laminations():
    a, b = _geo(4.0, 0.1, 0.1), _geo(4.0, 0.2, 0.1)          # 4.3 vs 4.4
    d = _slot_diffs(a, b)
    assert len(d) == 1 and abs(d[0]["die"] - 4.3) < 1e-9 and abs(d[0]["live"] - 4.4) < 1e-9
    # another pair with the same pocket and a different derived width
    c, e = _geo(4.0, 0.1, 0.1), _geo(4.1, 0.0, 0.1)           # derived 4.4 vs 4.3; pocket 4.3 vs 4.3
    assert _slot_diffs(c, e) == []
    f = _geo(4.2, 0.1, 0.1)                                   # pocket 4.5
    assert len(_slot_diffs(c, f)) == 1


def test_missing_input_means_no_verdict():
    a = _geo(4.0, 0.1, 0.1)
    b = {k: v for k, v in _geo(4.5, 0.1, 0.1).items() if k != "wire_width"}
    assert _slot_diffs(a, b) == []


def test_a_rounding_micron_is_not_another_lamination():
    # a round-wire proxy stack is rounded to the micron: 1 um apart is the same pocket (the slot warning's tolerance)
    a, b = _geo(4.0, 0.1, 0.1), _geo(4.001, 0.1, 0.1)
    assert 0 < abs(fam._built_slot_width(a) - fam._built_slot_width(b)) < fam.SLOT_MISMATCH_WARN_MM
    assert _slot_diffs(a, b) == []
    c = _geo(4.0 + 2 * fam.SLOT_MISMATCH_WARN_MM, 0.1, 0.1)
    assert len(_slot_diffs(a, c)) == 1
