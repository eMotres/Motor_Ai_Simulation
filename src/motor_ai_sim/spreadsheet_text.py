"""Untrusted text in a spreadsheet export stays text.

A cell whose text starts with ``=``, ``+``, ``-`` or ``@`` (or a tab / carriage return) is stored by openpyxl as a FORMULA, and a
spreadsheet application may evaluate it or show an attacker-written link.  :func:`force_text_cells` marks every such cell of a worksheet
as a string cell, so the file carries the text and no formula element.  Numbers and the real formulas a workbook writes on purpose are
not touched: call it only on a worksheet whose cells are data.
"""
from __future__ import annotations

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def is_formula_like(value) -> bool:
    return isinstance(value, str) and value.startswith(FORMULA_STARTS)


def force_text_cells(ws) -> int:
    """Make every formula-looking string cell of ``ws`` a plain string cell; returns how many were changed."""
    n = 0
    for row in ws.iter_rows():
        for cell in row:
            if cell.data_type != "s" and is_formula_like(cell.value):
                cell.data_type = "s"
                n += 1
    return n
