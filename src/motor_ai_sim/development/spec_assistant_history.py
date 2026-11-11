"""The specification assistant's conversation, kept on the server (owner 2026-11-11).

One file per project and user: ``<project>/spec_assistant/<sha256(email)[:16]>.json`` (atomic ``json_store`` writes through
``store``), ``{"messages": [{id, role, text, at, proposals, applied}]}``.  ``proposals`` / ``applied`` are proposal ids (the cards
of that reply); the current state of every card is read from ``spec_proposals`` when the history is loaded.  The whole history
is stored (bounded by ``MAX_STORED``); the model's context still takes only the last ``MAX_TURNS`` turns.

Only that user and staff (admins / engineers) can read it; whole project folders move with the account (``regions/migrate.py``
walks the folder), so nothing extra is needed there.
"""
from __future__ import annotations

import hashlib
import secrets

from motor_ai_sim.development import models, spec_proposals as P, store

MAX_STORED = 500
MAX_TEXT = 8000
_DIR = "spec_assistant"


def _name(email: str) -> str:
    return f"{_DIR}/{hashlib.sha256(email.strip().lower().encode('utf-8')).hexdigest()[:16]}.json"


def _doc() -> dict:
    return {"messages": []}


def _row(role: str, text: str, proposals: list[str] | None = None, applied: list[str] | None = None) -> dict:
    return {"id": "m-" + secrets.token_hex(5), "role": role, "text": str(text)[:MAX_TEXT], "at": models.now(),
            "proposals": list(proposals or []), "applied": list(applied or [])}


def append(project_id: str, email: str, rows: list[dict]) -> None:
    """Add messages (``{role, text, proposals?, applied?}``) to this user's history; the oldest fall off past ``MAX_STORED``."""
    if not rows:
        return
    new = [_row(r["role"], r["text"], r.get("proposals"), r.get("applied")) for r in rows]

    def mutate(doc: dict) -> None:
        msgs = doc.setdefault("messages", [])
        msgs.extend(new)
        del msgs[:-MAX_STORED]

    store.mutate_json(project_id, _name(email), mutate, default=_doc())


def clear(project_id: str, email: str) -> None:
    store.save_json(project_id, _name(email), _doc())


def load(project_id: str, email: str) -> list[dict]:
    """The stored messages of this user; every card id is replaced by the proposal as it stands now (open / applied / undone ...)."""
    doc = store.load_json(project_id, _name(email), _doc())
    rows = [m for m in (doc.get("messages") if isinstance(doc, dict) else []) or [] if isinstance(m, dict)]
    by_id = {p["id"]: p for p in P.list_proposals(project_id)}
    out: list[dict] = []
    for m in rows:
        row = {k: m.get(k) for k in ("id", "role", "text", "at")}
        cards: dict[str, list[dict]] = {}
        for key in ("proposals", "applied"):
            found = [by_id[i] for i in (m.get(key) or []) if i in by_id]
            cards[key] = P.with_current(project_id, found) if found else []
        row.update(cards)
        out.append(row)
    return out


def readable_by(user: dict, target_email: str) -> bool:
    """A user reads their own history; staff (admin / engineer) read anyone's."""
    from motor_ai_sim.development import access
    return access.is_engineer(user) or str(user.get("email") or "").strip().lower() == target_email.strip().lower()

