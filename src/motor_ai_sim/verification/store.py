"""The verification case store: an append-only event log and, derived from it, one current-state file per case.

Where (``MOTRES_VERIFICATION_ROOT`` overrides; the default sits beside the NDA records, in the part of the shared tree that the
hourly restic backup covers)::

    <legal root>/verification/events.jsonl           every event of every case, one JSON object per line, NEVER rewritten
    <legal root>/verification/cases/<case id>.json   the current state of one case = reduce(events of the case); can always be rebuilt
    <legal root>/verification/index.json             {user: [case ids]}; rebuilt from the cases when missing
    <legal root>/verification/documents/<case id>/<doc id>.<ext>   documents the applicant uploaded (private, never served inline)

Nothing here is ever deleted.  An event: ``{ts, iso, case, type, actor, data}`` with ``actor`` = ``system`` | ``applicant`` |
``admin:<e-mail>``.  The reducer :func:`apply` is the only place where an event becomes state.

Event types: case_created, application_updated, check_run, status_changed, mail_out, reply_in, email_otp_sent, email_otp_failed,
email_verified, document_uploaded, admin_action, access_granted, access_revoked, download.
"""
from __future__ import annotations

import copy
import datetime
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from motor_ai_sim.private_files import ensure_private_dir, open_private

log = logging.getLogger(__name__)

_LOCK = threading.RLock()
EVENT_TYPES = frozenset("""case_created application_updated check_run status_changed mail_out reply_in email_otp_sent email_otp_failed
email_verified document_uploaded admin_action access_granted access_revoked download""".split())


def root() -> Path:
    env = (os.environ.get("MOTRES_VERIFICATION_ROOT") or "").strip()
    if env:
        return Path(env)
    from motor_ai_sim import nda as N
    return N.legal_root() / "verification"


def events_path() -> Path:
    return root() / "events.jsonl"


def cases_dir() -> Path:
    return root() / "cases"


def documents_dir() -> Path:
    return root() / "documents"


def index_path() -> Path:
    return root() / "index.json"


def new_case_id(now: Optional[datetime.datetime] = None) -> str:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return "v%s-%s" % (now.strftime("%Y%m%d"), secrets.token_hex(6))


def valid_case_id(cid: str) -> bool:
    import re
    return bool(re.fullmatch(r"v[0-9]{8}-[0-9a-f]{12}", str(cid or "")))


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- the reducer -----------------------------------------------------------------------------------------------------

def _blank(cid: str) -> dict:
    return {"id": cid, "user": "", "created": 0.0, "created_iso": "", "updated": 0.0, "updated_iso": "", "status": "PENDING",
            "status_reason": "", "status_actor": "", "status_at": 0.0, "reasons": [], "requests": [], "application": {}, "nda": {},
            "lang": "en", "emails": {"verified": [], "pending": {}}, "urls": [], "notes": [], "documents": [], "evidence": [],
            "decision": {}, "rounds": 0, "open_replies": 0, "requested": [], "reply_since_request": False, "correspondence": [],
            "access": {"granted": False, "at": 0.0, "by": "", "reason": ""}, "admin_actions": [], "downloads": 0,
            "last_download": None, "events": 0, "last_mail_ts": 0.0}


def apply(state: Optional[dict], ev: dict) -> dict:
    """Pure: the state after one more event."""
    s = copy.deepcopy(state) if state else _blank(ev["case"])
    t, d, ts, actor = ev["type"], ev.get("data") or {}, ev["ts"], ev.get("actor", "")
    s["events"] = int(s.get("events", 0)) + 1
    s["updated"], s["updated_iso"] = ts, ev["iso"]
    if t == "case_created":
        s.update(user=d["user"], created=ts, created_iso=ev["iso"], application=dict(d.get("application") or {}),
                 nda=dict(d.get("nda") or {}), lang=d.get("lang") or "en")
        s["status"], s["status_at"], s["status_actor"], s["status_reason"] = "PENDING", ts, "system", "case opened"
    elif t == "application_updated":
        s["application"].update(d.get("fields") or {})
        if d.get("lang"):
            s["lang"] = d["lang"]
    elif t == "check_run":
        s["evidence"] = d.get("evidence") or []
        s["decision"] = d.get("decision") or {}
        s["rounds"] = int(s.get("rounds", 0)) + 1
    elif t == "status_changed":
        s["status"], s["status_reason"], s["status_actor"], s["status_at"] = d["status"], d.get("reason", ""), actor, ts
        s["reasons"] = d.get("reasons") or []
        s["requests"] = d.get("requests") or []
        if d["status"] == "MORE_INFORMATION_REQUIRED":
            s["reply_since_request"] = False
    elif t == "mail_out":
        s["correspondence"].append({"dir": "out", "ts": ts, "iso": ev["iso"], "to": d.get("to"), "subject": d.get("subject"),
                                    "body": d.get("body"), "codes": d.get("codes") or [], "delivered": d.get("delivered"), "by": actor})
        s["last_mail_ts"] = ts
        for c in d.get("codes") or []:
            if c not in s["requested"]:
                s["requested"].append(c)
        s["reply_since_request"] = False
    elif t == "reply_in":
        s["correspondence"].append({"dir": "in", "ts": ts, "iso": ev["iso"], "urls": d.get("urls") or [], "note": d.get("note") or "",
                                    "fields": d.get("fields") or {}})
        for u in d.get("urls") or []:
            if u not in s["urls"]:
                s["urls"].append(u)
        if d.get("note"):
            s["notes"].append({"ts": ts, "note": d["note"]})
        s["reply_since_request"] = bool(s["requested"])
        if d.get("counts", True):
            s["open_replies"] = int(s.get("open_replies", 0)) + 1
    elif t == "email_otp_sent":
        s["emails"]["pending"][d["email"]] = {"hash": d["hash"], "expires": d["expires"], "attempts": 0, "sent": ts}
    elif t == "email_otp_failed":
        p = s["emails"]["pending"].get(d["email"])
        if p:
            p["attempts"] = int(p.get("attempts", 0)) + 1
    elif t == "email_verified":
        s["emails"]["pending"].pop(d["email"], None)
        if not any(v["email"] == d["email"] for v in s["emails"]["verified"]):
            s["emails"]["verified"].append({"email": d["email"], "at": ts, "method": d.get("method", "otp")})
    elif t == "document_uploaded":
        s["documents"].append({k: d.get(k) for k in ("id", "name", "ext", "bytes", "sha256")} | {"at": ts})
    elif t == "admin_action":
        s["admin_actions"].append({"ts": ts, "iso": ev["iso"], "actor": actor, "action": d.get("action"), "reason": d.get("reason", "")})
    elif t == "access_granted":
        s["access"] = {"granted": True, "at": ts, "by": actor, "reason": d.get("reason", "")}
    elif t == "access_revoked":
        s["access"] = {"granted": False, "at": ts, "by": actor, "reason": d.get("reason", "")}
    elif t == "download":
        s["downloads"] = int(s.get("downloads", 0)) + 1
        s["last_download"] = {k: d.get(k) for k in ("download_id", "file", "motor_id", "sha256", "kind")} | {"at": ts}
    return s


