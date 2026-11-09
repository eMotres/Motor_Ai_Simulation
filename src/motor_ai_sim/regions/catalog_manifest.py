"""The manifest of the shared library that EU authors and US mirrors READ-ONLY.

``/srv/motres/shared`` holds two kinds of data (docs/regions/DESIGN.md, "Data map"):

* OUR library — the catalog dies, materials, bearings, wire stock, controller catalog, devices, propellers, reference
  bases, passports, motor revisions, 3-D catalog models.  Authored on EU, replicated to US by hash.
* REGION-OWNED data that only LOOKS shared because it sits in the same tree — ``projects/``, ``production/``,
  ``directory/`` (companies and people), ``legal/`` (signature records), ``motor_tests/``, ``digests/``,
  ``owner_report/``, ``runs/``.  Never replicated: it belongs to the region of the account / company it is about.

The manifest is an ALLOW-LIST (an unknown top-level entry is not replicated: fail closed).  A die is left out when its
``die.yaml`` names a customer (``data_sources._customer_reason``) or carries ``home_region`` other than ``eu`` — a closed
motor built for a client is that client's data and lives in the client's region.

OPEN-ONLY (owner 2026-11-09): the US server mirrors ONLY what is open in the public catalog.  A die is mirrored when its
die-level visibility is ``public`` OR at least one of its configurations is ``public``
(``motor_access.get_die_access`` / ``file_visibility``).  Of such a die: ``die.yaml``; every configuration that is
``public`` (or ``inherit`` while the die is ``public``) together with its ``runs/<cfg>/`` result files and ``<cfg>.*``
assets; a configuration that is ``private`` (or ``inherit`` under a non-public die) is left out with everything that
belongs to it.  A file that cannot be attributed to a configuration is left out (fail closed) and listed in
``excluded_files``.  ``passports/`` and ``cad/`` (keyed ``<die>/<cfg>``) follow the same per-configuration rule;
``motor_revisions/`` (one JSON for all lineages, with client and project ids) is mirrored only while every revision in it
names a mirrored configuration.  Backup / temp files (``*.bak*``, ``*.tmp``, ``*~``, ``*.orig``) are never listed; a
file that cannot be read is skipped and counted (``skipped_unreadable``), never a crash.

``build()`` -> ``{root_id, files: {relpath: {sha256, bytes}}, count, bytes, excluded_dies, excluded_files,
skipped_unreadable, skipped_unreadable_paths, ts}``.  Hashes are cached by (path, size, mtime) so a repeated call costs
one ``stat`` per file.  The US side diffs it against its own copy (``diff()``) and pulls the changed files (stage 2: rsync
over WireGuard driven by this list; nothing is pushed by the app).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

#: Top-level entries of the shared root that ARE our library.
LIBRARY_DIRS = frozenset({"dies", "devices", "propellers", "basis", "passports", "motor_revisions", "cad"})
LIBRARY_FILES = frozenset({"materials_library.yaml", "bearings_library.yaml", "wire_stock.yaml", "fusion_param_map.yaml",
                           "catalog.yaml", "ecb_eurofxref.json"})
#: Region-owned entries (documented; anything not in the allow-list above is excluded anyway).
REGION_OWNED = frozenset({"projects", "production", "directory", "legal", "motor_tests", "digests", "owner_report", "runs"})
MAX_FILES = 50000
#: At most this many excluded / skipped paths are listed in a result (the counts are exact for skipped files).
MAX_REPORTED = 200

_CACHE: Dict[str, Tuple[int, int, str]] = {}
_LOCK = threading.Lock()


def _sha(p: Path) -> str:
    st = p.stat()
    key = str(p)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    d = h.hexdigest()
    with _LOCK:
        _CACHE[key] = (st.st_size, st.st_mtime_ns, d)
    return d


def _skip(name: str) -> bool:
    """Caches, temp files and backups are never part of the library (``L12.yaml.bak-20261030-liner`` included)."""
    return (name.startswith(".") or name == "__pycache__" or name.endswith((".tmp", ".part", "~", ".orig"))
            or ".bak" in name)


class _Acc:
    """Collects the result of one ``build()``."""

    def __init__(self) -> None:
        self.files: Dict[str, Dict[str, Any]] = {}
        self.excluded_dies: List[Dict[str, str]] = []
        self.excluded_files: List[Dict[str, str]] = []
        self.skipped: List[str] = []
        self.skipped_count = 0

    def add(self, p: Path, rel: str) -> None:
        if len(self.files) >= MAX_FILES:
            raise OverflowError("shared library has more files than the manifest allows")
        try:
            st = p.stat()
            self.files[rel] = {"sha256": _sha(p), "bytes": st.st_size}
        except OSError:                                     # PermissionError, vanished file: skip, never crash
            self.files.pop(rel, None)
            self._skipped(rel)

    def _skipped(self, rel: str) -> None:
        self.skipped_count += 1
        if len(self.skipped) < MAX_REPORTED:
            self.skipped.append(rel)

    def leave_out(self, rel: str, why: str) -> None:
        if len(self.excluded_files) < MAX_REPORTED:
            self.excluded_files.append({"path": rel, "why": why})


def _iter(base: Path, acc: _Acc, rel: str) -> List[Path]:
    try:
        return sorted(base.iterdir())
    except OSError:
        acc._skipped(rel + "/")
        return []


def die_excluded(die_dir: Path) -> Optional[str]:
    """Why a catalog die is NOT replicated (customer data / pinned to a region), or None."""
    f = die_dir / "die.yaml"
    try:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return "die.yaml unreadable"
    if not isinstance(doc, dict):
        return "die.yaml malformed"
    from motor_ai_sim import data_sources as DS
    why = DS._customer_reason(doc, "die.yaml")
    if why:
        return why
    reg = str(doc.get("home_region") or "").strip().lower()
    if reg and reg != "eu":
        return f"die.yaml pins it to region {reg}"
    return None


# ── open-only rule ───────────────────────────────────────────────────────────

def _cfg_open(cfg_file: Path, die_public: bool) -> bool:
    """A configuration is mirrored when it is ``public``, or ``inherit`` under a ``public`` die; ``private`` never
    (and an unreadable file reads as ``private``: ``file_visibility`` fails closed)."""
    from motor_ai_sim import motor_access as MA
    st = MA.file_visibility(cfg_file)
    return st == MA.CFG_PUBLIC or (st == MA.CFG_INHERIT and die_public)


def open_configs(die_dir: Path) -> Tuple[bool, Dict[str, bool]]:
    """``(die_public, {cfg: mirrored})`` for one die folder."""
    from motor_ai_sim import motor_access as MA
    pub = MA.get_die_access(die_dir.name).get("visibility") == MA.VIS_PUBLIC
    try:
        files = sorted(f for f in die_dir.glob("*.yaml") if f.is_file() and f.name != "die.yaml" and not _skip(f.name))
    except OSError:
        files = []
    return pub, {f.stem: _cfg_open(f, pub) for f in files}


def die_open_reason(die_dir: Path) -> Optional[str]:
    """None when the die is mirrored; else why not."""
    pub, cfgs = open_configs(die_dir)
    if pub or any(cfgs.values()):
        return None
    return "not open to the public catalog (die visibility is not public and no configuration is public)"


def _owner_cfg(name: str, cfgs: Dict[str, bool]) -> Optional[str]:
    """The configuration a name belongs to: its exact stem, else the longest ``<cfg>.`` prefix (``L12.thumb.png``)."""
    if name in cfgs:
        return name
    hits = [c for c in cfgs if name.startswith(c + ".")]
    return max(hits, key=len) if hits else None


def _walk(base: Path, rel: str, acc: _Acc) -> None:
    """Plain recursive walk (a subtree that is already attributed, or a library dir that is not keyed per die)."""
    for p in _iter(base, acc, rel):
        if _skip(p.name) or p.is_symlink():
            continue
        r = f"{rel}/{p.name}" if rel else p.name
        if p.is_dir():
            _walk(p, r, acc)
        elif p.is_file():
            acc.add(p, r)


def _take(p: Path, r: str, owner: Optional[str], cfgs: Dict[str, bool], acc: _Acc, what: str = "") -> None:
    mark = "/" if p.is_dir() else ""
    if owner is None:
        acc.leave_out(r + mark, f"{what}cannot be attributed to a configuration")
    elif not cfgs.get(owner):
        acc.leave_out(r + mark, f"{what}belongs to configuration {owner}, which is not public")
    elif p.is_dir():
        _walk(p, r, acc)
    elif p.is_file():
        acc.add(p, r)


def _mirror_die(d: Path, cfgs: Dict[str, bool], acc: _Acc) -> None:
    rel0 = f"dies/{d.name}"
    for p in _iter(d, acc, rel0):
        if _skip(p.name) or p.is_symlink():
            continue
        r = f"{rel0}/{p.name}"
        if p.name == "die.yaml" and p.is_file():
            acc.add(p, r)
        elif p.is_dir() and p.name == "runs":                # runs/<cfg>/... : result sidecars of one configuration
            for q in _iter(p, acc, r):
                if _skip(q.name) or q.is_symlink():
                    continue
                _take(q, f"{r}/{q.name}", q.name if q.is_dir() and q.name in cfgs else None, cfgs, acc)
        else:
            _take(p, r, _owner_cfg(p.stem if p.suffix == ".yaml" and p.is_file() else p.name, cfgs), cfgs, acc)


def _mirror_keyed(top: Path, acc: _Acc, mirrored: Dict[str, Dict[str, bool]], excluded: set) -> None:
    """``passports/`` and ``cad/``: ``<die>/<cfg>[.json|/...]`` follow the open-only rule of the die in ``dies/``."""
    kind = top.name
    for dd in _iter(top, acc, kind):
        if _skip(dd.name) or dd.is_symlink():
            continue
        rel0 = f"{kind}/{dd.name}"
        if not dd.is_dir():
            acc.leave_out(rel0, f"{kind}: not keyed by die")
            continue
        cfgs = mirrored.get(dd.name)
        if cfgs is None:
            acc.leave_out(rel0 + "/", f"{kind}: die is not mirrored" if dd.name in excluded
                          else f"{kind}: die not found in dies/, cannot attribute")
            continue
        for p in _iter(dd, acc, rel0):
            if _skip(p.name) or p.is_symlink():
                continue
            _take(p, f"{rel0}/{p.name}", _owner_cfg(p.stem if p.is_file() else p.name, cfgs), cfgs, acc, f"{kind}: ")


def _mirror_revisions(top: Path, acc: _Acc, mirrored: Dict[str, Dict[str, bool]]) -> None:
    """``motor_revisions/``: one JSON for every lineage (it also carries client and project ids), snapshots per lineage.
    Mirrored only when EVERY revision in it names a mirrored die and configuration; else left out whole (fail closed)."""
    why = ""
    try:
        f = top / "revisions.json"
        doc = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
        lineages = doc.get("lineages") if isinstance(doc, dict) else None
        if lineages is None and not f.is_file():
            lineages = {}
        if not isinstance(lineages, dict):
            raise ValueError("no lineages")
        for lin in lineages.values():
            for rev in (lin.get("revisions") or []) if isinstance(lin, dict) else []:
                if not (mirrored.get(str(rev.get("die") or "")) or {}).get(str(rev.get("config") or "")):
                    why = f"revision {rev.get('die')!r}/{rev.get('config')!r} is not a mirrored configuration"
                    break
            if why:
                break
    except (OSError, ValueError, AttributeError, UnicodeDecodeError):
        why = "revisions.json unreadable or malformed"
    if why:
        acc.leave_out("motor_revisions/", why)
    else:
        _walk(top, top.name, acc)


def _mirror_dies(dies: Path, acc: _Acc) -> Tuple[Dict[str, Dict[str, bool]], set]:
    """Mirror ``dies/`` -> (``{die: {cfg: mirrored}}`` of the mirrored dies, names of the excluded dies)."""
    mirrored: Dict[str, Dict[str, bool]] = {}
    excluded: set = set()
    for d in _iter(dies, acc, "dies"):
        if not d.is_dir() or _skip(d.name) or d.is_symlink():
            continue
        why = die_excluded(d) or die_open_reason(d)
        if why:
            acc.excluded_dies.append({"die": d.name, "why": why})
            excluded.add(d.name)
            continue
        cfgs = open_configs(d)[1]
        mirrored[d.name] = cfgs
        _mirror_die(d, cfgs, acc)
    return mirrored, excluded


def build(root: Optional[Path] = None, now: Optional[float] = None) -> Dict[str, Any]:
    if root is None:
        from motor_ai_sim.workspace import shared_root
        root = Path(str(shared_root()))
    root = Path(root)
    acc = _Acc()
    if root.is_dir():
        mirrored: Dict[str, Dict[str, bool]] = {}
        excluded: set = set()
        if (root / "dies").is_dir():
            mirrored, excluded = _mirror_dies(root / "dies", acc)
        for p in _iter(root, acc, ""):
            if _skip(p.name) or p.is_symlink():
                continue
            if p.is_file() and p.name in LIBRARY_FILES:
                acc.add(p, p.name)
            elif p.is_dir() and p.name in ("passports", "cad"):
                _mirror_keyed(p, acc, mirrored, excluded)
            elif p.is_dir() and p.name == "motor_revisions":
                _mirror_revisions(p, acc, mirrored)
            elif p.is_dir() and p.name in LIBRARY_DIRS and p.name != "dies":
                _walk(p, p.name, acc)
    files = acc.files
    root_id = hashlib.sha256("\n".join(f"{k} {v['sha256']}" for k, v in sorted(files.items())).encode()).hexdigest()
    return {"root_id": root_id, "files": files, "count": len(files), "bytes": sum(v["bytes"] for v in files.values()),
            "excluded_dies": acc.excluded_dies, "excluded_files": acc.excluded_files,
            "skipped_unreadable": acc.skipped_count, "skipped_unreadable_paths": acc.skipped,
            "ts": round(time.time() if now is None else now, 1)}


def diff(source: Dict[str, Any], mirror: Dict[str, Any]) -> Dict[str, List[str]]:
    """What the mirror must fetch / delete to equal the source (both are ``build()`` results)."""
    s, m = source.get("files") or {}, mirror.get("files") or {}
    return {"fetch": sorted(k for k, v in s.items() if (m.get(k) or {}).get("sha256") != v.get("sha256")),
            "delete": sorted(k for k in m if k not in s)}
