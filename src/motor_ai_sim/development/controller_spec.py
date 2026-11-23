"""The specification of a CONTROLLER project (``project_type`` = controller; owner 2026-11-22, coordinator 2026-11-23).

The fields are a data file, ``controller_schema.json`` (7 steps, EN + zh-CN), so the owner can change the list without code.
Every numeric value (number, range, each protection threshold) holds up to THREE values side by side - ``requirement``,
``model`` and ``measured`` - each with its own source (owner 2026-11-23: the requirements first, then the simulation results,
measured later).  An answer stored with the earlier one-value-one-status shape is read into the column its status named.  A field marked ``per_build`` holds one answer per build
(6S, 12S ...).

Stored beside the project as ``controller_spec.json`` (``{schema_version, revision, updated_at, updated_by, fields}``); every
save appends what changed to ``controller_spec_history.jsonl``.  The same edit rule as the motor specification: the project
owner and global admins change it (``access.require_spec_editor``).  A motor project never has these files.  Internal only:
no order flow, no prices, no MCP tool and no specification assistant read it."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("controller_schema.json")
FILE = "controller_spec.json"
HISTORY = "controller_spec_history.jsonl"
STATUSES = ("requirement", "model", "measured")
COLUMNS = STATUSES                                       # the three values of a numeric answer, in this order
NUMERIC = ("number", "range", "thresholds")
_TEXT_MAX = {"text": 200, "longtext": 4000}
_REF_MAX = 200
_LIST_MAX = 20
_ROWS_MAX = 50


@lru_cache(maxsize=1)
def schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def fields() -> dict[str, dict]:
    return {f["id"]: f for s in schema()["sections"] for f in s["fields"]}


def empty() -> dict:
    return {"schema_version": schema()["schema_version"], "revision": 0, "updated_at": None, "updated_by": None, "fields": {}}


def _number(v: Any, path: str) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ValueError(f"{path}: a number is expected")
    return float(v)


def _text(v: Any, path: str, limit: int) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(f"{path}: text is expected")
    if len(v) > limit:
        raise ValueError(f"{path}: at most {limit} characters")
    return v.strip()


def _options(field: dict) -> set:
    return {o["value"] for o in field.get("options") or []}


def _value(field: dict, v: Any, path: str) -> Any:
    kind = field["kind"]
    if kind in _TEXT_MAX:
        return _text(v, path, _TEXT_MAX[kind])
    if kind == "number":
        return _number(v, path)
    if kind == "range":
        if v is None:
            return None
        if not isinstance(v, dict) or set(v) - {"min", "nom", "max"}:
            raise ValueError(f"{path}: min / nom / max expected")
        out = {k: _number(v.get(k), f"{path}.{k}") for k in ("min", "nom", "max")}
        lo, hi = out["min"], out["max"]
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"{path}: min is above max")
        return out
    if kind == "choice":
        if v in (None, ""):
            return None
        if v not in _options(field):
            raise ValueError(f"{path}: not one of the choices")
        return v
    if kind == "multi":
        v = v or []
        if not isinstance(v, list) or set(v) - _options(field):
            raise ValueError(f"{path}: not among the choices")
        return [o["value"] for o in field["options"] if o["value"] in v]          # schema order, no duplicates
    if kind == "list":
        v = v or []
        if not isinstance(v, list) or len(v) > _LIST_MAX:
            raise ValueError(f"{path}: a list of at most {_LIST_MAX} entries")
        out = []
        for i, item in enumerate(v):
            t = _text(item, f"{path}[{i}]", 40)
            if t and t not in out:
                out.append(t)
        return out
    if kind == "thresholds":
        v = v or {}
        if not isinstance(v, dict) or set(v) - _options(field):
            raise ValueError(f"{path}: not among the choices")
        out = {}
        for opt in (o["value"] for o in field["options"]):
            if opt not in v:
                continue
            item = v[opt] if isinstance(v[opt], dict) else {}
            p = f"{path}.{opt}"
            if "values" in item:
                if set(item) - {"unit", "values"}:
                    raise ValueError(f"{p}: unit / values expected")
                cols = item["values"]
            else:                                        # the earlier shape: one threshold with its status
                if set(item) - {"threshold", "unit", "status"}:
                    raise ValueError(f"{p}: unit / values expected")
                cols = {_status(item.get("status"), p) or "requirement": {"value": item.get("threshold")}}
            out[opt] = {"unit": _text(item.get("unit"), f"{p}.unit", 20), "values": _columns(cols, p, lambda x, q: _number(x, q))}
        return out
    if kind == "rows":
        v = v or []
        cols = [c["id"] for c in field["columns"]]
        if not isinstance(v, list) or len(v) > _ROWS_MAX:
            raise ValueError(f"{path}: at most {_ROWS_MAX} rows")
        out = []
        for i, row in enumerate(v):
            if not isinstance(row, dict) or set(row) - set(cols):
                raise ValueError(f"{path}[{i}]: columns {', '.join(cols)} expected")
            r = {c: _text(row.get(c), f"{path}[{i}].{c}", 200) for c in cols}
            if any(r.values()):
                out.append(r)
        return out
    raise ValueError(f"{path}: unknown kind {kind}")


def _status(v: Any, path: str) -> str | None:
    if v in (None, ""):
        return None
    if v not in STATUSES:
        raise ValueError(f"{path}: status must be one of {', '.join(STATUSES)}")
    return v


def _columns(raw: Any, path: str, check) -> dict:
    """``{column: {"value", "ref"}}`` for the columns that hold a value or a source; ``check`` validates one value."""
    if not isinstance(raw, dict) or set(raw) - set(COLUMNS):
        raise ValueError(f"{path}: columns {', '.join(COLUMNS)} expected")
    out = {}
    for col in COLUMNS:
        cell = raw.get(col)
        if cell is None:
            continue
        if not isinstance(cell, dict) or set(cell) - {"value", "ref"}:
            raise ValueError(f"{path}.{col}: value / ref expected")
        value = check(cell.get("value"), f"{path}.{col}")
        ref = _text(cell.get("ref"), f"{path}.{col}.ref", _REF_MAX)
        empty = value is None or (isinstance(value, dict) and all(x is None for x in value.values()))
        if not empty or ref:
            out[col] = {"value": None if empty else value, "ref": ref}
    return out


def _answer(field: dict, a: Any, path: str) -> dict:
    if field["kind"] in ("number", "range"):           # three values side by side, each with its source
        if not isinstance(a, dict):
            raise ValueError(f"{path}: values expected")
        if "values" in a:
            if set(a) - {"values"}:
                raise ValueError(f"{path}: values expected")
            cols = a["values"]
        else:                                          # the earlier shape: one value, its status names the column
            if set(a) - {"value", "status", "ref"}:
                raise ValueError(f"{path}: values expected")
            cols = {_status(a.get("status"), path) or "requirement": {"value": a.get("value"), "ref": a.get("ref")}}
        return {"values": _columns(cols, path, lambda x, q: _value(field, x, q))}
    if not isinstance(a, dict) or set(a) - {"value"}:
        raise ValueError(f"{path}: value expected")
    return {"value": _value(field, a.get("value"), path)}


def normalize(raw: Any) -> dict:
    """The submitted ``fields`` map checked against the schema; ``ValueError`` names the first wrong value."""
    if not isinstance(raw, dict):
        raise ValueError("fields must be an object")
    known = fields()
    unknown = sorted(set(raw) - set(known))
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(unknown)}")
    out: dict = {}
    for fid, a in raw.items():
        field = known[fid]
        if field.get("per_build"):
            builds = a.get("builds") if isinstance(a, dict) and set(a) == {"builds"} else None
            if not isinstance(builds, dict) or len(builds) > _LIST_MAX:
                raise ValueError(f"{fid}: one answer per build expected ({{\"builds\": {{...}}}})")
            out[fid] = {"builds": {str(b)[:40]: _answer(field, x, f"{fid}.{b}") for b, x in builds.items()}}
        else:
            out[fid] = _answer(field, a, fid)
    return out


def stored(raw: Any) -> dict:
    """A stored ``fields`` map in today's shape (an earlier answer is read into its column); a value that no longer fits the
    schema is left out of the answer rather than failing the read (the file itself is never rewritten by a read)."""
    out = {}
    for fid, a in (raw or {}).items() if isinstance(raw, dict) else ():
        try:
            out.update(normalize({fid: a}))
        except ValueError:
            continue
    return out


def _cells(field: dict, a: Any) -> dict:
    """``{"<build.>column[.option]": cell}`` of a numeric answer, for naming the columns a save changed."""
    out: dict = {}
    if not isinstance(a, dict):
        return out
    parts = a["builds"].items() if "builds" in a and isinstance(a.get("builds"), dict) else [("", a)]
    for build, ans in parts:
        pre = f"{build}." if build else ""
        if not isinstance(ans, dict):
            continue
        if field["kind"] == "thresholds":
            for opt, item in (ans.get("value") or {}).items():
                for col, cell in (item.get("values") or {}).items():
                    out[f"{pre}{col}.{opt}"] = (cell, item.get("unit"))
        else:
            for col, cell in (ans.get("values") or {}).items():
                out[f"{pre}{col}"] = cell
    return out


def changes(before: dict, after: dict) -> list[dict]:
    """Field by field what a save changed (``before`` / ``after`` as stored; a removed field is ``None``).  A numeric field also
    names the columns that changed (``columns``: ``requirement`` / ``model`` / ``measured``, with the build and the protection)."""
    out = []
    known = fields()
    for fid in [f for f in known if f in before or f in after]:
        if before.get(fid) != after.get(fid):
            row = {"field": fid, "before": before.get(fid), "after": after.get(fid)}
            if known[fid]["kind"] in NUMERIC:
                b, a = _cells(known[fid], before.get(fid)), _cells(known[fid], after.get(fid))
                row["columns"] = sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))
            out.append(row)
    return out
