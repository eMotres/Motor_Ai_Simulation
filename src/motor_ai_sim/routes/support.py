"""Support assistant — proxies the in-app chat to an AI provider (Claude or Gemini).

POST /api/support/chat takes the running conversation and returns the assistant's
reply. API keys live ONLY on the backend and are NEVER shipped to the browser.

Config resolution (admin override > env > auto), per field:
  - provider:  Firestore config/ai.provider  >  SUPPORT_PROVIDER env  >  auto
  - keys/models: Firestore config/ai.*  >  env  >  built-in default

Admins set the override from the Admin UI (POST /api/admin/support -> set_overrides),
which writes config/ai in Firestore via the Admin SDK. Keys are write-only: the
status endpoint returns only a masked hint, never the key. IMPORTANT: lock your
Firestore rules so clients cannot read the `config` collection (see firestore.rules).

When no key is configured anywhere, the endpoint returns a flagged mock reply.

ANONYMOUS CALLERS (2026-09-17).  The landing page shows this widget to a
signed-out visitor, so the route is on ``auth._ANON_OK_PATHS`` and no longer
carries a tier — otherwise the product's own "how can I get access?" answer was
a 401 that the widget printed as "Sorry — I couldn't answer just now".  It is
the only open route that costs money per call, so the bill is held down here
instead of at the door:

  * per IP: ``ANON_BURST_MAX`` messages per ``ANON_BURST_WINDOW_S`` and
    ``ANON_DAY_MAX`` per day (the address nginx hands us — ``client_ip``);
  * for the anonymous audience as a whole: ``ANON_GLOBAL_DAY_MAX`` per day;
  * the history is cut to ``ANON_MAX_TURNS`` and each message to
    ``ANON_MAX_CHARS`` before it is spent on a provider call.

Over a cap the caller gets a polite canned reply with 429 and NO provider call,
and one line reaches the log.  A signed-in caller is never counted or capped —
the counters are keyed on "no credentials presented" and nothing else.  They are
in-memory (one API process) and thread-safe; a restart forgives everyone, which
is the right failure for a limit whose job is to bound a bill, not to punish.

WHERE A VISITOR'S WORDS GO (2026-09-17, the owner's question: "how will the
messages they write to the bot reach us?").  Two things happen to an
ANONYMOUS turn after the provider answers, and neither to a signed-in one:

  * every turn is appended to the day's visitor log
    (``support_store.log_visitor_turn``) — including the ones the limiter
    refused, so "forty questions then a wall" is visible;
  * if the reply ends with the assistant's ACCESS-REQUEST MARKER, the marker is
    parsed, STRIPPED from what the visitor reads, and filed as a structured
    request in the admin inbox, with a Telegram push if the owner configured one.

The marker is this codebase's substitute for a tool API — the provider call here
is one plain text completion, with no tools and no function calling, so the only
channel the model has back to us is the text itself::

    [[ACCESS_REQUEST: name="…"; company="…"; email="…"; note="…"]]

``VISITOR_NOTE`` is the contract that tells it when to write one.  The parser is
deliberately forgiving about quoting and unknown keys and deliberately strict
about ONE thing — the e-mail — because that is the field the record is keyed and
de-duplicated on, and an invented address is worse than no request at all.

EVERYTHING A SIGNED-IN USER REPORTS GOES THROUGH THE ASSISTANT (2026-10-05).
There is no report form.  The widget sends the conversation plus a snapshot of
the session (``context``: tab, motor, Configure knobs and tiles, build, browser,
the last failed API calls - sanitised by ``support_context``, never a token or
another user's data); the model gets the snapshot as hidden context and the
prompt FOR THE CALLER'S ROLE (``support_prompt``: a regular account sees only
Motors + Configure, staff see every tab).  When the user has a bug, a request, an
account problem or a question the model cannot answer, it ends its reply with a
``[[TICKET_DRAFT {json}]]`` marker (``parse_ticket_draft``): the marker is
stripped and returned as ``ticketDraft``, the widget shows it as a card, and the
user edits it and presses Send, which posts to ``POST /api/support/tickets``
with the conversation and context attached.  Nothing is filed by the model.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import threading
import time
from typing import Optional

from fastapi import APIRouter, Body, Header, Request
from fastapi.responses import JSONResponse

from motor_ai_sim import support_context
from motor_ai_sim.support_prompt import (
    STAFF_PROMPT, TICKET_PROTOCOL, USER_PROMPT, prompt_for_role,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/support", tags=["support"])

_MAX_TURNS = 20

# Env defaults (used when there's no admin override in Firestore).
ENV_PROVIDER = os.environ.get("SUPPORT_PROVIDER", "").strip().lower()
ENV_GEMINI_KEY = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
ENV_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest").strip()
ENV_ANTHROPIC_KEY = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
ENV_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_SUPPORT_MODEL", "claude-opus-4-8").strip()

#: The FULL prompt (every tab).  Kept under its old name for the admin settings
#: page and for callers that want "the" prompt; what a given caller is actually
#: sent is ``prompt_for_role`` (a regular account gets the Motors + Configure
#: one - see ``support_prompt``).
SYSTEM_PROMPT = STAFF_PROMPT


#: Appended to the system prompt for a caller with NO account (the landing page
#: widget).  A visitor is not a user: they cannot open a tab, they have no
#: granted motors, and the only answer they are actually after is how to get in.
#:
#: It also carries the ACCESS-REQUEST CONTRACT — the marker that turns "somebody
#: chatted with the bot" into a row in the team's inbox.  There is no tool API in
#: this call, so the contract is written the way a person would be told it, and
#: the backend (``parse_access_request``) is what actually enforces it: the line
#: never reaches the visitor, and a request without a valid e-mail is never filed.
VISITOR_NOTE = """

