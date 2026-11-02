"""Exchange rates for the admin-only cost estimate (owner 2026-11-02): the European Central Bank euro reference rates.

The daily XML (``eurofxref-daily.xml``, no key; CNY and USD are in it) is fetched at most once a day and cached on disk under
the shared data folder (``<shared>/fx/ecb_eurofxref.json``, or ``$MOTRES_FX_CACHE_DIR``).  When the fetch fails the last
cached rates are kept and the answer says so (``state: "stale"``); with nothing cached there is no rate at all
(``state: "none"``) - a rate is never invented.  A failed fetch is not retried for ``RETRY_AFTER_S`` so a dead network does not
slow every request.  Pure parsing (``parse_ecb_xml``) is separate from the I/O (``fetch_text`` - replaced in tests).

``live_rates()`` returns ``(ecb, status)``: ``ecb`` = ``{date, rates: {CNY: x, USD: y}}`` or None, units per 1 EUR."""
from __future__ import annotations

import datetime
import math
import os
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from motor_ai_sim import json_store

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
CURRENCIES = ("CNY", "USD")            # the non-EUR currencies the estimate uses
RETRY_AFTER_S = 15 * 60
TIMEOUT_S = 8
MAX_BYTES = 200_000


class FxError(Exception):
    """The ECB answer could not be read."""


def parse_ecb_xml(text: str, currencies=CURRENCIES) -> dict:
    """``{date: "YYYY-MM-DD", rates: {CNY: x, ...}}`` from the ECB daily XML (any namespace prefix).  A DTD / entity is refused
    (nothing legitimate in this feed has one); a missing date or currency raises ``FxError``."""
    low = text.lower()
    if "<!doctype" in low or "<!entity" in low:
        raise FxError("unexpected DTD in the ECB answer")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FxError("not valid XML: %s" % exc) from None
    date: Optional[str] = None
    found: Dict[str, float] = {}
    for el in root.iter():
        if not str(el.tag).endswith("Cube"):
            continue
        if el.get("time"):
            date = el.get("time")
        cur = el.get("currency")
        if cur in currencies:
            try:
                rate = float(el.get("rate"))
            except (TypeError, ValueError):
                raise FxError("bad rate for %s" % cur) from None
            if not math.isfinite(rate) or rate <= 0:
                raise FxError("bad rate for %s" % cur)
            found[cur] = rate
    try:
        datetime.date.fromisoformat(date or "")
    except ValueError:
        raise FxError("no reference date in the ECB answer") from None
    missing = [c for c in currencies if c not in found]
    if missing:
        raise FxError("no rate for %s in the ECB answer" % ", ".join(missing))
    return {"date": date, "rates": {c: found[c] for c in currencies}}


def fetch_text(url: str = ECB_URL) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "aerostator-cost-estimate/1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:     # noqa: S310 - a fixed https URL
        return resp.read(MAX_BYTES).decode("utf-8", "replace")


def cache_path() -> Path:
    configured = os.environ.get("MOTRES_FX_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser() / "ecb_eurofxref.json"
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "fx" / "ecb_eurofxref.json"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _read_cache() -> dict:
    try:
        value = json_store.read_json(cache_path(), default=None)
    except Exception:                                   # an unreadable cache is no cache
        return {}
    return value if isinstance(value, dict) else {}


def _write_cache(doc: dict) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        json_store.atomic_write_json(path, doc)
    except OSError:
        pass                                            # a read-only share: the rates still work for this request


def _stamp(dt: datetime.datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _since(stamp: Any, now: datetime.datetime) -> Optional[float]:
    try:
        return (now - datetime.datetime.fromisoformat(str(stamp))).total_seconds()
    except ValueError:
        return None


def _cached_rates(cache: dict) -> Optional[dict]:
    rates = cache.get("rates")
    if isinstance(rates, dict) and cache.get("date") and all(isinstance(rates.get(c), (int, float)) and rates[c] > 0 for c in CURRENCIES):
        return {"date": str(cache["date"]), "rates": {c: float(rates[c]) for c in CURRENCIES}}
    return None


def live_rates(now: Optional[datetime.datetime] = None) -> Tuple[Optional[dict], dict]:
    """``(ecb, status)``.  ``status`` = ``{state, date, fetched_at, error}`` with ``state``:

    * ``"ok"``    - rates fetched within the last day (or cached today);
    * ``"stale"`` - the fetch failed, the last cached rates (of ``date``) are used;
    * ``"none"``  - the fetch failed and nothing is cached: no rate is known."""
    now = now or _now()
    cache = _read_cache()
    have = _cached_rates(cache)
    age = _since(cache.get("fetched_at"), now)
    if have and age is not None and 0 <= age < 24 * 3600:
        return have, {"state": "ok", "date": have["date"], "fetched_at": cache.get("fetched_at"), "error": None}
    retry = _since(cache.get("last_attempt"), now)
    if retry is not None and 0 <= retry < RETRY_AFTER_S and cache.get("last_error"):
        error = str(cache["last_error"])
    else:
        try:
            doc = parse_ecb_xml(fetch_text())
        except Exception as exc:                        # network, TLS, timeout, bad XML: all the same to the caller
            error = ("%s: %s" % (type(exc).__name__, exc))[:300]
            _write_cache({**cache, "last_attempt": _stamp(now), "last_error": error})
        else:
            _write_cache({"date": doc["date"], "rates": doc["rates"], "fetched_at": _stamp(now), "source": "ECB",
                          "last_attempt": _stamp(now), "last_error": None})
            return doc, {"state": "ok", "date": doc["date"], "fetched_at": _stamp(now), "error": None}
    if have:
        return have, {"state": "stale", "date": have["date"], "fetched_at": cache.get("fetched_at"), "error": error}
    return None, {"state": "none", "date": None, "fetched_at": None, "error": error}
