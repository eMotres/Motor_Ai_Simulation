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

import copy
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
        if v is None or v == "":
            return None
        if not isinstance(v, str) or v not in _options(field):
            raise ValueError(f"{path}: not one of the choices")
        return v
    if kind == "multi":
        v = [] if v is None else v
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v) or set(v) - _options(field):
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
        v = {} if v is None else v
        if not isinstance(v, dict) or set(v) - _options(field):
            raise ValueError(f"{path}: not among the choices")
        out = {}
        for opt in (o["value"] for o in field["options"]):
            if opt not in v:
                continue
            item = v[opt]
            if not isinstance(item, dict):                # a bare number is not silently emptied (Astra on #834)
                raise ValueError(f"{path}.{opt}: unit / values expected")
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


def _per_build(field: dict, a: Any, builds: list, fid: str) -> dict:
    """A per-build answer.  ``{"builds": {...}}`` names only builds of the builds list (a 422 names any other).  A single answer
    (stored before the field became per build, e.g. Control method) is the same answer for every build; with no builds yet it is
    kept as it is and spread when builds appear - a valid earlier answer is never dropped (Astra on #834)."""
    if isinstance(a, dict) and set(a) == {"builds"}:
        answers = a["builds"]
        if not isinstance(answers, dict) or len(answers) > _LIST_MAX:
            raise ValueError(f"{fid}: one answer per build expected")
        stray = [str(b) for b in answers if b not in builds]
        if stray:
            raise ValueError(f"{fid}: answer for build {', '.join(stray)}, which is not in the builds list")
        return {"builds": {b: _answer(field, answers[b], f"{fid}.{b}") for b in builds if b in answers}}
    single = _answer(field, a, fid)
    return {"builds": {b: copy.deepcopy(single) for b in builds}} if builds else single


def normalize(raw: Any) -> dict:
    """The submitted ``fields`` map checked against the schema; ``ValueError`` names the first wrong value.  The per-build
    answers are checked against the builds list of the SAME map."""
    if not isinstance(raw, dict):
        raise ValueError("fields must be an object")
    known = fields()
    unknown = sorted(set(raw) - set(known))
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(unknown)}")
    out: dict = {}
    if "builds" in raw:
        out["builds"] = _answer(known["builds"], raw["builds"], "builds")
        raw_list = (raw["builds"] or {}).get("value") if isinstance(raw["builds"], dict) else None
        if isinstance(raw_list, list) and any(isinstance(x, str) and not x.strip() for x in raw_list):
            raise ValueError("builds: a build name cannot be empty")
        for b in out["builds"]["value"]:
            if "." in b or b.casefold() in COLUMNS:          # a build name is never read as a column or a path (Astra on #836)
                raise ValueError(f"builds: '{b}' cannot be a build name (no dot, not requirement / model / measured)")
    builds = list((out.get("builds") or {}).get("value") or [])
    for fid, a in raw.items():
        if fid == "builds":
            continue
        field = known[fid]
        out[fid] = _per_build(field, a, builds, fid) if field.get("per_build") else _answer(field, a, fid)
    return out


def stored(raw: Any) -> dict:
    """A stored ``fields`` map in today's shape: an earlier answer is read into its column, a single answer of a field that is per
    build now is spread over the builds (``_per_build``).  Only a value that fits no shape at all is left out rather than failing
    the read; the file itself is never rewritten by a read."""
    if not isinstance(raw, dict):
        return {}
    try:
        return normalize(raw)
    except ValueError:
        pass
    known = fields()
    builds_raw = raw.get("builds")
    try:
        builds = list((_answer(known["builds"], builds_raw, "builds") if builds_raw is not None else {}).get("value") or [])
    except ValueError:
        builds = []
    out = {"builds": {"value": builds}} if builds_raw is not None and builds else {}
    for fid, a in raw.items():
        if fid == "builds" or fid not in known:
            continue
        field = known[fid]
        try:
            if field.get("per_build") and isinstance(a, dict) and set(a) == {"builds"} and isinstance(a["builds"], dict):
                # answers of a build no longer in the list are kept for that build (the form asks before it drops them)
                keep = [b for b in builds if b in a["builds"]] + [b for b in a["builds"] if b not in builds]
                out[fid] = {"builds": {b: _answer(field, a["builds"][b], f"{fid}.{b}") for b in keep}}
            else:
                out[fid] = _per_build(field, a, builds, fid) if field.get("per_build") else _answer(field, a, fid)
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


def _parts(field: dict, a: Any) -> tuple[dict, dict]:
    """A numeric answer as structured parts, never a joined string (Astra on #836): ``cells[(build, option, column)] = cell`` and,
    for protections, ``meta[(build, option)] = unit`` - a protection that is ticked counts even with no value in any column."""
    cells: dict = {}
    meta: dict = {}
    if not isinstance(a, dict):
        return cells, meta
    parts = list(a["builds"].items()) if isinstance(a.get("builds"), dict) and set(a) == {"builds"} else [(None, a)]
    for build, ans in parts:
        if not isinstance(ans, dict):
            continue
        if field["kind"] == "thresholds":
            for opt, item in (ans.get("value") or {}).items():
                item = item if isinstance(item, dict) else {}
                meta[(build, opt)] = item.get("unit")
                for col, cell in (item.get("values") or {}).items():
                    cells[(build, opt, col)] = cell
        else:
            for col, cell in (ans.get("values") or {}).items():
                cells[(build, None, col)] = cell
    return cells, meta


def change_scope(before: dict, after: dict) -> set:
    """Which side a save touches, from the two normalized ``fields`` maps: ``requirements`` (the requirement column, a protection
    ticked or not and its unit, every field without columns - the Specification tab: project owner and admins) and / or
    ``results`` (the model and measured columns - the Controller sim tab: MOTRES staff; owner 2026-11-23)."""
    out: set = set()
    for fid, field in fields().items():
        b, a = before.get(fid), after.get(fid)
        if b == a:
            continue
        if field["kind"] not in NUMERIC:
            out.add("requirements")
            continue
        (bc, bm), (ac, am) = _parts(field, b), _parts(field, a)
        if bm != am:
            out.add("requirements")
        for key in set(bc) | set(ac):
            if bc.get(key) != ac.get(key):
                out.add("requirements" if key[2] == "requirement" else "results")
    return out
