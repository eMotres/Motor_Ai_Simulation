"""Untrusted text in a spreadsheet export is a string cell, never a formula."""
from __future__ import annotations

import io
import zipfile

from motor_ai_sim import spreadsheet_text as ST
from motor_ai_sim.production import bom_xlsx

CANARIES = ["=1+1", "+1+1", "-2+3", "@SUM(1,1)", "=HYPERLINK(\"https://example.test/x\",\"a\")"]


def _sheet_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("xl/worksheets/sheet1.xml").decode("utf-8")


def test_formula_looking_text_is_stored_as_text_in_every_textual_column():
    parts = [{"id": "p%d" % i, "name": {"en": c, "zh": c}, "kind": "made", "material_i18n": {"en": c}, "spec_i18n": {"en": c}}
             for i, c in enumerate(CANARIES)]
    bom = [{"part_id": p["id"], "qty_per_motor": 2} for p in parts]
    costs = [{"part_id": p["id"], "unit_price": 1.5, "currency": "EUR", "note": c} for p, c in zip(parts, CANARIES)]
    data = bom_xlsx.build_workbook(parts, bom, costs, "")
    xml = _sheet_xml(data)
    assert "<f>" not in xml and "<f " not in xml                                # no formula element anywhere
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(data)).active
    for r, c in enumerate(CANARIES, start=2):
        assert ws.cell(r, 2).value == c and ws.cell(r, 3).value == c and ws.cell(r, 6).value == c and ws.cell(r, 7).value == c
        assert ws.cell(r, 10).value == c and ws.cell(r, 2).data_type == "s"       # it imports back unchanged, as text
        assert ws.cell(r, 5).value == 2 and ws.cell(r, 8).value == 1.5            # the numbers stay numbers


def test_helper_touches_only_formula_looking_strings():
    from openpyxl import Workbook
    ws = Workbook().active
    ws.append(["plain", "=1+1", 5, "-x", None, "a=b"])
    assert ST.force_text_cells(ws) == 1                      # only "=..." is turned into a formula cell by openpyxl; the rest is text already
    assert [c.data_type for c in ws[1]][:4] == ["s", "s", "n", "s"]
