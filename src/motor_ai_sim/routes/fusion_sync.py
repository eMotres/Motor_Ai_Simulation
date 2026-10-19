"""Fusion <-> AeroStator sync (docs/FUSION_SYNC.md section 5.2).  The client is the Fusion add-in fusion/AeroStatorSync.

GET  /api/fusion/sync/targets                     allow-listed dies and their configurations (picker of the first export)
GET  /api/fusion/sync/link?urn=                   the die/config a Fusion document is linked to (404 = not linked)
PUT  /api/fusion/sync/link                        link a Fusion document to an allow-listed die/config
POST /api/fusion/sync/bundles                     upload an export bundle (zip); stores it and queues the CAD rebuild
GET  /api/fusion/sync/bundles?die=                stored versions of a die
GET  /api/fusion/sync/{die}/{config}/params.json  Import source: the EM parameters of THAT die/config under Fusion names
GET  /api/fusion/sync/{die}/{config}/status       the version guard (3-D and Production tabs)

AUTH.  Every route needs an admin: a browser session (require_admin), or an agent key (``Bearer emk_...``, created in
"Access for agents") that carries the scope ``fusion:sync`` and whose owner is an admin.  The prefix is on the auth door's
own-credentials list (auth._ANON_OK_PREFIXES) because the door only knows session tokens; the check is here.
Only dies on the allow-list (fusion_sync.store.allowed_dies, Stage 1: CIANO14 50 edited) are served: the frozen 40 mm die
is refused with 403 fusion_sync_not_enabled.  Nothing here writes a die or configuration file.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from motor_ai_sim.fusion_sync import check as FC
from motor_ai_sim.fusion_sync import store as FS

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/fusion/sync", tags=["fusion-sync"])
SCOPE = "fusion:sync"


def require_fusion_sync(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """An admin, by session or by an agent key with the scope fusion:sync.  Returns {actor, via}."""
    tok = authorization.strip() if isinstance(authorization, str) else ""
    if tok.lower().startswith("bearer emk_"):
        from motor_ai_sim import agent_keys as AK
        from motor_ai_sim import auth as A
        p, why = AK.verify(tok)
        if p is None:
            raise HTTPException(401, detail={"code": "bad_key", "message": "agent key not accepted (%s)" % why})
        if not p.has(SCOPE):
            raise HTTPException(403, detail={"code": "scope_missing", "message": "the key needs the scope %s" % SCOPE})
        admin = (not A.AUTH_ENFORCE and not A._ADMIN_EMAILS) or A._registry_role(p.email, fallback="user") == "admin"
        if not admin:
            raise HTTPException(403, detail={"code": "admin_only", "message": "Fusion sync keys must belong to an admin account"})
        return dict(actor=p.email, via="key:" + p.credential_id)
    from motor_ai_sim.auth import require_admin
    user = require_admin(authorization)
    return dict(actor=(user or {}).get("email") or (user or {}).get("uid") or "admin", via="session")


def _enabled(die: str) -> str:
    from motor_ai_sim.routes import family as fam
    die = fam._check_name(die, "die")
    if die not in FS.allowed_dies():
        raise HTTPException(403, detail={"code": "fusion_sync_not_enabled",
                                         "message": "Fusion sync is not enabled for die %r (allowed: %s)" % (die, ", ".join(FS.allowed_dies()) or "none")})
    return die


def _die_dir(die: str) -> str:
    from motor_ai_sim.routes import family as fam
    d = fam._die_dir(die)
    if not (d / "die.yaml").is_file():
        raise HTTPException(404, detail={"code": "no_die", "message": "die %r not found" % die})
    return str(d)


def _config(die: str, config: str) -> str:
    from motor_ai_sim.routes import family as fam
    config = fam._check_name(config, "configuration")
    if not fam._cfg_file(die, config).is_file():
        raise HTTPException(404, detail={"code": "no_config", "message": "configuration %r of die %r not found" % (config, die)})
    return config


def _refuse(e: FS.BundleError):
    raise HTTPException(e.status, detail={"code": e.code, "message": str(e)})


@router.get("/targets")
def targets(_who: dict = Depends(require_fusion_sync)):
    from motor_ai_sim.routes import family as fam
    out = []
    for die in FS.allowed_dies():
        try:
            d = fam._die_dir(die)
        except HTTPException:
            continue
        if (d / "die.yaml").is_file():
            out.append(dict(die=die, configs=sorted(p.stem for p in d.glob("*.yaml") if p.name != "die.yaml"), link=FS.link_of_die(die)))
    return {"targets": out}


@router.get("/link")
def get_link(urn: str = Query(..., min_length=3, max_length=300), _who: dict = Depends(require_fusion_sync)):
    ln = FS.link_of_urn(urn)
    if not ln:
        raise HTTPException(404, detail={"code": "not_linked", "message": "this Fusion document is not linked to a die"})
    return ln


class LinkIn(BaseModel):
    urn: str = Field(..., min_length=3, max_length=300)
    document: str = Field(..., min_length=1, max_length=200)
    die: str
    config: str
    sketches: Optional[List[str]] = None


@router.put("/link")
def put_link(req: LinkIn, who: dict = Depends(require_fusion_sync)):
    die = _enabled(req.die)
    _die_dir(die)
    cfg = _config(die, req.config)
    try:
        return FS.set_link(req.urn, req.document, die, cfg, who["actor"], req.sketches)
    except FS.BundleError as e:
        _refuse(e)


@router.post("/bundles")
async def upload_bundle(file: UploadFile = File(...), who: dict = Depends(require_fusion_sync)):
    data = await file.read(FS.MAX_ZIP_BYTES + 1)
    try:
        m, files = FS.read_bundle(data)
    except FS.BundleError as e:
        _refuse(e)
    ln = FS.link_of_urn(m["urn"])
    if not ln:
        raise HTTPException(409, detail={"code": "not_linked", "message": "link the Fusion document to a die first (PUT /link)"})
    die = _enabled(ln["die"])
    cfg = _config(die, ln["config"])
    if (m.get("link") or {}).get("die") not in (None, die):
        raise HTTPException(409, detail={"code": "link_mismatch", "message": "the bundle says die %r, the link says %r" % (m["link"]["die"], die)})
    ddir = _die_dir(die)
    try:
        eff = FC.effective(ddir, cfg)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(409, detail={"code": "die_unreadable", "message": str(e)})
    try:
        rec, again = FS.store_bundle(die, cfg, m, files, data, who["actor"], extra=dict(aerostator_files=FC.file_hashes(eff)))
    except FS.BundleError as e:
        _refuse(e)
    rows = FC.param_rows(eff["geometry"])
    import json as _json
    bparams = _json.loads(files["parameters.json"].decode("utf-8"))
    out = dict(ok=True, die=die, config=cfg, version=rec["version"], already_stored=again,
               param_diff=FC.param_diff(bparams, rows), body_checks=FC.body_checks(m, eff["geometry"]))
    out["rebuild"] = None if again else _queue_rebuild(die, cfg, rec["version"], who["actor"])
    log.info("fusion sync: %s v%d of %s/%s by %s (%s)%s", m.get("document"), rec["version"], die, cfg, who["actor"], who["via"],
             " - already stored" if again else "")
    return out


def _queue_rebuild(die: str, cfg: str, version: int, actor: str) -> Dict[str, Any]:
    """The CAD rebuild from the Fusion bodies (cad.rebuild_queue); the worker reads the bundle through fusion_sync.store."""
    from motor_ai_sim import cad_params as CP
    from motor_ai_sim.cad import rebuild_queue as RQ
    from motor_ai_sim.routes import family as fam
    try:
        ov = CP.clean_overrides(fam._load_yaml(fam._cfg_file(die, cfg), "configuration").get(CP.OVERRIDES_KEY))
        layer = "shared" if not fam._ws_layering() else str(fam._die_layer(die))
        rec = RQ.request(CP.cad_root(), die, cfg, requested_by="fusion-sync:" + actor, overrides_hash=CP.overrides_hash(ov),
                         layer=layer, fusion_version=int(version))
        return {k: rec.get(k) for k in ("state", "requested_iso", "fusion_version")}
    except Exception as e:      # noqa: BLE001 - a stored bundle stays stored; the status route shows the missing rebuild
        log.warning("fusion sync: rebuild request for %s/%s failed: %s", die, cfg, e)
        return dict(state="not_queued", message=str(e)[:300])


@router.get("/bundles")
def list_bundles(die: str = Query(...), _who: dict = Depends(require_fusion_sync)):
    die = _enabled(die)
    return {"die": die, "latest": FS.latest(die), "versions": FS.versions(die)}


@router.get("/{die}/{config}/params.json")
def die_params(die: str, config: str, _who: dict = Depends(require_fusion_sync)):
    die = _enabled(die)
    ddir = _die_dir(die)
    cfg = _config(die, config)
    try:
        eff = FC.effective(ddir, cfg)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(409, detail={"code": "die_unreadable", "message": str(e)})
    lt = FS.latest(die)
    return {"die": die, "config": cfg, "parameters": FC.param_rows(eff["geometry"]),
            "files": FC.file_hashes(eff), "fusion_version": (lt or {}).get("version")}


def guard(die: str, config: str) -> Dict[str, Any]:
    """The version guard for a die/config (also used by the 3-D and Production routes); {} for a die that is not synced."""
    if die not in FS.allowed_dies():
        return {}
    from motor_ai_sim import cad_params as CP
    from motor_ai_sim.cad import rebuild_queue as RQ
    try:
        ddir = _die_dir(die)
    except HTTPException:
        return {}
    return FC.status(die, config, die_dir=ddir, cad_manifest=CP.read_manifest(die, config), rebuild=RQ.read(CP.cad_root(), die, config))


@router.get("/{die}/{config}/status")
def sync_status(die: str, config: str, _who: dict = Depends(require_fusion_sync)):
    die = _enabled(die)
    cfg = _config(die, config)
    return guard(die, cfg)
