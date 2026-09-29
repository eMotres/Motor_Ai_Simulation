"""Unit tests for scripts/fusion_param_rename.py -- the owner-approved
(2026-09-25) legacy-Fusion-CSV -> canonical-names converter.

Only this file's tests are meant to be run for the converter (per the repo's
"no whole-suite runs by agents" rule): `pytest tests/test_fusion_param_rename.py`.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fusion_param_common as FPC  # noqa: E402
import fusion_param_rename as FPR  # noqa: E402

HEADER = FPR.HEADER


def _rows_to_csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    for r in rows:
        w.writerow([r.get(h, "") for h in HEADER])
    return buf.getvalue().encode("utf-8-sig")


def _row(name: str, expr: str, unit: str = "mm", comment: str = "") -> dict:
    return {"Name": name, "Unit": unit, "Expression": expr, "Value": expr,
            "Comments": comment, "Favorite": "False"}


@pytest.fixture()
def defaults() -> dict:
    return {"num_slots_per_segment": 6.0, "shaft_height": 2.0,
            "sleeve_thickness": 0.0, "stator_fillet_r1": 0.1,
            "rotor_fill_r": 0.2, "motor_length": 40.0,
            "stator_diameter": 50.0, "magnet_lamination": 0.0}


def _run(tmp_path: Path, rows: list[dict], defaults: dict):
    in_path = tmp_path / "in.csv"
    in_path.write_bytes(_rows_to_csv_bytes(rows))
    fieldnames, parsed = FPR.read_param_csv(in_path)
    return FPR.rename(parsed, defaults, fieldnames)


def _by_name(res, name: str) -> dict:
    for r in res.rows:
        if r["Name"] == name:
            return r
    raise KeyError(name)


# ── plain renames ──────────────────────────────────────────────────────────
def test_plain_rename_carries_value_unchanged(tmp_path, defaults):
    res = _run(tmp_path, [_row("slot_h", "1.842")], defaults)
    row = _by_name(res, "slot_height")
    assert float(row["Value"]) == pytest.approx(1.842)
    assert any("slot_h -> slot_height" in line for line in res.renamed)


# ── stator_diameter: CREATE fresh + derive stator_up_r (the ONE formula
# the owner asked to change) -- stator_up_r itself is NEVER renamed. ───────
def test_stator_diameter_is_created_with_explicit_unit_and_doubled_value(tmp_path, defaults):
    res = _run(tmp_path, [_row("stator_up_r", "6")], defaults)
    row = _by_name(res, "stator_diameter")
    assert float(row["Value"]) == pytest.approx(12.0)
    assert row["Expression"] == "12 mm"  # explicit unit, never a bare number


def test_stator_up_r_is_not_renamed_but_becomes_derived(tmp_path, defaults):
    res = _run(tmp_path, [_row("stator_up_r", "6")], defaults)
    row = _by_name(res, "stator_up_r")  # name unchanged
    assert row["Expression"] == "stator_diameter / 2"  # the one changed formula
    assert float(row["Value"]) == pytest.approx(6.0)  # same number as before


def test_other_rows_referencing_stator_up_r_are_left_completely_untouched(tmp_path, defaults):
    # stator_mid_r is NOT one of the 33 -- its formula references stator_up_r,
    # which is never renamed, so this row needs NO changes at all: no
    # substitution, no parenthesising, no reformatting (owner, 2026-09-25:
    # "формулы не меняй, только одну: stator_up_r = stator_diameter/2").
    rows = [
        _row("stator_up_r", "6"),
        _row("slot_h", "1.842"),
        _row("core_h", "0.7"),
        _row("stator_mid_r", "stator_up_r - slot_h - core_h / 2"),
    ]
    res = _run(tmp_path, rows, defaults)
    row = _by_name(res, "stator_mid_r")
    # slot_h/core_h ARE renamed (plain 1:1), so those tokens change...
    assert "slot_height" in row["Expression"] and "core_thickness" in row["Expression"]
    # ...but stator_up_r is untouched, byte for byte.
    assert "stator_up_r - " in row["Expression"] or row["Expression"].startswith("stator_up_r")
    assert "stator_diameter" not in row["Expression"]
    assert "(" not in row["Expression"]  # no parenthesising was introduced


def test_stator_up_r_absent_creates_stator_diameter_from_config_default(tmp_path, defaults):
    # A file with no stator_up_r at all: stator_diameter is still created,
    # from motor_ai_sim's own default geometry.
    res = _run(tmp_path, [_row("gap", "0.1")], defaults)
    row = _by_name(res, "stator_diameter")
    assert row["Name"] == "stator_diameter"
    with pytest.raises(KeyError):
        _by_name(res, "stator_up_r")  # never fabricated


# ── magnet_lamination: CREATE fresh from mag_step's value; mag_step itself
# is left completely untouched (no clean, always-valid inverse formula). ───
def test_mag_step_equal_to_motor_length_means_no_lamination(tmp_path, defaults):
    rows = [_row("stator_w", "40"), _row("mag_step", "40")]
    res = _run(tmp_path, rows, defaults)
    row = _by_name(res, "magnet_lamination")
    assert float(row["Value"]) == pytest.approx(0.0)
    assert row["Expression"] == "0 mm"


def test_mag_step_shorter_than_motor_length_is_carried_over(tmp_path, defaults):
    rows = [_row("stator_w", "180"), _row("mag_step", "10")]
    res = _run(tmp_path, rows, defaults)
    row = _by_name(res, "magnet_lamination")
    assert float(row["Value"]) == pytest.approx(10.0)


def test_mag_step_row_itself_is_left_completely_as_is(tmp_path, defaults):
    # mag_step keeps its NAME and its formula; only a reference to a renamed
    # parameter follows the rename (stator_w -> motor_length), otherwise the
    # imported CSV would point at a name that no longer exists.
    rows = [_row("stator_w", "40"), _row("mag_step", "stator_w", comment="orig")]
    res = _run(tmp_path, rows, defaults)
    mag_step_after = _by_name(res, "mag_step")
    assert mag_step_after["Expression"] == "motor_length"
    assert mag_step_after["Name"] == "mag_step"  # never renamed
    assert "mag_step" in " ".join(res.unchanged_legacy)[0:2000] or any(
        "mag_step" in line for line in res.unchanged_legacy)


# ── token-safety: a name must never match as a substring of another ────────
def test_wire_h_does_not_match_inside_wire_h2(tmp_path, defaults):
    rows = [
        _row("wire_h", "0.2"),
        _row("wire_h2", "wire_h * 2"),  # a made-up, unmapped name
    ]
    res = _run(tmp_path, rows, defaults)
    # wire_h itself is renamed...
    assert _by_name(res, "wire_height")
    # ...but wire_h2 keeps its own name (never partially matched / renamed)
    wire_h2 = _by_name(res, "wire_h2")
    # and its formula's wire_h token becomes wire_height, not "wire_height2"
    # or any other corrupted substring result.
    assert wire_h2["Expression"] == "wire_height * 2"


def test_slot_hs1_is_not_confused_with_slot_hs(tmp_path, defaults):
    rows = [
        _row("slot_hs", "0.11", unit=""),
        _row("slot_hs1", "slot_hs - 0.09", unit=""),  # unmapped, references slot_hs
    ]
    res = _run(tmp_path, rows, defaults)
    assert _by_name(res, "slot_hs")  # identity rename, still present
    slot_hs1 = _by_name(res, "slot_hs1")  # name untouched (not one of the 33)
    assert slot_hs1["Expression"] == "slot_hs - 0.09"  # already canonical, no corruption


# ── unknown / untouched rows ────────────────────────────────────────────────
def test_unrelated_mechanical_row_is_left_alone(tmp_path, defaults):
    res = _run(tmp_path, [_row("bolt_head_d", "8.8")], defaults)
    row = _by_name(res, "bolt_head_d")
    assert float(row["Value"]) == pytest.approx(8.8)
    assert "bolt_head_d" in res.unknown


def test_wire_split_is_never_created(tmp_path, defaults):
    res = _run(tmp_path, [_row("gap", "0.1")], defaults)
    names = [r["Name"] for r in res.rows]
    assert "wire_split" not in names


def test_wire_split_present_under_its_own_name_is_left_alone(tmp_path, defaults):
    rows = [_row("gap", "0.1"), _row("wire_split", "2", unit="")]
    res = _run(tmp_path, rows, defaults)
    row = _by_name(res, "wire_split")
    assert float(row["Value"]) == pytest.approx(2.0)


# ── create-missing rows ─────────────────────────────────────────────────────
def test_missing_primaries_are_created_from_defaults(tmp_path, defaults):
    res = _run(tmp_path, [_row("gap", "0.1")], defaults)
    names = {r["Name"]: r for r in res.rows}
    for canonical in ("shaft_height", "sleeve_thickness", "num_slots_per_segment"):
        assert canonical in names, canonical
        assert float(names[canonical]["Value"]) == pytest.approx(defaults[canonical])
    assert any(canonical in line for line in res.created
               for canonical in ("shaft_height", "sleeve_thickness", "num_slots_per_segment"))


def test_rename_or_create_uses_old_name_when_present(tmp_path, defaults):
    res = _run(tmp_path, [_row("stator_r1", "0.05")], defaults)
    row = _by_name(res, "stator_fillet_r1")
    assert float(row["Value"]) == pytest.approx(0.05)  # from the file, not the default


def test_rename_or_create_creates_when_old_name_absent(tmp_path, defaults):
    res = _run(tmp_path, [_row("gap", "0.1")], defaults)
    row = _by_name(res, "stator_fillet_r1")
    assert float(row["Value"]) == pytest.approx(defaults["stator_fillet_r1"])


# ── collision refusal ───────────────────────────────────────────────────────
def test_collision_between_two_old_names_is_refused(tmp_path, defaults):
    # Contrived: a file that already has a row literally named "air_gap" AND
    # a separate "gap" row that would also rename to air_gap.
    rows = [_row("gap", "0.1"), _row("air_gap", "9.9")]
    with pytest.raises(FPR.FusionRenameError):
        _run(tmp_path, rows, defaults)


# ── no parameter is ever deleted (owner, 2026-09-25) ────────────────────────
def test_no_row_disappears_except_by_a_recorded_1to1_rename(tmp_path, defaults):
    rows = [
        _row("stator_up_r", "6"), _row("slot_h", "1.842"), _row("core_h", "0.7"),
        _row("stator_mid_r", "stator_up_r - slot_h - core_h / 2"),
        _row("stator_w", "40"), _row("mag_step", "40"),
        _row("bolt_head_d", "8.8"),  # untouched mechanical row
        _row("N1", "2", unit=""), _row("Nm", "5", unit=""),
    ]
    res = _run(tmp_path, rows, defaults)
    input_names = {r["Name"] for r in rows}
    output_names = {r["Name"] for r in res.rows}
    renamed_pairs = dict(line.split(" -> ") for line in res.renamed)

    for name in input_names:
        if name in output_names:
            continue  # kept under its own name (untouched, or stator_up_r/mag_step)
        assert name in renamed_pairs, "%s disappeared without a recorded rename" % name
        assert renamed_pairs[name] in output_names

    # rows_out == rows_in + created (nothing lost, nothing silently dropped)
    assert len(res.rows) == len(rows) + len(res.created)


def test_no_row_disappears_on_the_real_file(tmp_path):
    src = Path(r"C:\Users\vadim\Downloads\ExportedParameters.csv")
    if not src.is_file():
        pytest.skip("owner's Downloads file not present in this environment")
    fieldnames, rows = FPR.read_param_csv(src)
    defaults = FPR.load_create_defaults(FPR.DEFAULT_CONFIG)
    res = FPR.rename(rows, defaults, fieldnames)
    input_names = {r["Name"] for r in rows if r.get("Name", "").strip()}
    output_names = {r["Name"] for r in res.rows}
    renamed_pairs = dict(line.split(" -> ") for line in res.renamed)
    for name in input_names:
        if name in output_names:
            continue
        assert name in renamed_pairs, "%s disappeared without a recorded rename" % name
        assert renamed_pairs[name] in output_names
    assert len(res.rows) == len(input_names) + len(res.created)
    # stator_up_r and mag_step specifically must survive under their own name
    assert "stator_up_r" in output_names
    assert "mag_step" in output_names


# ── on the owner's real file ────────────────────────────────────────────────
def test_real_exported_parameters_file_renames_without_error():
    src = Path(r"C:\Users\vadim\Downloads\ExportedParameters.csv")
    if not src.is_file():
        pytest.skip("owner's Downloads file not present in this environment")
    fieldnames, rows = FPR.read_param_csv(src)
    defaults = FPR.load_create_defaults(FPR.DEFAULT_CONFIG)
    res = FPR.rename(rows, defaults, fieldnames)
    out_names = {r["Name"] for r in res.rows}
    # every one of the 33 approved canonical names must be present afterward
    for entry in FPC.ENTRIES:
        if entry["action"] == "not_mapped":
            continue
        assert entry["canonical"] in out_names, entry["canonical"]
    # and no duplicate names slipped through
    names_list = [r["Name"] for r in res.rows]
    assert len(names_list) == len(set(names_list))


# ── config/fusion_param_map.yaml stays in sync with the code ───────────────
def test_approved_yaml_section_matches_fusion_param_common():
    """scripts/fusion_param_common.py is the executable source of truth (see
    its module docstring); config/fusion_param_map.yaml's
    `legacy_fusion_names_approved_2026_09_25` section is a hand-kept human-
    readable copy of the same 33 rows. This is the guard against the two
    drifting apart."""
    import yaml
    d = yaml.safe_load((REPO_ROOT / "config" / "fusion_param_map.yaml").read_text(encoding="utf-8"))
    approved = d["legacy_fusion_names_approved_2026_09_25"]
    assert len(approved) == 33 == len(FPC.ENTRIES)
    for e in FPC.ENTRIES:
        y = approved[e["canonical"]]
        old = None if y["old"] == "null" else y["old"]
        assert old == e["old"], e["canonical"]
        assert y["action"] == e["action"], e["canonical"]
        y_conv = None if y["conversion"] in ("null", None) else tuple(
            (None if v == "null" else v) for v in y["conversion"])
        e_conv = e["conversion"]
        if e_conv is None:
            assert y_conv is None, e["canonical"]
        else:
            assert y_conv[0] == e_conv[0], e["canonical"]
