"""MOVE or COPY one configuration to another die: every store that names it follows.

2026-10-29/30: the owner's live edits changed the shared snapshot of the Erium die
(``CIANO56 400_80 vadim 3``) under five other configurations, and the ceramic L100
variants had to be copied BY HAND to new dies (vadim 4 / vadim 5): no route moved a
configuration together with its results, its project links and its customers'
access.  ``POST /api/family/config/{die}/{cfg}/move`` (admin) does, as one
transaction - the shape of ``die_rename.py``, keyed by the PAIR (die, configuration)
instead of the die name:

* every step is ``step(ctx)``: with ``ctx.dry_run`` it only lists what it would change;
  applied, it writes (atomically) and registers an undo closure;
* the steps run in a fixed order; when one raises, the undo closures run newest first
  and the error is re-raised: a half-moved configuration is never left behind;
* every file the move rewrites or removes is first copied into a BACKUP folder
  ``<catalog root>/.config_moves/<stamp> <die>~<cfg>/`` (``manifest.json`` lists them);
  the source files of a move are moved there last, not deleted.  The identity registry
  ``users.json`` is private and never copied there: the manifest lists each account's change;
* a finished move writes one admin-audit entry (``config.move`` / ``config.copy``).

MOVE retargets (``"<die>/<cfg>"`` -> ``"<target>/<cfg>"``):

  catalog          the configuration file (``die`` back-reference), ``runs/<cfg>/`` and
                   the ``die`` in its stored field files, its ``.history`` snapshots
  duty_results     ``results`` and ``history`` of the shared ledger, of EVERY workspace
                   ledger, and the legacy per-die ledger inside the die folder
  active_context   ``.family_context.json`` of every workspace (die / released_from /
                   transitioned_from)
  workspace memory ``.panel_settings.json`` per-motor Thermal fields, ``user_motors.json``
                   source links
  catalog cards    ``die`` + name of a card / preset of this configuration
  user_grants      identity ``users.json``: ``motors.configs`` / ``motors.sources`` /
                   ``motors.default``; an account that saw the configuration through the
                   OLD die gets the configuration grant on the new one (access is kept)
  projects         ``linked_motors``, ``motor_history``, ``grant_removal_candidates``,
                   ``project_motor_grants`` (key + ``grant_key``), the motor snapshots'
                   name and access scope, ``projects/index.json``; a ``motor.move`` audit line
  production, motor_tests, cad_models, cad_rebuild_queue: their per-configuration folders

COPY leaves the source alone and adds the copy: the file, its runs, its results and
history rows; projects pick it up through ``development.autolink`` (a new variant of a
project's motor joins the project).  Grants, production, tests and CAD are not copied.

Refused (``MoveBlocked``, HTTP 409): the target already has a configuration of that name
(or rows / runs / folders under it), the dies live in different catalog layers, the
configuration holds a product revision (a move; a copy is allowed), a data move of either
die is in progress, a job is running on it.

Not rewritten, by design: append-only audit logs, the frozen content of project snapshots,
job records, the stored Configure answers (their stamps change, they are recomputed),
``derived_from`` provenance of other dies.  Reported under ``manual`` (deploy-managed, read
only for the application): ``cooling_options.yaml`` entries of the source die, passport and
propeller-S1 folders of the configuration.

Change descriptor: ``{"store", "where", "change"}`` (+ ``rows`` for a ledger).
"""
from __future__ import annotations

import copy as _copy
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from motor_ai_sim.die_rename import _null_cm, _read_bytes, _restore_bytes, walk_replace

log = logging.getLogger(__name__)


class MoveBlocked(Exception):
    """The move must not start (HTTP 409 in the route): the message says why."""


@dataclass
class Ctx:
    fam: Any                       # routes.family
    die: str
    cfg: str
    dst: str
    src_dir: Path
    dst_dir: Path
    dry_run: bool
    copy: bool
    glob_scope: bool               # the shared catalog's die: accounts / projects / production follow
    ws_roots: List[Path]
    actor: str = ""
    backup_dir: Optional[Path] = None
    undo: List[Callable[[], None]] = field(default_factory=list)
    after: List[Callable[[], None]] = field(default_factory=list)
    changes: List[Dict[str, Any]] = field(default_factory=list)
    manual: List[Dict[str, Any]] = field(default_factory=list)
    backups: List[Dict[str, str]] = field(default_factory=list)

    @property
    def old_key(self) -> str:
        return f"{self.die}/{self.cfg}"

    @property
    def new_key(self) -> str:
        return f"{self.dst}/{self.cfg}"

    def add(self, store: str, where: Any, change: str, **extra: Any) -> None:
        self.changes.append({"store": store, "where": str(where), "change": change, **extra})

    def is_pair(self, die: Any, cfg: Any) -> bool:
        return (str(die or "").casefold() == self.die.casefold()
                and str(cfg or "").casefold() == self.cfg.casefold())

    def backup(self, path: Path, raw: Optional[bytes] = None) -> None:
        """Keep a copy of ``path`` (its bytes now, or ``raw``) in the backup folder before it is rewritten."""
        if self.dry_run or self.backup_dir is None:
            return
        data = raw if raw is not None else _read_bytes(path)
        if data is None:
            return
        name = f"{len(self.backups):03d}-{path.name}"
        dest = self.backup_dir / "files" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self.backups.append({"of": str(path), "copy": str(dest)})


