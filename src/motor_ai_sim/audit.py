"""Forensic trail for the two files that define *which machine* is loaded.

On 2026-08-06 the active geometry silently became a different motor (150 mm /
24s28p → 30 mm / 12s14p) and the preset that held the user's design was
overwritten with it.  Nothing in the system could say who wrote either file:
there was no request log for the running backend and no history of the JSON.
Physics bugs are recoverable; losing a day of optimization because the machine
changed under you is not.

Two cheap instruments, both write-only and both fail-open (an audit must never
be the reason an edit cannot be saved):

* ``record_write`` — one JSONL line per write to the live geometry or to a
  preset: what changed, from where in the code, in which process.
* ``snapshot_presets`` — a timestamped copy of ``motor_presets.json`` BEFORE it
  is mutated, so a clobbered design can always be recovered.
"""

from __future__ import annotations

import json
import os
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_PATH = _ROOT / "logs" / "geometry_audit.jsonl"
_HISTORY_DIR = _ROOT / "config" / ".presets_history"
_KEEP = 40

# The fields that say WHICH MACHINE this is.  A change in any of them is a
# different motor, not an edit — that is the event worth reconstructing later.
IDENTITY = ("stator_diameter", "motor_length", "num_slots", "num_poles",
            "tooth_width", "magnet_height", "slot_height")


def _identity(geo: Optional[Mapping[str, Any]]) -> dict:
    if not isinstance(geo, Mapping):
        return {}
    return {k: geo.get(k) for k in IDENTITY if geo.get(k) is not None}


def _caller(skip: int = 2) -> list:
    """Innermost application frames — an HTTP handler and an internal solve
    call look nothing alike here, which is the whole point."""
    out = []
    for fr in reversed(traceback.extract_stack()[:-skip]):
        f = fr.filename.replace("\\", "/")
        if "/motor_ai_sim/" not in f and "/scripts/" not in f and "/tests/" not in f:
            continue
        out.append(f"{f.split('/motor_ai_sim/')[-1]}:{fr.lineno} {fr.name}")
        if len(out) >= 6:
            break
    return out


def record_write(target: str, before: Optional[Mapping[str, Any]],
                 after: Optional[Mapping[str, Any]], *,
                 note: str = "", client: str = "") -> None:
    """Append one audit line.  Never raises."""
    try:
        b, a = _identity(before), _identity(after)
        changed = sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "t": datetime.now().astimezone().isoformat(timespec="seconds"),
            "target": target,
            "pid": os.getpid(),
            "changed": changed,
            "before": {k: b.get(k) for k in changed},
            "after": {k: a.get(k) for k in changed},
            "note": note,
            "client": client,
            "stack": _caller(),
        }
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def snapshot_presets(path: Path, note: str = "") -> None:
    """Copy the presets file aside before it is mutated.  Never raises."""
    try:
        if not Path(path).exists():
            return
        _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in note)[:40]
        shutil.copy2(path, _HISTORY_DIR / f"presets_{stamp}{('_' + tag) if tag else ''}.json")
        keep = sorted(_HISTORY_DIR.glob("presets_*.json"))[:-_KEEP]
        for old in keep:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass
