"""File-system storage for one production: ``<shared>/production/<die>/<config>/``.

Every JSON file is written through ``motor_ai_sim.json_store``; the audit log is
append-only JSONL.  Contract: docs/PRODUCTION_MODULE_DESIGN.md section 3.
"""
from __future__ import annotations

import contextlib
import contextvars
import copy
import json
import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional

from motor_ai_sim import json_store
from motor_ai_sim.api_errors import ApiError

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
    "checklist_status.json": {"items": {}},   # checklist.py: item key -> status record
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
    if name in _GUARDED:
        _check_change(die, config, name, _GUARDED[name](_read_json(p, default=_DEFAULTS.get(name, {}))), _GUARDED[name](data))
    json_store.atomic_write_json(p, data, indent=2)


def mutate_file(die: str, config: str, name: str, mutator: Any) -> Any:
    p = _data_path(die, config, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if name in _GUARDED:
        mutator = _guarded_mutator(die, config, name, mutator)
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
    check_file_overwrite(die, config, file_id)
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
    check_file_overwrite(die, config, file_id)
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


# ── released files: no replacement without a revision (owner 2026-11-15) ────────────────────────────────────────────────────────
#
# Incident: a shaft drawing (v1.1/v1.2, no M5 thread) was replaced by v1.3 under the same name by a bulk update with NO published
# revision and NO notice; the factory built 100 shafts without the thread.  From now on a drawing / STEP file of a part that is in a
# published revision cannot be replaced, renamed, removed or archived, and its bytes cannot be overwritten, by ANY caller of this
# module (routes, staff bulk tools, import scripts) - except inside the publish of a new revision that supersedes it
# (``revision_scope``, opened by ``routes._publish_revision``, which also needs at least one change line).  The old file is archived as
# "superseded by ...", kept, and a download of it warns first.  Writing bytes straight into the files folder bypasses this guard: do not.

#: file types protected when they belong to a part (documents); a part's own drawing_files / optional_step are always protected
PROTECTED_EXT = frozenset("pdf step stp iges igs stl dxf dwg x_t x_b sat 3mf f3d fcstd sldprt sldasm slddrw ipt iam prt asm catpart "
                          "catproduct dwf".split())
_SCOPE: contextvars.ContextVar = contextvars.ContextVar("production_revision_scope", default=None)


class ReleasedFileError(ApiError, ValueError):
    """HTTP 409 ``production.released_file``: names the file, the revision that released it and what was attempted."""

    def __init__(self, name: str, revision: str, action: str):
        msg = ("'%s' belongs to published revision %s and cannot be %s without a new revision. Upload the new file with its version in the "
               "name (for example %s) and publish a revision that supersedes it, with at least one change line."
               % (name, revision, action, "name_v1.4.pdf"))
        ApiError.__init__(self, 409, "production.released_file", msg, {"file": name, "revision": revision, "action": action})
        self.file, self.revision, self.action = name, revision, action


@contextlib.contextmanager
def revision_scope(die: str, config: str):
    """Inside the publish of a revision: replaced files may be archived (marked superseded) - nothing else is loosened."""
    token = _SCOPE.set((die, config))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _in_scope(die: str, config: str) -> bool:
    return _SCOPE.get() == (die, config)


def _ext(name: Any) -> str:
    return PurePosixPath(str(name or "").replace(chr(92), "/")).suffix.lstrip(".").lower()


def _doc_protected(d: dict) -> bool:
    return bool(isinstance(d, dict) and d.get("file_id") and d.get("part_id") and not d.get("internal") and _ext(d.get("name")) in PROTECTED_EXT)


def _part_file_ids(part: dict) -> list:
    out = [str(x.get("file_id")) for x in (part.get("drawing_files") or []) if isinstance(x, dict) and x.get("file_id")]
    step = part.get("optional_step")
    if isinstance(step, dict) and step.get("file_id"):
        out.append(str(step["file_id"]))
    return out


def current_protected_file_ids(die: str, config: str, *, uploaded_by: Optional[str] = None) -> set:
    """File ids that are protected NOW: the drawings / STEP of the parts and the part documents of a protected type; ``uploaded_by``
    (an ISO time) limits the documents to those uploaded up to then (used for revisions that predate the snapshot)."""
    out = set()
    for d in (load_file(die, config, "documents.json").get("documents") or []):
        if _doc_protected(d) and (uploaded_by is None or str(d.get("uploaded_at") or "") <= uploaded_by):
            out.add(str(d["file_id"]))
    for p in (load_file(die, config, "parts.json").get("parts") or []):
        if isinstance(p, dict):
            out.update(_part_file_ids(p))
    return out


def released_files(die: str, config: str) -> Dict[str, str]:
    """``{file_id: first revision number that released it}`` over every published revision.  A revision with a snapshot
    (``files.released_files``) releases exactly that; an older one releases what existed at its date."""
    revs = [r for r in (load_file(die, config, "revisions.json").get("revisions") or []) if isinstance(r, dict)]
    out: Dict[str, str] = {}
    for r in revs:
        num = str(r.get("number") or "")
        snap = (r.get("files") or {}).get("released_files")
        ids = snap if isinstance(snap, list) else current_protected_file_ids(die, config, uploaded_by=str(r.get("date") or "") or None)
        for fid in ids:
            out.setdefault(str(fid), num)
    return out


def _doc_view(data: Any) -> list:
    return [d for d in (data.get("documents") if isinstance(data, dict) else None) or [] if isinstance(d, dict)]


def _part_view(data: Any) -> list:
    return [p for p in (data.get("parts") if isinstance(data, dict) else None) or [] if isinstance(p, dict)]


_GUARDED = {"documents.json": _doc_view, "parts.json": _part_view}


def _check_change(die: str, config: str, name: str, before: list, after: list) -> None:
    rel = released_files(die, config)
    if not rel:
        return
    if name == "documents.json":
        now = {d.get("id"): d for d in after}
        for d in before:
            rev = rel.get(str(d.get("file_id") or ""))
            if rev is None or not _doc_protected(d):
                continue
            n = now.get(d.get("id"))
            if n is None:
                raise ReleasedFileError(str(d.get("name")), rev, "removed")
            if n.get("file_id") != d.get("file_id"):
                raise ReleasedFileError(str(d.get("name")), rev, "replaced")
            if n.get("name") != d.get("name"):
                raise ReleasedFileError(str(d.get("name")), rev, "renamed")
            archived = (n.get("status") == "superseded" and d.get("status") != "superseded") or (n.get("archived_at") and not d.get("archived_at"))
            if archived and not _in_scope(die, config):
                raise ReleasedFileError(str(d.get("name")), rev, "archived")
    else:
        now = {p.get("id"): p for p in after}
        for p in before:
            mine = [f for f in _part_file_ids(p) if f in rel]
            if not mine:
                continue
            label = "a drawing or STEP file of part '%s'" % (((p.get("name") or {}).get("en") if isinstance(p.get("name"), dict) else p.get("name")) or p.get("id"))
            n = now.get(p.get("id"))
            if n is None:
                raise ReleasedFileError(label, rel[mine[0]], "removed")
            if not _in_scope(die, config):
                missing = [f for f in mine if f not in _part_file_ids(n)]
                if missing:
                    raise ReleasedFileError(label, rel[missing[0]], "replaced or removed")


def _guarded_mutator(die: str, config: str, name: str, mutator: Any):
    view = _GUARDED[name]

    def wrapped(d):
        before = copy.deepcopy(view(d))
        out = mutator(d)
        _check_change(die, config, name, before, view(out if out is not None else d))
        return out
    return wrapped


def check_file_overwrite(die: str, config: str, file_id: str) -> None:
    """Refuse to write bytes into the file of a released drawing / STEP (a new file always gets a new id)."""
    if not valid_file_id(file_id) or not file_dir(die, config).joinpath(file_id).exists():
        return
    rev = released_files(die, config).get(file_id)
    if rev is None:
        return
    name = next((str(d.get("name")) for d in load_file(die, config, "documents.json").get("documents") or []
                 if isinstance(d, dict) and d.get("file_id") == file_id), file_id)
    raise ReleasedFileError(name, rev, "overwritten")
