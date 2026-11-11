"""Order requests of a development project (owner 2026-11-11): the "Order this motor" button of Motors & results.

The button only sends a REQUEST to our team.  The app never shows, estimates or discusses prices, costs or lead times: the team
answers outside this flow (full simulation -> agreement -> costs computed -> quote -> payment -> production).

``POST /api/projects/{id}/order-requests`` stores one request in the project record (``order_requests``), posts a system line in
the project thread, writes an admin audit line and sends one internal mail to the team.  Nothing goes to the customer.
``GET`` lists the requests; ``PATCH /{request_id}`` lets staff mark one ``acknowledged`` or ``closed``.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from motor_ai_sim.development import access, store

log = logging.getLogger(__name__)
router = APIRouter(tags=["development-order-requests"])      # included by routes.router (prefix /api/projects)

MAX_NOTE = 2000
MAX_QUANTITY = 1_000_000
ACTIVE = ("open", "acknowledged")


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _key(die: str, config: str) -> str:
    return f"{die.casefold()}/{config.casefold()}"


def _has_results(request: Request, project_id: str, die: str, config: str) -> bool | None:
    """True / False for a variant linked to the project (computed results or not), None when it is not linked.  Uses the same
    answer as the Motors & results list, so the button and the table agree."""
    from motor_ai_sim.development import motors
    wanted = _key(die, config)
    for item in motors.list_project_motors(project_id, request).get("motors", []):
        names = (_key(str(item.get("canonical_die") or ""), str(item.get("config") or "")), _key(str(item.get("die") or ""), str(item.get("config") or "")))
        if wanted in names:
            return bool(item.get("can_open_points") or any(isinstance(r, dict) and r.get("has_result") for r in item.get("results") or []))
    return None


def _label(die: str, config: str) -> str:
    return f"{die} {config}".strip()


def _notify_team(project_id: str, project: dict, rec: dict, user: dict) -> None:
    """Internal only: an admin audit line and one mail to the team address.  Failures never block the customer."""
    try:
        from motor_ai_sim import admin_audit
        admin_audit.record(_email(user), "project.order_request", project_id, subject=_email(user),
                           details={"die": rec["die"], "config": rec["config"], "quantity": rec["quantity"], "request_id": rec["id"]})
    except Exception:                                       # noqa: BLE001
        log.warning("order request audit failed for %s", project_id, exc_info=True)
    try:
        from motor_ai_sim import auth_email as E, nda
        to = nda.admin_address()
        if not to:
            return
        title = project.get("title") or project_id
        lines = ["Order requested", "", f"Project: {title}", f"Company: {project.get('customer_org') or '-'}", f"Account: {_email(user)}",
                 f"Motor: {_label(rec['die'], rec['config'])}", f"Quantity: {rec['quantity']}"]
        if rec["note"]:
            lines += ["", f"Note: {rec['note']}"]
        lines += ["", f"{E.app_url()}/projects/{project_id}/specification", ""]
        E.send(to, f"[AeroStator] Order requested: {title}", "\n".join(lines), kind="project-admin")
    except Exception:                                       # noqa: BLE001
        log.warning("order request mail failed for %s", project_id, exc_info=True)


@router.get("/{project_id}/order-requests")
def list_order_requests(request: Request, project_id: str):
    _, project, _ = access.require_project(request, project_id)
    return {"order_requests": [r for r in project.get("order_requests") or [] if isinstance(r, dict)]}


@router.post("/{project_id}/order-requests")
async def create_order_request(request: Request, project_id: str):
    user, project, _ = access.require_project(request, project_id, edit=True)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="A JSON body is required")
    die, config = body.get("die"), body.get("config")
    if not (isinstance(die, str) and die.strip() and isinstance(config, str) and config.strip()):
        raise HTTPException(status_code=422, detail="die and config name the motor")
    die, config = die.strip(), config.strip()
    quantity = body.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY:
        raise HTTPException(status_code=422, detail="quantity must be a whole number of at least 1")
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise HTTPException(status_code=422, detail="note must be text")
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"note must be at most {MAX_NOTE} characters")
    has = _has_results(request, project_id, die, config)
    if has is None:
        raise HTTPException(status_code=404, detail="That motor is not linked to the project")
    if not has:
        raise HTTPException(status_code=409, detail="The motor has no computed results yet")

    created: list[dict] = []
    existing: list[dict] = []

    def apply(doc: dict) -> None:
        rows = doc.get("order_requests")
        if not isinstance(rows, list):
            rows = doc["order_requests"] = []
        for row in rows:
            if isinstance(row, dict) and row.get("status") in ACTIVE and _key(str(row.get("die")), str(row.get("config"))) == _key(die, config):
                existing.append(row)
                return
        rec = {"id": secrets.token_urlsafe(9), "die": die, "config": config, "quantity": quantity, "note": note,
               "by": _email(user), "at": datetime.now(timezone.utc).isoformat(), "status": "open"}
        rows.append(rec)
        created.append(rec)

    store.mutate_project(project_id, apply)
    if existing:
        return {"order_request": existing[0], "created": False}
    rec = created[0]
    from motor_ai_sim.development import routes
    message = {"id": secrets.token_urlsafe(12), "author_email": _email(user), "created_at": rec["at"], "system": True,
               "text": f"Order requested: {_label(die, config)}, {quantity} pcs", "field_ref": None, "row_id": None,
               "version": None, "revision": None}
    store.append_jsonl(project_id, "thread.jsonl", message)
    store.append_audit(project_id, _email(user), "create", "order_request", rec["id"], {"die": die, "config": config, "quantity": quantity})
    routes.notify_project_members(project_id, message)
    if not access.is_engineer(user):
        _notify_team(project_id, project, rec, user)
    return {"order_request": rec, "created": True}


@router.patch("/{project_id}/order-requests/{request_id}")
async def update_order_request(request: Request, project_id: str, request_id: str):
    user, _, _ = access.require_project(request, project_id)
    if not access.is_engineer(user):
        raise HTTPException(status_code=403, detail="Staff only")
    try:
        body = await request.json()
    except ValueError:
        body = None
    status = body.get("status") if isinstance(body, dict) else None
    if status not in ("acknowledged", "closed"):
        raise HTTPException(status_code=422, detail="status must be acknowledged or closed")
    found: list[dict] = []

    def apply(doc: dict) -> None:
        for row in doc.get("order_requests") or []:
            if isinstance(row, dict) and row.get("id") == request_id:
                row["status"] = status
                row["status_by"] = _email(user)
                row["status_at"] = datetime.now(timezone.utc).isoformat()
                found.append(dict(row))
                return

    store.mutate_project(project_id, apply)
    if not found:
        raise HTTPException(status_code=404, detail="Order request not found")
    store.append_audit(project_id, _email(user), "update", "order_request", request_id, {"status": status})
    return {"order_request": found[0]}
