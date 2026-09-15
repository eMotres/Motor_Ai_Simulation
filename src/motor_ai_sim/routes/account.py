"""Account route — who am I.

GET /api/me lets the frontend learn the signed-in user's tier + admin flag
(the role is only known server-side, from the verified token + ADMIN_EMAILS).
Used to gate the admin page and the full-UI vs configurator split. Open
endpoint: an anonymous caller just gets tier 'anon'.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Request

from motor_ai_sim.auth import account_info

router = APIRouter(tags=["account"])


@router.get("/api/me")
def me(request: Request, authorization: Optional[str] = Header(default=None)):
    """Who is calling, plus WHY they are anonymous when they are.

    The ip / user-agent go in so a rejection can be traced to a browser in
    logs/auth_events.jsonl — "which profile lost the session at 07:40" is not
    answerable without them."""
    ip = request.client.host if request and request.client else ""
    ua = request.headers.get("user-agent", "") if request else ""
    return account_info(authorization, ip=ip, user_agent=ua)
