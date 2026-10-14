"""Consent to share the caller's own data with the AeroStator engineering dataset (see :mod:`motor_ai_sim.data_sharing`).

* ``GET /api/data-sharing/mine``   the caller's consent ``{consent, version, time}`` and the current text version
* ``PUT /api/data-sharing/mine``   ``{"consent": bool}``  give or withdraw it; every change is an appended audit line
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException, Request

from motor_ai_sim import data_sharing as DS
from motor_ai_sim.routes.nda import _caller

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data-sharing", tags=["data-sharing"])


@router.get("/mine")
def mine(authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    if not rec:
        raise HTTPException(status_code=400, detail="This account has no registry record; sign in with an account.")
    return dict(DS.state(who["id"]), current_version=DS.CONSENT_VERSION)


@router.put("/mine")
def set_mine(request: Request, body: dict = Body(default={}), authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    if not rec:
        raise HTTPException(status_code=400, detail="This account has no registry record; sign in with an account.")
    if not isinstance(body.get("consent"), bool):
        raise HTTPException(status_code=422, detail="consent must be true or false")
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        out = DS.set_consent(who["id"], body["consent"], source="account", ip=ip)
    except OSError as e:
        log.error("data-sharing: cannot store the audit line (%s)", e)
        raise HTTPException(status_code=503, detail="The choice could not be stored; nothing was changed. Try again later.")
    from motor_ai_sim import admin_audit as AA
    AA.record(who["id"], "user.data_sharing", who["id"], subject=who["id"],
              details={"consent": out["consent"], "version": out["version"]})
    return dict(out, current_version=DS.CONSENT_VERSION)
