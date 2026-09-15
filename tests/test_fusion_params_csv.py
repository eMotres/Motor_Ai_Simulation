"""Fusion 360 Parameter I/O CSV — the six columns the add-in demands.

Our export used to write four columns (Name, Unit, Expression, Comment) and
Parameter I/O refused the whole file: "The file should contain at least six
columns, corresponding to the following categories: name, unit, expression,
value, comment, and favorite."  The add-in's OWN export writes those six, in
that order, with Favorite as True/False — so we mirror it exactly.

The import side must keep reading Expression (never Value, which is only the
add-in's evaluation of it) and must keep swallowing our older four-column
file — it matches columns by header NAME, which is what these tests pin.

Nothing here writes state: only the export function and the import parser in
DRY-RUN mode (``dry_run=1``), which returns before the geometry write path.
"""
from __future__ import annotations

import asyncio
import csv
import io

from fastapi import UploadFile

from motor_ai_sim.routes.fusion import (
    FUSION_EXCLUDED_NAMES, _rows, export_params_csv, import_params,
)

HEADER = ["Name", "Unit", "Expression", "Value", "Comment", "Favorite"]


def _csv_text() -> str:
    r = export_params_csv()
    return bytes(r.body).decode("utf-8-sig")


def _dry_run(text: str) -> dict:
    up = UploadFile(filename="params.csv", file=io.BytesIO(text.encode("utf-8-sig")))
    return asyncio.run(import_params(file=up, dry_run=1, _admin={"role": "admin"}))


def test_export_has_the_six_parameter_io_columns():
    rows = list(csv.reader(io.StringIO(_csv_text())))
    assert rows[0] == HEADER
    assert len(rows) > 1
    for r in rows[1:]:
        assert len(r) == 6, r


def test_export_value_mirrors_expression_and_favorite_is_false():
    rows = list(csv.reader(io.StringIO(_csv_text())))[1:]
    for name, unit, expr, value, _comment, fav in rows:
        assert name
        assert unit in ("", "mm")
        # Parameter I/O puts the EVALUATED expression in Value; for a plain
        # constant (and for a unitless row) that is the same bare number.
        assert value == expr, name
        assert fav == "False", name


def test_export_is_primaries_only():
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    names = [r[0] for r in list(csv.reader(io.StringIO(_csv_text())))[1:]]
    assert len(names) == len(set(names))
    assert not (set(names) & set(DERIVED_GEOMETRY_NAMES))
    # ...and minus the explicit exclusions (user 2026-09-14: "выкинь slot_hs,
    # мы его не используем") — a hidden parameter the model does not drive.
    assert "slot_hs" in FUSION_EXCLUDED_NAMES
    assert not (set(names) & set(FUSION_EXCLUDED_NAMES))
    assert len(names) == len(_rows())


def test_comment_commas_are_quoted_not_split_into_extra_columns():
    """Several schema descriptions contain commas ("…, so hidden from the UI").

    ``csv.writer`` is QUOTE_MINIMAL, so those fields are wrapped in quotes and
    the row is still six fields — RFC 4180, which is what Parameter I/O's own
    export writes and what its importer reads.  Counting a row's fields with
    ``line.split(",")`` says 7 or 8 and is simply the wrong measurement: this
    test asserts the discrepancy on purpose, so a future "fix" that makes the
    naive count come out at six (by stripping the commas, or by quoting
    everything into one field) fails here."""
    text = _csv_text()
    rows = list(csv.reader(io.StringIO(text)))
    assert {len(r) for r in rows} == {6}

    commented = [r for r in rows[1:] if "," in r[4]]
    assert commented, "expected at least one schema description with a comma"
    lines = text.splitlines()
    for r in commented:
        line = next(ln for ln in lines if ln.startswith(r[0] + ","))
        assert '"' in line, r[0]                      # the field IS quoted
        assert len(line.split(",")) > 6, r[0]         # naive split over-counts
        # and the quoted comment survives the quoting intact
        assert r[4].count(",") == len(line.split(",")) - 6


