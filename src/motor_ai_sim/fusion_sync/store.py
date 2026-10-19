"""Server store of the Fusion export bundles (docs/FUSION_SYNC.md section 5.1).

    <root>/<die slug>/link.json          {urn, document, die, config, sketches, linked_by, linked_at}
    <root>/<die slug>/bundles/v<N>/      the unpacked bundle (immutable) + bundle.zip + record.json
    <root>/<die slug>/latest.json        {version, exported_at, exported_by, stored_at}
    <root>/<die slug>/audit.jsonl        one line per link / upload

<root> = $MOTRES_FUSION_SYNC_ROOT, else <shared root>/fusion.  Nothing here writes a die or configuration file.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from motor_ai_sim.json_store import atomic_write_json, lock_for, read_json

SCHEMA = "motres-fusion-bundle/1"
#: Stage 1 allow-list (owner 2026-10-19): the one motor whose Fusion model is the sync's prototype.  Frozen dies (the 40 mm,
#: mould ordered) are never on it.  Override with FUSION_SYNC_DIES (comma separated die names).
DEFAULT_DIES = ("CIANO14 50 edited",)
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_FILES = 2000
DEFAULT_SKETCHES = ("Core/Sketch1", "Rotor/Sketch1", "Magnet/Sketch1", "Coil/Sketch1")
_MEMBER = re.compile(r"^(manifest\.json|parameters\.json|assembly\.step|parts/[A-Za-z0-9_.-]+\.step|sketches/[A-Za-z0-9_.-]+\.dxf)$")


class BundleError(ValueError):
    """A refused bundle: `code` for the client, the message for the owner."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


def root() -> Path:
    env = os.environ.get("MOTRES_FUSION_SYNC_ROOT", "").strip()
    if env:
        return Path(env)
    from motor_ai_sim.workspace import shared_root
    return Path(shared_root()) / "fusion"


def allowed_dies() -> Tuple[str, ...]:
    env = os.environ.get("FUSION_SYNC_DIES")
    if env is None:
        return DEFAULT_DIES
    return tuple(x.strip() for x in env.split(",") if x.strip())


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_") or "unnamed"


