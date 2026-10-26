"""Safe RENAME of a die: every store keyed by the die name moves with it.

``PATCH /api/family/die/{die}`` used to move the folder and the configurations'
back-reference and nothing else, so a rename orphaned everything that is keyed by
the NAME: the stored duty results and their history, the motor grants of the
accounts, the per-die access list, the development projects that link the motor,
the production records, the CAD models, the catalog cards ...  (owner decision
2026-10-26: names are now editable, so a rename must never lose data.)

This module is the one place that knows the list.  A rename is a TRANSACTION:

* every step is a small function ``step(ctx) -> [change, ...]``.  With
  ``ctx.dry_run`` it only reports what it would change (``?dry_run=1``); applied, it
  mutates AND registers an undo closure;
* the steps run in a fixed order; when one raises, the undo closures of the steps
  that already ran are called in reverse order and the error is re-raised, so a
  half-renamed die is never left behind;
* a finished rename writes one admin-audit entry (``die.rename``) and a log line.

SCOPE.  Stores of the caller's own workspace (results, panel memory, active
context, "My motors") follow every rename.  Stores of the SHARED catalog (account
grants, the die access list, projects, production, CAD) follow only when the die
that is renamed IS the shared catalog's die - a user renaming a private copy in
his workspace must not retarget what the shared catalog's name means to everybody
else.  Deploy-managed files that are read-only for the application (the repository's
``cooling_options.yaml``, the passport / propeller-S1 folders) cannot be rewritten
here; when they name the old die the response lists them under ``manual`` with the
exact path, so the operator can move them.

Every change descriptor is ``{"store": <short name>, "where": <path or key>,
"change": <what>}``.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


class RenameBlocked(Exception):
    """The rename must not start (HTTP 409 in the route): the message says why."""


@dataclass
class Ctx:
    fam: Any                       # routes.family (paths, yaml helpers, context file)
    old: str
    new: str
    src_dir: Path
    dst_dir: Path
    dry_run: bool
    glob_scope: bool               # does the shared catalog's name change too?
    actor: str = ""
    undo: List[Callable[[], None]] = field(default_factory=list)
    after: List[Callable[[], None]] = field(default_factory=list)    # append-only audit lines: only once all steps passed
    changes: List[Dict[str, Any]] = field(default_factory=list)
    manual: List[Dict[str, Any]] = field(default_factory=list)
    configs: List[str] = field(default_factory=list)

    @property
    def cur(self) -> Path:
        """Where the die's folder is NOW: the source while only planning, the target after the move."""
        return self.src_dir if self.dry_run else self.dst_dir

    def add(self, store: str, where: Any, change: str, **extra: Any) -> None:
        self.changes.append({"store": store, "where": str(where), "change": change, **extra})


# ── tiny helpers ─────────────────────────────────────────────────────────────

def walk_replace(obj: Any, old: str, new: str, keys: tuple = ("die",)) -> int:
    """Replace, in place, every ``{key: old}`` (key in ``keys``) found anywhere in ``obj``.
    Returns how many values changed."""
    n = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in keys and isinstance(v, str) and v == old:
                obj[k] = new
                n += 1
            elif isinstance(v, (dict, list)):
                n += walk_replace(v, old, new, keys)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                n += walk_replace(v, old, new, keys)
    return n


def _read_bytes(p: Path) -> Optional[bytes]:
    try:
        return p.read_bytes()
    except OSError:
        return None


def _restore_bytes(p: Path, data: Optional[bytes]) -> None:
    if data is None:
        try:
            p.unlink()
        except OSError:
            pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _json_file_rename(ctx: Ctx, store: str, path: Path, mutate: Callable[[Any], int],
                      what: str, indent: Optional[int] = 2) -> bool:
    """Read ``path`` (JSON), let ``mutate`` rewrite the document in place (returns how many
    places it changed), write it back atomically, register the undo.  A missing / corrupt /
    unchanged file is skipped.  Returns True when something changed (or would)."""
    raw = _read_bytes(path)
    if raw is None:
        return False
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    n = mutate(doc)
    if not n:
        return False
    ctx.add(store, path, f"{what} ({n})")
    if ctx.dry_run:
        return True
    from motor_ai_sim import json_store
    json_store.atomic_write_json(path, doc, indent=indent)
    ctx.undo.append(lambda p=path, b=raw: _restore_bytes(p, b))
    return True


