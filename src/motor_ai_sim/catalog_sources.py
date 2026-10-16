"""ONE precedence rule for every catalog kind: dies, materials, devices, bearings.

The rule (docs/OPEN_PRIVATE_DATA.md, "Precedence"):

1. Every catalog item has an ID (die name, ``<category>/<material>``, device
   part number, bearing name) and comes from a named SOURCE (``open``,
   ``private``, ``shared``, ``public``, ``global``…).
2. An ID must be defined by exactly ONE source.  There is no silent "this
   source wins": the same ID in two sources is a :class:`CatalogClash`, a loud
   error — the item cannot be resolved and a calculation that asks for it
   fails with the clash named.
3. The only way through a clash is an explicit OVERRIDE RECORD in
   ``<config dir>/catalog_overrides.yaml``::

       overrides:
         - {kind: devices, id: IMZA65R020M2H, source: private,
            by: vadim@…, reason: "corrected Rth from the vendor"}

   It names the kind, the ID and the source to use.  Anything else in the
   file is ignored with a warning; an unreadable file counts as NO overrides
   (fail closed: the clash stays an error).
4. What a calculation used is recorded as a :class:`CatalogRef` — kind, ID,
   source and REVISION (sha256 of the item's content) — in the result's
   provenance (``catalog_refs``), so a same-named item from another source can
   never silently swap the engineering data behind a stored number.

Workspace copies (a user's copy-on-write of a catalog die) are the user's own
edit, not a catalog source; their refs say ``source: workspace``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

log = logging.getLogger(__name__)

KIND_DIES = "dies"
KIND_MATERIALS = "materials"
KIND_DEVICES = "devices"
KIND_BEARINGS = "bearings"
KINDS = (KIND_DIES, KIND_MATERIALS, KIND_DEVICES, KIND_BEARINGS)

OVERRIDES_NAME = "catalog_overrides.yaml"


class CatalogClash(LookupError):
    """One ID defined by two or more sources and no override record."""

    def __init__(self, kind: str, ident: str, sources: Sequence[str]):
        self.kind, self.ident, self.sources = kind, ident, list(sources)
        super().__init__(
            f"{kind} '{ident}' is defined by more than one source "
            f"({', '.join(self.sources)}) and no override record picks one — "
            f"remove one copy or add {{kind: {kind}, id: {ident}, source: …}} "
            f"to {OVERRIDES_NAME}")


@dataclass(frozen=True)
class CatalogRef:
    kind: str
    id: str
    source: str
    revision: Optional[str]
    override: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── overrides ────────────────────────────────────────────────────────────────

def _state_dir() -> Path:
    from motor_ai_sim import config as _config
    return Path(str(_config.DEFAULT_CONFIG_PATH)).parent


def overrides_file() -> Path:
    raw = os.environ.get("MOTOR_AI_SIM_CATALOG_OVERRIDES", "").strip()
    return Path(raw) if raw else _state_dir() / OVERRIDES_NAME


def load_overrides() -> Dict[Tuple[str, str], str]:
    """``{(kind, id): source}``.  Unreadable / wrong shape → ``{}`` and an
    ERROR log: without a trustworthy record every clash stays an error."""
    p = overrides_file()
    if not p.is_file():
        return {}
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:                                 # noqa: BLE001
        log.error("catalog overrides %s unreadable (%s) — ignoring ALL overrides", p, exc)
        return {}
    rows = doc.get("overrides") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        log.error("catalog overrides %s: no 'overrides' list — ignoring the file", p)
        return {}
    out: Dict[Tuple[str, str], str] = {}
    for r in rows:
        if (isinstance(r, dict) and r.get("kind") in KINDS
                and isinstance(r.get("id"), str) and isinstance(r.get("source"), str)):
            out[(r["kind"], r["id"])] = r["source"]
        else:
            log.warning("catalog overrides: ignoring malformed row %r", r)
    return out


# ── the rule ─────────────────────────────────────────────────────────────────

def resolve(kind: str, ident: str, candidates: Sequence[Tuple[str, Any]],
            overrides: Optional[Dict[Tuple[str, str], str]] = None
            ) -> Optional[Tuple[str, Any, bool]]:
    """``(source, payload, overridden)`` for one ID, ``None`` when no source
    has it; raises :class:`CatalogClash` for two or more without an override."""
    cands = list(candidates)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0][0], cands[0][1], False
    ov = (overrides if overrides is not None else load_overrides()).get((kind, str(ident)))
    if ov is not None:
        for src, payload in cands:
            if src == ov:
                return src, payload, True
    raise CatalogClash(kind, str(ident), [s for s, _ in cands])


def merge(kind: str, sources: Iterable[Tuple[str, Dict[str, Any]]]
          ) -> Tuple[Dict[str, Tuple[str, Any, bool]], Dict[str, CatalogClash]]:
    """Apply :func:`resolve` to whole listings ``[(source, {id: payload})]``.

    Returns ``(resolved, clashes)`` — a clashing ID is in ``clashes`` only,
    never in ``resolved``."""
    by_id: Dict[str, List[Tuple[str, Any]]] = {}
    for src, items in sources:
        for ident, payload in items.items():
            by_id.setdefault(str(ident), []).append((src, payload))
    ov = load_overrides()
    ok: Dict[str, Tuple[str, Any, bool]] = {}
    bad: Dict[str, CatalogClash] = {}
    for ident, cands in by_id.items():
        try:
            r = resolve(kind, ident, cands, ov)
        except CatalogClash as exc:
            bad[ident] = exc
            continue
        if r is not None:
            ok[ident] = r
    return ok, bad


# ── revisions ────────────────────────────────────────────────────────────────

_FILE_REV: Dict[str, Tuple[float, int, str]] = {}


def file_revision(path: Path) -> Optional[str]:
    """sha256 (first 16 hex) of a file's bytes; cached on (mtime, size)."""
    try:
        st = path.stat()
    except OSError:
        return None
    hit = _FILE_REV.get(str(path))
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    h = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    _FILE_REV[str(path)] = (st.st_mtime, st.st_size, h)
    return h


