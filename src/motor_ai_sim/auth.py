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
import logging
import os
import threading
import time
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

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

# ── PUBLIC_EXHIBIT — is there an anonymous audience at all? ──────────────────
#
# Until 2026-09-16 an anonymous caller on the public server could read
# /api/family/tree (the "public exhibit": the dies carrying a passport card),
# their full geometry and duty payloads, and could BUILD THE REPORT
# (/api/family/report/...).  That was a deliberate marketing rule written when
# the app only ever ran on the owner's workstation (motor_access.MODE_ANONYMOUS)
# — and it is the wrong rule for an internet-facing host, where the standing
# requirement is that a new visitor sees NOTHING until an account is granted it.
#
# `PUBLIC_EXHIBIT=1` (the DEFAULT) is that old behaviour, byte for byte: this
# workstation and every test that does not set the variable are unaffected.
# `PUBLIC_EXHIBIT=0` closes it: a caller with no valid credentials gets 401 on
# every /api route except the three below, whatever AUTH_ENFORCE says — the two
# switches answer different questions ("which tier may run this?" vs "is anyone
# allowed in without signing in?") and closing the door must not depend on the
# tier table being enforced.
#
# Read PER CALL (not bound at import) so the server can flip it with a restart
# of the process only, and so a test can monkeypatch the environment.
_ENV_PUBLIC_EXHIBIT = "PUBLIC_EXHIBIT"


