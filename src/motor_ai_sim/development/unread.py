"""Unread messages of the project threads (owner 2026-10-28).  Read state lives in ``message_reads`` (one mark per user in the
project folder); this module reads the project thread and applies it."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from motor_ai_sim import message_reads
from motor_ai_sim.development import access, store

THREAD_KEY = "thread"


def thread_messages(project_id: str) -> list[dict]:
    """The real messages of the thread, oldest first (resolution events are bookkeeping, not messages)."""
    path = store._file(project_id, "thread.jsonl")
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("kind") != "resolution" and item.get("id"):
            out.append(item)
    return out


def since() -> datetime:
    return message_reads.since_for(store.root())


def visible_thread(project_id: str, user: Optional[dict]) -> list[dict]:
    """The thread as ``user`` may see it: a price-restricted message only for whoever sees prices (see ``access.can_see_message``).
    No ``user`` = no restricted message (fail closed)."""
    project = store.get_project(project_id) or {}
    return access.visible_messages(user, project, thread_messages(project_id))


def unread_ids(project_id: str, email: str, *, messages: Optional[list[dict]] = None,
               started: Optional[datetime] = None, user: Optional[dict] = None) -> list[str]:
    """Unread message ids of ``email``; a price-restricted message counts only when ``user`` may see it (never without ``user``)."""
    if messages is None:
        messages = visible_thread(project_id, user)
    else:
        messages = access.visible_messages(user, store.get_project(project_id) or {}, messages)
    read_id = message_reads.last_read_id(store.path_for(project_id), email, THREAD_KEY)
    found = message_reads.unread_messages(messages, email, read_id, started or since())
    return [str(m["id"]) for m in found]


def mark_read(project_id: str, email: str, up_to: Optional[str], user: Optional[dict] = None) -> str:
    """A restricted message the caller may not see is "not found" here too (KeyError), never a different answer."""
    visible = visible_thread(project_id, user)
    if up_to is not None and str(up_to) not in {str(m.get("id")) for m in visible}:
        raise KeyError(up_to)                                # also when nothing at all is visible to the caller
    return message_reads.mark_read(store.path_for(project_id), visible, email, THREAD_KEY, up_to)


def counts_for(user: dict) -> dict[str, int]:
    """Unread counts (non-zero only) of the projects ``user`` may open - the same rule as the project list."""
    email = str(user.get("email") or "").strip().lower()
    if access.is_engineer(user):
        projects = store.list_projects()
    else:
        projects = [p for p in (store.get_project(pid) for pid in store.membership_projects(email)) if p]
    started = since()
    out = {}
    for project in projects:
        project_id = str(project.get("id") or "")
        if not store.valid_id(project_id) or not access.can_view(user, project):
            continue
        n = len(unread_ids(project_id, email, started=started, user=user))
        if n:
            out[project_id] = n
    return out