# ── helpers ──────────────────────────────────────────────────────────────────

def _json_rewrite(ctx: Ctx, store: str, path: Path, mutate: Callable[[Any], int], what: str,
                  indent: Optional[int] = 2) -> bool:
    """Read JSON ``path``, let ``mutate`` rewrite it in place (returns how many places), back it
    up, write it atomically, register the undo.  A missing / corrupt / unchanged file is skipped."""
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
    ctx.backup(path, raw)
    from motor_ai_sim import json_store
    json_store.atomic_write_json(path, doc, indent=indent)
    ctx.undo.append(lambda p=path, b=raw: _restore_bytes(p, b))
    return True


def _unique_paths(paths) -> List[Path]:
    out, seen = [], set()
    for p in paths:
        if p is None:
            continue
        p = Path(str(p))
        try:
            key = str(p.resolve()).casefold()
        except OSError:
            key = str(p).casefold()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def workspace_roots(fam: Any, glob_scope: bool) -> List[Path]:
    """The workspace roots whose stores may name the configuration: the caller's own always; for
    a shared-catalog die every workspace under ``WORKSPACES_ROOT`` and the process workspace too
    (customers solve the shared motors in their own workspaces)."""
    from motor_ai_sim import workspace as W
    roots = [Path(str(W.root()))]
    if glob_scope and W.layering():
        wr = W.workspaces_root()
        if wr is not None and Path(wr).is_dir():
            roots += [d for d in sorted(Path(wr).iterdir()) if d.is_dir() and not d.name.startswith(".")]
        try:
            roots.append(Path(str(W.process_workspace().root)))
        except Exception:                                   # noqa: BLE001
            pass
    return _unique_paths(roots)


# ── step 1: the configuration's files land in the target die ────────────────

def _runs(ctx: Ctx, d: Path) -> Path:
    return d / "runs" / ctx.cfg


def _history_files(ctx: Ctx) -> List[Path]:
    h = ctx.src_dir.parent / ".history" / ctx.die
    return sorted(h.glob(f"{ctx.cfg}.*.yaml")) if h.is_dir() else []


def step_files(ctx: Ctx) -> None:
    fam = ctx.fam
    src_file, dst_file = ctx.src_dir / f"{ctx.cfg}.yaml", ctx.dst_dir / f"{ctx.cfg}.yaml"
    if dst_file.exists():
        raise MoveBlocked(f"die '{ctx.dst}' already has a configuration '{ctx.cfg}'")
    src_runs, dst_runs = _runs(ctx, ctx.src_dir), _runs(ctx, ctx.dst_dir)
    if dst_runs.exists() and any(dst_runs.iterdir()):
        raise MoveBlocked(f"die '{ctx.dst}' already has stored runs under runs/{ctx.cfg}")
    verb = "copied" if ctx.copy else "moved"
    ctx.add("catalog", src_file, f"configuration {verb} to '{ctx.dst}' (die back-reference -> '{ctx.dst}')")
    if src_runs.is_dir():
        n = sum(1 for p in src_runs.rglob("*") if p.is_file())
        ctx.add("catalog", src_runs, f"stored runs {verb} to '{ctx.dst}/runs/{ctx.cfg}' ({n} file(s))")
    hist = _history_files(ctx)
    if hist:
        ctx.add("catalog_history", hist[0].parent, f"{len(hist)} saved version(s) of '{ctx.cfg}' {verb}")
    if ctx.dry_run:
        return
    doc = fam._load_yaml(src_file, "configuration")
    doc["die"] = ctx.dst
    fam._save_yaml(dst_file, doc)
    ctx.undo.append(lambda: _restore_bytes(dst_file, None))
    if src_runs.is_dir():
        dst_runs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_runs, dst_runs, dirs_exist_ok=True)
        ctx.undo.append(lambda: shutil.rmtree(dst_runs, ignore_errors=True))
    if hist:
        hdst = ctx.dst_dir.parent / ".history" / ctx.dst
        hdst.mkdir(parents=True, exist_ok=True)
        for f in hist:
            q = hdst / f.name
            if q.exists():
                continue
            shutil.copy2(f, q)
            ctx.undo.append(lambda q=q: _restore_bytes(q, None))


