"""Two regions, stage 2 (docs/regions/DESIGN.md sections 7, 9): the catalog extracts and the cross-region compute hand-over.

PEER (server-to-server, HMAC-signed like every ``/api/peer/v1`` route: 404 for anything unsigned and in single-region mode)
  GET  /api/peer/v1/catalog/extracts   the per-die extracts of the shared ``.duty_results.json``, ``end_effect_passports.json``
                                       and ``die_access.json`` for the mirrored dies (EU only; ``regions/catalog_manifest``)
  POST /api/peer/v1/foreign/submit     the computing region takes one anonymised job (``regions/foreign.accept``)
  POST /api/peer/v1/foreign/result     the home region receives the result of a job it sent out (``foreign.receive_result``)

The foreign routes answer ``{ok: false, code}`` and never an error page: the other server then runs the job at home or retries.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException

from motor_ai_sim.regions import config as RC
from motor_ai_sim.regions import peer as P
from motor_ai_sim.routes.regions import require_peer

router = APIRouter(tags=["regions"])


@router.get(P.PREFIX + "/catalog/extracts")
def peer_catalog_extracts(_peer: str = Depends(require_peer)) -> Dict[str, Any]:
    if RC.local_region() != "eu":
        raise HTTPException(status_code=404, detail="Not Found")      # only the authoring region publishes the library
    from motor_ai_sim.regions import catalog_manifest as CM
    return CM.build_all()[1]


@router.post(P.PREFIX + "/foreign/submit")
def peer_foreign_submit(payload: Dict[str, Any] = Body(default={}), _peer: str = Depends(require_peer)) -> Dict[str, Any]:
    from motor_ai_sim.regions import foreign as F
    return F.accept(payload)


@router.post(P.PREFIX + "/foreign/result")
def peer_foreign_result(payload: Dict[str, Any] = Body(default={}), _peer: str = Depends(require_peer)) -> Dict[str, Any]:
    from motor_ai_sim.regions import foreign as F
    return F.receive_result(payload)
