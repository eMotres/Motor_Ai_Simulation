"""The wrong-region answer: a signed-in account served by the other region's server.

Consistent with the China pattern (host nginx sends a mainland visitor's PAGE loads to cn.aerostator.com with a 302
and never touches /api/): the edge routes anonymous visitors by geo (stage 4), and THIS guard handles signed-in
accounts, whose home region the edge cannot know.

* An ``/api/`` request with a valid bearer of a NON-staff account whose home region is not this server's region gets
  ``421 Misdirected Request`` and the JSON body
  ``{"detail": {"code": "wrong_region", "region": "us", "base_url": "https://us.aerostator.com", "message": ...}}``
  plus the header ``X-Motres-Region: <this region>``.  421 (not 409) because the request is fine — it was sent to the
  wrong server — and nothing on this server changed.  The web (``lib/regionRedirect.ts``) answers it by moving the tab
  to ``base_url`` + the current path: that IS the page redirect (page loads are served by nginx and carry no bearer).
* Never guarded: staff (admin / engineer work on both servers), anonymous callers, an invalid token (the role gate
  answers those), and the paths a person must reach on either server — health, version, region info, the peer
  channel, sign-in / sign-out / account mails, the legal texts, node agents.

Inactive unless ``MOTRES_REGION_GUARD=1`` AND a peer is configured (config.guard_enabled): in single-region mode the
middleware is one env read per request and passes everything.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from motor_ai_sim.regions import config as RC

log = logging.getLogger(__name__)

STATUS = 421
CODE = "wrong_region"
MESSAGE = "This account is served by another region."
STAFF_ROLES = frozenset({"admin", "engineer"})
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


def misdirected_region(token: str) -> Optional[str]:
    """The home region of the token's account when it is NOT this server's region and the account is not staff."""
    if not token:
        return None
    from motor_ai_sim import users as U
    from motor_ai_sim.regions import store as RS
    try:
        res = U.verify_token(token)
    except Exception:                                       # noqa: BLE001 - the role gate owns token errors
        return None
    if not res.ok:
        return None
    email = str((res.user or {}).get("email") or res.email or "")
    rec = U.get_user(email) or {}
    if str(rec.get("role") or "user") in STAFF_ROLES:
        return None
    home = RS.effective_region(rec)
    return home if home != RC.local_region() else None


def body_for(region: str) -> dict:
    return {"detail": {"code": CODE, "region": region, "base_url": RC.base_url(region), "message": MESSAGE}}


class RegionGuardMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not RC.guard_enabled() or exempt(scope.get("path") or "/"):
            return await self.app(scope, receive, send)
        region = misdirected_region(_bearer(scope.get("headers") or []))
        if region is None:
            return await self.app(scope, receive, send)
        payload = json.dumps(body_for(region)).encode("utf-8")
        await send({"type": "http.response.start", "status": STATUS,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()),
                                (b"x-motres-region", RC.local_region().encode())]})
        await send({"type": "http.response.body", "body": payload})


def install(app) -> None:
    app.add_middleware(RegionGuardMiddleware)
