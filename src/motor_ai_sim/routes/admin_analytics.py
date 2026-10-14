"""Admin -> Analytics: site statistics from the self-hosted Umami.

``GET /api/admin/analytics?range=24h|7d|30d`` (admin only, ``require_admin``) asks the
Umami REST API server-side and returns one compact payload for the Admin panel:
totals (visitors, visits, page views, bounce rate, average visit time), a daily
series, top pages, countries, referrers, browsers, operating systems and devices.

Umami itself is internal only (docker network); the browser never talks to it.
Configuration comes from the environment, nothing is stored or logged:

  UMAMI_API_URL      base URL of the Umami instance, e.g. http://umami-umami-1:3000
  UMAMI_WEBSITE_ID   the website id (the public tracker uses the same id)
  UMAMI_USERNAME / UMAMI_PASSWORD   login for POST /api/auth/login (bearer token,
                     cached in memory until it expires or a 401 forces a new login)

Missing configuration or an unreachable Umami answers 503 with a plain message
(never a 500): the panel shows "Analytics backend not configured".  Every upstream
call has a 3 s timeout.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from motor_ai_sim.auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])
log = logging.getLogger(__name__)

#: Seconds allowed for each call to Umami.
TIMEOUT_S = 3.0

#: range -> (seconds covered, Umami bucket unit)
RANGES: dict[str, tuple[int, str]] = {
    "24h": (24 * 3600, "hour"),
    "7d": (7 * 86400, "day"),
    "30d": (30 * 86400, "day"),
}

#: Metric lists the panel shows: payload key -> Umami ``type``.
_METRICS = (("pages", "path"), ("countries", "country"), ("referrers", "referrer"),
            ("browsers", "browser"), ("os", "os"), ("devices", "device"))
_TOP_N = 10

NOT_CONFIGURED = ("Analytics backend not configured: set UMAMI_API_URL, UMAMI_WEBSITE_ID, "
                  "UMAMI_USERNAME and UMAMI_PASSWORD on the server.")
UNREACHABLE = "Analytics backend is unreachable or returned an error."


def _make_client() -> httpx.Client:
    """One HTTP client per request, 3 s timeout (tests swap this for a mock transport)."""
    return httpx.Client(timeout=TIMEOUT_S)


class AnalyticsUnavailable(Exception):
    """Umami is not configured, not reachable or refused the login."""


_lock = threading.Lock()
_token: dict[str, Any] = {"value": None, "until": 0.0}


def _config() -> tuple[str, str, str, str]:
    base = os.environ.get("UMAMI_API_URL", "").strip().rstrip("/")
    site = os.environ.get("UMAMI_WEBSITE_ID", "").strip()
    user = os.environ.get("UMAMI_USERNAME", "").strip()
    pw = os.environ.get("UMAMI_PASSWORD", "")
    if not (base and site and user and pw):
        raise AnalyticsUnavailable(NOT_CONFIGURED)
    return base, site, user, pw


def reset_token_cache() -> None:
    with _lock:
        _token["value"] = None
        _token["until"] = 0.0


def _login(client: httpx.Client, base: str, user: str, pw: str) -> str:
    try:
        r = client.post(f"{base}/api/auth/login", json={"username": user, "password": pw})
        r.raise_for_status()
        tok = r.json().get("token")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("umami login failed: %s", type(e).__name__)   # never the credentials
        raise AnalyticsUnavailable(UNREACHABLE) from e
    if not tok:
        raise AnalyticsUnavailable(UNREACHABLE)
    with _lock:
        _token["value"] = tok
        _token["until"] = time.time() + 3000      # Umami tokens last far longer; renew early
    return tok


def _bearer(client: httpx.Client, base: str, user: str, pw: str, *, fresh: bool = False) -> str:
    with _lock:
        tok, until = _token["value"], _token["until"]
    if fresh or not tok or time.time() >= until:
        return _login(client, base, user, pw)
    return tok


def _get(client: httpx.Client, base: str, user: str, pw: str, path: str, params: dict) -> Any:
    """GET with the cached bearer; one re-login on 401."""
    for attempt in (0, 1):
        tok = _bearer(client, base, user, pw, fresh=attempt == 1)
        try:
            r = client.get(f"{base}{path}", params=params,
                           headers={"Authorization": f"Bearer {tok}"})
        except httpx.HTTPError as e:
            raise AnalyticsUnavailable(UNREACHABLE) from e
        if r.status_code == 401 and attempt == 0:
            continue
        if r.status_code >= 400:
            raise _UpstreamStatus(r.status_code)
        try:
            return r.json()
        except ValueError as e:
            raise AnalyticsUnavailable(UNREACHABLE) from e
    raise AnalyticsUnavailable(UNREACHABLE)


class _UpstreamStatus(Exception):
    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


def _num(v: Any) -> float:
    """Umami 2.x returns ``{"value": n, "prev": m}``, 1.x a bare number."""
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def shape_totals(stats: dict) -> dict:
    visits = _num(stats.get("visits"))
    bounces = _num(stats.get("bounces"))
    total_time = _num(stats.get("totaltime"))
    return {
        "visitors": int(_num(stats.get("visitors"))),
        "visits": int(visits),
        "pageviews": int(_num(stats.get("pageviews"))),
        "bounce_rate": round(100.0 * bounces / visits, 1) if visits > 0 else 0.0,
        "avg_visit_s": round(total_time / visits) if visits > 0 else 0,
    }


def shape_series(pv: dict) -> list[dict]:
    """Merge Umami's ``pageviews`` and ``sessions`` buckets by timestamp."""
    by_t: dict[str, dict] = {}
    for key, out in (("pageviews", "pageviews"), ("sessions", "visitors")):
        for p in (pv.get(key) or []):
            t = str(p.get("x") if p.get("x") is not None else p.get("t"))
            row = by_t.setdefault(t, {"t": t, "pageviews": 0, "visitors": 0})
            row[out] = int(_num(p.get("y")))
    return [by_t[k] for k in sorted(by_t)]


