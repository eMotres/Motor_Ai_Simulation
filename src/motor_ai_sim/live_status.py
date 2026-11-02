"""What is in use RIGHT NOW, per module (owner 2026-10-18: "we fix bugs continuously after the launch; restart only
what changed, when nobody is using it").

Three signals, all in this API process (one uvicorn process, so in-memory is the truth).  Since deploy stage 2 the
computations run in the separate compute worker (``roles``): the API's snapshot then MERGES the worker's own
(``worker_client.fetch_state``) - its jobs, the requests it is serving - so the deploy script and the Admin panel keep
one answer for the whole site, and ``worker`` in it says what the worker is doing.

* users per tab - the activity heartbeat (``activity.record_beat``) also notes the tab here; a user counts as present
  in a tab for ``LIVE_WINDOW_S`` after the last beat (the app beats about once a minute while the tab is visible and
  the user interacts);
* requests in flight per module - an ASGI middleware counts the open requests by path prefix;
* open streams per module - a request the CLIENT holds open with no work behind it (the MCP server-sent-event stream an
  AI app keeps connected for as long as it is connected, ``is_stream``) is counted apart, as ``streams``: never as a
  request, so it never makes the site, the worker or a module busy.  A tool call that starts a solve is a short POST
  that queues a job: the job queue counts it;
* queued / running jobs - read from the per-workspace job files.

The deploy script reads ``GET /api/admin/live`` (admin, or a direct loopback call inside the container) and waits
until the modules its change touches are idle.  ``notice`` is the short banner ("a quick update in 2 minutes") the
deploy shows before it restarts the API.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LIVE_WINDOW_S = 150.0

#: path prefix -> module (= the app tab that uses it, where there is one); first match wins
MODULE_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("/api/production", "production"), ("/api/admin/production", "production"),
    ("/api/design-agent", "designer"), ("/api/propellers", "compare"), ("/api/configure", "compare"),
    ("/api/model3d", "model3d"), ("/api/nda", "model3d"), ("/api/verification", "model3d"),
    ("/api/simulation", "simulation"), ("/api/jobs", "simulation"), ("/api/thermal", "thermal"),
    ("/api/coupled", "thermal"), ("/api/mechanical", "mechanical"), ("/api/controller", "controller"),
    ("/api/optimization", "sweep"), ("/api/sweep", "sweep"), ("/api/static3d", "static3d"),
    ("/api/geometry", "geometry"), ("/api/mesh", "mesh"), ("/api/materials", "materials"),
    ("/api/family", "motors"), ("/api/catalog", "motors"), ("/api/presets", "motors"),
    ("/api/support", "support"), ("/api/admin", "admin"), ("/api/auth", "auth"), ("/oauth", "auth"),
    ("/mcp", "mcp"), ("/api", "other"),
)

#: Routes whose GET / HEAD is a stream the client holds open (not work): the MCP standalone server-sent-event stream
#: (streamable HTTP: an MCP client opens ``GET /mcp`` with ``Accept: text/event-stream`` and keeps it for the whole
#: connection - 2026-11-02 one stayed open 1044 s and the worker never counted as idle, so the deploy waited forever).
STREAM_ROUTES: Tuple[str, ...] = ("/mcp",)

_lock = threading.Lock()
_tabs: Dict[Tuple[str, str], Tuple[str, float]] = {}          # (user, sid) -> (tab, last beat)
_inflight: Dict[int, Tuple[str, float]] = {}                  # request token -> (module, started)
_streams: Dict[int, Tuple[str, float]] = {}                   # stream token -> (module, opened); never counted as work
_seq = 0


def module_of(path: str) -> Optional[str]:
    for pfx, mod in MODULE_PREFIXES:
        if path == pfx or path.startswith(pfx + "/") or (pfx == "/api" and path.startswith("/api")):
            return mod
    return None


def note_tab(user: str, sid: str, tab: str, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with _lock:
        _tabs[(user, sid)] = (tab or "?", now)
        if len(_tabs) > 5000:                                  # bounded: drop the stale ones
            for k in [k for k, (_t, ts) in _tabs.items() if now - ts > LIVE_WINDOW_S]:
                _tabs.pop(k, None)


#: Work that is running in this process without an HTTP request holding it open (the design assistant's answer thread ...).
#: The blue-green drain waits for it as it waits for requests: stopping the old API colour would kill it.
_background: Dict[str, int] = {}


@contextmanager
def background(kind: str):
    """``with background("design-agent"):`` - count this thread as work in progress for the drain."""
    with _lock:
        _background[kind] = _background.get(kind, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _background[kind] = _background.get(kind, 1) - 1
            if _background[kind] <= 0:
                _background.pop(kind, None)


def is_stream(scope) -> bool:
    """A request the client holds open with no work behind it: a ``GET`` / ``HEAD`` of a ``STREAM_ROUTES`` path, or any
    ``GET`` / ``HEAD`` that asks for ``text/event-stream`` (a server-sent-event stream).  POSTs are never streams (an MCP
    tool call is a POST, and the solve it starts is a queued job)."""
    if str(scope.get("method") or "").upper() not in ("GET", "HEAD"):
        return False
    path = str(scope.get("path") or "").rstrip("/")
    if path in STREAM_ROUTES:
        return True
    for k, v in scope.get("headers") or ():
        if k.lower() == b"accept" and b"text/event-stream" in v.lower():
            return True
    return False


def _begin(path: str, stream: bool = False) -> Optional[int]:
    global _seq
    mod = module_of(path)
    if mod is None:
        return None
    with _lock:
        _seq += 1
        (_streams if stream else _inflight)[_seq] = (mod, time.time())
        return _seq


def _end(token: Optional[int]) -> None:
    if token is not None:
        with _lock:
            _inflight.pop(token, None)
            _streams.pop(token, None)


class InflightMiddleware:
    """Counts the HTTP requests being served, by module (pure ASGI: streaming responses are counted until done).  An
    open stream (``is_stream``) is counted apart and is never work."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") in (
                "/api/admin/live", "/api/health", "/api/notice", "/api/worker/state", "/api/worker/drain"):
            return await self.app(scope, receive, send)
        token = _begin(scope.get("path") or "", stream=is_stream(scope))
        try:
            return await self.app(scope, receive, send)
        finally:
            _end(token)


