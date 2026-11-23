"""A new SHEET for a die that has results: a SUCCESSOR die, never a change of the die in place (owner 2026-11-23, coordinator
decision after the review of #839 / #840: results are keyed by die + configuration, so die identity IS sheet identity).

The switch, under the configuration archive's lifecycle lock, one intent file, every step idempotent (``apply`` resumes):

1. FREEZE: result saves for both die names are refused, visibly, while the intent exists (``frozen``).
2. The successor's own configurations whose names continue are moved ASIDE (renamed "<cfg> sweep <date>") and archived with
   their results - a working copy's numbers never become a motor's result.
3. The predecessor (``pred``, e.g. "CILN28") is renamed to the retired name (``die_rename``: every name-keyed store follows,
   catalogue cards included, so the successor has no public card until its results are saved).
4. The successor (``succ``, the record that already holds the new sheet) is renamed to the predecessor's name.
5. Per continuing configuration: a DEFINITION copy by whitelist (``COPY_KEYS`` / ``DUTY_COPY_KEYS``; a key on neither list
   refuses the plan), results empty; then its links move: account grants, project links, cost estimates, display state
   (``config_move`` link steps).  Production records and product-revision lineages stay with the predecessor (the plan refuses a
   continuing configuration that has one).
6. Saved Compare snapshots / sweeps that name a continuing configuration get their source rewritten to the retired name and
   ``previous_sheet`` set; they keep their numbers.
7. The retired die is retired (admin-only visible); the freeze is lifted.

``restore`` undoes it in reverse (refused when a successor configuration got results after the switch).  ``plan`` is the dry
run the owner reads; ``content_hash`` is compared with the other host's plan before ``apply``."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

#: a configuration's DEFINITION: what a successor copies (anything else is never copied)
COPY_KEYS = frozenset({"name", "die", "role", "created", "geometry_overrides", "winding", "materials", "duties", "applications",
                       "battery", "bearings", "coil_overrides", "controller", "cooling_note", "default_duty",
                       "end_winding_factor", "limits", "locked", "note", "parts", "terminals", "thermal_defaults", "visibility"})
#: computed from the predecessor's sheet / CAD, or its lineage: never copied
NEVER_KEYS = frozenset({"cogging", "cad_mass", "cad_variant", "copied_from"})
DUTY_COPY_KEYS = frozenset({"name", "mode", "kind", "current_arms", "rpm", "gamma_deg", "torque_nm", "power_kw", "note",
                            "star_delta", "duty_cycle", "mesh", "materials"})
DUTY_NEVER_KEYS = frozenset({"result", "summary", "runs", "solved_on", "saved_at", "duty_class", "geometry", "sheet_stale"})
SHEET_KEYS = ("tooth_width", "tooth2_width", "cut_width", "slot_height", "stator_inner_radius", "rotor_outer_radius",
              "rotor_inner_radius", "magnet_height", "magnet_fill_up", "magnet_up_gap", "rotor_hole", "rotor_house_height")
INTENT_DIR = "successor_switch"
FROZEN_TEXT = "not saved: this die is being switched to its successor sheet right now - save again in a few minutes"


class SwitchRefused(ValueError):
    """The switch (or restore) cannot be done as asked; the text says why."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _shared() -> Path:
    from motor_ai_sim import workspace as WS
    return Path(str(WS.shared_root()))


def _dies() -> Path:
    return _shared() / "dies"


def _yaml(p: Path) -> dict:
    import yaml
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return doc if isinstance(doc, dict) else {}


def _body(doc: dict) -> dict:
    return doc["config"] if isinstance(doc.get("config"), dict) else doc


# ── the whitelist copy ──────────────────────────────────────────────────────────────────────────────────────────────────

def unknown_keys(cfg_doc: dict) -> List[str]:
    """Keys of a configuration (and its duties) that are on neither list - the plan refuses them."""
    body = _body(cfg_doc)
    out = sorted(set(body) - COPY_KEYS - NEVER_KEYS)
    for d in body.get("duties") or []:
        if isinstance(d, dict):
            out += [f"duties[{d.get('name')}].{k}" for k in sorted(set(d) - DUTY_COPY_KEYS - DUTY_NEVER_KEYS)]
    return out