## Visitor mode — this person is NOT signed in
They are on the public landing page and have no account yet. Answer product
questions briefly and plainly — what the portal is for, what it analyses, what a
motor design involves — in two or three sentences. Access is **by invitation**:
the **Request access** link on the page, or a note to vadim@motresres.com, and
the team creates the account and grants the motors it may open. Plans and
pricing are agreed individually; never name a price or a plan, and never say
there is a free tier or a trial. Do NOT describe internal data, the catalog's contents,
any specific customer machine or numbers from any design, and do not walk
them step by step through tabs they cannot open yet.

### What to do, and when
1. **A question about the product** → answer it, briefly. Nothing else needed.
2. **They want access, a quote, a demo, prices, or to be contacted** → collect
   their details, ONE QUESTION AT A TIME, in a sentence, never as a form or a
   list of fields: their name, then the company, then their work e-mail, then in
   one sentence what they want to do (which machine, what power / torque /
   speed, what it is for). Never ask again for something they already told you.
   As soon as you have a valid e-mail — even if the rest is still missing —
   finish that reply with the confirmation *"I've passed this to the team —
   you'll hear from vadim@motresres.com."* and then, as the VERY LAST line of
   the message, on its own line, exactly this:
   [[ACCESS_REQUEST: name="…"; company="…"; email="…"; note="…"]]
   Fill in what you know and leave "" for what you do not. Write it at most ONCE
   per reply, never in a reply that carries no e-mail address, and never mention
   it, explain it, quote it or offer it as an example — it is for the team's
   system, not for the visitor, who never sees it.
3. **A bug, an idea, or something that looks broken** → thank them and say the
   team reads these conversations, so it has been passed on. Ask for one detail
   (what they did and what happened) so it is useful.
