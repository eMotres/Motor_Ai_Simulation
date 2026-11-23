"""Replace the SHEET (the stamped lamination) of ONE existing die record - owner 2026-11-23: "one die per size and pole count";
a size and pole count keeps one die record and its sheet is replaced inside it - no new die and no copy.

Only the sheet keys below change; the configuration-level keys of the die (stack length, the wire stack, its winding defaults)
are never written here.  Every stored result of every configuration of the die was computed on the PREVIOUS sheet, so all of them
are marked ``sheet_stale`` (the previous sheet's hash and the date) - the duty in the configuration file and every row of the
results store, a duty without a geometry stamp included: no stamp is stale, never "assumed current".  A mark is cleared only by a
save computed on the new sheet (``refresh_mark`` for the configuration's duty, ``duty_results.record`` for the store rows).

``is_current`` is THE rule every reader asks.  Staff and the project's own members keep seeing a marked number (marked); the
outward readers - the MCP tools, the public catalogue cards and the help assistant's catalogue - leave marked results out.

``plan`` is the dry run (what the owner reads before the write); ``apply`` writes, under the configuration archive's lifecycle
lock, after a backup copy of the die and every configuration file and the die's stored rows (``<shared>/sheet_replace_backups/``),
and refuses while a job runs on the die, when a configuration is a frozen product revision, or when nothing would change."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

#: THE sheet of a die (the lamination as stamped): the only keys this module writes
SHEET_KEYS = ("tooth_width", "tooth2_width", "cut_width", "slot_height", "stator_inner_radius", "rotor_outer_radius",
              "rotor_inner_radius", "magnet_height", "magnet_fill_up", "magnet_up_gap", "rotor_hole", "rotor_house_height")
BACKUP_DIR = "sheet_replace_backups"
MARK = "sheet_stale"
_TOL = 1e-9


class SheetRefused(ValueError):
    """The replacement cannot be done as asked (the text names why and is safe to show)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sheet_of(geometry: Any) -> Dict[str, Any]:
    g = geometry if isinstance(geometry, dict) else {}
    return {k: g.get(k) for k in SHEET_KEYS}


