"""The project's "Company details" form (owner 2026-11-22): the legal, signatory and delivery data of the CUSTOMER company that
owns the project, filled by the project owner for the contract, the invoice, the customs paper and the shipping destination.

``GET  /api/projects/{id}/company-details``            the stored form (+ a prefill from the caller's NDA / verification record when empty)
``PUT  /api/projects/{id}/company-details``            ``{"values": {field: value}}``: fill / edit; any change clears the confirmation
``POST /api/projects/{id}/company-details/confirm``    "confirmed by <email> on <date>" (project owner or global admin)
``GET  /api/projects/{id}/company-details/sources``    other projects of the caller whose form is filled
``POST /api/projects/{id}/company-details/copy-from``  ``{"project_id"}``: explicit copy of such a form into this one

Access (commercial data, the strictest rule of the project module): the project's role "owner" (the second owner too) and
MOTRES staff (global admin / engineer) read and edit; every other member - engineer, customer - and every outsider get the
same 404 "Company details not found" as an unknown project.  No MCP agent, whoever its owner is (403).  ``no-store``.
Changes go to the project audit (who, when, which fields, old and new values); completion writes one more audit line, which the
owner's daily report shows."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response

from motor_ai_sim import auth
from motor_ai_sim import company_form as CF
from motor_ai_sim.development import access, store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["development-company"])

FILE = "company_details.json"
_NOT_FOUND = "Company details not found"
_NO_STORE = {"Cache-Control": "no-store"}


def _refuse(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail, headers=_NO_STORE)


def access_level(user: Optional[dict], project: dict) -> Optional[str]:
    """``"staff"`` (global admin / engineer), ``"owner"`` (project role owner) or None."""
    if not user:
        return None
    if access.is_engineer(user):
        return "staff"
    if access.membership(str(user.get("email") or ""), project) == "owner":
        return "owner"
    return None


def _caller(request: Request, response: Response, project_id: str) -> tuple:
    response.headers["Cache-Control"] = "no-store"
    if auth._AGENT_USER.get() is not None:
        raise _refuse(403, "Company details are not available to agents.")
    try:
        user = access.require_signed_in(request)
    except HTTPException as exc:
        raise _refuse(exc.status_code, str(exc.detail)) from None
    project = store.get_project(project_id) if store.valid_id(project_id) else None
    level = access_level(user, project) if project else None
    if level is None:
        raise _refuse(404, _NOT_FOUND)
    return user, project, level


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def load(project_id: str) -> dict:
    doc = store.load_json(project_id, FILE, None)
    return doc if isinstance(doc, dict) else CF.empty_doc()


def _view(project_id: str, user: dict, level: str) -> dict:
    doc = load(project_id)
    out = CF.public_view(CF.PROJECT_FORM, doc)
    out.update({"fields": [{"id": f.id, "group": f.group, "kind": f.kind} for f in CF.PROJECT_FORM.fields],
                "required": list(CF.PROJECT_FORM.required), "complete_keys": list(CF.PROJECT_FORM.complete),
                "can_confirm": level == "owner" or str(user.get("role") or "").lower() == "admin"})
    if not out["values"] and level == "owner":
        out["prefill"] = prefill_for(_email(user))
    return out


# -- prefill: the owner's own NDA / verification / directory record ------------------------------------------------------

def prefill_for(email: str) -> dict:
    """Suggested values from what the account already told us (a signed NDA, the verification application, the confirmed
    directory company); never saved by itself, the form shows them and the owner saves."""
    raw: dict = {}

    def take(key: str, value) -> None:
        if key not in raw and isinstance(value, str) and value.strip():
            raw[key] = value.strip()

    try:
        from motor_ai_sim import nda
        rec = next((r for r in nda.read_records(5000) if str(r.get("email") or "").lower() == email), None)
        if rec:
            for src, dst in (("company", "legal_name"), ("reg_no", "reg_no"), ("country", "country"), ("address", "reg_address"),
                             ("signatory", "signatory_name"), ("title", "signatory_title"), ("email", "signatory_email")):
                take(dst, rec.get(src))
    except Exception:                                             # noqa: BLE001 - a missing record never blocks the form
        log.warning("company details: NDA prefill of %s failed", email, exc_info=True)
    try:
        from motor_ai_sim.verification import store as VS
        case = VS.latest_of(email)
        app = (case or {}).get("application") or {}
        for src, dst in (("legal_name", "legal_name"), ("reg_no", "reg_no"), ("vat_no", "vat_no"), ("country", "country"),
                         ("address", "reg_address"), ("applicant_name", "signatory_name"), ("title", "signatory_title"),
                         ("work_email", "signatory_email")):
            take(dst, app.get(src))
    except Exception:                                             # noqa: BLE001
        log.warning("company details: verification prefill of %s failed", email, exc_info=True)
    try:
        from motor_ai_sim import platform_agreement
        comp = platform_agreement.company_of(email) or {}
        for src, dst in (("name", "legal_name"), ("reg_no", "reg_no"), ("country", "country"), ("address", "reg_address")):
            take(dst, comp.get(src))
    except Exception:                                             # noqa: BLE001
        log.warning("company details: directory prefill of %s failed", email, exc_info=True)
    fields = CF.PROJECT_FORM.by_id()
    out = {}
    for key, value in raw.items():
        try:
            cleaned = CF._clean(fields[key], value)
        except CF.CompanyFormError:
            continue
        if cleaned is not None:
            out[key] = cleaned
    return out


# -- write ----------------------------------------------------------------------------------------------------------------

def _save(project_id: str, user: dict, patch: dict, action: str = "company.update", extra: Optional[dict] = None) -> dict:
    result: dict = {}

    def mutator(doc: dict) -> None:
        base = dict(doc) if doc else CF.empty_doc()
        try:
            new, changes = CF.apply_patch(CF.PROJECT_FORM, base, patch, _email(user))
        except CF.CompanyFormError as exc:
            raise HTTPException(status_code=422, detail=str(exc), headers=_NO_STORE) from None
        result["changes"] = changes
        result["first_completed"] = bool(changes) and base.get("completed_at") is None and new.get("completed_at") is not None
        if changes:
            doc.clear()
            doc.update(new)

    store.mutate_json(project_id, FILE, mutator, default={})
    changes = result.get("changes") or {}
    if changes:
        details = CF.audit_changes(CF.PROJECT_FORM, changes)
        details.update(extra or {})
        store.append_audit(project_id, _email(user), action, "company", project_id, details)
        if result.get("first_completed"):
            store.append_audit(project_id, _email(user), "company.completed", "company", project_id, {})
    return result


@router.get("/{project_id}/company-details")
def get_company_details(project_id: str, request: Request, response: Response):
    user, _project, level = _caller(request, response, project_id)
    return _view(project_id, user, level)


@router.put("/{project_id}/company-details")
async def put_company_details(project_id: str, request: Request, response: Response):
    user, _project, level = _caller(request, response, project_id)
    try:
        body = await request.json()
    except ValueError:
        raise _refuse(422, "A JSON body {values: {...}} is required") from None
    if not isinstance(body, dict) or not isinstance(body.get("values"), dict):
        raise _refuse(422, "A JSON body {values: {...}} is required")
    _save(project_id, user, body["values"])
    return _view(project_id, user, level)


@router.post("/{project_id}/company-details/confirm")
def confirm_company_details(project_id: str, request: Request, response: Response):
    user, _project, level = _caller(request, response, project_id)
    if not (level == "owner" or str(user.get("role") or "").lower() == "admin"):
        raise _refuse(403, "Only the project owner or an admin confirms the company details")
    out: dict = {}

    def mutator(doc: dict) -> None:
        try:
            new = CF.confirm(CF.PROJECT_FORM, doc or CF.empty_doc(), _email(user))
        except CF.CompanyFormError as exc:
            out["error"] = str(exc)
            return
        doc.clear()
        doc.update(new)

    store.mutate_json(project_id, FILE, mutator, default={})
    if out.get("error"):
        raise _refuse(422, out["error"])
    store.append_audit(project_id, _email(user), "company.confirm", "company", project_id, {})
    return _view(project_id, user, level)


def _readable_sources(user: dict, exclude: str) -> list:
    rows = []
    for pid in store.membership_projects(_email(user)):
        if pid == exclude:
            continue
        project = store.get_project(pid)
        if not project or access_level(user, project) is None:
            continue
        values = load(pid).get("values") or {}
        if values:
            rows.append({"project_id": pid, "title": str(project.get("title") or pid), "company": str(values.get("legal_name") or "")})
    return rows


@router.get("/{project_id}/company-details/sources")
def company_sources(project_id: str, request: Request, response: Response):
    user, _project, _level = _caller(request, response, project_id)
    return {"sources": _readable_sources(user, project_id)}


@router.post("/{project_id}/company-details/copy-from")
async def copy_company_details(project_id: str, request: Request, response: Response):
    user, _project, level = _caller(request, response, project_id)
    try:
        body = await request.json()
    except ValueError:
        body = None
    source_id = str((body or {}).get("project_id") or "") if isinstance(body, dict) else ""
    source = store.get_project(source_id) if store.valid_id(source_id) else None
    if source is None or source_id == project_id or access_level(user, source) is None:
        raise _refuse(404, _NOT_FOUND)
    values = dict(load(source_id).get("values") or {})
    if not values:
        raise _refuse(404, _NOT_FOUND)
    current = set((load(project_id).get("values") or {}))
    _save(project_id, user, {**{k: None for k in current if k not in values}, **values}, extra={"copied_from": source_id})
    return _view(project_id, user, level)


# -- consumers ---------------------------------------------------------------------------------------------------------------

def company_placeholders(project: dict) -> dict:
    """``{{company_*}}`` values for the contract and invoice templates: always every key (empty text when unknown), the delivery
    address falls back to the registered one."""
    values = (load(str(project.get("id") or "")).get("values") or {}) if store.valid_id(str(project.get("id") or "")) else {}
    out = {f"company_{f.id}": str(values.get(f.id) or "") for f in CF.PROJECT_FORM.fields}
    out["company_delivery_address"] = out["company_delivery_address"] or out["company_reg_address"]
    out["company_delivery_country"] = out["company_delivery_country"] or out["company_country"]
    doc = load(str(project.get("id") or "")) if values else {}
    confirmed = doc.get("confirmed") or {}
    out["company_confirmed_by"] = str(confirmed.get("by") or "")
    out["company_confirmed_on"] = str(confirmed.get("at") or "")[:10]
    return out


def shipping_destination(project_id: str) -> Optional[dict]:
    """``{country, city}`` of the delivery address (the registered country when no separate delivery country), or None."""
    try:
        values = load(project_id).get("values") or {}
        country = str(values.get("delivery_country") or values.get("country") or "").upper()
        if not country:
            return None
        return {"country": country, "city": str(values.get("delivery_city") or "")}
    except Exception:                                               # noqa: BLE001 - never breaks an estimate
        return None
