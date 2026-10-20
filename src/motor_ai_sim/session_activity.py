"""What keeps a web session open: user activity and the account's own computations.

Owner request 2026-10-20: a web session is closed after 30 minutes without use, so the Admin
"active now" number is the truth, and (clarification the same day) NOTHING is closed while the
account has something queued or running.

The rule (``sessions.check_idle``):

    a web session is closed only when BOTH hold for ``idle_timeout_s()`` (default 30 min)
      (a) no USER activity, and
      (b) none of the account's computations is queued or running.
    When the last computation ends, the clock runs from the LATER of the last activity and that
    computation's end.  If the job state cannot be read, the session stays open (fail open).

This module owns the two inputs of that rule.

User activity (``is_user_activity``)
------------------------------------
* any request that is not GET / HEAD / OPTIONS (a click that changes something), except the
  automatic ones in ``PASSIVE_NON_GET`` (the working-time beat, worker / node traffic);
* ``POST /api/auth/heartbeat`` - the app sends it at most once a minute, only when the tab is
  visible and there was real input (pointer, key, wheel, touch) since the previous one.

Everything else is background traffic and never extends a session: ``GET`` polls (job status,
live load, version checks, ``/api/me``...).

The account's computations (``account_job_state``)
--------------------------------------------------
Read from the sources the platform already keeps, never from the browser's polls:

* the job queue's records in the account's workspace (``<ws>/.jobs.json``, written by the API
  process or by the compute worker - the cross-process truth) and this process's live queue;
* the progress registry (report / datasheet generation, CAD builds and every other run that
  reports progress under an owner);
* the account's own BYO compute nodes (``compute_nodes``).

A record that says ``running`` / ``queued`` for longer than ``JOB_STALE_S`` is a crashed
leftover and does not hold a session.  An unreadable source raises ``JobStateUnreadable`` and
the caller keeps the session open.

Request context
---------------
``SessionContextMiddleware`` (pure ASGI, outermost) records the method and path of the request
in a context variable so that token verification, wherever it happens, knows whether THIS
request is user activity.  When a presented token was refused for idling it also adds
``X-Auth-Reason: idle_timeout`` to the response (and exposes it to CORS), so the web app learns
about the closed session from any answer, not only from a 401.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ENV_IDLE = "MOTRES_SESSION_IDLE_S"
#: 30 minutes of no use closes a web session (owner 2026-10-20).
SESSION_IDLE_TIMEOUT_S = 1800.0
#: "Active now" in Admin = user activity within this many seconds.
ACTIVE_WINDOW_S = 300.0
#: A `running` / `queued` job record older than this is a crashed leftover, not work in flight.
JOB_STALE_S = 48 * 3600.0

#: Non-GET requests that are NOT a user acting: the working-time beat is sent for up to 5 minutes after
#: the last input; node / worker traffic carries its own tokens.
PASSIVE_NON_GET = ("/api/activity/heartbeat", "/api/nodes/", "/api/worker/")
HEARTBEAT_PATH = "/api/auth/heartbeat"


class JobStateUnreadable(RuntimeError):
    """A source of the account's job state could not be read - the caller keeps the session open."""


def idle_timeout_s() -> float:
    """The idle limit in seconds (env ``MOTRES_SESSION_IDLE_S``, read per call); 0 disables it."""
    raw = os.environ.get(ENV_IDLE, "").strip()
    if not raw:
        return SESSION_IDLE_TIMEOUT_S
    try:
        v = float(raw)
    except ValueError:
        return SESSION_IDLE_TIMEOUT_S
    return max(0.0, v)


# ── what counts as activity ──────────────────────────────────────────────────

def is_user_activity(method: str, path: str) -> bool:
    m = (method or "").upper()
    p = path or ""
    if p == HEARTBEAT_PATH:
        return m == "POST"
    if m in ("GET", "HEAD", "OPTIONS", ""):
        return False
    return not any(p == q.rstrip("/") or p.startswith(q) for q in PASSIVE_NON_GET)


class RequestInfo:
    """The request in flight as far as session activity is concerned (mutable: verification writes
    ``reason`` into it so the middleware can put it on the response)."""
    __slots__ = ("method", "path", "reason")

    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.path = path
        self.reason = ""

    @property
    def is_activity(self) -> bool:
        return is_user_activity(self.method, self.path)


