"""Project cost estimate (owner 2026-11-02): an ADMIN-ONLY, deliberately SIMPLE estimate of what a development project
costs us («не надо до такой степени оценивать стоимость» - the 68-line catalogue of #551 was too detailed).

* A project has 1..5 ITERATIONS (default 2, 3 when "complex" is ticked).  Each iteration has exactly FOUR lines:
  ``tooling`` (an amount in every iteration, the same window; owner 2026-11-02: no "% of iteration 1" any more),
  ``manufacturing`` (the cost of ONE motor x the motors of the iteration), ``testing`` (one amount; the
  bench is dyno / propeller stand / both / none) and ``shipping`` (DHL, one amount).
* EVERY AMOUNT IS IN EUR (owner 2026-11-02: «все цены в евро»): a line has no currency choice, new saves refuse any other
  currency, and an old line in another currency is converted to EUR on read (``legacy_to_eur``: at the stored or the cached
  ECB rate, source ``converted``, the original amount in the note; with no rate known the line is missing).
* Settings: iterations, contingency % (default 10), ``display_currency`` (``None`` = EUR; the FINAL table may be shown in
  EUR / USD / CNY at the rates below - the only place with a currency choice).  The estimate also carries dated exchange rates, ``fx`` (EUR
  base: ECB reference rates stored with their date, optionally overridden per currency by hand - a manual rate wins), and
  ``compute`` adds ``converted``: the lines and totals in the display currency at those rates.  A rate that is not known
  is never invented: the totals then stay per currency (``converted.available`` is false).
* Every number keeps its origin: ``production`` (the per-motor cost / tooling sum of the most similar built motor, x ONE
  factor = target active mass / analog active mass), ``estimate`` (another project's estimate, as-is), ``manual`` (the
  owner, with e-mail and date), ``converted`` (from an old 68-line estimate, or an old "% of iteration 1" tooling rule turned into its amount).
  ``suggest`` never overwrites a ``manual`` or ``converted`` value.

This module is pure (no I/O): schema, defaults, validation, the old-shape conversion, totals, ``suggest`` over gathered
data, and the diff.  Storage and data gathering live in ``cost_estimate_store`` / ``cost_estimate_sources``, the admin
routes in ``cost_estimate_routes``.  Nothing here is ever shown to a customer, a project viewer, the factory (OEM) or an
MCP agent.
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
VERSION = 2
LINES = ("tooling", "manufacturing", "testing", "shipping")
SOURCES = ("production", "estimate", "manual", "converted", "rule")
#: sources ``suggest`` may refresh; ``manual`` and ``converted`` (the owner's own old numbers) are never overwritten
AUTO_SOURCES = ("production", "estimate")
_MAX_NOTE = 500
_MAX_REF = 200
FX_BASE = "EUR"
FX_SOURCES = ("ECB", "manual")

Money = Dict[str, float]


class EstimateError(ValueError):
    """A malformed estimate payload (-> HTTP 422)."""


# ---------------------------------------------------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------------------------------------------------

def _check_schema(schema: dict) -> dict:
    if not isinstance(schema, dict) or schema.get("schema_version") != 2:
        raise ValueError("cost estimate schema: schema_version 2 expected")
    if not schema.get("currencies") or schema.get("default_currency") not in schema["currencies"]:
        raise ValueError("cost estimate schema: currencies / default_currency")
    if schema.get("line_currency") != FX_BASE:
        raise ValueError("cost estimate schema: line_currency must be %s" % FX_BASE)
    by_locale = schema.get("display_currency_by_locale") or {}
    if set(by_locale) != set(schema.get("locales") or []) or any(c not in schema["currencies"] for c in by_locale.values()):
        raise ValueError("cost estimate schema: display_currency_by_locale")
    if [ln.get("id") for ln in schema.get("lines") or []] != list(LINES):
        raise ValueError("cost estimate schema: the lines must be %s" % ", ".join(LINES))
    lo, hi = schema.get("factor_limits") or (0, 0)
    if not 0 < float(lo) < 1 < float(hi):
        raise ValueError("cost estimate schema: factor_limits")
    for entry in (schema.get("lines") or []) + (schema.get("settings") or []) + (schema.get("iteration_fields") or []):
        for lang in schema.get("locales") or []:
            if not (entry.get("label") or {}).get(lang) or not (entry.get("help") or {}).get(lang):
                raise ValueError("cost estimate schema: %r has no %s label / help" % (entry.get("id"), lang))
    return schema


@lru_cache(maxsize=4)
def _schema_cached(path: str, mtime: float) -> dict:
    with open(path, encoding="utf-8") as fh:
        return _check_schema(json.load(fh))


def load_schema(path: Optional[Path] = None) -> dict:
    """The schema (a deep copy: callers may not mutate the cached one)."""
    p = Path(path or SCHEMA_PATH)
    return copy.deepcopy(_schema_cached(str(p), p.stat().st_mtime))


def _field(schema: dict, group: str, fid: str) -> dict:
    return next(f for f in schema.get(group) or [] if f["id"] == fid)


def factor_limits(schema: dict) -> Tuple[float, float]:
    lo, hi = schema["factor_limits"]
    return float(lo), float(hi)


# ---------------------------------------------------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------------------------------------------------

def default_iterations(complex_project: bool, schema: dict) -> int:
    rule = _field(schema, "settings", "iterations")["default"]
    return int(rule["if_true"] if complex_project else rule["if_false"])


def _target_from(linked_motors: Iterable[dict]) -> Optional[dict]:
    for link in linked_motors or []:
        if isinstance(link, dict) and link.get("die") and link.get("config") and str(link.get("status") or "current") == "current":
            return {"die": str(link["die"]), "config": str(link["config"])}
    return None


def default_settings(schema: dict, *, linked_motors: Iterable[dict] = ()) -> dict:
    complex_project = bool(_field(schema, "settings", "complex")["default"])
    return {"complex": complex_project, "iterations": default_iterations(complex_project, schema),
            "currency": schema["line_currency"],
            "contingency_pct": float(_field(schema, "settings", "contingency_pct")["default"]),
            "display_currency": None, "target_motor": _target_from(linked_motors)}


def blank_line(currency: str) -> dict:
    return {"status": "missing", "amount": None, "currency": currency, "pct": None, "source": None, "source_ref": None,
            "factor": None, "date": None, "by": None, "note": "", "hint": ""}


def default_line(line_id: str, index: int, currency: str, schema: dict) -> dict:
    """A new line is missing (tooling too, in every iteration)."""
    return blank_line(currency)


def default_iteration(schema: dict, settings: dict, index: int) -> dict:
    cur = str(settings.get("currency") or schema["default_currency"])
    return {"index": index, "motors": int(_field(schema, "iteration_fields", "motors")["default"]),
            "bench": _field(schema, "iteration_fields", "bench")["default"],
            "lines": {lid: default_line(lid, index, cur, schema) for lid in LINES}}


def default_estimate(schema: dict, project_id: str, *, linked_motors: Iterable[dict] = ()) -> dict:
    settings = default_settings(schema, linked_motors=linked_motors)
    return {"version": VERSION, "schema_version": schema["schema_version"], "project_id": project_id, "revision": 0,
            "settings": settings, "fx": None,
            "iterations": [default_iteration(schema, settings, i) for i in range(1, settings["iterations"] + 1)],
            "migration": None, "updated_at": None, "updated_by": None}


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


def normalize_settings(raw: Any, schema: dict, legacy: bool = False) -> dict:
    if not isinstance(raw, dict):
        raise EstimateError("settings: an object is required")
    it_field = _field(schema, "settings", "iterations")
    pct_field = _field(schema, "settings", "contingency_pct")
    currency = schema["line_currency"] if legacy else raw.get("currency", schema["line_currency"])
    if currency != schema["line_currency"]:
        raise EstimateError("currency: %s only (every amount is in %s)" % (schema["line_currency"], schema["line_currency"]))
    target = raw.get("target_motor")
    if target is not None:
        if not isinstance(target, dict) or not str(target.get("die") or "").strip() or not str(target.get("config") or "").strip():
            raise EstimateError("target_motor: {die, config} or null")
        target = {"die": _text(target["die"], _MAX_REF), "config": _text(target["config"], _MAX_REF)}
    display = raw.get("display_currency")
    if display in (None, ""):
        display = None
    else:
        display = str(display).strip().upper()
        if display not in schema["currencies"]:
            raise EstimateError("display_currency: null or one of %s" % ", ".join(schema["currencies"]))
    complex_project = bool(raw.get("complex"))
    return {"complex": complex_project,
            "iterations": _int(raw.get("iterations", default_iterations(complex_project, schema)), "iterations",
                               int(it_field["min"]), int(it_field["max"])),
            "currency": currency,
            "contingency_pct": _num(raw.get("contingency_pct", pct_field["default"]), "contingency_pct",
                                    lo=pct_field["min"], hi=pct_field["max"], allow_none=False),
            "display_currency": display, "target_motor": target}


def _source_ref(raw: Any, what: str) -> Optional[dict]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise EstimateError("%s.source_ref: an object or null" % what)
    out: Dict[str, Any] = {}
    for k in ("production", "project", "title", "iteration"):
        if raw.get(k) not in (None, ""):
            out[k] = _text(raw.get(k), _MAX_REF)
    for k in ("analog_mass_kg", "target_mass_kg"):
        v = _num(raw.get(k), "%s.source_ref.%s" % (what, k))
        if v:
            out[k] = v
    return out or None


def normalize_line(raw: Any, line_id: str, index: int, schema: dict, what: str, legacy: bool = False) -> dict:
    """One line as it is SAVED.  The status follows the content: ``filled`` = an amount (an old later-iteration tooling
    ``pct`` is accepted and turned into its amount by ``normalize_estimate``); otherwise ``missing``."""
    if not isinstance(raw, dict):
        raise EstimateError("%s: an object is required" % what)
    source = raw.get("source")
    if source is not None and source not in SOURCES:
        raise EstimateError("%s.source: one of %s" % (what, ", ".join(SOURCES)))
    currency = str(raw.get("currency") or schema["line_currency"]).strip().upper()
    if currency not in (schema["currencies"] if legacy else [schema["line_currency"]]):        # old data: converted on read
        raise EstimateError("%s.currency: %s only (every amount is in %s)" % (what, schema["line_currency"], schema["line_currency"]))
    amount = _num(raw.get("amount"), what + ".amount", hi=1e12)
    pct = _num(raw.get("pct"), what + ".pct", hi=1000)
    if pct is not None and not (line_id == "tooling" and index > 1):
        raise EstimateError("%s.pct: only a later iteration's tooling is a %% of iteration 1" % what)
    if pct is not None:
        amount = None                                 # one mode at a time: % of iteration 1 OR an amount
    filled = amount is not None or pct is not None
    if filled and source is None:
        source = "rule" if pct is not None else "manual"
    if not filled and source in AUTO_SOURCES + ("rule",):
        source = None
    date = _text(raw.get("date"), 32) or None
    if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ][0-9:.+Z-]*)?", date):
        raise EstimateError("%s.date: YYYY-MM-DD" % what)
    factor = _num(raw.get("factor"), what + ".factor")
    return {"status": "filled" if filled else "missing", "amount": amount, "currency": currency, "pct": pct,
            "source": source, "source_ref": _source_ref(raw.get("source_ref"), what) if filled else None,
            "factor": factor if filled else None, "date": date[:10] if date else None,
            "by": _text(raw.get("by"), _MAX_REF) or None, "note": _text(raw.get("note")),
            "hint": "" if filled else _text(raw.get("hint"))}


def _rules_to_amounts(iterations: List[dict]) -> None:
    """An old estimate stored a later iteration's tooling as "N % of iteration 1" (``pct``).  The % is gone (owner
    2026-11-02, "no %"): on read the rule becomes its amount - N % of iteration 1's tooling, in iteration 1's currency,
    source ``converted`` - and, when iteration 1's tooling has no amount, a missing line."""
    first = next((it for it in iterations if it["index"] == 1), None)
    base = first["lines"]["tooling"] if first else None
    for it in iterations:
        line = it["lines"]["tooling"]
        if line.get("pct") is None:
            continue
        pct = float(line["pct"])
        note = line.get("note") or ""
        if base is not None and it["index"] > 1 and base.get("amount") is not None:
            line.update({"status": "filled", "amount": round(float(base["amount"]) * pct / 100.0, 2), "currency": base["currency"],
                         "pct": None, "source": "converted", "source_ref": None, "factor": None, "hint": "",
                         "note": note or ("was %s %% of iteration 1 tooling" % _fmt(pct))})
        else:
            line.update({"status": "missing", "amount": None, "pct": None, "source": None, "source_ref": None, "factor": None,
                         "date": None, "by": None, "note": note, "hint": "iteration 1 tooling missing"})