def public_exhibit() -> bool:
    """Is the anonymous public exhibit open?  Default: yes (today's behaviour)."""
    return os.environ.get(_ENV_PUBLIC_EXHIBIT, "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


#: The ONLY /api paths an anonymous caller may reach with the exhibit closed.
#: /api/health is the container's liveness probe; /api/me must answer (with the
#: anonymous shape) or the SPA cannot tell "not signed in" from "server down"
#: and never renders its login screen; /api/version is the header badge and the
#: frontend/backend skew check, which run BEFORE sign-in — it returns the app
#: version, a git sha and a build timestamp, and nothing about a machine.
#:
#: Every other pre-sign-in request the SPA makes (geometry, geometry/schema,
#: config, materials, parts, mesh/config, winding/config, sweep/config, modules,
#: family/tree, family/context, my_motors …) DOES describe a machine or the
#: catalog, so it stays closed and the page renders without it (2026-09-16: the
#: landing was checked against the live network log, not against a guess).
#:
#: 2026-09-17 adds ONE more: POST /api/support/chat, the "Help & feedback" chat
#: the landing page already renders for a signed-out visitor.  It was tier
#: "free" (and therefore 401 at the door), so the one question a visitor
#: actually has — "how do I get access?" — got "Sorry — I couldn't answer just
#: now".  It is the only open route that COSTS money per call, so it is also the
#: only one with a rate limit: routes/support.py caps an anonymous caller per IP
#: (burst + daily) and the anonymous audience as a whole per day, and answers a
#: canned notice instead of calling the provider once a cap is hit.  It
#: describes the product, never a machine: the visitor prompt forbids catalog
#: contents and customer designs, and the route hands the model no app state.
_ANON_OK_PATHS = frozenset({"/api/health", "/api/me", "/api/version",
                            "/api/support/chat"})
#: …plus the sign-in endpoints themselves: the password login, the Google GIS
#: token exchange, and logout (which must work for a token we are rejecting).
#: NOT the rest of routes/auth_local.py — /api/auth/users, /api/auth/sessions and
#: /api/auth/password are admin/account surface and keep their own require_admin.
_ANON_OK_PREFIXES = ("/api/auth/login", "/api/auth/google", "/api/auth/logout")


def anonymous_allowed(path: str) -> bool:
    """May a caller WITHOUT credentials reach `path` when the exhibit is closed?

    Everything outside /api passes: the SPA's own HTML/JS/CSS is served by nginx
    on the server and by the dev server locally, and a login screen nobody can
    download is not a login screen.
    """
    p = (path or "/").rstrip("/") or "/"
    if not (p == "/api" or p.startswith("/api/")):
        return True
    if p in _ANON_OK_PATHS:
        return True
    return any(p == pfx or p.startswith(pfx + "/") for pfx in _ANON_OK_PREFIXES)

# (HTTP method, exact path) -> minimum tier required to call it.
# Everything not listed here (and not matched by _GATED_PREFIX below) is open.
_GATED: dict[tuple[str, str], str] = {
    ("GET",  "/api/simulation/physics/fem_transient"): "pro",
    ("GET",  "/api/simulation/physics/fem_field2d"): "pro",
    # Physics-cache diagnostics: what the server is holding in memory for the
    # SHARED machine.  Owner-only, like every other shared-store view/mutation.
    ("GET",  "/api/simulation/caches"): "admin",
    # The results ledger: what stored runs exist for the SHARED machine, and
    # throwing them away.  Same class as the cache view above — owner-only.
    ("GET",  "/api/simulation/ledger"): "admin",
    ("DELETE", "/api/simulation/ledger"): "admin",
    # The History popover's three verbs on ONE ledger row (2026-09-22) — same
    # shared-store bargain as the whole-ledger view/clear just above, not the
    # per-workspace "pro" tier `/api/history` gets away without gating at all.
    ("GET",  "/api/simulation/ledger/recent"): "admin",
    # "Have you already computed exactly this?" — a hash lookup, no solve, but
    # it answers a question only a paying engineering user gets to ask, so it
    # rides the same tier as the run it is a pre-flight for.
    ("GET",  "/api/simulation/physics/fem_transient/ledger_match"): "pro",
    # Rotor structural solve — same class of compute as the field solves above
    # (a gmsh mesh + an FE solve per call), so the same tier.
    ("GET",  "/api/mechanical/rotor_stress"): "pro",
    ("GET",  "/api/mechanical/materials"): "pro",
    # Modal analysis, added 2026-09-05 ("нам нужно сделать ещё модальный анализ,
    # чтобы понять все частоты — это очень важно для 20000 rpm").  /modes is a
    # gmsh mesh + a sparse eigensolve and /critical_speeds is a Campbell sweep
    # of dense eigenvalue problems: the same class of compute as the solves
    # above, so the same tier.
    ("GET",  "/api/mechanical/modes"): "pro",
    ("GET",  "/api/mechanical/critical_speeds"): "pro",
    # Added 2026-09-06 with "если есть [расчёты] — подгружается последний
    # расчёт": /last hands back a structural result that was already paid for
    # and /mesh runs the same gmsh pass the solve does, so both ride the tier of
    # the solves they serve rather than being open because they are cheaper.
    ("GET",  "/api/mechanical/last"): "pro",
    ("GET",  "/api/mechanical/mesh"): "pro",
    # Thermal, split out of the simulation router on 2026-09-07 and gated
    # IDENTICALLY to mechanical above, because it is the same bargain: /field is
    # a full EM transient plus a conduction solve, /coupled is up to twelve of
    # them, /mesh runs the same gmsh pass the solve does, and /last hands back a
    # result that was already paid for.  The route it replaces
    # (/api/simulation/physics/thermal_field2d) was never listed here — an
    # oversight, not a decision: it was always heavier than /physics/fem_field2d,
    # which IS gated, since it CALLS it.
    ("GET",  "/api/thermal/field"): "pro",
    ("GET",  "/api/thermal/coupled"): "pro",
    ("GET",  "/api/thermal/last"): "pro",
    ("GET",  "/api/thermal/mesh"): "pro",
    # The DUTY CYCLE (2026-09-14): one conduction solve plus a transient
    # integration over up to 200 cycles, and /duty_cycle/last hands back a
    # result that was already paid for — the same bargain the four above make.
    ("POST", "/api/thermal/duty_cycle"): "pro",
    ("GET",  "/api/thermal/duty_cycle/last"): "pro",
    # The EM<->thermal orchestrator (2026-09-08).  /run is up to six FULL
    # electromagnetic transients plus a conduction solve each — the heaviest
    # single request in this backend — so it rides the same tier as the two
    # solves it drives.  /cancel and /last follow the pattern their transient and
    # thermal equivalents set: the Stop button is gated with the run it stops,
    # and /last hands back a result that was already paid for.
    ("POST", "/api/coupled/run"): "pro",
    ("POST", "/api/coupled/cancel"): "pro",
    ("GET",  "/api/coupled/last"): "pro",
    # NOT listed, and that is the decision, not an oversight (2026-09-07):
    #   GET /api/mechanical/progress
    #   GET /api/thermal/progress
    #   GET /api/coupled/progress
    # They mirror GET /api/simulation/physics/fem_transient/progress, which has
    # been open since it was written.  A progress endpoint carries no physics —
    # a step counter, an elapsed time and a phase name — and it is polled twice
    # a second for the whole of a solve the user is ALREADY paying the gated
    # tier for.  Gating them would mean a progress bar that 401s over a running
    # solve, i.e. the one moment the user most needs to see something.
    ("GET",  "/api/simulation/mesh/build2d"): "pro",
    ("GET",  "/api/simulation/mesh/build2d_sliding_band"): "pro",
    # NOT listed since 2026-09-17: POST /api/support/chat.  It was "free" —
    # "calls the paid provider API, so require a signed-in account so an
    # anonymous visitor can't run up the bill" — which is the right worry and
    # was the wrong lever: the landing page shows the chat widget to a visitor,
    # so the gate turned the product's own "how can I get access?" answer into
    # "Sorry — I couldn't answer just now" (tested twice by the owner as a
    # visitor, 2026-09-17).  The bill is now held down where it is actually
    # spent — routes/support.py rate-limits the ANONYMOUS caller per IP and the
    # anonymous audience as a whole, and stops calling the provider at the cap —
    # so the door can stay open for the question a visitor has.
}

# (HTTP method, path PREFIX) -> minimum tier.  Checked when the exact table
# above has no entry; FIRST matching prefix wins (order the list from the
# most to the least specific).  This is the deployment write-protection:
# ordinary users work on a CLIENT-SIDE copy of a motor (their geometry and
# materials travel per-request as ?geo= / ?mat= overrides), so every route
# that mutates the SHARED server config/stores is the owner's alone.  Routes
# family.py / presets.py / admin.py / auth_local.py protect themselves with
# require_admin already — entries here are the belt for the rest.
_GATED_PREFIX: list[tuple[str, str, str]] = [
    # a tab's remembered input fields — per signed-in user, the same tier as
    # the solves whose inputs they are
    ("GET",    "/api/panel_settings", "pro"),
    ("PUT",    "/api/panel_settings", "pro"),
    # heavy compute a signed-up engineering user may run on their own copy
    ("POST",   "/api/kernel/run", "pro"),
    ("POST",   "/api/kernel/study", "pro"),
    ("POST",   "/api/simulation/physics/fem_transient/cancel", "pro"),
    # The History popover's load/delete on ONE ledger row — a path param, so
    # the exact table above cannot name it; same "admin" bargain as the
    # ledger's exact-path GET/DELETE and .../ledger/recent right next to them.
    ("POST",   "/api/simulation/ledger/", "admin"),
    ("DELETE", "/api/simulation/ledger/", "admin"),
    # shared-config / shared-store mutations → owner only
    ("PUT",    "/api/geometry", "admin"),
    ("POST",   "/api/geometry/parameter", "admin"),
    ("DELETE", "/api/geometry/parameter", "admin"),
    ("POST",   "/api/geometry/reset", "admin"),
    ("PATCH",  "/api/materials", "admin"),
    ("POST",   "/api/materials/global", "admin"),
    ("DELETE", "/api/materials/global", "admin"),
    ("PATCH",  "/api/mesh/config", "admin"),
    ("PATCH",  "/api/simulation/config", "admin"),
    ("PUT",    "/api/sweep/config", "admin"),
    ("POST",   "/api/simulation/run", "admin"),
    ("POST",   "/api/simulation/caches/clear", "admin"),
    ("POST",   "/api/catalog", "admin"),
    ("DELETE", "/api/catalog", "admin"),
    ("POST",   "/api/presets", "admin"),
    ("PATCH",  "/api/presets", "admin"),
    ("DELETE", "/api/presets", "admin"),
    # Saved-sims = the ENGINEER'S Compare tab (clients compare saved
    # configurations inside Configure instead — user's call 2026-08-25, which
    # also reverted the brief "free" opening of these writes).
    ("POST",   "/api/sims/saved", "admin"),
    ("PATCH",  "/api/sims/saved", "admin"),
    ("DELETE", "/api/sims/saved", "admin"),
    # the optimizer burns the whole machine on the shared config — owner's tool
    ("POST",   "/api/optimization", "admin"),
    ("DELETE", "/api/optimization", "admin"),
    ("POST",   "/api/pipeline", "admin"),
    ("POST",   "/api/static3d", "admin"),
]

#: The four shared-store writes above that stop being SHARED the moment the
#: multi-user layering is on: ``presets``, ``catalog``, ``saved-sims`` and the
#: sweep config all resolve their file through ``workspace.root()``, so with
#: ``WORKSPACES_ROOT`` set each account writes ITS OWN copy and "owner only" is
#: the wrong rule — it is the same bargain ``routes/family.py`` makes for the
#: die catalog (``require_catalog_write``).
#:
#: With the env var UNSET — this workstation, and every test that does not set
#: it — there is one copy of each of these files and it is the owner's, so the
#: entries above stay ``admin`` character for character.
_WORKSPACE_STORE_PREFIXES = frozenset({
    "/api/presets", "/api/catalog", "/api/sims/saved", "/api/sweep/config",
})
#: What those writes require instead: a REGISTERED account (anonymous not).
WORKSPACE_STORE_MIN_TIER = "free"


def _layering_on() -> bool:
    """Is the per-user workspace layering active?  Late import on purpose:
    ``workspace`` reaches back into this module for the caller's identity."""
    try:
        from motor_ai_sim.workspace import layering
        return bool(layering())
    except Exception:                                   # noqa: BLE001
        return False


def required_tier(method: str, path: str) -> Optional[str]:
    """The minimum tier for one request, or ``None`` when the route is open.

    The exact table wins; otherwise the FIRST matching prefix does, with the
    per-workspace relaxation above applied to the four stores that are the
    caller's own once layering is on.
    """
    need = _GATED.get((method, path))
    if need is not None:
        return need
    for _m, _pfx, _tier in _GATED_PREFIX:
        if method == _m and path.startswith(_pfx):
            if (_tier == "admin" and _pfx in _WORKSPACE_STORE_PREFIXES
                    and _layering_on()):
                return WORKSPACE_STORE_MIN_TIER
            return _tier
    return None


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


# ── Google Identity Services (direct, post-Firebase) ────────────────────────
# The Firebase project is gone (deleted 2026-08-20); Google sign-in now runs
# through GIS: the frontend gets a Google ID token (RS256, iss accounts.google.com,
# aud = our OAuth client id) and we verify it against Google's JWKS.  Identity
# comes from Google; the TIER comes from OUR user registry (users.json) with
# ADMIN_EMAILS on top — rights stay under our control.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_gjwks: dict = {}
_gjwks_exp: float = 0.0


def _google_keys(force: bool = False) -> dict:
    global _gjwks, _gjwks_exp
    with _lock:
        if not force and _gjwks and time.time() < _gjwks_exp:
            return _gjwks
        with urllib.request.urlopen(_GOOGLE_JWKS_URL, timeout=5) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        _gjwks = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
                  for k in raw.get("keys", [])}
        _gjwks_exp = time.time() + 3600
        return _gjwks


