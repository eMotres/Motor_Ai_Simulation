"""Account route — who am I.

GET /api/me lets the frontend learn the signed-in user's role + admin flag
(the role is only known server-side, from the verified token + ADMIN_EMAILS).
Used to gate the admin page and the full-UI vs configurator split. Open
endpoint: an anonymous caller just gets role 'anon'.
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


# The last machine is different from the UI language above: it is private
# selection state and must never use panel_settings._who(), whose deliberate
# anonymous fallback is the shared workspace.
_LAST_MOTOR_FIELDS = {"ref_id", "die", "config", "duty"}
_LAST_MOTOR_ID_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _last_motor_user(authorization: Optional[str]) -> str:
    from motor_ai_sim.auth import resolve_user_detail
    detail = resolve_user_detail(authorization, path="/api/me/last_motor")
    if detail.get("reason") == "store_unavailable":
        raise HTTPException(503, detail="account store unavailable")
    user = detail.get("user")
    email = str((user or {}).get("email") or "").strip().lower()
    if not user or not email:
        raise HTTPException(401, detail="signed-in account required")
    return email


def _validate_last_motor(selection: object, authorization: Optional[str]) -> dict:
    if not isinstance(selection, dict) or set(selection) != _LAST_MOTOR_FIELDS:
        raise HTTPException(422, detail="selection must contain ref_id, die, config, and duty")
    ref_id, die, config, duty = (selection.get(k) for k in ("ref_id", "die", "config", "duty"))
    if ref_id is not None and (not isinstance(ref_id, str)
                               or not _LAST_MOTOR_ID_RE.fullmatch(ref_id)):
        raise HTTPException(422, detail="invalid ref_id")
    if not isinstance(die, str) or not isinstance(config, str):
        raise HTTPException(422, detail="die and config must be strings")
    if duty is not None and not isinstance(duty, str):
        raise HTTPException(422, detail="invalid duty")

    # Keep this route read-only: use the same access guard and YAML readers as
    # the family payload endpoint, but do not build/activate a geometry payload.
    from motor_ai_sim.routes import family
    die = family._check_name(die, "die")
    config = family._check_name(config, "configuration")
    family._require_die_access(die, authorization)
    cfg = family._load_yaml(family._cfg_file(die, config), "configuration")
    if duty is not None:
        duty = family._check_name(duty, "duty")
        if not any(isinstance(item, dict) and item.get("name") == duty
                   for item in (cfg.get("duties") or [])):
            raise HTTPException(404, detail="duty not found")
    # A stored reference ID is never allowed to point Configure at a different
    # visible motor. Re-resolve it through the same grant/private-filtered
    # catalogue response and bind its backing family card to this tuple.
    if ref_id is not None:
        from motor_ai_sim.routes.catalog import get_references
        refs = get_references(authorization).get("motors", [])
        ref = next((item for item in refs if item.get("id") == ref_id), None)
        card = (ref or {}).get("card") or {}
        # A passport can be attached to one configuration while other builds
        # of the same die use it as their cross-section reference (e.g. L12 and
        # L20). The die identity is authoritative; the selected config was
        # independently checked above and the UI matches the restored geometry.
        if card.get("die") != die:
            raise HTTPException(404, detail="catalog reference not found for selection")
    return {"ref_id": ref_id, "die": die, "config": config, "duty": duty}


@router.get("/api/me/last_motor")
def get_last_motor(authorization: Optional[str] = Header(default=None)):
    """Read the authenticated account's last accessible motor selection."""
    who = _last_motor_user(authorization)
    with _PREF_LOCK:
        value = (_prefs_load().get(who) or {}).get("last_motor")
    if value is None:
        return {"user": who, "selection": None, "unavailable": False}
    try:
        selection = _validate_last_motor(value, authorization)
    except HTTPException as exc:
        if exc.status_code in (403, 404, 422):
            # The selection may have been removed or access revoked. Do not
            # disclose its name to a caller who can no longer read it.
            return {"user": who, "selection": None, "unavailable": True}
        raise
    return {"user": who, "selection": selection, "unavailable": False}


@router.put("/api/me/last_motor")
def put_last_motor(body: dict = Body(...),
                   authorization: Optional[str] = Header(default=None)):
    """Remember a successfully loaded tuple for this verified account only."""
    who = _last_motor_user(authorization)
    selection = _validate_last_motor(body.get("selection") if isinstance(body, dict) else None,
                                     authorization)
    with _PREF_LOCK:
        data = _prefs_load()
        data.setdefault(who, {})["last_motor"] = selection
        path = _prefs_path()
        tmp = f"{path}.tmp{_os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=1, ensure_ascii=False)
        _os.replace(tmp, path)
    return {"user": who, "selection": selection}
