"""Which stored dies are the same stamped sheet (owner 2026-11-17): "all our 80 mm motors come from one die; draw ONE icon
and under it the different motors for the different projects".

The catalog made one header per STORED die record (the shared die, a later record of the same sheet, a workspace copy).
:func:`sheet_id` is the identity of the stamped sheet a motor is built on: the stator and rotor lamination outline only.
Everything that belongs to the motor is left out (owner rule): the winding and the stack (``routes/family.FREE_GEO_KEYS``),
magnet grade and steel (``materials``, not geometry at all), the magnet fill, its segmentation and the liner; derived values
are compared through their inputs; retired keys that no builder reads are ignored.

Equality is EXACT (rounded to 1e-6 mm, float noise only): two records that differ by a thousandth of a millimetre are
two sheets until the owner says they are one; merging is never silent.  The table of 2026-11-17 that this rule was checked
against: ``~/reports/die_identity_table_2026-11-17.md``.  Pure; nothing is stored.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping, Optional

#: the motor's, not the sheet's (owner rule 2026-11-17): magnet fill, magnet segmentation, the liner
MOTOR_KEYS = frozenset({"magnet_fill_down", "magnet_fill_up", "magnet_fill_radius", "magnet_lamination", "sleeve_thickness"})
#: computed from the inputs (cadquery_geometry / derived_geometry): compared through their inputs
DERIVED_KEYS = frozenset({"stator_outer_radius", "stator_inner_radius", "num_slots", "num_poles", "angle_slot", "angle_pole",
                          "slot_pitch", "pole_pitch", "rotor_outer_radius", "rotor_inner_radius", "slot_width"})
#: no builder reads them (routes/_validation.RETIRED_GEOMETRY_KEYS), or they place the winding, not the sheet
IGNORED_KEYS = frozenset({"magnet_top", "shaft_diameter", "wire_split_series", "wire_bore_margin"})
#: opt-in keys whose absent value is the builder's own default (cadquery_geometry._magnet_flat / _pocket_clearance)
DEFAULTS: Mapping[str, Any] = {"magnet_flat": 0.0, "magnet_pocket_clearance": 0.0}


def _free_keys() -> frozenset:
    from motor_ai_sim.routes.family import FREE_GEO_KEYS          # lazy: the route module imports this one's callers
    return frozenset(FREE_GEO_KEYS)


def _canon(v: Any) -> Any:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        f = float(v)
        return round(f, 6) if f == f else None
    return v


def sheet_of(geometry: Mapping[str, Any]) -> Dict[str, Any]:
    """The sheet keys of an effective geometry (die geometry with the configuration's overrides on top), canonical."""
    skip = _free_keys() | MOTOR_KEYS | DERIVED_KEYS | IGNORED_KEYS
    out = {k: _canon(v) for k, v in (geometry or {}).items() if k not in skip and v is not None}
    for k, d in DEFAULTS.items():
        if out.get(k) in (None, "", 0.0):
            out[k] = d
    return {k: v for k, v in out.items() if v is not None}


def sheet_id(geometry: Mapping[str, Any]) -> Optional[str]:
    """A short stable id of the sheet; None when the geometry has no stator diameter (nothing to compare)."""
    s = sheet_of(geometry)
    if s.get("stator_diameter") is None:
        return None
    blob = json.dumps(s, sort_keys=True, separators=(",", ":"), default=str)
    return "sheet:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
