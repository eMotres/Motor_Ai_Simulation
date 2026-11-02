"""Project cost estimate (owner 2026-11-02): an ADMIN-ONLY questionnaire that estimates what a development project costs us.

After the simulation there is no full BOM of the motor, so the cost is ESTIMATED from other projects: the real BOMs, cost
rows and tooling of motors already built (Production), the test costs other projects recorded, and the masses of the
simulation.  Where nothing can be based on, the owner enters the number himself.

* A project has ITERATIONS (1..5; usually 2, at least 3 for a complex project).  Each iteration splits into MANUFACTURING,
  TESTING and LOGISTICS; iteration 1 carries most of the tooling, later iterations default to "modifications" (a % of
  iteration 1's tooling line) plus parts and testing.  PROJECT-WIDE lines (engineering hours, travel, warranty) come on top.
* The line catalogue is ``cost_estimate_schema.json`` (sections with items, help text, unit and the default suggestion
  rule), so lines are added without code.
* Every line keeps where its number came from: ``source`` (``analog`` - another production's cost / tooling row, scaled;
  ``test_history`` - another project's test cost; ``model`` - a simulation mass x a price per kg; ``manual`` - the owner;
  ``rule`` - a default rule such as "20 % of iteration 1"), ``source_ref``, the scale rule and factor, date and note.
* Totals are PER CURRENCY (EUR / CNY / USD): no exchange rate exists in the app and none is invented here.

This module is pure (no I/O): schema, defaults, validation, totals, the scale rule, ``suggest`` over gathered source data,
and the diff between two estimates.  Storage and data gathering live in ``cost_estimate_store`` / ``cost_estimate_sources``,
the admin routes in ``cost_estimate_routes``.  Nothing here is ever shown to a customer, a project viewer, the factory (OEM)
or an MCP agent.
"""
from __future__ import annotations

import copy
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_PATH = Path(__file__).with_name("cost_estimate_schema.json")
VERSION = 1
SOURCES = ("analog", "test_history", "model", "manual", "rule")
STATUSES = ("filled", "not_applicable", "missing")
#: sources ``suggest`` may refresh (never ``manual``: the owner's numbers are never overwritten)
AUTO_SOURCES = ("analog", "test_history", "model", "rule")
_MAX_NOTE = 500
_MAX_REF = 200


class EstimateError(ValueError):
    """A malformed estimate payload (-> HTTP 422)."""


# ---------------------------------------------------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------------------------------------------------

def _check_schema(schema: dict) -> dict:
    if not isinstance(schema, dict) or schema.get("schema_version") != 1:
        raise ValueError("cost estimate schema: schema_version 1 expected")
    currencies = schema.get("currencies") or []
    if not currencies or schema.get("default_currency") not in currencies:
        raise ValueError("cost estimate schema: currencies / default_currency")
    rules = schema.get("scale_rules") or {}
    units = schema.get("units") or {}
    seen = set()
    for group in schema.get("groups") or []:
        if group.get("scope") not in ("iteration", "project"):
            raise ValueError("cost estimate schema: group %r scope" % group.get("id"))
        for section in group.get("sections") or []:
            for item in section.get("items") or []:
                iid = item.get("id")
                if not iid or iid in seen:
                    raise ValueError("cost estimate schema: duplicate or empty item id %r" % iid)
                seen.add(iid)
                if item.get("unit") not in units:
                    raise ValueError("cost estimate schema: item %r unit %r" % (iid, item.get("unit")))
                for lang in schema.get("locales") or []:
                    if not (item.get("label") or {}).get(lang) or not (item.get("help") or {}).get(lang):
                        raise ValueError("cost estimate schema: item %r has no %s label / help" % (iid, lang))
                analog = item.get("analog")
                if analog and (analog.get("scale") not in rules or not analog.get("match")):
                    raise ValueError("cost estimate schema: item %r analog rule" % iid)
                if item.get("scale") and item["scale"] not in rules:
                    raise ValueError("cost estimate schema: item %r scale rule" % iid)
    if not seen:
        raise ValueError("cost estimate schema: no items")
    return schema


@lru_cache(maxsize=1)
def _schema_cached(path: str, mtime: float) -> dict:
    with open(path, encoding="utf-8") as fh:
        return _check_schema(json.load(fh))


def load_schema(path: Optional[Path] = None) -> dict:
    """The line catalogue (a deep copy: callers may not mutate the cached one)."""
    p = Path(path or SCHEMA_PATH)
    return copy.deepcopy(_schema_cached(str(p), p.stat().st_mtime))


def catalogue(schema: dict) -> Dict[str, Tuple[dict, dict, dict]]:
    """``item id -> (group, section, item)`` in schema order."""
    out: Dict[str, Tuple[dict, dict, dict]] = {}
    for group in schema.get("groups") or []:
        for section in group.get("sections") or []:
            for item in section.get("items") or []:
                out[item["id"]] = (group, section, item)
    return out


def _items_of_scope(schema: dict, scope: str) -> List[Tuple[dict, dict, dict]]:
    return [v for v in catalogue(schema).values() if v[0]["scope"] == scope]


# ---------------------------------------------------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------------------------------------------------