def _verify_google_token(token: str) -> Optional[dict]:
    if not (_HAS_JWT and GOOGLE_CLIENT_ID):
        return None
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        key = _google_keys().get(kid) or _google_keys(force=True).get(kid)
        if key is None:
            logger.warning("google verify: unknown kid %r (JWKS has %d keys)",
                           kid, len(_gjwks))
            return None
        return jwt.decode(token, key=key, algorithms=["RS256"],
                          audience=GOOGLE_CLIENT_ID,
                          issuer=["https://accounts.google.com",
                                  "accounts.google.com"],
                          # ±60 s: a rebooted workstation's clock is routinely
                          # a few seconds off; GIS tokens carry nbf/iat.
                          leeway=60,
                          options={"require": ["exp", "iat", "sub"]})
    except Exception as e:
        # The REASON must reach the log — "was not accepted" alone sent us
        # blind when live sign-ins started failing (2026-08-21).  For an
        # audience mismatch, SAY both audiences: it is the classic split
        # between the frontend's baked-in client id and the backend env.
        try:
            _aud = jwt.decode(token, options={"verify_signature": False}).get("aud")
        except Exception:
            _aud = "<unreadable>"
        logger.warning("google verify failed: %s: %s | token aud=%r vs "
                       "GOOGLE_CLIENT_ID=%r", type(e).__name__, e, _aud,
                       GOOGLE_CLIENT_ID[:20] + "...")
        return None


