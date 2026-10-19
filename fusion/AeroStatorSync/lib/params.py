"""User parameters: units, the Import diff, legacy-name detection.  Pure Python (no `adsk`), unit-tested outside Fusion.

Fusion keeps every value in internal units (length cm, angle rad); a user parameter has its own display unit.  The bundle and
the diff always speak the parameter's own unit, so "50 mm" stays 50, never 5.0.
"""
from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Optional

LENGTH_PER_CM = {"mm": 10.0, "cm": 1.0, "m": 0.01, "in": 1 / 2.54, "ft": 1 / 30.48, "um": 1e4}
ANGLE_PER_RAD = {"deg": 180.0 / math.pi, "rad": 1.0}

#: Names Fusion accepts inside an expression that are not parameters.
_FUNCS = {"sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "abs", "ceil", "floor", "round", "log", "ln", "exp", "pow",
          "min", "max", "sign", "PI", "E", "pi", "e"}
_UNIT_WORDS = set(LENGTH_PER_CM) | set(ANGLE_PER_RAD) | {"mm2", "mm3"}
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLAIN = re.compile(r"^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z]*)\s*$")

#: The approved legacy -> canonical names (owner decision 2026-09-25, scripts/fusion_param_common.py ENTRIES).  Only used to
#: say "this document still has the legacy names, run the approved rename first"; the add-in never writes a legacy name.
LEGACY_NAMES = {
    "stator_up_r": "stator_diameter", "slot_h": "slot_height", "core_h": "core_thickness", "N1": "num_seg",
    "Nm": "num_poles_per_segment", "gap": "air_gap", "teeth_w": "tooth_width", "tooth2_w": "tooth2_width",
    "cut_down": "cut_width", "ins_w": "insulation_thickness", "wire_w": "wire_width", "wire_h": "wire_height",
    "wire_dist_x": "wire_spacing_x", "wire_dist_y": "wire_spacing_y", "wire_N": "num_wires_per_slot",
    "magnet_h": "magnet_height", "r_housing_h": "rotor_house_height", "mag_r": "magnet_fill_radius",
    "mag_sh": "magnet_up_gap", "mag_hole": "rotor_hole", "mag_down_h": "magnet_down_height", "stator_r": "stator_fillet_r",
    "stator_r1": "stator_fillet_r1", "rotor_r1": "rotor_fill_r", "stator_w": "motor_length",
}


def internal_to_unit(value: float, unit: str) -> float:
    """Fusion internal value (cm / rad / plain) -> the same quantity in `unit`."""
    u = (unit or "").strip()
    if u in LENGTH_PER_CM:
        return value * LENGTH_PER_CM[u]
    if u in ANGLE_PER_RAD:
        return value * ANGLE_PER_RAD[u]
    return value


def references(expression: str) -> List[str]:
    """Parameter names an expression refers to (functions and unit words excluded)."""
    return sorted({t for t in _IDENT.findall(expression or "") if t not in _FUNCS and t not in _UNIT_WORDS})


def is_formula(expression: str) -> bool:
    """True when the expression refers to another parameter: writing a number there would cut the owner's formula."""
    return bool(references(expression))


def format_expression(value: float, unit: str) -> str:
    """A server value as a Fusion expression with an explicit unit ("50 mm"); counts and ratios stay unitless."""
    txt = ("%.10g" % float(value))
    return (txt + " " + unit) if unit else txt


def same_value(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))


def legacy_names_present(fusion_names: Iterable[str]) -> Dict[str, str]:
    """Legacy names in the document whose canonical name is absent: {legacy: canonical}."""
    names = set(fusion_names)
    return {old: new for old, new in LEGACY_NAMES.items() if old in names and new not in names}


def import_diff(fusion: Dict[str, dict], server: List[dict]) -> List[dict]:
    """Rows of the Import dialog.

    fusion: {name: {"expression", "unit", "value"}} - value in the parameter's own unit.
    server: [{"name", "key", "value", "unit"}] - the die/config's parameters under their Fusion names.

    Row action: "update" (value differs), "create" (no such user parameter yet), "unchanged".  `tick` is the dialog's default:
    an update of a FORMULA row is shown but not ticked (the owner decides), everything else that changes is ticked."""
    rows = []
    for s in server:
        name, unit = s["name"], (s.get("unit") or "")
        new = float(s["value"])
        f = fusion.get(name)
        row = dict(name=name, key=s.get("key", name), unit=unit, server_value=new, new_expression=format_expression(new, unit))
        if f is None:
            row.update(action="create", fusion_expression=None, fusion_value=None, formula=False, tick=True)
        else:
            fu = f.get("unit") or ""
            row.update(fusion_expression=f.get("expression"), fusion_value=f.get("value"), formula=is_formula(f.get("expression", "")))
            if fu != unit:
                row["unit_note"] = "Fusion unit %r, AeroStator unit %r" % (fu, unit)
            if f.get("value") is not None and same_value(f["value"], new, 1e-7):
                row.update(action="unchanged", tick=False)
            else:
                row.update(action="update", tick=not row["formula"])
        rows.append(row)
    order = {"update": 0, "create": 1, "unchanged": 2}
    rows.sort(key=lambda r: (order[r["action"]], r["name"]))
    return rows


def export_diff(bundle_params: Dict[str, dict], server: List[dict]) -> List[dict]:
    """Where a Fusion export and the AeroStator die/config disagree (the version guard's `param_diff`)."""
    out = []
    for s in server:
        f = bundle_params.get(s["name"])
        if f is None or f.get("value") is None:
            continue
        if not same_value(float(f["value"]), float(s["value"]), 1e-7):
            out.append(dict(name=s["name"], key=s.get("key", s["name"]), unit=s.get("unit") or "",
                            fusion=f["value"], aerostator=float(s["value"])))
    return out


def plain_number(expression: str) -> Optional[float]:
    """The number of a plain "12", "12 mm" expression, else None."""
    m = _PLAIN.match(expression or "")
    return float(m.group(1)) if m else None
