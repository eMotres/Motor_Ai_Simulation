"""Server-side session registry + append-only auth event log.

WHY this exists.  Until 2026-09-03 a sign-in produced nothing but a 30-day
HS256 bearer: stateless, unlistable, unrevocable, and — worst of all —
undiagnosable.  The user was being signed out roughly daily (1-4 Google logins
per day in logs/api.log since 2026-08-20) and NOTHING in the backend recorded
whether a token had ever been presented and refused, let alone why.  The whole
verification path swallowed every exception and returned None; the frontend
read that None as "expired" and wiped a possibly perfectly good session.

Two instruments live here:

* ``config/.sessions.json`` — one record per sign-in {sid, email, created,
  last_seen, expires, user_agent, ip, login_method, revoked}.  The sid rides in
  the token's claims, so a session can be listed, aged, and revoked.  Written
  atomically (tmp + replace) under an RLock, exactly like ``users._save``.
* ``logs/auth_events.jsonl`` — one JSON line per login / logout / renew /
  reject / store_unavailable / revoke, with the reason, the ip and the user
  agent.  Append-only, trimmed at 20 MB.  This is the file that will finally
  answer "why was I signed out at 07:40 this morning".

Both are FAIL-OPEN for writes (an audit must never be the reason a login
cannot happen) but FAIL-CLOSED-SAFE for reads: an unreadable session store
resolves to ``store_unavailable``, which the caller must NOT report as a
rejected token — see ``users.resolve_local_token``.

IDLE EXPIRY (owner 2026-10-20).  Each record also carries ``last_activity``: the last time the USER
did something (a non-GET request or the app's once-a-minute input heartbeat - ``session_activity``
says exactly what counts).  ``last_seen`` keeps meaning "any authenticated request", background
polls included, and never decides anything.  A web session is closed - revoked with
``revoked_reason: "idle_timeout"`` and a ``revoke`` auth event - only when BOTH hold for
``MOTRES_SESSION_IDLE_S`` seconds (default 1800; 0 disables): no user activity, and none of the
account's computations queued or running (the clock then starts at the later of the last activity
and the newest job's end; an unreadable job state keeps the session open).  The next request with
that token is refused with reason ``idle_timeout``.  Closing happens on the next request of the
session, and every ``SWEEP_EVERY_S`` seconds a sweep closes the sessions nobody returns to, so
Admin's "open sessions" count is true.  ``last_activity`` is written at most once per
``ACTIVITY_WRITE_INTERVAL_S`` per session.  MCP / OAuth agent tokens, agent API keys and
server-to-server tokens never touch this registry.  The 30-day token TTL stays the outer bound.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from motor_ai_sim.config import DEFAULT_CONFIG_PATH
from motor_ai_sim.file_lock import store_lock

log = logging.getLogger(__name__)

_SESSIONS_FILE = Path(DEFAULT_CONFIG_PATH).parent / ".sessions.json"
_ROOT = Path(__file__).resolve().parents[2]
_EVENTS_FILE = _ROOT / "logs" / "auth_events.jsonl"
# Cross-process (deploy stage 2): every authenticated request of any process may touch last_seen - see file_lock.py.
_LOCK = store_lock(lambda: _SESSIONS_FILE)

#: A session record older than this since `expires` is swept on the next write.
_KEEP_EXPIRED_S = 7 * 24 * 3600
#: `last_seen` is written at most this often per sid (one write per request
#: would turn every GET into a disk round trip).
TOUCH_INTERVAL_S = 60.0
#: `last_activity` is written at most this often per session (the idle limit is 30 minutes, so a
#: minute of lag is invisible and the JSON store is not rewritten per request).
ACTIVITY_WRITE_INTERVAL_S = 60.0
#: How often a process sweeps the registry for idle sessions nobody returns to.
SWEEP_EVERY_S = 300.0
IDLE_REASON = "idle_timeout"
#: The event log is trimmed to its second half when it grows past this.
_EVENTS_MAX_BYTES = 20 * 1024 * 1024

EVENTS = ("login", "logout", "renew", "reject", "store_unavailable", "revoke")   # idle close = "revoke", reason idle_timeout


class StoreUnavailable(RuntimeError):
    """The session store exists but could not be read (lock, AV, corruption).

    Distinct from "no such session": the difference decides whether the client
    keeps its session or is signed out.
    """


# ── store ────────────────────────────────────────────────────────────────────

def _load() -> dict:
    """All sessions keyed by sid.  Raises StoreUnavailable on a read error.

    A MISSING file is not an error — it is an empty registry (first run).
    Anything else (PermissionError from a concurrent replace, a half-written
    file, an antivirus hold) must be told apart from "unknown session", which
    is why this raises instead of returning {}.
    """
    try:
        with open(_SESSIONS_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.error("sessions: %s is unreadable (%s: %s) — treating as "
                  "store_unavailable, sessions are NOT being rejected",
                  _SESSIONS_FILE, type(e).__name__, e)
        raise StoreUnavailable(str(e)) from e
    return d if isinstance(d, dict) else {}


def _save(d: dict) -> None:
    from motor_ai_sim.private_files import chmod_private, ensure_private_dir, open_private
    ensure_private_dir(_SESSIONS_FILE.parent)
    tmp = _SESSIONS_FILE.with_suffix(".tmp")
    with open_private(tmp, "w") as f:       # 0600 — audit 2026-09-29 #9
        json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
    tmp.replace(_SESSIONS_FILE)
    chmod_private(_SESSIONS_FILE)


def _ip(ip: str) -> str:
    """Sessions and auth events keep a KEYED HASH of the client address, never
    the address (audit 2026-09-29 #16): "same client as yesterday?" still
    answers, "where does this person live?" does not."""
    from motor_ai_sim.log_redaction import hash_ip
    s = (ip or "").strip()[:64]
    return "" if s in ("", "?") else hash_ip(s)


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _short_ua(ua: str, limit: int = 300) -> str:
    ua = (ua or "").strip().replace("\n", " ")
    return ua[:limit]


def _sweep(d: dict, now: float) -> dict:
    """Drop records long past their expiry — the registry is a live list, not
    an archive (the archive is logs/auth_events.jsonl)."""
    return {sid: r for sid, r in d.items()
            if float(r.get("expires") or 0) + _KEEP_EXPIRED_S > now
            or not isinstance(r, dict)}


# ── lifecycle ────────────────────────────────────────────────────────────────

def new_sid() -> str:
    """128 bits of randomness — the session identifier that rides in the token."""
    return secrets.token_hex(16)


def create(email: str, *, expires: float, login_method: str = "password",
           ip: str = "", user_agent: str = "", sid: Optional[str] = None) -> str:
    """Record a new session and return its sid.  Never raises: a store that
    cannot be written must not stop a sign-in (the token still works, it just
    cannot be listed or revoked until the store recovers)."""
    sid = sid or new_sid()
    now = time.time()
    rec = {
        "sid": sid,
        "email": _norm(email),
        "created": now,
        "last_seen": now,
        "last_activity": now,
        "expires": float(expires),
        "user_agent": _short_ua(user_agent),
        "ip": _ip(ip),
        "login_method": login_method if login_method in ("password", "google") else "password",
        "revoked": False,
    }
    try:
        with _LOCK:
            # An unreadable registry is NEVER replaced by an empty one: that would erase every revocation and
            # bring revoked tokens back (security audit 2026-10-18).  The sign-in still succeeds; this session
            # just is not recorded until the store is readable again.
            d = _load()
            d = _sweep(d, now)
            d[sid] = rec
            _save(d)
    except Exception as e:                                  # pragma: no cover
        log.error("sessions: could not record session for %s (%s: %s)",
                  _norm(email), type(e).__name__, e)
    return sid


def get(sid: str) -> Optional[dict]:
    """One session record, or None if unknown.  Raises StoreUnavailable when
    the store itself could not be read — the caller MUST tell the two apart."""
    if not sid:
        return None
    return _load().get(sid)


def list_for(email: str) -> list[dict]:
    """This account's sessions, newest first.  Live records only."""
    email = _norm(email)
    try:
        d = _load()
    except StoreUnavailable:
        return []
    out = [r for r in d.values()
           if isinstance(r, dict) and _norm(r.get("email")) == email]
    out.sort(key=lambda r: float(r.get("created") or 0), reverse=True)
    return out


def list_all(email: Optional[str] = None) -> list[dict]:
    """Every session (admin view), optionally filtered by email."""
    try:
        d = _load()
    except StoreUnavailable:
        return []
    out = [r for r in d.values() if isinstance(r, dict)]
    if email:
        e = _norm(email)
        out = [r for r in out if _norm(r.get("email")) == e]
    out.sort(key=lambda r: float(r.get("created") or 0), reverse=True)
    return out


def revoke(sid: str) -> Optional[dict]:
    """Mark one session revoked → the record, or None if it does not exist."""
    with _LOCK:
        try:
            d = _load()
        except StoreUnavailable:
            return None
        rec = d.get(sid)
        if not isinstance(rec, dict):
            return None
        rec["revoked"] = True
        rec["revoked_at"] = time.time()
        d[sid] = rec
        _save(d)
        return rec


def revoke_all(email: str) -> int:
    """Revoke every live session of an account → how many were revoked."""
    email = _norm(email)
    with _LOCK:
        try:
            d = _load()
        except StoreUnavailable:
            return 0
        n = 0
        for sid, rec in d.items():
            if isinstance(rec, dict) and _norm(rec.get("email")) == email \
                    and not rec.get("revoked"):
                rec["revoked"] = True
                rec["revoked_at"] = time.time()
                n += 1
        if n:
            _save(d)
        return n


def extend(sid: str, expires: float) -> None:
    """Push a session's expiry out (sliding renewal keeps the SAME sid)."""
    with _LOCK:
        try:
            d = _load()
        except StoreUnavailable:
            return
        rec = d.get(sid)
        if not isinstance(rec, dict):
            return
        rec["expires"] = float(expires)
        rec["last_seen"] = time.time()
        d[sid] = rec
        _save(d)


# ── last-seen touch (rate-limited) ───────────────────────────────────────────
# One write per verified request would be one disk round trip per API call.
# The in-memory clock below lets at most one write per sid per TOUCH_INTERVAL_S
# through; a restart simply starts the clocks over, which costs one extra write.
_last_touch: dict[str, float] = {}


def touch(sid: str, *, ip: str = "", user_agent: str = "") -> None:
    """Refresh `last_seen` for a verified session.  Never raises."""
    if not sid:
        return
    now = time.time()
    with _LOCK:
        if now - _last_touch.get(sid, 0.0) < TOUCH_INTERVAL_S:
            return
        _last_touch[sid] = now
        try:
            d = _load()
            rec = d.get(sid)
            if not isinstance(rec, dict):
                return
            rec["last_seen"] = now
            if ip:
                rec["ip"] = _ip(ip)
            if user_agent:
                rec["user_agent"] = _short_ua(user_agent)
            d[sid] = rec
            _save(d)
        except Exception:
            # A touch is bookkeeping — it must never turn into a rejection.
            pass


# ── idle expiry ──────────────────────────────────────────────────────────────

def effective_activity(rec: dict) -> float:
    """When the user last did something: `last_activity`, else (a record from before the idle
    rule) `last_seen`, else `created`."""
    return float(rec.get("last_activity") or rec.get("last_seen") or rec.get("created") or 0.0)


def _stamp_activity(sid: str, now: float) -> None:
    """Write `last_activity` (never raises: bookkeeping must not cost a request)."""
    try:
        with _LOCK:
            d = _load()
            rec = d.get(sid)
            if not isinstance(rec, dict) or rec.get("revoked"):
                return
            if float(rec.get("last_activity") or 0.0) >= now:
                return
            rec["last_activity"] = now
            d[sid] = rec
            _save(d)
    except Exception:                                       # noqa: BLE001
        pass


def close_idle(sid: str, now: float, limit: float, job_end: float = 0.0) -> bool:
    """Revoke one session for idling.  Re-checks under the lock (another process may have just
    seen the user), writes ONE `revoke` event.  False = not closed (never raises)."""
    try:
        with _LOCK:
            d = _load()
            rec = d.get(sid)
            if not isinstance(rec, dict) or rec.get("revoked"):
                return False
            if now - max(effective_activity(rec), job_end) <= limit:
                return False
            rec["revoked"] = True
            rec["revoked_at"] = now
            rec["revoked_reason"] = IDLE_REASON
            d[sid] = rec
            _save(d)
            idle_for = round(now - effective_activity(rec))
            email = rec.get("email") or ""
    except Exception as e:                                  # noqa: BLE001 - cannot write = keep it open
        log.warning("sessions: could not close idle session %s (%s: %s)", sid, type(e).__name__, e)
        return False
    record_event("revoke", email=email, sid=sid, reason=IDLE_REASON, idle_s=idle_for)
    return True


def _job_state(email: str, now: float, cache: Optional[dict] = None):
    """`(busy, last_end)`, or None when the job state cannot be read (callers then keep the session)."""
    from motor_ai_sim import session_activity as SA
    key = (email or "").strip().lower()
    if cache is not None and key in cache:
        return cache[key]
    try:
        res = SA.account_job_state(key, now)
    except SA.JobStateUnreadable as e:
        log.warning("sessions: job state of %s unreadable (%s) - session stays open", key, e)
        res = None
    if cache is not None:
        cache[key] = res
    return res


def is_idle(rec: dict, now: float, limit: float, cache: Optional[dict] = None) -> tuple[bool, float]:
    """`(idle, job_end)` for one record under the both-conditions rule."""
    la = effective_activity(rec)
    if now - la <= limit:
        return False, 0.0
    st = _job_state(rec.get("email") or "", now, cache)
    if st is None:
        return False, 0.0                                   # unreadable: fail open
    busy, job_end = st
    if busy:
        return False, 0.0
    return (now - max(la, job_end) > limit), job_end


def check_idle(sid: str, rec: dict, *, activity: bool = False, now: Optional[float] = None) -> bool:
    """Apply the idle rule to the session a verified token names.  True = the session is (now)
    closed for idling.  `activity` = this request is user activity (it refreshes `last_activity`)."""
    from motor_ai_sim import session_activity as SA
    limit = SA.idle_timeout_s()
    if limit <= 0 or not isinstance(rec, dict):
        return False
    now = time.time() if now is None else float(now)
    if "last_activity" not in rec and now - effective_activity(rec) <= limit:
        _stamp_activity(sid, now)         # a session from before the rule: its clock starts at first sight
        return False
    idle, job_end = is_idle(rec, now, limit)
    if idle:
        return close_idle(sid, now, limit, job_end)
    if activity and now - float(rec.get("last_activity") or 0.0) >= ACTIVITY_WRITE_INTERVAL_S:
        _stamp_activity(sid, now)
    return False


_last_sweep = 0.0


def sweep_idle(now: Optional[float] = None) -> int:
    """Close every session that is idle under the rule -> how many.  Never raises."""
    from motor_ai_sim import session_activity as SA
    limit = SA.idle_timeout_s()
    if limit <= 0:
        return 0
    now = time.time() if now is None else float(now)
    try:
        d = _load()
    except StoreUnavailable:
        return 0
    cache: dict = {}
    n = 0
    for sid, rec in list(d.items()):
        if not isinstance(rec, dict) or rec.get("revoked") or float(rec.get("expires") or 0) <= now:
            continue
        idle, job_end = is_idle(rec, now, limit, cache)
        if idle and close_idle(sid, now, limit, job_end):
            n += 1
    return n


def maybe_sweep_idle(now: Optional[float] = None) -> int:
    """`sweep_idle`, at most once per SWEEP_EVERY_S per process (called from request paths)."""
    global _last_sweep
    t = time.time() if now is None else float(now)
    if t - _last_sweep < SWEEP_EVERY_S:
        return 0
    _last_sweep = t
    try:
        return sweep_idle(t)
    except Exception:                                       # noqa: BLE001
        return 0


def live_counts(now: Optional[float] = None) -> dict:
    """What Admin shows: sessions with user activity in the last 5 minutes, and web sessions that
    are open (not revoked, not expired, not idle under the rule).  From the registry only."""
    from motor_ai_sim import session_activity as SA
    limit = SA.idle_timeout_s()
    now = time.time() if now is None else float(now)
    out = {"active_sessions": 0, "active_accounts": 0, "open_sessions": 0,
           "window_s": SA.ACTIVE_WINDOW_S, "idle_timeout_s": limit}
    try:
        d = _load()
    except StoreUnavailable:
        return out
    cache: dict = {}
    accounts = set()
    for rec in d.values():
        if not isinstance(rec, dict) or rec.get("revoked") or float(rec.get("expires") or 0) <= now:
            continue
        if limit > 0 and is_idle(rec, now, limit, cache)[0]:
            continue
        out["open_sessions"] += 1
        if now - effective_activity(rec) <= SA.ACTIVE_WINDOW_S:
            out["active_sessions"] += 1
            accounts.add(_norm(rec.get("email")))
    out["active_accounts"] = len(accounts)
    return out


def public(rec: dict) -> dict:
    """The shape the API hands out (identical for the self and admin views)."""
    if not isinstance(rec, dict):
        return {}
    return {
        "sid": rec.get("sid"),
        "email": rec.get("email"),
        "created": rec.get("created"),
        "lastSeen": rec.get("last_seen"),
        "expires": rec.get("expires"),
        "userAgent": rec.get("user_agent") or "",
        "ip": rec.get("ip") or "",
        "loginMethod": rec.get("login_method") or "password",
        "revoked": bool(rec.get("revoked")),
        "revokedAt": rec.get("revoked_at"),
        "revokedReason": rec.get("revoked_reason") or "",
        "lastActivity": rec.get("last_activity"),
    }


# ── auth event log ───────────────────────────────────────────────────────────

def record_event(event: str, *, email: str = "", sid: str = "", reason: str = "",
                 ip: str = "", user_agent: str = "", path: str = "",
                 **extra) -> None:
    """Append one line to logs/auth_events.jsonl.  Never raises.

    This is the forensic record the daily-logout investigation needed and did
    not have: every rejection carries its REASON, so "the token expired" and
    "users.json was locked for 40 ms" stop looking the same from outside.
    """
    try:
        from motor_ai_sim.private_files import ensure_private_dir, open_private
        ensure_private_dir(_EVENTS_FILE.parent)
        _trim_events()
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "t": round(time.time(), 3),
            "event": event,
            "email": _norm(email),
            "sid": sid or "",
            "reason": reason or "",
            "ip": _ip(ip),
            "user_agent": _short_ua(user_agent, 200),
            "path": (path or "")[:200],
            "pid": os.getpid(),
        }
        if extra:
            line.update({k: v for k, v in extra.items() if v is not None})
        with open_private(_EVENTS_FILE, "a") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _trim_events() -> None:
    """Keep the newest half when the log passes 20 MB.  Never raises."""
    try:
        if not _EVENTS_FILE.is_file():
            return
        if _EVENTS_FILE.stat().st_size <= _EVENTS_MAX_BYTES:
            return
        with open(_EVENTS_FILE, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        keep = lines[len(lines) // 2:]
        tmp = _EVENTS_FILE.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(keep)
        tmp.replace(_EVENTS_FILE)
    except Exception:
        pass


def read_events(limit: int = 200, email: str = "") -> list[dict]:
    """The newest `limit` events, newest first, optionally for one account."""
    try:
        if not _EVENTS_FILE.is_file():
            return []
        with open(_EVENTS_FILE, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        log.error("sessions: auth_events.jsonl unreadable (%s: %s)",
                  type(e).__name__, e)
        return []
    want = _norm(email)
    out: list[dict] = []
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if want and _norm(rec.get("email")) != want:
            continue
        out.append(rec)
        if len(out) >= max(1, int(limit)):
            break
    return out
