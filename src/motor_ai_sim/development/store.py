from __future__ import annotations

import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from motor_ai_sim import json_store
from fastapi import HTTPException

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def root() -> Path:
    configured = os.environ.get("MOTRES_PROJECTS_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    from motor_ai_sim.workspace import shared_root
    return (Path(str(shared_root())) / "projects").resolve()


def ensure_writable() -> Path:
    """Prepare the configured project store for a write, without probe files.

    Reads and ``root()`` stay side-effect-free. A missing explicit nested store
    is created only after its nearest existing ancestor passes permission and
    ACL checks. An unavailable default shared store fails closed with HTTP 503.
    """
    target = root()

    def check_directory(path: Path) -> None:
        try:
            mode = path.stat().st_mode
            writable_mode = mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
            if not stat.S_ISDIR(mode) or not writable_mode or not os.access(path, os.W_OK):
                raise OSError("directory is not writable")
        except OSError as exc:
            raise HTTPException(status_code=503, detail="Project store is unavailable or read-only") from exc

    try:
        if target.exists():
            check_directory(target)
            return target
        ancestor = target.parent
        while not ancestor.exists():
            parent = ancestor.parent
            if parent == ancestor:
                raise OSError("no existing ancestor for project store")
            ancestor = parent
        check_directory(ancestor)
        target.mkdir(parents=True, exist_ok=True)
        check_directory(target)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Project store is unavailable or read-only") from exc
    return target


@contextmanager
def project_transaction():
    """Serialize membership, project, and motor-index cross-file mutations."""
    ensure_writable()
    with json_store.lock_for(root() / ".project-transaction.lock"):
        yield


def valid_id(project_id: str) -> bool:
    return bool(_ID.fullmatch(str(project_id or "")))


def path_for(project_id: str) -> Path:
    if not valid_id(project_id):
        raise ValueError("invalid project id")
    base = root()
    path = (base / project_id).resolve()
    if base != path and base not in path.parents:
        raise ValueError("project path outside root")
    return path


def _file(project_id: str, name: str) -> Path:
    candidate = (path_for(project_id) / name).resolve()
    base = path_for(project_id).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError("project file path outside project root")
    return candidate


def _read(path: Path, default: Any) -> Any:
    return json_store.read_json(path, default=default)


def get_project(project_id: str) -> dict | None:
    if not valid_id(project_id):
        return None
    path = _file(project_id, "project.json")
    value = _read(path, None)
    return value if isinstance(value, dict) else None


def mutate_project(project_id: str, mutator: Callable[[dict], None]) -> dict:
    ensure_writable()
    path = _file(project_id, "project.json")
    return json_store.mutate_json(path, mutator, default={})


def create_project(project: dict) -> dict:
    ensure_writable()
    project_id = str(project.get("id") or "")
    path = _file(project_id, "project.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with project_transaction():
        with json_store.lock_for(path):
            if path.exists():
                raise FileExistsError(project_id)
            json_store.atomic_write_json(path, project)
        try:
            sync_membership_index(project_id, [], project.get("members", []))
        except Exception:
            # Do not leave a created-but-undiscoverable project if index publication fails.
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
            raise
    return project


def load_json(project_id: str, name: str, default: Any) -> Any:
    return _read(_file(project_id, name), default)


def save_json(project_id: str, name: str, data: Any) -> None:
    ensure_writable()
    path = _file(project_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    json_store.atomic_write_json(path, data)


def mutate_json(project_id: str, name: str, mutator: Callable[[Any], None], default: Any = None) -> Any:
    ensure_writable()
    path = _file(project_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return json_store.mutate_json(path, mutator, default={} if default is None else default)


def append_jsonl(project_id: str, name: str, entry: dict) -> None:
    ensure_writable()
    path = _file(project_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\r\n"
    with json_store.lock_for(path):
        # The serialized line already contains CRLF. Disable newline
        # translation so Windows does not turn it into CRCRLF and introduce
        # blank, non-JSON audit records.
        with path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(line)


def append_audit(project_id: str, actor_email: str, action: str, entity: str, entity_id: str, details: dict | None = None) -> None:
    from datetime import datetime, timezone
    append_jsonl(project_id, "audit.jsonl", {"time": datetime.now(timezone.utc).isoformat(), "actor_email": actor_email.lower(),
                                               "action": action, "entity": entity, "entity_id": entity_id, "details": details or {}})


def save_snapshot(project_id: str, snapshot: dict) -> dict:
    """Persist a versioned project-only machine snapshot; never writes catalog data."""
    ensure_writable()
    snapshot_id = str(snapshot.get("id") or "")
    if not valid_id(snapshot_id):
        raise ValueError("invalid snapshot id")
    path = _file(project_id, f"snapshots/{snapshot_id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with json_store.lock_for(path):
        if path.exists():
            raise FileExistsError(snapshot_id)
        json_store.atomic_write_json(path, snapshot)
    return snapshot


def list_snapshots(project_id: str) -> list[dict]:
    directory = _file(project_id, "snapshots")
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        value = _read(path, None)
        if isinstance(value, dict) and value.get("version") == 1:
            rows.append(value)
    return sorted(rows, key=lambda row: str(row.get("created_at") or ""), reverse=True)


def motor_history(project_id: str, project: dict | None = None) -> list[dict]:
    """Read stage-3 events plus legacy audit/grant history for Restore rows."""
    project = project if isinstance(project, dict) else get_project(project_id) or {}
    rows = [dict(row) for row in project.get("motor_history", []) if isinstance(row, dict)]
    known = {(str(row.get("die") or "").casefold(), str(row.get("config") or "").casefold())
             for row in rows}
    audit_path = _file(project_id, "audit.jsonl")
    ordered: list[dict] = []
    try:
        with audit_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict) or event.get("entity") != "motor":
                    continue
                if event.get("action") == "motor.rename":
                    # A die was renamed (die_rename.py): the earlier link / unlink events
                    # of that die belong to the NEW name - the audit log itself is append-only.
                    old_die = str(event.get("entity_id") or "").casefold()
                    new_die = str((event.get("details") or {}).get("to") or "")
                    if old_die and new_die:
                        for prior in ordered:
                            if prior["die"].casefold() == old_die:
                                prior["die"] = new_die
                    continue
                if event.get("action") == "motor.move":
                    # A configuration was moved to another die (config_move.py): its earlier
                    # link / unlink events belong to the die it lives in now.
                    old_die, _s1, old_cfg = str(event.get("entity_id") or "").partition("/")
                    new_die, _s2, _new_cfg = str((event.get("details") or {}).get("to") or "").partition("/")
                    if old_die and old_cfg and new_die:
                        for prior in ordered:
                            if (prior["die"].casefold(), prior["config"].casefold()) == (
                                    old_die.casefold(), old_cfg.casefold()):
                                prior["die"] = new_die
                    continue
                die, sep, config = str(event.get("entity_id") or "").partition("/")
                if not sep or not die or not config or event.get("action") not in ("motor.link", "motor.unlink"):
                    continue
                ordered.append({
                    "die": die, "config": config, "action": event.get("action"),
                    "time": event.get("time"), "actor": event.get("actor_email") or ""})
    except OSError:
        pass
    events: dict[tuple[str, str], list[dict]] = {}
    for item in ordered:
        events.setdefault((item["die"].casefold(), item["config"].casefold()), []).append(item)
    for key, history in events.items():
        if key in known:
            continue
        linked_at = linked_by = None
        for event in history:
            if event["action"] == "motor.link":
                linked_at, linked_by = event["time"], event["actor"]
            else:
                rows.append({"die": event["die"], "config": event["config"],
                             "linked_at": linked_at, "linked_by": linked_by,
                             "unlinked_at": event["time"], "unlinked_by": event["actor"]})
                linked_at = linked_by = None
        if linked_at or linked_by:
            rows.append({"die": history[-1]["die"], "config": history[-1]["config"],
                         "linked_at": linked_at, "linked_by": linked_by})
    # Stage 2 projects already kept a provenance ledger for project-added grants;
    # use its canonical identities as an unknown-time fallback when logs are absent.
    for key, item in (project.get("project_motor_grants") or {}).items():
        if isinstance(item, dict):
            grant = str(item.get("grant_key") or "")
            die, sep, config = grant.partition("/")
        else:
            die, sep, config = str(key).partition("\0")
        identity = (die.casefold(), config.casefold())
        if sep and die and config and identity not in known and not any(
                (str(row.get("die") or "").casefold(), str(row.get("config") or "").casefold()) == identity
                for row in rows):
            rows.append({"die": die, "config": config, "linked_at": None,
                         "linked_by": None, "unlinked_at": None, "unlinked_by": None})
    return rows


def list_projects() -> list[dict]:
    base = root()
    if not base.is_dir():
        return []
    result = []
    for child in sorted(base.iterdir()):
        if child.is_dir() and valid_id(child.name):
            project = _read(child / "project.json", None)
            if isinstance(project, dict):
                result.append(project)
    return result


def index_path() -> Path:
    return root() / "index.json"


def load_index() -> dict:
    value = _read(index_path(), {"motors": {}})
    return value if isinstance(value, dict) else {"motors": {}}


def save_index(data: dict) -> None:
    ensure_writable()
    json_store.atomic_write_json(index_path(), data)


def membership_index_path() -> Path:
    return root() / "memberships.json"


def load_membership_index() -> dict:
    value = _read(membership_index_path(), {})
    return value if isinstance(value, dict) else {}


def save_membership_index(data: dict) -> None:
    ensure_writable()
    json_store.atomic_write_json(membership_index_path(), data)


def membership_projects(email: str) -> list[str]:
    """Read the sign-in visibility index without opening project documents."""
    normalized = str(email or "").strip().lower()
    doc = load_membership_index()
    projects = doc.get(normalized, []) if isinstance(doc, dict) else []
    return sorted({p for p in projects if valid_id(p)}) if isinstance(projects, list) else []


def sync_membership_index(project_id: str, old_members: list, new_members: list) -> None:
    """Update only membership rows for one project; caller serializes project mutations."""
    ensure_writable()
    from motor_ai_sim import json_store
    old = {str(m.get("email") or "").strip().lower() for m in old_members if isinstance(m, dict)}
    new = {str(m.get("email") or "").strip().lower() for m in new_members if isinstance(m, dict)}
    old.discard(""); new.discard("")
    def mutate(doc):
        if not isinstance(doc, dict):
            doc.clear()
        for email in old | new:
            ids = set(doc.get(email, [])) if isinstance(doc.get(email), list) else set()
            if email in new:
                ids.add(project_id)
            else:
                ids.discard(project_id)
            if ids:
                doc[email] = sorted(ids)
            else:
                doc.pop(email, None)
    json_store.mutate_json(membership_index_path(), mutate, default={})


def rebuild_membership_index() -> dict:
    """Explicit one-time migration helper; never called on sign-in reads."""
    with project_transaction():
        rebuilt = {}
        for project in list_projects():
            for member in project.get("members", []):
                email = str(member.get("email") or "").strip().lower() if isinstance(member, dict) else ""
                if email:
                    rebuilt.setdefault(email, []).append(project["id"])
        result = {email: sorted(set(ids)) for email, ids in rebuilt.items()}
        json_store.atomic_write_json(membership_index_path(), result)
        return result