def definition_copy(cfg_doc: dict, new_die: str, copied_from: str) -> dict:
    """The definition of a configuration for the successor die: whitelisted keys only, every duty without its results."""
    bad = unknown_keys(cfg_doc)
    if bad:
        raise SwitchRefused("a configuration key on neither list: " + ", ".join(bad))
    body = _body(cfg_doc)
    out = {k: copy.deepcopy(v) for k, v in body.items() if k in COPY_KEYS and k != "duties"}
    out["die"] = new_die
    out["duties"] = [{k: copy.deepcopy(v) for k, v in d.items() if k in DUTY_COPY_KEYS}
                     for d in body.get("duties") or [] if isinstance(d, dict)]
    out["copied_from"] = {"die": copied_from, "config": str(body.get("name") or ""), "at": _now(), "reason": "successor die"}
    return out


def _definition_diff(a: dict, b: dict) -> List[str]:
    """Whitelist keys (and duty keys) where two configuration definitions differ - what a working copy changed."""
    x, y = definition_copy(a, "-", "-"), definition_copy(b, "-", "-")
    keys = sorted(k for k in (set(x) | set(y)) - {"die", "copied_from", "created", "duties"} if x.get(k) != y.get(k))
    dx = {d.get("name"): d for d in x.get("duties") or []}
    dy = {d.get("name"): d for d in y.get("duties") or []}
    for n in sorted(set(dx) | set(dy), key=str):
        if dx.get(n) != dy.get(n):
            keys.append(f"duties[{n}]")
    return keys


# ── the plan ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _cfgs(die_dir: Path) -> Dict[str, dict]:
    return {p.stem: _yaml(p) for p in sorted(die_dir.glob("*.yaml")) if p.name != "die.yaml"}


def content_hash(die: str) -> str:
    """The die's record and configuration files, canonicalised - what both hosts must agree on before the switch."""
    d = _dies() / die
    parts = {p.name: _yaml(p) for p in sorted(d.glob("*.yaml"))}
    for doc in parts.values():
        doc.pop("thumb_svg", None)
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]


def _aside_name(cfg: str, day: str) -> str:
    return f"{cfg} sweep {day}"


def _snapshots(pred: str, names: set) -> List[Dict[str, Any]]:
    """Saved Compare snapshots / sweeps of every workspace that name a continuing configuration of ``pred``."""
    from motor_ai_sim import workspace as WS
    out = []
    roots = [Path(str(WS.workspaces_root()))] if hasattr(WS, "workspaces_root") else []
    for r in roots:
        for f in sorted(r.glob("*/saved_simulations.json")):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for bucket, rows in (doc.items() if isinstance(doc, dict) else []):
                for row in rows if isinstance(rows, list) else []:
                    src = row.get("source") if isinstance(row, dict) and isinstance(row.get("source"), dict) else {}
                    params = row.get("params") if isinstance(row, dict) and isinstance(row.get("params"), dict) else {}
                    die = src.get("die") or params.get("die")
                    cfg = src.get("config") or params.get("config")
                    if die == pred and cfg in names and not row.get("previous_sheet"):
                        out.append({"file": str(f), "bucket": bucket, "id": row.get("id"), "config": cfg})
    return out


