"""MOTRES ERP pulls the companies catalogue and the production motor prices from here (owner 2026-10-18).

GET /api/erp-export/directory      companies (with their people and supplier documents)
GET /api/erp-export/motor-prices   per production: cost and factory price per motor

Read-only.  Authorised by the dedicated service token ``ERP_SYNC_TOKEN`` (bearer; it opens these two routes and
nothing else) or by an admin session (to look at what the ERP receives).
"""
from __future__ import annotations

import hmac
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header

from motor_ai_sim import erp_export as _x
from motor_ai_sim.auth import require_admin

router = APIRouter(prefix="/api/erp-export", tags=["erp-export"])


def _authorise(authorization: Optional[str]) -> None:
    token = (os.environ.get("ERP_SYNC_TOKEN") or "").strip()
    if token and isinstance(authorization, str):
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and hmac.compare_digest(parts[1].strip(), token):
            return
    require_admin(authorization)          # 401 anonymous, 403 not an admin


def _server_data(fn):
    """The catalogue and the productions are server data: read them in the process workspace, whatever workspace the
    request was given (a service-token request counts as anonymous and would otherwise see an empty layer)."""
    from motor_ai_sim import workspace as _ws
    with _ws.use_workspace(_ws.process_workspace()):
        return fn()


@router.get("/directory")
def directory(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _authorise(authorization)
    return _server_data(_x.directory_export)


@router.get("/motor-prices")
def motor_prices(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _authorise(authorization)
    return _server_data(_x.motor_prices_export)

