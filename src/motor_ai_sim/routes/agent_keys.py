"""Access for agents — the signed-in account's own MCP keys (Stage 1; Stage 3
adds the ``designs:write`` / ``simulate`` scopes and their descriptions).

GET    /api/agent_keys           my keys (never the secret)
POST   /api/agent_keys           {name, scopes?} -> {token, key}; token shown ONCE
DELETE /api/agent_keys/{id}      revoke
GET    /api/agent_keys/audit     my last MCP calls
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from motor_ai_sim import agent_keys as _keys
from motor_ai_sim.auth import ANON_OWNER, caller_identity

router = APIRouter(prefix="/api/agent_keys", tags=["agent-keys"])


def _owner(authorization: Optional[str]) -> str:
    who = caller_identity(authorization)
    ident = str(who.get("id") or "")
    if not ident or ident == ANON_OWNER:
        raise HTTPException(401, detail="Sign in to manage agent keys.")
    return ident


class KeyCreate(BaseModel):
    name: str = ""
    scopes: Optional[List[str]] = None


@router.get("")
def list_my_keys(authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    from motor_ai_sim import agent_designs as _ad
    sim_day = _ad.daily_limit(_keys.Principal(email=owner, credential_id=""))
    return {"keys": _keys.list_keys(owner), "scopes": list(_keys.SCOPES),
            "default_scopes": list(_keys.DEFAULT_SCOPES),
            "scope_descriptions": dict(_keys.SCOPE_DESCRIPTIONS),
            "limits": {"per_minute": _keys.per_minute_limit(),
                       "per_day": _keys.per_day_limit(),
                       "simulations_per_day": sim_day}}


@router.post("")
def create_my_key(req: KeyCreate, authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    try:
        token, rec = _keys.create_key(owner, req.name, req.scopes)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    return {"token": token, "key": rec,
            "note": "copy the token now — it is not stored and cannot be shown again"}


@router.delete("/{kid}")
def revoke_my_key(kid: str, authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    if not _keys.revoke_key(owner, kid):
        raise HTTPException(404, detail="no such active key")
    return {"revoked": kid}


@router.get("/audit")
def my_audit(limit: int = 100, authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    return {"calls": _keys.read_audit(owner, limit=max(1, min(limit, 500)))}