def default_settings(schema: dict, *, linked_motors: Iterable[dict] = ()) -> dict:
    out: Dict[str, Any] = {}
    for field in schema.get("settings") or []:
        default = field.get("default")
        if isinstance(default, dict) and default.get("rule") == "complex":
            continue
        out[field["id"]] = default
    for field in schema.get("settings") or []:
        default = field.get("default")
        if isinstance(default, dict) and default.get("rule") == "complex":
            out[field["id"]] = default["if_true"] if out.get("complex") else default["if_false"]
    target = None
    for link in linked_motors or []:
        if isinstance(link, dict) and link.get("die") and link.get("config") and str(link.get("status") or "current") == "current":
            target = {"die": str(link["die"]), "config": str(link["config"])}
            break
    out["target_motor"] = target
    out["analogs"] = []
    out["basis_overrides"] = {}
    return out


def default_iteration_settings(schema: dict) -> dict:
    return {f["id"]: f.get("default") for f in schema.get("iteration_settings") or []}


def _applies(item: dict, settings: dict, it_settings: Optional[dict]) -> bool:
    cond = item.get("applies_if") or {}
    for key, want in cond.items():
        have = (it_settings or {}).get(key) if key in (it_settings or {}) else settings.get(key)
        if isinstance(want, list):
            if have not in want:
                return False
        elif bool(have) != bool(want):
            return False
    return True


def blank_line(item: dict, currency: str) -> dict:
    return {"status": "missing", "qty": None, "unit": item["unit"], "unit_cost": None, "currency": currency,
            "source": None, "source_ref": None, "scale": None, "derive": None, "date": None, "note": "", "by": None}


def default_line(item: dict, settings: dict, *, iteration: Optional[int], it_settings: Optional[dict] = None) -> dict:
    """The line a new estimate starts with: missing; ``not_applicable`` where the item does not apply (bench type, liquid
    cooling, HV) or by its ``first`` rule in iteration 1; a later iteration's tooling = "modifications" (``later`` rule:
    ``pct`` of iteration 1's line, source ``rule``)."""
    line = blank_line(item, str(settings.get("currency") or "EUR"))
    if item.get("qty", {}).get("rule") == "one":
        line["qty"] = 1.0
    elif item.get("qty", {}).get("rule") == "motors":
        line["qty"] = float(settings.get("motors_per_iteration") or 1)
    if not _applies(item, settings, it_settings):
        line["status"] = "not_applicable"
        return line
    if iteration == 1 and item.get("first") == "not_applicable":
        line["status"] = "not_applicable"
        return line
    later = item.get("later")
    if iteration is not None and iteration > 1 and isinstance(later, dict) and later.get("mode") == "pct_of_first":
        line.update({"status": "filled", "source": "rule", "derive": {"pct": float(later.get("pct") or 0), "from_iteration": 1},
                     "qty": None, "unit_cost": None})
    return line


def default_iteration(schema: dict, settings: dict, index: int) -> dict:
    its = default_iteration_settings(schema)
    return {"index": index, "settings": its,
            "lines": {item["id"]: default_line(item, settings, iteration=index, it_settings=its)
                      for _g, _s, item in _items_of_scope(schema, "iteration")}}


def default_estimate(schema: dict, project_id: str, *, linked_motors: Iterable[dict] = ()) -> dict:
    settings = default_settings(schema, linked_motors=linked_motors)
    return {"version": VERSION, "schema_version": schema["schema_version"], "project_id": project_id, "revision": 0,
            "settings": settings,
            "iterations": [default_iteration(schema, settings, i) for i in range(1, int(settings["iterations"]) + 1)],
            "project_lines": {item["id"]: default_line(item, settings, iteration=None)
                              for _g, _s, item in _items_of_scope(schema, "project")},
            "basis": {}, "updated_at": None, "updated_by": None}


# ---------------------------------------------------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------------------------------------------------

def _num(v: Any, what: str, *, lo: Optional[float] = 0.0, hi: Optional[float] = None, allow_none: bool = True) -> Optional[float]:
    if v is None or (isinstance(v, str) and v.strip() == ""):
        if allow_none:
            return None
        raise EstimateError("%s: a number is required" % what)
    if isinstance(v, bool):
        raise EstimateError("%s: a number is required" % what)
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise EstimateError("%s: %r is not a number" % (what, v)) from None
    if not math.isfinite(x) or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise EstimateError("%s: %r is out of range" % (what, v))
    return x


def _int(v: Any, what: str, lo: int, hi: int) -> int:
    x = _num(v, what, lo=lo, hi=hi, allow_none=False)
    if x is None or int(x) != x:
        raise EstimateError("%s: a whole number %d..%d is required" % (what, lo, hi))
    return int(x)


def _text(v: Any, n: int = _MAX_NOTE) -> str:
    return str(v or "").strip()[:n]


def normalize_settings(raw: Any, schema: dict) -> dict:
    if not isinstance(raw, dict):
        raise EstimateError("settings: an object is required")
    out: Dict[str, Any] = {}
    for field in schema.get("settings") or []:
        fid, kind = field["id"], field["type"]
        v = raw.get(fid, None)
        if kind == "bool":
            out[fid] = bool(v)
        elif kind == "int":
            out[fid] = _int(v, fid, int(field["min"]), int(field["max"]))
        elif kind == "number":
            out[fid] = _num(v, fid, lo=field.get("min"), hi=field.get("max"), allow_none=False)
        elif kind == "enum":
            if v not in field["options"]:
                raise EstimateError("%s: one of %s" % (fid, ", ".join(field["options"])))
            out[fid] = v
    target = raw.get("target_motor")
    if target is not None:
        if not isinstance(target, dict) or not str(target.get("die") or "").strip() or not str(target.get("config") or "").strip():
            raise EstimateError("target_motor: {die, config} or null")
        target = {"die": _text(target["die"], _MAX_REF), "config": _text(target["config"], _MAX_REF)}
    out["target_motor"] = target
    analogs = raw.get("analogs") or []
    if not isinstance(analogs, list) or not all(isinstance(a, str) and "/" in a for a in analogs):
        raise EstimateError("analogs: a list of 'die/config' productions")
    out["analogs"] = [_text(a, _MAX_REF) for a in analogs][:50]
    overrides = raw.get("basis_overrides") or {}
    if not isinstance(overrides, dict):
        raise EstimateError("basis_overrides: an object")
    allowed = set(schema.get("basis_fields") or [])
    if set(overrides) - allowed:
        raise EstimateError("basis_overrides: unknown keys %s" % ", ".join(sorted(set(overrides) - allowed)))
    out["basis_overrides"] = {k: _num(v, k) for k, v in overrides.items() if _num(v, k) is not None}
    return out


