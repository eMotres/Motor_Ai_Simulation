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

import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

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


# ── live analytics (real-time active users + event feed) ─────────────────────

_LIVE_CACHE: dict[str, Any] = {"data": None, "ts": 0.0}
_LIVE_CACHE_LOCK = threading.Lock()
LIVE_CACHE_TTL_S = 10.0


def reset_live_cache() -> None:
    with _LIVE_CACHE_LOCK:
        _LIVE_CACHE["data"] = None
        _LIVE_CACHE["ts"] = 0.0


def resolve_user_country(email: str, user_rec: Optional[dict] = None) -> str:
    """Best-effort country for a signed-in account: user profile -> NDA -> verification."""
    if user_rec and user_rec.get("country"):
        return str(user_rec["country"]).strip().upper()
    try:
        from motor_ai_sim import nda as N
        for rec in N.read_records(200):
            if str(rec.get("email") or rec.get("user") or "").strip().lower() == email:
                if rec.get("country"):
                    return str(rec["country"]).strip().upper()
    except Exception:
        pass
    try:
        from motor_ai_sim.verification import store as VS
        case = VS.case_for_user(email)
        if case and (case.get("application") or {}).get("country"):
            return str(case["application"]["country"]).strip().upper()
    except Exception:
        pass
    return ""


