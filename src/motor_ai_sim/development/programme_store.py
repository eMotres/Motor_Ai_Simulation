"""Revisioned programme storage inside the Development project store.

Layout, under ``<projects root>/_programmes/<programme id>/``::

    revisions/r000001-<sha16>.json   immutable, one per committed revision
    manifest.json                    head + full revision history + idempotency keys
    orphans/                         unpublished files moved aside by recovery

``_programmes`` is not a valid project id, so ``store.list_projects`` and every
legacy route never see it: programme records are absent from them, not filtered.

Publication is manifest-last. A commit writes its revision file first and the
manifest last, both with ``json_store.atomic_write_json``; readers resolve only
what the manifest names and verify its sha256. A crash between the two leaves an
orphan revision file that no reader can reach; the next commit or ``recover``
moves it to ``orphans/``. Nothing here deletes a file.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from motor_ai_sim import json_store
from motor_ai_sim.development import programme, store

NAMESPACE = "_programmes"
MANIFEST_VERSION = 1
_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_REVISION_FILE = re.compile(r"^r(\d{6})-([0-9a-f]{16})\.json$")


class RevisionConflict(programme.ProgrammeError):
    def __init__(self, current: int, expected: Any):
        super().__init__(f"programme is at revision {current}, request expected {expected}")
        self.current = current


class IdempotencyConflict(programme.ProgrammeError):
    """The idempotency key was already used for a different request."""


class StoreCorrupt(programme.ProgrammeError):
    """A file named by the manifest is missing or fails its hash check."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def namespace_root() -> Path:
    return store.root() / NAMESPACE


def programme_dir(programme_id: str) -> Path:
    if not programme.valid_programme_id(programme_id):
        raise ValueError("invalid programme id")
    base = namespace_root().resolve()
    path = (base / programme_id).resolve()
    if base not in path.parents:
        raise ValueError("programme path outside root")
    return path


