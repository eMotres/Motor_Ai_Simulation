"""``geo=`` overrides carry their own slot and pole counts.

The builder treats ``num_slots`` / ``num_poles`` as authoritative and the
segment view as derived — so an override giving ``num_seg × per_segment`` but
no counts inherited the LOADED machine's counts when merged over its dict.  On
2026-09-08 the live machine was a 24-slot G2-L40 and every 2×6-slot test
geometry came out as a 24-slot cross-section ("not buildable").
"""
from __future__ import annotations

import json

from motor_ai_sim.routes._validation import parse_geo_override


def test_counts_follow_the_override_primaries():
    ov = parse_geo_override(json.dumps({"num_seg": 2, "num_slots_per_segment": 6,
                                        "num_poles_per_segment": 7, "stator_diameter": 30}))
    assert ov["num_slots"] == 12 and ov["num_poles"] == 14


def test_explicit_counts_in_the_override_are_kept():
    ov = parse_geo_override(json.dumps({"num_seg": 2, "num_slots_per_segment": 6,
                                        "num_slots": 12, "num_poles": 14}))
    assert ov["num_slots"] == 12 and ov["num_poles"] == 14


def test_a_partial_override_adds_nothing_it_cannot_know():
    # num_seg alone: the per-segment counts are the loaded machine's, so the
    # totals cannot be derived here — leave them to the merge as before.
    ov = parse_geo_override(json.dumps({"num_seg": 4}))
    assert "num_slots" not in ov and "num_poles" not in ov
    ov2 = parse_geo_override(json.dumps({"stator_diameter": 40}))
    assert "num_slots" not in ov2
