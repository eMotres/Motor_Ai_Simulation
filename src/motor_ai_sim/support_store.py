"""What a VISITOR said to the assistant, and who asked us for access.

The in-app assistant answers signed-out visitors on the landing page
(``routes/support.py``).  Until now those conversations went nowhere: the
provider answered, the browser printed it, and the team never learned that
somebody had stood in the doorway and asked how to get in.  The owner's
question on 2026-09-17 was exactly that — *"как сообщения, которые они пишут
боту, будут доходить до нас?"*

Two stores, both under the SAME root as ``users.json`` (the identity/admin data
root — ``config/`` on this workstation, ``/srv/motres/config`` on the server),
never under a per-user workspace: a visitor has no workspace, and this is the
team's inbox, not anybody's saved work.

* ``support/visitor_chats/<YYYY-MM-DD>.jsonl`` — one line per visitor TURN
  (question + answer + how it was answered).  Append-only, capped per day,
  pruned after ``RETENTION_DAYS``.  A SIGNED-IN user's chat is never written
  here: they have a name, a Report tab and a session log already, and logging
  their questions would be surveillance of a customer rather than a doorbell.
* ``support/access_requests.json`` — the structured requests: the contact
  details the assistant collected, the conversation they came out of, and a
  status the team moves (``new`` → ``contacted`` / ``invited`` / ``declined``).
  Repeat requests from one address inside ``MERGE_WINDOW_S`` merge into the
  record that already exists instead of filling the inbox with the same person.

The visitor's IP is never stored: what is kept is a salted hash of it, which is
enough to tell two visitors apart (and to recognise the same one tomorrow) and
not enough to be a record of who read the landing page.

Nothing in here may raise on a bad day.  A doorbell that takes the door down
when it cannot ring is worse than no doorbell: every write is wrapped, and a
failure costs one log line and the visitor's reply still goes out.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from motor_ai_sim.config import DEFAULT_CONFIG_PATH
from motor_ai_sim.json_store import mutate_json, read_json

log = logging.getLogger(__name__)

#: Day logs older than this are deleted on the next append.
RETENTION_DAYS = 90
#: One day's log stops growing here (a scripted visitor must not fill a disk).
DAY_MAX_BYTES = 4 * 1024 * 1024
#: A second request from the same e-mail inside this window updates the first.
MERGE_WINDOW_S = 24 * 3600
#: How much of one turn is kept.  A visitor's question fits in a paragraph, and
#: the route has already cut what it sent the provider to ANON_MAX_CHARS.
MAX_TEXT = 4000
#: The statuses the team moves a request through.
STATUSES = ("new", "contacted", "invited", "declined")

_LOCK = threading.RLock()
_ENV_ROOT = "SUPPORT_STORE_DIR"
_prune_last = 0.0
_full_warned: dict[str, bool] = {}


# ── where ────────────────────────────────────────────────────────────────────

def root() -> Path:
    """The store root — beside ``users.json`` unless ``SUPPORT_STORE_DIR`` says
    otherwise.  Read PER CALL so a test can point it at a tmp dir and so a
    deployment can move it without a code change."""
    env = (os.environ.get(_ENV_ROOT) or "").strip()
    if env:
        return Path(env).expanduser()
    return Path(DEFAULT_CONFIG_PATH).parent / "support"


def chats_dir() -> Path:
    return root() / "visitor_chats"


def requests_file() -> Path:
    return root() / "access_requests.json"


# ── identity without an identity ─────────────────────────────────────────────

def _salt() -> str:
    """A stable per-deployment salt, so the same visitor hashes the same way
    tomorrow.  The auth secret is the one long-lived random string this
    deployment already keeps; if it cannot be read we still hash (a constant
    salt is weaker, but a store that refuses to record a visitor because a file
    was locked for 40 ms is worse)."""
    try:
        from motor_ai_sim import users as U
        return U._secret()
    except Exception:                                        # noqa: BLE001
        return "motres-support-visitor"


def ip_hash(ip: str) -> str:
    """A salted, truncated hash of the visitor's address — never the address."""
    if not ip:
        return ""
    return hashlib.sha256((_salt() + "|" + ip).encode("utf-8")).hexdigest()[:12]


def conversation_id(ip_h: str, messages: list) -> str:
    """Which CONVERSATION this turn belongs to.

    The widget holds the whole history in the browser and re-sends it every
    turn, so the FIRST user message is a stable name for the conversation for as
    long as the visitor keeps the panel open — no cookie, no session id, and
    nothing that survives them closing the tab (which is exactly right: the next
    visit IS a new conversation).
    """
    first = ""
    for m in messages or ():
        if isinstance(m, dict) and m.get("role") == "user":
            first = str(m.get("content") or "")[:200]
            break
    return hashlib.sha1(f"{ip_h}|{first}".encode("utf-8")).hexdigest()[:8]


