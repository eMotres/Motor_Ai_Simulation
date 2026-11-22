"""The "Contract" section of a customer project (owner 2026-11-22): the agreement, its annexes, the signed scans, the status and the
history, with the "How it works" scheme of how money and goods move.

``/api/projects/{project_id}/contract``:

* read        who may read the project's prices: MOTRES staff (global admin / engineer) and the members ``sees_prices`` lets through
              (the project owner always; another member when the owner has switched "sees prices" on, PR #800).  Until that switch
              exists only the project owner and staff read it.  Everybody else - and every factory (``oem``) account, member or not -
              gets 404, so the section is not even named to them;
* upload      staff: the agreement (one standard body for all customers), Annex A (product, guaranteed values, test conditions),
              Annex B (commercial terms), the MOTRES signed scan and other files; the customer side who may read it uploads ONLY its
              signed scan.  A PDF, DOCX, PNG or JPEG whose bytes match its name; every upload is a new version, nothing is deleted;
* status      draft -> sent -> signed by customer -> signed by both, bound to ONE revision of the contract:
              - "sent" (staff) freezes the revision's agreement and annexes (file ids + sha256 in the history row);
              - signing is CONFIRMED BY A PERSON, never inferred from a file: a scan uploaded on a sent revision is "awaiting the
                MOTRES check"; staff open it and confirm exactly that file (id + sha256 + revision + terms) -> "signed by
                customer", then upload the countersigned scan and confirm it -> "signed by both"; or reject a scan with a reason
                (the status stays, the scan stays in the history).  The customer side never sets a signed state;
              - every signing request carries the revision AND a digest of the terms the caller saw (``terms_digest``), checked
                under the lock: a stale page or a race is a 409;
              - from "sent" on, the frozen files and the scans cannot be replaced or archived; changing the terms is an explicit
                NEW REVISION (staff, back to draft), which needs fresh scans;
* download    who may read it; written to the traced download log and the project audit.

Signing, stage 1: download the PDF, sign, upload the scan.  No e-signature provider.  The section is never part of the shared
Documents or Messages, and it sends no mail: a change is a project audit row (``contract.*``) only, written into the store with
the change itself (``project_uploads``).

The scheme: the customer view is fixed text of the product (``web/src/locales/*/contract.json``); the STAFF view of the full
supply chain is internal and lives in the private data checkout (``config/contract/supply_scheme_staff.json``), never in this
public repository - without it the staff view says it is not configured.  Annex A generation waits for the guaranteed-values task.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import secrets
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from motor_ai_sim import users
from motor_ai_sim.development import access, models, store
from motor_ai_sim.development.file_structure import DOC_EXT, SCAN_EXT, content_matches
from motor_ai_sim.development.project_uploads import bounded_form, flush_events, queue_event

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["development-contract"])

CONTRACT_JSON = "contract/contract.json"
FILES_DIR = "contract/files"
STAFF_SCHEME = ("config", "contract", "supply_scheme_staff.json")
MAX_BYTES = 30 * 1024 * 1024
_CHUNK = 1024 * 1024
_NOTE_MAX = 300

STATUSES = ("draft", "sent", "signed_customer", "signed_both")
#: what the staff upload; the customer side uploads only its own signed scan
STAFF_KINDS = ("agreement", "annex_a", "annex_b", "signed_motres", "other")
CUSTOMER_KINDS = ("signed_customer",)
KINDS = STAFF_KINDS + CUSTOMER_KINDS
#: frozen by "sent" for the revision: what the customer signs
TERMS_KINDS = ("agreement", "annex_a", "annex_b")
SCAN_KINDS = ("signed_customer", "signed_motres")
#: the line of the project steps on the scheme, the current one highlighted
STEPS = ("specification", "simulation_agreement", "contract_payment", "samples_1", "samples_2", "series")


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _staff(user: dict) -> bool:
    return access.is_engineer(user)


def may_read(user: dict, project: dict) -> bool:
    """Staff, or whoever reads the project's prices; never a factory account.  ``access.sees_prices`` (PR #800) is the one answer
    when it exists; before it the project owner alone stands in for it."""
    if str(user.get("role") or "").lower() == "oem":
        return False
    if _staff(user):
        return True
    sees = getattr(access, "sees_prices", None)
    if callable(sees):
        return bool(sees(user, project))
    return access.membership(_email(user), project) == "owner"


# ── storage ───────────────────────────────────────────────────────────────────

def files_dir(project_id: str) -> Path:
    return store.path_for(project_id) / FILES_DIR


def _defaults(doc: dict) -> dict:
    doc.setdefault("status", "draft")
    doc.setdefault("revision", 1)
    doc.setdefault("history", [])
    doc.setdefault("files", [])
    return doc


def load(project_id: str) -> dict:
    doc = store.load_json(project_id, CONTRACT_JSON, {})
    return _defaults(doc if isinstance(doc, dict) else {})


def _mutate(project_id: str, mutator) -> dict:
    out: dict = {}

    def apply(doc: dict) -> None:
        mutator(_defaults(doc))
        out.update(json.loads(json.dumps(doc)))

    store.mutate_json(project_id, CONTRACT_JSON, apply, default={})
    return out


def _flush(project_id: str) -> None:
    """Write the pending audit rows; a new change is refused (503) while the audit cannot be written."""
    try:
        flush_events(project_id, CONTRACT_JSON)
    except OSError as exc:
        log.error("project contract: audit not writable (%s) - change refused", exc)
        raise HTTPException(status_code=503, detail="Audit log unavailable; try again later.")


def _flush_after(project_id: str) -> None:
    """After a committed change: its audit row is in the store already, so a failing append only leaves it pending for the next call."""
    try:
        flush_events(project_id, CONTRACT_JSON)
    except Exception as exc:                                # noqa: BLE001
        log.error("project contract: audit row left pending (%s); written by the next call", exc)


def _queue(doc: dict, user: dict, action: str, details: dict, project_id: str) -> None:
    queue_event(doc, _email(user), action, "contract", project_id, details)


def _file_path(project_id: str, file_id: str) -> Path | None:
    if not store.valid_file_id(file_id):
        return None
    candidate = files_dir(project_id) / file_id
    return candidate if candidate.is_file() else None


def _save_stream(project_id: str, file_id: str, chunks: Iterator[bytes], limit: int) -> tuple[int, str]:
    store.ensure_writable()
    if not store.valid_file_id(file_id):
        raise ValueError("invalid file id")
    directory = files_dir(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / file_id
    digest = hashlib.sha256()
    size = 0
    try:
        with dest.open("wb") as stream:
            for chunk in chunks:
                size += len(chunk)
                if size > limit:
                    raise ValueError("too_large")
                digest.update(chunk)
                stream.write(chunk)
        if size == 0:
            raise ValueError("empty")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    from motor_ai_sim.private_files import chmod_private
    chmod_private(dest)
    return size, digest.hexdigest()




# ── what the page reads ─────────────────────────────────────────────────────────

def current_step(project: dict, contract: dict) -> Optional[str]:
    """Where the project stands on the line of steps (None for a closed project)."""
    status = str(project.get("status") or "spec")
    if status == "closed":
        return None
    if status == "production":
        return "series"
    if status == "prototype":
        return "samples_2" if int(contract.get("sample_iteration") or 1) >= 2 else "samples_1"
    if status == "quote" or contract.get("status") in ("signed_customer", "signed_both"):
        return "contract_payment"
    if status in ("design", "simulation"):
        return "simulation_agreement"
    return "specification"


def company_state(project: dict) -> dict:
    """Annex B takes the customer company from the Company details form (PR #802); until it exists the state is unknown."""
    try:
        from motor_ai_sim.development import company_routes as C      # noqa: WPS433 - optional until PR #802 is merged
    except ImportError:
        return {"available": False, "filled": False, "confirmed_on": "", "confirmed_by": ""}
    values = C.company_placeholders(project)
    filled = any(v for k, v in values.items() if k not in ("company_confirmed_by", "company_confirmed_on"))
    return {"available": True, "filled": filled, "confirmed_on": values.get("company_confirmed_on", ""),
            "confirmed_by": values.get("company_confirmed_by", "")}


def staff_scheme() -> Optional[dict]:
    """The internal supply-chain scheme from the private data checkout, or None when it is not there."""
    from motor_ai_sim.private_data import private_path
    path = private_path(*STAFF_SCHEME)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("contract: staff scheme unreadable (%s)", exc)
        return None
    return data if isinstance(data, dict) else None


def _display(email: str, staff: bool) -> str:
    if staff:
        return email
    rec = users.get_user(email) if email else None
    name = str((rec or {}).get("name") or "")
    return "" if not name or "@" in name else name


def _ref(row: dict) -> dict:
    return {"id": row.get("id"), "kind": row.get("kind"), "name": row.get("name"), "sha256": row.get("sha256")}


def frozen_ids(doc: dict) -> set[str]:
    """The files the current revision's signing rests on: the terms frozen by "sent", the confirmed scans and the scan awaiting
    the MOTRES check."""
    ids = {str(r.get("id")) for r in (doc.get("sent_files") or [])}
    ids |= {str(r.get("id")) for r in (doc.get("signatures") or {}).values() if isinstance(r, dict)}
    if isinstance(doc.get("pending_scan"), dict):
        ids.add(str(doc["pending_scan"].get("id")))
    return ids


def terms_digest(doc: dict) -> str:
    """A short digest of the terms a signature is about: the frozen files (id + sha256) of a sent revision, else the live terms a
    "sent" would freeze.  The page captures it with the revision when a dialog opens; every signing request carries both."""
    refs = doc.get("sent_files") if doc.get("status") != "draft" else [_ref(f) for f in _live_terms(doc)]
    text = "|".join(sorted(f"{r.get('id')}:{r.get('sha256')}" for r in refs or []))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _live_terms(doc: dict) -> list[dict]:
    """The newest non-archived file of each terms kind (what "sent" freezes)."""
    out = []
    for kind in TERMS_KINDS:
        rows = [f for f in doc["files"] if f.get("kind") == kind and not f.get("archived")]
        if rows:
            out.append(max(rows, key=lambda f: str(f.get("uploaded_at") or "")))
    return out


def _public_file(row: dict, staff: bool, frozen: set[str]) -> dict:
    out = {k: row.get(k) for k in ("id", "kind", "name", "note", "size", "sha256", "uploaded_at", "archived", "archived_at", "revision")}
    out["uploader"] = _display(str(row.get("uploader_email") or ""), staff)
    out["frozen"] = str(row.get("id")) in frozen
    return out


def _view(project: dict, user: dict, contract: dict, include_archived: bool) -> dict:
    staff = _staff(user)
    frozen = frozen_ids(contract)
    files = [f for f in contract.get("files", []) if isinstance(f, dict) and (include_archived or not f.get("archived"))]
    files.sort(key=lambda f: str(f.get("uploaded_at") or ""), reverse=True)
    current: dict[str, str] = {}
    for f in files:                                       # what the signing rests on wins; else the newest live file of the kind
        if str(f.get("id")) in frozen:
            current[str(f.get("kind"))] = str(f.get("id"))
    for f in files:
        if not f.get("archived"):
            current.setdefault(str(f.get("kind")), str(f.get("id")))
    history = [{"status": h.get("status"), "at": h.get("at"), "by": _display(str(h.get("by") or ""), staff), "revision": h.get("revision"),
                "files": h.get("files") or [], "reason": h.get("reason") or ""} for h in contract.get("history", []) if isinstance(h, dict)]
    pending = contract.get("pending_scan") if isinstance(contract.get("pending_scan"), dict) else None
    status = contract.get("status", "draft")
    return {"status": status, "revision": int(contract.get("revision") or 1), "history": history,
            "files": [_public_file(f, staff, frozen) for f in files], "current": current,
            "sample_iteration": int(contract.get("sample_iteration") or 1),
            "step": current_step(project, contract), "steps": list(STEPS), "company": company_state(project),
            "staff": staff, "can_manage": staff, "upload_kinds": _upload_kinds(contract, staff),
            "terms_digest": terms_digest(contract), "pending_scan": pending,
            "staff_scheme": staff_scheme() if staff else None}


def _upload_kinds(doc: dict, staff: bool) -> list[str]:
    """What may be uploaded now: the terms only on a draft; the customer scan only on a sent revision, the MOTRES scan only after
    the customer's is confirmed; no scan while one awaits the MOTRES check."""
    status = str(doc.get("status") or "draft")
    allowed = {"draft": ["agreement", "annex_a", "annex_b", "other"], "sent": ["signed_customer", "other"],
               "signed_customer": ["signed_motres", "other"], "signed_both": ["other"]}[status]
    if doc.get("pending_scan"):
        allowed = [k for k in allowed if k not in SCAN_KINDS]
    return [k for k in allowed if staff or k in CUSTOMER_KINDS]


def _context(request: Request, project_id: str):
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    user, project, _role = access.require_project(request, project_id)
    if not may_read(user, project):
        raise HTTPException(status_code=404, detail="Not found")
    return user, project


def _seen_revision(raw: Any) -> int:
    """The contract revision the caller was looking at (every upload and status change carries it); 422 when missing."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="revision (the contract revision you are looking at) is required")
    if value < 1:
        raise HTTPException(status_code=422, detail="revision must be 1 or more")
    return value


STALE = "The contract changed meanwhile (now revision %d); reload it and check what you sign"


def _status_row(status: str, user: dict, revision: int, files: list[dict], reason: str = "") -> dict:
    row = {"status": status, "at": _now(), "by": _email(user), "revision": revision, "files": files}
    if reason:
        row["reason"] = reason
    return row


def _seen_terms(raw: Any) -> str:
    text = str(raw or "").strip()
    if not re.fullmatch(r"[0-9a-f]{16}", text):
        raise HTTPException(status_code=422, detail="terms_digest (of the terms you are looking at) is required")
    return text


def _stale(doc: dict, seen_rev: int, seen_terms: str) -> Optional[str]:
    """Under the lock: the revision AND the terms the caller looked at must still be the ones here."""
    if int(doc["revision"]) != seen_rev or terms_digest(doc) != seen_terms:
        return STALE % int(doc["revision"])
    return None


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/contract")
def get_contract(request: Request, project_id: str, archived: bool = False):
    user, project = _context(request, project_id)
    _flush_after(project_id)
    return _view(project, user, load(project_id), include_archived=archived and _staff(user))


@router.post("/{project_id}/contract/files")
async def upload_file(request: Request, project_id: str):
    """One file per request: ``file``, ``kind``, ``revision`` (the contract revision the caller is looking at: 409 when it changed
    meanwhile, so a stale page never signs a newer revision) and an optional ``note``.  What a status allows: see ``_upload_kinds``; a scan
    uploaded at its step moves the status on and is recorded with its id + sha256."""
    user, project = _context(request, project_id)
    _flush(project_id)
    form = await bounded_form(request, MAX_BYTES)
    try:
        return await _upload(project_id, project, user, form)
    finally:
        await form.close()


async def _upload(project_id: str, project: dict, user: dict, form) -> dict:
    staff = _staff(user)
    kind = str(form.get("kind") or "").strip()
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail="kind must be one of: " + ", ".join(KINDS))
    if not staff and kind not in CUSTOMER_KINDS:
        raise HTTPException(status_code=403, detail="The customer side uploads only its signed scan")
    uploaded = form.get("file")
    if uploaded is None or isinstance(uploaded, str):
        raise HTTPException(status_code=400, detail="No file uploaded")
    name = str(uploaded.filename or "contract.pdf")
    allowed = SCAN_EXT if kind in SCAN_KINDS else DOC_EXT        # a signed scan is a PDF or a picture, never a DOCX
    if not name.lower().endswith(allowed):
        raise HTTPException(status_code=422, detail="Allowed files: " + ", ".join(allowed))
    try:
        note = models.clean_document_label(form.get("note") or "", _NOTE_MAX, "note")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    seen = _seen_revision(form.get("revision"))
    seen_terms = _seen_terms(form.get("terms_digest"))
    early = load(project_id)                                               # early answers; checked again under the lock below
    if _stale(early, seen, seen_terms):
        raise HTTPException(status_code=409, detail=_stale(early, seen, seen_terms))
    if kind not in _upload_kinds(early, True):
        raise HTTPException(status_code=409, detail=_refusal(early, kind))
    file_id = secrets.token_urlsafe(16)

    def chunks() -> Iterator[bytes]:
        while True:
            block = uploaded.file.read(_CHUNK)
            if not block:
                return
            yield block

    try:
        size, sha256 = await run_in_threadpool(_save_stream, project_id, file_id, chunks(), MAX_BYTES)
    except ValueError as exc:
        if str(exc) == "too_large":
            raise HTTPException(status_code=413, detail="The file is larger than the allowed %d MB" % (MAX_BYTES // (1024 * 1024)))
        if str(exc) == "empty":
            raise HTTPException(status_code=400, detail="Empty file")
        raise HTTPException(status_code=400, detail="Invalid upload")
    if not await run_in_threadpool(content_matches, files_dir(project_id) / file_id, name):
        (files_dir(project_id) / file_id).unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="The file is not a readable %s" % Path(name).suffix.lstrip(".").upper())
    row = {"id": file_id, "kind": kind, "name": name[:200], "note": note, "size": size, "sha256": sha256, "uploaded_at": _now(),
           "uploader_email": _email(user), "archived": False, "archived_at": None}
    problem: dict = {}

    def apply(doc: dict) -> None:
        stale = _stale(doc, seen, seen_terms)                               # the revision and terms the caller saw are still these
        if stale:
            problem["detail"] = stale
            return
        if kind not in _upload_kinds(doc, True):
            problem["detail"] = _refusal(doc, kind)
            return
        row["revision"] = int(doc["revision"])
        doc["files"].append(row)
        _queue(doc, user, "contract.upload", {"kind": kind, "name": row["name"], "sha256": sha256, "size": size,
                                              "revision": row["revision"]}, project_id)
        if kind in SCAN_KINDS:
            # a scan is NOT a signature: it awaits the MOTRES check, and a person confirms (or rejects) exactly this file
            doc["pending_scan"] = {**_ref(row), "revision": row["revision"]}
            doc["history"].append(_status_row("scan_uploaded", user, row["revision"], [_ref(row)]))

    try:
        doc = _mutate(project_id, apply)
    except BaseException:
        (files_dir(project_id) / file_id).unlink(missing_ok=True)
        raise
    if "detail" in problem:
        (files_dir(project_id) / file_id).unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=problem["detail"])
    _flush_after(project_id)
    return _view(project, user, doc, include_archived=False)


def _refusal(doc: dict, kind: str) -> str:
    status = str(doc.get("status") or "draft")
    if kind in SCAN_KINDS and doc.get("pending_scan"):
        return "A signed scan is awaiting the MOTRES check; it is confirmed or rejected first"
    if kind in TERMS_KINDS:
        return "The terms are frozen once the contract is sent; start a new revision to change them"
    if kind == "signed_customer":
        return "The customer's signed scan is uploaded when the contract is sent" if status == "draft" else "The customer has signed this revision already"
    if kind == "signed_motres":
        return "The MOTRES scan follows the confirmed customer scan" if status in ("draft", "sent") else "MOTRES has signed this revision already"
    return "Not allowed now"


@router.post("/{project_id}/contract/status")
async def set_status(request: Request, project_id: str):
    """Staff, always with ``"revision"`` = the revision the caller is looking at (409 when it changed meanwhile):
    ``{"status": "sent"}`` (draft -> sent, freezes the revision's agreement and annexes; needs the agreement),
    ``{"new_revision": true}`` (back to draft as revision n+1: the terms may change and need fresh scans), ``{"sample_iteration": 1 | 2}``.
    The two signed states come only from the scans (upload)."""
    user, project = _context(request, project_id)
    if not _staff(user):
        raise HTTPException(status_code=403, detail="Only MOTRES staff change the contract status")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = None
    if not isinstance(body, dict) or not body or set(body) - {"status", "new_revision", "sample_iteration", "revision", "terms_digest"}:
        raise HTTPException(status_code=422, detail="Send status, new_revision and / or sample_iteration, with revision")
    if "sample_iteration" in body and body["sample_iteration"] not in (1, 2):
        raise HTTPException(status_code=422, detail="sample_iteration must be 1 or 2")
    if "status" in body and body["status"] != "sent":
        raise HTTPException(status_code=422, detail="Only \"sent\" is set by hand; the signed states come from the uploaded scans")
    if "new_revision" in body and body["new_revision"] is not True:
        raise HTTPException(status_code=422, detail="new_revision must be true")
    if "status" in body and "new_revision" in body:
        raise HTTPException(status_code=422, detail="Send status or new_revision, not both")
    signing = "status" in body or "new_revision" in body
    seen = _seen_revision(body.get("revision")) if signing else None
    seen_terms = _seen_terms(body.get("terms_digest")) if signing else None
    _flush(project_id)
    problem: dict = {}

    def apply(doc: dict) -> None:
        if seen is not None and _stale(doc, seen, seen_terms):
            problem["detail"] = _stale(doc, seen, seen_terms)
            return
        if body.get("status") == "sent":
            if doc["status"] != "draft":
                problem["detail"] = "Only a draft is sent; start a new revision first"
                return
            terms = _live_terms(doc)
            if not any(f.get("kind") == "agreement" for f in terms):
                problem["detail"] = "Upload the agreement first"
                return
            doc["status"] = "sent"
            doc["sent_files"] = [_ref(f) for f in terms]
            doc["signatures"] = {}
            doc["history"].append(_status_row("sent", user, int(doc["revision"]), doc["sent_files"]))
            _queue(doc, user, "contract.status", {"status": "sent", "revision": int(doc["revision"]), "files": doc["sent_files"]}, project_id)
        if body.get("new_revision"):
            if doc["status"] == "draft":
                problem["detail"] = "This revision is still a draft"
                return
            doc["revision"] = int(doc["revision"]) + 1
            doc["status"] = "draft"
            doc["sent_files"] = []
            doc["signatures"] = {}
            doc.pop("pending_scan", None)
            doc["history"].append(_status_row("draft", user, doc["revision"], []))
            _queue(doc, user, "contract.revision", {"revision": doc["revision"]}, project_id)
        if "sample_iteration" in body:
            doc["sample_iteration"] = body["sample_iteration"]
            _queue(doc, user, "contract.sample_iteration", {"sample_iteration": body["sample_iteration"]}, project_id)

    doc = _mutate(project_id, apply)
    if "detail" in problem:
        raise HTTPException(status_code=409, detail=problem["detail"])
    _flush_after(project_id)
    return _view(project, user, doc, include_archived=False)


async def _review_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Send file_id, sha256, revision and terms_digest")
    return body


@router.post("/{project_id}/contract/confirm")
async def confirm_scan(request: Request, project_id: str):
    """Staff, after opening the scan: ``{"file_id", "sha256", "revision", "terms_digest"}`` of EXACTLY the scan awaiting the check.
    A customer scan makes the revision "signed by customer", the MOTRES countersigned scan "signed by both".  The person who
    confirmed is in the history row.  Anything else - another file, another hash, a revision or terms that changed - is a 409."""
    user, project = _context(request, project_id)
    if not _staff(user):
        raise HTTPException(status_code=403, detail="Only MOTRES staff confirm a signature")
    body = await _review_body(request)
    seen, seen_terms = _seen_revision(body.get("revision")), _seen_terms(body.get("terms_digest"))
    fid, sha = str(body.get("file_id") or ""), str(body.get("sha256") or "")
    _flush(project_id)
    problem: dict = {}

    def apply(doc: dict) -> None:
        stale = _stale(doc, seen, seen_terms)
        pending = doc.get("pending_scan") if isinstance(doc.get("pending_scan"), dict) else None
        if stale:
            problem["detail"] = stale
            return
        if not pending or pending.get("id") != fid or pending.get("sha256") != sha or int(pending.get("revision") or 0) != seen:
            problem["detail"] = "This is not the scan awaiting the check (it was replaced, confirmed or rejected); reload"
            return
        nxt = {("sent", "signed_customer"): "signed_customer", ("signed_customer", "signed_motres"): "signed_both"}.get(
            (doc["status"], pending.get("kind")))
        if not nxt:
            problem["detail"] = "This scan does not fit the contract's status"
            return
        ref = {k: pending.get(k) for k in ("id", "kind", "name", "sha256")}
        doc["status"] = nxt
        doc.setdefault("signatures", {})[pending["kind"]] = ref
        doc.pop("pending_scan", None)
        doc["history"].append(_status_row(nxt, user, int(doc["revision"]), [ref]))
        _queue(doc, user, "contract.status", {"status": nxt, "revision": int(doc["revision"]), "confirmed": ref}, project_id)

    doc = _mutate(project_id, apply)
    if "detail" in problem:
        raise HTTPException(status_code=409, detail=problem["detail"])
    _flush_after(project_id)
    return _view(project, user, doc, include_archived=False)


@router.post("/{project_id}/contract/reject")
async def reject_scan(request: Request, project_id: str):
    """Staff: ``{"file_id", "sha256", "revision", "terms_digest", "reason"}`` - the scan awaiting the check is not a valid signature.
    The status stays where it was, the scan stays in the history (and the files), the reason is recorded."""
    user, project = _context(request, project_id)
    if not _staff(user):
        raise HTTPException(status_code=403, detail="Only MOTRES staff reject a scan")
    body = await _review_body(request)
    seen, seen_terms = _seen_revision(body.get("revision")), _seen_terms(body.get("terms_digest"))
    fid, sha = str(body.get("file_id") or ""), str(body.get("sha256") or "")
    try:
        reason = models.clean_document_label(body.get("reason") or "", _NOTE_MAX, "reason")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")
    _flush(project_id)
    problem: dict = {}

    def apply(doc: dict) -> None:
        stale = _stale(doc, seen, seen_terms)
        pending = doc.get("pending_scan") if isinstance(doc.get("pending_scan"), dict) else None
        if stale:
            problem["detail"] = stale
            return
        if not pending or pending.get("id") != fid or pending.get("sha256") != sha:
            problem["detail"] = "This is not the scan awaiting the check; reload"
            return
        ref = {k: pending.get(k) for k in ("id", "kind", "name", "sha256")}
        doc.pop("pending_scan", None)
        doc["history"].append(_status_row("scan_rejected", user, int(doc["revision"]), [ref], reason=reason))
        _queue(doc, user, "contract.scan_rejected", {"revision": int(doc["revision"]), "scan": ref, "reason": reason}, project_id)

    doc = _mutate(project_id, apply)
    if "detail" in problem:
        raise HTTPException(status_code=409, detail=problem["detail"])
    _flush_after(project_id)
    return _view(project, user, doc, include_archived=False)


@router.patch("/{project_id}/contract/files/{file_id}")
async def patch_file(request: Request, project_id: str, file_id: str):
    """Staff: ``{"archived": true | false}`` (archive / restore).  Nothing is deleted; a file the current revision's signing rests on
    (frozen terms, recorded scans) is not archived."""
    user, project = _context(request, project_id)
    if not _staff(user):
        raise HTTPException(status_code=403, detail="Only MOTRES staff archive contract files")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = None
    if not isinstance(body, dict) or set(body) != {"archived"} or not isinstance(body["archived"], bool):
        raise HTTPException(status_code=422, detail='Send {"archived": true | false}')
    _flush(project_id)
    found: dict = {}

    def apply(doc: dict) -> None:
        for f in doc["files"]:
            if f.get("id") == file_id:
                found["row"] = f
                if body["archived"] and file_id in frozen_ids(doc):
                    found["frozen"] = True
                    return
                if bool(f.get("archived")) != body["archived"]:
                    f["archived"] = body["archived"]
                    f["archived_at"] = _now() if body["archived"] else None
                    _queue(doc, user, "contract.archive" if body["archived"] else "contract.restore",
                           {"kind": f.get("kind"), "name": f.get("name"), "sha256": f.get("sha256")}, project_id)

    doc = _mutate(project_id, apply)
    if "row" not in found:
        raise HTTPException(status_code=404, detail="File not found")
    if found.get("frozen"):
        raise HTTPException(status_code=409, detail="The signing of this revision rests on this file; start a new revision first")
    _flush_after(project_id)
    return _view(project, user, doc, include_archived=True)


@router.get("/{project_id}/contract/files/{file_id}/download")
def download_file(request: Request, project_id: str, file_id: str):
    user, _project = _context(request, project_id)
    staff = _staff(user)
    row = next((f for f in load(project_id)["files"] if f.get("id") == file_id and (staff or not f.get("archived"))), None)
    path = _file_path(project_id, file_id) if row else None
    if row is None or path is None:
        raise HTTPException(status_code=404, detail="File not found")
    from motor_ai_sim.development.documents import _log_download        # the one traced download log of project files
    _log_download(request, user, project_id, kind="project_contract", file=str(row.get("name") or ""), source_file=file_id,
                  sha256=str(row.get("sha256") or ""), size=int(row.get("size") or 0))
    store.append_audit(project_id, _email(user), "contract.download", "contract", project_id,      # a read: no file without its row
                       {"kind": row.get("kind"), "name": row.get("name")})
    return FileResponse(path, media_type="application/octet-stream", filename=str(row.get("name") or "contract.pdf"),
                        headers={"Cache-Control": "no-store"})
