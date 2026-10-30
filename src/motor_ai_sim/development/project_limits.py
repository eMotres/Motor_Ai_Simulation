"""How many open development projects one account may own (owner 2026-10-30).

Everybody gets ``DEFAULT_OPEN_LIMIT`` (2).  An admin may set a per-account override in Admin > Users: an integer >= 0, or
``"unlimited"``.  The overrides live in ``project_limits.json`` next to ``users.json`` and are keyed by e-mail, NOT stored on the
account record: an admin can set one for an address that has no account yet (a pending invite) and it applies on sign-up.  Staff
(admin / engineer) are never limited - see ``self_service.limit_reached``.  No override is ever set in code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from motor_ai_sim import json_store

DEFAULT_OPEN_LIMIT = 2
UNLIMITED = "unlimited"
MAX_LIMIT = 1000
Limit = Union[int, str]


def _file() -> Path:
    from motor_ai_sim import users as U
    return Path(U._USERS_FILE).parent / "project_limits.json"


def _norm_email(email: str) -> str:
    return str(email or "").strip().lower()


def normalize(raw) -> Optional[Limit]:
    """The stored form of a limit: ``None`` (no override: the default), an ``int`` in 0..MAX_LIMIT, or ``"unlimited"``.
    Empty text and None clear it; anything else raises ``ValueError``."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, str) and raw.strip().lower() in (UNLIMITED, "∞"):
        return UNLIMITED
    if isinstance(raw, bool):
        raise ValueError("project limit must be a whole number or 'unlimited'")
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise ValueError("project limit must be a whole number or 'unlimited'")
    if number != number or number != int(number) or number < 0 or number > MAX_LIMIT:
        raise ValueError("project limit must be a whole number from 0 to %d, or 'unlimited'" % MAX_LIMIT)
    return int(number)


def overrides() -> dict:
    """Every stored override ``{email: int | "unlimited"}`` (malformed entries are skipped)."""
    doc = json_store.read_json(_file(), default={})
    out = {}
    for email, value in (doc.items() if isinstance(doc, dict) else []):
        try:
            limit = normalize(value)
        except ValueError:
            continue
        if limit is not None and _norm_email(email):
            out[_norm_email(email)] = limit
    return out


def get_override(email: str) -> Optional[Limit]:
    return overrides().get(_norm_email(email))


def set_override(email: str, raw) -> Optional[Limit]:
    """Set (or, with None / empty, clear) the override of ``email``; returns what is stored now."""
    email = _norm_email(email)
    if not email or "@" not in email:
        raise ValueError("enter a valid e-mail address")
    limit = normalize(raw)

    def mutate(doc):
        if not isinstance(doc, dict):
            doc.clear()
        if limit is None:
            doc.pop(email, None)
        else:
            doc[email] = limit
    json_store.mutate_json(_file(), mutate, default={})
    return limit


def clear(email: str) -> int:
    """Drop the override of ``email`` (account purge); returns how many were removed."""
    email = _norm_email(email)
    if not _file().is_file():
        return 0
    hit = {"n": 0}

    def mutate(doc):
        if isinstance(doc, dict) and doc.pop(email, None) is not None:
            hit["n"] = 1
    json_store.mutate_json(_file(), mutate, default={})
    return hit["n"]


def limit_for(email: str) -> Optional[int]:
    """The number of open projects ``email`` may own; ``None`` = unlimited."""
    override = get_override(email)
    if override == UNLIMITED:
        return None
    return DEFAULT_OPEN_LIMIT if override is None else int(override)
