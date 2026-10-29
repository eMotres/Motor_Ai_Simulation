"""Saved Sweep results - a durable, per-workspace store.

Incident 2026-10-29: the owner's L100 sweep was interrupted, finished in a
sandbox, and could not be put back into the app.  The "saved" store the Sweep
tab offered was ``<repo>/optimization_saves`` - ``/app/optimization_saves`` in
the server image, which is NOT on a mounted volume, so every deploy replaced the
image and dropped every saved scan.

Where a save lives now
----------------------
``<workspace root>/optimization_saves/<id>.json`` - the same per-workspace
layout the ``.last_scan.json`` / ``.last_descent.json`` stores use, so it is
under ``/srv/motres/workspaces/<ws_id>/`` on the server (a mounted volume) and
beside ``motor_config.yaml`` on a workstation.  Another account's workspace is a
different folder: per-user isolation is the directory, there is no id lookup that
could cross it.

What a save carries
-------------------
The scan result as it was, plus the MACHINE it belongs to (die / config / duty /
fingerprint / swept variables - the stamp the run was started with, see
``machine_binding``), so a reopened scan can say which machine it is for.  A
scan that never named its machine is stored as ``{"unattributed": true}`` - it is
NEVER stamped with whatever happens to be loaded at save time.

The old folder
--------------
``<repo>/optimization_saves`` is imported ONCE into a workspace (marker file
``.legacy_imported``), copy not move.  In a multi-user server only an admin's
workspace takes it: the old folder was one global store, not any customer's.

Pure helpers: no FastAPI, no scan state.  ``routes/optimization.py`` owns the
routes and the live scan slot.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

__all__ = [
    "SAVE_DIRNAME", "LEGACY_MARKER", "ENV_LEGACY_DIR", "ID_RE",
    "saves_dir", "legacy_dir", "import_legacy_once", "safe_path",
    "validate_result", "build_entry", "write_entry", "read_entry",
    "list_entries", "delete_entry", "find_by_run_id", "meta",
    "new_id", "unattributed", "clean_machine",
]

SAVE_DIRNAME = "optimization_saves"
LEGACY_MARKER = ".legacy_imported"
#: Overrides the old folder (tests; a server whose old image path differs).
ENV_LEGACY_DIR = "OPTIMIZATION_SAVES_LEGACY_DIR"
ID_RE = re.compile(r"^[A-Za-z0-9_]+$")

#: Machine fields a save keeps (everything else in a stamp is not for display).
_MACHINE_KEYS = ("die", "config", "duty", "fingerprint", "swept", "declared")


# ── where ────────────────────────────────────────────────────────────────────
def saves_dir(create: bool = False) -> Path:
    """The CALLER's folder of saved sweeps (``workspace.root()``, per request)."""
    from motor_ai_sim.workspace import root
    d = Path(str(root())) / SAVE_DIRNAME
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def legacy_dir() -> Optional[Path]:
    """The pre-2026-10-30 global folder, or ``None`` when it does not exist."""
    raw = os.environ.get(ENV_LEGACY_DIR, "").strip()
    d = Path(raw).expanduser() if raw else Path(__file__).resolve().parents[2] / SAVE_DIRNAME
    return d if d.is_dir() else None


def safe_path(sid: str) -> Path:
    """``<saves dir>/<sid>.json`` for a well-formed id, else ``ValueError``."""
    if not ID_RE.match(sid or ""):
        raise ValueError("bad id")
    return saves_dir() / f"{sid}.json"


def _may_take_legacy() -> bool:
    try:
        from motor_ai_sim import workspace as _ws
        return (not _ws.layering()) or _ws.is_admin()
    except Exception:                                    # noqa: BLE001
        return False


def import_legacy_once() -> int:
    """Copy the old global folder into THIS workspace, once.  Returns files copied.

    Never moves or deletes the old files, never overwrites a save that already
    exists here, and is a no-op for a workspace that may not take it (see the
    module docstring).  Best effort: a failure here must not break the list."""
    try:
        if not _may_take_legacy():
            return 0
        src = legacy_dir()
        if src is None:
            return 0
        dst = saves_dir(create=True)
        marker = dst / LEGACY_MARKER
        if marker.exists():
            return 0
        n = 0
        for f in sorted(src.glob("*.json")):
            if not ID_RE.match(f.stem):
                continue
            target = dst / f.name
            if target.exists():
                continue
            try:
                shutil.copy2(f, target)
                n += 1
            except OSError as e:                         # noqa: PERF203
                log.warning("legacy sweep save %s not imported: %s", f.name, e)
        marker.write_text(json.dumps({"from": str(src), "copied": n,
                                      "at": datetime.now().isoformat(timespec="seconds")}),
                          encoding="utf-8")
        if n:
            log.info("imported %d legacy saved sweeps from %s into %s", n, src, dst)
        return n
    except Exception as e:                               # noqa: BLE001
        log.warning("legacy sweep saves not imported: %s", e)
        return 0


# ── content ──────────────────────────────────────────────────────────────────
def unattributed() -> Dict[str, Any]:
    """The machine of a scan that never recorded one - explicitly NOT any machine."""
    return {"unattributed": True, "fingerprint": ""}


def _has_id(p: Any) -> bool:
    return (isinstance(p, dict) and isinstance(p.get("geom_id"), int)
            and not isinstance(p.get("geom_id"), bool)
            and isinstance(p.get("op_index"), int) and not isinstance(p.get("op_index"), bool))


