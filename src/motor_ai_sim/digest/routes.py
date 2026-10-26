"""HTTP side of the activity digests.

Settings (the project owner or an admin for a project; an admin for a production)::

    GET/PUT /api/digest/settings/project/{project_id}
    GET/PUT /api/digest/settings/production/{die}/{config}
    GET/PUT /api/digest/mine                      a member's own "leave / rejoin this digest"

By signed e-mail link (no session; the token is the credential)::

    GET/POST /api/digest/unsubscribe/{scope}/{token}   the page / one click (RFC 8058 target)

Admin -> Digests (the ONLY place a digest is sent)::

    GET  /api/admin/digests                  pending drafts + history
    GET  /api/admin/digests/{id}             the draft: the single text, items, recipients
    GET  /api/admin/digests/{id}/preview     the e-mail exactly as one recipient gets it (?recipient=)
    POST /api/admin/digests/{id}/send        {"approve": true} - the explicit approval
    POST /api/admin/digests/{id}/discard
    POST /api/admin/digests/run              make the due drafts now ({"dry_run": true} writes nothing); sends nothing
    GET  /api/admin/digests/log              who changed what, who sent what
"""
from __future__ import annotations

import html
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from motor_ai_sim import auth
from motor_ai_sim import auth_email as E
from motor_ai_sim import mailing
from motor_ai_sim.digest import approval as AP
from motor_ai_sim.digest import build as B
from motor_ai_sim.digest import store as DS
from motor_ai_sim.production.access import require_signed_in

router = APIRouter(prefix="/api/digest", tags=["digest"])
admin_router = APIRouter(prefix="/api/admin/digests", tags=["digest-admin"])

#: pause between two messages of one approved send (seconds)
SEND_DELAY_S = float(os.environ.get("DIGEST_SEND_DELAY_S", "1.0") or 0)


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _summary(d: dict) -> dict:
    out = {k: d.get(k) for k in ("id", "scope", "kind", "title", "title_zh", "status", "period", "created_at", "updated_at",
                                 "bilingual", "subject")}
    out["items"] = len(d.get("items") or []) + int(d.get("more") or 0)
    out["recipients"] = len(d.get("recipients") or [])
    out["revision"] = int(d.get("revision") or 0)
    if d.get("opened"):
        out["opened"] = d["opened"]
    for k in ("sent", "discarded", "last_error", "partial", "superseded_by"):
        if d.get(k):
            out[k] = d[k]
    return out


# -- settings --------------------------------------------------------------------------------------------------------------------

def _settings_view(group: B.Group, *, can_edit: bool) -> dict:
    s = DS.get_settings(group.scope)
    recipients, skipped = B.resolve_recipients(group, s)
    state = DS.get_state(group.scope)
    pending = DS.load_draft(state["draft_id"]) if state.get("draft_id") else None
    sent = [r for r in DS.read_log(group.scope) if r.get("event") == "sent"]
    return {
        "scope": group.scope, "title": group.title_en, "mode": s["mode"], "frequency": s["frequency"], "recipients": s["recipients"],
        "chosen": s["chosen"], "optouts": s["optouts"], "updated_at": s.get("updated_at"), "updated_by": s.get("updated_by"),
        "can_edit": can_edit, "active": group.active,
        "members": [{"email": e, "role": r, "opted_out": e in s["optouts"]} for e, r in group.members],
        "will_receive": [{"email": r["email"], "role": r["role"], "lang": r["lang"]} for r in recipients],
        "not_receiving": skipped,
        "pending": _summary(pending) if pending and pending.get("status") in DS.OPEN_STATUSES else None,
        "last_sent": sent[-1]["ts"] if sent else None,
    }


def _project_manager(request: Request, project_id: str) -> tuple[dict, B.Group]:
    from motor_ai_sim.development import access as DA
    from motor_ai_sim.development import store as PS
    user = require_signed_in(request)
    project = PS.get_project(project_id)
    admin = str(user.get("role") or "").lower() == "admin"
    if project is None or not (admin or DA.membership(_email(user), project) == "owner"):
        raise HTTPException(status_code=404, detail="Project not found")
    return user, B.project_group(project)


