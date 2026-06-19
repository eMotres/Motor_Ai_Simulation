"""Account route — who am I.

GET /api/me lets the frontend learn the signed-in user's tier + admin flag
(the role is only known server-side, from the verified token + ADMIN_EMAILS).
Used to gate the admin page and the full-UI vs configurator split. Open
endpoint: an anonymous caller just gets tier 'anon'.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header

from motor_ai_sim.auth import account_info

router = APIRouter(tags=["account"])


@router.get("/api/me")
def me(authorization: Optional[str] = Header(default=None)):
    return account_info(authorization)
