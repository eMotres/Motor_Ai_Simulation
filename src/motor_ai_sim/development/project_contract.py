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
supply chain is internal and lives in the server's SHARED store (``<shared root>/contract/supply_scheme_staff.json``, the same
place on every server; the private data checkout as the development fallback), never in this public repository.  Admins upload
it through ``PUT /api/contract-scheme`` (checked against :func:`validate_scheme`); every MOTRES staff member (admin or engineer)
reads it, in the section and through ``GET /api/contract-scheme``; the customer side and the factory never.  Without it the
staff view says it is not configured.  Annex A generation waits for the guaranteed-values task.
"""
from __future__ import annotations

import contextlib
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
from fastapi.responses import FileResponse, Response

from motor_ai_sim import users
from motor_ai_sim.development import access, contract_templates, models, store
from motor_ai_sim.development.file_structure import DOC_EXT, SCAN_EXT, content_matches
from motor_ai_sim.development.project_uploads import bounded_form, flush_events, queue_event

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["development-contract"])
#: the contract schemes (staff, customer), managed by admins (coordinator 2026-11-22: no file is placed on a server by hand)
scheme_router = APIRouter(prefix="/api/contract-scheme", tags=["contract-scheme"])

CONTRACT_JSON = "contract/contract.json"
FILES_DIR = "contract/files"
STAFF_SCHEME = ("config", "contract", "supply_scheme_staff.json")
#: the two stored schemes (owner 2026-11-22: a text change of the customer view needs no deploy): file name per kind
SCHEME_FILES = {"staff": "supply_scheme_staff.json", "customer": "scheme_customer.json"}
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


def contract_store_root() -> Optional[Path]:
    """Where the section's server-side documents live (coordinator 2026-11-22): the SHARED root, like the object registry -
    ``<shared root>/contract/`` (override ``MOTRES_CONTRACT_STORE_ROOT``) - the same place on every server, never this public
    repository.  None when no shared store is configured (then the private checkout is the development fallback)."""
    env = (os.environ.get("MOTRES_CONTRACT_STORE_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    # only a CONFIGURED shared store: SHARED_ROOT (set on both production servers).  shared_root() alone - and with only
    # WORKSPACES_ROOT set - falls back to the process config directory, which is not a store; then the private checkout serves
    if not (os.environ.get("SHARED_ROOT") or "").strip():
        return None
    try:
        from motor_ai_sim.workspace import shared_root
        return Path(str(shared_root())) / "contract"
    except Exception:                                       # noqa: BLE001 - no workspace layer in this process
        return None


def scheme_path(kind: str = "staff") -> Optional[Path]:
    """A stored scheme (``staff`` | ``customer``): the shared-root store first, then the private data checkout (development)."""
    name = SCHEME_FILES[kind]
    base = contract_store_root()
    if base is not None and (base / name).is_file():
        return base / name
    from motor_ai_sim.private_data import private_path
    return private_path("config", "contract", name)


def stored_scheme(kind: str = "staff") -> Optional[dict]:
    """A stored scheme, or None when there is none (or it does not pass :func:`validate_scheme`) - the customer view then
    shows the built-in one of the page, the staff view "not configured"."""
    path = scheme_path(kind)
    if path is None:
        return None
    try:
        doc = validate_scheme(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        log.warning("contract: %s scheme unreadable (%s)", kind, exc)
        return None
    if doc.get("audience", kind) != kind:                  # a file marked for the other audience is never served here
        log.error("contract: the stored %s scheme is marked for audience %r - not served", kind, doc.get("audience"))
        return None
    return doc


def staff_scheme_path() -> Optional[Path]:
    return scheme_path("staff")


def staff_scheme() -> Optional[dict]:
    """The internal supply-chain scheme, or None when it is not configured."""
    return stored_scheme("staff")


_SCHEME_TEXT_MAX = 200
_SCHEME_KINDS = ("money", "goods", "documents")


def _scheme_text(v: Any, where: str) -> Any:
    if isinstance(v, str) and len(v) <= _SCHEME_TEXT_MAX:
        return v
    if isinstance(v, dict) and v and set(v) <= {"en", "zh"} and all(isinstance(x, str) and len(x) <= _SCHEME_TEXT_MAX for x in v.values()):
        return {k: v[k] for k in ("en", "zh") if k in v}
    raise ValueError(f"{where}: a text or {{en, zh}} of at most {_SCHEME_TEXT_MAX} characters")


def _num(v: Any, lo: float, hi: float, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"{where}: a number from {lo} to {hi}")
    return v


def validate_scheme(doc: Any) -> dict:
    """The contract schemes' schema, staff and customer alike (the shape ``web/src/components/contract/contractModel.ts``
    draws): ``nodes`` (1..20: id, label, sub?, x, y, w? / h? of at least 24 on the 1000 x 420 grid), ``edges`` (0..40: from /
    to an existing node, kind money | goods | documents, label?, bend?), ``notes`` (0..20 texts), optional ``version`` and
    ``audience`` (customer | staff; required on upload).  Raises ``ValueError`` naming what is wrong; returns only the known
    fields."""
    if not isinstance(doc, dict):
        raise ValueError("the scheme is a JSON object")
    nodes, edges, notes = doc.get("nodes"), doc.get("edges", []), doc.get("notes", [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 20:
        raise ValueError("nodes: a list of 1 to 20 boxes")
    if not isinstance(edges, list) or len(edges) > 40 or not isinstance(notes, list) or len(notes) > 20:
        raise ValueError("edges (at most 40) and notes (at most 20) are lists")
    out_nodes, ids = [], set()
    for i, n in enumerate(nodes):
        where = f"nodes[{i}]"
        if not isinstance(n, dict) or not isinstance(n.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", n["id"]):
            raise ValueError(f"{where}: id of lowercase letters, digits and _")
        if n["id"] in ids:
            raise ValueError(f"{where}: id '{n['id']}' is used twice")
        ids.add(n["id"])
        node = {"id": n["id"], "label": _scheme_text(n.get("label"), where + ".label"),
                "x": _num(n.get("x"), 0, 1000, where + ".x"), "y": _num(n.get("y"), 0, 420, where + ".y")}
        if "sub" in n:
            node["sub"] = _scheme_text(n["sub"], where + ".sub")
        for k, hi in (("w", 1000), ("h", 420)):
            if k in n:
                node[k] = _num(n[k], 24, hi, f"{where}.{k}")    # at least 24: an arrow end stays inside the side's corners
        out_nodes.append(node)
    out_edges = []
    for i, e in enumerate(edges):
        where = f"edges[{i}]"
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in ("from", "to", "kind")) \
                or e["from"] not in ids or e["to"] not in ids or e["kind"] not in _SCHEME_KINDS:
            raise ValueError(f"{where}: from / to name existing nodes, kind is one of {', '.join(_SCHEME_KINDS)}")
        edge = {"from": e["from"], "to": e["to"], "kind": e["kind"]}
        if "label" in e:
            edge["label"] = _scheme_text(e["label"], where + ".label")
        if "bend" in e:
            edge["bend"] = _num(e["bend"], -400, 400, where + ".bend")
        out_edges.append(edge)
    out = {"nodes": out_nodes, "edges": out_edges, "notes": [_scheme_text(t, f"notes[{i}]") for i, t in enumerate(notes)]}
    if "version" in doc:
        out["version"] = _scheme_text(doc["version"], "version")
    if "audience" in doc:
        if doc["audience"] not in SCHEME_FILES:
            raise ValueError("audience: customer or staff")
        out["audience"] = doc["audience"]
    return out


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


def terms_digest(doc: dict, snapshot: Optional[dict] = None) -> str:
    """A short digest of the terms a signature is about: the frozen files (id + sha256) of a sent revision, else the live terms a
    "sent" would freeze.  The page captures it with the revision when a dialog opens; every signing request carries both."""
    return _digest(doc.get("sent_files") if doc.get("status") != "draft" else _terms_to_send(doc, snapshot))


def _digest(refs: Optional[list]) -> str:
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


def _terms_to_send(doc: dict, snapshot: Optional[dict] = None) -> list[dict]:
    """What "sent" freezes: the project's own live terms; with no agreement of its own, the current standard agreement (the
    template) by its version - empty when there is neither."""
    refs = [_ref(f) for f in _live_terms(doc)]
    if not any(r.get("kind") == "agreement" for r in refs):
        if snapshot is None:
            snapshot = contract_templates.snapshot()
        tpl = snapshot.get("agreement")                  # None also when the store is damaged: fail closed, nothing to send
        if tpl is not None:
            refs.insert(0, contract_templates.ref(tpl))
    return refs


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
    snap = contract_templates.snapshot()                 # ONE catalogue read: what the page shows is what its digest is about
    templates = contract_templates.summary(snap)
    status = contract.get("status", "draft")
    return {"status": status, "revision": int(contract.get("revision") or 1), "history": history,
            "files": [_public_file(f, staff, frozen) for f in files], "current": current,
            "sample_iteration": int(contract.get("sample_iteration") or 1),
            "step": current_step(project, contract), "steps": list(STEPS), "company": company_state(project),
            "staff": staff, "can_manage": staff, "upload_kinds": _upload_kinds(contract, staff),
            "terms_digest": terms_digest(contract, snap), "pending_scan": pending,
            "staff_scheme": staff_scheme() if staff else None,
            "customer_scheme": stored_scheme("customer"),       # None: the page shows its built-in customer view
            # the standard documents of every project (owner 2026-11-22), read-only here; what a sent revision was made from
            "templates": templates,
            "template_only": status == "draft" and bool(templates.get("agreement")) and not any(
                f.get("kind") in TERMS_KINDS and not f.get("archived") for f in contract.get("files", []) if isinstance(f, dict)),
            "sent_files": contract.get("sent_files") or []}


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
    try:
        ok = await run_in_threadpool(content_matches, files_dir(project_id) / file_id, name)
    except Exception:                                       # noqa: BLE001 - any validation failure is invalid content
        ok = False
    if not ok:
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
        if body.get("status") == "sent":
            if doc["status"] != "draft":
                problem["detail"] = "Only a draft is sent; start a new revision first"
                return
            # ONE reference set, resolved once under the template lock: the same set is checked against what the caller saw
            # and frozen - a template published in between can never be sent unseen
            refs = _terms_to_send(doc)
            if int(doc["revision"]) != seen or _digest(refs) != seen_terms:
                problem["detail"] = STALE % int(doc["revision"])
                return
            if not any(r.get("kind") == "agreement" for r in refs):
                problem["detail"] = "There is no agreement: upload the project's own or the standard one first"
                return
            for r in refs:
                if r.get("template"):
                    try:
                        contract_templates.verified_bytes(r)          # the bytes that are frozen are really there
                    except LookupError as exc:
                        problem["detail"] = str(exc)
                        return
        elif seen is not None and _stale(doc, seen, seen_terms):
            problem["detail"] = _stale(doc, seen, seen_terms)
            return
        if body.get("status") == "sent":
            doc["status"] = "sent"
            doc["sent_files"] = refs
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

    # the send resolves its one reference set under the template store's lock: no publication lands in between
    with contract_templates.lock() if body.get("status") == "sent" else contextlib.nullcontext():
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


@router.get("/{project_id}/contract/template/{kind}/download")
def download_template(request: Request, project_id: str, kind: str, version: Optional[str] = None, sha256: Optional[str] = None):
    """A standard document of the section, for whoever reads it; traced like every contract file.

    * ``?sha256=`` (with ``version``): a document THIS project's revisions were sent with, identified by its frozen blob hash -
      served from exactly that frozen reference (the history's buttons);
    * ``?version=`` alone: that version as the catalogue lists it now (the section's current-template button);
    * neither: the current version.
    The bytes are checked against the SHA-256; a missing or changed file is refused, never replaced by another one."""
    user, _project = _context(request, project_id)
    if kind not in contract_templates.KINDS:
        raise HTTPException(status_code=404, detail="File not found")
    if sha256:
        contract = load(project_id)
        frozen = [r for r in (contract.get("sent_files") or []) + [f for h in contract.get("history") or [] for f in h.get("files") or []]
                  if isinstance(r, dict) and r.get("template") and r.get("kind") == kind and r.get("sha256") == sha256
                  and (version is None or r.get("version") == version)]
        row = frozen[0] if frozen else None
    else:
        try:
            row = contract_templates.find(kind, version) if version else contract_templates.current(kind)
        except contract_templates.TemplateStoreDamaged:
            raise HTTPException(status_code=503, detail="The standard contract documents need repair on this server")
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    try:
        data = contract_templates.verified_bytes(row)
    except LookupError as exc:
        log.error("contract templates: %s (project %s)", exc, project_id)
        raise HTTPException(status_code=409, detail=str(exc))
    from motor_ai_sim.development.documents import _log_download
    _log_download(request, user, project_id, kind="project_contract_template", file=str(row.get("name") or ""),
                  source_file=f"template:{kind}:{row.get('version')}", sha256=str(row.get("sha256") or ""), size=len(data))
    store.append_audit(project_id, _email(user), "contract.template_download", "contract", project_id,
                       {"kind": kind, "version": row.get("version"), "sha256": row.get("sha256")})
    from urllib.parse import quote
    name = str(row.get("name") or "agreement.pdf")
    return Response(content=data, media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
            "".join(c for c in name if c.isascii() and c not in '"\\') or "template", quote(name)),
        "Cache-Control": "no-store"})


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


# ── the contract schemes: staff read the staff one, every section reader the customer one, admins upload ─────────────────────────────────────────────────────────────

SCHEME_MAX_BYTES = 256 * 1024


def _scheme_reader(request: Request) -> dict:
    """MOTRES staff (global admin / engineer) read the scheme - the same people the project section shows it to."""
    user = access.require_signed_in(request)
    if not _staff(user):
        raise HTTPException(status_code=403, detail="Only MOTRES staff read the staff scheme")
    return user


def _scheme_admin(request: Request) -> dict:
    user = access.require_signed_in(request)
    if str(user.get("role") or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="Only MOTRES admins upload a contract scheme")
    return user


def write_strict(path: Path, data: bytes) -> None:
    """Atomic or nothing: a new file beside ``path`` (exclusive create), flushed and synced, then ``os.replace``.  No in-place
    fallback - any failure removes the new file and raises ``OSError`` (the route answers 503)."""
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


def _keep_previous(path: Path) -> None:
    """The previous file beside it as ``<name>.<time>-<random>.json``, created exclusively (never overwritten)."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name("%s.%s-%s.json" % (path.stem, stamp, secrets.token_hex(4)))
    with open(backup, "xb") as fh:
        fh.write(path.read_bytes())
        fh.flush()
        os.fsync(fh.fileno())


def _kind(kind: str) -> str:
    if kind not in SCHEME_FILES:
        raise HTTPException(status_code=422, detail="kind is staff or customer")
    return kind


@scheme_router.get("")
def get_scheme(request: Request, kind: str = "staff"):
    """Where a scheme is and what it holds.  ``staff``: MOTRES staff, the read is in the admin audit (who and the route, not the
    content).  ``customer``: every signed-in account but a factory one (it is what every Contract section shows; no prices)."""
    kind = _kind(kind)
    if kind == "staff":
        user = _scheme_reader(request)
        from motor_ai_sim import admin_audit as _AA
        _AA.record(_email(user), "contract.scheme_read", "GET /api/contract-scheme")
    else:
        user = access.require_signed_in(request)
        if str(user.get("role") or "").lower() == "oem":
            raise HTTPException(status_code=403, detail="Not available")
    path = scheme_path(kind)
    base = contract_store_root()
    out = {"kind": kind, "scheme": stored_scheme(kind), "can_upload": str(user.get("role") or "").lower() == "admin"}
    if _staff(user):                                        # where it is stored is for staff only
        out.update(store=str(base or ""), source="shared" if path and base and path.parent == base else ("private" if path else None))
    return out


@scheme_router.put("")
async def put_scheme(request: Request, replace_damaged: bool = False, kind: str = "staff"):
    """A contract scheme (``kind`` = staff | customer) as JSON (:func:`validate_scheme`), admins only, written atomically to the
    shared store; the previous file is kept beside it.  The file must say whom it is for (``"audience"``) and that must be
    ``kind``: the two share one schema, so a staff scheme can never be published to the customer view by mistake (422).
    A stored file that is damaged is not replaced silently: 409 unless ``?replace_damaged=true`` (its bytes
    are kept as the previous version either way).  The body is capped while it arrives (413).  Admin audit ``contract.scheme``."""
    user = _scheme_admin(request)
    kind = _kind(kind)
    base = contract_store_root()
    if base is None:
        raise HTTPException(status_code=503, detail="This server has no shared store for the contract documents")
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > SCHEME_MAX_BYTES:
            raise HTTPException(status_code=413, detail="The scheme is larger than 256 KB")
    try:
        scheme = validate_scheme(json.loads(bytes(raw).decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"The scheme is not valid: {exc}")
    if scheme.get("audience") != kind:
        raise HTTPException(status_code=422, detail=(
            f'This file is for audience {scheme.get("audience")!r}, not {kind!r}: add "audience": "{kind}" only if it is meant for '
            + ("every reader of the Contract section (customers)" if kind == "customer" else "MOTRES staff")))
    from motor_ai_sim import json_store
    path = base / SCHEME_FILES[kind]
    try:
        base.mkdir(parents=True, exist_ok=True)
        with json_store.lock_for(path):
            if path.is_file():
                try:
                    validate_scheme(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, UnicodeDecodeError, ValueError):
                    if not replace_damaged:
                        raise HTTPException(status_code=409, detail=(
                            f"The stored {kind} scheme is damaged; it is kept as it is. Upload again with replace_damaged=true to "
                            "replace it (its bytes are kept as the previous version)"))
                _keep_previous(path)
            write_strict(path, (json.dumps(scheme, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        log.error("contract: %s scheme not writable (%s)", kind, exc)
        raise HTTPException(status_code=503, detail=f"The {kind} scheme cannot be written on this server; nothing was changed")
    from motor_ai_sim import admin_audit as _AA
    _AA.record(_email(user), "contract.scheme", SCHEME_FILES[kind],
               details={"nodes": len(scheme["nodes"]), "edges": len(scheme["edges"]), "version": scheme.get("version"),
                        "replaced_damaged": bool(replace_damaged)})
    return {"ok": True, "scheme": scheme}
