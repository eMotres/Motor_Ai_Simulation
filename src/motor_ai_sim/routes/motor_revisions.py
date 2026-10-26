"""``/api/motor-revisions``: product revisions of a motor (Rev A, Rev B, ...; owner 2026-10-27).  Logic: ``motor_revisions``.

* ``POST /assign``                       Rev A for a die / configuration (staff)
* ``POST /{lineage}/new``                the next revision from the current one (staff)
* ``PATCH /{lineage}/{rev}/agreed``      mark / unmark "agreed with the client" (staff)
* ``GET  ?die=&config=``                 the revision of one motor + its lineage
* ``GET  /index``                        every revision the caller may see (what the catalogue / project pages label rows with)
* ``GET  /{lineage}``                    one lineage

WRITES are staff only (admin / engineer) on top of the catalogue's own write gate; READS need a signed-in account and show only the
revisions of motors the caller may see (a hidden configuration is, for the caller, a revision that does not exist).  The reason
("what changed and why") is the engineers' record: members read the letters, dates and links, staff also read the reasons.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from motor_ai_sim import motor_revisions as MR
from motor_ai_sim.routes import family

router = APIRouter(prefix="/api/motor-revisions", tags=["motor-revisions"])


def _staff(authorization: Optional[str]) -> bool:
    ident = family._auth.caller_identity(authorization)
    return bool(ident.get("is_admin")) or str(ident.get("role") or "").lower() in {"admin", "engineer"}


def _actor(who: dict) -> str:
    return str(((who.get("user") or {}).get("email")) or "").strip().lower()


def require_staff_write(who: dict = Depends(family.require_catalog_write)) -> dict:
    """The catalogue write gate, then: a global engineer or admin."""
    if not (who.get("is_admin") or _staff(who.get("authorization"))):
        raise HTTPException(403, detail="Engineer access required")
    return who


def _require_signed_in(authorization: Optional[str]) -> None:
    if family.catalog_access(authorization)["mode"] == family.MODE_ANONYMOUS:
        raise HTTPException(401, detail="Sign in required.")


def _visible(authorization: Optional[str], rev: dict) -> bool:
    try:
        family._require_die_access(str(rev.get("die")), authorization, str(rev.get("config")), "pair")
        return True
    except HTTPException:
        return False


def _lineage_view(lin: dict, authorization: Optional[str], staff: bool, *, only_visible: bool = True) -> Optional[dict]:
    """The lineage with the revisions the caller may see (``None`` when it sees none)."""
    revs = [r for r in lin.get("revisions") or [] if not only_visible or _visible(authorization, r)]
    if not revs:
        return None
    return {"id": lin.get("id"), "title": lin.get("title"), "created_at": lin.get("created_at"),
            "current": (lin.get("revisions") or [{}])[-1].get("rev"),
            "revisions": [MR.public_revision(lin, r, staff=staff, with_integrity=staff) for r in revs]}


class AssignBody(BaseModel):
    die: str
    config: str
    project_id: Optional[str] = None
    agreed_with_client: bool = False


class NewRevisionBody(BaseModel):
    reason: str = ""
    new_die: bool = False
    config_name: Optional[str] = None
    die_name: Optional[str] = None
    from_rev: Optional[str] = None
    agreed_with_client: bool = False


class AgreedBody(BaseModel):
    agreed: bool


@router.post("/assign")
def assign(body: AssignBody, who: dict = Depends(require_staff_write)):
    die = family._check_name(body.die, "die")
    cfg = family._check_name(body.config, "configuration")
    family._require_die_write(die, who, cfg, "pair")
    lin = MR.assign(die, cfg, actor=_actor(who), project_id=(body.project_id or "").strip() or None,
                    agreed_with_client=body.agreed_with_client)
    view = _lineage_view(lin, who.get("authorization"), True, only_visible=False)
    return {"ok": True, "lineage": view, "rev": "A", "revision": view["revisions"][-1]}


@router.post("/{lineage}/new")
def new_revision(lineage: str, body: NewRevisionBody, who: dict = Depends(require_staff_write)):
    lin = MR.get_lineage(lineage)
    if lin is None:
        raise HTTPException(404, detail="motor lineage not found")
    cur = (lin.get("revisions") or [{}])[-1]
    family._require_die_write(str(cur.get("die")), who, str(cur.get("config")), "pair")
    out = MR.new_revision(lineage, reason=body.reason, who=who, actor=_actor(who), new_die=body.new_die,
                          config_name=(body.config_name or "").strip() or None, die_name=(body.die_name or "").strip() or None,
                          from_rev=(body.from_rev or "").strip() or None, agreed_with_client=body.agreed_with_client)
    view = _lineage_view(out["lineage"], who.get("authorization"), True, only_visible=False)
    newest = out["revision"]
    return {"ok": True, "lineage": view, "rev": newest["rev"], "die": newest["die"], "config": newest["config"],
            "previous": {"rev": out["previous"]["rev"], "die": out["previous"]["die"], "config": out["previous"]["config"], "read_only": True},
            "project_id": newest.get("project_id"), "recommended_carried": out["recommended_carried"],
            "revision": view["revisions"][-1]}


@router.patch("/{lineage}/{rev}/agreed")
def set_agreed(lineage: str, rev: str, body: AgreedBody, who: dict = Depends(require_staff_write)):
    lin = MR.get_lineage(lineage)
    if lin is None:
        raise HTTPException(404, detail="motor lineage not found")
    updated = MR.set_agreed(lineage, rev, body.agreed)
    return {"ok": True, "lineage": _lineage_view(updated, who.get("authorization"), True, only_visible=False)}


@router.get("/index")
def index(authorization: str = Header(default=None)) -> Dict[str, Any]:
    """Every revision the caller may see: ``[{die, config, rev, label, lineage, latest, frozen}]`` - what the catalogue, the project
    page and production label motor names with.  One small request instead of one per row."""
    _require_signed_in(authorization)
    rows: List[dict] = []
    for lin in MR._load().values():
        for rev in lin.get("revisions") or []:
            if _visible(authorization, rev):
                rows.append({"die": rev.get("die"), "config": rev.get("config"), "rev": rev.get("rev"), "label": MR.label(rev.get("rev")),
                             "lineage": lin.get("id"), "latest": MR.is_latest(lin, rev), "frozen": bool(rev.get("frozen"))})
    return {"revisions": rows}


@router.get("")
def for_motor(die: str = "", config: str = "", authorization: str = Header(default=None)) -> Dict[str, Any]:
    """The product revision of one motor and the lineage it belongs to; ``revision: null`` for a motor with none."""
    _require_signed_in(authorization)
    die, config = die.strip(), config.strip()
    if not die or not config:
        raise HTTPException(422, detail="give both die and config")
    family._require_die_access(die, authorization, config)
    # a hidden configuration answered 404 above; a missing one answers the same
    if not family._cfg_file(die, config).is_file():
        raise HTTPException(404, detail=family._config_missing_detail(die, config))
    hit = MR.find(die, config)
    if hit is None:
        return {"revision": None, "lineage": None}
    lin, rev = hit
    staff = _staff(authorization)
    return {"revision": MR.public_revision(lin, rev, staff=staff, with_integrity=staff),
            "lineage": _lineage_view(lin, authorization, staff)}


@router.get("/{lineage}")
def get_lineage(lineage: str, authorization: str = Header(default=None)) -> Dict[str, Any]:
    _require_signed_in(authorization)
    lin = MR.get_lineage(lineage)
    view = _lineage_view(lin, authorization, _staff(authorization)) if lin else None
    if view is None:
        raise HTTPException(404, detail="motor lineage not found")
    return {"lineage": view}
