"""Documents of a development project (owner 2026-11-01): the full production package of a project - KiCad project archives,
Gerber / drill / pick-and-place zips, BOM, 3-D STEP models, PDF drawings, images.

``/api/projects/{project_id}/documents`` follows the Production module's documents (``production/routes.py``):

* upload      multipart, members with write rights (project owner / engineer, global engineer / admin); the file is streamed to
              ``<project>/documents/files/<file_id>``, never held in memory; up to ``models.DOCUMENT_MAX_BYTES`` (200 MB);
* list        every project member; a document marked ``internal`` is listed for MOTRES staff only, one marked ``price_restricted``
              ("Price - restricted", owner 2026-11-22) for staff, project owners and members with ``sees_prices`` (never an MCP agent),
              an archived one for those who may edit;
* download    every project member (same visibility); each download - and the archive - is written to the traced download log
              (``model3d.log_download``) and to the project audit;
* patch       ``internal`` / ``price_restricted`` / ``folder`` / ``revision`` (and ``archived: false`` to restore);
* delete      archives the document (hidden), the file and its audit rows stay;
* documents.zip  the files visible to the caller in one streamed ZIP.

Every change writes a project audit row (``document.*``); the owner's daily report lists them.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import os
import re
import secrets
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from motor_ai_sim import auth, users
from motor_ai_sim.development import access, models, store
from motor_ai_sim.development.project_uploads import flush_events, queue_event

log = logging.getLogger(__name__)
router = APIRouter(tags=["development-documents"])     # included by routes.router, which carries the /api/projects prefix

#: all files of one project together (archived ones stay on disk and count)
PROJECT_MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_BYTES = 1024 * 1024 * 1024
MAX_ZIP_FILES = 2000
_CHUNK = 1024 * 1024
_REVISION_MAX = 40
#: already compressed - stored, not deflated again
_STORED_EXT = {".zip", ".7z", ".gz", ".tgz", ".rar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".xlsx", ".docx", ".3mf"}


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _staff(user: dict, role: str | None) -> bool:
    """MOTRES staff: global admin / engineer (the rule of the project notes and stage).  A customer who creates a project is its
    ``owner``, so a project role never makes staff: internal documents stay hidden from the customer side."""
    return access.is_engineer(user)


def _can_edit(user: dict, role: str | None) -> bool:
    """Who may upload, relabel and archive: staff and the project's owner / engineer."""
    return _staff(user, role) or role in ("owner", "engineer")


def _prices(user: dict, project: dict, staff: bool | None = None) -> bool:
    """May this caller read price-restricted documents: the ONE central rule ``access.sees_prices`` (identity first: never an MCP agent
    or a factory account, always MOTRES staff).  ``staff`` is accepted for old callers and is not consulted."""
    return access.sees_prices(user, project)


def _public(row: dict, staff: bool) -> dict:
    out = {k: row.get(k) for k in ("id", "name", "folder", "revision", "internal", "size", "sha256", "uploaded_at", "archived",
                                    "archived_at")}
    out["price_restricted"] = bool(row.get("price_restricted"))
    email = str(row.get("uploader_email") or "")
    if staff:
        out["uploader"] = email
    else:
        rec = users.get_user(email) if email else None
        name = str((rec or {}).get("name") or "")
        out["uploader"] = "" if not name or "@" in name else name
    if not staff:
        out["internal"] = False          # the customer side never sees the flag of a row it can list (it lists none)
        out.pop("archived_at", None)
    return out


def _visible(rows: list[dict], staff: bool, include_archived: bool = False, prices: bool | None = None) -> list[dict]:
    """``prices``: the caller may read price-restricted rows (default: staff only, so a reader that does not know the project
    never shows one)."""
    prices = staff if prices is None else prices
    if staff:
        return [r for r in rows if (include_archived or not r.get("archived")) and (prices or not r.get("price_restricted"))]
    return [r for r in rows if not r.get("internal") and (prices or not r.get("price_restricted"))
            and (include_archived or not r.get("archived"))]


def _details(row: dict, extra: dict | None = None) -> dict:
    details = {"name": row.get("name"), "folder": row.get("folder"), "revision": row.get("revision"),
               "internal": bool(row.get("internal")), "price_restricted": bool(row.get("price_restricted")),
               "size": row.get("size")}
    details.update(extra or {})
    return details


