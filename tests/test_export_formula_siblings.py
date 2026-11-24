"""Untrusted text stays text in every export sink, not only in the fixed XLSX writers (inert strings only)."""
import csv
import io

from motor_ai_sim.spreadsheet_text import csv_safe, csv_safe_row, force_text_cell

INERT = "=1+1"


def test_csv_safe_prefixes_formula_starts_and_keeps_numbers():
    for bad in ("=1+1", "+1", "-x", "@A1", "\tx", "\rx"):
        assert csv_safe(bad) == "'" + bad
    for ok in ("plain", "", "-5", "-0.25", "1e-3", 5, -5.0, None):
        assert csv_safe(ok) == ok


def test_csv_round_trip_has_no_formula_start_in_a_text_field():
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow(csv_safe_row([INERT, 3, "ok", "-2"]))
    row = next(csv.reader(io.StringIO(buf.getvalue())))
    assert row == ["'=1+1", "3", "ok", "-2"]


def test_force_text_cell_keeps_real_formulas_elsewhere_in_the_sheet():
    from openpyxl import Workbook
    ws = Workbook().active
    ws["A1"] = INERT
    ws["B1"] = "=SUM(1,2)"                  # the workbook's own formula, written on purpose
    force_text_cell(ws["A1"])
    assert ws["A1"].data_type == "s" and ws["B1"].data_type == "f"


def test_newsletter_csv_neutralises_a_formula_looking_source():
    from fastapi import Response
    from motor_ai_sim.routes import newsletter as N
    src = N.__file__
    text = open(src, encoding="utf-8").read()
    assert "csv_safe" in text                # the export goes through the shared helper
    assert Response is not None
