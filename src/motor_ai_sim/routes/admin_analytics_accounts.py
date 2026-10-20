"""Admin account and NDA counts for the Analytics panel.

This endpoint reads local stores only and remains available when Umami is down.
It returns counts and UTC buckets without account identifiers.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from motor_ai_sim.auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])
log = logging.getLogger(__name__)
_RANGE_SECONDS = {"24h": 24 * 3600, "7d": 7 * 86400, "30d": 30 * 86400}


def _timestamp(value: Any) -> float | None:
    """Parse a stored epoch, ISO timestamp, or YYYY-MM-DD date as UTC."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).timestamp()


def _bucket_start(timestamp: float, unit: str) -> dt.datetime:
    value = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc)
    if unit == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _iso_bucket(value: dt.datetime, unit: str) -> str:
    return value.strftime("%Y-%m-%d %H:00:00" if unit == "hour" else "%Y-%m-%d 00:00:00")


def build_accounts_analytics(range_key: str, *, now: float | None = None) -> dict:
    """Build counts from account and NDA stores; source counts may overlap."""
    seconds = _RANGE_SECONDS[range_key]
    end = float(time.time() if now is None else now)
    start = end - seconds
    unit = "hour" if range_key == "24h" else "day"
    step = 3600 if unit == "hour" else 86400
    first_bucket = _bucket_start(start, unit)
    last_bucket = _bucket_start(end, unit)
    bucket_count = int((last_bucket.timestamp() - first_bucket.timestamp()) // step) + 1
    points: dict[float, dict[str, Any]] = {}
    for index in range(bucket_count):
        timestamp = first_bucket.timestamp() + step * index
        points[timestamp] = {"t": _iso_bucket(first_bucket + dt.timedelta(seconds=step * index), unit),
                             "accounts": 0, "nda": 0}

    from motor_ai_sim import nda as NDA
    from motor_ai_sim import users as USERS

    accounts = USERS._load()
    normalized_accounts = {str(email).strip().lower(): rec for email, rec in accounts.items()
                           if str(email).strip() and isinstance(rec, dict)}
    created_by_account: dict[str, float] = {}
    for email, rec in normalized_accounts.items():
        created = _timestamp(rec.get("created"))
        if created is not None:
            created_by_account[email] = created

    online_times: dict[str, float] = {}
    online_accounts: set[str] = set()
    online_record_accounts: set[str] = set()
    # `read_records(0)` uses rows[-0:] and therefore returns the full append-only log.
    records = NDA.read_records(0)
    for record in records:
        if not isinstance(record, dict):
            continue
        identity = str(record.get("user") or record.get("email") or "").strip().lower()
        if identity not in normalized_accounts:
            continue
        online_accounts.add(identity)
        online_record_accounts.add(identity)
        signed_at = _timestamp(record.get("ts")) or _timestamp(record.get("iso"))
        if signed_at is not None:
            online_times[identity] = min(signed_at, online_times.get(identity, signed_at))

    online_flag_times: dict[str, float] = {}
    admin_flag_times: dict[str, float] = {}
    admin_flag_accounts: set[str] = set()
    for email, rec in normalized_accounts.items():
        flag = rec.get("nda")
        if isinstance(flag, dict) and flag.get("signed") is True:
            signed_at = _timestamp(flag.get("date"))
            if str(flag.get("by") or "").strip().lower() == "online":
                online_accounts.add(email)
                if email not in online_record_accounts and signed_at is not None:
                    online_flag_times[email] = signed_at
            else:
                admin_flag_accounts.add(email)
                if signed_at is not None:
                    admin_flag_times[email] = signed_at

    # Prefer the first precise online record; fall back to an online account flag,
    # then to an admin-set flag when no online evidence exists.
    nda_accounts = online_accounts | admin_flag_accounts
    nda_times = {
        email: (online_times.get(email, online_flag_times.get(email)) if email in online_accounts
                else admin_flag_times.get(email))
        for email in nda_accounts
    }
    accounts_new = sum(start <= value <= end for value in created_by_account.values())
    nda_new = sum(value is not None and start <= value <= end for value in nda_times.values())
    for timestamp in created_by_account.values():
        if start <= timestamp <= end:
            bucket = _bucket_start(timestamp, unit).timestamp()
            if bucket in points:
                points[bucket]["accounts"] += 1
    for timestamp in nda_times.values():
        if timestamp is not None and start <= timestamp <= end:
            bucket = _bucket_start(timestamp, unit).timestamp()
            if bucket in points:
                points[bucket]["nda"] += 1

    return {
        "range": range_key,
        "accounts": {"total": len(normalized_accounts), "new": accounts_new},
        "nda": {"total": len(nda_accounts), "new": nda_new,
                "online_total": len(online_accounts), "admin_flag_total": len(admin_flag_accounts)},
        "series": [points[timestamp] for timestamp in sorted(points)],
    }


@router.get("/analytics/accounts")
def admin_analytics_accounts(range: str = Query("7d"), _admin: dict = Depends(require_admin)):
    if range not in _RANGE_SECONDS:
        raise HTTPException(status_code=400, detail="range must be one of 24h, 7d, 30d.")
    try:
        return build_accounts_analytics(range)
    except Exception:  # noqa: BLE001 - counts endpoint should return a clear server error, never PII
        log.exception("admin account analytics failed")
        raise HTTPException(status_code=500, detail="Could not load account analytics.")