def sheet_hash(geometry: Any) -> str:
    """A short hash of the sheet values (rounded to 1e-6 mm, so a re-serialised float does not move it)."""
    vals = {k: (round(float(v), 6) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
            for k, v in sheet_of(geometry).items()}
    return hashlib.sha256(json.dumps(vals, sort_keys=True).encode()).hexdigest()[:16]


def is_current(entry: Any) -> bool:
    """THE rule: a duty or a stored result row is current unless it carries the mark of a replaced sheet."""
    return not (isinstance(entry, dict) and entry.get(MARK))


def current_duties(duties: Any) -> list:
    return [d for d in (duties or []) if is_current(d)]


def _same(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) <= _TOL * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return a == b


def on_sheet(geometry: Any, die_geometry: Any) -> bool:
    """Was ``geometry`` (a saved stamp, the live machine) computed on the die's sheet?  Every sheet key must be present and equal:
    a stamp silent on a key proves nothing, so it is not "on the sheet"."""
    g, d = (geometry if isinstance(geometry, dict) else {}), sheet_of(die_geometry)
    return all(k in g and g[k] is not None and _same(g[k], d[k]) for k in SHEET_KEYS)


def validate(sheet: Any) -> Dict[str, float]:
    if not isinstance(sheet, dict) or not sheet:
        raise SheetRefused("give the sheet as {key: value}")
    other = sorted(set(sheet) - set(SHEET_KEYS))
    if other:
        raise SheetRefused(f"not a sheet key (only {', '.join(SHEET_KEYS)} change here): {', '.join(other)}")
    out = {}
    for k, v in sheet.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or float(v) < 0:
            raise SheetRefused(f"{k}: a finite number >= 0 is expected")
        out[k] = float(v)
    return out


def _shared_dies() -> Path:
    from motor_ai_sim import workspace as WS
    return Path(str(WS.shared_root())) / "dies"


def _die_dir(die: str) -> Path:
    d = _shared_dies() / die
    if not (d / "die.yaml").is_file() or d.resolve().parent != _shared_dies().resolve():
        raise SheetRefused(f"die '{die}' is not in the shared catalogue")
    from motor_ai_sim import workspace as WS
    if WS.is_tombstoned(die):
        raise SheetRefused(f"die '{die}' is retired")
    return d


def _yaml(p: Path) -> dict:
    import yaml
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return doc if isinstance(doc, dict) else {}


def _cfg_doc(doc: dict) -> dict:
    return doc["config"] if isinstance(doc.get("config"), dict) else doc


def plan(die: str, sheet: Any) -> Dict[str, Any]:
    """The dry run, in the words the owner reads: every key old -> new, every configuration with its duties, stored rows and the
    projects that link it, and why the write would be refused now (``refusals``, empty when it can go)."""
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import motor_revisions as MR
    new = validate(sheet)
    ddir = _die_dir(die)
    d = _yaml(ddir / "die.yaml")
    geo = dict(d.get("geometry") or {})
    changes = [{"key": k, "old": geo.get(k), "new": v} for k, v in new.items() if not _same(geo.get(k), v)]
    after = dict(geo, **new)
    projects = CA._projects_strict()
    jobs = CA._live_jobs_strict(die)
    rows = ((DR.read_all().get("results") or {}).get(die) or {})
    configs, refusals = [], []
    for cf in sorted(ddir.glob("*.yaml")):
        if cf.name == "die.yaml":
            continue
        name = cf.stem
        c = _cfg_doc(_yaml(cf))
        duties = [x for x in (c.get("duties") or []) if isinstance(x, dict)]
        stored = sum(len(k) for k in (rows.get(name) or {}).values() if isinstance(k, dict))
        linked = sorted({str(p.get("title") or p.get("id")) for p in projects if str(p.get("status") or "") != "closed"
                         and any(isinstance(m, dict) and CA.same_name(m.get("die"), die) and CA.same_name(m.get("config"), name)
                                 for m in p.get("linked_motors") or [])})
        try:
            MR.guard_config_write(die, name)
            frozen = False
        except Exception:                                   # noqa: BLE001 - a frozen product revision refuses any write
            frozen = True
            refusals.append(f"'{die}/{name}' is a frozen product revision")
        configs.append({"config": name, "archived": CA.state(die, name) != CA.ACTIVE, "frozen_revision": frozen,
                        "duties": [str(x.get("name")) for x in duties], "stored_results": stored, "projects": linked,
                        "sheet_overrides": sorted(k for k in SHEET_KEYS if (c.get("geometry_overrides") or {}).get(k) is not None)})
    if jobs:
        refusals.append("a job is running on the die: " + ", ".join(sorted({j.get("where", "?") for j in jobs})))
    if not changes:
        refusals.append("the die already has this sheet")
    overriding = [c["config"] for c in configs if c["sheet_overrides"]]
    if overriding:
        refusals.append("a configuration overrides a sheet key (it would not follow the die): " + ", ".join(overriding))
    return {"die": die, "changes": changes, "previous_hash": sheet_hash(geo), "new_hash": sheet_hash(after),
            "configurations": configs, "refusals": refusals}


def text(p: Dict[str, Any]) -> str:
    """The dry run as plain lines (what the owner reads)."""
    out = [f"Die {p['die']}: sheet {p['previous_hash']} -> {p['new_hash']}"]
    out += [f"  {c['key']}: {c['old']} -> {c['new']}" for c in p["changes"]]
    out.append("Configurations (every stored result is marked 'computed on the previous sheet' until it is saved again):")
    for c in p["configurations"]:
        flags = [w for w, on in (("archived", c["archived"]), ("FROZEN REVISION", c["frozen_revision"])) if on]
        out.append(f"  {c['config']}{' (' + ', '.join(flags) + ')' if flags else ''}: {len(c['duties'])} duties "
                   f"({', '.join(c['duties']) or '-'}), {c['stored_results']} stored rows; projects: {', '.join(c['projects']) or '-'}")
    out.append("Refused now: " + "; ".join(p["refusals"]) if p["refusals"] else "Can be written.")
    return "\n".join(out)


def apply(die: str, sheet: Any, actor: str) -> Dict[str, Any]:
    """Write the sheet into the die record and mark every stored result of its configurations.  Under the lifecycle lock, after a
    backup; the plan is made again under the lock and the write refused as a whole when it is refused."""
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim.routes import family as F
    with CA.lifecycle_lock():
        p = plan(die, sheet)
        if p["refusals"]:
            raise SheetRefused("; ".join(p["refusals"]))
        ddir = _die_dir(die)
        stamp = _now()
        backup = _backup(die, ddir, stamp)
        mark = {"previous_sheet": p["previous_hash"], "new_sheet": p["new_hash"], "at": stamp}
        d = F._load_yaml(ddir / "die.yaml", "die")
        old_sheet = sheet_of(d.get("geometry"))
        geo = dict(d.get("geometry") or {})
        geo.update(validate(sheet))
        d["geometry"] = geo
        d.setdefault("sheet_history", []).append({"at": stamp, "by": actor, "previous": old_sheet,
                                                  "previous_hash": p["previous_hash"], "new_hash": p["new_hash"]})
        try:
            from motor_ai_sim.routes.presets import _gen_thumb_svg
            thumb = _gen_thumb_svg(dict(geo))
            if thumb:
                d["thumb_svg"] = thumb
        except Exception:                                   # noqa: BLE001 - the thumbnail is cosmetic
            pass
        marked = {}
        for c in p["configurations"]:
            path = ddir / f"{c['config']}.yaml"
            doc = F._load_yaml(path, "configuration")
            body = _cfg_doc(doc)
            for duty in body.get("duties") or []:
                if isinstance(duty, dict):
                    duty[MARK] = dict(mark)
            F._save_yaml(path, doc)
            marked[c["config"]] = len(body.get("duties") or [])
        F._save_yaml(ddir / "die.yaml", d)
        rows = DR.mark_sheet_stale(die, mark)
        try:
            from motor_ai_sim import admin_audit as AA
            AA.record(actor, "die.sheet_replace", die, details={"changes": p["changes"], "backup": str(backup),
                                                               "marked_duties": marked, "marked_rows": rows})
        except Exception:                                   # noqa: BLE001 - the write is done; the audit line is logged
            import logging
            logging.getLogger(__name__).warning("sheet_replace: audit row for %s not written", die, exc_info=True)
        if hasattr(F, "_TREE_CACHE"):
            F._TREE_CACHE.clear()                           # the catalog tree shows the new sheet and the marks at once
        return {**p, "refusals": [], "backup": str(backup), "marked_duties": marked, "marked_rows": rows}


def _backup(die: str, ddir: Path, stamp: str) -> Path:
    """A copy of the die record, every configuration file and the die's stored rows, beside the store."""
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import workspace as WS
    out = Path(str(WS.shared_root())) / BACKUP_DIR / die / stamp.replace(":", "")
    out.mkdir(parents=True, exist_ok=False)
    for f in ddir.glob("*.yaml"):
        shutil.copy2(f, out / f.name)
    doc = DR.read_all()
    (out / "duty_results.json").write_text(json.dumps({"results": (doc.get("results") or {}).get(die) or {},
                                                       "history": (doc.get("history") or {}).get(die) or {}}, indent=1),
                                           encoding="utf-8")
    return out


def refresh_mark(die: str, cfg: str, duty_name: str) -> bool:
    """After a duty is saved: its mark goes only when the saved geometry stamp is on the die's CURRENT sheet (an old-sheet save, or
    a save without a stamp, keeps it).  True when a mark was cleared.  Never raises (the save itself already succeeded)."""
    try:
        from motor_ai_sim.routes import family as F
        cpath = F._cfg_file(die, cfg)
        doc = F._load_yaml(cpath, "configuration")
        body = _cfg_doc(doc)
        duty = next((x for x in body.get("duties") or [] if isinstance(x, dict) and x.get("name") == duty_name), None)
        if not duty or is_current(duty):
            return False
        die_geo = (F._load_yaml(F._die_file(die), "die").get("geometry") or {})
        if not on_sheet(F.duty_saved_geometry(duty), die_geo):
            return False
        duty.pop(MARK, None)
        F._save_yaml(cpath, doc)
        return True
    except Exception:                                       # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("sheet_replace: mark of %s/%s/%s not refreshed", die, cfg, duty_name, exc_info=True)
        return False


def live_on_sheet(die: str) -> bool:
    """Is the live machine (what a result being recorded was computed on) on the die's current sheet?"""
    try:
        from motor_ai_sim.config import get_config
        from motor_ai_sim.routes import family as F
        die_geo = (F._load_yaml(F._die_file(die), "die").get("geometry") or {})
        return on_sheet((get_config() or {}).get("geometry") or {}, die_geo)
    except Exception:                                       # noqa: BLE001 - cannot tell: the mark stays
        return False
