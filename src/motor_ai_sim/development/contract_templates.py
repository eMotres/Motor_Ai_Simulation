"""The standard contract documents of EVERY project (owner 2026-11-22: «универсальные договора должны появиться во всех проектах»):
the customer agreement body (the template) and, later, the Annex A / Annex B blanks.

* stored in the server's SHARED store, like the staff scheme and the object registry - never in this public repository:
  ``<shared root>/contract/templates/`` (override ``MOTRES_CONTRACT_TEMPLATES_ROOT``; the private data checkout as the
  development fallback), the same place on every server;
* every file is an IMMUTABLE blob named by its SHA-256 (``files/<sha256>.<ext>``); ``index.json`` lists every version of each
  kind, append-only: a version label is used once per kind, forever, and nothing is deleted.  The newest version is current,
  and one upload changes what every project's Contract section shows - no per-project upload;
* FAIL CLOSED: a damaged index, or a missing index while blobs exist, is a damaged store - no template is shown or sent and no
  upload is accepted until it is repaired (a version label of a sent revision can never be reused);
* a sent project revision freezes the template's kind, version, blob and SHA-256; a download or a send reads the bytes of
  exactly that blob and checks the hash - a missing or changed file refuses, it is never replaced by another one.

``GET /api/contract-templates`` lists the versions, ``POST /api/contract-templates`` uploads one (multipart ``file``, ``kind``,
``version``).  Admin only; the admin audit records it.  The project-side read routes live in ``project_contract``.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from motor_ai_sim import json_store
from motor_ai_sim.development.file_structure import content_matches
from motor_ai_sim.development.project_uploads import bounded_form

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/contract-templates", tags=["contract-templates"])

KINDS = ("agreement", "annex_a", "annex_b")
ALLOWED_EXT = (".pdf", ".docx")
MAX_BYTES = 30 * 1024 * 1024
_VERSION_MAX = 40
_CHUNK = 1024 * 1024
_BLOB = re.compile(r"[0-9a-f]{64}\.(pdf|docx)")


class TemplateStoreDamaged(Exception):
    """The template store exists but cannot be trusted (index unreadable, wrong shape, or gone while blobs remain)."""


def root() -> Optional[Path]:
    """Where the templates live: the override, else the shared store, else the private data checkout; None when none exists."""
    env = (os.environ.get("MOTRES_CONTRACT_TEMPLATES_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    # the shared store only when SHARED_ROOT names one (both production servers set it); shared_root() alone falls back to
    # the process config directory, which is not a store - then the private checkout (development) is used
    if (os.environ.get("SHARED_ROOT") or "").strip():      # WORKSPACES_ROOT alone does not name a shared store
        try:
            from motor_ai_sim.workspace import shared_root
            return Path(str(shared_root())) / "contract" / "templates"
        except Exception:                                   # noqa: BLE001 - no workspace layer in this process
            pass
    from motor_ai_sim.private_data import private_root
    base = private_root()
    return base / "config" / "contract" / "templates" if base is not None else None


def _index_path(base: Path) -> Path:
    return base / "index.json"


def lock():
    """The template store's lock (the index file's): publication and every send that freezes a template take it."""
    base = root()
    return json_store.lock_for(_index_path(base)) if base is not None else _NoLock()


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _validate(doc: Any, base: Path) -> dict:
    """The whole index, strictly: known kinds, every row of its own kind with a non-empty version used once, a blob named by its
    hash - and every blob on disk referenced (a blob the index does not list means the index lost rows)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("versions"), dict):
        raise TemplateStoreDamaged("index.json has the wrong shape")
    referenced = set()
    for kind, rows in doc["versions"].items():
        if kind not in KINDS or not isinstance(rows, list):
            raise TemplateStoreDamaged(f"index.json: unknown kind {kind!r}")
        seen = set()
        for r in rows:
            if not isinstance(r, dict) or r.get("kind") != kind or not isinstance(r.get("version"), str) or not r["version"].strip() \
                    or not _BLOB.fullmatch(str(r.get("blob") or "")) or str(r.get("blob", "")).split(".")[0] != r.get("sha256"):
                raise TemplateStoreDamaged(f"index.json: a malformed {kind} entry")
            if r["version"] in seen:
                raise TemplateStoreDamaged(f"index.json: {kind} {r['version']} listed twice")
            seen.add(r["version"])
            referenced.add(r["blob"])
    reserved = doc.get("reserved", [])
    if not isinstance(reserved, list) or any(not isinstance(r, dict) or r.get("kind") not in KINDS or not str(r.get("version") or "").strip()
                                             for r in reserved):
        raise TemplateStoreDamaged("index.json: malformed reserved labels")
    files = base / "files"
    on_disk = {f.name for f in files.iterdir() if _BLOB.fullmatch(f.name)} if files.is_dir() else set()
    if on_disk - referenced:
        raise TemplateStoreDamaged("index.json does not list %d stored template file(s)" % len(on_disk - referenced))
    return doc


