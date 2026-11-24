"""Durable audit of thread changes (coordinator 2026-11-23).

The audit INTENT travels inside the thread record itself (``audit_intent``), written in the same append as the message or the
resolution, so the change and the intent commit together.  ``flush`` turns every intent that is not yet in the project audit into a
pending outbox event (the event id IS the intent id, so the outbox writes it once) and flushes it; a done marker is added only after
the row is really in ``audit.jsonl``.  Any failure leaves the intent pending, never lost and never written twice.  Call it after a
post, a resolve and a thread read."""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from motor_ai_sim.development import store
from motor_ai_sim.development.project_uploads import PENDING, flush_events

log = logging.getLogger(__name__)
DONE_FILE = "thread_audit_done.jsonl"


def make_intent(actor: str, action: str, entity_id: str, details: dict) -> dict:
    return {"id": secrets.token_urlsafe(12), "time": datetime.now(timezone.utc).isoformat(), "actor_email": actor, "action": action,
            "entity": "thread_message", "entity_id": entity_id, "details": dict(details or {})}


def strip(row: dict) -> dict:
    """The row without its bookkeeping (never shown to a reader)."""
    return {k: v for k, v in row.items() if k != "audit_intent"} if "audit_intent" in row else row


def _rows(project_id: str, name: str) -> list:
    path = store._file(project_id, name)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _written(project_id: str, wanted: set) -> set:
    return {str((r.get("details") or {}).get("event_id")) for r in _rows(project_id, "audit.jsonl")} & wanted


def flush(project_id: str) -> None:
    try:
        done = {str(r.get("intent_id")) for r in _rows(project_id, DONE_FILE)}
        todo = {str(r["audit_intent"]["id"]): r["audit_intent"] for r in _rows(project_id, "thread.jsonl")
                if isinstance(r.get("audit_intent"), dict) and r["audit_intent"].get("id") and str(r["audit_intent"]["id"]) not in done}
        if not todo:
            return
        missing = {i: v for i, v in todo.items() if i not in _written(project_id, set(todo))}

        def queue(doc: dict) -> None:
            have = {str(e.get("event_id")) for e in doc.get(PENDING) or []}
            for i, v in missing.items():
                if i not in have:
                    doc.setdefault(PENDING, []).append({
                        "event_id": i, "time": v.get("time"), "actor_email": v.get("actor_email"), "action": v.get("action"),
                        "entity": v.get("entity"), "entity_id": v.get("entity_id"), "details": dict(v.get("details") or {})})
        if missing:
            store.mutate_project(project_id, queue)
            flush_events(project_id, "project.json")
        for i in _written(project_id, set(todo)):
            store.append_jsonl(project_id, DONE_FILE, {"intent_id": i})
    except Exception as exc:                                # noqa: BLE001 - the intent stays in the thread record: next call retries
        log.error("project %s: thread audit left pending (%s)", project_id, exc)