def _registry_tier(email: str, fallback: str = "free") -> str:
    """OUR user registry decides the tier; ADMIN_EMAILS overrides upward.
    Auto-provisions a record on first sight so the admin panel can manage
    every account that ever signed in.  A disabled record resolves to None
    upstream (checked here via a sentinel).

    A registry that momentarily cannot be READ must not silently demote anyone:
    it would answer "no such user" and hand back the fallback tier, so an admin
    mid-edit would start getting 403s from the gated routes for the duration of
    a file lock.  On a read failure we keep the tier the caller already proved
    (the `fallback`, which for a verified token is its own claim) and provision
    nothing."""
    if email and email in _ADMIN_EMAILS:
        return "admin"
    try:
        from motor_ai_sim import users as _users
        try:
            store = _users._load()
        except Exception as e:
            logger.error("auth: registry unreadable while resolving the tier of "
                         "%r (%s: %s) — keeping %r, provisioning nothing",
                         email, type(e).__name__, e, fallback)
            return fallback
        u = store.get((email or "").strip().lower())
        if u is None and email:
            try:
                import secrets as _sec
                _users.create_user(email, _sec.token_hex(16), tier=fallback)
            except Exception:
                pass
            return fallback
        if u is not None:
            if u.get("disabled"):
                return "__disabled__"
            return u.get("tier", fallback)
    except Exception:
        pass
    return fallback


