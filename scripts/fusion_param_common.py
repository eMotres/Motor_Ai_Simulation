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


# ─────────────────────────────────────────────────────────────────────────
# V1-BUG REPAIR (2026-09-25).  The FIRST version of fusion360_rename_params
# (commit e65e16e) did a plain 1:1 rename of stator_up_r -> stator_diameter
# and mag_step -> magnet_lamination -- WRONG for exactly those two, because
# their whole point is that the VALUE has to change meaning (radius ->
# diameter) or be recomputed (segment length -> our 0-means-solid
# convention), not just carry over verbatim under a new Name. Once a design
# has been through that first version, the CURRENT (v2) script sees
# `stator_diameter`/`magnet_lamination` already present and does nothing,
# permanently baking in an impossible machine (stator_diameter holding a
# RADIUS value, e.g. 6 mm instead of 12 mm; magnet_lamination holding the
# motor length instead of 0).
#
# These two functions detect that specific v1-broken state (never a false
# positive on a healthy, never-renamed, or correctly v2-renamed design) so
# the rename script can REPAIR it first -- renaming the canonical name back
# to the legacy one (Fusion rewrites dependents automatically, exactly as
# it did for the original, wrong, forward rename) -- and only then run the
# normal v2 plan on the now-correctly-legacy-named design.
# ─────────────────────────────────────────────────────────────────────────
_STATOR_DIAMETER_HALVED_RE = re.compile(
    r"\bstator_diameter\s*/\s*2(?:\.0*)?\b|\bstator_diameter\s*\*\s*0\.5\b")
_STATOR_DIAMETER_REF_RE = re.compile(r"\bstator_diameter\b")


def stator_diameter_used_as_radius(dependent_expressions: List[str]) -> bool:
    """True if any of `dependent_expressions` (the Expression text of OTHER
    user parameters that reference the `stator_diameter` token) uses it as
    though it were still a RADIUS -- i.e. references the bare token without
    ever dividing it by 2 (or multiplying by 0.5) first. A healthy v2
    design's only reference to `stator_diameter` is `stator_up_r`'s own
    `"stator_diameter / 2"`, which is excluded by the caller (it is not an
    *other* parameter's expression); anything else that uses the bare token
    (e.g. `stator_diameter - slot_height - core_thickness`, the v1-broken
    signature) is a radius-shaped use of a name that means diameter."""
    for expr in dependent_expressions:
        if _STATOR_DIAMETER_REF_RE.search(expr) and not _STATOR_DIAMETER_HALVED_RE.search(expr):
            return True
    return False


def stator_diameter_value_too_small(stator_diameter_mm: float, *, slot_height_mm: float = 0.0,
                                     core_thickness_mm: float = 0.0, air_gap_mm: float = 0.0,
                                     magnet_height_mm: float = 0.0, rotor_house_height_mm: float = 0.0,
                                     shaft_height_mm: float = 0.0) -> bool:
    """Secondary/fallback evidence (used when no dependent expression is
    available to inspect, e.g. from an exported CSV): the radial stack
    (slot_height + core_thickness + air_gap + magnet_height +
    rotor_house_height + shaft_height) must fit inside HALF of a genuine
    stator_diameter. Here we check something weaker but still damning: that
    the stack does not even fit inside stator_diameter taken at FACE VALUE
    (undivided) -- exactly what happens when a radius value (e.g. 6 mm) is
    relabelled as if it were the diameter, which is smaller than a real
    diameter would be by a factor of ~2. A `stator_diameter` that fails even
    this weaker, undivided check cannot be a genuine diameter for this
    machine -- "its value < the value of a quantity that must be smaller
    than the stator OUTER radius" does not hold."""
    stack = (slot_height_mm + core_thickness_mm + air_gap_mm + magnet_height_mm
             + rotor_house_height_mm + shaft_height_mm)
    return float(stator_diameter_mm) < stack


