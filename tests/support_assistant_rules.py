"""String rules shared by the assistant's tests and its live check.

An answer must never send a regular user to a tab, menu or page their account
does not have.  The rule is deliberately about WORDS NEXT TO "tab" (a "Thermal
block" inside Configure is fine; "the Thermal tab" is not), so it can be applied
to a real model's free text as well as to the canned fixture replies.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "support_assistant_exchanges.json"

#: Every tab of the app except the two a regular account has.
USER_MISSING_TABS = ("Geometry", "Materials", "Mesh", "Electromagnetic", "3D", "Mechanical",
                     "Thermal", "Controller", "Optimization", "Compare", "Cost", "Admin",
                     "Simulation", "Report")


def exchanges() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["exchanges"]


def missing_tab_mentions(text: str, names=USER_MISSING_TABS) -> list[str]:
    """The tab names that ``text`` presents as a tab / menu / page / screen."""
    hits = []
    for n in names:
        pats = (
            rf"\*{{0,2}}[\"'“]?{re.escape(n)}[\"'”]?\*{{0,2}}\s+(?:tab|menu|page|screen)\b",
            rf"\b(?:tab|menu|page|screen)\s+(?:called\s+|named\s+)?[\"'“*]*{re.escape(n)}\b",
        )
        if any(re.search(p, text, flags=re.IGNORECASE) for p in pats):
            hits.append(n)
    return hits


def check_answer(ex: dict, reply: str) -> list[str]:
    """Problems with one answer to one fixture question (empty list = fine)."""
    bad = []
    if ex["role"] == "user":
        bad += [f"mentions a tab the account does not have: {n}"
                for n in missing_tab_mentions(reply)]
    any_of = ex.get("must_contain_any") or []
    if any_of and not any(s.lower() in reply.lower() for s in any_of):
        bad.append(f"none of {any_of} in the answer")
    for s in ex.get("must_contain_all") or []:
        if s.lower() not in reply.lower():
            bad.append(f"missing {s!r}")
    for marker in ("[[TICKET_DRAFT", "[[ACCESS_REQUEST"):
        if marker in reply:
            bad.append(f"{marker} reached the reader")
    return bad