def step_fields(ctx: Ctx) -> None:
    """The stored field files (``.npz``) name their die in their own meta: the copy says the target."""
    from motor_ai_sim import duty_fields as DF
    root = _runs(ctx, ctx.src_dir if ctx.dry_run else ctx.dst_dir)
    if not root.is_dir():
        return
    todo = [p for p in sorted(root.rglob("*.npz")) if p.parent.name == "fields" and DF.meta_die(p) == ctx.die]
    if todo:
        ctx.add("duty_fields", root, f"meta die -> '{ctx.dst}' ({len(todo)} field file(s))")
    if ctx.dry_run:
        return
    for p in todo:
        if DF.retag_meta(p, die=ctx.dst):
            ctx.undo.append(lambda p=p: DF.retag_meta(p, die=ctx.die))


# ── step 2: the result ledgers ──────────────────────────────────────────────

def _ledgers(ctx: Ctx) -> List[Path]:
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import workspace as W
    if not W.layering():
        return [DR.store_path()]
    out = [DR.shared_store_path()] if ctx.glob_scope else []
    out += [r / ".duty_results.json" for r in ctx.ws_roots]
    return _unique_paths(out)


def step_duty_results(ctx: Ctx) -> None:
    from motor_ai_sim import duty_results as DR
    verb = "copied" if ctx.copy else "re-keyed"
    # the legacy per-die ledger inside the die folder: its rows go to the TARGET die's own ledger
    legacy_src, legacy_dst = ctx.src_dir / ".duty_results.json", ctx.dst_dir / ".duty_results.json"
    if legacy_src.is_file():
        with DR.using_store(legacy_src):
            rows = DR.config_rows(ctx.die, ctx.cfg)
        if rows["results"] or rows["history"]:
            ctx.add("duty_results", legacy_src, f"legacy per-die rows {verb} to {legacy_dst}",
                    rows={"results": rows["results"], "history": rows["history"]})
            with DR.using_store(legacy_dst):
                if DR.config_rows(ctx.dst, ctx.cfg)["results"]:
                    raise MoveBlocked(f"{legacy_dst} already has results for '{ctx.new_key}'")
            if not ctx.dry_run:
                raw_s, raw_d = _read_bytes(legacy_src), _read_bytes(legacy_dst)
                ctx.backup(legacy_src, raw_s)
                ctx.backup(legacy_dst, raw_d)
                with DR.using_store(legacy_src):
                    src_doc = DR.read_all()
                with DR.using_store(legacy_dst):
                    tgt = DR.read_all()
                    for b in ("results", "history"):
                        rows_b = ((src_doc.get(b) or {}).get(ctx.die) or {}).get(ctx.cfg)
                        if rows_b:
                            tgt.setdefault(b, {}).setdefault(ctx.dst, {})[ctx.cfg] = _copy.deepcopy(rows_b)
                    if not DR._write_all(tgt):
                        raise OSError(f"could not write {legacy_dst}")
                ctx.undo.append(lambda p=legacy_dst, b=raw_d: _restore_bytes(p, b))
                if not ctx.copy:
                    with DR.using_store(legacy_src):
                        DR.forget(ctx.die, ctx.cfg)
                    ctx.undo.append(lambda p=legacy_src, b=raw_s: _restore_bytes(p, b))
    ledgers = _ledgers(ctx)
    # the first ledger (shared for a shared die, else the caller's own) is always listed: a missing or empty
    # one is visible in a dry run, not silently skipped
    always = {str(ledgers[0])} if ledgers else set()
    for store in ledgers:
        if store == legacy_src:
            continue
        with DR.using_store(store):
            rows = DR.config_rows(ctx.die, ctx.cfg)
            found = bool(rows["results"] or rows["history"])
            if found and DR.config_rows(ctx.dst, ctx.cfg)["results"]:
                raise MoveBlocked(f"{store} already has results for '{ctx.new_key}'")
            if not found:
                if str(store) in always:
                    ctx.add("duty_results", store, "no rows for this configuration" if rows["exists"]
                            else "store file does not exist", exists=rows["exists"],
                            rows={"results": 0, "history": 0})
                continue
            ctx.add("duty_results", store, f"results + per-kind history {verb}", exists=True,
                    rows={"results": rows["results"], "history": rows["history"]})
            if ctx.dry_run:
                continue
            raw = _read_bytes(store)
            ctx.backup(store, raw)
            try:
                DR.move_config(ctx.die, ctx.cfg, ctx.dst, copy=ctx.copy)
            except ValueError as exc:
                raise MoveBlocked(f"{store}: {exc}")
            ctx.undo.append(lambda p=store, b=raw: _restore_bytes(p, b))


# ── step 3: the active contexts ──────────────────────────────────────────────