def _rename_key(d: Any, old: str, new: str) -> bool:
    if isinstance(d, dict) and old in d:
        if new in d:
            raise RenameBlocked(f"'{new}' already has entries in this store")
        d[new] = d.pop(old)
        return True
    return False


# ── step 1: the folder, die.yaml, configurations ────────────────────────────

def step_folder(ctx: Ctx) -> None:
    fam = ctx.fam
    src, dst = ctx.src_dir, ctx.dst_dir
    ctx.add("catalog", src, f"folder moves to '{dst.name}'")
    ctx.configs = sorted(p.stem for p in src.glob("*.yaml") if p.name != "die.yaml")
    ctx.add("catalog", src / "die.yaml", f"name -> '{ctx.new}'")
    for c in ctx.configs:
        ctx.add("catalog", src / f"{c}.yaml", f"die back-reference -> '{ctx.new}'")
    hist_src = src.parent / ".history" / ctx.old          # the catalog history sits beside the dies
    hist_dst = src.parent / ".history" / ctx.new
    if hist_src.is_dir():
        if hist_dst.exists():
            raise RenameBlocked(f"catalog history folder '.history/{ctx.new}' already exists")
        ctx.add("catalog_history", hist_src, "history folder moves with the die")
    if ctx.dry_run:
        return
    originals = {p.name: p.read_bytes() for p in src.glob("*.yaml")}
    src.rename(dst)
    ctx.undo.append(lambda: (dst.rename(src),
                             [_restore_bytes(src / n, b) for n, b in originals.items()]))
    had_history = hist_src.is_dir()
    kept = {f.name for f in hist_src.iterdir()} if had_history else set()
    if had_history:
        hist_src.rename(hist_dst)

    def _undo_history() -> None:
        # the saves below file snapshots of the pre-rename versions under the new name;
        # a rollback removes what the rename itself added
        if hist_dst.is_dir():
            for f in hist_dst.iterdir():
                if f.name not in kept:
                    f.unlink()
            if had_history:
                hist_dst.rename(hist_src)
            elif not any(hist_dst.iterdir()):
                hist_dst.rmdir()
    ctx.undo.append(_undo_history)
    d = fam._load_yaml(dst / "die.yaml", "die")
    d["name"] = ctx.new
    walk_replace(d, ctx.old, ctx.new)
    fam._save_yaml(dst / "die.yaml", d)
    for c in ctx.configs:
        doc = fam._load_yaml(dst / f"{c}.yaml", "configuration")
        doc["die"] = ctx.new
        walk_replace(doc, ctx.old, ctx.new)
        fam._save_yaml(dst / f"{c}.yaml", doc)


# ── step 2: other dies that were derived from this one ──────────────────────

def step_derived_from(ctx: Ctx) -> None:
    fam = ctx.fam
    for p in sorted(ctx.src_dir.parent.glob("*/die.yaml")):
        if p.parent.name in (ctx.old, ctx.new):
            continue
        try:
            doc = fam._load_yaml(p, "die")
        except Exception:                                   # noqa: BLE001 - a broken sibling is not ours
            continue
        derived = doc.get("derived_from")
        if not (isinstance(derived, dict) and derived.get("die") == ctx.old):
            continue
        ctx.add("catalog", p, f"derived_from.die -> '{ctx.new}'")
        if ctx.dry_run:
            continue
        raw = p.read_bytes()
        hist = p.parent.parent / ".history" / p.parent.name
        kept = {f.name for f in hist.iterdir()} if hist.is_dir() else set()
        derived["die"] = ctx.new
        fam._save_yaml(p, doc)

        def _undo(p=p, b=raw, hist=hist, kept=kept) -> None:
            _restore_bytes(p, b)
            if hist.is_dir():                       # the snapshot that save filed
                for f in hist.iterdir():
                    if f.name not in kept:
                        f.unlink()
        ctx.undo.append(_undo)