4. **Never promise a timeline, a price, a plan, a tier, a trial, a delivery date
   or a callback within any particular time.** The team answers every request
   personally, and that is all you may say about when."""


# ── the access-request marker ────────────────────────────────────────────────
#: What the assistant appends when it has collected a visitor's contact details.
#: Values may be double-quoted, single-quoted or bare (a model is not a parser
#: generator); keys other than the four are ignored rather than fatal; a `]` can
#: never appear inside, which is what stops a half-written marker from eating the
#: rest of the reply.
_MARKER_RE = re.compile(r"\[\[\s*ACCESS_REQUEST\s*:(?P<body>[^\]]*)\]\]",
                        re.IGNORECASE | re.DOTALL)
#: The same thing, plus the whitespace that only existed to separate it from the
#: sentence above: removing the marker alone leaves the visitor's message ending
#: in a hole where it used to be.
_MARKER_CUT_RE = re.compile(r"\s*\[\[\s*ACCESS_REQUEST\s*:[^\]]*\]\][ \t]*",
                            re.IGNORECASE | re.DOTALL)
#: A marker the model started and never closed: it must still not reach the
#: visitor, and there is nothing after it worth keeping.
_MARKER_TAIL_RE = re.compile(r"\s*\[\[\s*ACCESS_REQUEST\s*:.*\Z",
                             re.IGNORECASE | re.DOTALL)
_FIELD_RE = re.compile(
    r"""(?P<key>[A-Za-z_]+)\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^;]*))""",
    re.DOTALL)
#: Deliberately plain: one @, a dotted domain, a 2+ letter TLD, no spaces.  It is
#: a guard against "the model wrote a sentence where the address goes", not an
#: RFC 5322 implementation — the team's reply is what proves an address real.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$")
_FIELDS = ("name", "company", "email", "note")
#: Said in the visitor's place when stripping the marker leaves nothing — a
#: model that answers with the marker ALONE has still done the job, and an empty
#: bubble would tell the visitor their details went nowhere.
ACCESS_CONFIRMATION = (
    "Thank you — I've passed this to the team. You'll hear from "
    "vadim@motresres.com."
)


def valid_email(value: str) -> bool:
    v = (value or "").strip()
    return bool(v) and len(v) <= 254 and bool(_EMAIL_RE.match(v))


def _fields_of(body: str) -> dict:
    out: dict = {}
    for m in _FIELD_RE.finditer(body or ""):
        key = (m.group("key") or "").strip().lower()
        if key not in _FIELDS:
            continue
        raw = m.group("dq")
        if raw is None:
            raw = m.group("sq")
        if raw is None:
            raw = m.group("bare") or ""
        out[key] = raw.strip().strip(",").strip()
    return out


def parse_access_request(reply: str) -> tuple[str, Optional[dict]]:
    """Split one assistant reply into (what the visitor reads, the request).

    The marker NEVER survives into the first half — not when it parses, not when
    it is malformed, not when the model wrote three of them.  The second half is
    the LAST marker that carries a valid e-mail, or ``None``: a request with a
    made-up address is a row nobody can answer, and filing it would only teach
    the inbox to be ignored.
    """
    text = reply or ""
    best: Optional[dict] = None
    for m in _MARKER_RE.finditer(text):
        f = _fields_of(m.group("body"))
        email = (f.get("email") or "").strip().lower()
        if not valid_email(email):
            continue
        best = {"name": f.get("name", "")[:200],
                "company": f.get("company", "")[:200],
                "email": email,
                "note": f.get("note", "")[:1000]}
    clean = _MARKER_CUT_RE.sub("", text)
    clean = _MARKER_TAIL_RE.sub("", clean)
    # Collapse the blank lines the removal left behind, so the visitor's bubble
    # does not end in a hole where the marker used to be.
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, best


# ── the ticket-draft marker ──────────────────────────────────────────────────
#: What the assistant appends when it has prepared a ticket for a SIGNED-IN user
#: (``support_prompt.TICKET_PROTOCOL``)::
#:
#:     [[TICKET_DRAFT {"type": "bug", "title": "…", "description": "…"}]]
#:
#: Like the access-request marker it is this codebase's substitute for a tool API
#: (the provider call is one plain text completion).  Unlike it, the payload is
#: JSON, because a description is free text and may contain ``]``.  The parser
#: is forgiving (a ``key="value"`` body is accepted too) and the marker NEVER
#: survives into what the user reads - parsed, malformed or unclosed.  A draft is
#: only a proposal: the widget shows it, the user edits it and presses Send, and
#: only ``POST /api/support/tickets`` files anything.
_DRAFT_START_RE = re.compile(r"\[\[\s*TICKET_DRAFT\b", re.IGNORECASE)
_DRAFT_FIELD_RE = re.compile(
    r"""(?P<key>type|title|description)\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^;\]]*))""",
    re.IGNORECASE | re.DOTALL)


def _draft_type(raw) -> str:
    t = str(raw or "").strip().lower()
    for key, name in (("bug", "bug"), ("feat", "feature"), ("account", "account"),
                      ("access", "account"), ("login", "account"), ("sign", "account")):
        if key in t:
            return name
    return "question"


def _draft_from(fields: dict) -> Optional[dict]:
    from motor_ai_sim import ticket_store as T
    title = re.sub(r"\s+", " ", str(fields.get("title") or "")).strip()
    if not title:
        return None
    desc = str(fields.get("description") or "").strip()
    return {"type": _draft_type(fields.get("type")),
            "title": title[:T.MAX_TITLE],
            "description": desc[:T.MAX_DESCRIPTION]}


def parse_ticket_draft(reply: str) -> tuple[str, Optional[dict]]:
    """Split one assistant reply into (what the user reads, the ticket draft).

    Every ``[[TICKET_DRAFT ...]]`` span is cut out of the text, whether or not it
    parses; the draft is the LAST span with a non-empty title, else ``None``.
    """
    text = reply or ""
    draft: Optional[dict] = None
    out: list[str] = []
    pos = 0
    dec = json.JSONDecoder(strict=False)   # a model writes raw newlines inside strings
    while True:
        m = _DRAFT_START_RE.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        out.append(text[pos:m.start()])
        i = m.end()
        fields: Optional[dict] = None
        end = len(text)
        j = text.find("{", i)
        if j != -1 and not text[i:j].strip(" \t\r\n:"):
            try:
                obj, k = dec.raw_decode(text, j)
            except ValueError:
                obj, k = None, j
            if isinstance(obj, dict):
                fields = obj
                end = k
                close = text.find("]]", k)
                if close != -1 and not text[k:close].strip():
                    end = close + 2
        if fields is None:
            close = text.find("]]", i)
            body = text[i: close if close != -1 else len(text)]
            fields = {}
            for fm in _DRAFT_FIELD_RE.finditer(body):
                raw = fm.group("dq")
                if raw is None:
                    raw = fm.group("sq")
                if raw is None:
                    raw = fm.group("bare") or ""
                fields[fm.group("key").lower()] = raw.strip()
            end = (close + 2) if close != -1 else len(text)
        d = _draft_from(fields)
        if d is not None:
            draft = d
        pos = end
    clean = "".join(out)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, draft


# ── Firestore-backed admin overrides (config/ai) ─────────────────────────────
_fb_done = False
_fb_db = None


def _firestore():
    """Firestore client via the Admin SDK, or None if unavailable (local dev)."""
    global _fb_done, _fb_db
    if _fb_done:
        return _fb_db
    _fb_done = True
    try:
        import firebase_admin
        from firebase_admin import firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        _fb_db = firestore.client()
    except Exception:
        _fb_db = None
    return _fb_db


_ov_cache: dict = {}
_ov_exp: float = 0.0


def _load_overrides() -> dict:
    """Admin overrides from Firestore config/ai, cached ~20s. {} if unavailable."""
    global _ov_cache, _ov_exp
    now = time.time()
    if now < _ov_exp:
        return _ov_cache
    data: dict = {}
    db = _firestore()
    if db is not None:
        try:
            snap = db.collection("config").document("ai").get()
            if getattr(snap, "exists", False):
                data = snap.to_dict() or {}
        except Exception:
            data = {}
    _ov_cache, _ov_exp = data, now + 20.0
    return data


def _str(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _effective() -> dict:
    """Merge admin overrides over env defaults. Keys resolve admin > env."""
    ov = _load_overrides()
    gem_key = _str(ov.get("gemini_key")) or ENV_GEMINI_KEY
    gem_src = "admin" if _str(ov.get("gemini_key")) else ("env" if ENV_GEMINI_KEY else "none")
    ant_key = _str(ov.get("anthropic_key")) or ENV_ANTHROPIC_KEY
    ant_src = "admin" if _str(ov.get("anthropic_key")) else ("env" if ENV_ANTHROPIC_KEY else "none")
    gem_model = _str(ov.get("gemini_model")) or ENV_GEMINI_MODEL
    ant_model = _str(ov.get("anthropic_model")) or ENV_ANTHROPIC_MODEL
    prov = (_str(ov.get("provider")) or ENV_PROVIDER).lower()
    if prov not in ("anthropic", "gemini"):
        prov = "gemini" if gem_key else ("anthropic" if ant_key else "none")
    return {
        "provider": prov,
        "gemini": {"key": gem_key, "model": gem_model, "key_source": gem_src},
        "anthropic": {"key": ant_key, "model": ant_model, "key_source": ant_src},
    }


def _effective_prompt(role: Optional[str] = None) -> str:
    """Admin-overridden system prompt (config/ai.system_prompt, one text for every
    caller) or the default FOR THE CALLER'S ROLE (``support_prompt.prompt_for_role``;
    no role = the full prompt, which is what the admin settings page shows)."""
    custom = _str(_load_overrides().get("system_prompt"))
    if custom:
        return custom
    return STAFF_PROMPT if role is None else prompt_for_role(role)


def _mask(k: str):
    if not k:
        return None
    return (k[:4] + "…" + k[-4:]) if len(k) > 9 else ("…" + k[-2:])


def provider_status() -> dict:
    """Non-secret status for the admin UI — never returns a key, only a masked hint."""
    eff = _effective()
    g, a = eff["gemini"], eff["anthropic"]
    active = eff["provider"]
    model = g["model"] if active == "gemini" else a["model"] if active == "anthropic" else None
    configured = bool(g["key"]) if active == "gemini" else bool(a["key"]) if active == "anthropic" else False
    return {
        "provider": active, "model": model, "configured": configured,
        "providerOverride": _str(_load_overrides().get("provider")),  # "" = auto
        "store": "firestore" if _firestore() is not None else "env-only",
        "gemini": {"model": g["model"], "configured": bool(g["key"]), "hint": _mask(g["key"]), "keySource": g["key_source"]},
        "anthropic": {"model": a["model"], "configured": bool(a["key"]), "hint": _mask(a["key"]), "keySource": a["key_source"]},
        "systemPrompt": _effective_prompt(),
        "promptIsCustom": bool(_str(_load_overrides().get("system_prompt"))),
    }


def set_overrides(data: dict, who: str = "admin") -> dict:
    """Persist admin overrides to Firestore config/ai. Keys are write-only.
    Returns {ok: False, error} when the store is unavailable (local dev)."""
    db = _firestore()
    if db is None:
        return {"ok": False, "error": "Settings store unavailable (Firebase Admin SDK not configured on this server). Configure via env vars in local dev."}
    patch: dict = {}
    prov = _str(data.get("provider")).lower()
    if prov in ("", "auto"):
        patch["provider"] = ""
    elif prov in ("anthropic", "gemini"):
        patch["provider"] = prov
    for f in ("gemini_model", "anthropic_model"):
        if isinstance(data.get(f), str):
            patch[f] = _str(data.get(f))
    # System prompt (the assistant's project knowledge). Empty -> falls back to default.
    if isinstance(data.get("system_prompt"), str):
        patch["system_prompt"] = _str(data.get("system_prompt"))
    for name in ("gemini", "anthropic"):
        kf = f"{name}_key"
        if data.get(f"{name}_key_clear"):
            patch[kf] = ""               # explicit clear
        elif _str(data.get(kf)):
            patch[kf] = _str(data.get(kf))  # set only when a non-empty value is provided
    patch["updatedBy"] = who
    try:
        from firebase_admin import firestore as _fs
        patch["updatedAt"] = _fs.SERVER_TIMESTAMP
    except Exception:
        pass
    db.collection("config").document("ai").set(patch, merge=True)
    global _ov_exp
    _ov_exp = 0.0  # invalidate cache so the change takes effect immediately
    return {"ok": True}


# ── Model discovery (live from each provider, with a static fallback) ─────────
_STATIC_MODELS = {
    "gemini": [
        "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash",
        "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash",
    ],
    "anthropic": [
        "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5",
    ],
}


# Drop non-text-chat models (image / audio / music / robotics / etc.) from the
# picker — they can't power a text support chat even though they list generateContent.
_GEMINI_DENY = (
    "image", "tts", "audio", "music", "lyria", "robotics", "embedding",
    "computer-use", "nano-banana", "deep-research", "antigravity", "veo", "imagen",
)


def _gemini_models(key: str) -> list[str]:
    import urllib.request
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for m in data.get("models", []):
        if "generateContent" in (m.get("supportedGenerationMethods") or []):
            name = (m.get("name") or "").split("/")[-1]
            if name and not any(d in name for d in _GEMINI_DENY):
                out.append(name)
    return sorted(set(out))


def _anthropic_models(key: str) -> list[str]:
    import anthropic
    c = anthropic.Anthropic(api_key=key)
    return [m.id for m in c.models.list()]


def list_models() -> dict:
    """Available models per provider — live from the provider API when a key is
    present, else a curated static list. Never raises."""
    eff = _effective()
    res: dict = {}
    for name, fetch in (("gemini", _gemini_models), ("anthropic", _anthropic_models)):
        models, source = _STATIC_MODELS[name], "static"
        key = eff[name]["key"]
        if key:
            try:
                live = fetch(key)
                if live:
                    models, source = live, "live"
            except Exception:
                pass
        res[name] = {"models": models, "source": source}
    return res


# ── Provider clients ─────────────────────────────────────────────────────────
_ant_clients: dict = {}


def _anthropic_client(key: str):
    if not key:
        return None
    if key in _ant_clients:
        return _ant_clients[key]
    try:
        import anthropic
        c = anthropic.Anthropic(api_key=key)
    except Exception:
        c = None
    _ant_clients[key] = c
    return c


def _gemini_reply(messages: list[dict], key: str, model: str, system_prompt: str) -> str:
    """Call the Google Gemini REST API (no SDK dependency). Raises on failure."""
    import urllib.request
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        # headroom for a ticket draft (a JSON line) after the answer
        "generationConfig": {"maxOutputTokens": 2048},
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    cands = out.get("candidates") or []
    parts = (cands[0].get("content", {}).get("parts") if cands else None) or []
    return "".join(p.get("text", "") for p in parts).strip()


# ── one retry, for the provider's own bad minute ─────────────────────────────
#: Statuses that mean "not now", not "no": Gemini answers 503 "the model is
#: overloaded" often enough that a visitor's FIRST and only question lands on
#: one (seen live on 2026-09-17, on the old prompt as well as the new one — the
#: same call succeeded two seconds later).  A visitor does not press send twice;
#: they read "Sorry — I couldn't answer just now" and leave.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_PAUSE_S = 1.2


def _is_transient(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in _RETRY_STATUS:
        return True
    m = str(e).lower()
    return any(w in m for w in ("unavailable", "overloaded", "timed out",
                                "timeout", "resource_exhausted"))


def _call_provider(fn, *, attempts: int = 2, pause: Optional[float] = None):
    """Run one provider call, retrying ONCE on a transient failure.

    Deliberately not a backoff ladder: the caller is a person watching a
    "thinking…" dot, and the rate limiter counts the request, not the attempts.
    """
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:                              # noqa: BLE001
            if i + 1 >= attempts or not _is_transient(e):
                raise
            logger.warning("support: provider call failed (%s: %s) — retrying once",
                           type(e).__name__, str(e)[:120])
            time.sleep(_RETRY_PAUSE_S if pause is None else pause)


def _sanitize(raw, *, max_turns: int = _MAX_TURNS, max_chars: int = 4000) -> list[dict]:
    """Coerce the client payload into a clean alternating-friendly message list.

    An anonymous caller gets the SHORTER caps (``ANON_MAX_TURNS`` /
    ``ANON_MAX_CHARS``): the history is what a chat costs per call, and a
    visitor's question fits in a paragraph.
    """
    out: list[dict] = []
    for m in (raw or [])[-max_turns:]:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content[:max_chars]})
    while out and out[0]["role"] != "user":  # the API requires a leading user turn
        out.pop(0)
    return out


# ── The anonymous audience: how much it may ask ──────────────────────────────
#: Per IP, per ``ANON_BURST_WINDOW_S`` — a real visitor asks a handful of
#: questions; eight in ten minutes is more than the owner's own two test runs
#: and far less than a script's.
ANON_BURST_MAX = 8
ANON_BURST_WINDOW_S = 600.0
#: Per IP, per day.
ANON_DAY_MAX = 40
#: And the whole anonymous audience per day — the ceiling on the bill even if
#: the traffic arrives from a thousand addresses.
ANON_GLOBAL_DAY_MAX = 500
#: What an anonymous conversation may carry into a provider call.
ANON_MAX_TURNS = 6
ANON_MAX_CHARS = 1000

_DAY_S = 86400.0
#: Env overrides, read per call so a server can retune without a code change.
_CAP_ENV = {
    "burst": "SUPPORT_ANON_BURST_MAX",
    "day": "SUPPORT_ANON_DAY_MAX",
    "global": "SUPPORT_ANON_GLOBAL_DAY_MAX",
}


def _cap(name: str) -> int:
    default = {"burst": ANON_BURST_MAX, "day": ANON_DAY_MAX,
               "global": ANON_GLOBAL_DAY_MAX}[name]
    raw = os.environ.get(_CAP_ENV[name], "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


#: The address families that can only be a hop of OUR OWN plumbing — the docker
#: bridge (172.16/12), a host-local proxy (127/8), a LAN (10/8, 192.168/16) —
#: and never a visitor arriving from the internet.  Spelled out rather than
#: taken from ``ip.is_private``, which in Python also answers True for the
#: documentation ranges (192.0.2/24, 198.51.100/24, 203.0.113/24) that tests and
#: examples are written in: those must behave like the visitors they stand for.
_INTERNAL_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "169.254.0.0/16", "0.0.0.0/32", "::1/128", "fc00::/7", "fe80::/10",
))


def _is_internal(ip: str) -> bool:
    """Is this hop our own plumbing rather than a visitor?"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in _INTERNAL_NETS if n.version == a.version)


