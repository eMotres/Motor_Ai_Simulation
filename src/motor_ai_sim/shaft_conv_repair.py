"""One-off repair: stored duty results that lost ``shaft_conv`` (PR #900 follow-up).

``/api/family/duty_result`` used to drop ``shaft_conv`` (it was not in the stored key list), so every point saved from a
marked run (``summary.shaft_convention == "fe_on_shaft_k3d_v1"``) carried the marked summary but a result without the flag
and was shown as the earlier basis.  The route is fixed; this module finds the records already stored that way and sets the
ONE key ``shaft_conv = 1`` on the result that sits beside a marked summary.  Nothing else of a record changes.

Pure part (``plan_config`` / ``apply_config``) works on one parsed configuration document; ``scan`` / ``repair`` walk the
catalog through the application's own helpers (``routes.family``: die listing, load, and the save path ``_save_yaml`` with its
frozen-revision guard).  ``repair`` never runs without ``apply=True`` and, with it, copies each file to ``backup_dir`` first (an exclusive, collision-proof
file name).  The save is the application's own ``_save_yaml`` in its METADATA-ONLY mode: no layer redirection, no derived-geometry
refresh, no redaction (a save that would need one is refused).  A configuration that is a frozen product revision or has a
production record, or whose protection metadata cannot be read, is NEVER touched: dry run and apply both report it with the
reason, and the check is repeated just before the backup and the save.  Before writing, the re-parsed new text must equal the
original document except for the planned ``shaft_conv`` keys.
"""
from __future__ import annotations

import copy
import os
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


def skip_reason(die: str, cfg: str) -> Optional[str]:
    """Why this configuration must not be repaired (None = it may be): a frozen product revision, a production record, or
    protection metadata that cannot be read (refusal, never a guess)."""
    try:
        from motor_ai_sim import motor_revisions as _mr
        hit = _mr.find(die, cfg)
        if hit is not None and hit[1].get("frozen"):
            return "frozen_revision"
    except Exception:  # noqa: BLE001
        return "protection_unreadable"
    try:
        from motor_ai_sim.production import store as _ps
        if _ps.production_exists(die, cfg):
            return "production_linked"
    except Exception:  # noqa: BLE001
        return "protection_unreadable"
    return None


def scan() -> List[Dict[str, Any]]:
    """Dry run: ``[{"die", "config", "duty", "drive"[, "skip"]}]`` of every stored result to repair (no values); ``skip`` names why
    a row would not be touched."""
    out = []
    for family, die, cfg in _walk():
        try:
            doc = family._load_yaml(family._cfg_file(die, cfg), "configuration")
        except Exception:  # noqa: BLE001 - an unreadable file is reported by the script, never repaired
            out.append({"die": die, "config": cfg, "duty": None, "drive": None, "error": "unreadable"})
            continue
        plan = plan_config(doc)
        why = skip_reason(die, cfg) if plan else None
        out.extend({"die": die, "config": cfg, **t, **({"skip": why} if why else {})} for t in plan)
    return out


def _norm(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The document without the planned key at its targets: before and after must agree on everything else."""
    d = copy.deepcopy(doc)
    for duty in d.get("duties") or []:
        if not isinstance(duty, dict):
            continue
        recs = [duty] + [r for r in (duty.get("runs") or {}).values()] if isinstance(duty.get("runs"), dict) else [duty]
        for rec in recs:
            if isinstance(rec, dict) and isinstance(rec.get("summary"), dict) and _sc.has_marker(rec["summary"]) \
                    and isinstance(rec.get("result"), dict):
                rec["result"].pop(KEY, None)
    return d


def _exclusive_backup(src: Path, dest_dir: Path, stem: str, stamp: str) -> Path:
    """Copy ``src``'s bytes to a NEW file in ``dest_dir`` (never overwrites: O_EXCL, numbered on a clash)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    n = 0
    while True:
        name = f"{stem}.yaml.bak-shaftconv-{stamp}" + (f"-{n}" if n else "")
        try:
            fd = os.open(str(dest_dir / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            n += 1
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return dest_dir / name


def repair(*, apply: bool = False, backup_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Dry run by default.  With ``apply`` every changed configuration is first copied into ``backup_dir`` and then saved
    through the application's own ``_save_yaml`` in metadata-only mode.  Rows with a ``skip`` reason are never touched."""
    rows = scan()
    if not apply:
        return {"applied": False, "rows": rows, "saved": 0, "failed": []}
    if backup_dir is None:
        raise ValueError("apply needs a backup_dir")
    import yaml
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved, failed = 0, []
    todo = sorted({(r["die"], r["config"]) for r in rows if r.get("duty") is not None and not r.get("skip")})
    from motor_ai_sim.routes import family
    for die, cfg in todo:
        p = family._cfg_file(die, cfg)
        try:
            why = skip_reason(die, cfg)                         # again, right before the backup and the save
            if why:
                failed.append({"die": die, "config": cfg, "error": why})
                continue
            doc = family._load_yaml(p, "configuration")
            if not plan_config(doc):
                continue
            before = _norm(doc)
            new_doc = copy.deepcopy(doc)
            n = apply_config(new_doc)
            reparsed = yaml.safe_load(yaml.safe_dump(new_doc, sort_keys=False, allow_unicode=True))
            if _norm(reparsed) != before:                       # the re-parsed text may differ ONLY by the planned keys
                failed.append({"die": die, "config": cfg, "error": "unplanned_change"})
                continue
            _exclusive_backup(p, Path(backup_dir) / die, cfg, stamp)
            family._save_yaml(p, new_doc, history_note=f"shaft_conv set on {n} stored result(s) beside a marked summary",
                              metadata_only=True)
            saved += n
        except Exception as exc:  # noqa: BLE001 - one refused file (e.g. a frozen revision) never stops the rest
            failed.append({"die": die, "config": cfg, "error": type(exc).__name__})
    return {"applied": True, "rows": rows, "saved": saved, "failed": failed}
