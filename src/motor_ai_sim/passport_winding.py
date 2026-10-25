"""Winding metadata shared by passport entry points."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional
from contextvars import ContextVar

_NO_OVERRIDE = object()
_SIX_PHASE_OVERRIDE = ContextVar("passport_six_phase_override", default=_NO_OVERRIDE)


def set_six_phase_override(value):
    """Set a request-local override for the Simulation route; return token."""
    return _SIX_PHASE_OVERRIDE.set(value)


def reset_six_phase_override(token) -> None:
    _SIX_PHASE_OVERRIDE.reset(token)


def six_phase_override():
    return _SIX_PHASE_OVERRIDE.get()


def has_six_phase_override() -> bool:
    """Whether this request explicitly selected three- or six-phase winding."""
    return _SIX_PHASE_OVERRIDE.get() is not _NO_OVERRIDE


def resolved_six_phase(winding: Mapping[str, Any], geometry: Mapping[str, Any],
                       connection: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolve the configured two-set split; three-phase remains a no-op."""
    from motor_ai_sim.winding import parse_connection
    from motor_ai_sim.winding_sets import resolve_six_phase

    w = dict(winding or {})
    if int(w.get("phases") or 3) != 6:
        return None
    if connection:
        w["n_parallel"] = int(parse_connection(connection)[0])
    return resolve_six_phase(
        w, num_slots=int(geometry["num_slots"]), num_poles=int(geometry["num_poles"]),
        single_layer=int(w.get("layers") or 1) == 1,
        layout_str=w.get("layout") or None,
        star_delta=str(w.get("star_delta") or "star"))


def passport_phase_metadata(spec: Optional[Mapping[str, Any]], current_a: float,
                            voltage_v: Optional[float] = None) -> Dict[str, Any]:
    """Explicit per-inverter values alongside equivalent three-phase totals."""
    if not spec:
        return {}
    return {
        "phase_count": 6,
        "phase_sets": 2,
        "current_basis": "equivalent_three_phase_total",
        "six_phase": {
            **dict(spec),
            "current_per_inverter_A": float(current_a) / 2.0,
            "voltage_per_set_V": voltage_v,
            "stage_capability": {
                "stage_a_3d": "unavailable_without_six_phase_validation",
                "pwm_controller": "unavailable_without_six_phase_model",
                "fault_cases": "not_computed",
            },
        },
    }


def snapshot_six_phase(snapshot: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve new and pre-metadata snapshots from their frozen inputs."""
    spec = snapshot.get("six_phase_spec")
    if spec:
        return dict(spec)
    winding = dict(snapshot.get("winding") or {})
    if int(winding.get("phases") or 3) != 6:
        return None
    return resolved_six_phase(winding, snapshot["geometry"], winding.get("connection"))
