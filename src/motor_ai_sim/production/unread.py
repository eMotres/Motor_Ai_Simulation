"""Unread messages of the production threads (owner 2026-10-28).  A production has one thread per part plus the general one; the
user's read mark is kept per thread (``message_reads``, ``message_reads.json`` in the production folder)."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from motor_ai_sim import message_reads
from motor_ai_sim.production import store


def since() -> datetime:
    return message_reads.since_for(store.production_root())


def thread_unread_ids(die: str, config: str, thread: dict, email: str, started: Optional[datetime] = None) -> list[str]:
    read_id = message_reads.last_read_id(store.path_for(die, config), email, str(thread.get("id") or ""))
    found = message_reads.unread_messages(thread.get("messages") or [], email, read_id, started or since())
    return [str(m["id"]) for m in found if m.get("id")]


def threads_with_unread(die: str, config: str, email: str) -> list[dict]:
    """The production's threads, each with ``unread`` (count) and ``unread_ids`` for ``email``."""
    started = since()
    out = []
    for thread in store.load_file(die, config, "threads.json").get("threads", []):
        if not isinstance(thread, dict):
            continue
        ids = thread_unread_ids(die, config, thread, email, started)
        out.append({**thread, "unread": len(ids), "unread_ids": ids})
    return out


def production_count(die: str, config: str, email: str, started: Optional[datetime] = None) -> int:
    started = started or since()
    total = 0
    for thread in store.load_file(die, config, "threads.json").get("threads", []):
        if isinstance(thread, dict):
            total += len(thread_unread_ids(die, config, thread, email, started))
    return total


def mark_read(die: str, config: str, thread_id: str, email: str, up_to: Optional[str]) -> Optional[str]:
    """Mark thread ``thread_id`` read up to ``up_to`` (newest when ``None``).  ``None`` when the thread does not exist."""
    for thread in store.load_file(die, config, "threads.json").get("threads", []):
        if isinstance(thread, dict) and thread.get("id") == thread_id:
            return message_reads.mark_read(store.path_for(die, config), thread.get("messages") or [], email, thread_id, up_to)
    return None


def counts_for(email: str, productions: list[dict], can_open: Callable[[dict], bool]) -> dict[str, int]:
    """Unread counts (non-zero only) keyed ``"<die>/<config>"`` for the productions the caller already may list and ``can_open``."""
    started = since()
    out = {}
    for rec in productions:
        die, config = str(rec.get("die") or ""), str(rec.get("config") or "")
        if not die or not config or not can_open(rec):
            continue
        n = production_count(die, config, email, started)
        if n:
            out[f"{die}/{config}"] = n
    return out
