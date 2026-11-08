"""The manifest of the shared library that EU authors and US mirrors READ-ONLY.

``/srv/motres/shared`` holds two kinds of data (docs/regions/DESIGN.md, "Data map"):

* OUR library — the catalog dies, materials, bearings, wire stock, controller catalog, devices, propellers, reference
  bases, passports, motor revisions, 3-D catalog models.  Authored on EU, replicated to US by hash.
* REGION-OWNED data that only LOOKS shared because it sits in the same tree — ``projects/``, ``production/``,
  ``directory/`` (companies and people), ``legal/`` (signature records), ``motor_tests/``, ``digests/``,
  ``owner_report/``, ``runs/``.  Never replicated: it belongs to the region of the account / company it is about.

The manifest is an ALLOW-LIST (an unknown top-level entry is not replicated: fail closed).  A die is left out when its
``die.yaml`` names a customer (``data_sources._customer_reason``) or carries ``home_region`` other than ``eu`` — a closed
motor built for a client is that client's data and lives in the client's region.  Dot-entries (caches) and
``*.tmp`` / ``*.part`` are skipped.

``build()`` -> ``{root_id, files: {relpath: {sha256, bytes}}, count, bytes, excluded_dies, ts}``.  Hashes are cached by
(path, size, mtime) so a repeated call costs one ``stat`` per file.  The US side diffs it against its own copy
(``diff()``) and pulls the changed files (stage 2: rsync over WireGuard driven by this list; nothing is pushed by the app).
"""
from __future__ import annotations

import hashlib
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
    return name.startswith(".") or name.endswith((".tmp", ".part")) or name == "__pycache__"


def die_excluded(die_dir: Path) -> Optional[str]:
    """Why a catalog die is NOT replicated (customer data / pinned to a region), or None."""
    f = die_dir / "die.yaml"
    try:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
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


def _walk(base: Path, rel: str, out: Dict[str, Dict[str, Any]]) -> None:
    for p in sorted(base.iterdir()):
        if _skip(p.name) or p.is_symlink():
            continue
        r = f"{rel}/{p.name}" if rel else p.name
        if p.is_dir():
            _walk(p, r, out)
        elif p.is_file():
            if len(out) >= MAX_FILES:
                raise OverflowError("shared library has more files than the manifest allows")
            out[r] = {"sha256": _sha(p), "bytes": p.stat().st_size}


def build(root: Optional[Path] = None, now: Optional[float] = None) -> Dict[str, Any]:
    if root is None:
        from motor_ai_sim.workspace import shared_root
        root = Path(str(shared_root()))
    root = Path(root)
    files: Dict[str, Dict[str, Any]] = {}
    excluded: List[Dict[str, str]] = []
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if _skip(p.name) or p.is_symlink():
                continue
            if p.is_file() and p.name in LIBRARY_FILES:
                files[p.name] = {"sha256": _sha(p), "bytes": p.stat().st_size}
            elif p.is_dir() and p.name == "dies":
                for d in sorted(p.iterdir()):
                    if not d.is_dir() or _skip(d.name):
                        continue
                    why = die_excluded(d)
                    if why:
                        excluded.append({"die": d.name, "why": why})
                    else:
                        _walk(d, f"dies/{d.name}", files)
            elif p.is_dir() and p.name in LIBRARY_DIRS:
                _walk(p, p.name, files)
    root_id = hashlib.sha256("\n".join(f"{k} {v['sha256']}" for k, v in sorted(files.items())).encode()).hexdigest()
    return {"root_id": root_id, "files": files, "count": len(files), "bytes": sum(v["bytes"] for v in files.values()),
            "excluded_dies": excluded, "ts": round(time.time() if now is None else now, 1)}


def diff(source: Dict[str, Any], mirror: Dict[str, Any]) -> Dict[str, List[str]]:
    """What the mirror must fetch / delete to equal the source (both are ``build()`` results)."""
    s, m = source.get("files") or {}, mirror.get("files") or {}
    return {"fetch": sorted(k for k, v in s.items() if (m.get(k) or {}).get("sha256") != v.get("sha256")),
            "delete": sorted(k for k in m if k not in s)}
