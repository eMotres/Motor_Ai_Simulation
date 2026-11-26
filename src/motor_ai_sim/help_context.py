"""Where the user is, for the help assistant (owner 2026-11-26: the assistant must see the window the user works in).

The widget sends a small ``ui_context`` with every question: the tab and sub-tab ids, and whatever the page registered through
``useHelpContext`` (names the user already sees, the mode or step, status chips, the last visible error).  This module is the
server's side of it:

  * ``sanitize_ui_context``: whitelist per tab (``help_docs/registry.json``), value kinds and caps, secret scrub; a caller below the
    tab's ``min_role`` and a visitor keep the tab ids only;
  * ``help_docs_for``: the short help documents of that window, loaded by id from ``help_docs/`` (EN source + zh-CN mirror);
  * ``render_window``: the text put at the top of the context part of the system prompt.

Owner decision D3 (2026-10-31) holds: the model gets page names, modes and status chips only; a number never passes (counts of rows
are small integers, every digit in a free text is masked), nothing of a geometry, a result or a price.
Free of FastAPI and of the stores, so the rules are tested alone.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent / "help_docs"

MAX_STR = 80            # a name or an enum value
MAX_ERROR = 160
MAX_LIST = 8
MAX_COUNT = 99
MAX_JSON_CHARS = 1000   # the whole sanitised ui_context, serialised
MAX_DOC_CHARS = 5000    # the help documents injected into one request (about 1200 tokens)

_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_ROLE_RANK = {"anon": -1, "": -1, "user": 0, "oem": 1, "engineer": 2, "admin": 3, "staff": 3}

#: Keys every window may carry, with the kind of value each one takes.
GENERIC_KEYS = {"route": "name", "project": "name", "production": "name", "motor": "name", "mode": "enum", "step": "enum",
                "status": "enums", "error": "error"}


def _registry() -> dict:
    try:
        raw = json.loads((_DIR / "registry.json").read_text(encoding="utf-8"))
    except Exception:                                         # noqa: BLE001 - no registry: tab ids only
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict) and _ID.match(k)}


def registry() -> dict:
    """tab id -> {min_role, subs, docs, keys}."""
    return _registry()


def _text(v: Any, n: int) -> str:
    from motor_ai_sim.support_context import _clip, redact
    return _clip(redact(str(v)), n)


def _value(kind: str, v: Any) -> Any:
    """One value reduced to its kind, or None to drop it."""
    if kind == "flag":
        return v if isinstance(v, bool) else None
    if kind == "count":
        if isinstance(v, bool) or not isinstance(v, int):
            return None
        return v if 0 <= v <= MAX_COUNT else None
    if kind in ("name", "enum"):
        if not isinstance(v, str) or not v.strip():
            return None
        return _text(v, MAX_STR)
    if kind == "enums":
        if not isinstance(v, list):
            return None
        out = [_text(x, 40) for x in v[:MAX_LIST] if isinstance(x, str) and x.strip()]
        return out or None
    if kind == "error":
        if not isinstance(v, str) or not v.strip():
            return None
        # a number never rides in a message: every digit run is masked
        return re.sub(r"\d+(?:[.,]\d+)?", "#", _text(v, MAX_ERROR))
    return None


def _rank(role: str) -> int:
    return _ROLE_RANK.get(str(role or "").lower(), 0)


def sanitize_ui_context(raw: Any, role: str = "") -> dict:
    """The safe form of a client ``ui_context`` ({} when there is none).

    ``tab`` and ``sub`` are ids (lowercase letters, digits, underscore).  A tab that is not in the registry, a visitor, and a
    caller whose role is below the tab's ``min_role`` keep ``tab`` (and a ``sub`` that is a plain id) only.  For a registered tab
    the generic keys and the tab's own keys survive, each reduced to its kind; ``sub`` must be one of the tab's sub-views."""
    if not isinstance(raw, dict):
        return {}
    tab = raw.get("tab")
    if not isinstance(tab, str) or not _ID.match(tab):
        return {}
    out: dict = {"tab": tab}
    sub = raw.get("sub")
    reg = _registry().get(tab)
    ids_only = reg is None or _rank(role) < _rank(reg.get("min_role", "admin"))
    if isinstance(sub, str) and _ID.match(sub) and (reg is None or sub in (reg.get("subs") or [])):
        out["sub"] = sub
    if ids_only:
        return out
    kinds = {**GENERIC_KEYS, **{k: v for k, v in (reg.get("keys") or {}).items() if isinstance(v, str)}}
    for k, kind in kinds.items():
        if k in raw:
            v = _value(kind, raw[k])
            if v not in (None, "", [], {}):
                out[k] = v
    while len(json.dumps(out, ensure_ascii=False)) > MAX_JSON_CHARS and len(out) > 2:
        out.pop(max((k for k in out if k not in ("tab", "sub")), key=lambda k: len(json.dumps(out[k], ensure_ascii=False))))
    return out


def _read_doc(doc_id: str, lang: str) -> str:
    reg = _registry()
    known = {d for v in reg.values() for d in (v.get("docs") or [])}
    if doc_id not in known:                                   # never a path from the client
        return ""
    order = ["zh-CN", "en"] if str(lang or "").lower().startswith("zh") else ["en"]
    for lg in order:
        p = _DIR / f"{doc_id}.{lg}.md"
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def help_docs_for(ui: dict, lang: str = "en") -> str:
    """The help documents of the window ``ui`` names, capped at ``MAX_DOC_CHARS`` ('' when the window has none).

    Priority: the sub-view document, then the tab overview, then the saved-gearbox document when a record is open (gears).  Whole
    documents are added while they fit; a first document longer than the cap is cut."""
    tab, sub = ui.get("tab"), ui.get("sub")
    if not isinstance(tab, str):
        return ""
    wanted = ([f"{tab}.{sub}"] if sub else []) + [tab] + ([f"{tab}.saved"] if ui.get("record") else [])
    picked: list[tuple[str, str]] = []
    used = 0
    for d in wanted:
        body = _read_doc(d, lang)
        if not body:
            continue
        if used + len(body) > MAX_DOC_CHARS:
            if picked:
                continue
            body = body[: MAX_DOC_CHARS - 1] + "…"
        picked.append((d, body))
        used += len(body)
    # reading order: overview first, the sub-view after it
    picked.sort(key=lambda x: wanted.index(x[0]) if x[0] != tab else -1)
    return "\n\n".join(b for _, b in picked)


WINDOW_RULES = (
    "Answer about THIS window unless the question clearly names another one. The help documents below (when there are any) are the "
    "ONLY source for UI steps, button names and their order: never invent a button, a tab or a step. When no document is given for "
    "this window, say plainly that you do not have the step-by-step for it, say what you do know about it (its name and the state "
    "above), describe general features only, and offer to file a ticket. Never answer about another feature (for example the "
    "project specification) just because it is the one you know best.")


def render_window(ui: dict, *, lang: str = "en") -> str:
    """The block at the top of the context part: where the user is, what he does, the rules, the help documents."""
    if not isinstance(ui, dict) or not ui.get("tab"):
        return ""
    txt = json.dumps(ui, ensure_ascii=False, separators=(",", ":"))
    docs = help_docs_for(ui, lang)
    out = ("\n\n## The user is here (hidden from the user)\n"
           "The user is here, doing this (JSON: tab and sub-tab ids, names he sees, mode, status chips, the last visible error; "
           "no numbers): " + txt + "\n" + WINDOW_RULES)
    if docs:
        out += "\n\n### Help documents for this window\n" + docs
    else:
        out += "\n\nNo help document exists for this window."
    return out
