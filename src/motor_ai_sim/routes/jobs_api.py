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
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

from motor_ai_sim import jobs as _JOBS
from motor_ai_sim import progress as _PROG
from motor_ai_sim import workspace as _WSP

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _may_read(owner: Any) -> bool:
    """May this caller read a job of ``owner``?  The owner themself, or an admin."""
    return bool(_WSP.is_admin()) or str(owner or "") == _JOBS.current_owner()


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
    rows = [_with_progress(r) for r in recs]
    # Jobs that went to the caller's OWN compute nodes (docs/BYO_COMPUTE.md):
    # same table, ``where`` says which machine.
    try:
        from motor_ai_sim import compute_nodes as _CN
        rows += _CN.list_jobs(None if (all and admin) else owner, limit=limit)
        rows.sort(key=lambda r: r.get("queued_at") or 0, reverse=True)
        rows = rows[:max(1, int(limit))]
    except Exception as exc:                            # noqa: BLE001
        log.debug("remote jobs not listed: %s", exc)
    out = {"owner": owner, "jobs": rows}
    snap = getattr(q, "snapshot", None)
    if snap is not None:
        qs = snap()
        if not admin:
            # the queue as COUNTS: the items carry other accounts' owners, workspaces and request bodies
            qs = {k: v for k, v in qs.items() if k != "items"}
        out["queue"] = qs
    return out


@router.get("/{run_id}")
def get_job(run_id: str) -> Dict[str, Any]:
    """One job by run id - the record, its queue position and its progress.

    The caller's own job, or any job for an admin; anything else answers 404 exactly as an unknown id does, so the
    answer does not tell whether somebody else's run exists.
    """
    rec = _JOBS.queue().status(run_id)
    if rec is None:
        from motor_ai_sim import compute_nodes as _CN
        rj = _CN.get_job(run_id)
        if rj is not None:
            if not _may_read(rj.get("owner")):
                raise HTTPException(status_code=404, detail="no such run")
            return _CN.public(rj)
        # It may still have a progress entry - a solve that never went through
        # the queue (a nested one, or a route not yet admitted) still reports.
        e = _PROG.registry().entry(run_id)
        # An entry with NO owner is not public: an opaque run id is not authorisation (admin-only in layered mode).
        if e is None or not _may_read(e.owner):
            raise HTTPException(status_code=404, detail="no such run")
        return {"run_id": run_id, "state": "unknown", "kind": e.kind,
                "progress": {**e.snapshot(), "kind": e.kind}}
    if not _may_read(rec.owner):
        raise HTTPException(status_code=404, detail="no such run")
    return _with_progress(rec)


@router.post("/{run_id}/cancel")
def cancel_job(run_id: str,
               authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Stop this run.  403 unless it is the caller's own, or the caller is admin.

    THE one cancel.  Every per-route Stop button ends up here, which is what
    makes "who may stop what" a single rule instead of five.
    """
    try:
        # A job on the caller's own node: flagged here, the worker sees the
        # flag on its next progress call (no inbound port to push to).
        from motor_ai_sim import auth as _AUTH
        from motor_ai_sim import compute_nodes as _CN
        who = _AUTH.caller_identity(authorization)
        remote = _CN.cancel(run_id, who["id"], bool(who["is_admin"]))
        if remote is not None:
            return remote
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
