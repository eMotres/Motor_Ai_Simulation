"""The factory's "Company details" of one production (owner 2026-11-22): the OEM company that builds this motor, entered once by
an OEM member of the production for the contracts (names in Chinese and English, unified social credit code, legal representative,
contact, bank account, the invoicing title of the VAT special invoice).

``GET  /api/production/{die}/{config}/company-details``            the stored record
``PUT  /api/production/{die}/{config}/company-details``            ``{"values": {...}}``: fill / edit (any change clears the confirmation)
``POST /api/production/{die}/{config}/company-details/confirm``    "confirmed by <email> on <date>"
``GET  /api/production/{die}/{config}/company-details/sources``    other productions of the caller whose record is filled
``POST /api/production/{die}/{config}/company-details/copy-from``  ``{"die", "config"}``: explicit copy (one factory, several productions)

Access: any OEM member of THIS production (with the NDA gate every OEM page has), the production's own owner / engineer members and
global admins read and edit.  The customer side - a project member who only derives a read-only "viewer" - and everybody else get
the 404 of an unknown production; no MCP agent (403).  The record carries nothing of the project or its customer, and the
factory's bank details are never written to the audit, a digest or a log: the audit says only WHICH fields changed (and that a
bank field did), never a value."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response

from motor_ai_sim import auth
from motor_ai_sim import company_form as CF
from motor_ai_sim.production import access, models, store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/production", tags=["production-company"])

FILE = "company.json"
_NOT_FOUND = "Production not found."
_NO_STORE = {"Cache-Control": "no-store"}
_FORM = CF.OEM_FORM


def _refuse(status: int, detail: str, headers: Optional[dict] = None) -> HTTPException:
    return HTTPException(status_code=status, detail=detail, headers={**_NO_STORE, **(headers or {})})


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def level_of(user: dict, production: dict) -> Optional[str]:
    """``"staff"`` (global admin, production owner / engineer), ``"oem"`` or None."""
    if access.is_admin(user):
        return "staff"
    role = access.is_member(_email(user), production)
    if role in ("owner", "engineer"):
        return "staff"
    if role == "oem":
        return "oem"
    return None


def _caller(request: Request, response: Response, die: str, config: str) -> tuple:
    response.headers["Cache-Control"] = "no-store"
    if auth._AGENT_USER.get() is not None:
        raise _refuse(403, "Company details are not available to agents.")
    try:
        user = access.require_signed_in(request)
        production = access.load_production(die, config)
    except HTTPException as exc:
        raise _refuse(exc.status_code, str(exc.detail)) from None
    except ValueError:
        production = None
    level = level_of(user, production) if production else None
    if level is None:
        raise _refuse(404, _NOT_FOUND)
    if level == "oem" and not access.is_admin(user):
        denial = access.full_view_denial(request, user, production)
        if denial is not None:
            raise _refuse(403, denial, {"code": "nda_required"})
    return user, production, level


def load(die: str, config: str) -> dict:
    doc = store.load_file(die, config, FILE)
    return doc if isinstance(doc, dict) and doc else CF.empty_doc()


def _view(die: str, config: str) -> dict:
    out = CF.public_view(_FORM, load(die, config))
    out.update({"fields": [{"id": f.id, "group": f.group, "kind": f.kind, "bank": f.bank} for f in _FORM.fields],
                "any_of": [list(g) for g in _FORM.any_of], "complete_keys": list(_FORM.complete)})
    return out


def display_name(die: str, config: str) -> str:
    values = load(die, config).get("values") or {}
    return str(values.get("name_en") or values.get("name_zh") or "")


def payload_flags(role: Optional[str], die: str, config: str) -> dict:
    """What the production payload says about the record: the company name to staff, a reminder flag to the factory's members.
    Nothing for anybody else (the customer-side viewer never gets this)."""
    try:
        if role in ("owner", "engineer", "admin"):
            name = display_name(die, config)
            return {"oem_company": name} if name else {}
        if role == "oem":
            return {} if load(die, config).get("completed_at") else {"oem_company_missing": True}
    except Exception:                                               # noqa: BLE001 - a flag never breaks the production payload
        log.warning("company details flags of %s/%s failed", die, config, exc_info=True)
    return {}


def _save(die: str, config: str, user: dict, patch: dict, extra: Optional[dict] = None) -> None:
    result: dict = {}

    def mutator(doc: dict) -> None:
        base = dict(doc) if doc else CF.empty_doc()
        try:
            new, changes = CF.apply_patch(_FORM, base, patch, _email(user))
        except CF.CompanyFormError as exc:
            raise HTTPException(status_code=422, detail=str(exc), headers=_NO_STORE) from None
        result["changes"] = changes
        result["first_completed"] = bool(changes) and base.get("completed_at") is None and new.get("completed_at") is not None
        if changes:
            doc.clear()
            doc.update(new)

    store.mutate_file(die, config, FILE, mutator)
    changes = result.get("changes") or {}
    if changes:
        snap = {"fields": sorted(changes), **(extra or {})}
        if any(_FORM.by_id()[k].bank for k in changes):
            snap["bank_changed"] = True
        store.append_audit(die, config, models.audit_entry(_email(user), "update", "company", "", snap))
        if result.get("first_completed"):
            store.append_audit(die, config, models.audit_entry(_email(user), "company.completed", "company", "", {}))


@router.get("/{die}/{config}/company-details")
def get_company_details(die: str, config: str, request: Request, response: Response):
    _caller(request, response, die, config)
    return _view(die, config)


@router.put("/{die}/{config}/company-details")
async def put_company_details(die: str, config: str, request: Request, response: Response):
    user, _production, _level = _caller(request, response, die, config)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("values"), dict):
        raise _refuse(422, "A JSON body {values: {...}} is required")
    _save(die, config, user, body["values"])
    return _view(die, config)


@router.post("/{die}/{config}/company-details/confirm")
def confirm_company_details(die: str, config: str, request: Request, response: Response):
    user, _production, _level = _caller(request, response, die, config)
    out: dict = {}

    def mutator(doc: dict) -> None:
        try:
            new = CF.confirm(_FORM, dict(doc) if doc else CF.empty_doc(), _email(user))
        except CF.CompanyFormError as exc:
            out["error"] = str(exc)
            return
        doc.clear()
        doc.update(new)

    store.mutate_file(die, config, FILE, mutator)
    if out.get("error"):
        raise _refuse(422, out["error"])
    store.append_audit(die, config, models.audit_entry(_email(user), "company.confirm", "company", "", {}))
    return _view(die, config)


def _sources(user: dict, request: Request, die: str, config: str) -> list:
    from motor_ai_sim.production import routes
    rows = []
    for rec in routes._production_list_for(_email(user), access.is_admin(user)):
        d, c = str(rec.get("die") or ""), str(rec.get("config") or "")
        if (d, c) == (die, config):
            continue
        try:
            prod = access.load_production(d, c)
        except ValueError:
            continue
        if not prod or level_of(user, prod) is None:
            continue
        if level_of(user, prod) == "oem" and not access.is_admin(user) and access.full_view_denial(request, user, prod) is not None:
            continue
        if load(d, c).get("values"):
            rows.append({"die": d, "config": c, "company": display_name(d, c)})
    return rows


@router.get("/{die}/{config}/company-details/sources")
def company_sources(die: str, config: str, request: Request, response: Response):
    user, _production, _level = _caller(request, response, die, config)
    return {"sources": _sources(user, request, die, config)}


@router.post("/{die}/{config}/company-details/copy-from")
async def copy_company_details(die: str, config: str, request: Request, response: Response):
    user, _production, _level = _caller(request, response, die, config)
    try:
        body = await request.json()
    except ValueError:
        body = None
    src = (str((body or {}).get("die") or ""), str((body or {}).get("config") or "")) if isinstance(body, dict) else ("", "")
    if src == (die, config) or src not in {(r["die"], r["config"]) for r in _sources(user, request, die, config)}:
        raise _refuse(404, _NOT_FOUND)
    values = dict(load(*src).get("values") or {})
    current = set(load(die, config).get("values") or {})
    _save(die, config, user, {**{k: None for k in current if k not in values}, **values}, extra={"copied": True})
    return _view(die, config)


# -- consumers ---------------------------------------------------------------------------------------------------------------

def oem_placeholders(die: str, config: str) -> dict:
    """``{{oem_*}}`` values for the purchase-terms template: every key, empty text when unknown.  Bank fields are included for the
    template only when the caller asks for the payment clause - they are real values, so keep the result out of logs."""
    doc = load(die, config)
    values = doc.get("values") or {}
    out = {}
    for f in _FORM.fields:
        v = values.get(f.id)
        out[f"oem_{f.id}"] = ("yes" if v else "no" if v is False else "") if f.kind == "bool" else str(v or "")
    confirmed = doc.get("confirmed") or {}
    out["oem_confirmed_by"] = str(confirmed.get("by") or "")
    out["oem_confirmed_on"] = str(confirmed.get("at") or "")[:10]
    return out
