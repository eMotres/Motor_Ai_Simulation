"""Two regions, stage 1 (docs/regions/DESIGN.md): the public region info, the signed peer API and the admin routes.

PUBLIC (no sign-in, nothing personal)
  GET  /api/region                              this server's region, mode, the public base URLs

PEER (server-to-server, HMAC-signed: regions/peer.py; 404 for anything unsigned and in single-region mode)
  GET  /api/peer/v1/health                      status: region, version, commit, load, queue depth, disk
  GET  /api/peer/v1/directory                   the global account directory entries this server hosts (email -> id,
                                                region, tier, created; nothing else)
  GET  /api/peer/v1/catalog/manifest            hashes of the shared library (EU only: EU authors, US mirrors)
  GET  /api/peer/v1/admin/{users|projects|verification|jobs}
                                                this server's rows, tagged with the region, for the OTHER region's admin
                                                view.  Fetched live, shown, never stored by the caller.

ADMIN (require_admin)
  GET  /api/admin/regions                       both regions' status (the Servers card); the local one alone when single
  GET  /api/admin/regions/accounts              {email: region} for the admin tables (local + directory)
  GET  /api/admin/regions/remote/{kind}         the peer's rows of users / projects / verification / jobs, live
  GET  /api/admin/regions/classify              the classifier's DRY-RUN report (proposed region + reason per account)
  POST /api/admin/regions/directory/sync        pull the peer's directory entries into the remote directory
  POST /api/admin/users/{email}/region          admin override {region, reason}; audited ``user.region``
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from motor_ai_sim import admin_audit as _AA
from motor_ai_sim.auth import require_admin
from motor_ai_sim.regions import config as RC
from motor_ai_sim.regions import peer as P
from motor_ai_sim.regions import status as ST
from motor_ai_sim.regions import store as RS

router = APIRouter(tags=["regions"])

REMOTE_KINDS = ("users", "projects", "verification", "jobs")


# ── public ───────────────────────────────────────────────────────────────────

@router.get("/api/region")
def region_info() -> Dict[str, Any]:
    d = RC.describe()
    return {"region": d["region"], "mode": d["mode"], "base_urls": d["base_urls"]}


# ── peer ─────────────────────────────────────────────────────────────────────

async def require_peer(request: Request) -> str:
    """A valid signed request from the configured peer, else the same 404 an unknown path gets."""
    path = request.url.path + (("?" + request.url.query) if request.url.query else "")
    body = await request.body()
    ok, why = P.verify(request.method, path, request.headers, body)
    if not ok:
        raise HTTPException(status_code=404, detail="Not Found")
    return RC.peer_region()


@router.get(P.PREFIX + "/health")
def peer_health(_peer: str = Depends(require_peer)) -> Dict[str, Any]:
    return ST.local_status()


@router.get(P.PREFIX + "/directory")
def peer_directory(_peer: str = Depends(require_peer)) -> Dict[str, Any]:
    return {"region": RC.local_region(), "entries": RS.local_directory()}


@router.get(P.PREFIX + "/catalog/manifest")
def peer_catalog_manifest(_peer: str = Depends(require_peer)) -> Dict[str, Any]:
    if RC.local_region() != "eu":
        raise HTTPException(status_code=404, detail="Not Found")      # only the authoring region publishes a manifest
    from motor_ai_sim.regions import catalog_manifest as CM
    return CM.build()


def _local_users() -> List[Dict[str, Any]]:
    from motor_ai_sim import users as U
    reg = RC.local_region()
    rows = []
    for em, rec in sorted(U._load_soft().items()):
        if isinstance(rec, dict) and RS.effective_region(rec) == reg:
            rows.append({"email": em, "name": rec.get("name", ""), "role": rec.get("role", "user"),
                         "created": rec.get("created"), "disabled": bool(rec.get("disabled")), "region": reg})
    return rows


def _local_projects() -> List[Dict[str, Any]]:
    from motor_ai_sim.development import store as DS
    reg = RC.local_region()
    rows = []
    for p in DS.list_projects():
        owners = [str(m.get("email") or "") for m in (p.get("members") or [])
                  if isinstance(m, dict) and m.get("role") == "owner"]
        rows.append({"id": p.get("id"), "title": p.get("title", ""), "status": p.get("status", ""),
                     "owners": owners, "region": reg})
    return rows


def _local_verification() -> List[Dict[str, Any]]:
    from motor_ai_sim.verification import store as VS
    reg = RC.local_region()
    rows = []
    for c in VS.list_cases():
        a = c.get("application") or {}
        rows.append({"id": c.get("id"), "user": c.get("user"), "status": c.get("status"),
                     "company": a.get("legal_name", ""), "country": a.get("country", ""),
                     "updated_iso": c.get("updated_iso", ""), "region": reg})
    return rows


def _local_jobs() -> List[Dict[str, Any]]:
    from motor_ai_sim import cluster_monitor as CM
    reg = RC.local_region()
    v = CM.jobs_view()
    return [{**{k: it.get(k) for k in ("run_id", "owner", "kind", "state", "elapsed_s", "waited_s")}, "region": reg}
            for it in (v.get("items") or [])]


_LOCAL_ROWS = {"users": _local_users, "projects": _local_projects, "verification": _local_verification,
               "jobs": _local_jobs}


@router.get(P.PREFIX + "/admin/{kind}")
def peer_admin_rows(kind: str, _peer: str = Depends(require_peer)) -> Dict[str, Any]:
    fn = _LOCAL_ROWS.get(kind)
    if fn is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return {"region": RC.local_region(), "kind": kind, "rows": fn()}


# ── admin ────────────────────────────────────────────────────────────────────

@router.get("/api/admin/regions")
def admin_regions(_admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    return ST.regions_view()


@router.get("/api/admin/regions/accounts")
def admin_region_accounts(_admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    m = RS.region_map()
    return {"region": RC.local_region(), "mode": RC.describe()["mode"], "accounts": m,
            "counts": {r: sum(1 for v in m.values() if v == r) for r in RC.REGIONS}}


@router.get("/api/admin/regions/remote/{kind}")
def admin_remote_rows(kind: str, admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    """The peer's rows, fetched live for this view; nothing of them is written on this server."""
    if kind not in REMOTE_KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")
    if not RC.peer_configured():
        return {"configured": False, "reachable": False, "region": None, "rows": []}
    got = P.get(f"/admin/{kind}")
    if not isinstance(got, dict) or not isinstance(got.get("rows"), list):
        return {"configured": True, "reachable": False, "region": RC.peer_region(), "rows": []}
    if kind in ("users", "verification"):
        _AA.record(_AA.actor_of(admin), "regions.remote_read", kind, details={"region": RC.peer_region(),
                                                                              "rows": len(got["rows"])})
    rows = [dict(r, region=RC.peer_region()) for r in got["rows"] if isinstance(r, dict)]
    return {"configured": True, "reachable": True, "region": RC.peer_region(), "rows": rows}