def client_ip(request: Optional[Request]) -> str:
    """The VISITOR's address, read out of the proxy chain in front of us.

    On the server a request crosses TWO nginxes: the host one (TLS, port 443)
    and the container one (``deploy/nginx.conf``, in front of the API).  Both
    use ``$proxy_add_x_forwarded_for``, i.e. each APPENDS the peer it saw, so
    what arrives is

        <whatever the client itself sent>, <the real client>, 172.18.0.x

    — the last entry is the docker bridge and is the same for every visitor on
    earth (seen in the live log on 2026-09-17: ``ip=172.18.0.1`` for a per-IP
    limit, which would have made ONE bucket for the whole internet).  The last
    entry a client can forge is never the last PUBLIC one either, because every
    hop appends after it: dropping the internal tail and taking what remains is
    both correct and unforgeable.  ``X-Real-IP`` is rewritten by the inner nginx
    to that same bridge address, so it is only a fallback for a one-hop
    deployment; last comes the socket peer, which is what a local run without a
    proxy has.
    """
    if request is None:
        return ""
    try:
        headers = request.headers
    except Exception:                                        # pragma: no cover
        return ""
    xff = (headers.get("x-forwarded-for") or "").strip()
    if xff:
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        while hops and _is_internal(hops[-1]):
            hops.pop()
        if hops:
            return hops[-1]
        last = [h.strip() for h in xff.split(",") if h.strip()]
        if last:                       # an all-internal chain: a LAN/dev call
            return last[-1]
    real = (headers.get("x-real-ip") or "").strip()
    if real:
        return real
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or ""


