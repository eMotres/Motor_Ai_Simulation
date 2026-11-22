"""Shared helpers of the project's own file stores (tab "3D model", section "Contract"; owner 2026-11-22).

* ``bounded_form``  parses a multipart upload under a CUMULATIVE byte cap enforced while the body is received (a missing or false
  Content-Length does not help), with at most one file part and a few fields.  Anything larger is a 413 before the form is built.
* ``queue_event`` / ``flush_events``  make a store change and its audit row one unit: the event is written into the store's JSON
  together with the change (same lock, same write), then appended to ``audit.jsonl`` and removed.  When the append fails the change
  stays with its event pending, and the next call of ``flush_events`` on that store writes it (idempotent: an event already in the
  audit file is not written twice).
"""
from __future__ import annotations

import datetime
import json
import secrets
from typing import Any

from fastapi import HTTPException, Request

from motor_ai_sim.development import store

PENDING = "pending_audit"
_SLACK = 64 * 1024                       # multipart headers and boundaries around the one file


async def bounded_form(request: Request, limit: int, *, max_fields: int = 8):
    """``request.form()`` that never receives more than ``limit`` bytes of body (413) and accepts one file part only."""
    cap = limit + _SLACK
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > cap:
        raise HTTPException(status_code=413, detail="The file is larger than %d MB" % (limit // (1024 * 1024)))
    seen = 0
    receive = request._receive                                     # noqa: SLF001 - Starlette reads the body through it

    async def counted():
        nonlocal seen
        message = await receive()
        if message.get("type") == "http.request":
            seen += len(message.get("body", b""))
            if seen > cap:
                raise HTTPException(status_code=413, detail="The file is larger than %d MB" % (limit // (1024 * 1024)))
        return message

    request._receive = counted                                     # noqa: SLF001
    try:
        return await request.form(max_files=1, max_fields=max_fields)
    except HTTPException:
        raise
    except Exception as exc:                                       # noqa: BLE001 - a malformed body or too many parts
        raise HTTPException(status_code=400, detail="Invalid upload: %s" % exc)


def queue_event(doc: dict, actor_email: str, action: str, entity: str, entity_id: str, details: dict | None = None) -> None:
    """Add one audit event to ``doc`` (inside the store's mutation, so the change and its event are written together)."""
    doc.setdefault(PENDING, []).append({
        "event_id": secrets.token_urlsafe(12), "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "actor_email": actor_email, "action": action, "entity": entity, "entity_id": entity_id, "details": dict(details or {})})


def _audit_path(project_id: str):
    return store.path_for(project_id) / "audit.jsonl"


def _written_event_ids(project_id: str) -> set:
    """The event ids of the complete, parsed records of ``audit.jsonl`` (call under its lock).  NON-DESTRUCTIVE (coordinator
    2026-11-22): the file is never truncated or rewritten.  Bytes that are not UTF-8 are read as replacement characters and a line
    that does not parse is skipped, so a torn line never counts as written and never raises.  When the file does not end with a
    newline, one is appended (append-only), so the next record starts on its own line."""
    path = _audit_path(project_id)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return set()
    if raw and not raw.endswith(b"\n"):
        with path.open("ab") as fh:
            fh.write(b"\r\n")
    ids = set()
    # split ONLY on "\n": str.splitlines() also breaks on U+0085 / U+2028 / U+2029, which a file name inside a JSON string may hold
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        details = rec.get("details") if isinstance(rec, dict) else None
        if isinstance(details, dict) and details.get("event_id"):
            ids.add(str(details["event_id"]))
    return ids


def flush_events(project_id: str, json_name: str) -> None:
    """Append every pending event of one store to the project audit and drop it from the store.  Under the audit's lock: only
    complete, parsed records count as written, and an event is dropped only after its own append returned.
    A failing append raises ``OSError`` and leaves the events pending (the store is not rewritten), so nothing is lost and
    nothing is written twice."""
    current = store.load_json(project_id, json_name, {})
    if not (isinstance(current, dict) and current.get(PENDING)):
        return
    from motor_ai_sim import json_store

    def apply(doc: Any) -> None:
        written = _written_event_ids(project_id)
        for event in list(doc.get(PENDING) or []):
            eid = str(event.get("event_id"))
            if eid not in written:
                store.append_jsonl(project_id, "audit.jsonl", {
                    "time": event.get("time"), "actor_email": str(event.get("actor_email") or "").lower(), "action": event.get("action"),
                    "entity": event.get("entity"), "entity_id": event.get("entity_id"),
                    "details": {**(event.get("details") or {}), "event_id": eid}})
                written.add(eid)
            doc[PENDING].remove(event)
        if not doc.get(PENDING):
            doc.pop(PENDING, None)

    try:
        with json_store.lock_for(_audit_path(project_id)):
            store.mutate_json(project_id, json_name, apply, default={})
    except ValueError as exc:                               # never a bare 500: the caller turns OSError into a 503
        raise OSError("audit log unreadable: %s" % exc) from exc
