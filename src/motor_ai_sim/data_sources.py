"""The two SOURCES of catalog dies: OPEN (public repository) and PRIVATE.

Every die — its ``die.yaml``, every configuration, every duty and every result
under ``runs/`` — belongs to exactly ONE source:

* ``open``     lives in the public AGPL repository under ``data/open/dies/``.
               Everybody who clones the repository gets it.
* ``private``  lives in the private data repository (the checkout
               ``MOTOR_AI_SIM_PRIVATE_DATA`` points at) under ``config/dies/``
               — the same relative path the die catalog has always had, which
               is the private repository's convention (PR #41).

The loader reads BOTH and merges them by die name.  A name present in both is a
CLASH: the private copy wins and a loud warning is logged, because the only way
to get there is a half-finished move (or a hand copy) and somebody must look.

A die that is not in the open set is private by default — that is the rule for
every existing real product.  The open set is small and deliberate: a few
made-up demo motors so that the public repository runs, and its tests pass,
with no private data at all.

This module only ANSWERS questions (where does a die live, what would moving
it publish, what blocks the move).  Moving is :mod:`motor_ai_sim.data_publish`.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

log = logging.getLogger(__name__)

SOURCE_OPEN = "open"
SOURCE_PRIVATE = "private"
SOURCES = (SOURCE_OPEN, SOURCE_PRIVATE)

#: Where dies live INSIDE each repository checkout.
OPEN_REL: Tuple[str, ...] = ("data", "open", "dies")
PRIVATE_REL: Tuple[str, ...] = ("config", "dies")

#: The public repository checkout whose ``data/open/dies`` is the open layer.
#: Unset = this source tree (the repository the code runs from).
ENV_OPEN_REPO = "OPEN_DATA_REPO_DIR"
#: Force the open/private layers ON when multi-user layering is off (a fresh
#: public clone does this automatically when ``config/dies`` has no dies).
ENV_SOURCES = "MOTOR_AI_SIM_DATA_SOURCES"

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: ``die.yaml`` markers that forbid publication.  Any one of them is enough.
CONFIDENTIAL_TAGS = frozenset({"customer", "nda", "confidential"})


# ── roots ────────────────────────────────────────────────────────────────────

def open_repo_dir() -> Path:
    raw = os.environ.get(ENV_OPEN_REPO, "").strip()
    return Path(raw).expanduser() if raw else _REPO_ROOT


def private_repo_dir() -> Optional[Path]:
    from motor_ai_sim.private_data import private_root
    return private_root()


def repo_dir(source: str) -> Optional[Path]:
    return open_repo_dir() if source == SOURCE_OPEN else private_repo_dir()


def rel_of(source: str) -> Tuple[str, ...]:
    return OPEN_REL if source == SOURCE_OPEN else PRIVATE_REL


def dies_dir(source: str) -> Optional[Path]:
    """``<checkout>/<rel>`` for a source, or ``None`` when it is not configured."""
    base = repo_dir(source)
    return base.joinpath(*rel_of(source)) if base is not None else None


def open_dies_dir() -> Path:
    return open_repo_dir().joinpath(*OPEN_REL)


def private_dies_dir() -> Optional[Path]:
    return dies_dir(SOURCE_PRIVATE)


def source_dirs() -> List[Tuple[str, Path]]:
    """``[(source, dies_dir)]`` in PRECEDENCE order — private first, so the
    private copy of a clashing name wins."""
    out: List[Tuple[str, Path]] = []
    p = private_dies_dir()
    if p is not None and p.is_dir():
        out.append((SOURCE_PRIVATE, p))
    o = open_dies_dir()
    if o.is_dir():
        out.append((SOURCE_OPEN, o))
    return out


def forced_on() -> bool:
    return os.environ.get(ENV_SOURCES, "").strip().lower() in ("1", "true", "yes", "on")


# ── the merged listing ───────────────────────────────────────────────────────

def _die_names(d: Path) -> List[str]:
    try:
        return sorted(p.name for p in d.iterdir()
                      if p.is_dir() and (p / "die.yaml").is_file())
    except OSError:
        return []


_WARNED: set = set()


def scan() -> Dict[str, Dict[str, Any]]:
    """``{die: {"source", "dir", "shadowed": [source, …]}}`` over both sources.

    A clash (one name in both) resolves to the private copy and is logged at
    WARNING once per process per name — loud, but not once per request.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for src, d in source_dirs():
        for name in _die_names(d):
            if name in out:
                out[name]["shadowed"].append(src)
                key = (name, out[name]["source"], src)
                if key not in _WARNED:
                    _WARNED.add(key)
                    log.warning(
                        "data_sources: die '%s' exists in BOTH the %s and the %s "
                        "data set — the %s copy wins; finish or undo the move "
                        "(%s vs %s)", name, out[name]["source"], src,
                        out[name]["source"], out[name]["dir"], d / name)
                continue
            out[name] = {"source": src, "dir": d / name, "shadowed": []}
    return out


def clashes() -> List[str]:
    return sorted(n for n, e in scan().items() if e["shadowed"])


def source_of_dir(die_dir: Optional[Path]) -> Optional[str]:
    """``open`` / ``private`` when the folder sits directly in a source dir."""
    if die_dir is None:
        return None
    parent = Path(str(die_dir)).parent
    for src in SOURCES:
        d = dies_dir(src)
        if d is None:
            continue
        try:
            if parent.resolve() == d.resolve():
                return src
        except OSError:
            continue
    return None


