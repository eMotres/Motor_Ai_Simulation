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

import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from motor_ai_sim.auth import resolve_user

router = APIRouter(prefix="/api/sims", tags=["saved-sims"])

_ROOT = Path(__file__).parent.parent.parent.parent
# Beside the config THIS PROCESS is pointed at (``MOTOR_AI_SIM_CONFIG``).  It was
# pinned to the repo's own config/, and `_save_all` WRITES it: a redirected
# process appended its sandbox runs to the user's real saved-simulation library.
# With no env var set this is byte-identical to `_ROOT / "config" / …`.
#
# Migration Stage 1: resolved PER CALL against the caller's workspace.  With
# none set that is the same folder and the same file.  The NAME survives for the
# completeness test and for tests/test_saved_sims_nested.py, which patches it.
def _store() -> Path:
    _ov = globals().get("_STORE")
    if _ov is not None:
        return Path(str(_ov))
    try:
        from motor_ai_sim.workspace import root as _ws_root
        return _ws_root() / "saved_simulations.json"
    except Exception:                   # noqa: BLE001 — never break a save
        return _ROOT / "config" / "saved_simulations.json"


def __getattr__(name):
    if name == "_STORE":
        return _store()
    raise AttributeError(name)
# Serialise read-modify-write so two near-simultaneous saves can't clobber each
# other (single-worker uvicorn handles requests on a threadpool).
_LOCK = threading.Lock()


def _load_all() -> Dict[str, list]:
    """Full store {bucket: [sims]}.  Migrates a legacy flat list → {'local': …}."""
    if not _store().exists():
        return {}
    try:
        data = json.loads(_store().read_text(encoding="utf-8"))
        if isinstance(data, list):           # legacy single-list format
            return {"local": data}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(store: Dict[str, list]) -> None:
    """Atomic (temp + replace).  Every caller already re-reads under `_LOCK`, so
    the merge is right; what was missing was that a plain `write_text` truncates
    in place, and `_load_all` answers `{}` to a parse error — so a reader landing
    inside that window reads an empty library and the next save persists it."""
    try:
        from motor_ai_sim.json_store import atomic_write_json
        atomic_write_json(_store(), store)
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


def bucket_for(request: Request) -> str:
    """Public alias of the per-user bucket key, for server-side writers (the
    auto-optimizer stores its result point in the SAME library the Compare tab
    reads, and must file it under the user who launched the run)."""
    return _bucket(request)


def append_sim(bucket: str, name: str, params: Dict[str, Any],
               results: Dict[str, Any]) -> Dict[str, Any]:
    """Append a snapshot to ``bucket``'s library and return the stored entry.

    Same store, same lock and same id scheme as the POST route — so a point a
    background job files is indistinguishable from one the user saved by hand,
    and the two can never interleave into a lost write."""
    with _LOCK:
        store = _load_all()
        items = store.get(bucket, [])
        sid = "sim_%d_%d" % (int(time.time() * 1000), len(items))
        entry = {
            "id": sid,
            "name": (name or "untitled").strip()[:80] or "untitled",
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "params": params or {},
            "results": results or {},
        }
        items.insert(0, entry)
        store[bucket] = items
        _save_all(store)
    return entry


@router.get("/saved")
def list_saved(request: Request):
    """This user's saved simulation snapshots (newest first).  A snapshot of an archived configuration (its params name the
    die and the configuration) is left out for a non-admin, like the configuration itself (owner 2026-11-22)."""
    sims = _load_all().get(_bucket(request), [])
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import motor_access as MA
    if not MA.catalog_access(request.headers.get("authorization")).get("is_admin"):
        sims = [s for s in sims if not _hidden_for_archive(s)]
    return {"sims": sims}


def _hidden_for_archive(entry: dict) -> bool:
    """A non-admin does not see a snapshot of an archived shared configuration.  A stamped snapshot names its source; an
    older one (saved before 2026-11-22: neither ``source`` nor die / config in its params) is identified BY ITS MACHINE - its
    geometry and materials against every archived configuration (round 3 of #820).  A damaged archive hides every
    unidentified older snapshot (fail closed, like the shared catalog itself)."""
    from motor_ai_sim import config_archive as CA
    die, cfg = _source_of(entry)
    if die and cfg:
        return CA.is_archived(die, cfg)
    try:
        machines = _archived_machines()
    except CA.ArchiveUnavailable:
        return True
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    return any(_same_machine(params, m) for m in machines)