# ── step 3: stored field files (npz meta carries the die name) ──────────────

def step_fields(ctx: Ctx) -> None:
    from motor_ai_sim import duty_fields as DF
    root = ctx.cur / "runs"
    if not root.is_dir():
        return
    files = [p for p in sorted(root.rglob("*.npz")) if p.parent.name == "fields"]
    todo = [p for p in files if DF.meta_die(p) == ctx.old]
    for p in todo:
        ctx.add("duty_fields", p, f"meta die -> '{ctx.new}'")
    if ctx.dry_run:
        return
    for p in todo:
        if DF.retag_meta(p, die=ctx.new):
            ctx.undo.append(lambda p=p: DF.retag_meta(p, die=ctx.old))


# ── step 4: duty results (+ per-kind history) ────────────────────────────────

def step_duty_results(ctx: Ctx) -> None:
    from motor_ai_sim import duty_results as DR
    # the legacy per-die ledger travels in the folder; its keys name the die too
    legacy = ctx.cur / ".duty_results.json"
    if legacy.is_file():
        def _m(doc):
            n = 0
            for block in ("results", "history"):
                n += int(_rename_key(doc.get(block), ctx.old, ctx.new))
            return n
        _json_file_rename(ctx, "duty_results", legacy, _m, "results/history key renamed", indent=None)
    # THE SHARED CATALOG'S LEDGER is chosen by what is renamed, not by the request identity: a call without the
    # verified-admin context (a worker that lost the headers, an in-process call) must not silently miss it.
    from motor_ai_sim import workspace as W
    forced = DR.shared_store_path() if (ctx.glob_scope and W.layering()) else None
    with DR.using_store(forced):
        try:
            store = DR.store_path()
        except Exception:                                   # noqa: BLE001
            return
        if store.resolve() == legacy.resolve():
            return
        rows = DR.die_rows(ctx.old)
        found = bool(rows["results"] or rows["history"])
        # always reported - a missing or empty store is visible in the dry run, not silently skipped
        ctx.add("duty_results", store,
                "results + per-kind history re-keyed" if found else
                ("no rows for this die in this store" if rows["exists"] else "store file does not exist"),
                exists=rows["exists"], rows={"results": rows["results"], "history": rows["history"]})
        if ctx.dry_run or not found:
            return
        if DR.rename_die(ctx.old, ctx.new):
            def _undo(forced=forced) -> None:
                with DR.using_store(forced):
                    DR.rename_die(ctx.new, ctx.old)
            ctx.undo.append(_undo)


# ── step 5: the active family context ────────────────────────────────────────

def step_context(ctx: Ctx) -> None:
    p = ctx.fam._ctx_file()
    def _m(doc):
        n = 0
        if isinstance(doc, dict):
            for k in ("die", "released_from"):
                if doc.get(k) == ctx.old:
                    doc[k] = ctx.new
                    n += 1
            tf = doc.get("transitioned_from")
            if isinstance(tf, dict) and tf.get("die") == ctx.old:
                tf["die"] = ctx.new
                n += 1
        return n
    _json_file_rename(ctx, "active_context", p, _m, "active die")


# ── step 6: the caller's workspace memories ──────────────────────────────────