def step_context(ctx: Ctx) -> None:
    if ctx.copy:
        return

    def _m(doc):
        n = 0
        if not isinstance(doc, dict):
            return 0
        if doc.get("die") == ctx.die and doc.get("config") == ctx.cfg:
            doc["die"] = ctx.dst
            n += 1
        if doc.get("released_from") == ctx.die and doc.get("released_config") == ctx.cfg:
            doc["released_from"] = ctx.dst
            n += 1
        return n
    files = [ctx.fam._ctx_file()] + [r / ".family_context.json" for r in ctx.ws_roots]
    for p in _unique_paths(files):
        _json_rewrite(ctx, "active_context", p, _m, "active configuration")


# ── step 4: workspace memories ───────────────────────────────────────────────

def step_workspace_memory(ctx: Ctx) -> None:
    if ctx.copy:
        return
    old, new = f"{ctx.die}|{ctx.cfg}", f"{ctx.dst}|{ctx.cfg}"

    def _panel(doc):
        n = 0
        thermal = doc.get("thermal") if isinstance(doc, dict) else None
        for entry in (thermal.values() if isinstance(thermal, dict) else []):
            if not isinstance(entry, dict):
                continue
            by = entry.get("by_motor")
            if isinstance(by, dict) and old in by and new not in by:
                by[new] = by.pop(old)
                n += 1
            if entry.get("motor") == old:
                entry["motor"] = new
                n += 1
        return n

    def _mine(doc):
        n = 0
        for entries in (doc.values() if isinstance(doc, dict) else []):
            for e in (entries.values() if isinstance(entries, dict) else []):
                src = e.get("src") if isinstance(e, dict) else None
                if isinstance(src, dict) and ctx.is_pair(src.get("die"), src.get("config")):
                    src["die"] = ctx.dst
                    n += 1
        return n
    for r in ctx.ws_roots:
        _json_rewrite(ctx, "panel_settings", r / ".panel_settings.json", _panel, "per-motor Thermal memory re-keyed")
        _json_rewrite(ctx, "my_motors", r / "user_motors.json", _mine, "source of a private copy")


# ── step 5: catalog cards + presets of this configuration ───────────────────

def step_catalog_cards(ctx: Ctx) -> None:
    if ctx.copy:
        return
    from motor_ai_sim.routes import catalog as CAT
    from motor_ai_sim.routes import presets as PRE
    old_name = f"{ctx.die} {ctx.cfg}".casefold()

    def _fix(m) -> int:
        n = 0
        if not isinstance(m, dict):
            return 0
        if ctx.is_pair(m.get("die"), m.get("config")):
            m["die"] = ctx.dst
            n += 1
        if isinstance(m.get("name"), str) and m["name"].casefold() == old_name:
            m["name"] = f"{ctx.dst} {ctx.cfg}"
            n += 1
        return n
    _json_rewrite(ctx, "catalog_cards", CAT._catalog_path(),
                  lambda doc: sum(_fix(m) for m in ((doc.get("motors") if isinstance(doc, dict) else []) or [])),
                  "card die / name")
    _json_rewrite(ctx, "presets", PRE._presets_path(),
                  lambda doc: sum(_fix(m) for m in (doc.values() if isinstance(doc, dict) else [])),
                  "preset die / name")


# ── shared-catalog steps ─────────────────────────────────────────────────────

def _access_carry(ctx: Ctx) -> List[str]:
    """Accounts that see the configuration through the SOURCE die (its grant, or the die's
    selected-client list) and would not see it on the target die: they need its grant."""
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users
    vis = MA.file_visibility(ctx.src_dir / f"{ctx.cfg}.yaml")
    if vis != MA.CFG_INHERIT:
        return []                       # public: everybody; private: only its own grants (retargeted)
    src_da, dst_da = MA.get_die_access(ctx.die), MA.get_die_access(ctx.dst)
    if dst_da.get("visibility") == MA.VIS_PUBLIC:
        return []
    if src_da.get("visibility") == MA.VIS_PUBLIC or ctx.die == getattr(users, "_BASELINE_DIE", None):
        ctx.manual.append({"store": "die_access", "where": ctx.die, "change": (
            f"'{ctx.die}' is open to every signed-in account and '{ctx.dst}' is not: make '{ctx.dst}' "
            f"or the configuration '{ctx.cfg}' public if that should stay so")})
    out = []
    try:
        registry = users._load_soft()
    except Exception:                                       # noqa: BLE001
        return []
    for email, rec in registry.items():
        if not isinstance(rec, dict) or rec.get("disabled") or str(rec.get("role") or "") in ("admin", "engineer"):
            continue
        g = users.normalize_grants(rec.get("motors"))
        if g.get("all"):
            continue
        em = str(email).strip().lower()
        saw = ctx.die in g["dies"] or (src_da.get("visibility") == MA.VIS_SELECTED and em in src_da.get("clients", []))
        sees = ctx.dst in g["dies"] or (dst_da.get("visibility") == MA.VIS_SELECTED and em in dst_da.get("clients", []))
        if saw and not sees:
            out.append(em)
    return sorted(out)


