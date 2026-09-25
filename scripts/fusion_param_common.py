"""Shared data + logic for the owner-approved Fusion <-> motor_ai_sim geometry
parameter mapping (2026-09-25) -- the 33 geometry-schema inputs only (32
`geometry_schema` keys + `sleeve_thickness`; derived/internal names are never
renamed).  See docs/FUSION_PARAMETER_MAP_2026-09-25.md for the full analysis
and owner sign-off, and config/fusion_param_map.yaml's
`legacy_fusion_names_approved_2026_09_25` section for the human-reviewed copy
of the same table.

STDLIB ONLY.  This module is imported both by the normal-Python CLI
(`fusion_param_rename.py`, which also has PyYAML/motor_ai_sim available) and
by the three in-Fusion scripts under `scripts/fusion360_*_params/`, which run
inside Fusion 360's sandboxed interpreter -- the existing
`fusion360_sync_params.py` already restricts itself to stdlib
(json/traceback/urllib.request) for exactly this reason, and this module
follows the same rule so all four callers can share it without a PyYAML (or
any other third-party) dependency inside Fusion.  It is therefore the single
source of TRUTH for the mapping *logic*; the YAML file is the human-reviewed
record of the same 33 rows and must be kept in sync by hand when this table
changes (small and stable enough that generating one from the other was not
worth the indirection).

Each Fusion script adds this file's directory to `sys.path` before importing
it (see any of the `scripts/fusion360_*_params/*.py` files) since Fusion
loads a script as `__main__` from its own folder, not as part of a package.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────
# THE APPROVED TABLE (owner decision 2026-09-25) -- 33 rows.
#
#   canonical    our geometry_schema key (or "sleeve_thickness")
#   old          the owner's legacy Fusion parameter name, or None
#   unit         "mm" | "" (count/ratio) -- matches geometry_schema
#   action       "rename"          old name -> canonical, values carried over
#                                   (optionally through `conversion`)
#                "rename_or_create" rename if `old` is present in the design/
#                                   CSV, otherwise create canonical fresh
#                "create"           `old` is always None; always create
#                "not_mapped"       leave alone; never rename, never create
#                                   (wire_split only -- ours defaults to 1)
#   conversion   None, or ("linear", factor) meaning canonical = old * factor
#                (and, symmetrically, old = canonical / factor -- used both
#                to convert the renamed row's own value and to fix any OTHER
#                expression that still references the old name), or
#                ("lamination", None) for the magnet_lamination special case
#                (see `lamination_forward` below) -- not a simple ratio, so
#                references to `old` inside other expressions are substituted
#                with the canonical name as-is and flagged for manual review.
# ─────────────────────────────────────────────────────────────────────────
ENTRIES: List[dict] = [
    {"canonical": "stator_diameter", "old": "stator_up_r", "unit": "mm",
     "action": "rename", "conversion": ("linear", 2.0),
     "note": "old was a RADIUS; canonical is the DIAMETER"},
    {"canonical": "slot_height", "old": "slot_h", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "core_thickness", "old": "core_h", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "num_seg", "old": "N1", "unit": "", "action": "rename",
     "conversion": None, "note": ""},
    {"canonical": "num_slots_per_segment", "old": None, "unit": "",
     "action": "create", "conversion": None,
     "note": "old model hardcoded 6 slots/segment (N1*6) in angle_wire; no "
             "user parameter existed for it"},
    {"canonical": "num_poles_per_segment", "old": "Nm", "unit": "",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "air_gap", "old": "gap", "unit": "mm", "action": "rename",
     "conversion": None, "note": ""},
    {"canonical": "tooth_width", "old": "teeth_w", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "tooth2_width", "old": "tooth2_w", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "cut_width", "old": "cut_down", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "insulation_thickness", "old": "ins_w", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "wire_width", "old": "wire_w", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "wire_height", "old": "wire_h", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "wire_spacing_x", "old": "wire_dist_x", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "wire_spacing_y", "old": "wire_dist_y", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "num_wires_per_slot", "old": "wire_N", "unit": "",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "wire_split", "old": None, "unit": "",
     "action": "not_mapped", "conversion": None,
     "note": "ours defaults to 1; do not create unless already present "
             "under this exact name"},
    {"canonical": "slot_hs", "old": "slot_hs", "unit": "", "action": "rename",
     "conversion": None, "note": "identity -- already matches"},
    {"canonical": "magnet_height", "old": "magnet_h", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "rotor_house_height", "old": "r_housing_h", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "shaft_height", "old": None, "unit": "mm",
     "action": "create", "conversion": None, "note": ""},
    {"canonical": "magnet_fill_down", "old": "magnet_fill_down", "unit": "",
     "action": "rename", "conversion": None, "note": "identity"},
    {"canonical": "magnet_fill_up", "old": "magnet_fill_up", "unit": "",
     "action": "rename", "conversion": None, "note": "identity"},
    {"canonical": "magnet_fill_radius", "old": "mag_r", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "magnet_up_gap", "old": "mag_sh", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "rotor_hole", "old": "mag_hole", "unit": "",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "magnet_down_height", "old": "mag_down_h", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "magnet_lamination", "old": "mag_step", "unit": "mm",
     "action": "rename", "conversion": ("lamination", None),
     "note": "mag_step is the axial lamination SEGMENT LENGTH; if it equals "
             "the motor length -> no slicing -> our magnet_lamination = 0"},
    {"canonical": "stator_fillet_r", "old": "stator_r", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "stator_fillet_r1", "old": "stator_r1", "unit": "mm",
     "action": "rename_or_create", "conversion": None,
     "note": "create with our value if stator_r1 is not in the design"},
    {"canonical": "rotor_fill_r", "old": "rotor_r1", "unit": "mm",
     "action": "rename_or_create", "conversion": None,
     "note": "create with our value if rotor_r1 is not in the design"},
    {"canonical": "motor_length", "old": "stator_w", "unit": "mm",
     "action": "rename", "conversion": None, "note": ""},
    {"canonical": "sleeve_thickness", "old": None, "unit": "mm",
     "action": "create", "conversion": None, "note": ""},
]

assert len({e["canonical"] for e in ENTRIES}) == len(ENTRIES) == 33, \
    "the approved table must have exactly 33 distinct canonical entries"

#: canonical -> entry, for direct lookup.
BY_CANONICAL: Dict[str, dict] = {e["canonical"]: e for e in ENTRIES}

#: old Fusion name -> entry, for entries that have one (rename / rename_or_create).
BY_OLD: Dict[str, dict] = {e["old"]: e for e in ENTRIES if e["old"]}


# ─────────────────────────────────────────────────────────────────────────
# Value conversions
# ─────────────────────────────────────────────────────────────────────────
def lamination_forward(mag_step_value: float, motor_length_value: float,
                        tol: float = 1e-6) -> float:
    """Old `mag_step` (segment length) -> our `magnet_lamination`.

    0 in our model means "solid magnet, no axial slicing"; the old model
    apparently expressed the same thing as a lamination step equal to the
    FULL stack length (one slice = the whole magnet).  Anything shorter is
    carried over as-is (the segment length itself, same meaning as ours)."""
    if abs(float(mag_step_value) - float(motor_length_value)) <= tol:
        return 0.0
    return float(mag_step_value)


def lamination_backward(magnet_lamination_value: float, motor_length_value: float) -> float:
    """Our `magnet_lamination` -> the legacy `mag_step` convention (only used
    if a caller ever needs to emit the OLD name/semantics again -- the three
    Fusion scripts never do this, since they only ever write canonical
    names, but it documents the inverse for completeness / testing)."""
    v = float(magnet_lamination_value)
    return float(motor_length_value) if v <= 0 else v


def convert_forward(entry: dict, old_value: float, *,
                     motor_length_value: Optional[float] = None) -> float:
    """`old_value` (in the OLD parameter's basis) -> the value to write into
    the CANONICAL parameter, per `entry["conversion"]`."""
    conv = entry.get("conversion")
    if conv is None:
        return float(old_value)
    kind = conv[0]
    if kind == "linear":
        return float(old_value) * float(conv[1])
    if kind == "lamination":
        if motor_length_value is None:
            raise ValueError("magnet_lamination conversion needs motor_length_value")
        return lamination_forward(old_value, motor_length_value)
    raise ValueError("unknown conversion kind %r" % (kind,))


# ─────────────────────────────────────────────────────────────────────────
# Token-safe expression rewriting -- word-boundary tokenizer, never a
# substring replace (so `wire_h` never matches inside `wire_h2`, `slot_hs`
# never matches inside `slot_hs1`, etc: `[A-Za-z_][A-Za-z0-9_]*` is greedy
# and consumes the WHOLE identifier before the substitution table is even
# consulted).
# ─────────────────────────────────────────────────────────────────────────
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def substitution_map() -> Tuple[Dict[str, str], List[str]]:
    """old-name -> replacement TEXT for use inside some OTHER row's formula
    (not the renamed row's own value, which is converted separately).

    A plain rename substitutes the bare canonical name.  A LINEAR conversion
    substitutes an algebraic expression that reproduces the OLD value from
    the NEW one (`stator_up_r` -> `(stator_diameter / 2)`), so a dependent
    formula that used to read the old radius keeps computing the same number
    after the source parameter's own value is converted to a diameter.  A
    NON-LINEAR conversion (lamination) has no such algebraic inverse, so the
    bare canonical name is substituted and the name is returned in the
    second element for the caller to flag as needing manual review.

    Returns (substitutions, nonlinear_names).
    """
    subs: Dict[str, str] = {}
    nonlinear: List[str] = []
    for e in ENTRIES:
        old = e.get("old")
        if not old:
            continue
        conv = e.get("conversion")
        if conv is None:
            subs[old] = e["canonical"]
        elif conv[0] == "linear":
            subs[old] = "(%s / %g)" % (e["canonical"], conv[1])
        elif conv[0] == "lamination":
            subs[old] = e["canonical"]
            nonlinear.append(old)
        else:  # pragma: no cover - guarded by convert_forward already
            subs[old] = e["canonical"]
    return subs, nonlinear


def rewrite_expression(expr: str, subs: Dict[str, str]) -> Tuple[str, List[str]]:
    """Token-safe rewrite of every identifier in `expr` found in `subs`.

    Returns (new_expr, tokens_replaced) -- the second so a caller can note
    which (if any) nonlinear-conversion names were touched."""
    touched: List[str] = []

    def _sub(m: "re.Match[str]") -> str:
        tok = m.group(0)
        if tok in subs:
            touched.append(tok)
            return subs[tok]
        return tok

    return _TOKEN_RE.sub(_sub, expr), touched


def post_rename_fixups() -> Tuple[Dict[str, str], List[str]]:
    """For the IN-FUSION scripts only (never the CSV converter).

    Fusion's own `UserParameter.name = ...` already rewrites every OTHER
    parameter's expression to use the NEW name automatically (per the
    Fusion API), so by the time a rename has happened every dependent
    expression already reads e.g. `stator_diameter` where it used to read
    `stator_up_r` -- textually correct, but still numerically wrong until
    the source parameter's own value is converted (still the old radius at
    that point). This returns canonical-name -> replacement TEXT to apply
    to every OTHER parameter's (already-renamed) expression BEFORE
    converting the source value, so the compensation lands first:
    `stator_diameter` -> `(stator_diameter / 2)`.

    Returns (substitutions, nonlinear_names) -- same shape as
    `substitution_map()`, but keyed by the CANONICAL (post-rename) name.
    """
    subs: Dict[str, str] = {}
    nonlinear: List[str] = []
    for e in ENTRIES:
        conv = e.get("conversion")
        if conv is None:
            continue
        canonical = e["canonical"]
        if conv[0] == "linear":
            subs[canonical] = "(%s / %g)" % (canonical, conv[1])
        elif conv[0] == "lamination":
            nonlinear.append(canonical)
    return subs, nonlinear


def num(value: float) -> str:
    """The number the way Fusion/Parameter I/O writes it: no trailing zeros."""
    return "%g" % float(value)
