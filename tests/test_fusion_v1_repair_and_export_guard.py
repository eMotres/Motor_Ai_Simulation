"""Unit tests for the 2026-09-25 owner-reported bug: the FIRST version of
fusion360_rename_params.py (commit e65e16e) plainly renamed
stator_up_r -> stator_diameter (keeping the RADIUS value) and
mag_step -> magnet_lamination (keeping the motor-length value), instead of
doing the CREATE-and-derive / CREATE-from-legacy the current (v2) script
does -- so a design that already went through v1 gets silently skipped by
v2 ("stator_diameter already present") and permanently carries an
impossible machine (`config/../CIAN_12_40_canonical_params.csv`:
stator_diameter = 6 mm, i.e. the old RADIUS value, and
magnet_lamination = 40 mm = the motor length).

This file tests the pure, stdlib-only logic in scripts/fusion_param_common.py
that the in-Fusion scripts call: detecting the v1-broken state and planning
its repair, the export-time sanity guard, the magnet_lamination export
guard, and the Parameter-I/O-CSV-tolerant parsing the import script falls
back to. None of this needs Fusion's `adsk` module (not importable outside
Fusion) -- exactly why this logic lives in the shared, stdlib-only module
rather than in the `adsk`-importing scripts themselves.

Run only this file (per the repo's "no whole-suite runs by agents" rule):
`pytest tests/test_fusion_v1_repair_and_export_guard.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fusion_param_common as FPC  # noqa: E402

# The owner's real, reproduced bug data (C:\Users\vadim\Downloads\
# CIAN_12_40_canonical_params.csv, byte-identical to
# tests/test_zzz_repro_fusion_owner_bug.py's CSV): a v1-broken export.
CIAN_STACK = {
    "slot_height_mm": 1.842, "core_thickness_mm": 0.7, "air_gap_mm": 0.1,
    "magnet_height_mm": 2.0, "rotor_house_height_mm": 0.3, "shaft_height_mm": 2.0,
}
CIAN_MOTOR_LENGTH = 40.0
CIAN_STATOR_DIAMETER_BROKEN = 6.0   # the old stator_up_r RADIUS value, carried over as-is
CIAN_MAGNET_LAMINATION_BROKEN = 40.0  # == motor_length: the mag_step "whole stack" value


# ── plan_v1_repair: detection ───────────────────────────────────────────────
def test_v1_broken_design_is_detected_via_dependent_expression():
    present = {"stator_diameter", "slot_height", "core_thickness", "air_gap",
               "magnet_height", "rotor_house_height", "shaft_height", "motor_length",
               "magnet_lamination"}
    plan = FPC.plan_v1_repair(
        present,
        dependent_expressions=["stator_diameter - slot_height - core_thickness"],
        stator_diameter_value=CIAN_STATOR_DIAMETER_BROKEN, stack_components=CIAN_STACK,
        motor_length_value=CIAN_MOTOR_LENGTH,
        magnet_lamination_value=CIAN_MAGNET_LAMINATION_BROKEN)
    assert plan["repair_stator_diameter"] is True
    assert plan["stator_evidence"]
    assert plan["repair_magnet_lamination"] is True


def test_v1_broken_design_is_detected_via_value_fallback_with_no_dependent_expressions():
    # No dependent-expression evidence available at all (e.g. exported CSV,
    # no formulas) -- the value-too-small fallback still catches it.
    present = {"stator_diameter", "slot_height", "core_thickness", "air_gap",
               "magnet_height", "rotor_house_height", "shaft_height"}
    plan = FPC.plan_v1_repair(
        present, dependent_expressions=[],
        stator_diameter_value=CIAN_STATOR_DIAMETER_BROKEN, stack_components=CIAN_STACK)
    assert plan["repair_stator_diameter"] is True
    assert "too small" in plan["stator_evidence"][0]


def test_healthy_v2_design_without_stator_diameter_is_left_alone():
    present = {"stator_up_r", "mag_step", "slot_height"}
    plan = FPC.plan_v1_repair(present)
    assert plan == {"repair_stator_diameter": False, "stator_evidence": [],
                     "repair_magnet_lamination": False}


def test_healthy_v2_design_with_both_names_present_is_left_alone():
    # stator_up_r already exists alongside stator_diameter (already
    # correctly v2-renamed, or hand-fixed) -- never a repair target, no
    # matter what the dependent expressions look like.
    present = {"stator_diameter", "stator_up_r", "mag_step", "magnet_lamination"}
    plan = FPC.plan_v1_repair(
        present, dependent_expressions=["stator_diameter - slot_height"],
        magnet_lamination_value=0.0, motor_length_value=40.0)
    assert plan["repair_stator_diameter"] is False
    assert plan["repair_magnet_lamination"] is False


def test_derived_stator_up_r_expression_itself_is_not_mistaken_for_v1_evidence():
    # A genuine v2 design's ONLY reference to stator_diameter is
    # stator_up_r's own "stator_diameter / 2" -- the caller excludes
    # stator_up_r's own expression (it belongs to the aliased parameter,
    # not an "other" one), and even if it were included it must not count
    # as evidence since it IS halved.
    assert FPC.stator_diameter_used_as_radius(["stator_diameter / 2"]) is False
    assert FPC.stator_diameter_used_as_radius(["stator_diameter * 0.5"]) is False


def test_magnet_lamination_repair_needs_value_equal_to_motor_length():
    # magnet_lamination present, mag_step absent, but NOT equal to the
    # motor length -- this is a genuine (already-correct) v2 value, not the
    # v1 bug signature, so no repair.
    present = {"magnet_lamination", "motor_length"}
    plan = FPC.plan_v1_repair(present, magnet_lamination_value=10.0, motor_length_value=40.0)
    assert plan["repair_magnet_lamination"] is False


# ── export_sanity_check: the CIAN reproduction case ────────────────────────
def test_export_guard_flags_the_broken_6mm_stator_diameter():
    values = dict(stator_diameter=CIAN_STATOR_DIAMETER_BROKEN,
                  slot_height=CIAN_STACK["slot_height_mm"],
                  core_thickness=CIAN_STACK["core_thickness_mm"],
                  air_gap=CIAN_STACK["air_gap_mm"],
                  magnet_height=CIAN_STACK["magnet_height_mm"],
                  rotor_house_height=CIAN_STACK["rotor_house_height_mm"],
                  shaft_height=CIAN_STACK["shaft_height_mm"])
    result = FPC.export_sanity_check(values)
    assert result["skipped"] is False
    assert result["ok"] is False
    assert result["failures"]  # rotor_inner / shaft_inner come out negative
    assert "fusion360_rename_params" in result["likely_cause"]


def test_export_guard_passes_the_repaired_12mm_stator_diameter():
    # Same CIAN slot/core/air-gap/magnet/rotor-house stack, stator_diameter
    # corrected to what the repair produces (2 x the true 6 mm radius).
    # shaft_height is reduced from the CIAN CSV's exported 2 mm -- that 2 mm
    # coincides exactly with fusion360_rename_params.FALLBACK_DEFAULTS
    # ["shaft_height"], strong evidence it was never actually configured
    # for this specific tiny motor (a separate, pre-existing data-quality
    # question, not the stator_diameter bug this guard targets) -- to a
    # value that lets a genuinely-sized 12 mm-diameter motor's radial stack
    # fit, isolating the one thing under test: radius-vs-diameter.
    values = dict(stator_diameter=12.0,
                  slot_height=CIAN_STACK["slot_height_mm"],
                  core_thickness=CIAN_STACK["core_thickness_mm"],
                  air_gap=CIAN_STACK["air_gap_mm"],
                  magnet_height=CIAN_STACK["magnet_height_mm"],
                  rotor_house_height=CIAN_STACK["rotor_house_height_mm"],
                  shaft_height=0.4)
    result = FPC.export_sanity_check(values)
    assert result["ok"] is True
    assert result["failures"] == []
    assert result["derived"]["shaft_inner"] > 0


def test_export_guard_cross_checks_stator_diameter_against_stator_up_r():
    values = dict(stator_diameter=CIAN_STATOR_DIAMETER_BROKEN,  # should be 12
                  slot_height=CIAN_STACK["slot_height_mm"],
                  core_thickness=CIAN_STACK["core_thickness_mm"],
                  air_gap=CIAN_STACK["air_gap_mm"],
                  magnet_height=CIAN_STACK["magnet_height_mm"],
                  rotor_house_height=CIAN_STACK["rotor_house_height_mm"],
                  shaft_height=0.4, stator_up_r=6.0)
    result = FPC.export_sanity_check(values)
    assert result["ok"] is False
    assert any("!=" in f and "stator_up_r" in f for f in result["failures"])


def test_export_guard_skips_cleanly_when_not_enough_data():
    result = FPC.export_sanity_check({"stator_diameter": 12.0})
    assert result["skipped"] is True
    assert result["ok"] is True
    assert result["failures"] == []


# ── magnet_lamination_export_value: the CIAN reproduction case ────────────
def test_magnet_lamination_export_guard_overrides_the_cian_40_to_0():
    value, overridden = FPC.magnet_lamination_export_value(
        CIAN_MAGNET_LAMINATION_BROKEN, CIAN_MOTOR_LENGTH)
    assert overridden is True
    assert value == 0.0


def test_magnet_lamination_export_guard_leaves_a_genuine_value_alone():
    value, overridden = FPC.magnet_lamination_export_value(10.0, CIAN_MOTOR_LENGTH)
    assert overridden is False
    assert value == 10.0


def test_magnet_lamination_export_guard_is_a_noop_without_a_motor_length():
    value, overridden = FPC.magnet_lamination_export_value(40.0, None)
    assert overridden is False
    assert value == 40.0


# ── parse_param_io_csv_rows: the import script's CSV fallback ─────────────
def test_csv_rows_tolerate_comment_vs_comments_header():
    fieldnames = ["Name", "Unit", "Expression", "Value", "Comments", "Favorite"]
    rows = [{"Name": "air_gap", "Unit": "mm", "Expression": "0.1", "Value": "0.1",
             "Comments": "a note", "Favorite": "False"}]
    values, refused = FPC.parse_param_io_csv_rows(rows, fieldnames)
    assert values["air_gap"] == (0.1, "mm", "a note")
    assert refused == {}


def test_csv_rows_match_columns_by_name_not_position():
    # Expression and Unit swapped relative to our own writer's column
    # order -- still read correctly because lookup is by header name.
    fieldnames = ["Unit", "Name", "Value", "Expression", "Comment", "Favorite"]
    rows = [{"Unit": "mm", "Name": "slot_height", "Value": "1.842",
             "Expression": "1.842", "Comment": "", "Favorite": "False"}]
    values, refused = FPC.parse_param_io_csv_rows(rows, fieldnames)
    assert values["slot_height"] == (1.842, "mm", "")


def test_csv_rows_refuse_a_formula_and_still_parse_the_rest():
    fieldnames = ["Name", "Unit", "Expression", "Value", "Comment", "Favorite"]
    rows = [
        {"Name": "stator_mid_r", "Unit": "mm", "Expression": "stator_up_r - slot_h",
         "Value": "4.158", "Comment": "", "Favorite": "False"},
        {"Name": "air_gap", "Unit": "mm", "Expression": "0.1", "Value": "0.1",
         "Comment": "", "Favorite": "False"},
    ]
    values, refused = FPC.parse_param_io_csv_rows(rows, fieldnames)
    assert "stator_mid_r" in refused
    assert values["air_gap"] == (0.1, "mm", "")


def test_csv_rows_missing_required_columns_raises():
    import pytest
    with pytest.raises(ValueError):
        FPC.parse_param_io_csv_rows([{"Foo": "bar"}], ["Foo"])


def test_parse_param_io_expression_accepts_cm_and_m():
    assert FPC.parse_param_io_expression("3.6 cm") == (36.0, "")
    assert FPC.parse_param_io_expression("0.05 m") == (50.0, "")
    assert FPC.parse_param_io_expression("36") == (36.0, "")
    val, why = FPC.parse_param_io_expression("stator_up_r * 2")
    assert val is None and why
