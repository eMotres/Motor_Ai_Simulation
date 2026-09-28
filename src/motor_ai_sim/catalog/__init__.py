"""Unified reference catalogues — stage 1: bearings, lubricants, power devices.

One envelope (:mod:`.envelope`) around each kind's existing body; one adapter
per kind (:mod:`.bearings`, :mod:`.devices`) that reads the unchanged file
through the unchanged loader.  Entry ids are the keys machines already use, so
no configuration reference changes.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from motor_ai_sim.catalog import bearings as _b
from motor_ai_sim.catalog import devices as _d
from motor_ai_sim.catalog.envelope import (KINDS, PROV_TYPES, STATUSES,
                                           field_prov, make_envelope,
                                           validate_envelope)

__all__ = ["KINDS", "PROV_TYPES", "STATUSES", "list_cards", "get_card",
           "field_prov", "make_envelope", "validate_envelope", "kinds_summary"]

_LIST: Dict[str, Callable[[], List[Dict[str, Any]]]] = {
    "bearing": _b.bearing_envelopes,
    "lubricant": _b.lubricant_envelopes,
    "device": _d.device_envelopes,
}
_ONE: Dict[str, Callable[[str], Optional[Dict[str, Any]]]] = {
    "bearing": _b.bearing_envelope,
    "lubricant": _b.lubricant_envelope,
    "device": _d.device_envelope,
}
_ROW: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "bearing": _b.bearing_row,
    "lubricant": _b.lubricant_row,
    "device": _d.device_row,
}


def _check(kind: str) -> None:
    if kind not in _LIST:
        raise KeyError(f"unknown catalogue kind {kind!r}; known: {', '.join(KINDS)}")


def summary(env: Dict[str, Any]) -> Dict[str, Any]:
    """One list row: identity, status, flags and the kind's key columns —
    never the (possibly large) body."""
    if env.get("error"):
        return {"id": env["id"], "kind": env["kind"], "error": env["error"]}
    return {"id": env["id"], "kind": env["kind"],
            "manufacturer": env.get("manufacturer"),
            "part_number": env.get("part_number"),
            "description": env.get("description"),
            "status": env.get("status"), "flags": env.get("flags"),
            "has_validation": bool(env.get("validation")),
            "cols": _ROW[env["kind"]](env)}


def list_cards(kind: str) -> List[Dict[str, Any]]:
    _check(kind)
    return _LIST[kind]()


def get_card(kind: str, card_id: str) -> Optional[Dict[str, Any]]:
    _check(kind)
    return _ONE[kind](card_id)


def kinds_summary() -> List[Dict[str, Any]]:
    return [{"kind": k, "count": len(_LIST[k]())} for k in KINDS]
