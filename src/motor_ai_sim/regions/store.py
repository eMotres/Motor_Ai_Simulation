"""The home region of an account, and the global account directory.

HOME REGION (users.json, beside the account's other registry fields)::

    "home_region": "us",
    "home_region_set": {"by": "admin" | "classifier", "actor": "<admin e-mail>", "reason": "...", "iso": "..."}

An account without the field is ``eu`` (today every account lives on the EU server).  Only an admin override
(``set_home_region``, audited) or the classifier's ``--apply`` run writes it; the classifier never overwrites an admin's
choice.  Changing the field moves NO data: the migration of a US account's workspace / projects / files is stage 3
(docs/regions/DESIGN.md), and until it runs the account keeps being served where its data are.

GLOBAL ACCOUNT DIRECTORY (``<identity dir>/region_directory.json``): the accounts the OTHER region hosts, as the peer
reported them — ``{email: {id, region, tier, created}}`` and nothing else (no name, company, country or any other
personal data: those stay in the home region).  It keeps e-mail addresses unique across both regions and tells the
login of either server where an account lives.  The local half is derived from users.json on every call, never copied.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from motor_ai_sim.file_lock import store_lock
from motor_ai_sim.regions import config as RC

DIRECTORY_FIELDS = ("id", "region", "tier", "created")

_DIR_LOCK = store_lock(lambda: directory_path())


def _users():
    from motor_ai_sim import users as U
    return U


def effective_region(rec: Optional[dict]) -> str:
    """The home region stored on a registry record; ``eu`` when absent or malformed."""
    if not isinstance(rec, dict):
        return RC.DEFAULT_REGION
    return RC.norm_region(rec.get("home_region"), RC.DEFAULT_REGION) or RC.DEFAULT_REGION


def region_source(rec: Optional[dict]) -> str:
    """``admin`` | ``classifier`` | ``default`` — who decided the region shown."""
    if not isinstance(rec, dict) or RC.norm_region(rec.get("home_region")) is None:
        return "default"
    src = rec.get("home_region_set") if isinstance(rec.get("home_region_set"), dict) else {}
    return "admin" if src.get("by") == "admin" else "classifier"


def get_home_region(email: str) -> str:
    U = _users()
    return effective_region(U.get_user(email))


def set_home_region(email: str, region: str, *, by: str, actor: str = "", reason: str = "",
                    respect_admin: bool = False) -> Dict[str, Any]:
    """Store the home region of one account -> ``{email, region, previous, changed, source}``.

    ``by`` is ``admin`` (the admin override, which the caller audits) or ``classifier``.  With ``respect_admin`` an
    existing admin choice is kept (the classifier's apply run).  Raises ``KeyError`` for an unknown account and
    ``ValueError`` for an unknown region.
    """
    U = _users()
    r = RC.norm_region(region)
    if r is None:
        raise ValueError(f"region must be one of {RC.REGIONS}")
    if by not in ("admin", "classifier"):
        raise ValueError("by must be admin or classifier")
    em = U._norm(email)
    with U._LOCK:
        users = U._load()
        rec = users.get(em)
        if not isinstance(rec, dict):
            raise KeyError(em)
        prev = effective_region(rec)
        if respect_admin and region_source(rec) == "admin":
            return {"email": em, "region": prev, "previous": prev, "changed": False, "source": "admin"}
        changed = prev != r or region_source(rec) == "default"
        rec["home_region"] = r
        rec["home_region_set"] = {"by": by, "actor": str(actor or "")[:200], "reason": str(reason or "")[:200],
                                  "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        U._save(users)
    return {"email": em, "region": r, "previous": prev, "changed": changed, "source": by}


# ── the global account directory ─────────────────────────────────────────────

def directory_path() -> Path:
    env = (os.environ.get("MOTRES_REGION_DIRECTORY_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    U = _users()
    return Path(U._USERS_FILE).parent / "region_directory.json"


def directory_entry(email: str, rec: dict) -> Dict[str, Any]:
    """The ONLY fields of an account that ever leave its home region (plus the e-mail key)."""
    from motor_ai_sim.workspace import workspace_id
    return {"id": workspace_id(email), "region": effective_region(rec), "tier": str(rec.get("role") or "user"),
            "created": rec.get("created")}


def local_directory(region: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Directory entries of the accounts THIS server hosts (home region == ``region``, default the local one)."""
    U = _users()
    want = RC.norm_region(region, None) or RC.local_region()
    out: Dict[str, Dict[str, Any]] = {}
    for em, rec in sorted(U._load_soft().items()):
        if isinstance(rec, dict) and effective_region(rec) == want:
            out[em] = directory_entry(em, rec)
    return out


def _clean_entry(raw: Any, region: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    r = RC.norm_region(raw.get("region"))
    if r != region:
        return None
    created = raw.get("created")
    return {"id": str(raw.get("id") or "")[:64], "region": r, "tier": str(raw.get("tier") or "user")[:20],
            "created": created if isinstance(created, (str, int, float)) else None}


def load_remote_directory() -> Dict[str, Any]:
    p = directory_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"updated": None, "region": None, "entries": {}}
    if not isinstance(d, dict) or not isinstance(d.get("entries"), dict):
        return {"updated": None, "region": None, "entries": {}}
    return d


def store_remote_directory(entries: Dict[str, Any], region: str) -> Dict[str, Any]:
    """Replace the remote half with what the peer (``region``) reported.  Entries of any other region, extra fields and
    malformed rows are dropped: the peer can never write personal data or a local account into this file."""
    from motor_ai_sim.private_files import chmod_private, ensure_private_dir, open_private
    r = RC.norm_region(region)
    if r is None or r == RC.local_region():
        raise ValueError("the remote directory holds the peer region only")
    U = _users()
    clean: Dict[str, Dict[str, Any]] = {}
    for em, raw in (entries or {}).items():
        key = U._norm(str(em))
        e = _clean_entry(raw, r)
        if key and "@" in key and e is not None:
            clean[key] = e
    doc = {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "region": r, "entries": clean}
    p = directory_path()
    with _DIR_LOCK:
        ensure_private_dir(p.parent)
        tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
        with open_private(tmp, "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        os.replace(tmp, p)
        chmod_private(p)
    return doc


def region_of_email(email: str) -> Optional[str]:
    """Where an e-mail lives: the local registry first, then the remote directory; ``None`` = unknown anywhere."""
    U = _users()
    em = U._norm(email)
    rec = U._load_soft().get(em)
    if isinstance(rec, dict):
        return effective_region(rec)
    e = (load_remote_directory().get("entries") or {}).get(em)
    return RC.norm_region((e or {}).get("region")) if isinstance(e, dict) else None


def email_taken_elsewhere(email: str) -> bool:
    """True when the OTHER region already hosts this e-mail (stage 3 wires it into sign-up; unused before)."""
    U = _users()
    em = U._norm(email)
    if em in U._load_soft():
        return False
    return em in (load_remote_directory().get("entries") or {})


def region_map(emails: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """``{email: region}`` for the admin tables (local registry + remote directory)."""
    U = _users()
    out = {em: effective_region(rec) for em, rec in U._load_soft().items() if isinstance(rec, dict)}
    for em, e in (load_remote_directory().get("entries") or {}).items():
        if em not in out and isinstance(e, dict) and RC.norm_region(e.get("region")):
            out[em] = RC.norm_region(e.get("region"))
    if emails is not None:
        want = {U._norm(e) for e in emails}
        out = {k: v for k, v in out.items() if k in want}
    return out


def counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    c = {r: 0 for r in RC.REGIONS}
    for row in rows:
        r = RC.norm_region(row.get("region"))
        if r:
            c[r] += 1
    return c
