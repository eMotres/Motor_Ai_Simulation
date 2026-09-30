"""Review of PR #75 (Codex, 2026-09-30): the always-on comparison rules.

1. ``report._geo_sig_inputs_differ``: an input present on ONE stamp only is a
   difference (unless its absence has a defined meaning).
2. ``num_slots`` / ``num_poles`` are derived only when BOTH compared documents
   carry a valid segment form; count-form-only documents compare them.
3. ONE canonical derived set, equal to what the derivation returns.
"""
from __future__ import annotations

import json

import yaml

from motor_ai_sim.geometry import motor_geometry as mg
from motor_ai_sim.routes import family as fam

from tests.test_die_derived_geometry import CFG, DIE, STALE

COUNT_FORM = {k: v for k, v in STALE.items()
              if k not in ("num_seg", "num_slots_per_segment",
                           "num_poles_per_segment")}


# ── 3 · canonical classification ─────────────────────────────────────────────

def test_canonical_derived_set_equals_what_the_derivation_returns():
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    from motor_ai_sim.services.geometry_service import _DERIVED_PARAMS
    assert set(mg.derived_geometry(dict(STALE))) == set(mg.DERIVED_GEOMETRY_FIELDS)
    assert (set(mg.fresh_derived_values(STALE))
            == set(mg.DERIVED_GEOMETRY_FIELDS) | set(mg.DERIVED_COUNT_FIELDS))
    assert frozenset(DERIVED_GEOMETRY_NAMES) == mg.ALL_DERIVED_GEOMETRY_NAMES
    assert frozenset(_DERIVED_PARAMS) == mg.ALL_DERIVED_GEOMETRY_NAMES
    for k in mg.ALL_DERIVED_GEOMETRY_NAMES - set(mg.DERIVED_COUNT_FIELDS):
        assert not mg.is_compared_geometry_input(k, STALE, STALE), k
    assert not mg.is_compared_geometry_input("slot_hs")
    for k in ("slot_height", "air_gap", "num_seg", "stator_diameter"):
        assert mg.is_compared_geometry_input(k, STALE, STALE), k


# ── 2 · slot/pole totals ─────────────────────────────────────────────────────

def test_counts_are_inputs_unless_both_sides_carry_the_segment_form():
    assert not mg.is_compared_geometry_input("num_slots", STALE, STALE)
    assert mg.is_compared_geometry_input("num_slots", STALE, COUNT_FORM)
    assert mg.is_compared_geometry_input("num_poles", COUNT_FORM, COUNT_FORM)
    assert mg.is_compared_geometry_input("num_poles")        # no documents: input
    assert mg.is_compared_geometry_input("num_slots", dict(STALE, num_seg=0), STALE)


def test_count_form_only_topology_change_is_a_blocking_diff():
    die = {"geometry": dict(COUNT_FORM)}
    duty = {"geometry": dict(COUNT_FORM, num_slots=24, num_poles=28)}
    diffs = fam.duty_geometry_diff(die, {}, duty)
    keys = {x["key"] for x in fam._blocking(diffs)}
    assert {"num_slots", "num_poles"} <= keys


def test_count_form_only_same_machine_has_no_count_diff():
    die = {"geometry": dict(COUNT_FORM)}
    duty = {"geometry": mg.refresh_derived_geometry(COUNT_FORM)}
    assert fam.duty_geometry_diff(die, {}, duty) == []


def test_segment_form_stale_count_is_not_a_diff():
    die = {"geometry": dict(STALE, num_slots=24)}           # stale total
    duty = {"geometry": mg.refresh_derived_geometry(STALE)}
    assert fam.duty_geometry_diff(die, {}, duty) == []


def test_count_form_only_lock_check_refuses_a_count_change(monkeypatch, tmp_path):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": True, "geometry": dict(COUNT_FORM)},
        sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump(
        {"name": CFG, "die": DIE}, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG}), encoding="utf-8")
    ref = fam.geometry_lock_check({"num_slots": 24})
    assert ref and [x["field"] for x in ref["invalid_parameters"]] == ["num_slots"]
    assert fam.geometry_lock_check({"num_slots": 12}) is None


# ── 1 · report signature comparison ──────────────────────────────────────────

def test_report_sig_one_sided_input_is_a_mismatch():
    from motor_ai_sim import report as R
    a = "air_gap:0.3|magnet_height:7|rotor_outer_radius:32.8"
    # magnet_height present on one side only -> different
    assert R._geo_sig_inputs_differ(a, "air_gap:0.3|rotor_outer_radius:32.4")
    # only a derived copy differs / is missing -> same machine
    assert not R._geo_sig_inputs_differ(a, "air_gap:0.3|magnet_height:7")
    # an absent key with a defined meaning compares as that value
    assert not R._geo_sig_inputs_differ(a, a + "|sleeve_thickness:0")
    assert R._geo_sig_inputs_differ(a, a + "|sleeve_thickness:0.5")
    # slot_hs never counts
    assert not R._geo_sig_inputs_differ(a, a + "|slot_hs:0.3")
    # totals without the segment form are inputs
    assert R._geo_sig_inputs_differ(a + "|num_slots:12", a + "|num_slots:24")
    seg = "|num_seg:2|num_slots_per_segment:6|num_poles_per_segment:7"
    assert not R._geo_sig_inputs_differ(a + seg + "|num_slots:12",
                                        a + seg + "|num_slots:24")
