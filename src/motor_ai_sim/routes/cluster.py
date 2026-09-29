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


_LOAD_RANGES = {"15m", "1h", "24h", "7d"}
_LOAD_CACHE: Dict[str, Any] = {"key": None, "ts": 0.0, "data": None}
_LOAD_CACHE_TTL_S = 5.0


@router.get("/load/live")
def load_live(range: str = "1h", top: int = 8, _admin: dict = Depends(require_admin)):
    """Admin -> Overview live load: per-node series + per-user stacked CPU +
    a running/queued snapshot. Cached for a few seconds so the auto-refreshing
    panel does not hammer the store on every tab."""
    import time as _t
    rng = range if range in _LOAD_RANGES else "1h"
    cache_key = f"{rng}:{top}"
    now = _t.time()
    cached = _LOAD_CACHE
    if cached["key"] == cache_key and now - cached["ts"] < _LOAD_CACHE_TTL_S:
        return cached["data"]

    from motor_ai_sim import job_usage as U
    lookback = CM.RANGE_LOOKBACK_S.get(rng, 3600)
    node_rng = "7d" if rng == "7d" else ("24h" if rng == "24h" else rng)
    nodes = CM.list_nodes()
    node_series = {n["id"]: CM.history(n["id"], node_rng) for n in nodes["nodes"]
                  if n["status"] != "revoked"}
    user_load = U.user_load_series(now - lookback, now, top_n=top)
    outside_app = CM.outside_app_series(now - lookback, now, top_n=top)
    outside_app["now"] = CM.outside_app_now()
    since = [n.get("created") for n in nodes["nodes"]
            if n.get("created") and n["status"] != "revoked"]
    try:
        snapshot = CM.jobs_view()
        for row in snapshot.get("items") or []:
            row.setdefault("node", U.node_name())
    except Exception as e:                                # noqa: BLE001
        snapshot = {"error": CM.redact(str(e))[:200], "items": []}

    data = {"range": rng, "nodes": node_series, "cluster": nodes["cluster"],
            "user_load": user_load, "outside_app": outside_app, "snapshot": snapshot,
            "monitoring_since": min(since) if since else None}
    _LOAD_CACHE.update(key=cache_key, ts=now, data=data)
    return data


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


# ── usage statistics (motor_ai_sim.usage_stats) — CPU-hours, storage, counts ─
@router.get("/usage/monthly")
def usage_monthly(month: str = "", format: str = "json",
                  _admin: dict = Depends(require_admin)):
    import re as _re
    import time as _t
    from fastapi.responses import Response
    from motor_ai_sim import usage_stats as US
    month = month or _t.strftime("%Y-%m", _t.gmtime())
    if not _re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    rep = US.monthly(month)
    if format == "csv":
        return Response(US.monthly_csv(rep), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="usage_{month}.csv"'})
    return rep


@router.get("/usage/daily")
def usage_daily(days: float = 7, _admin: dict = Depends(require_admin)):
    import time as _t
    from motor_ai_sim import usage_stats as US
    e = _t.time()
    return {"days": US.daily(e - 86400 * days, e)}