def _normalize_migration(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    dropped = raw.get("dropped") if isinstance(raw.get("dropped"), list) else []
    return {"from_version": int(raw.get("from_version") or 1), "date": _text(raw.get("date"), 32) or None,
            "dropped": [_text(d, _MAX_REF) for d in dropped if d][:50], "note": _text(raw.get("note"))}


def normalize_estimate(raw: Any, schema: dict, project_id: str, *, legacy: bool = False) -> dict:
    """A full estimate as it is SAVED: every setting and line checked (``EstimateError``), unknown lines refused, missing
    lines and iterations filled with their defaults, the iteration list cut / grown to ``settings.iterations``.
    ``legacy`` (reading stored data only) lets a line keep an old non-EUR currency for ``legacy_to_eur``."""
    if not isinstance(raw, dict):
        raise EstimateError("an estimate object is required")
    settings = normalize_settings(raw.get("settings"), schema, legacy)
    raw_iters = raw.get("iterations") or []
    if not isinstance(raw_iters, list):
        raise EstimateError("iterations: a list")
    by_index: Dict[int, dict] = {}
    for it in raw_iters:
        if not isinstance(it, dict):
            raise EstimateError("iterations: objects")
        by_index[_int(it.get("index"), "iterations.index", 1, 5)] = it
    m_field = _field(schema, "iteration_fields", "motors")
    b_field = _field(schema, "iteration_fields", "bench")
    iterations = []
    for index in range(1, settings["iterations"] + 1):
        src = by_index.get(index)
        if src is None:
            iterations.append(default_iteration(schema, settings, index))
            continue
        bench = src.get("bench", b_field["default"])
        if bench not in b_field["options"]:
            raise EstimateError("iteration %d bench: one of %s" % (index, ", ".join(b_field["options"])))
        lines_raw = src.get("lines") or {}
        if not isinstance(lines_raw, dict):
            raise EstimateError("iteration %d lines: an object" % index)
        unknown = set(lines_raw) - set(LINES)
        if unknown:
            raise EstimateError("iteration %d: unknown lines %s" % (index, ", ".join(sorted(unknown)[:5])))
        iterations.append({
            "index": index,
            "motors": _int(src.get("motors", m_field["default"]), "iteration %d motors" % index, int(m_field["min"]), int(m_field["max"])),
            "bench": bench,
            "lines": {lid: (normalize_line(lines_raw[lid], lid, index, schema, "iteration %d %s" % (index, lid), legacy) if lid in lines_raw
                            else default_line(lid, index, settings["currency"], schema)) for lid in LINES}})
    _rules_to_amounts(iterations)
    return {"version": VERSION, "schema_version": schema["schema_version"], "project_id": project_id,
            "revision": int(raw.get("revision") or 0), "settings": settings, "fx": normalize_fx(raw.get("fx"), schema),
            "iterations": iterations, "migration": _normalize_migration(raw.get("migration")),
            "updated_at": raw.get("updated_at"), "updated_by": raw.get("updated_by")}


# ---------------------------------------------------------------------------------------------------------------------
# exchange rates (EUR base).  ``fx`` = {base, date, rates, source, ecb: {date, rates} | None, manual: {cur: rate},
# manual_date}: ``rates`` are the EFFECTIVE units of each currency per 1 EUR (a manual rate wins over the ECB one),
# ``source`` is "manual" as soon as one rate is manual, else "ECB"; ``date`` is the date shown with the rates (the manual
# date for "manual", else the ECB reference date).  Pure: the ECB fetch and its disk cache are in ``cost_estimate_fx``.
# ---------------------------------------------------------------------------------------------------------------------

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def fx_currencies(schema: dict) -> List[str]:
    """The currencies that need a rate (everything but the EUR base)."""
    return [c for c in schema["currencies"] if c != FX_BASE]


def _rate(v: Any, what: str) -> float:
    x = _num(v, what, lo=0.0, hi=1e9, allow_none=False)
    if not x or x <= 0:
        raise EstimateError("%s: a rate above 0 is required" % what)
    return float(x)


def _rate_map(raw: Any, schema: dict, what: str) -> Dict[str, float]:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise EstimateError("%s: an object {currency: rate}" % what)
    allowed = fx_currencies(schema)
    out: Dict[str, float] = {}
    for cur, value in raw.items():
        c = str(cur).strip().upper()
        if c not in allowed:
            raise EstimateError("%s: %r is not one of %s" % (what, cur, ", ".join(allowed)))
        out[c] = _rate(value, "%s.%s" % (what, c))
    return {c: out[c] for c in allowed if c in out}


def _fx_date(v: Any, what: str) -> Optional[str]:
    d = _text(v, 32)[:10]
    if not d:
        return None
    if not _DATE.fullmatch(d):
        raise EstimateError("%s: YYYY-MM-DD" % what)
    return d


def build_fx(ecb: Optional[dict], manual: Dict[str, float], manual_date: Optional[str]) -> Optional[dict]:
    """The ``fx`` document from the ECB rates (``{date, rates}`` or None) and the manual ones; None when no rate is known."""
    rates: Dict[str, float] = dict((ecb or {}).get("rates") or {})
    rates.update(manual)
    if not rates:
        return None
    source = "manual" if manual else "ECB"
    return {"base": FX_BASE, "date": manual_date if manual else (ecb or {}).get("date"), "rates": rates, "source": source,
            "ecb": {"date": ecb.get("date"), "rates": dict(ecb["rates"])} if ecb and ecb.get("rates") else None,
            "manual": dict(manual), "manual_date": manual_date if manual else None}


def normalize_fx(raw: Any, schema: dict) -> Optional[dict]:
    """A stored / submitted ``fx`` checked and rebuilt (``None`` stays None).  The effective rates are re-derived from the
    ECB part and the manual part, so they can never disagree with them."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise EstimateError("fx: an object or null")
    if raw.get("base", FX_BASE) != FX_BASE:
        raise EstimateError("fx.base: %s" % FX_BASE)
    if raw.get("source") not in (None,) + FX_SOURCES:
        raise EstimateError("fx.source: one of %s" % ", ".join(FX_SOURCES))
    manual = _rate_map(raw.get("manual"), schema, "fx.manual")
    ecb_raw = raw.get("ecb")
    ecb = None
    if isinstance(ecb_raw, dict):
        rates = _rate_map(ecb_raw.get("rates"), schema, "fx.ecb.rates")
        if rates:
            ecb = {"date": _fx_date(ecb_raw.get("date"), "fx.ecb.date"), "rates": rates}
    elif ecb_raw is not None:
        raise EstimateError("fx.ecb: an object or null")
    if ecb is None and not manual:
        # rates given without their parts (a hand-made payload): they are ECB rates of the given date
        rates = _rate_map(raw.get("rates"), schema, "fx.rates")
        if rates and raw.get("source") != "manual":
            ecb = {"date": _fx_date(raw.get("date"), "fx.date"), "rates": rates}
    return build_fx(ecb, manual, _fx_date(raw.get("manual_date"), "fx.manual_date") if manual else None)


def merge_fx(previous: Optional[dict], manual: Optional[Dict[str, float]], live: Optional[dict], schema: dict, today: str) -> Optional[dict]:
    """The ``fx`` to store: the rates of the PREVIOUS stored ``fx`` kept, the live ECB rates (``{date, rates}``) taken when they
    are newer or equal, the manual rates as given (a manual rate wins, dated today unless unchanged).  Nothing is invented: with
    no ECB rate and no manual one the result is None."""
    prev = previous or {}
    prev_ecb = prev.get("ecb") if isinstance(prev.get("ecb"), dict) else None
    ecb = prev_ecb
    if live and live.get("rates") and (prev_ecb is None or str(live.get("date") or "") >= str(prev_ecb.get("date") or "")):
        ecb = {"date": live.get("date"), "rates": dict(live["rates"])}
    manual = _rate_map(manual if manual is not None else prev.get("manual"), schema, "fx.manual")
    manual_date = prev.get("manual_date") if manual and manual == (prev.get("manual") or {}) else (today or None)
    return build_fx(ecb, manual, manual_date if manual else None)


def fx_rates(fx: Optional[dict]) -> Dict[str, float]:
    """Units of each currency per 1 EUR, EUR itself included; only the currencies a rate is known for."""
    rates = {FX_BASE: 1.0}
    for cur, v in ((fx or {}).get("rates") or {}).items():
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and x > 0:
            rates[cur] = x
    return rates


def convert_money(money: Money, display: str, rates: Dict[str, float]) -> Tuple[Optional[float], List[str]]:
    """``money`` expressed in ``display`` at ``rates`` (units per 1 EUR): ``(value, [])``, or ``(None, [currencies without a
    rate])`` - never a guessed rate."""
    total = 0.0
    lacking: List[str] = []
    for cur, amount in money.items():
        if cur == display:
            total += amount
        elif cur in rates and display in rates:
            total += amount / rates[cur] * rates[display]
        else:
            lacking.extend(c for c in (cur, display) if c not in rates and c not in lacking)
    return (None, lacking) if lacking else (total, [])


# ---------------------------------------------------------------------------------------------------------------------
# reading a stored estimate (any shape) and converting the old 68-line shape
# ---------------------------------------------------------------------------------------------------------------------

#: old (version 1) line id prefix -> new line; ``proj_`` (project-wide lines) has no new home
_V1_GROUPS = (("tool_", "tooling"), ("lam_", "manufacturing"), ("mag_", "manufacturing"), ("wire_", "manufacturing"),
              ("wind_", "manufacturing"), ("el_", "manufacturing"), ("mech_", "manufacturing"), ("asm_", "manufacturing"),
              ("scrap_", "manufacturing"), ("redesign_", "manufacturing"), ("test_", "testing"), ("log_", "shipping"))
#: the old percentage lines: scrap = % of the iteration's manufacturing without tooling / redesign; warranty = % of all
#: manufacturing (project-wide, dropped)
_V1_SCRAP = "scrap_allowance"


def _v1_group(item_id: str) -> Optional[str]:
    for prefix, group in _V1_GROUPS:
        if item_id.startswith(prefix):
            return group
    return None


def _money_text(m: Money) -> str:
    return " + ".join("%s %s" % (c, _fmt(a)) for c, a in m.items())


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def migrate_v1(old: dict, schema: dict, project_id: str, *, linked_motors: Iterable[dict] = (), today: str = "") -> dict:
    """Convert a stored estimate of the old 68-line shape (version 1, still a draft) into the 4-line shape.

    Per iteration the old filled lines are summed into the four new lines (tooling / manufacturing / testing / shipping);
    manufacturing becomes a per-motor cost (old total / the old motors per iteration).  A converted line has source
    ``converted`` and a note naming how many old lines it sums.  A group whose old lines are in several currencies is
    left missing with a hint listing them (no exchange rate is invented).  A later iteration whose old tooling was only
    the default "% of iteration 1" keeps that % as the new rule.  Project-wide lines (engineering hours, travel,
    warranty) and payment milestones have no new home: they are listed in ``migration.dropped``.  When the owner had
    filled nothing (only the default rules), the estimate starts blank with the old settings."""
    s_old = old.get("settings") if isinstance(old.get("settings"), dict) else {}
    defaults = default_settings(schema, linked_motors=linked_motors)
    complex_project = bool(s_old.get("complex"))
    try:
        n_iter = min(5, max(1, int(s_old.get("iterations") or default_iterations(complex_project, schema))))
    except (TypeError, ValueError):
        n_iter = default_iterations(complex_project, schema)
    currency = s_old.get("currency") if s_old.get("currency") in schema["currencies"] else schema["default_currency"]
    pct = _f(s_old.get("contingency_pct"))
    target = s_old.get("target_motor") if isinstance(s_old.get("target_motor"), dict) else defaults["target_motor"]
    settings = {"complex": complex_project, "iterations": n_iter, "currency": currency,
                "contingency_pct": pct if pct is not None and 0 <= pct <= 100 else defaults["contingency_pct"],
                "target_motor": target}
    try:
        motors = min(50, max(1, int(s_old.get("motors_per_iteration") or 2)))
    except (TypeError, ValueError):
        motors = 2

    old_iters = {}
    for it in old.get("iterations") or []:
        if isinstance(it, dict) and isinstance(it.get("lines"), dict):
            try:
                old_iters[int(it.get("index"))] = it
            except (TypeError, ValueError):
                continue

    def plain(line: dict) -> Optional[Money]:
        q, u = _f(line.get("qty")), _f(line.get("unit_cost"))
        if q is None or u is None:
            return None
        cur = str(line.get("currency") or currency).upper()
        return {cur if cur in schema["currencies"] else currency: q * u}

    owner_filled = False
    first_amounts: Dict[str, Money] = {}
    iterations = []
    for index in range(1, n_iter + 1):
        it_old = old_iters.get(index) or {}
        lines_old = it_old.get("lines") or {}
        bench = (it_old.get("settings") or {}).get("bench")
        new_it = default_iteration(schema, settings, index)
        new_it["motors"] = motors
        new_it["bench"] = bench if bench in _field(schema, "iteration_fields", "bench")["options"] else new_it["bench"]
        sums: Dict[str, Money] = {g: {} for g in LINES}
        counts = {g: 0 for g in LINES}
        derive_pcts: Dict[str, set] = {g: set() for g in LINES}
        manual_like = {g: 0 for g in LINES}
        scrap_pct = None
        for iid, line in lines_old.items():
            group = _v1_group(str(iid))
            if group is None or not isinstance(line, dict) or line.get("status") != "filled":
                continue
            if line.get("source") != "rule":
                owner_filled = True
            if iid == _V1_SCRAP:
                scrap_pct = _f(line.get("qty"))
                continue
            if isinstance(line.get("derive"), dict):
                base = first_amounts.get(iid) if index > 1 else None
                p = _f(line["derive"].get("pct"))
                if base is None or p is None:
                    continue
                amount = {c: a * p / 100.0 for c, a in base.items()}
                derive_pcts[group].add(p)
            else:
                amount = plain(line)
                if amount is None:
                    continue
                manual_like[group] += 1
            if index == 1:
                first_amounts[iid] = amount
            for c, a in amount.items():
                sums[group][c] = sums[group].get(c, 0.0) + a
            counts[group] += 1
        if scrap_pct:
            # the old scrap line: % of this iteration's manufacturing (tooling, redesign and scrap excluded)
            base: Money = {}
            for iid, line in lines_old.items():
                if _v1_group(str(iid)) == "manufacturing" and not str(iid).startswith(("redesign_", "scrap_")) \
                        and isinstance(line, dict) and line.get("status") == "filled" and not line.get("derive"):
                    for c, a in (plain(line) or {}).items():
                        base[c] = base.get(c, 0.0) + a
            for c, a in base.items():
                sums["manufacturing"][c] = sums["manufacturing"].get(c, 0.0) + a * scrap_pct / 100.0
            if base:
                counts["manufacturing"] += 1
        for group in LINES:
            money = dict(sums[group])
            if not counts[group]:
                continue                                       # nothing filled: the new default line stays
            if group == "tooling" and index > 1 and not manual_like[group] and len(derive_pcts[group]) == 1:
                new_it["lines"]["tooling"].update({"status": "filled", "pct": next(iter(derive_pcts[group])), "source": "rule",
                                                   "amount": None})
                continue
            line = blank_line(currency)
            if len(money) == 1:
                cur, total = next(iter(money.items()))
                per_motor = group == "manufacturing"
                line.update({"status": "filled", "currency": cur, "source": "converted", "date": today or None,
                             "amount": round(total / motors, 2) if per_motor else round(total, 2),
                             "note": ("%d old line(s), %s %s total over %d motor(s)" % (counts[group], cur, _fmt(total), motors))
                             if per_motor else "%d old line(s)" % counts[group]})
            else:
                line["hint"] = "old lines in several currencies (%s): enter it in one currency" % _money_text(money)
            new_it["lines"][group] = line
        iterations.append(new_it)

    dropped = []
    for iid, line in (old.get("project_lines") or {}).items():
        if not isinstance(line, dict) or line.get("status") != "filled":
            continue
        owner_filled = True
        money = plain(line)
        q = _f(line.get("qty"))
        dropped.append("project-wide %s: %s" % (iid, _money_text(money) if money else ("%s %%" % _fmt(q) if q is not None else "set")))
    for it in old.get("iterations") or []:
        mp = ((it or {}).get("settings") or {}).get("milestone_pct") if isinstance(it, dict) else None
        if mp is not None:
            dropped.append("payment milestone of iteration %s: %s %%" % (it.get("index"), _fmt(mp)))

    est = {"version": VERSION, "schema_version": schema["schema_version"], "project_id": project_id,
           "revision": int(old.get("revision") or 0), "settings": settings, "iterations": iterations,
           "updated_at": old.get("updated_at"), "updated_by": old.get("updated_by")}
    if not owner_filled:
        blank = [default_iteration(schema, settings, i) for i in range(1, n_iter + 1)]
        for it, b in zip(iterations, blank):
            b["motors"], b["bench"] = it["motors"], it["bench"]
        est["iterations"] = blank
        est["migration"] = {"from_version": 1, "date": today or None, "dropped": [],
                            "note": "The old detailed estimate had no filled line: started blank with its settings."}
    else:
        est["migration"] = {"from_version": 1, "date": today or None, "dropped": dropped[:50],
                            "note": "Converted from the old detailed estimate: lines summed into tooling / manufacturing / "
                                    "testing / shipping per iteration."}
    return normalize_estimate(est, schema, project_id, legacy=True)


def _money_text_one(x: float) -> str:
    return "{:,.2f}".format(x).replace(",", " ")


def legacy_to_eur(estimate: dict, schema: dict, fallback_rates: Optional[Dict[str, float]] = None) -> dict:
    """Old data had a currency per line.  Every amount is in EUR now: a line in another currency becomes EUR at the
    estimate's stored rate (``fx``; the ECB or the owner's), else at ``fallback_rates`` (the cached ECB rates); source
    ``converted`` and the original amount in the note ("was CNY 400.00").  With no rate known the line is MISSING, its hint
    giving the old amount - a rate is never invented."""
    base = schema["line_currency"]
    est = copy.deepcopy(estimate)
    rates = dict(fallback_rates or {})
    rates.update(fx_rates(est.get("fx")))
    for it in est["iterations"]:
        for lid in LINES:
            line = it["lines"][lid]
            cur = line.get("currency") or base
            if cur == base:
                continue
            amount = line.get("amount")
            line["currency"] = base
            if amount is None:
                continue
            was = "was %s %s" % (cur, _money_text_one(float(amount)))
            note = line.get("note") or ""
            rate = rates.get(cur)
            if rate:
                line.update({"status": "filled", "amount": round(float(amount) / rate, 2), "source": "converted", "source_ref": None,
                             "factor": None, "hint": "", "note": ((note + "; ") if note else "") + was})
            else:
                line.update({"status": "missing", "amount": None, "source": None, "source_ref": None, "factor": None, "date": None,
                             "by": None, "note": note, "hint": "%s: no exchange rate known, enter it in %s" % (was, base)})
    return est


def from_stored(stored: Any, schema: dict, project_id: str, *, linked_motors: Iterable[dict] = (), today: str = "",
                fallback_rates: Optional[Dict[str, float]] = None) -> dict:
    """The estimate of a stored document of ANY shape: version 2 normalised, version 1 converted (``migrate_v1``).  Never
    raises: a document that cannot be read starts blank (keeping its revision, so the owner can save over it)."""
    if isinstance(stored, dict):
        try:
            if stored.get("version") == 1:
                return legacy_to_eur(migrate_v1(stored, schema, project_id, linked_motors=linked_motors, today=today), schema, fallback_rates)
            return legacy_to_eur(normalize_estimate(stored, schema, project_id, legacy=True), schema, fallback_rates)
        except (EstimateError, TypeError, ValueError, KeyError, AttributeError):
            pass
    est = default_estimate(schema, project_id, linked_motors=linked_motors)
    if isinstance(stored, dict):
        try:
            est["revision"] = int(stored.get("revision") or 0)
        except (TypeError, ValueError):
            pass
        version = stored.get("version")
        est["migration"] = {"from_version": version if isinstance(version, int) else 0,
                            "date": today or None, "dropped": [], "note": "The stored estimate could not be read: started blank."}
    return est


# ---------------------------------------------------------------------------------------------------------------------
# totals
# ---------------------------------------------------------------------------------------------------------------------

def _rounded(acc: Money, order: List[str]) -> Money:
    rank = {c: i for i, c in enumerate(order)}
    return {c: round(acc[c], 2) for c in sorted(acc, key=lambda c: (rank.get(c, 99), c))}


def _merge(acc: Money, other: Money) -> None:
    for cur, amount in other.items():
        acc[cur] = acc.get(cur, 0.0) + amount


def display_currency_of(estimate: dict, schema: dict, display_currency: Optional[str] = None) -> str:
    """The currency the money is shown in: the owner's own choice stored in the estimate (``settings.display_currency``), else
    the caller's (the UI language's: en -> EUR, zh-CN -> CNY), else the schema default.  An unknown code is refused."""
    def valid(code: Any) -> Optional[str]:
        code = str(code or "").strip().upper()
        if code and code not in schema["currencies"]:
            raise EstimateError("display_currency: one of %s" % ", ".join(schema["currencies"]))
        return code or None
    asked = valid(display_currency)
    return valid((estimate.get("settings") or {}).get("display_currency")) or asked or schema["default_currency"]


def compute(estimate: dict, schema: dict, display_currency: Optional[str] = None) -> dict:
    """Per iteration: tooling / manufacturing / testing / shipping / subtotal; then the project subtotal, contingency and
    total - all PER CURRENCY - plus the missing lines (``[{iteration, line, reason}]``).  ``converted`` repeats the lines and
    the totals in the display currency at the estimate's ``fx`` rates (see ``display_currency_of``); with a rate missing for a
    currency in use it is ``available: false`` and the per-currency figures are the only totals."""
    order = list(schema["currencies"])
    display = display_currency_of(estimate, schema, display_currency)
    rates = fx_rates(estimate.get("fx"))
    lacking: List[str] = []
    conv_iters: List[dict] = []
    missing: List[dict] = []
    out_iters = []
    for it in estimate["iterations"]:
        index = it["index"]
        lines = it["lines"]
        amounts: Dict[str, Optional[Money]] = {}
        reasons: Dict[str, str] = {}
        for lid in LINES:
            line = lines[lid]
            if lid == "testing" and it.get("bench") == "none":
                amounts[lid] = {}                              # no bench in this iteration: nothing to test, nothing missing
                continue
            if line.get("amount") is None:
                amounts[lid], reasons[lid] = None, line.get("hint") or "not filled"
                continue
            mult = float(it.get("motors") or 1) if lid == "manufacturing" else 1.0
            amounts[lid] = {line["currency"]: float(line["amount"]) * mult}
        subtotal: Money = {}
        line_out = {}
        conv_lines: Dict[str, Optional[float]] = {}
        for lid in LINES:
            amount = amounts[lid]
            if amount is None:
                missing.append({"iteration": index, "line": lid, "reason": reasons.get(lid, "not filled")})
                conv_lines[lid] = None
            else:
                _merge(subtotal, amount)
                value, lack = convert_money(amount, display, rates)
                conv_lines[lid] = None if value is None else round(value, 2)
                lacking.extend(c for c in lack if c not in lacking)
            line_out[lid] = {"amount": None if amount is None else _rounded(amount, order),
                             "origin": origin_text(lines[lid], index, bench=it.get("bench") if lid == "testing" else None)}
        out_iters.append({"index": index, "motors": it.get("motors"), "bench": it.get("bench"),
                          **{lid: (None if amounts[lid] is None else _rounded(amounts[lid], order)) for lid in LINES},
                          "subtotal": _rounded(subtotal, order), "lines": line_out})
        conv_iters.append({"index": index, "lines": conv_lines, "_subtotal": subtotal})
    subtotal_all: Money = {}
    for it in out_iters:
        _merge(subtotal_all, it["subtotal"])
    pct = float(estimate["settings"].get("contingency_pct") or 0.0)
    contingency = {c: a * pct / 100.0 for c, a in subtotal_all.items()}
    total = {c: subtotal_all[c] + contingency[c] for c in subtotal_all}
    currencies = list(_rounded(total, order).keys())
    warnings = []
    available = not lacking
    if len(currencies) > 1 and not available:
        warnings.append("Lines are in several currencies (%s) and no exchange rate is known for %s; totals stay per currency."
                        % (", ".join(currencies), ", ".join(sorted(lacking, key=lambda c: order.index(c) if c in order else 99))))
    conv_total = None
    for ci in conv_iters:
        sub = ci.pop("_subtotal")
        value, _lack = convert_money(sub, display, rates)
        ci["subtotal"] = None if value is None else round(value, 2)
    if available:
        sub_all, _l = convert_money(subtotal_all, display, rates)
        sub_all = sub_all or 0.0
        conv_total = {"subtotal": round(sub_all, 2), "contingency": round(sub_all * pct / 100.0, 2),
                      "total": round(sub_all * (1 + pct / 100.0), 2)}
    converted = {"currency": display, "available": available,
                 "missing_rates": sorted(lacking, key=lambda c: order.index(c) if c in order else 99),
                 "iterations": conv_iters, **(conv_total or {"subtotal": None, "contingency": None, "total": None})}
    return {"iterations": out_iters, "subtotal": _rounded(subtotal_all, order), "contingency_pct": pct,
            "contingency": _rounded(contingency, order), "total": _rounded(total, order), "currencies": currencies,
            "converted": converted, "missing": missing, "missing_count": len(missing), "warnings": warnings}


def origin_text(line: dict, index: int = 1, *, bench: Optional[str] = None) -> str:
    """Where the number came from, in one English line (the web shows its own translated chip)."""
    if bench == "none":
        return "no test bench in this iteration"
    if line.get("status") != "filled":
        return "missing" + ((": " + line["hint"]) if line.get("hint") else "")
    src, ref = line.get("source"), line.get("source_ref") or {}
    if src == "production":
        return "from production %s, x%s by active mass" % (ref.get("production") or "?", _fmt(line.get("factor")))
    if src == "estimate":
        return "from estimate of project %s%s" % (ref.get("project") or "?",
                                                  (" (iteration %s)" % ref["iteration"]) if ref.get("iteration") else "")
    if src == "converted":
        return "converted from the old detailed estimate" + ((" (%s)" % line["note"]) if line.get("note") else "")
    if src == "rule":
        return "default rule"
    return ("entered by %s on %s" % (line.get("by") or "the owner", line.get("date") or "")).strip()


def _fmt(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "?"
    return ("%.3f" % v).rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------------------------------------------------
# suggest
# ---------------------------------------------------------------------------------------------------------------------

def _newest_first(stamp: Any) -> Tuple[int, str]:
    """Sort key: a newer ISO time stamp first, an undated record last."""
    s = str(stamp or "")
    return (0, "".join(chr(0x10FFFF - ord(c)) for c in s)) if s else (1, "")


def pick_analog(productions: List[dict], kind: str, target_mass: Optional[float], schema: dict) -> Tuple[Optional[dict], str]:
    """The production to base ``kind`` (``cost_per_motor`` or ``tooling``) on: among the productions that HAVE that data
    and a simulated active mass, the one whose active mass is closest to the target's (the factor target / analog nearest
    1, i.e. the smallest |ln factor|; ties: the newest).  ``(None, one-line reason)`` when there is none, when the
    factor is outside the limits (0.25..4: the nearest analog and its factor are named) or when the analog's money is in
    several currencies (no exchange rate)."""
    what = "per-motor cost" if kind == "cost_per_motor" else "tooling"
    with_data = [p for p in productions if any((v or 0) > 0 for v in (p.get(kind) or {}).values())]
    if not with_data:
        return None, "no built motor has %s data" % what
    if not target_mass or target_mass <= 0:
        return None, "the project's motor has no simulated active mass"
    with_mass = [p for p in with_data if (p.get("active_mass_kg") or 0) > 0]
    if not with_mass:
        return None, "no built motor with %s data has a simulated active mass" % what
    best = min(with_mass, key=lambda p: (round(abs(math.log(target_mass / float(p["active_mass_kg"]))), 9),
                                         _newest_first(p.get("updated_at")), str(p.get("ref"))))
    factor = round(target_mass / float(best["active_mass_kg"]), 4)
    lo, hi = factor_limits(schema)
    if not lo <= factor <= hi:
        return None, "nearest analog %s is x%s by active mass, outside %s..%s: enter it" % (best["ref"], _fmt(factor), _fmt(lo), _fmt(hi))
    money = {c: float(v) for c, v in (best.get(kind) or {}).items() if v}
    if len(money) != 1:
        return None, "analog %s %s is in several currencies (%s), x%s: enter it in one currency" % (
            best["ref"], what, _money_text(money), _fmt(factor))
    cur, base = next(iter(money.items()))
    return {"ref": best["ref"], "title": best.get("title") or best["ref"], "factor": factor, "currency": cur, "base": base,
            "amount": round(base * factor, 2), "analog_mass_kg": float(best["active_mass_kg"]), "target_mass_kg": float(target_mass),
            "missing_prices": int(best.get("missing_prices") or 0)}, ""


def _from_estimates(estimates: List[dict], line_id: str, index: int, bench: Optional[str]) -> Optional[dict]:
    """Another project's value for ``line_id``, as-is: the newest estimate first; in it the same iteration first (and for
    testing the same bench), else its first filled iteration."""
    best = None
    for other in estimates:
        for it in other.get("iterations") or []:
            line = (it.get("lines") or {}).get(line_id) or {}
            if line.get("status") != "filled" or line.get("amount") is None or line.get("currency") != "EUR":
                continue
            if line_id == "testing" and it.get("bench") == "none":
                continue
            key = (_newest_first(other.get("updated_at")), 0 if it.get("index") == index else 1,
                   0 if (line_id != "testing" or it.get("bench") == bench) else 1, int(it.get("index") or 0))
            if best is None or key < best[0]:
                best = (key, other, it, line)
    if best is None:
        return None
    _k, other, it, line = best
    return {"project": other["project_id"], "title": other.get("title") or other["project_id"], "iteration": it.get("index"),
            "amount": float(line["amount"]), "currency": line["currency"]}


def _suggestable(line: dict, refresh: bool) -> bool:
    if line.get("status") == "missing":
        return True                                    # no value to lose
    # a filled line: the owner's numbers (manual, converted) are never overwritten; with refresh an earlier suggestion is
    return refresh and line.get("source") in AUTO_SOURCES


def suggest(estimate: dict, schema: dict, data: dict, *, today: str, refresh: bool = False) -> Tuple[dict, dict]:
    """Fill the missing lines from ``data`` and return ``(new estimate, report)``.

    ``data`` = ``{"target_ref", "target_mass_kg", "productions": [{ref, title, updated_at, active_mass_kg,
    cost_per_motor: {cur: amount}, tooling: {cur: amount}, missing_prices}], "estimates": [{project_id, title, updated_at,
    iterations: [{index, bench, lines}]}]}``.

    * manufacturing (every iteration): the nearest analog's per-motor cost x (target / analog active mass);
    * tooling of iteration 1: the nearest analog's tooling sum x the same kind of factor; a later iteration's tooling is
      left for the owner to enter;
    * testing and shipping: another project's value for that line, as-is.
    A line that cannot be suggested stays missing with a one-line ``hint``.  Never touches a ``manual`` or ``converted``
    line; with ``refresh`` it also re-suggests lines an earlier suggestion filled."""
    est = copy.deepcopy(estimate)
    target_mass = data.get("target_mass_kg")
    productions = list(data.get("productions") or [])
    estimates = [e for e in data.get("estimates") or [] if e.get("project_id") != est.get("project_id")]
    picks = {kind: pick_analog(productions, kind, target_mass, schema) for kind in ("cost_per_motor", "tooling")}
    rates = fx_rates(est.get("fx"))
    base_cur = schema["line_currency"]
    filled: List[dict] = []
    skipped: List[dict] = []
    notes: List[str] = []

    def put(line: dict, new: dict, index: int, lid: str) -> None:
        note = line.get("note") or ""
        line.clear()
        line.update(blank_line(new["currency"]))
        line.update(new)
        line.update({"status": "filled", "date": today, "by": "suggest", "note": note, "hint": ""})
        filled.append({"iteration": index, "line": lid, "source": new["source"]})

    def miss(line: dict, index: int, lid: str, reason: str) -> None:
        if line.get("status") == "missing":            # a refreshed earlier suggestion keeps its number
            line["hint"] = reason[:_MAX_NOTE]
        skipped.append({"iteration": index, "line": lid, "reason": reason})

    for it in est["iterations"]:
        index = it["index"]
        for lid in LINES:
            line = it["lines"][lid]
            if not _suggestable(line, refresh):
                continue
            if lid == "testing" and it.get("bench") == "none":
                continue
            if lid == "tooling" and index > 1:
                miss(line, index, lid, "a later iteration's tooling is not suggested: enter it")
                continue
            if lid in ("manufacturing", "tooling"):
                pick, why = picks["cost_per_motor" if lid == "manufacturing" else "tooling"]
                if pick is None:
                    miss(line, index, lid, why)
                    continue
                amount = pick["amount"]
                if pick["currency"] != base_cur:               # every amount is in EUR: the analog's money is converted
                    if not rates.get(pick["currency"]):
                        miss(line, index, lid, "analog %s is priced in %s and no exchange rate is known: enter it in %s"
                             % (pick["ref"], pick["currency"], base_cur))
                        continue
                    amount = round(amount / rates[pick["currency"]], 2)
                put(line, {"amount": amount, "currency": base_cur, "source": "production", "factor": pick["factor"],
                           "source_ref": {"production": pick["ref"], "title": pick["title"],
                                          "analog_mass_kg": pick["analog_mass_kg"], "target_mass_kg": pick["target_mass_kg"]}},
                    index, lid)
                if pick["missing_prices"]:
                    notes.append("analog %s has %d unpriced BOM row(s): its per-motor cost is incomplete"
                                 % (pick["ref"], pick["missing_prices"]))
                continue
            other = _from_estimates(estimates, lid, index, it.get("bench"))
            if other is None:
                miss(line, index, lid, "no other project's estimate has %s: enter it" % lid)
                continue
            put(line, {"amount": other["amount"], "currency": other["currency"], "source": "estimate", "factor": None,
                       "source_ref": {"project": other["project"], "title": other["title"], "iteration": str(other["iteration"])}},
                index, lid)
    report = {"filled": filled, "skipped": skipped, "filled_count": len(filled), "notes": sorted(set(notes)),
              "target_ref": data.get("target_ref") or "",
              "target_mass_kg": target_mass,
              "analogs": {("manufacturing" if k == "cost_per_motor" else "tooling"):
                          ({"ref": p["ref"], "factor": p["factor"]} if p else {"ref": None, "reason": why})
                          for k, (p, why) in picks.items()},
              "estimates": [e.get("project_id") for e in estimates]}
    return est, report


# ---------------------------------------------------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------------------------------------------------

_LINE_FIELDS = ("status", "amount", "currency", "pct", "source", "source_ref", "factor", "note")


def _flat(estimate: dict) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (estimate.get("settings") or {}).items():
        out["settings.%s" % k] = v
    fx = estimate.get("fx") or {}
    for k in ("date", "source"):
        if fx.get(k) is not None:
            out["fx.%s" % k] = fx[k]
    for cur, rate in (fx.get("rates") or {}).items():
        out["fx.rates.%s" % cur] = rate
    for it in estimate.get("iterations") or []:
        for k in ("motors", "bench"):
            out["iteration %s.%s" % (it["index"], k)] = it.get(k)
        for lid, line in (it.get("lines") or {}).items():
            for f in _LINE_FIELDS:
                out["iteration %s.%s.%s" % (it["index"], lid, f)] = line.get(f)
    return out


def diff(old: Optional[dict], new: dict, schema: Optional[dict] = None) -> List[dict]:
    """What changed from ``old`` to ``new``: ``[{path, was, now}]`` over the settings, the iteration fields and every line
    field (``None`` -> everything is new).  With ``schema`` the totals are compared too (``total.<currency>``)."""
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
