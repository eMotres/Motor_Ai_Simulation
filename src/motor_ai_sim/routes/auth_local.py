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
- POST /api/auth/register     — self-service e-mail/password sign-up (unverified)
- POST /api/auth/verify       — consume the e-mail confirmation link
- POST /api/auth/reset/request, /reset/confirm — password reset by link
- GET  /api/auth/methods      — which sign-in methods the form may offer
- GET  /api/auth/pending, POST /api/auth/pending/{email}/approve — admin

Login is rate-limited in-memory per account and per IP (auth_email.Limiter);
register/reset answers never reveal whether an address has an account.

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

from motor_ai_sim import auth_email as E
from motor_ai_sim import sessions as S
from motor_ai_sim import users as U
from motor_ai_sim import admin_audit as _AA
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

def _ip(request: Optional[Request]) -> str:
    """Client IP, proxy-aware the same way the support chat is (X-Real-IP set
    by our nginx; the rightmost X-Forwarded-For hop otherwise)."""
    try:
        from motor_ai_sim.routes.support import client_ip
        return client_ip(request) or "?"
    except Exception:                                       # pragma: no cover
        return request.client.host if request and request.client else "?"


def _locked(email: str, ip: str) -> None:
    if E.LOGIN_ACCOUNT.blocked(email) or E.LOGIN_IP.blocked(ip):
        raise HTTPException(429, detail=(
            "too many failed sign-ins — wait 15 minutes and try again"))


