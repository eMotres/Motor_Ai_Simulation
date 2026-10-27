"""The compute worker's state and drain switch (deploy stage 2, docs/DEPLOY_STAGE2.md).

GET  /api/worker/state   what this process is doing: its live status (jobs, requests in flight per module), the queued and
                         running jobs, the drain flag, the build it runs.  Read by the API (Admin -> "Now on the site",
                         Servers) and by deploy/deploy_module.sh.
POST /api/worker/drain   {"on": true}  take no NEW job; queued and running ones finish.  {"on": false} lifts it.
                         The deploy script drains right before it restarts the worker (and only when it is idle), so a
                         restart never meets a running computation.

Not routed by nginx (``roles.WORKER_PREFIXES`` has no ``/api/worker``): only a direct loopback call inside the container
or a request carrying the deployment's internal key (``roles.internal_key``) gets in - the same door the stage-1 live
status uses (``auth._deploy_loopback``), checked again here.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request

from motor_ai_sim import jobs as _JOBS
from motor_ai_sim import live_status as L
from motor_ai_sim import roles

router = APIRouter(tags=["worker"])

_LOOPBACK = {"127.0.0.1", "::1"}
_STARTED = time.time()


def _allowed(request: Request) -> bool:
    host = getattr(request.client, "host", "") if request.client else ""
    if host in _LOOPBACK and not request.headers.get("x-forwarded-for") and not request.headers.get("x-real-ip"):
        return True
    return roles.internal_ok(request.headers)


def _require(request: Request) -> None:
    if not _allowed(request):
        raise HTTPException(403, detail="internal only")


def _live_activity():
    try:
        return _JOBS.live_targets()
    except Exception:                       # noqa: BLE001 - the state answer must not depend on it
        return []


def _recent_failures():
    try:
        return _JOBS.recent_failures()
    except Exception:                       # noqa: BLE001
        return []


def _server_load():
    try:
        return _JOBS.server_load()
    except Exception:                       # noqa: BLE001
        return None


def state_payload() -> Dict[str, Any]:
    """What ``GET /api/worker/state`` answers (a function so the tests and the CLI can use it without HTTP)."""
    out: Dict[str, Any] = {
        "role": roles.role(), "instance": roles.instance(), "build": roles.build_id(), "pid": os.getpid(),
        "started_at": _STARTED, "uptime_s": round(time.time() - _STARTED, 1),
        "draining": _JOBS.is_draining(),
        "live": L.local_snapshot(include_users=False),
        "jobs": L.job_rows(),
        # which machine each running / queued run is for (no run ids, owners or bodies): the project pages' live activity
        "activity": _live_activity(),
        # how many runs run / wait on this server (counts only): the "N on the server" line of the project pages
        "queue": _server_load(),
        "failures": _recent_failures(),
    }
    try:                                    # Admin -> Servers shows the queue of the process that runs it
        from motor_ai_sim import cluster_monitor as CM
        out["jobs_view"] = CM.jobs_view(local=True)
    except Exception:                       # noqa: BLE001 - the state answer must not depend on a view
        out["jobs_view"] = None
    return out


@router.get("/api/worker/state")
def worker_state(request: Request) -> Dict[str, Any]:
    _require(request)
    return state_payload()


@router.post("/api/worker/drain")
def worker_drain(request: Request, body: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
    _require(request)
    on = bool((body or {}).get("on", True))
    draining = _JOBS.set_draining(on)
    live = L.local_snapshot(include_users=False)
    return {"draining": draining, "jobs": live["jobs"], "requests_total": live["requests_total"]}
