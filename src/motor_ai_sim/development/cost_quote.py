"""The customer's read-only QUOTE of a project (owner 2026-11-03): what the members of a project see once an admin has
checked the cost estimate and shared it.  Pure: built from a FROZEN snapshot of the estimate, never from the live one.

The payload is a WHITELIST (``QUOTE_KEYS`` / ``ITERATION_KEYS`` / ``LINE_KEYS``), never the raw estimate:

* per iteration: index, motors, bench, the four lines (tooling, manufacturing, testing, shipping) with their customer
  price and weeks, the iteration price and the iteration weeks;
* the total price and weeks, the currency (EUR), the dated ECB rates for the EUR / USD / CNY display switch, the delivery
  place ("DAP <city>"), the payment terms and the date it was shared.

The CONTINGENCY is folded into the prices and never named: each line is ``line x (1 + contingency %)`` rounded to whole EUR;
the iterations are rounded the same way and the largest one adjusted so they sum exactly to the rounded total; inside
each iteration the largest line is adjusted so the lines sum exactly to the iteration price.

Never sent: notes, hints, sources and source references (DHL lane, analog production, weight basis), factors, percentages,
who/when per line, manual exchange rates, DHL lanes, analog or BOM references, the revision history, other snapshots."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from motor_ai_sim.development import cost_estimate as CE

QUOTE_KEYS = ("currency", "iterations", "total_price", "total_weeks", "exchange_rates", "delivery", "payment_terms",
              "shared_on")
ITERATION_KEYS = ("index", "motors", "bench", "lines", "price", "weeks")
LINE_KEYS = ("price", "weeks")
RATE_KEYS = ("date", "per_eur")
CURRENCY = "EUR"


class QuoteError(ValueError):
    """The snapshot cannot be quoted (a line without an amount)."""


def _dec(x: Any) -> Decimal:
    return Decimal(str(x))


def _whole(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _fit(raw: List[Decimal], target: int) -> List[int]:
    """``raw`` rounded to whole EUR, the largest part adjusted so the parts sum exactly to ``target``."""
    parts = [_whole(r) for r in raw]
    diff = target - sum(parts)
    if diff and parts:
        largest = max(range(len(raw)), key=lambda i: (raw[i], -i))
        parts[largest] += diff
    return parts


def _weeks(v: Any) -> Optional[float]:
    if v is None:
        return None
    x = float(v)
    return int(x) if x.is_integer() else round(x, 2)


def line_costs(it: dict) -> Dict[str, Optional[Decimal]]:
    """The EUR cost of each line of one iteration (manufacturing = per motor x motors; testing 0 without a bench);
    ``None`` = the line has no amount."""
    out: Dict[str, Optional[Decimal]] = {}
    for lid in CE.LINES:
        line = (it.get("lines") or {}).get(lid) or {}
        if lid == "testing" and it.get("bench") == "none":
            out[lid] = Decimal(0)
            continue
        amount = line.get("amount")
        if amount is None:
            out[lid] = None
            continue
        out[lid] = _dec(amount) * (int(it.get("motors") or 1) if lid == "manufacturing" else 1)
    return out


def missing_lines(estimate: dict) -> List[dict]:
    """``[{iteration, line}]`` of the lines a quote cannot be built without (an admin cannot share it then)."""
    return [{"iteration": it.get("index"), "line": lid} for it in estimate.get("iterations") or []
            for lid, cost in line_costs(it).items() if cost is None]


def delivery_text(settings: dict) -> Optional[str]:
    """"DAP <city>" (the country code when no city is known); None without a destination."""
    dest = settings.get("ship_to") or {}
    place = str(dest.get("city") or "").strip() or str(dest.get("country") or "").strip()
    return ("DAP " + place) if place else None


def exchange_rates(fx: Optional[dict], schema: dict) -> Optional[dict]:
    """The dated ECB reference rates of the snapshot (units per 1 EUR) for the display switch; never a manual rate."""
    ecb = (fx or {}).get("ecb") or {}
    rates = {c: float(v) for c, v in (ecb.get("rates") or {}).items()
             if c in schema["currencies"] and c != CURRENCY and isinstance(v, (int, float)) and v > 0}
    if not rates:
        return None
    return {"date": str(ecb.get("date") or "") or None, "per_eur": rates}


def build_quote(estimate: dict, schema: dict, shared_on: str) -> dict:
    """The customer payload of a frozen ``estimate`` (see the module doc).  ``QuoteError`` when a line has no amount."""
    missing = missing_lines(estimate)
    if missing:
        raise QuoteError("%d line(s) without an amount" % len(missing))
    settings = estimate.get("settings") or {}
    k = 1 + _dec(settings.get("contingency_pct") or 0) / 100
    iterations = list(estimate.get("iterations") or [])
    costs = [line_costs(it) for it in iterations]
    total = _whole(sum((c for cs in costs for c in cs.values()), Decimal(0)) * k)
    it_prices = _fit([sum(cs.values(), Decimal(0)) * k for cs in costs], total)
    out_iters = []
    for it, cs, price in zip(iterations, costs, it_prices):
        line_prices = _fit([cs[lid] * k for lid in CE.LINES], price)
        weeks = CE.iteration_weeks(it)
        out_iters.append({
            "index": int(it["index"]), "motors": int(it.get("motors") or 0), "bench": str(it.get("bench") or "none"),
            "lines": {lid: {"price": p, "weeks": _weeks(weeks.get(lid))} for lid, p in zip(CE.LINES, line_prices)},
            "price": price, "weeks": _weeks(weeks["total"])})
    quote = {"currency": CURRENCY, "iterations": out_iters, "total_price": total,
             "total_weeks": _weeks(sum(float(it["weeks"] or 0) for it in out_iters)),
             "exchange_rates": exchange_rates(estimate.get("fx"), schema), "delivery": delivery_text(settings),
             "payment_terms": str(settings.get("payment_terms") or ""), "shared_on": str(shared_on or "")[:10]}
    assert tuple(quote) == QUOTE_KEYS
    return quote
