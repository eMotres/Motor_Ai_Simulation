"""Conversations of the design agent and the Claude tool loop that answers them.

One conversation = one JSON file in the caller's folder of the store (``DESIGN_AGENT_STORE_DIR``, default beside
``users.json``): the API-format message history (what the model reads back), the display transcript (what the
page shows: text, the tool steps, the motor cards) and the money spent.  A turn runs in a background thread; the
page polls the conversation until ``state`` is ``idle`` again, and sees each tool step while it runs.

MONEY (owner 2026-10-18, pilot): the corporate API key pays, so every call is priced from the usage the API
reports and booked in a daily ledger.  A turn is refused before the first call when the conversation, the user's
day or the whole service's day is over its cap, and stops between calls when the conversation cap is reached.
Caps and the model come from the environment (``DESIGN_AGENT_*``); the key is ``DESIGN_AGENT_API_KEY`` or
``ANTHROPIC_API_KEY`` and never leaves the server.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim.agent_keys import Principal
from motor_ai_sim.design_agent import tools as _tools
from motor_ai_sim.design_agent.prompt import SYSTEM_PROMPT
from motor_ai_sim.json_store import atomic_write_json, lock_for, read_json

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
#: USD per million tokens: input, output, cache write (5 min), cache read - the public list prices of 2026-10-18
PRICES: Dict[str, tuple] = {
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.10),
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}
MAX_STEPS = 12               # model calls per turn
MAX_USER_CHARS = 4000
MAX_USER_TURNS = 30
STALE_RUNNING_S = 600        # a turn "running" for longer than this died with its process


class AgentError(ValueError):
    """Refused before anything is spent (cap reached, busy, too long ...) - shown to the user as is."""


# ── configuration ────────────────────────────────────────────────────────────

def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def model() -> str:
    return (os.environ.get("DESIGN_AGENT_MODEL") or DEFAULT_MODEL).strip()


def api_key() -> str:
    return (os.environ.get("DESIGN_AGENT_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def limits() -> Dict[str, float]:
    return {"conversation_usd": _env_f("DESIGN_AGENT_CONVERSATION_USD", 1.0),
            "user_day_usd": _env_f("DESIGN_AGENT_USER_DAY_USD", 3.0),
            "service_day_usd": _env_f("DESIGN_AGENT_DAY_USD", 10.0)}


def access_mode() -> str:
    """Who may use it: ``admin`` (pilot default), ``staff`` (admin + engineer) or ``signed_in``."""
    v = (os.environ.get("DESIGN_AGENT_ACCESS") or "admin").strip().lower()
    return v if v in ("admin", "staff", "signed_in") else "admin"


def may_use(ident: Dict[str, Any]) -> bool:
    who = str(ident.get("id") or "")
    if not who or who == "anonymous":
        return False
    mode = access_mode()
    if ident.get("is_admin"):
        return True
    if mode == "staff":
        return ident.get("role") == "engineer"
    return mode == "signed_in"


def cost_usd(usage: Dict[str, int], mdl: str) -> float:
    pin, pout, pcw, pcr = PRICES.get(mdl, PRICES[DEFAULT_MODEL])
    return (usage.get("input_tokens", 0) * pin + usage.get("output_tokens", 0) * pout
            + usage.get("cache_creation_input_tokens", 0) * pcw
            + usage.get("cache_read_input_tokens", 0) * pcr) / 1e6


# ── store ────────────────────────────────────────────────────────────────────

def root() -> Path:
    env = (os.environ.get("DESIGN_AGENT_STORE_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH
    return Path(DEFAULT_CONFIG_PATH).parent / "design_agent"


def _owner_dir(owner: str) -> Path:
    return root() / "conversations" / hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]


def _path(owner: str, cid: str) -> Path:
    if not cid.startswith("c-") or not all(ch.isalnum() or ch == "-" for ch in cid):
        raise AgentError("bad conversation id")
    return _owner_dir(owner) / f"{cid}.json"


def _day(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts if ts is not None else time.time()))


def _ledger(day: str) -> Path:
    return root() / "usage" / f"{day}.jsonl"


def _book(owner: str, cid: str, mdl: str, usage: Dict[str, int], usd: float) -> None:
    p = _ledger(_day())
    p.parent.mkdir(parents=True, exist_ok=True)
    with lock_for(p):
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": round(time.time(), 1), "owner": owner, "conversation": cid, "model": mdl,
                                 **usage, "usd": round(usd, 6)}) + "\n")


def ledger_rows(day: Optional[str] = None) -> List[Dict[str, Any]]:
    p = _ledger(day or _day())
    if not p.exists():
        return []
    rows = []
    with lock_for(p):
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def spent_today(owner: Optional[str] = None) -> float:
    return sum(float(r.get("usd") or 0) for r in ledger_rows() if owner is None or r.get("owner") == owner)


def usage_summary(days: int = 14) -> Dict[str, Any]:
    out = []
    for i in range(days):
        day = _day(time.time() - i * 86400)
        rows = ledger_rows(day)
        if not rows:
            continue
        per: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            u = per.setdefault(str(r.get("owner")), {"usd": 0.0, "calls": 0, "conversations": set()})
            u["usd"] += float(r.get("usd") or 0)
            u["calls"] += 1
            u["conversations"].add(r.get("conversation"))
        out.append({"day": day, "usd": round(sum(u["usd"] for u in per.values()), 4),
                    "users": [{"owner": k, "usd": round(v["usd"], 4), "calls": v["calls"],
                               "conversations": len(v["conversations"])} for k, v in sorted(per.items())]})
    return {"model": model(), "limits": limits(), "access": access_mode(), "days": out}


def _load(owner: str, cid: str) -> Dict[str, Any]:
    doc = read_json(_path(owner, cid), default=None)
    if not isinstance(doc, dict) or doc.get("owner") != owner:
        raise AgentError("conversation not found")
    return doc


def _save(doc: Dict[str, Any]) -> None:
    doc["updated"] = time.time()
    p = _path(doc["owner"], doc["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, doc)


def view(doc: Dict[str, Any]) -> Dict[str, Any]:
    state = doc.get("state") or "idle"
    if state == "running" and time.time() - float(doc.get("updated") or 0) > STALE_RUNNING_S:
        state = "error"
    return {"id": doc["id"], "title": doc.get("title") or "", "created": doc.get("created"),
            "updated": doc.get("updated"), "state": state, "error": doc.get("error"),
            "display": doc.get("display") or [], "pending": doc.get("pending") or None,
            "cost_usd": round(float(doc.get("cost_usd") or 0), 4), "limits": limits()}


def list_conversations(owner: str) -> List[Dict[str, Any]]:
    d = _owner_dir(owner)
    rows = []
    if d.exists():
        for p in d.glob("c-*.json"):
            doc = read_json(p, default=None)
            if isinstance(doc, dict) and doc.get("owner") == owner:
                rows.append({"id": doc["id"], "title": doc.get("title") or "", "updated": doc.get("updated"),
                             "cost_usd": round(float(doc.get("cost_usd") or 0), 4)})
    return sorted(rows, key=lambda r: -(r["updated"] or 0))


def get_conversation(owner: str, cid: str) -> Dict[str, Any]:
    return view(_load(owner, cid))


def delete_conversation(owner: str, cid: str) -> None:
    doc = _load(owner, cid)
    if doc.get("state") == "running" and view(doc)["state"] == "running":
        raise AgentError("the assistant is still answering")
    _path(owner, cid).unlink(missing_ok=True)


# ── a turn ───────────────────────────────────────────────────────────────────

def _check_budget(owner: str, doc: Optional[Dict[str, Any]]) -> None:
    lim = limits()
    if doc is not None and float(doc.get("cost_usd") or 0) >= lim["conversation_usd"]:
        raise AgentError("this conversation reached its cost limit - start a new one")
    if spent_today(owner) >= lim["user_day_usd"]:
        raise AgentError("your daily limit for the design assistant is used up - it resets at 00:00 UTC")
    if spent_today() >= lim["service_day_usd"]:
        raise AgentError("the design assistant is busy today - please try again tomorrow")


def start_turn(principal: Principal, text: str, cid: Optional[str] = None) -> Dict[str, Any]:
    """Append the user's message and answer it in a background thread.  Returns the conversation view."""
    owner = principal.email
    text = str(text or "").strip()
    if not text:
        raise AgentError("empty message")
    if len(text) > MAX_USER_CHARS:
        raise AgentError(f"message too long (max {MAX_USER_CHARS} characters)")
    if not api_key():
        raise AgentError("the design assistant is not configured on this server")
    if cid:
        path = _path(owner, cid)
        with lock_for(path):
            doc = _load(owner, cid)
            if view(doc)["state"] == "running":
                raise AgentError("the assistant is still answering")
            if sum(1 for d in doc.get("display") or [] if d.get("role") == "user") >= MAX_USER_TURNS:
                raise AgentError("this conversation is long - start a new one")
            _check_budget(owner, doc)
            _close_open_round(doc, "the previous answer was interrupted")
            doc["messages"].append({"role": "user", "content": text})
            doc["display"].append({"role": "user", "text": text, "ts": time.time()})
            doc.update(state="running", error=None, pending={"steps": []})
            _save(doc)
    else:
        _check_budget(owner, None)
        cid = "c-" + secrets.token_hex(6)
        doc = {"id": cid, "owner": owner, "title": text[:80], "created": time.time(), "model": model(),
               "messages": [{"role": "user", "content": text}],
               "display": [{"role": "user", "text": text, "ts": time.time()}],
               "state": "running", "error": None, "pending": {"steps": []}, "cost_usd": 0.0}
        _save(doc)
    threading.Thread(target=_run_turn_tracked, args=(principal, cid), daemon=True,
                     name=f"design-agent-{cid}").start()
    return view(doc)