def _jobs() -> Dict[str, Any]:
    import os
    root = (os.environ.get("WORKSPACES_ROOT") or "").strip()
    out: Dict[str, Any] = {"running": 0, "queued": 0, "kinds": {}}
    if not root:
        return out
    for f in Path(root).glob("*/.jobs.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        jobs = d.get("jobs") if isinstance(d, dict) else d
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        for j in jobs or []:
            st = str((j or {}).get("state") or (j or {}).get("status") or "")
            if st in ("running", "queued"):
                out[st] += 1
                k = str(j.get("kind") or "?")
                out["kinds"][k] = out["kinds"].get(k, 0) + 1
    return out


def _jobs_memory() -> Dict[str, Any]:
    """Queued / running jobs of THIS process's queue (the compute worker: the truth, a record file only says it at the
    moment it was written)."""
    from motor_ai_sim import jobs as _J
    snap = _J.queue().snapshot()
    kinds: Dict[str, int] = {}
    for it in snap.get("items") or []:
        k = str(it.get("kind") or "?")
        kinds[k] = kinds.get(k, 0) + 1
    return {"running": int(snap.get("running") or 0), "queued": int(snap.get("queued") or 0), "kinds": kinds}


def job_rows(limit: int = 50) -> List[Dict[str, Any]]:
    """The running / queued jobs of this process for the Admin panel (no request bodies)."""
    from motor_ai_sim import jobs as _J
    out: List[Dict[str, Any]] = []
    for it in (_J.queue().snapshot().get("items") or [])[:limit]:
        out.append({k: it.get(k) for k in ("run_id", "kind", "owner", "state", "position", "elapsed_s", "waited_s")})
    return out


def snapshot(include_users: bool = False, now: Optional[float] = None) -> Dict[str, Any]:
    """The site's live status: this process's own, plus the compute worker's when this is the API process."""
    snap = local_snapshot(include_users, now)
    from motor_ai_sim import roles
    if roles.is_api():
        snap = merge_worker(snap)
    return snap


def merge_worker(snap: Dict[str, Any], state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fold the worker's live status into the API's.  ``state`` None = ask the worker now."""
    if state is None:
        from motor_ai_sim import worker_client
        state = worker_client.fetch_state()
    if not state or not isinstance(state.get("live"), dict):
        # The worker cannot be asked.  Keep the record-file view of the jobs (conservative: it reports busy), and say so.
        snap["worker"] = {"reachable": False, "draining": None, "jobs": snap["jobs"], "items": []}
        return snap
    live = state["live"]
    snap["jobs"] = live.get("jobs") or {"running": 0, "queued": 0, "kinds": {}}
    for name, m in (live.get("modules") or {}).items():
        mine = snap["modules"].setdefault(name, {"users": 0, "requests": 0, "oldest_request_s": 0.0})
        mine["requests"] += int(m.get("requests") or 0)
        mine["oldest_request_s"] = max(float(mine.get("oldest_request_s") or 0.0), float(m.get("oldest_request_s") or 0.0))
        if m.get("streams"):
            mine["streams"] = int(mine.get("streams") or 0) + int(m.get("streams") or 0)
    snap["requests_total"] += int(live.get("requests_total") or 0)
    snap["streams_total"] = int(snap.get("streams_total") or 0) + int(live.get("streams_total") or 0)
    snap["worker"] = {"reachable": True, "draining": bool(state.get("draining")), "build": state.get("build"),
                      "instance": state.get("instance"), "uptime_s": state.get("uptime_s"),
                      "jobs": snap["jobs"], "items": state.get("jobs") or []}
    return snap


def local_snapshot(include_users: bool = False, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    with _lock:
        tabs = [(u, t) for (u, _s), (t, ts) in _tabs.items() if now - ts <= LIVE_WINDOW_S]
        infl = list(_inflight.values())
        strm = list(_streams.values())
        bg = dict(_background)
    by_tab: Dict[str, Dict[str, Any]] = {}
    for user, tab in tabs:
        e = by_tab.setdefault(tab, {"users": set()})
        e["users"].add(user)
    modules: Dict[str, Dict[str, Any]] = {}
    for tab, e in by_tab.items():
        m = modules.setdefault(tab, {"users": 0, "requests": 0, "oldest_request_s": 0.0})
        m["users"] = len(e["users"])
        if include_users:
            m["accounts"] = sorted(e["users"])
    for mod, started in infl:
        m = modules.setdefault(mod, {"users": 0, "requests": 0, "oldest_request_s": 0.0})
        m["requests"] += 1
        m["oldest_request_s"] = round(max(m["oldest_request_s"], now - started), 1)
    for mod, _opened in strm:                                  # shown, never counted as requests / busy
        m = modules.setdefault(mod, {"users": 0, "requests": 0, "oldest_request_s": 0.0})
        m["streams"] = int(m.get("streams") or 0) + 1
    from motor_ai_sim import roles
    return {"at": now, "window_s": LIVE_WINDOW_S, "users_total": len({u for u, _t in tabs}),
            "requests_total": len(infl), "streams_total": len(strm), "jobs": _jobs_memory() if roles.is_worker() else _jobs(), "modules": modules,
            "background": {"total": sum(bg.values()), "kinds": bg},
            # what stopping this process would cut off: requests in flight + background threads (the drain checks it);
            # open streams are not in it - the client reconnects, no work is lost
            "busy_total": len(infl) + sum(bg.values()), "notice": read_notice(now)}


def idle(snap: Dict[str, Any], modules: List[str]) -> bool:
    """No job queued or running, and nobody / no request in any of ``modules`` ("all" = anywhere).  Open streams
    (``streams``) do not count."""
    if snap["jobs"]["running"] or snap["jobs"]["queued"]:
        return False
    for name, m in snap["modules"].items():
        if "all" in modules or name in modules:
            if m["users"] or m["requests"]:
                return False
    return True


# ── the maintenance notice ───────────────────────────────────────────────────

def _notice_path() -> Path:
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH
    return Path(DEFAULT_CONFIG_PATH).parent / "maintenance_notice.json"


def read_notice(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now = time.time() if now is None else now
    try:
        d = json.loads(_notice_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or float(d.get("until") or 0) < now:
        return None
    return {"message": d.get("message") or {}, "at": d.get("at"), "until": d.get("until")}


def set_notice(message: Dict[str, str], minutes: float) -> Dict[str, Any]:
    now = time.time()
    d = {"message": {"en": str(message.get("en") or "")[:300], "zh": str(message.get("zh") or "")[:300]},
         "at": now + 0.0, "until": now + max(1.0, min(120.0, float(minutes))) * 60.0}
    p = _notice_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    tmp.replace(p)
    return d


def clear_notice() -> None:
    try:
        _notice_path().unlink()
    except FileNotFoundError:
        pass
