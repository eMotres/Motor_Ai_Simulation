# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Component library routes - /api/components (read-only).

    GET /api/components?kind=      the cards the stores can produce, one summary row each
    GET /api/components/{id}       one ComponentCard (JSON)

Engineer and admin only (``auth._GATED``: the Simulation menus' audience).  Nothing here
solves or writes anything; the cards are built by the adapters from the existing stores
(``modules/component_library.py``).  A motor card is performance + mass + outer envelope
only, and a motor of a die the caller may not see (``motor_access.may_see_die``) is neither
listed nor served (404).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from motor_ai_sim import motor_access as _ma
from motor_ai_sim.modules import component_library as _lib

router = APIRouter(prefix="/api/components", tags=["components"])

KINDS = ("motor", "propeller", "transmission", "controller", "battery", "frame", "link", "wheel", "other")


def _visible(entry: _lib.Entry, access: dict) -> bool:
    return entry.die is None or _ma.may_see_die(access, entry.die)


@router.get("")
def list_components(kind: Optional[str] = Query(None, description="|".join(KINDS)),
                    authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=400, detail={"error": f"unknown kind {kind!r}", "kinds": list(KINDS)})
    lib = _lib.get_library()
    access = _ma.catalog_access(authorization)
    rows = [_lib.summary(e) for e in lib.entries.values()
            if (kind is None or e.card.kind == kind) and _visible(e, access)]
    return {"components": rows, "count": len(rows), "kinds": list(KINDS),
            "rejected": list(lib.rejected) if access.get("is_admin") else []}


@router.get("/{component_id}")
def get_component(component_id: str, authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    lib = _lib.get_library()
    entry = lib.entries.get(component_id)
    if entry is None or not _visible(entry, _ma.catalog_access(authorization)):
        raise HTTPException(status_code=404, detail={"error": f"no component {component_id!r}"})
    return {"card": entry.card.model_dump(mode="json"), "flags": list(entry.flags)}
