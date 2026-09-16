"""Self-hosted user accounts: file store + PBKDF2 passwords + HS256 JWTs.

Replaces Firebase Auth (project deleted 2026-08-20).  Design constraints:

- ZERO new dependencies: hashing is hashlib.pbkdf2_hmac (600k iterations,
  per-user random salt), tokens are pyjwt HS256 (pyjwt was already a
  dependency of the old Firebase verification).
- The store is one JSON file, config/users.json — the same file-backed
  philosophy as the family catalog: readable, diffable, backed up with the
  repo's config directory.  Writes are atomic (tmp + replace) under a lock.
- Tokens carry {sub=email, tier, exp}; the tier is ALSO re-read from the
  store on every resolve, so demoting or disabling an account takes effect
  on the next request, not at token expiry.
- The signing secret lives in config/.auth_secret (created on first use,
  0600 not enforceable on Windows — the config dir is already the trust
  boundary here).  AUTH_SECRET env overrides it for multi-node deploys.
  An EXISTING secret file is never overwritten: silently minting a new secret
  because a read came back empty would invalidate every live session at once,
  which is one of the ways "everybody got logged out" can happen and leave no
  trace.  A secret that cannot be read fails closed (store_unavailable).
- Every issued token carries a `sid` — a server-side session id recorded in
  sessions.py, so a session can be listed, aged, and revoked, and so a
  rejection can be logged with something more useful than an email.
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

import jwt

from motor_ai_sim.config import DEFAULT_CONFIG_PATH
from motor_ai_sim.sessions import StoreUnavailable

log = logging.getLogger(__name__)

_USERS_FILE = Path(DEFAULT_CONFIG_PATH).parent / "users.json"
_SECRET_FILE = Path(DEFAULT_CONFIG_PATH).parent / ".auth_secret"
_LOCK = threading.RLock()

_PBKDF2_ITERS = 600_000
_TOKEN_TTL_S = 30 * 24 * 3600          # 30 days
#: Inside this window before `exp`, /api/me hands back a renewed token with the
#: SAME sid — a session that is actively used never expires under the user.
RENEW_WINDOW_S = 7 * 24 * 3600         # 7 days
TIERS = ("free", "pro", "team", "admin")

#: Every possible outcome of verifying a bearer token.  Only the first is a
#: success; only `store_unavailable` is NOT the client's fault and must never
#: cost the user their session.
REASONS = ("ok", "expired", "bad_signature", "malformed", "unknown_user",
           "disabled", "revoked", "store_unavailable")


@dataclasses.dataclass
class TokenResult:
    """The structured answer verification owes its caller.

    `None` was never enough: "expired", "the account is gone" and "users.json
    was locked for 40 ms by an antivirus scan" all collapsed into it, and the
    frontend treated all three as a logout.
    """
    reason: str                              # one of REASONS
    user: Optional[dict] = None              # {uid,email,tier} when reason=='ok'
    email: str = ""                          # decodable even on failure
    sid: str = ""
    exp: float = 0.0
    renewed_token: Optional[str] = None      # set when inside RENEW_WINDOW_S

    @property
    def ok(self) -> bool:
        return self.reason == "ok" and self.user is not None

    @property
    def store_unavailable(self) -> bool:
        return self.reason == "store_unavailable"


# ── secret ───────────────────────────────────────────────────────────────────

def _secret() -> str:
    """The HS256 signing secret.  Raises StoreUnavailable rather than rotate.

    Created once on first use.  If the file EXISTS but reads back empty or
    raises, that is a filesystem problem, not a missing secret — generating a
    replacement would sign out every session on the deployment, so we refuse
    and say so.
    """
    env = os.environ.get("AUTH_SECRET", "").strip()
    if env:
        return env
    with _LOCK:
        if _SECRET_FILE.exists():
            try:
                s = _SECRET_FILE.read_text(encoding="utf-8").strip()
            except Exception as e:
                log.error("auth: %s exists but could not be read (%s: %s) — "
                          "REFUSING to mint a new secret (that would sign out "
                          "every session); auth is unavailable until this is "
                          "fixed", _SECRET_FILE, type(e).__name__, e)
                raise StoreUnavailable(f"secret unreadable: {e}") from e
            if s:
                return s
            log.error("auth: %s exists but is EMPTY — refusing to overwrite it. "
                      "Restore the secret from a backup, or delete the file "
                      "deliberately to start fresh (everyone signs in again).",
                      _SECRET_FILE)
            raise StoreUnavailable("secret file is empty")
        s = secrets.token_hex(32)
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_text(s, encoding="utf-8")
        log.warning("auth: generated a new signing secret at %s (first run)",
                    _SECRET_FILE)
        return s


# ── store ────────────────────────────────────────────────────────────────────

def _load() -> dict:
    """The registry.  Raises StoreUnavailable when the file is there but
    unreadable — a caller that cannot tell that apart from an empty registry
    will either sign everyone out or, if it then writes, DELETE every account.
    A missing file is genuinely empty and returns {}."""
    try:
        with open(_USERS_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.error("auth: %s is unreadable (%s: %s) — this is transient/"
                  "environmental, sessions are NOT being rejected for it",
                  _USERS_FILE, type(e).__name__, e)
        raise StoreUnavailable(str(e)) from e
    return d if isinstance(d, dict) else {}


def _load_soft() -> dict:
    """`_load` for read-only lookups that must degrade rather than raise."""
    try:
        return _load()
    except StoreUnavailable:
        return {}


def _save(d: dict) -> None:
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _USERS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
    tmp.replace(_USERS_FILE)


def _norm(email: str) -> str:
    return (email or "").strip().lower()


# ── passwords ────────────────────────────────────────────────────────────────

def _hash_pw(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex),
        _PBKDF2_ITERS).hex()


def create_user(email: str, password: str, tier: str = "free",
                name: str = "") -> dict:
    email = _norm(email)
    if not email or "@" not in email:
        raise ValueError(f"'{email}' is not an email address")
    if len(password or "") < 8:
        raise ValueError("password must be at least 8 characters")
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    with _LOCK:
        users = _load()
        if email in users:
            raise ValueError(f"user '{email}' already exists")
        salt = secrets.token_hex(16)
        users[email] = {
            "pw_salt": salt,
            "pw_hash": _hash_pw(password, salt),
            "pw_iters": _PBKDF2_ITERS,
            "tier": tier,
            "name": name or "",
            "disabled": False,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _save(users)
    return public_user(email)


def set_password(email: str, password: str) -> None:
    email = _norm(email)
    if len(password or "") < 8:
        raise ValueError("password must be at least 8 characters")
    with _LOCK:
        users = _load()
        if email not in users:
            raise KeyError(email)
        salt = secrets.token_hex(16)
        users[email]["pw_salt"] = salt
        users[email]["pw_hash"] = _hash_pw(password, salt)
        users[email]["pw_iters"] = _PBKDF2_ITERS
        _save(users)


def update_user(email: str, *, tier: Optional[str] = None,
                disabled: Optional[bool] = None,
                name: Optional[str] = None) -> dict:
    email = _norm(email)
    with _LOCK:
        users = _load()
        if email not in users:
            raise KeyError(email)
        if tier is not None:
            if tier not in TIERS:
                raise ValueError(f"tier must be one of {TIERS}")
            users[email]["tier"] = tier
        if disabled is not None:
            users[email]["disabled"] = bool(disabled)
        if name is not None:
            users[email]["name"] = str(name)
        _save(users)
    return public_user(email)


def delete_user(email: str) -> None:
    email = _norm(email)
    with _LOCK:
        users = _load()
        if email not in users:
            raise KeyError(email)
        del users[email]
        _save(users)


def get_user(email: str) -> Optional[dict]:
    return _load_soft().get(_norm(email))


def list_users() -> list[dict]:
    return [public_user(e) for e in sorted(_load_soft().keys())]


def public_user(email: str) -> dict:
    u = _load_soft().get(_norm(email)) or {}
    return {"email": _norm(email), "tier": u.get("tier", "free"),
            "name": u.get("name", ""), "disabled": bool(u.get("disabled")),
            "created": u.get("created"),
            # Which catalog motors this account may open (see below).  Shipped
            # with the record so the admin table draws the count without a
            # second round trip per user.
            "motors": normalize_grants(u.get("motors"))}


# ── per-user motor grants ────────────────────────────────────────────────────
# WHICH motors of the shared catalog an account may see is registry data, not a
# property of the machines: `motors: {"all": bool, "dies": [die names]}` on the
# user record.  An ABSENT key means nothing is granted — a brand-new account
# sees an empty catalog until the vendor assigns motors to it (user's rule,
# 2026-09-02).  Admins (tier admin / ADMIN_EMAILS) bypass this entirely.

def normalize_grants(raw) -> dict:
    """Any stored shape → `{"all": bool, "dies": [str]}` (never None)."""
    if not isinstance(raw, dict):
        return {"all": False, "dies": []}
    dies = raw.get("dies")
    if not isinstance(dies, (list, tuple, set)):
        dies = []
    return {"all": bool(raw.get("all")),
            "dies": sorted({str(d).strip() for d in dies if str(d).strip()})}


def get_motor_grants(email: str) -> dict:
    return normalize_grants((_load_soft().get(_norm(email)) or {}).get("motors"))


def set_motor_grants(email: str, *, all_motors: bool,
                     dies: Optional[list] = None) -> dict:
    """Replace an account's grants.  Written through the same atomic _save as
    every other registry change — never a second writer on users.json."""
    email = _norm(email)
    with _LOCK:
        users = _load()
        if email not in users:
            raise KeyError(email)
        users[email]["motors"] = normalize_grants(
            {"all": all_motors, "dies": list(dies or [])})
        _save(users)
    return get_motor_grants(email)


# ── invites ──────────────────────────────────────────────────────────────────
# An invite is NOT a second store.  There is one registry, and an invite is a
# row in it that the vendor created on purpose, stamped with who invited whom,
# when and why:  `invite: {"by", "at", "note", "tier"}`.  A second file would
# have to be kept in step with users.json on every rename, disable and delete —
# and the one thing this deployment cannot afford is two answers to "may this
# person in?".
#
# No e-mail is sent, and none can be: the host blocks outbound 25/465, so the
# admin tells the person to sign in with Google.  The row exists first, which is
# what makes the difference between an invited account (its tier and its motors
# are already decided) and an unknown Google address (auto-registered at `free`
# with NOTHING granted — see auth._registry_tier).
#
# The password of an invited account is RANDOM and nobody holds it: the door is
# Google sign-in, or an admin password reset.  It is not left empty, because an
# empty hash is a hash somebody might one day match.

def invite_user(email: str, *, tier: str = "free", name: str = "",
                by: str = "", note: str = "") -> dict:
    """Create — or re-invite — the account `email`, and stamp the invite.

    Re-inviting an EXISTING account is deliberate and idempotent: it sets the
    tier, clears `disabled` (an invite is "come in", and refusing the person at
    the door because an old row says disabled is the worst kind of silent no)
    and re-stamps who/when/why.  It never touches the password: an account that
    already signs in keeps signing in.
    """
    email = _norm(email)
    if not email or "@" not in email:
        raise ValueError(f"'{email}' is not an email address")
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    stamp = {"by": _norm(by) or "admin",
             "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "note": str(note or "")[:500],
             "tier": tier}
    with _LOCK:
        users = _load()
        rec = users.get(email)
        if rec is None:
            salt = secrets.token_hex(16)
            rec = {"pw_salt": salt,
                   "pw_hash": _hash_pw(secrets.token_urlsafe(32), salt),
                   "pw_iters": _PBKDF2_ITERS,
                   "tier": tier,
                   "name": name or "",
                   "disabled": False,
                   "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
            users[email] = rec
        else:
            rec["tier"] = tier
            rec["disabled"] = False
            if name:
                rec["name"] = str(name)
        rec["invite"] = stamp
        _save(users)
    return public_user(email)


def invite_of(email: str) -> Optional[dict]:
    """The invite stamp on an account, or None if it was never invited."""
    inv = (_load_soft().get(_norm(email)) or {}).get("invite")
    return dict(inv) if isinstance(inv, dict) else None


def list_invites() -> list[dict]:
    """Every INVITED account, newest invite first: the public record + stamp.

    Accounts that arrived by themselves (an unknown Google address signing in)
    carry no stamp and are not listed here — they are in `list_users()`.
    """
    out = []
    for email, rec in _load_soft().items():
        inv = rec.get("invite")
        if not isinstance(inv, dict):
            continue
        out.append({**public_user(email),
                    "invited_by": inv.get("by") or "",
                    "invited_at": inv.get("at") or "",
                    "note": inv.get("note") or ""})
    out.sort(key=lambda r: r.get("invited_at") or "", reverse=True)
    return out


def any_users() -> bool:
    return bool(_load_soft())


# ── login / tokens ───────────────────────────────────────────────────────────

def check_login(email: str, password: str) -> Optional[dict]:
    """Constant-time-ish password check → the user record, or None."""
    email = _norm(email)
    u = _load_soft().get(email)
    # Always burn a hash even for unknown users, so response timing does not
    # reveal which emails exist.
    salt = (u or {}).get("pw_salt") or secrets.token_hex(16)
    iters = int((u or {}).get("pw_iters") or _PBKDF2_ITERS)
    calc = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                               bytes.fromhex(salt), iters).hex()
    if u is None or u.get("disabled"):
        return None
    if not hmac.compare_digest(calc, u.get("pw_hash", "")):
        return None
    return u


def issue_token(email: str, sid: Optional[str] = None,
                ttl_s: Optional[int] = None) -> str:
    """Sign a bearer for `email`.  `sid` names the server-side session; it is
    optional only so old call sites (and tests) keep working — a token without
    one simply cannot be listed or revoked."""
    email = _norm(email)
    u = _load_soft().get(email) or {}
    now = int(time.time())
    claims = {"sub": email, "email": email, "tier": u.get("tier", "free"),
              "iat": now, "exp": now + int(ttl_s or _TOKEN_TTL_S),
              "iss": "motor-ai-sim"}
    if sid:
        claims["sid"] = sid
    return jwt.encode(claims, _secret(), algorithm="HS256")


def token_exp(token: str) -> float:
    """`exp` of a token WITHOUT verifying it — for logging a rejection."""
    try:
        return float(jwt.decode(token, options={"verify_signature": False})
                     .get("exp") or 0.0)
    except Exception:
        return 0.0


def _unverified(token: str) -> dict:
    try:
        d = jwt.decode(token, options={"verify_signature": False})
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def verify_token(token: str, *, renew: bool = False) -> TokenResult:
    """Verify OUR HS256 token and SAY WHY when it fails.

    The reason is the whole point.  Before this existed, `/api/me` answered
    "rejected" for an expired token, a disabled account AND a users.json that
    happened to be locked by a virus scanner — and the frontend wiped the
    session for all three.  Only the first two are the user's problem.

    `renew=True` additionally mints a replacement token (same sid, exp pushed
    to now + _TOKEN_TTL_S) when the presented one is inside RENEW_WINDOW_S of
    expiry, so a session in daily use never runs out under the user.
    """
    if not token or not isinstance(token, str):
        return TokenResult("malformed")
    try:
        secret = _secret()
    except StoreUnavailable:
        return TokenResult("store_unavailable")

    raw = _unverified(token)
    email_hint = _norm(raw.get("email") or raw.get("sub") or "")
    sid_hint = str(raw.get("sid") or "")

    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"],
                            issuer="motor-ai-sim",
                            options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError:
        return TokenResult("expired", email=email_hint, sid=sid_hint,
                           exp=float(raw.get("exp") or 0.0))
    except jwt.InvalidSignatureError:
        return TokenResult("bad_signature", email=email_hint, sid=sid_hint)
    except jwt.InvalidIssuerError:
        # Someone else's HS256 token, or ours from before the issuer existed.
        return TokenResult("bad_signature", email=email_hint, sid=sid_hint)
    except Exception:
        return TokenResult("malformed", email=email_hint, sid=sid_hint)

    email = _norm(claims.get("email") or claims.get("sub") or "")
    sid = str(claims.get("sid") or "")
    exp = float(claims.get("exp") or 0.0)

    try:
        users = _load()
    except StoreUnavailable:
        return TokenResult("store_unavailable", email=email, sid=sid, exp=exp)

    u = users.get(email)
    if u is None:
        return TokenResult("unknown_user", email=email, sid=sid, exp=exp)
    if u.get("disabled"):
        return TokenResult("disabled", email=email, sid=sid, exp=exp)

    # The session registry is consulted only for tokens that CARRY a sid; a
    # legacy token predates the registry and cannot be revoked through it.
    if sid:
        from motor_ai_sim import sessions as S
        try:
            rec = S.get(sid)
        except StoreUnavailable:
            return TokenResult("store_unavailable", email=email, sid=sid, exp=exp)
        if isinstance(rec, dict) and rec.get("revoked"):
            return TokenResult("revoked", email=email, sid=sid, exp=exp)

    renewed = None
    if renew and exp and (exp - time.time()) < RENEW_WINDOW_S:
        try:
            renewed = issue_token(email, sid=sid or None)
            if sid:
                from motor_ai_sim import sessions as S
                S.extend(sid, time.time() + _TOKEN_TTL_S)
        except Exception as e:                              # pragma: no cover
            log.warning("auth: token renewal failed for %s (%s: %s)",
                        email, type(e).__name__, e)
            renewed = None

    return TokenResult("ok", user={"uid": email, "email": email,
                                   "tier": u.get("tier", "free")},
                       email=email, sid=sid, exp=exp, renewed_token=renewed)


def resolve_local_token(token: str) -> Optional[dict]:
    """Back-compat shim: `verify_token(...).user` or None.  New code should
    call `verify_token` and act on the REASON."""
    return verify_token(token).user