def normalize_line(raw: Any, item: dict, schema: dict, what: str) -> dict:
    if not isinstance(raw, dict):
        raise EstimateError("%s: an object is required" % what)
    status = raw.get("status") or "missing"
    if status not in STATUSES:
        raise EstimateError("%s.status: one of %s" % (what, ", ".join(STATUSES)))
    source = raw.get("source")
    if source is not None and source not in SOURCES:
        raise EstimateError("%s.source: one of %s" % (what, ", ".join(SOURCES)))
    if status == "filled" and source is None:
        source = "manual"
    currency = str(raw.get("currency") or schema["default_currency"]).strip().upper()
    if currency not in schema["currencies"]:
        raise EstimateError("%s.currency: one of %s" % (what, ", ".join(schema["currencies"])))
    unit = raw.get("unit") or item["unit"]
    if unit not in (schema.get("units") or {}):
        raise EstimateError("%s.unit: %r is not a known unit" % (what, unit))
    ref = raw.get("source_ref")
    if ref is not None:
        if not isinstance(ref, dict):
            raise EstimateError("%s.source_ref: an object or null" % what)
        ref = {k: (_text(ref.get(k), _MAX_REF) or None) for k in ("project", "production", "row", "iteration", "label", "updated_at")
               if ref.get(k) not in (None, "")}
    scale = raw.get("scale")
    if scale is not None:
        if not isinstance(scale, dict) or scale.get("rule") not in (schema.get("scale_rules") or {}):
            raise EstimateError("%s.scale: {rule, factor}" % what)
        scale = {"rule": scale["rule"], "factor": _num(scale.get("factor"), what + ".scale.factor", allow_none=False),
                 "target": _num(scale.get("target"), what + ".scale.target"),
                 "analog": _num(scale.get("analog"), what + ".scale.analog"),
                 "exponent": _num(scale.get("exponent", 1.0), what + ".scale.exponent", lo=0, hi=3) or 1.0}
    derive = raw.get("derive")
    if derive is not None:
        if not isinstance(derive, dict):
            raise EstimateError("%s.derive: {pct} or null" % what)
        derive = {"pct": _num(derive.get("pct"), what + ".derive.pct", hi=1000, allow_none=False), "from_iteration": 1}
    pct_line = item["unit"] == "pct"
    qty = _num(raw.get("qty"), what + ".qty", hi=1000 if pct_line else 1e9)
    unit_cost = _num(raw.get("unit_cost"), what + ".unit_cost", hi=1e12)
    date = _text(raw.get("date"), 32) or None
    if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ][0-9:.+Z-]*)?", date):
        raise EstimateError("%s.date: YYYY-MM-DD" % what)
    return {"status": status, "qty": qty, "unit": unit, "unit_cost": unit_cost, "currency": currency, "source": source,
            "source_ref": ref or None, "scale": scale, "derive": derive, "date": date[:10] if date else None,
            "note": _text(raw.get("note")), "by": _text(raw.get("by"), _MAX_REF) or None}


