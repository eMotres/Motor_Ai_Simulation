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
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

import jwt

from motor_ai_sim.config import DEFAULT_CONFIG_PATH

_USERS_FILE = Path(DEFAULT_CONFIG_PATH).parent / "users.json"
_SECRET_FILE = Path(DEFAULT_CONFIG_PATH).parent / ".auth_secret"
_LOCK = threading.RLock()

_PBKDF2_ITERS = 600_000
_TOKEN_TTL_S = 30 * 24 * 3600          # 30 days
TIERS = ("free", "pro", "team", "admin")


# ── secret ───────────────────────────────────────────────────────────────────

def _secret() -> str:
    env = os.environ.get("AUTH_SECRET", "").strip()
    if env:
        return env
    with _LOCK:
        if _SECRET_FILE.is_file():
            s = _SECRET_FILE.read_text(encoding="utf-8").strip()
            if s:
                return s
        s = secrets.token_hex(32)
        _SECRET_FILE.write_text(s, encoding="utf-8")
        return s


# ── store ────────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        with open(_USERS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        # A corrupt users file must FAIL CLOSED (nobody logs in), not crash
        # the API — and it must be said in the log, loudly.
        import logging
        logging.getLogger(__name__).error("users.json is unreadable — logins disabled")
        return {}


def _save(d: dict) -> None:
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
    return _load().get(_norm(email))


def list_users() -> list[dict]:
    return [public_user(e) for e in sorted(_load().keys())]


def public_user(email: str) -> dict:
    u = _load().get(_norm(email)) or {}
    return {"email": _norm(email), "tier": u.get("tier", "free"),
            "name": u.get("name", ""), "disabled": bool(u.get("disabled")),
            "created": u.get("created")}


def any_users() -> bool:
    return bool(_load())


# ── login / tokens ───────────────────────────────────────────────────────────

def check_login(email: str, password: str) -> Optional[dict]:
    """Constant-time-ish password check → the user record, or None."""
    email = _norm(email)
    u = _load().get(email)
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


def issue_token(email: str) -> str:
    email = _norm(email)
    u = _load().get(email) or {}
    now = int(time.time())
    return jwt.encode(
        {"sub": email, "email": email, "tier": u.get("tier", "free"),
         "iat": now, "exp": now + _TOKEN_TTL_S, "iss": "motor-ai-sim"},
        _secret(), algorithm="HS256")


def resolve_local_token(token: str) -> Optional[dict]:
    """Verify OUR HS256 token → {uid,email,tier} or None.  The tier and the
    disabled flag are re-read from the store so revocation is immediate."""
    try:
        claims = jwt.decode(token, _secret(), algorithms=["HS256"],
                            issuer="motor-ai-sim",
                            options={"require": ["exp", "sub"]})
    except Exception:
        return None
    email = _norm(claims.get("email") or claims.get("sub") or "")
    u = _load().get(email)
    if u is None or u.get("disabled"):
        return None
    return {"uid": email, "email": email, "tier": u.get("tier", "free")}