def plan(pred: str, succ: str, retired: str, *, stays: tuple = ()) -> Dict[str, Any]:
    """The dry run, in the words the owner reads.  ``stays``: configurations of ``pred`` that do NOT continue (they stay with the
    retired die); an archived configuration never continues."""
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import motor_revisions as MR
    from motor_ai_sim import workspace as WS
    refusals: List[str] = []
    pdir, sdir = _dies() / pred, _dies() / succ
    for name, d in ((pred, pdir), (succ, sdir)):
        if not (d / "die.yaml").is_file() or WS.is_tombstoned(name):
            raise SwitchRefused(f"'{name}' is not an active die of the shared catalogue")
    if (_dies() / retired).exists():
        refusals.append(f"the retired name '{retired}' is taken")
    pdie, sdie = _yaml(pdir / "die.yaml"), _yaml(sdir / "die.yaml")
    sheet = [{"key": k, "old": (pdie.get("geometry") or {}).get(k), "new": (sdie.get("geometry") or {}).get(k)}
             for k in SHEET_KEYS]
    for k in ("num_slots", "num_poles", "stator_diameter"):
        if (pdie.get("geometry") or {}).get(k) != (sdie.get("geometry") or {}).get(k):
            refusals.append(f"the successor is another machine ({k} differs)")
    pcfgs, scfgs = _cfgs(pdir), _cfgs(sdir)
    projects = CA._projects_strict()
    for name in (pred, succ):
        jobs = CA._live_jobs_strict(name)
        if jobs:
            refusals.append(f"a job is running or queued on '{name}': " + ", ".join(sorted({j.get('where', '?') for j in jobs})))
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    continuing, behind = [], []
    for name, doc in pcfgs.items():
        archived = CA.state(pred, name) != CA.ACTIVE
        if archived or name in stays:
            behind.append({"config": name, "why": "archived" if archived else "stays with the retired die"})
            continue
        links = sorted({str(p.get("title") or p.get("id")) for p in projects if str(p.get("status") or "") != "closed"
                        and any(isinstance(m, dict) and m.get("die") == pred and m.get("config") == name
                                for m in p.get("linked_motors") or [])})
        row = {"config": name, "duties": [str(d.get("name")) for d in _body(doc).get("duties") or [] if isinstance(d, dict)],
               "projects": links, "unknown_keys": unknown_keys(doc)}
        if row["unknown_keys"]:
            refusals.append(f"'{pred}/{name}' holds keys on neither list: {', '.join(row['unknown_keys'])}")
        try:
            prod = [r for r in CA._productions_strict(pred, name) if r.get("die") == pred and r.get("config") == name]
        except Exception as exc:                            # noqa: BLE001 - unknown is not "none"
            refusals.append(f"the production records cannot be read ({exc})")
            prod = []
        if prod:
            refusals.append(f"'{pred}/{name}' has a production record: it stays with the built motor")
        if MR.find(pred, name):
            refusals.append(f"'{pred}/{name}' is in a product-revision lineage: it stays with that revision")
        if name in scfgs:
            row["successor_own_moved_aside"] = _aside_name(name, day)
            try:
                row["working_copy_differs_in"] = _definition_diff(scfgs[name], doc)
            except SwitchRefused as exc:                    # a key on neither list: a refusal of the plan, not a failure
                refusals.append(f"'{succ}/{name}' (the working copy): {exc}")
                row["working_copy_differs_in"] = []
            if (sdir / f"{_aside_name(name, day)}.yaml").exists():
                refusals.append(f"'{succ}/{_aside_name(name, day)}' exists already")
        continuing.append(row)
    try:
        from motor_ai_sim import passport_store as PS
        cards = (PS.card_index() or {}).get(pred, {})
    except Exception:                                       # noqa: BLE001
        cards = {}
    public_empty = sorted(f"{pred}/{c['config']}" for c in continuing if cards.get(c["config"]))
    snaps = _snapshots(pred, {c["config"] for c in continuing})
    return {"pred": pred, "succ": succ, "retired": retired, "sheet": sheet, "continuing": continuing, "behind": behind,
            "successor_others": sorted(n for n in scfgs if n not in pcfgs), "public_empty": public_empty,
            "snapshots": len(snaps), "content_hash": content_hash(pred), "successor_hash": content_hash(succ),
            "refusals": refusals}


def text(p: Dict[str, Any]) -> str:
    """The plan as short lines (what the owner reads)."""
    out = [f"'{p['pred']}' becomes '{p['retired']}' (retired, nothing deleted); '{p['succ']}' becomes '{p['pred']}'.",
           "Sheet, old -> new:"]
    out += [f"  {s['key']}: {s['old']} -> {s['new']}" for s in p["sheet"] if s["old"] != s["new"]]
    out.append("Continue on the new sheet (definition copied, results empty until the recompute is saved):")
    for c in p["continuing"]:
        line = f"  {c['config']}: {len(c['duties'])} duties; projects: {', '.join(c['projects']) or '-'}"
        if c.get("successor_own_moved_aside"):
            line += f"; the successor's own copy is moved aside as '{c['successor_own_moved_aside']}' and archived"
            if c.get("working_copy_differs_in"):
                line += f" (it differs in: {', '.join(c['working_copy_differs_in'])})"
        out.append(line)
    out.append("Stay with the retired die: " + (", ".join(f"{b['config']} ({b['why']})" for b in p["behind"]) or "-"))
    out.append(f"Saved Compare snapshots / sweeps marked 'previous sheet' (numbers kept): {p['snapshots']}")
    out.append("No public card until results are saved: " + (", ".join(p["public_empty"]) or "-"))
    out.append("Customers see: the same motor names in their projects, results empty until the recompute is saved.")
    out.append(f"Content hash (both hosts must agree): {p['content_hash']}")
    out.append("Refused now: " + "; ".join(p["refusals"]) if p["refusals"] else "Can be switched.")
    return "\n".join(out)