def _run_turn_tracked(principal: Principal, cid: str) -> None:
    """The answer thread, counted as work in progress so an API update (blue-green, deploy stage 2) lets it finish."""
    from motor_ai_sim import live_status
    with live_status.background("design-agent"):
        _run_turn(principal, cid)


def _close_open_round(doc: Dict[str, Any], why: str) -> None:
    """Roles must alternate: a history that ends with a user turn (an interrupted answer) gets a short assistant
    turn saying so before anything else is appended."""
    msgs = doc.get("messages") or []
    if msgs and msgs[-1]["role"] == "user":
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"({why})"}]})


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=api_key(), max_retries=2, timeout=120.0)


def _tools_param() -> List[Dict[str, Any]]:
    specs = copy.deepcopy(_tools.TOOL_SPECS)
    specs[-1]["cache_control"] = {"type": "ephemeral"}
    return specs


def _with_cache_mark(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A copy of the history with one cache breakpoint on its last block (earlier turns are read from cache)."""
    msgs = copy.deepcopy(messages)
    last = msgs[-1]
    if isinstance(last["content"], str):
        last["content"] = [{"type": "text", "text": last["content"]}]
    last["content"][-1]["cache_control"] = {"type": "ephemeral"}
    return msgs


def _blocks(content: Any) -> List[Dict[str, Any]]:
    out = []
    for b in content or []:
        t = getattr(b, "type", None)
        if t == "text":
            out.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input or {})})
    return out


def _usage(resp: Any) -> Dict[str, int]:
    u = getattr(resp, "usage", None)
    return {k: int(getattr(u, k, 0) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                    "cache_read_input_tokens")}


def _update(owner: str, cid: str, fn) -> Dict[str, Any]:
    path = _path(owner, cid)
    with lock_for(path):
        doc = _load(owner, cid)
        fn(doc)
        _save(doc)
        return doc


def _run_turn(principal: Principal, cid: str) -> None:
    owner, mdl = principal.email, model()
    steps: List[Dict[str, Any]] = []
    cards: List[Dict[str, Any]] = []
    try:
        client = _client()
        for _ in range(MAX_STEPS):
            doc = _load(owner, cid)
            resp = client.messages.create(
                model=mdl, max_tokens=4096,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                tools=_tools_param(), messages=_with_cache_mark(doc["messages"]))
            usage = _usage(resp)
            usd = cost_usd(usage, mdl)
            _book(owner, cid, mdl, usage, usd)
            blocks = _blocks(resp.content)
            results = []
            if resp.stop_reason == "tool_use":
                for b in blocks:
                    if b["type"] != "tool_use":
                        continue
                    step = {"tool": b["name"], "args": b["input"], "ok": True}
                    try:
                        res = _tools.run_tool(principal, b["name"], b["input"])
                        if b["name"] == "present_motor" and isinstance(res.get("card"), dict):
                            cards.append(res["card"])
                        results.append({"type": "tool_result", "tool_use_id": b["id"],
                                        "content": json.dumps(res, ensure_ascii=False, default=str)})
                    except _tools.AgentToolError as exc:
                        step.update(ok=False, error=str(exc))
                        results.append({"type": "tool_result", "tool_use_id": b["id"], "is_error": True,
                                        "content": str(exc)})
                    except Exception as exc:                 # noqa: BLE001 - a tool bug must not kill the turn
                        log.exception("design agent tool %s failed", b["name"])
                        step.update(ok=False, error="internal error")
                        results.append({"type": "tool_result", "tool_use_id": b["id"], "is_error": True,
                                        "content": f"internal error in {b['name']}: {type(exc).__name__}"})
                    steps.append(step)

            def _apply(d: Dict[str, Any]) -> None:
                d["cost_usd"] = float(d.get("cost_usd") or 0) + usd
                d["messages"].append({"role": "assistant", "content": blocks or [{"type": "text", "text": "..."}]})
                if results:
                    d["messages"].append({"role": "user", "content": results})
                d["pending"] = {"steps": steps}
            doc = _update(owner, cid, _apply)
            if not results:
                text = "\n\n".join(b["text"] for b in blocks if b["type"] == "text").strip()
                _finish(owner, cid, text, steps, cards)
                return
            if float(doc.get("cost_usd") or 0) >= limits()["conversation_usd"]:
                _finish(owner, cid, None, steps, cards,
                        error="the conversation reached its cost limit before the answer was complete")
                return
        _finish(owner, cid, None, steps, cards, error="the assistant needed too many steps - please narrow the question")
    except Exception as exc:                                 # noqa: BLE001
        log.exception("design agent turn failed (%s)", cid)
        try:
            _finish(owner, cid, None, steps, cards, error=f"the assistant is unavailable ({type(exc).__name__})")
        except Exception:                                    # noqa: BLE001
            log.exception("design agent could not record the failure (%s)", cid)


def _finish(owner: str, cid: str, text: Optional[str], steps: List[Dict[str, Any]], cards: List[Dict[str, Any]],
            error: Optional[str] = None) -> None:
    def _apply(d: Dict[str, Any]) -> None:
        if text or steps or cards:
            d["display"].append({"role": "assistant", "text": text or "", "steps": steps, "cards": cards,
                                 "ts": time.time()})
        if error:
            # the history must end with a user turn the model can continue from: close an open tool round
            _close_open_round(d, error)
        d.update(state="idle" if not error else "error", error=error, pending=None)
    _update(owner, cid, _apply)