_rl_lock = threading.Lock()
_ip_hits: dict[str, list[float]] = {}
_global_hits: list[float] = []

#: What the visitor reads instead of an answer.  It is still an ANSWER to the
#: question they came with — access is by invitation — so it names the way in.
_LIMIT_REPLIES = {
    "ip_burst": (
        "I've answered as many questions as I can from this connection for the "
        "moment — please try again in a few minutes.\n\n"
        "If you're after access: the portal is invitation-only. Use the "
        "**Request access** link on this page, or write to vadim@motresres.com, "
        "and we'll set you up."
    ),
    "ip_day": (
        "That's my daily limit for questions from this connection. Please write "
        "to vadim@motresres.com — access to the portal is by invitation and we "
        "answer every request personally."
    ),
    "global_day": (
        "The assistant has reached today's limit for visitors. Please write to "
        "vadim@motresres.com — access to the portal is by invitation, and we "
        "answer every request personally."
    ),
}
_RETRY_AFTER = {"ip_burst": int(ANON_BURST_WINDOW_S), "ip_day": 3600,
                "global_day": 3600}


def reset_limits() -> None:
    """Forget every counter (tests; a fresh process starts here anyway)."""
    with _rl_lock:
        _ip_hits.clear()
        _global_hits.clear()


def _charge_anonymous(ip: str, now: Optional[float] = None) -> Optional[str]:
    """Check AND record one anonymous message, atomically.

    Returns ``None`` when the call may go to the provider, else the key of the
    cap it hit (``ip_burst`` | ``ip_day`` | ``global_day``).  A refused call is
    NOT recorded: the counters measure what was spent, and a refusal spends
    nothing.
    """
    now = time.time() if now is None else now
    key = ip or "?"
    with _rl_lock:
        hits = [t for t in _ip_hits.get(key, ()) if now - t < _DAY_S]
        recent = sum(1 for t in hits if now - t < ANON_BURST_WINDOW_S)
        glob = [t for t in _global_hits if now - t < _DAY_S]
        _global_hits[:] = glob
        if recent >= _cap("burst"):
            _ip_hits[key] = hits
            return "ip_burst"
        if len(hits) >= _cap("day"):
            _ip_hits[key] = hits
            return "ip_day"
        if len(glob) >= _cap("global"):
            _ip_hits[key] = hits
            return "global_day"
        hits.append(now)
        _ip_hits[key] = hits
        _global_hits.append(now)
        if len(_ip_hits) > 5000:                             # pragma: no cover
            for k, v in list(_ip_hits.items()):
                if not v or now - v[-1] > _DAY_S:
                    _ip_hits.pop(k, None)
        return None


