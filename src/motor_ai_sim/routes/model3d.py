"""3D model menu routes: list the models a caller may see, serve the GLB, download the stamped STEP.

Access is the same as for the other motor routes (``motor_access``: admin / granted dies / per-die visibility) plus the NDA
rule of :mod:`motor_ai_sim.model3d`: an account without "NDA signed" is served the simplified envelope only, whatever it asks
for.  The URL names the motor, never a variant or a file: the server picks the variant from the caller.  Nothing priced and no
bill of materials is served here.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
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
    # 404 for "not granted" and "not built" alike: the list does not reveal motors the caller may not see
    if e is None or not MA.may_see_die(MA.catalog_access(authorization), die):
        raise HTTPException(status_code=404, detail="No 3D model for this motor.")
    return e


@router.get("/models")
def list_models(authorization: Optional[str] = Header(default=None)):
    """The built models of the motors this caller may open, with the variant (``envelope`` / ``full``) it will get."""
    who, rec = _caller(authorization)
    variant = M.variant_for(who, rec)
    from motor_ai_sim import motor_access as MA
    access = MA.catalog_access(authorization)
    models = [M.model_info(e, variant) for e in M.scan() if MA.may_see_die(access, e["die"])]
    return {"variant": variant, "models": [m for m in models if m["available"]]}


@router.get("/{die}/{config}/model.glb")
def get_glb(die: str, config: str, authorization: Optional[str] = Header(default=None)):
    who, rec = _caller(authorization)
    e = _entry_for(authorization, die, config)
    variant = M.variant_for(who, rec)
    row = M.file_row(e, variant, "glb")
    if row is None:
        raise HTTPException(status_code=404, detail="This model has not been built yet.")
    return FileResponse(row["path"], media_type="model/gltf-binary",
                        headers={"X-Model-Variant": variant, "ETag": '"%s"' % row["sha256"],
                                 "Cache-Control": "private, max-age=0, must-revalidate"})


@router.get("/{die}/{config}/model.step")
def get_step(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The STEP of the caller's variant with the customer name and the date in its header and file name.  Logged."""
    who, rec = _caller(authorization)
    e = _entry_for(authorization, die, config)
    variant = M.variant_for(who, rec)
    row = M.file_row(e, variant, "step")
    if row is None:
        raise HTTPException(status_code=404, detail="This model has not been built yet.")
    customer = M.customer_name(who, rec)
    day = datetime.datetime.now(datetime.timezone.utc).date()
    name = M.download_name(e["motor_id"], variant, customer, day)
    m = e["manifest"]
    version = "template %s, built %s, param %s" % ((m.get("template") or {}).get("version", "?"), m.get("built_at", "?"),
                                                  (m.get("param_hash") or "")[:12])
    with open(row["path"], "rb") as fh:
        src = fh.read()
    data = M.stamp_step(src, filename=name, customer=customer, day=day, variant=variant, motor_id=e["motor_id"], version=version)
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        M.log_download(dict(user=who["id"], name=customer, role=who.get("role"), nda=variant == M.VARIANT_FULL and not who.get("is_admin"),
                            die=die, config=config, motor_id=e["motor_id"], variant=variant, kind="step", file=name,
                            source_file=row["name"], source_sha256=row["sha256"], sha256=M.sha256_bytes(data), bytes=len(data),
                            template=(m.get("template") or {}).get("version"), built_at=m.get("built_at"),
                            param_hash=m.get("param_hash"), ip=ip))
    except OSError as ex:
        log.error("model3d: download log not writable (%s) - download refused", ex)
        raise HTTPException(status_code=503, detail="Download log unavailable; try again later.")
    return Response(content=data, media_type="model/step",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name, "X-Model-Variant": variant,
                             "Cache-Control": "no-store"})


@router.get("/downloads")
def downloads(limit: int = 200, _admin: dict = Depends(require_admin)):
    """Admin: the most recent downloads, newest first."""
    return {"downloads": M.read_log(max(1, min(int(limit), 2000)))}
