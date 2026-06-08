"""Geometry feasibility constraints.

A SINGLE source of truth for "is this cross-section physically buildable?",
shared by the simulation geometry AND the optimizer so neither can ever produce
an invalid mesh (e.g. coils overflowing the slot into the air gap).

Each constraint bounds ONE *target* variable as a function of the others.
Violating values are CLAMPED to the bound — the design stays valid, the user /
optimizer just can't push that knob past the physical limit.

Add new constraints to ``CONSTRAINTS`` as the model grows (the user asked to
introduce these one at a time).  Each entry:

    name    : short id
    target  : the geometry key that gets clamped
    kind    : 'max' (target ≤ bound) or 'min' (target ≥ bound)
    bound   : callable(geo) -> float, the limiting value
    label   : human-readable formula (shown in the UI / messages)
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Tuple


def _f(geo: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(geo.get(key, default))
    except Exception:
        return default


def _wire_height_max(g: Dict[str, Any]) -> float:
    """Max wire_height so that ``num_wires`` rows of wire (each wire_height tall,
    separated radially by wire_spacing_y) fit inside the slot between the two
    insulation layers (top + bottom).  Mirrors the radial stack in
    cadquery_geometry.get_2d_polygons."""
    nw = max(1.0, _f(g, "num_wires_per_slot", 1))
    avail = _f(g, "slot_height") - 2.0 * _f(g, "insulation_thickness")
    return avail / nw - _f(g, "wire_spacing_y")


CONSTRAINTS: List[Dict[str, Any]] = [
    {
        "name": "wire_height_fits_slot",
        "target": "wire_height",
        "kind": "max",
        "bound": _wire_height_max,
        "label": "wire_height ≤ (slot_height − 2·insulation)/num_wires_per_slot − wire_spacing_y",
        "why": "winding must fit the slot — otherwise coils overflow across the "
               "air gap onto the rotor and the FEM solves an invalid cross-section",
    },
    # ── add more constraints here, one at a time ───────────────────────────────
]

# Never clamp a positive dimension to ≤ 0.
_FLOOR = 1e-3


def evaluate(geo: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One record per constraint: computed bound + whether ``geo`` violates it."""
    recs: List[Dict[str, Any]] = []
    for c in CONSTRAINTS:
        tgt = c["target"]
        val = _f(geo, tgt)
        bound = float(c["bound"](geo))
        if c["kind"] == "max":
            violated = val > bound + 1e-9
        else:
            violated = val < bound - 1e-9
        recs.append({
            "name": c["name"], "target": tgt, "kind": c["kind"],
            "value": round(val, 6), "bound": round(bound, 6),
            "violated": bool(violated), "label": c["label"], "why": c["why"],
        })
    return recs


def clamp(geo: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return (clamped_geo, applied) — every violating target clamped to its
    bound.  Iterated a few times because constraints can interact."""
    g = dict(geo)
    applied: Dict[str, Dict[str, Any]] = {}
    for _ in range(4):
        changed = False
        for rec in evaluate(g):
            if not rec["violated"]:
                continue
            tgt = rec["target"]
            new_val = rec["bound"]
            if rec["kind"] == "max":
                new_val = max(_FLOOR, new_val)
            g[tgt] = round(float(new_val), 6)
            applied[tgt] = {**rec, "clamped_to": g[tgt]}
            changed = True
        if not changed:
            break
    return g, list(applied.values())


def bounds(geo: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """target → {kind, bound, label} for every constraint, so the UI can show a
    dynamic min/max next to the field."""
    out: Dict[str, Dict[str, Any]] = {}
    for c in CONSTRAINTS:
        out[c["target"]] = {
            "kind": c["kind"],
            "bound": round(float(c["bound"](geo)), 6),
            "label": c["label"],
        }
    return out
