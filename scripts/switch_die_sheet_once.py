"""Switch ONE die to its successor sheet, once, OFFLINE (coordinator decision 2026-11-23 after the reviews of #839 / #840 / #841).

Runs against a STORE DIRECTORY with the API and the workers stopped and the region mirror paused (the maintenance window); the
rollback is the full copy of the store taken before.  No live route, no locks.  The data change is decided by a MANIFEST built
from the real store and reviewed before the run.

  python scripts/switch_die_sheet_once.py inventory --store <dir> --die <P> --die <S> --out inventory.json
      every file under the store that names a die (its path, or a string value / key equal to the name or starting with "name/")

The later subcommands (manifest, dry-run, apply, check) are added in the same file.  The store layout is that of the server:
<store>/shared (SHARED_ROOT, with projects/ and production/ inside), <store>/workspaces, <store>/published, <store>/identity.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

TEXT_SUFFIXES = {".json", ".yaml", ".yml", ".jsonl"}
SKIP_DIRS = {"__pycache__"}


def _names_in(obj: Any, names: Tuple[str, ...], path: str = "$") -> Iterator[Tuple[str, str]]:
    """``(json-path, name)`` for every string value or dict key equal to one of ``names`` or starting with "<name>/"."""
    def hit(s: str):
        for n in names:
            if s == n or s.startswith(n + "/"):
                return n
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                n = hit(k)
                if n:
                    yield f"{path}.{{key:{k}}}", n
            yield from _names_in(v, names, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _names_in(v, names, f"{path}[{i}]")
    elif isinstance(obj, str):
        n = hit(obj)
        if n:
            yield path, n


def _load(p: Path) -> List[Any]:
    """The documents of a structured file (a .jsonl gives one per line); [] when it cannot be parsed."""
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        if p.suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        if p.suffix == ".json":
            return [json.loads(text)]
        import yaml
        return [yaml.safe_load(text)]
    except Exception:                                       # noqa: BLE001 - reported as unparsed
        return [{"__unparsed__": True}]


def inventory(store: Path, names: Tuple[str, ...]) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    unparsed: List[str] = []
    for p in sorted(store.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        rel = str(p.relative_to(store))
        parts = p.relative_to(store).parts
        by_path = sorted({n for n in names for part in parts if part == n or part.startswith(n + "~") or part.startswith(n + ".")})
        hits: List[Tuple[str, str]] = []
        if p.suffix in TEXT_SUFFIXES:
            for doc in _load(p):
                if isinstance(doc, dict) and doc.get("__unparsed__"):
                    unparsed.append(rel)
                    continue
                hits.extend(_names_in(doc, names))
        if by_path or hits:
            per = {n: sum(1 for _p, m in hits if m == n) for n in names}
            files.append({"file": rel, "root": parts[0] if parts else "", "store": "/".join(parts[:2]),
                          "by_path": by_path, "hits": {k: v for k, v in per.items() if v},
                          "sample": [h[0] for h in hits[:6]]})
    stores: Dict[str, Dict[str, int]] = {}
    for f in files:
        s = stores.setdefault(f["store"], {"files": 0, **{n: 0 for n in names}})
        s["files"] += 1
        for n, c in f["hits"].items():
            s[n] += c
        for n in f["by_path"]:
            s[n] += 0
    return {"names": list(names), "stores": stores, "files": files, "unparsed": unparsed}


# ── the manifest ───────────────────────────────────────────────────────────────────────────────────────────────────────

SHEET_KEYS = ("tooth_width", "tooth2_width", "cut_width", "slot_height", "stator_inner_radius", "rotor_outer_radius",
              "rotor_inner_radius", "magnet_height", "magnet_fill_up", "magnet_up_gap", "rotor_hole", "rotor_house_height",
              "magnet_pocket_clearance")
#: a configuration's DEFINITION (copied); nested exceptions in NEVER_NESTED
COPY_KEYS = {"name", "die", "role", "created", "geometry_overrides", "winding", "materials", "duties", "applications", "battery",
             "bearings", "coil_overrides", "controller", "cooling_note", "default_duty", "end_winding_factor", "limits", "locked",
             "note", "parts", "terminals", "thermal_defaults", "visibility"}
NEVER_KEYS = {"cogging": "a result of the previous sheet", "cad_mass": "CAD of the previous sheet",
              "cad_variant": "CAD of the previous sheet", "cad_overrides": "CAD of the previous sheet",
              "copied_from": "lineage (re-set to the retired die)", "continuous_source": "where a saved result came from"}
NEVER_NESTED = {("bearings", "thermal_path"): "measured from the CAD of the previous sheet"}
DUTY_COPY_KEYS = {"name", "mode", "kind", "current_arms", "rpm", "gamma_deg", "note", "star_delta", "duty_cycle", "mesh",
                  "materials"}
DUTY_NEVER_KEYS = {"result": "a result", "summary": "a result", "runs": "results and their files", "solved_on": "a result stamp",
                   "saved_at": "the save of a result", "duty_class": "derived from coupled results",
                   "torque_nm": "filled from the saved run (no target marker in the duty)",
                   "power_kw": "filled from the saved run (no target marker in the duty)"}


def _leaves(obj: Any, path: str = "") -> Iterator[Tuple[str, Any]]:
    if isinstance(obj, dict) and obj:
        for k, v in obj.items():
            yield from _leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        for i, v in enumerate(obj):
            yield from _leaves(v, f"{path}[{i}]")
    else:
        yield path, obj


def classify_config(doc: dict) -> Dict[str, Any]:
    """Every leaf of one configuration with its fate: copied (with the value) or not copied (with the reason); unknown keys."""
    body = doc["config"] if isinstance(doc.get("config"), dict) else doc
    copied, dropped, unknown = [], [], []
    for k, v in body.items():
        if k == "duties":
            continue
        if k in NEVER_KEYS:
            dropped.append({"key": k, "why": NEVER_KEYS[k]})
        elif k not in COPY_KEYS:
            unknown.append(k)
        else:
            for leaf, val in _leaves(v, k):
                nested = next((why for (a, b), why in NEVER_NESTED.items() if leaf == f"{a}.{b}" or leaf.startswith(f"{a}.{b}.")), None)
                (dropped.append({"key": leaf, "why": nested}) if nested else copied.append({"key": leaf, "value": val}))
    duties = []
    for d in body.get("duties") or []:
        if not isinstance(d, dict):
            continue
        row = {"duty": d.get("name"), "copied": [], "dropped": [], "unknown": []}
        for k, v in d.items():
            if k in DUTY_COPY_KEYS:
                row["copied"] += [{"key": leaf, "value": val} for leaf, val in _leaves(v, k)]
            elif k in DUTY_NEVER_KEYS:
                item = {"key": k, "why": DUTY_NEVER_KEYS[k]}
                if k in ("torque_nm", "power_kw"):
                    s = d.get("summary") if isinstance(d.get("summary"), dict) else {}
                    item.update(value=v, solved=(s.get("T_em_avg_Nm") if k == "torque_nm" else
                                                     (round(s["P_mech_W"] / 1000.0, 3) if isinstance(s.get("P_mech_W"), (int, float)) else None)),
                                        input_current_arms=d.get("current_arms"))
                row["dropped"].append(item)
            else:
                row["unknown"].append(k)
        duties.append(row)
    return {"copied": copied, "dropped": dropped, "unknown": unknown, "duties": duties}


def definition_copy(doc: dict, new_die: str, retired: str) -> dict:
    """The successor's copy: exactly the 'copied' leaves of ``classify_config`` (refuses unknown keys)."""
    import copy as _copy
    c = classify_config(doc)
    if c["unknown"] or any(r["unknown"] for r in c["duties"]):
        raise SystemExit(f"refused: keys on neither list: {c['unknown']} {[r['unknown'] for r in c['duties']]}")
    body = doc["config"] if isinstance(doc.get("config"), dict) else doc
    out = {k: _copy.deepcopy(v) for k, v in body.items() if k in COPY_KEYS and k != "duties"}
    for (a, b) in NEVER_NESTED:
        if isinstance(out.get(a), dict):
            out[a].pop(b, None)
    out["die"] = new_die
    out["duties"] = [{k: _copy.deepcopy(v) for k, v in d.items() if k in DUTY_COPY_KEYS}
                     for d in body.get("duties") or [] if isinstance(d, dict)]
    out["copied_from"] = {"die": retired, "config": str(body.get("name") or ""), "reason": "successor die (sheet change)"}
    return out



def _y(p: Path) -> dict:
    import yaml
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return d if isinstance(d, dict) else {}


