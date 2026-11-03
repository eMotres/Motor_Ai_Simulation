"""Storage of a project's cost estimate (admin-only): ``<project>/cost_estimate/estimate.json`` (the live estimate, with an
optimistic ``revision``), ``cost_estimate/snapshots/NNNN.json`` (frozen copies with a note and the "what changed" diff
against the previous snapshot), ``cost_estimate/shared_quote.json`` (which snapshot, if any, an admin shared with the
project's customer as its quote: ``{"current": {snapshot_n, revision, shared_at, shared_by} | null}``) and
``cost_estimate/audit.jsonl``.  Kept OUT of the project's ``audit.jsonl`` on purpose:
nothing commercial ever lands in a file other readers of the project use."""
from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Optional

from motor_ai_sim import json_store
from motor_ai_sim.development import store

ESTIMATE = "cost_estimate/estimate.json"
SNAPSHOTS = "cost_estimate/snapshots"
SHARED = "cost_estimate/shared_quote.json"
AUDIT = "cost_estimate/audit.jsonl"


class RevisionConflict(Exception):
    def __init__(self, current: Optional[dict]):
        super().__init__("cost estimate changed meanwhile")
        self.current = current


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


#: stored shapes: 1 = the old 68-line catalogue (#551, converted on read by ``cost_estimate.from_stored``), 2 = 4 lines
VERSIONS = (1, 2)


def load(project_id: str) -> Optional[dict]:
    """The stored document as it is on disk (either shape); ``cost_estimate.from_stored`` reads it."""
    value = store.load_json(project_id, ESTIMATE, None)
    return value if isinstance(value, dict) and value.get("version") in VERSIONS else None


def save(project_id: str, expected_revision: int, build: Callable[[Optional[dict]], dict], actor: str) -> tuple[Optional[dict], dict]:
    """Under the file lock: refuse a stale ``expected_revision`` (``RevisionConflict`` with the stored estimate), else write
    ``build(previous)`` as revision + 1.  Returns ``(previous, saved)``."""
    store.ensure_writable()
    path = store._file(project_id, ESTIMATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: Dict[str, Any] = {}

    def mutate(doc: Any) -> dict:
        previous = doc if isinstance(doc, dict) and doc.get("version") in VERSIONS else None
        current_rev = int((previous or {}).get("revision") or 0)
        if int(expected_revision) != current_rev:
            raise RevisionConflict(previous)
        new = build(previous)
        new["revision"] = current_rev + 1
        new["updated_at"] = now()
        new["updated_by"] = actor
        seen["previous"] = previous
        return new

    saved = json_store.mutate_json(path, mutate, default={})
    return seen.get("previous"), saved


def audit(project_id: str, actor: str, action: str, details: Optional[dict] = None) -> None:
    store.append_jsonl(project_id, AUDIT, {"time": now(), "actor_email": str(actor or "").lower(), "action": action,
                                           "details": details or {}})


def read_audit(project_id: str) -> List[dict]:
    import json
    path = store._file(project_id, AUDIT)
    out: List[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        pass
    return out


def list_snapshots(project_id: str) -> List[dict]:
    directory = store._file(project_id, SNAPSHOTS)
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        value = json_store.read_json(path, default=None)
        if isinstance(value, dict) and value.get("n"):
            rows.append(value)
    return sorted(rows, key=lambda r: int(r.get("n") or 0))


def add_snapshot(project_id: str, make: Callable[[Optional[dict], int], dict]) -> dict:
    """Write the next snapshot ``make(previous snapshot, n)`` under the project's cost-estimate lock (numbers never reused)."""
    store.ensure_writable()
    directory = store._file(project_id, SNAPSHOTS)
    directory.mkdir(parents=True, exist_ok=True)
    with json_store.lock_for(directory / ".lock"):
        existing = list_snapshots(project_id)
        n = (int(existing[-1]["n"]) if existing else 0) + 1
        snap = make(existing[-1] if existing else None, n)
        path = store._file(project_id, f"{SNAPSHOTS}/{n:04d}.json")
        if path.exists():
            raise FileExistsError(path.name)
        json_store.atomic_write_json(path, snap)
    return snap


def get_snapshot(project_id: str, n: Any) -> Optional[dict]:
    """Snapshot number ``n`` as stored, or None."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    value = json_store.read_json(store._file(project_id, f"{SNAPSHOTS}/{n:04d}.json"), default=None)
    return value if isinstance(value, dict) and int(value.get("n") or 0) == n else None


def _valid_mark(mark: Any) -> Optional[dict]:
    if not isinstance(mark, dict):
        return None
    try:
        n = int(mark.get("snapshot_n"))
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return {"snapshot_n": n, "revision": mark.get("revision"), "shared_at": str(mark.get("shared_at") or ""),
            "shared_by": str(mark.get("shared_by") or "")}


def load_shared(project_id: str) -> Optional[dict]:
    """The project's shared quote mark ``{snapshot_n, revision, shared_at, shared_by}``, or None when nothing is shared."""
    doc = store.load_json(project_id, SHARED, None)
    return _valid_mark(doc.get("current")) if isinstance(doc, dict) else None


def set_shared(project_id: str, mark: Optional[dict]) -> Optional[dict]:
    """Replace the shared quote mark (``None`` = stop sharing) under the file lock; returns the previous mark."""
    store.ensure_writable()
    path = store._file(project_id, SHARED)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: Dict[str, Any] = {}

    def mutate(doc: Any) -> dict:
        seen["previous"] = _valid_mark(doc.get("current")) if isinstance(doc, dict) else None
        return {"current": _valid_mark(mark) if mark is not None else None, "updated_at": now()}

    json_store.mutate_json(path, mutate, default={})
    return seen.get("previous")
