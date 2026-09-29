"""Newsletter consent + campaigns, and in-app notices (see newsletter.py).

Anonymous (rate-limited per IP, never reveal whether an address exists):
- POST /api/newsletter/confirm        {token}  — double opt-in link
- POST /api/newsletter/unsubscribe    ?t= or {token}; also the RFC 8058
                                      one-click POST from mailbox providers
Signed in:
- GET/POST /api/newsletter/me         — status / opt in (mails a link) / out
- GET /api/notices, POST /api/notices/{id}/read
Admin only (require_admin):
- /api/newsletter/admin/*             — status, subscribers (+CSV), campaigns
- /api/notices/admin                  — post / list / withdraw notices
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from motor_ai_sim import auth_email as E
from motor_ai_sim import newsletter as N
from motor_ai_sim.auth import require_admin, resolve_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])
notices_router = APIRouter(prefix="/api/notices", tags=["notices"])


def _ip(request: Optional[Request]) -> str:
    from motor_ai_sim.routes.auth_local import _ip as ip
    return ip(request)


def _limit(request: Request) -> None:
    ip = _ip(request)
    if N.LINK_IP.blocked(ip):
        raise HTTPException(429, detail="too many requests — try again later")
    N.LINK_IP.hit(ip)


def _me(authorization: Optional[str]) -> dict:
    me = resolve_user(authorization)
    if me is None or not me.get("email"):
        raise HTTPException(401, detail="sign in first")
    return me


# ── consent (public / self) ─────────────────────────────────────────────────

class TokenReq(BaseModel):
    token: str


@router.post("/confirm")
def confirm(req: TokenReq, request: Request):
    _limit(request)
    if not N.confirm(req.token):
        raise HTTPException(400, detail=(
            "this link is invalid, already used or expired — subscribe again "
            "from Notifications in your account menu"))
    return {"ok": True}


@router.post("/unsubscribe")
async def unsubscribe(request: Request, t: str = ""):
    """Web page link (JSON {token}) and RFC 8058 one-click (the provider
    POSTs `List-Unsubscribe=One-Click` as a form to the ?t= URL)."""
    _limit(request)
    token = t
    if not token:
        try:
            body = await request.json()
            token = str((body or {}).get("token") or "")
        except Exception:
            token = ""
    if not N.unsubscribe_by_token(token):
        raise HTTPException(400, detail="this unsubscribe link is invalid")
    return {"ok": True}


@router.get("/me")
def my_status(authorization: str = Header(default=None)):
    me = _me(authorization)
    return N.status(me["email"])


class MeReq(BaseModel):
    subscribe: bool


@router.post("/me")
def set_mine(req: MeReq, request: Request,
             authorization: str = Header(default=None)):
    me = _me(authorization)
    if req.subscribe:
        if not E.smtp_configured():
            raise HTTPException(503, detail="e-mail is not configured on this server")
        if E.MAIL_ACCOUNT.blocked(me["email"]):
            raise HTTPException(429, detail="too many requests — try again later")
        E.MAIL_ACCOUNT.hit(me["email"])
        outcome = N.request_subscribe(me["email"], source="settings", ip=_ip(request))
        return N.status(me["email"]) | {"outcome": outcome}
    N.unsubscribe(me["email"], source="settings")
    return N.status(me["email"]) | {"outcome": "unsubscribed"}


# ── admin ───────────────────────────────────────────────────────────────────

@router.get("/admin/status")
def admin_status(_a: dict = Depends(require_admin)):
    subs = N.subscribers()
    return {"smtp": E.smtp_configured(), "rate_per_min": N.rate_per_min(),
            "daily_cap": N.daily_cap(), "sent_24h": N.sent_last_24h(),
            "confirmed": sum(1 for s in subs if s["status"] == "confirmed"),
            "pending": sum(1 for s in subs if s["status"] == "pending"),
            "unsubscribed": sum(1 for s in subs if s["status"] == "unsubscribed"),
            "consent_text": N.CONSENT_TEXT,
            "consent_version": N.CONSENT_TEXT_VERSION}


@router.get("/admin/subscribers")
def admin_subscribers(format: str = "json", _a: dict = Depends(require_admin)):
    rows = N.subscribers()
    if format != "csv":
        return {"subscribers": rows}
    buf = io.StringIO()
    cols = ["email", "status", "tier", "source", "text_version", "consent_at",
            "confirmed_at", "unsubscribed_at"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) if r.get(k) is not None else "" for k in cols})
    return Response(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="subscribers.csv"'})


class CampaignReq(BaseModel):
    subject: str
    body_md: str
    tiers: list[str] = []


class CampaignPatch(BaseModel):
    subject: Optional[str] = None
    body_md: Optional[str] = None
    tiers: Optional[list[str]] = None


@router.get("/admin/campaigns")
def admin_campaigns(_a: dict = Depends(require_admin)):
    return {"campaigns": N.list_campaigns()}


@router.post("/admin/campaigns")
def admin_create(req: CampaignReq, a: dict = Depends(require_admin)):
    try:
        return N.create_campaign(req.subject, req.body_md, req.tiers,
                                 author=(a or {}).get("email") or "")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.put("/admin/campaigns/{cid}")
def admin_update(cid: str, req: CampaignPatch, _a: dict = Depends(require_admin)):
    try:
        return N.update_campaign(cid, req.subject, req.body_md, req.tiers)
    except KeyError:
        raise HTTPException(404, detail="no such campaign")
    except ValueError as e:
        raise HTTPException(409, detail=str(e))


@router.post("/admin/preview")
def admin_preview(req: CampaignReq, a: dict = Depends(require_admin)):
    who = (a or {}).get("email") or "preview@example.com"
    h, t = N.render(req.subject, req.body_md, N.unsubscribe_url(who))
    return {"html": h, "text": t, "audience": len(N.audience(req.tiers))}


@router.post("/admin/test")
def admin_test(req: CampaignReq, a: dict = Depends(require_admin)):
    to = (a or {}).get("email")
    if not to:
        raise HTTPException(422, detail="your admin session has no e-mail address")
    if not E.smtp_configured():
        raise HTTPException(503, detail="SMTP is not configured")
    try:
        N.send_test(to, req.subject, req.body_md)
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(502, detail=f"SMTP: {type(e).__name__}")
    return {"ok": True, "to": to}


class SendReq(BaseModel):
    at: Optional[float] = None       # UNIX seconds; None = now


@router.post("/admin/campaigns/{cid}/send")
def admin_send(cid: str, req: SendReq, _a: dict = Depends(require_admin)):
    try:
        return N.schedule(cid, req.at)
    except KeyError:
        raise HTTPException(404, detail="no such campaign")
    except ValueError as e:
        raise HTTPException(409, detail=str(e))


@router.post("/admin/campaigns/{cid}/cancel")
def admin_cancel(cid: str, _a: dict = Depends(require_admin)):
    try:
        return N.cancel(cid)
    except KeyError:
        raise HTTPException(404, detail="no such campaign")
    except ValueError as e:
        raise HTTPException(409, detail=str(e))


# ── in-app notices ──────────────────────────────────────────────────────────

@notices_router.get("")
def my_notices(authorization: str = Header(default=None)):
    me = _me(authorization)
    return {"notices": N.notices_for(me["email"], me.get("tier") or "")}


@notices_router.post("/{nid}/read")
def read_notice(nid: str, authorization: str = Header(default=None)):
    me = _me(authorization)
    visible = {n["id"] for n in N.notices_for(me["email"], me.get("tier") or "")}
    if nid not in visible:
        raise HTTPException(404, detail="no such notice")
    N.mark_read(me["email"], nid)
    return {"ok": True}


class NoticeReq(BaseModel):
    title: str
    body: str = ""
    level: str = "info"
    emails: list[str] = []
    tiers: list[str] = []
    expires_at: Optional[float] = None


@notices_router.get("/admin")
def admin_notices(_a: dict = Depends(require_admin)):
    return {"notices": N.all_notices()}


@notices_router.post("/admin")
def admin_post_notice(req: NoticeReq, a: dict = Depends(require_admin)):
    try:
        return N.post_notice(req.title, req.body, level=req.level,
                             emails=req.emails, tiers=req.tiers,
                             expires_at=req.expires_at,
                             author=(a or {}).get("email") or "")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@notices_router.post("/admin/{nid}/withdraw")
def admin_withdraw(nid: str, _a: dict = Depends(require_admin)):
    try:
        N.withdraw_notice(nid)
    except KeyError:
        raise HTTPException(404, detail="no such notice")
    return {"ok": True}
