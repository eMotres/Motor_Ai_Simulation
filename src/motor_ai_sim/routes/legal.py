"""The legal texts and the acceptance of the current version (see :mod:`motor_ai_sim.legal_texts`).

* ``GET  /api/legal/status``           signed in: ``{version, required, accepted_version, accepted_at, agreement: {accepted, mode, accepted_at}}``;
                                       ``required`` is true when the account has no acceptance of the current Terms of Use / Privacy
                                       Policy and is not staff / MOTRES's own (the "updated terms" line of the web)
* ``POST /api/legal/accept``           signed in: ``{accept_terms: true, terms_version, lang, accept_label}`` -> records a Terms-only line,
                                       source ``existing`` (422 ``terms_required`` when the box is not ticked or the version is not the
                                       current one); capacity / country fields of an old client are ignored
* ``POST /api/legal/accept-agreement`` signed in, the Platform Agreement at the START of a simulation: ``{accept_agreement: true,
                                       agreement_version, mode: FREE | PRIVATE, capacity, country, business_name, historical_opt_in, lang,
                                       accept_label, election_label}`` -> records an agreement line, source ``simulation_start``
                                       (422 ``agreement_box_required`` when the box is not ticked or the version is not the current one,
                                       422 ``mode_required``, ``capacity_required``, ``country_required``, ``business_name_required``);
                                       mode PRIVATE also places the no-training hold of ``platform_agreement.record_hold``
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
        raise ApiError(422, "terms_required", "Accept the Terms of Use and the Privacy Policy (version %s)." % LT.TERMS_VERSION)
    if LT.accepted_version(email):                              # a second click or a second tab: nothing more to record
        return {"ok": True, "already": True, "status": LT.status_for(email, role)}
    try:
        r = LT.record_acceptance(email, source="existing", labels=LT.clean_labels(body.get("accept_label")), lang=str(body.get("lang") or "en"),
                                 ip=_ip(request), user_agent=request.headers.get("user-agent", ""))
    except OSError as e:
        log.error("terms: cannot store the acceptance (%s)", e)
        raise HTTPException(status_code=503, detail="The acceptance could not be stored; nothing was accepted. Try again later.")
    return {"ok": True, "record_id": r["id"], "status": LT.status_for(email, role)}


def _ip(request: Request) -> str:
    try:
        from motor_ai_sim.client_ip import from_request
        return from_request(request) or ""
    except Exception:                                           # noqa: BLE001
        return ""


@router.post("/accept-agreement")
def accept_agreement(request: Request, body: dict = Body(default={}), authorization: Optional[str] = Header(default=None)):
    """The Platform Agreement at the start of a simulation (owner 2026-11-01).  The label of the box carries the FREE exchange and the
    irreversibility sentence (or, for PRIVATE, the no-training sentence); the mode is chosen explicitly.  PRIVATE records the acceptance
    with mode PRIVATE AND places the no-training hold on the account at once (platform_agreement holds, agreement section 3.4): the
    account may then simulate, its data are held out of training, and the team hears of it at once (audit line + internal staff mail)."""
    email, role = _caller(authorization)
    body = body if isinstance(body, dict) else {}
    if body.get("accept_agreement") is not True or str(body.get("agreement_version") or "").strip() != LT.AGREEMENT_PUBLIC_VERSION:
        raise ApiError(422, "agreement_box_required", "Tick the box to accept the Platform Agreement (version %s)." % LT.AGREEMENT_PUBLIC_VERSION)
    mode = str(body.get("mode") or "").strip().upper()
    try:
        if mode not in LT.MODES:
            raise LT.CompanyError("mode_required", "Choose FREE, or ask for PRIVATE.")
        LT.clean_acceptance(body.get("country"), body.get("business_name"), body.get("capacity"))     # refuse BEFORE the "already" shortcut
    except LT.CompanyError as e:
        raise ApiError(422, e.code, str(e))
    cur = LT.agreement_line(email)
    if cur is not None and LT.line_mode(cur) == mode:           # a second click or a second tab: nothing more to record
        return {"ok": True, "already": True, "mode": mode, "status": LT.status_for(email, role)}
    try:
        r = LT.record_acceptance(email, source="simulation_start", mode=mode, country=body.get("country"),
                                 business_name=body.get("business_name") or "", capacity=body.get("capacity"),
                                 historical_opt_in=body.get("historical_opt_in") is True,
                                 labels=LT.clean_labels(body.get("accept_label"), body.get("election_label")),
                                 lang=str(body.get("lang") or "en"), ip=_ip(request), user_agent=request.headers.get("user-agent", ""))
    except LT.CompanyError as e:
        raise ApiError(422, e.code, str(e))
    except OSError as e:
        log.error("terms: cannot store the agreement acceptance (%s)", e)
        raise HTTPException(status_code=503, detail="The acceptance could not be stored; nothing was accepted. Try again later.")
    out = {"ok": True, "record_id": r["id"], "mode": mode, "status": LT.status_for(email, role)}
    if mode == "PRIVATE":
        from motor_ai_sim import data_sharing as DS
        from motor_ai_sim import platform_agreement as PA
        out["hold"] = _hold_private(email, r["id"])
        DS.withdraw_for_private(email, ip=_ip(request), record_id=r["id"])
        if not PA.scope_of(email, role)["exempt"]:              # staff's own tries send nothing, like a staff project
            PA.notify_private_request(email, key=PA.hold_key(email=email), company=r.get("business_name"), record_id=r["id"],
                                      version=r["agreement_public_version"], country=r.get("country"), capacity=r.get("capacity"),
                                      source=r["source"], hold=out["hold"])
    return out


def _hold_private(email: str, record_id: str) -> bool:
    """A PRIVATE request puts the account on a no-training hold at once.  The acceptance line itself already closes the account's
    training window (``platform_agreement._terms_window``), so a hold that cannot be written (the legal directory is not installed) is
    logged and reported, never silently dropped."""
    from motor_ai_sim import platform_agreement as PA
    try:
        PA.record_hold(PA.hold_key(email=email), kind="hold", actor=email, note="PRIVATE requested at the start of a simulation",
                       reference=record_id)
        return True
    except (OSError, ValueError) as e:
        log.error("terms: PRIVATE requested by an account but its no-training hold was not written (%s)", e)
        return False


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