_MACHINES: Dict[str, Any] = {"sig": None, "rows": []}
_IDENTITY_KEYS = ("num_slots", "num_poles", "stator_diameter")


def _archived_machines() -> List[Dict[str, Any]]:
    """``[{"geometry", "materials"}]`` of every archived configuration of the SHARED catalog (die geometry + configuration
    overrides), read once per change of the archive file.  Raises ``ArchiveUnavailable`` for a damaged archive."""
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import workspace as W
    keys = CA.archived_keys()
    try:
        st = CA.store_path().stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    if sig is not None and _MACHINES["sig"] == sig:
        return _MACHINES["rows"]
    rows: List[Dict[str, Any]] = []
    root = Path(str(W.shared_root())) / "dies"
    for k in keys:
        die, _, cfg = k.partition("/")
        try:
            die_doc = yaml.safe_load((root / die / "die.yaml").read_text(encoding="utf-8")) or {}
            cfg_doc = yaml.safe_load((root / die / f"{cfg}.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            continue
        if isinstance(cfg_doc.get("config"), dict):
            cfg_doc = cfg_doc["config"]
        geo = dict(die_doc.get("geometry") or {})
        geo.update({g: v for g, v in (cfg_doc.get("geometry_overrides") or {}).items() if v is not None})
        rows.append({"geometry": geo, "materials": dict(cfg_doc.get("materials") or {})})
    _MACHINES.update(sig=sig, rows=rows)
    return rows


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _same_machine(params: Dict[str, Any], machine: Dict[str, Any]) -> bool:
    """Is the snapshot's machine this archived configuration?  The identity keys (slots, poles, lamination diameter) must be
    in both and equal, and so must every other geometry input both carry and every material both name."""
    from motor_ai_sim.geometry.motor_geometry import is_compared_geometry_input
    geo = machine["geometry"]
    for k in _IDENTITY_KEYS:
        a, b = _num(params.get(k)), _num(geo.get(k))
        if a is None or b is None or abs(a - b) > 1e-6 * max(1.0, abs(b)):
            return False
    for k in set(params) & set(geo):
        a, b = _num(params.get(k)), _num(geo.get(k))
        if a is None or b is None or not is_compared_geometry_input(k, params, geo):
            continue
        if abs(a - b) > 1e-6 * max(1.0, abs(b)):
            return False
    for part, name in (machine.get("materials") or {}).items():
        mine = params.get(f"mat_{part}")
        if isinstance(mine, str) and mine and isinstance(name, str) and name and mine != name:
            return False
    return True


def _source_of(entry: dict) -> tuple:
    """``(die, config)`` the snapshot was made from: its ``source`` (stamped at save since 2026-11-22), else what its params name;
    ``("", "")`` for an older snapshot that names none (it then belongs to no catalog configuration and stays listed)."""
    src = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    return (str(src.get("die") or params.get("die") or ""), str(src.get("config") or params.get("config") or ""))


def _active_source() -> dict:
    """The configuration open in the editor when the snapshot is saved (the family context), or ``{}``."""
    try:
        from motor_ai_sim.routes import family as F
        ctx = F._read_ctx() or {}
        return {"die": str(ctx.get("die") or ""), "config": str(ctx.get("config") or "")} if ctx.get("config") else {}
    except Exception:                                       # noqa: BLE001 - a snapshot never fails for this
        return {}


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
            "source": _active_source(),           # which configuration it was made from: an archived one's snapshots follow it
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
    """Delete one of THIS user's snapshots by id.

    Also sweeps the anonymous 'local' bucket for the same id.  Not a privacy
    hole — ids are minted per save, so a signed-in user can only ever match a
    'local' row that is their own earlier anonymous save (or one an offline
    script wrote into both buckets).  Without this, a row saved before signing
    in survives the delete and reappears on the next sign-out, which reads as
    "delete does not work".
    """
    with _LOCK:
        store = _load_all()
        bucket = _bucket(request)
        removed = 0
        for b in {bucket, "local"}:
            items = store.get(b, [])
            kept = [s for s in items if s.get("id") != sim_id]
            removed += len(items) - len(kept)
            store[b] = kept
        if not removed:
            raise HTTPException(status_code=404, detail=f"sim '{sim_id}' not found")
        _save_all(store)
    return {"status": "ok", "deleted": sim_id, "rows_removed": removed}


@router.delete("/saved")
def clear_saved(request: Request):
    """Delete every snapshot in THIS user's library."""
    with _LOCK:
        store = _load_all()
        store[_bucket(request)] = []
        _save_all(store)
    return {"status": "ok", "cleared": True}