def load() -> dict:
    """The index.  None-configured or a brand-new store: empty.  Damaged: :class:`TemplateStoreDamaged`."""
    base = root()
    if base is None:
        return {"versions": {}}
    path = _index_path(base)
    if not path.exists():
        files = base / "files"
        if files.is_dir() and any(_BLOB.fullmatch(f.name) for f in files.iterdir()):
            raise TemplateStoreDamaged("index.json is missing while template files exist")
        return {"versions": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise TemplateStoreDamaged(f"index.json unreadable: {exc}") from exc
    return _validate(doc, base)


def _labels_path(base: Path) -> Path:
    return base / "labels.jsonl"


def used_labels(base: Path) -> set:
    """Every (kind, version) ever published - append-only, kept even if the index is lost, so a label is never reused."""
    out = set()
    try:
        raw = _labels_path(base).read_bytes()
    except FileNotFoundError:
        return out
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.add((str(rec.get("kind")), str(rec.get("version"))))
    return out


def _append_label(base: Path, row: dict) -> None:
    """Append-only, one record per line: a line an earlier append left cut is ended first, so this record never joins it."""
    path = _labels_path(base)
    try:
        with open(path, "rb") as tail:
            tail.seek(-1, 2)
            if tail.read(1) != b"\n":
                with open(path, "ab") as fh:
                    fh.write(b"\n")
    except OSError:                                         # missing or empty: nothing to end
        pass
    with open(path, "ab") as fh:
        fh.write((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())


def versions(kind: str) -> list:
    return _versions(load(), kind)


def _versions(doc: dict, kind: str) -> list:
    rows = (doc.get("versions") or {}).get(kind) or []
    return sorted(rows, key=lambda r: str(r.get("uploaded_at") or ""), reverse=True)


def current(kind: str) -> Optional[dict]:
    rows = versions(kind)
    return rows[0] if rows else None


def find(kind: str, version: str) -> Optional[dict]:
    return next((r for r in versions(kind) if str(r.get("version")) == str(version)), None)


def blob_path(ref: dict) -> Optional[Path]:
    """The file of a template reference (an index entry or a frozen ``ref``); None for a malformed reference."""
    base = root()
    blob = str(ref.get("blob") or "")
    if base is None or not _BLOB.fullmatch(blob):
        return None
    return base / "files" / blob


def verified_bytes(ref: dict) -> bytes:
    """The bytes of exactly this reference, checked against its SHA-256.  Raises ``LookupError`` when the file is missing or
    different - never another file instead."""
    path = blob_path(ref)
    try:
        data = path.read_bytes() if path is not None else None
    except OSError:
        data = None
    if data is None or hashlib.sha256(data).hexdigest() != str(ref.get("sha256") or ""):
        raise LookupError("the standard document %s %s is missing or changed" % (ref.get("kind"), ref.get("version")))
    return data


def public(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {k: row.get(k) for k in ("kind", "version", "name", "size", "sha256", "uploaded_at")}


def ref(row: dict) -> dict:
    """What a sent project revision freezes about the template it was made from: kind, version, blob and hash."""
    return {"kind": row.get("kind"), "template": True, "version": row.get("version"), "name": row.get("name"),
            "blob": row.get("blob"), "sha256": row.get("sha256"), "id": f"template:{row.get('kind')}:{row.get('version')}"}


def snapshot() -> dict:
    """ONE read of the catalogue: ``{kind: current row | None, "unavailable": bool}`` - what a page shows and what its digest
    and a send are about come from the same read.  Nothing (and ``unavailable``) while the store is damaged."""
    try:
        doc = load()
    except TemplateStoreDamaged as exc:
        log.error("contract templates: store damaged (%s) - no template is shown", exc)
        return {**{k: None for k in KINDS}, "unavailable": True}
    return {**{k: (_versions(doc, k) or [None])[0] for k in KINDS}, "unavailable": False}


def summary(snap: Optional[dict] = None) -> dict:
    """The public form of :func:`snapshot` for a project's section."""
    snap = snapshot() if snap is None else snap
    return {**{k: public(snap.get(k)) for k in KINDS}, "unavailable": bool(snap.get("unavailable"))}


def _admin(request: Request) -> dict:
    from motor_ai_sim.development import access
    user = access.require_signed_in(request)
    if str(user.get("role") or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="Only MOTRES admins manage the standard contract documents")
    return user


def _damaged(exc: Exception) -> HTTPException:
    log.error("contract templates: %s", exc)
    return HTTPException(status_code=503, detail="The standard contract documents need repair on this server; nothing was changed")


@router.get("")
def list_templates(request: Request):
    _admin(request)
    try:
        rows = {k: [public(r) for r in versions(k)] for k in KINDS}
    except TemplateStoreDamaged as exc:
        raise _damaged(exc)
    return {"configured": root() is not None, "kinds": list(KINDS), "versions": rows}


@router.post("")
async def upload_template(request: Request):
    """One file: ``kind`` (agreement | annex_a | annex_b), ``version`` (a label, used once per kind forever), ``file`` (PDF or
    DOCX).  Stored as an immutable blob named by its SHA-256, then appended to the index."""
    user = _admin(request)
    base = root()
    if base is None:
        raise HTTPException(status_code=503, detail="This server has no store for the standard contract documents")
    try:
        load()
    except TemplateStoreDamaged as exc:
        raise _damaged(exc)
    form = await bounded_form(request, MAX_BYTES)
    try:
        kind = str(form.get("kind") or "").strip()
        if kind not in KINDS:
            raise HTTPException(status_code=422, detail="kind must be one of: " + ", ".join(KINDS))
        version = " ".join(str(form.get("version") or "").split())
        if not version or len(version) > _VERSION_MAX:
            raise HTTPException(status_code=422, detail="version is required (at most %d characters)" % _VERSION_MAX)
        uploaded = form.get("file")
        if uploaded is None or isinstance(uploaded, str):
            raise HTTPException(status_code=400, detail="No file uploaded")
        name = str(uploaded.filename or "template.pdf")[:200]
        ext = Path(name.lower()).suffix
        if ext not in ALLOWED_EXT:
            raise HTTPException(status_code=422, detail="Allowed files: " + ", ".join(ALLOWED_EXT))

        def chunks() -> Iterator[bytes]:
            while True:
                block = uploaded.file.read(_CHUNK)
                if not block:
                    return
                yield block

        try:
            tmp, size, sha256 = await run_in_threadpool(_save_tmp, base, chunks())
        except OSError as exc:
            log.error("contract templates: not writable (%s)", exc)
            raise HTTPException(status_code=503, detail="The standard contract documents cannot be written on this server")
        try:
            ok = await run_in_threadpool(content_matches, tmp, name)
        except Exception:                                   # noqa: BLE001 - any validation failure is invalid content
            ok = False
        if not ok:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="The file is not a readable %s" % ext.lstrip(".").upper())
        blob = f"{sha256}{ext}"
        row = {"kind": kind, "version": version, "name": name, "blob": blob, "size": size, "sha256": sha256,
               "uploaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "uploaded_by": str(user.get("email") or "")}
        clash: dict = {}
        try:
            with lock():
                doc = load()                                # read-latest under the lock; a damaged store raises
                if any(r.get("version") == version for r in (doc["versions"].get(kind) or [])) or (kind, version) in used_labels(base) \
                        or any(r.get("kind") == kind and r.get("version") == version for r in doc.get("reserved") or []):
                    clash["hit"] = True
                else:
                    if not _index_path(base).exists():      # first initialisation: the index exists before any blob does
                        write_strict(_index_path(base), (json.dumps({"versions": {}, "initialized_at": row["uploaded_at"]}) + "\n").encode())
                    _append_label(base, row)                # the label is used from now on, whatever happens next
                    dest = base / "files" / blob
                    created = not dest.exists()
                    if created:
                        os.replace(tmp, dest)
                    else:
                        tmp.unlink(missing_ok=True)         # the same bytes are one blob
                    doc["versions"].setdefault(kind, []).append(row)
                    try:
                        write_strict(_index_path(base), (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                    except OSError:
                        if created:                         # no index entry, no new blob: a first upload never leaves a "damaged" store
                            dest.unlink(missing_ok=True)
                        raise
        except TemplateStoreDamaged as exc:
            tmp.unlink(missing_ok=True)
            raise _damaged(exc)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            log.error("contract templates: index not written (%s)", exc)
            raise HTTPException(status_code=503, detail="The standard contract documents cannot be written on this server; nothing was changed")
        if clash:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="%s %s exists already; a version label is never reused" % (kind, version))
        from motor_ai_sim import admin_audit as _AA
        _AA.record(str(user.get("email") or ""), "contract.template", f"{kind} {version}", details={"sha256": sha256, "size": size})
        return {"ok": True, **public(row)}
    finally:
        await form.close()


def write_strict(path: Path, data: bytes) -> None:
    """Atomic or nothing: an exclusive temp file beside ``path``, flushed and synced, then ``os.replace``; no in-place fallback.
    Any failure removes the temp file and raises ``OSError``."""
    tmp = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(8)))
    try:
        with open(tmp, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _save_tmp(base: Path, chunks: Iterator[bytes]) -> tuple[Path, int, str]:
    directory = base / "files"
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (".upload-%s.tmp" % secrets.token_hex(8))
    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "xb") as fh:
            for chunk in chunks:
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="The file is larger than %d MB" % (MAX_BYTES // (1024 * 1024)))
                digest.update(chunk)
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        if size == 0:
            raise HTTPException(status_code=400, detail="Empty file")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, size, digest.hexdigest()


def _loose_labels(path: Path) -> set:
    """(kind, version) pairs a possibly damaged index still names (best effort; never raises)."""
    out: set = set()
    try:
        doc = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return out
    if isinstance(doc, dict):
        for kind, rows in (doc.get("versions") or {}).items() if isinstance(doc.get("versions"), dict) else []:
            for r in rows if isinstance(rows, list) else []:
                if isinstance(r, dict) and kind in KINDS and str(r.get("version") or "").strip():
                    out.add((kind, str(r["version"])))
        for r in doc.get("reserved") or [] if isinstance(doc.get("reserved"), list) else []:
            if isinstance(r, dict) and r.get("kind") in KINDS and str(r.get("version") or "").strip():
                out.add((r["kind"], str(r["version"])))
    return out


def _loose_rows(path: Path) -> list:
    try:
        doc = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    out = []
    for kind, rows in (doc.get("versions") or {}).items() if isinstance(doc, dict) and isinstance(doc.get("versions"), dict) else []:
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and kind in KINDS and r.get("kind") == kind and str(r.get("version") or "").strip():
                out.append(r)
    return out


@router.post("/repair")
def repair_templates(request: Request):
    """Admin repair of a damaged store, without manual edits.

    The index is rebuilt from the append-only publication log (``labels.jsonl``) and whatever rows the damaged index still
    holds: a row is kept when its blob exists and matches its hash (the first of each kind + version).  No label is ever
    forgotten: every label the log, the current or the damaged index knows and that cannot be kept as a row is RESERVED in the
    new index (never reusable).  A stored file that fails its hash or that no kept row references is moved to
    ``files/quarantine/`` (kept, never served).  The damaged index is kept as ``index.damaged-<time>-<random>.json``."""
    user = _admin(request)
    base = root()
    if base is None:
        raise HTTPException(status_code=503, detail="This server has no store for the standard contract documents")
    kept, reserved_n, quarantined = 0, 0, 0
    with lock():
        index = _index_path(base)
        candidates = []
        try:
            raw = _labels_path(base).read_bytes()
        except FileNotFoundError:
            raw = b""
        for line in raw.decode("utf-8", errors="replace").split("\n"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("kind") in KINDS and str(rec.get("version") or "").strip():
                candidates.append(rec)
        candidates += _loose_rows(index)
        known = used_labels(base) | _loose_labels(index)
        rows: dict = {}
        have = set()
        for rec in candidates:
            label = (rec["kind"], str(rec["version"]))
            if label in have:
                continue
            try:
                verified_bytes(rec)
            except LookupError:
                continue
            rows.setdefault(rec["kind"], []).append(rec)
            have.add(label)
            kept += 1
        reserved = [{"kind": k, "version": v, "reason": "no verified file at repair"} for k, v in sorted(known - have)]
        reserved_n = len(reserved)
        referenced = {r["blob"] for rs in rows.values() for r in rs}
        files = base / "files"
        if files.is_dir():
            for f in sorted(files.iterdir()):
                if _BLOB.fullmatch(f.name) and f.name not in referenced:
                    q = files / "quarantine"
                    q.mkdir(exist_ok=True)
                    dest = q / f.name
                    if dest.exists():
                        dest = q / ("%s.%s" % (f.name, secrets.token_hex(4)))
                    f.replace(dest)
                    quarantined += 1
        if index.exists():
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            with open(base / ("index.damaged-%s-%s.json" % (stamp, secrets.token_hex(4))), "xb") as fh:
                fh.write(index.read_bytes())
        doc = {"versions": rows, "reserved": reserved, "repaired_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            _validate(doc, base)
        except TemplateStoreDamaged as exc:
            raise HTTPException(status_code=409, detail="Cannot rebuild the index: %s" % exc)
        write_strict(index, (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    from motor_ai_sim import admin_audit as _AA
    _AA.record(str(user.get("email") or ""), "contract.template", "repair",
               details={"kept": kept, "reserved": reserved_n, "quarantined": quarantined})
    return {"ok": True, "kept": kept, "reserved": reserved_n, "quarantined": quarantined}
