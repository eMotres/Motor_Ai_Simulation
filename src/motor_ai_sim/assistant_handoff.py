"""The hand-off from the help chat to a new specification (owner 2026-11-11: the assistant creates the project and the
specification itself when a user asks for a motor or a spec).

When the assistant ends a reply with ``[[CREATE_SPEC]]`` the chat route stores what it gathered as ONE short-lived record:
``<identity>/assistant_handoff/<token>.json`` = ``{token, account, created, expires, lang, values: [{field, value, reason, basis,
answer_status?, sources?}], conversation: [{role, text}]}``.  ``values`` are the specification answers (the propeller estimate's
``assistant_estimate.spec_values`` plus the model's own SPEC_PROPOSAL markers of that reply); ``conversation`` is what the user typed
and what the assistant answered in that chat - nothing else (no IP, no session context).

* a SIGNED-IN user: the record carries the account; the route creates the project at once (``create_for``).  If the Platform
  Agreement is not accepted yet the create path answers 428 - the widget then calls ``POST /api/support/handoff/<token>/create``,
  which the app's global 428 handler repeats once after acceptance;
* a VISITOR: ``account`` is empty; the reply carries ``/projects?new=spec&handoff=<token>``, which goes through sign-up (the link
  survives it, ``return_path``) and is claimed by the first account that opens it.

Records expire after ``TTL_S`` (an expired one is deleted when touched and by ``sweep``); a used one is deleted.  Nothing here
raises into a chat reply: the caller catches.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TTL_S = 2 * 3600
MAX_VALUES = 20
MAX_TURNS = 30
MAX_TEXT = 4000
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class HandoffError(LookupError):
    """No such (or no longer valid) hand-off for this caller."""


def _dir() -> Path:
    from motor_ai_sim.account_lifecycle import identity_dir
    return identity_dir() / "assistant_handoff"


def valid_token(raw: Any) -> str:
    s = str(raw or "").strip()
    return s if _TOKEN.match(s) else ""


def _path(token: str) -> Path:
    return _dir() / f"{token}.json"


def put(values: List[dict], conversation: List[dict], *, account: str = "", lang: str = "en", now: Optional[float] = None) -> str:
    """Store a hand-off; returns its token."""
    from motor_ai_sim import json_store
    from motor_ai_sim.private_files import ensure_private_dir
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(18)
    rec = {"token": token, "account": (account or "").strip().lower(), "created": round(now, 3), "expires": round(now + TTL_S, 3),
           "lang": "zh-CN" if str(lang).startswith("zh") else "en",
           "values": [v for v in values if isinstance(v, dict) and v.get("field")][:MAX_VALUES],
           "conversation": [{"role": m.get("role"), "text": str(m.get("text") or m.get("content") or "")[:MAX_TEXT]}
                            for m in conversation[-MAX_TURNS:] if isinstance(m, dict) and m.get("role") in ("user", "assistant")]}
    ensure_private_dir(_dir())
    json_store.atomic_write_json(_path(token), rec)
    return token


def get(token: str, account: str = "", *, now: Optional[float] = None) -> dict:
    """The record for this caller (``HandoffError`` when missing, expired or someone else's)."""
    from motor_ai_sim import json_store
    token = valid_token(token)
    if not token:
        raise HandoffError("hand-off not found")
    p = _path(token)
    rec = json_store.read_json(p, None)
    now = time.time() if now is None else now
    if not isinstance(rec, dict):
        raise HandoffError("hand-off not found")
    if float(rec.get("expires") or 0) < now:
        drop(token)
        raise HandoffError("hand-off expired")
    owner = str(rec.get("account") or "")
    if owner and owner != (account or "").strip().lower():
        raise HandoffError("hand-off not found")
    return rec


def drop(token: str) -> None:
    try:
        _path(valid_token(token) or "_").unlink()
    except OSError:
        pass


def sweep(now: Optional[float] = None) -> int:
    """Delete expired records; returns how many."""
    now = time.time() if now is None else now
    n = 0
    d = _dir()
    if not d.is_dir():
        return 0
    for p in d.glob("*.json"):
        try:
            if float(json.loads(p.read_text(encoding="utf-8")).get("expires") or 0) < now:
                p.unlink()
                n += 1
        except (OSError, ValueError, AttributeError):
            continue
    return n


def apply_values(project_id: str, values: List[dict], email: str, lang: str = "en") -> Dict[str, list]:
    """Write ``values`` into the project's draft as the assistant's fills (the Apply button's path, Undo kept), as the project
    owner.  Returns ``{applied, open, refused}``: a value that does not validate is dropped; one the rules keep as a card (an
    ASSUMED application / operating point / supply) stays a card."""
    from motor_ai_sim.development import spec_proposals as P
    applied, opened, refused = [], [], []
    for v in values:
        fid = str(v.get("field") or "")
        basis = str(v.get("basis") or "assumed")
        reason = str(v.get("reason") or "")
        if basis == "assumed" and "assumed" not in reason.lower() and "假设" not in reason:
            reason = f"{reason} {'（假设，待确认）' if lang == 'zh-CN' else '(assumed, to confirm)'}".strip()
        try:
            prop = P.create(project_id, fid, v.get("value"), reason or fid, basis=basis,
                            answer_status=str(v.get("answer_status") or "filled"), sources=v.get("sources"))
        except Exception as exc:                                  # noqa: BLE001 - one bad value never stops the others
            log.warning("handoff: value for %r dropped (%s)", fid, str(exc)[:160])
            refused.append(fid)
            continue
        if fid in P.STATED_ONLY and basis not in P.FILL_BASES:
            opened.append(prop)
            continue
        try:
            applied.append(P.fill(project_id, prop, email)["proposal"])
        except Exception as exc:                                  # noqa: BLE001 - stays a card
            log.warning("handoff: %r kept as a card (%s)", fid, str(exc)[:160])
            opened.append(prop)
    return {"applied": applied, "open": opened, "refused": refused}


def create_for(user: dict, token: str) -> dict:
    """Create the project from a hand-off as ``user`` (the existing create path: 428 agreement / 429 limit propagate as
    ``HTTPException``), apply the values, carry the conversation over into the project's chat history and delete the record.
    Returns ``{project: {id, title}, applied, open, refused}``."""
    from motor_ai_sim.development import routes as R, spec_assistant_history as H, spec_proposals as P
    email = str(user.get("email") or "").strip().lower()
    rec = get(token, email)
    project = R.create_project_for(user, {}, audit_details={"via": "assistant"})
    pid = project["id"]
    drop(token)
    got = apply_values(pid, rec.get("values") or [], email, rec.get("lang") or "en")
    try:
        rows = [{"role": m["role"], "text": m["text"]} for m in rec.get("conversation") or [] if m.get("text")]
        if rows and got["applied"]:
            rows[-1]["applied"] = [p["id"] for p in got["applied"]]
        if rows and got["open"]:
            rows[-1]["proposals"] = [p["id"] for p in got["open"]]
        H.append(pid, email, rows)
    except Exception:                                             # noqa: BLE001 - the history is a convenience
        log.warning("handoff: conversation not carried over to %s", pid, exc_info=True)
    from motor_ai_sim.development import store
    title = (store.get_project(pid) or project).get("title") or pid
    return {"project": {"id": pid, "title": title}, "applied": P.with_current(pid, got["applied"]),
            "open": P.with_current(pid, got["open"]), "refused": got["refused"]}
