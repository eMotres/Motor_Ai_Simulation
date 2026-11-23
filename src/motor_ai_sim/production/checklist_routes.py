"""Routes of the mass-production checklist (owner 2026-11-23).  Mounted by ``api.py`` next to the production router.

Read: every production member who may see the production (the factory behind the NDA gate included) and global admins.  Write (a status
and its evidence): the production's owner / engineer, the factory (``oem`` role) and global admins.  A project viewer has neither.
Not exposed through MCP.  Every status change is written to the production's audit log."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from motor_ai_sim.production import access, checklist, checklist_xlsx, models, store

router = APIRouter(prefix="/api/production", tags=["production"])


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _internal_ok(user: dict, production: dict) -> bool:
    """Our internal documents are not shown to the factory."""
    return access.is_admin(user) or models.is_member(_email(user), production) != "oem"


def _can_write(user: dict, production: dict) -> bool:
    return access.is_admin(user) or models.is_member(_email(user), production) in ("owner", "engineer", "oem")


@router.get("/{die}/{config}/checklist")
def get_checklist(request: Request, die: str, config: str):
    user, production = access.require_member_with_full_view(request, die, config)
    data = checklist.build(die, config, include_internal=_internal_ok(user, production))
    data["can_edit"] = _can_write(user, production)
    data["statuses"] = list(checklist.STATUSES)
    return data


@router.put("/{die}/{config}/checklist")
async def put_checklist_status(request: Request, die: str, config: str):
    """Body ``{key, status, evidence?: {doc_id, note}}``: set the status of one item (the item must be in the current list)."""
    user, production = access.require_member_with_full_view(request, die, config)
    if not _can_write(user, production):
        raise HTTPException(status_code=403, detail="Editor or factory access required.")
    try:
        body = await request.json()
    except ValueError:
        body = None
    try:
        update = checklist.validate_update(body)
        done = checklist.set_status(die, config, _email(user), update, include_internal=_internal_ok(user, production))
    except ValueError as e:
        reason = str(e)
        if reason == "unknown_item":
            raise HTTPException(status_code=404, detail="Checklist item not found.")
        raise HTTPException(status_code=422, detail="Unknown evidence document." if reason == "unknown_document" else reason)
    store.append_audit(die, config, models.audit_entry(
        _email(user), "update", "checklist", update["key"],
        {"from": done["before"], "to": update["status"], "evidence": update["evidence"] if update["has_evidence"] else None}))
    return done["item"]


@router.get("/{die}/{config}/checklist.xlsx")
def export_checklist_xlsx(request: Request, die: str, config: str):
    """The checklist as Excel (EN + 中文): an export only, there is no upload that changes requirements."""
    user, production = access.require_member_with_full_view(request, die, config)
    data = checklist.build(die, config, include_internal=_internal_ok(user, production))
    payload = checklist_xlsx.build_workbook(data, production.get("title"))
    store.append_audit(die, config, models.audit_entry(_email(user), "download", "checklist_xlsx", "checklist", {}))
    name = f"{die}_{config}_checklist.xlsx"
    return Response(content=payload, media_type=checklist_xlsx.XLSX_MEDIA_TYPE,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