# ── verification with a REASON ───────────────────────────────────────────────
# Until 2026-09-03 this whole path answered `None` and logged nothing, so
# "expired", "account disabled" and "users.json was locked for 40 ms" were
# indistinguishable from the outside — and the frontend signed the user out for
# all three.  Every answer now carries a reason code (users.REASONS plus
# 'no_token' and 'google_rejected'), every rejection reaches the log, and
# `store_unavailable` is explicitly NOT a rejection.

#: Rejections repeat on every request of a broken session; log/record one per
#: (reason, sid|email) per minute instead of one per request.
_REJECT_QUIET_S = 60.0
_reject_seen: dict[tuple, float] = {}


def _should_report(key: tuple) -> bool:
    now = time.time()
    with _lock:
        if now - _reject_seen.get(key, 0.0) < _REJECT_QUIET_S:
            return False
        _reject_seen[key] = now
        if len(_reject_seen) > 2000:                       # pragma: no cover
            for k, t in list(_reject_seen.items()):
                if now - t > 10 * _REJECT_QUIET_S:
                    _reject_seen.pop(k, None)
        return True


def resolve_user_detail(authorization: Optional[str], *, renew: bool = False,
                        ip: str = "", user_agent: str = "",
                        path: str = "") -> dict:
    """Full verification result for a `Bearer <token>` header.

    ``{"user": {uid,email,tier}|None, "reason": str, "sid": str, "email": str,
       "presented": bool, "renewedToken": str|None}``

    `reason` is 'ok' on success, 'no_token' when no credentials arrived, and
    otherwise one of ``users.REASONS`` / 'google_rejected'.  ONLY a reason that
    blames the token itself may sign a user out; 'store_unavailable' means our
    own filesystem hiccuped and the client must keep its session.
    """
    from motor_ai_sim import sessions as _sessions
    from motor_ai_sim import users as _users

    def _out(user, reason, sid="", email="", renewed=None, presented=True):
        return {"user": user, "reason": reason, "sid": sid, "email": email,
                "presented": presented, "renewedToken": renewed}

    if not isinstance(authorization, str) or not authorization.strip():
        return _out(None, "no_token", presented=False)
    if not authorization.lower().startswith("bearer "):
        return _out(None, "malformed")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        return _out(None, "malformed")

    # 1) our own account token
    try:
        res = _users.verify_token(token, renew=renew)
    except Exception as e:                                  # pragma: no cover
        logger.error("auth: token verification crashed (%s: %s) — reporting "
                     "store_unavailable, NOT signing the session out",
                     type(e).__name__, e)
        _sessions.record_event("store_unavailable", reason=type(e).__name__,
                               ip=ip, user_agent=user_agent, path=path)
        return _out(None, "store_unavailable")

    if res.reason == "store_unavailable":
        if _should_report(("store_unavailable", res.sid or res.email)):
            logger.error("auth: STORE UNAVAILABLE while verifying a token "
                         "(email=%r sid=%r ip=%s ua=%r path=%s) — the session "
                         "is kept, the client will retry",
                         res.email, res.sid, ip, user_agent[:80], path)
            _sessions.record_event("store_unavailable", email=res.email,
                                   sid=res.sid, reason="store_unavailable",
                                   ip=ip, user_agent=user_agent, path=path)
        return _out(None, "store_unavailable", sid=res.sid, email=res.email)

    if res.ok and res.user is not None:
        tier = _registry_tier(res.user["email"], fallback=res.user.get("tier", "free"))
        if tier == "__disabled__":
            _report_reject("disabled", res.email, res.sid, ip, user_agent, path)
            return _out(None, "disabled", sid=res.sid, email=res.email)
        _sessions.touch(res.sid, ip=ip, user_agent=user_agent)
        if res.renewed_token:
            _sessions.record_event("renew", email=res.email, sid=res.sid,
                                   reason="sliding", ip=ip,
                                   user_agent=user_agent, path=path)
        return _out({"uid": res.user["uid"], "email": res.user["email"],
                     "tier": tier}, "ok", sid=res.sid, email=res.email,
                    renewed=res.renewed_token)

    # A token that is definitively OURS and definitively bad — do not waste a
    # Google round trip on it, and say so.
    if res.reason in ("expired", "revoked", "disabled", "unknown_user"):
        _report_reject(res.reason, res.email, res.sid, ip, user_agent, path)
        return _out(None, res.reason, sid=res.sid, email=res.email)

    # 2) a raw Google ID token (RS256 — our HS256 decode called it malformed)
    claims = _verify_google_token(token)
    if claims is not None:
        email = (claims.get("email") or "").strip().lower()
        if claims.get("email_verified") is False:
            _report_reject("google_unverified_email", email, "", ip, user_agent, path)
            return _out(None, "google_rejected", email=email)
        tier = _registry_tier(email)
        if tier == "__disabled__":
            _report_reject("disabled", email, "", ip, user_agent, path)
            return _out(None, "disabled", email=email)
        return _out({"uid": claims.get("sub"), "email": email, "tier": tier},
                    "ok", email=email)

    _report_reject(res.reason, res.email, res.sid, ip, user_agent, path)
    return _out(None, res.reason, sid=res.sid, email=res.email)


