"""The owner queue: decisions, questions and messages that agent sessions post for the owner (docs/OWNER_QUEUE.md).

The app only RECORDS.  It never executes anything on an answer: the asking session reads the answer back and acts under its own rules.

Store: ``<shared_root>/owner_queue/`` (``MOTRES_OWNER_QUEUE_DIR`` overrides it, tests use that)

* ``items.jsonl``  append-only; each row is a FULL snapshot of one item, the current state of an id is its LAST row;
* ``tokens.json``  ``[{name, token_hash, created_at, created_by, revoked_at, last_used_at}]``.  A token is ``oq_`` + 40 urlsafe
                   characters, returned exactly once at creation; only its sha256 is stored (never the token, never logged).

A damaged file is never silently reset: reads and writes raise :class:`StoreDamaged` (the routes answer 503) until the owner looks.
"""
from __future__ import annotations

import hashlib
import calendar
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim.file_lock import store_lock
from motor_ai_sim.private_files import chmod_private, ensure_private_dir, open_private

TOKEN_PREFIX = "oq_"
KINDS = ("decision", "question", "message", "catalog_write", "outside_message")
OPTION_KINDS = ("decision",)                                    # the client sends 2-5 options only for a decision
#: Kinds whose answer is an approval of exact content (owner 2026-11-25): the server fixes the options, the detail is required.
APPROVAL_KINDS = ("catalog_write", "outside_message")
APPROVAL_OPTIONS = ("Approve", "Reject")
MAX_RECIPIENT = 500
BLOCKING_KINDS = ("decision", "question", "catalog_write", "outside_message")
STATES = ("open", "answered", "read", "withdrawn")

MAX_ASKED_BY = 80
MAX_QUESTION = 300
MAX_LABEL = 40
MAX_DETAIL = 4000
MAX_BLOCKS = 200
MAX_PROJECT = 120
MAX_ANSWER_TEXT = 4000
MIN_OPTIONS, MAX_OPTIONS = 2, 5
MAX_OPEN_PER_TOKEN = 200
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_ID_RE = re.compile(r"^oq-[0-9a-f]{12}$")
_TOUCH_EVERY_S = 60.0


class Refused(Exception):
    """A request the store will not accept (``status`` is the HTTP code the route answers)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class StoreDamaged(Exception):
    """A store file does not parse.  Nothing is written or reset until a human looks at it."""


def root() -> Path:
    env = (os.environ.get("MOTRES_OWNER_QUEUE_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim import workspace as ws
    return ws.shared_root() / "owner_queue"


def _items_path() -> Path:
    return root() / "items.jsonl"


def _tokens_path() -> Path:
    return root() / "tokens.json"


_LOCK = store_lock(lambda: _items_path())


def _now_iso(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# -- pure functions -------------------------------------------------------------------------------------------------------------

def approval_hash(kind: str, detail: Optional[str], recipient: Optional[str]) -> str:
    """sha256 (hex) of the canonical JSON of ``{kind, detail, recipient or null}``: sorted keys, no spaces, UTF-8 (not ASCII-escaped).
    scripts/ask_owner.py carries a copy of this exact algorithm; one shared test vector pins both."""
    canon = json.dumps({"kind": kind, "detail": detail, "recipient": recipient or None},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def band_of(item: Dict[str, Any]) -> int:
    """Band 1 holds what BLOCKS something (decision, question, catalog_write, outside_message); band 2 plain messages."""
    return 2 if item.get("kind") == "message" else 1


def queue_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Band 1 first, then band 2; inside a band the oldest ``created_at`` first (a tie keeps the given order)."""
    return sorted(items, key=lambda it: (band_of(it), str(it.get("created_at") or "")))


