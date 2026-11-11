"""Append-only archive of assistant conversations, for our own analysis and interface work (owner 2026-11-11).

Covers the ONE in-app assistant (owner 2026-11-11: help chat and specification assistant merged): records written since then carry
``assistant: "assistant"`` and ``context: "help" | "spec"``; older records carry ``assistant: "spec"`` or ``"support"`` and are read as
that context (signed-in callers, and anonymous visitors keyed by a random session id the browser makes up - never by IP).  One JSONL file per UTC day beside the identity
store: ``<identity>/assistant_conversations/YYYY-MM-DD.jsonl``, written only by :func:`log_turn` / :func:`log_event` (no update or
delete API).  A user's "Clear conversation" only clears THEIR view (the project's ``spec_assistant/`` history file); this archive
is not touched by it.

A line is one of

* ``kind: "turn"``  - ``{ts, iso, time, kind, assistant, context, conv_id, account, anon, project_id, page, user_text, reply, model,
  source, error, fill_mode, applied: [{field_id, basis, proposal_id, sources?}], proposed: [{field_id}], refused: [field_id], dropped,
  searches: [query], sources: [{url, title}]}`` (``searches`` / ``sources``: the web lookups of that turn);
* ``kind: "event"`` - ``{..., event: "undo" | "dismiss" | ..., field_id, proposal_id}``.

What is NOT stored: the system prompt, the specification snapshot or the support context the model reads (hidden context), and
anything that looks like a secret (keys, bearer tokens, ``password=...``) - those are redacted from the texts.  Nothing here ever
raises into the caller's reply.

Readers (:func:`iter_records`, :func:`conversations`, :func:`stats`, :func:`export_lines`) are for the admin API only
(``routes/assistant_conversations.py``).  The export marks every record as a candidate for the closed dataset; whether it may be
used for training stays decided downstream by the existing gate (``personal_engineering_ai`` ``training_allow`` and
``platform_agreement.training_allowed``) - nothing here lets a record past it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from motor_ai_sim.file_lock import store_lock
from motor_ai_sim.private_files import ensure_private_dir, open_private

log = logging.getLogger(__name__)

ASSISTANTS = ("spec", "support", "assistant")
#: The context of a record: new records name it, old ones are read from their assistant (spec -> spec, support -> help).
CONTEXTS = ("help", "spec")
_LEGACY_CONTEXT = {"spec": "spec", "support": "help"}
MAX_TEXT = 8000
_SID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_LOCK = store_lock(lambda: archive_dir() / ".lock")

_SECRETS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "[redacted-key]"),
    (re.compile(r"\b(?:AIza|ghp_|gho_|xox[bap]-|AKIA)[A-Za-z0-9_\-]{12,}"), "[redacted-key]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [redacted]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_ -]?key|token)\b(\s*(?:is|=|:)\s*)\S+"), r"\1\2[redacted]"),
]


def archive_dir() -> Path:
    from motor_ai_sim.account_lifecycle import identity_dir
    return identity_dir() / "assistant_conversations"


def redact(text: Any) -> str:
    out = str(text or "")[:MAX_TEXT]
    for rx, sub in _SECRETS:
        out = rx.sub(sub, out)
    return out


def valid_sid(raw: Any) -> str:
    s = str(raw or "").strip()
    return s if _SID.match(s) else ""


def conv_key(assistant: str, *parts: str) -> str:
    """A stable, non-reversible conversation id from its parts (project + account, or the visitor's random session id)."""
    return assistant[:1] + "-" + hashlib.sha256("|".join(p.strip().lower() for p in parts).encode("utf-8")).hexdigest()[:16]


def _path(day: str) -> Path:
    return archive_dir() / f"{day}.jsonl"


def context_of(rec: dict) -> str:
    """``help`` or ``spec`` for any record, old or new."""
    c = rec.get("context")
    return c if c in CONTEXTS else _LEGACY_CONTEXT.get(str(rec.get("assistant") or ""), "help")


def _stamp(now: float) -> dict:
    """Every record's time, written by the archive itself (the 2026-11-11 records had ``time: null`` where readers looked)."""
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    return {"ts": round(now, 3), "iso": iso, "time": iso}


def _write(rec: dict) -> None:
    now = time.time()
    rec = {**{k: v for k, v in rec.items() if k not in ("ts", "iso", "time")}, **_stamp(now)}
    p = _path(rec["iso"][:10])
    try:
        with _LOCK:
            ensure_private_dir(p.parent)
            with open_private(p, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:                                  # noqa: BLE001 - the archive must never fail a reply
        log.error("assistant_archive: could not append (%s: %s)", type(exc).__name__, exc)


def log_turn(*, assistant: str, conv_id: str, account: str = "", project_id: str = "", page: str = "", user_text: str = "",
             reply: str = "", model: str = "", source: str = "", error: str = "", fill_mode: Optional[bool] = None,
             applied: Optional[List[dict]] = None, proposed: Optional[List[dict]] = None, refused: Optional[List[str]] = None,
             dropped: int = 0, context: str = "", searches: Optional[List[str]] = None, sources: Optional[List[dict]] = None) -> None:
    if assistant not in ASSISTANTS:
        return
    ctx = context if context in CONTEXTS else _LEGACY_CONTEXT.get(assistant, "help")
    _write({"kind": "turn", "assistant": assistant, "context": ctx, "conv_id": conv_id, "account": (account or "").strip().lower()[:200],
            "anon": not account, "project_id": project_id[:64], "page": str(page or "")[:80],
            "user_text": redact(user_text), "reply": redact(reply), "model": str(model or "")[:80], "source": str(source or "")[:30],
            "error": str(error or "")[:200], "fill_mode": fill_mode,
            "applied": [{"field_id": str(a.get("field_id")), "basis": a.get("basis"), "proposal_id": a.get("id"),
                         **({"sources": list(a["sources"])[:5]} if a.get("sources") else {})} for a in applied or []],
            "searches": [redact(q)[:200] for q in searches or []][:10],
            "sources": [{"url": str(x.get("url") or "")[:300], "title": redact(x.get("title"))[:200]}
                        for x in sources or [] if isinstance(x, dict) and x.get("url")][:12],
            "proposed": [{"field_id": str(a.get("field_id")), "proposal_id": a.get("id")} for a in proposed or []],
            "refused": [str(f)[:60] for f in refused or []], "dropped": int(dropped)})


def log_event(*, assistant: str, event: str, conv_id: str = "", account: str = "", project_id: str = "", field_id: str = "",
              proposal_id: str = "", context: str = "") -> None:
    if assistant not in ASSISTANTS:
        return
    ctx = context if context in CONTEXTS else _LEGACY_CONTEXT.get(assistant, "spec" if project_id else "help")
    _write({"kind": "event", "assistant": assistant, "context": ctx, "event": event[:30], "conv_id": conv_id,
            "account": (account or "").strip().lower()[:200],
            "project_id": project_id[:64], "field_id": field_id[:60], "proposal_id": proposal_id[:40]})


# -- readers (admin API) ------------------------------------------------------

def _days(date_from: str, date_to: str) -> List[Path]:
    d = archive_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("????-??-??.jsonl")):
        day = p.stem
        if (date_from and day < date_from) or (date_to and day > date_to):
            continue
        out.append(p)
    return out


def iter_records(*, assistant: str = "", project: str = "", account: str = "", date_from: str = "", date_to: str = "",
                 conv_id: str = "", context: str = "") -> Iterator[dict]:
    """Records matching the filters.  ``assistant`` keeps its old meaning for the admin filters: ``spec`` = the specification
    context, ``support`` = the help context (whether the record is old or from the one assistant); ``assistant`` = new records only.
    Every record comes out with ``context`` and ``time`` set (old ones get them from their assistant / ``iso``)."""
    account = account.strip().lower()
    want_ctx = context or {"spec": "spec", "support": "help"}.get(assistant, "")
    for p in _days(date_from, date_to):
        with _LOCK:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(r, dict) or not r.get("iso"):
                continue
            r.setdefault("time", r.get("iso"))
            if not r.get("time"):
                r["time"] = r["iso"]
            r["context"] = context_of(r)
            if want_ctx and r["context"] != want_ctx:
                continue
            if assistant == "assistant" and r.get("assistant") != "assistant":
                continue
            if project and r.get("project_id") != project:
                continue
            if account and account not in str(r.get("account") or ""):
                continue
            if conv_id and r.get("conv_id") != conv_id:
                continue
            yield r


def conversations(**flt: Any) -> List[dict]:
    """One row per conversation: ``{conv_id, assistant, account, project_id, first, last, turns, filled, proposed, errors}``, newest first."""
    rows: Dict[str, dict] = {}
    for r in iter_records(**flt):
        c = rows.setdefault(r.get("conv_id") or "?", {
            "conv_id": r.get("conv_id"), "assistant": r.get("assistant"), "context": r.get("context"), "account": r.get("account") or "",
            "project_id": r.get("project_id") or "", "first": r["iso"], "last": r["iso"], "turns": 0, "filled": 0,
            "proposed": 0, "errors": 0})
        c["last"] = r["iso"]
        if r.get("kind") == "turn":
            c["turns"] += 1
            c["filled"] += len(r.get("applied") or [])
            c["proposed"] += len(r.get("proposed") or [])
            c["errors"] += 1 if (r.get("error") or r.get("source") in ("error",)) else 0
    return sorted(rows.values(), key=lambda c: c["last"], reverse=True)


def stats(**flt: Any) -> dict:
    """Counters per UTC day and in total: conversations, turns, fields filled / proposed / undone, refusals, errors."""
    zero = lambda: {"conversations": 0, "turns": 0, "filled": 0, "proposed": 0, "undone": 0, "refusals": 0, "errors": 0}  # noqa: E731
    days: Dict[str, dict] = {}
    seen: Dict[str, set] = {}
    for r in iter_records(**flt):
        day = r["iso"][:10]
        d = days.setdefault(day, zero())
        seen.setdefault(day, set()).add(r.get("conv_id"))
        if r.get("kind") == "event":
            d["undone"] += 1 if r.get("event") == "undo" else 0
            continue
        d["turns"] += 1
        d["filled"] += len(r.get("applied") or [])
        d["proposed"] += len(r.get("proposed") or [])
        d["refusals"] += len(r.get("refused") or []) + (1 if r.get("source") == "rate_limited" else 0)
        d["errors"] += 1 if (r.get("error") or r.get("source") == "error") else 0
    total = zero()
    for day, d in days.items():
        d["conversations"] = len(seen[day])
        for k in total:
            total[k] += d[k]
    return {"days": [{"day": k, **v} for k, v in sorted(days.items())], "total": total}


def export_lines(training_allowed=None, **flt: Any) -> Iterable[str]:
    """JSONL for the analysis / dataset: every record plus a ``dataset`` block.  ``candidate`` only says the record may be offered to
    the dataset; ``training_allowed`` is the account's own flag from the Platform Agreement and the dataset's own gate
    (``training_allow``) still decides downstream.  Anonymous visitors are never training-allowed."""
    cache: Dict[str, bool] = {}
    for r in iter_records(**flt):
        acct = str(r.get("account") or "")
        ok = False
        if acct and training_allowed is not None:
            if acct not in cache:
                try:
                    cache[acct] = bool(training_allowed(acct))
                except Exception:                              # noqa: BLE001
                    cache[acct] = False
            ok = cache[acct]
        out = {**r, "dataset": {"candidate": True, "training_allowed": ok, "gate": "training_allow (personal_engineering_ai) decides downstream"}}
        yield json.dumps(out, ensure_ascii=False, sort_keys=True)