def object_revision(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def dir_revision(d: Path) -> Optional[str]:
    """Revision of a die: the die.yaml plus every top-level configuration."""
    try:
        files = sorted(p for p in d.glob("*.yaml") if p.is_file())
    except OSError:
        return None
    if not files:
        return None
    h = hashlib.sha256()
    for p in files:
        h.update(p.name.encode() + b"\0" + (file_revision(p) or "").encode() + b"\n")
    return h.hexdigest()[:16]


# ── refs per kind ────────────────────────────────────────────────────────────

def die_ref(name: str, die_dir: Optional[Path] = None) -> Dict[str, Any]:
    from motor_ai_sim import data_sources as DS
    e = DS.scan().get(str(name))
    if e and e.get("clash"):
        return {"kind": KIND_DIES, "id": name, "source": None, "revision": None,
                "error": e.get("error")}
    if e and (die_dir is None or Path(die_dir) == e["dir"]):
        return CatalogRef(KIND_DIES, name, e["source"], dir_revision(e["dir"]),
                          bool(e.get("override"))).as_dict()
    if die_dir is not None:
        return CatalogRef(KIND_DIES, name, "workspace", dir_revision(Path(die_dir))).as_dict()
    return {"kind": KIND_DIES, "id": name, "source": None, "revision": None,
            "error": "not found"}


def material_ref(name: str) -> Dict[str, Any]:
    from motor_ai_sim import materials as M
    lib = M._load() or {}
    src_file = "shared" if M._lib_path() != M._DEFAULT_LIB_PATH else "public"
    for cat, group in lib.items():
        if not isinstance(group, dict) or name not in group:
            continue
        ident = f"{cat}/{name}"
        try:
            from motor_ai_sim import materials_store
            g = materials_store.global_material(cat, name)
        except Exception:                                    # noqa: BLE001
            g = None
        if g is not None:
            # An admin edit of a built-in in the global layer IS the explicit
            # override record (made by an admin, stored with updatedBy).
            return CatalogRef(KIND_MATERIALS, ident, "global", object_revision(g), True).as_dict()
        return CatalogRef(KIND_MATERIALS, ident, src_file, object_revision(group[name])).as_dict()
    return {"kind": KIND_MATERIALS, "id": name, "source": None, "revision": None,
            "error": "not in the materials library"}


def device_ref(part: str) -> Dict[str, Any]:
    from motor_ai_sim.inverter import devices as D
    try:
        src, p, ov = D.resolve_card(part)
    except CatalogClash as exc:
        return {"kind": KIND_DEVICES, "id": part, "source": None, "revision": None,
                "error": str(exc)}
    if p is None:
        return {"kind": KIND_DEVICES, "id": part, "source": None, "revision": None,
                "error": "no card"}
    return CatalogRef(KIND_DEVICES, part, src, file_revision(p), ov).as_dict()


def bearing_ref(name: str) -> Dict[str, Any]:
    from motor_ai_sim import bearings as B
    lib = B._load() or {}
    src_file = "shared" if B._lib_path() != B._DEFAULT_LIB_PATH else "public"
    for group in ("bearings", "lubricants"):
        raw = (lib.get(group) or {}).get(name)
        if raw is not None:
            return CatalogRef(KIND_BEARINGS, f"{group}/{name}", src_file,
                              object_revision(raw)).as_dict()
    return {"kind": KIND_BEARINGS, "id": name, "source": None, "revision": None,
            "error": "not in the bearings library"}


def _strings(d: Any) -> List[str]:
    if isinstance(d, dict):
        return [v.strip() for v in d.values() if isinstance(v, str) and v.strip()]
    return []


def refs_for_config(cfg: Dict[str, Any], die: Optional[str] = None,
                    die_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every catalog item this machine configuration uses, as refs."""
    out: List[Dict[str, Any]] = []
    if die:
        out.append(die_ref(die, die_dir))
    for m in sorted(set(_strings(cfg.get("materials")))):
        out.append(material_ref(m))
    ctl = cfg.get("controller")
    if isinstance(ctl, dict) and isinstance(ctl.get("device"), str) and ctl["device"].strip():
        out.append(device_ref(ctl["device"].strip()))
    brg = cfg.get("bearings")
    names = set(_strings(brg))
    if isinstance(brg, dict):
        for v in brg.values():
            names.update(_strings(v))
    for b in sorted(names):
        out.append(bearing_ref(b))
    return out
