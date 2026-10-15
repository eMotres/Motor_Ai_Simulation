"""Reference catalogue cards — /api/catalog/cards (unified catalogues, stage 1).

Read for everybody, write for admins only (owner decision 2026-09-28):

  GET  /api/catalog/cards                        kinds and counts
  GET  /api/catalog/cards/{kind}                 one summary row per card
  GET  /api/catalog/cards/{kind}/card?id=...     one card, whole envelope
  GET  /api/catalog/cards/{kind}/used_by?id=...  machines that name it
  POST /api/catalog/cards/device                 add/replace a device card (ADMIN)

``id`` is a QUERY parameter because bearing ids carry slashes
(``618/8-2Z``, ``71910 CE/HCP4A``).  Bearings and lubricants have no writer
here: ``config/bearings_library.yaml`` is edited in git (by an admin), exactly
as before.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from motor_ai_sim import catalog as _cat
from motor_ai_sim.auth import require_admin
from motor_ai_sim.catalog.used_by import used_by as _used_by

router = APIRouter(prefix="/api/catalog/cards", tags=["catalog-cards"])


def _kind(kind: str) -> str:
    if kind not in _cat.KINDS:
        raise HTTPException(status_code=404, detail={
            "error": "unknown_kind",
            "message": f"unknown catalogue kind {kind!r}; known: {', '.join(_cat.KINDS)}"})
    return kind


@router.get("")
def get_kinds() -> Dict[str, Any]:
    return {"kinds": _cat.kinds_summary(), "statuses": list(_cat.STATUSES),
            "prov_types": list(_cat.PROV_TYPES)}


@router.get("/{kind}")
def get_list(kind: str) -> Dict[str, Any]:
    _kind(kind)
    try:
        cards = _cat.list_cards(kind)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"kind": kind, "cards": [_cat.summary(e) for e in cards]}


@router.get("/{kind}/card")
def get_one(kind: str, id: str = Query(..., description="the card id")) -> Dict[str, Any]:
    _kind(kind)
    env = _cat.get_card(kind, id)
    if env is None:
        raise HTTPException(status_code=404, detail={
            "error": "unknown_card", "message": f"no {kind} card {id!r}"})
    return env


@router.get("/{kind}/used_by")
def get_used_by(kind: str, id: str = Query(...),
                authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _kind(kind)
    # A machine the caller may not see - a die they hold no grant for, or a PRIVATE
    # configuration - is not named here (motor_access.may_see_config).
    from motor_ai_sim import motor_access as _ma
    access = _ma.catalog_access(authorization)
    machines = [m for m in _used_by(kind, id)
                if _ma.may_see_config(access, str(m.get("die")), str(m.get("config")))]
    return {"kind": kind, "id": id, "machines": machines}


@router.post("/device")
def post_device(body: Dict[str, Any] = Body(...),
                _admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    """Same writer as ``POST /api/controller/devices`` (validate, then write
    ``config/devices/<part>.yaml``) — admin only."""
    from motor_ai_sim.routes.controller import store_device_card
    return store_device_card(body)