def _clip(v, n: int = MAX_TEXT) -> str:
    return str(v or "")[:n]


def _day(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if ts is None else ts))


# ── the visitor chat log ─────────────────────────────────────────────────────

def log_visitor_turn(*, ip: str = "", user_agent: str = "", messages: list | None = None,
                     user_message: str = "", reply: str = "", source: str = "",
                     model: str = "", limit: str = "") -> None:
    """Append ONE visitor turn to today's log.  Never raises.

    Called for an anonymous caller only — including the turns the rate limiter
    refused (``limit`` names the cap), because "forty questions from one address
    and then a wall" is a thing the team must be able to see.
    """
    try:
        ip_h = ip_hash(ip)
        rec = {
            "ts": round(time.time(), 3),
            "conv": conversation_id(ip_h, messages or []),
            "ip_hash": ip_h,
            "ua": _clip(user_agent, 300),
            "user": _clip(user_message),
            "reply": _clip(reply),
            "source": _clip(source, 40),
            "model": _clip(model, 80),
            "limit": _clip(limit, 40) or None,
        }
        d = chats_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_day()}.jsonl"
        with _LOCK:
            if path.is_file() and path.stat().st_size >= DAY_MAX_BYTES:
                if not _full_warned.get(path.name):
                    _full_warned[path.name] = True
                    log.warning("support_store: %s reached %d bytes — today's "
                                "visitor turns are no longer logged", path.name,
                                DAY_MAX_BYTES)
                return
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        _prune()
    except Exception as e:                                   # noqa: BLE001
        log.warning("support_store: could not log a visitor turn (%s: %s)",
                    type(e).__name__, e)


def _prune(force: bool = False) -> None:
    """Delete day logs older than ``RETENTION_DAYS``.  At most once an hour."""
    global _prune_last
    now = time.time()
    if not force and now - _prune_last < 3600:
        return
    _prune_last = now
    try:
        cutoff = _day(now - RETENTION_DAYS * 86400)
        for p in chats_dir().glob("*.jsonl"):
            if p.stem < cutoff:
                p.unlink(missing_ok=True)
                log.info("support_store: pruned visitor chat log %s", p.name)
    except Exception:                                        # noqa: BLE001
        pass


def days() -> list[str]:
    """Every day that has a visitor-chat log, newest first."""
    try:
        return sorted((p.stem for p in chats_dir().glob("*.jsonl")
                       if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem)), reverse=True)
    except Exception:                                        # noqa: BLE001
        return []


def read_day(day: str = "") -> list[dict]:
    """Every turn of one day, oldest first.  `''` = the newest day with a log."""
    day = (day or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        d = days()
        if not d:
            return []
        day = d[0]
    path = chats_dir() / f"{day}.jsonl"
    out: list[dict] = []
    try:
        if not path.is_file():
            return []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:                            # noqa: BLE001
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except Exception as e:                                   # noqa: BLE001
        log.warning("support_store: %s unreadable (%s: %s)", path,
                    type(e).__name__, e)
    return out


def conversations(day: str = "") -> dict:
    """One day's turns, grouped into conversations, newest conversation first."""
    turns = read_day(day)
    groups: dict[str, dict] = {}
    for t in turns:
        key = str(t.get("conv") or t.get("ip_hash") or "?")
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"conv": key, "ip_hash": t.get("ip_hash") or "",
                               "ua": t.get("ua") or "",
                               "started": t.get("ts") or 0.0,
                               "last": t.get("ts") or 0.0, "turns": []}
        g["turns"].append(t)
        g["last"] = max(float(g["last"] or 0.0), float(t.get("ts") or 0.0))
        g["started"] = min(float(g["started"] or 0.0) or float(t.get("ts") or 0.0),
                           float(t.get("ts") or 0.0))
    rows = sorted(groups.values(), key=lambda g: g.get("last") or 0.0, reverse=True)
    d = (day or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or ""):
        all_days = days()
        d = all_days[0] if all_days else _day()
    return {"day": d, "days": days(), "count": len(rows), "conversations": rows}


def turn_count(day: str = "") -> int:
    return len(read_day(day))


# ── the access-request inbox ─────────────────────────────────────────────────

def _new_id() -> str:
    return "req_" + hashlib.sha1(
        f"{time.time()}{os.getpid()}{os.urandom(8)!r}".encode("utf-8")).hexdigest()[:8]


def _public(rec: dict) -> dict:
    return {
        "id": rec.get("id") or "",
        "ts": float(rec.get("ts") or 0.0),
        "updated": float(rec.get("updated") or rec.get("ts") or 0.0),
        "name": rec.get("name") or "",
        "company": rec.get("company") or "",
        "email": rec.get("email") or "",
        "note": rec.get("note") or "",
        "status": rec.get("status") if rec.get("status") in STATUSES else "new",
        "ip_hash": rec.get("ip_hash") or "",
        "ua": rec.get("ua") or "",
        "merged": int(rec.get("merged") or 1),
        "transcript": [t for t in (rec.get("transcript") or [])
                       if isinstance(t, dict)],
    }


