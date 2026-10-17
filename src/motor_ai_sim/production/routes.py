"""Caller routes for the production module: ``/api/production/*``.

Contract: docs/PRODUCTION_MODULE_DESIGN.md section 5 and
web/src/modules/production/api.ts.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from motor_ai_sim import auth, model3d, users
from motor_ai_sim.production import store, models
from motor_ai_sim.production.access import (
    require_member, require_member_with_full_view,
    require_editor, require_editor_with_full_view,
    require_proposer_or_editor,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/production", tags=["production"])


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _is_admin(user: dict) -> bool:
    return bool(user.get("role") == "admin")


def _now() -> str:
    return models._now()


def _production_list_for(email: str, is_admin: bool) -> list[dict]:
    root = store.production_root()
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for c in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
            p = c / "production.json"
            if not p.is_file():
                continue
            rec = store._read_json(p, default={})
            if not isinstance(rec, dict) or not rec.get("die"):
                continue
            if is_admin or models.is_member(email, rec) is not None:
                out.append(rec)
    return out


def _touch(die: str, config: str) -> None:
    def mut(d):
        if not isinstance(d, dict):
            d.update(models.normalize_production({}, die, config))
        d["updated_at"] = _now()
    store.mutate_file(die, config, "production.json", mut)


def _audit(who: str, die: str, config: str, action: str, entity: str, entity_id: str, snapshot: Optional[dict] = None):
    store.append_audit(die, config, models.audit_entry(who, action, entity, entity_id, snapshot))


@router.get("")
def list_productions(request: Request):
    user = auth.resolve_user(request.headers.get("authorization"))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return {"productions": _production_list_for(_email(user), _is_admin(user))}


@router.get("/{die}/{config}")
def get_production(request: Request, die: str, config: str):
    user, production = require_member_with_full_view(request, die, config)
    return production


# ── parts ────────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/parts")
def list_parts(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "parts.json")


@router.post("/{die}/{config}/parts")
async def create_part(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    part = models.normalize_part(body)
    part["name"] = models.ensure_translated(part["name"])
    part["description"] = models.ensure_translated(part["description"])
    store.mutate_file(die, config, "parts.json", lambda d: d.setdefault("parts", []).append(part))
    _touch(die, config)
    _audit(_email(user), die, config, "create", "part", part["id"], {"part": part})
    return part


@router.patch("/{die}/{config}/parts/{part_id}")
async def update_part(request: Request, die: str, config: str, part_id: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)

    def mut(d):
        parts = d.setdefault("parts", [])
        for i, p in enumerate(parts):
            if isinstance(p, dict) and p.get("id") == part_id:
                # merge allowed scalar fields, replace bilingual objects
                updated = dict(p)
                for k in ("kind", "qty_per_motor", "material", "spec", "optional_step", "revision"):
                    if k in body:
                        updated[k] = body[k]
                if "name" in body:
                    updated["name"] = models.ensure_translated(models.normalize_bilingual(body["name"]))
                if "description" in body:
                    updated["description"] = models.ensure_translated(models.normalize_bilingual(body["description"]))
                if "drawing_files" in body:
                    updated["drawing_files"] = models.normalize_part({"drawing_files": body["drawing_files"]}).get("drawing_files", [])
                parts[i] = updated
                return
        raise HTTPException(status_code=404, detail="Part not found")

    store.mutate_file(die, config, "parts.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "update", "part", part_id, {"patch": body})
    parts = store.load_file(die, config, "parts.json").get("parts", [])
    for p in parts:
        if p.get("id") == part_id:
            return p
    raise HTTPException(status_code=404, detail="Part not found")


@router.delete("/{die}/{config}/parts/{part_id}")
def delete_part(request: Request, die: str, config: str, part_id: str):
    user, _production = require_editor_with_full_view(request, die, config)

    def mut(d):
        before = list(d.get("parts", []))
        d["parts"] = [p for p in before if not (isinstance(p, dict) and p.get("id") == part_id)]
        if len(d["parts"]) == len(before):
            raise HTTPException(status_code=404, detail="Part not found")

    store.mutate_file(die, config, "parts.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "delete", "part", part_id, {})
    return {}


# ── BOM ──────────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/bom")
def get_bom(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "bom.json")


@router.put("/{die}/{config}/bom")
async def save_bom(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    rows = body.get("rows") if isinstance(body, dict) else []
    parts = store.load_file(die, config, "parts.json").get("parts", [])
    part_ids = {p.get("id") for p in parts if isinstance(p, dict)}
    cleaned = models.normalize_bom_rows(rows, part_ids)
    store.save_file(die, config, "bom.json", {"rows": cleaned})
    _touch(die, config)
    _audit(_email(user), die, config, "update", "bom", "bom", {"rows": cleaned})
    return {}


# ── process ──────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/process")
def get_process(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "process.json")


@router.put("/{die}/{config}/process")
async def save_process(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    sections = body.get("sections") if isinstance(body, dict) else []
    if not isinstance(sections, list):
        sections = []
    cleaned = [models.normalize_process_section(s) for s in sections]
    store.save_file(die, config, "process.json", {"sections": cleaned})
    _touch(die, config)
    _audit(_email(user), die, config, "update", "process", "process", {"sections": cleaned})
    return {}


# ── documents / files ────────────────────────────────────────────────────────

@router.get("/{die}/{config}/documents")
def list_documents(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "documents.json")


@router.post("/{die}/{config}/documents")
async def upload_document(request: Request, die: str, config: str):
    # every member may upload (owner 2026-10-17: the factory sends photos, test reports, anything it needs;
    # we sort the documents out ourselves).  Design data (parts / BOM / process) stays editor-only.
    user, _production = require_member_with_full_view(request, die, config)
    form = await request.form()
    uploaded = form.get("file")
    if uploaded is None:
        raise HTTPException(status_code=400, detail="No file uploaded")
    data = await uploaded.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    file_id = secrets.token_urlsafe(16)
    store.save_uploaded_file(die, config, file_id, data)
    part_id = form.get("part_id")
    if part_id and not isinstance(part_id, str):
        part_id = str(part_id)
    doc = models.normalize_document({}, file_id, _email(user), str(uploaded.filename or "unnamed"), part_id if part_id else None)
    store.mutate_file(die, config, "documents.json", lambda d: d.setdefault("documents", []).append(doc))
    _touch(die, config)
    _audit(_email(user), die, config, "create", "document", doc["id"], {"doc": doc})
    return doc


@router.get("/{die}/{config}/files/{file_id}")
def download_file(request: Request, die: str, config: str, file_id: str):
    """Download one production file.  Logged in the SAME traced download log as the full 3-D / STEP files
    (model3d.log_download: who, company, NDA basis, sha256, IP) plus the production audit."""
    import datetime
    from motor_ai_sim import model3d as M
    if not store.valid_file_id(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    user, _production = require_member_with_full_view(request, die, config)
    path = store.read_uploaded_file(die, config, file_id)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    data = path.read_bytes()
    name = _original_name(die, config, file_id) or file_id
    rec = users.get_user(_email(user)) or {}
    basis = M.nda_basis(user, rec)
    who_for = M.recipient_of(user, rec, basis)
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        M.log_download(dict(download_id=M.new_download_id(now), user=user.get("id"), email=who_for["email"],
                            name=M.customer_name(user, rec), role=user.get("role"), company=who_for["company"],
                            nda=basis["type"] != "internal", nda_basis=basis, die=die, config=config,
                            motor_id="%s/%s" % (die, config), kind="production_file", file=name, source_file=file_id,
                            sha256=M.sha256_bytes(data), bytes=len(data), ip=ip,
                            user_agent=request.headers.get("user-agent", "")[:300]))
    except OSError as ex:
        log.error("production: download log not writable (%s) - download refused", ex)
        raise HTTPException(status_code=503, detail="Download log unavailable; try again later.")
    _audit(_email(user), die, config, "download", "document", file_id, {"file": name})
    safe = M.ascii_text(name, 120).replace('"', "") or file_id
    from urllib.parse import quote
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (safe, quote(name)),
                             "Cache-Control": "no-store"})


def _original_name(die: str, config: str, file_id: str) -> Optional[str]:
    """The uploader's file name of a document or a message attachment (None when unknown)."""
    for d in (store.load_file(die, config, "documents.json") or {}).get("documents", []):
        if d.get("file_id") == file_id or d.get("id") == file_id:
            return d.get("name") or d.get("filename")
    for t in (store.load_file(die, config, "threads.json") or {}).get("threads", []):
        for m in t.get("messages", []):
            for a in m.get("attachments", []) or []:
                if a.get("file_id") == file_id:
                    return a.get("name")
    return None


