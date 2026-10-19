"""ONE Configure load, resolved on the server (owner 2026-10-19: «сначала полный пересчет на
сервере, а потом грузить уже одну страницу»).

A Configure load (the default motor, a preset button, a motor switch) used to arrive in pieces:
the configuration context, then the propeller's series, then its continuous (S1) limit, each
answer moving the page one more step.  ``resolve`` picks the ONE target - the configuration and
its duty (the requested one, else ``rated``) - and gathers everything that point depends on in a
single answer: the context (presets, pack, drive variants, cooling), the duty's stored point, the
propeller the configuration opens on with its rpm series (+ housing film h) and its motor S1 limit
for the configuration's own drive and pack.  The page then computes the rated speed once and
renders once.

Nothing here is new physics: the context is ``GET /configure_context``'s, the series is
``propeller.series`` + ``cooling_models.outer_air`` (``GET /api/propellers/{id}/series``) and the
S1 limit is ``propeller_s1.s1_rating`` (``GET /api/propellers/{id}/s1``).  The rules that pick the
target are pure functions below, mirrored by ``web/src/lib/configuratorModes.ts``.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

#: a "rated" duty that is not THE rated point (e.g. ``rated_short_time``) is never a preset
#: button: rated is always continuous, short-time is only peak (owner 2026-10-19)
_RATED_VARIANT = re.compile(r"^rated[\s_-]+", re.IGNORECASE)


def _finite(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def is_preset_mode(name: str) -> bool:
    """A saved duty that may be a Configure preset button: named, and not a rated variant."""
    n = str(name or "").strip()
    return bool(n) and not _RATED_VARIANT.match(n)


def _rank(name: str) -> int:
    n = str(name or "").strip().lower()
    return 0 if n == "rated" else 1 if n.startswith("peak") else 2


def preset_modes(preset: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The modes of one preset that are buttons, in button order: rated, peak, the rest as saved."""
    pts = [d for d in (preset.get("duty_points") or []) if isinstance(d, dict)
           and is_preset_mode(d.get("name") or "")
           and _finite(d.get("current_A")) is not None
           and (_finite(d.get("rpm")) or 0.0) > 0.0]
    return [d for _, d in sorted(enumerate(pts), key=lambda x: (_rank(x[1].get("name")), x[0]))]


def pick_mode(preset: Dict[str, Any], duty: Optional[str]) -> Optional[Dict[str, Any]]:
    """The mode a load lands on: the requested duty when it is a button, else rated, else the first."""
    modes = preset_modes(preset)
    if duty:
        hit = next((m for m in modes if m.get("name") == duty), None)
        if hit:
            return hit
    return modes[0] if modes else None


def pick_propeller(cooling: Optional[Dict[str, Any]], config: str, requested: Optional[str],
                   catalog: Dict[str, Any]) -> Optional[str]:
    """The propeller a load opens on - the rule of the page (``effectivePropeller`` /
    ``defaultPropellerFor``): the requested one if allowed and computable, else the
    configuration's default, else the first allowed with measured torque, else the first usable.
    ``None`` when the machine offers no propeller cooling."""
    c = cooling if isinstance(cooling, dict) else {}
    opts = c.get("cooling_options") or []
    ids = [i for i in (c.get("propellers") or []) if i in catalog]
    if "propeller_air" not in opts or not ids:
        return None
    usable = [i for i in ids if getattr(catalog[i], "selectable", False)]
    if requested and requested in usable:
        return requested
    d = (c.get("defaults") or {}).get(config) if config else c.get("default_propeller")
    if d and d in usable:
        return d
    measured = [i for i in usable if getattr(catalog[i], "power_data", None) == "measured_torque"]
    return (measured or usable or [None])[0]


def preset_pack(preset: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """The pack window the page's S1 request sends for this preset's battery
    (``batteryFromPack`` x cells: the per-cell voltages, else the pack's / cells)."""
    b = preset.get("battery") if isinstance(preset.get("battery"), dict) else None
    if not b:
        return None
    cells = _finite(b.get("cells"))
    ns = int(round(cells)) if cells else 0
    if ns <= 0:
        return None

    def per_cell(cell_key: str, pack_key: str) -> Optional[float]:
        v = _finite(b.get(cell_key))
        if v is not None and v > 0:
            return v
        p = _finite(b.get(pack_key))
        return p / ns if p is not None and p > 0 else None
    lo, hi = per_cell("v_cell_min", "v_min"), per_cell("v_cell_max", "v_max")
    if lo is None or hi is None:
        return None
    return {"min": ns * lo, "max": ns * hi}