def _audit(project_id: str, user: dict, action: str, row: dict, extra: dict | None = None) -> None:
    store.append_audit(project_id, _email(user), action, "document", str(row.get("id") or ""), _details(row, extra))


def _queue(doc: dict, user: dict, action: str, row: dict, extra: dict | None = None) -> None:
    """Record the audit row of a document change INSIDE the documents mutation: the change and its event are written together."""
    queue_event(doc, _email(user), action, "document", str(row.get("id") or ""), _details(row, extra))


def _flush(project_id: str) -> None:
    """Write the events committed with a change.  A failing append leaves them pending (the change stands, the next call writes
    them); it never undoes, blocks or fails the change - in particular it never stops the cover from being cleared."""
    try:
        flush_events(project_id, store.DOCUMENTS_JSON)
    except Exception as exc:                                # noqa: BLE001
        log.error("project %s: document audit rows left pending (%s); written by the next call", project_id, exc)


def clear_cover(project_id: str, user: dict, doc_id: str, reason: str) -> bool:
    """Take a document off the project cover (its audit row is committed with the change, written best-effort afterwards)."""
    removed = store.clear_cover(project_id, doc_id, audit=(_email(user), reason))
    store.flush_project_audit(project_id)
    return removed


def _context(request: Request, project_id: str, *, edit: bool = False):
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    user, project, role = access.require_project(request, project_id, edit=edit)
    return user, project, role, _staff(user, role)


def _find(project_id: str, doc_id: str, staff: bool, include_archived: bool = False, prices: bool | None = None) -> dict:
    for row in _visible(store.load_documents(project_id), staff, include_archived, prices):
        if row.get("id") == doc_id:
            return row
    raise HTTPException(status_code=404, detail="Document not found")


def _log_download(request: Request, user: dict, project_id: str, *, kind: str, file: str, source_file: str | None,
                  sha256: str, size: int) -> None:
    """The traced download log shared with the 3-D / STEP and production files; a download nobody can account for is refused."""
    from motor_ai_sim import model3d as M
    role = str(user.get("role") or "")
    ident = {**user, "id": _email(user) or str(user.get("id") or ""), "is_admin": role.lower() == "admin"}
    rec = users.get_user(ident["id"]) or {}
    basis = M.nda_basis(ident, rec)
    who = M.recipient_of(ident, rec, basis)
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        M.log_download(dict(download_id=M.new_download_id(now), user=ident["id"], email=who["email"], name=M.customer_name(ident, rec),
                            role=role, company=who["company"], nda=basis["type"] != "internal", nda_basis=basis,
                            die="", config="", motor_id="project/%s" % project_id, project=project_id, kind=kind, file=file,
                            source_file=source_file, sha256=sha256, bytes=size, ip=ip,
                            user_agent=request.headers.get("user-agent", "")[:300]))
    except OSError as exc:
        log.error("project documents: download log not writable (%s) - download refused", exc)
        raise HTTPException(status_code=503, detail="Download log unavailable; try again later.")


# ── list / upload / patch / archive ───────────────────────────────────────────

@router.get("/{project_id}/documents")
def list_documents(request: Request, project_id: str, archived: bool = False):
    user, project, role, staff = _context(request, project_id)
    can_edit = _can_edit(user, role)
    rows = _visible(store.load_documents(project_id), staff, include_archived=bool(archived and can_edit),
                    prices=_prices(user, project, staff))
    cover = (store.get_project(project_id) or {}).get("cover")
    cover = cover if isinstance(cover, dict) else {}
    return {"documents": [dict(_public(r, staff), is_cover=bool(cover) and r.get("id") == cover.get("document_id")) for r in rows],
            "folders": list(models.DOCUMENT_FOLDERS), "can_edit": can_edit, "is_staff": staff, "max_bytes": models.DOCUMENT_MAX_BYTES,
            "cover_version": cover.get("version"), "can_restrict": _can_restrict(user, project)}


def _can_restrict(user: dict, project: dict) -> bool:
    """Who may set ``price_restricted``: ``access.can_manage_prices`` (global admin / engineer and the project's owner; never a project
    engineer, an MCP agent or a factory account)."""
    return access.can_manage_prices(user, project)


