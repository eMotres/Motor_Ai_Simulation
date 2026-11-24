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


def _plain_number(text: str) -> bool:
    """Only a leading minus is exempt (``%g`` of a negative number); ``+3`` and ``=3`` stay neutralised."""
    if not text.startswith("-"):
        return False
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        return False


def csv_safe(value):
    """A CSV text field that cannot start a formula in a spreadsheet application.

    Quoting is not neutralisation: a string that starts with ``=``, ``+``, ``-``, ``@``, tab or carriage return gets a leading
    apostrophe.  Numbers (and strings that are plain numbers, such as ``-5`` written with ``%g``) are returned unchanged.
    """
    if is_formula_like(value) and not _plain_number(value):
        return "'" + value
    return value


def csv_safe_row(row):
    return [csv_safe(v) for v in row]


def force_text_cell(cell) -> None:
    """One user-text cell of a worksheet that also carries real formulas: text it stays."""
    if cell.data_type != "s" and is_formula_like(cell.value):
        cell.data_type = "s"
