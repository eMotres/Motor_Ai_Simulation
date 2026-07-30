"""Loud, structured rejection of client input for the geometry / simulation routes.

Every helper here exists because the endpoint it serves used to ACCEPT garbage
and carry on: a `geo=` override that failed to parse fell back to the shared
global config (so a broken client got someone else's design, with a 200), a
misspelled key in ``PUT /api/geometry`` was written to the in-memory geometry
but not to the YAML (in-memory / on-disk divergence that survives until the next
restart), a new parameter could be created with ``min > max`` or under the name
of a value the solver derives, and the parameter the solver cannot run without
could be deleted with a 200.

Contract for every rejection raised from this module — one shape, so the
frontend renders them all with the list it already has (``GeometryParamErrors``
in web/src/components/parameters/GeometryForm.tsx)::

    422 {"detail": {"error": "<what went wrong, one line>",
                    "invalid_parameters": [
                        {"field": "tooth_widht", "value": 3.1,
                         "kind": "unknown_field", "message": "...",
                         # kind-specific extras: "suggestion", "min", "max"
                        }, ...]}}

``field`` is ALWAYS the offending name — the query parameter (``geo``, ``mat``,
``n_points``) or the geometry key.  Never a generic "bad request".
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import os
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Name sets
# ─────────────────────────────────────────────────────────────────────────────

#: Names the code DERIVES — writing one as an independent parameter creates a
#: value that is silently overwritten on the next reload.  Built from
#: MotorGeometryParams._compute_derived (+ its radius properties), which is what
#: params_from_config and every geometry consumer read back.
#: services.geometry_service._DERIVED_PARAMS is the same list; it is imported
#: below and unioned in, so the two can never drift apart unnoticed.
DERIVED_GEOMETRY_NAMES: frozenset = frozenset({
    # _compute_derived()
    "stator_outer_radius", "stator_inner_radius",
    "rotor_outer_radius", "rotor_inner_radius",
    "num_slots", "num_poles",
    "angle_slot", "angle_pole",
    "slot_pitch", "pole_pitch",
    "slot_width",
    # @property
    "stator_slot_radius", "rotor_core_radius", "shaft_radius",
})

try:  # keep in lock-step with the service that strips them on write
    from motor_ai_sim.services.geometry_service import (
        _DERIVED_PARAMS as _SVC_DERIVED,
    )
    DERIVED_GEOMETRY_NAMES = frozenset(DERIVED_GEOMETRY_NAMES | set(_SVC_DERIVED))
except Exception:  # pragma: no cover - the literal set above is the fallback
    pass


#: Parameters the solver cannot run without.  Seeded from every key
#: ``params_from_config`` (src/motor_ai_sim/simulation/geometry_2d.py:140) reads
#: off the geometry dict, plus the full GEO_30MM design in
#: tests/test_physics_regression.py — i.e. the exact set that has to be present
#: for a mesh + FEM solve of a real machine.  Deleting any of them turns the
#: next solve into a KeyError three layers down.
SOLVER_REQUIRED_PARAMS: frozenset = frozenset({
    # radii chain (params_from_config)
    "stator_diameter", "core_thickness", "slot_height", "air_gap",
    "magnet_height", "rotor_house_height", "shaft_height",
    # topology
    "num_seg", "num_slots_per_segment", "num_poles_per_segment",
    "num_slots", "num_poles",
    # winding / slot
    "wire_width", "wire_height", "wire_spacing_x", "wire_spacing_y",
    "wire_split", "insulation_thickness", "num_wires_per_slot",
    # stator cross-section
    "tooth_width", "tooth2_width", "cut_width", "slot_hs",
    "stator_fillet_r", "stator_fillet_r1",
    # rotor / magnets
    "magnet_fill_down", "magnet_fill_up", "magnet_fill_radius",
    "magnet_up_gap", "magnet_down_height", "magnet_lamination",
    "rotor_hole", "rotor_fill_r",
    # stack
    "motor_length",
})

#: Upper bound for GET /api/geometry/pointcloud.  20 000 is the default and the
#: UI never asks for more than ~2e5; 2e6 points is already ~50 MB of JSON and
#: several seconds of numpy — anything above it is a typo or an attack, not a
#: request.
MAX_POINTCLOUD_POINTS = 2_000_000


# ─────────────────────────────────────────────────────────────────────────────
# Rejection helpers
# ─────────────────────────────────────────────────────────────────────────────

def param_error(field: str, value: Any, kind: str, message: str,
                **extra: Any) -> Dict[str, Any]:
    """One entry of ``invalid_parameters``."""
    out: Dict[str, Any] = {"field": field, "value": value, "kind": kind,
                           "message": message}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def reject(error: str, params: List[Dict[str, Any]]) -> HTTPException:
    """Build the 422 every path in this module raises."""
    return HTTPException(status_code=422, detail={
        "error": error,
        "invalid_parameters": params,
    })


# ─────────────────────────────────────────────────────────────────────────────
# geo= / mat= : a per-request JSON override must parse or fail
# ─────────────────────────────────────────────────────────────────────────────

def parse_json_object_query(raw: Optional[str], *, field: str, what: str,
                            not_applied: str) -> Optional[dict]:
    """Parse a per-request JSON-object query parameter, or 422 with the reason.

    This used to be ``except Exception: pass`` — a client whose override was
    truncated, double-encoded or built from a stale schema silently got the
    SHARED GLOBAL CONFIG back with a 200, i.e. somebody else's design presented
    as its own result.  There is no safe fallback for "I could not read what you
    asked me to compute": say so, name the parameter, quote the parser.

    Returns None only when the parameter is genuinely absent/empty.
    """
    if raw is None or raw == "":
        return None
    try:
        ov = json.loads(raw)
    except Exception as e:
        raise reject(
            f"malformed {field}= override — {not_applied}",
            [param_error(field, _clip(raw), "malformed_json",
                         f"{field}= is not valid JSON: {e}. "
                         f"The request was refused rather than silently "
                         f"computed on the server's own {what}.",
                         parse_error=str(e))])
    if not isinstance(ov, dict):
        raise reject(
            f"malformed {field}= override — {not_applied}",
            [param_error(field, _clip(raw), "malformed_json",
                         f"{field}= must be a JSON object "
                         f"({{\"name\": value, ...}}), got "
                         f"{type(ov).__name__}.")])
    return ov


def parse_geo_override(geo: Optional[str], *, field: str = "geo") -> Optional[dict]:
    """``geo=`` → a geometry dict, or 422 naming what is wrong with it.

    On top of the JSON parse: every value has to be a finite number, because
    that is the only thing the geometry builder can consume.  A string or a null
    that slipped into the override used to travel all the way into CadQuery and
    come back as a 500 (or, worse, as a coerced 0.0).
    """
    ov = parse_json_object_query(
        geo, field=field, what="geometry",
        not_applied="your geometry was not applied")
    if ov is None:
        return None
    if not ov:
        return None                      # `{}` = "no overrides", not an error
    bad: List[Dict[str, Any]] = []
    clean: Dict[str, Any] = {}
    for k, v in ov.items():
        name = str(k)
        if isinstance(v, bool):
            clean[name] = v
            continue
        try:
            fv = float(v)                # numeric strings are accepted
        except (TypeError, ValueError):
            bad.append(param_error(
                name, v, "not_a_number",
                f"{field}= carries a non-numeric value for '{name}' "
                f"({v!r}); every geometry parameter is a number."))
            continue
        if not math.isfinite(fv):
            bad.append(param_error(
                name, v, "not_finite",
                f"{field}= carries a non-finite value for '{name}' ({v!r})."))
            continue
        clean[name] = v
    if bad:
        raise reject(f"malformed {field}= override — your geometry was "
                     f"not applied", bad)
    return clean


def parse_mat_override(mat: Optional[str], *, field: str = "mat") -> Optional[dict]:
    """``mat=`` → {'assignment': {...}, 'materials': {...}} or 422.

    Same disease as ``geo=``: an unreadable material override fell back to the
    shared config's materials and reported the resulting torque as the user's.
    """
    ov = parse_json_object_query(
        mat, field=field, what="materials",
        not_applied="your materials were not applied")
    if ov is None:
        return None
    if not ov:
        return None
    if "assignment" not in ov and "materials" not in ov:
        raise reject(
            f"malformed {field}= override — your materials were not applied",
            [param_error(field, sorted(str(k) for k in ov), "unknown_field",
                         f"{field}= must carry 'assignment' and/or 'materials'; "
                         f"got {sorted(str(k) for k in ov)}.")])
    bad: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {}
    a, m = ov.get("assignment"), ov.get("materials")
    if a is not None and not isinstance(a, dict):
        bad.append(param_error("assignment", _clip(repr(a)), "wrong_type",
                               f"{field}=.assignment must be an object "
                               f"{{region: material}}, got {type(a).__name__}."))
    if m is not None and not isinstance(m, dict):
        bad.append(param_error("materials", _clip(repr(m)), "wrong_type",
                               f"{field}=.materials must be an object "
                               f"{{name: props}}, got {type(m).__name__}."))
    if bad:
        raise reject(f"malformed {field}= override — your materials were "
                     f"not applied", bad)
    if isinstance(a, dict) and a:
        out["assignment"] = {str(k): str(v) for k, v in a.items() if v}
    if isinstance(m, dict) and m:
        for k, v in m.items():
            if not isinstance(v, dict):
                bad.append(param_error(
                    str(k), _clip(repr(v)), "wrong_type",
                    f"{field}=.materials['{k}'] must be an object of material "
                    f"properties, got {type(v).__name__}."))
        if bad:
            raise reject(f"malformed {field}= override — your materials were "
                         f"not applied", bad)
        out["materials"] = {str(k): v for k, v in m.items()}
    return out or None


def _clip(s: Any, n: int = 200) -> str:
    t = s if isinstance(s, str) else repr(s)
    return t if len(t) <= n else t[:n] + "…"


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/geometry : unknown keys and the schema's own min/max
# ─────────────────────────────────────────────────────────────────────────────

def geometry_schema_meta() -> Dict[str, dict]:
    """The ``geometry_schema`` block the frontend clamps from
    (GET /api/geometry/schema serves exactly this).

    Normalised to PLAIN dicts: the config layer hands back OmegaConf
    ``DictConfig`` nodes, which are Mappings but fail ``isinstance(x, dict)`` —
    the check silently skipped every parameter until this was noticed.
    """
    from motor_ai_sim.config import get_config
    raw = (get_config() or {}).get("geometry_schema", {}) or {}
    out: Dict[str, dict] = {}
    for name, meta in dict(raw).items():
        try:
            out[str(name)] = {str(k): v for k, v in dict(meta).items()}
        except (TypeError, ValueError):
            continue
    return out


def known_geometry_keys() -> set:
    """Every geometry key the server recognises: the schema (what the UI edits),
    the config's geometry section (incl. the derived shadows the YAML stores),
    and whatever the live in-memory geometry carries."""
    from motor_ai_sim.config import get_config
    cfg = get_config() or {}
    keys = set(cfg.get("geometry_schema", {}) or {})
    keys |= set(cfg.get("geometry", {}) or {})
    keys |= set(DERIVED_GEOMETRY_NAMES)
    try:
        from motor_ai_sim.services.geometry_service import get_current_geometry
        keys |= set(get_current_geometry().to_dict())
    except Exception:
        pass
    return keys


def check_unknown_geometry_keys(submitted: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reject a key the server does not know, with the nearest real name.

    An unknown key used to be accepted twice over: ``update_current_geometry``
    set it as an attribute on the live geometry object, while the YAML writer
    (``if key in geometry_section``) dropped it — so the process and the file
    disagreed until the next restart quietly reverted the "saved" edit.  A typo
    ('tooth_widht') therefore looked like it worked.
    """
    known = known_geometry_keys()
    bad: List[Dict[str, Any]] = []
    for name, value in submitted.items():
        if name in known:
            continue
        match = difflib.get_close_matches(name, sorted(known), n=1, cutoff=0.6)
        hint = f" — did you mean '{match[0]}'?" if match else \
               " — it is not a geometry parameter."
        bad.append(param_error(
            name, value, "unknown_field",
            f"unknown field '{name}'{hint}",
            suggestion=(match[0] if match else None)))
    return bad