def _flag(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


@router.post("/{project_id}/documents")
async def upload_document(request: Request, project_id: str):
    """One file per request (``file``), with optional ``folder`` (kicad | fabrication | bom | 3d | docs; guessed from the name when
    omitted), ``revision`` and ``internal``."""
    user, project, role, staff_flag = _context(request, project_id, edit=True)
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > models.DOCUMENT_MAX_BYTES + 2 * _CHUNK:
        raise HTTPException(status_code=413, detail="The file is larger than %d MB" % (models.DOCUMENT_MAX_BYTES // (1024 * 1024)))
    form = await request.form()
    uploaded = form.get("file")
    if uploaded is None or isinstance(uploaded, str):
        raise HTTPException(status_code=400, detail="No file uploaded")
    restricted = _flag(form.get("price_restricted"))
    if restricted and not _can_restrict(user, project):
        raise HTTPException(status_code=403, detail="Only the project owner or MOTRES staff restrict a document's price content")
    folder = form.get("folder")
    folder = str(folder).strip().lower() if folder else None
    if folder and folder not in models.DOCUMENT_FOLDERS:
        raise HTTPException(status_code=422, detail="folder must be one of: " + ", ".join(models.DOCUMENT_FOLDERS))
    try:
        revision = models.clean_document_label(form.get("revision") or "", _REVISION_MAX, "revision")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    used = sum(int(r.get("size") or 0) for r in store.load_documents(project_id))
    if used >= PROJECT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="The project's document storage is full")
    file_id = secrets.token_urlsafe(16)

    def chunks() -> Iterator[bytes]:
        while True:
            block = uploaded.file.read(_CHUNK)
            if not block:
                return
            yield block

    limit = min(models.DOCUMENT_MAX_BYTES, PROJECT_MAX_BYTES - used)
    try:
        size, sha256 = await run_in_threadpool(store.save_document_stream, project_id, file_id, chunks(), limit)
    except ValueError as exc:
        if str(exc) == "too_large":
            raise HTTPException(status_code=413, detail="The file is larger than the allowed %d MB" % (limit // (1024 * 1024)))
        if str(exc) == "empty":
            raise HTTPException(status_code=400, detail="Empty file")
        raise HTTPException(status_code=400, detail="Invalid upload")
    row = models.normalize_document(file_id, _email(user), str(uploaded.filename or "unnamed"), folder=folder, revision=revision,
                                    internal=staff_flag and _flag(form.get("internal")), price_restricted=restricted, size=size, sha256=sha256,
                                    content_type=str(getattr(uploaded, "content_type", "") or ""))
    def add(doc: dict) -> None:
        doc.setdefault("documents", []).append(row)         # the row carries the restriction chosen with the upload: one write
        _queue(doc, user, "document.upload", row)

    try:
        store.mutate_json(project_id, store.DOCUMENTS_JSON, add, default={"documents": []})
    except BaseException:
        # the row is the index of the file: without it the bytes are unreachable, so do not keep them
        (store.documents_file_dir(project_id) / file_id).unlink(missing_ok=True)
        raise
    _flush(project_id)
    return _public(row, True)


@router.patch("/{project_id}/documents/{doc_id}")
async def patch_document(request: Request, project_id: str, doc_id: str):
    """``internal`` (hidden from customer-side members), ``folder``, ``revision``; ``archived: false`` restores an archived document."""
    user, project, role, staff_flag = _context(request, project_id)
    prices = _prices(user, project, staff_flag)
    _require_row_then_editor(user, project_id, doc_id, role, staff_flag, prices)
    body = await request.json()
    allowed = ("internal", "price_restricted", "folder", "revision", "archived")
    if not isinstance(body, dict) or not any(k in body for k in allowed):
        raise HTTPException(status_code=422, detail="internal, price_restricted, folder, revision or archived expected")
    changes: dict[str, Any] = {}
    if "price_restricted" in body:
        if not _can_restrict(user, project):
            raise HTTPException(status_code=403, detail="Only the project owner or MOTRES staff restrict a document's price content")
        if not isinstance(body["price_restricted"], bool):
            raise HTTPException(status_code=422, detail="price_restricted: true | false expected")
        changes["price_restricted"] = body["price_restricted"]
    if "internal" in body:
        if not staff_flag:
            raise HTTPException(status_code=403, detail="Only MOTRES staff mark a document internal")
        if not isinstance(body["internal"], bool):
            raise HTTPException(status_code=422, detail="internal: true | false expected")
        changes["internal"] = body["internal"]
    if "folder" in body:
        if body["folder"] not in models.DOCUMENT_FOLDERS:
            raise HTTPException(status_code=422, detail="folder must be one of: " + ", ".join(models.DOCUMENT_FOLDERS))
        changes["folder"] = body["folder"]
    if "revision" in body:
        try:
            changes["revision"] = models.clean_document_label(body["revision"], _REVISION_MAX, "revision")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    if "archived" in body:
        if body["archived"] is not False:
            raise HTTPException(status_code=422, detail="archived: false (restore) expected; DELETE archives a document")
        changes["archived"] = False
    found: dict[str, Any] = {}

    def apply(doc: dict) -> None:
        for row in doc.setdefault("documents", []):
            if row.get("id") == doc_id and _visible([row], staff_flag, True, prices):      # a row this caller cannot list is "not found"
                before = {k: row.get(k) for k in changes}
                row.update(changes)
                if changes.get("archived") is False:
                    row["archived_at"] = None
                    row["archived_by"] = None
                changed = {k: {"from": bool(before[k]) if k == "price_restricted" else before[k], "to": v}
                           for k, v in changes.items() if (bool(before[k]) if k == "price_restricted" else before[k]) != v}
                found["row"], found["changed"] = dict(row), changed
                if changed:                                 # old and new captured under the lock, the event written WITH the change
                    _queue(doc, user, "document.restore" if "archived" in changed else "document.update", row, {"changes": changed})
                return

    store.mutate_json(project_id, store.DOCUMENTS_JSON, apply, default={"documents": []})
    if "row" not in found:
        raise HTTPException(status_code=404, detail="Document not found")
    # the cover is shown to the whole project: clear it BEFORE the audit is written, so an audit failure never leaves it in place
    try:
        if found["row"].get("internal"):
            clear_cover(project_id, user, doc_id, "document marked internal")
        elif found["row"].get("price_restricted"):
            clear_cover(project_id, user, doc_id, "document marked price-restricted")
    finally:
        _flush(project_id)
    return _public(found["row"], staff_flag)


def _require_row_then_editor(user: dict, project_id: str, doc_id: str, role: str | None, staff: bool, prices: bool) -> None:
    """404 for a document this caller cannot list - exactly the answer for an unknown id (no existence oracle) - and only then 403
    for a member who sees it but may not edit."""
    can_edit = _can_edit(user, role)
    _find(project_id, doc_id, staff, include_archived=can_edit, prices=prices)
    if not can_edit:
        raise HTTPException(status_code=403, detail="Project engineer access required")


@router.delete("/{project_id}/documents/{doc_id}")
def archive_document(request: Request, project_id: str, doc_id: str):
    """Archive = hide.  The file stays on disk and the audit trail keeps every row; ``PATCH {"archived": false}`` restores it."""
    user, project, role, staff_flag = _context(request, project_id)
    prices = _prices(user, project, staff_flag)
    _require_row_then_editor(user, project_id, doc_id, role, staff_flag, prices)
    found: dict[str, Any] = {}

    def apply(doc: dict) -> None:
        for row in doc.setdefault("documents", []):
            if row.get("id") == doc_id and _visible([row], staff_flag, True, prices):      # a row this caller cannot list is "not found"
                found["was"] = bool(row.get("archived"))
                if not row.get("archived"):
                    row["archived"] = True
                    row["archived_at"] = models.now()
                    row["archived_by"] = _email(user)
                    _queue(doc, user, "document.archive", row)
                found["row"] = dict(row)
                return

    store.mutate_json(project_id, store.DOCUMENTS_JSON, apply, default={"documents": []})
    if "row" not in found:
        raise HTTPException(status_code=404, detail="Document not found")
    if not found["was"]:
        try:
            clear_cover(project_id, user, doc_id, "document archived")
        finally:
            _flush(project_id)
    return _public(found["row"], staff_flag)


# ── download ──────────────────────────────────────────────────────────────────

@router.get("/{project_id}/documents/{doc_id}/download")
def download_document(request: Request, project_id: str, doc_id: str):
    """One document, streamed from disk.  Staff may also fetch an archived one."""
    user, project, role, staff = _context(request, project_id)
    row = _find(project_id, doc_id, staff, include_archived=_can_edit(user, role), prices=_prices(user, project, staff))
    path = store.read_document_file(project_id, str(row.get("file_id") or ""))
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    name = str(row.get("name") or row.get("file_id"))
    _log_download(request, user, project_id, kind="project_document", file=name, source_file=str(row.get("file_id")),
                  sha256=str(row.get("sha256") or ""), size=int(row.get("size") or path.stat().st_size))
    _audit(project_id, user, "document.download", row)
    return FileResponse(path, media_type="application/octet-stream", filename=name,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _zip_names(rows: list[dict]) -> list[tuple[dict, str]]:
    """``folder/name`` of every row, unique inside the archive (a repeated name gets " (2)", " (3)" ...)."""
    from motor_ai_sim.production.routes import _zip_component
    used: set[str] = set()
    out = []
    for row in rows:
        base = _zip_component(str(row.get("name") or row.get("file_id") or "document"), "document")
        folder = str(row.get("folder") or "docs")
        stem, dot, ext = base.rpartition(".")
        if not dot or not stem:
            stem, ext = base, ""
        else:
            ext = "." + ext
        candidate, n = "%s/%s%s" % (folder, stem, ext), 1
        while candidate.casefold() in used:
            n += 1
            candidate = "%s/%s (%d)%s" % (folder, stem, n, ext)
        used.add(candidate.casefold())
        out.append((row, candidate))
    return out


@router.get("/{project_id}/documents.zip")
def download_documents_zip(request: Request, project_id: str):
    """All documents visible to the caller in one ZIP (folders kicad / fabrication / bom / 3d / docs + MANIFEST.txt)."""
    from motor_ai_sim.production.routes import _TempFileStreamingResponse
    user, project, _role, staff = _context(request, project_id)
    rows = _visible(store.load_documents(project_id), staff, prices=_prices(user, project, staff))
    present = []
    missing = []
    total = 0
    for row in rows:
        path = store.read_document_file(project_id, str(row.get("file_id") or ""))
        if path is None:
            missing.append(row)
            continue
        total += path.stat().st_size
        present.append((row, path))
    if len(present) > MAX_ZIP_FILES or total > MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="The documents exceed the %d MB archive limit; download them one by one."
                            % (MAX_ZIP_BYTES // (1024 * 1024)))
    names = dict((id(row), arc) for row, arc in _zip_names([r for r, _p in present]))
    now = datetime.datetime.now(datetime.timezone.utc)
    fd, temp_name = tempfile.mkstemp(prefix="project-documents-", suffix=".zip")
    temp_path = Path(temp_name)
    try:
        os.close(fd)
        manifest = ["Project documents: %s" % (project.get("title") or project_id), "Generated (UTC): %s" % now.isoformat(timespec="seconds"),
                    "Files included: %d" % len(present), "", "path | revision | size | sha256 | uploaded"]
        with zipfile.ZipFile(temp_path, "w", allowZip64=True) as archive:
            for row, path in present:
                arcname = names[id(row)]
                mode = zipfile.ZIP_STORED if Path(arcname).suffix.lower() in _STORED_EXT else zipfile.ZIP_DEFLATED
                archive.write(path, arcname, compress_type=mode)
                manifest.append("%s | %s | %d | %s | %s" % (arcname, row.get("revision") or "-", int(row.get("size") or 0),
                                                           row.get("sha256") or "-", str(row.get("uploaded_at") or "")[:10]))
            if missing:
                manifest += ["", "Not available on disk (%d): %s" % (len(missing), ", ".join(str(r.get("name")) for r in missing))]
            archive.writestr("MANIFEST.txt", "\n".join(manifest) + "\n")
        digest = hashlib.sha256()
        with temp_path.open("rb") as fh:
            for block in iter(lambda: fh.read(_CHUNK), b""):
                digest.update(block)
        size = temp_path.stat().st_size
        filename = "%s_documents.zip" % (re.sub(r"[^A-Za-z0-9_.-]+", "_", project_id).strip("._-") or "project")
        _log_download(request, user, project_id, kind="project_documents_zip", file=filename, source_file=None,
                      sha256=digest.hexdigest(), size=size)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    store.append_audit(project_id, _email(user), "documents.zip", "document", "", {"files": len(present), "bytes": size})

    def stream() -> Iterator[bytes]:
        try:
            with temp_path.open("rb") as fh:
                for block in iter(lambda: fh.read(_CHUNK), b""):
                    yield block
        finally:
            temp_path.unlink(missing_ok=True)

    iterator = stream()
    return _TempFileStreamingResponse(iterator, archive_path=temp_path, archive_iterator=iterator, media_type="application/zip",
                                      headers={"Content-Disposition": 'attachment; filename="%s"' % filename,
                                               "Cache-Control": "no-store", "Content-Length": str(size)})