def validate_result(result: Any) -> Tuple[int, int]:
    """``(n_points, n_without_id)`` of a scan result, or ``ValueError``.

    A scan result is a dict whose ``points`` is a non-empty list of dicts.  A
    point without ``geom_id`` / ``op_index`` cannot be selected or applied, so it
    is counted (the caller reports it) - but a result with NO identifiable point
    at all is refused: it would reopen as a chart nobody can use."""
    if not isinstance(result, dict):
        raise ValueError("a scan result must be a JSON object")
    pts = result.get("points")
    if not isinstance(pts, list) or not pts:
        raise ValueError("the scan result has no points")
    dicts = [p for p in pts if isinstance(p, dict)]
    if not dicts:
        raise ValueError("the scan result has no point objects")
    without = sum(1 for p in dicts if not _has_id(p))
    if without == len(dicts):
        raise ValueError("no point of the scan result carries geom_id / op_index")
    return len(dicts), without


def new_id() -> str:
    """A timestamp id, never one that already exists here (the Windows clock ticks
    in ~15 ms steps, so two saves in a row can read the same microsecond)."""
    base = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    sid, n = base, 0
    while (saves_dir() / f"{sid}.json").exists():
        n += 1
        sid = f"{base}_{n}"
    return sid


def clean_machine(m: Any) -> Dict[str, Any]:
    if not isinstance(m, dict) or m.get("unattributed"):
        return unattributed()
    out = {k: m[k] for k in _MACHINE_KEYS if m.get(k) not in (None, "", [])}
    return out if out.get("fingerprint") or out.get("die") else unattributed()


def build_entry(result: Dict[str, Any], *, name: str = "",
                config: Optional[Dict[str, Any]] = None,
                declared_machine: Optional[Dict[str, Any]] = None,
                imported: bool = False, source: str = "") -> Dict[str, Any]:
    """The JSON document of one save.

    The machine is the result's OWN stamp.  ``declared_machine`` (die / config /
    duty typed by an admin importing a file that carries none) fills in only the
    names, marked ``declared`` - it never invents a fingerprint."""
    n_pts, n_noid = validate_result(result)
    ts = datetime.now()
    sid = new_id()
    res = dict(result)
    machine = clean_machine(res.get("machine"))
    if declared_machine and machine.get("unattributed"):
        dm = {k: str(declared_machine[k]).strip()[:120] for k in ("die", "config", "duty")
              if str(declared_machine.get(k) or "").strip()}
        if dm:
            machine = {**dm, "declared": True}
    # A run id is what Apply is matched on; a result produced elsewhere may lack one.
    if not str(res.get("run_id") or "").strip():
        res["run_id"] = f"imported_{sid}"
    # The live-state markers belong to a process, not to the saved result.
    for k in ("restored_from_disk", "reopened_from_saved", "partial"):
        res.pop(k, None)
    res["machine"] = machine
    cfg = dict(config or {})
    cfg.setdefault("steps_per_period", res.get("steps_per_period"))
    cfg.setdefault("variables", res.get("variables"))
    cfg.setdefault("operating_points", res.get("operating_points"))
    name = (name or "").strip()[:200] or f"scan {ts.strftime('%Y-%m-%d %H:%M')}"
    entry: Dict[str, Any] = {
        "id": sid, "name": name, "created_at": ts.isoformat(timespec="seconds"),
        "config": cfg, "result": res, "machine": machine,
        "n_points_without_id": n_noid,
    }
    if imported:
        entry["imported"] = True
        entry["source"] = str(source)[:300]
    return entry


def write_entry(entry: Dict[str, Any]) -> Path:
    """Atomic write (tmp + replace): a crash mid-write never leaves half a save."""
    p = safe_path(str(entry["id"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entry, default=float), encoding="utf-8")
    os.replace(tmp, p)
    return p


def read_entry(sid: str) -> Optional[Dict[str, Any]]:
    p = safe_path(sid)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def delete_entry(sid: str) -> bool:
    p = safe_path(sid)
    if p.exists():
        p.unlink()
        return True
    return False


def meta(d: Dict[str, Any]) -> Dict[str, Any]:
    """The list row of one save (no points)."""
    r = d.get("result") or {}
    cfg = d.get("config") or {}
    machine = d.get("machine")
    if not isinstance(machine, dict):
        machine = r.get("machine")
    machine = clean_machine(machine) if machine else unattributed()
    return {
        "id": d["id"], "name": d.get("name", d["id"]),
        "created_at": d.get("created_at", ""),
        "n_geometries": r.get("n_geometries"),
        "n_points": r.get("n_total_points") or len(r.get("points") or []),
        "n_built": r.get("n_built"),
        "n_front": len(r.get("pareto_indices", [])),
        "n_operating_points": len(r.get("operating_points") or []),
        "steps_per_period": cfg.get("steps_per_period") or r.get("steps_per_period"),
        "variables": [v.get("name") for v in (r.get("variables") or []) if isinstance(v, dict)],
        "run_id": r.get("run_id") or "",
        "machine": machine,
        "imported": bool(d.get("imported")),
        "n_points_without_id": d.get("n_points_without_id"),
    }


def list_entries() -> List[Dict[str, Any]]:
    """Every save's list row, newest first.  Imports the old folder first, once."""
    import_legacy_once()
    d = saves_dir()
    if not d.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            out.append(meta(json.loads(f.read_text(encoding="utf-8"))))
        except Exception:                                # noqa: BLE001
            continue
    return out


def find_by_run_id(run_id: str) -> Optional[Dict[str, Any]]:
    """The list row of a save of this run, if one exists (a run is saved once)."""
    rid = str(run_id or "")
    if not rid:
        return None
    for row in list_entries():
        if row.get("run_id") == rid:
            return row
    return None