def _production_manager(request: Request, die: str, config: str) -> tuple[dict, B.Group]:
    from motor_ai_sim.production import access as PA
    user = require_signed_in(request)
    if not PA.is_admin(user):
        raise HTTPException(status_code=404, detail="Production not found")
    rec = PA.load_production(die, config)
    if rec is None:
        raise HTTPException(status_code=404, detail="Production not found")
    return user, B.production_group(rec)


async def _apply(request: Request, group: B.Group, user: dict) -> dict:
    try:
        body = await request.json()
        DS.set_settings(group.scope, DS.validate_settings_update(body), by=_email(user))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _settings_view(group, can_edit=True)


@router.get("/settings/project/{project_id}")
def get_project_settings(request: Request, project_id: str):
    _, group = _project_manager(request, project_id)
    return _settings_view(group, can_edit=True)


@router.put("/settings/project/{project_id}")
async def put_project_settings(request: Request, project_id: str):
    user, group = _project_manager(request, project_id)
    return await _apply(request, group, user)


@router.get("/settings/production/{die}/{config}")
def get_production_settings(request: Request, die: str, config: str):
    _, group = _production_manager(request, die, config)
    return _settings_view(group, can_edit=True)


@router.put("/settings/production/{die}/{config}")
async def put_production_settings(request: Request, die: str, config: str):
    user, group = _production_manager(request, die, config)
    return await _apply(request, group, user)


# -- a member's own opt-out ---------------------------------------------------------------------------------------------------------

@router.get("/mine")
def my_digests(request: Request):
    me = _email(require_signed_in(request))
    out = []
    for g in B.list_groups():
        if any(e == me for e, _ in g.members):
            s = DS.get_settings(g.scope)
            if s["mode"] != "off":
                out.append({"scope": g.scope, "title": g.title_en, "kind": g.kind, "opted_out": me in s["optouts"]})
    return {"digests": out}


@router.put("/mine")
async def set_my_digest(request: Request):
    me = _email(require_signed_in(request))
    body = await request.json()
    scope = str(body.get("scope") or "") if isinstance(body, dict) else ""
    opted_out = body.get("opted_out") if isinstance(body, dict) else None
    g = B.get_group(scope)
    if g is None or not any(e == me for e, _ in g.members) or not isinstance(opted_out, bool):
        raise HTTPException(status_code=422, detail="scope of a group you belong to and opted_out true|false required")
    DS.set_optout(scope, me, opted_out, by=me)
    return {"scope": scope, "opted_out": opted_out}


# -- unsubscribe by link ---------------------------------------------------------------------------------------------------------------

def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{html.escape(title)}</title></head>
<body style="font-family:Arial,'Microsoft YaHei',sans-serif;background:#f4f5f7;color:#1f2937;margin:0;padding:32px 16px">
<div style="max-width:480px;margin:0 auto;background:#fff;border-radius:8px;padding:24px">{body}</div></body></html>""", status_code=status)


def _unsub_target(scope_tok: str, token: str) -> tuple[str, str, B.Group]:
    scope = DS.scope_from_token(scope_tok)
    email = B.unsub_email(scope, token) if scope else None
    group = B.get_group(scope) if scope else None
    if not scope or not email or group is None:
        raise HTTPException(status_code=400, detail="This link is not valid any more.")
    return scope, email, group


@router.get("/unsubscribe/{scope_tok}/{token}")
def unsubscribe_page(scope_tok: str, token: str):
    """Shows what the link does and a button.  A GET never changes anything (mail scanners open links)."""
    try:
        _, _, group = _unsub_target(scope_tok, token)
    except HTTPException:
        return _page("Link not valid", "<p>This link is not valid or has expired. 链接无效或已过期。</p>", 400)
    t = html.escape(group.title_en)
    return _page("Stop updates", f'<h1 style="font-size:18px">Stop e-mail updates for "{t}"?</h1>'
                 f'<p>您将不再收到“{html.escape(group.title_zh)}”的更新邮件？</p>'
                 f'<form method="post"><button type="submit" style="padding:10px 18px;font-size:15px">Stop updates / 不再接收</button></form>')


@router.post("/unsubscribe/{scope_tok}/{token}")
def unsubscribe_do(scope_tok: str, token: str):
    try:
        scope, email, group = _unsub_target(scope_tok, token)
    except HTTPException:
        return _page("Link not valid", "<p>This link is not valid or has expired. 链接无效或已过期。</p>", 400)
    DS.set_optout(scope, email, True, by=email)
    return _page("Done", f'<h1 style="font-size:18px">Done</h1><p>You will not get e-mail updates for "{html.escape(group.title_en)}" any more. '
                 '您将不再收到此更新邮件。</p>')


# -- Admin -> Digests ---------------------------------------------------------------------------------------------------------------------

@admin_router.get("")
def admin_list(_admin: dict = Depends(auth.require_admin)):
    drafts = DS.list_drafts()
    return {"pending": [_summary(d) for d in drafts if d.get("status") in DS.OPEN_STATUSES],
            "history": [_summary(d) for d in drafts if d.get("status") not in DS.OPEN_STATUSES][:100],
            "smtp_configured": E.smtp_configured()}


@admin_router.get("/log")
def admin_log(scope: Optional[str] = None, _admin: dict = Depends(auth.require_admin)):
    return {"log": DS.read_log(scope)[-300:]}


@admin_router.post("/run")
async def admin_run(request: Request, _admin: dict = Depends(auth.require_admin)):
    """Make the due drafts now.  Sends nothing."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    dry = bool(body.get("dry_run")) if isinstance(body, dict) else False
    return {"dry_run": dry, "rows": B.run(write=not dry)}


