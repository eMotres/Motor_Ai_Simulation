"""Applicant verification routes (see :mod:`motor_ai_sim.verification`).

Signed in:
* ``GET  /api/verification/mine``            the caller's access state (``ok`` | ``nda`` | ``in_progress`` | ``need_info`` | ``denied``),
                                            the personal reply link and what is asked
* ``POST /api/verification/mine/details``    ``{website, purpose, vat_no, work_email}`` - complete or correct the details (re-runs the checks)

By the personal link in the mails (no session; the signed token is the credential; plain HTML, no JavaScript):
* ``GET  /api/verification/reply/{token}``            the reply page
* ``POST /api/verification/reply/{token}``            form: ``action`` = send (details + code to the e-mail) | confirm_email
* ``POST /api/verification/reply/{token}/document``   multipart: ONE pdf / png / jpg

Admin only (every call audited):
* ``GET  /api/admin/verification?status=``            the queue
* ``GET  /api/admin/verification/{case}``             case, evidence, timeline, downloads
* ``POST /api/admin/verification/{case}/approve|deny|request-info|rerun``
* ``GET  /api/admin/verification/{case}/documents/{doc}``   an uploaded document, always as an attachment
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from motor_ai_sim import admin_audit as AA
from motor_ai_sim import auth_email as E
from motor_ai_sim import link_pages as LP
from motor_ai_sim import verification as V
from motor_ai_sim.auth import require_admin
from motor_ai_sim.routes.nda import _caller
from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import pages as PG
from motor_ai_sim.verification import service as SV
from motor_ai_sim.verification import store as ST

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/verification", tags=["verification"])
admin_router = APIRouter(prefix="/api/admin/verification", tags=["verification"])

_HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
}


def _ip(request: Request) -> str:
    try:
        from motor_ai_sim.client_ip import from_request
        return from_request(request) or "?"
    except Exception:                                       # noqa: BLE001
        return "?"


# -- signed in -------------------------------------------------------------------------------------------------------

@router.get("/mine")
def mine(authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    out = V.public_access(who, rec)
    cid = out.get("case")
    case = ST.get(cid) if cid else None
    if case:
        a = case["application"]
        out["details"] = {k: a.get(k, "") for k in ("website", "purpose", "vat_no", "work_email")}
        out["status"] = case["status"] if out["state"] != "ok" else "VERIFIED"
    return out


@router.post("/mine/details")
def mine_details(body: dict = Body(default={}), authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    pa = V.public_access(who, rec)
    cid = pa.get("case")
    if not cid or pa["state"] in ("ok", "nda", "denied"):
        raise HTTPException(status_code=409, detail="There is nothing to complete for this account.")
    fields, errors = V.validate_intake(body, who["id"])
    if errors:
        raise HTTPException(status_code=422, detail={"error": "Check the highlighted fields.", "errors": errors})
    SV.update_details(cid, fields)
    return V.public_access(who, rec)


# -- the reply page --------------------------------------------------------------------------------------------------------

def _case_or_404(token: str, request: Request) -> dict:
    if E.LINK_IP.blocked(_ip(request)):
        raise HTTPException(429, detail="too many requests - try again later")
    cid = SV.case_for_token(token)
    case = ST.get(cid) if cid else None
    if case is None:
        E.LINK_IP.hit(_ip(request))
        raise HTTPException(404, detail="invalid link")
    return case


def _render(request: Request, token: str, case: dict, *, flash: str = "", kind: str = "", pending: str = "", typed: Optional[dict] = None,
            saved: bool = False) -> HTMLResponse:
    case = ST.get(case["id"]) or case
    lang = LP.pick_lang(query=request.query_params.get("lang", ""), stored=case.get("lang") or "",
                        accept_language=request.headers.get("accept-language", ""), email=case.get("user") or "")
    state = SV.public_state(case)
    other = "en" if lang == "zh-CN" else "zh"
    if not pending:                                       # a code still waiting for an address is offered again
        pend = [a for a, p in case["emails"]["pending"].items() if p["expires"] > time.time()]
        pending = pend[-1] if pend else ""
    html = PG.page(lang, case, state, token=token, requests=case.get("requests") or [], flash=flash, flash_kind=kind, pending_email=pending,
                   switch_url=request.url.path + "?lang=" + other, open_url=E.app_url() + ("/zh-cn/" if lang == "zh-CN" else "/"),
                   typed=typed, saved=saved)
    return HTMLResponse(html, headers=_HEADERS)


@router.get("/reply/{token}")
def reply_get(token: str, request: Request):
    try:
        case = _case_or_404(token, request)
    except HTTPException as e:
        if e.status_code == 404:
            return HTMLResponse(PG.bad_link(LP.pick_lang(accept_language=request.headers.get("accept-language", ""))), status_code=404, headers=_HEADERS)
        raise
    return _render(request, token, case)


@router.post("/reply/{token}")
def reply_post(token: str, request: Request, action: str = Form(""), email: str = Form(""), code: str = Form(""), urls: str = Form(""),
               note: str = Form(""), website: str = Form(""), reg_no: str = Form(""), vat_no: str = Form("")):
    try:
        case = _case_or_404(token, request)
    except HTTPException as e:
        if e.status_code == 404:
            return HTMLResponse(PG.bad_link(LP.pick_lang(accept_language=request.headers.get("accept-language", ""))), status_code=404, headers=_HEADERS)
        raise
    if case["status"] == V.DENIED:
        return _render(request, token, case)
    cid = case["id"]
    if action in ("send", "add_email", "reply"):         # "add_email" / "reply": pages rendered before the one-button form
        # ONE button: the links / details typed are stored, and a code goes to the e-mail typed (not again while one is waiting)
        a = case["application"]
        changed = {k: v.strip() for k, v in (("website", website), ("reg_no", reg_no), ("vat_no", vat_no)) if v.strip() and v.strip() != (a.get(k) or "")}
        saved = bool(urls.strip() or note.strip() or changed)
        if saved:
            SV.reply(cid, urls_text=urls, note=note, fields=changed)
        address = email.strip().lower()
        if not address or action == "reply":
            if not saved:                                 # nothing new: the details are on file already, the e-mail is what is missing
                a = case["application"]
                if action == "send" and (a.get("website") or a.get("reg_no") or a.get("vat_no")):
                    return _render(request, token, case, flash="need_email")
                return _render(request, token, case, flash="nothing", kind="err")
            return _render(request, token, case, flash="reply")
        if address in {v["email"] for v in case["emails"]["verified"]}:
            return _render(request, token, case, flash="verified", pending=address, saved=saved)
        p = case["emails"]["pending"].get(address)
        if p and p["expires"] > time.time() and p["attempts"] < C.OTP_MAX_ATTEMPTS:
            return _render(request, token, case, flash="sent", pending=address, saved=saved)
        ok, c = SV.start_email(cid, address)
        if ok:
            return _render(request, token, case, flash=c, pending=address, saved=saved)
        typed = {"email": email} if saved else {"email": email, "urls": urls, "note": note, "website": website, "reg_no": reg_no, "vat_no": vat_no}
        return _render(request, token, case, flash=c, kind="err", typed=typed, saved=saved)
    if action == "confirm_email":
        ok, c = SV.confirm_email(cid, email, code)
        return _render(request, token, case, flash="verified" if ok else c, kind="" if ok else "err", pending="" if ok else email.strip().lower())
    if action == "resend":
        address = email.strip().lower()
        ok, c = SV.resend_email(cid, address)
        case = ST.get(cid) or case
        pending_addr = address or (list(case["emails"]["pending"].keys())[-1] if case["emails"]["pending"] else "")
        if ok:
            return _render(request, token, case, flash="sent", pending=pending_addr)
        return _render(request, token, case, flash=c, kind="err", pending=pending_addr)
    return _render(request, token, case)


@router.post("/reply/{token}/document")
async def reply_document(token: str, request: Request, file: UploadFile = File(...)):
    try:
        case = _case_or_404(token, request)
    except HTTPException as e:
        if e.status_code == 404:
            return HTMLResponse(PG.bad_link(LP.pick_lang(accept_language=request.headers.get("accept-language", ""))), status_code=404, headers=_HEADERS)
        raise
    if case["status"] == V.DENIED:
        return _render(request, token, case)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > C.UPLOAD_MAX_BYTES + 65536:
        return _render(request, token, case, flash="size", kind="err")
    data = await file.read(C.UPLOAD_MAX_BYTES + 1)
    ok, c = SV.add_document(case["id"], file.filename or "", data)
    return _render(request, token, case, flash="doc" if ok else ("docs" if c == "limit" else c), kind="" if ok else "err")


# -- admin -----------------------------------------------------------------------------------------------------------------

def _row(c: dict) -> dict:
    a = c.get("application") or {}
    d = c.get("decision") or {}
    return {"id": c["id"], "user": c["user"], "status": c["status"], "status_reason": c.get("status_reason", ""), "company": a.get("legal_name", ""),
            "country": a.get("country", ""), "applicant": a.get("applicant_name", ""), "title": a.get("title", ""), "website": a.get("website", ""),
            "created_iso": c.get("created_iso", ""), "updated_iso": c.get("updated_iso", ""), "rounds": c.get("rounds", 0),
            "open_replies": c.get("open_replies", 0), "access": c.get("access", {}).get("granted", False), "downloads": c.get("downloads", 0),
            "signals": len(d.get("signals") or []), "company_confirmed": bool((d.get("company") or {}).get("confirmed"))}


@admin_router.get("")
def admin_list(status: str = "", limit: int = 200, admin: dict = Depends(require_admin)):
    st = status.strip().upper()
    if st and st not in V.STATUSES:
        raise HTTPException(422, detail="unknown status")
    rows = [_row(c) for c in ST.list_cases(st)][: max(1, min(int(limit), 1000))]
    counts: dict = {}
    for c in ST.list_cases():
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    counts["waiting"] = SV.count_waiting()
    AA.record(AA.actor_of(admin), "verification.read", "queue", details={"status": st or "all"})
    return {"cases": rows, "counts": counts}


@admin_router.get("/waiting")
def admin_waiting(admin: dict = Depends(require_admin)):
    return {"count": SV.count_waiting()}


@admin_router.get("/{case_id}")
def admin_case(case_id: str, admin: dict = Depends(require_admin)):
    from motor_ai_sim import model3d as M
    from motor_ai_sim import nda as N
    case = ST.get(case_id)
    if case is None:
        raise HTTPException(404, detail="No such case.")
    AA.record(AA.actor_of(admin), "verification.read", case_id, subject=case["user"])
    rid = (case.get("nda") or {}).get("id")
    return {"case": case, "row": _row(case), "events": ST.events_of(case_id), "downloads": M.read_log(500, user=case["user"]),
            "nda": N.public_record(N.get_record(rid)) if rid and N.get_record(rid) else None, "reply_url": SV.reply_url(case_id)}


def _act(case_id: str, admin: dict, action: str, fn, **audit):
    actor = AA.actor_of(admin)
    try:
        case = fn(case_id, actor)
    except SV.ActionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except OSError as e:
        log.error("verification: %s of %s could not be stored (%s)", action, case_id, e)
        raise HTTPException(status_code=503, detail="The action could not be stored; nothing was changed.")
    AA.record(actor, "verification." + action, case_id, subject=(case or {}).get("user", ""), details=audit)
    return {"ok": True, "case": case, "row": _row(case)}


@admin_router.post("/{case_id}/approve")
def admin_approve(case_id: str, body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    reason = str(body.get("reason") or "")
    return _act(case_id, admin, "approve", lambda c, a: SV.admin_approve(c, a, reason), reason=reason)


@admin_router.post("/{case_id}/deny")
def admin_deny(case_id: str, body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    reason = str(body.get("reason") or "")
    note = str(body.get("public_note") or "")
    return _act(case_id, admin, "deny", lambda c, a: SV.admin_deny(c, a, reason, note), reason=reason, public_note=note)


@admin_router.post("/{case_id}/request-info")
def admin_request_info(case_id: str, body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    ask = str(body.get("text") or "")
    return _act(case_id, admin, "request", lambda c, a: SV.admin_request_info(c, a, ask), ask=ask)


@admin_router.post("/{case_id}/rerun")
def admin_rerun(case_id: str, admin: dict = Depends(require_admin)):
    return _act(case_id, admin, "rerun", lambda c, a: SV.admin_rerun(c, a))


@admin_router.get("/{case_id}/documents/{doc_id}")
def admin_document(case_id: str, doc_id: str, admin: dict = Depends(require_admin)):
    hit = SV.document_path(case_id, doc_id)
    if hit is None:
        raise HTTPException(404, detail="No such document.")
    p, d = hit
    AA.record(AA.actor_of(admin), "verification.document", case_id, subject=(ST.get(case_id) or {}).get("user", ""), details={"document": doc_id})
    # always an attachment, never rendered inline; the type is fixed by the checked extension, not by the upload
    media = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}.get(d["ext"], "application/octet-stream")
    return FileResponse(p, media_type=media,
                        headers={"Content-Disposition": 'attachment; filename="%s.%s"' % (doc_id, d["ext"]), "X-Content-Type-Options": "nosniff",
                                 "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; sandbox"})
