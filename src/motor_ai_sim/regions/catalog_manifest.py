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

OPEN-ONLY (owner 2026-11-09): the US server mirrors ONLY what is open in the public catalog.  OPEN = (a) the anonymous
exhibit (``exhibit.die_is_client_ready`` against ``motor_catalog.json``, as ``routes/family.tree`` does; if that file is
missing the result says ``exhibit_source: missing`` and only (b)+(c) apply), (b) die access ``public``, (c) configurations
whose ``file_visibility`` is ``public``.  A die linked to a development project is excluded unless its die access is
``public``.  A die is mirrored when open by (a) or (b) or with at least one ``public`` configuration.  Of such a die:
``die.yaml``; every configuration that is ``public`` (or ``inherit`` while the die is open by (a) or (b)) together with its ``runs/<cfg>/`` result files and ``<cfg>.*``
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


def _is_dir(p: Path) -> bool:
    """``Path.is_dir`` that never raises (a root-owned unreadable entry reads as "not a directory": skipped, not a crash)."""
    try:
        return p.is_dir()
    except OSError:
        return False


def _is_file(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:
        return False


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink()
    except OSError:
        return True                                         # cannot tell: treat as unsafe, skip


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
    if DS._customer_reason(doc, "die.yaml"):
        return "marked as customer data"                    # neutral text: never the customer's name
    reg = str(doc.get("home_region") or "").strip().lower()
    if reg and reg != "eu":
        return f"pinned to region {reg}"
    return None


# ── open-only rule ───────────────────────────────────────────────────────────

def _cfg_open(cfg_file: Path, die_open: bool) -> bool:
    """A configuration is mirrored when it is ``public``, or ``inherit`` while the die is open (public die access or
    anonymous exhibit); ``private`` never (an unreadable file reads as ``private``: ``file_visibility`` fails closed)."""
    from motor_ai_sim import motor_access as MA
    st = MA.file_visibility(cfg_file)
    return st == MA.CFG_PUBLIC or (st == MA.CFG_INHERIT and die_open)


def _die_yaml_geometry(die_dir: Path) -> Dict[str, Any]:
    try:
        doc = yaml.safe_load((die_dir / "die.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return {}
    g = doc.get("geometry") if isinstance(doc, dict) else None
    return g if isinstance(g, dict) else {}


def open_configs(die_dir: Path, exhibit: Optional[list] = None) -> Tuple[bool, Dict[str, bool]]:
    """``(die_open, {cfg: mirrored})`` for one die folder.  ``die_open`` = die access ``public`` OR the die is in the
    anonymous exhibit (``exhibit`` = the passported geometries of ``motor_catalog.json``, None = catalog not found)."""
    from motor_ai_sim import exhibit as EX
    from motor_ai_sim import motor_access as MA
    pub = MA.get_die_access(die_dir.name).get("visibility") == MA.VIS_PUBLIC
    shown = bool(exhibit) and EX.die_is_client_ready(_die_yaml_geometry(die_dir), exhibit)
    try:
        files = sorted(f for f in die_dir.glob("*.yaml") if _is_file(f) and f.name != "die.yaml" and not _skip(f.name))
    except OSError:
        files = []
    return pub or shown, {f.stem: _cfg_open(f, pub or shown) for f in files}


def die_open_reason(die_dir: Path, exhibit: Optional[list] = None) -> Optional[str]:
    """None when the die is mirrored; else why not."""
    is_open, cfgs = open_configs(die_dir, exhibit)
    if is_open or any(cfgs.values()):
        return None
    return "not open to the public catalog (not in the public exhibit, die visibility not public, no public configuration)"


def linked_dies(root: Path) -> Tuple[set, int]:
    """Dies linked to a development project (``<root>/projects/<id>/project.json`` ``linked_motors``) and the number of
    project files that could not be read (the caller then fails closed)."""
    dies: set = set()
    bad = 0
    pr = root / "projects"
    if not _is_dir(pr):
        return dies, bad
    try:
        kids = sorted(pr.iterdir())
    except OSError:
        return dies, 1
    for k in kids:
        f = k / "project.json"
        if not _is_dir(k) or not _is_file(f):
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            bad += 1
            continue
        for m in (doc.get("linked_motors") or []) if isinstance(doc, dict) else []:
            if isinstance(m, dict) and m.get("die"):
                dies.add(str(m["die"]))
    return dies, bad


def _owner_cfg(name: str, cfgs: Dict[str, bool]) -> Optional[str]:
    """The configuration a name belongs to: its exact stem, else the longest ``<cfg>.`` prefix (``L12.thumb.png``)."""
    if name in cfgs:
        return name
    hits = [c for c in cfgs if name.startswith(c + ".")]
    return max(hits, key=len) if hits else None


def _walk(base: Path, rel: str, acc: _Acc) -> None:
    """Plain recursive walk (a subtree that is already attributed, or a library dir that is not keyed per die)."""
    for p in _iter(base, acc, rel):
        if _skip(p.name) or _is_link(p):
            continue
        r = f"{rel}/{p.name}" if rel else p.name
        if _is_dir(p):
            _walk(p, r, acc)
        elif _is_file(p):
            acc.add(p, r)


def _take(p: Path, r: str, owner: Optional[str], cfgs: Dict[str, bool], acc: _Acc, what: str = "") -> None:
    mark = "/" if _is_dir(p) else ""
    if owner is None:
        acc.leave_out(r + mark, f"{what}cannot be attributed to a configuration")
    elif not cfgs.get(owner):
        acc.leave_out(r + mark, f"{what}belongs to configuration {owner}, which is not public")
    elif _is_dir(p):
        _walk(p, r, acc)
    elif _is_file(p):
        acc.add(p, r)


def _mirror_die(d: Path, cfgs: Dict[str, bool], acc: _Acc) -> None:
    rel0 = f"dies/{d.name}"
    for p in _iter(d, acc, rel0):
        if _skip(p.name) or _is_link(p):
            continue
        r = f"{rel0}/{p.name}"
        if p.name == "die.yaml" and _is_file(p):
            acc.add(p, r)
        elif _is_dir(p) and p.name == "runs":                # runs/<cfg>/... : result sidecars of one configuration
            for q in _iter(p, acc, r):
                if _skip(q.name) or _is_link(q):
                    continue
                _take(q, f"{r}/{q.name}", q.name if _is_dir(q) and q.name in cfgs else None, cfgs, acc)
        else:
            _take(p, r, _owner_cfg(p.stem if p.suffix == ".yaml" and _is_file(p) else p.name, cfgs), cfgs, acc)


def _mirror_keyed(top: Path, acc: _Acc, mirrored: Dict[str, Dict[str, bool]], excluded: set) -> None:
    """``passports/`` and ``cad/``: ``<die>/<cfg>[.json|/...]`` follow the open-only rule of the die in ``dies/``."""
    kind = top.name
    for dd in _iter(top, acc, kind):
        if _skip(dd.name) or _is_link(dd):
            continue
        rel0 = f"{kind}/{dd.name}"
        if not _is_dir(dd):
            acc.leave_out(rel0, f"{kind}: not keyed by die")
            continue
        cfgs = mirrored.get(dd.name)
        if cfgs is None:
            acc.leave_out(rel0 + "/", f"{kind}: die is not mirrored" if dd.name in excluded
                          else f"{kind}: die not found in dies/, cannot attribute")
            continue
        for p in _iter(dd, acc, rel0):
            if _skip(p.name) or _is_link(p):
                continue
            _take(p, f"{rel0}/{p.name}", _owner_cfg(p.stem if _is_file(p) else p.name, cfgs), cfgs, acc, f"{kind}: ")


def _mirror_revisions(top: Path, acc: _Acc, mirrored: Dict[str, Dict[str, bool]]) -> None:
    """``motor_revisions/``: one JSON for every lineage (it also carries client and project ids), snapshots per lineage.
    Mirrored only when EVERY revision in it names a mirrored die and configuration; else left out whole (fail closed)."""
    why = ""
    try:
        f = top / "revisions.json"
        doc = json.loads(f.read_text(encoding="utf-8")) if _is_file(f) else {}
        lineages = doc.get("lineages") if isinstance(doc, dict) else None
        if lineages is None and not _is_file(f):
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


def _mirror_dies(dies: Path, acc: _Acc, exhibit: Optional[list], linked: set, links_unreadable: bool
                 ) -> Tuple[Dict[str, Dict[str, bool]], set]:
    """Mirror ``dies/`` -> (``{die: {cfg: mirrored}}`` of the mirrored dies, names of the excluded dies)."""
    from motor_ai_sim import motor_access as MA
    mirrored: Dict[str, Dict[str, bool]] = {}
    excluded: set = set()
    for d in _iter(dies, acc, "dies"):
        if not _is_dir(d) or _skip(d.name) or _is_link(d):
            continue
        why = die_excluded(d)
        if not why:
            pub = MA.get_die_access(d.name).get("visibility") == MA.VIS_PUBLIC
            if d.name in linked and not pub:
                why = "linked to a customer project"          # fail closed: only an explicit public die access reopens it
            elif links_unreadable and not pub:
                why = "project links could not be read"
        why = why or die_open_reason(d, exhibit)
        if why:
            acc.excluded_dies.append({"die": d.name, "why": why})
            excluded.add(d.name)
            continue
        cfgs = open_configs(d, exhibit)[1]
        mirrored[d.name] = cfgs
        _mirror_die(d, cfgs, acc)
    return mirrored, excluded


def _exhibit_geos(catalog_path: Optional[Path]) -> Tuple[Optional[list], str]:
    """The passported geometries the anonymous exhibit is built from, found the way ``routes/family.tree`` finds them
    (``motor_catalog.json`` in the workspace root).  -> (geos or None, ``"workspace"`` | ``"missing"``)."""
    from motor_ai_sim import exhibit as EX
    try:
        if catalog_path is None:
            from motor_ai_sim.workspace import root as ws_root
            catalog_path = Path(str(ws_root())) / "motor_catalog.json"
        return EX.load_passported_geos(Path(catalog_path)), "workspace"
    except Exception:                                       # noqa: BLE001 - no catalog: fall back to die access + config status
        return None, "missing"


def build(root: Optional[Path] = None, now: Optional[float] = None, catalog_path: Optional[Path] = None) -> Dict[str, Any]:
    """The manifest (file hashes + a SUMMARY of the per-die extracts).  ``build_all`` also returns the extracts."""
    return build_all(root, now, catalog_path)[0]


def build_all(root: Optional[Path] = None, now: Optional[float] = None, catalog_path: Optional[Path] = None,
              access: Optional[Dict[str, Dict[str, Any]]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(manifest, extracts)`` from one walk of the shared tree.  ``access`` = ``{die: die_access entry}`` (default: the
    server's ``die_access.json`` through ``motor_access``)."""
    if root is None:
        from motor_ai_sim.workspace import shared_root
        root = Path(str(shared_root()))
    root = Path(root)
    acc = _Acc()
    exhibit, exhibit_source = _exhibit_geos(catalog_path)
    mirrored: Dict[str, Dict[str, bool]] = {}
    if _is_dir(root):
        excluded: set = set()
        if _is_dir(root / "dies"):
            linked, bad = linked_dies(root)
            mirrored, excluded = _mirror_dies(root / "dies", acc, exhibit, linked, bool(bad))
        for p in _iter(root, acc, ""):
            if _skip(p.name) or _is_link(p):
                continue
            if _is_file(p) and p.name in LIBRARY_FILES:
                acc.add(p, p.name)
            elif _is_dir(p) and p.name in ("passports", "cad"):
                _mirror_keyed(p, acc, mirrored, excluded)
            elif _is_dir(p) and p.name == "motor_revisions":
                _mirror_revisions(p, acc, mirrored)
            elif _is_dir(p) and p.name in LIBRARY_DIRS and p.name != "dies":
                _walk(p, p.name, acc)
    files = acc.files
    extracts = build_extracts(root, mirrored, files, access=access)
    root_id = hashlib.sha256("\n".join(f"{k} {v['sha256']}" for k, v in sorted(files.items())).encode()).hexdigest()
    manifest = {"root_id": root_id, "files": files, "count": len(files), "bytes": sum(v["bytes"] for v in files.values()),
                "excluded_dies": acc.excluded_dies, "excluded_files": acc.excluded_files,
                "exhibit_source": exhibit_source,
                "skipped_unreadable": acc.skipped_count + extracts["counts"]["unreadable"],
                "skipped_unreadable_paths": acc.skipped,
                "extracts": {"id": extracts["id"], "dies": sorted(extracts["dies"]),
                             "passports": len(extracts["passports"]), "counts": extracts["counts"]},
                "ts": round(time.time() if now is None else now, 1)}
    return manifest, extracts


# ── per-die extracts of the PARTIAL shared files ─────────────────────────────
# ``<shared>/.duty_results.json`` (every die's solver rows), ``<shared>/end_effect_passports.json`` (keyed by geometry
# fingerprint) and the server's ``die_access.json`` (every die's visibility) are ONE file for the whole library, so they
# cannot be copied by hash.  EU cuts out what belongs to the mirrored dies; US merges it into its own stores
# (``catalog_mirror.merge_extracts``) and removes what it merged earlier for a die that is no longer mirrored.
# Fail closed: a row that cannot be attributed to a mirrored die and configuration is left out and COUNTED, never sent.

EXTRACT_VERSION = 1
#: Keys that carry a machine's geometry fingerprint in a result row / a passport file.
FP_KEYS = frozenset({"geometry_fingerprint", "geo_fingerprint"})
DUTY_STORE = ".duty_results.json"
END_EFFECT_STORE = "end_effect_passports.json"


def _collect_fps(value: Any, out: set, depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if k in FP_KEYS and isinstance(v, str) and v:
                out.add(v)
            else:
                _collect_fps(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _collect_fps(v, out, depth + 1)


def _read_json(p: Path) -> Tuple[Any, bool]:
    """-> ``(document, unreadable)``.  A missing file is an empty document, not an error."""
    try:
        return json.loads(p.read_text(encoding="utf-8")), False
    except FileNotFoundError:
        return None, False
    except (OSError, ValueError, UnicodeDecodeError):
        return None, True


def _cut_block(block: Any, mirrored: Dict[str, Dict[str, bool]], counts: Dict[str, int],
               fps: set) -> Dict[str, Dict[str, Any]]:
    """``{die: {cfg: ...}}`` -> the same for mirrored dies and configurations only (every other entry is counted)."""
    out: Dict[str, Dict[str, Any]] = {}
    if block is None:
        return out
    if not isinstance(block, dict):
        counts["unattributed"] += 1
        return out
    for die, cfgs in block.items():
        if not isinstance(cfgs, dict):
            counts["unattributed"] += 1
            continue
        m = mirrored.get(str(die))
        for cfg, rows in cfgs.items():
            if m is None:
                counts["closed"] += 1                       # a die that is not mirrored: closed by design
            elif str(cfg) not in m:
                counts["unattributed"] += 1                 # mirrored die, unknown configuration (stale row)
            elif not m[str(cfg)]:
                counts["closed"] += 1                       # a configuration that is not public
            else:
                out.setdefault(str(die), {})[str(cfg)] = rows
                counts["mirrored"] += 1
                _collect_fps(rows, fps)
    return out


def build_extracts(root: Path, mirrored: Dict[str, Dict[str, bool]], files: Optional[Dict[str, Any]] = None,
                   access: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The per-die extracts for the mirrored dies ``{die: {cfg: mirrored?}}``.

    ``{v, id, dies: {die: {"duty": {"results": {cfg: ...}, "history": {cfg: ...}}, "access": {...}}}, passports:
    {fingerprint: entry}, counts}``.  ``access`` carries an entry only for a die whose access is ``public`` (a
    ``selected`` die's client e-mails never cross; every other mirrored die takes US's default).  A passport entry is
    carried only when its fingerprint appears in a mirrored configuration's result rows or passport file.
    """
    from motor_ai_sim import motor_access as MA
    counts = {"mirrored": 0, "closed": 0, "unattributed": 0, "unreadable": 0,
              "passports_mirrored": 0, "passports_left_out": 0, "access": 0}
    fps: set = set()
    dies: Dict[str, Dict[str, Any]] = {d: {} for d in mirrored}

    doc, bad = _read_json(Path(root) / DUTY_STORE)
    if bad:
        counts["unreadable"] += 1
        doc = None
    if doc is not None and not isinstance(doc, dict):
        counts["unattributed"] += 1
        doc = None
    doc = doc or {}
    results = _cut_block(doc.get("results"), mirrored, counts, fps)
    history = _cut_block(doc.get("history"), mirrored, {"mirrored": 0, "closed": 0, "unattributed": 0}, fps)
    for die in dies:
        if die in results or die in history:
            dies[die]["duty"] = {"results": results.get(die, {}), "history": history.get(die, {})}

    for rel in (files or {}):                               # fingerprints named by the mirrored configurations' passports
        if rel.startswith("passports/") and rel.endswith(".json"):
            _collect_fps(_read_json(Path(root) / rel)[0], fps)

    store, bad = _read_json(Path(root) / END_EFFECT_STORE)
    if bad:
        counts["unreadable"] += 1
    passports: Dict[str, Any] = {}
    for fp, entry in (store.items() if isinstance(store, dict) else []):
        if isinstance(entry, dict) and str(fp) in fps:
            passports[str(fp)] = entry
            counts["passports_mirrored"] += 1
        else:
            counts["passports_left_out"] += 1

    acc_src = access if access is not None else MA.all_die_access()
    for die in dies:
        e = MA.normalize_die_access(acc_src.get(die))
        if e["visibility"] == MA.VIS_PUBLIC:
            dies[die]["access"] = {"visibility": MA.VIS_PUBLIC, "clients": []}
            counts["access"] += 1
    dies = {d: v for d, v in dies.items() if v}
    body = {"dies": dies, "passports": passports}
    ident = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return {"v": EXTRACT_VERSION, "id": ident, **body, "counts": counts}


def diff(source: Dict[str, Any], mirror: Dict[str, Any]) -> Dict[str, List[str]]:
    """What the mirror must fetch / delete to equal the source (both are ``build()`` results)."""
    s, m = source.get("files") or {}, mirror.get("files") or {}
    return {"fetch": sorted(k for k, v in s.items() if (m.get(k) or {}).get("sha256") != v.get("sha256")),
            "delete": sorted(k for k in m if k not in s)}
