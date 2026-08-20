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

Login is rate-limited in-memory: 5 failures per email-or-IP → 60 s lockout.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from motor_ai_sim import users as U
from motor_ai_sim.auth import require_admin, resolve_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

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
    token = U.issue_token(req.email)
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
    token = U.issue_token(email)
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
