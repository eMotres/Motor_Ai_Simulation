"""The US half of the catalog mirror (docs/regions/DESIGN.md sections 3, 7, 12): plan, apply, merge.

EU authors the open library; US mirrors it READ-ONLY.  ``scripts/mirror_catalog_us.sh`` (systemd timer, every 15 min) runs
three steps; the two Python ones are in this module, rsync is the host's:

    python -m motor_ai_sim.regions.catalog_mirror plan  [--init] [--prune-leftovers]
        pulls the manifest and the per-die extracts from EU over the signed peer channel, compares them with the files
        this job put here last time (its STATE file), refuses to go on when the US copy has local edits, writes
        ``plan.json`` and ``fetch.txt`` (the rsync ``--files-from`` list) into the mirror work directory
    rsync -a --files-from=fetch.txt rsync://<eu wireguard>/motres-library/ <shared>/       (host, read-only module)
    python -m motor_ai_sim.regions.catalog_mirror apply
        deletes the delete list, re-hashes what rsync fetched against the manifest, merges the extracts into the US
        stores (``.duty_results.json``, ``end_effect_passports.json``, ``die_access.json``), records the new state

Rules:

* US never authors the library.  A file this job installed earlier whose content changed (or vanished) on US is a LOCAL
  EDIT: the job refuses to run (exit 2) and touches nothing until somebody looks.
* The job deletes only what it installed itself (the STATE file).  Everything else on US - for example a closed customer
  motor that lives on US because its owner does - is none of its business.  A mirrored path that already exists on US with
  other content and is not in the state is a COLLISION: refused, never overwritten (``--init`` is the one-time adoption
  of the copy that was put here by hand before the job existed).
* A manifest path that is absolute, climbs out (``..``) or is outside the library allow-list is dropped and counted: a
  wrong manifest cannot make US write elsewhere.
* The extracts are merged per die.  What the job merged earlier for a die that is no longer mirrored is removed; rows of
  other dies and anything the job did not merge are left alone.  A corrupt target store is never overwritten.

Exit codes of ``plan`` / ``apply``: 0 ok (also "already in sync"), 2 local edits / collision, 3 first run without
``--init``, 4 EU did not answer, 5 a fetched file did not match the manifest (state stays consistent, the next run
refetches), 6 not a US server / single-region mode, 7 a file or row that must go could not be removed (or a store is
corrupt): the state keeps it, the next run tries again, and the unit shows "failed" so a de-published die cannot linger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from motor_ai_sim.regions import catalog_manifest as CM
from motor_ai_sim.regions import config as RC

log = logging.getLogger(__name__)

STATE_VERSION = 1
ENV_STATE = "MOTRES_MIRROR_STATE_FILE"
ENV_DIR = "MOTRES_MIRROR_DIR"
EXTRACT_TIMEOUT_S = 60.0
EXIT_OK, EXIT_EDITS, EXIT_INIT, EXIT_PEER, EXIT_VERIFY, EXIT_MODE, EXIT_FAILED = 0, 2, 3, 4, 5, 6, 7

_LOCK = threading.RLock()


class MirrorError(RuntimeError):
    """The merge cannot go on safely (a target store is corrupt); nothing was written to it."""


# ── where things live ────────────────────────────────────────────────────────

def work_dir() -> Path:
    env = (os.environ.get(ENV_DIR) or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim import users as U
    return Path(U._USERS_FILE).parent / "mirror"


def state_path() -> Path:
    env = (os.environ.get(ENV_STATE) or "").strip()
    return Path(env).expanduser() if env else work_dir() / "catalog_mirror_state.json"


def shared_dir() -> Path:
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root()))


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    try:
        d = json.loads((path or state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = None
    if not isinstance(d, dict) or not isinstance(d.get("files"), dict):
        return {"v": STATE_VERSION, "files": {}, "extracts": {}, "root_id": None, "ts": None, "initialised": False}
    d.setdefault("extracts", {})
    d["initialised"] = True
    return d


def _atomic_write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    state = {k: v for k, v in state.items() if k != "initialised"}
    state["v"] = STATE_VERSION
    _atomic_write(path or state_path(), json.dumps(state, indent=1, sort_keys=True))


# ── hashing the US copy ──────────────────────────────────────────────────────

def _sha(p: Path) -> Optional[str]:
    """sha256 of a file, or ``None`` when it is missing / unreadable.  Never cached: the point is to see an edit."""
    h = hashlib.sha256()
    try:
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def safe_rel(rel: Any) -> Optional[str]:
    """The manifest path if it is a plain relative path inside the library allow-list, else ``None``."""
    if not isinstance(rel, str) or not rel or "\\" in rel or any(ord(c) < 32 or ord(c) == 127 for c in rel):
        return None
    pp = PurePosixPath(rel)
    if pp.is_absolute() or ".." in pp.parts or "." in pp.parts or str(pp) != rel:
        return None
    top = pp.parts[0]
    if len(pp.parts) == 1:
        return rel if top in CM.LIBRARY_FILES else None
    return rel if top in CM.LIBRARY_DIRS else None


def clean_manifest_files(manifest: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """-> ``({rel: {sha256, bytes}}, dropped)``; a malformed entry or an unsafe path is dropped and counted."""
    out: Dict[str, Dict[str, Any]] = {}
    dropped = 0
    files = manifest.get("files")
    for rel, meta in (files.items() if isinstance(files, dict) else []):
        ok = safe_rel(rel)
        sha = meta.get("sha256") if isinstance(meta, dict) else None
        if ok is None or not isinstance(sha, str) or len(sha) != 64:
            dropped += 1
            continue
        out[ok] = {"sha256": sha, "bytes": int(meta.get("bytes") or 0)}
    return out, dropped


# ── plan ─────────────────────────────────────────────────────────────────────

def local_edits(root: Path, state: Dict[str, Any]) -> List[Dict[str, str]]:
    """Files this job installed whose content on US is no longer what it recorded (changed or removed)."""
    out = []
    for rel, meta in sorted((state.get("files") or {}).items()):
        want = meta.get("sha256") if isinstance(meta, dict) else None
        have = _sha(Path(root) / rel)
        if have is None:
            out.append({"path": rel, "why": "removed or unreadable on US"})
        elif have != want:
            out.append({"path": rel, "why": "changed on US"})
    return out


def _dir_names(root: Path, top: str) -> List[str]:
    """Folder names under ``<root>/<top>``.  A folder that does not exist is ``[]``; ANY other listing error raises
    ``MirrorError``: an empty list would read as "no US-local dies" and the prune would then delete their rows."""
    try:
        return sorted(p.name for p in (Path(root) / top).iterdir() if p.is_dir() and not p.is_symlink())
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise MirrorError(f"cannot list {top}/ on US ({type(exc).__name__}); refusing to decide which dies are US-local") from exc


def _die_dirs(root: Path) -> List[str]:
    return _dir_names(root, "dies")


def mirrored_dies(manifest: Dict[str, Any], extracts: Optional[Dict[str, Any]] = None) -> set:
    """Dies EU mirrors: those with a file in the manifest, plus those of the extracts."""
    out = {p.split("/")[1] for p in (manifest.get("files") or {}) if p.startswith("dies/") and p.count("/") >= 2}
    out |= set((extracts or {}).get("dies") or {})
    return out


def excluded_here(root: Path, manifest: Dict[str, Any], keep: set) -> List[str]:
    """Names of dies on US - a folder under ``dies/``, ``passports/`` or ``cad/`` - that EU says it does NOT mirror.  EU sends
    no names: it sends an HMAC (peer secret) of every excluded name and US matches them against its own folders.  Dies in
    ``keep`` are never named."""
    hm = set(manifest.get("excluded_die_hmacs") or [])
    if not hm:
        return []
    names = set(_dir_names(root, "dies")) | set(_dir_names(root, "passports")) | set(_dir_names(root, "cad"))
    return sorted(n for n in names if n not in keep and CM.die_hmac(n) in hm)


def leftovers(root: Path, manifest: Dict[str, Any], state: Dict[str, Any]) -> List[str]:
    """Files on US under ``dies/<die>/``, ``passports/<die>/`` and ``cad/<die>/`` of a die EU says is NOT mirrored, that this
    job did not install: what an earlier hand copy left behind.  Reported by ``plan``; removed by ``--prune-leftovers``."""
    root = Path(root)
    keep = mirrored_dies(manifest) | {p.split("/")[1] for p in state.get("files", {}) if p.startswith("dies/")}
    out: List[str] = []
    for name in excluded_here(root, manifest, keep):
        for top in ("dies", "passports", "cad"):
            base = root / top / name
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if p.is_file() and not p.is_symlink():
                    out.append(p.relative_to(root).as_posix())
    return out


def make_plan(manifest: Dict[str, Any], root: Path, state: Dict[str, Any], *, init: bool = False,
              prune: bool = False) -> Dict[str, Any]:
    """What to fetch / delete, or why to refuse.  Pure: reads the disk, writes nothing."""
    root = Path(root)
    src, dropped = clean_manifest_files(manifest)
    mine: Dict[str, Any] = state.get("files") or {}
    plan: Dict[str, Any] = {"v": 1, "ts": round(time.time(), 1), "root_id": manifest.get("root_id"), "refuse": None,
                            "fetch": [], "delete": [], "edits": [], "collisions": [], "leftovers": [],
                            "dropped_paths": dropped, "init": bool(init),
                            "prune_files": bool(prune), "prune_stores": bool(prune or init),
                            "excluded_dies": int(manifest.get("excluded_dies_count") or 0),
                            "skipped_unreadable": int(manifest.get("skipped_unreadable") or 0)}
    if not state.get("initialised") and not init:
        plan["refuse"] = {"code": EXIT_INIT, "why": "first run: no state file; check the copy on US, then run with --init"}
        return plan
    plan["edits"] = local_edits(root, state)
    if plan["edits"]:
        plan["refuse"] = {"code": EXIT_EDITS, "why": "the US copy has local edits; US never authors the library"}
        return plan
    for rel, meta in sorted(src.items()):
        have = _sha(root / rel)
        if have == meta["sha256"]:
            continue
        if have is not None and rel not in mine and not init:
            plan["collisions"].append({"path": rel, "why": "exists on US with other content and was not installed by the mirror"})
        else:
            plan["fetch"].append(rel)
    if plan["collisions"]:
        plan["refuse"] = {"code": EXIT_EDITS, "why": "a mirrored path collides with a file that is not the mirror's"}
        plan["fetch"] = []
        return plan
    plan["delete"] = sorted(rel for rel in mine if rel not in src and safe_rel(rel) is not None)
    plan["leftovers"] = leftovers(root, manifest, state)
    return plan


def fetch_list_text(plan: Dict[str, Any]) -> str:
    return "".join(rel + "\n" for rel in plan.get("fetch") or [])


# ── apply ────────────────────────────────────────────────────────────────────

def _prune_empty_dirs(root: Path, rel: str) -> None:
    p = (Path(root) / rel).parent
    top = Path(root)
    while p != top and top in p.parents:
        try:
            p.rmdir()
        except OSError:
            return
        p = p.parent


def _remove(root: Path, rel: str) -> bool:
    if safe_rel(rel) is None:
        return False
    try:
        (Path(root) / rel).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("catalog mirror: cannot delete %s (%s)", rel, type(exc).__name__)
        return False
    _prune_empty_dirs(root, rel)
    return True


def apply_plan(manifest: Dict[str, Any], extracts: Optional[Dict[str, Any]], plan: Dict[str, Any], root: Path,
               state: Dict[str, Any], *, prune_leftovers: bool = False, prune_stores: bool = False,
               duty_store: Optional[Path] = None, passport_store: Optional[Path] = None) -> Dict[str, Any]:
    """Delete, verify what was fetched, merge the extracts, record the state.  -> a report: ``bad`` non-empty = a fetched file
    did not match (exit 5); ``failed`` non-empty = a file or row that must go could not be removed (exit 7)."""
    root = Path(root)
    src, _dropped = clean_manifest_files(manifest)
    report: Dict[str, Any] = {"deleted": 0, "fetched_ok": 0, "bad": [], "failed": [], "leftovers_removed": 0,
                              "extracts": None}
    files = dict(state.get("files") or {})
    for rel in plan.get("delete") or []:
        if rel not in files:
            continue
        if _remove(root, rel):
            files.pop(rel, None)
            report["deleted"] += 1
        else:
            report["failed"].append(rel)              # stays in the state: the next run tries again, the exit code is non-zero
    if prune_leftovers:
        for rel in plan.get("leftovers") or []:
            if safe_rel(rel) is not None and Path(rel).parts[:1] in (("dies",), ("passports",), ("cad",)):
                if _remove_any(root, rel):
                    report["leftovers_removed"] += 1
                else:
                    report["failed"].append(rel)
    for rel in plan.get("fetch") or []:
        # Record what is on disk, right or wrong: rsync wrote it, so it is not a local edit; a wrong one differs from the
        # manifest and is fetched again by the next run.
        have = _sha(root / rel)
        if have is None:
            files.pop(rel, None)
        else:
            files[rel] = {"sha256": have, "bytes": (root / rel).stat().st_size}
        if rel in src and have == src[rel]["sha256"]:
            report["fetched_ok"] += 1
        else:
            report["bad"].append(rel)
    # in-sync files that were not fetched this time are recorded as they are on disk (a --init adoption)
    for rel, meta in src.items():
        if rel not in files:
            have = _sha(root / rel)
            if have == meta["sha256"]:
                files[rel] = {"sha256": have, "bytes": meta["bytes"]}
    state = {**state, "files": {k: v for k, v in files.items() if v}, "root_id": manifest.get("root_id"),
             "ts": round(time.time(), 1)}
    # The file state is saved BEFORE the merge: the files above are already deleted / fetched on disk, and a state that still
    # listed the deleted ones would make every later run refuse with "local edits" (exit 2).
    save_state(state)
    if isinstance(extracts, dict) and extracts.get("v") == CM.EXTRACT_VERSION:
        if (manifest.get("extracts") or {}).get("id") in (None, extracts.get("id")):
            try:
                state["extracts"] = merge_extracts(extracts, state.get("extracts") or {}, root, duty_store=duty_store,
                                                   passport_store=passport_store, mirrored=mirrored_dies(manifest, extracts),
                                                   manifest=manifest if prune_stores else None)
            except (MirrorError, OSError) as exc:
                report["failed"].append("extracts: " + str(exc)[:200])        # reported (exit 7); the next run merges again
                report["extracts"] = {"error": str(exc)[:200]}
                return report
            report["extracts"] = {"id": extracts.get("id"), "dies": len(extracts.get("dies") or {}),
                                  "passports": len(extracts.get("passports") or {})}
            save_state(state)
        else:
            report["extracts"] = {"skipped": "extracts changed between the manifest and the extract; next run"}
    return report


def _remove_any(root: Path, rel: str) -> bool:
    try:
        (Path(root) / rel).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("catalog mirror: cannot delete %s (%s)", rel, type(exc).__name__)
        return False
    _prune_empty_dirs(root, rel)
    return True


# ── merging the per-die extracts ─────────────────────────────────────────────

class _FileLock:
    """Cross-process lock on ``<store>.lock`` - the same sidecar ``duty_results`` takes for the shared store."""

    def __init__(self, path: Path):
        self.path = path.with_name(path.name + ".lock")
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+b")
        try:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        except ImportError:                                  # Windows: thread lock only
            pass
        return self

    def __exit__(self, *exc):
        try:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        self.fh.close()


def _load_store(p: Path, what: str) -> Dict[str, Any]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise MirrorError(f"{what} ({p.name}) is unreadable: {type(exc).__name__}; nothing merged into it") from exc
    if not isinstance(d, dict):
        raise MirrorError(f"{what} ({p.name}) is not an object; nothing merged into it")
    return d


def _foreign_dies(root: Path, manifest: Dict[str, Any], keep: set, names: set, *, hmac_only: bool = False) -> set:
    """Dies in a US store that are NOT the mirror's and NOT US-local: EU says they are closed (HMAC match), or no folder of
    that name exists on US at all (a stale row).  A die with a folder on US that EU does not know is US-local: never touched."""
    hm = set(manifest.get("excluded_die_hmacs") or [])
    closed = {d for d in names if d not in keep and hm and CM.die_hmac(d) in hm}
    if hmac_only:                                   # die_access: an entry without a folder is not ours to delete
        return closed
    local = set(_die_dirs(root))                    # raises MirrorError when the listing fails
    return closed | {d for d in names if d not in keep and d not in local}


def merge_extracts(extracts: Dict[str, Any], previous: Dict[str, Any], root: Path, *, duty_store: Optional[Path] = None,
                   passport_store: Optional[Path] = None, mirrored: Any = (),
                   manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge the per-die extracts into the US stores.  -> the record of what was merged (kept in the state file, so the
    next run can remove the rows of a die that stopped being public).

    The mirror owns the rows of every die it mirrors (``mirrored``) and of every die it merged earlier (``previous``): they
    are replaced by the extract or removed.  With ``manifest`` (``--init`` / ``--prune-leftovers``) it also removes the rows of
    dies that EU says are closed (HMAC match) or that have no folder on US, and the end-effect entries only those rows named;
    ``die_access`` entries are removed on an HMAC match only; the rows of US-local dies stay.  A store that cannot be read is never overwritten (``MirrorError``)."""
    from motor_ai_sim import motor_access as MA
    root = Path(root)
    keep = set(mirrored) | set((extracts.get("dies") or {}))
    duty_path = Path(duty_store) if duty_store else root / CM.DUTY_STORE
    pass_path = Path(passport_store) if passport_store else root / CM.END_EFFECT_STORE
    dies = extracts.get("dies") if isinstance(extracts.get("dies"), dict) else {}
    passports = extracts.get("passports") if isinstance(extracts.get("passports"), dict) else {}
    fps = sorted(str(k) for k, v in passports.items() if isinstance(v, dict))

    duty_dies = sorted(d for d, v in dies.items() if isinstance(v, dict) and isinstance(v.get("duty"), dict))
    orphan_fps: set = set()
    with _LOCK, _FileLock(duty_path):
        doc = _load_store(duty_path, "the duty results store")
        res = doc.get("results") if isinstance(doc.get("results"), dict) else {}
        hist = doc.get("history") if isinstance(doc.get("history"), dict) else {}
        drop = (set(previous.get("duty_dies") or []) | set(mirrored)) - set(duty_dies)
        if manifest is not None:
            drop |= _foreign_dies(root, manifest, keep, set(res) | set(hist)) - set(duty_dies)
        for die in sorted(drop):
            for block in (res, hist):
                if die in block:
                    CM._collect_fps(block.pop(die), orphan_fps)
        for die in duty_dies:
            d = dies[die]["duty"]
            res[die] = d.get("results") or {}
            hist[die] = d.get("history") or {}
        doc["results"], doc["history"] = res, hist
        doc.setdefault("version", 1)
        doc["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _atomic_write(duty_path, json.dumps(doc, ensure_ascii=False, default=str))
        still: set = set()
        CM._collect_fps(res, still)
        CM._collect_fps(hist, still)

    with _LOCK, _FileLock(pass_path):
        store = _load_store(pass_path, "the end-effect passport store")
        for fp in previous.get("passport_fps") or []:
            if fp not in fps:
                store.pop(fp, None)
        if manifest is not None:
            for fp in orphan_fps - set(fps) - still:
                store.pop(fp, None)                   # named only by rows of a closed / vanished die
        for fp in fps:
            store[fp] = passports[fp]
        _atomic_write(pass_path, json.dumps(store, ensure_ascii=False, indent=1))

    access_dies = sorted(d for d, v in dies.items() if isinstance(v, dict) and isinstance(v.get("access"), dict)
                         and v["access"].get("visibility") == MA.VIS_PUBLIC)
    with MA._DA_LOCK:                                   # shared with the API's writer (flock across processes)
        acc_path = MA.die_access_file()
        store = _load_store(acc_path, "die_access.json")     # corrupt -> MirrorError, never overwritten
        before = json.dumps(store, sort_keys=True)
        gone = (set(previous.get("access_dies") or []) | set(mirrored)) - set(access_dies)
        if manifest is not None:
            gone |= _foreign_dies(root, manifest, keep, set(store), hmac_only=True) - set(access_dies)
        for die in gone:
            store.pop(die, None)
        for die in access_dies:
            store[die] = {"visibility": MA.VIS_PUBLIC, "clients": []}
        if json.dumps(store, sort_keys=True) != before:
            MA._save_die_access(store)
    return {"id": extracts.get("id"), "duty_dies": duty_dies, "passport_fps": fps, "access_dies": access_dies,
            "ts": round(time.time(), 1)}


# ── US backups: the mirrored library is restorable from EU, so it is not backed up ─────────────────────────────────

def _restic_escape(path: str) -> str:
    return "".join("\\" + c if c in "*?[\\" else c for c in path)


def backup_exclude_lines(state: Dict[str, Any], root: Path) -> List[str]:
    """One restic exclude pattern per file the mirror installed (absolute path under ``root``).  Everything else under the
    shared tree - a closed motor that lives on US, projects, production, directory, legal - stays in the US backup."""
    root = Path(root)
    return [_restic_escape((root / rel).as_posix()) for rel in sorted(state.get("files") or {}) if safe_rel(rel)]


def write_backup_excludes(out: Path, state: Optional[Dict[str, Any]] = None, root: Optional[Path] = None) -> int:
    """Write the exclude file ``motres-backup-us.service`` passes to restic.  -> the number of patterns."""
    lines = backup_exclude_lines(state if state is not None else load_state(), root or shared_dir())
    header = "# generated by regions/catalog_mirror.py backup-exclude: files the mirror installed\n"
    _atomic_write(Path(out), header + "".join(ln + "\n" for ln in lines))
    return len(lines)


# ── command line ─────────────────────────────────────────────────────────────

def _pull(init: bool, prune: bool) -> int:
    from motor_ai_sim.regions import peer as P
    if not RC.peer_configured() or RC.local_region() != "us":
        print("catalog mirror: this server is not a US server in multi-region mode", file=sys.stderr)
        return EXIT_MODE
    manifest = P.get("/catalog/manifest", timeout=EXTRACT_TIMEOUT_S)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        print("catalog mirror: EU did not answer the manifest request", file=sys.stderr)
        return EXIT_PEER
    extracts = P.get("/catalog/extracts", timeout=EXTRACT_TIMEOUT_S)
    wd = work_dir()
    root = shared_dir()
    state = load_state()
    try:
        plan = make_plan(manifest, root, state, init=init, prune=prune)
    except MirrorError as exc:
        print(f"catalog mirror: {exc}", file=sys.stderr)
        return EXIT_FAILED
    wd.mkdir(parents=True, exist_ok=True)
    _atomic_write(wd / "manifest.json", json.dumps(manifest))
    _atomic_write(wd / "extracts.json", json.dumps(extracts if isinstance(extracts, dict) else {}))
    _atomic_write(wd / "plan.json", json.dumps(plan, indent=1))
    _atomic_write(wd / "fetch.txt", fetch_list_text(plan))
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in plan.items()
                      if k in ("root_id", "refuse", "fetch", "delete", "edits", "collisions", "leftovers", "dropped_paths")}))
    for e in (plan["edits"] + plan["collisions"])[:20]:
        print(f"  {e['path']}: {e['why']}", file=sys.stderr)
    if extracts is None:
        print("catalog mirror: EU did not answer the extracts request; files only this round", file=sys.stderr)
    return int((plan.get("refuse") or {}).get("code") or EXIT_OK)