# ── threads / messages ───────────────────────────────────────────────────────

@router.get("/{die}/{config}/threads")
def list_threads(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "threads.json")


@router.post("/{die}/{config}/threads")
async def create_thread(request: Request, die: str, config: str):
    user, _production = require_member_with_full_view(request, die, config)
    body = await _json_or_form(request)
    part_id = body.get("part_id") if isinstance(body, dict) else None
    thread = models.normalize_thread({"part_id": part_id})
    store.mutate_file(die, config, "threads.json", lambda d: d.setdefault("threads", []).append(thread))
    _touch(die, config)
    _audit(_email(user), die, config, "create", "thread", thread["id"], {"thread": thread})
    return thread


@router.post("/{die}/{config}/threads/{thread_id}/messages")
async def post_message(request: Request, die: str, config: str, thread_id: str):
    user, _production = require_member_with_full_view(request, die, config)
    text: Optional[dict] = None
    attachments: list[dict] = []
    src_lang = "en"

    content_type = request.headers.get("content-type") or ""
    if "multipart/form-data" in content_type:
        form = await request.form()
        text_raw = form.get("text")
        if isinstance(text_raw, str):
            try:
                import json as _json
                text = _json.loads(text_raw)
            except Exception:                               # noqa: BLE001
                text = None
        src_lang = str(form.get("src_lang") or "en")
        for key in form:
            val = form[key]
            if key == "files" and hasattr(val, "read"):
                data = await val.read()
                if data:
                    file_id = secrets.token_urlsafe(16)
                    store.save_uploaded_file(die, config, file_id, data)
                    attachments.append({"file_id": file_id, "name": str(val.filename or "attachment")})
    else:
        body = await _json_or_form(request)
        if isinstance(body, dict):
            text = body.get("text")
            src_lang = str(body.get("src_lang") or "en")

    if not isinstance(text, dict):
        raise HTTPException(status_code=400, detail="Message text required")

    message = models.normalize_message({
        "author_email": _email(user),
        "src_lang": src_lang,
        "text": text,
        "attachments": attachments,
    })

    def mut(d):
        for t in d.setdefault("threads", []):
            if isinstance(t, dict) and t.get("id") == thread_id:
                t.setdefault("messages", []).append(message)
                return
        raise HTTPException(status_code=404, detail="Thread not found")

    store.mutate_file(die, config, "threads.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "create", "thread_message", message["id"], {"thread_id": thread_id})
    return _find_thread(die, config, thread_id)


def _find_thread(die: str, config: str, thread_id: str) -> dict:
    for t in store.load_file(die, config, "threads.json").get("threads", []):
        if isinstance(t, dict) and t.get("id") == thread_id:
            return t
    raise HTTPException(status_code=404, detail="Thread not found")


# ── proposals ────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/proposals")
def list_proposals(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "proposals.json")


@router.post("/{die}/{config}/proposals")
async def create_proposal(request: Request, die: str, config: str):
    user, production = require_member_with_full_view(request, die, config)
    is_admin = _is_admin(user)
    if not models.is_member(_email(user), production) and not is_admin:
        raise HTTPException(status_code=404, detail="Production not found")
    body = await _json_or_form(request)
    part_id = body.get("part_id") if isinstance(body, dict) else None
    text = body.get("text") if isinstance(body, dict) else {}
    if not isinstance(text, dict):
        raise HTTPException(status_code=400, detail="Proposal text required")
    proposal = models.normalize_proposal({
        "part_id": part_id,
        "author_email": _email(user),
        "text": text,
    })
    proposal["text"] = models.ensure_translated(proposal["text"])
    store.mutate_file(die, config, "proposals.json", lambda d: d.setdefault("proposals", []).append(proposal))
    _touch(die, config)
    _audit(_email(user), die, config, "create", "proposal", proposal["id"], {"proposal": proposal})
    return proposal


async def _decide_proposal(request: Request, die: str, config: str, proposal_id: str, action: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    note = body.get("note") if isinstance(body, dict) else None
    if note is not None and not isinstance(note, dict):
        note = None

    def mut(d):
        for p in d.setdefault("proposals", []):
            if isinstance(p, dict) and p.get("id") == proposal_id:
                p["status"] = action
                p["resolved_at"] = _now()
                if note:
                    p["decision_note"] = models.ensure_translated(models.normalize_bilingual(note))
                else:
                    p["decision_note"] = None
                if action == "implemented":
                    p["linked_revision"] = _publish_revision_from_proposal(die, config, user, p)
                return
        raise HTTPException(status_code=404, detail="Proposal not found")

    store.mutate_file(die, config, "proposals.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, action, "proposal", proposal_id, {"note": note})
    proposals = store.load_file(die, config, "proposals.json").get("proposals", [])
    for p in proposals:
        if p.get("id") == proposal_id:
            return p
    raise HTTPException(status_code=404, detail="Proposal not found")


@router.post("/{die}/{config}/proposals/{proposal_id}/accept")
async def accept_proposal(request: Request, die: str, config: str, proposal_id: str):
    return await _decide_proposal(request, die, config, proposal_id, "accepted")


@router.post("/{die}/{config}/proposals/{proposal_id}/reject")
async def reject_proposal(request: Request, die: str, config: str, proposal_id: str):
    return await _decide_proposal(request, die, config, proposal_id, "rejected")


@router.post("/{die}/{config}/proposals/{proposal_id}/implement")
async def implement_proposal(request: Request, die: str, config: str, proposal_id: str):
    return await _decide_proposal(request, die, config, proposal_id, "implemented")


# ── revisions ────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/revisions")
def list_revisions(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "revisions.json")


@router.post("/{die}/{config}/revisions")
async def publish_revision(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    summary = body.get("summary") if isinstance(body, dict) else {}
    changes = body.get("changes") if isinstance(body, dict) else []
    if not isinstance(summary, dict):
        raise HTTPException(status_code=400, detail="Revision summary required")
    if not isinstance(changes, list):
        changes = []
    summary_norm = models.ensure_translated(models.normalize_bilingual(summary))
    changes_norm = [models.ensure_translated(models.normalize_bilingual(c)) for c in changes]
    revision = _publish_revision(die, config, user, summary_norm, changes_norm)
    return revision


@router.post("/{die}/{config}/revisions/{rev_number}/ack")
async def ack_revision(request: Request, die: str, config: str, rev_number: str):
    user, _production = require_member_with_full_view(request, die, config)

    def mut(d):
        for r in d.setdefault("revisions", []):
            if isinstance(r, dict) and str(r.get("number")) == rev_number:
                acks = r.setdefault("acknowledgements", [])
                acks = [a for a in acks if not (isinstance(a, dict) and a.get("email") == _email(user))]
                acks.append({"email": _email(user), "time": _now()})
                r["acknowledgements"] = acks
                return
        raise HTTPException(status_code=404, detail="Revision not found")

    store.mutate_file(die, config, "revisions.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "ack_revision", "revision", rev_number, {})
    revisions = store.load_file(die, config, "revisions.json").get("revisions", [])
    for r in revisions:
        if str(r.get("number")) == rev_number:
            return r
    raise HTTPException(status_code=404, detail="Revision not found")


def _publish_revision(die: str, config: str, user: dict, summary: dict, changes: list) -> dict:
    existing = store.load_file(die, config, "revisions.json").get("revisions", [])
    number = models.next_revision_number(existing)
    revision = models.normalize_revision({
        "number": number,
        "author_email": _email(user),
        "summary": summary,
        "changes": changes,
    })
    store.mutate_file(die, config, "revisions.json", lambda d: d.setdefault("revisions", []).append(revision))
    store.mutate_file(die, config, "production.json", lambda d: d.update({"current_revision": number, "updated_at": _now()}))
    _touch(die, config)
    _audit(_email(user), die, config, "publish_revision", "revision", number, {"revision": revision})
    return revision


def _publish_revision_from_proposal(die: str, config: str, user: dict, proposal: dict) -> str:
    summary = proposal.get("text") or models._machine_fill("Implemented proposal", "en")
    revision = _publish_revision(die, config, user, summary, [])
    return revision["number"]


# ── translation helpers ──────────────────────────────────────────────────────

@router.post("/{die}/{config}/translate/retry")
async def retry_translation(request: Request, die: str, config: str):
    user, _production = require_member_with_full_view(request, die, config)
    ref = await _json_or_form(request)
    if not isinstance(ref, dict):
        raise HTTPException(status_code=400, detail="Translation reference required")
    _apply_translation(die, config, ref, retry=True)
    _audit(_email(user), die, config, "translate_retry", ref.get("entity", ""), ref.get("entity_id", ""), {"ref": ref})
    return {}


@router.post("/{die}/{config}/translate/confirm")
async def confirm_translation(request: Request, die: str, config: str):
    user, _production = require_member_with_full_view(request, die, config)
    body = await _json_or_form(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Translation reference required")
    lang = str(body.get("lang") or "en")
    text = body.get("text")
    if text is not None:
        text = str(text)
    ref = {k: body[k] for k in ("entity", "entity_id", "field", "message_id") if k in body}
    _apply_translation(die, config, ref, lang=lang, text=text)
    _audit(_email(user), die, config, "translate_confirm", ref.get("entity", ""), ref.get("entity_id", ""), {"ref": ref, "lang": lang})
    return {}


def _do_translate_or_confirm(bi: dict, retry: bool, lang: str, text: Optional[str]):
    if not isinstance(bi, dict) or "src_lang" not in bi:
        raise HTTPException(status_code=400, detail="Field is not bilingual")
    if retry:
        src_lang = bi.get("src_lang") or "en"
        other = "zh" if src_lang == "en" else "en"
        from motor_ai_sim.production import translate as T
        try:
            translated = T.translate_text(bi[src_lang], src_lang, other)
            bi[other] = translated
            bi.setdefault("status", {})[other] = "machine"
        except Exception:                               # noqa: BLE001
            pass
    else:
        if text is not None:
            bi[lang] = str(text)
            bi.setdefault("status", {})[lang] = "confirmed"
        elif bi.get("status", {}).get(lang) in ("machine", "pending") and bi.get(lang):
            bi.setdefault("status", {})[lang] = "confirmed"


def _apply_translation(die: str, config: str, ref: dict, *, retry: bool = False, lang: str = "en", text: Optional[str] = None):
    entity = str(ref.get("entity") or "")
    entity_id = str(ref.get("entity_id") or "")
    field = str(ref.get("field") or "")
    message_id = ref.get("message_id")

    if entity == "production":
        def mut(prod):
            bi = prod.get(field)
            _do_translate_or_confirm(bi, retry, lang, text)
        store.mutate_file(die, config, "production.json", mut)
    elif entity == "part":
        def mut(d):
            for p in d.setdefault("parts", []):
                if p.get("id") == entity_id:
                    bi = p.get(field)
                    _do_translate_or_confirm(bi, retry, lang, text)
                    return
            raise HTTPException(status_code=404, detail="Part not found")
        store.mutate_file(die, config, "parts.json", mut)
    elif entity == "process":
        def mut(d):
            for s in d.setdefault("sections", []):
                if s.get("id") == entity_id:
                    bi = s.get(field)
                    _do_translate_or_confirm(bi, retry, lang, text)
                    return
            raise HTTPException(status_code=404, detail="Process section not found")
        store.mutate_file(die, config, "process.json", mut)
    elif entity == "thread":
        def mut(d):
            for th in d.setdefault("threads", []):
                if th.get("id") == entity_id:
                    for msg in th.get("messages", []):
                        if msg.get("id") == message_id:
                            bi = msg.get(field)
                            _do_translate_or_confirm(bi, retry, lang, text)
                            return
                    raise HTTPException(status_code=404, detail="Message not found")
            raise HTTPException(status_code=404, detail="Thread not found")
        store.mutate_file(die, config, "threads.json", mut)
    elif entity == "proposal":
        def mut(d):
            for p in d.setdefault("proposals", []):
                if p.get("id") == entity_id:
                    bi = p.get(field)
                    _do_translate_or_confirm(bi, retry, lang, text)
                    return
            raise HTTPException(status_code=404, detail="Proposal not found")
        store.mutate_file(die, config, "proposals.json", mut)
    elif entity == "revision":
        def mut(d):
            for r in d.setdefault("revisions", []):
                if r.get("number") == entity_id:
                    bi = r.get(field)
                    _do_translate_or_confirm(bi, retry, lang, text)
                    return
            raise HTTPException(status_code=404, detail="Revision not found")
        store.mutate_file(die, config, "revisions.json", mut)
    else:
        raise HTTPException(status_code=400, detail="Unknown entity")


async def _json_or_form(request: Request):
    ctype = request.headers.get("content-type") or ""
    if "multipart/form-data" in ctype:
        form = await request.form()
        return {k: form[k] for k in form}
    try:
        return await request.json()
    except Exception:                               # noqa: BLE001
        return {}