def step_workspace_memory(ctx: Ctx) -> None:
    from motor_ai_sim.workspace import root as ws_root
    base = Path(str(ws_root()))
    # remembered Thermal fields per motor: "<die>|<config>" keys
    def _panel(doc):
        n = 0
        thermal = doc.get("thermal") if isinstance(doc, dict) else None
        for entry in (thermal.values() if isinstance(thermal, dict) else []):
            if not isinstance(entry, dict):
                continue
            by = entry.get("by_motor")
            if isinstance(by, dict):
                for key in list(by):
                    die, sep, cfg = str(key).partition("|")
                    if sep and die == ctx.old:
                        by[f"{ctx.new}|{cfg}"] = by.pop(key)
                        n += 1
            mk = entry.get("motor")
            if isinstance(mk, str) and "|" in mk and mk.partition("|")[0] == ctx.old:
                entry["motor"] = f"{ctx.new}|{mk.partition('|')[2]}"
                n += 1
        return n
    _json_file_rename(ctx, "panel_settings", base / ".panel_settings.json", _panel,
                      "per-motor Thermal memory re-keyed")
    # "My motors": the provenance link of a private copy
    def _mine(doc):
        n = 0
        for entries in (doc.values() if isinstance(doc, dict) else []):
            for e in (entries.values() if isinstance(entries, dict) else []):
                src = e.get("src") if isinstance(e, dict) else None
                if isinstance(src, dict) and src.get("die") == ctx.old:
                    src["die"] = ctx.new
                    n += 1
        return n
    _json_file_rename(ctx, "my_motors", base / "user_motors.json", _mine,
                      "source die of a private copy")


# ── step 7: catalog cards + presets named after the die ──────────────────────

def _card_name(name: str, old: str, new: str, configs: List[str]) -> Optional[str]:
    low = name.casefold()
    if low == old.casefold():
        return new
    for c in configs:
        if low == f"{old} {c}".casefold():
            return f"{new} {c}"
    return None


def step_catalog_cards(ctx: Ctx) -> None:
    from motor_ai_sim.routes import catalog as CAT
    from motor_ai_sim.routes import presets as PRE
    def _cards(doc):
        n = 0
        for m in (doc.get("motors") if isinstance(doc, dict) else []) or []:
            if isinstance(m, dict) and isinstance(m.get("name"), str):
                nn = _card_name(m["name"], ctx.old, ctx.new, ctx.configs)
                if nn:
                    m["name"] = nn
                    n += 1
        return n
    def _presets(doc):
        n = 0
        for m in (doc.values() if isinstance(doc, dict) else []):
            if isinstance(m, dict) and isinstance(m.get("name"), str):
                nn = _card_name(m["name"], ctx.old, ctx.new, ctx.configs)
                if nn:
                    m["name"] = nn
                    n += 1
        return n
    _json_file_rename(ctx, "catalog_cards", CAT._catalog_path(), _cards, "card name")
    _json_file_rename(ctx, "presets", PRE._presets_path(), _presets, "preset name")


# ── shared-catalog steps (glob_scope only) ───────────────────────────────────

def step_grants(ctx: Ctx) -> None:
    from motor_ai_sim import users
    hits, undo = users.rename_die_in_grants(ctx.old, ctx.new, dry_run=ctx.dry_run)
    for email, what in hits:
        ctx.add("user_grants", email, what)
    if undo is not None:
        ctx.undo.append(undo)


def step_die_access(ctx: Ctx) -> None:
    from motor_ai_sim import motor_access
    hit, undo = motor_access.rename_die_access(ctx.old, ctx.new, dry_run=ctx.dry_run)
    if hit:
        ctx.add("die_access", motor_access.die_access_file(), "visibility / client list re-keyed")
    if undo is not None:
        ctx.undo.append(undo)


