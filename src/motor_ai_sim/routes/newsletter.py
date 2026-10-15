"""Legacy newsletter records (read-only) and in-app notices (see newsletter.py).

The newsletter opt-in is RETIRED (2026-10-15): there is no subscribe / confirm / campaign route any more; the Communication
preferences page is the only place to choose e-mail (``routes/comm_prefs.py``).

Anonymous (rate-limited per IP, never reveal whether an address exists):
- POST /api/newsletter/unsubscribe    ?t= or {token}; also the RFC 8058 one-click POST from mailbox providers, for the links
                                      and headers of mails sent BEFORE the retirement.  Also switches every new topic off.
Signed in:
- GET /api/notices, POST /api/notices/{id}/read
Admin only (require_admin):
- GET /api/newsletter/admin/status, /subscribers (+CSV), /campaigns   read-only history of the old newsletter
- /api/notices/admin                  post / list / withdraw notices
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

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


# ── unsubscribe (links of mails sent before the retirement) ────────────────

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


# ── admin ───────────────────────────────────────────────────────────────────

@router.get("/admin/status")
def admin_status(_a: dict = Depends(require_admin)):
    subs = N.subscribers()
    return {"confirmed": sum(1 for s in subs if s["status"] == "confirmed"),
            "pending": sum(1 for s in subs if s["status"] == "pending"),
            "unsubscribed": sum(1 for s in subs if s["status"] == "unsubscribed")}


@router.get("/admin/subscribers")
def admin_subscribers(format: str = "json", _a: dict = Depends(require_admin)):
    rows = N.subscribers()
    if format != "csv":
        return {"subscribers": rows}
    buf = io.StringIO()
    cols = ["email", "status", "role", "source", "text_version", "consent_at",
            "confirmed_at", "unsubscribed_at"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) if r.get(k) is not None else "" for k in cols})
    return Response(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="subscribers.csv"'})


@router.get("/admin/campaigns")
def admin_campaigns(_a: dict = Depends(require_admin)):
    return {"campaigns": N.list_campaigns()}


# ── in-app notices ──────────────────────────────────────────────────────────

@notices_router.get("")
def my_notices(authorization: str = Header(default=None)):
    me = _me(authorization)
    return {"notices": N.notices_for(me["email"], me.get("role") or "")}


@notices_router.post("/{nid}/read")
def read_notice(nid: str, authorization: str = Header(default=None)):
    me = _me(authorization)
    visible = {n["id"] for n in N.notices_for(me["email"], me.get("role") or "")}
    if nid not in visible:
        raise HTTPException(404, detail="no such notice")
    N.mark_read(me["email"], nid)
    return {"ok": True}


class NoticeReq(BaseModel):
    title: str
    body: str = ""
    level: str = "info"
    emails: list[str] = []
    roles: list[str] = []
    expires_at: Optional[float] = None


@notices_router.get("/admin")
def admin_notices(_a: dict = Depends(require_admin)):
    return {"notices": N.all_notices()}


@notices_router.post("/admin")
def admin_post_notice(req: NoticeReq, a: dict = Depends(require_admin)):
    try:
        return N.post_notice(req.title, req.body, level=req.level,
                             emails=req.emails, roles=req.roles,
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
