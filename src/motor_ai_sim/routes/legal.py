"""The legal texts and the acceptance of the current version (see :mod:`motor_ai_sim.legal_texts`).

* ``GET  /api/legal/status``           signed in: ``{version, required, accepted_version, accepted_at, ...}``; ``required`` is true when the
                                       account has no acceptance of the current texts and is not staff / MOTRES's own
* ``POST /api/legal/accept``           signed in: ``{accept_terms: true, terms_version, country, capacity, business_name, historical_opt_in, lang,
                                       accept_label, election_label}`` -> records source ``existing``
                                       (422 ``terms_required`` when the box is not ticked or the version is not the current one,
                                       422 ``country_required`` without a country from the list, 422 ``capacity_required`` without one of
                                       business | sole_trader | other, 422 ``business_name_required`` for ``business`` without a name)
* ``GET  /api/legal/mine``             signed in: the account's acceptances as shown rows (capacity, business name if any, country, forum)
* ``GET  /api/legal/mine/pdf``         signed in: the newest acceptance as a PDF (no empty company / registration / title / mode rows)
* ``GET  /api/admin/legal/{id}/pdf``   admin: any account acceptance as a PDF
* ``GET  /api/legal/countries``        no sign-in: the country codes of the acceptance form (the NDA / agreement list)
* ``GET  /api/legal/{doc}?lang=``      no sign-in: ``{doc, version, lang, text, sha256}`` for ``terms`` | ``privacy`` | ``platform-agreement`` |
                                       ``schedules`` (404 for an unknown document or language)

Sign-up acceptance itself rides on ``/api/auth/register`` and ``/api/auth/google`` (routes/auth_local.py).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from motor_ai_sim import legal_texts as LT
from motor_ai_sim.api_errors import ApiError
from motor_ai_sim.auth import ANON_OWNER, caller_identity, require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/legal", tags=["legal"])
admin_router = APIRouter(prefix="/api/admin/legal", tags=["legal"])


def _caller(authorization: Optional[str]) -> tuple[str, str]:
    """(e-mail, role) of the signed-in caller; 401 otherwise."""
    who = caller_identity(authorization)
    if not who.get("id") or who["id"] == ANON_OWNER:
        raise HTTPException(status_code=401, detail="Sign in first.")
    from motor_ai_sim import users as U
    rec = U.get_user(who["id"]) or {}
    return str(who["id"]).strip().lower(), "admin" if who.get("is_admin") else str(rec.get("role") or who.get("role") or "")


@router.get("/status")
def status(authorization: Optional[str] = Header(default=None)):
    email, role = _caller(authorization)
    return LT.status_for(email, role)


@router.post("/accept")
def accept(request: Request, body: dict = Body(default={}), authorization: Optional[str] = Header(default=None)):
    email, role = _caller(authorization)
    body = body if isinstance(body, dict) else {}
    if not LT.accepted_now(body.get("accept_terms"), body.get("terms_version")):
        raise ApiError(422, "terms_required", "Tick the box to accept the Terms of Use, the Platform Agreement and the Privacy Policy "
                                              "(version %s)." % LT.TERMS_VERSION)
    try:
        LT.clean_acceptance(body.get("country"), body.get("business_name"), body.get("capacity"))   # refuse BEFORE the "already accepted" shortcut
    except LT.CompanyError as e:
        raise ApiError(422, e.code, str(e))
    if LT.accepted_version(email):                              # a second click or a second tab: nothing more to record
        return {"ok": True, "already": True, "status": LT.status_for(email, role)}
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request) or ""
    except Exception:                                           # noqa: BLE001
        ip = ""
    try:
        r = LT.record_acceptance(email, source="existing", country=body.get("country"), business_name=body.get("business_name") or "",
                                 capacity=body.get("capacity"), historical_opt_in=body.get("historical_opt_in") is True,
                                 labels=LT.clean_labels(body.get("accept_label"), body.get("election_label")),
                                 lang=str(body.get("lang") or "en"), ip=ip, user_agent=request.headers.get("user-agent", ""))
    except LT.CompanyError as e:
        raise ApiError(422, e.code, str(e))
    except OSError as e:
        log.error("terms: cannot store the acceptance (%s)", e)
        raise HTTPException(status_code=503, detail="The acceptance could not be stored; nothing was accepted. Try again later.")
    return {"ok": True, "record_id": r["id"], "status": LT.status_for(email, role)}


@router.get("/mine")
def mine(authorization: Optional[str] = Header(default=None)):
    email, _role = _caller(authorization)
    return {"acceptances": [{"id": r.get("id"), "rows": [{"label": k, "value": v} for k, v in LT.record_view(r)]}
                            for r in reversed(LT.records(email))]}


@router.get("/mine/pdf")
def mine_pdf(authorization: Optional[str] = Header(default=None)):
    email, _role = _caller(authorization)
    rec = LT.latest(email)
    if not rec:
        raise HTTPException(status_code=404, detail="No acceptance on file.")
    return Response(LT.record_pdf(rec), media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="Account_acceptance_%s.pdf"' % rec.get("id", ""), "Cache-Control": "no-store"})


@admin_router.get("/{record_id}/pdf")
def admin_pdf(record_id: str, _admin: dict = Depends(require_admin)):
    from motor_ai_sim import admin_audit as AA
    rec = next((r for r in LT.records() if r.get("id") == record_id), None)
    if rec is None:
        raise HTTPException(status_code=404, detail="No such acceptance.")
    AA.record(AA.actor_of(_admin), "legal.pdf", record_id, details={"record": record_id})
    return Response(LT.record_pdf(rec), media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="Account_acceptance_%s.pdf"' % record_id, "Cache-Control": "no-store"})


@router.get("/countries")
def country_list():
    return {"countries": LT.countries()}


@router.get("/{doc}")
def text(doc: str, lang: str = "en"):
    try:
        return LT.read(doc, lang)
    except LT.UnknownText:
        raise HTTPException(status_code=404, detail="Not found.")
