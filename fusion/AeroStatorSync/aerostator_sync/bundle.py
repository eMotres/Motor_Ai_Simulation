"""The export bundle: manifest, checksums, zip.  Pure Python (no `adsk`), unit-tested outside Fusion.

Layout of `<document>_v<N>.zip` (schema motres-fusion-bundle/1, docs/FUSION_SYNC.md section 3.1):

    manifest.json              document, version, link, units, frame, parts, sketches, files{path: sha256, bytes}
    parameters.json            every user parameter: name, expression, unit, value (in the parameter's own unit), comment
    assembly.step              the whole root component
    parts/<file>.step          one STEP per component that carries solid bodies (component coordinates)
    sketches/<file>.dxf        the sketches the link lists (lamination, magnet, coil cross-sections)

`manifest.json` lists every other file with its SHA-256; the server refuses a bundle whose files do not match.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import zipfile
from typing import Dict, List, Optional

SCHEMA = "motres-fusion-bundle/1"
ADDIN_VERSION = "0.1.0"
FRAME = "Fusion root frame, mm, Z = motor axis (+z = propeller side)"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_name(text: str) -> str:
    """A component / sketch name as a file name: 'PCB 40 serial_new' -> 'PCB_40_serial_new'."""
    return _SAFE.sub("_", str(text)).strip("_") or "unnamed"


def unique_file(stem: str, used: set, ext: str) -> str:
    name, i = stem + ext, 2
    while name.lower() in used:
        name, i = "%s_%d%s" % (stem, i, ext), i + 1
    used.add(name.lower())
    return name


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def bundle_name(document: str, version: int) -> str:
    return "%s_v%d.zip" % (safe_name(document), int(version))


def make_manifest(*, document: str, urn: str, version: int, link: Optional[dict], fusion_build: str, user: str,
                  parts: Dict[str, dict], sketches: Dict[str, dict], root_dir: str, exported_at: Optional[str] = None) -> dict:
    """The manifest of the files already written under root_dir (parameters.json, assembly.step, parts/, sketches/)."""
    files = {}
    for dirpath, _dirs, names in os.walk(root_dir):
        for n in sorted(names):
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, root_dir).replace(os.sep, "/")
            if rel == "manifest.json":
                continue
            files[rel] = dict(sha256=sha256_file(full), bytes=os.path.getsize(full))
    return dict(schema=SCHEMA, document=document, urn=urn, version=int(version), link=link or {},
                exported_at=exported_at or datetime.datetime.now().isoformat(timespec="seconds"),
                exported_by=user, fusion_build=fusion_build, addin_version=ADDIN_VERSION, units="mm", frame=FRAME,
                parts=parts, sketches=sketches, files=dict(sorted(files.items())))


def write_zip(root_dir: str, manifest: dict, out_path: str) -> str:
    """manifest.json + every file of the manifest into out_path (deflated).  Returns out_path."""
    with open(os.path.join(root_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(root_dir, "manifest.json"), "manifest.json")
        for rel in manifest["files"]:
            z.write(os.path.join(root_dir, *rel.split("/")), rel)
    return out_path


def check_manifest(manifest: dict, read_file) -> List[str]:
    """Problems of a bundle (empty = good).  read_file(rel) -> bytes or None.  Used by the add-in's self-check and mirrored
    by the server's validator (src/motor_ai_sim/fusion_sync/bundle.py)."""
    errs = []
    if manifest.get("schema") != SCHEMA:
        errs.append("schema %r, expected %r" % (manifest.get("schema"), SCHEMA))
    if not isinstance(manifest.get("version"), int) or manifest["version"] < 1:
        errs.append("version must be a positive integer")
    for key in ("document", "urn"):
        if not manifest.get(key):
            errs.append("missing %s" % key)
    files = manifest.get("files") or {}
    for need in ("parameters.json", "assembly.step"):
        if need not in files:
            errs.append("missing %s" % need)
    for rel, meta in files.items():
        data = read_file(rel)
        if data is None:
            errs.append("file %s listed but absent" % rel)
        elif hashlib.sha256(data).hexdigest() != meta.get("sha256"):
            errs.append("file %s: sha256 mismatch" % rel)
    for comp, p in (manifest.get("parts") or {}).items():
        if p.get("file") not in files:
            errs.append("part %s: file %s not in the bundle" % (comp, p.get("file")))
    for key, s in (manifest.get("sketches") or {}).items():
        if s.get("file") not in files:
            errs.append("sketch %s: file %s not in the bundle" % (key, s.get("file")))
    return errs