def _draft(draft_id: str) -> dict:
    d = DS.load_draft(draft_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Digest not found")
    return d


@admin_router.get("/{draft_id}")
def admin_get(draft_id: str, _admin: dict = Depends(auth.require_admin)):
    d = _draft(draft_id)
    DS.mark_opened(draft_id, _email(_admin))                # from now on this text is frozen: a refresh supersedes it
    d = _draft(draft_id)
    out = _summary(d)
    out.update({k: d.get(k) for k in ("items", "more", "recipients", "skipped", "caps", "text_body")})
    out["content_hash"] = B.content_hash(d)
    return out


@admin_router.get("/{draft_id}/preview")
def admin_preview(draft_id: str, recipient: str = "", _admin: dict = Depends(auth.require_admin)):
    d = _draft(draft_id)
    emails = [r["email"] for r in d["recipients"]]
    who = (recipient or (emails[0] if emails else "")).strip().lower()
    if who not in emails:
        raise HTTPException(status_code=404, detail="That address is not on this digest's list")
    DS.mark_opened(draft_id, _email(_admin))
    d = _draft(draft_id)
    r = B.render_for(d, who)
    # the hash is what the owner approves: the send request must carry it back
    return {"to": who, "subject": r["subject"], "text": r["text"], "html": r["html"], "recipients": emails,
            "content_hash": B.content_hash(d)}


@admin_router.post("/{draft_id}/send")
async def admin_send(draft_id: str, request: Request, admin: dict = Depends(auth.require_admin)):
    """THE approval.  The body must be ``{"approve": true, "content_hash": "<hash the preview showed>"}``; the admin's own address
    is recorded as the approver.  A hash that is not the draft's CURRENT content (it was refreshed or superseded) = 409."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("approve") is not True:
        raise HTTPException(status_code=422, detail='Send needs an explicit approval: {"approve": true, "content_hash": "..."}')
    if not isinstance(body.get("content_hash"), str) or not body["content_hash"]:
        raise HTTPException(status_code=422, detail="Send needs the content_hash of the text you previewed.")
    if not E.smtp_configured():
        raise HTTPException(status_code=409, detail="E-mail (SMTP) is not configured on this server.")
    try:
        result = AP.send_draft(draft_id, approved_by=_email(admin), content_hash=body["content_hash"],
                               deliver=lambda msg: E.deliver(msg), delay_s=SEND_DELAY_S)
    except mailing.SendAborted as exc:                    # another send of this draft is running (or crashed): not a server error
        raise HTTPException(status_code=409, detail=str(exc))
    except AP.ApprovalError as exc:
        status = {"not_found": 404, "approval_required": 403}.get(exc.code, 409)
        raise HTTPException(status_code=status, detail=str(exc))
    return result


@admin_router.post("/{draft_id}/discard")
def admin_discard(draft_id: str, admin: dict = Depends(auth.require_admin)):
    try:
        d = AP.discard_draft(draft_id, by=_email(admin))
    except AP.ApprovalError as exc:
        raise HTTPException(status_code=404 if exc.code == "not_found" else 409, detail=str(exc))
    return _summary(d)
