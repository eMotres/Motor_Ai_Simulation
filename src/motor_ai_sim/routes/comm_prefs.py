"""Communication preferences (see :mod:`motor_ai_sim.comm_prefs`).

Signed in:
* ``GET  /api/me/comm-prefs``                  the caller's four opt-in topics (all false until ticked), the NDA state
* ``PUT  /api/me/comm-prefs``                  ``{"topics": {topic: bool}, "lang": "en"|"zh-CN"}`` or ``{"unsubscribe_all": true}``

By signed e-mail link (no session; the token is the credential, 90 days, this account's preferences only):
* ``GET  /api/comm-prefs/{token}``             the same state, with the address masked
* ``PUT  /api/comm-prefs/{token}``             same body as above
* ``POST /api/comm-prefs/{token}/unsubscribe`` one click, unsubscribes from everything (RFC 8058: mailbox providers POST here)

Admin only:
* ``GET  /api/admin/comm-prefs/summary``       opted-in counts per topic, unsubscribed count, last mailing campaign
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

from motor_ai_sim import auth_email as E
from motor_ai_sim import comm_prefs as CP
from motor_ai_sim import users as U
from motor_ai_sim.auth import require_admin, resolve_user

log = logging.getLogger(__name__)

router_me = APIRouter(prefix="/api/me/comm-prefs", tags=["comm-prefs"])
router_token = APIRouter(prefix="/api/comm-prefs", tags=["comm-prefs"])
router_admin = APIRouter(prefix="/api/admin/comm-prefs", tags=["comm-prefs"])

#: Bad or expired links per IP.  Successful calls are not counted (a mailbox provider's address may unsubscribe many users).
BAD_TOKEN_IP = E.Limiter(max_hits=30, window_s=600.0, lock_s=600.0)


def _ip(request: Optional[Request]) -> str:
    try:
        from motor_ai_sim.client_ip import from_request
        return from_request(request) or "?"
    except Exception:                                       # noqa: BLE001
        return "?"


def mask(email: str) -> str:
    """``v***@motresres.com`` - enough for the owner of the link to recognise the account."""
    local, _, dom = (email or "").partition("@")
    return (local[:1] + "***@" + dom) if dom else "***"


def _nda(email: str) -> dict:
    try:
        from motor_ai_sim import nda as N
        cur = N.current("en")
        signable = bool(cur and cur["signable"])
    except Exception:                                       # noqa: BLE001 - a missing legal directory is "not open yet"
        signable = False
    return {"signed": bool(U.get_nda(email).get("signed")), "signable": signable}


def _payload(email: str, *, own: bool) -> dict:
    st = CP.get(email)
    return {"account": email if own else mask(email), "topics": st["topics"], "unsubscribed_all": st["unsubscribed_all"],
            "updated": st["updated"], "text_version": CP.TEXT_VERSION, "topic_ids": list(CP.TOPICS), "nda": _nda(email)}


def _apply(email: str, body, source: str, ip: str) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(422, detail="body must be a JSON object")
    lang = CP.norm_lang(body.get("lang"))
    try:
        if body.get("unsubscribe_all") is True:
            CP.unsubscribe_all(email, source="unsubscribe" if source == "email_link" else source, ip=ip, lang=lang)
        if "topics" in body:
            CP.set_topics(email, body.get("topics"), source=source, ip=ip, lang=lang)
        elif body.get("unsubscribe_all") is not True:
            raise HTTPException(422, detail="send topics or unsubscribe_all")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except OSError as e:
        log.error("comm_prefs: cannot store the change (%s)", e)
        raise HTTPException(503, detail="The choice could not be stored; nothing was changed. Try again later.")
    return {}


# -- signed in -------------------------------------------------------------------------

def _me(authorization: Optional[str]) -> str:
    me = resolve_user(authorization)
    email = str((me or {}).get("email") or "").strip().lower()
    if not email or U.get_user(email) is None:
        raise HTTPException(401, detail="sign in first")
    return email


@router_me.get("")
def my_prefs(authorization: Optional[str] = Header(default=None)):
    return _payload(_me(authorization), own=True)


@router_me.put("")
def set_my_prefs(request: Request, body: dict = Body(default={}), authorization: Optional[str] = Header(default=None)):
    email = _me(authorization)
    _apply(email, body, "prefs_page", _ip(request))
    return _payload(email, own=True)


# -- by e-mail link ----------------------------------------------------------------------

def _account(token: str, request: Request) -> str:
    ip = _ip(request)
    if BAD_TOKEN_IP.blocked(ip):
        raise HTTPException(429, detail="too many requests - try again later")
    email = CP.account_for_token(token)
    if not email:
        BAD_TOKEN_IP.hit(ip)
        raise HTTPException(404, detail="This link is not valid or has expired. Open the preferences from your account menu instead.")
    return email


@router_token.get("/{token}")
def token_prefs(token: str, request: Request):
    return _payload(_account(token, request), own=False)


@router_token.put("/{token}")
def set_token_prefs(token: str, request: Request, body: dict = Body(default={})):
    email = _account(token, request)
    _apply(email, body, "email_link", _ip(request))
    return _payload(email, own=False)


@router_token.post("/{token}/unsubscribe")
def token_unsubscribe(token: str, request: Request):
    """One click.  Idempotent; also the RFC 8058 target (the provider posts ``List-Unsubscribe=One-Click`` as a form)."""
    email = _account(token, request)
    try:
        CP.unsubscribe_all(email, source="unsubscribe", ip=_ip(request))
    except OSError as e:
        log.error("comm_prefs: cannot store the unsubscribe (%s)", e)
        raise HTTPException(503, detail="The unsubscribe could not be stored. Try again later.")
    return {"ok": True}


# -- admin ------------------------------------------------------------------------------

@router_admin.get("/summary")
def admin_summary(_a: dict = Depends(require_admin)):
    from motor_ai_sim import mailing as M
    rows = U.list_users()
    out = CP.summary(users_total=len(rows))
    out["verified_users"] = sum(1 for r in rows if r.get("email_verified") and not r.get("disabled"))
    out["campaign"] = M.last_campaign_status()
    # the retired single newsletter: read-only history, nobody is moved into the new topics
    from motor_ai_sim import newsletter as N
    subs = N.subscribers()
    out["legacy_newsletter"] = {"confirmed": sum(1 for r in subs if r["status"] == "confirmed"),
                                "pending": sum(1 for r in subs if r["status"] == "pending"),
                                "unsubscribed": sum(1 for r in subs if r["status"] == "unsubscribed"),
                                "campaigns": len(N.list_campaigns())}
    out["smtp"] = E.smtp_configured()
    return out
