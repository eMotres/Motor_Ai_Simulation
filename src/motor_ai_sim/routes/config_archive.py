"""``/api/config-archive``: archive ONE configuration of a die that is not needed any more (``motor_ai_sim.config_archive``).

* ``GET  ""``                         every archived configuration (``?die=`` one die): the die view's "Archived" group
* ``GET  /{die}/{cfg}``               can it be archived now: whether it is archived, the refusals, its archive history
* ``POST /{die}/{cfg}/archive``       ``{"reason"}``: archive it (refused while a project links it, it has a Production record,
                                      or a job runs on it: 409 ``config.in_use``, in words)
* ``POST /{die}/{cfg}/restore``       undo

A verified signed-in admin only.  Nothing on disk is touched; every change is a history row in the archive store (same write)
and an admin-audit entry.  A non-admin never reaches these routes and sees an archived configuration as a missing one.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from motor_ai_sim import config_archive as CA
from motor_ai_sim.routes import family as F

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config-archive", tags=["config-archive"])


class ArchiveRequest(BaseModel):
    reason: str = Field(default="", max_length=CA.REASON_MAX)


def _admin(w: dict) -> str:
    return F._require_verified_admin(w) or "local-dev"


def _existing(die: str, cfg: str) -> tuple:
    die = F._check_name(die, "die")
    cfg = F._check_name(cfg, "configuration")
    try:
        path = F._cfg_file(die, cfg)
    except HTTPException:
        raise HTTPException(404, detail=f"configuration not found: {cfg}.yaml")
    if not path.is_file():
        raise HTTPException(404, detail=f"configuration not found: {cfg}.yaml")
    return die, cfg


@router.get("")
def list_archived(die: Optional[str] = None, w: dict = Depends(F.require_catalog_write)):
    _admin(w)
    return {"configs": [{**row, "duty": _first_duty(row["die"], row["config"])}
                        for row in CA.listing(F._check_name(die, "die") if die else None)]}


def _first_duty(die: str, cfg: str) -> Optional[str]:
    """The duty the die view's "Open" loads (the configuration's first one); None when it has none or the file is gone."""
    try:
        import yaml
        doc = yaml.safe_load(F._cfg_file(die, cfg).read_text(encoding="utf-8")) or {}
        for d in doc.get("duties") or []:
            if isinstance(d, dict) and d.get("name"):
                return str(d["name"])
    except Exception:                                       # noqa: BLE001 - a listing, never fatal
        pass
    return None


@router.get("/{die}/{cfg}")
def archive_check(die: str, cfg: str, w: dict = Depends(F.require_catalog_write)):
    _admin(w)
    die, cfg = _existing(die, cfg)
    archived = CA.is_archived(die, cfg)
    return {"die": die, "config": cfg, "archived": archived, "refusals": [] if archived else CA.refusals(die, cfg),
            "history": CA.history(die, cfg)}


@router.post("/{die}/{cfg}/archive")
def archive_config(die: str, cfg: str, req: ArchiveRequest, w: dict = Depends(F.require_catalog_write)):
    from motor_ai_sim import admin_audit as _AA
    from motor_ai_sim.api_errors import ApiError
    by = _admin(w)
    die, cfg = _existing(die, cfg)
    if CA.is_archived(die, cfg):
        raise ApiError(409, "config.already_archived", f"'{die}/{cfg}' is archived already", params={"die": die, "config": cfg})
    refusals = CA.refusals(die, cfg)
    if refusals:
        raise ApiError(409, "config.in_use", CA.refusal_text(die, cfg, refusals),
                       params={"die": die, "config": cfg, "refusals": refusals})
    rec = CA.archive(die, cfg, by=by, reason=req.reason)
    _AA.record(by, "config.archive", f"{die}/{cfg}", details={"reason": rec["reason"]})
    log.warning("config_archive: '%s/%s' archived by %s", die, cfg, by)
    return {"ok": True, "die": die, "config": cfg, "archived": True, **rec}


@router.post("/{die}/{cfg}/restore")
def restore_config(die: str, cfg: str, w: dict = Depends(F.require_catalog_write)):
    from motor_ai_sim import admin_audit as _AA
    by = _admin(w)
    die, cfg = _existing(die, cfg)
    if not CA.restore(die, cfg, by=by):
        raise HTTPException(404, detail=f"'{die}/{cfg}' is not archived")
    _AA.record(by, "config.unarchive", f"{die}/{cfg}")
    log.warning("config_archive: '%s/%s' restored by %s", die, cfg, by)
    return {"ok": True, "die": die, "config": cfg, "archived": False}
