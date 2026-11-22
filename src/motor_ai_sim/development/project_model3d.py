"""The 3-D model of a development project (owner 2026-11-22): the STEP of the real CAD of the whole motor (the full assembly, or the
simplified integration model for the customer's housing), uploaded with a revision label and a "what changed" note.

A project before production has no Production record, so this is the one place for that model.  The tab "3D model" of the project
shows it in the SAME viewer as Configure (``modules/viewer3d``) next to the generated model of the linked configuration.

``/api/projects/{project_id}/model3d``:

* list        every project member; staff (global admin / engineer) see every row, the customer side only the rows marked
              ``visible_to_customer`` (the default of an upload is internal); archived rows only with ``?archived=1`` and only staff;
* upload      staff only, one STEP per request (``file``, ``revision`` required, ``note``, ``visible_to_customer``);
* patch       staff only: ``visible_to_customer`` and ``archived`` (archive / restore); a row and its file are never deleted;
* model.glb   the 3-D view of one row for whoever may list it (built once by ``production/step_glb.py``, cached next to the files);
              viewing on the page never hands out the STEP;
* download    the STEP itself: staff, and on the customer side only through the applicant-verification / NDA gate of the full STEP
              (``model3d.can_download``); 403 ``nda_required`` otherwise.  Every view and download goes to the traced download log.

The factory is not a project member and does not see this.  When the motor gets a Production record, the Production 3-D view reads
the SAME rows through ``models_for_project`` (one storage, two entrances, no copy).  No mail is sent from here: every change is a
project audit row (``model3d.*``) only.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import secrets
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from motor_ai_sim import users
from motor_ai_sim.development import access, models, store
from motor_ai_sim.development.project_uploads import bounded_form, flush_events, queue_event

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["development-model3d"])

MODELS_JSON = "model3d/models.json"
FILES_DIR = "model3d/files"
VIEW_CACHE_DIR = "model3d/viewcache"
#: one STEP; the 3-D view itself has the lower limit of the converter (step_glb.MAX_STEP_BYTES), a larger file is download-only
MAX_BYTES = models.DOCUMENT_MAX_BYTES
STEP_EXT = (".step", ".stp")
_REVISION_MAX = 40
_NOTE_MAX = 500
_CHUNK = 1024 * 1024


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _staff(user: dict) -> bool:
    """MOTRES staff: global admin / engineer.  A project role never makes staff (the customer creating a project is its owner)."""
    return access.is_engineer(user)


def _flag(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ── storage ───────────────────────────────────────────────────────────────────

def files_dir(project_id: str) -> Path:
    return store.path_for(project_id) / FILES_DIR


def load_rows(project_id: str) -> list[dict]:
    value = store.load_json(project_id, MODELS_JSON, {"models": []})
    rows = value.get("models") if isinstance(value, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []




def _mutate_doc(project_id: str, mutator) -> Any:
    """``mutator(doc)`` on the whole store document (rows and pending audit events) under the lock."""
    result: dict = {}

    def apply(doc: dict) -> None:
        doc.setdefault("models", [])
        result["value"] = mutator(doc)

    store.mutate_json(project_id, MODELS_JSON, apply, default={"models": []})
    return result.get("value")


def _file_path(project_id: str, file_id: str) -> Path | None:
    if not store.valid_file_id(file_id):
        return None
    candidate = files_dir(project_id) / file_id
    return candidate if candidate.is_file() else None


def _save_stream(project_id: str, file_id: str, chunks: Iterator[bytes], limit: int) -> tuple[int, str, bytes]:
    """Write ``chunks`` to ``model3d/files/<file_id>`` without holding the file; returns (size, sha256, first bytes).
    A file over ``limit`` or an empty one is removed again (``ValueError`` "too_large" / "empty")."""
    store.ensure_writable()
    if not store.valid_file_id(file_id):
        raise ValueError("invalid file id")
    directory = files_dir(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / file_id
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with dest.open("wb") as stream:
            for chunk in chunks:
                size += len(chunk)
                if size > limit:
                    raise ValueError("too_large")
                if len(head) < 64:
                    head += chunk[:64 - len(head)]
                digest.update(chunk)
                stream.write(chunk)
        if size == 0:
            raise ValueError("empty")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    from motor_ai_sim.private_files import chmod_private
    chmod_private(dest)
    return size, digest.hexdigest(), head


def visible_rows(rows: list[dict], staff: bool, include_archived: bool = False) -> list[dict]:
    """What a caller may list: staff everything (archived only on request), the customer side the visible, current rows only."""
    if staff:
        return [r for r in rows if include_archived or not r.get("archived")]
    return [r for r in rows if r.get("visible_to_customer") and not r.get("archived")]


def latest(rows: list[dict]) -> dict | None:
    """The row the viewer opens on: the newest upload that is not archived."""
    live = [r for r in rows if not r.get("archived")]
    return max(live, key=lambda r: str(r.get("uploaded_at") or "")) if live else None


def models_for_project(project_id: str, *, staff: bool) -> list[dict]:
    """The seam for a second entrance (the Production 3-D view of the same motor): the rows a reader sees, newest first, with the
    path of each file - read from THIS storage, never copied."""
    rows = sorted(visible_rows(load_rows(project_id), staff), key=lambda r: str(r.get("uploaded_at") or ""), reverse=True)
    return [{**_public(r, staff), "path": str(_file_path(project_id, str(r.get("id") or "")) or "")} for r in rows]


def _public(row: dict, staff: bool) -> dict:
    out = {k: row.get(k) for k in ("id", "name", "revision", "note", "size", "sha256", "uploaded_at")}
    if staff:
        out.update(visible_to_customer=bool(row.get("visible_to_customer")), archived=bool(row.get("archived")),
                   archived_at=row.get("archived_at"), uploader=str(row.get("uploader_email") or ""))
    else:
        rec = users.get_user(str(row.get("uploader_email") or "")) if row.get("uploader_email") else None
        name = str((rec or {}).get("name") or "")
        out["uploader"] = "" if not name or "@" in name else name
    return out


def _details(row: dict, extra: dict | None = None) -> dict:
    details = {"name": row.get("name"), "revision": row.get("revision"), "sha256": row.get("sha256"),
               "visible_to_customer": bool(row.get("visible_to_customer"))}
    details.update(extra or {})
    return details


def _queue(doc: dict, user: dict, action: str, row: dict, extra: dict | None = None) -> None:
    """The audit row of a change, written into the store together with it (``project_uploads``)."""
    queue_event(doc, _email(user), action, "model3d", str(row.get("id") or ""), _details(row, extra))


def _flush(project_id: str) -> None:
    """Write the pending audit rows; a new change is refused (503) while the audit cannot be written."""
    try:
        flush_events(project_id, MODELS_JSON)
    except OSError as exc:
        log.error("project model3d: audit not writable (%s) - change refused", exc)
        raise HTTPException(status_code=503, detail="Audit log unavailable; try again later.")


def _flush_after(project_id: str) -> None:
    """After a committed change: its audit row is in the store already, so a failing append only leaves it pending for the next call."""
    try:
        flush_events(project_id, MODELS_JSON)
    except Exception as exc:                                # noqa: BLE001
        log.error("project model3d: audit row left pending (%s); written by the next call", exc)


# ── access ────────────────────────────────────────────────────────────────────

def _context(request: Request, project_id: str):
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    user, project, role = access.require_project(request, project_id)
    if str(user.get("role") or "").lower() == "oem":
        # the factory never sees the project model, even when it is (also) a project member: it has Production (owner 2026-11-22)
        raise HTTPException(status_code=404, detail="Project not found")
    return user, project, role, _staff(user)


def _require_staff(staff: bool) -> None:
    if not staff:
        raise HTTPException(status_code=403, detail="Only MOTRES staff upload and change the project model")


def _find(project_id: str, model_id: str, staff: bool, include_archived: bool = False) -> dict:
    for row in visible_rows(load_rows(project_id), staff, include_archived):
        if row.get("id") == model_id:
            return row
    raise HTTPException(status_code=404, detail="Model not found")


def _identity(user: dict) -> tuple[dict, dict]:
    role = str(user.get("role") or "")
    ident = {**user, "id": _email(user) or str(user.get("id") or ""), "is_admin": role.lower() == "admin"}
    return ident, users.get_user(ident["id"]) or {}


def _can_download(user: dict, staff: bool) -> bool:
    """The STEP itself: staff, else the gate of the full STEP (NDA signed AND applicant verification VERIFIED)."""
    if staff:
        return True
    from motor_ai_sim import model3d as M
    ident, rec = _identity(user)
    return M.can_download(ident, rec)


def _trace(request: Request, user: dict, project_id: str, row: dict, kind: str) -> None:
    """The traced download log shared with the 3-D / STEP, production and project files; a read nobody can account for is refused."""
    from motor_ai_sim import model3d as M
    ident, rec = _identity(user)
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
                            role=str(user.get("role") or ""), company=who["company"], nda=basis["type"] != "internal", nda_basis=basis,
                            die="", config="", motor_id="project/%s" % project_id, project=project_id, kind=kind,
                            file=str(row.get("name") or ""), source_file=str(row.get("id") or ""), sha256=str(row.get("sha256") or ""),
                            bytes=int(row.get("size") or 0), ip=ip, user_agent=request.headers.get("user-agent", "")[:300]))
    except OSError as exc:
        log.error("project model3d: download log not writable (%s) - read refused", exc)
        raise HTTPException(status_code=503, detail="Download log unavailable; try again later.")


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/model3d")
def list_models(request: Request, project_id: str, archived: bool = False):
    user, _project, _role, staff = _context(request, project_id)
    _flush_after(project_id)                                # an audit row a failed append left pending
    rows = visible_rows(load_rows(project_id), staff, include_archived=archived and staff)
    rows = sorted(rows, key=lambda r: str(r.get("uploaded_at") or ""), reverse=True)
    top = latest(rows)
    return {"models": [_public(r, staff) for r in rows], "latest": top.get("id") if top else None,
            "can_upload": staff, "can_download": _can_download(user, staff)}


@router.post("/{project_id}/model3d")
async def upload_model(request: Request, project_id: str):
    """One STEP per request: ``file``, ``revision`` (required), ``note`` (what changed), ``visible_to_customer`` (default internal)."""
    user, _project, _role, staff = _context(request, project_id)
    _require_staff(staff)
    _flush(project_id)
    form = await bounded_form(request, MAX_BYTES)
    try:
        return await _upload(project_id, user, form)
    finally:
        await form.close()


async def _upload(project_id: str, user: dict, form) -> dict:
    uploaded = form.get("file")
    if uploaded is None or isinstance(uploaded, str):
        raise HTTPException(status_code=400, detail="No file uploaded")
    name = str(uploaded.filename or "model.step")
    if not name.lower().endswith(STEP_EXT):
        raise HTTPException(status_code=422, detail="The project model is a STEP file (.step / .stp)")
    try:
        revision = models.clean_document_label(form.get("revision") or "", _REVISION_MAX, "revision")
        note = models.clean_document_label(form.get("note") or "", _NOTE_MAX, "note")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not revision:
        raise HTTPException(status_code=422, detail="revision is required")
    file_id = secrets.token_urlsafe(16)

    def chunks() -> Iterator[bytes]:
        while True:
            block = uploaded.file.read(_CHUNK)
            if not block:
                return
            yield block

    try:
        size, sha256, head = await run_in_threadpool(_save_stream, project_id, file_id, chunks(), MAX_BYTES)
    except ValueError as exc:
        if str(exc) == "too_large":
            raise HTTPException(status_code=413, detail="The file is larger than the allowed %d MB" % (MAX_BYTES // (1024 * 1024)))
        if str(exc) == "empty":
            raise HTTPException(status_code=400, detail="Empty file")
        raise HTTPException(status_code=400, detail="Invalid upload")
    from motor_ai_sim.production import step_glb
    if not step_glb.looks_like_step(head):
        (files_dir(project_id) / file_id).unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="This file is not a STEP file.")
    row = {"id": file_id, "name": name[:200], "revision": revision, "note": note, "size": size, "sha256": sha256,
           "uploaded_at": _now(), "uploader_email": _email(user), "visible_to_customer": _flag(form.get("visible_to_customer")),
           "archived": False, "archived_at": None}

    def add(doc: dict) -> None:
        doc["models"].append(row)
        _queue(doc, user, "model3d.upload", row, {"note": note})

    try:
        _mutate_doc(project_id, add)
    except BaseException:
        (files_dir(project_id) / file_id).unlink(missing_ok=True)     # the row is the index of the file: no row, no bytes
        raise
    _flush_after(project_id)
    return _public(row, True)


@router.patch("/{project_id}/model3d/{model_id}")
async def patch_model(request: Request, project_id: str, model_id: str):
    """Staff only: ``visible_to_customer`` (bool) and ``archived`` (true = archive, false = restore).  Nothing is deleted."""
    user, _project, _role, staff = _context(request, project_id)
    _require_staff(staff)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = None
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=422, detail="Send visible_to_customer and / or archived")
    unknown = sorted(set(body) - {"visible_to_customer", "archived"})
    if unknown:
        raise HTTPException(status_code=422, detail="Unknown field(s): " + ", ".join(unknown))
    for key in body:
        if not isinstance(body[key], bool):
            raise HTTPException(status_code=422, detail="%s must be true or false" % key)
    _find(project_id, model_id, True, include_archived=True)
    _flush(project_id)
    changes: dict = {}

    def apply(doc: dict) -> dict | None:
        for r in doc["models"]:
            if r.get("id") == model_id:
                if "visible_to_customer" in body and bool(r.get("visible_to_customer")) != body["visible_to_customer"]:
                    r["visible_to_customer"] = body["visible_to_customer"]
                    changes["visible_to_customer"] = body["visible_to_customer"]
                if "archived" in body and bool(r.get("archived")) != body["archived"]:
                    r["archived"] = body["archived"]
                    r["archived_at"] = _now() if body["archived"] else None
                    changes["archived"] = body["archived"]
                if "archived" in changes:
                    _queue(doc, user, "model3d.archive" if changes["archived"] else "model3d.restore", r)
                if "visible_to_customer" in changes:
                    _queue(doc, user, "model3d.visibility", r)
                return dict(r)
        return None

    row = _mutate_doc(project_id, apply)
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")
    _flush_after(project_id)
    return _public(row, True)


@router.get("/{project_id}/model3d/{model_id}/model.glb")
async def model_view(request: Request, project_id: str, model_id: str):
    """The 3-D view of one STEP for whoever may list it; converted once and cached in ``model3d/viewcache``."""
    from motor_ai_sim.production import step_glb
    user, _project, _role, staff = _context(request, project_id)
    row = _find(project_id, model_id, staff, include_archived=staff)
    path = _file_path(project_id, model_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Model not found")
    if path.stat().st_size > step_glb.MAX_STEP_BYTES:
        raise HTTPException(status_code=413, detail="This STEP file is too large for the 3-D view; download it instead.")
    await run_in_threadpool(_trace, request, user, project_id, row, "project_model3d_view")
    try:
        glb = await run_in_threadpool(step_glb.glb_for_step, path, model_id, store.path_for(project_id) / VIEW_CACHE_DIR)
    except step_glb.StepConversionError as ex:
        raise HTTPException(status_code=ex.status, detail=ex.detail)
    return Response(content=glb, media_type="model/gltf-binary", headers={"Cache-Control": "no-store"})


@router.get("/{project_id}/model3d/{model_id}/download")
def download_model(request: Request, project_id: str, model_id: str):
    """The STEP: staff, or the customer side through the NDA / verification gate (403 ``nda_required``)."""
    user, _project, _role, staff = _context(request, project_id)
    row = _find(project_id, model_id, staff, include_archived=staff)
    if not _can_download(user, staff):
        from motor_ai_sim import model3d as M
        ident, rec = _identity(user)
        raise HTTPException(status_code=403, detail={"code": "nda_required", "message": M.denial_detail(ident, rec)})
    path = _file_path(project_id, model_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Model not found")
    _trace(request, user, project_id, row, "project_model3d_step")
    store.append_audit(project_id, _email(user), "model3d.download", "model3d", model_id, _details(row))   # a read: no file without its row
    return FileResponse(path, media_type="application/octet-stream", filename=str(row.get("name") or "model.step"),
                        headers={"Cache-Control": "no-store"})