def test_six_column_export_round_trips_through_the_import_parser():
    out = _dry_run(_csv_text())
    assert out["ok"] and out["dry_run"] is True
    assert out["refused"] == {}
    assert out["unknown"] == []
    # Same machine in, same machine out: nothing differs from the live values.
    assert out["applied"] == {}
    assert len(out["unchanged"]) == len(_rows())
    # The file fed in above already carries quoted, comma-bearing comments.
    assert any("," in r[4] for r in list(csv.reader(io.StringIO(_csv_text())))[1:])


def test_import_reads_quoted_fields_including_a_comma_comment_row():
    """A comma (and an embedded quote) in Comment must not shift Expression.

    The parser is ``csv.DictReader`` — a ``csv.reader`` underneath — so the
    quoted comment stays one field and Expression is still column three.  The
    row here is bumped by +1 mm, so if the comment ever leaked into the value
    column the import would refuse it ("not a plain number") instead of
    applying it, and this assert would catch that."""
    rows = list(csv.reader(io.StringIO(_csv_text())))
    hdr, body = rows[0], [list(r) for r in rows[1:]]
    target = next(r for r in body if r[1] == "mm")
    bumped = "%g" % (float(target[2]) + 1.0)
    for r in body:
        if r[0] == target[0]:
            r[2] = r[3] = bumped
            r[4] = 'width, height and "depth", in mm — commas, quotes and all'
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(hdr)
    w.writerows(body)
    text = buf.getvalue()
    assert {len(r) for r in csv.reader(io.StringIO(text))} == {6}

    out = _dry_run(text)
    assert out["dry_run"] is True
    assert out["refused"] == {}
    assert set(out["applied"]) == {target[0]}
    assert abs(out["applied"][target[0]] - float(bumped)) < 1e-6


def test_import_still_accepts_the_old_four_column_file():
    """Header-name matching, not position — Value/Favorite are simply absent."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Name", "Unit", "Expression", "Comment"])
    for r in list(csv.reader(io.StringIO(_csv_text())))[1:]:
        w.writerow([r[0], r[1], r[2], r[4]])
    old = _dry_run(buf.getvalue())
    new = _dry_run(_csv_text())
    assert old["refused"] == {} and old["unknown"] == []
    assert old["unchanged"] == new["unchanged"]
    assert old["applied"] == new["applied"] == {}


def test_import_ignores_a_slot_hs_row_from_an_older_file():
    """An older four-column CSV still carries the row we no longer export.

    It must be swallowed the way a derived row is — listed under
    ``excluded_ignored``, never applied, never counted as ``unknown`` (the
    key IS a geometry key) and never ``refused`` (the number is perfectly
    parseable).  The value below deliberately differs from the live one, so a
    regression that let it through would show up in ``applied``."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Name", "Unit", "Expression", "Comment"])
    for r in list(csv.reader(io.StringIO(_csv_text())))[1:]:
        w.writerow([r[0], r[1], r[2], r[4]])
    w.writerow(["slot_hs", "mm", "0.42",
                "Distance from the last wire to the air gap, hidden from the UI"])
    out = _dry_run(buf.getvalue())

    assert out["ok"] and out["dry_run"] is True
    assert out["excluded_ignored"] == ["slot_hs"]
    assert out["applied"] == {}
    assert out["unknown"] == [] and out["refused"] == {}
    assert "slot_hs" not in out["unchanged"]
    assert len(out["unchanged"]) == len(_rows())


def test_value_column_is_ignored_expression_is_what_is_read():
    """A Value column contradicting Expression must not reach the geometry."""
    rows = list(csv.reader(io.StringIO(_csv_text())))
    hdr, body = rows[0], rows[1:]
    target = next(r for r in body if r[1] == "mm")
    bumped = [list(r) for r in body]
    for r in bumped:
        if r[0] == target[0]:
            r[2] = "%g" % (float(target[2]) + 1.0)    # Expression: +1 mm
            r[3] = target[2]                          # Value: the OLD number
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(hdr)
    w.writerows(bumped)
    out = _dry_run(buf.getvalue())
    assert out["dry_run"] is True
    assert set(out["applied"]) == {target[0]}
    assert abs(out["applied"][target[0]] - (float(target[2]) + 1.0)) < 1e-6
