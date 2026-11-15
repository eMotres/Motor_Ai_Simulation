"""Blade geometry of a ``propeller-2`` catalogue entry (stage A1 of the propeller module plan).

The ``blade`` block is the COMPLETE parametric definition of a blade: the blade-element
model reads it, and a later CAD/CFD generator must rebuild the same surface from it
(docs/PROPELLER_MODULE_PLAN_2026-11-15.md, section 6C).  This module only parses and
validates; it computes no aerodynamics and does no I/O.

A number that is not known is absent, never filled: ``geometry_source: inferred`` needs
``estimated: true`` and a ``method``.  Every error names the field.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from motor_ai_sim.propeller import PropellerDataError

GEOMETRY_SOURCES = ("measured", "published", "vendor", "inferred")
INTERPOLATIONS = ("linear", "pchip")
POLAR_SOURCES = ("neuralfoil", "xfoil_table", "measured", "flatplate_model")
ROTATIONS = ("cw_from_front", "ccw_from_front")
MIN_STATIONS = 8
DEFAULT_PITCH_AXIS = 0.25            # fraction of chord from the leading edge
DEFAULT_INTERPOLATION = "pchip"


@dataclass(frozen=True)
class BladeStation:
    r_m: float
    chord_m: float
    twist_deg: float
    airfoil: str
    thickness_rel: Optional[float] = None


@dataclass(frozen=True)
class BladeGeometry:
    geometry_source: str
    source_ref: str
    estimated: bool
    method: Optional[str]
    hub_radius_m: float
    root_cutout_m: float
    tip_radius_m: float
    pitch_axis: float
    interpolation: str
    rotation: Optional[str]
    stations: Tuple[BladeStation, ...]
    sections: Dict[str, str]
    polars_source: Optional[str]
    polars_re_ref: Optional[float]


def _num(d: Dict[str, Any], key: str, where: str, *, lo: float = -math.inf, hi: float = math.inf,
         required: bool = True, lo_open: bool = False) -> Optional[float]:
    v = d.get(key)
    if v is None:
        if required:
            raise PropellerDataError(f"{where}: missing {key}")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise PropellerDataError(f"{where}: {key} must be a finite number, got {v!r}")
    f = float(v)
    if f < lo or f > hi or (lo_open and f == lo):
        raise PropellerDataError(f"{where}: {key} = {f:g} is outside {'(' if lo_open else '['}{lo:g}, {hi:g}]")
    return f


def parse_blade(raw: Any, diameter_m: float, where: str) -> BladeGeometry:
    """Validate the ``blade`` block of a propeller-2 entry for a propeller of ``diameter_m``."""
    w = f"{where} blade"
    if not isinstance(raw, dict):
        raise PropellerDataError(f"{w}: must be a mapping")
    src = raw.get("geometry_source")
    if src not in GEOMETRY_SOURCES:
        raise PropellerDataError(f"{w}: geometry_source must be one of {list(GEOMETRY_SOURCES)}, got {src!r}")
    ref = raw.get("source_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise PropellerDataError(f"{w}: missing source_ref")
    estimated = raw.get("estimated", src == "inferred")
    if not isinstance(estimated, bool):
        raise PropellerDataError(f"{w}: estimated must be true or false")
    method = raw.get("method")
    if src == "inferred" and not estimated:
        raise PropellerDataError(f"{w}: geometry_source 'inferred' must carry estimated: true")
    if estimated and not (isinstance(method, str) and method.strip()):
        raise PropellerDataError(f"{w}: an estimated geometry needs a method")

    tip = diameter_m / 2.0
    hub = _num(raw, "hub_radius_mm", w, lo=0.0, hi=tip * 1000.0 * 0.6) / 1000.0
    cut_mm = _num(raw, "root_cutout_mm", w, lo=0.0, hi=tip * 1000.0, required=False)
    cut = hub if cut_mm is None else cut_mm / 1000.0
    if cut < hub - 1e-12:
        raise PropellerDataError(f"{w}: root_cutout_mm is smaller than hub_radius_mm")
    axis = _num(raw, "pitch_axis", w, lo=0.0, hi=1.0, required=False)
    axis = DEFAULT_PITCH_AXIS if axis is None else axis
    interp = raw.get("interpolation", DEFAULT_INTERPOLATION)
    if interp not in INTERPOLATIONS:
        raise PropellerDataError(f"{w}: interpolation must be one of {list(INTERPOLATIONS)}, got {interp!r}")
    rot = raw.get("rotation")
    if rot is not None and rot not in ROTATIONS:
        raise PropellerDataError(f"{w}: rotation must be one of {list(ROTATIONS)}, got {rot!r}")

    st_raw = raw.get("stations")
    if not isinstance(st_raw, list) or len(st_raw) < MIN_STATIONS:
        raise PropellerDataError(f"{w}: stations must be a list of at least {MIN_STATIONS} entries")
    stations = []
    last = -math.inf
    for i, s in enumerate(st_raw):
        sw = f"{w} stations[{i}]"
        if not isinstance(s, dict):
            raise PropellerDataError(f"{sw}: must be a mapping")
        r = _num(s, "r_mm", sw, lo=0.0, hi=tip * 1000.0 * (1.0 + 1e-9), lo_open=True) / 1000.0
        if r <= last:
            raise PropellerDataError(f"{sw}: r_mm must increase along the blade")
        if r < hub - 1e-12:
            raise PropellerDataError(f"{sw}: r_mm is inside the hub radius")
        last = r
        chord = _num(s, "chord_mm", sw, lo=0.0, hi=tip * 1000.0, lo_open=True) / 1000.0
        twist = _num(s, "twist_deg", sw, lo=-30.0, hi=90.0)
        af = s.get("airfoil")
        if not isinstance(af, str) or not af.strip():
            raise PropellerDataError(f"{sw}: missing airfoil")
        th = _num(s, "thickness_rel", sw, lo=0.0, hi=0.6, required=False, lo_open=True)
        stations.append(BladeStation(r, chord, twist, af.strip(), th))
    if stations[-1].r_m < 0.9 * tip:
        raise PropellerDataError(f"{w}: the last station is at {stations[-1].r_m * 1000:g} mm, "
                                 f"less than 90 % of the tip radius {tip * 1000:g} mm")

    sec = raw.get("sections") or {}
    if not isinstance(sec, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in sec.items()):
        raise PropellerDataError(f"{w}: sections must map an airfoil id to a file reference")
    known = {s.airfoil for s in stations}
    for k in sec:
        if k not in known:
            raise PropellerDataError(f"{w}: sections lists airfoil {k!r} that no station uses")

    pol = raw.get("polars")
    pol_src = pol_re = None
    if pol is not None:
        if not isinstance(pol, dict):
            raise PropellerDataError(f"{w}: polars must be a mapping")
        pol_src = pol.get("source")
        if pol_src not in POLAR_SOURCES:
            raise PropellerDataError(f"{w}: polars.source must be one of {list(POLAR_SOURCES)}, got {pol_src!r}")
        pol_re = _num(pol, "re_ref", f"{w} polars", lo=0.0, required=False, lo_open=True)

    return BladeGeometry(
        geometry_source=src, source_ref=ref.strip(), estimated=estimated,
        method=(method.strip() if isinstance(method, str) else None),
        hub_radius_m=hub, root_cutout_m=cut, tip_radius_m=tip, pitch_axis=axis, interpolation=interp,
        rotation=rot, stations=tuple(stations), sections=dict(sec),
        polars_source=pol_src, polars_re_ref=pol_re)
