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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

__all__ = [
    "SAVE_DIRNAME", "LEGACY_MARKER", "ENV_LEGACY_DIR", "ID_RE",
    "saves_dir", "legacy_dir", "import_legacy_once", "safe_path",
    "validate_result", "build_entry", "write_entry", "read_entry",
    "list_entries", "delete_entry", "find_by_run_id", "meta",
    "new_id", "unattributed", "clean_machine",
    "auto_name", "auto_id", "autosave", "has_run", "has_campaign", "computed_points",
    "REBUILD_MARKER",
]

SAVE_DIRNAME = "optimization_saves"
LEGACY_MARKER = ".legacy_imported"
#: Overrides the old folder (tests; a server whose old image path differs).
ENV_LEGACY_DIR = "OPTIMIZATION_SAVES_LEGACY_DIR"
ID_RE = re.compile(r"^[A-Za-z0-9_]+$")

#: Machine fields a save keeps (everything else in a stamp is not for display).
_MACHINE_KEYS = ("die", "config", "duty", "fingerprint", "swept", "declared",
                 "source_fp", "rebuilt")
#: Sidecar with a save's list row, so listing never parses every save's points.
META_SUFFIX = ".meta"
#: Written by the one-time rebuild of past sweeps (see ``sweep_rebuild``).
REBUILD_MARKER = ".rebuild_done"


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
        out = unattributed()
        if isinstance(m, dict):                 # keep WHERE it came from, claim nothing
            out.update({k: m[k] for k in ("source_fp", "rebuilt") if m.get(k)})
        return out
    out = {k: m[k] for k in _MACHINE_KEYS if m.get(k) not in (None, "", [])}
    if out.get("fingerprint") or out.get("die"):
        return out
    return clean_machine({"unattributed": True, **{k: m[k] for k in ("source_fp", "rebuilt") if m.get(k)}})


def build_entry(result: Dict[str, Any], *, name: str = "",
                config: Optional[Dict[str, Any]] = None,
                declared_machine: Optional[Dict[str, Any]] = None,
                imported: bool = False, source: str = "",
                sid: Optional[str] = None, created_at: Optional[datetime] = None,
                extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The JSON document of one save.

    The machine is the result's OWN stamp.  ``declared_machine`` (die / config /
    duty typed by an admin importing a file that carries none) fills in only the
    names, marked ``declared`` - it never invents a fingerprint."""
    n_pts, n_noid = validate_result(result)
    ts = created_at or datetime.now()
    sid = sid or new_id()
    res = dict(result)
    machine = clean_machine(res.get("machine"))
    if declared_machine and machine.get("unattributed"):
        dm = {k: str(declared_machine[k]).strip()[:120] for k in ("die", "config", "duty")
              if str(declared_machine.get(k) or "").strip()}
        if dm:
            machine = {**dm, "declared": True,
                       **{k: machine[k] for k in ("source_fp", "rebuilt") if k in machine}}
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
    if extra:
        entry.update(extra)
    return entry


def write_entry(entry: Dict[str, Any]) -> Path:
    """Atomic write (tmp + replace): a crash mid-write never leaves half a save."""
    p = safe_path(str(entry["id"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entry, default=float), encoding="utf-8")
    os.replace(tmp, p)
    _write_meta(p, entry)
    return p


def _meta_path(p: Path) -> Path:
    return p.with_suffix(META_SUFFIX)


def _write_meta(p: Path, entry: Dict[str, Any]) -> None:
    """The list row beside the save.  Best effort: a missing sidecar is rebuilt
    from the save the next time the list is read."""
    try:
        mp = _meta_path(p)
        tmp = mp.with_suffix(".meta.tmp")
        tmp.write_text(json.dumps(meta(entry), default=float), encoding="utf-8")
        os.replace(tmp, mp)
    except Exception as e:                               # noqa: BLE001
        log.debug("sweep save meta not written: %s", e)


def read_entry(sid: str) -> Optional[Dict[str, Any]]:
    p = safe_path(sid)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def delete_entry(sid: str) -> bool:
    p = safe_path(sid)
    try:
        _meta_path(p).unlink()
    except OSError:
        pass
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
        # How it got here: saved by the server when the sweep ended (``auto``),
        # rebuilt from the eval logs (``rebuilt``), and how far the run got.
        "auto": bool(d.get("auto")), "rebuilt": bool(d.get("rebuilt")),
        "state": d.get("state") or "",
        "n_done": d.get("n_done"), "n_planned": d.get("n_planned"),
        "campaign_id": d.get("campaign_id") or r.get("campaign_id") or "",
    }


def list_entries() -> List[Dict[str, Any]]:
    """Every save's list row, newest first.  Imports the old folder first, once."""
    import_legacy_once()
    d = saves_dir()
    if not d.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for f in d.glob("*.json"):
        row = _read_meta_row(f)
        if row is not None:
            out.append(row)
    out.sort(key=_when_key, reverse=True)
    return out


