"""Where Stage A end-effect passports live (owner 2026-10-27: a 3-D factor is a property of the MACHINE, not of the
account that happened to compute it).

Two layers, keyed by the same ``routes.simulation._geometry_fingerprint``:

* the SHARED store ``<shared>/end_effect_passports.json`` (``workspace.shared_root()``) - every account reads it; the
  admin adopt route and the catalog passport builder write here;
* the WORKSPACE store ``<workspace>/end_effect_passports.json`` - the older per-account file; still read, and an entry
  there wins over the shared one for the same fingerprint (a local re-measurement).

Reads never raise: an unreadable file is an empty layer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

FILE = "end_effect_passports.json"


def _read(path: Path) -> Dict[str, dict]:
    try:
        d = json.loads(path.read_text(encoding="utf-8")) or {}
        return {str(k): v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 - no passport is a normal state
        return {}


def shared_path() -> Path:
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / FILE


def workspace_path() -> Path:
    from motor_ai_sim.workspace import root
    return Path(str(root())) / FILE


def load() -> Dict[str, dict]:
    """Shared entries overlaid by this workspace's own."""
    merged = dict(_read(shared_path()))
    try:
        ws = workspace_path()
        if ws != shared_path():
            merged.update(_read(ws))
    except Exception:  # noqa: BLE001
        pass
    return merged


def put_shared(fp: str, entry: dict) -> bool:
    """Write one entry into the shared store (atomic replace); returns whether it replaced one."""
    path = shared_path()
    store = _read(path)
    replaced = fp in store
    store[str(fp)] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return replaced
