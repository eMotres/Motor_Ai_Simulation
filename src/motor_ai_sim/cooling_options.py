"""Per-motor cooling options — which cooling the UI may offer for a die/config.

The Ø40 family ("CIANO14 40 new", configurations L12 6S and L20 12S) is a drone
motor: air-cooled only, with the propeller's own slipstream as the only airflow
(owner, 2026-10-05).  Configure must therefore show ONLY "Air — propeller" for it,
with the propellers that are allowed for it.  That choice is data, not code, so it
lives in ``config/cooling_options.yaml``::

    version: 1
    dies:
      "CIANO14 40 new":
        cooling_options: [propeller_air]
        propellers: [tmotor_fpv_10x5, tmotor_p12x4, ...]
        configs:                       # optional per-configuration override
          L20: {propellers: [tmotor_fpv_13x10]}

A die with no entry is UNRESTRICTED (``cooling_options`` is ``None``): every
existing cooling mode stays on offer, exactly as before this file existed.

Layering follows the passport store: the repository file is the default and
``<shared>/cooling_options.yaml`` on the server wins per die when it exists.  The
die's own ``die.yaml`` is deliberately NOT touched — the die lives on the server
and a cooling choice is a property of how the machine is used, not of its
lamination.  This module only READS.

The vocabulary of ``cooling_options`` is open (a string per mode); the one this
change introduces is ``propeller_air`` = air speed computed from rpm and the
chosen propeller (``motor_ai_sim.propeller``).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

log = logging.getLogger(__name__)

_REPO_FILE = Path(__file__).resolve().parents[2] / "config" / "cooling_options.yaml"
#: Overridden by tests; a moved path wins outright and the shared layer is skipped.
_FILE: Path = _REPO_FILE

PROPELLER_AIR = "propeller_air"


def _read(path: Path) -> Dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:                                # noqa: BLE001
        log.warning("cooling options: %s is unreadable: %s", path, exc)
        return {}
    dies = raw.get("dies") if isinstance(raw, dict) else None
    return dies if isinstance(dies, dict) else {}


def _layers() -> List[tuple]:
    out = []
    if _FILE.is_file():
        out.append(("repo" if _FILE == _REPO_FILE else "file", _read(_FILE)))
    if _FILE == _REPO_FILE:
        try:
            from motor_ai_sim.workspace import shared_root
            p = Path(str(shared_root())) / "cooling_options.yaml"
            if p.is_file() and p.resolve() != _REPO_FILE.resolve():
                out.append(("server", _read(p)))
        except Exception:                                   # noqa: BLE001
            pass
    return out


def _clean_list(v: Any) -> Optional[List[str]]:
    if v is None:
        return None
    if not isinstance(v, (list, tuple)):
        return None
    return [str(x).strip() for x in v if str(x).strip()]


def cooling_options(die: str, config: Optional[str] = None) -> Dict[str, Any]:
    """The cooling options for ``die`` (and ``config``, when given).

    ``cooling_options`` / ``propellers`` are ``None`` when nothing restricts the
    die; ``restricted`` says so in one flag.  ``source`` names the layer the entry
    came from (``repo`` / ``server`` / ``none``).
    """
    die = str(die or "").strip()
    cfg = str(config or "").strip()
    entry: Optional[Dict[str, Any]] = None
    source = "none"
    for src, dies in _layers():
        if die in dies and isinstance(dies[die], dict):
            entry, source = dies[die], src               # later layer (server) wins
    opts = props = None
    if entry is not None:
        opts = _clean_list(entry.get("cooling_options"))
        props = _clean_list(entry.get("propellers"))
        over = (entry.get("configs") or {}).get(cfg) if cfg else None
        if isinstance(over, dict):
            if "cooling_options" in over:
                opts = _clean_list(over.get("cooling_options"))
            if "propellers" in over:
                props = _clean_list(over.get("propellers"))
    return {"die": die, "config": cfg or None, "cooling_options": opts,
            "propellers": props, "restricted": opts is not None, "source": source}