def validate_new(body: Any) -> Dict[str, Any]:
    """The checked fields of a new item or a ``ValueError`` (the route answers 422).  Nothing is truncated."""
    if not isinstance(body, dict):
        raise ValueError("body must be an object")

    def text(key: str, limit: int, required: bool, one_line: bool = False) -> Optional[str]:
        v = body.get(key)
        if v is None or v == "":
            if required:
                raise ValueError(f"{key} is required")
            return None
        if not isinstance(v, str):
            raise ValueError(f"{key} must be a string")
        if len(v) > limit:
            raise ValueError(f"{key} is {len(v)} characters, the limit is {limit}")
        if one_line and ("\n" in v or "\r" in v):
            raise ValueError(f"{key} must be one line")
        return v

    kind = body.get("kind")
    if kind not in KINDS:
        raise ValueError("kind must be one of " + ", ".join(KINDS))
    out: Dict[str, Any] = {"kind": kind}
    out["asked_by"] = (text("asked_by", MAX_ASKED_BY, True, True) or "").strip()
    if not out["asked_by"]:
        raise ValueError("asked_by is required")
    out["question"] = (text("question", MAX_QUESTION, True, True) or "").strip()
    if not out["question"]:
        raise ValueError("question is required")
    out["project_id"] = text("project_id", MAX_PROJECT, False, True)
    approval = kind in APPROVAL_KINDS
    out["detail"] = text("detail", MAX_DETAIL, approval)
    if approval and not (out["detail"] or "").strip():
        raise ValueError(f"a {kind} needs a detail: the full text of what is approved")
    out["recipient"] = None
    if kind == "outside_message":
        out["recipient"] = (text("recipient", MAX_RECIPIENT, True, True) or "").strip()
        if not out["recipient"]:
            raise ValueError("an outside_message needs a recipient")
    elif body.get("recipient") not in (None, ""):
        raise ValueError("recipient is only for an outside_message")
    out["blocks"] = text("blocks", MAX_BLOCKS, False, True)
    raw = body.get("options")
    if kind in OPTION_KINDS:
        if not isinstance(raw, list) or not (MIN_OPTIONS <= len(raw) <= MAX_OPTIONS):
            raise ValueError(f"a {kind} needs {MIN_OPTIONS} to {MAX_OPTIONS} options")
        opts, seen, rec = [], set(), 0
        for o in raw:
            if not isinstance(o, dict):
                raise ValueError("each option must be an object {label, recommended}")
            lab = o.get("label")
            if not isinstance(lab, str) or not lab.strip():
                raise ValueError("an option label is required")
            if len(lab) > MAX_LABEL:
                raise ValueError(f"option label is {len(lab)} characters, the limit is {MAX_LABEL}")
            if lab in seen:
                raise ValueError("option labels must be different")
            seen.add(lab)
            r = o.get("recommended", False)
            if not isinstance(r, bool):
                raise ValueError("recommended must be true or false")
            rec += 1 if r else 0
            opts.append({"label": lab, "recommended": r})
        if rec > 1:
            raise ValueError("at most one option may be recommended")
        out["options"] = opts
    else:
        if raw not in (None, []):
            raise ValueError(f"a {kind} has no client options" + (" (the server fixes Approve / Reject)" if approval else ""))
        out["options"] = [{"label": o, "recommended": False} for o in APPROVAL_OPTIONS] if approval else []
    return out


# -- storage --------------------------------------------------------------------------------------------------------------------

def _read_rows() -> List[Dict[str, Any]]:
    p = _items_path()
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as e:
        raise StoreDamaged(f"items.jsonl unreadable: {type(e).__name__}")
    rows = []
    for n, ln in enumerate(raw.split("\n"), 1):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            raise StoreDamaged(f"items.jsonl line {n} does not parse")
        if not isinstance(r, dict) or not _ID_RE.match(str(r.get("id") or "")):
            raise StoreDamaged(f"items.jsonl line {n} is not an item row")
        rows.append(r)
    return rows