class LoginReq(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(req: LoginReq, request: Request):
    """Password login.  Rate limited per ACCOUNT (8 failures / 15 min, keyed
    on the normalized address whether or not it exists — so the lockout itself
    reveals nothing) and per IP (30 / 15 min)."""
    ip = _ip(request)
    email = U._norm(req.email)
    _locked(email, ip)
    status, _rec = U.authenticate(email, req.password)
    if status == "bad":
        E.LOGIN_ACCOUNT.hit(email)
        E.LOGIN_IP.hit(ip)
        S.record_event("login_failed", email=email, reason="bad_password",
                       ip=ip, path="/api/auth/login")
        # One message for wrong password AND unknown user — no user enumeration.
        raise HTTPException(401, detail="wrong email or password")
    # Past this point the caller holds the correct password.
    if status == "unverified":
        raise HTTPException(403, detail=(
            "confirm your e-mail first — use the link we sent you"))
    if status == "disabled":
        raise HTTPException(403, detail="this account is disabled")
    E.LOGIN_ACCOUNT.clear(email)
    token = _start_session(email, method="password", request=request)
    log.info("auth: password login ok for %s from %s", email, ip)
    return {"token": token, "user": U.public_user(email)}


# ── self-service registration / verification / reset ─────────────────────────

_ACCEPTED = {"ok": True, "message": (
    "If this address can be used, we sent it a link. Check your inbox.")}


def _mail_allowed(email: str, ip: str) -> bool:
    """Mail-sending endpoints: a flooded IP gets 429 (says nothing about any
    account); a flooded ADDRESS is silently not mailed again (a 429 there
    would reveal that the address had been asked about)."""
    if E.MAIL_IP.blocked(ip):
        raise HTTPException(429, detail="too many requests — try again later")
    E.MAIL_IP.hit(ip)
    if E.MAIL_ACCOUNT.blocked(email):
        return False
    E.MAIL_ACCOUNT.hit(email)
    return True


def _send_verification(email: str) -> None:
    token = U.issue_link_token(email, "verify")
    subj, body = E.verify_mail(email, token)
    if not E.send(email, subj, body):
        U.mark_pending_approval(email, "smtp_not_configured")
        log.warning("auth: SMTP not configured — verification link for %s "
                    "(or approve in Admin -> Pending sign-ups): %s",
                    email, E.link("verify", token))


def _valid_email(email: str) -> bool:
    if not email or len(email) > 254 or email.count("@") != 1:
        return False
    if any(c.isspace() for c in email):
        return False
    local, dom = email.split("@")
    return bool(local) and "." in dom and not dom.startswith(".") \
        and not dom.endswith(".")


class RegisterReq(BaseModel):
    email: str
    password: str
    name: str = ""
    newsletter: bool = False        # the sign-up checkbox (unchecked default)


def _newsletter_after_proof(email: str) -> None:
    """The address is proven now: mail the newsletter double opt-in link if
    the sign-up checkbox was ticked (one mail at a time, never both at once)."""
    try:
        from motor_ai_sim import newsletter as N
        if N.has_unsent_pending(email):
            N.send_confirmation(email)
    except Exception as e:                                   # noqa: BLE001
        log.warning("newsletter: confirmation after sign-up failed: %s", e)


@router.post("/register", status_code=202)
def register(req: RegisterReq, request: Request):
    """Create an unverified account and mail the confirmation link.

    The answer is IDENTICAL whether the address was new, pending or already
    registered — only format and password-policy errors (which depend on the
    input alone) are reported."""
    ip = _ip(request)
    email = U._norm(req.email)
    if not _valid_email(email):
        raise HTTPException(422, detail="enter a valid e-mail address")
    try:
        U.check_password_policy(req.password, email)
    except U.PasswordPolicyError as e:
        raise HTTPException(422, detail=str(e))
    if not (req.name or "").strip():
        raise HTTPException(422, detail="enter your name")
    may_mail = _mail_allowed(email, ip)
    outcome = U.register_self(email, req.password, req.name.strip())
    S.record_event("register", email=email, reason=outcome, ip=ip,
                   path="/api/auth/register")
    if outcome == "created" and req.newsletter:
        # Consent recorded now; the confirmation mail follows the address
        # proof (/verify or admin approval).  Existing accounts: ignored.
        from motor_ai_sim import newsletter as N
        N.record_pending(email, source="signup", ip=ip)
    if may_mail:
        if outcome in ("created", "exists_unverified"):
            _send_verification(email)
        else:
            subj, body = E.exists_mail(email)
            E.send(email, subj, body)
    elif outcome == "created":
        # Throttled address, fresh row: still make it reachable for the admin.
        U.mark_pending_approval(email, "mail_throttled")
    return _ACCEPTED


class TokenReq(BaseModel):
    token: str


@router.post("/verify")
def verify_email(req: TokenReq, request: Request):
    email = U.consume_link_token(req.token, "verify")
    if not email:
        raise HTTPException(400, detail=(
            "this link is invalid, already used or expired — sign up again "
            "to get a new one"))
    U.mark_verified(email, by="link")
    S.record_event("verified", email=email, reason="link", ip=_ip(request),
                   path="/api/auth/verify")
    _newsletter_after_proof(email)
    return {"ok": True, "email": email}


class EmailReq(BaseModel):
    email: str


@router.post("/reset/request", status_code=202)
def reset_request(req: EmailReq, request: Request):
    """Mail a reset link if the account exists.  Same answer either way.
    Also the way a Google-only account gets a password."""
    ip = _ip(request)
    email = U._norm(req.email)
    if not _valid_email(email):
        raise HTTPException(422, detail="enter a valid e-mail address")
    may_mail = _mail_allowed(email, ip)
    rec = U.get_user(email)
    if may_mail and rec is not None and not rec.get("disabled"):
        token = U.issue_link_token(email, "reset")
        subj, body = E.reset_mail(email, token)
        if not E.send(email, subj, body):
            log.warning("auth: SMTP not configured — password-reset link for "
                        "%s: %s", email, E.link("reset", token))
        S.record_event("reset_requested", email=email, ip=ip,
                       path="/api/auth/reset/request")
    return _ACCEPTED


class ResetReq(BaseModel):
    token: str
    password: str


@router.post("/reset/confirm")
def reset_confirm(req: ResetReq, request: Request):
    """Set a new password from a reset link; every session is revoked."""
    try:
        U.check_password_policy(req.password)
    except U.PasswordPolicyError as e:
        raise HTTPException(422, detail=str(e))
    email = U.consume_link_token(req.token, "reset")
    if not email:
        raise HTTPException(400, detail=(
            "this link is invalid, already used or expired — ask for a new one"))
    try:
        U.reset_password(email, req.password)
    except U.PasswordPolicyError as e:
        raise HTTPException(422, detail=str(e))
    except KeyError:
        raise HTTPException(400, detail="this link is invalid")
    n = S.revoke_all(email)
    E.LOGIN_ACCOUNT.clear(email)
    S.record_event("password_reset", email=email, reason=f"revoked {n}",
                   ip=_ip(request), path="/api/auth/reset/confirm")
    log.info("auth: password reset for %s, %d session(s) revoked", email, n)
    return {"ok": True, "email": email}


@router.get("/methods")
def auth_methods():
    """What the login form may offer (no secrets, anonymous)."""
    from motor_ai_sim.auth import GOOGLE_CLIENT_ID
    return {"google": bool(GOOGLE_CLIENT_ID), "password": True,
            "register": True, "mail": E.smtp_configured()}


# ── admin: pending accounts ──────────────────────────────────────────────────

@router.get("/pending")
def pending_list(_admin: dict = Depends(require_admin)):
    _AA.record(_AA.actor_of(_admin), "session.list", str("pending"), subject=str(""), details=None)
    return {"pending": U.list_pending(), "smtp": E.smtp_configured()}


@router.post("/pending/{email}/approve")
def pending_approve(email: str, _admin: dict = Depends(require_admin)):
    _AA.record(_AA.actor_of(_admin), "user.approve", str(email), subject=str(email), details=None)
    if U.get_user(email) is None:
        raise HTTPException(404, detail=f"user '{email}' not found")
    U.mark_verified(email, by="admin")
    S.record_event("verified", email=U._norm(email), reason="admin",
                   path="/api/auth/pending/approve")
    _newsletter_after_proof(U._norm(email))
    log.info("auth: %s approved by admin %s", email, (_admin or {}).get("email"))
    return {"ok": True, "user": U.public_user(email)}


class GoogleReq(BaseModel):
    credential: str            # the GIS ID token from the Google button
    newsletter: bool = False   # checkbox under the button; FIRST sign-in only


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
    first_sign_in = U.get_user(email) is None
    tier = _registry_tier(email)
    if tier == "__disabled__":
        raise HTTPException(403, detail="this account is disabled")
    # Same address by password and by Google = one account.  Google's proof
    # of the mailbox verifies a pending password account (and discards its
    # unproven password — see users.link_google).
    U.link_google(email)
    token = _start_session(email, method="google", request=request)
    if first_sign_in and req.newsletter:
        # Google proved the mailbox; the newsletter still needs its own
        # double opt-in click (consent != mailbox ownership).
        try:
            from motor_ai_sim import newsletter as N
            N.request_subscribe(email, source="google", ip=_ip(request))
        except Exception as e:                               # noqa: BLE001
            log.warning("newsletter: google sign-up consent failed: %s", e)
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
    _AA.record(_AA.actor_of(_admin), "user.create", str(req.email), subject=str(req.email), details={"tier": req.tier})
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
    _AA.record(_AA.actor_of(_admin), "user.update", str(email), subject=str(email), details={k: v for k, v in req.model_dump().items() if v is not None and k != "password"} | {"password_reset": req.password is not None})
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
    """Admin delete = the same full purge as a self-service deletion after its
    grace period (audit 2026-09-29 #5): users.json, workspace, published work,
    sessions, agent keys, OAuth grants, newsletter, support requests; audit
    logs pseudonymised.  See motor_ai_sim.account_lifecycle."""
    from motor_ai_sim import account_lifecycle as AL
    _AA.record(_AA.actor_of(_admin), "user.delete", str(email), subject=str(email),
               details={"mode": "purge"})
    if U.get_user(email) is None:
        raise HTTPException(404, detail=f"user '{email}' not found")
    rep = AL.purge(email, actor=_AA.actor_of(_admin), reason="admin")
    log.warning("auth: account %s PURGED by admin", rep["subject"])
    return {"ok": rep["ok"], "subject": rep["subject"], "steps": rep["steps"]}


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
        U.check_password_policy(req.new_password, me["email"])
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
