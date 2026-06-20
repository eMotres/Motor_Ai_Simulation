"""Firebase ID-token verification + tier gating for expensive endpoints.

Stateless and credential-free: a Firebase ID token is an RS256-signed JWT.
We verify its signature against Google's public x509 certs (cached, public —
no service account needed), then check audience == project, issuer, expiry.
This runs fine on Cloud Run with zero secrets.

Gating is OFF unless the env var AUTH_ENFORCE is truthy, so local development
is completely unaffected (every endpoint stays open). In production we set
AUTH_ENFORCE=1 and ADMIN_EMAILS=<owner emails>; those accounts get the highest
tier, everyone else is 'free'. Per-user paid tiers (Stripe) will later arrive
as a custom claim ('tier') on the token, which _tier_for() already honours.

Only a small allowlist of genuinely expensive endpoints is gated (live FEM,
optimization, CAD generation). Catalog / presets / geometry / analytical
preview stay free for everyone.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
import urllib.request
from typing import Optional

from fastapi import Header, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Lazy/defensive import: if pyjwt+cryptography are absent the module still loads;
# verification then raises and (when enforcing) the gate fails closed.
try:
    import jwt
    from cryptography.x509 import load_pem_x509_certificate
    _HAS_JWT = True
except Exception:  # pragma: no cover
    _HAS_JWT = False

PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "aerostator-core-simulation")
_CERT_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)
_ISSUER = f"https://securetoken.google.com/{PROJECT_ID}"

AUTH_ENFORCE = os.environ.get("AUTH_ENFORCE", "0").strip().lower() in (
    "1", "true", "yes", "on",
)
_ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
}

# Optional static token for headless read-only automation (the nightly tickets
# agent). When set, a `Bearer <ADMIN_API_TOKEN>` header authenticates admin READ
# endpoints without a Firebase session. NEVER honour it on mutating endpoints.
ADMIN_API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "").strip()

_TIER_RANK = {"anon": -1, "free": 0, "pro": 1, "team": 2, "admin": 3}

# (HTTP method, exact path) -> minimum tier required to call it.
# Everything not listed here is open to all (free + anonymous).
_GATED: dict[tuple[str, str], str] = {
    ("GET",  "/api/simulation/physics/fem_transient"): "pro",
    ("GET",  "/api/simulation/physics/fem_field2d"): "pro",
    ("GET",  "/api/simulation/physics/torque_sweep"): "pro",
    ("GET",  "/api/simulation/mesh/build2d"): "pro",
    ("GET",  "/api/simulation/mesh/build2d_sliding_band"): "pro",
    ("POST", "/api/optimization/run"): "pro",
    ("POST", "/api/optimization/refine"): "pro",
    ("POST", "/api/optimization/scan"): "pro",
    ("POST", "/api/pipeline/generate"): "pro",
    # AI support assistant calls the paid Anthropic API — require a signed-in
    # account (>= free) so an anonymous visitor can't run up the bill.
    ("POST", "/api/support/chat"): "free",
}

_certs: dict = {}
_certs_exp: float = 0.0
_lock = threading.Lock()


def _load_certs(force: bool = False) -> dict:
    """Fetch + cache Google's securetoken x509 public keys, keyed by `kid`."""
    global _certs, _certs_exp
    with _lock:
        if not force and _certs and time.time() < _certs_exp:
            return _certs
        with urllib.request.urlopen(_CERT_URL, timeout=5) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            cache_control = resp.headers.get("Cache-Control", "") or ""
        max_age = 3600
        for tok in cache_control.split(","):
            tok = tok.strip()
            if tok.startswith("max-age="):
                try:
                    max_age = int(tok.split("=", 1)[1])
                except ValueError:
                    pass
        _certs = {
            kid: load_pem_x509_certificate(pem.encode("utf-8")).public_key()
            for kid, pem in raw.items()
        }
        _certs_exp = time.time() + max(60, max_age)
        return _certs


def verify_id_token(id_token: str) -> dict:
    """Return verified claims for a Firebase ID token, or raise on any failure."""
    if not _HAS_JWT:
        raise RuntimeError("pyjwt/cryptography not installed")
    kid = jwt.get_unverified_header(id_token).get("kid")
    key = _load_certs().get(kid) or _load_certs(force=True).get(kid)
    if key is None:
        raise jwt.InvalidTokenError("unknown key id")
    return jwt.decode(
        id_token,
        key=key,
        algorithms=["RS256"],
        audience=PROJECT_ID,
        issuer=_ISSUER,
        options={"require": ["exp", "iat", "sub"]},
    )


