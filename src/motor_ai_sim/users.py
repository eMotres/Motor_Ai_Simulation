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
ROLES = ("user", "admin")


def _migrate_role(rec: dict) -> str:
    """Old free/pro/team/internal/etc. tiers collapse to user; admin stays admin."""
    v = rec.get("role") or rec.get("tier") or "user"
    return "admin" if str(v) == "admin" else "user"

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
        from motor_ai_sim.private_files import ensure_private_dir, write_private_text
        ensure_private_dir(_SECRET_FILE.parent)
        write_private_text(_SECRET_FILE, s)
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
    d = d if isinstance(d, dict) else {}
    for rec in d.values():
        if isinstance(rec, dict):
            rec["role"] = _migrate_role(rec)
    return d


def _load_soft() -> dict:
    """`_load` for read-only lookups that must degrade rather than raise."""
    try:
        return _load()
    except StoreUnavailable:
        return {}


def _save(d: dict) -> None:
    from motor_ai_sim.private_files import chmod_private, ensure_private_dir, open_private
    ensure_private_dir(_USERS_FILE.parent)
    tmp = _USERS_FILE.with_suffix(".tmp")
    with open_private(tmp, "w") as f:       # 0600 — audit 2026-09-29 #9
        json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
    tmp.replace(_USERS_FILE)
    chmod_private(_USERS_FILE)


def _norm(email: str) -> str:
    return (email or "").strip().lower()


# ── passwords ────────────────────────────────────────────────────────────────
# New hashes are argon2id (argon2-cffi, library defaults: m=64 MiB, t=3, p=4),
# stored as the self-describing "$argon2id$..." string with `pw_algo:
# "argon2id"` and NO pw_salt.  Records written before 2026-09-28 carry
# `pw_salt` + `pw_iters` + a hex PBKDF2-SHA256 `pw_hash`; they keep verifying
# and are re-hashed to argon2id on the next successful login.  The schema
# change is read-compatible: a record without `pw_algo` is PBKDF2, a record
# without `email_verified` is verified (every pre-existing account was created
# by an admin, an invite or Google — all of which prove the address).

from argon2 import PasswordHasher as _PasswordHasher
from argon2 import exceptions as _argon2_exc

_PH = _PasswordHasher()                    # argon2id by default
_DUMMY_HASH: Optional[str] = None          # burned for unknown accounts

PASSWORD_MIN_LEN = 10
PASSWORD_MAX_LEN = 256

#: A deliberately small list of the passwords that survive a 10-character
#: minimum and still top every breach corpus.  Not a substitute for a breach
#: API; enough to refuse the obvious.
COMMON_PASSWORDS = frozenset("""
password12 password123 password1234 password12345 password123456 passw0rd123
1234567890 12345678910 0123456789 0987654321 9876543210 1111111111 0000000000
1234512345 1234554321 1q2w3e4r5t 1qaz2wsx3edc qwertyuiop qwerty1234 qwerty12345
qwerty123456 asdfghjkl1 asdfghjkl; zxcvbnm123 abcdefghij abcd123456 abc1234567
iloveyou12 iloveyou123 letmein123 welcome123 welcome1234 administrator admin12345
admin123456 changeme123 football123 baseball123 monkey12345 dragon12345
sunshine123 princess123 superman123 starwars123 trustno1234 michael123
computer123 internet123 whatever123 master12345 shadow12345 jordan23123
p@ssw0rd123 p@ssword123 password!123 motorsim123 aerostator1 aerostator123
""".split())


class PasswordPolicyError(ValueError):
    """A password that the policy refuses; the message is safe to show."""


def check_password_policy(password: str, email: str = "") -> None:
    """Raise PasswordPolicyError with a human reason, or return None."""
    pw = password or ""
    if len(pw) < PASSWORD_MIN_LEN:
        raise PasswordPolicyError(
            f"password must be at least {PASSWORD_MIN_LEN} characters")
    if len(pw) > PASSWORD_MAX_LEN:
        raise PasswordPolicyError(
            f"password must be at most {PASSWORD_MAX_LEN} characters")
    low = pw.lower()
    if low in COMMON_PASSWORDS or len(set(pw)) <= 2:
        raise PasswordPolicyError("this password is too common — choose another")
    local = _norm(email).split("@", 1)[0]
    if local and len(local) >= 4 and (low == local or low == _norm(email)):
        raise PasswordPolicyError("password must not be your e-mail address")


