"""ONE answer to "does this machine have a retaining sleeve?" (owner 2026-10-26).

    «если нет слива, то не надо о нём ничего писать: Retaining sleeve — просто выкинуть всю информацию»

A rotor whose ``sleeve_thickness`` is 0 has NO sleeve, even when its material assignment still carries a sleeve card
(``sleeve: M40X_UD_60``).  Every surface that would name the sleeve - the report's materials / geometry / loss / inertia
tables, the bill of materials, the datasheet, the Configure and materials panels - asks this module, so they cannot
disagree.  The web mirror is ``web/src/lib/hasSleeve.ts``; keep the two in step.

``has_sleeve`` accepts:

* a number - the thickness in mm;
* a geometry mapping (``sleeve_thickness`` / ``sleeve_thickness_mm``, also nested under ``geometry`` / ``geo``) - the
  thickness decides, and an absent or unreadable thickness means "no sleeve";
* a run summary without a geometry (the electromagnetic ``em`` block of a report): the run itself says so - a sleeve mass
  row, a sleeve inertia, a sleeve eddy loss above zero or a sleeve hoop-stress block.

Pure functions, no imports from the rest of the package.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

_THICKNESS_KEYS = ("sleeve_thickness", "sleeve_thickness_mm")
_NESTED = ("geometry", "geo", "geometry_params")


def _pos(value: Any) -> Optional[float]:
    """``value`` as a finite number, or ``None`` (a bool or text that is not a number is not a thickness)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def sleeve_thickness_mm(source: Any) -> Optional[float]:
    """The stated sleeve thickness in mm, or ``None`` when ``source`` states none."""
    if isinstance(source, Mapping):
        for key in _THICKNESS_KEYS:
            if key in source:
                return _pos(source.get(key))
        for key in _NESTED:
            inner = source.get(key)
            if isinstance(inner, Mapping):
                found = sleeve_thickness_mm(inner)
                if found is not None:
                    return found
        return None
    return _pos(source)


def _run_has_sleeve(run: Mapping) -> bool:
    """A run summary with no geometry: does the run itself carry a sleeve?"""
    for comp in run.get("mass_components") or []:
        if isinstance(comp, Mapping) and str(comp.get("name") or "").lower().startswith("sleeve"):
            if (_pos(comp.get("mass_kg")) or 0.0) > 0.0 or (_pos(comp.get("mass_modelled_kg")) or 0.0) > 0.0:
                return True
    inertia = run.get("rotor_inertia")
    if isinstance(inertia, Mapping) and (_pos(inertia.get("sleeve")) or 0.0) > 0.0:
        return True
    if (_pos(run.get("P_sleeve_W")) or 0.0) > 0.0:
        return True
    return isinstance(run.get("sleeve_hoop"), Mapping) and bool(run.get("sleeve_hoop"))


def has_sleeve(source: Any) -> bool:
    """True only when the machine really has a retaining sleeve (thickness > 0).  See the module docstring."""
    if isinstance(source, Mapping):
        found = sleeve_thickness_mm(source)
        if found is not None:
            return found > 0.0
        return _run_has_sleeve(source)
    found = _pos(source)
    return found is not None and found > 0.0