# ── the freeze ───────────────────────────────────────────────────────────────────────────────────────────────────────

def _intent_path(pred: str) -> Path:
    return _shared() / INTENT_DIR / f"{pred}.json"


def _intents() -> List[dict]:
    d = _shared() / INTENT_DIR
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            out.append({"pred": f.stem, "unreadable": True})      # unknown = frozen
    return out


def frozen(die: str) -> bool:
    """Is ``die`` part of a switch in progress (either name, or the retired name)?  Result saves are refused while it is."""
    for it in _intents():
        if it.get("unreadable") or die in {it.get("pred"), it.get("succ"), it.get("retired")}:
            return True
    return False


# ── the switch ───────────────────────────────────────────────────────────────────────────────────────────────────────

STEPS = ("aside", "rename_pred", "rename_succ", "copies", "links", "snapshots", "retire")


def _write_intent(it: dict) -> None:
    from motor_ai_sim import json_store
    p = _intent_path(it["pred"])
    p.parent.mkdir(parents=True, exist_ok=True)
    json_store.atomic_write_json(p, it)


def apply(pred: str, succ: str, retired: str, *, actor: str, peer_content_hash: str, w: dict,
          stays: tuple = ()) -> Dict[str, Any]:
    """Switch ``pred`` to its successor ``succ``.  A switch that was interrupted resumes from its intent file (the same call)."""
    from motor_ai_sim import config_archive as CA
    with CA.lifecycle_lock():
        p_intent = _intent_path(pred)
        it = json.loads(p_intent.read_text(encoding="utf-8")) if p_intent.is_file() else None
        if it is None:
            p = plan(pred, succ, retired, stays=stays)
            if p["refusals"]:
                raise SwitchRefused("; ".join(p["refusals"]))
            if peer_content_hash != p["content_hash"]:
                raise SwitchRefused(f"the other host's plan of '{pred}' differs (content hash {peer_content_hash or '-'} here "
                                    f"{p['content_hash']}): mirror first")
            it = {"pred": pred, "succ": succ, "retired": retired, "actor": actor, "at": _now(), "done": [],
                  "continuing": [c["config"] for c in p["continuing"]],
                  "aside": {c["config"]: c["successor_own_moved_aside"] for c in p["continuing"]
                            if c.get("successor_own_moved_aside")}}
            _write_intent(it)
        elif (it.get("pred"), it.get("succ"), it.get("retired")) != (pred, succ, retired):
            raise SwitchRefused(f"another switch of '{pred}' is in progress: {it.get('succ')} as {it.get('retired')}")
        for step in STEPS:
            if step in it["done"]:
                continue
            globals()[f"_step_{step}"](it, w)
            it["done"].append(step)
            _write_intent(it)
        _audit(actor, "die.successor_switch", pred, {"successor": succ, "retired": retired, "continuing": it["continuing"],
                                                     "aside": it["aside"]})
        p_intent.unlink()
        _clear_caches()
        return {"ok": True, **{k: it[k] for k in ("pred", "succ", "retired", "continuing", "aside")}}


def _fam():
    from motor_ai_sim.routes import family as F
    return F


def _rename_die(old: str, new: str, actor: str) -> None:
    from motor_ai_sim import die_rename as DRN
    F = _fam()
    if not (_dies() / old / "die.yaml").is_file() and (_dies() / new / "die.yaml").is_file():
        return                                              # done before an interruption
    DRN.run(F, old, new, src_dir=_dies() / old, dst_dir=_dies() / new, dry_run=False, glob_scope=True, actor=actor)


def _step_aside(it: dict, w: dict) -> None:
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import config_move as CM
    F = _fam()
    for cfg, aside in it["aside"].items():
        sdir = _dies() / it["succ"]
        if (sdir / f"{cfg}.yaml").is_file() and not (sdir / f"{aside}.yaml").is_file():
            CM.run(F, it["succ"], cfg, it["succ"], src_dir=sdir, dst_dir=sdir, dry_run=False, copy=False, glob_scope=True,
                   actor=it["actor"], new_cfg=aside)
        if CA.state(it["succ"], aside) == CA.ACTIVE:
            CA.archive(it["succ"], aside, by=it["actor"], reason=f"working copy moved aside by the successor switch of {it['pred']}")


def _step_rename_pred(it: dict, w: dict) -> None:
    _rename_die(it["pred"], it["retired"], it["actor"])


