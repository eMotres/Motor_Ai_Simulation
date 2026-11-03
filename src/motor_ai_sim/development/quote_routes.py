"""The customer's read-only quote of a project (owner 2026-11-03).

``GET /api/projects/{id}/quote``   the quote an admin SHARED (``POST /api/admin/projects/{id}/cost-estimate/share``), built
                                   by ``cost_quote.build_quote`` from the frozen snapshot of that share - never from the live
                                   estimate, so admin edits after sharing stay invisible until the next share.

Access (commercial data, so the strictest rule of the project module):

* members of THAT project (any project role) and global admins only.  A global engineer who is not a member, a member of
  another project, the factory (OEM) of a production: 404 "Quote not found", the same answer as an unknown project or a
  project with nothing shared, so the answer never reveals whether the project exists;
* no session: 401;
* an MCP agent (an in-process call acting for its key owner): 403, whoever the owner is (the NO-PRICE RULE of the MCP);
* ``Cache-Control: no-store``."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response

from motor_ai_sim import auth
from motor_ai_sim.development import access, store
from motor_ai_sim.development import cost_estimate as CE
from motor_ai_sim.development import cost_estimate_store as CS
from motor_ai_sim.development import cost_quote as Q

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["development-quote"])

_NOT_FOUND = "Quote not found"
_NO_STORE = {"Cache-Control": "no-store"}


def _refuse(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail, headers=_NO_STORE)


def can_read_quote(user: Optional[dict], project: dict) -> bool:
    """A global admin, or a member of this project (any role).  Never a global engineer who is not a member."""
    if not user:
        return False
    if str(user.get("role") or "").lower() == "admin":
        return True
    return access.membership(str(user.get("email") or ""), project) is not None


def quote_shared(user: Optional[dict], project: dict) -> bool:
    """The only thing the project payload says about the quote: ``True`` when ``user`` may read it and one is shared."""
    try:
        return can_read_quote(user, project) and CS.load_shared(str(project.get("id") or "")) is not None
    except Exception:                                   # noqa: BLE001 - a flag never breaks the project payload
        log.warning("quote flag failed for %s", project.get("id"), exc_info=True)
        return False


@router.get("/{project_id}/quote")
def get_quote(project_id: str, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if auth._AGENT_USER.get() is not None:
        raise _refuse(403, "The quote is not available to agents.")
    try:
        user = access.require_signed_in(request)
    except HTTPException as exc:
        raise _refuse(exc.status_code, str(exc.detail)) from None
    project = store.get_project(project_id) if store.valid_id(project_id) else None
    if project is None or not can_read_quote(user, project):
        raise _refuse(404, _NOT_FOUND)
    mark = CS.load_shared(project_id)
    snap = CS.get_snapshot(project_id, mark["snapshot_n"]) if mark else None
    if not mark or not snap or not isinstance(snap.get("estimate"), dict):
        raise _refuse(404, _NOT_FOUND)
    schema = CE.load_schema()
    estimate = CE.from_stored(snap["estimate"], schema, project_id)
    try:
        return Q.build_quote(estimate, schema, mark["shared_at"])
    except Q.QuoteError:
        log.warning("shared quote of %s (snapshot %s) cannot be built", project_id, mark["snapshot_n"])
        raise _refuse(404, _NOT_FOUND) from None
