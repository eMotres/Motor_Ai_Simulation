"""``/api/die-successor``: a new sheet for a die that has results is a SUCCESSOR die (``motor_ai_sim.successor_die``; owner
2026-11-23).  A verified admin only.  ``plan`` / ``restore/plan`` change nothing (``text`` is what the owner reads); ``apply`` /
``restore`` act only with ``confirm`` = the die's name, and ``apply`` only with the other host's ``content_hash`` of the same plan.
``GET /{die}/id`` is the die record's stable id for agent saves (record it when the job starts, send it with the save)."""
from __future__ import annotations

import logging
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from motor_ai_sim import successor_die as SD
from motor_ai_sim.routes import family as F

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/die-successor", tags=["die-successor"])


class SwitchRequest(BaseModel):
    successor: str
    retired: str
    stays: List[str] = Field(default_factory=list)
    peer_content_hash: str = ""
    confirm: str = ""


class RestoreRequest(BaseModel):
    successor_name: str            # what the successor was called before the switch
    retired: str
    aside: Dict[str, str] = Field(default_factory=dict)
    confirm: str = ""


def _admin(w: dict) -> str:
    return F._require_verified_admin(w) or "local-dev"


def _names(die: str, *more: str) -> list:
    return [F._check_name(die, "die")] + [F._check_name(m, "die") for m in more]


@router.get("/{die}/id")
def die_record_id(die: str, w: dict = Depends(F.require_catalog_write)):
    return {"die": F._check_name(die, "die"), "die_id": SD.die_id(die)}


@router.post("/{die}/plan")
def switch_plan(die: str, req: SwitchRequest, w: dict = Depends(F.require_catalog_write)):
    _admin(w)
    die, succ, retired = _names(die, req.successor, req.retired)
    try:
        p = SD.plan(die, succ, retired, stays=tuple(req.stays))
    except SD.SwitchRefused as exc:
        raise HTTPException(422, detail=str(exc))
    return {**p, "text": SD.text(p)}


@router.post("/{die}/apply")
def switch_apply(die: str, req: SwitchRequest, w: dict = Depends(F.require_catalog_write)):
    by = _admin(w)
    die, succ, retired = _names(die, req.successor, req.retired)
    if req.confirm != die:
        raise HTTPException(422, detail="confirm with the die's exact name (run the plan on both hosts first)")
    try:
        out = SD.apply(die, succ, retired, actor=by, peer_content_hash=req.peer_content_hash, w=w, stays=tuple(req.stays))
    except SD.SwitchRefused as exc:
        raise HTTPException(409, detail=str(exc))
    log.warning("successor_die: '%s' switched to '%s' (retired as '%s') by %s", die, succ, retired, by)
    return out


@router.post("/{die}/restore/plan")
def restore_plan(die: str, req: RestoreRequest, w: dict = Depends(F.require_catalog_write)):
    _admin(w)
    die, succ, retired = _names(die, req.successor_name, req.retired)
    try:
        return SD.restore_plan(die, succ, retired)
    except SD.SwitchRefused as exc:
        raise HTTPException(422, detail=str(exc))


@router.post("/{die}/restore")
def restore(die: str, req: RestoreRequest, w: dict = Depends(F.require_catalog_write)):
    by = _admin(w)
    die, succ, retired = _names(die, req.successor_name, req.retired)
    if req.confirm != die:
        raise HTTPException(422, detail="confirm with the die's exact name (run the restore plan first)")
    try:
        return SD.restore(die, succ, retired, actor=by, aside=req.aside, w=w)
    except SD.SwitchRefused as exc:
        raise HTTPException(409, detail=str(exc))