def plan_v1_repair(present_names, *, dependent_expressions: Optional[List[str]] = None,
                    stator_diameter_value: Optional[float] = None,
                    stack_components: Optional[dict] = None,
                    motor_length_value: Optional[float] = None,
                    magnet_lamination_value: Optional[float] = None,
                    tol: float = 1e-6) -> dict:
    """Decide whether the design in front of us is v1-broken and needs
    repairing before the normal v2 plan runs.

    `present_names`: every user-parameter Name that currently exists in the
    design (or CSV). Nothing here is ever true unless BOTH the canonical
    name is present AND its legacy counterpart is absent -- a design that
    already has both (hand-fixed, or a genuine conflict) is left for the
    existing conflict handling, never touched by repair.

    Returns {"repair_stator_diameter": bool, "stator_evidence": [str, ...],
             "repair_magnet_lamination": bool}.
    """
    present = set(present_names)
    repair_stator = False
    stator_evidence: List[str] = []
    if "stator_diameter" in present and "stator_up_r" not in present:
        deps = dependent_expressions or []
        if stator_diameter_used_as_radius(deps):
            repair_stator = True
            stator_evidence.append(
                "another parameter's formula references stator_diameter without "
                "dividing it by 2 -- it is being used as a radius")
        elif (stator_diameter_value is not None and stack_components is not None
              and stator_diameter_value_too_small(stator_diameter_value, **stack_components)):
            repair_stator = True
            stator_evidence.append(
                "stator_diameter (%.4g mm) is smaller than the radial stack that must "
                "fit inside half of it -- too small to be a genuine diameter"
                % stator_diameter_value)

    repair_magnet = (
        "magnet_lamination" in present and "mag_step" not in present
        and magnet_lamination_value is not None and motor_length_value is not None
        and abs(float(magnet_lamination_value) - float(motor_length_value)) <= tol)

    return {"repair_stator_diameter": repair_stator, "stator_evidence": stator_evidence,
            "repair_magnet_lamination": bool(repair_magnet)}


# ─────────────────────────────────────────────────────────────────────────
# EXPORT SANITY GUARD (2026-09-25).  Before fusion360_export_params.py
# writes a CSV, it computes the same derived radii motor_ai_sim's own
# geometry solver does and checks they are positive and correctly ordered
# -- catching an impossible machine (e.g. the v1-bug signature: exported
# stator_diameter = 6 with the CIAN 40_12 values) before it ever reaches a
# CSV file instead of failing silently downstream in the app.
# ─────────────────────────────────────────────────────────────────────────
_RADIUS_STACK_KEYS = ("stator_diameter", "slot_height", "core_thickness", "air_gap",
                      "magnet_height", "rotor_house_height", "shaft_height")


def compute_derived_radii(values: Dict[str, float]) -> Dict[str, float]:
    """The same derived-radius chain the app itself builds the geometry
    from, in order from the outside in."""
    stator_outer = float(values["stator_diameter"]) / 2.0
    stator_inner = stator_outer - values["slot_height"] - values["core_thickness"]
    rotor_outer = stator_inner - values["air_gap"]
    rotor_inner = rotor_outer - values["magnet_height"] - values["rotor_house_height"]
    shaft_inner = rotor_inner - values["shaft_height"]
    return {"stator_outer": stator_outer, "stator_inner": stator_inner,
            "rotor_outer": rotor_outer, "rotor_inner": rotor_inner,
            "shaft_inner": shaft_inner}


def export_sanity_check(values: Dict[str, float], tol: float = 1e-6) -> dict:
    """`values`: canonical name -> mm value, for whichever of
    `_RADIUS_STACK_KEYS` (plus, optionally, `stator_up_r`) are available.

    Returns {"ok": bool, "skipped": bool, "failures": [str, ...],
             "derived": {...}, "likely_cause": str}. `skipped` is True (and
    `ok` True) only when there is not enough data to run the check at all --
    that is never itself treated as a failure."""
    missing = [k for k in _RADIUS_STACK_KEYS if k not in values]
    if missing:
        return {"ok": True, "skipped": True,
                "failures": [], "derived": {}, "likely_cause": "",
                "reason": "not enough data (missing %s)" % ", ".join(missing)}

    derived = compute_derived_radii(values)
    order = [("stator_outer", derived["stator_outer"]),
             ("stator_inner", derived["stator_inner"]),
             ("rotor_outer", derived["rotor_outer"]),
             ("rotor_inner", derived["rotor_inner"]),
             ("shaft_inner", derived["shaft_inner"])]

    failures: List[str] = []
    for name, val in order:
        if val <= 0:
            failures.append("%s = %.4g mm is not positive" % (name, val))
    for (n1, v1), (n2, v2) in zip(order, order[1:]):
        if v1 <= v2:
            failures.append("%s (%.4g mm) is not greater than %s (%.4g mm)" % (n1, v1, n2, v2))

    if "stator_up_r" in values:
        su = float(values["stator_up_r"])
        sd = float(values["stator_diameter"])
        if abs(sd - 2.0 * su) > max(tol, 1e-6 * abs(su)):
            failures.append("stator_diameter (%.4g mm) != 2 x stator_up_r (%.4g mm -> %.4g mm)"
                             % (sd, su, 2.0 * su))

    likely_cause = ("run fusion360_rename_params -- it repairs a design renamed by the "
                     "first script version" if failures else "")
    return {"ok": not failures, "skipped": False, "failures": failures,
            "derived": derived, "likely_cause": likely_cause, "reason": ""}