def _action(rel: str, pred: str, succ: str, cont: set, stays: set, aside: set) -> Tuple[str, str]:
    """(action, why) for one file of the inventory."""
    parts = rel.split("/")
    if parts[:3] == ["shared", "dies", pred]:
        return "follows the predecessor to the retired name", "die_rename: the predecessor stays complete on the previous sheet"
    if parts[:3] == ["shared", "dies", succ]:
        if len(parts) > 3 and parts[3].rsplit(".", 1)[0] in aside:
            return "moved aside and archived (with its results)", "the successor's own working copy of a continuing name"
        return "becomes the successor under the predecessor's name", "die_rename"
    if parts[:2] == ["shared", "dies"]:
        return "left", "another die naming it as lineage text"
    if rel == "shared/.duty_results.json":
        return "rows of the predecessor follow to the retired name; the successor's rows follow it", "die_rename rekeys the ledger"
    if parts[:2] == ["shared", ".answers"]:
        return "cleared where it names the predecessor", "a cached answer computed on the previous sheet (rebuilt on demand)"
    if parts[:2] in (["shared", ".config_moves"], ["shared", "digests"], ["shared", "legal"]) or parts[:2] == ["workspaces", "logs"]:
        return "left", "history / audit / logs (never served as a current result)"
    if parts[:2] == ["shared", "projects"]:
        if len(parts) > 3 and parts[3] == "snapshots":
            return "retargeted to the retired name and marked 'previous sheet' (numbers kept)", "a project snapshot of the previous sheet"
        if parts[-1] == "project.json":
            return "continuing links keep '<die>/<cfg>' (= the successor's copy); others follow to the retired name", "links-only move"
        return "left", "project history / thread / audit"
    if parts[:2] == ["shared", "object_registry"]:
        if parts[-1].startswith("config_archive"):
            return "archive keys follow the rename; the moved-aside copies are archived", "config archive"
        return "predecessor items renamed with aliases; continuing names point to the successor's copies", "die_rename + registry relink"
    if rel == "identity/users.json":
        return "grants of continuing configurations keep their key (= the successor's copy); others follow to the retired name", "grants"
    if parts[0] == "workspaces" and "optimization_saves" in parts:
        return "marked 'previous sheet' (machine names the retired die; numbers kept)", "a saved sweep of the previous sheet"
    if parts[0] == "workspaces" and parts[-1] == ".duty_results.json":
        return "rows of the predecessor -> the retired name; rows of the successor -> the predecessor's name", "duty_results.rename_die"
    if parts[0] == "workspaces" and parts[-1] == ".last_scan.json":
        return "the machine it names follows (retired name, or the aside copy of the successor)", "last scan relabel"
    if parts[0] == "workspaces" and parts[-1] == "saved_simulations.json":
        return "rows of the predecessor marked previous_sheet (source / params / '<die> / ...' name -> the retired name)", "Compare rows"
    if parts[0] == "workspaces":
        return "left", "a user's own working state (presets, panel settings, datasets) naming the old machine"
    if parts[:2] in (["shared", "cad"], ["shared", "production"], ["shared", "motor_tests"]):
        return "left", "belongs to another die (a built prototype), untouched"
    return "left", "unclassified - review"


def build_manifest(store: Path, inv: Dict[str, Any], pred: str, succ: str, retired: str, cont: List[str], stays: List[str],
                   pocket: Any = None) -> Dict[str, Any]:
    sh = store / "shared"
    pdie, sdie = _y(sh / "dies" / pred / "die.yaml"), _y(sh / "dies" / succ / "die.yaml")
    pg, sg = pdie.get("geometry") or {}, sdie.get("geometry") or {}
    def _r(v):
        return round(v, 6) if isinstance(v, float) else v
    pg, sg = {k: _r(v) for k, v in pg.items()}, {k: _r(v) for k, v in sg.items()}
    sheet = [{"key": k, "old": pg.get(k), "new": (pocket if k == "magnet_pocket_clearance" and pocket is not None else sg.get(k))}
             for k in SHEET_KEYS]
    other = sorted(k for k in set(pg) | set(sg) if k not in SHEET_KEYS and pg.get(k) != sg.get(k))
    scfgs = {f.stem for f in (sh / "dies" / succ).glob("*.yaml") if f.name != "die.yaml"}
    configs = []
    for c in cont:
        doc = _y(sh / "dies" / pred / f"{c}.yaml")
        row = {"config": c, **classify_config(doc)}
        if c in scfgs:
            own = classify_config(_y(sh / "dies" / succ / f"{c}.yaml"))
            a = {x["key"]: x["value"] for x in row["copied"]}
            b = {x["key"]: x["value"] for x in own["copied"]}
            row["successor_own"] = {"moved_aside_as": f"{c} sweep {{date}}",
                                    "differs": [{"key": k, "catalog": a.get(k), "working_copy": b.get(k)}
                                                for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]}
        configs.append(row)
    projects = []
    for pj in sorted((sh / "projects").glob("*/project.json")):
        d = json.loads(pj.read_text(encoding="utf-8"))
        links = [m for m in d.get("linked_motors") or [] if isinstance(m, dict) and m.get("die") == pred]
        if links:
            projects.append({"project": d.get("id"), "title": d.get("title"), "links": [m.get("config") for m in links],
                             "members": [{"member": f"{(m.get('email') or '')[:2]}... ({m.get('role')})",
                                          "access_before": "sees the motor", "access_after": "sees the motor (same name)"}
                                         for m in d.get("members") or []]})
    aside = {c for c in cont if c in scfgs}
    stores: Dict[str, Dict[str, Any]] = {}
    for f in inv["files"]:
        act, why = _action(f["file"], pred, succ, set(cont), set(stays), aside)
        e = stores.setdefault(f["store"], {"files": 0, "actions": {}})
        e["files"] += 1
        e["actions"].setdefault(act, {"why": why, "files": 0, "examples": []})
        e["actions"][act]["files"] += 1
        if len(e["actions"][act]["examples"]) < 3:
            e["actions"][act]["examples"].append(f["file"])
    access = json.loads((store / "identity" / "die_access.json").read_text()) if (store / "identity" / "die_access.json").is_file() else {}
    shadows = [str(p.relative_to(store)) for root in ("workspaces", "published") for p in (store / root).glob("**/dies/*")
               if p.is_dir() and p.name in (pred, succ)]
    return {"pred": pred, "succ": succ, "retired": retired, "sheet": sheet, "die_keys_also_differing": other,
            "pocket_clearance_slot": {"key": "magnet_pocket_clearance", "level": "die (geometry)", "value": pocket},
            "continuing": configs, "stays": stays, "never_nested": [".".join(k) for k in NEVER_NESTED], "projects": projects, "stores": stores,
            "die_access": {pred: access.get(pred, "inherit"), succ: access.get(succ, "inherit")},
            "workspace_or_published_copies": shadows}


def manifest_text(m: Dict[str, Any]) -> str:
    L = [f"# Manifest: {m['pred']} -> successor sheet ({m['succ']})", "",
         f"- '{m['pred']}' is renamed '{m['retired']}' and retired (complete, nothing deleted); '{m['succ']}' becomes '{m['pred']}'.",
         f"- Die access: {m['pred']} = {m['die_access'][m['pred']]}, {m['succ']} = {m['die_access'][m['succ']]}.",
         f"- Workspace / published copies of either die: {m['workspace_or_published_copies'] or 'none'}.",
         f"- Pocket clearance: `{m['pocket_clearance_slot']['key']}` is a {m['pocket_clearance_slot']['level']} key; value: "
         f"{m['pocket_clearance_slot']['value'] if m['pocket_clearance_slot']['value'] is not None else 'EMPTY (as stored in the successor)'}.",
         "", "## Sheet, old -> new", ""]
    L += [f"- {s['key']}: {s['old']} -> {s['new']}" for s in m["sheet"]]
    L += ["", f"Other die keys that differ (configuration-level, NOT written; each configuration keeps its own): {', '.join(m['die_keys_also_differing']) or '-'}",
          "", "## Continuing configurations (definition copied, results empty)", ""]
    for c in m["continuing"]:
        L.append(f"### {c['config']}")
        L.append("Copied: " + "; ".join(f"{x['key']}={json.dumps(x['value'], ensure_ascii=False)}" for x in c["copied"]))
        L.append("Not copied: " + "; ".join(f"{x['key']} ({x['why']})" for x in c["dropped"]) if c["dropped"] else "Not copied: -")
        if c["unknown"]:
            L.append(f"**UNKNOWN keys (refuse): {c['unknown']}**")
        for d in c["duties"]:
            L.append(f"- duty {d['duty']}: copied " + ", ".join(f"{x['key']}={json.dumps(x['value'], ensure_ascii=False)}" for x in d["copied"]))
            tp = [x for x in d["dropped"] if x["key"] in ("torque_nm", "power_kw")]
            if tp:
                L.append(f"  - torque / power NOT copied: solved outputs of the current-driven run at {tp[0].get('input_current_arms')} A rms "
                         "(the target is in the duty name / note, which are copied): " + ", ".join(
                    f"{x['key']}={x.get('value')} (run: {x.get('solved')})" for x in tp))
            if d["unknown"]:
                L.append(f"  - **UNKNOWN duty keys (refuse): {d['unknown']}**")
        if c.get("successor_own"):
            o = c["successor_own"]
            L.append(f"- The successor's own {c['config']} is moved aside as '{o['moved_aside_as']}' and archived with its results.")
            L.append("  It differs from the catalog definition in: " + ("; ".join(
                f"{x['key']}: catalog {json.dumps(x['catalog'])} / working copy {json.dumps(x['working_copy'])}" for x in o["differs"]) or "nothing"))
        L.append("")
    L += ["Every continuing duty is current-driven: its current was chosen on the previous sheet, so on the new sheet the same current"
          " gives a different torque / power until the duty is recomputed and re-saved (the project sessions hold those points).", ""]
    L += [f"Stay with the retired die: {', '.join(m['stays'])}", "", "## Projects (links and access)", ""]
    for p in m["projects"]:
        L.append(f"- {p['title']} ({p['project']}): {', '.join(p['links'])} -> same names on the successor; "
                 + "; ".join(f"{x['member']}: {x['access_before']} -> {x['access_after']}" for x in p["members"]))
    L += ["", "Customers see: the same motor names in their projects, results empty until the recompute is saved.", "",
          "## Every store that names the predecessor or the successor", ""]
    for st, e in sorted(m["stores"].items()):
        for act, a in e["actions"].items():
            L.append(f"- {st} ({a['files']} files): {act} - {a['why']}. e.g. {', '.join(a['examples'][:2])}")
    return "\n".join(L) + "\n"



# ── the switch (offline, against the store directory; the deployed application code does each move) ───────────────────

