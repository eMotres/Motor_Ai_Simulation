"""Login and user administration for the self-hosted deployment.

Identity comes from Google sign-in (GIS ID tokens, verified in auth.py) or
from a password account below; RIGHTS always come from our registry
(config/users.json + ADMIN_EMAILS).  This router adds:

- POST /api/auth/login        — password login → our HS256 token
- GET  /api/auth/users        — admin: list accounts
- POST /api/auth/users        — admin: create a password account
- PATCH /api/auth/users/{email} — admin: tier / disabled / name / new password
- DELETE /api/auth/users/{email} — admin: remove an account
- POST /api/auth/password     — self-service password change (signed in)
- GET  /api/auth/sessions     — the caller's own sessions
- POST /api/auth/sessions/{sid}/revoke — sign one of them out
- POST /api/auth/logout       — revoke the session this request is using

Login is rate-limited in-memory: 5 failures per email-or-IP → 60 s lockout.

Every sign-in now creates a SERVER-SIDE session (sessions.py) whose sid rides
in the token, and appends a line to logs/auth_events.jsonl.  Before that, a
sign-in left one INFO line and nothing else: there was no way to answer "why
was I signed out" or "which browser is that".
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from motor_ai_sim import sessions as S
from motor_ai_sim import users as U
from motor_ai_sim.auth import require_admin, resolve_user, resolve_user_detail

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _who(request: Optional[Request]) -> tuple[str, str]:
    """(ip, user agent) — both go into the session record and the event log."""
    ip = request.client.host if request and request.client else "?"
    ua = request.headers.get("user-agent", "") if request else ""
    return ip, ua


def _start_session(email: str, *, method: str, request: Optional[Request]) -> str:
    """Record the session, return the signed token that names it.

    FIRST SIGN-IN PROVISIONING (Stage 10): the account's workspace is created
    and seeded HERE, before the token leaves the building.  The middleware would
    also do it on the first request that arrives, but then the very first
    ``/api/config`` of a brand-new account is racing a directory copy — and if
    the seed fails, it fails inside a route instead of at the door where the
    log line is about sign-in.  ``workspace.provision`` is a no-op when
    ``WORKSPACES_ROOT`` is unset, so this workstation is unaffected.
    """
    ip, ua = _who(request)
    from motor_ai_sim import workspace as W
    W.provision(email)
    sid = S.create(email, expires=time.time() + U._TOKEN_TTL_S,
                   login_method=method, ip=ip, user_agent=ua)
    token = U.issue_token(email, sid=sid)
    S.record_event("login", email=email, sid=sid, reason=method, ip=ip,
                   user_agent=ua, path="/api/auth/" + method)
    return token

_FAILS: dict[str, list[float]] = {}
_FLOCK = threading.Lock()
_MAX_FAILS, _WINDOW_S, _LOCK_S = 5, 300.0, 60.0


def _throttle(key: str) -> None:
    now = time.time()
    with _FLOCK:
        lst = [t for t in _FAILS.get(key, []) if now - t < _WINDOW_S]
        _FAILS[key] = lst
        if len(lst) >= _MAX_FAILS and now - lst[-1] < _LOCK_S:
            raise HTTPException(429, detail=(
                "too many failed logins — wait a minute and try again"))


def _note_fail(key: str) -> None:
    with _FLOCK:
        _FAILS.setdefault(key, []).append(time.time())


class LoginReq(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(req: LoginReq, request: Request):
    ip = (request.client.host if request and request.client else "?")
    key = f"{req.email.strip().lower()}|{ip}"
    _throttle(key)
    u = U.check_login(req.email, req.password)
    if u is None:
        _note_fail(key)
        # One message for wrong password AND unknown user — no user enumeration.
        raise HTTPException(401, detail="wrong email or password")
    token = _start_session(req.email, method="password", request=request)
    log.info("auth: password login ok for %s from %s", req.email.strip().lower(), ip)
    return {"token": token, "user": U.public_user(req.email)}


class GoogleReq(BaseModel):
    credential: str            # the GIS ID token from the Google button


@router.post("/google")
def google_login(req: GoogleReq, request: Request):
    """Exchange a Google ID token (1-hour life) for OUR 30-day HS256 token.
    Identity is Google's; the tier comes from the registry (auto-provisioned
    on first sign-in) with ADMIN_EMAILS on top."""
    from motor_ai_sim.auth import _registry_tier, _verify_google_token, GOOGLE_CLIENT_ID
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, detail=(
            "Google sign-in is not configured on this server "
            "(GOOGLE_CLIENT_ID is not set)"))
    claims = _verify_google_token(req.credential)
    if claims is None:
        raise HTTPException(401, detail="Google token was not accepted")
    if claims.get("email_verified") is False:
        raise HTTPException(403, detail="Google account email is not verified")
    email = (claims.get("email") or "").strip().lower()
    tier = _registry_tier(email)
    if tier == "__disabled__":
        raise HTTPException(403, detail="this account is disabled")
    token = _start_session(email, method="google", request=request)
    ip = (request.client.host if request and request.client else "?")
    log.info("auth: Google login ok for %s (tier %s) from %s", email, tier, ip)
    return {"token": token,
            "user": {**U.public_user(email), "tier": tier,
                     "name": claims.get("name") or U.public_user(email).get("name", "")}}


class CreateReq(BaseModel):
    email: str
    password: str
    tier: str = "free"
    name: str = ""


@router.get("/users")
def users_list(_admin: dict = Depends(require_admin)):
    return {"users": U.list_users()}


@router.post("/users")
def users_create(req: CreateReq, _admin: dict = Depends(require_admin)):
    try:
        return {"ok": True, "user": U.create_user(req.email, req.password,
                                                  tier=req.tier, name=req.name)}
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


class PatchReq(BaseModel):
    tier: Optional[str] = None
    disabled: Optional[bool] = None
    name: Optional[str] = None
    password: Optional[str] = None      # admin reset


@router.patch("/users/{email}")
def users_patch(email: str, req: PatchReq, _admin: dict = Depends(require_admin)):
    try:
        if req.password is not None:
            U.set_password(email, req.password)
        out = U.update_user(email, tier=req.tier, disabled=req.disabled,
                            name=req.name)
        log.info("auth: user %s updated (%s)", email,
                 {k: v for k, v in req.model_dump().items()
                  if v is not None and k != "password"})
        return {"ok": True, "user": out}
    except KeyError:
        raise HTTPException(404, detail=f"user '{email}' not found")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.delete("/users/{email}")
def users_delete(email: str, _admin: dict = Depends(require_admin)):
    try:
        U.delete_user(email)
        log.warning("auth: user %s DELETED", email)
        return {"ok": True}
    except KeyError:
        raise HTTPException(404, detail=f"user '{email}' not found")


class SelfPassword(BaseModel):
    old_password: str
    new_password: str


@router.post("/password")
def change_password(req: SelfPassword,
                    authorization: str = Header(default=None)):
    me = resolve_user(authorization)
    if me is None or not me.get("email"):
        raise HTTPException(401, detail="sign in first")
    if U.check_login(me["email"], req.old_password) is None:
        raise HTTPException(403, detail="current password does not match")
    try:
        U.set_password(me["email"], req.new_password)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    return {"ok": True}


# ── sessions (own) ───────────────────────────────────────────────────────────

@router.get("/sessions")
def my_sessions(request: Request, authorization: str = Header(default=None)):
    """Every sign-in of the calling account: when, from where, with what.

    This is the list that makes an invisible cause visible — a session that
    only ever appears once and never comes back is a browser profile whose
    storage does not survive (a preview pane, a private window), not an expiry.
    """
    det = resolve_user_detail(authorization)
    me = det["user"]
    if me is None or not me.get("email"):
        raise HTTPException(401, detail="sign in first")
    rows = [S.public(r) for r in S.list_for(me["email"])]
    return {"email": me["email"], "current": det["sid"] or None,
            "count": len(rows), "sessions": rows}


@router.post("/sessions/{sid}/revoke")
def revoke_my_session(sid: str, request: Request,
                      authorization: str = Header(default=None)):
    """Sign one of MY sessions out.  Someone else's sid is a 404, not a 403 —
    an account must not be able to probe for other people's session ids."""
    det = resolve_user_detail(authorization)
    me = det["user"]
    if me is None or not me.get("email"):
        raise HTTPException(401, detail="sign in first")
    try:
        rec = S.get(sid)
    except S.StoreUnavailable as e:
        raise HTTPException(503, detail=f"session store is busy: {e}")
    if not isinstance(rec, dict) or (rec.get("email") or "") != me["email"]:
        raise HTTPException(404, detail="no such session")
    S.revoke(sid)
    ip, ua = _who(request)
    S.record_event("revoke", email=me["email"], sid=sid, reason="self",
                   ip=ip, user_agent=ua, path="/api/auth/sessions/revoke")
    log.info("auth: session %s revoked by its owner %s", sid, me["email"])
    return {"ok": True, "sid": sid}


@router.post("/logout")
def logout(request: Request, authorization: str = Header(default=None)):
    """Revoke the session this request is authenticated with.

    Called by the frontend BEFORE it clears localStorage, so a token copied out
    of a browser stops working the moment the user signs out — and so the event
    log records a deliberate logout instead of a session that simply stops
    being seen."""
    det = resolve_user_detail(authorization)
    ip, ua = _who(request)
    sid = det["sid"]
    email = det["email"] or ((det["user"] or {}).get("email") or "")
    if sid:
        S.revoke(sid)
    S.record_event("logout", email=email, sid=sid, reason=det["reason"],
                   ip=ip, user_agent=ua, path="/api/auth/logout")
    return {"ok": True, "sid": sid or None}
