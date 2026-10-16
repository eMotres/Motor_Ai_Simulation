"""Time each signed-in user works with the system.

``POST /api/activity/heartbeat``  - the web app, signed-in users only (see ``activity.py``
for what is counted).  ``GET /api/admin/activity`` and ``GET /api/admin/activity/user`` -
Admin only (``require_admin``).  Only the tab id is ever accepted from the client; the
server clock decides how much time a beat is worth.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from motor_ai_sim import activity as AC
from motor_ai_sim.auth import require_admin, resolve_user

router = APIRouter(prefix="/api/activity", tags=["activity"])
admin_router = APIRouter(prefix="/api/admin/activity", tags=["admin"])


@router.post("/heartbeat")
def heartbeat(body: dict = Body(default={}), authorization: Optional[str] = Header(default=None)):
    user = resolve_user(authorization)
    email = str((user or {}).get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Sign in required.")
    # the client timestamp (body["ts"]) is deliberately ignored: server time is authoritative
    return {"ok": True, **AC.record_beat(email, (body or {}).get("sid"), (body or {}).get("tab"))}


@admin_router.get("")
def activity_summary(_admin: dict = Depends(require_admin)):
    """Per account: active seconds today / 7 d / 30 d, sessions, last seen, top tabs, runs."""
    return {"users": AC.summaries()}


@admin_router.get("/user")
def activity_user(email: str = Query(..., min_length=3, max_length=254),
                  days: int = Query(30, ge=1, le=396), _admin: dict = Depends(require_admin)):
    return {"user": email.strip().lower(), "days": AC.user_days(email, days)}
