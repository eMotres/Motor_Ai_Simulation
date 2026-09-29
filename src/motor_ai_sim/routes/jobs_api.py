"""WHAT IS THIS SERVER DOING FOR ME?  (migration Stage 4)

Three endpoints over :mod:`motor_ai_sim.jobs` and
:class:`motor_ai_sim.progress.ProgressRegistry`, and one idea: until now every
answer to that question was per ROUTE.  A user watching the Simulation strip
could not see that their own duty cycle was queued behind somebody else's
campaign, and a user whose run had finished while the tab was closed had no way
to find out how.

* ``GET /api/jobs`` — this caller's jobs: queued (with a position), running
  (with the progress bar), and the recent finished ones, including the ones a
  RESTART interrupted (they come back from ``<ws>/.jobs.json``).
* ``GET /api/jobs/{run_id}`` — one job, the same record plus its progress.
* ``POST /api/jobs/{run_id}/cancel`` — stop it.  **Owner-checked**: 403 unless
  the run is the caller's own or the caller is an admin.  The per-route cancel
  endpoints (``/api/simulation/physics/fem_transient/cancel``,
  ``/api/coupled/cancel``, ``/api/static3d/solve/cancel``, the optimizer's two)
  all delegate to the same check, so there is ONE rule about who may stop what.

Not gated, exactly as the three progress routes are not gated (``auth._GATED``):
the SOLVE is gated, and its status is a status read.  What keeps one account out
of another's business here is the OWNER filter, not the tier.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from motor_ai_sim import jobs as _JOBS
from motor_ai_sim import progress as _PROG
from motor_ai_sim import workspace as _WSP

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _with_progress(rec: "_JOBS.JobRecord") -> Dict[str, Any]:
    """One record + the live bar of the run it names.

    The progress is the TRACKER's, not a second arithmetic: a finished job whose
    entry has already been TTL-evicted simply carries no ``progress`` key, which
    is the honest answer to "how far did it get" half an hour later.
    """
    out = rec.public()
    e = _PROG.registry().entry(rec.run_id)
    if e is not None:
        out["progress"] = e.snapshot()
        out["progress"]["kind"] = e.kind
    return out


@router.get("")
def list_jobs(limit: int = 50, all: bool = False) -> Dict[str, Any]:
    """This caller's jobs, newest first.

    ``?all=true`` is honoured for an ADMIN only — and silently ignored for
    anyone else rather than refused, because a listing is not a place to teach
    somebody that other accounts exist.
    """
    owner = _JOBS.current_owner()
    admin = _WSP.is_admin()
    q = _JOBS.queue()
    try:
        recs = q.list_for_owner(owner, limit=limit, is_admin=bool(all and admin))
    except NotImplementedError:
        raise HTTPException(status_code=503,
                            detail="the configured queue cannot list jobs")
    out = {"owner": owner, "jobs": [_with_progress(r) for r in recs]}
    snap = getattr(q, "snapshot", None)
    if snap is not None:
        out["queue"] = snap()
    return out


@router.get("/{run_id}")
def get_job(run_id: str) -> Dict[str, Any]:
    """One job by run id — the record, its queue position and its progress.

    Readable by anyone who has the id, like every other progress route: an id is
    an opaque nonce, and a strip that 403s on the run it is drawing helps nobody.
    Cancelling is the operation that checks ownership.
    """
    rec = _JOBS.queue().status(run_id)
    if rec is None:
        # It may still have a progress entry — a solve that never went through
        # the queue (a nested one, or a route not yet admitted) still reports.
        e = _PROG.registry().entry(run_id)
        if e is None:
            raise HTTPException(status_code=404, detail="no such run")
        return {"run_id": run_id, "state": "unknown", "kind": e.kind,
                "progress": {**e.snapshot(), "kind": e.kind}}
    return _with_progress(rec)


@router.post("/{run_id}/cancel")
def cancel_job(run_id: str) -> Dict[str, Any]:
    """Stop this run.  403 unless it is the caller's own, or the caller is admin.

    THE one cancel.  Every per-route Stop button ends up here, which is what
    makes "who may stop what" a single rule instead of five.
    """
    try:
        out = _JOBS.cancel_run(run_id)
    except _JOBS.NotOwner:
        raise HTTPException(status_code=403,
                            detail="that run belongs to another account")
    except NotImplementedError:
        raise HTTPException(status_code=503,
                            detail="the configured queue cannot cancel jobs")
    rec = _JOBS.queue().status(run_id)
    if rec is not None:
        # The kinds that need more than a flag — the optimizer's eval
        # subprocesses, static3d's worker — hang their own hook off the queue
        # (``jobs.register_cancel_hook``); it has already run by now.
        out["kind"] = rec.kind
        out["state"] = rec.state
    return out
