"""Storage of the activity digests: settings per working group, the draft waiting for approval, the cursor and the audit log.

Everything lives under one directory next to the other shared stores (``<shared root>/digests``, ``MOTRES_DIGEST_ROOT``
overrides it)::

    settings.json          {scope: {mode, frequency, recipients, chosen, optouts, updated_at, updated_by}}
    state.json             {scope: {cursor, draft_id}}   the end of the last digest period and the draft waiting now
    drafts/<id>.json       one draft = ONE text for the whole group (owner 2026-10-26) + its recipient list
    log.jsonl              append-only: settings changes, drafts made, sent, discarded (who, when)
    sendlog/<id>.sendlog.jsonl   the mailing send log of one approved draft (idempotent send, see mailing.py)

A *scope* is ``project:<id>`` (a development project) or ``production:<die>/<config>`` (one production).

Modes (owner 2026-10-26): ``approve`` (default) - the digest is generated and WAITS in Admin until the owner sends or
discards it; ``off`` - nothing.  There is NO automatic mode: nothing here ever sends.  A value ``auto`` found in an old file
is read as ``approve``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from motor_ai_sim import json_store

MODES = ("approve", "off")
FREQUENCIES = ("daily", "weekly")
RECIPIENTS = ("all", "chosen")
DEFAULT_SETTINGS = {"mode": "approve", "frequency": "daily", "recipients": "all", "chosen": [], "optouts": []}

_ID_RE = re.compile(r"^d-[0-9a-f]{16}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")


def root() -> Path:
    env = (os.environ.get("MOTRES_DIGEST_ROOT") or "").strip()
    if env:
        return Path(env)
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "digests"


def _settings_file() -> Path:
    return root() / "settings.json"


def _state_file() -> Path:
    return root() / "state.json"


def log_file() -> Path:
    return root() / "log.jsonl"


def draft_file(draft_id: str) -> Path:
    if not valid_draft_id(draft_id):
        raise ValueError("invalid digest id")
    return root() / "drafts" / f"{draft_id}.json"


def sendlog_file(draft_id: str) -> Path:
    if not valid_draft_id(draft_id):
        raise ValueError("invalid digest id")
    return root() / "sendlog" / f"{draft_id}.sendlog.jsonl"


def valid_draft_id(draft_id: Any) -> bool:
    return bool(_ID_RE.match(str(draft_id or "")))


# -- scopes ----------------------------------------------------------------------------------------------------------------

def project_scope(project_id: str) -> str:
    return f"project:{project_id}"


def production_scope(die: str, config: str) -> str:
    return f"production:{die}/{config}"


def parse_scope(scope: str) -> Optional[tuple[str, tuple[str, ...]]]:
    """``('project', (id,))`` / ``('production', (die, config))`` or None."""
    scope = str(scope or "")
    if scope.startswith("project:") and len(scope) > 8:
        return "project", (scope[8:],)
    if scope.startswith("production:"):
        die, sep, config = scope[11:].rpartition("/")
        if sep and die and config:
            return "production", (die, config)
    return None


def scope_token(scope: str) -> str:
    """URL-safe form of a scope (the unsubscribe link carries it)."""
    return base64.urlsafe_b64encode(scope.encode("utf-8")).rstrip(b"=").decode("ascii")


def scope_from_token(token: str) -> Optional[str]:
    try:
        raw = base64.urlsafe_b64decode(str(token) + "=" * (-len(str(token)) % 4)).decode("utf-8")
    except Exception:                                        # noqa: BLE001
        return None
    return raw if parse_scope(raw) else None


def draft_id_for(scope: str, period_start: str) -> str:
    """One id per (group, period): the same period can only ever have ONE draft."""
    return "d-" + hashlib.sha256(f"{scope}|{period_start}".encode("utf-8")).hexdigest()[:16]


# -- time ------------------------------------------------------------------------------------------------------------------

def iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> Optional[float]:
    """Seconds since the epoch of an ISO-8601 string (naive = UTC), None when unreadable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# -- settings --------------------------------------------------------------------------------------------------------------

def _emails(values: Any) -> list[str]:
    out: list[str] = []
    for v in values if isinstance(values, (list, tuple)) else []:
        e = str(v or "").strip().lower()
        if _EMAIL_RE.match(e) and e not in out:
            out.append(e)
    return out


def normalise_settings(raw: Any) -> dict:
    """Valid settings from anything stored: unknown mode (also an old ``auto``) -> ``approve``."""
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode") if raw.get("mode") in MODES else "approve"
    out = dict(DEFAULT_SETTINGS)
    out.update({
        "mode": mode,
        "frequency": raw.get("frequency") if raw.get("frequency") in FREQUENCIES else "daily",
        "recipients": raw.get("recipients") if raw.get("recipients") in RECIPIENTS else "all",
        "chosen": _emails(raw.get("chosen")),
        "optouts": _emails(raw.get("optouts")),
    })
    for k in ("updated_at", "updated_by"):
        if raw.get(k):
            out[k] = str(raw[k])
    return out


def get_settings(scope: str) -> dict:
    doc = json_store.read_json(_settings_file(), {})
    return normalise_settings(doc.get(scope) if isinstance(doc, dict) else None)


