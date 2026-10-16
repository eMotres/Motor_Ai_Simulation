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
from typing import Any, Callable, Dict, List, Optional, Tuple


def _f(geo: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(geo.get(key, default))
    except Exception:
        return default


#: The MECHANICAL clearance the transient solver's sliding band needs between
#: the outermost rotating surface and the stator bore, in mm.
#:
#: Not a comfort margin — it is read off the mesher's own arithmetic
#: (``simulation/mesher._simplify_polys``), which is the code that has to build
#: a band in whatever is left of the air gap:
#:
#:   * a band is only built at all when the measured gap exceeds 0.05 mm
#:     (``_gap_est > 0.05``);
#:   * its half-width is ``δ = min(max(0.25·gap, 0.04), 0.4)`` — never thinner
#:     than 0.04 mm;
#:   * the moving band additionally needs ``mid − δ > r_ro + 0.02`` and
#:     ``mid + δ < r_si − 0.02``, i.e. 0.02 mm of clear air between each side of
#:     the band and the iron it faces.
#:
#: Since ``mid`` is the middle of the gap, that last pair is ``gap/2 > δ + 0.02``
#: ⇒ ``gap > 2·(0.04 + 0.02) = 0.12 mm``.  Below it the sliding band silently
#: degrades to the merged (non-moving) topology or fails to build, and the
#: torque comes from a mesh the run never asked for.
#:
#: A SLEEVE eats into exactly this number: it sits on the rotor OD, inside the
#: air gap, so the band has to fit in ``air_gap − sleeve_thickness``.
MIN_MECH_GAP_MM = 0.12


def _wire_height_max(g: Dict[str, Any]) -> float:
    """Max wire_height so that ``num_wires`` rows of wire (each wire_height tall,
    separated radially by wire_spacing_y) fit inside the slot between the two
    insulation layers (top + bottom).  Mirrors the radial stack in
    cadquery_geometry.get_2d_polygons."""
    nw = max(1.0, _f(g, "num_wires_per_slot", 1))
    avail = _f(g, "slot_height") - 2.0 * _f(g, "insulation_thickness")
    return avail / nw - _f(g, "wire_spacing_y")


def _sleeve_thickness_max(g: Dict[str, Any]) -> float:
    """Thickest retaining sleeve that still leaves the sliding band a gap.

    Never negative: on a machine whose air gap is already below the band
    minimum the bound is 0 = "no sleeve is the only legal sleeve", which is a
    value the knob can actually take.  (A NEGATIVE bound would make
    ``sleeve_thickness = 0`` itself a violation and the clamp would then invent
    a ring on every such machine.)
    """
    return max(0.0, _f(g, "air_gap") - MIN_MECH_GAP_MM)


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
    {
        "name": "sleeve_fits_air_gap",
        "target": "sleeve_thickness",
        "kind": "max",
        "bound": _sleeve_thickness_max,
        # 0 is a legal value for this knob ("no sleeve"), so the generic
        # _FLOOR — which exists to stop a positive DIMENSION being clamped to
        # zero — must not apply here: clamping "no sleeve" up to a 1 µm ring
        # would invent a part.
        "floor": 0.0,
        "label": "sleeve_thickness ≤ air_gap − {:.2f} mm (the sliding band's "
                 "minimum mechanical gap)".format(MIN_MECH_GAP_MM),
        "why": "the carbon-fibre ring sits ON the rotor OD, INSIDE the air gap, "
               "so what is left between its outer surface and the stator bore "
               "is the machine's real mechanical gap — and the transient "
               "solver's sliding band has to be meshed in it",
    },
    # ── add more constraints here, one at a time ───────────────────────────────
]

# Never clamp a positive dimension to ≤ 0.
_FLOOR = 1e-3


def _round_inside(x: float, kind: str, nd: int = 6) -> float:
    """Round a PUBLISHED bound into the feasible side at the 1e-6 grid.

    Plain round() can land a max-bound up to 5e-7 ABOVE the true limit, and the
    violation check only tolerates 1e-9 — so the number the UI/pre-fence hands
    out was itself rejected when fed back (measured 2026-09-01: the live
    geometry made wire_height's bound round up and a candidate AT the printed
    bound failed).  A shown bound must be a legal value: max floors, min ceils.
    """
    import math
    q = 10.0 ** nd
    return (math.floor(x * q) / q) if kind == "max" else (math.ceil(x * q) / q)


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
            "value": round(val, 6), "bound": _round_inside(bound, c["kind"]),
            "violated": bool(violated), "label": c["label"], "why": c["why"],
        })
    return recs


def violations(geo: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Only the constraint records this geometry BREAKS (empty ⇒ buildable)."""
    return [r for r in evaluate(geo) if r["violated"]]


def violation_message(geo: Dict[str, Any]) -> Optional[str]:
    """One engineer-readable line per broken bound, or None.

    ONE wording, shared by everything that refuses a design — the sweep's grid
    gate, the optimizer's pre-fence and the eval itself — so a point rejected in
    three different places cannot be explained three different ways.
    """
    bad = violations(geo)
    if not bad:
        return None
    return "; ".join(
        "{} = {:g} does not fit — the bound here is {:.4f} ({}). {}".format(
            r["target"], float(r["value"]), float(r["bound"]), r["label"], r["why"])
        for r in bad)


def clamp(geo: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return (clamped_geo, applied) — every violating target clamped to its
    bound.  Iterated a few times because constraints can interact."""
    g = dict(geo)
    _floor_of = {c["target"]: float(c.get("floor", _FLOOR)) for c in CONSTRAINTS}
    applied: Dict[str, Dict[str, Any]] = {}
    for _ in range(4):
        changed = False
        for rec in evaluate(g):
            if not rec["violated"]:
                continue
            tgt = rec["target"]
            new_val = rec["bound"]
            if rec["kind"] == "max":
                new_val = max(_floor_of.get(tgt, _FLOOR), new_val)
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
            "bound": _round_inside(float(c["bound"](geo)), c["kind"]),
            "label": c["label"],
        }
    return out