def step_projects(ctx: Ctx) -> None:
    from motor_ai_sim import users
    from motor_ai_sim.development import store as PS
    low = ctx.old.casefold()

    def _key(die: str, cfg: str) -> str:
        return f"{die.casefold()}\0{cfg.casefold()}"

    def _rewrite(project: dict) -> int:
        n = 0
        for row in project.get("linked_motors") or []:
            if isinstance(row, dict) and str(row.get("die") or "").casefold() == low:
                row["die"] = ctx.new
                n += 1
        for row in project.get("motor_history") or []:
            if isinstance(row, dict) and str(row.get("die") or "").casefold() == low:
                row["die"] = ctx.new
                n += 1
        for row in project.get("grant_removal_candidates") or []:
            if isinstance(row, dict) and str(row.get("die") or "").casefold() == low:
                row["die"] = ctx.new
                n += 1
        ledger = project.get("project_motor_grants")
        if isinstance(ledger, dict):
            for key in list(ledger):
                entry = ledger[key]
                die, sep, cfg = str(key).partition("\0")
                if sep and die.casefold() == low:
                    ledger[_key(ctx.new, cfg)] = ledger.pop(key)
                    n += 1
                    entry = ledger[_key(ctx.new, cfg)]
                if isinstance(entry, dict):
                    gk = str(entry.get("grant_key") or "")
                    gdie, gsep, gcfg = gk.rpartition("/")
                    if gsep and gdie == ctx.old:
                        entry["grant_key"] = f"{ctx.new}/{gcfg}"
                        n += 1
        return n

    def _rewrite_snapshot(snap: Any) -> int:
        n = 0
        machine = snap.get("machine") if isinstance(snap, dict) else None
        if isinstance(machine, dict) and str(machine.get("die_name") or "").casefold() == low:
            machine["die_name"] = ctx.new
            n += 1
        scope = snap.get("access_scope") if isinstance(snap, dict) else None
        if (isinstance(scope, dict) and scope.get("kind") == "catalog"
                and str(scope.get("die") or "").casefold() == low):
            scope["die"] = ctx.new
            scope["grant_key"] = f"{ctx.new}/{scope.get('config') or ''}"
            n += 1
        return n

    try:
        projects = PS.list_projects()
        index_exists = PS.index_path().is_file()
    except Exception:                                       # noqa: BLE001 - no store: nothing to do
        return
    if not projects and not index_exists:
        return
    index_path = PS.index_path()

    def _edit(doc: dict) -> None:
        _rewrite(doc)

    with (_null_cm() if ctx.dry_run else PS.project_transaction()), users._LOCK:
        for project in projects:
            pid = str(project.get("id") or "")
            if not PS.valid_id(pid):
                continue
            # the immutable motor snapshots keep their content; only the NAME that
            # opens them (machine.die_name, access_scope) follows the rename, or the
            # grant check of a snapshot would look for the old name
            snaps = []
            snap_dir = PS.path_for(pid) / "snapshots"
            for sp in (sorted(snap_dir.glob("*.json")) if snap_dir.is_dir() else []):
                raw = _read_bytes(sp)
                try:
                    doc = json.loads((raw or b"{}").decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                k = _rewrite_snapshot(doc)
                if k:
                    snaps.append((sp, raw, doc))
                    ctx.add("project_snapshots", sp, f"machine name / access scope ({k})")
            n = _rewrite(copy.deepcopy(project))
            if n:
                ctx.add("projects", f"{pid}/project.json",
                        f"linked motor / history / grant ledger rows ({n})")
            if ctx.dry_run or not (n or snaps):
                continue
            from motor_ai_sim import json_store
            for sp, raw, doc in snaps:
                json_store.atomic_write_json(sp, doc)
                ctx.undo.append(lambda sp=sp, raw=raw: _restore_bytes(sp, raw))
            if n:
                before = copy.deepcopy(project)
                PS.mutate_project(pid, _edit)
                ctx.undo.append(lambda pid=pid, before=before: PS.save_json(pid, "project.json", before))
                ctx.after.append(lambda pid=pid: PS.append_audit(
                    pid, ctx.actor or "system", "motor.rename", "motor", ctx.old, {"to": ctx.new}))
        # the motor -> project index (key = casefolded die + config)
        idx_raw = _read_bytes(index_path)
        idx = PS.load_index()
        motors = idx.get("motors") if isinstance(idx, dict) else None
        moved = 0
        if isinstance(motors, dict):
            for key in list(motors):
                row = motors[key]
                die, sep, cfg = str(key).partition("\0")
                if sep and (die == low or (isinstance(row, dict)
                                            and str(row.get("die") or "").casefold() == low)):
                    if isinstance(row, dict):
                        row["die"] = ctx.new
                    motors[_key(ctx.new, cfg)] = motors.pop(key)
                    moved += 1
        if moved:
            ctx.add("project_index", index_path, f"motor index rows re-keyed ({moved})")
            if not ctx.dry_run:
                PS.save_index(idx)
                ctx.undo.append(lambda: _restore_bytes(index_path, idx_raw))


class _null_cm:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def step_production(ctx: Ctx) -> None:
    from motor_ai_sim.production import store as PR
    try:
        root = PR.production_root()
    except Exception:                                       # noqa: BLE001
        return
    old_dir, new_dir = root / PR._safe_slug(ctx.old), root / PR._safe_slug(ctx.new)
    if not old_dir.is_dir():
        return
    moving = old_dir != new_dir
    if moving and new_dir.exists() and any(new_dir.iterdir()):
        raise RenameBlocked(f"production folder '{new_dir.name}' already exists")
    cur = old_dir
    jsons = [p for p in sorted(cur.rglob("*.json")) if "files" not in p.relative_to(cur).parts]
    originals = {p.relative_to(cur): _read_bytes(p) for p in jsons}
    productions = sorted(p.name for p in cur.iterdir() if p.is_dir())
    ctx.add("production", old_dir,
            f"folder moves to '{new_dir.name}'" if moving else "records keep their folder"
            + f"; {len(productions)} production(s)")
    for rel, raw in originals.items():
        try:
            doc = json.loads((raw or b"{}").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if walk_replace(doc, ctx.old, ctx.new):
            ctx.add("production", old_dir / rel, "die field")
    if ctx.dry_run:
        return
    if moving:
        if new_dir.exists():
            new_dir.rmdir()
        old_dir.rename(new_dir)
        ctx.undo.append(lambda: new_dir.rename(old_dir))
    from motor_ai_sim import json_store
    for rel, raw in originals.items():
        p = new_dir / rel
        try:
            doc = json.loads((raw or b"{}").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if walk_replace(doc, ctx.old, ctx.new):
            json_store.atomic_write_json(p, doc)
            ctx.undo.append(lambda p=p, b=raw: _restore_bytes(p, b))
    for cfg_slug in productions:
        ctx.after.append(lambda c=cfg_slug: PR.append_audit(ctx.new, c, {
            "action": "die.rename", "from": ctx.old, "to": ctx.new,
            "actor": ctx.actor or "system",
            "time": __import__("datetime").datetime.now().isoformat(timespec="seconds")}))


def step_motor_tests(ctx: Ctx) -> None:
    """The Tests store (``<shared>/motor_tests/<die>/<config>/tests.json``): same folder naming as production."""
    from motor_ai_sim.motor_tests import store as MT
    try:
        root = MT.root()
    except Exception:                                       # noqa: BLE001
        return
    from motor_ai_sim.production.store import _safe_slug
    old_dir, new_dir = root / _safe_slug(ctx.old), root / _safe_slug(ctx.new)
    if not old_dir.is_dir():
        return
    moving = old_dir != new_dir
    if moving and new_dir.exists() and any(new_dir.iterdir()):
        raise RenameBlocked(f"motor tests folder '{new_dir.name}' already exists")
    originals = {p.relative_to(old_dir): _read_bytes(p) for p in sorted(old_dir.rglob("*.json"))}
    configs = sorted(p.name for p in old_dir.iterdir() if p.is_dir())
    ctx.add("motor_tests", old_dir, (f"folder moves to '{new_dir.name}'" if moving else "records keep their folder")
            + f"; {len(configs)} configuration(s)")
    for rel, raw in originals.items():
        try:
            doc = json.loads((raw or b"{}").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if walk_replace(doc, ctx.old, ctx.new):
            ctx.add("motor_tests", old_dir / rel, "die field")
    if ctx.dry_run:
        return
    if moving:
        if new_dir.exists():
            new_dir.rmdir()
        old_dir.rename(new_dir)
        ctx.undo.append(lambda: new_dir.rename(old_dir))
    from motor_ai_sim import json_store
    for rel, raw in originals.items():
        p = new_dir / rel
        try:
            doc = json.loads((raw or b"{}").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if walk_replace(doc, ctx.old, ctx.new):
            json_store.atomic_write_json(p, doc)
            ctx.undo.append(lambda p=p, b=raw: _restore_bytes(p, b))
    for cfg_slug in configs:
        ctx.after.append(lambda c=cfg_slug: MT.append_audit(ctx.new, c, {
            "action": "die.rename", "from": ctx.old, "to": ctx.new, "who": ctx.actor or "system",
            "time": __import__("datetime").datetime.now().isoformat(timespec="seconds")}))


def step_cad(ctx: Ctx) -> None:
    from motor_ai_sim import model3d
    from motor_ai_sim.cad import rebuild_queue as RQ
    try:
        root = model3d.cad_root()
    except Exception:                                       # noqa: BLE001
        return
    old_dir, new_dir = root / ctx.old, root / ctx.new
    if old_dir.is_dir():
        if new_dir.exists():
            raise RenameBlocked(f"CAD model folder '{ctx.new}' already exists")
        manifests = sorted(old_dir.glob("*/manifest.json"))
        ctx.add("cad_models", old_dir, f"folder moves to '{ctx.new}' ({len(manifests)} model(s))")
        if not ctx.dry_run:
            originals = {m.relative_to(old_dir): _read_bytes(m) for m in manifests}
            old_dir.rename(new_dir)
            ctx.undo.append(lambda: new_dir.rename(old_dir))
            from motor_ai_sim import json_store
            for rel, raw in originals.items():
                try:
                    doc = json.loads((raw or b"{}").decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if walk_replace(doc, ctx.old, ctx.new):
                    p = new_dir / rel
                    json_store.atomic_write_json(p, doc)
                    ctx.undo.append(lambda p=p, b=raw: _restore_bytes(p, b))
    # rebuild requests: file name = hash of (die, config)
    qdir = Path(RQ.queue_dir(root))
    if qdir.is_dir():
        for cfg in ctx.configs or []:
            rec = RQ.read(root, ctx.old, cfg)
            if not rec:
                continue
            ctx.add("cad_rebuild_queue", RQ._path(root, ctx.old, cfg), "request re-keyed")
            if ctx.dry_run:
                continue
            old_p = Path(RQ._path(root, ctx.old, cfg))
            raw = _read_bytes(old_p)
            rec = dict(rec)
            rec["die"] = ctx.new
            RQ._write(root, rec)
            old_p.unlink()
            new_p = Path(RQ._path(root, ctx.new, cfg))
            ctx.undo.append(lambda a=old_p, b=raw, c=new_p: (_restore_bytes(a, b), _restore_bytes(c, None)))


def step_manual(ctx: Ctx) -> None:
    """Deploy-managed, read-only-for-the-app stores that still name the old die."""
    def note(store: str, path: Any, action: str) -> None:
        ctx.manual.append({"store": store, "where": str(path), "change": action})
    try:
        from motor_ai_sim import cooling_options as CO
        import yaml
        cands = []
        if CO._FILE.is_file():
            cands.append(CO._FILE)
        if CO._FILE == CO._REPO_FILE:
            from motor_ai_sim.workspace import shared_root
            sp = Path(str(shared_root())) / "cooling_options.yaml"
            if sp.is_file():
                cands.append(sp)
        for p in cands:
            dies = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("dies")
            if isinstance(dies, dict) and ctx.old in dies:
                note("cooling_options", p, f"rename the key '{ctx.old}' under dies -> '{ctx.new}'")
    except Exception:                                       # noqa: BLE001
        log.debug("die rename: cooling options probe failed", exc_info=True)
    try:
        from motor_ai_sim import passport_store as PP
        d = PP.store_dir() / ctx.old
        if d.is_dir():
            note("passports", d, f"move the folder to '{ctx.new}'")
    except Exception:                                       # noqa: BLE001
        log.debug("die rename: passport probe failed", exc_info=True)
    try:
        from motor_ai_sim import propeller_s1 as S1
        for root in S1.model_dirs():
            d = root / ctx.old
            if d.is_dir():
                note("propeller_s1", d, f"move the folder to '{ctx.new}'")
    except Exception:                                       # noqa: BLE001
        log.debug("die rename: propeller S1 probe failed", exc_info=True)


#: workspace-level steps, then the shared-catalog ones (``glob_scope``)
LOCAL_STEPS = (step_folder, step_derived_from, step_fields, step_duty_results,
               step_context, step_workspace_memory, step_catalog_cards)
GLOBAL_STEPS = (step_grants, step_die_access, step_projects, step_production, step_motor_tests, step_cad,
                step_manual)


def run(fam: Any, old: str, new: str, *, src_dir: Path, dst_dir: Path, dry_run: bool,
        glob_scope: bool, actor: str = "") -> Dict[str, Any]:
    """Rename ``old`` to ``new`` everywhere, or (``dry_run``) list where it would.  All or nothing."""
    from motor_ai_sim import users
    if glob_scope and old == getattr(users, "_BASELINE_DIE", None):
        raise RenameBlocked(
            f"'{old}' is the shared baseline motor every account is granted by default "
            "(users._BASELINE_DIE) - renaming it would remove it from every account; "
            "change the baseline first")
    try:
        from motor_ai_sim import data_publish
        mv = data_publish.active_move(old)
    except Exception:                                       # noqa: BLE001
        mv = None
    if mv:
        raise RenameBlocked(f"'{old}' is being moved between the open and private data sets "
                            f"(move {mv.get('id') or '?'}); finish or cancel the move first")
    ctx = Ctx(fam=fam, old=old, new=new, src_dir=src_dir, dst_dir=dst_dir, dry_run=dry_run,
              glob_scope=glob_scope, actor=actor)
    steps = LOCAL_STEPS + (GLOBAL_STEPS if glob_scope else ())
    done = 0
    try:
        for step in steps:
            step(ctx)
            done += 1
    except BaseException as exc:
        # Roll back everything that was applied, newest first.
        failures = []
        for undo in reversed(ctx.undo):
            try:
                undo()
            except Exception as uexc:                       # noqa: BLE001
                failures.append(repr(uexc))
        log.error("die rename '%s' -> '%s' FAILED in %s and was rolled back (%s)%s",
                  old, new, getattr(steps[done], "__name__", "?") if done < len(steps) else "?",
                  exc, f"; rollback problems: {failures}" if failures else "")
        if failures:
            try:
                exc.rollback_failures = failures        # the route must not claim "nothing was changed"
            except Exception:                           # noqa: BLE001
                pass
        raise
    if not dry_run:
        for line in ctx.after:
            try:
                line()
            except Exception:                               # noqa: BLE001 - an audit line must not undo a finished rename
                log.warning("die rename: an audit line could not be written", exc_info=True)
        try:
            from motor_ai_sim import admin_audit
            admin_audit.record(actor or "system", "die.rename", old, details={
                "to": new, "stores": sorted({c["store"] for c in ctx.changes}),
                "changes": len(ctx.changes), "manual": len(ctx.manual)})
        except Exception:                                   # noqa: BLE001
            log.warning("die rename: audit record failed", exc_info=True)
        log.info("die '%s' renamed to '%s' (%d change(s), %d manual)", old, new,
                 len(ctx.changes), len(ctx.manual))
    return {"ok": True, "die": new if not dry_run else old, "from": old, "to": new,
            "dry_run": dry_run, "scope": "shared catalog" if glob_scope else "workspace",
            "changes": ctx.changes, "manual": ctx.manual}
