"""The two git SOURCES of catalog dies: OPEN (public repository) and PRIVATE.

* ``open``     lives in the public Apache-2.0 repository under ``data/open/dies/``.
* ``private``  lives in the private data repository (the checkout
               ``MOTOR_AI_SIM_PRIVATE_DATA`` points at) under ``config/dies/``.

BOUNDARY: both repositories hold only curated MOTRES reference data — our own
products and validation data.  Customer projects and their results NEVER go to
either repository; they live only in the application storage (workspaces).
A die can be moved between the repositories only when its ``die.yaml``
affirmatively declares a MOTRES owner (``owner_org: MOTRES``) and nothing in
it is tagged customer.

PRECEDENCE: one rule for every catalog kind (:mod:`motor_ai_sim.catalog_sources`):
a die name in two sources is a CLASH — a loud error, the die does not resolve —
unless an explicit override record names the source to use.  On a layered
server the shared catalog takes part in the same rule.

VALIDATION fails closed: anything unreadable, of the wrong shape, outside the
file whitelist, too big, a symlink, or containing a secret, an e-mail address
or a customer tag is a BLOCKER — the move is refused with the error listed.

This module only ANSWERS questions (where does a die live, what would moving
it carry, what blocks the move).  Moving is :mod:`motor_ai_sim.data_publish`.
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

log = logging.getLogger(__name__)

SOURCE_OPEN = "open"
SOURCE_PRIVATE = "private"
SOURCE_SHARED = "shared"
SOURCES = (SOURCE_OPEN, SOURCE_PRIVATE)

#: Where dies live INSIDE each repository checkout.
OPEN_REL: Tuple[str, ...] = ("data", "open", "dies")
PRIVATE_REL: Tuple[str, ...] = ("config", "dies")

ENV_OPEN_REPO = "OPEN_DATA_REPO_DIR"
ENV_SOURCES = "MOTOR_AI_SIM_DATA_SOURCES"
#: Comma-separated organisations whose reference data may enter the repos.
ENV_REFERENCE_ORGS = "DATA_REFERENCE_ORGS"

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Tags that forbid PUBLICATION (open).  ``customer`` forbids both repos.
CONFIDENTIAL_TAGS = frozenset({"customer", "nda", "confidential"})
CUSTOMER_TAGS = frozenset({"customer"})


class DataError(RuntimeError):
    """A die file that cannot be read or has the wrong shape."""


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
    base = repo_dir(source)
    return base.joinpath(*rel_of(source)) if base is not None else None


def open_dies_dir() -> Path:
    return open_repo_dir().joinpath(*OPEN_REL)


def private_dies_dir() -> Optional[Path]:
    return dies_dir(SOURCE_PRIVATE)


def forced_on() -> bool:
    return os.environ.get(ENV_SOURCES, "").strip().lower() in ("1", "true", "yes", "on")


def _shared_dies_dir() -> Optional[Path]:
    """The shared catalog joins the rule only where it coexists with the two
    source layers: layered server AND sources switched on."""
    if not forced_on():
        return None
    try:
        from motor_ai_sim import workspace as W
        if not W.layering():
            return None
        return Path(str(W.shared_root())) / "dies"
    except Exception:                                        # noqa: BLE001
        return None


def source_dirs() -> List[Tuple[str, Path]]:
    """``[(source, dies_dir)]`` of the git sources that exist."""
    out: List[Tuple[str, Path]] = []
    p = private_dies_dir()
    if p is not None and p.is_dir():
        out.append((SOURCE_PRIVATE, p))
    o = open_dies_dir()
    if o.is_dir():
        out.append((SOURCE_OPEN, o))
    return out


# ── the merged listing (catalog precedence rule) ─────────────────────────────

def _die_names(d: Path) -> List[str]:
    try:
        return sorted(p.name for p in d.iterdir()
                      if p.is_dir() and (p / "die.yaml").is_file())
    except OSError:
        return []


_WARNED: set = set()


def scan() -> Dict[str, Dict[str, Any]]:
    """``{die: {"source", "dir", "shadowed", "clash", "override", "error"}}``.

    A name in two sources without an override record is a CLASH: ``source``
    and ``dir`` are ``None`` (the die does not resolve anywhere) and the error
    is logged at ERROR once per process per name.
    """
    from motor_ai_sim import catalog_sources as CS
    listings: List[Tuple[str, Dict[str, Path]]] = []
    sh = _shared_dies_dir()
    if sh is not None and sh.is_dir():
        listings.append((SOURCE_SHARED, {n: sh / n for n in _die_names(sh)}))
    for src, d in source_dirs():
        listings.append((src, {n: d / n for n in _die_names(d)}))
    ok, bad = CS.merge(CS.KIND_DIES, listings)
    out: Dict[str, Dict[str, Any]] = {}
    for name, (src, d, ov) in ok.items():
        others = [s for s, items in listings if name in items and s != src]
        out[name] = {"source": src, "dir": d, "shadowed": others, "clash": False,
                     "override": ov, "error": None}
    for name, exc in bad.items():
        if name not in _WARNED:
            _WARNED.add(name)
            log.error("data_sources: %s", exc)
        out[name] = {"source": None, "dir": None, "shadowed": list(exc.sources),
                     "clash": True, "override": False, "error": str(exc)}
    return dict(sorted(out.items()))


def clashes() -> List[str]:
    return sorted(n for n, e in scan().items() if e["clash"])


def resolves_to(name: str, source: str) -> bool:
    """Does ``name`` resolve to ``source`` under the rule?  The workspace
    layer reader asks this before it accepts a die from a catalog layer."""
    e = scan().get(str(name))
    return bool(e and not e["clash"] and e["source"] == source)


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
    """``open`` only for a die that RESOLVES to the open set; everything else
    (private, shared, workspace, clash, unknown) is private."""
    e = scan().get(str(name))
    return SOURCE_OPEN if e and e["source"] == SOURCE_OPEN else SOURCE_PRIVATE


def locate(name: str) -> Optional[Path]:
    """The source folder of a die in the open/private sets, or ``None`` (also
    for a clash — a clashing die does not resolve)."""
    e = scan().get(str(name))
    if not e or e["clash"] or e["source"] not in SOURCES:
        return None
    return e["dir"]


# ── strict reading (fail closed) ─────────────────────────────────────────────

def load_yaml_strict(p: Path) -> Dict[str, Any]:
    """The mapping in ``p``; raises :class:`DataError` on ANY problem."""
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DataError(f"{p.name}: cannot be read ({exc})") from exc
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DataError(f"{p.name}: invalid YAML ({str(exc).splitlines()[0]})") from exc
    if not isinstance(d, dict):
        raise DataError(f"{p.name}: must be a YAML mapping, got {type(d).__name__}")
    return d


def _configs(die_dir: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    """Every top-level configuration, strictly parsed (raises DataError)."""
    return [(p, load_yaml_strict(p)) for p in sorted(die_dir.glob("*.yaml"))
            if p.name != "die.yaml"]


def _str_values(d: Any) -> List[str]:
    if isinstance(d, dict):
        return [v.strip() for v in d.values() if isinstance(v, str) and v.strip()]
    return []


def referenced_materials(die_dir: Path) -> List[str]:
    names = set()
    for _p, cfg in _configs(die_dir):
        names.update(_str_values(cfg.get("materials")))
    names.update(_str_values(load_yaml_strict(die_dir / "die.yaml").get("materials")))
    return sorted(names)


def referenced_devices(die_dir: Path) -> List[str]:
    parts = set()
    for _p, cfg in _configs(die_dir):
        ctl = cfg.get("controller") or {}
        if isinstance(ctl, dict) and isinstance(ctl.get("device"), str) and ctl["device"].strip():
            parts.add(ctl["device"].strip())
    return sorted(parts)


def open_material_names() -> set:
    """Every material name in the PUBLIC library — the only ones an open die
    may use.  Unreadable library → DataError (fail closed)."""
    lib = load_yaml_strict(open_repo_dir() / "config" / "materials_library.yaml")
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


def _tags(doc: Dict[str, Any]) -> set:
    tags = doc.get("tags") or []
    if isinstance(tags, (list, tuple)):
        return {str(t).strip().lower() for t in tags}
    return set()


def _customer_reason(doc: Dict[str, Any], where: str) -> Optional[str]:
    if str(doc.get("customer") or "").strip():
        return f"{where} names a customer — customer data never goes to git"
    conf = str(doc.get("confidential") or "").strip().lower()
    if conf == "customer":
        return f"{where} is marked confidential: customer"
    if CUSTOMER_TAGS & _tags(doc):
        return f"{where} is tagged customer"
    return None


def confidential_reason(die_dir: Path) -> Optional[str]:
    """Why this die may never be PUBLISHED (open), or ``None``.  Raises
    DataError when die.yaml is unreadable — callers turn that into a blocker."""
    d = load_yaml_strict(die_dir / "die.yaml")
    conf = str(d.get("confidential") or "").strip().lower()
    if conf and conf not in ("0", "false", "no", "none"):
        return f"die.yaml marks it confidential ({conf})"
    if d.get("nda") is True:
        return "die.yaml marks it as covered by an NDA"
    why = _customer_reason(d, "die.yaml")
    if why:
        return why
    hit = sorted(CONFIDENTIAL_TAGS & _tags(d))
    if hit:
        return f"die.yaml is tagged {', '.join(hit)}"
    return None


def reference_orgs() -> set:
    raw = os.environ.get(ENV_REFERENCE_ORGS, "").strip() or "MOTRES"
    return {x.strip().upper() for x in raw.split(",") if x.strip()}


def ownership_reason(doc: Dict[str, Any]) -> Optional[str]:
    """Why this die is NOT curated MOTRES reference data, or ``None``.
    Affirmative: a die must declare ``owner_org: MOTRES``; absence blocks."""
    org = str(doc.get("owner_org") or "").strip().upper()
    if not org:
        return ("die.yaml does not declare owner_org — only curated MOTRES "
                "reference data may enter a git repository (add owner_org: MOTRES "
                "only if it is ours)")
    if org not in reference_orgs():
        return (f"owned by '{doc.get('owner_org')}', not MOTRES — customer and "
                "third-party dies stay in the application storage")
    return None


# ── the file whitelist + content checks ─────────────────────────────────────

MiB = 1024 * 1024
#: ``(pattern on the die-relative posix path, max bytes, kind)``.  Anything
#: that matches none of these is a blocker.
WHITELIST: Tuple[Tuple[re.Pattern, int, str], ...] = (
    (re.compile(r"^die\.yaml$"), 1 * MiB, "yaml"),
    (re.compile(r"^[^/]+\.yaml$"), 2 * MiB, "yaml"),
    (re.compile(r"^runs/[^/]+/[^/]+\.json\.gz$"), 20 * MiB, "json.gz"),
    (re.compile(r"^runs/[^/]+/[^/]+/fields/[^/]+\.npz$"), 50 * MiB, "npz"),
)
MAX_TOTAL_BYTES = 200 * MiB
MAX_FILES = 400

_EMAIL_RE = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_SECRET_RES = (
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}"), "a GitHub token"),
    (re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}"), "a GitHub token"),
    (re.compile(rb"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"), "an API secret key"),
    (re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}"), "a Google API key"),
    (re.compile(rb"(?im)^\s*[\"']?(password|passwd|secret|api_key|apikey|token|"
                rb"access_token|client_secret)[\"']?\s*[:=]\s*\S"), "a credential field"),
)
#: How much of a decompressed result is scanned — a bomb must not blow memory.
_SCAN_LIMIT = 64 * MiB


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_matches(p: Path, sha: str) -> bool:
    """Does ``p`` hold the confirmed content?  A checkout with
    ``core.autocrlf`` may hand a text file back with other line endings; the
    content is the same, so both spellings are accepted — nothing else."""
    if sha256_file(p) == sha:
        return True
    if p.suffix != ".yaml":
        return False
    data = p.read_bytes()
    lf = data.replace(b"\r\n", b"\n")
    return sha in (hashlib.sha256(lf).hexdigest(),
                   hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest())


def _content_problems(data: bytes, rel: str) -> List[str]:
    out = []
    for rx, what in _SECRET_RES:
        if rx.search(data):
            out.append(f"{rel}: contains {what}")
    m = _EMAIL_RE.search(data)
    if m:
        out.append(f"{rel}: contains an e-mail address "
                   "(personal data never goes to git)")
    return out


def _check_file(p: Path, rel: str, kind: str) -> List[str]:
    """Problems with one whitelisted file (empty = fine)."""
    try:
        if kind == "yaml":
            data = p.read_bytes()
            doc = load_yaml_strict(p)
            probs = _content_problems(data, rel)
            why = _customer_reason(doc, rel)
            if why:
                probs.append(why)
            if rel != "die.yaml":
                if "duties" in doc and not isinstance(doc["duties"], list):
                    probs.append(f"{rel}: 'duties' must be a list")
                if "materials" in doc and not isinstance(doc["materials"], dict):
                    probs.append(f"{rel}: 'materials' must be a mapping")
                for x in doc.get("duties") or []:
                    if isinstance(x, dict):
                        why = _customer_reason(x, f"{rel} duty {x.get('name')}")
                        if why:
                            probs.append(why)
            return probs
        if kind == "json.gz":
            import json
            with gzip.open(p, "rb") as fh:
                data = fh.read(_SCAN_LIMIT + 1)
            if len(data) > _SCAN_LIMIT:
                return [f"{rel}: decompresses to more than {_SCAN_LIMIT // MiB} MiB"]
            try:
                doc = json.loads(data.decode("utf-8"))
            except Exception as exc:                          # noqa: BLE001
                return [f"{rel}: not valid JSON ({exc})"]
            probs = _content_problems(data, rel)
            if isinstance(doc, dict):
                why = _customer_reason(doc, rel)
                if why:
                    probs.append(why)
            return probs
        if kind == "npz":
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
                if not names or any(not n.endswith(".npy") for n in names):
                    return [f"{rel}: not a plain numpy archive"]
                total = sum(i.file_size for i in z.infolist())
                if total > 4 * _SCAN_LIMIT:
                    return [f"{rel}: unpacks to more than {4 * _SCAN_LIMIT // MiB} MiB"]
                for n in names:                    # object arrays = pickles
                    with z.open(n) as fh:
                        head = fh.read(256)
                    if b"'descr': '|O'" in head or b"'descr': 'O'" in head:
                        return [f"{rel}: holds a pickled object array"]
            return []
    except DataError as exc:
        return [str(exc) if rel == p.name else f"{rel}: {exc}"]
    except Exception as exc:                                   # noqa: BLE001
        return [f"{rel}: cannot be read ({exc})"]
    return [f"{rel}: unknown file kind"]


def inspect_files(die_dir: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """``(files, blockers)`` over EVERY entry in the die folder.

    ``files`` = the whitelisted, validated export ``[{path, bytes, sha256}]``;
    ``blockers`` = everything else (not whitelisted, symlink, too big,
    unreadable, secret, e-mail, customer tag)."""
    files: List[Dict[str, Any]] = []
    blockers: List[Dict[str, str]] = []
    total = 0
    try:
        root = die_dir.resolve(strict=True)
        entries = sorted(die_dir.rglob("*"))
    except OSError as exc:
        return [], [{"kind": "unreadable", "name": die_dir.name,
                     "reason": f"the die folder cannot be listed ({exc})"}]
    for p in entries:
        rel = p.relative_to(die_dir).as_posix()
        if p.is_symlink():
            blockers.append({"kind": "file", "name": rel, "reason": "symbolic link — never exported"})
            continue
        if p.is_dir():
            continue
        try:
            p.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            blockers.append({"kind": "file", "name": rel, "reason": "escapes the die folder"})
            continue
        rule = next((r for r in WHITELIST if r[0].match(rel)), None)
        if rule is None:
            blockers.append({"kind": "file", "name": rel,
                             "reason": "not on the export whitelist (die.yaml, "
                                       "<config>.yaml, runs/<cfg>/*.json.gz, "
                                       "runs/<cfg>/<duty>/fields/*.npz) — remove it first"})
            continue
        try:
            size = p.stat().st_size
        except OSError as exc:
            blockers.append({"kind": "unreadable", "name": rel, "reason": str(exc)})
            continue
        if size > rule[1]:
            blockers.append({"kind": "file", "name": rel,
                             "reason": f"{size / MiB:.1f} MiB is over the {rule[1] // MiB} MiB limit"})
            continue
        probs = _check_file(p, rel, rule[2])
        for msg in probs:
            blockers.append({"kind": "content", "name": rel, "reason": msg})
        if probs:
            continue
        total += size
        files.append({"path": rel, "bytes": size, "sha256": sha256_file(p)})
    if not any(f["path"] == "die.yaml" for f in files) and not any(
            b["name"] == "die.yaml" for b in blockers):
        blockers.append({"kind": "unreadable", "name": "die.yaml", "reason": "missing"})
    if len(files) > MAX_FILES:
        blockers.append({"kind": "file", "name": die_dir.name,
                         "reason": f"{len(files)} files is over the {MAX_FILES} limit"})
    if total > MAX_TOTAL_BYTES:
        blockers.append({"kind": "file", "name": die_dir.name,
                         "reason": f"{total / MiB:.0f} MiB is over the {MAX_TOTAL_BYTES // MiB} MiB limit"})
    return files, blockers


def move_blockers(die_dir: Path, target: str) -> List[Dict[str, str]]:
    """Everything that stops this die from going to ``target``.

    Both directions: unreadable/wrong-shape files, files outside the
    whitelist, secrets / e-mails / customer tags, and a die that is not
    declared MOTRES reference data.  To ``open`` also: NDA/confidential tags
    and materials/devices that are not public yet."""
    _files, out = inspect_files(die_dir)
    try:
        doc = load_yaml_strict(die_dir / "die.yaml")
    except DataError as exc:
        if not any(b["kind"] == "content" and b["name"] == "die.yaml" for b in out):
            out.append({"kind": "unreadable", "name": "die.yaml", "reason": str(exc)})
        return out
    why = _customer_reason(doc, "die.yaml")
    if why and not any(b["reason"] == why for b in out):
        out.append({"kind": "customer", "name": die_dir.name, "reason": why})
    why = ownership_reason(doc)
    if why:
        out.append({"kind": "ownership", "name": die_dir.name, "reason": why})
    if target != SOURCE_OPEN:
        return out
    why = confidential_reason(die_dir)
    if why and not any(b["reason"] == why for b in out):
        out.append({"kind": "confidential", "name": die_dir.name, "reason": why})
    try:
        mats, devs = referenced_materials(die_dir), referenced_devices(die_dir)
    except DataError as exc:
        out.append({"kind": "unreadable", "name": die_dir.name, "reason": str(exc)})
        return out
    try:
        open_mats = open_material_names()
    except DataError as exc:
        out.append({"kind": "unreadable", "name": "materials_library.yaml",
                    "reason": f"public library: {exc}"})
        open_mats = set()
    for m in mats:
        if m not in open_mats:
            out.append({"kind": "material", "name": m,
                        "reason": "not in the public materials library — publish it first"})
    open_devs = open_device_parts()
    for part in devs:
        if part not in open_devs:
            out.append({"kind": "device", "name": part,
                        "reason": "device card is private — publish it first"})
    return out


def publish_blockers(die_dir: Path) -> List[Dict[str, str]]:
    return move_blockers(die_dir, SOURCE_OPEN)


def snapshot_id(die: str, source: str, target: str, files: List[Dict[str, Any]]) -> str:
    """The content hash the admin confirms: die, direction and every file hash."""
    h = hashlib.sha256(f"{die}\0{source}\0{target}\n".encode("utf-8"))
    for f in sorted(files, key=lambda x: x["path"]):
        h.update(f"{f['path']}\0{f['sha256']}\n".encode("utf-8"))
    return h.hexdigest()


def manifest(die_dir: Path) -> Dict[str, Any]:
    """EXACTLY what a move carries (the validated whitelist), for the dialog.
    Unreadable parts show as empty; the blockers carry the error."""
    files, _bl = inspect_files(die_dir)
    try:
        die_doc = load_yaml_strict(die_dir / "die.yaml")
    except DataError:
        die_doc = {}
    geo = die_doc.get("geometry") or {}
    configs, results = [], []
    try:
        cfgs = _configs(die_dir)
    except DataError:
        cfgs = []
    for p, cfg in cfgs:
        duties = [x for x in (cfg.get("duties") or []) if isinstance(x, dict)]
        configs.append({"name": p.stem, "role": cfg.get("role"),
                        "duties": [str(x.get("name")) for x in duties]})
        for x in duties:
            if x.get("result") or x.get("summary"):
                results.append(f"{p.stem} / {x.get('name')} (saved result)")
    results += [f["path"] for f in files if f["path"].startswith("runs/")]
    try:
        mats, devs = referenced_materials(die_dir), referenced_devices(die_dir)
    except DataError:
        mats, devs = [], []
    return {
        "die": die_dir.name,
        "files": files,
        "total_bytes": sum(f["bytes"] for f in files),
        "geometry": {k: geo[k] for k in sorted(geo)} if isinstance(geo, dict) else {},
        "configs": configs,
        "materials": mats,
        "devices": devs,
        "results": results,
        "owner_org": die_doc.get("owner_org"),
    }
