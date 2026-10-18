"""Live status for module-wise deploys (owner 2026-10-18), see motor_ai_sim.live_status.

GET    /api/admin/live      who is in which module now, requests in flight, jobs; admin or a direct loopback call.
                            In the API process of a two-process deployment (stage 2) the compute worker's jobs and
                            requests are merged in, and ``worker`` says what it is doing; ``?local=1`` = this process only
                            (what deploy/bluegreen.sh asks the OLD colour while it drains)
GET    /api/notice          the maintenance banner, if one is up (public: every page shows it)
PUT    /api/admin/notice    {message: {en, zh}, minutes}  admin or loopback (the deploy script)
DELETE /api/admin/notice
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Request

from motor_ai_sim import live_status as L
from motor_ai_sim.auth import caller_identity

router = APIRouter(tags=["live"])

_LOOPBACK = {"127.0.0.1", "::1"}


def _allowed(request: Request, authorization: Optional[str]) -> bool:
    """An admin, or the deploy script calling from inside the container (loopback, no proxy headers)."""
    host = getattr(request.client, "host", "") if request.client else ""
    if host in _LOOPBACK and not request.headers.get("x-forwarded-for") and not request.headers.get("x-real-ip"):
        return True
    return bool(caller_identity(authorization).get("is_admin"))


def _require(request: Request, authorization: Optional[str]) -> None:
    if not _allowed(request, authorization):
        raise HTTPException(403, detail="admin only")


@router.get("/api/admin/live")
def live(request: Request, local: bool = False,
         authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require(request, authorization)
    is_admin = bool(caller_identity(authorization).get("is_admin"))
    return L.local_snapshot(include_users=is_admin) if local else L.snapshot(include_users=is_admin)


@router.get("/api/notice")
def notice() -> Dict[str, Any]:
    return {"notice": L.read_notice()}


@router.put("/api/admin/notice")
def put_notice(request: Request, body: Dict[str, Any] = Body(default_factory=dict),
               authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require(request, authorization)
    msg = body.get("message") if isinstance(body.get("message"), dict) else {"en": str(body.get("message") or "")}
    return {"notice": L.set_notice(msg, float(body.get("minutes") or 5))}


@router.delete("/api/admin/notice")
def delete_notice(request: Request, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require(request, authorization)
    L.clear_notice()
    return {"notice": None}
