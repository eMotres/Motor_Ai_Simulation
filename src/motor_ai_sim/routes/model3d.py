"""3D model menu routes: list the models a caller may see, serve the GLB, download the stamped STEP.

ONE model for everybody (owner 2026-10-13): every signed-in user who may see a motor (``motor_access``: admin / granted dies /
per-die visibility) can view it - the GLB is the full model, copper board included.  Downloading the STEP is the NDA gate:
only accounts flagged "NDA signed" and the owner / admin get it; everybody else gets 403.  The URL names the motor, never a
file.  Every download carries a download id and the NDA it rests on (STEP header + the append-only log); the admin can list
the downloads and trace a file by id or sha256.  Nothing priced and no bill of materials is served here.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response

from motor_ai_sim import model3d as M
from motor_ai_sim.auth import ANON_OWNER, caller_identity, require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/model3d", tags=["model3d"])


def _caller(authorization: Optional[str]) -> tuple[dict, dict]:
    """(identity, user record) of a signed-in caller; 401 for anonymous."""
    who = caller_identity(authorization)
    if not who.get("is_admin") and (not who.get("id") or who["id"] == ANON_OWNER):
        raise HTTPException(status_code=401, detail="Sign in to open the 3D models.")
    from motor_ai_sim import users as U
    rec = U.get_user(who["id"]) or {}
    return who, rec


def _entry_for(authorization: Optional[str], die: str, config: str) -> dict:
    from motor_ai_sim import motor_access as MA
    e = M.find(die, config)
    # 404 for "not granted" and "not built" alike: the list does not reveal motors the caller may not see.
    # A PRIVATE configuration of a visible die is "not granted" too (motor_access.may_see_config).
    if e is None or not MA.may_see_config(MA.catalog_access(authorization), die, config):
        raise HTTPException(status_code=404, detail="No 3D model for this motor.")
    return e


@router.get("/models")
def list_models(authorization: Optional[str] = Header(default=None)):
    """The built models of the motors this caller may open, and whether this caller may download the STEP."""
    who, rec = _caller(authorization)
    from motor_ai_sim import motor_access as MA
    access = MA.catalog_access(authorization)
    models = [M.model_info(e) for e in M.scan() if MA.may_see_config(access, e["die"], e["config"])]
    return {"can_download": M.can_download(who, rec), "models": [m for m in models if m["available"]]}


@router.get("/{die}/{config}/model.glb")
def get_glb(die: str, config: str, authorization: Optional[str] = Header(default=None)):
    _caller(authorization)
    e = _entry_for(authorization, die, config)
    row = M.file_row(e, "glb")
    if row is None:
        raise HTTPException(status_code=404, detail="This model has not been built yet.")
    return FileResponse(row["path"], media_type="model/gltf-binary",
                        headers={"ETag": '"%s"' % row["sha256"], "Cache-Control": "private, max-age=0, must-revalidate"})


@router.get("/{die}/{config}/model.step")
def get_step(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The STEP with the customer name and the date in its header and file name.  NDA accounts and the owner only; logged."""
    who, rec = _caller(authorization)
    e = _entry_for(authorization, die, config)
    if not M.can_download(who, rec):
        raise HTTPException(status_code=403, detail="Download is available after the NDA is signed.")
    row = M.file_row(e, "step")
    if row is None:
        raise HTTPException(status_code=404, detail="This model has not been built yet.")
    customer = M.customer_name(who, rec)
    now = datetime.datetime.now(datetime.timezone.utc)
    day = now.date()
    name = M.download_name(e["motor_id"], customer, day)
    m = e["manifest"]
    version = "template %s, built %s, param %s" % ((m.get("template") or {}).get("version", "?"), m.get("built_at", "?"),
                                                  (m.get("param_hash") or "")[:12])
    basis = M.nda_basis(who, rec)
    who_for = M.recipient_of(who, rec, basis)
    download_id = M.new_download_id(now)
    stamp = M.build_stamp(download_id=download_id, motor_id=e["motor_id"], version=version, day=day, recipient=who_for, basis=basis)
    with open(row["path"], "rb") as fh:
        src = fh.read()
    data = M.stamp_step(src, filename=name, customer=customer, day=day, motor_id=e["motor_id"], version=version, stamp=stamp)
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        M.log_download(dict(download_id=download_id, user=who["id"], email=who_for["email"], name=customer, role=who.get("role"),
                            company=who_for["company"], nda=basis["type"] != "internal", nda_basis=basis,
                            die=die, config=config, motor_id=e["motor_id"], kind="step", file=name,
                            source_file=row["name"], source_sha256=row["sha256"], sha256=M.sha256_bytes(data), bytes=len(data),
                            template=(m.get("template") or {}).get("version"), built_at=m.get("built_at"),
                            param_hash=m.get("param_hash"), ip=ip, user_agent=request.headers.get("user-agent", "")[:300]))
    except OSError as ex:
        log.error("model3d: download log not writable (%s) - download refused", ex)
        raise HTTPException(status_code=503, detail="Download log unavailable; try again later.")
    return Response(content=data, media_type="model/step",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name, "Cache-Control": "no-store"})


@router.get("/downloads")
def downloads(limit: int = 200, user: str = "", company: str = "", motor: str = "", nda_version: str = "",
              _admin: dict = Depends(require_admin)):
    """Admin: the most recent downloads, newest first; optional case-insensitive filters (user, company, motor, NDA version)."""
    return {"downloads": M.read_log(max(1, min(int(limit), 2000)), user=user, company=company, motor=motor,
                                    nda_version=nda_version)}


@router.post("/trace")
def trace(body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    """Admin: which download is this file?  ``{"text": ...}`` is a STEP header line, a download id or a sha256 (the page hashes an
    uploaded file in the browser and sends its sha256 and the id found in its header; the file itself never leaves the browser)."""
    return M.trace(str(body.get("text") or "")[:20000])
