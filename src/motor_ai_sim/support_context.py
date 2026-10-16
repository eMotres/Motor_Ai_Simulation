"""What the support assistant is told about a user's session, and what a ticket carries.

The widget sends a small, structured snapshot of where the user is (tab, motor,
configuration, Configure knobs and result tiles, drive, propeller, battery, the
app build, the browser and the last few failed API calls).  It has two uses:

  * the model gets it as HIDDEN context, so an answer can name the actual
    numbers on the user's screen instead of asking for them;
  * a confirmed ticket stores it next to the conversation, so the team reads the
    state the problem happened in.

Both uses go through ``sanitize_context``, and nothing the browser sends is
trusted: only whitelisted top-level keys survive, every string is clipped and
scrubbed of anything that looks like a credential, keys that name a secret are
dropped outright, and the whole thing is capped.  The identity is never read
from it - the role and the build are added by the SERVER (``server_facts``).

Kept free of FastAPI and of the stores so the rules can be tested alone.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

#: Top-level keys a snapshot may carry; anything else is dropped.
ALLOWED_TOP = ("tab", "tabLabel", "lang", "app", "browser", "machine",
               "configure", "failedCalls")

MAX_STR = 240          # one string value
MAX_LIST = 12          # one list
MAX_KEYS = 40          # one object
MAX_DEPTH = 5
MAX_FAILED_CALLS = 10
MAX_JSON_CHARS = 6000  # the whole sanitised snapshot, serialised
MAX_PROMPT_CHARS = 3500  # what the model is shown

#: A key that names a secret is dropped with its value, whatever the value is.
_SECRET_KEY = re.compile(
    r"token|passw|secret|cookie|authoriz|api[_\-]?key|credential|session|bearer|jwt",
    re.IGNORECASE)
_KEY_OK = re.compile(r"^[A-Za-z0-9_.\-]{1,48}$")

# Patterns scrubbed from every string.  Order matters: the specific shapes first.
_SECRET_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]{6,}", re.IGNORECASE), "Bearer [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]*"), "[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "[redacted]"),
    (re.compile(r"\b(?:sk|pk|rk|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{16,}"), "[redacted]"),
    (re.compile(r"(?i)\b(token|password|passwd|pwd|secret|api[_\-]?key|authorization|cookie|session[_\-]?id)"
                r"\s*[=:]\s*[^\s,;&\"']+"), r"\1=[redacted]"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[redacted]"),
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), "[redacted]"),
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}")


def redact_secrets(text: str) -> str:
    """Scrub credential-shaped strings (tokens, keys, 'password=...')."""
    out = text
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact(text: str) -> str:
    """``redact_secrets`` plus e-mail addresses (another user's data must not
    ride along in a failed-call path or message)."""
    return _EMAIL.sub("[email]", redact_secrets(text))


def _clip(s: str, n: int = MAX_STR) -> str:
    s = s.replace("\r", " ").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _scrub(v: Any, depth: int = 0) -> Any:
    """Recursively reduce an untrusted JSON value to a safe, bounded one."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return round(v, 6) if isinstance(v, float) else v
    if isinstance(v, str):
        return _clip(redact(v))
    if depth >= MAX_DEPTH:
        return None
    if isinstance(v, (list, tuple)):
        return [_scrub(x, depth + 1) for x in list(v)[:MAX_LIST]]
    if isinstance(v, dict):
        out: dict = {}
        for k, x in v.items():
            if len(out) >= MAX_KEYS:
                break
            if not isinstance(k, str) or not _KEY_OK.match(k) or _SECRET_KEY.search(k):
                continue
            out[k] = _scrub(x, depth + 1)
        return out
    return None


def _api_path(raw: Any) -> str:
    """A request path with its query/fragment cut (those can carry the geometry
    JSON, a token or another account's id) and its e-mail-shaped segments hidden."""
    p = str(raw or "").strip()
    p = re.sub(r"^[a-z]+://[^/]+", "", p, flags=re.IGNORECASE)
    p = p.split("?", 1)[0].split("#", 1)[0]
    if not p.startswith("/api/"):
        return ""
    return _clip(redact(p), 160)


def _failed_calls(raw: Any) -> list[dict]:
    out: list[dict] = []
    for c in (raw if isinstance(raw, list) else [])[-MAX_FAILED_CALLS:]:
        if not isinstance(c, dict):
            continue
        path = _api_path(c.get("path"))
        if not path:
            continue
        try:
            status = int(c.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        row = {"method": _clip(str(c.get("method") or "GET").upper(), 8),
               "path": path, "status": status,
               "message": _clip(redact(str(c.get("message") or "")), 160)}
        age = c.get("agoS")
        if isinstance(age, (int, float)) and math.isfinite(age) and 0 <= age < 86400 * 7:
            row["agoS"] = int(age)
        out.append(row)
    return out


def sanitize_context(raw: Any) -> dict:
    """The safe, compact form of a client snapshot ({} when there is none).

    Whitelisted top-level keys only; ``failedCalls`` is rebuilt row by row (path
    without query, message scrubbed, at most ten); everything else goes through
    the generic bounded scrubber.  The serialised result is capped at
    ``MAX_JSON_CHARS`` by dropping the largest sections first.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for k in ALLOWED_TOP:
        if k not in raw:
            continue
        out[k] = _failed_calls(raw[k]) if k == "failedCalls" else _scrub(raw[k])
    out = {k: v for k, v in out.items() if v not in (None, "", [], {})}
    # a hard cap: drop the heaviest section until it fits
    while len(json.dumps(out, ensure_ascii=False)) > MAX_JSON_CHARS and out:
        heaviest = max(out, key=lambda k: len(json.dumps(out[k], ensure_ascii=False)))
        out.pop(heaviest)
    return out


def sanitize_conversation(raw: Any, *, max_msgs: int = 30, max_chars: int = 2000) -> list[dict]:
    """The chat to attach to a ticket: user / assistant turns only, the last
    ``max_msgs`` of them, each clipped and scrubbed of credentials (the user's
    own words are otherwise kept as written)."""
    out: list[dict] = []
    for m in (raw if isinstance(raw, list) else [])[-max_msgs:]:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": redact_secrets(content.strip())[:max_chars]})
    return out


def _version() -> str:
    try:
        return (Path(__file__).resolve().parent.parent.parent / "VERSION").read_text(
            encoding="utf-8").strip() or "0.0.0"
    except Exception:                                         # noqa: BLE001
        return "0.0.0"


def server_facts(role: str = "") -> dict:
    """What only the server may say: the deployed build and the caller's role."""
    return {"role": str(role or "")[:16], "version": _version(),
            "gitSha": os.environ.get("APP_GIT_SHA", "unknown")[:40]}


def render_for_model(ctx: dict, *, role: str = "") -> str:
    """The hidden block appended to the system prompt ('' when there is nothing)."""
    ctx = ctx or {}
    if not ctx and not role:
        return ""
    body = dict(ctx)
    if role:
        body = {"role": role, **body}
    txt = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if len(txt) > MAX_PROMPT_CHARS:
        txt = txt[: MAX_PROMPT_CHARS - 1] + "…"
    return (
        "\n\n## Session context (hidden from the user)\n"
        "This is the live state of the user's screen, as JSON. Use it to answer with the "
        "user's actual numbers and settings instead of asking for them, and to see why a "
        "tile is empty or red (see `configure.warnings` and `failedCalls`). Never print the "
        "JSON, never mention that you were given it, and never invent a value that is not "
        "in it.\n" + txt)