def _tier_for(claims: dict) -> str:
    email = (claims.get("email") or "").strip().lower()
    if email and email in _ADMIN_EMAILS:
        return "admin"
    claimed = claims.get("tier")
    if claimed in _TIER_RANK:
        return claimed
    return "free"


def resolve_user(authorization: Optional[str]) -> Optional[dict]:
    """Parse a `Bearer <idToken>` header → {uid,email,tier}, or None if absent/invalid."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_id_token(token)
    except Exception:
        return None
    return {
        "uid": claims.get("user_id") or claims.get("sub"),
        "email": (claims.get("email") or "").lower(),
        "tier": _tier_for(claims),
    }


class TierGateMiddleware(BaseHTTPMiddleware):
    """Block expensive endpoints for users below the required tier.

    No-op unless AUTH_ENFORCE is on. CORS preflight (OPTIONS) is never gated.
    """

    async def dispatch(self, request: Request, call_next):
        if AUTH_ENFORCE and request.method != "OPTIONS":
            need = _GATED.get((request.method, request.url.path))
            if need is not None:
                user = resolve_user(request.headers.get("authorization"))
                tier = user["tier"] if user else "anon"
                if _TIER_RANK.get(tier, -1) < _TIER_RANK[need]:
                    return JSONResponse(
                        status_code=401 if user is None else 403,
                        content={
                            "detail": (
                                "Sign in to use this feature."
                                if user is None
                                else f"This feature requires the '{need}' plan."
                            ),
                            "required_tier": need,
                            "your_tier": tier,
                        },
                    )
        return await call_next(request)


def install_tier_gate(app) -> None:
    """Attach the tier gate. Call BEFORE adding CORS so CORS stays outermost
    and 403/401 responses still carry CORS headers for the browser."""
    app.add_middleware(TierGateMiddleware)


def _is_admin_caller(authorization: Optional[str]) -> tuple[bool, Optional[dict]]:
    """(is_admin, user|None). Admin iff the signed-in account is admin-tier
    (email in ADMIN_EMAILS, or a 'tier=admin' claim).

    Local/unconfigured dev — no enforcement AND no ADMIN_EMAILS configured — is
    treated as admin so the admin UI is reachable without credentials. As soon
    as ADMIN_EMAILS is set (which production must do), only those accounts are
    admin, even with AUTH_ENFORCE off — so the admin page never leaks to ordinary
    visitors."""
    user = resolve_user(authorization)
    if not AUTH_ENFORCE and not _ADMIN_EMAILS:
        return True, user
    return (user is not None and user.get("tier") == "admin"), user


def account_info(authorization: Optional[str]) -> dict:
    """Resolve who's calling -> {uid,email,tier,isAdmin,enforced} for /api/me."""
    is_admin, user = _is_admin_caller(authorization)
    return {
        "uid": user["uid"] if user else ("local-dev" if is_admin else None),
        "email": user["email"] if user else None,
        "tier": "admin" if is_admin else (user["tier"] if user else "anon"),
        "isAdmin": is_admin,
        "enforced": AUTH_ENFORCE,
    }


def require_admin(authorization: Optional[str] = Header(default=None)) -> dict:
    """FastAPI dependency: allow only admin callers (see _is_admin_caller).
    A non-admin gets 403, an anonymous caller 401."""
    is_admin, user = _is_admin_caller(authorization)
    if not is_admin:
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in required.")
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user or {"uid": "local-dev", "email": None, "tier": "admin"}


def _has_service_token(authorization: Optional[str]) -> bool:
    """True if the request carries the configured ADMIN_API_TOKEN as a bearer."""
    if not ADMIN_API_TOKEN or not authorization:
        return False
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].strip(), ADMIN_API_TOKEN)


def require_admin_or_token(authorization: Optional[str] = Header(default=None)) -> dict:
    """Like require_admin, but ALSO accepts the static ADMIN_API_TOKEN bearer for
    headless read-only automation (the nightly tickets agent). Apply ONLY to GET
    read endpoints — mutating endpoints must keep require_admin."""
    if _has_service_token(authorization):
        return {"uid": "service-agent", "email": None, "tier": "admin", "service": True}
    return require_admin(authorization)
