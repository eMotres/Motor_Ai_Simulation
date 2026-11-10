"""The wrong-region answer: a signed-in account served by the other region's server.

Consistent with the China pattern (host nginx sends a mainland visitor's PAGE loads to cn.aerostator.com with a 302
and never touches /api/): the edge routes anonymous visitors by geo (stage 4), and THIS guard handles signed-in
accounts, whose home region the edge cannot know.

* An ``/api/`` request with a valid bearer of a NON-staff account whose home region is not this server's region gets
  ``421 Misdirected Request`` and the JSON body
  ``{"detail": {"code": "wrong_region", "region": "us", "base_url": "https://us.aerostator.com", "message": ...,
  "handoff": "<ticket>"}}`` plus the header ``X-Motres-Region: <this region>``.  421 (not 409) because the request is
  fine — it was sent to the wrong server — and nothing on this server changed.  The web (``lib/regionRedirect.ts``)
  answers it by moving the tab to ``base_url`` + the current path, through ``/region-handoff`` when a ticket came with
  it (``regions/handoff.py``: the caller's valid bearer is its proof), so the person is not asked to sign in again.
* WHEN: globally with ``MOTRES_REGION_GUARD=1`` and a configured peer (config.guard_enabled), or PER ACCOUNT once its
  migration has run (``region_guard`` on its record, regions/store.py) — independent of the global switch, which stays
  off.  An account whose home region is the peer but which was not migrated keeps being served here.
* FROZEN accounts (``region_freeze``, set while the migration builds the bundle) get ``423 Locked``
  ``{"detail": {"code": "account_migrating"}}`` on every write (any method but GET / HEAD / OPTIONS); reads go on.
* Never guarded: staff (admin / engineer work on both servers), anonymous callers, an invalid token (the role gate
  answers those), and the paths a person must reach on either server — health, version, region info, the peer
  channel, sign-in / sign-out / account mails, the legal texts, node agents.

Cost: in single-region mode with no flagged account the middleware is two env reads and one ``stat`` of the registry
per request; a bearer is verified only when the global guard is on or its (unverified) e-mail is a flagged account.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, Tuple

from motor_ai_sim.regions import config as RC

log = logging.getLogger(__name__)

STATUS = 421
CODE = "wrong_region"
MESSAGE = "This account is served by another region."
FROZEN_STATUS = 423
FROZEN_CODE = "account_migrating"
FROZEN_MESSAGE = "This account is being moved to its home server; changes are paused for a few minutes."
STAFF_ROLES = frozenset({"admin", "engineer"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
EXEMPT_PREFIXES = ("/api/health", "/api/version", "/api/region", "/api/peer/", "/api/auth/", "/api/legal/",
                   "/api/admin/nodes/metrics", "/api/nodes/", "/api/account/export/download", "/api/comm-prefs",
                   "/api/newsletter/unsubscribe", "/api/verification/reply")


def exempt(path: str) -> bool:
    p = path or "/"
    if not p.startswith("/api/"):
        return True
    for x in EXEMPT_PREFIXES:
        base = x.rstrip("/")
        if p == base or p.startswith(base + "/"):
            return True
    return False


def _bearer(headers) -> str:
    for k, v in headers:
        if k == b"authorization":
            s = v.decode("latin-1", "replace").strip()
            return s[7:].strip() if s[:7].lower() == "bearer " else ""
    return ""


def _account(token: str) -> Optional[Tuple[str, dict]]:
    """``(email, record)`` of a VALID bearer, else None.  Every bearer the role gate accepts on ``/api/`` counts: OUR
    token, and a raw Google ID token (auth.resolve_user_detail step 2) - a guard that only knew our tokens let a Google
    token write to a frozen or migrated account.  ``/mcp`` keys and OAuth tokens: :func:`account_block`."""
    got = _account_kind(token)
    return (got[0], got[1]) if got is not None else None


def _account_kind(token: str) -> Optional[Tuple[str, dict, str]]:
    """``(email, record, "session" | "google")`` of a valid bearer."""
    from motor_ai_sim import users as U
    res = U.verify_token(token)
    if res.ok:
        email = U._norm(str((res.user or {}).get("email") or res.email or ""))
        kind = "session"
    elif res.reason in ("expired", "revoked", "disabled", "unknown_user", "idle_timeout", "store_unavailable"):
        return None                                         # definitively ours and bad: the role gate answers it
    else:
        from motor_ai_sim import auth as A
        claims = A._verify_google_token(token)
        if not claims or claims.get("email_verified") is not True:
            return None
        email = U._norm(str(claims.get("email") or ""))
        kind = "google"
    rec = U.get_user(email)
    return (email, rec, kind) if isinstance(rec, dict) else None


def account_block(rec: Optional[dict], method: str = "POST") -> Optional[str]:
    """Why this account may not act on THIS server right now: ``wrong_region`` (migrated away, or the global guard),
    ``account_migrating`` (frozen; ``method`` decides for /api - the MCP channel passes a write, it cannot tell), or
    None.  The one rule for every channel: the /api middleware below and ``mcp_app.verify_any``."""
    if not isinstance(rec, dict):
        return None
    from motor_ai_sim.regions import store as RS
    if _misdirected(rec) is not None:
        return CODE
    if RS.is_frozen(rec) and (method or "GET").upper() not in SAFE_METHODS:
        return FROZEN_CODE
    return None


def _flagged_claim(token: str) -> bool:
    """The token's UNVERIFIED e-mail claim names a flagged account (cheap: it only ever skips work or, after a crash,
    keeps a write out)."""
    from motor_ai_sim import users as U
    from motor_ai_sim.regions import store as RS
    flagged = RS.flagged_emails()
    if not flagged or not token:
        return False
    claims = U._unverified(token)
    return U._norm(str(claims.get("email") or claims.get("sub") or "")) in flagged


def _worth_verifying(token: str) -> bool:
    """Cheap pre-check: verify the bearer only when the global guard is on or it names a flagged account.  The
    unverified claim only ever SKIPS work; every refusal below is decided on the verified token."""
    if not token:
        return False
    if RC.guard_enabled():
        return True
    return _flagged_claim(token)


def misdirected_region(token: str) -> Optional[str]:
    """The home region of the token's account when it is NOT this server's region, the guard applies to it (global or
    per account) and the account is not staff."""
    got = _account(token) if token else None
    if got is None:
        return None
    return _misdirected(got[1])


def _misdirected(rec: dict) -> Optional[str]:
    from motor_ai_sim.regions import store as RS
    if str(rec.get("role") or "user") in STAFF_ROLES:
        return None
    home = RS.effective_region(rec)
    if home == RC.local_region():
        return None
    return home if (RC.guard_enabled() or RS.is_guarded(rec)) else None


def body_for(region: str, ticket: str = "") -> dict:
    d = {"code": CODE, "region": region, "base_url": RC.base_url(region), "message": MESSAGE}
    if ticket:
        d["handoff"] = ticket
    return {"detail": d}


def decide(token: str, method: str) -> Optional[Tuple[int, dict]]:
    """-> ``(status, body)`` to answer instead of the route, or None to pass."""
    if not _worth_verifying(token):
        return None
    got = _account_kind(token)
    if got is None:
        return None
    email, rec, kind = got
    block = account_block(rec, method)
    if block == CODE:
        from motor_ai_sim.regions import handoff as H
        region = _misdirected(rec) or ""
        return STATUS, body_for(region, H.mint(email, region, method=kind, rec=rec))
    if block == FROZEN_CODE:
        return FROZEN_STATUS, frozen_body()
    return None


def frozen_body() -> dict:
    return {"detail": {"code": FROZEN_CODE, "message": FROZEN_MESSAGE}}


class RegionGuardMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or exempt(scope.get("path") or "/"):
            return await self.app(scope, receive, send)
        token, method = _bearer(scope.get("headers") or []), str(scope.get("method") or "GET")
        try:
            verdict = decide(token, method)
        except Exception:                                   # noqa: BLE001 - the guard never takes the site down
            # ... but never lets a write of a flagged (frozen / migrated) account through either: fail closed for those
            verdict = None
            try:
                if method.upper() not in SAFE_METHODS and _flagged_claim(token):
                    verdict = (FROZEN_STATUS, frozen_body())
            except Exception:                               # noqa: BLE001
                verdict = None
            log.exception("regions: guard check failed; %s", "write refused" if verdict else "request passed")
        if verdict is None:
            return await self.app(scope, receive, send)
        status, body = verdict
        payload = json.dumps(body).encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()),
                                (b"x-motres-region", RC.local_region().encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": payload})


def install(app) -> None:
    app.add_middleware(RegionGuardMiddleware)