def _caller_role(authorization: Optional[str]) -> str:
    """The caller's role - 'anon' | 'user' | 'admin'.  `caller_identity` is the ONE
    definition of who is calling in this backend, role 'anon' its answer for
    "nobody presented any" — which also keeps the local/unconfigured workstation
    (where the developer IS the admin) out of the limiter, exactly as it is out
    of every other gate."""
    try:
        from motor_ai_sim.auth import caller_identity
        return str(caller_identity(authorization).get("role") or "anon")
    except Exception:                                        # pragma: no cover
        return "anon"


def _is_anonymous(authorization: Optional[str]) -> bool:
    """No credentials at all?  (see `_caller_role`)"""
    return _caller_role(authorization) == "anon"


def build_system_prompt(role: str, *, anon: bool, context: Optional[dict] = None) -> str:
    """Everything the model is told for one call.

    * a visitor: the small (Motors + Configure) prompt + the visitor note, and
      nothing about a session - a visitor has none;
    * a signed-in caller: the prompt FOR THEIR ROLE (an admin's custom prompt, if
      any, replaces it for everyone) + the ticket protocol + the hidden session
      context, which the server has already sanitised.
    """
    if anon:
        return _effective_prompt("anon") + VISITOR_NOTE
    sp = _effective_prompt(role)
    if "TICKET_DRAFT" not in sp:
        sp += "\n\n" + TICKET_PROTOCOL
    return sp + support_context.render_for_model(context or {}, role=role)