def _current(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The last row of every id, in order of the id's FIRST appearance."""
    last: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        last[r["id"]] = r
    return list(last.values())


def _append(row: Dict[str, Any]) -> None:
    ensure_private_dir(root())
    with open_private(_items_path(), "a") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def _load_tokens() -> List[Dict[str, Any]]:
    try:
        raw = _tokens_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as e:
        raise StoreDamaged(f"tokens.json unreadable: {type(e).__name__}")
    try:
        data = json.loads(raw) if raw.strip() else []
    except ValueError:
        raise StoreDamaged("tokens.json does not parse")
    if not isinstance(data, list) or any(not isinstance(t, dict) for t in data):
        raise StoreDamaged("tokens.json is not a list of records")
    return data


def _save_tokens(tokens: List[Dict[str, Any]]) -> None:
    ensure_private_dir(root())
    p = _tokens_path()
    tmp = p.with_name(p.name + ".tmp")
    with open_private(tmp, "w") as f:
        f.write(json.dumps(tokens, indent=1))
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, p)
    chmod_private(p)


# -- tokens ---------------------------------------------------------------------------------------------------------------------

def create_token(name: str, created_by: str, now: Optional[float] = None) -> str:
    """A new agent token, returned ONCE.  Names are unique (a revoked name stays taken)."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise Refused(422, "name: 1-40 characters of letters, digits, '.', '_' or '-'")
    with _LOCK:
        tokens = _load_tokens()
        if any(t.get("name") == name for t in tokens):
            raise Refused(409, "a token with this name exists")
        tok = TOKEN_PREFIX + secrets.token_urlsafe(30)[:40]
        tokens.append({"name": name, "token_hash": _hash(tok), "created_at": _now_iso(now),
                       "created_by": str(created_by or "")[:200], "revoked_at": None, "last_used_at": None})
        _save_tokens(tokens)
    return tok


def list_tokens() -> List[Dict[str, Any]]:
    """Names and dates only, never a hash."""
    keep = ("name", "created_at", "created_by", "revoked_at", "last_used_at")
    with _LOCK:
        return [{k: t.get(k) for k in keep} for t in _load_tokens()]


def revoke_token(name: str, now: Optional[float] = None) -> bool:
    with _LOCK:
        tokens = _load_tokens()
        for t in tokens:
            if t.get("name") == name:
                if not t.get("revoked_at"):
                    t["revoked_at"] = _now_iso(now)
                    _save_tokens(tokens)
                return True
    return False


def verify_token(authorization: Optional[str], now: Optional[float] = None) -> Optional[str]:
    """``Authorization: Bearer oq_...`` -> the token's name, or None (malformed, unknown or revoked)."""
    if not isinstance(authorization, str):
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    tok = parts[1].strip()
    if not tok.startswith(TOKEN_PREFIX) or len(tok) > 200:
        return None
    h = _hash(tok)
    found = None
    with _LOCK:
        tokens = _load_tokens()
        for t in tokens:
            th = str(t.get("token_hash") or "")
            if th and hmac.compare_digest(th, h) and not t.get("revoked_at"):
                found = t
        if found is None:
            return None
        t_now = time.time() if now is None else now
        last = found.get("last_used_at")
        try:
            stale = last is None or t_now - calendar.timegm(time.strptime(last, "%Y-%m-%dT%H:%M:%SZ")) > _TOUCH_EVERY_S
        except ValueError:
            stale = True
        if stale:
            found["last_used_at"] = _now_iso(t_now)
            try:
                _save_tokens(tokens)
            except OSError:
                pass                                    # a failed touch never refuses a valid token
        return str(found["name"])


# -- items ----------------------------------------------------------------------------------------------------------------------