def magnet_lamination_export_value(raw_value: float, motor_length_value: Optional[float],
                                    tol: float = 1e-6) -> Tuple[float, bool]:
    """Guard for exporting an EXISTING `magnet_lamination` parameter found
    under its own canonical name (as opposed to one computed fresh from
    legacy `mag_step`, which already goes through `lamination_forward`): if
    its value still equals the motor length -- the exact signature of the
    v1-script bug, or a design where it was created but never actually set
    -- treat it as "no slicing" (0) rather than exporting a bogus non-zero
    lamination step. Returns (value_to_export, was_overridden)."""
    if motor_length_value is not None and abs(float(raw_value) - float(motor_length_value)) <= tol:
        return 0.0, True
    return float(raw_value), False


# ─────────────────────────────────────────────────────────────────────────
# PARAMETER I/O CSV PARSING (2026-09-25, owner audit: "исправь все эти
# косяки в скриптах").  fusion360_import_params.py normally pulls JSON from
# the running app, but when the API is unreachable it falls back to reading
# a CSV file instead -- either our own export's six columns (Name, Unit,
# Expression, Value, Comment, Favorite) or a plain export from the
# Parameter I/O add-in itself. Both are read by HEADER NAME, not position,
# mirroring motor_ai_sim.routes.fusion._parse_expression /
# import_params exactly (duplicated here, not imported, because this module
# is stdlib-only and importable from inside Fusion's sandboxed
# interpreter, and the routes module is not).
# ─────────────────────────────────────────────────────────────────────────
_UNIT_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-z]*)\s*$")


def parse_param_io_expression(expr, unit: str = "") -> Tuple[Optional[float], str]:
    """A Parameter I/O 'Expression' cell -> a value in mm / count: a plain
    number, optionally with a unit suffix ("36", "36 mm", "3.6 cm",
    "0.05 m"). Refuses anything else (a formula referencing other
    parameters, an angle, an unsupported unit) and says why -- a silently
    mis-scaled dimension is worse than a refused row."""
    m = _UNIT_RE.match(str(expr or ""))
    if not m:
        return None, "not a plain number (formula or unsupported form)"
    val = float(m.group(1))
    u = (m.group(2) or unit or "").strip().lower()
    if u in ("", "mm"):
        return val, ""
    if u == "cm":
        return val * 10.0, ""
    if u == "m":
        return val * 1000.0, ""
    return None, "unit %r not supported (mm / cm / m or unitless only)" % (u,)


def parse_param_io_csv_rows(rows: List[dict], fieldnames: Optional[List[str]]):
    """Parameter I/O CSV rows (a list of dict, as `csv.DictReader` yields)
    -> (values, refused).

    `values` is {Name: (value, unit, comment)} for every row whose Name and
    Expression parsed; `refused` is {Name: reason} for a row with a Name
    but an unparseable Expression (a formula, an unsupported unit).
    Columns are matched by HEADER NAME, case-insensitively, not position --
    tolerant of both our own export's six columns and any other
    Parameter I/O-shaped CSV, including one whose comment column is spelled
    differently ("Comment" vs the add-in's own "Comments") since only Name,
    Expression and (optionally) Unit/Comment are ever read."""
    cols = {c.strip().lower(): c for c in (fieldnames or [])}
    if "name" not in cols or "expression" not in cols:
        raise ValueError(
            "expected Parameter I/O columns Name, Unit, Expression, Value, Comment, "
            "Favorite (Name and Expression are the ones actually read) -- got %r"
            % (fieldnames,))
    comment_col = cols.get("comment") or cols.get("comments")
    values: Dict[str, Tuple[float, str, str]] = {}
    refused: Dict[str, str] = {}
    for r in rows:
        name = str(r.get(cols["name"]) or "").strip()
        if not name:
            continue
        unit = str(r.get(cols["unit"]) or "").strip() if "unit" in cols else ""
        val, why = parse_param_io_expression(r.get(cols["expression"]), unit)
        if val is None:
            refused[name] = why
            continue
        comment = str(r.get(comment_col) or "").strip() if comment_col else ""
        values[name] = (val, unit, comment)
    return values, refused