def _mock_reply(messages: list[dict]) -> str:
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return (
        "⚠️ Demo mode — the AI assistant isn't switched on for this server yet "
        "(no API key configured). Once it's enabled I'll answer questions about the "
        "Configurator, motor parameters, the catalog and how the app works.\n\n"
        + (f'You asked: "{last[:200]}".\n\n' if last else "")
        + "In the meantime, write to vadim@motresres.com."
    )


def _visitor_turn(messages: list[dict]) -> str:
    """The question this call is answering (the last user message)."""
    return next((m["content"] for m in reversed(messages)
                 if m.get("role") == "user"), "")


def _deliver(reply: str, source: str, *, anon: bool, messages: list[dict],
             ip: str = "", ua: str = "", model: str = "", limit: str = "",
             **extra) -> dict:
    """The ONE exit of the chat route for a caller who asked something.

    A ticket-draft marker never reaches the reader: it is parsed and STRIPPED for
    everybody, and handed back as ``ticketDraft`` only to a SIGNED-IN caller (the
    widget shows it as a card the user confirms; nothing is filed here).  A
    visitor has no account to file under, so for them a draft is dropped.

    For a SIGNED-IN caller the rest is a pass-through: their chat is theirs, it
    is not logged here, and no access-request marker is looked for (the note that
    defines one is only appended to a visitor's prompt).

    For a VISITOR it does the three things the owner asked for: strip and file
    the access-request marker, push it, and write the turn to the day's log.
    None of it may fail the reply — everything below either swallows its own
    errors (`support_store`, `notify`) or is wrapped here.
    """
    reply, draft = parse_ticket_draft(reply)
    out: dict = {"reply": reply, "source": source}
    if model:
        out["model"] = model
    if limit:
        out["limit"] = limit
    out.update(extra)
    if not anon:
        if draft is not None:
            out["ticketDraft"] = draft
        elif not reply.strip():
            out["reply"] = "(no reply)"
        return out
    if not reply.strip():
        reply = "(no reply)"
        out["reply"] = reply

    filed = None
    try:
        clean, req = parse_access_request(reply)
        if req is not None:
            from motor_ai_sim import notify, support_store
            transcript = [{"role": m.get("role"), "content": m.get("content")}
                          for m in messages]
            transcript.append({"role": "assistant", "content": clean})
            filed = support_store.file_access_request(
                email=req["email"], name=req["name"], company=req["company"],
                note=req["note"], transcript=transcript, ip=ip, user_agent=ua)
            if filed is not None:
                notify.access_request(filed)
        if not clean.strip():
            # The marker WAS the whole message.  Never hand back an empty bubble.
            clean = ACCESS_CONFIRMATION if req is not None else reply
        out["reply"] = clean
    except Exception as e:                                   # noqa: BLE001
        logger.warning("support: access-request handling failed (%s: %s) — the "
                       "visitor still gets their answer", type(e).__name__, e)

    try:
        from motor_ai_sim import notify, support_store
        support_store.log_visitor_turn(
            ip=ip, user_agent=ua, messages=messages,
            user_message=_visitor_turn(messages), reply=out["reply"],
            source=source, model=model, limit=limit)
        notify.maybe_daily_digest()
    except Exception:                                        # noqa: BLE001
        pass
    return out


