"""Admin -> Servers: node metrics ingest + cluster/app views (see cluster_monitor).

``POST /api/admin/nodes/metrics`` is the ONLY route here without require_admin:
it takes a per-node bearer token instead (and is whitelisted past the exhibit
door in auth._ANON_OK_PATHS for that reason).  Everything else is admin-only.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

from motor_ai_sim import cluster_monitor as CM
from motor_ai_sim.auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin-cluster"])

MAX_BODY = 512 * 1024


@router.post("/nodes/metrics")
async def ingest_metrics(request: Request,
                         authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    nid = CM.verify_token(authorization)
    if nid is None:
        raise HTTPException(status_code=401, detail="bad or revoked node token")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(status_code=413, detail="sample too large")
    import json
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="sample must be an object")
    CM.ingest(nid, data)
    return {"ok": True, "node": nid}


@router.get("/nodes")
def nodes(_admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    return CM.list_nodes()


@router.post("/nodes")
def add_node(body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    try:
        nid, tok = CM.create_node(str(body.get("name") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": nid, "token": tok}


@router.post("/nodes/{nid}/revoke")
def revoke(nid: str, _admin: dict = Depends(require_admin)):
    if not CM.revoke_node(nid):
        raise HTTPException(status_code=404, detail="no such node")
    return {"ok": True}


@router.delete("/nodes/{nid}")
def delete(nid: str, _admin: dict = Depends(require_admin)):
    if not CM.delete_node(nid):
        raise HTTPException(status_code=404, detail="no such node")
    return {"ok": True}


@router.get("/nodes/{nid}/history")
def node_history(nid: str, range: str = "24h", _admin: dict = Depends(require_admin)):
    return {"node": nid, "range": "7d" if range == "7d" else "24h",
            "points": CM.history(nid, range)}


@router.get("/cluster/app")
def app_view(_admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    out = CM.app_metrics()
    try:
        out["jobs"] = CM.jobs_view()
    except Exception as e:                               # noqa: BLE001
        out["jobs"] = {"error": CM.redact(str(e))[:200], "items": []}
    return out


@router.post("/cluster/jobs/{run_id}/stop")
def stop_job(run_id: str, _admin: dict = Depends(require_admin)):
    from motor_ai_sim import jobs as _J
    return _J.cancel_run(run_id, requester="", is_admin=True)


# ── usage accounting (motor_ai_sim.job_usage) ────────────────────────────────
def _period(start: Optional[float], end: Optional[float], days: Optional[float]):
    import time as _t
    e = float(end) if end else _t.time()
    s = float(start) if start else e - 86400.0 * float(days or 1)
    if s >= e:
        raise HTTPException(status_code=400, detail="start must be before end")
    return s, e


@router.get("/usage")
def usage(by: str = "user", start: Optional[float] = None, end: Optional[float] = None,
          days: Optional[float] = None, _admin: dict = Depends(require_admin)):
    from motor_ai_sim import job_usage as U
    s, e = _period(start, end, days)
    return U.summary(s, e, by=by)


@router.get("/usage/jobs")
def usage_jobs(user: Optional[str] = None, client: Optional[str] = None,
               start: Optional[float] = None, end: Optional[float] = None,
               days: Optional[float] = None, limit: int = 500,
               _admin: dict = Depends(require_admin)):
    from motor_ai_sim import job_usage as U
    s, e = _period(start, end, days)
    return {"jobs": U.jobs(s, e, user=user, client=client, limit=min(limit, 5000))}


@router.get("/usage.csv")
def usage_csv(by: str = "user", detail: str = "summary", user: Optional[str] = None,
              client: Optional[str] = None, start: Optional[float] = None,
              end: Optional[float] = None, days: Optional[float] = None,
              _admin: dict = Depends(require_admin)):
    from fastapi.responses import Response
    from motor_ai_sim import job_usage as U
    s, e = _period(start, end, days)
    rows = (U.jobs(s, e, user=user, client=client, limit=10 ** 6) if detail == "jobs"
            else U.summary(s, e, by=by)["rows"])
    return Response(U.to_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="usage_{detail}.csv"'})
