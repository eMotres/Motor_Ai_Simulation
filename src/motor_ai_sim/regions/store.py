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

PER-ACCOUNT FLAGS (stage 3, written only by ``scripts/migrate_account_region.py``)::

    "region_guard":  {"on": true, "by": "<actor>", "bundle": "<bundle id>", "iso": "..."}
    "region_freeze": {"on": true, "by": "<actor>", "reason": "...", "iso": "..."}

``region_guard`` turns the wrong-region answer on for THIS account (its data now live in its home region) whatever the
global ``MOTRES_REGION_GUARD`` says; ``region_freeze`` refuses the account's writes while its bundle is built and moved.
Staff accounts are never guarded or frozen.
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


STAFF_ROLES = frozenset({"admin", "engineer"})


def is_staff(rec: Optional[dict]) -> bool:
    return isinstance(rec, dict) and str(rec.get("role") or "user") in STAFF_ROLES


def is_guarded(rec: Optional[dict]) -> bool:
    """The per-account guard: on for a migrated, non-staff account."""
    g = rec.get("region_guard") if isinstance(rec, dict) else None
    return isinstance(g, dict) and g.get("on") is True and not is_staff(rec)


def is_frozen(rec: Optional[dict]) -> bool:
    """Writes of the account are refused while its migration bundle is built (never staff)."""
    f = rec.get("region_freeze") if isinstance(rec, dict) else None
    return isinstance(f, dict) and f.get("on") is True and not is_staff(rec)


def _set_flag(email: str, field: str, on: bool, **info: Any) -> Dict[str, Any]:
    U = _users()
    em = U._norm(email)
    with U._LOCK:
        users = U._load()
        rec = users.get(em)
        if not isinstance(rec, dict):
            raise KeyError(em)
        if on and is_staff(rec):
            raise ValueError("staff accounts work on both servers and are never guarded or frozen")
        prev = rec.get(field) if isinstance(rec.get(field), dict) else None
        if on:
            rec[field] = {"on": True, **{k: str(v)[:200] for k, v in info.items()},
                          "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        else:
            rec.pop(field, None)
        U._save(users)
    return {"email": em, "field": field, "on": on, "previous": prev}


def set_region_guard(email: str, on: bool, *, by: str = "", bundle: str = "") -> Dict[str, Any]:
    """Switch the per-account guard (the migration's last step; ``on=False`` only to roll a migration back)."""
    return _set_flag(email, "region_guard", on, by=by, bundle=bundle)


def set_region_freeze(email: str, on: bool, *, by: str = "", reason: str = "") -> Dict[str, Any]:
    return _set_flag(email, "region_freeze", on, by=by, reason=reason)


_FLAG_CACHE: Dict[str, Any] = {"key": None, "emails": frozenset()}


def flagged_emails() -> frozenset:
    """E-mails with a per-account guard or freeze, cached on the registry file's (mtime, size): the middleware asks this
    on every request, and nothing at all is flagged on a server that has never run a migration."""
    U = _users()
    try:
        st = os.stat(U._USERS_FILE)
        key = (str(U._USERS_FILE), st.st_mtime_ns, st.st_size)
    except OSError:
        return frozenset()
    if _FLAG_CACHE["key"] != key:
        _FLAG_CACHE["emails"] = frozenset(em for em, rec in U._load_soft().items()
                                          if is_guarded(rec) or is_frozen(rec))
        _FLAG_CACHE["key"] = key
    return _FLAG_CACHE["emails"]


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
    doc = {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "region": r, "entries": clean,
           "duplicates": duplicates(clean)}
    p = directory_path()
    with _DIR_LOCK:
        ensure_private_dir(p.parent)
        tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
        with open_private(tmp, "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        os.replace(tmp, p)
        chmod_private(p)
    return doc


def duplicates(entries: Optional[Dict[str, Any]] = None) -> List[str]:
    """E-mails BOTH regions host as their own account: a sign-up raced on the two servers within one sync interval, or
    with the peer down (DESIGN section 8).  Shown to the admin, never merged automatically.  A migrated account is not a
    duplicate: its record here names the peer as its home."""
    U = _users()
    if entries is None:
        entries = load_remote_directory().get("entries") or {}
    local = RC.local_region()
    out = []
    for em, rec in U._load_soft().items():
        if isinstance(rec, dict) and effective_region(rec) == local and em in entries:
            out.append(em)
    return sorted(out)


def upsert_remote_entry(email: str, entry: Dict[str, Any], region: str) -> Dict[str, Any]:
    """Put ONE peer entry into the remote directory now (the migration's hand-over, so this server sends the person on
    at once instead of after the next sync).  Same cleaning as a full sync."""
    U = _users()
    r = RC.norm_region(region)
    if r is None or r == RC.local_region():
        raise ValueError("the remote directory holds the peer region only")
    em = U._norm(email)
    e = _clean_entry(entry, r)
    if not em or "@" not in em or e is None:
        raise ValueError("not a directory entry of the peer region")
    cur = load_remote_directory()
    entries = dict(cur.get("entries") or {}) if RC.norm_region(cur.get("region")) in (None, r) else {}
    entries[em] = e
    return store_remote_directory(entries, r)


def peer_lookup(email: str, *, timeout: float = 2.0, transport: Any = None) -> Optional[Dict[str, Any]]:
    """Ask the peer synchronously whether it hosts ``email`` (sign-up uniqueness, DESIGN section 8).

    -> ``{"hosted": True, "entry": {...}}`` / ``{"hosted": False}``, or ``None`` when no peer is configured or it did
    not answer in ``timeout`` seconds (the caller then proceeds locally; the next sync flags a duplicate)."""
    if not RC.peer_configured():
        return None
    from urllib.parse import quote
    from motor_ai_sim.regions import peer as P
    U = _users()
    em = U._norm(email)
    got = P.get("/directory?email=" + quote(em, safe=""), timeout=timeout, transport=transport)
    if not isinstance(got, dict) or RC.norm_region(got.get("region")) != RC.peer_region():
        return None
    e = _clean_entry((got.get("entries") or {}).get(em), RC.peer_region())
    return {"hosted": True, "entry": e} if e is not None else {"hosted": False}


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