@router.post("/chat")
def chat(request: Request, body: dict = Body(default={}),
         authorization: Optional[str] = Header(default=None)):
    role = _caller_role(authorization)
    anon = role == "anon"
    messages = _sanitize(
        body.get("messages"),
        max_turns=ANON_MAX_TURNS if anon else _MAX_TURNS,
        max_chars=ANON_MAX_CHARS if anon else 4000,
    )
    if not messages:
        return {"reply": "Hi! How can I help you with the motor simulator?", "source": "mock"}

    ua = ""
    try:
        ua = (request.headers.get("user-agent") or "") if request else ""
    except Exception:                                        # pragma: no cover
        ua = ""
    ip = client_ip(request) if anon else ""
    deliver = lambda reply, source, **kw: _deliver(          # noqa: E731
        reply, source, anon=anon, messages=messages, ip=ip, ua=ua, **kw)

    # The limiter sits in front of the provider call and nowhere else: it must
    # bound what is SPENT, so a signed-in user never meets it and a refusal
    # never reaches the provider.
    if anon:
        hit = _charge_anonymous(ip)
        if hit is not None:
            logger.warning("support: anonymous chat REFUSED (%s) ip=%s ua=%r",
                           hit, ip or "?", ua)
            # Logged like any other turn — "forty questions and then a wall" is
            # something the team must be able to see in the visitor log.
            return JSONResponse(
                status_code=429,
                content=deliver(_LIMIT_REPLIES[hit], "rate_limited", limit=hit),
                headers={"Retry-After": str(_RETRY_AFTER[hit])},
            )

    eff = _effective()
    provider = eff["provider"]
    if provider == "none":
        return deliver(_mock_reply(messages), "mock")

    ctx = {} if anon else support_context.sanitize_context(body.get("context"))
    sp = build_system_prompt(role, anon=anon, context=ctx)
    try:
        if provider == "gemini":
            g = eff["gemini"]
            if not g["key"]:
                return deliver(_mock_reply(messages), "mock")
            text = _call_provider(
                lambda: _gemini_reply(messages, g["key"], g["model"], sp))
            return deliver(text or "(no reply)", "gemini", model=g["model"])
        # anthropic
        a = eff["anthropic"]
        client = _anthropic_client(a["key"])
        if client is None:
            return deliver(_mock_reply(messages), "mock")
        resp = _call_provider(lambda: client.messages.create(
            model=a["model"], max_tokens=2048, system=sp, messages=messages))
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return deliver(text or "(no reply)", "claude", model=a["model"])
    except Exception as e:
        msg = str(e)
        rate_limited = "429" in msg or "quota" in msg.lower() or "RESOURCE_EXHAUSTED" in msg
        logger.warning("support: provider call failed (%s: %s)%s",
                       type(e).__name__, msg[:160],
                       " [anonymous]" if anon else "")
        # There is no model to draft a ticket right now, so everybody gets the
        # address; a visitor's is also the way in.
        where = ("write to vadim@motresres.com — access is by invitation and we "
                 "answer every request personally"
                 if anon else
                 "write to vadim@motresres.com")
        return deliver(
            (f"The assistant is busy right now (usage limit reached). Please try "
             f"again in a minute — or {where}."
             if rate_limited else
             f"Sorry — I couldn't answer just now. Please try again, or {where}."),
            "error", detail=msg[:200])


# -- user tickets (bug / feature / question / account) -----------------------
# Self-hosted (ticket_store); the owner is ALWAYS the verified caller.

def _ticket_caller(authorization: Optional[str]) -> str:
    from fastapi import HTTPException
    from motor_ai_sim.auth import resolve_user
    user = resolve_user(authorization)
    email = str((user or {}).get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="sign in to use tickets")
    return email


@router.post("/tickets")
def create_ticket(body: dict = Body(default={}),
                  authorization: Optional[str] = Header(default=None)):
    """File a ticket as the signed-in caller (identity never read from the body).

    Called by the widget when the user presses Send on the assistant's draft (the
    user may have edited it).  ``conversation`` and ``context`` ride along: the
    chat that led to the ticket and the session snapshot, both sanitised here
    (credential-shaped strings scrubbed, bounded), plus the facts only the
    server knows - the caller's role and the deployed build."""
    from fastapi import HTTPException
    from motor_ai_sim import ticket_store as T
    email = _ticket_caller(authorization)
    body = body if isinstance(body, dict) else {}
    ctx = support_context.sanitize_context(body.get("context"))
    ctx["server"] = support_context.server_facts(_caller_role(authorization))
    try:
        t = T.create(email, body.get("type"), body.get("title"),
                     body.get("description"),
                     conversation=support_context.sanitize_conversation(body.get("conversation")),
                     context=ctx)
    except T.TicketError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"ok": True, "ticket": t}


@router.get("/tickets")
def my_tickets(authorization: Optional[str] = Header(default=None)):
    """The caller's own tickets, newest first."""
    from motor_ai_sim import ticket_store as T
    rows = T.list_for(_ticket_caller(authorization))
    return {"count": len(rows), "tickets": rows}