def create_item(token_name: str, body: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """Create an item for ``token_name``.  Returns ``{id, created}``; an identical OPEN item of the same token and
    session returns the existing id (``created`` False)."""
    try:
        f = validate_new(body)
    except ValueError as e:
        raise Refused(422, str(e))
    with _LOCK:
        cur = _current(_read_rows())
        mine = [r for r in cur if r.get("token_name") == token_name and r.get("state") == "open"]
        for r in mine:
            if r.get("asked_by") == f["asked_by"] and r.get("question") == f["question"]:
                return {"id": r["id"], "created": False}
        if len(mine) >= MAX_OPEN_PER_TOKEN:
            raise Refused(429, f"this token has {MAX_OPEN_PER_TOKEN} open items; wait for answers or withdraw some")
        taken = {r["id"] for r in cur}
        while True:
            iid = "oq-" + secrets.token_hex(6)
            if iid not in taken:
                break
        row = dict(f, id=iid, token_name=token_name, created_at=_now_iso(now), state="open", answer=None)
        _append(row)
    return {"id": iid, "created": True}


def get_item(iid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        for r in _current(_read_rows()):
            if r["id"] == iid:
                return r
    return None


def list_items(*, token_name: Optional[str] = None, state: str = "", project_id: str = "", asked_by: str = "",
               q: str = "", ordered: bool = True) -> List[Dict[str, Any]]:
    with _LOCK:
        cur = _current(_read_rows())
    needle = (q or "").strip().lower()
    out = []
    for r in cur:
        if token_name is not None and r.get("token_name") != token_name:
            continue
        if state and r.get("state") != state:
            continue
        if project_id and r.get("project_id") != project_id:
            continue
        if asked_by and r.get("asked_by") != asked_by:
            continue
        if needle and not any(needle in str(r.get(k) or "").lower() for k in ("question", "detail", "project_id")):
            continue
        out.append(r)
    return queue_order(out) if ordered else out


def counts() -> Dict[str, Any]:
    """``{open_blocking, open_messages, oldest_open_at}`` for the badge and the daily report."""
    op = [r for r in list_items(state="open", ordered=False)]
    return {"open_blocking": sum(1 for r in op if band_of(r) == 1),
            "open_messages": sum(1 for r in op if band_of(r) == 2),
            "oldest_open_at": min((r["created_at"] for r in op), default=None)}


def withdraw_item(token_name: str, iid: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Withdraw an OPEN item of this token.  Anything else is a 404 (no leak) or 409 (not open any more)."""
    with _LOCK:
        cur = {r["id"]: r for r in _current(_read_rows())}
        r = cur.get(iid)
        if r is None or r.get("token_name") != token_name:
            raise Refused(404, "no such item")
        if r.get("state") != "open":
            raise Refused(409, "the item is not open")
        row = dict(r, state="withdrawn", withdrawn_at=_now_iso(now))
        _append(row)
    return row


def answer_item(iid: str, body: Any, by: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Record the owner's answer.  ``body`` is ``{option}`` and/or ``{text}``; only an open item can be answered (409)."""
    if not isinstance(body, dict):
        raise Refused(422, "body must be an object")
    option, text = body.get("option"), body.get("text")
    if option is not None and not isinstance(option, str):
        raise Refused(422, "option must be a string")
    if text is not None and not isinstance(text, str):
        raise Refused(422, "text must be a string")
    text = text.strip() if text else None
    if text is not None and len(text) > MAX_ANSWER_TEXT:
        raise Refused(422, f"text is {len(text)} characters, the limit is {MAX_ANSWER_TEXT}")
    if not option and not text:
        raise Refused(422, "an option or a text is required")
    with _LOCK:
        cur = {r["id"]: r for r in _current(_read_rows())}
        r = cur.get(iid)
        if r is None:
            raise Refused(404, "no such item")
        if r.get("state") != "open":
            raise Refused(409, "the item is not open any more")
        kind = r["kind"]
        new_state = "answered"
        if kind == "message":
            if option and option != "read":
                raise Refused(422, "a message is answered with option 'read' or with a text")
            if option and text:
                raise Refused(422, "send either 'read' or a text reply")
            if option == "read":
                new_state = "read"
        elif kind == "question":
            if option:
                raise Refused(422, "a question is answered with a text")
        else:
            if option and option not in [o["label"] for o in r.get("options") or []]:
                raise Refused(422, "the option is not one of the item's options")
            if kind in APPROVAL_KINDS and not option:
                raise Refused(422, "choose Approve or Reject (a text may go with it)")
        ans = {"option": option or None, "text": text, "by": str(by or ""), "at": _now_iso(now)}
        if kind in APPROVAL_KINDS:
            ans["approved_hash"] = approval_hash(kind, r.get("detail"), r.get("recipient"))
        row = dict(r, state=new_state, answer=ans)
        _append(row)
    return row