def normalize_estimate(raw: Any, schema: dict, project_id: str) -> dict:
    """A full estimate as it is SAVED: every setting and line checked (``EstimateError``), unknown items refused, missing
    lines and iterations filled with their defaults, the iteration list cut / grown to ``settings.iterations``."""
    if not isinstance(raw, dict):
        raise EstimateError("an estimate object is required")
    settings = normalize_settings(raw.get("settings"), schema)
    iter_items = {i["id"]: i for _g, _s, i in _items_of_scope(schema, "iteration")}
    proj_items = {i["id"]: i for _g, _s, i in _items_of_scope(schema, "project")}
    raw_iters = raw.get("iterations") or []
    if not isinstance(raw_iters, list):
        raise EstimateError("iterations: a list")
    by_index: Dict[int, dict] = {}
    for it in raw_iters:
        if not isinstance(it, dict):
            raise EstimateError("iterations: objects")
        by_index[_int(it.get("index"), "iterations.index", 1, 5)] = it
    iterations = []
    its_fields = schema.get("iteration_settings") or []
    for index in range(1, settings["iterations"] + 1):
        src = by_index.get(index)
        if src is None:
            iterations.append(default_iteration(schema, settings, index))
            continue
        its_raw = src.get("settings") or {}
        if not isinstance(its_raw, dict):
            raise EstimateError("iteration %d settings: an object" % index)
        its: Dict[str, Any] = {}
        for f in its_fields:
            v = its_raw.get(f["id"], f.get("default"))
            if f["type"] == "enum":
                if v not in f["options"]:
                    raise EstimateError("iteration %d %s: one of %s" % (index, f["id"], ", ".join(f["options"])))
                its[f["id"]] = v
            elif f["type"] == "number":
                its[f["id"]] = _num(v, "iteration %d %s" % (index, f["id"]), lo=f.get("min"), hi=f.get("max"))
        lines_raw = src.get("lines") or {}
        if not isinstance(lines_raw, dict):
            raise EstimateError("iteration %d lines: an object" % index)
        unknown = set(lines_raw) - set(iter_items)
        if unknown:
            raise EstimateError("iteration %d: unknown lines %s" % (index, ", ".join(sorted(unknown)[:5])))
        lines = {}
        for iid, item in iter_items.items():
            lines[iid] = (normalize_line(lines_raw[iid], item, schema, "iteration %d %s" % (index, iid)) if iid in lines_raw
                          else default_line(item, settings, iteration=index, it_settings=its))
        iterations.append({"index": index, "settings": its, "lines": lines})
    proj_raw = raw.get("project_lines") or {}
    if not isinstance(proj_raw, dict):
        raise EstimateError("project_lines: an object")
    unknown = set(proj_raw) - set(proj_items)
    if unknown:
        raise EstimateError("project_lines: unknown lines %s" % ", ".join(sorted(unknown)[:5]))
    project_lines = {iid: (normalize_line(proj_raw[iid], item, schema, "project %s" % iid) if iid in proj_raw
                           else default_line(item, settings, iteration=None))
                     for iid, item in proj_items.items()}
    basis = raw.get("basis") if isinstance(raw.get("basis"), dict) else {}
    return {"version": VERSION, "schema_version": schema["schema_version"], "project_id": project_id,
            "revision": int(raw.get("revision") or 0), "settings": settings, "iterations": iterations,
            "project_lines": project_lines, "basis": _clean_basis(basis, schema),
            "updated_at": raw.get("updated_at"), "updated_by": raw.get("updated_by")}


def _clean_basis(basis: dict, schema: dict) -> dict:
    out: Dict[str, Any] = {}
    for k in schema.get("basis_fields") or []:
        v = basis.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) and float(v) > 0:
            out[k] = float(v)
    if isinstance(basis.get("motor"), str):
        out["motor"] = basis["motor"][:_MAX_REF]
    return out


# ---------------------------------------------------------------------------------------------------------------------
# totals
# ---------------------------------------------------------------------------------------------------------------------

def _add(acc: Dict[str, float], cur: str, amount: float) -> None:
    acc[cur] = acc.get(cur, 0.0) + amount


def _merge(acc: Dict[str, float], other: Dict[str, float]) -> None:
    for cur, amount in other.items():
        _add(acc, cur, amount)


def _rounded(acc: Dict[str, float], order: List[str]) -> Dict[str, float]:
    rank = {c: i for i, c in enumerate(order)}
    return {c: round(acc[c], 2) for c in sorted(acc, key=lambda c: (rank.get(c, 99), c))}


def _plain_amount(line: dict, multiplier: float = 1.0) -> Tuple[Optional[Dict[str, float]], str]:
    if line.get("qty") is None or line.get("unit_cost") is None:
        return None, "quantity or unit cost not set"
    return {line["currency"]: float(line["qty"]) * float(line["unit_cost"]) * multiplier}, ""


