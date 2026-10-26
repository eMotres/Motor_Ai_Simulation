"""WHICH MACHINE is a registered run for?  (owner 2026-10-27: customers of a development project see live which
variant of their motor is being computed on the server right now.)

A job record (``jobs.JobRecord``) is registered at the moment a route is entered, in the submitter's workspace.  That is
the one place where "the machine this run is for" is certain, so the attribution is written there, once, as
``record.target = {"die", "config", "duty"}``:

* the request itself, when it names the machine - a ``die`` / ``config`` (and ``duty``) in the JSON body or in the query
  (``coupled.run``, ``thermal.duty_cycle``, ``cogging`` ...), the same rule ``winding_limit.solved_config`` uses;
* otherwise the catalogue context the workspace has loaded (``.family_context.json`` - what the Simulation tab solves;
  ``duty_results.active_context`` reads the same file but needs a duty, a run does not).

Pure helpers: nothing here raises, and a run that belongs to no catalogued machine gets an empty target (it is then never
shown on any project).  Everything is plain strings so a record stays JSON.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

__all__ = ["clean", "from_call", "active", "resolve", "EMPTY"]

EMPTY: Dict[str, str] = {}


def _s(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def clean(raw: Any) -> Dict[str, str]:
    """``{"die", "config"[, "duty"]}`` out of anything dict-like, else ``{}``.  A target always has BOTH die and config."""
    if not isinstance(raw, Mapping):
        return {}
    die, config = _s(raw.get("die")), _s(raw.get("config"))
    if not die or not config:
        return {}
    out = {"die": die[:120], "config": config[:120]}
    duty = _s(raw.get("duty"))
    if duty:
        out["duty"] = duty[:120]
    return out


def _attrs(obj: Any) -> Dict[str, Any]:
    return {k: getattr(obj, k, None) for k in ("die", "config", "duty")}


def from_call(kw: Mapping[str, Any]) -> Dict[str, str]:
    """The machine a route call NAMES: its own ``die`` / ``config`` parameters, a ``body`` dict, or a request model
    (``req.die`` / ``req.config``).  ``{}`` when the call names none (the run is then for the loaded context)."""
    try:
        found = clean(kw)
        if found:
            return found
        for value in kw.values():
            if isinstance(value, Mapping):
                found = clean(value)
            elif value is not None and not isinstance(value, (str, int, float, bool, list, tuple)):
                found = clean(_attrs(value))
            else:
                continue
            if found:
                return found
    except Exception:                                       # noqa: BLE001 - attribution never fails a run
        pass
    return {}


def _context_file() -> Optional[Path]:
    try:
        from motor_ai_sim.workspace import root
        return Path(str(root())) / ".family_context.json"
    except Exception:                                       # noqa: BLE001
        return None


def active() -> Dict[str, str]:
    """The machine the CALLER's workspace has loaded (die + configuration, and the duty when one is named)."""
    path = _context_file()
    if path is None:
        return {}
    try:
        return clean(json.loads(path.read_text(encoding="utf-8")))
    except Exception:                                       # noqa: BLE001 - no context is the ordinary case
        return {}


def resolve(explicit: Any = None) -> Dict[str, str]:
    """The target of a run being registered: ``explicit`` (what the request named) first, else the loaded context.

    A request that names the machine but not the duty keeps the loaded duty only when the loaded machine IS that machine,
    so a run for one variant is never labelled with another variant's duty."""
    named = clean(explicit) if explicit else {}
    ctx = active()
    if named:
        if (not named.get("duty") and ctx.get("duty")
                and ctx["die"].casefold() == named["die"].casefold()
                and ctx["config"].casefold() == named["config"].casefold()):
            named["duty"] = ctx["duty"]
        return named
    return ctx