def file_access_request(*, email: str, name: str = "", company: str = "",
                        note: str = "", transcript: list | None = None,
                        ip: str = "", user_agent: str = "") -> Optional[dict]:
    """Record one access request.  Returns the stored record, or None on failure.

    A second request from the same address inside ``MERGE_WINDOW_S`` UPDATES the
    existing record rather than creating a neighbour: the visitor who answers
    "and my company is ACME" one message later is the same person, and two rows
    that say half of it each is exactly the inbox nobody reads.  Only non-empty
    fields overwrite — a later turn that omits the company must not erase it.
    """
    email = (email or "").strip().lower()
    if not email:
        return None
    now = time.time()
    ip_h = ip_hash(ip)
    turns = [{"role": str(t.get("role") or ""), "content": _clip(t.get("content"))}
             for t in (transcript or []) if isinstance(t, dict)
             and t.get("role") in ("user", "assistant")][-40:]
    stored: dict = {}

    def _mutate(doc: dict):
        if not isinstance(doc, dict):
            doc = {}
        prev = None
        for rid, rec in doc.items():
            if not isinstance(rec, dict):
                continue
            if (rec.get("email") or "").strip().lower() != email:
                continue
            when = float(rec.get("updated") or rec.get("ts") or 0.0)
            if now - when <= MERGE_WINDOW_S:
                if prev is None or when > float(prev[1].get("updated") or 0.0):
                    prev = (rid, rec)
        if prev is None:
            rid = _new_id()
            rec = {"id": rid, "ts": now, "updated": now, "email": email,
                   "name": name.strip(), "company": company.strip(),
                   "note": note.strip(), "status": "new", "ip_hash": ip_h,
                   "ua": _clip(user_agent, 300), "merged": 1,
                   "transcript": turns}
            doc[rid] = rec
        else:
            rid, rec = prev
            for field, value in (("name", name), ("company", company),
                                 ("note", note)):
                if str(value or "").strip():
                    rec[field] = str(value).strip()
            rec["updated"] = now
            rec["merged"] = int(rec.get("merged") or 1) + 1
            rec["ip_hash"] = ip_h or rec.get("ip_hash") or ""
            if user_agent:
                rec["ua"] = _clip(user_agent, 300)
            # The newer transcript CONTAINS the older one (the widget re-sends
            # the whole history), so the longer of the two is the truth.
            if len(turns) >= len(rec.get("transcript") or []):
                rec["transcript"] = turns
            doc[rid] = rec
        stored.update(_public(rec))
        return doc

    try:
        requests_file().parent.mkdir(parents=True, exist_ok=True)
        mutate_json(requests_file(), _mutate, default={})
    except Exception as e:                                   # noqa: BLE001
        log.error("support_store: could not file an access request from %s "
                  "(%s: %s)", email, type(e).__name__, e)
        return None
    log.info("support_store: access request from %s (%s) — %s", email,
             stored.get("company") or "no company",
             "merged" if stored.get("merged", 1) > 1 else "new")
    return stored


def list_access_requests() -> list[dict]:
    """Every request, newest activity first.  Never raises."""
    try:
        doc = read_json(requests_file(), {})
    except Exception:                                        # noqa: BLE001
        doc = {}
    if not isinstance(doc, dict):
        return []
    rows = [_public(r) for r in doc.values() if isinstance(r, dict)]
    rows.sort(key=lambda r: r.get("updated") or 0.0, reverse=True)
    return rows


def new_request_count() -> int:
    return sum(1 for r in list_access_requests() if r["status"] == "new")


def set_status(request_id: str, status: str) -> Optional[dict]:
    """Move one request's status.  Returns the record, or None if unknown."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    out: dict = {}

    def _mutate(doc: dict):
        if not isinstance(doc, dict):
            return {}
        rec = doc.get(request_id)
        if isinstance(rec, dict):
            rec["status"] = status
            rec["status_at"] = time.time()
            doc[request_id] = rec
            out.update(_public(rec))
        return doc

    mutate_json(requests_file(), _mutate, default={})
    return out or None


def delete_request(request_id: str) -> bool:
    """Remove one request (a test row, a duplicate the team is done with)."""
    gone = {"yes": False}

    def _mutate(doc: dict):
        if not isinstance(doc, dict):
            return {}
        if doc.pop(request_id, None) is not None:
            gone["yes"] = True
        return doc

    mutate_json(requests_file(), _mutate, default={})
    return gone["yes"]
