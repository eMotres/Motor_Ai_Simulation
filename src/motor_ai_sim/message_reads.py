"""Per-user read state of message threads (owner 2026-10-28: unread messages in projects and productions).

A conversation is an append-only list of messages.  For every user the server remembers the id of the LAST message the user has
seen in each thread (``mark_read``); a message is unread for a user when it comes after that one in the list, was written by
somebody else, and is newer than the feature's start (``since_for``).  Ids and list positions are used instead of timestamps:
production messages are stamped with one-second resolution, so a timestamp mark could swallow a message written in the same second.

Storage: ``message_reads.json`` inside the conversation's own folder (the project folder; the production folder) as
``{"marks": {"<email>": {"<thread key>": "<last read message id>"}}}``.  The marks follow the conversation when a die is renamed
(the production folder moves with it) and vanish with it when it is deleted.  Writes go through ``json_store.mutate_json``.

Migration: nothing written before the feature was first used counts as unread.  ``<root>/.unread_since.json`` holds that moment
(created on the first count after the deploy); a user who has no mark yet sees only messages newer than it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from motor_ai_sim import json_store

log = logging.getLogger(__name__)

MARKS_FILE = "message_reads.json"
SINCE_FILE = ".unread_since.json"
_PROCESS_START = datetime.now(timezone.utc)


def parse_time(value: Any) -> Optional[datetime]:
    """An ISO timestamp (with ``Z`` or an offset) as an aware UTC datetime; ``None`` when it is not one."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def since_for(root: Path) -> datetime:
    """When the unread feature started for the store at ``root``; written on first use.  A store that cannot be written falls
    back to the process start, so a read-only store never turns old history into unread messages."""
    path = Path(root) / SINCE_FILE
    got = parse_time((json_store.read_json(path, default={}) or {}).get("since"))
    if got is not None:
        return got
    if not Path(root).is_dir():                 # nothing stored yet: a read never creates the store
        return _PROCESS_START
    try:
        def mut(doc):
            if parse_time(doc.get("since")) is None:
                doc["since"] = datetime.now(timezone.utc).isoformat()
        doc = json_store.mutate_json(path, mut)
        return parse_time(doc.get("since")) or _PROCESS_START
    except OSError as exc:
        log.warning("unread marker %s not written (%s)", path, exc)
        return _PROCESS_START


def _marks_path(conv_dir: Path) -> Path:
    return Path(conv_dir) / MARKS_FILE


def last_read_id(conv_dir: Path, email: str, key: str) -> str:
    doc = json_store.read_json(_marks_path(conv_dir), default={}) or {}
    mine = (doc.get("marks") or {}).get(str(email or "").strip().lower())
    value = mine.get(key) if isinstance(mine, dict) else None
    return value if isinstance(value, str) else ""


def unread_messages(messages: Iterable[dict], email: str, read_id: str, since: datetime) -> list[dict]:
    """The messages ``email`` has not read: after the last-read one, written by somebody else, newer than ``since``."""
    me = str(email or "").strip().lower()
    items = [m for m in messages if isinstance(m, dict)]
    start = 0
    if read_id:
        for index, message in enumerate(items):
            if message.get("id") == read_id:
                start = index + 1
                break
    out = []
    for message in items[start:]:
        if str(message.get("author_email") or "").strip().lower() == me:
            continue
        stamp = parse_time(message.get("created_at"))
        if stamp is not None and stamp <= since:
            continue
        out.append(message)
    return out


def mark_read(conv_dir: Path, messages: list[dict], email: str, key: str, up_to: Optional[str]) -> str:
    """Move the user's mark for ``key`` forward to message ``up_to`` (the newest message when ``None``) and return the id now
    stored.  The mark never moves backwards.  Raises ``KeyError`` for an id that is not in ``messages``."""
    ids = [str(m.get("id")) for m in messages if isinstance(m, dict) and m.get("id")]
    if not ids:
        return ""
    target = ids[-1] if up_to is None else str(up_to)
    if target not in ids:
        raise KeyError(target)
    me = str(email or "").strip().lower()

    def mut(doc):
        marks = doc.setdefault("marks", {})
        mine = marks.setdefault(me, {})
        current = mine.get(key)
        if isinstance(current, str) and current in ids and ids.index(current) >= ids.index(target):
            return
        mine[key] = target
    doc = json_store.mutate_json(_marks_path(conv_dir), mut)
    return str(((doc.get("marks") or {}).get(me) or {}).get(key) or target)
