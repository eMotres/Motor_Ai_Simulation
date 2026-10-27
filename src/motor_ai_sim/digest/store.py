"""Storage of the activity digests: settings per working group, the draft waiting for approval, the cursor and the audit log.

Everything lives under one directory next to the other shared stores (``<shared root>/digests``, ``MOTRES_DIGEST_ROOT``
overrides it)::

    settings.json          {scope: {mode, frequency, recipients, chosen, optouts, updated_at, updated_by}}
    state.json             {scope: {cursor, draft_id}}   the end of the last digest period and the draft waiting now
    drafts/<id>.json       one draft = ONE text for the whole group (owner 2026-10-26) + its recipient list
    log.jsonl              append-only: settings changes, drafts made, sent, discarded (who, when)
    sendlog/<id>.sendlog.jsonl   the mailing send log of one sent draft (idempotent send, see mailing.py)
    migrations.json        {migration name: when} - one-off data migrations that already ran

A *scope* is ``project:<id>`` (a development project) or ``production:<die>/<config>`` (one production).

Modes (owner decision 2026-10-28, which reverses the "no automatic mode" rule of 2026-10-26): ``auto`` (default) - the daily
scheduled run (``approval.run_daily``, at 06:00 Europe/Ljubljana) sends each new draft of the group right after building it,
through the same send path as the admin Send; ``approve`` - the draft WAITS in Admin until the owner sends or discards it;
``off`` - nothing.  Only a group with changes during the period gets a draft at all.  Nothing in this module sends.

There is no separate "global default" record: the default of a group with no stored mode is ``DEFAULT_SETTINGS["mode"]``.
Stored data written before 2026-10-28 says ``approve`` for every group that ever had a record (an opt-out or a frequency change
stores the whole record, and ``approve`` was the default), so :func:`migrate_auto_default` runs once and turns such a record
into ``auto``; a record where somebody chose ``approve`` on purpose (the settings log shows a change TO ``approve``) or ``off`` keeps
it and gets ``mode_chosen: true``.  From then on ``set_settings`` marks every mode it sets as chosen.
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

MODES = ("auto", "approve", "off")
FREQUENCIES = ("daily", "weekly")
RECIPIENTS = ("all", "chosen")
DEFAULT_SETTINGS = {"mode": "auto", "frequency": "daily", "recipients": "all", "chosen": [], "optouts": []}

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


def draft_id_for(scope: str, period_start: str, revision: int = 0) -> str:
    """One id per (group, period, revision).  Revision 0 is the first draft of a period; a draft somebody already opened is never
    rewritten - a refresh makes revision 1, 2, ... and supersedes the old one (see ``build.run``)."""
    key = f"{scope}|{period_start}" + (f"|r{revision}" if revision else "")
    return "d-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


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
    """Valid settings from anything stored: a missing or unknown mode -> the default (``auto``)."""
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode") if raw.get("mode") in MODES else DEFAULT_SETTINGS["mode"]
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
    if raw.get("mode_chosen") is True:
        out["mode_chosen"] = True
    return out


# -- the 2026-10-28 migration: the stored default ``approve`` becomes ``auto`` -------------------------------------------------

MIGRATION_AUTO_DEFAULT = "auto_default_2026-10-28"


def _migrations_file() -> Path:
    return root() / "migrations.json"


def _chose_approve(scope: str, rows: list) -> bool:
    """Did somebody set this group to ``approve`` on purpose?  The last logged mode change of the group went TO ``approve``."""
    last = None
    for r in rows:
        ch = r.get("changed") if r.get("event") == "settings" and r.get("scope") == scope else None
        if isinstance(ch, dict) and isinstance(ch.get("mode"), list) and len(ch["mode"]) == 2:
            last = ch["mode"][1]
    return last == "approve"


def migrate_auto_default() -> list:
    """Run once per store: every stored ``approve`` that is only the old default becomes ``auto`` (owner 2026-10-28).  Kept as
    they are: ``off`` and an ``approve`` that the settings log shows was chosen (both get ``mode_chosen``).  -> the scopes changed.
    Idempotent and cheap after the first run (one marker file)."""
    if MIGRATION_AUTO_DEFAULT in (json_store.read_json(_migrations_file(), {}) or {}):
        return []
    rows = read_log()
    changed: list = []

    def mutate(doc):
        for scope, rec in list(doc.items()):
            if not isinstance(rec, dict) or rec.get("mode_chosen") is True:
                continue
            if rec.get("mode") == "off" or (rec.get("mode") == "approve" and _chose_approve(scope, rows)):
                rec["mode_chosen"] = True
            else:                                            # the old default (or a missing / unknown value) -> the new default
                if rec.get("mode") == "approve":
                    changed.append(scope)
                rec["mode"] = "auto"

    json_store.mutate_json(_settings_file(), mutate, default={})
    json_store.mutate_json(_migrations_file(), lambda m: m.__setitem__(MIGRATION_AUTO_DEFAULT, iso()), default={})
    if changed:
        append_log({"event": "migrated", "migration": MIGRATION_AUTO_DEFAULT, "by": "system", "scopes": sorted(changed),
                    "changed": {"mode": ["approve", "auto"]}})
    return changed


def get_settings(scope: str) -> dict:
    migrate_auto_default()
    doc = json_store.read_json(_settings_file(), {})
    return normalise_settings(doc.get(scope) if isinstance(doc, dict) else None)


def all_settings() -> dict:
    migrate_auto_default()
    doc = json_store.read_json(_settings_file(), {})
    return {k: normalise_settings(v) for k, v in doc.items()} if isinstance(doc, dict) else {}


def validate_settings_update(body: Any) -> dict:
    """The fields of a settings update, checked.  ValueError (the caller answers 422) on anything unknown."""
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
    migrate_auto_default()
    result: dict = {}

    def mutate(doc):
        before = normalise_settings(doc.get(scope))
        after = dict(before)
        after.update(changes)
        if "mode" in changes:
            after["mode_chosen"] = True                      # a deliberate choice: a later change of the default must not touch it
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
    migrate_auto_default()
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


#: statuses in which a draft can still be sent (``partial`` = some recipients failed; only they are retried)
OPEN_STATUSES = ("pending", "partial")


def mark_opened(draft_id: str, by: str) -> Optional[dict]:
    """The owner opened or previewed this draft: from now on its text is frozen (a refresh supersedes it instead of rewriting)."""
    if not valid_draft_id(draft_id) or not draft_file(draft_id).is_file():
        return None

    def mutate(doc):
        if isinstance(doc, dict) and doc.get("status") in OPEN_STATUSES and not doc.get("opened"):
            doc["opened"] = {"at": iso(), "by": str(by or "").lower()}

    return json_store.mutate_json(draft_file(draft_id), mutate, default={})


def update_draft(draft_id: str, mutator) -> Optional[dict]:
    """Edit a stored draft in place under its lock (``mutator(doc)``); None when there is no such draft."""
    if not valid_draft_id(draft_id) or not draft_file(draft_id).is_file():
        return None
    return json_store.mutate_json(draft_file(draft_id), mutator, default={})


def update_skipped(draft_id: str, skipped: list) -> None:
    """Refresh the informational "not receiving" list of a draft (not part of the approved content: the hash ignores it)."""
    def mutate(doc):
        if isinstance(doc, dict) and doc.get("status") in OPEN_STATUSES and doc.get("skipped") != skipped:
            doc["skipped"] = skipped

    json_store.mutate_json(draft_file(draft_id), mutate, default={})


def rewrite_unopened(draft: dict) -> bool:
    """Replace the content of a pending draft nobody has opened.  False (nothing written) when it was opened meanwhile - the check
    and the write happen under one lock, so a preview and a rewrite can never cross."""
    done: dict = {}

    def mutate(doc):
        if not isinstance(doc, dict) or doc.get("status") != "pending" or doc.get("opened"):
            done["ok"] = False
            return
        doc.clear()
        doc.update(draft)
        done["ok"] = True

    json_store.mutate_json(draft_file(draft["id"]), mutate, default={})
    return bool(done.get("ok"))


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
