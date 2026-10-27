"""GET /api/messages/unread - the unread-message counts the main menu polls (owner 2026-10-28).

One cheap read for the signed-in caller, over exactly the conversations the caller may open (the project list rule for projects,
the production list rule plus the OEM NDA gate for productions).  Nothing is marked read here: reading happens on the thread
routes (``POST /api/projects/{id}/thread/read``, ``POST /api/production/{die}/{config}/threads/{thread}/read``).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from motor_ai_sim.development import access as dev_access, unread as dev_unread
from motor_ai_sim.production import access as prod_access, unread as prod_unread
from motor_ai_sim.production.routes import _production_list_for

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.get("/unread")
def unread_counts(request: Request):
    """``{"projects": {id: n}, "productions": {"die/config": n}, "total_projects": N, "total_productions": N}``; only the
    conversations that have unread messages are listed."""
    user = dev_access.require_signed_in(request)
    email = str(user.get("email") or "").strip().lower()
    try:
        projects = dev_unread.counts_for(user)
    except Exception:                                       # noqa: BLE001 - the menu badge must not break the page
        log.warning("project unread counts failed", exc_info=True)
        projects = {}
    try:
        rows = _production_list_for(email, prod_access.is_admin(user))
        productions = prod_unread.counts_for(email, rows, lambda rec: prod_access.full_view_denial(request, user, rec) is None)
    except Exception:                                       # noqa: BLE001
        log.warning("production unread counts failed", exc_info=True)
        productions = {}
    return {"projects": projects, "productions": productions,
            "total_projects": sum(projects.values()), "total_productions": sum(productions.values())}
