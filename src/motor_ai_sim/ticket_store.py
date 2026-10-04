"""Self-hosted support tickets (bug / feature / question) filed by signed-in users.

Tickets used to be Firestore documents written straight from the browser; that
project is gone, so every ticket filed since was lost.  They now live in one
JSON file beside the access-request inbox (``support_store.root()``, i.e.
``/srv/motres/identity/support/tickets.json`` in production), written with the
same atomic, locked ``json_store.mutate_json`` the other self-hosted stores use.

The owner of a ticket is ALWAYS the authenticated caller (the route passes the
verified e-mail); nothing here reads an identity from a request body.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from motor_ai_sim import support_store
from motor_ai_sim.json_store import mutate_json, read_json

log = logging.getLogger(__name__)

TYPES = ("bug", "feature", "question")
STATUSES = ("open", "in_progress", "resolved", "closed")
MAX_TITLE = 120
MAX_DESCRIPTION = 4000
#: Tickets one account may file per rolling 24 h.
DAILY_LIMIT = 20
_DAY_S = 86400.0


class TicketError(ValueError):
    """A request the store refuses; ``status`` is the HTTP code to answer."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def tickets_file() -> Path:
    return support_store.root() / "tickets.json"


def _view(rec: dict) -> dict:
    """The shape the web UI reads (admin LogsSection and the widget)."""
    return {"id": rec.get("id"), "uid": rec.get("uid"), "type": rec.get("type"),
            "title": rec.get("title"), "description": rec.get("description") or "",
            "status": rec.get("status") or "open", "email": rec.get("email"),
            "createdAt": rec.get("createdAt")}


def validate(type_, title, description) -> tuple[str, str, str]:
    if type_ not in TYPES:
        raise TicketError(f"type must be one of {TYPES}")
    if not isinstance(title, str) or not title.strip():
        raise TicketError("title is required")
    if len(title.strip()) > MAX_TITLE:
        raise TicketError(f"title is longer than {MAX_TITLE} characters")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise TicketError("description must be text")
    if len(description.strip()) > MAX_DESCRIPTION:
        raise TicketError(f"description is longer than {MAX_DESCRIPTION} characters")
    return type_, title.strip(), description.strip()


def create(email: str, type_, title, description="") -> dict:
    """File one ticket for ``email`` (the authenticated caller)."""
    email = (email or "").strip().lower()
    if not email:
        raise TicketError("sign in to file a ticket", 401)
    type_, title, description = validate(type_, title, description)
    now = time.time()
    out: dict = {}

    def _mutate(doc):
        if not isinstance(doc, dict):
            doc = {}
        recent = sum(1 for r in doc.values() if isinstance(r, dict)
                     and r.get("uid") == email
                     and now - float(r.get("createdAt") or 0) / 1000.0 < _DAY_S)
        if recent >= DAILY_LIMIT:
            raise TicketError(
                f"ticket limit reached ({DAILY_LIMIT} per 24 h) - try again later", 429)
        tid = "t_" + uuid.uuid4().hex[:12]
        rec = {"id": tid, "uid": email, "email": email, "type": type_,
               "title": title, "description": description, "status": "open",
               "createdAt": now * 1000.0}
        doc[tid] = rec
        out.update(_view(rec))
        return doc

    tickets_file().parent.mkdir(parents=True, exist_ok=True)
    mutate_json(tickets_file(), _mutate, default={})
    log.info("tickets: %s filed %s %s", email, type_, out.get("id"))
    return out


def _all() -> list[dict]:
    try:
        doc = read_json(tickets_file(), {})
    except Exception:                                        # noqa: BLE001
        return []
    rows = [_view(r) for r in (doc.values() if isinstance(doc, dict) else ())
            if isinstance(r, dict)]
    rows.sort(key=lambda r: r.get("createdAt") or 0, reverse=True)
    return rows


def list_all() -> list[dict]:
    return _all()


def list_for(email: str) -> list[dict]:
    email = (email or "").strip().lower()
    return [r for r in _all() if r.get("uid") == email]


def set_status(ticket_id: str, status: str) -> Optional[dict]:
    if status not in STATUSES:
        raise TicketError(f"status must be one of {STATUSES}", 400)
    out: dict = {}

    def _mutate(doc):
        if not isinstance(doc, dict):
            return {}
        rec = doc.get(ticket_id)
        if isinstance(rec, dict):
            rec["status"] = status
            rec["statusAt"] = time.time() * 1000.0
            out.update(_view(rec))
        return doc

    mutate_json(tickets_file(), _mutate, default={})
    return out or None
