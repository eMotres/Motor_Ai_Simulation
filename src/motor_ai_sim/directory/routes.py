"""Admin routes for Companies & People Directory.

Contract: BRIEF_companies_people_catalog.md
- Admin only (require_admin).
- Dedicated routes for companies, people, affiliations, and commercial documents.
- Strict rejection of bank account / SWIFT details (422).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from motor_ai_sim.auth import require_admin
from motor_ai_sim.directory import service
from motor_ai_sim.directory.models import check_no_bank_fields

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/directory", tags=["admin-directory"])


def _actor(admin_user: dict) -> str:
    return str(admin_user.get("email") or admin_user.get("id") or "admin").strip().lower()


# -- Companies -----------------------------------------------------------------

@router.get("/companies")
def list_companies(_admin: dict = Depends(require_admin)):
    return {"companies": service.list_companies()}


@router.post("/companies")
def create_company(body: dict = Body(...), _admin: dict = Depends(require_admin)):
    try:
        check_no_bank_fields(body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not str(body.get("legal_name") or "").strip():
        raise HTTPException(status_code=422, detail="Company legal name is required.")

    actor = _actor(_admin)
    try:
        comp = service.create_company(body, actor=actor)
        return comp
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/companies/{company_id}")
def get_company(company_id: str, _admin: dict = Depends(require_admin)):
    comp = service.get_company(company_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Company not found.")
    return comp


@router.patch("/companies/{company_id}")
def update_company(company_id: str, body: dict = Body(...), _admin: dict = Depends(require_admin)):
    try:
        check_no_bank_fields(body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    actor = _actor(_admin)
    try:
        comp = service.update_company(company_id, body, actor=actor)
        if not comp:
            raise HTTPException(status_code=404, detail="Company not found.")
        return comp
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/companies/{company_id}")
def delete_company(company_id: str, _admin: dict = Depends(require_admin)):
    actor = _actor(_admin)
    ok = service.delete_company(company_id, actor=actor)
    if not ok:
        raise HTTPException(status_code=404, detail="Company not found.")
    return {"ok": True, "id": company_id}


# -- Commercial Documents -----------------------------------------------------

@router.post("/companies/{company_id}/documents")
def add_document(company_id: str, body: dict = Body(...), _admin: dict = Depends(require_admin)):
    try:
        check_no_bank_fields(body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    actor = _actor(_admin)
    try:
        doc = service.add_document(company_id, body, actor=actor)
        return doc
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.patch("/companies/{company_id}/documents/{doc_id}")
def update_document(company_id: str, doc_id: str, body: dict = Body(...), _admin: dict = Depends(require_admin)):
    try:
        check_no_bank_fields(body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    actor = _actor(_admin)
    try:
        doc = service.update_document(company_id, doc_id, body, actor=actor)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found.")
        return doc
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/companies/{company_id}/documents/{doc_id}")
def delete_document(company_id: str, doc_id: str, _admin: dict = Depends(require_admin)):
    actor = _actor(_admin)
    ok = service.delete_document(company_id, doc_id, actor=actor)
    if not ok:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"ok": True, "doc_id": doc_id}


# -- People -------------------------------------------------------------------

@router.get("/people")
def list_people(_admin: dict = Depends(require_admin)):
    return {"people": service.list_people()}


@router.post("/people")
def create_person(body: dict = Body(...), _admin: dict = Depends(require_admin)):
    try:
        check_no_bank_fields(body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not str(body.get("name") or "").strip():
        raise HTTPException(status_code=422, detail="Person name is required.")

    actor = _actor(_admin)
    person = service.create_person(body, actor=actor)
    return person


@router.get("/people/{person_id}")
def get_person(person_id: str, _admin: dict = Depends(require_admin)):
    person = service.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found.")
    return person


@router.patch("/people/{person_id}")
def update_person(person_id: str, body: dict = Body(...), _admin: dict = Depends(require_admin)):
    try:
        check_no_bank_fields(body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    actor = _actor(_admin)
    person = service.update_person(person_id, body, actor=actor)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found.")
    return person


# -- Affiliations -------------------------------------------------------------

@router.get("/affiliations/proposed")
def list_proposed_affiliations(_admin: dict = Depends(require_admin)):
    rows = service.list_proposed_affiliations()
    return {"proposed": rows, "count": len(rows)}


@router.get("/waiting")
def waiting_count(_admin: dict = Depends(require_admin)):
    rows = service.list_proposed_affiliations()
    return {"count": len(rows)}


@router.post("/affiliations")
def create_affiliation(body: dict = Body(...), _admin: dict = Depends(require_admin)):
    company_id = str(body.get("company_id") or "").strip()
    person_id = str(body.get("person_id") or "").strip()
    if not company_id or not person_id:
        raise HTTPException(status_code=422, detail="company_id and person_id are required.")

    actor = _actor(_admin)
    aff = service.create_affiliation(
        company_id=company_id,
        person_id=person_id,
        title=str(body.get("title") or ""),
        from_date=str(body.get("from_date") or ""),
        to_date=str(body.get("to_date") or ""),
        status=str(body.get("status") or "proposed"),
        source=str(body.get("source") or "manual"),
        actor=actor,
    )
    return aff


@router.post("/companies/{company_id}/invite")
def invite_person_by_email(company_id: str, body: dict = Body(...), _admin: dict = Depends(require_admin)):
    email = str(body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="e-mail is required.")

    actor = _actor(_admin)
    try:
        res = service.add_person_to_company_by_email(
            company_id=company_id,
            email=email,
            name=str(body.get("name") or ""),
            title=str(body.get("title") or ""),
            actor=actor,
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/affiliations/{affiliation_id}/confirm")
def confirm_affiliation(affiliation_id: str, _admin: dict = Depends(require_admin)):
    actor = _actor(_admin)
    aff = service.confirm_affiliation(affiliation_id, actor=actor)
    if not aff:
        raise HTTPException(status_code=404, detail="Affiliation not found.")
    return aff


@router.post("/affiliations/{affiliation_id}/end")
def end_affiliation(affiliation_id: str, body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    actor = _actor(_admin)
    to_date = str(body.get("to_date") or "")
    aff = service.end_affiliation(affiliation_id, actor=actor, to_date=to_date)
    if not aff:
        raise HTTPException(status_code=404, detail="Affiliation not found.")
    return aff


@router.delete("/affiliations/{affiliation_id}")
def delete_affiliation(affiliation_id: str, _admin: dict = Depends(require_admin)):
    actor = _actor(_admin)
    ok = service.delete_affiliation(affiliation_id, actor=actor)
    if not ok:
        raise HTTPException(status_code=404, detail="Affiliation not found.")
    return {"ok": True, "id": affiliation_id}


# -- Backfill -----------------------------------------------------------------

@router.post("/backfill/nda")
def trigger_nda_backfill(_admin: dict = Depends(require_admin)):
    actor = _actor(_admin)
    summary = service.backfill_nda(actor=actor)
    return summary
