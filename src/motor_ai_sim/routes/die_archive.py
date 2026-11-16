"""``/api/die-archive``: archive a die that is not needed any more, in a workspace or published layer (``motor_ai_sim.die_archive``).

* ``GET  ""``                    the archived dies of the caller's workspace (the Overview "Archived" list and the Restore list)
* ``GET  /{die}``                 can it be archived now: its layer, whether it is archived, and the refusals
* ``POST /{die}/archive``         ``{"confirm": <die>, "reason"}``: archive it (refused while it is in use, 409)
* ``POST /{die}/restore``         undo

A verified signed-in admin only.  A SHARED die is not archived here: it keeps its Retire (``/api/family/die/{die}/retire``),
the answer says so (409 ``die.retire_required``).  Nothing on disk is touched; every change goes to the admin audit.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from motor_ai_sim import die_archive as DA
from motor_ai_sim.routes import family as F

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/die-archive", tags=["die-archive"])


class ArchiveRequest(BaseModel):
    confirm: str = ""
    reason: str = Field(default="", max_length=DA.REASON_MAX)


def _admin(w: dict) -> str:
    return F._require_verified_admin(w) or "local-dev"


def _entry(die: str) -> dict:
    """The listing entry of ``die`` as this caller sees it (archived or not: the listing filter is not applied here).

    The first is the right one: ``iter_dies`` lists each name ONCE in read order (another account's published die is
    addressed as ``"<die> · by <name>"``, so it never shares a plain name), and the viewer's own workspace and own
    published copies of one name answer to the same archive key (``die_archive.scope_of_entry``)."""
    for e in F._iter_die_entries():
        if str(e["name"]) == die:
            return e
    raise HTTPException(404, detail=f"die '{die}' not found")


def _configs(e: dict) -> list:
    from pathlib import Path
    return sorted(p.stem for p in Path(str(e["dir"])).glob("*.yaml") if p.name != "die.yaml")


def _scope(e: dict) -> str:
    scope = DA.scope_of_entry(e)
    if scope:
        return scope
    from motor_ai_sim.api_errors import ApiError
    if str(e.get("layer") or "") == "published":
        raise ApiError(409, "die.archive_not_own", (
            f"'{e['name']}' is published by another account: only its owner can archive it"), params={"die": str(e["name"])})
    raise ApiError(409, "die.retire_required", (
        f"'{e['name']}' is a shared catalog die: use 'Retire for all users' (reversible) instead"), params={"die": str(e["name"])})


@router.get("")
def list_archived(w: dict = Depends(F.require_catalog_write)):
    _admin(w)
    from motor_ai_sim.workspace import workspace
    return {"dies": DA.for_scope(str(workspace().id or ""))}


@router.get("/{die}")
def archive_check(die: str, w: dict = Depends(F.require_catalog_write)):
    _admin(w)
    die = F._check_name(die, "die")
    e = _entry(die)
    scope = DA.scope_of_entry(e)
    return {"die": die, "layer": e["layer"], "archivable": bool(scope),
            "archived": bool(scope) and DA.is_archived(e), "refusals": DA.refusals(str(e["die"])) if scope else []}


@router.post("/{die}/archive")
def archive_die(die: str, req: ArchiveRequest, w: dict = Depends(F.require_catalog_write)):
    from motor_ai_sim import admin_audit as _AA
    by = _admin(w)
    die = F._check_name(die, "die")
    e = _entry(die)
    scope = _scope(e)
    if str(req.confirm or "").strip() != die:
        raise HTTPException(422, detail=f"repeat the die name '{die}' to confirm archiving it")
    refusals = DA.refusals(str(e["die"]))
    if refusals:
        from motor_ai_sim.api_errors import ApiError
        raise ApiError(409, "die.in_use", (f"'{die}' cannot be archived: " + "; ".join(
            f"{DA.REFUSAL_TEXT.get(r['code'], r['code'])} ({', '.join(r['names'])})" for r in refusals)),
            params={"die": die, "refusals": refusals})
    cfgs = _configs(e)
    rec = DA.archive(scope, str(e["die"]), layer=str(e["layer"]), by=by, reason=req.reason, configs=cfgs)
    _AA.record(by, "die.archive", die, details={"layer": e["layer"], "scope": scope, "configurations": cfgs,
                                                "reason": rec["reason"]})
    log.warning("die_archive: '%s' (%s, %s) archived by %s", die, e["layer"], scope, by)
    return {"ok": True, "die": die, "archived": True, **rec}


@router.post("/{die}/restore")
def restore_die(die: str, w: dict = Depends(F.require_catalog_write)):
    from motor_ai_sim import admin_audit as _AA
    by = _admin(w)
    die = F._check_name(die, "die")
    e = _entry(die)
    scope = _scope(e)
    if not DA.restore(scope, str(e["die"]), by=by):
        raise HTTPException(404, detail=f"'{die}' is not archived")
    _AA.record(by, "die.unarchive", die, details={"layer": e["layer"], "scope": scope})
    log.warning("die_archive: '%s' (%s, %s) restored by %s", die, e["layer"], scope, by)
    return {"ok": True, "die": die, "archived": False}
