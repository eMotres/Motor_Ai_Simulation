"""3D model menu: which CAD model a caller may see and download, the STEP stamp and the download log.

The generator (``motor_ai_sim.cad``) writes, per die/config, into ``<shared>/cad/<die>/<config>/``:

    <motor_id>_full.glb           the model for the viewer             -> every signed-in user who may see the motor
    <motor_id>_active.step        the full STEP                         -> download: NDA accounts and the owner / admin only
    manifest.json                 SHA-256 of every file

ONE model (owner 2026-10-13): everybody who may see a motor can view, rotate and section it; downloading it is the NDA
gate.  The rule lives in :func:`can_download` and nowhere else; the URL names the motor, never a file.  The bill of
materials and anything priced are not served by this module at all.

Every STEP download is stamped (customer name and date in the STEP header and in the file name) and logged
(``cad_downloads.jsonl`` beside ``users.json``: user, time, file, version, hashes).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Optional

from motor_ai_sim.private_files import ensure_private_dir, open_private

ORGANISATION = "MOTRES d.o.o."
_LOCK = threading.RLock()

#: file name suffix per kind; the GLB is written by cad/fc_scripts/export_models.py, the STEP by export_step.py
_SUFFIX = {"glb": "_full.glb", "step": "_active.step"}


# ── where things are ────────────────────────────────────────────────────────

def cad_root() -> Path:
    """``<shared>/cad`` (``MOTRES_CAD_ROOT`` overrides, tests)."""
    env = (os.environ.get("MOTRES_CAD_ROOT") or "").strip()
    if env:
        return Path(env)
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "cad"


def download_log_path() -> Path:
    env = (os.environ.get("CAD_DOWNLOAD_LOG") or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim import users as _U
    return Path(_U._USERS_FILE).parent / "cad_downloads.jsonl"


# ── who sees what ───────────────────────────────────────────────────────────

def can_download(identity: dict, user_rec: Optional[dict] = None) -> bool:
    """May this caller download the STEP?  The owner / admin and accounts flagged "NDA signed" in Admin; viewing is open."""
    if identity.get("is_admin"):
        return True
    rec = user_rec or {}
    nda = rec.get("nda") if isinstance(rec.get("nda"), dict) else {}
    return bool(nda.get("signed"))


def customer_name(identity: dict, user_rec: Optional[dict] = None) -> str:
    """The name printed into a downloaded file: the account name, else its e-mail."""
    name = str((user_rec or {}).get("name") or "").strip()
    return name or str(identity.get("id") or "unknown")


# ── the catalogue of built models ───────────────────────────────────────────

def _read_json(p: Path) -> dict:
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def scan(root: Optional[Path] = None) -> list[dict]:
    """Every built config with a manifest that lists the viewer model: ``[{die, config, motor_id, dir, manifest}]``."""
    root = Path(root) if root else cad_root()
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and ".building-" not in p.name
                    and ".old-" not in p.name):
        for c in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".") and ".building-" not in p.name
                        and ".old-" not in p.name):
            m = _read_json(c / "manifest.json")
            mid = m.get("motor_id")
            files = m.get("files") or {}
            if not mid or not any(k.endswith("_full.glb") for k in files):
                continue
            out.append(dict(die=d.name, config=c.name, motor_id=mid, dir=c, manifest=m))
    return out


def find(die: str, config: str, root: Optional[Path] = None) -> Optional[dict]:
    """The entry for exactly this die/config; names are matched against the scan, never joined into a path."""
    for e in scan(root):
        if e["die"] == die and e["config"] == config:
            return e
    return None


def file_row(entry: dict, kind: str) -> Optional[dict]:
    """``{path, name, sha256, bytes}`` of one output (``glb`` / ``step``), or None when the manifest does not list it / it is missing."""
    name = entry["motor_id"] + _SUFFIX[kind]
    row = (entry["manifest"].get("files") or {}).get(name)
    p = Path(entry["dir"]) / name
    if not row or not p.is_file():
        return None
    return dict(path=p, name=name, sha256=row.get("sha256"), bytes=p.stat().st_size)


def model_info(entry: dict) -> dict:
    """What the list route says about one model (no BOM, no prices, no store hashes)."""
    m = entry["manifest"]
    glb, step = file_row(entry, "glb"), file_row(entry, "step")
    return dict(die=entry["die"], config=entry["config"], motor_id=entry["motor_id"],
                available=bool(glb), glb_bytes=glb and glb["bytes"], glb_sha256=glb and glb["sha256"],
                step_available=bool(step), step_bytes=step and step["bytes"],
                version=dict(template=(m.get("template") or {}).get("version"), built_at=m.get("built_at"),
                             param_hash=(m.get("param_hash") or "")[:12]))


# ── STEP stamp ──────────────────────────────────────────────────────────────

def ascii_text(s: str, limit: int = 120) -> str:
    """STEP header strings are ASCII: transliterate, drop controls, double the apostrophes (done by the caller)."""
    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[\x00-\x1f\x7f\\]", " ", t).replace("/*", " ").replace("*/", " ")
    t = re.sub(r" {2,}", " ", t).strip()
    return t[:limit] or "unknown"


def _slug(s: str, limit: int = 32) -> str:
    t = re.sub(r"[^a-z0-9]+", "-", ascii_text(s).lower()).strip("-")
    return t[:limit].strip("-") or "customer"


def download_name(motor_id: str, customer: str, day: datetime.date, ext: str = "step") -> str:
    return "%s_%s_%s.%s" % (motor_id, _slug(customer), day.strftime("%Y%m%d"), ext)


def stamp_step(data: bytes, *, filename: str, customer: str, day: datetime.date, motor_id: str,
               version: str = "", when: Optional[datetime.datetime] = None) -> bytes:
    """Rewrite the ISO 10303-21 HEADER section: description, file name, author and organisation carry the customer name, the
    date and the model version.  The DATA section is copied byte for byte."""
    mark = data.find(b"ENDSEC;")
    if not data.lstrip().startswith(b"ISO-10303-21;") or mark < 0:
        raise ValueError("not an ISO 10303-21 STEP file")
    head = data[:mark].decode("latin-1")
    rest = data[mark:]
    sm = re.search(r"FILE_SCHEMA\s*\(.*?\)\s*;", head, re.S)
    schema = sm.group(0) if sm else "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));"
    q = lambda s, n=120: ascii_text(s, n).replace("'", "''")
    cust, org = q(customer), q(ORGANISATION)
    ts = (when or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S")
    desc = "MOTRES AeroStator model %s%s. Licensed to %s on %s. Not for redistribution." % (
        motor_id, (" " + version) if version else "", customer, day.isoformat())
    new = ("ISO-10303-21;\nHEADER;\n/* %s */\n" % q(desc, 400)
           + "FILE_DESCRIPTION(('%s'),'2;1');\n" % q(desc, 400)
           + "FILE_NAME('%s','%s',('%s'),('%s'),'Open CASCADE STEP processor','FreeCAD','Licensed to %s %s');\n" % (
               q(filename), ts, cust, org, cust, day.isoformat())
           + schema + "\n")
    return new.encode("latin-1") + rest


def stamp_of(step: bytes) -> dict:
    """Read the stamp back (tests, support): the FILE_NAME entity of a stamped file."""
    head = step[:step.find(b"ENDSEC;")].decode("latin-1")
    m = re.search(r"FILE_NAME\s*\(\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'\s*,\s*\(\s*'((?:[^']|'')*)'", head)
    return dict(filename=m.group(1), timestamp=m.group(2), customer=m.group(3)) if m else {}


# ── download log ────────────────────────────────────────────────────────────

def log_download(rec: dict) -> dict:
    """Append one JSON line to the download log.  Raises when the line cannot be written: a download nobody can account for
    is not served."""
    now = datetime.datetime.now(datetime.timezone.utc)
    row = dict(ts=round(now.timestamp(), 3), iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"), **rec)
    p = download_log_path()
    with _LOCK:
        ensure_private_dir(p.parent)
        with open_private(p, "a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def read_log(limit: int = 200) -> list[dict]:
    p = download_log_path()
    if not p.is_file():
        return []
    rows = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows[-limit:][::-1]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