def geo_unbounded() -> bool:
    """The documented escape hatch: GEO_UNBOUNDED=1 lifts the schema's min/max
    caps (exploration across motor scales, 40 mm ↔ 450 mm).  GET
    /api/geometry/schema already serves 0…1e6 while it is on; this keeps the
    write path consistent with what the client was told."""
    return os.environ.get("GEO_UNBOUNDED", "0") == "1"


def check_schema_bounds(submitted: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Enforce, server-side, the very min/max the frontend clamps its sliders to.

    The clamp lives in the browser, so anything that is not the browser — curl,
    a script, a stale tab, the API a client writes against — could write a
    3 000 mm stator or a 0.001 mm air gap into the shared config and get a 200.
    Only the fields THIS request touches are judged: a design already sitting
    outside a bound (an older preset, a bound tightened since) must stay
    editable in every other field.
    """
    if geo_unbounded():
        if submitted:
            log.warning(
                "GEO_UNBOUNDED=1 — schema min/max NOT enforced for this write "
                "(%s). This is a debug/exploration escape hatch: unset the env "
                "var to restore the caps.", ", ".join(sorted(submitted)))
        return []
    schema = geometry_schema_meta()
    bad: List[Dict[str, Any]] = []
    for name, value in submitted.items():
        meta = schema.get(name)
        if not isinstance(meta, dict):
            continue                      # not a schema-governed knob
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue                      # value sanity is validate_parameter_values' job
        try:
            fv = float(value)
        except (TypeError, ValueError):
            continue
        lo, hi = meta.get("min"), meta.get("max")
        label = meta.get("label", name)
        unit = (" " + meta["unit"]) if meta.get("unit") else ""
        try:
            if lo is not None and fv < float(lo):
                bad.append(param_error(
                    name, value, "out_of_range",
                    f"{label} = {fv:g}{unit} is below the allowed minimum "
                    f"{float(lo):g}{unit}.",
                    min=float(lo), max=(float(hi) if hi is not None else None)))
                continue
            if hi is not None and fv > float(hi):
                bad.append(param_error(
                    name, value, "out_of_range",
                    f"{label} = {fv:g}{unit} is above the allowed maximum "
                    f"{float(hi):g}{unit}.",
                    min=(float(lo) if lo is not None else None), max=float(hi)))
        except (TypeError, ValueError):
            continue
    return bad
