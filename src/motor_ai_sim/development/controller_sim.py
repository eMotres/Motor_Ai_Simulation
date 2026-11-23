"""The records of a controller project's "Controller sim" tab (owner 2026-11-23: "the test / simulation results must be published
separately"): the board revisions (R04 ... R08 with their status and date) and the test results as they arrive.  The results
against each requirement live in the specification itself (``controller_spec``: the model / measured columns).

Stored beside the project as ``controller_sim.json`` (``{revision, updated_at, updated_by, revisions, tests}``), revision-checked;
entered by MOTRES staff only (global engineer / admin), read by every project member.  Internal: no MCP tool, no assistant."""
from __future__ import annotations

import re
from typing import Any

FILE = "controller_sim.json"
REVISION_STATUSES = ("draft", "frozen", "released")
RESULTS = ("pass", "fail")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_ROWS = 200


def empty() -> dict:
    return {"revision": 0, "updated_at": None, "updated_by": None, "revisions": [], "tests": []}


def _text(v: Any, path: str, limit: int) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(f"{path}: text is expected")
    if len(v) > limit:
        raise ValueError(f"{path}: at most {limit} characters")
    return v.strip()


def _date(v: Any, path: str) -> str:
    t = _text(v, path, 10)
    if t and not _DATE.match(t):
        raise ValueError(f"{path}: a date YYYY-MM-DD is expected")
    return t


def _choice(v: Any, path: str, allowed: tuple) -> str:
    if v is None or v == "":
        return ""
    if not isinstance(v, str) or v not in allowed:
        raise ValueError(f"{path}: one of {', '.join(allowed)} expected")
    return v


def _rows(raw: Any, path: str, keys: dict) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > _MAX_ROWS:
        raise ValueError(f"{path}: a list of at most {_MAX_ROWS} rows")
    out = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict) or set(row) - set(keys):
            raise ValueError(f"{path}[{i}]: {', '.join(keys)} expected")
        clean = {k: check(row.get(k), f"{path}[{i}].{k}") for k, check in keys.items()}
        if any(clean.values()):
            out.append(clean)
    return out


REVISION_KEYS = {"rev": lambda v, p: _text(v, p, 40), "status": lambda v, p: _choice(v, p, REVISION_STATUSES),
                 "date": _date, "documents": lambda v, p: _text(v, p, 200), "note": lambda v, p: _text(v, p, 500)}
TEST_KEYS = {"date": _date, "revision": lambda v, p: _text(v, p, 40), "name": lambda v, p: _text(v, p, 120),
             "value": lambda v, p: _text(v, p, 120), "result": lambda v, p: _choice(v, p, RESULTS),
             "ref": lambda v, p: _text(v, p, 200)}


def normalize(body: Any) -> dict:
    """``{revisions, tests}`` checked; ``ValueError`` names the first wrong value.  Empty rows are dropped."""
    if not isinstance(body, dict) or set(body) - {"revisions", "tests"}:
        raise ValueError("revisions / tests expected")
    return {"revisions": _rows(body.get("revisions"), "revisions", REVISION_KEYS), "tests": _rows(body.get("tests"), "tests", TEST_KEYS)}
