"""My motors — a signed-in client's PRIVATE motor space (user's spec 2026-08-24).

In the Motors menu a client may only DUPLICATE existing catalog motors; the
copy lands here — a per-user store visible only to its owner — where they can
rename or delete it, and share it with everyone when (and only when) they
choose.  A copy is a SELF-CONTAINED machine snapshot (the same payload the
family loader applies: die geometry + configuration + duties + winding +
materials), so it stays intact even if the source die is later edited.

Clients have no engineering tabs, so nothing here ever touches the shared
live config — loading a private motor is a client-side apply, exactly like an
ordinary user's ▶ on a shared duty.
"""
from __future__ import annotations

import time
import uuid
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from motor_ai_sim.workspace import root as _ws_root_m
from motor_ai_sim.json_store import mutate_json, read_json

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/my_motors", tags=["my-motors"])

# Per WORKSPACE since Stage 1; the rows inside are ALREADY owner-keyed
# (routes/my_motors._ident), which is the model the rest of the stores are
# moving towards.  A str, not a Path, because json_store takes str keys.
def _store() -> str:
    _ov = globals().get("_STORE")
    if _ov is not None:
        return str(_ov)
    return str(_ws_root_m() / "user_motors.json")


def __getattr__(name):
    if name == "_STORE":
        return _store()
    raise AttributeError(name)


def _ident(authorization: Optional[str]) -> dict:
    from motor_ai_sim.auth import caller_identity
    ident = caller_identity(authorization)
    if not ident.get("id"):
        raise HTTPException(status_code=401,
                            detail="sign in to use your motor space")
    return ident


def _load() -> dict:
    return read_json(_store(), {}) or {}


class DuplicateReq(BaseModel):
    die: str
    config: str
    duty: Optional[str] = None
    name: Optional[str] = None


class RenameReq(BaseModel):
    name: str


def _summary_of(entry: dict) -> dict:
    """Card-sized view — the list never ships every payload (they are large)."""
    g = ((entry.get("payload") or {}).get("geometry")) or {}
    return {
        "id": entry["id"], "name": entry.get("name"),
        "owner": entry.get("owner"), "shared": bool(entry.get("shared")),
        "created_at": entry.get("created_at"),
        "src": entry.get("src"),
        "machine": {
            "stator_diameter": g.get("stator_diameter"),
            "motor_length": g.get("motor_length"),
            "num_slots": g.get("num_slots"), "num_poles": g.get("num_poles"),
        },
    }


@router.get("")
def list_my_motors(authorization: Optional[str] = Header(default=None)):
    """The caller's own motors + everything other users chose to share."""
    ident = _ident(authorization)
    store = _load()
    mine = [_summary_of(e) for e in (store.get(ident["id"]) or {}).values()]
    shared = [_summary_of(e)
              for owner, entries in store.items() if owner != ident["id"]
              for e in entries.values() if e.get("shared")]
    mine.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    shared.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return {"mine": mine, "shared": shared}


@router.get("/{motor_id}/payload")
def my_motor_payload(motor_id: str,
                     authorization: Optional[str] = Header(default=None)):
    """Full machine snapshot for a client-side load: the caller's own motor,
    or any motor its owner shared."""
    ident = _ident(authorization)
    store = _load()
    own = (store.get(ident["id"]) or {}).get(motor_id)
    if own:
        return own["payload"]
    for owner, entries in store.items():
        e = entries.get(motor_id)
        if e and e.get("shared"):
            return e["payload"]
    raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found "
                        "in your space (or it is not shared)")


@router.post("/duplicate")
def duplicate_to_my_space(req: DuplicateReq,
                          authorization: Optional[str] = Header(default=None)):
    """Snapshot a shared catalog machine into the caller's private space."""
    ident = _ident(authorization)
    from motor_ai_sim.routes.family import payload as family_payload
    snap = family_payload(req.die, req.config, req.duty)   # 404s loudly itself
    mid = f"um_{uuid.uuid4().hex[:10]}"
    name = (req.name or f"{req.die} / {req.config}").strip()[:80]
    entry = {
        "id": mid, "name": name, "owner": ident["id"], "shared": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "src": {"die": req.die, "config": req.config, "duty": req.duty},
        "payload": snap,
    }
    def _m(d: dict) -> None:
        d.setdefault(ident["id"], {})[mid] = entry
    mutate_json(_store(), _m, {})
    log.info("my-motors: '%s' duplicated '%s/%s' as '%s' (%s)",
             ident["id"], req.die, req.config, name, mid)
    return {"status": "ok", "motor": _summary_of(entry)}


def _require_own(store: dict, ident: dict, motor_id: str) -> dict:
    e = (store.get(ident["id"]) or {}).get(motor_id)
    if not e:
        raise HTTPException(status_code=404,
                            detail=f"motor '{motor_id}' is not in your space")
    return e


@router.patch("/{motor_id}")
def rename_my_motor(motor_id: str, req: RenameReq,
                    authorization: Optional[str] = Header(default=None)):
    ident = _ident(authorization)
    name = req.name.strip()[:80]
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    _require_own(_load(), ident, motor_id)
    def _m(d: dict) -> None:
        d[ident["id"]][motor_id]["name"] = name
    mutate_json(_store(), _m, {})
    return {"status": "ok", "motor": motor_id, "name": name}


@router.post("/{motor_id}/share")
def share_my_motor(motor_id: str, unshare: bool = False,
                   authorization: Optional[str] = Header(default=None)):
    """Only the owner decides when their motor becomes visible to others."""
    ident = _ident(authorization)
    _require_own(_load(), ident, motor_id)
    def _m(d: dict) -> None:
        d[ident["id"]][motor_id]["shared"] = not unshare
    mutate_json(_store(), _m, {})
    return {"status": "ok", "motor": motor_id, "shared": not unshare}


@router.delete("/{motor_id}")
def delete_my_motor(motor_id: str,
                    authorization: Optional[str] = Header(default=None)):
    ident = _ident(authorization)
    _require_own(_load(), ident, motor_id)
    def _m(d: dict) -> None:
        d[ident["id"]].pop(motor_id, None)
    mutate_json(_store(), _m, {})
    return {"status": "ok", "deleted": motor_id}
