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
    reason: str = Field(..., max_length=CA.REASON_MAX)


def _admin(w: dict) -> str:
    return F._require_verified_admin(w) or "local-dev"


def _existing(die: str, cfg: str) -> tuple:
    """``(die, cfg)`` as STORED: a configuration of a SHARED-catalog die, by the exact file name on disk (a case alias of the
    request resolves to the stored spelling, so the archive key is the one every reader asks).  404 when there is none; 409
    for a die of another layer (a workspace or published copy is not archived here)."""
    from pathlib import Path
    from motor_ai_sim.api_errors import ApiError
    die = F._check_name(die, "die")
    cfg = F._check_name(cfg, "configuration")
    entry = next((e for e in F._iter_die_entries() if str(e["name"]) == die), None)
    if entry is None:
        raise HTTPException(404, detail=f"configuration not found: {cfg}.yaml")
    if str(entry.get("layer") or "") != "shared":
        raise ApiError(409, "config.archive_shared_only", (
            f"'{die}' is not a shared catalog die: only configurations of the shared catalog are archived"), params={"die": die})
    names = {p.stem: p for p in Path(str(entry["dir"])).glob("*.yaml") if p.name != "die.yaml"}
    stored = cfg if cfg in names else next((n for n in names if n.casefold() == cfg.casefold()), None)
    if stored is None:
        raise HTTPException(404, detail=f"configuration not found: {cfg}.yaml")
    return die, stored


def _unavailable(exc: Exception) -> HTTPException:
    log.error("config_archive: cannot decide (%s)", exc)
    return HTTPException(503, detail="The configuration archive cannot be checked right now; nothing was changed")


@router.get("")
def list_archived(die: Optional[str] = None, w: dict = Depends(F.require_catalog_write)):
    _admin(w)
    try:
        rows = CA.listing(F._check_name(die, "die") if die else None)
    except CA.ArchiveUnavailable as exc:
        raise _unavailable(exc)
    return {"configs": [{**row, "duty": _first_duty(row["die"], row["config"])} for row in rows]}


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
    try:
        keys = CA.archived_keys()
        archived = f"{die}/{cfg}" in keys
        return {"die": die, "config": cfg, "archived": archived, "refusals": [] if archived else CA.refusals(die, cfg),
                "history": CA.history(die, cfg)}
    except Exception as exc:                                # noqa: BLE001 - a store or a source that cannot be read
        raise _unavailable(exc)


@router.post("/{die}/{cfg}/archive")
def archive_config(die: str, cfg: str, req: ArchiveRequest, w: dict = Depends(F.require_catalog_write)):
    from motor_ai_sim import admin_audit as _AA
    from motor_ai_sim.api_errors import ApiError
    by = _admin(w)
    die, cfg = _existing(die, cfg)
    reason = " ".join(str(req.reason or "").split())
    if not reason:
        raise HTTPException(422, detail="a reason is required")
    # the refusal and the archive under ONE lock, which a project link and a production creation also take
    with CA.lifecycle_lock():
        try:
            if f"{die}/{cfg}" in CA.archived_keys():
                raise ApiError(409, "config.already_archived", f"'{die}/{cfg}' is archived already", params={"die": die, "config": cfg})
            refusals = CA.refusals(die, cfg)
        except ApiError:
            raise
        except Exception as exc:                            # noqa: BLE001 - a source that cannot be read is no proof of no use
            raise _unavailable(exc)
        if refusals:
            raise ApiError(409, "config.in_use", CA.refusal_text(die, cfg, refusals),
                           params={"die": die, "config": cfg, "refusals": refusals})
        try:
            rec = CA.archive(die, cfg, by=by, reason=reason)
        except CA.ArchiveUnavailable as exc:
            raise _unavailable(exc)
    _AA.record(by, "config.archive", f"{die}/{cfg}", details={"reason": rec["reason"]})
    log.warning("config_archive: '%s/%s' archived by %s", die, cfg, by)
    return {"ok": True, "die": die, "config": cfg, "archived": True, **rec}


@router.post("/{die}/{cfg}/restore")
def restore_config(die: str, cfg: str, w: dict = Depends(F.require_catalog_write)):
    from motor_ai_sim import admin_audit as _AA
    by = _admin(w)
    die, cfg = _existing(die, cfg)
    try:
        hit = CA.restore(die, cfg, by=by)
    except CA.ArchiveUnavailable as exc:
        raise _unavailable(exc)
    if not hit:
        raise HTTPException(404, detail=f"'{die}/{cfg}' is not archived")
    _AA.record(by, "config.unarchive", f"{die}/{cfg}")
    log.warning("config_archive: '%s/%s' restored by %s", die, cfg, by)
    return {"ok": True, "die": die, "config": cfg, "archived": False}