def compute(estimate: dict, schema: dict) -> dict:
    """Amounts of every line, totals per iteration x (manufacturing, testing, logistics), project-wide lines, subtotal,
    contingency and total - all PER CURRENCY - plus the missing lines and the payment milestones."""
    cat = catalogue(schema)
    order = list(schema["currencies"])
    settings = estimate["settings"]
    groups = [g["id"] for g in schema["groups"] if g["scope"] == "iteration"]
    missing: List[dict] = []
    iterations_out = []
    first_amounts: Dict[str, Optional[Dict[str, float]]] = {}
    all_manufacturing: Dict[str, float] = {}

    def note_missing(scope: str, index: Optional[int], iid: str, reason: str) -> None:
        group, section, item = cat[iid]
        missing.append({"scope": scope, "iteration": index, "group": group["id"], "section": section["id"], "item": iid,
                        "reason": reason})

    for it in estimate["iterations"]:
        index = it["index"]
        amounts: Dict[str, Optional[Dict[str, float]]] = {}
        reasons: Dict[str, str] = {}
        pct_items = []
        for iid, line in it["lines"].items():
            group, section, item = cat[iid]
            if line["status"] == "not_applicable":
                amounts[iid] = {}
                continue
            if line["status"] == "missing":
                amounts[iid], reasons[iid] = None, "not filled"
                continue
            if item.get("amount_rule"):
                pct_items.append(iid)
                continue
            if line.get("derive"):
                base = first_amounts.get(iid) if index > 1 else None
                if base is None:
                    amounts[iid], reasons[iid] = None, "iteration 1 line not filled"
                else:
                    amounts[iid] = {c: a * float(line["derive"]["pct"]) / 100.0 for c, a in base.items()}
                continue
            amounts[iid], reasons[iid] = _plain_amount(line)
        for iid in pct_items:
            line, (_g, _s, item) = it["lines"][iid], cat[iid]
            rule = item["amount_rule"]
            if line.get("qty") is None:
                amounts[iid], reasons[iid] = None, "percentage not set"
                continue
            base: Dict[str, float] = {}
            for other, amount in amounts.items():
                og, osec, oitem = cat[other]
                if amount and og["id"] == rule["pct_of"] and osec["id"] not in rule.get("exclude_sections", []) \
                        and not oitem.get("amount_rule"):
                    _merge(base, amount)
            amounts[iid] = {c: a * float(line["qty"]) / 100.0 for c, a in base.items()}
        if index == 1:
            first_amounts = dict(amounts)
        by_group: Dict[str, Dict[str, float]] = {g: {} for g in groups}
        subtotal: Dict[str, float] = {}
        line_out = {}
        for iid, amount in amounts.items():
            gid = cat[iid][0]["id"]
            if amount is None:
                note_missing("iteration", index, iid, reasons.get(iid, ""))
            else:
                _merge(by_group[gid], amount)
                _merge(subtotal, amount)
            line_out[iid] = {"amount": None if amount is None else _rounded(amount, order),
                             "origin": origin_text(it["lines"][iid], schema, cat[iid][2])}
        _merge(all_manufacturing, by_group.get("manufacturing", {}))
        iterations_out.append({"index": index, **{g: _rounded(by_group[g], order) for g in groups},
                               "subtotal": _rounded(subtotal, order), "lines": line_out})

    project_amounts: Dict[str, Optional[Dict[str, float]]] = {}
    project_total: Dict[str, float] = {}
    project_lines_out = {}
    n_iter = len(estimate["iterations"])
    for iid, line in estimate["project_lines"].items():
        _g, _s, item = cat[iid]
        amount: Optional[Dict[str, float]]
        reason = ""
        if line["status"] == "not_applicable":
            amount = {}
        elif line["status"] == "missing":
            amount, reason = None, "not filled"
        elif item.get("amount_rule"):
            if line.get("qty") is None:
                amount, reason = None, "percentage not set"
            else:
                amount = {c: a * float(line["qty"]) / 100.0 for c, a in all_manufacturing.items()}
        else:
            amount, reason = _plain_amount(line, float(n_iter) if item.get("per_iteration") else 1.0)
        project_amounts[iid] = amount
        if amount is None:
            note_missing("project", None, iid, reason)
        else:
            _merge(project_total, amount)
        project_lines_out[iid] = {"amount": None if amount is None else _rounded(amount, order),
                                  "origin": origin_text(line, schema, item)}

    subtotal_all: Dict[str, float] = {}
    for it in iterations_out:
        _merge(subtotal_all, it["subtotal"])
    _merge(subtotal_all, project_total)
    pct = float(settings.get("contingency_pct") or 0.0)
    contingency = {c: a * pct / 100.0 for c, a in subtotal_all.items()}
    total = {c: subtotal_all[c] + contingency[c] for c in subtotal_all}
    currencies = list(_rounded(total, order).keys())

    # payment milestones: the % the owner set per iteration; the suggestion is the iteration's share of the subtotal in
    # the main currency (the estimate's currency, else the first one that has money)
    main = settings.get("currency") if settings.get("currency") in subtotal_all else (currencies[0] if currencies else None)
    iter_sum = sum(it["subtotal"].get(main, 0.0) for it in iterations_out) if main else 0.0
    milestones = []
    set_sum = 0.0
    any_set = False
    for it, it_out in zip(estimate["iterations"], iterations_out):
        mp = (it.get("settings") or {}).get("milestone_pct")
        suggested = round(100.0 * it_out["subtotal"].get(main, 0.0) / iter_sum, 1) if main and iter_sum > 0 else None
        if mp is not None:
            any_set = True
            set_sum += float(mp)
        share = mp if mp is not None else suggested
        milestones.append({"iteration": it["index"], "pct": mp, "suggested_pct": suggested,
                           "amount": None if share is None else _rounded({c: a * float(share) / 100.0 for c, a in total.items()}, order)})
    warnings = []
    if len(currencies) > 1:
        warnings.append("Lines are in several currencies (%s); totals are per currency and never added together." % ", ".join(currencies))
    if any_set and abs(set_sum - 100.0) > 0.05:
        warnings.append("Payment milestones add up to %.1f %%, not 100 %%." % set_sum)
    return {"iterations": iterations_out, "project": {"total": _rounded(project_total, order), "lines": project_lines_out},
            "subtotal": _rounded(subtotal_all, order), "contingency_pct": pct, "contingency": _rounded(contingency, order),
            "total": _rounded(total, order), "currencies": currencies, "missing": missing, "missing_count": len(missing),
            "milestones": milestones, "warnings": warnings}


def origin_text(line: dict, schema: dict, item: Optional[dict] = None) -> str:
    """Where the number came from, in one English line (the web shows its own translated chip)."""
    status = line.get("status")
    if status == "not_applicable":
        return "not applicable"
    if status == "missing":
        return "missing"
    src, ref = line.get("source"), line.get("source_ref") or {}
    when = line.get("date") or ""
    scale = line.get("scale") or {}
    rule = (schema.get("scale_rules") or {}).get(scale.get("rule") or "", {})
    scaled = ""
    if scale and scale.get("rule") != "one_to_one":
        scaled = ", scaled by %s x%s" % ((rule.get("label") or {}).get("en", scale.get("rule")), _fmt(scale.get("factor")))
    if line.get("derive"):
        return "%s %% of iteration 1 (default rule)" % _fmt(line["derive"].get("pct"))
    if src == "manual":
        return ("entered by %s on %s" % (line.get("by") or "the owner", when)).strip()
    if src == "analog":
        what = ref.get("production") or ref.get("project") or "another project"
        return "from %s %s%s%s" % ("production" if ref.get("production") else "project", what,
                                   (" (%s)" % ref["label"]) if ref.get("label") else "", scaled)
    if src == "test_history":
        return "test cost of project %s%s" % (ref.get("project") or "?", scaled)
    if src == "model":
        price = ref.get("production") or ref.get("project")
        return "simulation mass x price per kg%s" % ((" from %s" % price) if price else "")
    if src == "rule":
        return "default rule"
    return ""


