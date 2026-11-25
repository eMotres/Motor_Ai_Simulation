"""Owner queue routes (docs/OWNER_QUEUE.md).

Two audiences, two auth rules:

* the AGENT side ``/api/owner-queue/agent/...``: an ``oq_`` bearer ONLY, checked here (let past the anonymous door by
  ``auth._OWNER_QUEUE_AGENT_PATH``).  A Firebase session is not accepted.  An agent creates, reads and withdraws ITS OWN items;
  there is no answer route and no other capability.  A foreign or unknown id is a 404 (no leak);
* the owner side ``/api/owner-queue/...``: ``require_admin`` (a verified admin session, never the service token).

The app only records.  It executes nothing on an answer.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from motor_ai_sim import admin_audit as AUD
from motor_ai_sim import owner_queue as OQ
from motor_ai_sim.auth import require_admin

agent_router = APIRouter(prefix="/api/owner-queue/agent", tags=["owner-queue-agent"])
_owner_router = APIRouter(prefix="/api/owner-queue", tags=["owner-queue"])
router = APIRouter()          # what api.py includes: the owner side and the agent side


def _agent(authorization: Optional[str]) -> str:
    try:
        name = OQ.verify_token(authorization)
    except OQ.StoreDamaged:
        raise HTTPException(status_code=503, detail="owner queue store needs attention")
    if name is None:
        raise HTTPException(status_code=401, detail="bad or revoked owner-queue token")
    return name


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except OQ.Refused as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    except OQ.StoreDamaged:
        raise HTTPException(status_code=503, detail="owner queue store needs attention")


# -- agent side -------------------------------------------------------------------------------------------------------------------
@agent_router.post("/items")
def agent_create(body: Any = Body(default=None), authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    name = _agent(authorization)
    res = _guard(OQ.create_item, name, body)
    if res["created"]:
        AUD.record(name, "owner_queue.create", res["id"],
                   details={"kind": body.get("kind"), "session": body.get("asked_by")})
    return {"id": res["id"]}


@agent_router.get("/items")
def agent_list(state: str = "", project_id: str = "",
               authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    name = _agent(authorization)
    return {"items": _guard(OQ.list_items, token_name=name, state=state, project_id=project_id)}


@agent_router.get("/items/{iid}")
def agent_get(iid: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    name = _agent(authorization)
    it = _guard(OQ.get_item, iid)
    if it is None or it.get("token_name") != name:
        raise HTTPException(status_code=404, detail="no such item")
    return it


@agent_router.post("/items/{iid}/withdraw")
def agent_withdraw(iid: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    name = _agent(authorization)
    row = _guard(OQ.withdraw_item, name, iid)
    AUD.record(name, "owner_queue.withdraw", iid, details={"kind": row.get("kind")})
    return row


# -- owner side -------------------------------------------------------------------------------------------------------------------
@_owner_router.get("/items")
def items(state: str = "", project_id: str = "", asked_by: str = "", q: str = "",
          _admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    return {"items": _guard(OQ.list_items, state=state, project_id=project_id, asked_by=asked_by, q=q)}


@_owner_router.get("/count")
def count(_admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    return _guard(OQ.counts)


@_owner_router.post("/items/{iid}/answer")
def answer(iid: str, body: Any = Body(default=None), admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    who = AUD.actor_of(admin)
    row = _guard(OQ.answer_item, iid, body, who)
    ans = row.get("answer") or {}
    AUD.record(who, "owner_queue.answer", iid,
               details={"kind": row.get("kind"), "answer_kind": ans.get("option") or "text"})
    return row


@_owner_router.get("/tokens")
def tokens(_admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    return {"tokens": _guard(OQ.list_tokens)}


@_owner_router.post("/tokens")
def token_create(body: Any = Body(default=None), admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    name = body.get("name") if isinstance(body, dict) else None
    who = AUD.actor_of(admin)
    tok = _guard(OQ.create_token, name, who)
    AUD.record(who, "owner_queue.token_create", name, details={"name": name})
    return {"name": name, "token": tok, "note": "the token is shown once; the app keeps only its hash"}


@_owner_router.post("/tokens/{name}/revoke")
def token_revoke(name: str, admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    if not _guard(OQ.revoke_token, name):
        raise HTTPException(status_code=404, detail="no such token")
    AUD.record(AUD.actor_of(admin), "owner_queue.token_revoke", name, details={"name": name})
    return {"ok": True}


router.include_router(_owner_router)
router.include_router(agent_router)