def _apply() -> int:
    wd = work_dir()
    try:
        plan = json.loads((wd / "plan.json").read_text(encoding="utf-8"))
        manifest = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
        extracts = json.loads((wd / "extracts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"catalog mirror: no plan to apply ({type(exc).__name__}); run plan first", file=sys.stderr)
        return EXIT_PEER
    if plan.get("refuse"):
        print("catalog mirror: the plan was refused; nothing applied", file=sys.stderr)
        return int(plan["refuse"].get("code") or EXIT_EDITS)
    try:
        rep = apply_plan(manifest, extracts or None, plan, shared_dir(), load_state(),
                         prune_leftovers=bool(plan.get("prune_files")), prune_stores=bool(plan.get("prune_stores")))
    except MirrorError as exc:
        print(f"catalog mirror: {exc}", file=sys.stderr)
        return EXIT_FAILED
    print(json.dumps(rep))
    if rep["failed"]:
        print(f"catalog mirror: {len(rep['failed'])} file(s) that must go could not be removed; will retry", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_VERIFY if rep["bad"] else EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="catalog_mirror", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan", help="pull the manifest + extracts from EU, write plan.json and fetch.txt")
    pl.add_argument("--init", action="store_true", help="first run: adopt the copy that is already on US")
    pl.add_argument("--prune-leftovers", action="store_true", help="also remove files of dies EU does not mirror")
    sub.add_parser("apply", help="delete, verify, merge the extracts, record the state")
    sub.add_parser("status", help="print the state file summary")
    be = sub.add_parser("backup-exclude", help="write the restic exclude file for the mirrored library (US backups)")
    be.add_argument("--out", default=None, help="default: <mirror work dir>/restic-exclude-mirrored.txt")
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        return _pull(a.init, a.prune_leftovers)
    if a.cmd == "apply":
        return _apply()
    if a.cmd == "backup-exclude":
        out = Path(a.out) if a.out else work_dir() / "restic-exclude-mirrored.txt"
        print(f"{write_backup_excludes(out)} patterns -> {out}")
        return EXIT_OK
    st = load_state()
    print(json.dumps({"initialised": st.get("initialised"), "files": len(st.get("files") or {}),
                      "root_id": st.get("root_id"), "ts": st.get("ts"),
                      "extracts": {k: (len(v) if isinstance(v, list) else v) for k, v in (st.get("extracts") or {}).items()}}))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
