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
_REPO_CONFIG_DIR = (_ROOT / "config").resolve()


def audit_path() -> Path:
    """WHERE this process writes its geometry audit — resolved per call.

    The path was pinned to the repo's ``logs/geometry_audit.jsonl``, so every
    process that was NOT the owner's API — pytest's TestClient, a sandbox
    uvicorn on another port with ``MOTOR_AI_SIM_CONFIG`` redirected — wrote its
    PUTs into the owner's forensic trail (seen 2026-09-20: pid 48328 on
    localhost:5199 and test pids beside the owner's 12:47 entry).  The trail
    exists to answer "who changed MY machine?", and foreign machines in it are
    noise at best and a false accusation at worst.

    Rule, in order:
      1. ``MOTOR_AI_SIM_LOG_DIR`` set → ``<that dir>/geometry_audit.jsonl``;
      2. the process config (``config.DEFAULT_CONFIG_PATH``) lives OUTSIDE the
         repo's ``config/`` (a redirected sandbox) → ``<config dir>/logs/…``,
         i.e. the audit follows the machine it describes;
      3. otherwise (this workstation's API, no env) the repo's ``logs/`` —
         byte-identical to before.
    A monkeypatched ``_AUDIT_PATH`` in the module dict still wins.
    """
    _ov = globals().get("_AUDIT_PATH")
    if _ov is not None:
        return Path(str(_ov))
    env = os.environ.get("MOTOR_AI_SIM_LOG_DIR", "").strip()
    if env:
        return Path(env).expanduser() / "geometry_audit.jsonl"
    try:
        from motor_ai_sim import config as _config
        cfg_dir = Path(str(_config.DEFAULT_CONFIG_PATH)).expanduser().resolve().parent
        if cfg_dir != _REPO_CONFIG_DIR:
            return cfg_dir / "logs" / "geometry_audit.jsonl"
    except Exception:                   # noqa: BLE001 — never break an audit
        pass
    return _ROOT / "logs" / "geometry_audit.jsonl"
# The presets backups go BESIDE THE STORE THEY BACK UP — i.e. beside the config
# this process is pointed at (``MOTOR_AI_SIM_CONFIG``).  Pinned to the repo's own
# config/, `snapshot_presets` dropped a redirected process's sandbox presets into
# the user's real `.presets_history/` and evicted their genuine backups past
# `_KEEP` — the recovery copies that exist precisely for the 2026-08-06 accident.
# With no env var set this is byte-identical to `_ROOT / "config" / …`.
#
# Migration Stage 1: resolved PER CALL against the caller's workspace.  A
# monkeypatched ``_HISTORY_DIR`` in the module dict still wins; a plain read of
# the name goes through ``__getattr__`` below.
def _history_dir() -> Path:
    _ov = globals().get("_HISTORY_DIR")
    if _ov is not None:
        return Path(str(_ov))
    try:
        from motor_ai_sim.workspace import root as _ws_root
        return _ws_root() / ".presets_history"
    except Exception:                   # noqa: BLE001 — never break a snapshot
        return _ROOT / "config" / ".presets_history"


def __getattr__(name):
    if name == "_HISTORY_DIR":
        return _history_dir()
    if name == "_AUDIT_PATH":
        return audit_path()
    raise AttributeError(name)


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
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
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
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def snapshot_presets(path: Path, note: str = "") -> None:
    """Copy the presets file aside before it is mutated.  Never raises."""
    try:
        if not Path(path).exists():
            return
        _history_dir().mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in note)[:40]
        shutil.copy2(path, _history_dir() / f"presets_{stamp}{('_' + tag) if tag else ''}.json")
        keep = sorted(_history_dir().glob("presets_*.json"))[:-_KEEP]
        for old in keep:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass
