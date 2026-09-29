"""Agent keys — per-user API keys for the MCP server (Stage 1, 2026-09-28).

An external AI agent (Claude Desktop / Claude Code / a ChatGPT connector)
talks to ``/mcp`` with ``Authorization: Bearer emk_<id>_<secret>``.  The key is
created by the signed-in owner in the web ("Access for agents"), shown ONCE,
and only its SHA-256 is stored.

Storage: one JSON file next to ``users.json`` (``agent_keys.json``), written
through ``json_store.mutate_json`` (lock + atomic replace).  Deliberately NOT
inside ``users.json``: that file belongs to the auth module and a second writer
there is how an account store gets clobbered.

Stage-2 readiness (OAuth 2.1, MCP auth spec): every credential resolves to the
SAME ``Principal`` shape (``email``, ``credential_id``, ``kind``, ``scopes``).
An OAuth access token will be one more ``kind`` ("oauth") resolved by a second
verifier; keys keep working unchanged.  See docs/MCP_2026-09-28.md.

Quotas: per key, calls/minute and calls/day, in process memory (one API
process serves the server).  Audit: one JSONL line per MCP tool call.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from motor_ai_sim.json_store import lock_for, mutate_json, read_json

#: Every scope a key / OAuth grant may carry.  Stage 3 (2026-09-28) added
#: ``designs:write`` (create DRAFT machines in the owner's workspace) and
#: ``simulate`` (queue solves of those drafts, daily quota).  Overwriting a
#: saved duty (``duties:write``) does not exist yet.
SCOPES: Tuple[str, ...] = ("catalog:read", "machines:read", "designs:write",
                           "simulate")
#: What a key gets when the owner ticks nothing: read-only.
DEFAULT_SCOPES: Tuple[str, ...] = ("catalog:read", "machines:read")
#: One line per scope — the key dialog and the consent page print these.
SCOPE_DESCRIPTIONS: Dict[str, str] = {
    "catalog:read": "Read the materials and dies catalog",
    "machines:read": "Read your machines and their saved results",
    "designs:write": "Create draft machines in your workspace (marked as the "
                     "agent's; never changes your saved machines or open motor)",
    "simulate": "Queue simulations of those drafts in your job queue "
                "(daily quota)",
}
TOKEN_PREFIX = "emk_"
MAX_KEYS_PER_USER = 10


def _config_dir() -> Path:
    # Same directory as users.json (users._USERS_FILE), resolved late so a test
    # can point it elsewhere with MCP_KEYS_DIR.
    env = os.environ.get("MCP_KEYS_DIR", "").strip()
    if env:
        return Path(env)
    from motor_ai_sim import users as _users
    return Path(_users._USERS_FILE).parent


def store_path() -> Path:
    return _config_dir() / "agent_keys.json"


def audit_path() -> Path:
    return _config_dir() / "mcp_audit.jsonl"


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _now() -> float:
    return time.time()


# ── principal ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Principal:
    """Who an MCP call acts for.  The one shape every credential kind maps to."""
    email: str                       # the owning account (same id as `owner`)
    credential_id: str               # key id (or, Stage 2, OAuth grant id)
    kind: str = "api_key"            # "api_key" | "oauth" (Stage 2)
    scopes: Tuple[str, ...] = field(default_factory=tuple)
    #: The key's name / the OAuth client's name — what drafts and jobs are
    #: labelled with ("created by agent <client_name>").
    client_name: str = ""

    def has(self, scope: str) -> bool:
        return scope in self.scopes


# ── store ────────────────────────────────────────────────────────────────────

def _load() -> Dict[str, Any]:
    d = read_json(store_path(), default=None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("version", 1)
    d.setdefault("keys", {})
    return d


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: rec.get(k) for k in ("id", "name", "prefix", "scopes",
                                    "created_at", "last_used_at",
                                    "revoked_at", "kind")} | {
        "active": not rec.get("revoked_at")}


def create_key(owner: str, name: str = "",
               scopes: Optional[List[str]] = None) -> Tuple[str, Dict[str, Any]]:
    """Mint a key for `owner`.  Returns ``(token, public_record)`` — the token
    is never stored and never shown again."""
    owner = (owner or "").strip().lower()
    if not owner:
        raise ValueError("an owner is required")
    sc = list(dict.fromkeys(scopes or DEFAULT_SCOPES))
    bad = [s for s in sc if s not in SCOPES]
    if bad:
        raise ValueError(f"unknown scope(s): {bad}; allowed: {list(SCOPES)}")
    if not sc:
        raise ValueError("at least one scope is required")
    kid = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    token = f"{TOKEN_PREFIX}{kid}_{secret}"
    rec = {"id": kid, "owner": owner, "name": (name or "").strip()[:80] or "agent key",
           "prefix": token[:12], "hash": _hash(secret), "scopes": sc,
           "kind": "api_key", "created_at": _now(), "last_used_at": None,
           "revoked_at": None}

    def _mut(d):
        d = d if isinstance(d, dict) else {}
        keys = d.setdefault("keys", {})
        d.setdefault("version", 1)
        live = [r for r in keys.values()
                if r.get("owner") == owner and not r.get("revoked_at")]
        if len(live) >= MAX_KEYS_PER_USER:
            raise ValueError(f"at most {MAX_KEYS_PER_USER} active keys per account")
        keys[kid] = rec
        return d

    mutate_json(store_path(), _mut, default={})
    return token, _public(rec)


def list_keys(owner: str) -> List[Dict[str, Any]]:
    owner = (owner or "").strip().lower()
    return sorted((_public(r) for r in _load()["keys"].values()
                   if r.get("owner") == owner),
                  key=lambda r: -(r.get("created_at") or 0))


def revoke_key(owner: str, kid: str) -> bool:
    owner = (owner or "").strip().lower()
    hit = {"ok": False}

    def _mut(d):
        rec = (d or {}).get("keys", {}).get(kid)
        if rec and rec.get("owner") == owner and not rec.get("revoked_at"):
            rec["revoked_at"] = _now()
            hit["ok"] = True
        return d

    mutate_json(store_path(), _mut, default={})
    return hit["ok"]


#: last_used_at is written at most this often per key (a write per call would
#: turn every MCP request into a file rewrite).
_TOUCH_EVERY_S = 60.0
_touched: Dict[str, float] = {}


def _touch(kid: str) -> None:
    now = _now()
    if now - _touched.get(kid, 0.0) < _TOUCH_EVERY_S:
        return
    _touched[kid] = now

    def _mut(d):
        rec = (d or {}).get("keys", {}).get(kid)
        if rec:
            rec["last_used_at"] = now
        return d
    try:
        mutate_json(store_path(), _mut, default={})
    except Exception:                                   # noqa: BLE001
        pass


def verify(authorization: Optional[str]) -> Tuple[Optional[Principal], str]:
    """``Bearer emk_…`` -> ``(Principal, "ok")`` or ``(None, reason)``.

    reason: no_token | malformed | unknown_key | revoked | disabled.
    """
    if not isinstance(authorization, str) or not authorization.strip():
        return None, "no_token"
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None, "malformed"
    token = parts[1].strip()
    if not token.startswith(TOKEN_PREFIX):
        return None, "malformed"
    body = token[len(TOKEN_PREFIX):]
    kid, sep, secret = body.partition("_")
    if not sep or not kid or not secret:
        return None, "malformed"
    rec = _load()["keys"].get(kid)
    if not rec or not hmac.compare_digest(str(rec.get("hash") or ""), _hash(secret)):
        return None, "unknown_key"
    if rec.get("revoked_at"):
        return None, "revoked"
    owner = str(rec.get("owner") or "")
    if _account_disabled(owner):
        return None, "disabled"
    _touch(kid)
    return Principal(email=owner, credential_id=kid, kind="api_key",
                     scopes=tuple(rec.get("scopes") or ()),
                     client_name=str(rec.get("name") or "agent key")), "ok"


def _account_disabled(email: str) -> bool:
    """A disabled account's keys stop working with its sessions."""
    try:
        from motor_ai_sim import users as _users
        u = _users.get_user(email)
        return bool(u and u.get("disabled"))
    except Exception:                                   # noqa: BLE001
        return False


