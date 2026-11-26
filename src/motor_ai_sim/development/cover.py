"""Cover image of a development project (owner 2026-11-01): the board's top view, a render or a photo shown on the project card
and at the top of the project page.

A member with write rights marks one image of the project's Documents (PNG / JPEG / WebP, not internal, not archived) as the
cover.  The server makes a small WebP thumbnail (``COVER_WIDTH`` px) so the Projects list stays light; ``GET /{id}/cover`` serves it
to project members and ``cover_version`` in the project summary is its cache key.  No cover = nothing is shown.
"""
from __future__ import annotations

import io
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from motor_ai_sim.development import documents, store

log = logging.getLogger(__name__)
router = APIRouter(tags=["development-cover"])      # included by routes.router (prefix /api/projects)

COVER_WIDTH = 480
COVER_FORMATS = {"PNG", "JPEG", "WEBP"}
MAX_PIXELS = 60_000_000


def make_thumbnail(path) -> bytes:
    """A WebP thumbnail (at most ``COVER_WIDTH`` x ``COVER_WIDTH`` px) of a PNG / JPEG / WebP file; ``ValueError`` for anything else."""
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as image:
            if image.format not in COVER_FORMATS:
                raise ValueError("The cover must be a PNG, JPEG or WebP image")
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("The image is too large (over %d megapixels)" % (MAX_PIXELS // 1_000_000))
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
            image.thumbnail((COVER_WIDTH, COVER_WIDTH), Image.LANCZOS)
            out = io.BytesIO()
            image.save(out, "WEBP", quality=80, method=4)
            return out.getvalue()
    except ValueError:
        raise
    except Exception as exc:                                # noqa: BLE001 - corrupt / bomb / unreadable file
        log.info("cover: unreadable image (%s)", type(exc).__name__)
        raise ValueError("The file is not a readable PNG, JPEG or WebP image")


def _cover_allowed(row: dict | None) -> bool:
    """The cover is shown to the WHOLE project, so a document that is price-restricted, internal or archived (or gone) never is one."""
    return bool(row) and not (row.get("price_restricted") or row.get("internal") or row.get("archived") or row.get("spec_source"))


@router.get("/{project_id}/cover")
def get_cover(request: Request, project_id: str):
    _user, project, _role, _staff = documents._context(request, project_id)
    cover = project.get("cover")
    doc_id = cover.get("document_id") if isinstance(cover, dict) else None
    row = next((r for r in store.load_documents(project_id) if r.get("id") == doc_id), None) if doc_id else None
    # enforced on every read, whatever happened to the stored cover (a failed clearing, a race): the source document decides
    if not _cover_allowed(row):
        raise HTTPException(status_code=404, detail="No cover image")
    path = store.cover_path(project_id, cover)          # the SAME pointer that was validated above, never a second read
    if path is None:
        raise HTTPException(status_code=404, detail="No cover image")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "no-store",
                                                                "X-Content-Type-Options": "nosniff"})


@router.put("/{project_id}/cover")
async def set_cover(request: Request, project_id: str):
    """``{"document_id": ...}``: make that document the cover."""
    user, project, _role, staff = documents._context(request, project_id, edit=True)
    body = await request.json()
    doc_id = body.get("document_id") if isinstance(body, dict) else None
    if not isinstance(doc_id, str) or not doc_id:
        raise HTTPException(status_code=422, detail="document_id expected")
    row = documents._find(project_id, doc_id, staff, prices=documents._prices(user, project, staff), spec=documents._spec(user, project))
    if row.get("price_restricted"):
        raise HTTPException(status_code=422, detail="A price-restricted document cannot be the cover (the whole project sees it)")
    if row.get("spec_source"):
        raise HTTPException(status_code=422, detail="A specification source file cannot be the cover (the whole project sees it)")
    if row.get("internal"):
        raise HTTPException(status_code=422, detail="An internal document cannot be the cover (the whole project sees it)")
    path = store.read_document_file(project_id, str(row.get("file_id") or ""))
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        thumbnail = await run_in_threadpool(make_thumbnail, path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    actor = documents._email(user)

    def publish() -> dict | None:
        """Re-read the source document UNDER the documents lock and publish only if it is still allowed: a PATCH that restricts it
        while the thumbnail was being made either commits first (refused here) or after (its cover clearing removes this one)."""
        out: dict = {}

        def apply(doc: dict) -> None:
            for current in doc.get("documents") or []:
                if isinstance(current, dict) and current.get("id") == doc_id:
                    if _cover_allowed(current) and current.get("file_id") == row.get("file_id"):
                        out["cover"] = store.set_cover(project_id, thumbnail, doc_id, actor,
                                                       audit={"document_id": doc_id, "name": current.get("name")})
                    return

        store.mutate_json(project_id, store.DOCUMENTS_JSON, apply, default={"documents": []})
        return out.get("cover")

    cover = await run_in_threadpool(publish)
    if cover is None:
        raise HTTPException(status_code=409, detail="The document changed while the cover was being made; it cannot be the cover")
    store.flush_project_audit(project_id)
    return {"document_id": doc_id, "cover_version": cover["version"]}


@router.delete("/{project_id}/cover")
def clear_cover(request: Request, project_id: str):
    user, _project, _role, _staff = documents._context(request, project_id, edit=True)
    store.clear_cover(project_id, audit=(documents._email(user), "cover removed"))
    store.flush_project_audit(project_id)
    return {"cover_version": None}
