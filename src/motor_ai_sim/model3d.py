"""3D model menu: which CAD model a caller may see and download, the STEP stamp and the download log.

The generator (``motor_ai_sim.cad``) writes, per die/config, into ``<shared>/cad/<die>/<config>/``:

    <motor_id>_full.glb           the model for the viewer             -> every signed-in user who may see the motor
    <motor_id>_active.step        the full STEP                         -> download: NDA accounts and the owner / admin only
    <motor_id>_simplified.step    the SIMPLIFIED envelope-only model (+ .glb, .json)  -> every signed-in user, NO NDA (owner 2026-10-15)
    manifest.json                 SHA-256 of every file

ONE model (owner 2026-10-13): everybody who may see a motor can view, rotate and section it; downloading it is the NDA
gate.  The rule lives in :func:`can_download` and nowhere else; the URL names the motor, never a file.  The bill of
materials and anything priced are not served by this module at all.

Every STEP download is tied to its NDA, stamped and logged (owner 2026-10-14):

* a unique download id and the NDA basis (online click-through record / admin flag / internal) are written into the STEP
  header (FILE_DESCRIPTION / FILE_NAME) while the geometry stays byte for byte;
* the same facts, the sha256 of the delivered (stamped) file, the IP and the user agent go into the append-only
  ``cad_downloads.jsonl`` beside ``users.json`` (never rewritten), so a leaked file is traced by its header or its hash (:func:`trace`).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import unicodedata
from pathlib import Path
from typing import Optional

from motor_ai_sim.private_files import ensure_private_dir, open_private

log = logging.getLogger(__name__)

ORGANISATION = "MOTRES d.o.o."
_LOCK = threading.RLock()

#: file name suffix per kind; the GLB is written by cad/fc_scripts/export_models.py, the STEP by export_step.py
_SUFFIX = {"glb": "_full.glb", "step": "_active.step",
           # the SIMPLIFIED model (owner 2026-10-15): envelope only, no NDA, written by cad/fc_scripts/export_simplified.py
           "simplified_step": "_simplified.step", "simplified_glb": "_simplified.glb", "simplified_json": "_simplified.json"}

SIMPLIFIED_NOTICE = "SIMPLIFIED - envelope only"

#: a download id: d<UTC day>-<12 hex>; what the trace tool looks for in pasted headers and properties
ID_RE = re.compile(r"\bd[0-9]{8}-[0-9a-f]{12}\b")
SHA_RE = re.compile(r"\b[0-9a-f]{64}\b")


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
    """May this caller download the STEP and use the 3-D tools?  The owner / admin; an account flagged "NDA signed" by an
    administrator; an account that signed the NDA online ONLY once its applicant verification is VERIFIED (or an administrator
    approved it) - the signature alone no longer opens it (motor_ai_sim.verification, owner 2026-10-15).  Viewing is open."""
    from motor_ai_sim import verification as V
    return V.access_ok(identity, user_rec)


def denial_detail(identity: dict, user_rec: Optional[dict] = None) -> str:
    """The 403 text for a caller who may not use the confidential files: names what is missing (the NDA, or the verification)."""
    from motor_ai_sim import verification as V
    nda = (user_rec or {}).get("nda") if isinstance((user_rec or {}).get("nda"), dict) else {}
    if nda.get("signed") and nda.get("by") == "online":
        st = V.public_access(identity, user_rec or {}, create=False).get("state")
        return ("Access is not open: your verification is not complete (state: %s). Open the 3D model tab to see what is needed." % st)
    return "Download is available after the NDA is signed."


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
    s_step, s_glb = file_row(entry, "simplified_step"), file_row(entry, "simplified_glb")
    return dict(die=entry["die"], config=entry["config"], motor_id=entry["motor_id"],
                assembly=m.get("cad_variant") or "full",              # "full" | "frameless" (no envelope / simplified variants are served)
                available=bool(glb), glb_bytes=glb and glb["bytes"], glb_sha256=glb and glb["sha256"],
                step_available=bool(step), step_bytes=step and step["bytes"],
                # the simplified model needs a signed-in user and the right to see the motor - nothing else (no NDA, no verification)
                simplified_available=bool(s_step), simplified_step_bytes=s_step and s_step["bytes"],
                simplified_glb_bytes=s_glb and s_glb["bytes"],
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


# ── NDA basis of a download ─────────────────────────────────────────────────

def new_download_id(now: Optional[datetime.datetime] = None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return "d%s-%s" % (now.strftime("%Y%m%d"), secrets.token_hex(6))


def nda_basis(identity: dict, user_rec: Optional[dict] = None) -> dict:
    """On what NDA this caller downloads: ``{type, ref, label, ...}``.

    * ``internal``  the owner / admin (no NDA needed);
    * ``online``    the click-through: NDA record id, version, text sha256, company, signatory (from the append-only record);
    * ``admin``     the "NDA signed" flag set in Admin: who set it and the date on the flag.
    ``ref`` is the short text written into the file, ``label`` what the notice names after "MOTRES Model Access and NDA"."""
    if identity.get("is_admin"):
        return dict(type="internal", ref="internal", label="internal")
    nda = (user_rec or {}).get("nda") if isinstance((user_rec or {}).get("nda"), dict) else {}
    if nda.get("by") == "online" and nda.get("record_id"):
        from motor_ai_sim import nda as N
        rid = str(nda["record_id"])
        rec = N.get_record(rid) or {}
        ver = str(rec.get("version") or nda.get("version") or "?")
        return dict(type="online", ref="%s record %s" % (ver, rid), label=ver, version=ver, record_id=rid,
                    text_sha256=rec.get("text_sha256"), company=rec.get("company") or nda.get("company") or "",
                    signatory=rec.get("signatory") or "", signed_at=rec.get("iso") or nda.get("date") or "",
                    record_found=bool(rec), verification=_verification_basis(identity))
    day = str(nda.get("date") or "?")
    return dict(type="admin", ref="admin-flag %s" % day, label="admin-flag %s" % day, set_by=str(nda.get("by") or ""), date=day)


def _verification_basis(identity: dict) -> dict:
    try:
        from motor_ai_sim import verification as V
        return V.basis_of(identity.get("id") or "")
    except Exception:                                       # noqa: BLE001 - the log line is written even if the case cannot be read
        return {}


def recipient_of(identity: dict, user_rec: Optional[dict], basis: dict) -> dict:
    """Who the file is for: ``{name, email, company}`` (company from the signed NDA, else the account's, else the owner's)."""
    company = basis.get("company") or str((user_rec or {}).get("company") or "").strip()
    if basis["type"] == "internal":
        company = company or ORGANISATION
    return dict(name=customer_name(identity, user_rec), email=str(identity.get("id") or ""), company=company)


def build_stamp(*, download_id: str, motor_id: str, version: str, day: datetime.date, recipient: dict, basis: dict) -> dict:
    """Everything a delivered file says about its download (ASCII; written into the STEP header)."""
    notice = "CONFIDENTIAL - MOTRES Model Access and NDA %s" % basis["label"]
    who = recipient["name"] + (", " + recipient["company"] if recipient.get("company") else "")
    line = ("AeroStator download %s; model %s%s; recipient %s <%s>; NDA %s; date %s; %s" % (
        download_id, motor_id, (" (" + version + ")") if version else "", who, recipient["email"], basis["ref"], day.isoformat(), notice))
    return dict(download_id=download_id, motor_id=motor_id, version=version, date=day.isoformat(), recipient=recipient["name"],
                email=recipient["email"], company=recipient.get("company") or "", nda_ref=basis["ref"], notice=notice, line=line)


# ── STEP stamp ──────────────────────────────────────────────────────────────

def build_simplified_stamp(*, download_id: str, motor_id: str, version: str, day: datetime.date, recipient: dict, mass: str = "") -> dict:
    """The stamp of a SIMPLIFIED download: same keys as :func:`build_stamp`, no NDA reference - the model is envelope only.  ``mass`` is
    the text of the total mass of the real model (written into the STEP header)."""
    notice = "%s - not for manufacture; no electromagnetic construction detail" % SIMPLIFIED_NOTICE
    who = recipient["name"] + (", " + recipient["company"] if recipient.get("company") else "")
    line = ("AeroStator download %s; SIMPLIFIED model %s%s; recipient %s <%s>; no NDA required (envelope only); date %s%s; %s" % (
        download_id, motor_id, (" (" + version + ")") if version else "", who, recipient["email"], day.isoformat(),
        ("; mass " + mass) if mass else "", notice))
    return dict(download_id=download_id, motor_id=motor_id, version=version, date=day.isoformat(), recipient=recipient["name"],
                email=recipient["email"], company=recipient.get("company") or "", nda_ref="none (simplified model)", notice=notice,
                line=line, simplified=True, mass=mass)


def stamp_step(data: bytes, *, filename: str, customer: str, day: datetime.date, motor_id: str,
               version: str = "", when: Optional[datetime.datetime] = None, stamp: Optional[dict] = None) -> bytes:
    """Rewrite the ISO 10303-21 HEADER section: description, file name, author and organisation carry the customer name, the
    date and the model version - and, with ``stamp`` (:func:`build_stamp`), the download id, the recipient company, the NDA
    reference and the confidentiality notice.  The DATA section is copied byte for byte."""
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
    if stamp:
        if stamp.get("simplified"):
            desc = "MOTRES AeroStator SIMPLIFIED model %s%s (envelope only). Downloaded by %s on %s." % (
                motor_id, (" " + version) if version else "", customer, day.isoformat())
        descs = [stamp["notice"], desc,
                 "Download ID: %s" % stamp["download_id"],
                 "Recipient: %s%s <%s>" % (stamp["recipient"], (", " + stamp["company"]) if stamp["company"] else "", stamp["email"]),
                 "NDA reference: %s" % stamp["nda_ref"],
                 "Date: %s" % stamp["date"]] + (["Mass of the real model: %s" % stamp["mass"]] if stamp.get("mass") else [])
        authors = "'%s'%s" % (cust, (",'" + q(stamp["company"]) + "'") if stamp["company"] else "")
        authorisation = ("Downloaded by %s %s; download %s; SIMPLIFIED - envelope only" if stamp.get("simplified")
                         else "Licensed to %s %s; download %s; NDA %s") % ((customer, day.isoformat(), stamp["download_id"])
                                                                         + (() if stamp.get("simplified") else (stamp["nda_ref"],)))
        fdesc = "FILE_DESCRIPTION((%s),'2;1');\n" % ",".join("'%s'" % q(d, 400) for d in descs)
        comment = "/* %s */\n" % q(stamp["line"], 700)
    else:
        authors = "'%s'" % cust
        authorisation = "Licensed to %s %s" % (customer, day.isoformat())
        fdesc = "FILE_DESCRIPTION(('%s'),'2;1');\n" % q(desc, 400)
        comment = "/* %s */\n" % q(desc, 400)
    new = ("ISO-10303-21;\nHEADER;\n" + comment + fdesc
           + "FILE_NAME('%s','%s',(%s),('%s'),'Open CASCADE STEP processor','FreeCAD','%s');\n" % (
               q(filename), ts, authors, org, q(authorisation, 300))
           + schema + "\n")
    return new.encode("latin-1") + rest


def stamp_of(step: bytes) -> dict:
    """Read the stamp back (tests, support): the FILE_NAME entity and the download id of a stamped file."""
    head = step[:step.find(b"ENDSEC;")].decode("latin-1")
    m = re.search(r"FILE_NAME\s*\(\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'\s*,\s*\(\s*'((?:[^']|'')*)'", head)
    out = dict(filename=m.group(1), timestamp=m.group(2), customer=m.group(3)) if m else {}
    d = re.search(r"Download ID: (d[0-9]{8}-[0-9a-f]{12})", head)
    if d:
        out["download_id"] = d.group(1)
    return out


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
    if row.get("nda") is True:                              # into the verification case timeline too (this log stays the master)
        try:
            from motor_ai_sim import verification as V
            V.note_download(row)
        except Exception as e:                              # noqa: BLE001 - the delivery is already logged; the case timeline is a copy
            log.warning("verification: download %s not noted in its case (%s)", row.get("download_id"), type(e).__name__)
    return row


def _all_rows() -> list[dict]:
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
    return rows


def _has(row_val, needle: str) -> bool:
    return needle.casefold() in str(row_val or "").casefold()


def read_log(limit: int = 200, *, user: str = "", company: str = "", motor: str = "", nda_version: str = "",
             kind: str = "") -> list[dict]:
    """Newest first.  The filters are case-insensitive substrings (user: id / e-mail / name; company; motor: die, config or
    motor id; nda_version: the NDA version or admin-flag / internal); ``kind`` is exact (``step``)."""
    rows = _all_rows()
    if user:
        rows = [r for r in rows if _has(r.get("user"), user) or _has(r.get("name"), user)]
    if company:
        rows = [r for r in rows if _has(r.get("company"), company)]
    if motor:
        rows = [r for r in rows if _has(r.get("motor_id"), motor) or _has(r.get("die"), motor) or _has(r.get("config"), motor)]
    if nda_version:
        rows = [r for r in rows if _has((r.get("nda_basis") or {}).get("version") or (r.get("nda_basis") or {}).get("ref")
                                        or (r.get("nda_basis") or {}).get("type"), nda_version)]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    return rows[-limit:][::-1]


def trace(text: str) -> dict:
    """Which download is this?  ``text`` is anything pasted: a STEP header line, a download id, a sha256.
    -> ``{ids, hashes, matches}``: every download id and sha256 found in the text and the log rows they point at (by the
    id, or by the sha256 of the delivered file)."""
    text = str(text or "")
    ids = list(dict.fromkeys(ID_RE.findall(text)))
    hashes = list(dict.fromkeys(SHA_RE.findall(text.lower())))
    rows = [r for r in _all_rows() if r.get("download_id") in ids or r.get("sha256") in hashes]
    return dict(ids=ids, hashes=hashes, matches=rows[::-1])


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
