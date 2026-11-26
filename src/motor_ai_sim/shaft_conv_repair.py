"""One-off repair: stored duty results that lost ``shaft_conv`` (PR #900 follow-up).

``/api/family/duty_result`` used to drop ``shaft_conv`` (it was not in the stored key list), so every point saved from a
marked run (``summary.shaft_convention == "fe_on_shaft_k3d_v1"``) carried the marked summary but a result without the flag
and was shown as the earlier basis.  The route is fixed; this module finds the records already stored that way and sets the
ONE key ``shaft_conv = 1`` on the result that sits beside a marked summary.  Nothing else of a record changes.

Pure part (``plan_config`` / ``apply_config``) works on one parsed configuration document; ``scan`` / ``repair`` walk the
catalog through the application's own helpers (``routes.family``: die listing, load, and the save path ``_save_yaml`` with its
frozen-revision guard).  ``repair`` never runs without ``apply=True`` and, with it, copies each file to ``backup_dir`` first.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim import shaft_convention as _sc

KEY = "shaft_conv"


def _needs(summary: Any, result: Any) -> bool:
    return (isinstance(summary, dict) and _sc.has_marker(summary)
            and isinstance(result, dict) and bool(result) and result.get(KEY) != 1)


def _targets(doc: Dict[str, Any]):
    """(duty name, drive or None for the headline result, the result dict) of every record to repair."""
    for duty in doc.get("duties") or []:
        if not isinstance(duty, dict):
            continue
        name = str(duty.get("name") or "")
        if _needs(duty.get("summary"), duty.get("result")):
            yield name, None, duty["result"]
        runs = duty.get("runs")
        for drive, rec in (runs.items() if isinstance(runs, dict) else []):
            if isinstance(rec, dict) and _needs(rec.get("summary"), rec.get("result")):
                yield name, str(drive), rec["result"]


def plan_config(doc: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
    """What a repair of this configuration would touch: duty and drive only, no values."""
    return [{"duty": n, "drive": d} for n, d, _ in _targets(doc)]


def apply_config(doc: Dict[str, Any]) -> int:
    """Set ``shaft_conv = 1`` on every record ``plan_config`` lists, in place; returns how many."""
    n = 0
    for _, _, result in _targets(doc):
        result[KEY] = 1.0
        n += 1
    return n


def _walk():
    from motor_ai_sim.routes import family
    for entry in family._iter_die_entries():
        die = str(entry["name"])
        for f in sorted(Path(str(entry["dir"])).glob("*.yaml")):
            if f.name != "die.yaml":
                yield family, die, f.stem


def scan() -> List[Dict[str, Any]]:
    """Dry run: ``[{"die", "config", "duty", "drive"}]`` of every stored result to repair (no values)."""
    out = []
    for family, die, cfg in _walk():
        try:
            doc = family._load_yaml(family._cfg_file(die, cfg), "configuration")
        except Exception:  # noqa: BLE001 - an unreadable file is reported by the script, never repaired
            out.append({"die": die, "config": cfg, "duty": None, "drive": None, "error": "unreadable"})
            continue
        out.extend({"die": die, "config": cfg, **t} for t in plan_config(doc))
    return out


def repair(*, apply: bool = False, backup_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Dry run by default.  With ``apply`` every changed configuration is first copied into ``backup_dir`` and then saved
    through the application's own ``_save_yaml``."""
    rows = scan()
    if not apply:
        return {"applied": False, "rows": rows, "saved": 0, "failed": []}
    if backup_dir is None:
        raise ValueError("apply needs a backup_dir")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved, failed = 0, []
    todo = sorted({(r["die"], r["config"]) for r in rows if r.get("duty") is not None})
    from motor_ai_sim.routes import family
    for die, cfg in todo:
        p = family._cfg_file(die, cfg)
        try:
            doc = family._load_yaml(p, "configuration")
            if not plan_config(doc):
                continue
            dest = Path(backup_dir) / die
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest / f"{cfg}.yaml.bak-shaftconv-{stamp}")
            n = apply_config(doc)
            family._save_yaml(p, doc, history_note=f"shaft_conv set on {n} stored result(s) beside a marked summary")
            saved += n
        except Exception as exc:  # noqa: BLE001 - one refused file (e.g. a frozen revision) never stops the rest
            failed.append({"die": die, "config": cfg, "error": type(exc).__name__})
    return {"applied": True, "rows": rows, "saved": saved, "failed": failed}