def _step_rename_succ(it: dict, w: dict) -> None:
    _rename_die(it["succ"], it["pred"], it["actor"])


def _step_copies(it: dict, w: dict) -> None:
    F = _fam()
    for cfg in it["continuing"]:
        src = _yaml(_dies() / it["retired"] / f"{cfg}.yaml")
        F._save_yaml(_dies() / it["pred"] / f"{cfg}.yaml", definition_copy(src, it["pred"], it["retired"]))


def _step_links(it: dict, w: dict) -> None:
    from motor_ai_sim import config_move as CM
    F = _fam()
    for cfg in it["continuing"]:
        CM.move_links(F, it["retired"], cfg, it["pred"], src_dir=_dies() / it["retired"], dst_dir=_dies() / it["pred"],
                      actor=it["actor"])


def _step_snapshots(it: dict, w: dict) -> None:
    _stamp_snapshots(it["pred"], set(it["continuing"]), it["retired"], forward=True)


def _stamp_snapshots(pred: str, names: set, retired: str, *, forward: bool) -> int:
    """Forward: the saved snapshots naming ``pred/<cfg>`` name the retired die and say ``previous_sheet``.  Backward: undone."""
    from motor_ai_sim import json_store
    from motor_ai_sim import workspace as WS
    n = 0
    root = WS.workspaces_root()
    for f in sorted(Path(str(root)).glob("*/saved_simulations.json")) if root else []:
        def edit(doc, n_box):
            for rows in (doc.values() if isinstance(doc, dict) else []):
                for row in rows if isinstance(rows, list) else []:
                    if not isinstance(row, dict):
                        continue
                    src = row.get("source") if isinstance(row.get("source"), dict) else None
                    params = row.get("params") if isinstance(row.get("params"), dict) else {}
                    if forward and not row.get("previous_sheet"):
                        die = (src or {}).get("die") or params.get("die")
                        cfg = (src or {}).get("config") or params.get("config")
                        if die == pred and cfg in names:
                            row["previous_sheet"] = {"was": {"source": copy.deepcopy(src), "die": params.get("die")}, "die": retired}
                            if src is not None:
                                src["die"] = retired
                            if params.get("die") == pred:
                                params["die"] = retired
                            n_box[0] += 1
                    elif not forward and isinstance(row.get("previous_sheet"), dict) and row["previous_sheet"].get("die") == retired:
                        was = row.pop("previous_sheet")["was"]
                        if was.get("source") is not None:
                            row["source"] = was["source"]
                        if was.get("die") is not None:
                            params["die"] = was["die"]
                        n_box[0] += 1
            # NB: return nothing - json_store.mutate_json takes a returned value as the new document
        box = [0]
        json_store.mutate_json(f, lambda doc: edit(doc, box), default={})
        n += box[0]
    return n


def _step_retire(it: dict, w: dict) -> None:
    from motor_ai_sim import workspace as WS
    if not WS.is_tombstoned(it["retired"]):
        WS.add_tombstone(it["retired"], it["actor"])


def _audit(actor: str, action: str, target: str, details: dict) -> None:
    from motor_ai_sim import admin_audit as AA
    AA.record(actor, action, target, details=details)


def _clear_caches() -> None:
    F = _fam()
    if hasattr(F, "_TREE_CACHE"):
        F._TREE_CACHE.clear()
    for mod, name in (("motor_ai_sim.public_motors", "clear_cache"), ("motor_ai_sim.support_catalog", "clear_cache")):
        try:
            import importlib
            fn = getattr(importlib.import_module(mod), name, None)
            if fn:
                fn()
        except Exception:                                   # noqa: BLE001 - a cache that cannot be cleared expires on its own
            pass


# ── the restore (the switch in reverse) ─────────────────────────────────────────────────────────────────────────────

def restore_plan(pred: str, succ: str, retired: str) -> Dict[str, Any]:
    """The dry run of a restore: ``pred`` (the successor now) goes back to ``succ``, ``retired`` back to ``pred``."""
    from motor_ai_sim import duty_results as DR
    refusals = []
    sdir, rdir = _dies() / pred, _dies() / retired
    if not (sdir / "die.yaml").is_file() or not (rdir / "die.yaml").is_file():
        raise SwitchRefused(f"'{pred}' and '{retired}' must both exist to restore")
    if (_dies() / succ).exists():
        refusals.append(f"the name '{succ}' is taken")
    rows = ((DR.read_all().get("results") or {}).get(pred) or {})
    copies = [p.stem for p in sdir.glob("*.yaml") if p.name != "die.yaml" and (rdir / p.name).is_file()]
    with_results = sorted(c for c in copies if rows.get(c) or any(
        d.get("result") or d.get("summary") or d.get("runs") for d in _body(_yaml(sdir / f"{c}.yaml")).get("duties") or []
        if isinstance(d, dict)))
    if with_results:
        refusals.append("results were saved on the successor since the switch: " + ", ".join(with_results))
    return {"pred": pred, "succ": succ, "retired": retired, "copies": sorted(copies), "refusals": refusals}


