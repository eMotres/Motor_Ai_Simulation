"""Login and user administration for the self-hosted deployment.

Identity comes from Google sign-in (GIS ID tokens, verified in auth.py) or
from a password account below; RIGHTS always come from our registry
(config/users.json + ADMIN_EMAILS).  This router adds:

- POST /api/auth/login        — password login → our HS256 token
- GET  /api/auth/users        — admin: list accounts
- POST /api/auth/users        — admin: create a password account
- PATCH /api/auth/users/{email} — admin: role / disabled / name / new password
- DELETE /api/auth/users/{email} — admin: remove an account
- POST /api/auth/password     — self-service password change (signed in)
- GET  /api/auth/sessions     — the caller's own sessions
- POST /api/auth/sessions/{sid}/revoke — sign one of them out
- POST /api/auth/logout       — revoke the session this request is using
- POST /api/auth/register     — self-service e-mail/password sign-up (unverified)
- POST /api/auth/verify       — consume the e-mail confirmation link
- POST /api/auth/register/resend        — mail the confirmation link again (rate-limited)
- POST /api/auth/register/change-email  — an unverified account fixes a mistyped address (password proves it is theirs)
- POST /api/auth/reset/request, /reset/confirm — password reset by link
- GET  /api/auth/methods      — which sign-in methods the form may offer
- GET  /api/auth/pending, POST /api/auth/pending/{email}/approve — admin

Login is rate-limited in-memory per account and per IP (auth_email.Limiter);
register/reset answers never reveal whether an address has an account.  The one thing they DO say, because it depends on the
input alone, is that the DOMAIN of the address does not exist (email_domain.py): a mistyped domain bounces every mail and locks the
person out, so it is refused before anything is sent.

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
from motor_ai_sim import email_domain as ED
from motor_ai_sim.api_errors import ApiError
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
    """Client IP for the per-IP limits: forwarding headers are honoured only
    from a trusted proxy (``motor_ai_sim.client_ip``, env TRUSTED_PROXIES)."""
    from motor_ai_sim import client_ip as _cip
    return _cip.from_request(request) or "?"


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


def _check_domain(email: str) -> None:
    """422 when the domain of the address is known not to exist (nothing is sent, nothing is created).  A resolver that cannot
    tell (timeout, no network) never blocks: the mail is sent."""
    c = ED.check(email)
    if not c.ok:
        code = "auth.email_domain_unknown_suggest" if c.suggestion else "auth.email_domain_unknown"
        raise ApiError(422, code, ED.message(c), {"domain": c.domain, "suggestion": c.suggestion})


def _send_verification(email: str, return_path: str = "", return_host: str = "") -> None:
    token = U.issue_link_token(email, "verify")
    subj, body = E.verify_mail(email, token, return_path, return_host)
    if not E.send(email, subj, body, kind="verify"):
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
    newsletter: bool = False        # RETIRED: accepted for old clients, ignored (see comm_prefs below)
    #: The four opt-in topics of the communication preferences ({topic: bool}, all unticked by default) and the language the
    #: wording was shown in; recorded with the consent proof (motor_ai_sim.comm_prefs) once the account exists.
    comm_prefs: Optional[dict] = None
    lang: str = "en"
    #: Sign-up started inside an OAuth authorization window (MCP connector):
    #: the consent-page path ``/agent-consent?request=<id>``.  A NEW account
    #: then gets a single-use continuation bound to {request, this address};
    #: the confirmation link carries it (docs/MCP_DISCOVERY.md "Sign-up").
    return_to: Optional[str] = None


def _oauth_rid(return_to: Optional[str]) -> Optional[str]:
    """None, or the id of the PENDING request whose consent page ``return_to``
    is (loud 422 / 410 otherwise)."""
    if not return_to:
        return None
    from motor_ai_sim import oauth as O
    rid = O.request_id_from_return_path(return_to)
    if rid is None:
        raise HTTPException(422, detail="return_to must be the consent page of a "
                                        "pending authorization")
    if O.describe_request(rid) is None:
        raise HTTPException(410, detail=(
            "this authorization request expired; start connecting again from "
            "your AI app"))
    return rid


def _continuation_path(rid: Optional[str], email: str) -> tuple[str, str]:
    """(link path, return host) for the confirmation mail of a new account
    registered from a consent page; ('', '') when there is none."""
    if not rid:
        return "", ""
    from motor_ai_sim import oauth as O
    info = O.describe_request(rid) or {}
    secret = O.create_continuation(rid, email)
    if not secret:
        return "", ""
    import re
    from urllib.parse import urlencode
    host = re.sub(r"[^A-Za-z0-9.:\[\]-]", "", str(info.get("redirect_host") or ""))[:100]
    return O.consent_path(rid) + "&" + urlencode({"continue": secret}), host


def _comm_prefs_ticked(raw) -> dict:
    """The topics the sign-up form ticked ({topic: True}); loud 422 for anything that is not {known topic: bool}."""
    if raw in (None, {}):
        return {}
    from motor_ai_sim import comm_prefs as CP
    try:
        checked = CP._check(raw)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    return {t: True for t, v in checked.items() if v}


def _record_signup_topics(email: str, topics: dict, source: str, ip: str, lang: str) -> None:
    """Consent given at sign-up; a failure to store it never blocks the account (the user can tick the boxes again)."""
    try:
        from motor_ai_sim import comm_prefs as CP
        CP.set_topics(email, topics, source=source, ip=ip, lang=lang)
    except Exception as e:                                   # noqa: BLE001
        log.warning("comm_prefs: sign-up consent of a new account was not stored (%s)", type(e).__name__)


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
    _check_domain(email)
    if E.SIGNUP_IP.blocked(ip):
        raise HTTPException(429, detail="too many sign-ups from this network — try again later")
    rid = _oauth_rid(req.return_to)
    topics = _comm_prefs_ticked(req.comm_prefs)
    E.SIGNUP_IP.hit(ip)
    may_mail = _mail_allowed(email, ip)
    outcome = U.register_self(email, req.password, req.name.strip())
    S.record_event("register", email=email, reason=outcome, ip=ip,
                   path="/api/auth/register")
    if outcome == "created" and topics:
        # The ticked topics are recorded with the wording shown; mailing needs the verified address anyway.
        _record_signup_topics(email, topics, "signup", ip, req.lang)
    if may_mail:
        if outcome == "created":
            # Only a row THIS request created gets the continuation: an older
            # pending row was not necessarily registered by this browser.
            _send_verification(email, *_continuation_path(rid, email))
        elif outcome == "exists_unverified":
            _send_verification(email)
        else:
            subj, body = E.exists_mail(email)
            E.send(email, subj, body)
    elif outcome == "created":
        # Throttled address, fresh row: still make it reachable for the admin.
        U.mark_pending_approval(email, "mail_throttled")
    return _ACCEPTED


class ResendReq(BaseModel):
    email: str


@router.post("/register/resend", status_code=202)
def register_resend(req: ResendReq, request: Request):
    """Mail the confirmation link again to an unconfirmed account.  Same answer whatever the address is; limited per address
    (E.MAIL_ACCOUNT, 3 an hour) and per IP."""
    ip = _ip(request)
    email = U._norm(req.email)
    if not _valid_email(email):
        raise HTTPException(422, detail="enter a valid e-mail address")
    may_mail = _mail_allowed(email, ip)
    rec = U.get_user(email)
    if may_mail and rec is not None and not U.is_verified(rec) and not rec.get("disabled"):
        _send_verification(email)
    return _ACCEPTED


class ChangeEmailReq(BaseModel):
    email: str          # the address the account was registered with (a typo)
    password: str       # proves the account is the caller's
    new_email: str
    lang: str = "en"


@router.post("/register/change-email", status_code=202)
def register_change_email(req: ChangeEmailReq, request: Request):
    """An UNCONFIRMED account fixes a mistyped address and gets a new confirmation link there.

    The password proves the account is the caller's (failures count against the same limits as a sign-in).  A new address that
    already has an account changes nothing and gets the usual "someone tried to register" note - the answer is the same either
    way."""
    ip = _ip(request)
    old, new = U._norm(req.email), U._norm(req.new_email)
    if not _valid_email(old) or not _valid_email(new):
        raise HTTPException(422, detail="enter a valid e-mail address")
    _locked(old, ip)
    _check_domain(new)
    rec = U.get_user(old)
    if rec is None or U.is_verified(rec) or rec.get("disabled") or not U._verify_pw(rec, req.password):
        E.LOGIN_ACCOUNT.hit(old)
        E.LOGIN_IP.hit(ip)
        raise HTTPException(400, detail="could not change the address: check the e-mail and the password you registered with")
    if new == old:
        if _mail_allowed(old, ip):
            _send_verification(old)
        return _ACCEPTED
    if not _mail_allowed(new, ip):
        return _ACCEPTED
    if U.get_user(new) is not None:
        subj, body = E.exists_mail(new)
        E.send(new, subj, body)
        return _ACCEPTED
    try:
        U.change_unverified_email(old, new)
    except (KeyError, ValueError):
        raise HTTPException(400, detail="could not change the address: check the e-mail and the password you registered with")
    try:
        from motor_ai_sim import comm_prefs as CP
        CP.move(old, new, ip=ip)
    except Exception as e:                                   # noqa: BLE001 - the choices can be ticked again
        log.warning("comm_prefs: sign-up choices did not follow the address change (%s)", type(e).__name__)
    S.record_event("email_changed", email=new, reason="signup_typo", ip=ip, path="/api/auth/register/change-email")
    _send_verification(new)
    return _ACCEPTED


class TokenReq(BaseModel):
    token: str


class VerifyReq(TokenReq):
    #: The ``continue`` secret of the confirmation link (sign-up started in an
    #: OAuth window).  The return target is DERIVED from its stored record —
    #: a caller-supplied path is never accepted here.
    continuation: Optional[str] = None


@router.post("/verify")
def verify_email(req: VerifyReq, request: Request):
    email = U.consume_link_token(req.token, "verify")
    if not email:
        raise HTTPException(400, detail=(
            "this link is invalid, already used or expired — sign up again "
            "to get a new one"))
    U.mark_verified(email, by="link")
    S.record_event("verified", email=email, reason="link", ip=_ip(request),
                   path="/api/auth/verify")
    out = {"ok": True, "email": email}
    if req.continuation:
        # Only the continuation minted for THIS address at sign-up resumes an
        # authorization; it is single use and binds the request to the address.
        from motor_ai_sim import oauth as O
        rid = O.consume_continuation(req.continuation, email)
        out["authorization_pending"] = bool(rid)
        if rid:
            out["return_to"] = O.consent_path(rid)
    return out


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
    _check_domain(email)
    may_mail = _mail_allowed(email, ip)
    rec = U.get_user(email)
    if may_mail and rec is not None and not rec.get("disabled"):
        token = U.issue_link_token(email, "reset")
        subj, body = E.reset_mail(email, token)
        if not E.send(email, subj, body, kind="reset"):
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
    log.info("auth: %s approved by admin %s", email, (_admin or {}).get("email"))
    return {"ok": True, "user": U.public_user(email)}


class GoogleReq(BaseModel):
    credential: str            # the GIS ID token from the Google button
    newsletter: bool = False   # RETIRED: accepted for old clients, ignored
    comm_prefs: Optional[dict] = None   # the four opt-in topics under the button; FIRST sign-in only
    lang: str = "en"


@router.post("/google")
def google_login(req: GoogleReq, request: Request):
    """Exchange a Google ID token (1-hour life) for OUR 30-day HS256 token.
    Identity is Google's; the role comes from the registry (auto-provisioned
    on first sign-in) with ADMIN_EMAILS on top."""
    from motor_ai_sim.auth import _registry_role, _verify_google_token, GOOGLE_CLIENT_ID
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
    topics = _comm_prefs_ticked(req.comm_prefs) if first_sign_in else {}
    role = _registry_role(email)
    if role == "__disabled__":
        raise HTTPException(403, detail="this account is disabled")
    # Same address by password and by Google = one account.  Google's proof
    # of the mailbox verifies a pending password account (and discards its
    # unproven password — see users.link_google).
    U.link_google(email)
    token = _start_session(email, method="google", request=request)
    if topics:
        _record_signup_topics(email, topics, "google", _ip(request), req.lang)
    ip = (request.client.host if request and request.client else "?")
    log.info("auth: Google login ok for %s (role %s) from %s", email, role, ip)
    return {"token": token,
            "user": {**U.public_user(email), "role": role,
                     "name": claims.get("name") or U.public_user(email).get("name", "")}}


class CreateReq(BaseModel):
    email: str
    password: str
    role: str = "user"
    name: str = ""


@router.get("/users")
def users_list(_admin: dict = Depends(require_admin)):
    """Every account; ``cpu_h_month`` = CPU-hours the account's finished jobs used this UTC month (None when the usage store
    cannot be read)."""
    from motor_ai_sim import usage_stats as US
    used = US.month_cpu_h_by_user()
    rows = U.list_users()
    for r in rows:
        r["cpu_h_month"] = None if used is None else used.get(r["email"], 0.0)
    return {"users": rows}


@router.post("/users")
def users_create(req: CreateReq, _admin: dict = Depends(require_admin)):
    _AA.record(_AA.actor_of(_admin), "user.create", str(req.email), subject=str(req.email), details={"role": req.role})
    try:
        return {"ok": True, "user": U.create_user(req.email, req.password,
                                                  role=req.role, name=req.name)}
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


class PatchReq(BaseModel):
    role: Optional[str] = None
    disabled: Optional[bool] = None
    name: Optional[str] = None
    password: Optional[str] = None      # admin reset
    new_email: Optional[str] = None     # an UNVERIFIED account's address (a typo at sign-up); a new confirmation link is mailed there
    nda_signed: Optional[bool] = None   # 3D model menu: full model + STEP only when True
    nda_date: Optional[str] = None      # YYYY-MM-DD (default today)
    sim_quota_cpu_h_month: Optional[float] = None   # CPU-hours per month; send null to clear (stored only, not enforced)


@router.patch("/users/{email}")
def users_patch(email: str, req: PatchReq, _admin: dict = Depends(require_admin)):
    _AA.record(_AA.actor_of(_admin), "user.update", str(email), subject=str(email), details={k: v for k, v in req.model_dump().items() if v is not None and k != "password"} | {"password_reset": req.password is not None})
    try:
        if req.new_email is not None:
            new = U._norm(req.new_email)
            if not _valid_email(new):
                raise HTTPException(422, detail="enter a valid e-mail address")
            _check_domain(new)
            try:
                old_email, email = email, U.change_unverified_email(email, new)
            except ValueError as e:
                raise HTTPException(409, detail=str(e))
            if email != U._norm(old_email):
                try:
                    from motor_ai_sim import comm_prefs as CP
                    CP.move(old_email, email)
                except Exception:                              # noqa: BLE001
                    log.warning("comm_prefs: sign-up choices did not follow the admin's address change")
                _send_verification(email)
        before = (U.get_user(email) or {}).get("role", "user")
        if req.password is not None:
            U.set_password(email, req.password)
        if req.nda_signed is not None:
            _AA.record(_AA.actor_of(_admin), "user.nda", str(email), subject=str(email),
                       details={"signed": req.nda_signed, "date": req.nda_date})
            U.set_nda(email, req.nda_signed, req.nda_date, by=_AA.actor_of(_admin))
        quota = ({"sim_quota_cpu_h_month": req.sim_quota_cpu_h_month}
                 if "sim_quota_cpu_h_month" in req.model_fields_set else {})
        out = U.update_user(email, role=req.role, disabled=req.disabled,
                            name=req.name, **quota)
        if req.role is not None and out["role"] != before:
            _AA.record(_AA.actor_of(_admin), "user.role", str(email), subject=str(email),
                       details={"from": before, "to": out["role"]})
        log.info("auth: user %s updated (%s)", email,
                 {k: v for k, v in req.model_dump().items()
                  if v is not None and k != "password"})
        return {"ok": True, "user": out}
    except KeyError:
        raise HTTPException(404, detail=f"user '{email}' not found")
    except U.RoleProtected as e:
        raise HTTPException(409, detail=str(e))
    except U.EmailUnverified as e:
        raise ApiError(409, "email_unverified", str(e), {"email": U._norm(email), "gate": "role"})
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
