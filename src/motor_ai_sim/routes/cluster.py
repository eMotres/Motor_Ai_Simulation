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
