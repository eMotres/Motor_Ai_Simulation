"""Saved-simulation library — snapshots of FEM transient runs for comparison.

Each entry bundles the INPUT parameters that were in effect (operating point +
mesh + geometry) and the RESULT summary (T_avg, ripple, losses, efficiency, …)
from one transient run.  The Compare tab loads several entries and shows only
the parameters that DIFFER between them, next to the key results.

Storage is a plain JSON list in config/saved_simulations.json — single-user,
read-modify-write.  All file ops are defensive so a corrupt/missing store can
never crash the backend (returns an empty list instead).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/sims", tags=["saved-sims"])

_ROOT = Path(__file__).parent.parent.parent.parent
_STORE = _ROOT / "config" / "saved_simulations.json"
# Serialise read-modify-write so two near-simultaneous saves can't clobber each
# other (single-worker uvicorn handles requests on a threadpool).
_LOCK = threading.Lock()


def _load() -> list:
    if not _STORE.exists():
        return []
    try:
        data = json.loads(_STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list) -> None:
    try:
        _STORE.write_text(json.dumps(items, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to write store: {e}")


class SaveSimRequest(BaseModel):
    name: str
    params: Dict[str, Any] = Field(default_factory=dict)   # INPUT params snapshot
    results: Dict[str, Any] = Field(default_factory=dict)  # FEM result summary


@router.get("/saved")
def list_saved():
    """All saved simulation snapshots (newest first)."""
    return {"sims": _load()}


@router.post("/saved")
def save_sim(req: SaveSimRequest):
    """Append a new saved-simulation snapshot and return it (with id)."""
    with _LOCK:
        items = _load()
        sid = "sim_%d_%d" % (int(time.time() * 1000), len(items))
        entry = {
            "id": sid,
            "name": (req.name or "untitled").strip()[:80] or "untitled",
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "params": req.params or {},
            "results": req.results or {},
        }
        items.insert(0, entry)           # newest first
        _save(items)
    return entry


@router.delete("/saved/{sim_id}")
def delete_sim(sim_id: str):
    """Delete one saved snapshot by id."""
    with _LOCK:
        items = _load()
        kept = [s for s in items if s.get("id") != sim_id]
        if len(kept) == len(items):
            raise HTTPException(status_code=404, detail=f"sim '{sim_id}' not found")
        _save(kept)
    return {"status": "ok", "deleted": sim_id}


@router.delete("/saved")
def clear_saved():
    """Delete every saved snapshot."""
    _save([])
    return {"status": "ok", "cleared": True}