def _argon2(password: str) -> str:
    return _PH.hash(password or "")


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _argon2(secrets.token_urlsafe(24))
    return _DUMMY_HASH


def _hash_pw(password: str, salt_hex: str) -> str:
    """Legacy PBKDF2-SHA256 — verification of pre-argon2 records only."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex),
        _PBKDF2_ITERS).hex()


def _pw_fields(password: str) -> dict:
    """The record fields for a fresh argon2id hash (legacy fields cleared)."""
    return {"pw_algo": "argon2id", "pw_hash": _argon2(password),
            "pw_salt": None, "pw_iters": None,
            "pw_changed": time.strftime("%Y-%m-%dT%H:%M:%S")}


def _apply_pw(rec: dict, password: str) -> None:
    rec.update(_pw_fields(password))
    rec.pop("pw_salt", None)
    rec.pop("pw_iters", None)


def _verify_pw(rec: Optional[dict], password: str) -> bool:
    """Constant-time check of `password` against a record (or a dummy hash
    when `rec` is None, so unknown accounts cost the same time)."""
    pw = password or ""
    if rec is None or rec.get("pw_algo") == "argon2id" or not rec.get("pw_salt"):
        h = (rec or {}).get("pw_hash") if rec is not None else None
        if not isinstance(h, str) or not h.startswith("$argon2"):
            try:
                _PH.verify(_dummy_hash(), pw)
            except Exception:
                pass
            return False
        try:
            return bool(_PH.verify(h, pw))
        except (_argon2_exc.VerifyMismatchError, _argon2_exc.VerificationError,
                _argon2_exc.InvalidHashError):
            return False
    try:
        iters = int(rec.get("pw_iters") or _PBKDF2_ITERS)
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                                   bytes.fromhex(rec["pw_salt"]), iters).hex()
    except Exception:
        return False
    return hmac.compare_digest(calc, str(rec.get("pw_hash") or ""))


def is_verified(rec: Optional[dict]) -> bool:
    """Absent key = a pre-2026-09-28 account = verified (read-compatible)."""
    return bool(rec) and rec.get("email_verified", True) is not False


def create_user(email: str, password: str, role: str = "user",
                name: str = "") -> dict:
    email = _norm(email)
    if not email or "@" not in email:
        raise ValueError(f"'{email}' is not an email address")
    if len(password or "") < 8:
        raise ValueError("password must be at least 8 characters")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    with _LOCK:
        users = _load()
        if email in users:
            raise ValueError(f"user '{email}' already exists")
        rec = {
            "role": role,
            "name": name or "",
            "disabled": False,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _apply_pw(rec, password)
        users[email] = rec
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
        _apply_pw(users[email], password)
        users[email]["has_password"] = True
        _save(users)


# ── self-service registration / verification / reset ─────────────────────────
# Single-use links: the signed token carries a random nonce whose SHA-256 is
# stored on the record under `link_nonces[purpose]`.  Using the link deletes
# it; asking for a new one replaces it.  Tokens are signed with a key DERIVED
# from the session secret (HMAC "email-link-v1"), and carry their own issuer,
# so a link can never be presented as a session token or vice versa.

LINK_TTL_S = 24 * 3600
_LINK_ISS = "motor-ai-sim-link"
LINK_PURPOSES = ("verify", "reset")


def _link_key() -> bytes:
    return hmac.new(_secret().encode("utf-8"), b"email-link-v1",
                    hashlib.sha256).digest()


def _nonce_digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def issue_link_token(email: str, purpose: str,
                     ttl_s: int = LINK_TTL_S) -> str:
    """Mint a single-use link token and remember its nonce on the record."""
    if purpose not in LINK_PURPOSES:
        raise ValueError(purpose)
    email = _norm(email)
    nonce = secrets.token_urlsafe(24)
    with _LOCK:
        users = _load()
        if email not in users:
            raise KeyError(email)
        ln = users[email].get("link_nonces")
        ln = dict(ln) if isinstance(ln, dict) else {}
        ln[purpose] = _nonce_digest(nonce)
        users[email]["link_nonces"] = ln
        _save(users)
    now = int(time.time())
    return jwt.encode({"sub": email, "purpose": purpose, "nonce": nonce,
                       "iat": now, "exp": now + int(ttl_s), "iss": _LINK_ISS},
                      _link_key(), algorithm="HS256")


def consume_link_token(token: str, purpose: str) -> Optional[str]:
    """Verify + burn a link token.  Returns the email, or None for ANY
    failure (expired, forged, reused, wrong purpose, unknown account)."""
    try:
        claims = jwt.decode(token or "", _link_key(), algorithms=["HS256"],
                            issuer=_LINK_ISS,
                            options={"require": ["exp", "sub", "nonce"]})
    except Exception:
        return None
    if claims.get("purpose") != purpose:
        return None
    email = _norm(claims.get("sub") or "")
    with _LOCK:
        users = _load()
        rec = users.get(email)
        if rec is None:
            return None
        ln = rec.get("link_nonces") if isinstance(rec.get("link_nonces"), dict) else {}
        want = ln.get(purpose) or ""
        got = _nonce_digest(str(claims.get("nonce") or ""))
        if not want or not hmac.compare_digest(want, got):
            return None
        ln = dict(ln)
        ln.pop(purpose, None)
        rec["link_nonces"] = ln
        _save(users)
    return email


def register_self(email: str, password: str, name: str = "") -> str:
    """Create an UNVERIFIED password account.

    Returns what happened, for the route to decide on mail — never for the
    client: "created" | "exists_unverified" | "exists_verified".  An existing
    account is never modified (in particular its password is NOT replaced:
    that would let anybody reset a pending registration)."""
    email = _norm(email)
    check_password_policy(password, email)
    fields = _pw_fields(password)          # hash first: same cost every branch
    with _LOCK:
        users = _load()
        rec = users.get(email)
        if rec is not None:
            return "exists_verified" if is_verified(rec) else "exists_unverified"
        rec = {"role": "user", "name": str(name or "")[:120],
               "disabled": False,
               "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "email_verified": False, "has_password": True,
               "signup": "email"}
        rec.update(fields)
        rec.pop("pw_salt", None)
        rec.pop("pw_iters", None)
        users[email] = rec
        _save(users)
    return "created"


def mark_verified(email: str, *, by: str = "link") -> bool:
    """Activate an account (link clicked, admin approval, Google proof)."""
    email = _norm(email)
    with _LOCK:
        users = _load()
        rec = users.get(email)
        if rec is None:
            return False
        rec["email_verified"] = True
        rec["verified_by"] = by
        rec["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        rec.pop("pending_approval", None)
        ln = rec.get("link_nonces")
        if isinstance(ln, dict):
            ln = dict(ln)
            ln.pop("verify", None)
            rec["link_nonces"] = ln
        _save(users)
    return True


def mark_pending_approval(email: str, reason: str = "smtp_not_configured") -> None:
    email = _norm(email)
    with _LOCK:
        users = _load()
        if email in users and not is_verified(users[email]):
            users[email]["pending_approval"] = reason
            _save(users)


def list_pending() -> list[dict]:
    """Every account that cannot sign in yet because its e-mail is unproven."""
    out = []
    for email, rec in _load_soft().items():
        if is_verified(rec):
            continue
        out.append({**public_user(email),
                    "reason": rec.get("pending_approval") or "awaiting_link"})
    out.sort(key=lambda r: r.get("created") or "", reverse=True)
    return out


def link_google(email: str) -> None:
    """Google just proved `email` belongs to the caller.

    If an UNVERIFIED password account with that address exists, it was created
    by someone who never proved the mailbox — possibly an attacker squatting
    the address in advance.  Google's proof wins: the account is verified and
    its unproven password is DISCARDED (the owner can set one via reset).  A
    verified password account is left exactly as it is: both doors open."""
    email = _norm(email)
    with _LOCK:
        users = _load()
        rec = users.get(email)
        if rec is None or is_verified(rec):
            return
        _apply_pw(rec, secrets.token_urlsafe(32))
        rec["has_password"] = False
        rec["email_verified"] = True
        rec["verified_by"] = "google"
        rec["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        rec.pop("pending_approval", None)
        rec["link_nonces"] = {}
        _save(users)


def reset_password(email: str, password: str) -> None:
    """Set a new password after a reset link: argon2id, proves the mailbox
    (so the account becomes verified), and burns every outstanding link."""
    email = _norm(email)
    check_password_policy(password, email)
    fields = _pw_fields(password)
    with _LOCK:
        users = _load()
        rec = users.get(email)
        if rec is None:
            raise KeyError(email)
        rec.update(fields)
        rec.pop("pw_salt", None)
        rec.pop("pw_iters", None)
        rec["has_password"] = True
        if not is_verified(rec):
            rec["email_verified"] = True
            rec["verified_by"] = "reset"
            rec["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            rec.pop("pending_approval", None)
        rec["link_nonces"] = {}
        _save(users)


def update_user(email: str, *, role: Optional[str] = None,
                disabled: Optional[bool] = None,
                name: Optional[str] = None) -> dict:
    email = _norm(email)
    with _LOCK:
        users = _load()
        if email not in users:
            raise KeyError(email)
        if role is not None:
            if role not in ROLES:
                raise ValueError(f"role must be one of {ROLES}")
            users[email]["role"] = role
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
    return {"email": _norm(email), "role": u.get("role", "user"),
            "name": u.get("name", ""), "disabled": bool(u.get("disabled")),
            "created": u.get("created"),
            # False only for a self-registered account whose link was never
            # used (absent on older records = verified).
            "email_verified": is_verified(u) if u else True,
            # Which catalog motors this account may open (see below).  Shipped
            # with the record so the admin table draws the count without a
            # second round trip per user.
            "motors": normalize_grants(u.get("motors"))}


# ── per-user motor grants ────────────────────────────────────────────────────
# WHICH motors of the shared catalog an account may see is registry data, not a
# property of the machines: `motors: {"all": bool, "dies": [die names]}` on the
# user record. Enabled registered accounts also receive a read-time baseline
# for the shared CIANO14 40 new motor when its canonical files are installed.
# Admins (tier admin / ADMIN_EMAILS) bypass this entirely.

def _norm_default(raw) -> Optional[dict]:
    """Any stored shape -> `{"die": str, "config": str}` or None."""
    if not isinstance(raw, dict):
        return None
    die = str(raw.get("die") or "").strip()
    cfg = str(raw.get("config") or "").strip()
    return {"die": die, "config": cfg} if die and cfg else None


def _default_allowed(all_motors: bool, dies: list, default: Optional[dict]) -> bool:
    """A default motor must be one the account may open: any die for an
    ``all`` grant, otherwise a die named in the grant list."""
    return bool(default) and (all_motors or default["die"] in dies)


def normalize_grants(raw) -> dict:
    """Any stored shape -> `{"all": bool, "dies": [str]}` (never None), plus a
    `"default": {die, config}` key ONLY when an admin set one.  The default
    motor is dropped here whenever it is not covered by the grant, so a stale
    default can never outlive the grant it rode on."""
    if not isinstance(raw, dict):
        return {"all": False, "dies": []}
    dies = raw.get("dies")
    if not isinstance(dies, (list, tuple, set)):
        dies = []
    dies = sorted({str(d).strip() for d in dies if str(d).strip()})
    all_motors = bool(raw.get("all"))
    default = _norm_default(raw.get("default"))
    out = {"all": all_motors, "dies": dies}
    if _default_allowed(all_motors, dies, default):
        out["default"] = default
    return out


_BASELINE_DIE = "CIANO14 40 new"
_BASELINE_CONFIG = "L12"


def _shared_baseline_available() -> bool:
    """The default applies only to an installed shared L12 motor."""
    try:
        from motor_ai_sim.workspace import shared_root
        die = Path(str(shared_root())) / "dies" / _BASELINE_DIE
        return (die / "die.yaml").is_file() and (die / f"{_BASELINE_CONFIG}.yaml").is_file()
    except (OSError, RuntimeError, ValueError):
        return False


def _effective_motor_grants(email: str) -> dict:
    """Read-time grants for an existing, enabled account, without persistence."""
    registry = _load_soft()
    rec = registry.get(_norm(email))
    grants = normalize_grants(rec.get("motors") if isinstance(rec, dict) else None)
    if not isinstance(rec, dict) or bool(rec.get("disabled")):
        return grants
    if not _shared_baseline_available():
        return grants
    effective = dict(grants)
    if not effective["all"]:
        effective["dies"] = sorted(set(grants["dies"]) | {_BASELINE_DIE})
    if "default" not in effective:
        effective["default"] = {"die": _BASELINE_DIE, "config": _BASELINE_CONFIG}
    return effective


def get_motor_grants(email: str) -> dict:
    return _effective_motor_grants(email)


def get_default_motor(email: str) -> Optional[dict]:
    """Configured or shared-baseline starting motor, returned as fresh data."""
    default = _effective_motor_grants(email).get("default")
    return dict(default) if isinstance(default, dict) else None


_KEEP = object()


def set_motor_grants(email: str, *, all_motors: bool,
                     dies: Optional[list] = None, default=_KEEP) -> dict:
    """Replace an account's grants.  Written through the same atomic _save as
    every other registry change — never a second writer on users.json.

    `default` (`{"die", "config"}` or None) is the motor Configure opens on.
    Left out, the stored default is kept when the NEW grant still covers its
    die and cleared when it does not; None clears it.  The route validates
    that the die/configuration exist; this layer guarantees default ⊆ grants.
    """
    email = _norm(email)
    with _LOCK:
        users = _load()
        if email not in users:
            raise KeyError(email)
        if default is _KEEP:
            default = _norm_default((users[email].get("motors") or {}).get("default")
                                    if isinstance(users[email].get("motors"), dict) else None)
        else:
            default = _norm_default(default)
        users[email]["motors"] = normalize_grants(
            {"all": all_motors, "dies": list(dies or []), "default": default})
        _save(users)
    return get_motor_grants(email)


# ── invites ──────────────────────────────────────────────────────────────────
# An invite is NOT a second store.  There is one registry, and an invite is a
# row in it that the vendor created on purpose, stamped with who invited whom,
# when and why:  `invite: {"by", "at", "note", "role"}`.  A second file would
# have to be kept in step with users.json on every rename, disable and delete —
# and the one thing this deployment cannot afford is two answers to "may this
# person in?".
#
# No e-mail is sent, and none can be: the host blocks outbound 25/465, so the
# admin tells the person to sign in with Google.  The row exists first, which is
# what makes the difference between an invited account (its tier and its motors
# are already decided) and an unknown Google address (auto-registered at `free`
# with NOTHING granted — see auth._registry_role).
#
# The password of an invited account is RANDOM and nobody holds it: the door is
# Google sign-in, or an admin password reset.  It is not left empty, because an
# empty hash is a hash somebody might one day match.

def invite_user(email: str, *, role: str = "user", name: str = "",
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
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    stamp = {"by": _norm(by) or "admin",
             "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "note": str(note or "")[:500],
             "role": role}
    with _LOCK:
        users = _load()
        rec = users.get(email)
        if rec is None:
            rec = {"role": role,
                   "name": name or "",
                   "disabled": False,
                   "has_password": False,
                   "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
            _apply_pw(rec, secrets.token_urlsafe(32))
            users[email] = rec
        else:
            rec["role"] = role
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

def authenticate(email: str, password: str) -> tuple[str, Optional[dict]]:
    """Password check with a status: ("ok", rec) | ("bad", None) |
    ("unverified", None) | ("disabled", None).

    The hash is ALWAYS computed (a dummy one for unknown addresses), and the
    non-"bad" statuses are only reachable with the CORRECT password, so a
    caller that reveals them reveals nothing to someone who does not already
    hold the credential.  A legacy PBKDF2 hash is upgraded to argon2id here."""
    email = _norm(email)
    u = _load_soft().get(email)
    if not _verify_pw(u, password):
        return "bad", None
    if u.get("disabled"):
        return "disabled", None
    if not is_verified(u):
        return "unverified", None
    if u.get("pw_algo") != "argon2id":
        try:
            with _LOCK:
                users = _load()
                if email in users and users[email].get("pw_algo") != "argon2id":
                    _apply_pw(users[email], password)
                    users[email].pop("pw_changed", None)
                    _save(users)
                    log.info("auth: upgraded %s's password hash to argon2id", email)
        except Exception as e:                              # pragma: no cover
            log.warning("auth: argon2 rehash of %s failed (%s)", email, e)
    return "ok", u


def check_login(email: str, password: str) -> Optional[dict]:
    """Back-compat: the record when the password is right AND the account may
    sign in, else None."""
    status, rec = authenticate(email, password)
    return rec if status == "ok" else None


def issue_token(email: str, sid: Optional[str] = None,
                ttl_s: Optional[int] = None) -> str:
    """Sign a bearer for `email`.  `sid` names the server-side session; it is
    optional only so old call sites (and tests) keep working — a token without
    one simply cannot be listed or revoked."""
    email = _norm(email)
    u = _load_soft().get(email) or {}
    now = int(time.time())
    claims = {"sub": email, "email": email, "role": u.get("role", "user"),
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
                                   "role": u.get("role", "user")},
                       email=email, sid=sid, exp=exp, renewed_token=renewed)


def resolve_local_token(token: str) -> Optional[dict]:
    """Back-compat shim: `verify_token(...).user` or None.  New code should
    call `verify_token` and act on the REASON."""
    return verify_token(token).user
