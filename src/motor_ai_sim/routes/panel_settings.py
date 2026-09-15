"""Server-side memory of a panel's INPUT fields — Mechanical, Thermal, and any
tab that comes after them.

WHY (user 2026-09-07: "запоминай все последние настройки механических и
термических моделирований … всё одинаково для всех моделирований"): the
Simulation tab keeps its operating point in the server config, so it comes back
on every browser, after every reload and every API restart.  The Mechanical and
Thermal tabs kept theirs in ``localStorage`` — one browser's memory — and their
``/last`` stored only the parameters of the last SOLVE.  A field the user set and
did not solve yet, or set in another browser, was gone.  This router is the
same bargain for every panel: the fields live here, the browser is a fallback.

One JSON file (``config/.panel_settings.json``, a dot-file like the ``.last_*``
stores — app state, not user configuration), keyed by panel and by the calling
user's e-mail (``shared`` when the caller is anonymous, i.e. auth not enforced).
Values are stored as the panel sends them (strings for the text fields), so the
panel's own migration/validation stays the single place that interprets them.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Path

router = APIRouter(prefix="/api/panel_settings", tags=["panel-settings"])

_LOCK = threading.Lock()
_PANELS = ("mechanical", "thermal", "simulation", "mesh", "sweep")
_MAX_KEYS = 200
_MAX_BYTES = 64 * 1024


def _store_path() -> str:
    try:
        # Stage 1: the caller's WORKSPACE, which with none set is
        # ``Path(DEFAULT_CONFIG_PATH).parent`` — the old expression exactly.
        from motor_ai_sim.workspace import root as _ws_root
        base = str(_ws_root())
    except Exception:  # noqa: BLE001
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config")
    return os.path.abspath(os.path.join(base, ".panel_settings.json"))


def _load() -> Dict[str, Any]:
    p = _store_path()
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 — a corrupt store is an empty store, never a 500
        return {}


def _save(d: Dict[str, Any]) -> None:
    p = _store_path()
    tmp = f"{p}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def _who(authorization: Optional[str]) -> str:
    """The caller's e-mail, or ``shared`` when there is no signed-in user."""
    try:
        from motor_ai_sim.auth import resolve_user
        u = resolve_user(authorization)
        e = (u or {}).get("email")
        return str(e).strip().lower() if e else "shared"
    except Exception:  # noqa: BLE001
        return "shared"


def _check_panel(panel: str) -> str:
    if panel not in _PANELS:
        raise HTTPException(status_code=422, detail={
            "error": f"unknown panel '{panel}' — one of {', '.join(_PANELS)}",
            "invalid_parameters": ["panel"]})
    return panel


@router.get("/{panel}")
def get_panel_settings(panel: str = Path(...),
                       authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """The remembered fields of one panel for the caller — ``{}`` when none.

    200 always: a tab that has never been used is the normal first state, not an
    error, and the panel falls back to its own defaults.
    """
    _check_panel(panel)
    who = _who(authorization)
    with _LOCK:
        d = _load()
    entry = ((d.get(panel) or {}).get(who)) or {}
    return {"panel": panel, "user": who, "settings": entry.get("settings") or {},
            "updated_at": entry.get("updated_at")}


@router.put("/{panel}")
def put_panel_settings(panel: str = Path(...),
                       body: Dict[str, Any] = Body(...),
                       authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Replace the caller's remembered fields of one panel.

    Body ``{"settings": {...}}`` — the panel's persisted fields as it keeps them
    (strings for text fields).  Only flat JSON scalars / small objects; bounded so
    a runaway client cannot turn the store into a dump.
    """
    _check_panel(panel)
    s = body.get("settings") if isinstance(body, dict) else None
    if not isinstance(s, dict):
        raise HTTPException(status_code=422, detail={
            "error": "body must be {\"settings\": {...}}", "invalid_parameters": ["settings"]})
    if len(s) > _MAX_KEYS:
        raise HTTPException(status_code=422, detail={
            "error": f"too many fields ({len(s)} > {_MAX_KEYS})", "invalid_parameters": ["settings"]})
    try:
        blob = json.dumps(s, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail={
            "error": f"settings are not JSON-serialisable: {exc}", "invalid_parameters": ["settings"]})
    if len(blob.encode("utf-8")) > _MAX_BYTES:
        raise HTTPException(status_code=422, detail={
            "error": f"settings too large ({len(blob)} bytes > {_MAX_BYTES})",
            "invalid_parameters": ["settings"]})
    who = _who(authorization)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00"
    with _LOCK:
        d = _load()
        d.setdefault(panel, {})[who] = {"settings": s, "updated_at": stamp}
        _save(d)
    return {"panel": panel, "user": who, "settings": s, "updated_at": stamp}
