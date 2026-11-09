"""The anonymous public exhibit: which dies a visitor without an account is shown.

A die is exhibit-visible when a characterised (passported) card of ``motor_catalog.json`` describes the same machine
(slots, poles, stator outer radius, magnet height).  ``routes/family.tree`` (anonymous mode) and the regions catalog
manifest (``regions/catalog_manifest``) both ask this module, so the two can never disagree.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def load_passported_geos(catalog_path: Path) -> List[Dict[str, Any]]:
    """The ``passport.geo`` blocks of every catalog card that has one.  RAISES when the file is missing or unreadable
    (the caller decides what "no catalog" means)."""
    cat = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    out: List[Dict[str, Any]] = []
    for m in cat.get("motors", []):
        g = ((m.get("passport") or {}).get("geo")) or {}
        if g:
            out.append(g)
    return out


def die_is_client_ready(g: dict, passported: List[Dict[str, Any]]) -> bool:
    """Does one of the passported cards describe the die geometry ``g`` (a ``die.yaml`` ``geometry`` block)?"""
    # Recompute the derived radii from the die's own primaries - stored deriveds can be stale (the 85 die carried
    # stator_outer_radius 15 beside stator_diameter 85; matching raw fields hid a passported motor from clients).
    try:
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        g = merge_geo_override(dict(g), None)
    except Exception:                                       # noqa: BLE001
        pass
    for _g in passported:
        try:
            if (int(_g.get("numSlots") or 0) == int(g.get("num_slots") or -1)
                    and int(_g.get("numPoles") or 0) == int(g.get("num_poles") or -1)
                    and abs(float(_g.get("statorOR_mm") or 0)
                            - float(g.get("stator_outer_radius") or -1)) <= 0.5
                    and abs(float(_g.get("magnetHeight_mm") or 0)
                            - float(g.get("magnet_height") or -1)) <= 0.25):
                return True
        except (TypeError, ValueError):
            continue
    return False