def die_dir(die: str) -> Path:
    return root() / slug(die)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def audit(die: str, action: str, who: str, **details) -> None:
    d = die_dir(die)
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(at=_now_iso(), action=action, who=who, **details), ensure_ascii=False, default=str)
    with lock_for(d / "audit.jsonl"):
        with open(d / "audit.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# ── link ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def link_of_die(die: str) -> Optional[Dict[str, Any]]:
    v = read_json(die_dir(die) / "link.json", default=None)
    return v if isinstance(v, dict) else None


def link_of_urn(urn: str) -> Optional[Dict[str, Any]]:
    """The link of a Fusion document (lineage URN) among the allow-listed dies."""
    for die in allowed_dies():
        ln = link_of_die(die)
        if ln and ln.get("urn") == urn:
            return ln
    return None


def set_link(urn: str, document: str, die: str, config: str, who: str, sketches=None) -> Dict[str, Any]:
    """One Fusion document per die and one die per document; re-linking the same pair is a no-op."""
    other = link_of_urn(urn)
    if other and other.get("die") != die:
        raise BundleError("document_linked_elsewhere", "this Fusion document is already linked to die %r" % other["die"], 409)
    cur = link_of_die(die)
    if cur and cur.get("urn") != urn:
        raise BundleError("die_linked_elsewhere", "die %r is already linked to the Fusion document %r" % (die, cur.get("document")), 409)
    rec = dict(urn=urn, document=document, die=die, config=config,
               sketches=list(sketches if sketches is not None else (cur or {}).get("sketches") or DEFAULT_SKETCHES),
               linked_by=(cur or {}).get("linked_by") or who, linked_at=(cur or {}).get("linked_at") or _now_iso())
    if cur and cur.get("config") != config:
        rec["relinked_at"] = _now_iso()
    die_dir(die).mkdir(parents=True, exist_ok=True)
    atomic_write_json(die_dir(die) / "link.json", rec)
    audit(die, "link", who, urn=urn, document=document, config=config)
    return rec


# ── bundle validation ────────────────────────────────────────────────────────────────────────────────────────────────
def read_bundle(data: bytes) -> Tuple[Dict[str, Any], Dict[str, bytes]]:
    """(manifest, {member: bytes}) of an uploaded zip, or BundleError.  Only the bundle's own layout is accepted (no paths
    outside it, no other file types), sizes are capped before anything is unpacked, every listed file must match its sha256
    and no unlisted file may ride along."""
    if len(data) > MAX_ZIP_BYTES:
        raise BundleError("too_large", "bundle larger than %d MB" % (MAX_ZIP_BYTES // 2 ** 20), 413)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BundleError("not_a_zip", "the upload is not a zip file")
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise BundleError("too_many_files", "more than %d files in the bundle" % MAX_FILES, 413)
    if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
        raise BundleError("too_large", "bundle unpacks to more than %d MB" % (MAX_UNPACKED_BYTES // 2 ** 20), 413)
    bad = [i.filename for i in infos if not _MEMBER.match(i.filename)]
    if bad:
        raise BundleError("bad_member", "files outside the bundle layout: %s" % ", ".join(sorted(bad)[:5]))
    files = {i.filename: zf.read(i) for i in infos}
    if "manifest.json" not in files:
        raise BundleError("no_manifest", "manifest.json missing")
    try:
        m = json.loads(files["manifest.json"].decode("utf-8"))
    except ValueError as e:
        raise BundleError("bad_manifest", "manifest.json is not JSON: %s" % e)
    errs = check_manifest(m, files)
    if errs:
        raise BundleError("bad_bundle", "; ".join(errs[:8]))
    return m, files


def check_manifest(m: Dict[str, Any], files: Dict[str, bytes]) -> List[str]:
    """The server twin of fusion/AeroStatorSync/aerostator_sync/bundle.py check_manifest, plus: no unlisted files."""
    errs = []
    if m.get("schema") != SCHEMA:
        errs.append("schema %r, expected %r" % (m.get("schema"), SCHEMA))
    if not isinstance(m.get("version"), int) or isinstance(m.get("version"), bool) or m["version"] < 1:
        errs.append("version must be a positive integer")
    for key in ("document", "urn"):
        if not m.get(key):
            errs.append("missing %s" % key)
    listed = m.get("files") or {}
    for need in ("parameters.json", "assembly.step"):
        if need not in listed:
            errs.append("missing %s" % need)
    for rel, meta in listed.items():
        data = files.get(rel)
        if data is None:
            errs.append("file %s listed but absent" % rel)
        elif hashlib.sha256(data).hexdigest() != (meta or {}).get("sha256"):
            errs.append("file %s: sha256 mismatch" % rel)
    extra = sorted(set(files) - set(listed) - {"manifest.json"})
    if extra:
        errs.append("files not listed in the manifest: %s" % ", ".join(extra[:5]))
    for comp, p in (m.get("parts") or {}).items():
        if (p or {}).get("file") not in listed:
            errs.append("part %s: file %s not in the bundle" % (comp, (p or {}).get("file")))
    for key, s in (m.get("sketches") or {}).items():
        if (s or {}).get("file") not in listed:
            errs.append("sketch %s: file %s not in the bundle" % (key, (s or {}).get("file")))
    if "parameters.json" in files:
        try:
            ps = json.loads(files["parameters.json"].decode("utf-8"))
            if not isinstance(ps, list) or not all(isinstance(x, dict) and x.get("name") for x in ps):
                errs.append("parameters.json must be a list of {name, expression, unit, value}")
        except ValueError:
            errs.append("parameters.json is not JSON")
    return errs


def content_key(m: Dict[str, Any], files: Dict[str, bytes]) -> str:
    """What identifies a version's CONTENT: the parameters and every body's volume.  STEP files carry export timestamps, so
    two exports of the same Fusion version are never byte-equal; this key is."""
    parts = {c: sorted(round(float(b.get("volume_mm3", 0.0)), 3) for b in (p or {}).get("bodies", []))
             for c, p in (m.get("parts") or {}).items()}
    h = hashlib.sha256()
    h.update(files.get("parameters.json", b""))
    h.update(json.dumps(parts, sort_keys=True).encode("utf-8"))
    return h.hexdigest()


# ── bundle store ─────────────────────────────────────────────────────────────────────────────────────────────────────
def bundle_dir(die: str, version: int) -> Path:
    return die_dir(die) / "bundles" / ("v%d" % int(version))


def latest(die: str) -> Optional[Dict[str, Any]]:
    v = read_json(die_dir(die) / "latest.json", default=None)
    return v if isinstance(v, dict) else None


def record(die: str, version: int) -> Optional[Dict[str, Any]]:
    v = read_json(bundle_dir(die, version) / "record.json", default=None)
    return v if isinstance(v, dict) else None


def versions(die: str) -> List[Dict[str, Any]]:
    d = die_dir(die) / "bundles"
    out = []
    if d.is_dir():
        for p in d.iterdir():
            r = read_json(p / "record.json", default=None) if p.is_dir() else None
            if isinstance(r, dict):
                out.append({k: r.get(k) for k in ("version", "exported_at", "exported_by", "stored_at", "zip_sha256", "config")})
    return sorted(out, key=lambda r: r.get("version") or 0)


def store_bundle(die: str, config: str, m: Dict[str, Any], files: Dict[str, bytes], data: bytes, who: str,
                 extra: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], bool]:
    """Store version m['version'] of `die` -> (record, already_stored).  Same version + same content -> the first copy is kept;
    same version + different content -> BundleError 409 (a Fusion version never changes)."""
    v = int(m["version"])
    key = content_key(m, files)
    bdir = bundle_dir(die, v)
    with lock_for(die_dir(die) / "bundles.lock"):
        cur = record(die, v)
        if cur:
            if cur.get("content_key") == key:
                return cur, True
            raise BundleError("version_changed", "version %d of %r is already stored with different content; a saved Fusion "
                              "version never changes - save a new version and export that" % (v, m.get("document")), 409)
        tmp = bdir.with_name(bdir.name + ".tmp-%d" % os.getpid())
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for rel, blob in files.items():
            p = tmp.joinpath(*rel.split("/"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(blob)
        (tmp / "bundle.zip").write_bytes(data)
        rec = dict(version=v, die=die, config=config, document=m.get("document"), urn=m.get("urn"),
                   exported_at=m.get("exported_at"), exported_by=m.get("exported_by"), stored_at=_now_iso(), stored_by=who,
                   zip_sha256=hashlib.sha256(data).hexdigest(), content_key=key, **(extra or {}))
        atomic_write_json(tmp / "record.json", rec)
        os.replace(tmp, bdir)
        lt = latest(die)
        if not lt or int(lt.get("version") or 0) <= v:
            atomic_write_json(die_dir(die) / "latest.json", {k: rec[k] for k in ("version", "config", "document", "exported_at",
                                                                                 "exported_by", "stored_at")})
    audit(die, "upload", who, version=v, document=m.get("document"), zip_sha256=rec["zip_sha256"])
    return rec, False


def bundle_parameters(die: str, version: int) -> List[Dict[str, Any]]:
    v = read_json(bundle_dir(die, version) / "parameters.json", default=[])
    return v if isinstance(v, list) else []


def bundle_manifest(die: str, version: int) -> Optional[Dict[str, Any]]:
    v = read_json(bundle_dir(die, version) / "manifest.json", default=None)
    return v if isinstance(v, dict) else None