# ── quotas ───────────────────────────────────────────────────────────────────

def _limit(env: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(env, str(default))))
    except ValueError:
        return default


def per_minute_limit() -> int:
    return _limit("MCP_RATE_PER_MIN", 30)


def per_day_limit() -> int:
    return _limit("MCP_RATE_PER_DAY", 2000)


_Q_LOCK = threading.Lock()
_minute: Dict[str, Deque[float]] = {}
_day: Dict[str, Tuple[str, int]] = {}


def _day_key(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def take_quota(cred_id: str, now: Optional[float] = None) -> Tuple[bool, int]:
    """Count one call.  ``(True, 0)`` when allowed, else ``(False, retry_after_s)``."""
    t = _now() if now is None else now
    with _Q_LOCK:
        dq = _minute.setdefault(cred_id, deque())
        while dq and t - dq[0] >= 60.0:
            dq.popleft()
        dk = _day_key(t)
        day, n = _day.get(cred_id, (dk, 0))
        if day != dk:
            day, n = dk, 0
        if n >= per_day_limit():
            midnight = (int(t // 86400) + 1) * 86400
            return False, max(1, int(midnight - t))
        if len(dq) >= per_minute_limit():
            return False, max(1, int(60.0 - (t - dq[0])) + 1)
        dq.append(t)
        _day[cred_id] = (day, n + 1)
        return True, 0


def reset_quotas() -> None:
    with _Q_LOCK:
        _minute.clear()
        _day.clear()


# ── audit ────────────────────────────────────────────────────────────────────

def _summ(args: Any, limit: int = 300) -> str:
    try:
        s = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:                                   # noqa: BLE001
        s = repr(args)
    return s if len(s) <= limit else s[:limit] + "…"


def audit(*, principal: Optional[Principal], method: str, tool: str = "",
          args: Any = None, status: int = 200, note: str = "") -> None:
    rec = {"ts": _now(), "email": principal.email if principal else None,
           "key": principal.credential_id if principal else None,
           "kind": principal.kind if principal else None,
           "method": method, "tool": tool or None,
           "args": _summ(args) if args is not None else None,
           "status": status, **({"note": note} if note else {})}
    if method == "tools/call":
        try:  # Admin -> Servers: MCP calls/min and 429s
            from motor_ai_sim import cluster_monitor as _cm
            _cm.note_mcp_call(status)
            from motor_ai_sim import usage_stats as _us
            who = principal.email if principal else None
            _us.note(who, "mcp_call", tool)
            if status == 429:
                _us.note(who, "mcp_429")
        except Exception:                               # noqa: BLE001
            pass
    p = audit_path()
    try:
        from motor_ai_sim.private_files import ensure_private_dir, open_private
        ensure_private_dir(p.parent)
        with lock_for(p):
            with open_private(p, "a") as f:     # 0600 — audit 2026-09-29 #9
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:                                   # noqa: BLE001
        pass


def read_audit(email: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    p = audit_path()
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    with lock_for(p):
        lines = p.read_text(encoding="utf-8").splitlines()
    for ln in reversed(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if email is None or r.get("email") == email:
            out.append(r)
            if len(out) >= limit:
                break
    return out
