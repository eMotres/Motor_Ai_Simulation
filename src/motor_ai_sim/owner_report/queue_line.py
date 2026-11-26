"""The owner-queue line of the daily report (owner 2026-11-25): open decisions, messages, the oldest age, up to three urgent items.

``build_report`` in run.py adds it for the real sources (the scheduled run, the admin preview and send-now alike); a failure is
``Decisions: unavailable`` and never breaks the report."""
from __future__ import annotations

import calendar
import time
from typing import Optional


MAX_NAMED_URGENT = 3


def urgent_names(urgent: list) -> str:
    """``Pump project: <question, 60 chars>; ...`` for the first three urgent items, then ``+N`` (pure).  Items are ``{project_id, question}``."""
    named = []
    for it in urgent[:MAX_NAMED_URGENT]:
        q = str(it.get("question") or "")
        q = q if len(q) <= 60 else q[:59].rstrip() + "…"
        named.append((f"{it['project_id']}: " if it.get("project_id") else "") + q)
    more = len(urgent) - MAX_NAMED_URGENT
    return "; ".join(named) + (f"; +{more}" if more > 0 else "")


def format_queue_line(counts: dict, now: float, urgent: Optional[list] = None) -> str:
    """``Decisions: 2 urgent (Pump project: ...; ...), 4 open (3 specification submits), 9 messages, oldest 2 d 5 h`` from
    ``owner_queue.counts()`` (pure).  The urgent part appears only when something urgent is open; it names at most three
    items, then ``+N``."""
    line = "Decisions: "
    if counts.get("open_urgent"):
        line += f"{int(counts['open_urgent'])} urgent" + (f" ({urgent_names(urgent)})" if urgent else "") + ", "
    line += f"{int(counts.get('open_blocking', 0))} open"
    if counts.get("open_spec_submits"):
        line += f" ({int(counts['open_spec_submits'])} specification submits)"
    line += f", {int(counts.get('open_messages', 0))} messages"
    oldest = counts.get("oldest_open_at")
    if oldest:
        age = max(0, int(now - calendar.timegm(time.strptime(oldest, "%Y-%m-%dT%H:%M:%SZ"))))
        d, h = age // 86400, age % 86400 // 3600
        line += ", oldest " + (f"{d} d {h} h" if d else f"{h} h")
    return line


def queue_line(now: float) -> str:
    """The owner-queue line of the report; a failure is ``Decisions: unavailable`` and never breaks the report."""
    try:
        from motor_ai_sim import owner_queue as OQ
        c = OQ.counts()
        urgent = ([{"project_id": i.get("project_id"), "question": i.get("question")}
                   for i in OQ.list_items(state="open") if i.get("priority") == "urgent"] if c.get("open_urgent") else None)
        return format_queue_line(c, now, urgent)
    except Exception:                                              # noqa: BLE001
        return "Decisions: unavailable"
