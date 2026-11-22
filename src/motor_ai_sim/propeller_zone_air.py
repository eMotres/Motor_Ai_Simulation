"""Zone-wise air speed at the motor from a propeller's radial air profile (``zone-1``, owner 2026-11-16).

The thermal solver takes an optional ``air_zones`` request field (interface by US · software, ``~/jobs/TASK_propeller_zone_air.md``):

    {"model_id": "zone-1", "source": "<text>", "zones": {"housing_outer": m/s, "stator_ends": m/s, "rotor_ends": m/s}, "calibration": None}

Every zone key is optional; a zone left out keeps the solver's scalar value.  This module builds that field from an ``AirField``
(``propeller_inflow``) by area-weighting the axial speed of the bands over the radius range each zone sits in:

* ``rotor_ends``    r in [0, rotor_outer_radius]            (rotor end faces; open frame)
* ``stator_ends``   r in [rotor_outer_radius, stator_outer_radius]   (end windings, tooth backs, gap recess)
* ``housing_outer`` r in [stator_outer_radius, housing_outer_radius] (outer film); only when the housing radius is given

A zone with no modelled band in its range (inside the hub of a blade-element model, or a uniform ``momentum-1`` field that does
not reach it) is LEFT OUT, never set to 0, and named in ``flags``.  Axial speed only (the interface refuses swirl in v1); the
speed is the one at the disc plane that the model reports (``bemt-1``: no wake contraction, no hub flow).  Not calibrated: the
calibration slot stays empty until the thrust stand measures the air behind the hub.  Pure function, no I/O.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

from motor_ai_sim import propeller_inflow as _pi

MODEL_ZONES = "zone-1"
MIN_ZONE_COVERAGE = 0.5          # a zone is exported only when the model covers at least half of its area (else omitted, never 0)
ZONE_KEYS = ("housing_outer", "stator_ends", "rotor_ends")


class ZoneAirError(_pi.InflowModelError):
    pass


def _radius(radii: Mapping[str, Any], key: str, required: bool) -> Optional[float]:
    v = radii.get(key)
    if v is None:
        if required:
            raise ZoneAirError("die radius %r is required (m)" % key)
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise ZoneAirError("die radius %r must be a positive number in m, got %r" % (key, v))
    return float(v)


def band_mean(field: _pi.AirField, r_lo: float, r_hi: float) -> Optional[float]:
    """Area-weighted mean axial speed of the modelled bands over [r_lo, r_hi]; ``None`` when no modelled band overlaps it."""
    num = den = 0.0
    for b in field.bands:
        if b.axial_ms is None or b.r_min_m is None or b.r_max_m is None:
            continue
        lo, hi = max(b.r_min_m, r_lo), min(b.r_max_m, r_hi)
        if hi <= lo:
            continue
        a = math.pi * (hi * hi - lo * lo)
        num += a * float(b.axial_ms)
        den += a
    return num / den if den > 0 else None


def zone_air_detail(field: _pi.AirField, die_radii: Mapping[str, Any]) -> Dict[str, Any]:
    """``{"air_zones": <request field>, "flags": [...], "coverage": {zone: fraction of the zone area with a modelled band}}``.

    ``die_radii`` in m: ``rotor_outer_radius_m`` and ``stator_outer_radius_m`` (required), ``housing_outer_radius_m`` (optional)."""
    r_rot = _radius(die_radii, "rotor_outer_radius_m", True)
    r_sta = _radius(die_radii, "stator_outer_radius_m", True)
    r_hou = _radius(die_radii, "housing_outer_radius_m", False)
    if not r_rot < r_sta:
        raise ZoneAirError("rotor_outer_radius_m must be below stator_outer_radius_m (inner rotor)")
    if r_hou is not None and not r_hou > r_sta:
        raise ZoneAirError("housing_outer_radius_m must be above stator_outer_radius_m")
    ranges: Dict[str, Tuple[float, float]] = {"rotor_ends": (0.0, r_rot), "stator_ends": (r_rot, r_sta)}
    if r_hou is not None:
        ranges["housing_outer"] = (r_sta, r_hou)
    flags = set()
    if r_hou is None:
        flags.add("housing_outer_not_given")
    zones: Dict[str, float] = {}
    coverage: Dict[str, float] = {}
    for z in ZONE_KEYS:
        if z not in ranges:
            continue
        lo, hi = ranges[z]
        v = band_mean(field, lo, hi)
        cov = 0.0
        for b in field.bands:
            if b.axial_ms is None or b.r_min_m is None or b.r_max_m is None:
                continue
            a, c = max(b.r_min_m, lo), min(b.r_max_m, hi)
            if c > a:
                cov += c * c - a * a
        coverage[z] = round(cov / (hi * hi - lo * lo), 6)
        if v is None:
            flags.add("%s_not_modelled" % z)
            continue
        if coverage[z] < MIN_ZONE_COVERAGE:              # review 2026-11-21 H9: a small modelled sliver is not the zone's air speed
            flags.add("%s_coverage_too_small" % z)
            continue
        if coverage[z] < 1.0 - 1e-9:
            flags.add("%s_partly_modelled" % z)
        zones[z] = max(0.0, float(v))
    source = "%s | %s | %.0f rpm | axial at the disc, area-weighted per zone" % (field.model_id, field.propeller_id, field.rpm)
    return {"air_zones": {"model_id": MODEL_ZONES, "source": source, "zones": zones, "calibration": None},
            "flags": sorted(flags | set(field.flags)), "coverage": coverage}


def zone_air(field: _pi.AirField, die_radii: Mapping[str, Any]) -> Dict[str, Any]:
    """The ``air_zones`` request field of the thermal solver (exactly ``model_id``, ``source``, ``zones``, ``calibration``)."""
    return zone_air_detail(field, die_radii)["air_zones"]