def _report_reject(reason: str, email: str, sid: str, ip: str,
                   user_agent: str, path: str) -> None:
    from motor_ai_sim import sessions as _sessions
    if not _should_report((reason, sid or email)):
        return
    logger.warning("auth: token REJECTED (%s) email=%r sid=%r ip=%s ua=%r path=%s",
                   reason, email or "?", sid or "-", ip or "?",
                   (user_agent or "")[:80], path or "-")
    _sessions.record_event("reject", email=email, sid=sid, reason=reason,
                           ip=ip, user_agent=user_agent, path=path)


def resolve_user(authorization: Optional[str]) -> Optional[dict]:
    """Parse a `Bearer <token>` header → {uid,email,tier}, or None.

    Accepts, in order: our own local HS256 token (password accounts /
    service use), then a Google ID token (GIS sign-in).  The legacy Firebase
    path is gone with its project.  `resolve_user_detail` is the same call
    with the reason attached — prefer it wherever the reason matters."""
    return resolve_user_detail(authorization)["user"]


class TierGateMiddleware(BaseHTTPMiddleware):
    """Two gates, one place, in this order:

    1. THE DOOR (``PUBLIC_EXHIBIT=0``): a caller with no valid credentials gets
       401 on every /api route but health / me / the sign-in endpoints.  Off by
       default, so with the variable unset this costs one env read per request
       and nothing else changes.
    2. THE TIER TABLE (``AUTH_ENFORCE=1``): expensive or shared-store endpoints
       need the tier ``required_tier`` names.

    The identity is resolved AT MOST ONCE per request, whichever gate asks for
    it.  CORS preflight (OPTIONS) is never gated by either.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method != "OPTIONS":
            path = request.url.path
            closed = not public_exhibit() and not anonymous_allowed(path)
            need = required_tier(request.method, path) if AUTH_ENFORCE else None
            if closed or need is not None:
                _authz = request.headers.get("authorization")
                user = resolve_user(_authz)
                # The static ADMIN_API_TOKEN is a CREDENTIAL, not an anonymous
                # visitor: a headless agent presenting it is let through the
                # door and then meets the same require_admin_or_token its route
                # already carries.  With the variable unset (this workstation,
                # and the server today) this test is always False.
                if closed and user is None and not _has_service_token(_authz):
                    return JSONResponse(
                        status_code=401,
                        content={
                            "detail": "Sign in to use this server.",
                            "required_tier": need or "free",
                            "your_tier": "anon",
                        },
                    )
                if need is not None:
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


def account_info(authorization: Optional[str], *, ip: str = "",
                 user_agent: str = "") -> dict:
    """Resolve who's calling -> {uid,email,tier,isAdmin,enforced} for /api/me.

    Also reports WHY the caller is anonymous, which the frontend needs to tell
    three very different situations apart:
      * tokenPresented=False — no credentials reached us.  The browser may well
        still hold a perfectly good session (a request that raced the fetch
        interceptor, a hot-reloaded module, a proxy that dropped the header).
        Dropping the stored session here logs the user out for nothing — that
        is exactly how "сессия постоянно протухает" happened (2026-08-21).
      * authError='store_unavailable' — a token was presented and we could not
        CHECK it, because users.json / .sessions.json / .auth_secret was
        momentarily unreadable (a Windows file lock during the atomic replace,
        an antivirus hold).  Not the user's fault and NOT a logout: the client
        keeps the session and retries.  This is the case that used to masquerade
        as an expiry and is the prime suspect for the daily sign-outs.
      * tokenRejected=True — a token WAS presented and genuinely did not verify
        (expired, revoked, signature, account disabled/deleted).  `authError`
        names which.  Only this is a real logout.

    A valid token inside its last 7 days comes back with `renewedToken`: the
    same session, a fresh 30-day expiry, swapped into storage by the client.
    """
    det = resolve_user_detail(authorization, renew=True, ip=ip,
                              user_agent=user_agent, path="/api/me")
    user = det["user"]
    reason = det["reason"]
    if not AUTH_ENFORCE and not _ADMIN_EMAILS:
        is_admin = True
    else:
        is_admin = user is not None and user.get("tier") == "admin"
    presented = bool(det["presented"])
    # store_unavailable is OUR failure, never the client's — it must not read
    # as a rejected token, or the browser wipes a valid session over a 40 ms
    # file lock.
    rejected = presented and user is None and reason != "store_unavailable"
    out = {
        "uid": user["uid"] if user else ("local-dev" if is_admin else None),
        "email": user["email"] if user else None,
        "tier": "admin" if is_admin else (user["tier"] if user else "anon"),
        "isAdmin": is_admin,
        "enforced": AUTH_ENFORCE,
        "tokenPresented": presented,
        "tokenRejected": bool(rejected),
        "authError": None if reason in ("ok", "no_token") else reason,
        "sid": det["sid"] or None,
    }
    if det.get("renewedToken"):
        out["renewedToken"] = det["renewedToken"]
    return out


# The owner string stamped on entries created by an admin (including the
# local-dev admin, who has no Firebase account and therefore no email).  Legacy
# entries — written before ownership existed — are back-filled to this, because
# the person who created them is the account that has been running this app.
ADMIN_OWNER = "admin"
# Every caller we cannot name.  Anonymous callers share this one identity: it
# protects a NAMED owner's motors from them, but it cannot tell two anonymous
# visitors apart.  Sign-in is what makes a motor yours alone.
ANON_OWNER = "anonymous"


def caller_identity(authorization: Optional[str] = None) -> dict:
    """Who is asking — `{"id": str, "is_admin": bool, "tier": str}`.

    The id is the SAME dialect the stores spell in an entry's `owner` field, so
    "is this mine?" is one string comparison and not a translation step. It is
    the signed-in account's email (lower-cased, the form ADMIN_EMAILS is matched
    in), falling back to the Firebase uid for an account without one.

    Admin-ness is `_is_admin_caller`'s answer and nothing else — one definition
    of admin for the whole backend, including the local/unconfigured dev case
    where the developer IS the admin (otherwise every write on a laptop would
    have to be signed in to a Firebase project that local dev does not have).

    `tier` rides along because the identity is ALREADY resolved here and the
    job queue needs it (jobs.priority_for): re-resolving a bearer token deep
    inside a submit would mean a second verification per solve, and a queue
    class that could disagree with the tier the gate let through. It is the
    registry tier of a signed-in account, 'admin' for an admin caller (the
    local-dev one included) and 'anon' when no credentials were presented.
    """
    # A route handler called DIRECTLY (tests, internal call paths) still carries
    # FastAPI's unresolved `Header(...)` default in this slot.  Anything that is
    # not a string is "no credentials presented", never an object we probe for
    # `.lower()` — an in-process call must not raise where an HTTP one resolves.
    if not isinstance(authorization, str):
        authorization = None
    is_admin, user = _is_admin_caller(authorization)
    ident = ""
    if user:
        ident = (user.get("email") or "").strip().lower() or (user.get("uid") or "")
    if not ident:
        ident = ADMIN_OWNER if is_admin else ANON_OWNER
    tier = "admin" if is_admin else str((user or {}).get("tier") or "anon")
    return {"id": ident, "is_admin": is_admin, "tier": tier}


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
