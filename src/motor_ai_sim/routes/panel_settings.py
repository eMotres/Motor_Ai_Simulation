"""Server-side memory of a panel's INPUT fields — Mechanical, Thermal, and any
tab that comes after them.

WHY (user 2026-09-07: "remember all the latest mechanical and thermal
simulation settings ... the same for every simulation"): the
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

THE THERMAL PANEL IS REMEMBERED PER MOTOR TOO (2026-10-15).  The cooling fields
belong to the machine they were typed for: a liquid jacket on the 200 mm L155 is
not the open frame and 20 m/s bore air of the 40 mm propeller motor.  The
``thermal`` entry of a user therefore carries, beside the top-level ``settings``
(= "last used", kept for everything that already reads it)::

    {"settings": {...}, "updated_at": "...",
     "motor": "<die>|<config>",            # which motor ``settings`` belong to
     "source": "saved|config|application", # where they came from
     "by_motor": {"<die>|<config>": {"settings": {...}, "updated_at": "..."}}}

A save from the panel writes ``by_motor`` for the motor it names (``motor`` in the
body) or, when it names none, for the motor the account has loaded.
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
#: Motors one account's Thermal panel remembers; the least recently saved goes.
_MAX_MOTORS = 200


def motor_key(die: Any, config: Any) -> str:
    """The key a motor's Thermal record is stored under: ``"<die>|<config>"``
    (``""`` when either name is missing)."""
    d, c = str(die or "").strip(), str(config or "").strip()
    return "%s|%s" % (d, c) if d and c else ""


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
    out = {"panel": panel, "user": who, "settings": entry.get("settings") or {},
           "updated_at": entry.get("updated_at")}
    if panel == "thermal":
        # Which motor the fields belong to and where they came from, so the
        # panel can say so; absent on a record saved before 2026-10-15.
        for k in ("motor", "source", "application"):
            if entry.get(k):
                out[k] = entry[k]
    return out


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
    key = _thermal_save_key(body, authorization) if panel == "thermal" else ""
    with _LOCK:
        d = _load()
        prev = (d.get(panel) or {}).get(who) or {}
        entry: Dict[str, Any] = {"settings": s, "updated_at": stamp}
        if panel == "thermal":
            entry.update(_thermal_entry_extras(prev, s, key, stamp))
        d.setdefault(panel, {})[who] = entry
        _save(d)
    out = {"panel": panel, "user": who, "settings": s, "updated_at": stamp}
    if key:
        out["motor"] = key
    return out


def _thermal_save_key(body: Dict[str, Any], authorization: Optional[str]) -> str:
    """The motor a Thermal save belongs to: the one the panel names (it knows
    which motor its fields were loaded for), else the account's loaded motor,
    else ``""`` (nothing per-motor is written)."""
    m = body.get("motor") if isinstance(body, dict) else None
    if isinstance(m, dict):
        key = motor_key(m.get("die"), m.get("config"))
        if key:
            return key
    try:
        from motor_ai_sim.routes.account import get_last_motor
        sel = (get_last_motor(authorization=authorization) or {}).get("selection")
        if isinstance(sel, dict):
            return motor_key(sel.get("die"), sel.get("config"))
    except Exception:  # noqa: BLE001 — anonymous / unreadable: no per-motor record
        pass
    return ""


def _thermal_entry_extras(prev: Dict[str, Any], s: Dict[str, Any], key: str,
                          stamp: str) -> Dict[str, Any]:
    """What a Thermal save keeps from the previous entry and adds for ``key``:
    every other motor's record untouched, this motor's record replaced."""
    by_motor = {k: v for k, v in (prev.get("by_motor") or {}).items()
                if isinstance(v, dict)} if isinstance(prev.get("by_motor"), dict) else {}
    extras: Dict[str, Any] = {}
    if key:
        by_motor[key] = {"settings": s, "updated_at": stamp}
        if len(by_motor) > _MAX_MOTORS:
            oldest = sorted(by_motor, key=lambda k: str(by_motor[k].get("updated_at") or ""))
            for k in oldest[:len(by_motor) - _MAX_MOTORS]:
                by_motor.pop(k, None)
        extras.update(motor=key, source="saved")
    elif prev.get("motor"):
        # A save that names no motor changes the last-used fields only; they no
        # longer provably belong to the motor the entry named.
        extras.update(motor=prev.get("motor"), source=prev.get("source") or "saved")
    if by_motor:
        extras["by_motor"] = by_motor
    return extras