def _point_at(store: Path) -> None:
    """Every root of the application at the store directory; must run before the first import of the application."""
    import os
    import sys as _sys
    if any(m == "motor_ai_sim" or m.startswith("motor_ai_sim.") for m in _sys.modules):
        raise SystemExit("refused: the application was imported before its roots were pointed at the store")
    # the process config directory IS the identity directory on the server (deploy/README.md, "the identity trap"): users,
    # die access, tombstones, the admin audit and the process workspace all resolve from it - bound to the store here
    # every other destination override of the application is CLEARED (its default then resolves inside the store, asserted
    # below): ADMIN_AUDIT_FILE, MOTRES_* (object registry, projects, production, motor tests, revisions, CAD, ...),
    # MOTOR_AI_SIM_* (presets, ...)
    global CLEARED_ENV
    CLEARED_ENV = sorted(k for k in os.environ if k.startswith(("MOTRES_", "MOTOR_AI_SIM_")) or k == "ADMIN_AUDIT_FILE")
    for k in CLEARED_ENV:
        os.environ.pop(k)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    _sys.dont_write_bytecode = True
    os.environ["MOTOR_AI_SIM_CONFIG"] = str(store / "identity" / "motor_config.yaml")
    os.environ["SHARED_ROOT"] = str(store / "shared")
    os.environ["WORKSPACES_ROOT"] = str(store / "workspaces")
    os.environ["PUBLISHED_ROOT"] = str(store / "published")
    from motor_ai_sim import config, motor_access, users
    from motor_ai_sim import workspace as WS
    want = (store / "identity").resolve()
    for name, path in (("config", Path(config.DEFAULT_CONFIG_PATH).parent), ("users", users._USERS_FILE.parent),
                       ("die access", motor_access._DIE_ACCESS_FILE.parent), ("tombstones", WS._tombstones_file().parent),
                       ("process workspace", Path(str(WS.process_workspace().root)))):
        if Path(path).resolve() != want:
            raise SystemExit(f"refused: the {name} path {path} is not inside the store")
    for name, path in (("shared", WS.shared_root()), ("workspaces", WS.workspaces_root()), ("published", WS.published_root())):
        if not str(Path(str(path)).resolve()).startswith(str(store.resolve()) + "/"):
            raise SystemExit(f"refused: the {name} root {path} is not inside the store")
    global ROUTES
    ROUTES = _assert_destinations(store)


CLEARED_ENV: List[str] = []
#: --roots-are-mounts: on the server the four roots are four bind mounts under the store directory (as in the API container)
ROOTS_ARE_MOUNTS = False
STORE_ROOTS = ("shared", "identity", "workspaces", "published")


def mount_problem(root: Path, mounts_text: str, roots_are_mounts: bool, top_entries: List[str]) -> str:
    """'' when the store's layout is acceptable, else why not.  Without the flag nothing may be mounted inside the store.  With
    it, exactly the four roots may be mount points and the store may hold nothing else at its top; a mount BELOW a root is
    always refused."""
    allowed = {str(root / r) for r in STORE_ROOTS} if roots_are_mounts else set()
    if roots_are_mounts:
        extra = sorted(set(top_entries) - set(STORE_ROOTS))
        if extra:
            return f"the store holds other top-level entries {extra} (only {list(STORE_ROOTS)} with --roots-are-mounts)"
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        mp = parts[1].replace("\\040", " ")
        if mp.startswith(str(root) + "/") and mp not in allowed:
            return f"a filesystem is mounted inside the store ({mp})"
    return ""
ROUTES: Dict[str, str] = {}


def _assert_destinations(store: Path) -> Dict[str, str]:
    """Every store the called code can write, resolved by the DEPLOYED application's own resolvers, must lie inside the store
    (symlinks followed); no symlink in the store may point outside it and no other filesystem may be mounted inside it."""
    import importlib
    root = store.resolve()
    probes = {
        "admin audit": ("motor_ai_sim.admin_audit", "audit_path"),
        "object registry": ("motor_ai_sim.object_registry", "registry_root"),
        "display state": ("motor_ai_sim.display_state", "registry_root"),
        "config archive": ("motor_ai_sim.config_archive", "store_path"),
        "die archive": ("motor_ai_sim.die_archive", "store_path"),
        "projects": ("motor_ai_sim.development.store", "root"),
        "production": ("motor_ai_sim.production.store", "production_root"),
        "motor tests": ("motor_ai_sim.motor_tests.store", "root"),
        "motor revisions": ("motor_ai_sim.motor_revisions", "root"),
        "cad": ("motor_ai_sim.model3d", "cad_root"),
        "cad download log": ("motor_ai_sim.model3d", "download_log_path"),
        "presets": ("motor_ai_sim.routes.presets", "_presets_path"),
        "shared ledger": ("motor_ai_sim.duty_results", "shared_store_path"),
        "process ledger": ("motor_ai_sim.duty_results", "store_path"),
        "tombstones": ("motor_ai_sim.workspace", "_tombstones_file"),
        "users": ("motor_ai_sim.users", None),
    }
    out: Dict[str, str] = {}
    for name, (mod, fn) in probes.items():
        m = importlib.import_module(mod)
        try:
            path = Path(str(getattr(m, fn)() if fn else m._USERS_FILE))
        except AttributeError:
            raise SystemExit(f"refused: the deployed {mod} has no {fn}: its destination cannot be asserted")
        r = path.resolve()
        if r != root and not str(r).startswith(str(root) + "/"):
            raise SystemExit(f"refused: the {name} destination {path} (-> {r}) is outside the store")
        out[name] = str(r)
    for p in store.rglob("*"):
        if p.is_symlink():
            t = p.resolve()
            if not str(t).startswith(str(root) + "/"):
                raise SystemExit(f"refused: the symlink {p} points outside the store ({t})")
    try:
        mounts_text = Path("/proc/mounts").read_text()
    except OSError:
        mounts_text = ""
    problem = mount_problem(root, mounts_text, ROOTS_ARE_MOUNTS, [p.name for p in store.iterdir()])
    if problem:
        raise SystemExit(f"refused: {problem}")
    return out


def _fam():
    from motor_ai_sim.routes import family as F
    return F


def preflight(store: Path, m: Dict[str, Any]) -> List[str]:
    """Refusals; [] = the switch may run."""
    dies = store / "shared" / "dies"
    pred, succ, retired = m["pred"], m["succ"], m["retired"]
    out = []
    if len({pred.casefold(), succ.casefold(), retired.casefold()}) < 3:
        out.append("the predecessor, the successor and the retired name must differ (also in letter case)")
    for n in (pred, succ):
        if not (dies / n / "die.yaml").is_file():
            out.append(f"'{n}' is not a die of the shared catalog")
    taken = {p.name.casefold() for p in dies.iterdir() if p.is_dir()}
    for r in _layer_roots(store):
        taken |= {p.name.casefold() for p in (r / "dies").glob("*") if p.is_dir()}
        if (r / "dies" / succ).is_dir():
            out.append(f"the successor has a copy in a non-shared layer: {(r / 'dies' / succ).relative_to(store)}")
    if retired.casefold() in taken:
        out.append(f"the retired name '{retired}' is taken")
    for c in m["continuing"]:
        if not (dies / pred / f"{c['config']}.yaml").is_file():
            out.append(f"'{pred}/{c['config']}' does not exist")
        if c["unknown"] or any(d["unknown"] for d in c["duties"]):
            out.append(f"'{pred}/{c['config']}' has keys on neither list")
        aside = (c.get("successor_own") or {}).get("moved_aside_as")
        if aside and (dies / succ / f"{aside.format(date=m['date'])}.yaml").exists():
            out.append(f"the aside name '{aside.format(date=m['date'])}' is taken")
        for root in ("production", "motor_revisions"):
            for d in (store / "shared" / root).glob("*"):
                if d.name in (pred, pred.replace(" ", "_")) and (d / c["config"]).exists():
                    out.append(f"'{pred}/{c['config']}' has {root} records")
    return out


def _snapshot_access_only(retired: str, continuing: set):
    """Replaces ``config_move._rewrite_snapshot`` during the link move: a project snapshot keeps its machine (the retired die,
    the previous sheet's numbers) AND its access scope (the two must agree: development/routes._catalog_origin fails closed for a
    non-admin otherwise) and is marked ``previous_sheet``; the members keep it through their grants on the retired die."""
    def rewrite(ctx, snap):
        n = 0
        machine = snap.get("machine") if isinstance(snap, dict) else None
        if (isinstance(machine, dict) and machine.get("die_name") == retired and machine.get("config_name") in continuing
                and not snap.get("previous_sheet")):
            snap["previous_sheet"] = {"die": retired, "successor": ctx.dst}
            n += 1
        return n
    return rewrite


