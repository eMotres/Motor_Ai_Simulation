"""Move the legacy "Internal notes" text of every project into its message thread as ONE staff-only message (owner 2026-11-24,
"one message stream per project").

The legacy store is the single text ``notes`` in ``project.json`` (staff-only).  Per project:

* the message id and the audit-intent id are STABLE (derived from the project id), so a second run finds the message and does nothing;
* the message is written like a normal staff-only post: one append to ``thread.jsonl`` that carries its ``audit_intent``, then
  ``thread_audit.flush`` (same durability, the audit row is written once);
* the original text is copied to ``internal_notes_backup.json`` in the project folder FIRST and never overwritten or deleted; the
  ``notes`` field of ``project.json`` is left untouched (nothing is lost, the thread message is a copy);
* it sends no e-mail and no notification, and a customer-side member never sees the message (``staff_only``); its time is the
  project's last update, so it is not new for anybody.

``plan`` is the dry run (counts per project, writes nothing); ``apply`` does the work.  The author is the project's
``owner_engineer`` when it is an address, else empty (the notes never recorded who wrote them)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from motor_ai_sim.development import store, thread_audit

BACKUP = "internal_notes_backup.json"


def message_id(project_id: str) -> str:
    return "note-" + hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16]


def _text(project: dict) -> str:
    value = project.get("notes")
    return value if isinstance(value, str) and value.strip() else ""


def _existing_ids(project_id: str) -> set:
    path = store._file(project_id, "thread.jsonl")
    out: set = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id"):
                out.add(str(row["id"]))
    return out


def plan() -> list[dict]:
    """The dry run: one row per project with its state: ``migrate`` | ``done`` | ``empty``.  Reads only."""
    rows = []
    for project in store.list_projects():
        pid = str(project.get("id") or "")
        if not store.valid_id(pid):
            continue
        text = _text(project)
        state = "empty" if not text else ("done" if message_id(pid) in _existing_ids(pid) else "migrate")
        rows.append({"project": pid, "state": state, "chars": len(text)})
    return rows


def _backup(project_id: str, text: str) -> None:
    path = store._file(project_id, BACKUP)
    if path.exists():
        return                                              # the first backup is the original: never overwritten
    record = {"saved_at": datetime.now(timezone.utc).isoformat(), "field": "notes", "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
              "text": text}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def apply() -> list[dict]:
    """Migrate every project that still has notes to move; returns the same rows as ``plan`` with the final state."""
    out = []
    for row in plan():
        pid = row["project"]
        if row["state"] != "migrate":
            out.append(row)
            continue
        project = store.get_project(pid) or {}
        text = _text(project)
        _backup(pid, text)
        mid = message_id(pid)
        owner = str(project.get("owner_engineer") or "").strip().lower()
        stamp = str(project.get("updated_at") or project.get("created_at") or datetime.now(timezone.utc).isoformat())
        message = {"id": mid, "author_email": owner if "@" in owner else "", "created_at": stamp, "text": text, "field_ref": None,
                   "row_id": None, "version": None, "revision": None, "staff_only": True, "migrated_from": "project_notes"}
        intent = thread_audit.make_intent(message["author_email"], "post", mid, {"field_ref": None, "staff_only": True,
                                                                                 "migrated_from": "project_notes"})
        intent["id"] = "intent-" + mid                      # stable: the audit row is written once, whatever the run count
        message["audit_intent"] = intent
        if mid not in _existing_ids(pid):                   # re-checked: a concurrent or interrupted run
            store.append_jsonl(pid, "thread.jsonl", message)
        thread_audit.flush(pid)
        out.append({**row, "state": "migrated"})
    return out
