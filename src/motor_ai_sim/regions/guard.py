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
    """``(email, record)`` of a VALID bearer, else None."""
    from motor_ai_sim import users as U
    try:
        res = U.verify_token(token)
    except Exception:                                       # noqa: BLE001 - the role gate owns token errors
        return None
    if not res.ok:
        return None
    email = U._norm(str((res.user or {}).get("email") or res.email or ""))
    rec = U.get_user(email)
    return (email, rec) if isinstance(rec, dict) else None


def _worth_verifying(token: str) -> bool:
    """Cheap pre-check: verify the bearer only when the global guard is on or it names a flagged account.  The
    unverified claim only ever SKIPS work; every refusal below is decided on the verified token."""
    if not token:
        return False
    if RC.guard_enabled():
        return True
    from motor_ai_sim import users as U
    from motor_ai_sim.regions import store as RS
    flagged = RS.flagged_emails()
    if not flagged:
        return False
    claims = U._unverified(token)
    return U._norm(str(claims.get("email") or claims.get("sub") or "")) in flagged


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
    got = _account(token)
    if got is None:
        return None
    email, rec = got
    region = _misdirected(rec)
    if region is not None:
        from motor_ai_sim.regions import handoff as H
        return STATUS, body_for(region, H.mint(email, region, method="session", rec=rec))
    from motor_ai_sim.regions import store as RS
    if RS.is_frozen(rec) and (method or "GET").upper() not in SAFE_METHODS:
        return FROZEN_STATUS, {"detail": {"code": FROZEN_CODE, "message": FROZEN_MESSAGE}}
    return None


class RegionGuardMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or exempt(scope.get("path") or "/"):
            return await self.app(scope, receive, send)
        try:
            verdict = decide(_bearer(scope.get("headers") or []), str(scope.get("method") or "GET"))
        except Exception:                                   # noqa: BLE001 - the guard never takes the site down
            log.exception("regions: guard check failed; request passed")
            verdict = None
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