# -- files -----------------------------------------------------------------------------------------------------------

def _read_json(p: Path):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise OSError("verification file %s unreadable: %s" % (p, e)) from e


def _write_json(p: Path, obj) -> None:
    ensure_private_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open_private(tmp, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, sort_keys=True, indent=1)
    os.replace(tmp, p)


def get(case_id: str) -> Optional[dict]:
    if not valid_case_id(case_id):
        return None
    try:
        return _read_json(cases_dir() / (case_id + ".json"))
    except OSError:
        return rebuild(case_id)


def _index() -> dict:
    try:
        idx = _read_json(index_path())
    except OSError:
        idx = None
    if isinstance(idx, dict):
        return idx
    return reindex()


def reindex() -> dict:
    idx: dict = {}
    d = cases_dir()
    if d.is_dir():
        for p in sorted(d.glob("v*.json")):
            try:
                c = _read_json(p) or {}
            except OSError:
                continue
            if c.get("user"):
                idx.setdefault(c["user"], []).append(c["id"])
    for ids in idx.values():
        ids.sort()
    if idx:
        with _LOCK:
            _write_json(index_path(), idx)
    return idx


def cases_of(user: str) -> list[dict]:
    out = []
    for cid in _index().get(str(user or "").strip().lower(), []):
        c = get(cid)
        if c:
            out.append(c)
    return out


def latest_of(user: str) -> Optional[dict]:
    cs = cases_of(user)
    return max(cs, key=lambda c: c.get("created", 0)) if cs else None


def list_cases(status: str = "") -> list[dict]:
    out = []
    d = cases_dir()
    if d.is_dir():
        for p in d.glob("v*.json"):
            try:
                c = _read_json(p)
            except OSError:
                continue
            if c and (not status or c.get("status") == status):
                out.append(c)
    return sorted(out, key=lambda c: -c.get("updated", 0))


def events_of(case_id: str) -> list[dict]:
    out = []
    p = events_path()
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if case_id in line:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("case") == case_id:
                        out.append(e)
    except OSError:
        return []
    return out


def rebuild(case_id: str) -> Optional[dict]:
    """The state of a case from its events (the event log is the master; the state file is a cache of it)."""
    state = None
    for e in events_of(case_id):
        state = apply(state, e)
    if state is not None:
        with _LOCK:
            _write_json(cases_dir() / (case_id + ".json"), state)
    return state


def append(case_id: str, type_: str, data: Optional[dict] = None, actor: str = "system", now: Optional[float] = None) -> dict:
    """Append ONE event, then update the case state.  Raises OSError when the event cannot be written (the caller must not
    proceed as if it had happened: an action nobody can account for is not done)."""
    if type_ not in EVENT_TYPES:
        raise ValueError("unknown event type %r" % type_)
    if not valid_case_id(case_id):
        raise ValueError("bad case id")
    ts = round(time.time() if now is None else now, 3)
    ev = {"ts": ts, "iso": _iso(ts), "case": case_id, "type": type_, "actor": actor, "data": data or {}}
    with _LOCK:
        cur = None
        if type_ != "case_created":
            cur = get(case_id) or rebuild(case_id)
            if cur is None:
                raise ValueError("unknown case %s" % case_id)
        ensure_private_dir(root())
        ensure_private_dir(cases_dir())
        with open_private(events_path(), "a") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
        state = apply(cur, ev)
        _write_json(cases_dir() / (case_id + ".json"), state)
        if type_ == "case_created":
            idx = _index()
            idx.setdefault(state["user"], [])
            if case_id not in idx[state["user"]]:
                idx[state["user"]].append(case_id)
            _write_json(index_path(), idx)
    return ev