def all_settings() -> dict:
    doc = json_store.read_json(_settings_file(), {})
    return {k: normalise_settings(v) for k, v in doc.items()} if isinstance(doc, dict) else {}


def validate_settings_update(body: Any) -> dict:
    """The fields of a settings update, checked.  ValueError (the caller answers 422) on anything unknown - ``auto`` included."""
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    out: dict = {}
    if "mode" in body:
        if body["mode"] not in MODES:
            raise ValueError("mode must be one of: " + ", ".join(MODES))
        out["mode"] = body["mode"]
    if "frequency" in body:
        if body["frequency"] not in FREQUENCIES:
            raise ValueError("frequency must be one of: " + ", ".join(FREQUENCIES))
        out["frequency"] = body["frequency"]
    if "recipients" in body:
        if body["recipients"] not in RECIPIENTS:
            raise ValueError("recipients must be one of: " + ", ".join(RECIPIENTS))
        out["recipients"] = body["recipients"]
    if "chosen" in body:
        if not isinstance(body["chosen"], list) or not all(isinstance(x, str) for x in body["chosen"]):
            raise ValueError("chosen must be a list of e-mail addresses")
        bad = [x for x in body["chosen"] if not _EMAIL_RE.match(x.strip().lower())]
        if bad:
            raise ValueError("not an e-mail address: " + bad[0][:60])
        out["chosen"] = _emails(body["chosen"])
    unknown = set(body) - {"mode", "frequency", "recipients", "chosen"}
    if unknown:
        raise ValueError("unknown setting(s): " + ", ".join(sorted(str(u) for u in unknown)))
    return out


def set_settings(scope: str, changes: dict, *, by: str) -> dict:
    """Apply a checked update, log who changed what.  -> the new settings."""
    if parse_scope(scope) is None:
        raise ValueError("unknown scope")
    changes = validate_settings_update(changes)
    result: dict = {}

    def mutate(doc):
        before = normalise_settings(doc.get(scope))
        after = dict(before)
        after.update(changes)
        after["updated_at"], after["updated_by"] = iso(), str(by or "").lower()
        doc[scope] = after
        result["before"], result["after"] = before, after

    json_store.mutate_json(_settings_file(), mutate, default={})
    changed = {k: [result["before"].get(k), result["after"][k]] for k in changes if result["before"].get(k) != result["after"][k]}
    if changed:
        append_log({"event": "settings", "scope": scope, "by": str(by or "").lower(), "changed": changed})
    return result["after"]


def set_optout(scope: str, email: str, out: bool, *, by: str) -> dict:
    """One member leaves (or rejoins) one group's digest."""
    email = str(email or "").strip().lower()
    if not _EMAIL_RE.match(email) or parse_scope(scope) is None:
        raise ValueError("bad address or scope")
    result: dict = {}

    def mutate(doc):
        cur = normalise_settings(doc.get(scope))
        opt = [e for e in cur["optouts"] if e != email]
        if out:
            opt.append(email)
        result["changed"] = sorted(opt) != sorted(cur["optouts"])
        cur["optouts"] = sorted(opt)
        doc[scope] = cur
        result["after"] = cur

    json_store.mutate_json(_settings_file(), mutate, default={})
    if result["changed"]:
        append_log({"event": "optout" if out else "optin", "scope": scope, "by": str(by or "").lower(), "member": email})
    return result["after"]


# -- state and drafts ----------------------------------------------------------------------------------------------------------

def get_state(scope: str) -> dict:
    doc = json_store.read_json(_state_file(), {})
    rec = doc.get(scope) if isinstance(doc, dict) else None
    return dict(rec) if isinstance(rec, dict) else {}


def set_state(scope: str, **fields: Any) -> dict:
    result: dict = {}

    def mutate(doc):
        rec = dict(doc.get(scope) or {})
        rec.update(fields)
        doc[scope] = rec
        result["rec"] = rec

    json_store.mutate_json(_state_file(), mutate, default={})
    return result["rec"]


def load_draft(draft_id: str) -> Optional[dict]:
    if not valid_draft_id(draft_id):
        return None
    d = json_store.read_json(draft_file(draft_id), None)
    return d if isinstance(d, dict) else None


def save_draft(draft: dict) -> None:
    p = draft_file(draft["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    json_store.atomic_write_json(p, draft)


def list_drafts() -> list[dict]:
    d = root() / "drafts"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("d-*.json")):
        rec = json_store.read_json(p, None)
        if isinstance(rec, dict) and valid_draft_id(rec.get("id")):
            out.append(rec)
    return sorted(out, key=lambda r: str(r.get("updated_at") or ""), reverse=True)


# -- the append-only log -------------------------------------------------------------------------------------------------------

def append_log(row: dict) -> None:
    p = log_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    row.setdefault("ts", iso())
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with json_store.lock_for(p):
        with open(p, "a", encoding="utf-8", newline="") as fh:
            fh.write(line)


def read_log(scope: Optional[str] = None) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(log_file(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and (scope is None or r.get("scope") == scope):
                    rows.append(r)
    except OSError:
        return []
    return rows
