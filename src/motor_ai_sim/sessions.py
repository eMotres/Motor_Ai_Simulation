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
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from motor_ai_sim.config import DEFAULT_CONFIG_PATH

log = logging.getLogger(__name__)

_SESSIONS_FILE = Path(DEFAULT_CONFIG_PATH).parent / ".sessions.json"
_ROOT = Path(__file__).resolve().parents[2]
_EVENTS_FILE = _ROOT / "logs" / "auth_events.jsonl"
_LOCK = threading.RLock()

#: A session record older than this since `expires` is swept on the next write.
_KEEP_EXPIRED_S = 7 * 24 * 3600
#: `last_seen` is written at most this often per sid (one write per request
#: would turn every GET into a disk round trip).
TOUCH_INTERVAL_S = 60.0
#: The event log is trimmed to its second half when it grows past this.
_EVENTS_MAX_BYTES = 20 * 1024 * 1024

EVENTS = ("login", "logout", "renew", "reject", "store_unavailable", "revoke")


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
    _SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _SESSIONS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
    tmp.replace(_SESSIONS_FILE)


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
        "expires": float(expires),
        "user_agent": _short_ua(user_agent),
        "ip": (ip or "")[:64],
        "login_method": login_method if login_method in ("password", "google") else "password",
        "revoked": False,
    }
    try:
        with _LOCK:
            try:
                d = _load()
            except StoreUnavailable:
                d = {}          # a fresh registry beats refusing the login
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
                rec["ip"] = ip[:64]
            if user_agent:
                rec["user_agent"] = _short_ua(user_agent)
            d[sid] = rec
            _save(d)
        except Exception:
            # A touch is bookkeeping — it must never turn into a rejection.
            pass


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
        _EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _trim_events()
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "t": round(time.time(), 3),
            "event": event,
            "email": _norm(email),
            "sid": sid or "",
            "reason": reason or "",
            "ip": (ip or "")[:64],
            "user_agent": _short_ua(user_agent, 200),
            "path": (path or "")[:200],
            "pid": os.getpid(),
        }
        if extra:
            line.update({k: v for k, v in extra.items() if v is not None})
        with open(_EVENTS_FILE, "a", encoding="utf-8") as f:
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
