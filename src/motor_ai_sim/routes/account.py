"""Account route — who am I.

GET /api/me lets the frontend learn the signed-in user's tier + admin flag
(the role is only known server-side, from the verified token + ADMIN_EMAILS).
Used to gate the admin page and the full-UI vs configurator split. Open
endpoint: an anonymous caller just gets tier 'anon'.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Request

from motor_ai_sim.auth import account_info

router = APIRouter(tags=["account"])


@router.get("/api/me")
def me(request: Request, authorization: Optional[str] = Header(default=None)):
    """Who is calling, plus WHY they are anonymous when they are.

    The ip / user-agent go in so a rejection can be traced to a browser in
    logs/auth_events.jsonl — "which profile lost the session at 07:40" is not
    answerable without them."""
    ip = request.client.host if request and request.client else ""
    ua = request.headers.get("user-agent", "") if request else ""
    return account_info(authorization, ip=ip, user_agent=ua)


# ── UI preferences (i18n, docs/I18N.md) ─────────────────────────────────────
# The interface language follows the USER, not the browser: a Chinese supplier
# who picks 中文 on one machine gets it on the next.  One small JSON file beside
# the panel settings, keyed by e-mail (``shared`` when auth is not enforced).
# The web keeps localStorage as the fallback when this answers anything but 200.
import json as _json
import os as _os
import threading as _threading

from fastapi import Body, HTTPException

#: Locales the web ships (web/src/locales/<code>/).  EN is the source.
SUPPORTED_LOCALES = ("en", "zh-CN")
_PREF_LOCK = _threading.Lock()


def _prefs_path() -> str:
    from motor_ai_sim.routes.panel_settings import _store_path
    return _os.path.join(_os.path.dirname(_store_path()), ".user_prefs.json")


def _prefs_load() -> dict:
    try:
        with open(_prefs_path(), encoding="utf-8") as f:
            d = _json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 — missing/corrupt store = no preferences
        return {}


@router.get("/api/me/preferences")
def get_preferences(authorization: Optional[str] = Header(default=None)):
    from motor_ai_sim.routes.panel_settings import _who
    who = _who(authorization)
    with _PREF_LOCK:
        entry = _prefs_load().get(who) or {}
    return {"user": who, "locale": entry.get("locale"),
            "supported_locales": list(SUPPORTED_LOCALES)}


@router.put("/api/me/preferences")
def put_preferences(body: dict = Body(...),
                    authorization: Optional[str] = Header(default=None)):
    from motor_ai_sim.api_errors import ApiError
    from motor_ai_sim.routes.panel_settings import _who
    loc = body.get("locale") if isinstance(body, dict) else None
    if loc not in SUPPORTED_LOCALES:
        raise ApiError(422, "prefs.locale_unsupported",
                       f"locale must be one of {', '.join(SUPPORTED_LOCALES)}",
                       {"locale": str(loc), "supported": ", ".join(SUPPORTED_LOCALES)})
    who = _who(authorization)
    with _PREF_LOCK:
        d = _prefs_load()
        d.setdefault(who, {})["locale"] = loc
        p = _prefs_path()
        tmp = f"{p}.tmp{_os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f, indent=1, ensure_ascii=False)
        _os.replace(tmp, p)
    return {"user": who, "locale": loc}
