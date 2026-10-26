"""AeroStator Platform Services and Data Agreement routes (see :mod:`motor_ai_sim.platform_agreement`).

* ``GET  /api/agreement/status``              the caller's state: enforced?, required?, accepted?, the acceptance in force, the company
                                              facts the text needs (admins also learn whether the approved text is installed)
* ``GET  /api/agreement/text?lang=``          EN and zh-CN texts + hashes (404 for a user while nothing is enforced; admins may read the draft)
* ``POST /api/agreement/accept``              ``{mode: FREE|PRIVATE, signer, title, business: true, authority: true, version, lang,
                                              [company, reg_no, country, address where unknown]}``
* ``GET  /api/agreement/mine/pdf``            the caller's accepted copy
* ``GET  /api/admin/agreement/acceptances``   admin: recent acceptances
* ``GET  /api/admin/agreement/{id}/pdf``      admin: any accepted copy
* ``GET  /api/admin/dataset/exclusions``      admin: what the engineering dataset must not record (PRIVATE clients' data)

Nothing here sends e-mail (owner rule: no outside message without his OK on the text); the admin notice is the audit line
``agreement.accept``, which the live activity feed shows.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse

from motor_ai_sim import platform_agreement as PA
from motor_ai_sim.auth import require_admin
from motor_ai_sim.routes.nda import _caller

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agreement", tags=["agreement"])
admin_router = APIRouter(prefix="/api/admin/agreement", tags=["agreement"])
dataset_router = APIRouter(prefix="/api/admin/dataset", tags=["agreement"])


def _role(who: dict, rec: dict) -> str:
    return "admin" if who.get("is_admin") else str(rec.get("role") or who.get("role") or "")


@router.get("/status")
def status(authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    return PA.status_for(who["id"], _role(who, rec), is_admin=bool(who.get("is_admin")))


@router.get("/text")
def text(lang: str = "en", authorization: Optional[str] = Header(default=None)):
    who, _rec = _caller(authorization)
    cur = PA.current(lang)
    if cur is None or (not PA.enforced() and not who.get("is_admin")):
        raise HTTPException(status_code=404, detail="Not found.")
    return cur


@router.post("/accept")
def accept(request: Request, body: dict = Body(default={}), authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    from motor_ai_sim import admin_audit as AA
    if not PA.enforced():
        raise HTTPException(status_code=409, detail="The agreement is not open yet.")
    role = _role(who, rec)
    sc = PA.scope_of(who["id"], role)
    if sc["exempt"]:
        raise HTTPException(status_code=409, detail="This account does not need to accept the agreement.")
    cur = PA.current(str(body.get("lang") or "en"))
    if str(body.get("version") or "") != cur["version"]:
        raise HTTPException(status_code=409, detail="The agreement was updated (version %s). Reload and read it again." % cur["version"])
    if PA._latest((sc["key"], sc["account_key"]), cur["version"]):
        raise HTTPException(status_code=409, detail="Version %s is already accepted for your company." % cur["version"])
    fields, errors = PA.validate(body, PA.company_facts(who["id"], sc["company"]))
    if errors:
        raise HTTPException(status_code=422, detail={"error": "Check the highlighted fields.", "errors": errors})
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        r = PA.accept(user=who["id"], role=role, fields=fields, cur=cur, ip=ip, user_agent=request.headers.get("user-agent", ""))
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Version %s is already accepted for your company." % cur["version"])
    except OSError as e:
        log.error("agreement: cannot store the acceptance (%s)", e)
        raise HTTPException(status_code=503, detail="The acceptance could not be stored; nothing was accepted. Try again later.")
    r.pop("_pdf", None)
    AA.record(who["id"], "agreement.accept", r["key"], subject=who["id"],
              details={"mode": r["mode"], "version": r["version"], "record": r["id"], "company": r["company"],
                       "private_pending_payment": r["private_pending_payment"]})
    return {"ok": True, "record_id": r["id"], "version": r["version"], "mode": r["mode"],
            "private_pending_payment": r["private_pending_payment"],
            "status": PA.status_for(who["id"], role, is_admin=bool(who.get("is_admin")))}


@router.get("/mine/pdf")
def mine_pdf(authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    sc = PA.scope_of(who["id"], _role(who, rec))
    r = PA._latest((sc["key"], sc["account_key"]))
    p = PA.pdf_path(r["id"]) if r else None
    if p is None:
        raise HTTPException(status_code=404, detail="No accepted agreement on file.")
    return FileResponse(p, media_type="application/pdf", filename="Platform_Agreement_%s.pdf" % r["id"], headers={"Cache-Control": "no-store"})


@admin_router.get("/acceptances")
def acceptances(limit: int = 200, _admin: dict = Depends(require_admin)):
    cur = PA.current("en")
    return {"acceptances": [PA.public_record(r) for r in PA.read_records(max(1, min(int(limit), 2000)))],
            "enforced": PA.enforced(), "current_version": cur["version"] if cur else None,
            "installed": bool(cur and cur["installed"])}


@admin_router.get("/{record_id}/pdf")
def record_pdf(record_id: str, _admin: dict = Depends(require_admin)):
    from motor_ai_sim import admin_audit as AA
    p = PA.pdf_path(record_id)
    if p is None:
        raise HTTPException(status_code=404, detail="No such accepted agreement.")
    AA.record(AA.actor_of(_admin), "agreement.pdf", record_id, details={"record": record_id})
    return FileResponse(p, media_type="application/pdf", filename="Platform_Agreement_%s.pdf" % record_id, headers={"Cache-Control": "no-store"})


@dataset_router.get("/exclusions")
def dataset_exclusions(_admin: dict = Depends(require_admin)):
    """Companies, e-mails, workspaces, projects, motors and productions of clients whose newest acceptance is PRIVATE."""
    try:
        return PA.dataset_exclusions()
    except Exception as exc:                                           # noqa: BLE001
        log.exception("dataset exclusions failed")
        raise HTTPException(status_code=500, detail="The dataset exclusion list could not be built.") from exc