def shape_metric(rows: Any) -> list[dict]:
    out = []
    for r in (rows or [])[:_TOP_N]:
        if isinstance(r, dict):
            out.append({"name": str(r.get("x") if r.get("x") not in (None, "") else "(direct / unknown)"),
                        "count": int(_num(r.get("y")))})
    return out


def fetch_analytics(range_key: str) -> dict:
    seconds, unit = RANGES[range_key]
    base, site, user, pw = _config()
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - seconds * 1000
    window = {"startAt": start_ms, "endAt": end_ms}
    try:
        with _make_client() as c:
            def get(path: str, extra: dict | None = None):
                return _get(c, base, user, pw, f"/api/websites/{site}{path}",
                            {**window, **(extra or {})})
            stats = get("/stats")
            series = get("/pageviews", {"unit": unit, "timezone": "UTC"})
            lists: dict[str, list[dict]] = {}
            for key, typ in _METRICS:
                try:
                    rows = get("/metrics", {"type": typ, "limit": _TOP_N})
                except _UpstreamStatus as e:
                    if typ == "path" and e.status == 400:      # Umami < 2.18 calls it "url"
                        rows = get("/metrics", {"type": "url", "limit": _TOP_N})
                    else:
                        raise
                lists[key] = shape_metric(rows)
    except _UpstreamStatus as e:
        log.warning("umami answered HTTP %s", e.status)
        raise AnalyticsUnavailable(UNREACHABLE) from e
    return {"range": range_key, "unit": unit, "totals": shape_totals(stats),
            "series": shape_series(series), **lists}


@router.get("/analytics")
def admin_analytics(range: str = Query("7d"), _admin: dict = Depends(require_admin)):
    if range not in RANGES:
        raise HTTPException(status_code=400, detail="range must be one of 24h, 7d, 30d.")
    try:
        return fetch_analytics(range)
    except AnalyticsUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:                                          # noqa: BLE001 - never crash the panel
        log.exception("admin analytics failed")
        raise HTTPException(status_code=503, detail=UNREACHABLE)