def restore(pred: str, succ: str, retired: str, *, actor: str, aside: Dict[str, str], w: dict) -> Dict[str, Any]:
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import config_move as CM
    from motor_ai_sim import workspace as WS
    F = _fam()
    with CA.lifecycle_lock():
        p = restore_plan(pred, succ, retired)
        if p["refusals"]:
            raise SwitchRefused("; ".join(p["refusals"]))
        _write_intent({"pred": pred, "succ": succ, "retired": retired, "restore": True, "at": _now()})
        WS.remove_tombstone(retired)
        _stamp_snapshots(pred, set(p["copies"]), retired, forward=False)
        for cfg in p["copies"]:
            CM.move_links(F, pred, cfg, retired, src_dir=_dies() / pred, dst_dir=_dies() / retired, actor=actor)
            (_dies() / pred / f"{cfg}.yaml").unlink()
        _rename_die(pred, succ, actor)
        _rename_die(retired, pred, actor)
        for cfg, a in aside.items():
            CA.restore(succ, a, by=actor)
            sdir = _dies() / succ
            if (sdir / f"{a}.yaml").is_file():
                CM.run(F, succ, a, succ, src_dir=sdir, dst_dir=sdir, dry_run=False, copy=False, glob_scope=True, actor=actor,
                       new_cfg=cfg)
        _intent_path(pred).unlink()
        _audit(actor, "die.successor_restore", pred, {"successor": succ, "retired": retired})
        _clear_caches()
        return {"ok": True, **p, "refusals": []}


# ── result saves: the freeze and the die identity ────────────────────────────────────────────────────────────────────────

import contextvars as _cv

_REFUSALS: "_cv.ContextVar[Optional[List[str]]]" = _cv.ContextVar("successor_refusals", default=None)
ID_MISMATCH_TEXT = ("not saved: this result was computed for another die record of this name (its sheet was replaced by a "
                    "successor die) - reload the page and run the point again")


class SaveRefused(ValueError):
    pass


def die_id(die: str) -> Optional[str]:
    """The die's stable id in the object registry (a rename keeps it; a successor has its own), or None when unknown."""
    try:
        from motor_ai_sim import object_registry as OR
        return OR.index().resolve("family", "catalog", str(die))
    except Exception:                                       # noqa: BLE001
        return None


def check_save(die: str, sent_die_id: Optional[str] = None) -> None:
    """A result save for ``die``: refused while a switch of it is in progress, and - when the caller says which die record it
    computed for (``sent_die_id``, defence against accidents) - when that is not the record the name means now.  A caller that
    sends no id is not asked (as before)."""
    if frozen(die):
        raise SaveRefused(FROZEN_TEXT)
    if sent_die_id:
        now = die_id(die)
        if now and now != str(sent_die_id):
            raise SaveRefused(ID_MISMATCH_TEXT)


def note_refusal(text: str) -> None:
    rows = _REFUSALS.get()
    if rows is not None and text not in rows:
        rows.append(text)


def install(app) -> None:
    """A request that refused to record a result (``duty_results.record`` during a switch) says so: ``not_saved`` in its JSON
    answer and ``X-Result-Not-Saved``.  Untouched otherwise."""
    from starlette.responses import Response

    @app.middleware("http")
    async def _successor_refusals(request, call_next):
        rows: List[str] = []
        token = _REFUSALS.set(rows)
        try:
            response = await call_next(request)
        finally:
            _REFUSALS.reset(token)
        if not rows:
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        headers["X-Result-Not-Saved"] = rows[0][:200]
        if "json" in str(response.headers.get("content-type", "")):
            try:
                doc = json.loads(body or b"null")
                if isinstance(doc, dict):
                    doc["not_saved"] = list(rows)
                    body = json.dumps(doc).encode()
            except ValueError:
                pass
        return Response(content=body, status_code=response.status_code, headers=headers, media_type=response.media_type)
