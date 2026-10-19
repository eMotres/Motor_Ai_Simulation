"""Sanitisers for catalogue text that leaves the building (MCP and non-admin HTTP).

The catalogue cards were written for the owner and the engineers: they carry
warehouse stock (batches, lots, kilograms left), attributions ("owner
2026-10-16"), design notes about our own machines ("does NOT fit the CIANO14 50
holder"), measurement document paths and operating-point remarks.  None of it is
a datasheet fact.  These helpers keep what a datasheet would print and drop the
rest, by clause, so a card is never blanked just because one sentence of its
description was internal.

Pure string functions - no I/O, no catalogue imports.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# A parenthetical / bracketed group that is internal commentary or stock.
_INTERNAL = re.compile(
    r"""(?ix)
    \b(owner|user)\b
  | \bciano\w*
  | \bg2\b
  | \bholder\b | \bflange\b | \bback\s+tube\b
  | \bdoes\s+not\s+(fit|reach)\b
  | docs?/ | tests?/ | \.md\b | \.yaml\b | \.py\b
  | \bthe\s+(real|\d+\s*mm)\b
  | \b\d+\s*mm\s+(motor|machine)s?\b
  | \bregression\b | \bpinned\b | \bfree-?run\b
  | \bcomparison\b | \bshaft\s+(seat|bore)\b | \bsensible\b
  | \bto\s+verify\b | \bverify\b | \bTODO\b
  | \b\d{1,3}[\s ]?\d{3}\s*rpm\b
  | \bbatch(es)?\b | \blots?\b | \bstock\b | \bwarehouse\b | \d\s*kg\b
  | \bW[0-9A-Z]{5,}\d{4,}\b
    """)

_GROUP = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_ATTRIBUTION = re.compile(
    r"[,;]?\s*\b(owner|user)\s+\d{4}-\d{2}-\d{2}(?=[\s).,;]|$)", re.I)
_WS = re.compile(r"\s{2,}")


def _split_clauses(s: str) -> list:
    """Split after '.' / ';' + space, but never inside brackets."""
    parts, start, depth = [], 0, 0
    for m in re.finditer(r"[()\[\]]|[.;]\s+", s):
        tok = m.group(0)
        if tok in "([":
            depth += 1
        elif tok in ")]":
            depth = max(0, depth - 1)
        elif depth == 0:
            parts.append(s[start:m.start() + 1])
            start = m.end()
    parts.append(s[start:])
    return parts


def public_text(text: Any) -> Any:
    """`text` without internal clauses.  Non-strings pass through unchanged.

    1. parenthetical groups that mention internal markers are removed;
    2. loose "owner 2026-10-16" attributions are removed;
    3. each remaining sentence / semicolon clause that still carries an
       internal marker is dropped.
    """
    if not isinstance(text, str) or not text:
        return text
    s = _GROUP.sub(lambda m: "" if _INTERNAL.search(m.group(0)) else m.group(0), text)
    s = _ATTRIBUTION.sub("", s)
    parts = [p for p in _split_clauses(s.strip()) if p and not _INTERNAL.search(p)]
    out = " ".join(parts).strip()
    out = _WS.sub(" ", out)
    out = re.sub(r"\s+([,.;])", r"\1", out)
    out = re.sub(r"[\s,;—-]+$", "", out)
    return out


def _wire_spec_fallback(thickness: Optional[float], width: Optional[float]) -> str:
    if thickness is None or width is None:
        return ""
    return f"{thickness:g}*{width:g}"


def public_wire_spec(spec: Any, thickness_mm: Optional[float] = None,
                     width_mm: Optional[float] = None) -> str:
    """Technical wire designation only: no batches, lots, kilograms or stock.

    ``"SFT-AIW 0.300x2.500 (batches W012020...3B260617 9.18 kg, ...)"`` ->
    ``"SFT-AIW 0.300x2.500"``; ``"1*7 (two lots: 302.849 + 18.534 kg)"`` ->
    ``"1*7"``.
    """
    s = str(spec or "")
    s = _GROUP.sub(lambda m: "" if _INTERNAL.search(m.group(0)) else m.group(0), s)
    s = re.sub(r"\b\d+(\.\d+)?\s*kg\b", "", s, flags=re.I)
    s = _WS.sub(" ", s).strip(" ,;:-")
    return s or _wire_spec_fallback(thickness_mm, width_mm)


def public_strings(row: Dict[str, Any], *, skip: tuple = ("id",)) -> Dict[str, Any]:
    """Copy of `row` with `public_text` applied to every top-level string value
    (keys are kept - a consumer that reads a key still finds it)."""
    return {k: (v if k in skip else public_text(v)) for k, v in row.items()}