def fetch_active_signed_in_users(window_s: float = 300.0, now: Optional[float] = None) -> list[dict]:
    """Accounts with a heartbeat in the last `window_s` seconds: user, name, tab, last_beat, country."""
    now = float(time.time() if now is None else now)
    cutoff = now - window_s
    try:
        from motor_ai_sim import activity as AC
        p = AC._path()
        if not p.is_file():
            return []
        con = AC._db()
    except Exception:
        return []

    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(activity_session)").fetchall()]
        has_tab = "tab" in cols
        has_country = "country" in cols

        q = "SELECT user, sid, last_ts"
        if has_tab:
            q += ", tab"
        if has_country:
            q += ", country"
        q += " FROM activity_session WHERE last_ts >= ? ORDER BY last_ts DESC"

        rows = con.execute(q, (cutoff,)).fetchall()

        from motor_ai_sim import users as U
        try:
            all_users = U._load_soft() if hasattr(U, "_load_soft") else {}
        except Exception:
            all_users = {}

        seen_users = set()
        active_list = []

        for row in rows:
            u_email = str(row[0] or "").strip().lower()
            if not u_email or u_email in seen_users:
                continue
            seen_users.add(u_email)

            last_ts = float(row[2] or 0.0)
            tab_val = ""
            if has_tab and len(row) > 3 and row[3]:
                tab_val = str(row[3])

            country_val = ""
            if has_country and len(row) > (4 if has_tab else 3):
                idx = 4 if has_tab else 3
                if row[idx]:
                    country_val = str(row[idx])

            if not tab_val:
                try:
                    day_str = AC.day_of(now)
                    d_row = con.execute("SELECT tabs FROM activity_day WHERE user=? AND day=?",
                                        (u_email, day_str)).fetchone()
                    if d_row and d_row[0]:
                        tabs_dict = json.loads(d_row[0])
                        if isinstance(tabs_dict, dict) and tabs_dict:
                            tab_val = max(tabs_dict.items(), key=lambda kv: kv[1])[0]
                except Exception:
                    pass
            if not tab_val:
                tab_val = "motors"

            if not country_val:
                country_val = resolve_user_country(u_email, all_users.get(u_email))

            u_rec = all_users.get(u_email) or {}
            display_name = str(u_rec.get("name") or "").strip()

            active_list.append({
                "user": u_email,
                "name": display_name,
                "tab": tab_val,
                "last_beat": round(last_ts, 1),
                "last_beat_iso": datetime.fromtimestamp(last_ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "country": str(country_val).upper() if country_val else "",
            })

        return active_list
    finally:
        con.close()


def fetch_umami_live(c: httpx.Client, base: str, site: str, user: str, pw: str) -> dict:
    """Anonymous visitors: active count + last 30 min top pages and countries."""
    end_ms = int(time.time() * 1000)
    start_30m = end_ms - 30 * 60 * 1000
    window = {"startAt": start_30m, "endAt": end_ms}

    # 1. Active count
    try:
        act = _get(c, base, user, pw, f"/api/websites/{site}/active", {})
        active_count = int(_num(act.get("x") if isinstance(act, dict) and act.get("x") is not None
                                else (act.get("visitors") if isinstance(act, dict) else act)))
    except Exception:
        active_count = 0

    # 2. Pages in last 30 min
    try:
        rows_pages = _get(c, base, user, pw, f"/api/websites/{site}/metrics", {**window, "type": "path", "limit": 10})
    except _UpstreamStatus as e:
        if e.status == 400:
            rows_pages = _get(c, base, user, pw, f"/api/websites/{site}/metrics", {**window, "type": "url", "limit": 10})
        else:
            rows_pages = []
    except Exception:
        rows_pages = []
    pages = shape_metric(rows_pages)

    # 3. Countries in last 30 min
    try:
        rows_countries = _get(c, base, user, pw, f"/api/websites/{site}/metrics", {**window, "type": "country", "limit": 10})
        countries = shape_metric(rows_countries)
    except Exception:
        countries = []

    # 4. Optional recent pageview events from Umami (if supported)
    page_view_events: list[dict] = []
    try:
        ev_res = _get(c, base, user, pw, f"/api/websites/{site}/events", {**window, "limit": 20})
        ev_list = ev_res.get("data", ev_res) if isinstance(ev_res, dict) else (ev_res if isinstance(ev_res, list) else [])
        for ev in ev_list:
            if isinstance(ev, dict):
                t_str = ev.get("createdAt") or ev.get("created_at") or ev.get("timestamp")
                t_val = time.time()
                if isinstance(t_str, (int, float)):
                    t_val = float(t_str) / 1000.0 if t_str > 1e11 else float(t_str)
                elif isinstance(t_str, str):
                    try:
                        dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
                        t_val = dt.timestamp()
                    except Exception:
                        pass
                url = ev.get("urlPath") or ev.get("url") or ev.get("path") or ""
                cc = str(ev.get("country") or "").upper()
                page_view_events.append({
                    "id": f"pv-{ev.get('id', secrets.token_hex(4))}",
                    "type": "page_view",
                    "user": "",
                    "ts": t_val,
                    "iso": datetime.fromtimestamp(t_val, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "details": url or ev.get("eventName", "pageview"),
                    "country": cc,
                })
    except Exception:
        pass

    return {
        "configured": True,
        "active": active_count,
        "pages": pages,
        "countries": countries,
        "page_view_events": page_view_events,
    }


def fetch_live_events(limit: int = 50, page_view_events: Optional[list[dict]] = None) -> list[dict]:
    """Tail read of existing stores: auth events, NDA signatures, runs started, admin audit."""
    events: list[dict] = []

    # 1. Page views from Umami
    if page_view_events:
        events.extend(page_view_events[:limit])

    # 2. Auth events (sign-ups, logins, e-mail confirmations)
    try:
        from motor_ai_sim import sessions as S
        for ev in S.read_events(limit=limit):
            ev_type = str(ev.get("event") or "").strip()
            t_val = float(ev.get("t") or 0.0)
            iso = str(ev.get("ts") or "")
            if not t_val and iso:
                try:
                    t_val = datetime.fromisoformat(iso).timestamp()
                except Exception:
                    t_val = 0.0
            if not iso and t_val:
                iso = datetime.fromtimestamp(t_val, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            email = str(ev.get("email") or "")
            reason = str(ev.get("reason") or "")

            feed_type = None
            if ev_type == "register":
                feed_type = "signup"
            elif ev_type == "login":
                feed_type = "login"
            elif ev_type == "verified":
                feed_type = "email_confirmed"
            elif ev_type in ("logout", "password_reset", "delete_requested"):
                feed_type = ev_type

            if feed_type:
                events.append({
                    "id": f"auth-{ev_type}-{t_val}-{email}",
                    "type": feed_type,
                    "user": email,
                    "ts": t_val,
                    "iso": iso,
                    "details": reason,
                    "country": "",
                })
    except Exception:
        pass

    # 3. NDA requests / signatures
    try:
        from motor_ai_sim import nda as N
        for rec in N.read_records(limit=limit):
            t_val = float(rec.get("ts") or 0.0)
            iso = str(rec.get("iso") or "")
            if not iso and t_val:
                iso = datetime.fromtimestamp(t_val, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            email = str(rec.get("email") or rec.get("user") or "")
            co = str(rec.get("company") or "")
            cc = str(rec.get("country") or "").upper()
            details = f"{co} ({cc})" if co and cc else (co or cc or "online")
            events.append({
                "id": f"nda-{rec.get('id', t_val)}",
                "type": "nda_request",
                "user": email,
                "ts": t_val,
                "iso": iso,
                "details": details,
                "country": cc,
            })
    except Exception:
        pass

    # 4. Runs started (jobs)
    try:
        from motor_ai_sim import job_usage as JU
        now = time.time()
        recent_jobs = JU.jobs(now - 86400, now + 1, limit=limit)
        for j in recent_jobs:
            t_start = float(j.get("ts_start") or 0.0)
            if not t_start:
                continue
            iso = datetime.fromtimestamp(t_start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            u = str(j.get("user") or "")
            kind = str(j.get("kind") or "")
            mach = str(j.get("machine") or "")
            det = f"{kind} · {mach}" if kind and mach else (kind or mach or "job")
            events.append({
                "id": f"job-{j.get('run_id', t_start)}",
                "type": "run_started",
                "user": u,
                "ts": t_start,
                "iso": iso,
                "details": det,
                "country": "",
            })
        with JU._LOCK:
            for rid, meter in list(JU._ACTIVE.items()):
                t0 = float(meter.t0)
                iso = datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                det = f"{meter.kind} · {meter.machine} (running)"
                events.append({
                    "id": f"job-active-{rid}",
                    "type": "run_started",
                    "user": meter.user,
                    "ts": t0,
                    "iso": iso,
                    "details": det,
                    "country": "",
                })
    except Exception:
        pass

    # 5. Admin audit
    try:
        from motor_ai_sim import admin_audit as AA
        for r in AA.read(limit=limit):
            t_val = float(r.get("ts") or 0.0)
            iso = str(r.get("iso") or "")
            if not iso and t_val:
                iso = datetime.fromtimestamp(t_val, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            act = str(r.get("action") or "")
            target = str(r.get("target") or r.get("subject") or "")
            actor = str(r.get("actor") or "")
            det = f"{act}: {target}" if target else act
            events.append({
                "id": f"admin-{t_val}-{act}",
                "type": "admin_action",
                "user": actor,
                "ts": t_val,
                "iso": iso,
                "details": det,
                "country": "",
            })
    except Exception:
        pass

    # Sort descending by timestamp (newest first)
    events.sort(key=lambda e: float(e.get("ts") or 0.0), reverse=True)
    return events[:limit]


def _scrub_ips(obj: Any) -> Any:
    """Ensure NO IP address or IP hash exists anywhere in the payload (privacy rule)."""
    if isinstance(obj, dict):
        return {k: _scrub_ips(v) for k, v in obj.items()
                if k not in ("ip", "ip_hash", "client_ip", "remote_ip")}
    if isinstance(obj, list):
        return [_scrub_ips(item) for item in obj]
    return obj


def fetch_live_analytics(now: Optional[float] = None) -> dict:
    curr = float(time.time() if now is None else now)
    with _LIVE_CACHE_LOCK:
        if _LIVE_CACHE["data"] is not None and (curr - _LIVE_CACHE["ts"] < LIVE_CACHE_TTL_S):
            return dict(_LIVE_CACHE["data"])

    # 1. Anonymous visitors from Umami
    try:
        base, site, user, pw = _config()
        with _make_client() as c:
            anon = fetch_umami_live(c, base, site, user, pw)
    except AnalyticsUnavailable:
        anon = {"configured": False, "active": 0, "pages": [], "countries": [], "page_view_events": []}
    except Exception as e:
        log.warning("umami live analytics failed: %s", e)
        anon = {"configured": False, "active": 0, "pages": [], "countries": [], "page_view_events": []}

    # 2. Signed-in active users (last 5 min)
    active_users = fetch_active_signed_in_users(window_s=300.0, now=curr)

    # 3. Live event feed
    page_events = anon.pop("page_view_events", [])
    events = fetch_live_events(limit=50, page_view_events=page_events)

    payload = {
        "active_users": active_users,
        "anonymous": anon,
        "events": events,
        "cached_at": curr,
    }

    # Strict privacy scrub: absolutely no IP or hash anywhere
    payload = _scrub_ips(payload)

    with _LIVE_CACHE_LOCK:
        _LIVE_CACHE["data"] = payload
        _LIVE_CACHE["ts"] = curr

    return payload


@router.get("/analytics/live")
def admin_analytics_live(_admin: dict = Depends(require_admin)):
    """Real-time active visitors + signed-in users + live event feed. Admin only. Cached up to 10s."""
    try:
        return fetch_live_analytics()
    except Exception:
        log.exception("admin live analytics failed")
        raise HTTPException(status_code=500, detail="Could not load live analytics.")


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