def _fmt(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "?"
    return ("%.3f" % v).rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------------------------------------------------
# scale rule and suggest
# ---------------------------------------------------------------------------------------------------------------------

def scale_factor(schema: dict, rule: str, target: dict, analog: dict, exponent: float = 1.0) -> Tuple[Optional[float], str, Optional[float], Optional[float]]:
    """``(factor, reason, target value, analog value)``: (target basis / analog basis) ** exponent for the rule's basis
    (active mass, magnet / copper / steel mass, outer diameter, stack length, power), 1 for ``one_to_one``.  ``None`` with
    the reason when a basis is unknown - a line is then never scaled by a guess."""
    spec = (schema.get("scale_rules") or {}).get(rule)
    if spec is None:
        return None, "unknown scale rule %r" % rule, None, None
    basis = spec.get("basis")
    if not basis:
        return 1.0, "", None, None
    t, a = (target or {}).get(basis), (analog or {}).get(basis)
    if not t or not a or t <= 0 or a <= 0:
        return None, "%s unknown for %s" % (basis, "the target" if not t or t <= 0 else "the analog"), t, a
    return round((float(t) / float(a)) ** float(exponent or 1.0), 4), "", float(t), float(a)


_LATIN = re.compile(r"[a-z0-9]")


def _match_len(text: str, keywords: Iterable[str]) -> int:
    """Length of the longest keyword in ``text``: a latin keyword must start a word (``ring`` is not in ``bearing``)."""
    best = 0
    for kw in keywords:
        k = kw.lower()
        if _LATIN.match(k):
            if re.search(r"(?<![a-z0-9])" + re.escape(k), text):
                best = max(best, len(k))
        elif k in text:
            best = max(best, len(k))
    return best


def assign_rows(schema: dict, rows: List[dict]) -> Dict[str, List[dict]]:
    """Each analog production row to AT MOST ONE catalogue item: the item whose keyword is the longest match on the row's
    name / material (ties: schema order) and whose ``analog.list`` takes that row's list.  No row is counted twice."""
    cands = [(i, item) for i, (_g, _s, item) in enumerate(catalogue(schema).values()) if item.get("analog")]
    out: Dict[str, List[dict]] = {}
    for row in rows:
        text = " ".join(str(row.get(k) or "") for k in ("name_en", "name_zh", "material")).lower()
        best: Tuple[int, int, Optional[str]] = (0, 0, None)
        for order, item in cands:
            lst = item["analog"]["list"]
            if (lst == "any" and row.get("list") == "tooling") or (lst != "any" and lst != row.get("list")):
                continue                                 # "any" = a motor-BOM or a work line, never a tool
            n = _match_len(text, item["analog"]["match"])
            if n > best[0]:                              # strictly longer: a tie keeps the first item in schema order
                best = (n, order, item["id"])
        if best[2]:
            out.setdefault(best[2], []).append(row)
    return out


def _closeness(factor: Optional[float]) -> float:
    return abs(math.log(factor)) if factor and factor > 0 else 0.0


def _suggestable(line: dict, refresh: bool) -> bool:
    if line.get("source") == "manual" or line.get("status") == "not_applicable":
        return False                                   # the owner's numbers and decisions are never overwritten
    if line.get("status") == "missing":
        return True
    return refresh and line.get("source") in AUTO_SOURCES and not line.get("derive")


def suggest(estimate: dict, schema: dict, data: dict, *, today: str, refresh: bool = False) -> Tuple[dict, dict]:
    """Fill the missing lines from the gathered ``data`` and return ``(new estimate, report)``.

    ``data`` = ``{"target_basis": {...}, "productions": [{ref, title, updated_at, basis, rows: [{list, row_id, name_en,
    name_zh, material, amount, currency}]}], "estimates": [{project_id, title, basis, iterations: [{index, lines}],
    project_lines}], "tests": {production ref: {"days": n, "kinds": {...}}}}``.

    Never touches a ``manual`` line or a line the owner set ``not_applicable``; with ``refresh`` it also re-suggests lines
    an earlier suggestion filled.  Order per line: (1) a ``model`` mass line - the simulation mass x buy factor x the price
    per kg of the nearest analog production's matching rows; (2) the nearest analog PRODUCTION's matching cost / tooling
    rows, scaled by the item's rule; (3) another project's estimate line of the same item (``test_history`` for testing,
    ``analog`` otherwise), scaled the same way.  "Nearest" = the scale factor closest to 1, then the newest."""
    est = copy.deepcopy(estimate)
    cat = catalogue(schema)
    settings = est["settings"]
    target = dict(data.get("target_basis") or {})
    target.update({k: v for k, v in (settings.get("basis_overrides") or {}).items() if v})
    wanted = set(settings.get("analogs") or [])
    productions = [p for p in data.get("productions") or [] if not wanted or p.get("ref") in wanted]
    rows_by_prod = {p["ref"]: assign_rows(schema, p.get("rows") or []) for p in productions}
    estimates = [e for e in data.get("estimates") or [] if e.get("project_id") != est.get("project_id")]
    motors = float(settings.get("motors_per_iteration") or 1)
    filled: List[dict] = []
    skipped: List[dict] = []

    def from_productions(item: dict) -> Optional[dict]:
        rule, exponent = item["analog"]["scale"], float(item["analog"].get("exponent") or 1.0)
        cands = []
        for prod in productions:
            rows = rows_by_prod.get(prod["ref"], {}).get(item["id"]) or []
            if not rows:
                continue
            curs = {r["currency"] for r in rows}
            if len(curs) != 1:
                skipped.append({"item": item["id"], "analog": prod["ref"], "reason": "rows in several currencies"})
                continue
            factor, why, t, a = scale_factor(schema, rule, target, prod.get("basis") or {}, exponent)
            if factor is None:
                skipped.append({"item": item["id"], "analog": prod["ref"], "reason": why})
                continue
            cands.append({"prod": prod, "rows": rows, "factor": factor, "t": t, "a": a, "rule": rule, "exponent": exponent,
                          "amount": sum(float(r["amount"]) for r in rows), "currency": next(iter(curs))})
        return min(cands, key=lambda c: (round(_closeness(c["factor"]), 6), _newest_first(c["prod"].get("updated_at"))))             if cands else None

    def from_estimates(item: dict, group_id: str, index: Optional[int]) -> Optional[dict]:
        rule = item.get("scale") or (item.get("analog") or {}).get("scale") or "one_to_one"
        best = None
        for other in estimates:
            if index is None:
                cands = [(None, (other.get("project_lines") or {}).get(item["id"]))]
            else:
                its = {i.get("index"): i for i in other.get("iterations") or [] if isinstance(i, dict)}
                order = [index] + [k for k in sorted(its) if k != index]
                cands = [(k, (its[k].get("lines") or {}).get(item["id"])) for k in order if k in its]
            for k, line in cands:
                if not isinstance(line, dict) or line.get("status") != "filled" or line.get("derive") \
                        or line.get("qty") is None or line.get("unit_cost") is None or line.get("unit") != item["unit"]:
                    continue
                factor, why, t, a = scale_factor(schema, rule, target, other.get("basis") or {})
                if factor is None:
                    skipped.append({"item": item["id"], "analog": "project " + str(other.get("project_id")), "reason": why})
                    break
                key = (0 if k == index else 1, _closeness(factor))
                cand = {"other": other, "iteration": k, "line": line, "factor": factor, "t": t, "a": a, "rule": rule, "key": key}
                if best is None or key < best["key"]:
                    best = cand
                break
        return best

    def fill(line: dict, item: dict, group_id: str, index: Optional[int], where: str) -> None:
        qrule = (item.get("qty") or {}).get("rule")
        new = None
        if qrule == "model_mass":
            mkey = item["qty"]["mass"]
            buy = float(item["qty"].get("buy_factor") or 1.0)
            mass = target.get(mkey)
            if not mass:
                skipped.append({"item": item["id"], "where": where, "reason": "%s unknown for the target motor" % mkey})
            else:
                qty = round(float(mass) * buy * motors, 3)
                price = None
                best = from_productions(item) if item.get("analog") else None
                if best and (best["prod"].get("basis") or {}).get(mkey):
                    price = best["amount"] / (float(best["prod"]["basis"][mkey]) * buy)
                new = {**line, "qty": qty, "unit": item["unit"], "source": "model", "date": today, "by": "suggest",
                       "scale": None, "derive": None,
                       "note": "%.3f kg x %s buy factor x %d motor(s)" % (float(mass), _fmt(buy), int(motors))}
                if price is not None:
                    new.update({"status": "filled", "unit_cost": round(price, 4), "currency": best["currency"],
                                "source_ref": {"production": best["prod"]["ref"], "label": _row_label(best["rows"]),
                                               "updated_at": best["prod"].get("updated_at")}})
                    new["note"] += "; price per kg from %s" % best["prod"]["ref"]
                else:
                    new.update({"status": "missing", "unit_cost": None, "source_ref": None})
                    new["note"] += "; price per kg: no analog - enter it"
        if new is None and item.get("analog") and qrule != "model_mass":
            best = from_productions(item)
            if best:
                # motor-BOM and work-line rows cost PER MOTOR, tooling rows are the price of the whole list line
                per_motor = best["rows"][0]["list"] != "tooling"
                qty = motors if item["unit"] == "motor" else 1.0
                unit_cost = best["amount"] * best["factor"]
                if item["unit"] != "motor" and per_motor:
                    unit_cost *= motors                    # a lot (e.g. one etch batch) for this iteration's motors
                new = {**line, "status": "filled", "qty": qty, "unit": item["unit"], "unit_cost": round(unit_cost, 2),
                       "currency": best["currency"], "source": "analog",
                       "source_ref": {"production": best["prod"]["ref"], "row": ",".join(str(r.get("row_id")) for r in best["rows"])[:_MAX_REF],
                                      "label": _row_label(best["rows"]), "updated_at": best["prod"].get("updated_at")},
                       "scale": {"rule": best["rule"], "factor": best["factor"], "target": best["t"], "analog": best["a"],
                                 "exponent": best["exponent"]},
                       "derive": None, "date": today, "by": "suggest",
                       "note": "%d row(s) of %s" % (len(best["rows"]), best["prod"]["ref"])}
        if new is None and (item.get("history") or item.get("analog")):
            best = from_estimates(item, group_id, index)
            if best:
                src = best["line"]
                new = {**line, "status": "filled", "qty": src["qty"], "unit": item["unit"],
                       "unit_cost": round(float(src["unit_cost"]) * best["factor"], 2), "currency": src["currency"],
                       "source": "test_history" if group_id == "testing" else "analog",
                       "source_ref": {"project": best["other"]["project_id"],
                                      "iteration": None if best["iteration"] is None else str(best["iteration"]),
                                      "label": best["other"].get("title") or None},
                       "scale": {"rule": best["rule"], "factor": best["factor"], "target": best["t"], "analog": best["a"],
                                 "exponent": 1.0},
                       "derive": None, "date": today, "by": "suggest",
                       "note": "project %s%s" % (best["other"]["project_id"],
                                                 "" if best["iteration"] is None else ", iteration %s" % best["iteration"])}
        if new is None:
            hint = _test_hint(item, group_id, data)
            if hint and hint not in (line.get("note") or ""):
                line["note"] = ((line.get("note") or "") + ("; " if line.get("note") else "") + hint)[:_MAX_NOTE]
            skipped.append({"item": item["id"], "where": where, "reason": "nothing to base it on - enter it"})
            return
        line.clear()
        line.update(new)
        filled.append({"item": item["id"], "where": where, "source": new["source"], "status": new["status"]})

    for it in est["iterations"]:
        index = it["index"]
        for iid, line in it["lines"].items():
            group, _section, item = cat[iid]
            if not _suggestable(line, refresh) or item.get("amount_rule"):
                continue
            later = item.get("later")
            if index > 1 and isinstance(later, dict) and later.get("mode") == "pct_of_first":
                line.update({"status": "filled", "source": "rule", "derive": {"pct": float(later.get("pct") or 0), "from_iteration": 1},
                             "qty": None, "unit_cost": None, "scale": None, "source_ref": None, "date": today, "by": "suggest"})
                filled.append({"item": iid, "where": "iteration %d" % index, "source": "rule", "status": "filled"})
                continue
            fill(line, item, group["id"], index, "iteration %d" % index)
    for iid, line in est["project_lines"].items():
        group, _section, item = cat[iid]
        if _suggestable(line, refresh) and not item.get("amount_rule"):
            fill(line, item, group["id"], None, "project")
    est["basis"] = _clean_basis({**target, "motor": data.get("target_ref") or ""}, schema)
    report = {"filled": filled, "skipped": skipped, "filled_count": len(filled),
              "analogs": [{"ref": p["ref"], "title": p.get("title") or "", "basis": p.get("basis") or {}} for p in productions],
              "estimates": [e.get("project_id") for e in estimates], "target_basis": target}
    return est, report


def _newest_first(stamp: Any) -> Tuple[int, str]:
    """Sort key: a newer ISO time stamp first, an undated record last."""
    s = str(stamp or "")
    return (0, "".join(chr(0x10FFFF - ord(c)) for c in s)) if s else (1, "")


def _row_label(rows: List[dict]) -> str:
    names = [str(r.get("name_en") or r.get("name_zh") or r.get("row_id") or "") for r in rows][:3]
    more = "" if len(rows) <= 3 else " +%d" % (len(rows) - 3)
    return (", ".join(n for n in names if n) + more)[:_MAX_REF]


def _test_hint(item: dict, group_id: str, data: dict) -> str:
    """For bench time: what the analog productions' test records show (test days), as a hint only - never a number."""
    if group_id != "testing" or item["id"] != "test_bench":
        return ""
    parts = []
    for ref, info in sorted((data.get("tests") or {}).items()):
        if isinstance(info, dict) and info.get("days"):
            parts.append("%s: %d test day(s) recorded" % (ref, int(info["days"])))
    return ("test records - " + "; ".join(parts[:3])) if parts else ""


# ---------------------------------------------------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------------------------------------------------

_LINE_FIELDS = ("status", "qty", "unit", "unit_cost", "currency", "source", "source_ref", "scale", "derive", "note")


def _flat(estimate: dict) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (estimate.get("settings") or {}).items():
        out["settings.%s" % k] = v
    for it in estimate.get("iterations") or []:
        for k, v in (it.get("settings") or {}).items():
            out["iteration %s.settings.%s" % (it["index"], k)] = v
        for iid, line in (it.get("lines") or {}).items():
            for f in _LINE_FIELDS:
                out["iteration %s.%s.%s" % (it["index"], iid, f)] = line.get(f)
    for iid, line in (estimate.get("project_lines") or {}).items():
        for f in _LINE_FIELDS:
            out["project.%s.%s" % (iid, f)] = line.get(f)
    return out


def diff(old: Optional[dict], new: dict, schema: Optional[dict] = None) -> List[dict]:
    """What changed from ``old`` to ``new``: ``[{path, was, now}]`` over the settings, the iteration settings and every
    line field (``None`` -> everything is new).  With ``schema`` the totals are compared too (``total.<currency>``)."""
    a, b = _flat(old or {}), _flat(new)
    out = [{"path": k, "was": a.get(k), "now": b.get(k)} for k in sorted(set(a) | set(b), key=_path_key) if a.get(k) != b.get(k)]
    if schema is not None:
        ta = compute(old, schema)["total"] if old else {}
        tb = compute(new, schema)["total"]
        for cur in sorted(set(ta) | set(tb)):
            if ta.get(cur) != tb.get(cur):
                out.append({"path": "total.%s" % cur, "was": ta.get(cur), "now": tb.get(cur)})
    return out


def _path_key(path: str) -> Tuple:
    parts = re.split(r"(\d+)", path)
    return tuple(int(p) if p.isdigit() else p for p in parts)
