"""How long each signed-in user works with the system (owner 2026-10-15).

The web app sends a heartbeat (``POST /api/activity/heartbeat``, routes/activity.py) while
the tab is visible and the user interacted in the last five minutes.  This module turns
heartbeats into per-user, per-day totals.  Server time is the only clock that counts.

What is counted
---------------
* A beat is credited ``min(60 s, gap)`` where the gap is the time since the user's previous
  beat (any session, so two open tabs never double-count: the credited intervals form a
  union).  The first beat of a session, and a beat that arrives more than ``NEW_SEGMENT_S``
  after the session's last one (the user went idle or closed the laptop), credit 0 and only
  start a new stretch.  Credited time can therefore never exceed real elapsed time, however
  often a client beats.
* Per (user, day, UTC): active seconds, sessions (distinct page loads), first/last seen,
  seconds per tab.  The tab is the app's tab id (``motors``, ``compare``...), never a die,
  configuration, e-mail or any content.
* Rate limits: at most ``USER_BEATS_PER_MIN`` beats per user and ``SESSION_BEATS_PER_MIN``
  per session are accepted per minute; the rest are dropped.

Storage and retention
---------------------
One SQLite file ``activity.sqlite`` next to the cluster history (``cluster_monitor.data_dir``).
It keeps daily aggregates only, never one row per beat.  Session bookkeeping rows are
dropped after ``SESSION_KEEP_DAYS``.  Per-user daily rows older than ``RETENTION_DAYS``
(13 months) are folded into per-day totals without any user and then deleted.

"Runs started" is not stored here: it is read from the existing job usage records
(``job_usage``), so nothing is duplicated.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional

BEAT_CREDIT_S = 60.0
#: a gap longer than this since the session's last beat starts a new stretch (credit 0)
NEW_SEGMENT_S = 90.0
USER_BEATS_PER_MIN = 12
SESSION_BEATS_PER_MIN = 4
SESSION_KEEP_DAYS = 3
RETENTION_DAYS = 396          # 13 months

_SID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_TAB_RE = re.compile(r"^[a-z0-9_]{1,24}$")

_LOCK = threading.Lock()
_RECENT_USER: Dict[str, Deque[float]] = defaultdict(deque)
_RECENT_SESSION: Dict[tuple, Deque[float]] = defaultdict(deque)
_LAST_PRUNE_DAY: Optional[str] = None


def _path():
    from motor_ai_sim import cluster_monitor as CM
    return CM.data_dir() / "activity.sqlite"


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(_path()), timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS activity_day (user TEXT, day TEXT, active_s REAL DEFAULT 0, "
                "sessions INTEGER DEFAULT 0, first_ts REAL, last_ts REAL, tabs TEXT DEFAULT '{}', "
                "PRIMARY KEY(user, day))")
    con.execute("CREATE TABLE IF NOT EXISTS activity_session (user TEXT, sid TEXT, day TEXT, "
                "last_ts REAL, PRIMARY KEY(user, sid))")
    con.execute("CREATE TABLE IF NOT EXISTS activity_user (user TEXT PRIMARY KEY, last_ts REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS activity_total (day TEXT PRIMARY KEY, users INTEGER, "
                "active_s REAL, sessions INTEGER)")
    return con


def reset_memory() -> None:
    """Tests: forget the in-memory rate-limit windows and the prune marker."""
    global _LAST_PRUNE_DAY
    with _LOCK:
        _RECENT_USER.clear()
        _RECENT_SESSION.clear()
        _LAST_PRUNE_DAY = None


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def clean_tab(tab: Any) -> str:
    t = str(tab or "").strip().lower()
    return t if _TAB_RE.match(t) else "other"


def clean_sid(sid: Any) -> Optional[str]:
    s = str(sid or "")
    return s if _SID_RE.match(s) else None


def _rate_ok(user: str, sid: str, now: float) -> bool:
    with _LOCK:
        for dq, key, cap in ((_RECENT_USER[user], user, USER_BEATS_PER_MIN),
                             (_RECENT_SESSION[(user, sid)], (user, sid), SESSION_BEATS_PER_MIN)):
            while dq and now - dq[0] > 60.0:
                dq.popleft()
            if len(dq) >= cap:
                return False
        _RECENT_USER[user].append(now)
        _RECENT_SESSION[(user, sid)].append(now)
        if len(_RECENT_SESSION) > 5000:        # bound the memory: forget idle sessions
            for k in [k for k, v in _RECENT_SESSION.items() if not v or now - v[-1] > 60.0]:
                _RECENT_SESSION.pop(k, None)
        return True


def record_beat(user: str, sid: Any, tab: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """Account one heartbeat.  Returns ``{"counted_s": float}`` or ``{"counted_s": 0, "dropped": why}``."""
    now = float(time.time() if now is None else now)
    user = str(user or "").strip().lower()
    s = clean_sid(sid)
    if not user or s is None:
        return {"counted_s": 0.0, "dropped": "invalid"}
    tab = clean_tab(tab)
    if not _rate_ok(user, s, now):
        return {"counted_s": 0.0, "dropped": "rate"}
    day = day_of(now)
    con = _db()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT last_ts FROM activity_session WHERE user=? AND sid=?", (user, s)).fetchone()
        urow = con.execute("SELECT last_ts FROM activity_user WHERE user=?", (user,)).fetchone()
        credit = 0.0
        if row is not None and 0.0 <= now - float(row[0]) <= NEW_SEGMENT_S:
            credit = min(BEAT_CREDIT_S, now - float(row[0]))
            if urow is not None:                   # union with the user's other sessions
                credit = min(credit, max(0.0, now - float(urow[0])))
        new_session = row is None
        con.execute("INSERT INTO activity_session(user, sid, day, last_ts) VALUES (?,?,?,?) "
                    "ON CONFLICT(user, sid) DO UPDATE SET last_ts=excluded.last_ts",
                    (user, s, day, now))
        con.execute("INSERT INTO activity_user(user, last_ts) VALUES (?,?) "
                    "ON CONFLICT(user) DO UPDATE SET last_ts=MAX(last_ts, excluded.last_ts)", (user, now))
        d = con.execute("SELECT active_s, sessions, first_ts, last_ts, tabs FROM activity_day "
                        "WHERE user=? AND day=?", (user, day)).fetchone()
        if d is None:
            tabs = {tab: credit} if credit > 0 else {}
            con.execute("INSERT INTO activity_day(user, day, active_s, sessions, first_ts, last_ts, tabs) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (user, day, credit, 1 if new_session else 0, now, now, json.dumps(tabs)))
        else:
            try:
                tabs = json.loads(d[4] or "{}")
            except ValueError:
                tabs = {}
            if credit > 0:
                tabs[tab] = round(float(tabs.get(tab, 0.0)) + credit, 3)
            con.execute("UPDATE activity_day SET active_s=?, sessions=?, first_ts=?, last_ts=?, tabs=? "
                        "WHERE user=? AND day=?",
                        (float(d[0]) + credit, int(d[1]) + (1 if new_session else 0),
                         min(float(d[2] or now), now), max(float(d[3] or now), now),
                         json.dumps(tabs), user, day))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    _maybe_prune(now)
    return {"counted_s": round(credit, 3)}


def _maybe_prune(now: float) -> None:
    global _LAST_PRUNE_DAY
    day = day_of(now)
    if _LAST_PRUNE_DAY == day:
        return
    _LAST_PRUNE_DAY = day
    try:
        prune(now)
    except Exception:                                   # noqa: BLE001 - housekeeping never fails a beat
        pass


def prune(now: Optional[float] = None) -> Dict[str, int]:
    """Drop old session bookkeeping; fold per-user days past the retention into per-day totals."""
    now = float(time.time() if now is None else now)
    keep_sess = day_of(now - SESSION_KEEP_DAYS * 86400)
    cutoff = day_of(now - RETENTION_DAYS * 86400)
    con = _db()
    try:
        s = con.execute("DELETE FROM activity_session WHERE day < ?", (keep_sess,)).rowcount
        old = con.execute("SELECT day, COUNT(DISTINCT user), SUM(active_s), SUM(sessions) FROM activity_day "
                          "WHERE day < ? GROUP BY day", (cutoff,)).fetchall()
        for day, users, active, sessions in old:
            con.execute("INSERT INTO activity_total(day, users, active_s, sessions) VALUES (?,?,?,?) "
                        "ON CONFLICT(day) DO UPDATE SET users=users+excluded.users, "
                        "active_s=active_s+excluded.active_s, sessions=sessions+excluded.sessions",
                        (day, users, active or 0.0, sessions or 0))
        d = con.execute("DELETE FROM activity_day WHERE day < ?", (cutoff,)).rowcount
        con.commit()
        return {"sessions_dropped": s, "user_days_folded": d}
    finally:
        con.close()


# ── reading (Admin) ──────────────────────────────────────────────────────────

def _tabs(raw: Any) -> Dict[str, float]:
    try:
        d = json.loads(raw or "{}")
        return {str(k): float(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _runs_by_user(start: float, end: float) -> Dict[str, int]:
    """Runs/jobs per account from the existing job usage records (nothing is duplicated here)."""
    try:
        from motor_ai_sim import job_usage as JU
        out: Dict[str, int] = defaultdict(int)
        for r in JU.jobs(start, end, limit=10 ** 7):
            u = str(r.get("user") or "").strip().lower()
            if u:
                out[u] += 1
        return out
    except Exception:                                   # noqa: BLE001 - no job store yet = zero runs
        return {}


def summaries(now: Optional[float] = None, top_tabs: int = 3) -> List[Dict[str, Any]]:
    """One row per account seen in the last 30 UTC days: active seconds today / 7 d / 30 d,
    sessions (7 d, 30 d), last seen, top tabs by time (30 d), runs (7 d, 30 d)."""
    now = float(time.time() if now is None else now)
    today = day_of(now)
    d7 = day_of(now - 6 * 86400)
    d30 = day_of(now - 29 * 86400)
    con = _db()
    try:
        rows = con.execute("SELECT user, day, active_s, sessions, last_ts, tabs FROM activity_day "
                           "WHERE day >= ?", (d30,)).fetchall()
    finally:
        con.close()
    runs7 = _runs_by_user(now - 7 * 86400, now + 1)
    runs30 = _runs_by_user(now - 30 * 86400, now + 1)
    agg: Dict[str, Dict[str, Any]] = {}
    for user, day, active, sessions, last_ts, tabs in rows:
        a = agg.setdefault(user, {"user": user, "today_s": 0.0, "d7_s": 0.0, "d30_s": 0.0,
                                  "sessions_7d": 0, "sessions_30d": 0, "last_seen": 0.0, "_tabs": {}})
        a["d30_s"] += active
        a["sessions_30d"] += sessions
        if day >= d7:
            a["d7_s"] += active
            a["sessions_7d"] += sessions
        if day == today:
            a["today_s"] += active
        a["last_seen"] = max(a["last_seen"], float(last_ts or 0.0))
        for t, v in _tabs(tabs).items():
            a["_tabs"][t] = a["_tabs"].get(t, 0.0) + v
    for u in set(runs30) - set(agg):
        agg[u] = {"user": u, "today_s": 0.0, "d7_s": 0.0, "d30_s": 0.0, "sessions_7d": 0,
                  "sessions_30d": 0, "last_seen": 0.0, "_tabs": {}}
    out = []
    for u, a in agg.items():
        tabs = sorted(a.pop("_tabs").items(), key=lambda kv: -kv[1])[:top_tabs]
        a["top_tabs"] = [{"tab": t, "seconds": round(v)} for t, v in tabs if v > 0]
        a["runs_7d"] = runs7.get(u, 0)
        a["runs_30d"] = runs30.get(u, 0)
        for k in ("today_s", "d7_s", "d30_s"):
            a[k] = round(a[k])
        out.append(a)
    out.sort(key=lambda r: -r["d30_s"])
    return out


def user_days(user: str, days: int = 30, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Daily series for one account, oldest first, zero-filled."""
    now = float(time.time() if now is None else now)
    days = max(1, min(int(days), RETENTION_DAYS))
    user = str(user or "").strip().lower()
    first = day_of(now - (days - 1) * 86400)
    con = _db()
    try:
        rows = con.execute("SELECT day, active_s, sessions, first_ts, last_ts, tabs FROM activity_day "
                           "WHERE user=? AND day >= ?", (user, first)).fetchall()
    finally:
        con.close()
    by_day = {r[0]: r for r in rows}
    start = datetime.fromtimestamp(now, timezone.utc).date() - timedelta(days=days - 1)
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        r = by_day.get(d)
        out.append({"day": d, "active_s": round(r[1]) if r else 0, "sessions": int(r[2]) if r else 0,
                    "first_ts": r[3] if r else None, "last_ts": r[4] if r else None,
                    "tabs": {k: round(v) for k, v in _tabs(r[5]).items()} if r else {}})
    return out