def _mark_saved_sweeps(store: Path, pred: str, retired: str, continuing: set, dry_run: bool) -> List[str]:
    """Saved sweeps (``optimization_saves/*.json``) and Compare rows whose machine is ``pred/<continuing>``: they name the
    retired die and say ``previous_sheet``; the numbers are kept.  Runs BEFORE the renames (afterwards ``pred`` is the
    successor and the two could not be told apart)."""
    hits = []
    for f in _lglob(store, "optimization_saves/*.json") + sorted(store.glob("shared/optimization_saves/*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        n = 0
        for holder in (doc, doc.get("result") if isinstance(doc, dict) else None):
            mach = holder.get("machine") if isinstance(holder, dict) else None
            if isinstance(mach, dict):
                die = mach.get("die") or mach.get("die_name")
                cfg = mach.get("config") or mach.get("config_name")
                if die == pred:
                    for k in ("die", "die_name"):
                        if mach.get(k) == pred:
                            mach[k] = retired
                    n += 1
                    if cfg in continuing or not cfg:
                        doc["previous_sheet"] = {"die": retired}
        if n:
            hits.append(str(f.relative_to(store)))
            if not dry_run:
                from motor_ai_sim import json_store
                json_store.atomic_write_json(f, doc)
    for f in _lglob(store, "saved_simulations.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        n = 0
        for rows in (doc.values() if isinstance(doc, dict) else []):
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                src = row.get("source") if isinstance(row.get("source"), dict) else {}
                params = row.get("params") if isinstance(row.get("params"), dict) else {}
                if (src.get("die") or params.get("die")) == pred:
                    for h in (src, params):
                        if h.get("die") == pred:
                            h["die"] = retired
                    row["previous_sheet"] = {"die": retired}
                    n += 1
        if n:
            hits.append(str(f.relative_to(store)))
            if not dry_run:
                from motor_ai_sim import json_store
                json_store.atomic_write_json(f, doc)
    return hits


def _layer_roots(store: Path) -> List[Path]:
    """Every NON-shared layer root a reader can resolve a die or a result through: the process workspace (``identity/``, the
    MOTOR_AI_SIM_CONFIG directory: anonymous callers, the service token, background jobs - workspace.py process_workspace /
    layers), every account workspace and every published namespace."""
    roots = [store / "identity"]
    for top in ("workspaces", "published"):
        roots += [d for d in sorted((store / top).glob("*")) if d.is_dir() and not d.name.startswith(".")]
    return roots


def _lglob(store: Path, pattern: str) -> List[Path]:
    return [f for r in _layer_roots(store) for f in sorted(r.glob(pattern))]


def _move_shadows(store: Path, pred: str, retired: str, dry_run: bool) -> List[str]:
    """A die folder named like the predecessor in a non-shared layer (``<layer>/dies/<pred>``) is read BEFORE the shared
    catalog by that layer's callers (workspace.layers): it would shadow the successor.  It follows the predecessor to the
    retired name, its history too; the die / configuration documents in it name the retired die."""
    import yaml
    moved = []
    for r in _layer_roots(store):
        src = r / "dies" / pred
        if not src.is_dir():
            continue
        dst = r / "dies" / retired
        if dst.exists():
            raise SystemExit(f"refused: {dst.relative_to(store)} exists")
        moved.append(str(src.relative_to(store)))
        if dry_run:
            continue
        src.rename(dst)
        hs, hd = r / "dies" / ".history" / pred, r / "dies" / ".history" / retired
        if hs.is_dir() and not hd.exists():
            hs.rename(hd)
        for f in sorted(dst.glob("*.yaml")):
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            body = doc["config"] if isinstance(doc.get("config"), dict) else doc
            key = "name" if f.name == "die.yaml" else "die"
            if isinstance(body, dict) and body.get(key) == pred:
                body[key] = retired
                f.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return moved


def _plan_paths(store: Path, m: Dict[str, Any]) -> Tuple[List[Path], List[Path]]:
    """(files, directories) the switch may rewrite, move, delete or create entries in: every file the inventory names, the
    two dies' folders and histories, every non-shared layer copy of either, the ledgers / saved simulations / sweeps / last
    scans of every layer, the cached answers, the identity and registry stores; their parent directories."""
    pred, succ = m["pred"], m["succ"]
    inv = json.loads(Path(m["inventory_path"]).read_text(encoding="utf-8")) if m.get("inventory_path") else {"files": []}
    files = {store / f["file"] for f in inv["files"]}
    dies = store / "shared" / "dies"
    roots = [dies / pred, dies / succ, dies / ".history" / pred, dies / ".history" / succ]
    for r in _layer_roots(store):
        roots += [r / "dies" / pred, r / "dies" / ".history" / pred]
    for r in roots:
        if r.is_dir():
            files |= {f for f in r.rglob("*") if f.is_file()}
    for pat in (".duty_results.json", "saved_simulations.json", "optimization_saves/*.json", ".last_scan.json"):
        files |= set(_lglob(store, pat))
    files |= set((store / "shared" / ".answers").rglob("*.json"))
    files |= {store / "shared" / ".duty_results.json", store / "identity" / "users.json", store / "identity" / "admin_audit.jsonl"}
    files |= set((store / "shared" / "object_registry").glob("*.json"))
    files = {f for f in files if f.exists()}
    dirs = {f.parent for f in files} | {dies, store / "shared", store / "identity", store / "shared" / "object_registry"}
    dirs |= {r / "dies" for r in _layer_roots(store) if (r / "dies" / pred).is_dir()}
    dirs |= {d for r in roots if r.is_dir() for d in [r, *r.rglob("*")] if d.is_dir()}
    return sorted(files), sorted(d for d in dirs if d.exists())


def permission_problems(store: Path, m: Dict[str, Any]) -> Dict[str, List[str]]:
    """What the running uid cannot do on the plan's paths: ``refuse`` (not readable, a read-only file it does not own, a
    directory it cannot write) and ``readonly_owned`` (a read-only file it owns: writable after ``chmod u+w``)."""
    import os
    import stat
    uid = os.geteuid()
    files, dirs = _plan_paths(store, m)
    refuse, fix = [], []
    for f in files:
        st = f.lstat()
        rel = str(f.relative_to(store))
        if not os.access(f, os.R_OK):
            refuse.append(f"{rel}: not readable (owner {st.st_uid}, mode {stat.filemode(st.st_mode)})")
        elif not os.access(f, os.W_OK):
            (fix if st.st_uid == uid else refuse).append(
                f"{rel}: read-only (owner {st.st_uid}, mode {stat.filemode(st.st_mode)})")
    for d in dirs:
        if not os.access(d, os.W_OK | os.X_OK):
            st = d.stat()
            refuse.append(f"{d.relative_to(store)}/: directory not writable (owner {st.st_uid}, mode {stat.filemode(st.st_mode)})")
    return {"refuse": refuse, "readonly_owned": fix, "uid": [str(uid)], "files_checked": [str(len(files))],
            "dirs_checked": [str(len(dirs))]}


def _make_writable(store: Path, rels: List[str]) -> List[Dict[str, Any]]:
    """chmod u+w on each read-only file the switch may rewrite; the original modes are returned (and restored afterwards)."""
    import os
    import stat
    out = []
    for line in rels:
        rel = line.split(": read-only", 1)[0]
        p = store / rel
        mode = stat.S_IMODE(p.stat().st_mode)
        os.chmod(p, mode | stat.S_IWUSR)
        out.append({"file": rel, "mode": oct(mode)})
    return out


def _restore_modes(store: Path, made: List[Dict[str, Any]], m: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Put back each original mode, at the file's path after the switch (a moved file is found under its new name)."""
    import os
    pred, succ, retired = m["pred"], m["succ"], m["retired"]
    done = []
    for e in made:
        rel = e["file"]
        cands = [rel]
        for a, b in ((f"/dies/{pred}/", f"/dies/{retired}/"), (f"/.history/{pred}/", f"/.history/{retired}/"),
                     (f"/dies/{succ}/", f"/dies/{pred}/"), (f"/.history/{succ}/", f"/.history/{pred}/")):
            if a in "/" + rel:
                cands.append(("/" + rel).replace(a, b, 1)[1:])
        hit = next((c for c in cands if (store / c).exists()), None)
        if hit:
            os.chmod(store / hit, int(e["mode"], 8))
        done.append({**e, "restored_at": hit})
    return done


def _relabel_last_scans(store: Path, pred: str, succ: str, retired: str, aside_map: Dict[str, str]) -> List[str]:
    """``.last_scan.json`` (the last sweep a user ran) names the machine it ran on: the predecessor's -> the retired name, the
    successor's moved-aside working copy -> its aside name under the predecessor's name."""
    from motor_ai_sim import json_store
    hits = []
    for f in _lglob(store, ".last_scan.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        mach = doc.get("machine") if isinstance(doc, dict) else None
        if not isinstance(mach, dict):
            continue
        if mach.get("die") == pred:
            mach["die"] = retired
            doc["previous_sheet"] = {"die": retired}
        elif mach.get("die") == succ:
            mach["die"] = pred
            mach["config"] = aside_map.get(mach.get("config"), mach.get("config"))
        else:
            continue
        json_store.atomic_write_json(f, doc)
        hits.append(str(f.relative_to(store)))
    return hits


def _relabel_compare_names(store: Path, pred: str, retired: str) -> List[str]:
    """Saved Compare rows that name their machine only in the row name ("<pred> / <cfg> / <duty> ..."): computed on the
    previous sheet, so the name says the retired die and the row says ``previous_sheet``; the numbers are kept."""
    from motor_ai_sim import json_store
    hits = []
    for f in _lglob(store, "saved_simulations.json"):
        doc = json.loads(f.read_text(encoding="utf-8"))
        n = 0
        for rows in (doc.values() if isinstance(doc, dict) else []):
            for row in rows if isinstance(rows, list) else []:
                name = row.get("name") if isinstance(row, dict) else None
                if isinstance(name, str) and name.startswith(f"{pred} / ") and not row.get("previous_sheet"):
                    row["name"] = retired + name[len(pred):]
                    row["previous_sheet"] = {"die": retired, "was_name": name}
                    n += 1
        if n:
            json_store.atomic_write_json(f, doc)
            hits.append(f"{f.relative_to(store)} ({n} rows)")
    return hits


def _drop_cached_answers(store: Path, pred: str, dry_run: bool) -> List[str]:
    """Cached answers computed on the previous sheet (``shared/.answers``): removed, they are rebuilt on demand."""
    hits = []
    for f in sorted((store / "shared" / ".answers").rglob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(n == pred for _p, n in _names_in(doc, (pred,))):
            hits.append(str(f.relative_to(store)))
            if not dry_run:
                f.unlink()
    return hits


def _grants_additive(pred: str, retired: str, cont: List[str]) -> Dict[str, Any]:
    """Accounts keep every grant on the retired die (its snapshots and old configurations stay theirs) and get the same on the
    successor: a die-level grant on the retired die adds one on the successor, a config grant on a continuing configuration adds
    the successor's copy (its ``sources`` entry copied).  Nothing is removed."""
    from motor_ai_sim import json_store, users
    out: Dict[str, Any] = {"accounts": 0, "added": 0}

    def edit(doc: Any) -> None:
        table = doc.get("users") if isinstance(doc.get("users"), dict) else doc
        for rec in table.values():
            mot = rec.get("motors") if isinstance(rec, dict) else None
            if not isinstance(mot, dict):
                continue
            n = 0
            dies_ = mot.get("dies")
            if isinstance(dies_, list) and retired in dies_ and pred not in dies_:
                dies_.append(pred)
                n += 1
            cfgs = mot.get("configs")
            if isinstance(cfgs, list):
                for c in cont:
                    if f"{retired}/{c}" in cfgs and f"{pred}/{c}" not in cfgs:
                        cfgs.append(f"{pred}/{c}")
                        src = mot.get("sources")
                        if isinstance(src, dict) and f"{retired}/{c}" in src:
                            src[f"{pred}/{c}"] = src[f"{retired}/{c}"]
                        n += 1
            if n:
                out["accounts"] += 1
                out["added"] += n
        # NB: return nothing - json_store.mutate_json takes a returned value as the new document
    with users._LOCK:
        json_store.mutate_json(users._USERS_FILE, edit, default={})
    return out


def _registry(pred: str, retired: str, cont: List[str], aside: List[str], projects: List[str]) -> Dict[str, Any]:
    from motor_ai_sim import json_store
    from motor_ai_sim import object_registry as OR
    out: Dict[str, Any] = {"minted": [], "aliases_dropped": [], "links_moved": []}

    def edit(doc: Any) -> None:
        objects, links = doc["objects"], doc["links"]
        at = OR._now()
        def find(kind, key):
            return next((o for o in objects.values() if o.get("kind") == kind and o.get("locator", {}).get("key") == key), None)
        fam = find("family", pred)
        if fam is None:
            fid = OR._mint("family", objects)
            objects[fid] = fam = {"id": fid, "kind": "family", "locator": {"ns": "catalog", "key": pred}, "aliases": [],
                                  "created_at": at, "family_kind": "die", "name": pred, "layers": ["shared"]}
            out["minted"].append(fid)
        old_fam = find("family", retired)
        if old_fam and pred in old_fam.get("aliases", []):
            old_fam["aliases"].remove(pred)
            out["aliases_dropped"].append(f"{old_fam['id']}: {pred}")
        for cfg in cont + aside:
            key = f"{pred}/{cfg}"
            it = find("item", key)
            if it is None:
                iid = OR._mint("item", objects)
                objects[iid] = it = {"id": iid, "kind": "item", "locator": {"ns": "catalog", "key": key}, "aliases": [],
                                     "created_at": at, "item_kind": "motor", "family": fam["id"], "die": pred, "config": cfg,
                                     "state": "present", "layers": ["shared"]}
                out["minted"].append(iid)
            old = find("item", f"{retired}/{cfg}")
            if old is None:
                continue
            if key in old.get("aliases", []):
                old["aliases"].remove(key)
                out["aliases_dropped"].append(f"{old['id']}: {key}")
            for lid, ln in list(links.items()):
                if ln.get("kind") == "project_item" and ln.get("to") == old["id"]:
                    prj = objects.get(ln.get("from"), {}).get("locator", {}).get("key")
                    if prj in projects:
                        nid = f"project_item:{ln['from']}:{it['id']}"
                        links.pop(lid)
                        links[nid] = {**ln, "id": nid, "to": it["id"], "previous_sheet_item": old["id"]}
                        out["links_moved"].append(f"{prj}: {old['id']} -> {it['id']}")
        # NB: return nothing - json_store.mutate_json takes a returned value as the new document
    json_store.mutate_json(OR.store_path(), edit, default=OR._empty())
    return out


def switch(store: Path, m: Dict[str, Any], *, dry_run: bool, actor: str) -> Dict[str, Any]:
    _point_at(store)
    if "never_nested" in m:                         # the manifest's own choice of dropped nested keys is what is applied
        NEVER_NESTED.clear()
        NEVER_NESTED.update({tuple(k.split(".", 1)): "dropped by the manifest" for k in m["never_nested"]})
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import config_move as CM
    from motor_ai_sim import die_rename as DRN
    from motor_ai_sim import workspace as WS
    F = _fam()
    pred, succ, retired, date = m["pred"], m["succ"], m["retired"], m["date"]
    dies = store / "shared" / "dies"
    cont = [c["config"] for c in m["continuing"]]
    log: List[Dict[str, Any]] = []
    F._IN_PLACE_PARENT.set(str(dies))          # every write lands in the store's shared catalog, never in a process workspace
    refusals = preflight(store, m)
    perms = permission_problems(store, m)
    refusals += [f"permission: {x}" for x in perms["refuse"]]
    if perms["readonly_owned"] and not m.get("chmod_readonly"):
        refusals += [f"permission: {x} (pass --chmod-readonly to make it writable for the switch and restore its mode)"
                     for x in perms["readonly_owned"]]
    if refusals:
        return {"refused": refusals, "permissions": perms}
    made: List[Dict[str, Any]] = []
    if not dry_run and perms["readonly_owned"]:
        made = _make_writable(store, perms["readonly_owned"])
    try:
        out = _switch_steps(store, m, dry_run=dry_run, actor=actor, F=F, CA=CA, CM=CM, DRN=DRN, WS=WS)
    finally:
        if made:
            restored = _restore_modes(store, made, m)
    out["permissions"] = perms
    if made:
        out["chmod_restored"] = restored
    return out


def _switch_steps(store: Path, m: Dict[str, Any], *, dry_run: bool, actor: str, F, CA, CM, DRN, WS) -> Dict[str, Any]:
    pred, succ, retired, date = m["pred"], m["succ"], m["retired"], m["date"]
    dies = store / "shared" / "dies"
    cont = [c["config"] for c in m["continuing"]]
    log: List[Dict[str, Any]] = []
    # 1. pocket clearance (a parameter; None = the successor's sheet as stored)
    if m.get("pocket_clearance_slot", {}).get("value") is not None:
        v = float(m["pocket_clearance_slot"]["value"])
        log.append({"step": "pocket", "die": succ, "magnet_pocket_clearance": v})
        if not dry_run:
            doc = _y(dies / succ / "die.yaml")
            doc.setdefault("geometry", {})["magnet_pocket_clearance"] = v
            F._save_yaml(dies / succ / "die.yaml", doc)
    # 2. the successor's own working copies of continuing names: aside + archived
    for c in m["continuing"]:
        own = c.get("successor_own")
        if not own:
            continue
        aside = own["moved_aside_as"].format(date=date)
        r = CM.run(F, succ, c["config"], succ, src_dir=dies / succ, dst_dir=dies / succ, dry_run=dry_run, copy=False,
                   glob_scope=True, actor=actor, new_cfg=aside)
        log.append({"step": "aside", "config": c["config"], "as": aside, "changes": len(r.get("changes") or [])})
        if not dry_run:
            CA.archive(succ, aside, by=actor, reason=f"working copy moved aside by the sheet switch of {pred}")
    # 3. history of the previous sheet that the renames do not carry: saved sweeps / Compare rows, cached answers
    log.append({"step": "saved_sweeps", "files": _mark_saved_sweeps(store, pred, retired, set(cont), dry_run)})
    log.append({"step": "cached_answers", "files": _drop_cached_answers(store, pred, dry_run)})
    # the successor's own saved sweeps of a moved-aside name belong to the aside copy (relabelled after the renames)
    aside_map = {c["config"]: c["successor_own"]["moved_aside_as"].format(date=date) for c in m["continuing"] if c.get("successor_own")}
    own_sweeps = []
    for f in _lglob(store, "optimization_saves/*.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        mach = doc.get("machine") if isinstance(doc, dict) else None
        if isinstance(mach, dict) and mach.get("die") == succ and mach.get("config") in aside_map:
            own_sweeps.append(f)
    log.append({"step": "successor_sweeps", "files": [str(f.relative_to(store)) for f in own_sweeps]})
    # 4. the two renames (every store die_rename knows)
    for old, new in ((pred, retired), (succ, pred)):
        if dry_run and old == succ:
            log.append({"step": "rename", "from": old, "to": new, "changes": "planned after the first rename"})
            continue
        r = DRN.run(F, old, new, src_dir=dies / old, dst_dir=dies / new, dry_run=dry_run, glob_scope=True, actor=actor)
        log.append({"step": "rename", "from": old, "to": new, "changes": len(r.get("changes") or []),
                    "manual": r.get("manual") or []})
    log.append({"step": "layer_shadows", "moved": _move_shadows(store, pred, retired, dry_run)})
    # 4b. every workspace / published ledger: rows of the predecessor -> the retired name, rows of the successor -> the
    #     predecessor's name (die_rename rekeys the shared ledger only); the application's own duty_results.rename_die
    if not dry_run:
        from motor_ai_sim import duty_results as DRS
        moved = []
        for led in _lglob(store, ".duty_results.json"):
            with DRS.using_store(led):
                a = DRS.rename_die(pred, retired)
                b = DRS.rename_die(succ, pred)
            if a or b:
                moved.append({"ledger": str(led.relative_to(store)), "predecessor": a, "successor": b})
        log.append({"step": "workspace_ledgers", "ledgers": moved})
        log.append({"step": "last_scans", "files": _relabel_last_scans(store, pred, succ, retired, aside_map)})
        log.append({"step": "compare_names", "files": _relabel_compare_names(store, pred, retired)})
    # 5. the definition copies (results empty) and their links
    if not dry_run:
        from motor_ai_sim import json_store
        for f in own_sweeps:
            doc = json.loads(f.read_text(encoding="utf-8"))
            for holder in (doc, doc.get("result") if isinstance(doc.get("result"), dict) else None):
                mach = holder.get("machine") if isinstance(holder, dict) else None
                if isinstance(mach, dict) and mach.get("config") in aside_map:
                    mach["config"] = aside_map[mach["config"]]
                    mach["die"] = pred
            json_store.atomic_write_json(f, doc)
        for cfg in cont:
            F._save_yaml(dies / pred / f"{cfg}.yaml", definition_copy(_y(dies / retired / f"{cfg}.yaml"), pred, retired))
        keep = CM._rewrite_snapshot
        CM._rewrite_snapshot = _snapshot_access_only(retired, set(cont))
        try:
            for cfg in cont:
                ctx = CM.Ctx(fam=F, die=retired, cfg=cfg, dst=pred, src_dir=dies / retired, dst_dir=dies / pred,
                             dry_run=False, copy=False, glob_scope=True, ws_roots=CM.workspace_roots(F, True), actor=actor)
                ctx.backup_dir = store / "shared" / ".config_moves" / f"{date} sheet switch {retired}~{cfg}"
                ctx.backup_dir.mkdir(parents=True, exist_ok=True)
                for step in (CM.step_projects, CM.step_cost_estimates, CM.step_display_state):
                    step(ctx)
                log.append({"step": "links", "config": cfg, "changes": ctx.changes})
        finally:
            CM._rewrite_snapshot = keep
        log.append({"step": "grants", **_grants_additive(pred, retired, cont)})
        # 6. the registry: the successor and its copies get their OWN items (a backfill would resolve "<die>/<cfg>" through
        #    the retired item's alias); the retired die and its continuing items drop those aliases; project links follow
        log.append({"step": "registry", **_registry(pred, retired, cont, list(aside_map.values()), [p["project"] for p in m["projects"]])})
        # 7. the retired die is NOT tombstoned: its access stays as it was, so every project member keeps the snapshots and
        #    old configurations he could open before (the current motors of the projects are the successor's copies)
        log.append({"step": "retire", "die": retired, "tombstone": False})
    return {"refused": [], "dry_run": dry_run, "roots_are_mounts": ROOTS_ARE_MOUNTS, "cleared_env": CLEARED_ENV,
            "destinations": ROUTES, "log": log}



# ── the report and the checker (pristine copy vs the switched store) ──────────────────────────────────────────────────

def _diff_paths(a: Any, b: Any, path: str = "$", out: Optional[List[str]] = None, cap: int = 60) -> List[str]:
    out = [] if out is None else out
    if len(out) >= cap:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append(f"+{path}.{k}")
            elif k not in b:
                out.append(f"-{path}.{k}")
            else:
                _diff_paths(a[k], b[k], f"{path}.{k}", out, cap)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff_paths(x, y, f"{path}[{i}]", out, cap)
    elif a != b:
        out.append(f"~{path}")
    return out


UNREADABLE: Dict[str, List[str]] = {}


def _hashes(root: Path) -> Dict[str, str]:
    """sha256 of every regular file; a file the running uid cannot read is skipped and listed in ``UNREADABLE``."""
    import hashlib
    out = {}
    skipped = UNREADABLE.setdefault(str(root), [])
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            h = hashlib.sha256()
            try:
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
            except PermissionError:
                skipped.append(str(p.relative_to(root)))
                continue
            out[str(p.relative_to(root))] = h.hexdigest()
    return out


def report(before: Path, after: Path) -> Dict[str, Any]:
    hb, ha = _hashes(before), _hashes(after)
    removed = sorted(set(hb) - set(ha))
    added = sorted(set(ha) - set(hb))
    by_hash: Dict[str, List[str]] = {}
    for r in removed:
        by_hash.setdefault(hb[r], []).append(r)
    renamed, plain_added = [], []
    for a in added:
        src = by_hash.get(ha[a])
        if src:
            renamed.append({"from": src.pop(0), "to": a})
        else:
            plain_added.append(a)
    moved_from = {r["from"] for r in renamed}
    changed = []
    for f in sorted(set(hb) & set(ha)):
        if hb[f] != ha[f]:
            row = {"file": f}
            if Path(f).suffix in TEXT_SUFFIXES:
                da, db = _load(before / f), _load(after / f)
                row["keys"] = _diff_paths(da, db) if len(da) == len(db) else [f"documents {len(da)} -> {len(db)}"]
            changed.append(row)
    return {"added": plain_added, "removed": [r for r in removed if r not in moved_from], "renamed": renamed, "changed": changed}


RESULT_KEYS = ("result", "summary", "runs", "solved_on", "cogging", "cad_mass", "duty_class")


def check(before: Path, after: Path, m: Dict[str, Any], rep: Dict[str, Any]) -> Dict[str, Any]:
    pred, succ, retired = m["pred"], m["succ"], m["retired"]
    cont = [c["config"] for c in m["continuing"]]
    res: Dict[str, Any] = {}
    bd, ad = before / "shared" / "dies", after / "shared" / "dies"
    # (1) no result of the previous sheet under the successor's continuing configurations
    bad = []
    for c in cont:
        body = _y(ad / pred / f"{c}.yaml")
        body = body.get("config", body) if isinstance(body.get("config"), dict) else body
        bad += [f"{c}: {k}" for k in RESULT_KEYS if k in body]
        bad += [f"{c}/{d.get('name')}: {k}" for d in body.get("duties") or [] for k in RESULT_KEYS if k in d]
        if (ad / pred / "runs" / c).exists():
            bad.append(f"{c}: runs/ folder")
    # the ledgers, by their real structure (results[die][cfg], history[die][cfg]) and through the application's own reader
    _point_at(after)
    from motor_ai_sim import duty_results as DRS
    ledgers = [Path("shared/.duty_results.json")] + [p.relative_to(after) for p in _lglob(after, ".duty_results.json")]
    aside = {c["config"]: c["successor_own"]["moved_aside_as"].format(date=m.get("date", "")) for c in m["continuing"]
             if c.get("successor_own")}
    ledger_rows = []
    for rel in ledgers:
        la, lb = after / rel, before / rel
        da = json.loads(la.read_text(encoding="utf-8")) if la.is_file() else {}
        db = json.loads(lb.read_text(encoding="utf-8")) if lb.is_file() else {}
        for block in ("results", "history"):
            node = (da.get(block) or {}).get(pred) or {}
            bad += [f"{rel}: {block}[{pred}][{c}]" for c in cont if c in node]
            # the predecessor's rows are the retired die's now; the successor's are the predecessor's (aside renamed)
            want_r = (db.get(block) or {}).get(pred) or {}
            want_p = {aside.get(k, k): v for k, v in ((db.get(block) or {}).get(succ) or {}).items()}
            got_r = (da.get(block) or {}).get(retired) or {}
            got_p = {k: v for k, v in ((da.get(block) or {}).get(pred) or {}).items()}
            if want_r != got_r:
                bad.append(f"{rel}: {block}[{retired}] differs from the predecessor's rows before")
            if want_p != got_p:
                bad.append(f"{rel}: {block}[{pred}] differs from the successor's rows before")
            if (da.get(block) or {}).get(succ):
                bad.append(f"{rel}: {block}[{succ}] still has rows")
            ledger_rows.append({"ledger": str(rel), "block": block, "retired_rows": len(got_r), "successor_rows": len(got_p)})
        if la.is_file():
            with DRS.using_store(la):
                for c in cont:
                    body = _y(ad / pred / f"{c}.yaml")
                    body = body.get("config", body) if isinstance(body.get("config"), dict) else body
                    if DRS.get(pred, c):
                        bad.append(f"{rel}: duty_results.get({pred}, {c}) returns {sorted(DRS.get(pred, c))}")
                    for d in body.get("duties") or []:
                        if DRS.history(pred, c, str(d.get("name"))):
                            bad.append(f"{rel}: duty_results.history({pred}, {c}, {d.get('name')}) is not empty")
    res["ledgers"] = {"ok": not any(": " in b and ("results[" in b or "history[" in b or "duty_results." in b) for b in bad),
                      "rows": ledger_rows}
    for f in sorted((after / "shared" / ".answers").rglob("*.json")):
        docs = _load(f)
        if docs and any(True for _ in _names_in(docs[0], tuple(f"{pred}/{c}" for c in cont) + (pred,))):
            bad.append(f"cached answer {f.relative_to(after)}")
    # the cogging and field stores of the copies (the configuration's own files beside it) must not exist
    for c in cont:
        bad += [f"{p.relative_to(after)}" for p in (ad / pred).glob(f"{c}.*") if p.suffix != ".yaml"]
    res["no_old_result_under_successor"] = {"ok": not bad, "found": bad[:40], "count": len(bad)}
    # (2) the predecessor complete under the retired name (each file the same apart from the die name)
    miss = []
    for f in sorted((bd / pred).rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(bd / pred)
        g = ad / retired / rel
        if not g.is_file():
            miss.append(f"missing {rel}")
        elif f.suffix == ".npz":
            # die_rename rewrites the die name in a field file's ``meta``; every array must be identical
            import numpy as np
            x, y = np.load(f, allow_pickle=False), np.load(g, allow_pickle=False)
            if sorted(x.files) != sorted(y.files):
                miss.append(f"{rel}: arrays {sorted(set(x.files) ^ set(y.files))}")
            for k in x.files:
                if k == "meta" or k not in y.files:
                    continue
                if not np.array_equal(x[k], y[k]):
                    miss.append(f"{rel}: array {k} differs")
            if "meta" in x.files and "meta" in y.files:
                d = [p for p in _diff_paths(json.loads(str(x["meta"])), json.loads(str(y["meta"])))
                     if not p.endswith((".die", ".die_name"))]
                if d:
                    miss.append(f"{rel}: meta {d[:5]}")
        elif f.suffix not in (".yaml", ".yml", ".json"):
            import hashlib
            if hashlib.sha256(f.read_bytes()).digest() != hashlib.sha256(g.read_bytes()).digest():
                miss.append(f"{rel}: bytes differ")
        elif f.suffix in (".yaml", ".yml", ".json"):
            a, b = _load(f), _load(g)
            d = [x for x in _diff_paths(a, b) if not x.endswith((".die", ".die_name", ".copied_from.die"))
                 and not (rel.name == "die.yaml" and x.endswith(".name"))]
            if d:
                miss.append(f"{rel}: {d[:5]}")
    res["predecessor_complete_as_retired"] = {"ok": not miss, "problems": miss[:40], "count": len(miss)}
    # (2b) no die named like the predecessor in any non-shared layer (it would shadow the successor), every such copy kept
    #      under the retired name, and the application's resolver sends a caller without a workspace to the shared successor
    shadow = []
    for r in _layer_roots(before):
        rel = r.relative_to(before)
        if (after / rel / "dies" / pred).exists():
            shadow.append(f"{rel}/dies/{pred} still exists")
        src = r / "dies" / pred
        for f in sorted(src.rglob("*")) if src.is_dir() else []:
            g = after / rel / "dies" / retired / f.relative_to(src)
            if f.is_file() and not g.is_file():
                shadow.append(f"missing {g.relative_to(after)}")
            elif f.is_file() and f.suffix in (".yaml", ".yml"):
                d = [x for x in _diff_paths(_load(f), _load(g)) if not x.endswith((".name", ".die"))]
                if d:
                    shadow.append(f"{g.relative_to(after)}: {d[:5]}")
    from motor_ai_sim import workspace as WS
    with WS.use_workspace(WS.process_workspace()):
        got = WS.resolve_die_dir(pred)
    if got is None or Path(str(got)).resolve() != (ad / pred).resolve():
        shadow.append(f"resolve_die_dir({pred!r}) in the process workspace -> {got}")
    res["no_layer_shadow"] = {"ok": not shadow, "problems": shadow}
    # (3) every link of the projects resolves, and their snapshots name the retired die
    links = []
    for p in m["projects"]:
        pj = json.loads((after / "shared" / "projects" / p["project"] / "project.json").read_text(encoding="utf-8"))
        for lm in pj.get("linked_motors") or []:
            if isinstance(lm, dict) and lm.get("config") in p["links"]:
                ok = lm.get("die") == pred and (ad / pred / f"{lm['config']}.yaml").is_file()
                links.append({"project": p["project"], "link": f"{lm.get('die')}/{lm.get('config')}", "ok": ok})
        for sp in sorted((after / "shared" / "projects" / p["project"] / "snapshots").glob("*.json")):
            sn = json.loads(sp.read_text(encoding="utf-8"))
            mach = sn.get("machine") or {}
            if mach.get("die_name") in (pred, retired) and mach.get("config_name") in cont:
                ok = mach.get("die_name") == retired and bool(sn.get("previous_sheet"))
                if not ok:
                    links.append({"project": p["project"], "snapshot": sp.name, "machine": mach.get("die_name"), "ok": False})
    res["project_links_resolve"] = {"ok": all(x["ok"] for x in links), "rows": links}
    # (4) grants: who saw a continuing configuration still does
    def grants(path: Path) -> Dict[str, set]:
        doc = json.loads(path.read_text(encoding="utf-8"))
        out: Dict[str, set] = {}
        for u in (doc.get("users") or doc).values() if isinstance(doc, dict) else []:
            if not isinstance(u, dict):
                continue
            mot = u.get("motors") or {}
            keys = set()
            for x in (mot.get("configs") or []) if isinstance(mot, dict) else []:
                keys.add(x)
            for x in (mot.get("dies") or []) if isinstance(mot, dict) else []:
                keys.add(x + "/*")
            out[str(u.get("name") or id(u))] = keys
        return out
    gb, ga = grants(before / "identity" / "users.json"), grants(after / "identity" / "users.json")
    lost = [f"{u}: {pred}/{c}" for u, ks in gb.items() for c in cont
            if (f"{pred}/{c}" in ks or f"{pred}/*" in ks) and not (f"{pred}/{c}" in ga.get(u, set()) or f"{pred}/*" in ga.get(u, set()))]
    res["grants_kept"] = {"ok": not lost, "lost": lost}
    # (5) nothing outside the manifest was touched
    named = {f["file"] for f in json.loads(Path(m["inventory_path"]).read_text())["files"]}
    def outside(path: str) -> bool:
        return (path not in named and not path.startswith(f"shared/dies/{pred}/") and not path.startswith(f"shared/dies/{retired}/")
                and not path.startswith(f"shared/dies/{succ}/") and "/.config_moves/" not in "/" + path
                and not path.startswith("shared/object_registry/") and not path.startswith("shared/.history"))
    def lineage_only(path: str) -> bool:
        """Another die's die.yaml (or its history entry) whose only change is its ``derived_from.die`` (now the retired name)."""
        parts = path.split("/")
        if parts[:2] != ["shared", "dies"] or len(parts) < 4:
            return False
        die = parts[3] if parts[2] == ".history" else parts[2]
        a, b = _load(before / "shared" / "dies" / die / "die.yaml"), _load(after / "shared" / "dies" / die / "die.yaml")
        return bool(a and b) and _diff_paths(a, b) == ["~$[0].derived_from.die"]
    def compare_relabel_only(path: str) -> bool:
        """A saved-simulations file whose only change is old-sheet Compare rows relabelled (name + previous_sheet)."""
        if not path.endswith("saved_simulations.json"):
            return False
        d = _diff_paths(_load(before / path), _load(after / path), cap=10000)
        return bool(d) and all(x.endswith(".name") or x.endswith(".previous_sheet") for x in d)
    def moved_shadow(path: str) -> bool:
        """A layer's copy of the predecessor, moved to the retired name with its history."""
        return any(path.startswith(f"{r.relative_to(before)}/dies/{n}/") for r in _layer_roots(before)
                   for n in (retired, f".history/{retired}"))
    def explained(path: str) -> bool:
        return (path.endswith(".lock") or compare_relabel_only(path) or moved_shadow(path) or path == "identity/admin_audit.jsonl"
                or any(path.startswith(f"shared/dies/.history/{n}/") for n in (pred, retired, succ)) or lineage_only(path))
    touched = ([r["to"] for r in rep["renamed"]] + rep["added"] + rep["removed"] + [c["file"] for c in rep["changed"]])
    out_of = sorted(t for t in touched if outside(t) and not explained(t))
    res["explained_outside_the_inventory"] = {"ok": True, "files": sorted(t for t in touched if outside(t) and explained(t))}
    res["nothing_outside_the_manifest"] = {"ok": not out_of, "files": out_of[:60], "count": len(out_of)}
    named_all = {f["file"] for f in json.loads(Path(m["inventory_path"]).read_text())["files"]}
    unread = sorted(set(UNREADABLE.get(str(before), [])) | set(UNREADABLE.get(str(after), [])))
    touched_unread = [u for u in unread if u in named_all or u.startswith(("shared/dies/", "identity/dies/"))]
    res["unreadable_files"] = {"ok": not touched_unread, "skipped_not_in_plan": [u for u in unread if u not in touched_unread],
                               "in_plan": touched_unread}
    res["ok"] = all(v["ok"] for v in res.values() if isinstance(v, dict))
    return res


def _acct(email: str) -> str:
    import hashlib
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def visibility(store: Path) -> Dict[str, Any]:
    """Per ACCOUNT (key = sha256 of the e-mail; role kept), in that account's own workspace context, with the application's own
    guards: every catalog entry of the three dies (``motor_access.may_see_config`` AND ``family._require_die_access``, the
    route's 404 guard) and every project snapshot of a CILN28-family machine (``_catalog_origin`` + ``_snapshot_catalog_visible``,
    as ``list_snapshots`` does).  Raw rows are kept."""
    _point_at(store)
    from motor_ai_sim import motor_access
    from motor_ai_sim import workspace as WS
    from motor_ai_sim.development import routes as DR
    from motor_ai_sim.development import store as PS
    F = _fam()
    users_doc = json.loads((store / "identity" / "users.json").read_text(encoding="utf-8"))
    table = users_doc.get("users") if isinstance(users_doc.get("users"), dict) else users_doc
    acc_of = lambda auth: motor_access.access_for_email(str(auth or ""))     # noqa: E731 - auth header = the e-mail here
    motor_access.catalog_access = acc_of
    F.catalog_access = acc_of

    class Req:
        def __init__(self, email):
            self.headers = {"authorization": email}
    dies = store / "shared" / "dies"
    names = [n for n in ("CILN28", "CILN28 sheet 1", "CILN28 vadim 2") if (dies / n / "die.yaml").is_file()]
    entries = [(d, f.stem) for d in names for f in sorted((dies / d).glob("*.yaml")) if f.name != "die.yaml"]
    projects = [p for p in PS.list_projects() if PS.valid_id(str(p.get("id") or ""))]
    members: Dict[str, set] = {}
    for p in projects:
        for mem in p.get("members") or []:
            members.setdefault(str(mem.get("email") or "").lower(), set()).add(str(p.get("id")))
    catalog_rows, snap_rows = [], []
    for email in sorted(set(table) | set(members)):
        email = email.lower()
        user = dict(table.get(email) or {}, email=email)
        key, role = _acct(email), str(user.get("role") or "")
        # the account's workspace, built without ensure_layout (which would create and seed folders in the store)
        ws = WS.Workspace(id=WS.workspace_id(email), email=email, root=Path(str(WS.workspaces_root())) / WS.workspace_id(email),
                          shared_root=WS.shared_root())
        with WS.use_workspace(ws):
            acc = motor_access.access_for_email(email)
            for d, c in entries:
                ok = False
                try:
                    if motor_access.may_see_die(acc, d) and motor_access.may_see_config(acc, d, c):
                        F._require_die_access(d, email, c)
                        ok = True
                except Exception:                           # noqa: BLE001 - the route's 404: not readable
                    ok = False
                catalog_rows.append({"account": key, "role": role, "entry": f"{d}/{c}", "readable": ok})
            for p in projects:
                if str(p.get("id")) not in members.get(email, set()):
                    continue
                for item in PS.list_snapshots(str(p.get("id"))):
                    machine = item.get("machine") if isinstance(item.get("machine"), dict) else {}
                    if not str(machine.get("die_name") or "").startswith("CILN28"):
                        continue
                    try:
                        origin = DR._catalog_origin(machine, item.get("access_scope"),
                                                    allow_unresolved_claim=role.lower() == "admin")
                        ok = DR._snapshot_catalog_visible(Req(email), user, origin) is True
                    except Exception:                       # noqa: BLE001
                        ok = False
                    snap_rows.append({"account": key, "role": role, "project": p.get("id"), "snapshot": item.get("id"),
                                      "machine": f"{machine.get('die_name')}/{machine.get('config_name')}", "visible": ok})
    return {"catalog_rows": catalog_rows, "snapshot_rows": snap_rows}


def visibility_diff(before: Dict[str, Any], after: Dict[str, Any], m: Dict[str, Any]) -> Dict[str, Any]:
    """Rule (coordinator 2026-11-24): every account reads after the switch exactly what it read before (the predecessor's
    entries under the retired name, the successor's under the predecessor's name, the aside copy under its new name) PLUS,
    for each continuing configuration it read before, the new copy; it MUST read both; nothing else is gained.  Every
    snapshot visible before is visible after and none becomes visible."""
    pred, succ, retired, date = m["pred"], m["succ"], m["retired"], m.get("date", "")
    cont = {c["config"] for c in m["continuing"]}
    aside = {c["config"]: c["successor_own"]["moved_aside_as"].format(date=date) for c in m["continuing"] if c.get("successor_own")}

    def mapped(entry: str) -> str:
        die, cfg = entry.split("/", 1)
        return f"{retired}/{cfg}" if die == pred else f"{pred}/{aside.get(cfg, cfg)}" if die == succ else entry
    def readable(rows):
        out: Dict[str, set] = {}
        for r in rows:
            if r["readable"]:
                out.setdefault(r["account"], set()).add(r["entry"])
        return out
    rb, ra = readable(before["catalog_rows"]), readable(after["catalog_rows"])
    lost, gained, missing_copy = [], [], []
    for acct in sorted(set(rb) | set(ra)):
        b = rb.get(acct, set())
        want = {mapped(e) for e in b}
        copies = {f"{pred}/{e.split('/', 1)[1]}" for e in b if e.split("/", 1)[0] == pred and e.split("/", 1)[1] in cont}
        a = ra.get(acct, set())
        lost += [f"{acct} {e}" for e in sorted(want - a)]
        missing_copy += [f"{acct} {e}" for e in sorted(copies - a)]
        gained += [f"{acct} {e}" for e in sorted(a - want - copies)]
    vis = lambda rows: {(r["account"], r["project"], r["snapshot"]) for r in rows if r["visible"]}     # noqa: E731
    sb, sa = vis(before["snapshot_rows"]), vis(after["snapshot_rows"])
    per_account: Dict[str, Dict[str, int]] = {}
    for r in after["catalog_rows"]:
        e = per_account.setdefault(r["account"], {"role": r["role"], "copies_read": 0, "retired_read": 0, "snapshots": 0})
        if r["readable"] and r["entry"].split("/", 1)[0] == pred and r["entry"].split("/", 1)[1] in cont:
            e["copies_read"] += 1
        if r["readable"] and r["entry"].startswith(retired + "/"):
            e["retired_read"] += 1
    for r in after["snapshot_rows"]:
        if r["visible"]:
            per_account.setdefault(r["account"], {"role": r["role"], "copies_read": 0, "retired_read": 0, "snapshots": 0})["snapshots"] += 1
    per_account = {k: v for k, v in per_account.items() if v["copies_read"] or v["retired_read"] or v["snapshots"]}
    ok = not (lost or gained or missing_copy or (sb - sa) or (sa - sb))
    return {"ok": ok, "catalog_lost": lost, "catalog_gained": gained, "copy_not_readable": missing_copy,
            "snapshots_lost": sorted(map(list, sb - sa)), "snapshots_gained": sorted(map(list, sa - sb)),
            "snapshots_visible_before": len(sb), "snapshots_visible_after": len(sa),
            "accounts_checked": len({r["account"] for r in after["catalog_rows"]}), "readers_after": per_account}


def surfaces(store: Path, pred: str, retired: str) -> Dict[str, Any]:
    """What else names the die: digest drafts (status, recipient count) and the US mirror's view of the dies."""
    out: Dict[str, Any] = {"digest_drafts": []}
    for f in sorted((store / "shared" / "digests" / "drafts").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(True for _ in _names_in(d, (pred, retired))) or f"{pred} /" in json.dumps(d, ensure_ascii=False):
            rec = d.get("recipients") or []
            out["digest_drafts"].append({"draft": f.name, "status": d.get("status"), "kind": d.get("kind"),
                                         "recipients": len(rec) if isinstance(rec, list) else None,
                                         "recipient_status": sorted({str(r.get("status")) for r in rec if isinstance(r, dict)})})
    _point_at(store)
    try:
        from motor_ai_sim.regions import catalog_manifest as CM2
        man = CM2.build(store / "shared")
        names = {k.split("/")[1] for k in man.get("files", {}) if k.startswith("dies/") and k.count("/") >= 2}
        out["mirror"] = {"mirrored_dies": sorted(n for n in names if n.startswith(pred)),
                         "excluded": [e for e in man.get("excluded_dies", []) if str(e.get("die", "")).startswith(pred)]}
    except Exception as exc:                                # noqa: BLE001
        out["mirror"] = {"error": repr(exc)}
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--store", required=True, type=Path)
    inv.add_argument("--die", action="append", required=True)
    inv.add_argument("--out", required=True, type=Path)
    mf = sub.add_parser("manifest")
    mf.add_argument("--store", required=True, type=Path)
    mf.add_argument("--inventory", required=True, type=Path)
    mf.add_argument("--pred", required=True)
    mf.add_argument("--succ", required=True)
    mf.add_argument("--retired", required=True)
    mf.add_argument("--continuing", action="append", required=True)
    mf.add_argument("--stays", action="append", default=[])
    mf.add_argument("--pocket", type=float, default=None)
    mf.add_argument("--keep-nested", action="append", default=None,
                    help="a nested key normally dropped that is copied after all, e.g. bearings.thermal_path (owner's choice)")
    mf.add_argument("--out", required=True, type=Path)
    for name in ("dry-run", "apply"):
        sp = sub.add_parser(name)
        sp.add_argument("--store", required=True, type=Path)
        sp.add_argument("--manifest", required=True, type=Path)
        sp.add_argument("--date", required=True)
        sp.add_argument("--actor", default="sheet switch (offline)")
        sp.add_argument("--pocket", type=float, default=None)
        sp.add_argument("--inventory", required=True, type=Path)
        sp.add_argument("--chmod-readonly", action="store_true",
                        help="read-only files the switch must rewrite and the running uid owns: chmod u+w, restore the mode")
        sp.add_argument("--out", required=True, type=Path)
    ck = sub.add_parser("check")
    ck.add_argument("--before", required=True, type=Path)
    ck.add_argument("--after", required=True, type=Path)
    ck.add_argument("--manifest", required=True, type=Path)
    ck.add_argument("--inventory", required=True, type=Path)
    ck.add_argument("--date", required=True)
    ck.add_argument("--out", required=True, type=Path)
    su = sub.add_parser("surfaces")
    su.add_argument("--store", required=True, type=Path)
    su.add_argument("--pred", required=True)
    su.add_argument("--retired", required=True)
    su.add_argument("--out", required=True, type=Path)
    vs = sub.add_parser("visibility")
    vs.add_argument("--store", required=True, type=Path)
    vs.add_argument("--out", required=True, type=Path)
    vd = sub.add_parser("visibility-diff")
    vd.add_argument("--before", required=True, type=Path)
    vd.add_argument("--after", required=True, type=Path)
    vd.add_argument("--manifest", required=True, type=Path)
    vd.add_argument("--date", required=True)
    vd.add_argument("--out", required=True, type=Path)
    for sp in sub.choices.values():
        sp.add_argument("--roots-are-mounts", action="store_true",
                        help="the four roots shared / identity / workspaces / published are bind mounts under --store")
    a = ap.parse_args(argv)
    global ROOTS_ARE_MOUNTS
    ROOTS_ARE_MOUNTS = bool(getattr(a, "roots_are_mounts", False))
    if a.cmd == "surfaces":
        r = surfaces(a.store, a.pred, a.retired)
        a.out.write_text(json.dumps(r, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        print(json.dumps(r, ensure_ascii=False, default=str)[:3000])
        return 0
    if a.cmd == "visibility-diff":
        m = json.loads(a.manifest.read_text(encoding="utf-8"))
        m["date"] = a.date
        r = visibility_diff(json.loads(a.before.read_text()), json.loads(a.after.read_text()), m)
        a.out.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: (v if not isinstance(v, list) else v[:20]) for k, v in r.items()}, indent=1, ensure_ascii=False))
        return 0 if r["ok"] else 1
    if a.cmd == "visibility":
        r = visibility(a.store)
        a.out.write_text(json.dumps(r, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        print(len(r["catalog_rows"]), "catalog rows,", sum(1 for x in r["catalog_rows"] if x["readable"]), "readable;",
              len(r["snapshot_rows"]), "snapshot rows,", sum(1 for x in r["snapshot_rows"] if x["visible"]), "visible")
        return 0
    if a.cmd == "check":
        m = json.loads(a.manifest.read_text(encoding="utf-8"))
        m["inventory_path"] = str(a.inventory)
        m["date"] = a.date
        rep = report(a.before, a.after)
        res = check(a.before, a.after, m, rep)
        a.out.write_text(json.dumps({"report": rep, "check": res}, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(res, indent=1, ensure_ascii=False)[:6000])
        print(json.dumps({k: len(v) for k, v in rep.items()}))
        return 0 if res["ok"] else 1
    if a.cmd in ("dry-run", "apply"):
        m = json.loads(a.manifest.read_text(encoding="utf-8"))
        m["date"] = a.date
        if a.pocket is not None:
            m["pocket_clearance_slot"]["value"] = a.pocket
        m["inventory_path"] = str(a.inventory)
        m["chmod_readonly"] = bool(a.chmod_readonly)
        try:
            r = switch(a.store, m, dry_run=a.cmd == "dry-run", actor=a.actor)
        except BaseException as exc:                        # noqa: BLE001 - after the preflight: something was written
            import traceback
            traceback.print_exc()
            if a.cmd == "apply":
                a.out.write_text(json.dumps({"partial": True, "error": repr(exc)}, indent=1), encoding="utf-8")
                print("PARTIAL: the switch stopped after it had started writing - restore the backup")
                return 3
            raise
        a.out.write_text(json.dumps(r, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        print(json.dumps(r, ensure_ascii=False, default=str)[:4000])
        if r.get("refused"):
            print("REFUSED: nothing was written")
            return 1
        print("DRY RUN: nothing was written" if a.cmd == "dry-run" else "APPLIED")
        return 0
    if a.cmd == "manifest":
        for k in (a.keep_nested or []):
            NEVER_NESTED.pop(tuple(k.split(".", 1)), None)
        m = build_manifest(a.store, json.loads(a.inventory.read_text()), a.pred, a.succ, a.retired, a.continuing, a.stays, a.pocket)
        a.out.write_text(json.dumps(m, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        a.out.with_suffix(".md").write_text(manifest_text(m), encoding="utf-8")
        print(manifest_text(m)[:6000])
        return 0
    if a.cmd == "inventory":
        out = inventory(a.store, tuple(a.die))
        a.out.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"files": len(out["files"]), "unparsed": len(out["unparsed"]), "stores": out["stores"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
