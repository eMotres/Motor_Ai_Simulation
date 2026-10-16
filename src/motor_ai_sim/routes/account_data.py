"""The account holder's own data rights: export, deletion, who looked.

GET    /api/account/deletion          pending deletion request (or null) + grace days
POST   /api/account/deletion          {password? | google_credential?} -> schedule
DELETE /api/account/deletion          cancel a pending request
POST   /api/account/export            -> {url, expires_at}: signed, single-use link
GET    /api/account/export/download   ?token=…  the ZIP (no session needed: the
                                      token IS the credential, 15 min, one use)
GET    /api/account/admin_access      admin actions that touched my account
                                      (the break-glass record, audit 2026-09-29 #8)

Admin side (require_admin; every call lands in the admin audit):
DELETE /api/admin/accounts/{email}           purge now (no grace)
POST   /api/admin/accounts/{email}/export    break-glass export link, logged
GET    /api/admin/accounts/deletions         pending self-service requests
GET    /api/admin/audit                      the admin audit log
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from motor_ai_sim import account_lifecycle as AL
from motor_ai_sim import admin_audit as AA
from motor_ai_sim.auth import require_admin, resolve_user_detail

log = logging.getLogger(__name__)

router = APIRouter(tags=["account-data"])

EXPORT_DOWNLOAD_PATH = "/api/account/export/download"


def _me(authorization: Optional[str]) -> str:
    det = resolve_user_detail(authorization)
    user = det.get("user")
    if not user or not user.get("email"):
        if det.get("reason") == "store_unavailable":
            raise HTTPException(503, detail="account store unavailable, retry")
        raise HTTPException(401, detail="Sign in first.")
    return str(user["email"]).strip().lower()


class DeletionReq(BaseModel):
    password: Optional[str] = None
    google_credential: Optional[str] = None


@router.get("/api/account/deletion")
def deletion_status(authorization: Optional[str] = Header(default=None)):
    me = _me(authorization)
    return {"pending": AL.pending(me), "grace_days": AL.grace_days()}


@router.post("/api/account/deletion")
def request_deletion(req: DeletionReq, authorization: Optional[str] = Header(default=None)):
    """Schedule MY account for deletion after the grace period.  Requires
    re-authentication: a stolen session alone must not be able to erase an
    account."""
    me = _me(authorization)
    if not AL.reauthenticate(me, password=req.password,
                             google_credential=req.google_credential):
        raise HTTPException(403, detail="Re-authentication failed: enter your "
                                        "password (or sign in with Google again).")
    rec = AL.request_deletion(me, by="self")
    return {"ok": True, "pending": rec, "grace_days": AL.grace_days()}


@router.delete("/api/account/deletion")
def cancel_deletion(authorization: Optional[str] = Header(default=None)):
    me = _me(authorization)
    return {"ok": True, "cancelled": AL.cancel_deletion(me)}


@router.post("/api/account/export")
def export_link(authorization: Optional[str] = Header(default=None)):
    me = _me(authorization)
    t = AL.issue_export_token(me)
    return {"url": f"{EXPORT_DOWNLOAD_PATH}?token={t['token']}",
            "expires_at": t["expires_at"]}


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@router.get(EXPORT_DOWNLOAD_PATH)
def export_download(background: BackgroundTasks, token: str = Query(...)):
    claims = AL.consume_export_token(token)
    if claims is None:
        raise HTTPException(403, detail="This export link is invalid, expired or "
                                        "already used — request a new one.")
    email = claims["sub"]
    if claims.get("adm"):
        AA.record(claims["adm"], "workspace.export", AL.subject_hash(email),
                  subject=email, break_glass=True)
    try:
        path = AL.build_export(email)
    except ValueError as exc:
        raise HTTPException(413, detail=str(exc))
    background.add_task(_unlink, str(path))
    return FileResponse(str(path), media_type="application/zip",
                        filename="emotres-data-export.zip")


@router.get("/api/account/admin_access")
def admin_access(authorization: Optional[str] = Header(default=None)):
    me = _me(authorization)
    rows = AA.for_subject(me, limit=500)
    return {"count": len(rows), "entries": [
        {k: r.get(k) for k in ("iso", "ts", "actor", "action", "break_glass")}
        for r in rows]}


# ── admin ────────────────────────────────────────────────────────────────────

def _actor(admin: dict) -> str:
    return AA.actor_of(admin)


@router.delete("/api/admin/accounts/{email}")
def admin_purge(email: str, admin: dict = Depends(require_admin)):
    from motor_ai_sim import users as U
    em = email.strip().lower()
    if U.get_user(em) is None and AL.pending(em) is None:
        raise HTTPException(404, detail=f"user '{em}' not found")
    return AL.purge(em, actor=_actor(admin), reason="admin")


@router.post("/api/admin/accounts/{email}/export")
def admin_export(email: str, admin: dict = Depends(require_admin)):
    """Break-glass: an admin exports another person's data.  Logged twice —
    when the link is minted and when it is downloaded — and visible to the
    person in GET /api/account/admin_access."""
    em = email.strip().lower()
    AA.record(_actor(admin), "workspace.read", AL.subject_hash(em), subject=em,
              break_glass=True, details={"via": "admin export link"})
    t = AL.issue_export_token(em, for_admin=_actor(admin))
    return {"url": f"{EXPORT_DOWNLOAD_PATH}?token={t['token']}",
            "expires_at": t["expires_at"]}


@router.get("/api/admin/accounts/deletions")
def admin_deletions(admin: dict = Depends(require_admin)):
    AA.record(_actor(admin), "audit.read", "account_deletions")
    return {"grace_days": AL.grace_days(), "pending": AL.list_pending()}


@router.get("/api/admin/audit")
def admin_audit_log(limit: int = 200, actor: str = "", action: str = "",
                    subject: str = "", admin: dict = Depends(require_admin)):
    rows = AA.read(max(1, min(int(limit), 2000)), actor=actor, action=action,
                   subject=subject)
    return {"count": len(rows), "entries": rows}
