"""The design agent ("Designer" tab, pilot 2026-10-18): a Claude tool loop over the catalogue, the propeller S1
ratings and the saved results, answering a customer's "which motor for my drone / robot?".

GET    /api/design-agent/status                     may I use it, model, limits, what I spent today
GET    /api/design-agent/conversations              my conversations (newest first)
POST   /api/design-agent/conversations              {text} -> a new conversation, its first turn running
GET    /api/design-agent/conversations/{id}         one conversation (poll while state == running)
POST   /api/design-agent/conversations/{id}/messages {text} -> the next turn, running
DELETE /api/design-agent/conversations/{id}
GET    /api/admin/design-agent/usage                admin: spend per day and user

Who may use it is ``DESIGN_AGENT_ACCESS`` (pilot: admin).  The agent acts with the caller's own permissions
(``design_agent.tools``) and the corporate API key stays on the server (``design_agent.runner``).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from motor_ai_sim import platform_agreement as _pa
from motor_ai_sim.agent_keys import Principal
from motor_ai_sim.auth import caller_identity, require_admin
from motor_ai_sim.design_agent import runner as _run

router = APIRouter(tags=["design-agent"])


def _principal(authorization: Optional[str]) -> Principal:
    who = caller_identity(authorization)
    if not str(who.get("id") or "") or who.get("id") == "anonymous":
        raise HTTPException(401, detail="Sign in to use the design assistant.")
    if not _run.may_use(who):
        raise HTTPException(403, detail="The design assistant is not open for this account yet.")
    return Principal(email=str(who["id"]), credential_id="design-agent", kind="web", scopes=("read",),
                     client_name="AeroStator design assistant")


def _guard(fn, *a):
    try:
        return fn(*a)
    except _run.AgentError as exc:
        msg = str(exc)
        raise HTTPException(404 if "not found" in msg else 409 if "still answering" in msg else 400, detail=msg)


@router.get("/api/design-agent/status")
def status(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    who = caller_identity(authorization)
    allowed = _run.may_use(who)
    out: Dict[str, Any] = {"allowed": allowed, "configured": bool(_run.api_key())}
    if allowed:
        out.update(model=_run.model(), limits=_run.limits(),
                   spent_today_usd=round(_run.spent_today(str(who["id"])), 4))
    return out


@router.get("/api/design-agent/conversations")
def conversations(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    p = _principal(authorization)
    return {"conversations": _run.list_conversations(p.email)}


@router.post("/api/design-agent/conversations")
def new_conversation(body: Dict[str, Any] = Body(default_factory=dict),
                     authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    p = _principal(authorization)
    _pa.require_accepted({"email": p.email, "role": caller_identity(authorization).get("role")})   # 428 until the company accepted
    return _guard(_run.start_turn, p, str(body.get("text") or ""))


@router.get("/api/design-agent/conversations/{cid}")
def get_conversation(cid: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    p = _principal(authorization)
    return _guard(_run.get_conversation, p.email, cid)


@router.post("/api/design-agent/conversations/{cid}/messages")
def send_message(cid: str, body: Dict[str, Any] = Body(default_factory=dict),
                 authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    p = _principal(authorization)
    return _guard(_run.start_turn, p, str(body.get("text") or ""), cid)


@router.delete("/api/design-agent/conversations/{cid}")
def delete_conversation(cid: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    p = _principal(authorization)
    _guard(_run.delete_conversation, p.email, cid)
    return {"deleted": cid}


@router.get("/api/admin/design-agent/usage")
def usage(_admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    return _run.usage_summary()
