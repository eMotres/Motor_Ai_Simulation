"""The built coil pocket has ONE implementation and its own name, `pocket_width`; the stored `slot_width` is untouched.

Pinned (owner / coordinator 2026-11-24, audit AUDIT_n_coils_and_slot_width):
  * ``family._built_slot_width`` IS ``motor_geometry.pocket_width`` (one implementation, the old name an alias);
  * ``derived_geometry`` still returns the SAME keys and the SAME stored ``slot_width`` (the wire pitch, one
    ``wire_spacing_x`` wider than the pocket): no key added, no value moved - mesh, thermal, hashes, designations and
    the passport (``fit.slotWidth_mm`` reads ``slot_width``) therefore stay bit-identical;
  * the die-diff entry carries the BUILT value under ``pocket_width``; the duty-save check still accepts the OLD key.
Pure: no catalog, no solver.
"""
from __future__ import annotations

import pytest

from motor_ai_sim.geometry import motor_geometry as mg
from motor_ai_sim.routes import family as fam

GEO = {"stator_diameter": 200.0, "core_thickness": 6.0, "slot_height": 20.0, "air_gap": 1.0, "magnet_height": 5.0,
       "rotor_house_height": 5.0, "wire_width": 7.0, "wire_spacing_x": 0.1, "insulation_thickness": 0.2,
       "wire_split": 1, "num_slots": 24, "num_poles": 28}


def test_one_implementation_and_the_old_name_is_an_alias():
    for g in (GEO, dict(GEO, wire_split=3, wire_width=2.3), {"wire_width": 3.0}, {}):
        assert fam._built_slot_width(g) == mg.pocket_width(g)
    assert mg.pocket_width(GEO) == pytest.approx(7.5)            # col 7 + 2 x 0.2 + 0.1
    assert mg.pocket_width({"wire_width": 3.0}) is None


def test_the_stored_slot_width_is_untouched_and_no_key_is_added():
    d = mg.derived_geometry(dict(GEO))
    assert d["slot_width"] == pytest.approx(7.6)                 # col + 2 x spacing + 2 x ins: the stored wire pitch
    assert "pocket_width" not in d
    assert "pocket_width" not in mg.DERIVED_GEOMETRY_FIELDS
    assert set(mg.DERIVED_GEOMETRY_FIELDS) == {"stator_outer_radius", "stator_inner_radius", "rotor_outer_radius",
                                               "rotor_inner_radius", "angle_slot", "angle_pole", "slot_pitch",
                                               "pole_pitch", "slot_width"}
    assert d["slot_width"] - mg.pocket_width(GEO) == pytest.approx(GEO["wire_spacing_x"])


def test_the_die_diff_carries_the_built_pocket_under_its_own_name():
    wider = dict(GEO, insulation_thickness=0.37)
    out = fam.die_shape_diffs(GEO, wider)
    assert [x["key"] for x in out] == ["pocket_width"]
    assert out[0]["label"].startswith("pocket width")
    assert out[0]["die"] == pytest.approx(7.5) and out[0]["live"] == pytest.approx(7.84)


def test_the_old_key_is_still_accepted_on_read():
    assert "slot_width" in fam.POCKET_WIDTH_KEYS and "pocket_width" in fam.POCKET_WIDTH_KEYS
    for diffs in ([{"key": "slot_width"}], [{"key": "pocket_width"}], [{"key": "slot_width"}, {"key": "pocket_width"}]):
        assert all(x["key"] in fam.POCKET_WIDTH_KEYS for x in diffs)
    assert not all(x["key"] in fam.POCKET_WIDTH_KEYS for x in [{"key": "pocket_width"}, {"key": "tooth_width"}])


def test_the_mismatch_warning_keeps_its_code_and_numbers():
    w = fam.config_slot_mismatch("D", "C", GEO, {"wire_spacing_x": 0.2})
    assert w["code"] == "slot_width_differs_from_die"
    assert w["die_slot_mm"] == 7.5 and w["config_slot_mm"] == pytest.approx(7.6)


def test_python_and_web_agree_on_the_shared_cases():
    """tests/fixtures/pocket_width_cases.json is also read by web/src/lib/__tests__/pocketWidth.test.mjs."""
    import json
    from pathlib import Path
    from motor_ai_sim.winding import STRIP_GAP_FACTOR
    fx = json.loads((Path(__file__).parent / "fixtures" / "pocket_width_cases.json").read_text(encoding="utf-8"))
    assert STRIP_GAP_FACTOR == fx["strip_gap_factor"]
    for c in fx["cases"]:
        v = mg.pocket_width(c["geo"])
        if c["expected"] is None:
            assert v is None, c["name"]
        else:
            assert v == pytest.approx(c["expected"], abs=fx["tolerance"]), c["name"]


def test_non_finite_inputs_are_missing_and_a_non_finite_split_is_one_strip():
    g = {"wire_width": 7.0, "wire_spacing_x": 0.1, "insulation_thickness": 0.2}
    assert mg.pocket_width(dict(g, wire_width=float("inf"))) is None
    assert mg.pocket_width(dict(g, insulation_thickness=float("nan"))) is None
    assert mg.pocket_width(dict(g, wire_split=float("inf"))) == pytest.approx(7.5)
    assert mg.pocket_width(dict(g, wire_split=float("nan"))) == pytest.approx(7.5)


def test_no_die_diff_branch_emits_the_stored_slot_width():
    """Only the built-pocket branch names a slot-ish key; the generic input loop skips the stored (derived) slot_width."""
    out = fam.die_shape_diffs(dict(GEO, slot_width=7.6), dict(GEO, slot_width=9.9))
    assert out == []
    out = fam.die_shape_diffs(GEO, dict(GEO, wire_width=7.5, slot_width=9.9))
    assert "slot_width" not in [x["key"] for x in out]
