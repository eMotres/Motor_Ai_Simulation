"""Agent drafts in the web (MCP Stage 3) — the owner's view of what his agents
created.  docs/MCP_2026-09-28.md "Stage 3".

GET    /api/agent_designs              my drafts (newest first)
GET    /api/agent_designs/{id}         one draft (Configure loads it from here)
PATCH  /api/agent_designs/{id}         the engineer's parameter edits
POST   /api/agent_designs/{id}/revert  back to exactly what the agent created
DELETE /api/agent_designs/{id}         delete the draft (and its sandbox)

The workspace is the request's (WorkspaceMiddleware), i.e. the signed-in
user's — the same one the agent's calls resolve to.  Nothing here touches the
active machine or the saved catalog.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Header, HTTPException

from motor_ai_sim import agent_designs as _ad
from motor_ai_sim.routes.agent_keys import _owner

router = APIRouter(prefix="/api/agent_designs", tags=["agent-designs"])


def _guard(fn, *a):
    try:
        return fn(*a)
    except _ad.DesignError as e:
        msg = str(e)
        raise HTTPException(404 if "not found" in msg else 400, detail=msg)


@router.get("")
def list_my_drafts(authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    return {"designs": [_ad.web_view(d) for d in _ad.list_designs(owner)]}


@router.get("/{design_id}")
def get_my_draft(design_id: str, authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    return _ad.web_view(_guard(_ad._read, design_id, owner))


@router.patch("/{design_id}")
def edit_my_draft(design_id: str, changes: Dict[str, Any] = Body(default_factory=dict),
                  authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    return _ad.web_view(_guard(_ad.patch_params, design_id, owner, changes))


@router.post("/{design_id}/revert")
def revert_my_draft(design_id: str, authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    return _ad.web_view(_guard(_ad.revert_design, design_id, owner))


@router.delete("/{design_id}")
def delete_my_draft(design_id: str, authorization: Optional[str] = Header(default=None)):
    owner = _owner(authorization)
    _guard(_ad.delete_design, design_id, owner)
    return {"deleted": design_id}