def die_source(name: str) -> str:
    """Which data set this die belongs to.  Anything not in the open set —
    every existing real product, a workspace die, a shared-catalog die — is
    ``private``: publishing is the deliberate act, never the default."""
    e = scan().get(str(name))
    return e["source"] if e else SOURCE_PRIVATE


def locate(name: str) -> Optional[Path]:
    """The source folder of a die (private first), or ``None``."""
    e = scan().get(str(name))
    return e["dir"] if e else None


# ── what a die contains ──────────────────────────────────────────────────────

def _load_yaml(p: Path) -> Dict[str, Any]:
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return d if isinstance(d, dict) else {}
    except Exception:                                       # noqa: BLE001
        log.warning("data_sources: cannot read %s", p, exc_info=True)
        return {}


def _configs(die_dir: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    return [(p, _load_yaml(p)) for p in sorted(die_dir.glob("*.yaml"))
            if p.name != "die.yaml"]


def referenced_materials(die_dir: Path) -> List[str]:
    names = set()
    for _p, cfg in _configs(die_dir):
        for v in (cfg.get("materials") or {}).values():
            if isinstance(v, str) and v.strip():
                names.add(v.strip())
    dm = _load_yaml(die_dir / "die.yaml").get("materials") or {}
    if isinstance(dm, dict):
        names.update(str(v).strip() for v in dm.values() if isinstance(v, str) and v.strip())
    return sorted(names)


def referenced_devices(die_dir: Path) -> List[str]:
    parts = set()
    for _p, cfg in _configs(die_dir):
        ctl = cfg.get("controller") or {}
        if isinstance(ctl, dict) and isinstance(ctl.get("device"), str) and ctl["device"].strip():
            parts.add(ctl["device"].strip())
    return sorted(parts)


def open_material_names() -> set:
    """Every material name in the PUBLIC library (the open checkout's
    ``config/materials_library.yaml``) — the only ones an open die may use."""
    lib = _load_yaml(open_repo_dir() / "config" / "materials_library.yaml")
    out = set()
    for group in lib.values():
        if isinstance(group, dict):
            out.update(str(k) for k in group)
    return out


def open_device_parts() -> set:
    d = open_repo_dir() / "config" / "devices"
    try:
        return {p.stem for p in d.glob("*.yaml")}
    except OSError:
        return set()


def confidential_reason(die_dir: Path) -> Optional[str]:
    """Why this die may NEVER be published, or ``None``.

    ``die.yaml`` carries ``confidential: customer|nda``, ``nda: true``,
    ``customer: <name>`` or a ``tags`` list with ``customer`` / ``nda``.
    """
    d = _load_yaml(die_dir / "die.yaml")
    conf = str(d.get("confidential") or "").strip().lower()
    if conf and conf not in ("0", "false", "no", "none"):
        return f"die.yaml marks it confidential ({conf})"
    if d.get("nda") is True:
        return "die.yaml marks it as covered by an NDA"
    if str(d.get("customer") or "").strip():
        return "die.yaml names a customer — customer designs are never published"
    tags = d.get("tags") or []
    if isinstance(tags, (list, tuple)):
        hit = sorted(CONFIDENTIAL_TAGS & {str(t).strip().lower() for t in tags})
        if hit:
            return f"die.yaml is tagged {', '.join(hit)}"
    return None


def publish_blockers(die_dir: Path) -> List[Dict[str, str]]:
    """What stops this die from going OPEN: private-only materials and
    devices (each must be published first) and a confidential tag."""
    out: List[Dict[str, str]] = []
    why = confidential_reason(die_dir)
    if why:
        out.append({"kind": "confidential", "name": die_dir.name, "reason": why})
    open_mats = open_material_names()
    for m in referenced_materials(die_dir):
        if m not in open_mats:
            out.append({"kind": "material", "name": m,
                        "reason": "not in the public materials library — publish it first"})
    open_devs = open_device_parts()
    for part in referenced_devices(die_dir):
        if part not in open_devs:
            out.append({"kind": "device", "name": part,
                        "reason": "device card is private — publish it first"})
    return out


def _walk(die_dir: Path) -> Iterable[Path]:
    for p in sorted(die_dir.rglob("*")):
        if p.is_file():
            yield p


def manifest(die_dir: Path) -> Dict[str, Any]:
    """EXACTLY what a move carries, for the confirmation dialog."""
    files = []
    total = 0
    for p in _walk(die_dir):
        sz = p.stat().st_size
        total += sz
        files.append({"path": p.relative_to(die_dir).as_posix(), "bytes": sz})
    die_doc = _load_yaml(die_dir / "die.yaml")
    geo = die_doc.get("geometry") or {}
    configs = []
    results = []
    for p, cfg in _configs(die_dir):
        duties = [x for x in (cfg.get("duties") or []) if isinstance(x, dict)]
        configs.append({"name": p.stem, "role": cfg.get("role"),
                        "duties": [str(x.get("name")) for x in duties]})
        for x in duties:
            if x.get("result") or x.get("summary"):
                results.append(f"{p.stem} / {x.get('name')} (saved result)")
    runs = [f["path"] for f in files if f["path"].startswith("runs/")]
    results += runs
    return {
        "die": die_dir.name,
        "files": files,
        "total_bytes": total,
        "geometry": {k: geo[k] for k in sorted(geo)} if isinstance(geo, dict) else {},
        "configs": configs,
        "materials": referenced_materials(die_dir),
        "devices": referenced_devices(die_dir),
        "results": results,
    }
