"""File-system storage for one production: ``<shared>/production/<die>/<config>/``.

Every JSON file is written through ``motor_ai_sim.json_store``; the audit log is
append-only JSONL.  Contract: docs/PRODUCTION_MODULE_DESIGN.md section 3.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from motor_ai_sim import json_store

log = logging.getLogger(__name__)


def production_root() -> Path:
    env = (os.environ.get("MOTRES_PRODUCTION_ROOT") or "").strip()
    if env:
        return Path(env)
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "production"


def _safe_slug(name: str) -> str:
    """Die/config names may contain spaces and slashes; keep them readable but
    safe for a directory name."""
    s = re.sub(r"[/\\]+", "_", name)
    s = re.sub(r"[\s]+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_.\-]", "_", s)
    s = re.sub(r"\.{2,}", "_", s)                 # no "..": a name can never climb out of the production root
    s = s.strip("_")
    return "_" if s in ("", ".") else s


def path_for(die: str, config: str) -> Path:
    root = production_root().resolve()
    p = (root / _safe_slug(die) / _safe_slug(config)).resolve()
    if root != p and root not in p.parents:
        raise ValueError("production path outside the production root")
    return p


def file_dir(die: str, config: str) -> Path:
    p = path_for(die, config) / "files"
    p.mkdir(parents=True, exist_ok=True)
    return p


def audit_path(die: str, config: str) -> Path:
    return path_for(die, config) / "audit.jsonl"


def _data_path(die: str, config: str, name: str) -> Path:
    return path_for(die, config) / name


_DEFAULTS: dict[str, Any] = {
    "production.json": {},
    "parts.json": {"parts": []},
    "bom.json": {"rows": []},
    "costs.json": {"rows": [], "extras": [], "margin_pct": 0.0},
    "procurement.json": {"rows": []},
    "schedule.json": {"phases": [], "milestones": [], "baseline": {}},
    "assembly_process.json": {"steps": []},
    "process.json": {"sections": []},
    "documents.json": {"documents": []},
    "threads.json": {"threads": []},
    "proposals.json": {"proposals": []},
    "revisions.json": {"revisions": []},
}


def _read_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    return json_store.read_json(path, default=default)


def load_file(die: str, config: str, name: str) -> Any:
    p = _data_path(die, config, name)
    return _read_json(p, default=_DEFAULTS.get(name, {}))


def save_file(die: str, config: str, name: str, data: Any) -> None:
    p = _data_path(die, config, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    json_store.atomic_write_json(p, data, indent=2)


def mutate_file(die: str, config: str, name: str, mutator: Any) -> Any:
    p = _data_path(die, config, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return json_store.mutate_json(p, mutator)


def append_audit(die: str, config: str, entry: dict) -> None:
    p = audit_path(die, config)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    # Atomic append through the file lock used by json_store
    lk = json_store.lock_for(p)
    with lk:
        try:
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as e:
            log.warning("production audit append failed: %s", e)
            raise


def save_uploaded_file(die: str, config: str, file_id: str, data: bytes) -> Path:
    d = file_dir(die, config)
    dest = d / file_id
    try:
        dest.write_bytes(data)
    except Exception as e:                                  # noqa: BLE001
        log.warning("production file write failed: %s", e)
        raise
    from motor_ai_sim.private_files import chmod_private
    chmod_private(dest)
    return dest


def save_uploaded_stream(die: str, config: str, file_id: str, chunks, limit: int) -> int:
    """Write ``chunks`` (an iterator of bytes) to the production's files without holding the file in memory; returns its size.

    A file over ``limit`` bytes, or an empty one, is removed again and ``ValueError`` is raised ("too_large" / "empty"), so a
    refused upload never leaves bytes behind."""
    if not valid_file_id(file_id):
        raise ValueError("invalid file id")
    dest = file_dir(die, config) / file_id
    size = 0
    try:
        with dest.open("wb") as stream:
            for chunk in chunks:
                size += len(chunk)
                if size > limit:
                    raise ValueError("too_large")
                stream.write(chunk)
        if size == 0:
            raise ValueError("empty")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    from motor_ai_sim.private_files import chmod_private
    chmod_private(dest)
    return size


_FILE_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{8,64}$")


def valid_file_id(file_id: str) -> bool:
    """File ids are server-generated (secrets.token_urlsafe): anything else - '..', separators - is refused."""
    return bool(_FILE_ID_RE.match(str(file_id or "")))


def read_uploaded_file(die: str, config: str, file_id: str) -> Optional[Path]:
    if not valid_file_id(file_id):
        return None
    d = file_dir(die, config)
    candidate = d / file_id
    if candidate.is_file():
        return candidate
    return None


def production_exists(die: str, config: str) -> bool:
    p = path_for(die, config) / "production.json"
    return p.is_file()