def step_grants(ctx: Ctx) -> None:
    if ctx.copy:
        return
    # users.json is a PRIVATE file (identity registry): it is never copied into the catalog's backup
    # folder.  The manifest lists every account's change, the in-memory undo restores them on a failure.
    from motor_ai_sim import users
    hits, undo = users.move_config_in_grants(ctx.die, ctx.cfg, ctx.dst, add_for=_access_carry(ctx),
                                             dry_run=ctx.dry_run)
    for email, what in hits:
        ctx.add("user_grants", email, what)
    if undo is not None:
        ctx.undo.append(undo)


def _rewrite_project(ctx: Ctx, project: dict) -> int:
    from motor_ai_sim.development import motors as M
    n = 0
    for block in ("linked_motors", "motor_history", "grant_removal_candidates"):
        for row in project.get(block) or []:
            if isinstance(row, dict) and ctx.is_pair(row.get("die"), row.get("config")):
                row["die"] = ctx.dst
                n += 1
    ledger = project.get("project_motor_grants")
    if isinstance(ledger, dict):
        old_k, new_k = M._key(ctx.die, ctx.cfg), M._key(ctx.dst, ctx.cfg)
        if old_k in ledger:
            if new_k in ledger:
                raise MoveBlocked(f"project {project.get('id')} already has a grant ledger row for '{ctx.new_key}'")
            ledger[new_k] = ledger.pop(old_k)
            n += 1
        for entry in ledger.values():
            if isinstance(entry, dict) and str(entry.get("grant_key") or "").casefold() == ctx.old_key.casefold():
                entry["grant_key"] = ctx.new_key
                n += 1
    return n


def _rewrite_snapshot(ctx: Ctx, snap: Any) -> int:
    n = 0
    machine = snap.get("machine") if isinstance(snap, dict) else None
    if isinstance(machine, dict) and ctx.is_pair(machine.get("die_name"), machine.get("config_name")):
        machine["die_name"] = ctx.dst
        n += 1
    scope = snap.get("access_scope") if isinstance(snap, dict) else None
    if (isinstance(scope, dict) and scope.get("kind") == "catalog"
            and ctx.is_pair(scope.get("die"), scope.get("config"))):
        scope["die"] = ctx.dst
        scope["grant_key"] = ctx.new_key
        n += 1
    return n