def _manifest_path(programme_id: str) -> Path:
    return programme_dir(programme_id) / "manifest.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_hash(payload: Any) -> str:
    return _sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _read_manifest(programme_id: str) -> dict | None:
    path = _manifest_path(programme_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise StoreCorrupt(f"manifest for {programme_id} is unreadable") from exc
    if (not isinstance(value, dict) or value.get("manifest_version") != MANIFEST_VERSION
            or value.get("programme_id") != programme_id or not isinstance(value.get("revisions"), list)):
        raise StoreCorrupt(f"manifest for {programme_id} is not a programme manifest")
    return value


def _read_revision(programme_id: str, entry: dict) -> dict:
    path = programme_dir(programme_id) / "revisions" / str(entry.get("file") or "")
    if not _REVISION_FILE.fullmatch(path.name) or not path.is_file():
        raise StoreCorrupt(f"revision {entry.get('revision')} of {programme_id} is missing")
    data = path.read_bytes()
    if _sha256(data) != entry.get("sha256"):
        raise StoreCorrupt(f"revision {entry.get('revision')} of {programme_id} fails its hash check")
    return json.loads(data)


def read(programme_id: str, revision: int | None = None) -> dict | None:
    """The committed revision envelope (head by default), or ``None``. Never writes."""
    manifest = _read_manifest(programme_id)
    if manifest is None:
        return None
    entries = manifest["revisions"]
    if revision is None:
        entry = entries[-1] if entries else None
    else:
        entry = next((e for e in entries if e.get("revision") == revision), None)
    return _read_revision(programme_id, entry) if entry else None


def history(programme_id: str) -> list[dict]:
    manifest = _read_manifest(programme_id)
    return [dict(entry) for entry in manifest["revisions"]] if manifest else []


def list_programmes() -> list[dict]:
    base = namespace_root()
    if not base.is_dir():
        return []
    rows = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or not programme.valid_programme_id(child.name):
            continue
        try:
            manifest = _read_manifest(child.name)
        except StoreCorrupt:
            rows.append({"id": child.name, "revision": None, "corrupt": True})
            continue
        if manifest and manifest["revisions"]:
            head = manifest["revisions"][-1]
            rows.append({"id": child.name, "title": manifest.get("title", ""), "revision": head["revision"],
                         "committed_at": head["committed_at"], "committed_by": head["committed_by"]})
    return rows


def _orphans(programme_id: str, manifest: dict | None) -> list[Path]:
    directory = programme_dir(programme_id) / "revisions"
    if not directory.is_dir():
        return []
    published = {entry.get("file") for entry in (manifest or {}).get("revisions", [])}
    return sorted(p for p in directory.iterdir() if p.is_file() and p.name not in published)


def _quarantine(programme_id: str, paths: list[Path]) -> list[str]:
    target = programme_dir(programme_id) / "orphans"
    moved = []
    for path in paths:
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path.rename(target / f"{stamp}-{path.name}")
        moved.append(path.name)
    return moved


def commit(programme_id: str, payload: Any, *, expected_revision: int, actor: str,
           idempotency_key: str | None = None,
           catalog_lookup: Callable[[str, str], str | None] | None = None) -> dict:
    """Validate ``payload`` as the next revision and publish it manifest-last.

    ``expected_revision`` is the head the caller edited (0 to create). A replay of
    an idempotency key with the same payload returns the revision it committed;
    the same key with a different payload is a conflict.
    """
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise programme.ProgrammeInvalid("expected_revision must be a non-negative integer",
                                         [programme.issue("invalid_value", "expected_revision", "non-negative integer required")])
    if idempotency_key is not None and not (isinstance(idempotency_key, str) and _KEY.fullmatch(idempotency_key)):
        raise programme.ProgrammeInvalid("invalid idempotency key",
                                         [programme.issue("invalid_value", "idempotency_key", "8-128 characters of A-Z a-z 0-9 . _ : -")])
    fingerprint = request_hash({"programme": payload, "expected_revision": expected_revision})
    manifest_path = _manifest_path(programme_id)
    store.ensure_writable()
    with json_store.lock_for(manifest_path):
        manifest = _read_manifest(programme_id)
        entries = manifest["revisions"] if manifest else []
        if idempotency_key is not None:
            used = next((e for e in entries if e.get("idempotency_key") == idempotency_key), None)
            if used is not None:
                if used.get("request_sha256") != fingerprint:
                    raise IdempotencyConflict("idempotency key was already used for a different request")
                envelope = _read_revision(programme_id, used)
                return _result(envelope, used, replayed=True)
        head = entries[-1]["revision"] if entries else 0
        if expected_revision != head:
            raise RevisionConflict(head, expected_revision)
        previous = _read_revision(programme_id, entries[-1])["programme"] if entries else None
        document = programme.validated(payload, programme_id, previous, catalog_lookup)

        revision = head + 1
        envelope = {"programme_id": programme_id, "revision": revision, "parent_revision": head or None,
                    "committed_at": now(), "committed_by": str(actor or "").strip().lower(),
                    "idempotency_key": idempotency_key, "request_sha256": fingerprint,
                    "programme": document}
        directory = programme_dir(programme_id) / "revisions"
        directory.mkdir(parents=True, exist_ok=True)
        swept = _quarantine(programme_id, _orphans(programme_id, manifest))
        text = json.dumps(envelope, indent=2, ensure_ascii=False)
        name = f"r{revision:06d}-{_sha256(text.encode('utf-8'))[:16]}.json"
        path = directory / name
        json_store.atomic_write_json(path, envelope)
        digest = _sha256(path.read_bytes())
        entry = {"revision": revision, "file": name, "sha256": digest, "committed_at": envelope["committed_at"],
                 "committed_by": envelope["committed_by"], "idempotency_key": idempotency_key,
                 "request_sha256": fingerprint}
        json_store.atomic_write_json(manifest_path, {
            "manifest_version": MANIFEST_VERSION, "programme_id": programme_id, "title": document["title"],
            "revisions": entries + [entry]})
        result = _result(envelope, entry, replayed=False)
        result["quarantined"] = swept
        return result


def _result(envelope: dict, entry: dict, *, replayed: bool) -> dict:
    return {"programme_id": envelope["programme_id"], "revision": envelope["revision"], "sha256": entry["sha256"],
            "committed_at": envelope["committed_at"], "replayed": replayed, "programme": envelope["programme"]}


def recover(programme_id: str, *, apply: bool = False) -> dict:
    """Verify every published file and find unpublished ones; with ``apply`` move those to ``orphans/``.

    Recovery never rewrites the manifest and never deletes: a missing or corrupt
    published revision is reported for a person to resolve.
    """
    def inspect() -> dict:
        manifest = _read_manifest(programme_id)
        broken = []
        for entry in (manifest or {}).get("revisions", []):
            try:
                _read_revision(programme_id, entry)
            except StoreCorrupt as exc:
                broken.append({"revision": entry.get("revision"), "file": entry.get("file"), "message": str(exc)})
        orphans = _orphans(programme_id, manifest)
        return {"programme_id": programme_id, "head": manifest["revisions"][-1]["revision"] if manifest and manifest["revisions"] else 0,
                "corrupt": broken, "orphans": [p.name for p in orphans], "quarantined": [], "_paths": orphans}

    if not apply:
        report = inspect()
        report.pop("_paths")
        return report
    store.ensure_writable()
    with json_store.lock_for(_manifest_path(programme_id)):
        report = inspect()
        report["quarantined"] = _quarantine(programme_id, report.pop("_paths"))
        return report
