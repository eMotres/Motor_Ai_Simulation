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
import os
import secrets
from typing import Any, Optional

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


#: per (audit file, store): (device, inode, offset read so far, fingerprint of the bytes just before it).  Only that - no ids
#: are kept between calls.  Bounded: at most this many cursors per process (the oldest is dropped; it only costs a rescan).
_CURSORS: dict = {}
_CURSORS_MAX = 512
_FP = 64                                    # bytes before the offset that prove the file still continues what was read
_CHUNK = 1024 * 1024


def _written_event_ids(project_id: str, json_name: str = "", wanted: Optional[set] = None, staged: Optional[dict] = None) -> set:
    """Which of the ``wanted`` event ids already have a COMPLETE, parsed record in ``audit.jsonl`` (call under its lock).

    NON-DESTRUCTIVE (coordinator 2026-11-22): the file is never truncated or rewritten; bytes that are not UTF-8 read as
    replacement characters and a line that does not parse is skipped (a torn line never counts and never raises; a complete
    record that only lacks its newline counts); a file that does not end with a newline gets one appended, so the next record
    starts on its own line.

    STREAMED AND BOUNDED (Astra follow-ups of #819 / #823): each store keeps its own cursor on the file and reads only the bytes
    appended since its last read, in chunks, looking only for its pending ids.  That is enough: a store's event is created
    before its record can be appended, and the store's previous flush read only up to the size the file had then - so the
    record lies after the cursor.  The cursor is trusted only while the file continues what was read: the same device / inode
    (from the opened file), a size not below the offset, and the same bytes just before the offset; otherwise the file is read
    again from the start."""
    path = _audit_path(project_id)
    wanted = set(wanted or ())
    key = (str(path), json_name)
    if not wanted:                                          # nothing pending: nothing to look for (never "collect everything")
        return set()
    try:
        fh = path.open("rb")
    except FileNotFoundError:
        _CURSORS.pop(key, None)
        return set()
    found: set = set()
    with fh:
        st = os.fstat(fh.fileno())
        dev_ino = (st.st_dev, st.st_ino)
        cur = _CURSORS.get(key)
        offset = 0
        if cur is not None and cur[0] == dev_ino and cur[1] <= st.st_size:
            fh.seek(max(0, cur[1] - _FP))
            if fh.read(min(_FP, cur[1])) == cur[2]:
                offset = cur[1]                             # the file still continues what this store read
        fh.seek(offset)
        pending = b""
        pos = offset
        while True:
            block = fh.read(_CHUNK)
            if not block:
                break
            data = pending + block
            cut = data.rfind(b"\n") + 1
            _scan(data[:cut], wanted, found)
            pending = data[cut:]
            pos += len(block)
        end = pos - len(pending)
    if pending:                                             # a last line without its newline: it counts if it is a complete record,
        _scan(pending, wanted, found)                       # and is ended (append-only) either way - a torn one never parses
        with path.open("ab") as out:
            out.write(b"\r\n")
        end = pos + 2
    with path.open("rb") as again:
        again.seek(max(0, end - _FP))
        fp = again.read(min(_FP, end))
    # the cursor is only PROPOSED here when the caller stages it: flush_events stores it once its pending removal has
    # committed, and drops the store's cursor on any failure - a failed save never leaves a cursor past an unremoved event
    if staged is not None:
        staged[key] = (dev_ino, end, fp)
    else:
        _store_cursor(key, (dev_ino, end, fp))
    return found


def _store_cursor(key: tuple, value: tuple) -> None:
    if len(_CURSORS) >= _CURSORS_MAX and key not in _CURSORS:
        _CURSORS.pop(next(iter(_CURSORS)))
    _CURSORS[key] = value


def _scan(data: bytes, wanted: set, found: set) -> None:
    # split ONLY on "\n": str.splitlines() also breaks on U+0085 / U+2028 / U+2029, which a file name inside a JSON string may hold
    for line in data.decode("utf-8", errors="replace").split("\n"):
        if not line.strip() or not any(w in line for w in wanted):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        details = rec.get("details") if isinstance(rec, dict) else None
        eid = str(details.get("event_id")) if isinstance(details, dict) and details.get("event_id") else ""
        if eid and eid in wanted:
            found.add(eid)


def flush_events(project_id: str, json_name: str) -> None:
    """Append every pending event of one store to the project audit and drop it from the store.  Under the audit's lock: only
    complete, parsed records count as written, and an event is dropped only after its own append returned.
    A failing append raises ``OSError`` and leaves the events pending (the store is not rewritten), so nothing is lost and
    nothing is written twice."""
    current = store.load_json(project_id, json_name, {})
    if not (isinstance(current, dict) and current.get(PENDING)):
        return
    from motor_ai_sim import json_store

    staged: dict = {}

    def apply(doc: Any) -> None:
        pending = doc.get(PENDING) or []                    # re-checked INSIDE the mutation: what is pending now
        if not pending:
            return
        written = _written_event_ids(project_id, json_name, {str(e.get("event_id")) for e in pending}, staged)
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

    key = (str(_audit_path(project_id)), json_name)
    try:
        with json_store.lock_for(_audit_path(project_id)):
            store.mutate_json(project_id, json_name, apply, default={})
            for k, v in staged.items():                     # the pending removal committed: now the cursor may move on
                _store_cursor(k, v)
    except BaseException as exc:
        _CURSORS.pop(key, None)                             # a failed flush keeps no progress: the next one rescans
        if isinstance(exc, ValueError):                     # never a bare 500: the caller turns OSError into a 503
            raise OSError("audit log unreadable: %s" % exc) from exc
        raise