_REQ: "contextvars.ContextVar[Optional[RequestInfo]]" = contextvars.ContextVar(
    "motor_ai_sim_session_request", default=None)


def current_request() -> Optional[RequestInfo]:
    return _REQ.get()


def request_is_activity() -> bool:
    """Is the request being served (if any) a user action?  Outside a request: False."""
    r = _REQ.get()
    return bool(r and r.is_activity)


def note_auth_reason(reason: str) -> None:
    r = _REQ.get()
    if r is not None and reason:
        r.reason = reason


class SessionContextMiddleware:
    """Pure ASGI: sets the request context and exposes an idle-closed session on the response."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        info = RequestInfo(str(scope.get("method") or ""), str(scope.get("path") or ""))
        tok = _REQ.set(info)

        async def _send(message):
            if message.get("type") == "http.response.start" and info.reason == "idle_timeout":
                headers = list(message.get("headers") or [])
                headers.append((b"x-auth-reason", b"idle_timeout"))
                exposed = [i for i, (k, _v) in enumerate(headers) if k.lower() == b"access-control-expose-headers"]
                if exposed:
                    k, v = headers[exposed[0]]
                    if b"x-auth-reason" not in v.lower():
                        headers[exposed[0]] = (k, v + b", X-Auth-Reason")
                else:
                    headers.append((b"access-control-expose-headers", b"X-Auth-Reason"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            return await self.app(scope, receive, _send)
        finally:
            _REQ.reset(tok)


# ── the account's computations ───────────────────────────────────────────────

def _jobs_file(email: str) -> Path:
    from motor_ai_sim import jobs as _J
    from motor_ai_sim import workspace as _W
    base = _W.workspaces_root()
    if base is None:
        return Path(str(_W.process_workspace().root)) / _J.JOBS_FILE
    return base / _W.workspace_id(email) / _J.JOBS_FILE


def _from_records(rows, now: float) -> tuple[bool, float]:
    busy, last_end = False, 0.0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        st = str(r.get("state") or r.get("status") or "")
        if st in ("running", "queued"):
            started = float(r.get("started_at") or r.get("queued_at") or 0.0)
            if not started or now - started <= JOB_STALE_S:
                busy = True
        else:
            last_end = max(last_end, float(r.get("finished_at") or 0.0))
    return busy, last_end


def account_job_state(email: str, now: Optional[float] = None) -> tuple[bool, float]:
    """``(busy, last_end)``: is anything of this account queued / running, and when did its newest
    finished computation end (0 = none known).  Raises ``JobStateUnreadable``."""
    now = time.time() if now is None else float(now)
    ident = (email or "").strip().lower()
    busy, last_end = False, 0.0

    def merge(b: bool, e: float) -> None:
        nonlocal busy, last_end
        busy = busy or b
        last_end = max(last_end, e)

    try:
        # 1. the workspace job file (API process and compute worker both write it)
        p = _jobs_file(ident)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        rows = raw.get("jobs") if isinstance(raw, dict) else raw
        if isinstance(rows, dict):
            rows = list(rows.values())
        merge(*_from_records(rows, now))

        # 2. this process's live queue (if it was ever built - do not build it here)
        from motor_ai_sim import jobs as _J
        q = getattr(_J, "_QUEUE", None)
        snap = getattr(q, "snapshot", None)
        if snap is not None:
            items = [i for i in (snap().get("items") or [])
                     if str(i.get("owner") or "").strip().lower() == ident]
            merge(*_from_records(items, now))

        # 3. progress registry: reports, CAD builds, every run that has an owner
        from motor_ai_sim import progress as _P
        merge(*_P.registry().owner_state(ident))

        # 4. the account's own compute nodes
        from motor_ai_sim import compute_nodes as _CN
        merge(*_from_records(_CN.list_jobs(ident, limit=100), now))
    except Exception as e:                                  # noqa: BLE001 - unreadable = keep the session
        raise JobStateUnreadable(f"{type(e).__name__}: {e}") from e
    return busy, last_end