@router.get("/api/admin/regions/classify")
def admin_classify(admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    from motor_ai_sim.regions import classifier as CL
    rep = CL.classify_all(apply=False)
    _AA.record(_AA.actor_of(admin), "regions.classify", "dry_run", details={"accounts": rep["accounts"]})
    return rep


@router.post("/api/admin/regions/directory/sync")
def admin_directory_sync(admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    if not RC.peer_configured():
        raise HTTPException(status_code=409, detail="no peer region is configured")
    got = P.get("/directory")
    if not isinstance(got, dict) or RC.norm_region(got.get("region")) != RC.peer_region():
        raise HTTPException(status_code=502, detail="the peer region did not answer")
    doc = RS.store_remote_directory(got.get("entries") or {}, RC.peer_region())
    _AA.record(_AA.actor_of(admin), "regions.directory_sync", RC.peer_region(), details={"entries": len(doc["entries"])})
    return {"ok": True, "region": doc["region"], "entries": len(doc["entries"]), "updated": doc["updated"]}


@router.post("/api/admin/users/{email}/region")
def admin_set_region(email: str, body: dict = Body(default={}), admin: dict = Depends(require_admin)) -> Dict[str, Any]:
    """The admin override of an account's home region.  Moves no data (stage 3 migrates); audited."""
    region = RC.norm_region((body or {}).get("region"))
    if region is None:
        raise HTTPException(status_code=422, detail=f"region must be one of {RC.REGIONS}")
    reason = str((body or {}).get("reason") or "")[:200]
    try:
        res = RS.set_home_region(email, region, by="admin", actor=_AA.actor_of(admin), reason=reason)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"user '{email}' not found")
    _AA.record(_AA.actor_of(admin), "user.region", res["email"], subject=res["email"],
               details={"region": res["region"], "previous": res["previous"], "reason": reason})
    return {"ok": True, **res}
