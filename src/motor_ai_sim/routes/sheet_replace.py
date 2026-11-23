"""``/api/die-sheet``: replace the SHEET of one existing shared die (``motor_ai_sim.sheet_replace``; owner 2026-11-23, one die per size
and pole count).  A verified admin only.  ``POST /{die}/plan`` is the dry run (changes nothing; ``text`` is what the owner reads);
``POST /{die}/apply`` writes - only with ``confirm`` = the die's name - under the lifecycle lock, after a backup copy, and marks
every stored result of the die's configurations "computed on the previous sheet"."""
from __future__ import annotations

import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from motor_ai_sim import sheet_replace as DS
from motor_ai_sim.routes import family as F

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/die-sheet", tags=["die-sheet"])


class SheetRequest(BaseModel):
    sheet: Dict[str, float]
    confirm: str = ""


def _admin(w: dict) -> str:
    return F._require_verified_admin(w) or "local-dev"


@router.post("/{die}/plan")
def sheet_plan(die: str, req: SheetRequest, w: dict = Depends(F.require_catalog_write)):
    _admin(w)
    die = F._check_name(die, "die")
    try:
        p = DS.plan(die, req.sheet)
    except DS.SheetRefused as exc:
        raise HTTPException(422, detail=str(exc))
    except Exception as exc:                                # noqa: BLE001 - a store that cannot be read: nothing was changed
        log.error("sheet_replace: plan of %s failed (%s)", die, exc)
        raise HTTPException(503, detail="The die cannot be checked right now; nothing was changed")
    return {**p, "text": DS.text(p)}


@router.post("/{die}/apply")
def sheet_apply(die: str, req: SheetRequest, w: dict = Depends(F.require_catalog_write)):
    by = _admin(w)
    die = F._check_name(die, "die")
    if req.confirm != die:
        raise HTTPException(422, detail="confirm with the die's exact name (run the plan first)")
    try:
        out = DS.apply(die, req.sheet, by)
    except DS.SheetRefused as exc:
        raise HTTPException(409, detail=str(exc))
    log.warning("sheet_replace: sheet of '%s' replaced by %s (%s -> %s); backup %s", die, by, out["previous_hash"], out["new_hash"],
                out["backup"])
    return {**out, "text": DS.text(out)}
