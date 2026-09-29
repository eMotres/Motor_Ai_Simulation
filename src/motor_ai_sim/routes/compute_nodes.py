"""My compute nodes (user-owned servers) — see docs/BYO_COMPUTE.md.

Three audiences, three auth rules:

* the account (``/api/nodes``, ``/api/nodes/{id}/...``, ``/api/nodes/prefs``,
  ``/api/nodes/jobs/mine``, ``/api/nodes/jobs/submit``): a signed-in caller,
  and every answer is filtered to THAT caller's nodes and jobs;
* the worker (``/api/nodes/heartbeat``, ``/api/nodes/jobs/lease``,
  ``/api/nodes/jobs/{run_id}/progress|complete|fail``): an ``mcnode_`` bearer
  only (let past the anonymous door by ``auth._NODE_AGENT_PATH``); a node
  sees only its owner's jobs, and an unknown or foreign run is a 404;
* the admin (``/api/admin/user-nodes...``): every node, every remote job,
  the audit log.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from motor_ai_sim import auth as _AUTH
from motor_ai_sim import compute_nodes as CN
from motor_ai_sim import cluster_monitor as CM
from motor_ai_sim.auth import require_admin

router = APIRouter(prefix="/api/nodes", tags=["compute-nodes"])
admin_router = APIRouter(prefix="/api/admin/user-nodes", tags=["admin-cluster"])

MAX_BODY = CN.MAX_RESULT_BYTES + 64 * 1024
_WORKER_DIR = Path(__file__).resolve().parents[3] / "deploy" / "compute-worker"


# ── helpers ──────────────────────────────────────────────────────────────────
def _me(authorization: Optional[str]) -> Dict[str, Any]:
    who = _AUTH.caller_identity(authorization)
    if who["id"] in (_AUTH.ANON_OWNER, ""):
        raise HTTPException(status_code=401, detail="Sign in required.")
    return who


def _node(authorization: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    got = CN.verify_token(authorization)
    if got is None:
        raise HTTPException(status_code=401, detail="bad or revoked node token")
    return got


async def _json(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")
    return data


def _refused(e: CN.Refused):
    raise HTTPException(status_code=e.status, detail=e.detail)


def _base_url(request: Request) -> str:
    return (os.environ.get("PUBLIC_API_URL", "").strip()
            or str(request.base_url)).rstrip("/")


def install_commands(base: str, token: str) -> Dict[str, str]:
    v = CN.platform_version()
    return {
        "script": ("curl -fsSL %s/api/nodes/worker/install.sh | sudo bash -s -- "
                   "--url %s --token %s" % (base, base, token)),
        "docker": ("docker run -d --name motres-worker --restart unless-stopped "
                   "-e MOTRES_URL=%s -e MOTRES_NODE_TOKEN=%s "
                   "ghcr.io/emotres/motres-worker:%s" % (base, token, v)),
    }


# ── the account ──────────────────────────────────────────────────────────────
@router.get("")
def my_nodes(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    who = _me(authorization)
    return {"nodes": CN.list_nodes(who["id"]), "pref": CN.get_pref(who["id"]),
            "prefs": list(CN.PREFS), "platform_version": CN.platform_version(),
            "remotable_kinds": CN.remotable_kinds()}


@router.post("")
def add_node(request: Request, body: dict = Body(default={}),
             authorization: Optional[str] = Header(default=None)):
    who = _me(authorization)
    try:
        nid, tok = CN.create_node(who["id"], str(body.get("name") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": nid, "token": tok, "install": install_commands(_base_url(request), tok),
            "note": "the token is shown once; the platform keeps only its hash"}


@router.post("/{nid}/revoke")
def revoke(nid: str, authorization: Optional[str] = Header(default=None)):
    who = _me(authorization)
    if not CN.revoke_node(nid, None if who["is_admin"] else who["id"]):
        raise HTTPException(status_code=404, detail="no such node")
    return {"ok": True}


@router.delete("/{nid}")
def delete(nid: str, authorization: Optional[str] = Header(default=None)):
    who = _me(authorization)
    if not CN.delete_node(nid, None if who["is_admin"] else who["id"]):
        raise HTTPException(status_code=404, detail="no such node")
    return {"ok": True}


@router.get("/{nid}/history")
def history(nid: str, range: str = "24h",
            authorization: Optional[str] = Header(default=None)):
    who = _me(authorization)
    n = CN._node(nid)
    if n is None or (n.get("owner") != who["id"] and not who["is_admin"]):
        raise HTTPException(status_code=404, detail="no such node")
    return {"node": nid, "points": CM.history(CN.HIST_PREFIX + nid, range)}


@router.put("/prefs")
def put_pref(body: dict = Body(default={}),
             authorization: Optional[str] = Header(default=None)):
    who = _me(authorization)
    try:
        return {"pref": CN.set_pref(who["id"], str(body.get("pref") or ""))}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/jobs/mine")
def my_jobs(limit: int = 50, authorization: Optional[str] = Header(default=None)):
    who = _me(authorization)
    return {"jobs": CN.list_jobs(who["id"], limit=min(limit, 500))}


@router.post("/jobs/submit")
def submit(body: dict = Body(default={}),
           authorization: Optional[str] = Header(default=None)):
    """Queue one job of a registered kind for the caller's nodes (the job's
    own ``pref`` may override the account's; ``platform_only`` is refused here
    because this endpoint exists to reach a node)."""
    from motor_ai_sim import workspace as _WSP
    who = _me(authorization)
    kind = str(body.get("kind") or "")
    if kind not in CN.remotable_kinds():
        raise HTTPException(status_code=400, detail="unknown job kind")
    args = body.get("body") if isinstance(body.get("body"), dict) else {}
    pref = str(body.get("pref") or CN.PREF_OWN_ONLY)
    if CN.route(who["id"], kind, pref=pref) != "remote":
        raise HTTPException(status_code=409,
                            detail="no online node of yours for this job")
    ws = _WSP.workspace_for_identity(who["id"])
    rec = CN.submit(who["id"], ws.id, str(ws.root), kind, args)
    return CN.public(rec)


# ── the worker ───────────────────────────────────────────────────────────────
@router.post("/heartbeat")
async def heartbeat(request: Request,
                    authorization: Optional[str] = Header(default=None)):
    nid, _n = _node(authorization)
    data = await _json(request)
    return CN.heartbeat(nid, data.get("sample") or {}, str(data.get("version") or ""))


@router.post("/jobs/lease")
async def lease(request: Request, authorization: Optional[str] = Header(default=None)):
    nid, n = _node(authorization)
    data = await _json(request)
    try:
        got = CN.lease(nid, n, str(data.get("version") or ""))
    except CN.Refused as e:
        _refused(e)
    return {"job": got}


@router.post("/jobs/{run_id}/progress")
async def progress(run_id: str, request: Request,
                   authorization: Optional[str] = Header(default=None)):
    nid, n = _node(authorization)
    data = await _json(request)
    try:
        return CN.progress(nid, n, run_id, data)
    except CN.Refused as e:
        _refused(e)


@router.post("/jobs/{run_id}/complete")
async def complete(run_id: str, request: Request,
                   authorization: Optional[str] = Header(default=None)):
    nid, n = _node(authorization)
    data = await _json(request)
    try:
        return CN.complete(nid, n, run_id, data)
    except CN.Refused as e:
        _refused(e)


@router.post("/jobs/{run_id}/fail")
async def fail(run_id: str, request: Request,
               authorization: Optional[str] = Header(default=None)):
    nid, n = _node(authorization)
    data = await _json(request)
    cpu = data.get("cpu_s")
    try:
        return CN.fail(nid, n, run_id, str(data.get("error") or ""),
                       float(cpu) if isinstance(cpu, (int, float)) else 0.0)
    except CN.Refused as e:
        _refused(e)


@router.get("/worker/{fname}")
def worker_file(fname: str):
    if fname not in ("install.sh", "motres_compute_worker.py"):
        raise HTTPException(status_code=404, detail="no such file")
    try:
        return PlainTextResponse((_WORKER_DIR / fname).read_text(encoding="utf-8"))
    except OSError:
        raise HTTPException(status_code=404, detail="worker files not shipped")


# ── the admin ────────────────────────────────────────────────────────────────
@admin_router.get("")
def all_nodes(_admin: dict = Depends(require_admin)):
    return {"nodes": CN.list_nodes(None)}


@admin_router.get("/jobs")
def all_jobs(limit: int = 200, _admin: dict = Depends(require_admin)):
    return {"jobs": CN.list_jobs(None, limit=min(limit, 2000))}


@admin_router.get("/audit")
def audit(limit: int = 200, _admin: dict = Depends(require_admin)):
    return {"events": CN.audit_tail(None, min(limit, 2000))}
