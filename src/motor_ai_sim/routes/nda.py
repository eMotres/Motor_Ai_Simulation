"""Online NDA click-through routes (see :mod:`motor_ai_sim.nda`).

* ``GET  /api/nda/current?lang=``   the current agreement text with its SHA-256 (the hash of exactly the text served)
* ``GET  /api/nda/mine``            the caller's NDA state (source, version, company, record id)
* ``POST /api/nda/sign``            validate, store the append-only record and the signed PDF, unlock the STEP download
* ``GET  /api/nda/mine/pdf``        the caller's signed PDF
* ``GET  /api/nda/signatures``      admin: recent online signatures (company, user, country, free-mail flag)
* ``GET  /api/nda/{record_id}/pdf`` admin: any signed PDF
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

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


@router.get("/mine")
def mine(authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    from motor_ai_sim import users as U
    nda = U.normalize_nda(rec.get("nda"))
    cur = N.current("en")
    return dict(signed=nda["signed"] or bool(who.get("is_admin")), nda=nda,
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
    if errors:
        raise HTTPException(status_code=422, detail={"error": "Check the highlighted fields.", "errors": errors})
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        r = N.sign(user=who["id"], email=who["id"], fields=fields, cur=cur, ip=ip, user_agent=request.headers.get("user-agent", ""))
    except OSError as e:
        log.error("nda: cannot store the signature (%s)", e)
        raise HTTPException(status_code=503, detail="The signature could not be stored; nothing was signed. Try again later.")
    pdf = r.pop("_pdf")
    U.set_nda(who["id"], True, r["iso"][:10], by="online", version=r["version"], company=r["company"], record_id=r["id"])
    AA.record(who["id"], "user.nda", who["id"], subject=who["id"],
              details={"signed": True, "source": "online", "version": r["version"], "record": r["id"]})
    N.mail_copies(r, pdf)
    return {"ok": True, "record_id": r["id"], "version": r["version"], "can_download": True}


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
    return {"signatures": [N.public_record(r) for r in N.read_records(max(1, min(int(limit), 1000)))]}


@router.get("/{record_id}/pdf")
def record_pdf(record_id: str, _admin: dict = Depends(require_admin)):
    from motor_ai_sim import admin_audit as AA
    p = N.pdf_path(record_id)
    if p is None:
        raise HTTPException(status_code=404, detail="No such signed NDA.")
    AA.record(AA.actor_of(_admin), "nda.pdf", record_id, details={"record": record_id})
    return FileResponse(p, media_type="application/pdf", filename="NDA_%s.pdf" % record_id, headers={"Cache-Control": "no-store"})