def _when_key(row: Dict[str, Any]) -> float:
    """Sort key: when the save was made (older saves wrote local naive times,
    automatic ones UTC-aware times)."""
    try:
        t = datetime.fromisoformat(str(row.get("created_at") or ""))
        return (t.astimezone() if t.tzinfo is None else t).timestamp()
    except ValueError:
        return 0.0


def _read_meta_row(f: Path) -> Optional[Dict[str, Any]]:
    """The list row of one save: its sidecar when that is current, else parsed
    from the save (and the sidecar written) - a save is never edited in place,
    so a sidecar newer than its save is right."""
    mp = _meta_path(f)
    try:
        if mp.exists() and mp.stat().st_mtime >= f.stat().st_mtime:
            row = json.loads(mp.read_text(encoding="utf-8"))
            if isinstance(row, dict) and row.get("id") == f.stem and "auto" in row:
                return row
    except Exception:                                    # noqa: BLE001
        pass
    try:
        entry = json.loads(f.read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        return None
    _write_meta(f, entry)
    return meta(entry)


def find_by_run_id(run_id: str) -> Optional[Dict[str, Any]]:
    """The list row of a save of this run, if one exists (a run is saved once)."""
    rid = str(run_id or "")
    if not rid:
        return None
    for row in list_entries():
        if row.get("run_id") == rid:
            return row
    return None


# ── automatic saves ──────────────────────────────────────────────────────────
# Owner 2026-10-30: he started a new sweep and cancelled it at 0/12, and the
# previous one (7/8 points, hours of FEM) was gone from the tab - ``.last_scan.json``
# holds ONE run, and "Save" was a button he had not pressed.  Every sweep that
# ends with at least one computed point is now filed by the server itself, and a
# new sweep files whatever the tab still held before it replaces it.
def computed_points(result: Any) -> int:
    """Points of a scan result that are real, identified FEM answers."""
    if not isinstance(result, dict):
        return 0
    return sum(1 for p in (result.get("points") or [])
               if _has_id(p) and p.get("feasible") and p.get("fem", True))


def auto_id(run_id: str) -> str:
    """The id of a run's automatic save: one run, one file."""
    return "auto_" + (re.sub(r"[^A-Za-z0-9_]+", "_", str(run_id or "")).strip("_")[:80] or "run")


def _utc_label(when: datetime) -> str:
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc)
    return when.strftime("%Y-%m-%d %H:%M") + " UTC"


def auto_name(result: Dict[str, Any], when: datetime, machine: Optional[Dict[str, Any]] = None) -> str:
    """``<config> <duty> <date time> . <variables>`` - what the list shows."""
    m = machine if machine is not None else clean_machine(result.get("machine"))
    who = " ".join(str(m.get(k)) for k in ("config", "duty") if m.get(k)) \
        or str(m.get("die") or "") or "machine unknown"
    names = [str(v.get("name")) for v in (result.get("variables") or [])
             if isinstance(v, dict) and v.get("name")]
    tail = (" · " + ", ".join(names)) if names else ""
    return f"{who} {_utc_label(when)}{tail}"


def has_run(run_id: str) -> bool:
    """Is a run already filed (automatically or by hand)?"""
    rid = str(run_id or "")
    if not rid:
        return False
    if (saves_dir() / f"{auto_id(rid)}.json").exists():
        return True
    return find_by_run_id(rid) is not None


def has_campaign(campaign_id: str) -> bool:
    cid = str(campaign_id or "")
    return bool(cid) and any(r.get("campaign_id") == cid for r in list_entries())


def autosave(result: Any, *, state: str, n_planned: Optional[int] = None,
             when: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """File a run that ended (``state``: finished | cancelled | machine-changed |
    previous) under its own deterministic id.  Returns the list row, or ``None``
    when there is nothing worth filing (no computed point) or the run is already
    filed.  Never raises: a failed save must not turn a finished sweep into an
    error."""
    try:
        if computed_points(result) < 1:
            return None
        rid = str(result.get("run_id") or "")
        if not rid or has_run(rid):
            return None
        when = when or datetime.now(timezone.utc)
        machine = clean_machine(result.get("machine"))
        n_done = computed_points(result)
        entry = build_entry(
            result, name=auto_name(result, when, machine), sid=auto_id(rid),
            created_at=when,
            extra={"auto": True, "state": state, "n_done": n_done,
                   "n_planned": (int(n_planned) if n_planned else None),
                   "campaign_id": str(result.get("campaign_id") or "")})
        write_entry(entry)
        return meta(entry)
    except Exception as e:                               # noqa: BLE001
        log.warning("sweep not auto-saved: %s", e)
        return None
