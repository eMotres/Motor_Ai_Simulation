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
# THE APPROVED TABLE (owner decision 2026-09-25; conversion handling revised
# 2026-09-25 per the owner's correction below) -- 33 rows.
#
#   canonical    our geometry_schema key (or "sleeve_thickness")
#   old          the owner's legacy Fusion parameter name, or None
#   unit         "mm" | "" (count/ratio) -- matches geometry_schema
#   action       "rename"             old name -> canonical, Name changes,
#                                      Expression/Value carried over VERBATIM
#                                      (no reformatting) -- plain 1:1 only,
#                                      `conversion` is always None for these
#                "rename_or_create"   rename (as above) if `old` is present
#                                      in the design/CSV, otherwise create
#                                      canonical fresh with our own value
#                "create"             `old` is always None; always create
#                                      fresh with our own value
#                "create_and_derive_old"  (stator_diameter only) CREATE the
#                                      canonical parameter fresh, holding OUR
#                                      value ("12 mm", explicit unit, never a
#                                      bare number for a length) -- and,
#                                      because the owner asked for exactly
#                                      ONE existing formula to change, turn
#                                      `old` (stator_up_r) into a DERIVED
#                                      parameter referencing it
#                                      (`stator_up_r = "stator_diameter / 2"`)
#                                      instead of renaming it away. Nothing
#                                      else about `old` changes: its NAME is
#                                      untouched, so every other row that
#                                      already referenced `stator_up_r`
#                                      keeps working unmodified.
#                "create_from_legacy" (magnet_lamination only) CREATE the
#                                      canonical parameter fresh, its value
#                                      computed FROM `old` (mag_step) via
#                                      `lamination_forward` -- but `old`
#                                      itself, and every row that references
#                                      it, is left COMPLETELY UNTOUCHED (no
#                                      clean, always-valid algebraic inverse
#                                      exists for this one -- see
#                                      `lamination_forward`'s docstring and
#                                      docs/FUSION_SCRIPTS_HOWTO.md).
#                "not_mapped"         leave alone; never rename, never create
#                                      (wire_split only -- ours defaults to 1)
#   conversion   None for "rename"/"rename_or_create"/"create"/"not_mapped".
#                ("linear", factor) for stator_diameter: canonical = old *
#                factor when computing the value to CREATE it with, and
#                old's own new expression is `"%s / %g" % (canonical, factor)`
#                (the algebraic inverse) -- used ONLY for that single formula.
#                ("lamination", None) for magnet_lamination: see
#                `lamination_forward`. `old` is never rewritten for this one.
# ─────────────────────────────────────────────────────────────────────────
ENTRIES: List[dict] = [
    {"canonical": "stator_diameter", "old": "stator_up_r", "unit": "mm",
     "action": "create_and_derive_old", "conversion": ("linear", 2.0),
     "note": "old was a RADIUS; canonical is the DIAMETER. old (stator_up_r) "
             "is turned into a derived parameter (\"stator_diameter / 2\"), "
             "not renamed -- every other row that referenced stator_up_r "
             "keeps working unmodified."},
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
     "action": "create_from_legacy", "conversion": ("lamination", None),
     "note": "mag_step is the axial lamination SEGMENT LENGTH; if it equals "
             "the motor length -> no slicing -> our magnet_lamination = 0. "
             "mag_step itself is left completely as-is -- no clean, "
             "always-valid inverse formula exists (see lamination_forward)."},
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
    """Documents why `mag_step` is left untouched rather than turned into a
    derived parameter like `stator_up_r` is: this is the inverse of
    `lamination_forward`, and it is NOT one clean, always-valid formula --
    it is a value of `motor_length` when `magnet_lamination` is (at this
    instant) zero, and `magnet_lamination` itself otherwise. A Fusion
    parameter expression has no conditional/branching operator, so there is
    no single formula for `mag_step` that stays correct across both states
    as either value is edited later. Per the owner's decision, `mag_step` is
    therefore left completely as-is (no rename, no rewritten expression);
    only used here to document the reasoning and for tests."""
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


def plain_rename_old_names() -> List[str]:
    """Old names that are ever plainly renamed (action "rename" or
    "rename_or_create") -- i.e. every old name EXCEPT stator_up_r and
    mag_step, whose entries use "create_and_derive_old" /
    "create_from_legacy" and are never renamed at all. A caller builds the
    actual old->canonical substitution table from only the subset of these
    that are truly present and renamed in a given file/design (not every
    entry here is necessarily renamed in every run -- `rename_or_create`
    entries are only renamed when `old` happens to be present)."""
    return [e["old"] for e in ENTRIES
            if e["old"] and e["action"] in ("rename", "rename_or_create")]


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


def num(value: float) -> str:
    """The number the way Fusion/Parameter I/O writes it: no trailing zeros."""
    return "%g" % float(value)


def expr_str(value: float, unit: str) -> str:
    """A bare-number Expression the way Fusion actually requires it: a
    LENGTH parameter's expression carries its unit explicitly ("12 mm",
    never a bare "12" -- owner correction, 2026-09-25); a UNITLESS
    parameter's expression must be a bare number (Fusion raises "Expression
    is invalid" on "0.13 mm" for one of those). Used only for brand-new
    leaf values this tooling writes (CREATE rows); an existing expression
    that already has its own text is never reformatted through this."""
    return ("%s %s" % (num(value), unit)).strip()


def derive_stator_up_r_expression(factor: float = 2.0) -> str:
    """The ONE existing formula this tooling ever changes (owner, 2026-09-25:
    "формулы не меняй, только одну: stator_up_r = stator_diameter/2"): no
    parentheses, no reformatting -- just this exact expression, since it
    stands alone as stator_up_r's entire new definition rather than being
    substituted into a larger formula."""
    return "stator_diameter / %g" % (factor,)