def step_projects(ctx: Ctx) -> None:
    if ctx.copy:
        return                          # a copy joins the projects through autolink (after the move)
    from motor_ai_sim import json_store, users
    from motor_ai_sim.development import motors as M
    from motor_ai_sim.development import store as PS
    try:
        projects = PS.list_projects()
        index_path = PS.index_path()
        index_exists = index_path.is_file()
    except Exception:                                       # noqa: BLE001 - no project store: nothing to do
        return
    if not projects and not index_exists:
        return
    with (_null_cm() if ctx.dry_run else PS.project_transaction()), users._LOCK:
        for project in projects:
            pid = str(project.get("id") or "")
            if not PS.valid_id(pid):
                continue
            snaps = []
            snap_dir = PS.path_for(pid) / "snapshots"
            for sp in (sorted(snap_dir.glob("*.json")) if snap_dir.is_dir() else []):
                raw = _read_bytes(sp)
                try:
                    doc = json.loads((raw or b"{}").decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                k = _rewrite_snapshot(ctx, doc)
                if k:
                    snaps.append((sp, raw, doc))
                    ctx.add("project_snapshots", sp, f"machine name / access scope ({k})")
            n = _rewrite_project(ctx, _copy.deepcopy(project))
            if n:
                ctx.add("projects", f"{pid}/project.json",
                        f"linked motor / history / grant ledger rows ({n})")
            if ctx.dry_run or not (n or snaps):
                continue
            for sp, raw, doc in snaps:
                ctx.backup(sp, raw)
                json_store.atomic_write_json(sp, doc)
                ctx.undo.append(lambda sp=sp, raw=raw: _restore_bytes(sp, raw))
            if n:
                before = _copy.deepcopy(project)
                ctx.backup(PS.path_for(pid) / "project.json")
                def _edit(doc: dict) -> None:            # in place: a returned value would BE the document
                    _rewrite_project(ctx, doc)
                PS.mutate_project(pid, _edit)
                ctx.undo.append(lambda pid=pid, before=before: PS.save_json(pid, "project.json", before))
                ctx.after.append(lambda pid=pid: PS.append_audit(
                    pid, ctx.actor or "system", "motor.move", "motor", ctx.old_key, {"to": ctx.new_key}))
        idx_raw = _read_bytes(index_path)
        idx = PS.load_index()
        motors = idx.get("motors") if isinstance(idx, dict) else None
        old_k, new_k = M._key(ctx.die, ctx.cfg), M._key(ctx.dst, ctx.cfg)
        if isinstance(motors, dict) and old_k in motors:
            if new_k in motors:
                raise MoveBlocked(f"the project motor index already has '{ctx.new_key}'")
            ctx.add("project_index", index_path, "motor index row re-keyed")
            if not ctx.dry_run:
                row = motors.pop(old_k)
                if isinstance(row, dict):
                    row["die"] = ctx.dst
                motors[new_k] = row
                ctx.backup(index_path, idx_raw)
                PS.save_index(idx)
                ctx.undo.append(lambda: _restore_bytes(index_path, idx_raw))


def _config_folder_move(ctx: Ctx, store: str, root: Path, old_dir: Path, new_dir: Path,
                        audit: Optional[Callable[[], None]] = None) -> None:
    """Move one per-configuration folder (production, tests) and rewrite the ``die`` its JSON records name."""
    if not old_dir.is_dir():
        return
    if ctx.copy:
        ctx.manual.append({"store": store, "where": str(old_dir),
                           "change": "not copied: the records stay with the source configuration"})
        return
    if new_dir.exists() and any(new_dir.iterdir()):
        raise MoveBlocked(f"{store} folder '{new_dir}' already exists")
    jsons = [p for p in sorted(old_dir.rglob("*.json")) if "files" not in p.relative_to(old_dir).parts]
    originals = {p.relative_to(old_dir): _read_bytes(p) for p in jsons}
    ctx.add(store, old_dir, f"folder moves to '{new_dir}'")
    for rel, raw in originals.items():
        try:
            doc = json.loads((raw or b"{}").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if walk_replace(doc, ctx.die, ctx.dst):
            ctx.add(store, old_dir / rel, "die field")
    if ctx.dry_run:
        return
    for rel, raw in originals.items():
        ctx.backup(old_dir / rel, raw)
    new_dir.parent.mkdir(parents=True, exist_ok=True)
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
        if walk_replace(doc, ctx.die, ctx.dst):
            json_store.atomic_write_json(p, doc)
            ctx.undo.append(lambda p=p, b=raw: _restore_bytes(p, b))
    if audit is not None:
        ctx.after.append(audit)


def step_production(ctx: Ctx) -> None:
    from motor_ai_sim.production import store as PR
    try:
        root = PR.production_root()
    except Exception:                                       # noqa: BLE001
        return
    slug = PR._safe_slug
    stamp = datetime.now().isoformat(timespec="seconds")
    _config_folder_move(ctx, "production", root, root / slug(ctx.die) / slug(ctx.cfg),
                        root / slug(ctx.dst) / slug(ctx.cfg),
                        lambda: PR.append_audit(ctx.dst, ctx.cfg, {
                            "action": "config.move", "from": ctx.old_key, "to": ctx.new_key,
                            "actor": ctx.actor or "system", "time": stamp}))


def step_motor_tests(ctx: Ctx) -> None:
    from motor_ai_sim.motor_tests import store as MT
    from motor_ai_sim.production.store import _safe_slug as slug
    try:
        root = MT.root()
    except Exception:                                       # noqa: BLE001
        return
    stamp = datetime.now().isoformat(timespec="seconds")
    _config_folder_move(ctx, "motor_tests", root, root / slug(ctx.die) / slug(ctx.cfg),
                        root / slug(ctx.dst) / slug(ctx.cfg),
                        lambda: MT.append_audit(ctx.dst, ctx.cfg, {
                            "action": "config.move", "from": ctx.old_key, "to": ctx.new_key,
                            "who": ctx.actor or "system", "time": stamp}))


def step_cad(ctx: Ctx) -> None:
    from motor_ai_sim import model3d
    from motor_ai_sim.cad import rebuild_queue as RQ
    try:
        root = Path(str(model3d.cad_root()))
    except Exception:                                       # noqa: BLE001
        return
    old_dir, new_dir = root / ctx.die / ctx.cfg, root / ctx.dst / ctx.cfg
    if old_dir.is_dir():
        if ctx.copy:
            ctx.manual.append({"store": "cad_models", "where": str(old_dir),
                               "change": "not copied: build the copy's CAD model from its own parameters"})
        else:
            if new_dir.exists():
                raise MoveBlocked(f"CAD model folder '{new_dir}' already exists")
            manifests = sorted(old_dir.rglob("manifest.json"))
            ctx.add("cad_models", old_dir, f"folder moves to '{new_dir}' ({len(manifests)} manifest(s))")
            if not ctx.dry_run:
                originals = {m.relative_to(old_dir): _read_bytes(m) for m in manifests}
                for rel, raw in originals.items():
                    ctx.backup(old_dir / rel, raw)
                new_dir.parent.mkdir(parents=True, exist_ok=True)
                old_dir.rename(new_dir)
                ctx.undo.append(lambda: new_dir.rename(old_dir))
                from motor_ai_sim import json_store
                for rel, raw in originals.items():
                    try:
                        doc = json.loads((raw or b"{}").decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if walk_replace(doc, ctx.die, ctx.dst):
                        json_store.atomic_write_json(new_dir / rel, doc)
                        ctx.undo.append(lambda p=new_dir / rel, b=raw: _restore_bytes(p, b))
    if ctx.copy:
        return
    rec = RQ.read(root, ctx.die, ctx.cfg) if Path(RQ.queue_dir(root)).is_dir() else None
    if rec:
        old_p = Path(RQ._path(root, ctx.die, ctx.cfg))
        ctx.add("cad_rebuild_queue", old_p, "request re-keyed")
        if not ctx.dry_run:
            raw = _read_bytes(old_p)
            ctx.backup(old_p, raw)
            rec = dict(rec, die=ctx.dst)
            RQ._write(root, rec)
            old_p.unlink()
            new_p = Path(RQ._path(root, ctx.dst, ctx.cfg))
            ctx.undo.append(lambda a=old_p, b=raw, c=new_p: (_restore_bytes(a, b), _restore_bytes(c, None)))


def step_manual(ctx: Ctx) -> None:
    """Deploy-managed, read-only-for-the-app stores that name the configuration or its source die."""
    def note(store: str, path: Any, action: str) -> None:
        ctx.manual.append({"store": store, "where": str(path), "change": action})
    try:
        import yaml
        from motor_ai_sim import cooling_options as CO
        cands = [CO._FILE] if CO._FILE.is_file() else []
        if CO._FILE == CO._REPO_FILE:
            from motor_ai_sim.workspace import shared_root
            sp = Path(str(shared_root())) / "cooling_options.yaml"
            if sp.is_file():
                cands.append(sp)
        for p in cands:
            dies = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("dies")
            if isinstance(dies, dict) and ctx.die in dies and ctx.dst not in dies:
                note("cooling_options", p, f"'{ctx.die}' has cooling options and '{ctx.dst}' has none: "
                                           f"add dies['{ctx.dst}'] if '{ctx.cfg}' should keep them")
    except Exception:                                       # noqa: BLE001
        log.debug("config move: cooling options probe failed", exc_info=True)
    try:
        from motor_ai_sim import passport_store as PP
        d = PP.store_dir() / ctx.die / ctx.cfg
        if d.is_dir():
            note("passports", d, f"{'copy' if ctx.copy else 'move'} the folder to '{PP.store_dir() / ctx.dst / ctx.cfg}'")
    except Exception:                                       # noqa: BLE001
        log.debug("config move: passport probe failed", exc_info=True)
    try:
        from motor_ai_sim import propeller_s1 as S1
        for root in S1.model_dirs():
            d = root / ctx.die / ctx.cfg
            if d.is_dir():
                note("propeller_s1", d, f"{'copy' if ctx.copy else 'move'} the folder to '{root / ctx.dst / ctx.cfg}'")
    except Exception:                                       # noqa: BLE001
        log.debug("config move: propeller S1 probe failed", exc_info=True)


# ── last step of a move: the source files go to the backup folder ───────────

def step_remove_source(ctx: Ctx) -> None:
    if ctx.copy:
        return
    src_file, src_runs = ctx.src_dir / f"{ctx.cfg}.yaml", _runs(ctx, ctx.src_dir)
    ctx.add("catalog", src_file, f"source configuration removed from '{ctx.die}' (kept in the backup folder)")
    if ctx.dry_run:
        return
    keep = ctx.backup_dir / "source"
    keep.mkdir(parents=True, exist_ok=True)
    moved = []
    for p, name in [(src_file, src_file.name), (src_runs, "runs")] + [(h, f"history-{h.name}")
                                                                      for h in _history_files(ctx)]:
        if p.exists():
            q = keep / name
            shutil.move(str(p), str(q))
            moved.append((p, q))
            ctx.backups.append({"of": str(p), "copy": str(q)})

    def _undo() -> None:
        for p, q in reversed(moved):
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(q), str(p))
    ctx.undo.append(_undo)


LOCAL_STEPS = (step_files, step_fields, step_duty_results, step_context, step_workspace_memory,
               step_catalog_cards)
GLOBAL_STEPS = (step_grants, step_projects, step_production, step_motor_tests, step_cad, step_manual)


def preflight(fam: Any, die: str, cfg: str, dst: str, *, copy: bool) -> None:
    """What refuses a move before any step looks at the stores (MoveBlocked = 409)."""
    if not copy:
        from fastapi import HTTPException
        from motor_ai_sim import motor_revisions as MR
        try:
            MR.refuse_rename_config(die, cfg)
        except HTTPException as exc:
            raise MoveBlocked(f"{exc.detail} (copy it instead: a copy leaves the revision where it is)")
    try:
        from motor_ai_sim import data_publish
        for d in (die, dst):
            mv = data_publish.active_move(d)
            if mv:
                raise MoveBlocked(f"'{d}' is being moved between the open and private data sets "
                                  f"(move {mv.get('id') or '?'}); finish or cancel that first")
    except MoveBlocked:
        raise
    except Exception:                                       # noqa: BLE001
        pass
    c = fam._read_ctx() or {}
    if not copy and c.get("die") == die and c.get("config") == cfg:
        try:
            running = fam._jobs_running_here()
        except Exception:                                   # noqa: BLE001
            running = []
        if running:
            raise MoveBlocked(f"'{die}/{cfg}' is loaded and {len(running)} job(s) are running on it; "
                              "wait for them or stop them first")


def run(fam: Any, die: str, cfg: str, dst: str, *, src_dir: Path, dst_dir: Path, dry_run: bool,
        copy: bool, glob_scope: bool, actor: str = "") -> Dict[str, Any]:
    """Move (or copy) ``die/cfg`` to ``dst/cfg`` everywhere, or (``dry_run``) list where.  All or nothing."""
    preflight(fam, die, cfg, dst, copy=copy)
    ctx = Ctx(fam=fam, die=die, cfg=cfg, dst=dst, src_dir=src_dir, dst_dir=dst_dir, dry_run=dry_run,
              copy=copy, glob_scope=glob_scope, ws_roots=workspace_roots(fam, glob_scope), actor=actor)
    if not dry_run:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        ctx.backup_dir = src_dir.parent.parent / ".config_moves" / f"{stamp} {die}~{cfg}"
        ctx.backup_dir.mkdir(parents=True, exist_ok=True)
    steps = LOCAL_STEPS + (GLOBAL_STEPS if glob_scope else ()) + (step_remove_source,)
    done = 0
    try:
        for step in steps:
            step(ctx)
            done += 1
    except BaseException as exc:
        failures = []
        for undo in reversed(ctx.undo):
            try:
                undo()
            except Exception as uexc:                       # noqa: BLE001
                failures.append(repr(uexc))
        log.error("config %s '%s/%s' -> '%s' FAILED in %s and was rolled back (%s)%s",
                  "copy" if copy else "move", die, cfg, dst,
                  getattr(steps[done], "__name__", "?") if done < len(steps) else "?", exc,
                  f"; rollback problems: {failures}" if failures else "")
        if failures:
            try:
                exc.rollback_failures = failures
            except Exception:                               # noqa: BLE001
                pass
        _write_manifest(ctx, status="rolled back" if not failures else "rollback incomplete", error=repr(exc))
        raise
    if not dry_run:
        _write_manifest(ctx, status="done")
        for line in ctx.after:
            try:
                line()
            except Exception:                               # noqa: BLE001 - an audit line must not undo a finished move
                log.warning("config move: an audit line could not be written", exc_info=True)
        if copy:
            try:
                fam._autolink_new_config(dst, cfg, None, copied_from=die)
            except Exception:                               # noqa: BLE001
                log.warning("config copy: autolink failed", exc_info=True)
        try:
            from motor_ai_sim import admin_audit
            admin_audit.record(actor or "system", "config.copy" if copy else "config.move", f"{die}/{cfg}",
                               details={"to": f"{dst}/{cfg}", "stores": sorted({c["store"] for c in ctx.changes}),
                                        "changes": len(ctx.changes), "manual": len(ctx.manual),
                                        "backup": str(ctx.backup_dir)})
        except Exception:                                   # noqa: BLE001
            log.warning("config move: audit record failed", exc_info=True)
        log.info("configuration '%s/%s' %s to '%s' (%d change(s), %d manual, backup %s)", die, cfg,
                 "copied" if copy else "moved", dst, len(ctx.changes), len(ctx.manual), ctx.backup_dir)
    return {"ok": True, "mode": "copy" if copy else "move", "dry_run": dry_run,
            "from": {"die": die, "config": cfg}, "to": {"die": dst, "config": cfg},
            "scope": "shared catalog" if glob_scope else "workspace",
            "changes": ctx.changes, "manual": ctx.manual,
            "backup": str(ctx.backup_dir) if ctx.backup_dir else None}


def _write_manifest(ctx: Ctx, *, status: str, error: Optional[str] = None) -> None:
    if ctx.dry_run or ctx.backup_dir is None:
        return
    try:
        (ctx.backup_dir / "manifest.json").write_text(json.dumps({
            "mode": "copy" if ctx.copy else "move", "from": ctx.old_key, "to": ctx.new_key,
            "actor": ctx.actor, "at": datetime.now().isoformat(timespec="seconds"), "status": status,
            "error": error, "changes": ctx.changes, "manual": ctx.manual, "backups": ctx.backups,
        }, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    except OSError:
        log.warning("config move: manifest not written to %s", ctx.backup_dir, exc_info=True)
