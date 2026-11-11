"""Staff view of the assistant conversations kept for service improvement (owner 2026-11-11).  Read-only, admins only.

GET /api/admin/assistant-conversations                 ?assistant=spec|support|assistant &context=help|spec &project= &account=
                                                       &date_from= &date_to=  -> conversations (spec = the specification context,
                                                       support = the help context, old and new records alike: ONE assistant since
                                                       2026-11-11, its records carry ``assistant="assistant"`` + ``context``)
GET /api/admin/assistant-conversations/stats           same filters -> counters per day and in total
GET /api/admin/assistant-conversations/export          same filters -> JSONL (one record per line, with a ``dataset`` block)
GET /api/admin/assistant-conversations/{conv_id}       the records of one conversation

Every call is an admin-audit entry (``assistant_conv.read`` / ``assistant_conv.export``): an admin who reads people's conversations
leaves a record.  The archive itself is :mod:`motor_ai_sim.assistant_archive`; nothing here can change or delete it.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from motor_ai_sim import admin_audit as AA, assistant_archive as AR
from motor_ai_sim.auth import require_admin

router = APIRouter(prefix="/api/admin/assistant-conversations", tags=["admin"])
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _actor(admin: dict) -> str:
    return str((admin or {}).get("email") or (admin or {}).get("id") or "admin")


def _filters(assistant: str = "", project: str = "", account: str = "", date_from: str = "", date_to: str = "",
             context: str = "") -> dict:
    if assistant and assistant not in AR.ASSISTANTS:
        raise HTTPException(status_code=422, detail="assistant must be spec, support or assistant")
    if context and context not in AR.CONTEXTS:
        raise HTTPException(status_code=422, detail="context must be help or spec")
    for d in (date_from, date_to):
        if d and not _DAY.match(d):
            raise HTTPException(status_code=422, detail="dates are YYYY-MM-DD")
    return {"assistant": assistant, "project": project.strip(), "account": account.strip(), "date_from": date_from, "date_to": date_to,
            "context": context}


@router.get("")
def list_conversations(assistant: str = "", project: str = "", account: str = "", date_from: str = "", date_to: str = "",
                       limit: int = 200, context: str = "", admin: dict = Depends(require_admin)):
    flt = _filters(assistant, project, account, date_from, date_to, context)
    rows = AR.conversations(**flt)[: max(1, min(int(limit), 1000))]
    AA.record(_actor(admin), "assistant_conv.read", "list", details={k: v for k, v in flt.items() if v})
    return {"count": len(rows), "conversations": rows}


@router.get("/stats")
def conversation_stats(assistant: str = "", project: str = "", account: str = "", date_from: str = "", date_to: str = "",
                       context: str = "", admin: dict = Depends(require_admin)):
    flt = _filters(assistant, project, account, date_from, date_to, context)
    AA.record(_actor(admin), "assistant_conv.read", "stats", details={k: v for k, v in flt.items() if v})
    return AR.stats(**flt)


@router.get("/export")
def export_conversations(assistant: str = "", project: str = "", account: str = "", date_from: str = "", date_to: str = "",
                         context: str = "", admin: dict = Depends(require_admin)):
    flt = _filters(assistant, project, account, date_from, date_to, context)
    from motor_ai_sim import platform_agreement as PA
    lines = list(AR.export_lines(PA.training_allowed, **flt))
    AA.record(_actor(admin), "assistant_conv.export", "jsonl", details={**{k: v for k, v in flt.items() if v}, "records": len(lines)})
    name = f"assistant_conversations_{date_from or 'start'}_{date_to or 'now'}.jsonl"
    return Response("\n".join(lines) + ("\n" if lines else ""), media_type="application/x-ndjson",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{conv_id}")
def open_conversation(conv_id: str, admin: dict = Depends(require_admin)):
    if not re.match(r"^[a-z]-[0-9a-f]{16}$", conv_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    rows = list(AR.iter_records(conv_id=conv_id))
    if not rows:
        raise HTTPException(status_code=404, detail="conversation not found")
    AA.record(_actor(admin), "assistant_conv.read", conv_id, subject=str(rows[0].get("account") or ""))
    return {"conv_id": conv_id, "records": rows}
