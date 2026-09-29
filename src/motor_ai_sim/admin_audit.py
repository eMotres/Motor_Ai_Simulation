"""Append-only record of what administrators did — and whose data they read.

Audit 2026-09-29 finding #8: die-access writes were audited, but tier changes,
disabling, invites, account deletion, session revocation and every READ of
support tickets / visitor chats / auth events were not.  An admin can see
personal data; the person it belongs to is owed a record of who looked.

One JSONL line per action, beside ``users.json`` (``admin_audit.jsonl``,
0600)::

    {"ts": 1759140000.123, "iso": "2026-09-29T10:00:00Z",
     "actor": "vadim@…", "action": "user.role", "target": "bob@…",
     "subject": "bob@…", "details": {"role": "admin"}, "break_glass": false}

* ``actor``   — the admin (``service-agent`` for the static read-only token,
                ``local-dev`` for the unconfigured laptop admin).
* ``action``  — dotted verb from :data:`ACTIONS` (free text is accepted but
                a typo'd action is logged as ``unknown:<name>``).
* ``target``  — the object acted on (an e-mail, a sid, a die, a request id).
* ``subject`` — the PERSON whose data was touched, when there is one; this is
                what :func:`for_subject` filters on for "who looked at my data".
* ``details`` — small, scrubbed: any key that smells of a secret is dropped
                and values are clipped (never a password, token or body).
* ``break_glass`` — True when an admin read or exported ANOTHER user's
                workspace; the owner of that workspace can list these
                (``GET /api/account/admin_access``).

Append-only by construction: the module has no update/delete API.  Retention
(:mod:`motor_ai_sim.retention`) is the only thing that drops old lines, after
``RETENTION_ADMIN_AUDIT_DAYS`` (default 730).  Deleting an account does NOT
rewrite this log — the record that an admin acted is kept for accountability
(GDPR Art. 5(2) / 17(3)(e)); the subject's e-mail in it is replaced by the
stable subject hash at deletion time (:func:`pseudonymise_subject`), which is
the one sanctioned rewrite and is itself logged.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from motor_ai_sim.private_files import chmod_private, ensure_private_dir, open_private

log = logging.getLogger(__name__)

_LOCK = threading.RLock()

ACTIONS = frozenset({
    # accounts
    "user.create", "user.update", "user.role", "user.tier", "user.disable", "user.delete",
    "user.delete_scheduled", "user.delete_cancelled", "user.approve",
    "user.invite", "user.invite_revoke", "user.motors", "user.export",
    "user.password_reset",
    # sessions / auth
    "session.list", "session.revoke", "session.revoke_all", "auth_events.read",
    # support / personal data reads
    "tickets.read", "tickets.status", "support.requests.read",
    "support.requests.status", "support.requests.delete",
    "support.chats.read", "support.config",
    # catalog / workspaces
    "die.access", "workspace.read", "workspace.export",
    # the log itself
    "audit.read", "audit.pseudonymise",
    # retention
    "retention.run",
})

_SECRETISH = ("password", "secret", "token", "key", "authorization", "cookie",
              "credential", "hash", "nonce", "body", "html", "text")
_MAX_VAL = 200


def audit_path() -> Path:
    env = (os.environ.get("ADMIN_AUDIT_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim import users as _U
    return Path(_U._USERS_FILE).parent / "admin_audit.jsonl"


def actor_of(admin: Optional[dict]) -> str:
    """The string an admin dependency's return value is logged as."""
    a = admin if isinstance(admin, dict) else {}
    return str(a.get("email") or a.get("uid") or "local-dev").strip().lower()


def _scrub(details: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (details or {}).items():
        lk = str(k).lower()
        if any(s in lk for s in _SECRETISH):
            continue
        if isinstance(v, (int, float, bool)) or v is None:
            out[str(k)] = v
        elif isinstance(v, (list, tuple)):
            out[str(k)] = [str(x)[:_MAX_VAL] for x in list(v)[:20]]
        else:
            out[str(k)] = str(v)[:_MAX_VAL]
    return out


def record(actor: str, action: str, target: str = "", *, subject: str = "",
           details: Optional[Dict[str, Any]] = None,
           break_glass: bool = False) -> Dict[str, Any]:
    """Append one entry.  Never raises (an audit problem must not block the
    action), but a failed write is logged at ERROR — a silent gap in an audit
    trail is the one failure this module exists to prevent."""
    now = time.time()
    act = action if action in ACTIONS else f"unknown:{action}"
    rec = {
        "ts": round(now, 3),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "actor": (actor or "unknown").strip().lower()[:200],
        "action": act,
        "target": str(target or "")[:200],
        "subject": (subject or "").strip().lower()[:200],
        "details": _scrub(details),
        "break_glass": bool(break_glass),
        "pid": os.getpid(),
    }
    p = audit_path()
    try:
        with _LOCK:
            ensure_private_dir(p.parent)
            with open_private(p, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as e:                                  # noqa: BLE001
        log.error("admin_audit: could not append %s by %s (%s: %s)",
                  act, rec["actor"], type(e).__name__, e)
    return rec


def _iter_lines() -> Iterable[Dict[str, Any]]:
    p = audit_path()
    if not p.is_file():
        return []
    with _LOCK:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def read(limit: int = 200, *, actor: str = "", action: str = "",
         subject: str = "") -> List[Dict[str, Any]]:
    """Newest first, optionally filtered."""
    want_a = (actor or "").strip().lower()
    want_s = (subject or "").strip().lower()
    out: List[Dict[str, Any]] = []
    for r in reversed(list(_iter_lines())):
        if want_a and r.get("actor") != want_a:
            continue
        if action and not str(r.get("action", "")).startswith(action):
            continue
        if want_s and r.get("subject") != want_s:
            continue
        out.append(r)
        if len(out) >= max(1, int(limit)):
            break
    return out


def for_subject(email: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Entries about one person — "which admin looked at my data, and when"."""
    return read(limit, subject=email)


def pseudonymise_subject(email: str, replacement: str) -> int:
    """Replace ``email`` by ``replacement`` in the subject/target fields of
    every line (account deletion).  Returns how many lines changed.  Rewrites
    via tmp + replace under the lock; the file stays 0600."""
    em = (email or "").strip().lower()
    if not em:
        return 0
    p = audit_path()
    if not p.is_file():
        return 0
    n = 0
    with _LOCK:
        rows = list(_iter_lines())
        for r in rows:
            hit = False
            for k in ("subject", "target"):
                if str(r.get(k) or "").strip().lower() == em:
                    r[k] = replacement
                    hit = True
            if hit:
                n += 1
        if n:
            tmp = p.with_suffix(".jsonl.tmp")
            with open_private(tmp, "w") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            os.replace(tmp, p)
            chmod_private(p)
    return n
