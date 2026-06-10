"""Saved-simulation library — snapshots of FEM transient runs for comparison.

Each entry bundles the INPUT parameters that were in effect (operating point +
mesh + geometry) and the RESULT summary (T_avg, ripple, losses, efficiency, …)
from one transient run.  The Compare tab loads several entries and shows only
the parameters that DIFFER between them, next to the key results.

PER-USER storage: the on-disk JSON is a dict ``{bucket: [sims]}`` where the
bucket is the signed-in user's Firebase uid (from the Bearer token the frontend
attaches automatically), or "local" when no token is present (local / anonymous
use).  So every user keeps their OWN permanent library; one user's saves are
never shown to (or deletable by) another.  All file ops are defensive so a
corrupt/missing store can never crash the backend.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from motor_ai_sim.auth import resolve_user

router = APIRouter(prefix="/api/sims", tags=["saved-sims"])

_ROOT = Path(__file__).parent.parent.parent.parent
_STORE = _ROOT / "config" / "saved_simulations.json"
# Serialise read-modify-write so two near-simultaneous saves can't clobber each
# other (single-worker uvicorn handles requests on a threadpool).
_LOCK = threading.Lock()


def _load_all() -> Dict[str, list]:
    """Full store {bucket: [sims]}.  Migrates a legacy flat list → {'local': …}."""
    if not _STORE.exists():
        return {}
    try:
        data = json.loads(_STORE.read_text(encoding="utf-8"))
        if isinstance(data, list):           # legacy single-list format
            return {"local": data}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(store: Dict[str, list]) -> None:
    try:
        _STORE.write_text(json.dumps(store, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to write store: {e}")


def _bucket(request: Request) -> str:
    """Per-user key: Firebase uid when signed in, else 'local'.  Never raises."""
    try:
        user = resolve_user(request.headers.get("authorization"))
        if user and user.get("uid"):
            return str(user["uid"])
    except Exception:
        pass
    return "local"


class SaveSimRequest(BaseModel):
    name: str
    params: Dict[str, Any] = Field(default_factory=dict)   # INPUT params snapshot
    results: Dict[str, Any] = Field(default_factory=dict)  # FEM result summary


class RenameRequest(BaseModel):
    name: str


@router.get("/saved")
def list_saved(request: Request):
    """This user's saved simulation snapshots (newest first)."""
    return {"sims": _load_all().get(_bucket(request), [])}


@router.post("/saved")
def save_sim(req: SaveSimRequest, request: Request):
    """Append a new snapshot to THIS user's library and return it (with id)."""
    with _LOCK:
        store = _load_all()
        bucket = _bucket(request)
        items = store.get(bucket, [])
        sid = "sim_%d_%d" % (int(time.time() * 1000), len(items))
        entry = {
            "id": sid,
            "name": (req.name or "untitled").strip()[:80] or "untitled",
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "params": req.params or {},
            "results": req.results or {},
        }
        items.insert(0, entry)               # newest first
        store[bucket] = items
        _save_all(store)
    return entry


@router.patch("/saved/{sim_id}")
def rename_sim(sim_id: str, req: RenameRequest, request: Request):
    """Rename one of THIS user's snapshots."""
    new_name = (req.name or "untitled").strip()[:80] or "untitled"
    with _LOCK:
        store = _load_all()
        bucket = _bucket(request)
        items = store.get(bucket, [])
        hit = next((s for s in items if s.get("id") == sim_id), None)
        if hit is None:
            raise HTTPException(status_code=404, detail=f"sim '{sim_id}' not found")
        hit["name"] = new_name
        store[bucket] = items
        _save_all(store)
    return {"status": "ok", "id": sim_id, "name": new_name}


@router.delete("/saved/{sim_id}")
def delete_sim(sim_id: str, request: Request):
    """Delete one of THIS user's snapshots by id."""
    with _LOCK:
        store = _load_all()
        bucket = _bucket(request)
        items = store.get(bucket, [])
        kept = [s for s in items if s.get("id") != sim_id]
        if len(kept) == len(items):
            raise HTTPException(status_code=404, detail=f"sim '{sim_id}' not found")
        store[bucket] = kept
        _save_all(store)
    return {"status": "ok", "deleted": sim_id}


@router.delete("/saved")
def clear_saved(request: Request):
    """Delete every snapshot in THIS user's library."""
    with _LOCK:
        store = _load_all()
        store[_bucket(request)] = []
        _save_all(store)
    return {"status": "ok", "cleared": True}
