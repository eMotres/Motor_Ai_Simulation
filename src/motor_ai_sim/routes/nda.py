"""Online NDA click-through routes (see :mod:`motor_ai_sim.nda`).

* ``GET  /api/nda/current?lang=``       EN and zh-CN agreement texts plus the selected-language SHA-256 and both hashes
* ``GET  /api/nda/current/pdf?lang=``   unsigned EN-then-zh-CN specimen PDF for legal review (nothing stored)
* ``GET  /api/nda/mine``                the caller's NDA state (source, version, company, record id)
* ``POST /api/nda/sign``                validate, store the append-only record and the signed PDF, open a verification case (the STEP opens
                                       when the case is VERIFIED, see motor_ai_sim.verification)
* ``GET  /api/nda/mine/pdf``            the caller's signed PDF
* ``GET  /api/nda/signatures``          admin: recent online signatures (company, user, country, free-mail flag)
* ``GET  /api/nda/{record_id}/pdf``     admin: any signed PDF
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response

from motor_ai_sim import nda as N
from motor_ai_sim.auth import ANON_OWNER, caller_identity, require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nda", tags=["nda"])


def _caller(authorization: Optional[str]) -> tuple[dict, dict]:
    who = caller_identity(authorization)
    if not who.get("is_admin") and (not who.get("id") or who["id"] == ANON_OWNER):
        raise HTTPException(status_code=401, detail="Sign in to sign the agreement.")
    from motor_ai_sim import users as U
    return who, U.get_user(who["id"]) or {}


@router.get("/current")
def get_current(lang: str = "en", authorization: Optional[str] = Header(default=None)):
    _caller(authorization)
    cur = N.current(lang)
    if cur is None:
        raise HTTPException(status_code=503, detail="The agreement text is not installed on this server.")
    return cur


@router.get("/current/pdf")
def get_current_pdf(lang: str = "en", authorization: Optional[str] = Header(default=None)):
    _caller(authorization)
    cur = N.current(lang)
    if cur is None:
        raise HTTPException(status_code=503, detail="The agreement text is not installed on this server.")
    pdf = N.build_specimen_pdf(cur)
    fname = "AeroStator_Core_Model_NDA_%s_%s_SPECIMEN.pdf" % (cur["version"], cur["lang"])
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="%s"' % fname,
            "Cache-Control": "no-store",
        },
    )


@router.get("/mine")
def mine(authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    from motor_ai_sim import users as U
    nda = U.normalize_nda(rec.get("nda"))
    cur = N.current("en")
    from motor_ai_sim import verification as V
    return dict(signed=nda["signed"] or bool(who.get("is_admin")), nda=nda, access=V.public_access(who, rec, create=False),
                source=("online" if nda.get("by") == "online" else "admin") if nda["signed"] else None,
                has_pdf=bool(nda.get("record_id") and N.pdf_path(nda["record_id"])),
                current_version=cur["version"] if cur else None, signable=bool(cur and cur["signable"]))


@router.post("/sign")
def sign(request: Request, body: dict = Body(default={}), authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    from motor_ai_sim import users as U
    from motor_ai_sim import admin_audit as AA
    if not rec:
        raise HTTPException(status_code=400, detail="This account has no registry record; sign in with an account.")
    lang = str(body.get("lang") or "en")
    cur = N.current(lang)
    if cur is None:
        raise HTTPException(status_code=503, detail="The agreement text is not installed on this server.")
    if not cur["signable"]:
        raise HTTPException(status_code=409, detail="Online signing is not open yet: the agreement text is a draft or not installed.")
    if str(body.get("version") or "") != cur["version"]:
        raise HTTPException(status_code=409, detail="The agreement was updated (version %s). Reload the page and read it again." % cur["version"])
    old = U.normalize_nda(rec.get("nda"))
    if old["signed"] and old.get("version") == cur["version"]:
        raise HTTPException(status_code=409, detail="You have already signed version %s." % cur["version"])
    fields, errors = N.validate(body)
    from motor_ai_sim import verification as V
    _intake, intake_errors = V.validate_intake(body, who["id"])
    errors = {**intake_errors, **errors}
    if errors:
        raise HTTPException(status_code=422, detail={"error": "Check the highlighted fields.", "errors": errors})
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        r = N.sign(user=who["id"], email=who["id"], fields=fields, cur=cur, ip=ip, user_agent=request.headers.get("user-agent", ""),
                   data_sharing=body.get("data_sharing") is True)
    except OSError as e:
        log.error("nda: cannot store the signature (%s)", e)
        raise HTTPException(status_code=503, detail="The signature could not be stored; nothing was signed. Try again later.")
    pdf = r.pop("_pdf")
    U.set_nda(who["id"], True, r["iso"][:10], by="online", version=r["version"], company=r["company"], record_id=r["id"])
    AA.record(who["id"], "user.nda", who["id"], subject=who["id"],
              details={"signed": True, "source": "online", "version": r["version"], "record": r["id"]})
    N.mail_copies(r, pdf)
    # applicant verification: the signature opens a CASE, it no longer opens the STEP by itself (motor_ai_sim.verification).  A failure
    # here never undoes the signature: the account then shows "we need one more thing" and the case is opened on its first look.
    try:
        V.on_nda_signed(who["id"], r, body, lang)
    except Exception:                                       # noqa: BLE001
        log.exception("nda: the verification case of %s could not be opened", r["id"])
    try:
        from motor_ai_sim.directory.service import ingest_nda_record
        ingest_nda_record(r, intake=body)
    except Exception:                                       # noqa: BLE001
        log.exception("nda: directory ingestion of %s failed", r["id"])
    # the optional dataset consent: its own audit line and user flag; a failure here never undoes the signature (fail-closed:
    # without the audit line the export filter refuses the data)
    from motor_ai_sim import data_sharing as DS
    try:
        ds = DS.set_consent(who["id"], r["data_sharing"]["consent"], source="nda_sign", ip=ip, record_id=r["id"], log_unchanged=True)
    except (OSError, KeyError) as e:
        log.error("nda: dataset consent of %s could not be stored (%s)", r["id"], e)
        ds = DS.state(who["id"])
    from motor_ai_sim import model3d as M
    acc = V.public_access(who, U.get_user(who["id"]) or {})
    return {"ok": True, "record_id": r["id"], "version": r["version"], "can_download": M.can_download(who, U.get_user(who["id"]) or {}),
            "access": acc, "data_sharing": ds}


@router.get("/mine/pdf")
def mine_pdf(authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    from motor_ai_sim import users as U
    rid = U.normalize_nda(rec.get("nda")).get("record_id", "")
    p = N.pdf_path(rid) if rid else None
    if p is None:
        raise HTTPException(status_code=404, detail="You have no signed NDA on file.")
    return FileResponse(p, media_type="application/pdf", filename="NDA_%s.pdf" % rid, headers={"Cache-Control": "no-store"})


@router.get("/signatures")
def signatures(limit: int = 100, _admin: dict = Depends(require_admin)):
    from motor_ai_sim import data_sharing as DS
    from motor_ai_sim import users as U
    rows = [N.public_record(r) for r in N.read_records(max(1, min(int(limit), 1000)))]
    for row in rows:                                         # the consent NOW (the record keeps the one given at signing)
        row["data_sharing_now"] = bool(U.get_data_sharing(row["user"]).get("consent"))
    return {"signatures": rows, "consenting_users": len(DS.consenting_users())}


@router.get("/{record_id}/pdf")
def record_pdf(record_id: str, _admin: dict = Depends(require_admin)):
    from motor_ai_sim import admin_audit as AA
    p = N.pdf_path(record_id)
    if p is None:
        raise HTTPException(status_code=404, detail="No such signed NDA.")
    AA.record(AA.actor_of(_admin), "nda.pdf", record_id, details={"record": record_id})
    return FileResponse(p, media_type="application/pdf", filename="NDA_%s.pdf" % record_id, headers={"Cache-Control": "no-store"})
