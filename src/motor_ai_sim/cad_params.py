# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Geometry tab "Full": the construction parameters of the CAD motor model that the electromagnetic simulation does not read.

Owner 2026-10-15: in Full the parameter table is EDITABLE for the non-EM construction - walls, shafts, holders, rings, bearings,
the phase board, screws, leads, CAD-only clearances.  This module is the catalogue of those parameters (name, part-tree group,
unit, limits, short EN / zh-CN tooltip), the validation of an edit and the pure helpers around it.  What it does NOT do:

* it never touches the electromagnetic inputs: the catalogue contains no name that the simulation maps
  (``config/fusion_param_map.yaml``), so a Full edit cannot change the geometry, its ``_geoSig`` or any run input
  (the EM parameters are edited in Active only);
* it never builds anything: an edit is saved as ``cad_overrides`` of the die configuration (``<config>.yaml``), the server CAD
  build (``motor_ai_sim.cad.build``) reads them (``cad/store.py``) and rebuilds the model; the old model stays in use meanwhile.

Validation reuses the template's own pre-check (``cad/motor_templates/segmented_flatwire/health.precheck``, pure Python): the
edit is applied to the parameter set of the last built model, every expression is evaluated, and the violations come back in
words - the same sentences the build would refuse with.  Without the template file in the deployment, saving is refused
loudly (an unchecked edit would only surface later as a failed rebuild).
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from motor_ai_sim import part_tree as PT
from motor_ai_sim.cad_param_labels import labels as _labels

#: the block of a die configuration that carries the CAD-only edits
OVERRIDES_KEY = "cad_overrides"
#: special override: the bearing card (bearings library) the CAD bearing is drawn after; not a number
BEARING_CARD_KEY = "bearing_card"

CAD_ONLY_LABEL = {"en": "CAD only, not simulated", "zh": "仅CAD，不参与仿真"}

# name | group | unit | min | max | English tooltip | zh-CN tooltip      (kind: a name ending in "_count" is an integer)
_TABLE = """
shaft_bore_tip_angle|shaft|deg|10|89|Included half-angle of the cone at the end of the shaft bore.|轴孔端部锥角(半角)。
shaft_chamfer|shaft|mm|0|2|Chamfer on the shaft journal ends and steps.|轴颈端部与台阶的倒角。
shaft_flange_step|shaft|mm|0.2|20|Flange radius above the rotor seat (front rotor stop).|转子座以上的法兰半径增量(前挡)。
shaft_flat_len|shaft|mm|0|30|Length of the drive flats from the journal end.|轴颈端部驱动平面长度。
shaft_flat_w|shaft|mm|1|60|Width across the two drive flats on the journal.|轴颈上两个驱动平面之间的宽度。
shaft_front_overhang|shaft|mm|0|60|Shaft or hub length beyond the front holder face.|超出前支架端面的轴(或轮毂)长度。
shaft_shoulder_len|shaft|mm|0.1|20|Axial length of the shoulder against the front bearing inner ring.|抵住前轴承内圈的轴肩长度。
shaft_shoulder_r|shaft|mm|1|60|Shoulder radius against the bearing inner ring.|抵住轴承内圈的轴肩半径。
shaft_thread_len|shaft|mm|0|60|Length of the threaded section of the shaft.|轴螺纹段长度。
shaft_thread_r|shaft|mm|0.5|30|Radius of the threaded section of the shaft.|轴螺纹段半径。
shaft_tip_len|shaft|mm|0|60|Length of the shaft tip beyond the thread.|螺纹之外的轴端长度。
shaft_tip_r|shaft|mm|0.5|30|Radius of the shaft tip.|轴端半径。
carrier_bore_r|shaft|mm|1|60|Bore radius of the front rotor-carrier hub.|前转子支架轮毂孔半径。
carrier_flange_t|shaft|mm|0.1|10|Carrier flange thickness beyond the rotor face.|超出转子端面的支架法兰厚度。
carrier_floor_wall|shaft|mm|0.2|10|Hub wall: journal radius minus the hub floor radius.|轮毂壁厚(轴颈半径减轮毂底半径)。
carrier_hole_count|shaft|count|0|24|Number of tapped holes in the front hub end face.|前轮毂端面螺纹孔数量。
carrier_hole_d|shaft|mm|0.5|10|Diameter of the hub holes as modelled.|轮毂孔直径(建模值)。
carrier_hole_pcd_r|shaft|mm|1|60|Pitch-circle radius of the hub holes.|轮毂孔分布圆半径。
carrier_rim_h|shaft|mm|0.5|30|Carrier rim height below the web.|腹板下方的支架轮缘高度。
carrier_rim_wall|shaft|mm|0.2|10|Carrier rim wall thickness.|支架轮缘壁厚。
carrier_tab_cap_r|shaft|mm|0.2|20|Rim key tab: cap radius.|轮缘键耳:顶部圆角半径。
carrier_tab_fillet_r|shaft|mm|0.2|20|Rim key tab: concave flank radius.|轮缘键耳:凹侧圆角半径。
carrier_web_t|shaft|mm|0.2|10|Carrier web thickness.|支架腹板厚度。
rotor_key_count|rotor|count|0|24|Number of keys in the rotor bore.|转子内孔键数量。
rotor_key_w|rotor|mm|0.2|20|Rotor key width.|转子键宽度。
rotor_key_h|rotor|mm|0.2|20|Rotor key depth below the bore radius.|转子键在内孔半径以下的深度。
rotor_key_offset|rotor|mm|0|2|Clearance of the key sides and tip (moved inwards).|键侧面与顶部的间隙(向内偏移)。
magnet_lower_fill_r|magnets|mm|0.05|3|Fillet radius at the bottom and knee of the magnet.|磁钢底部与拐角圆角半径。
offset_rotor|magnets|mm|0|0.5|Magnet pocket offset so a magnet can be inserted at manufacturing tolerance.|磁钢槽偏置量,保证按制造公差可装入磁钢。
coil_lead_ext|winding|mm|0|40|Coil leg extension beyond the straight end (lead stubs).|线圈直线段之外的引线延伸长度。
coil_lead_len|winding|mm|0|40|Length of the separate lead stubs.|独立引线长度。
holder_flange_t|housing|mm|0.2|10|Holder flange thickness.|支架法兰厚度。
holder_lip_t|housing|mm|0.1|10|Thickness of the lip that retains the bearing.|保持轴承的支架唇边厚度。
holder_lip_r|housing|mm|0.5|60|Inner radius of the bearing retaining lip.|轴承保持唇边内半径。
holder_hub_wall|housing|mm|0.2|10|Front holder hub wall thickness.|前支架轮毂壁厚。
holder_bolt_r|housing|mm|2|100|Bolt-circle radius of the holder screws and bosses.|支架螺钉与凸台分布圆半径。
holder_window_r|housing|mm|0.1|20|Window fillet radius at the spoke roots.|辐条根部窗口圆角半径。
holder_seat_depth|housing|mm|0.2|10|Depth of the boss seat into the stator pocket.|凸台嵌入定子槽的深度。
holder_chamfer|housing|mm|0.05|3|Chamfer of the seat bottom edges.|凸台底边倒角。
holder_tap_d|housing|mm|0.5|10|Back boss screw hole diameter (tap size as modelled).|后凸台螺纹孔直径(建模值)。
holder_rib_h|housing|mm|0|10|Spoke rib height above the flange face.|辐条筋高度(高出法兰面)。
holder_rib_half_w|housing|mm|0.1|10|Spoke rib half width.|辐条筋半宽。
back_holder_len|housing|mm|0.5|40|Length of the back holder cylinder.|后支架圆筒长度。
back_holder_ref_r|housing|mm|1|100|Construction radius of the back holder (outer radius = radius + wall).|后支架构造半径(外半径=半径+壁厚)。
back_holder_fillet_r|housing|mm|0.1|10|Fillet of the spoke ends at the back holder outer radius.|后支架外半径处辐条端部圆角。
back_screw_r|housing|mm|1|100|Radius of the M3 holes in the back holder face.|后支架端面M3孔的分布半径。
back_screw_tap_d|housing|mm|0.5|10|Diameter of the M3 holes as modelled.|M3孔直径(建模值)。
coil_holder_clearance|housing|mm|0.05|5|Radial clearance between the coil corner and the holder flange.|线圈角与支架法兰之间的径向间隙。
bolt_head_d|housing|mm|1|20|Screw head circle diameter of the holder bosses.|支架凸台的螺钉头圆直径。
ring_h|housing|mm|0.2|10|Axial offset of the holders from the stator end face (the space the removed glass-fibre ring used to fill); the holders and bearings move with it.|支架距定子端面的轴向偏移(原玻璃纤维环所占空间);支架与轴承随之移动。
ring_holder_gap|housing|mm|0|2|Extra axial gap between that offset and the holder flange face; adds to ring_h.|在上述偏移与支架法兰面之间的附加轴向间隙,与ring_h相加。
prop_fix_d|housing|mm|2|100|Outer diameter of the propeller fixing plate.|螺旋桨固定板外径。
prop_fix_relief_r|housing|mm|0.1|5|Corner relief radius of the double-D hole.|双D孔的拐角卸荷半径。
prop_fix_hub_t|housing|mm|0.2|10|Hub thickness of the propeller plate on the flats.|螺旋桨板在平面上的轮毂厚度。
prop_fix_plate_t|housing|mm|0.2|10|Plate thickness beyond the shaft end.|超出轴端的板厚度。
prop_fix_clearance|housing|mm|0|0.5|Clearance of the hole faces of the propeller plate.|螺旋桨板孔面间隙。
bearing_ball_count|bearings|count|3|60|Number of balls in each bearing.|每个轴承的钢球数量。
bearing_ball_d|bearings|mm|0.3|20|Ball diameter.|钢球直径。
bearing_edge_r|bearings|mm|0|2|Edge radius of the ring corners.|轴承圈拐角圆角半径。
bearing_inner_land_d|bearings|mm|1|100|Inner ring land diameter.|内圈挡边直径。
bearing_outer_land_d|bearings|mm|1|120|Outer ring land diameter.|外圈挡边直径。
bearing_seal_cone_dz|bearings|mm|0|2|2RS1 seal: axial length of the cone behind the groove.|密封圈:凹槽后锥面轴向长度。
bearing_seal_groove_r|bearings|mm|0|2|2RS1 seal: radius of the half-round lip groove.|密封圈:半圆唇槽半径。
bearing_seal_notch_r|bearings|mm|1|60|2RS1 outer ring: bore radius in the seal notch.|外圈密封槽内孔半径。
bearing_seal_notch_t|bearings|mm|0|2|2RS1 outer ring: axial depth of the seal notch.|外圈密封槽轴向深度。
bearing_seal_ri|bearings|mm|1|60|Seal inner radius.|密封圈内半径。
bearing_seal_rm|bearings|mm|1|60|Seal radius where the inner lip meets the flange.|密封圈内唇与法兰交界半径。
bearing_seal_ro|bearings|mm|1|60|Seal outer radius.|密封圈外半径。
bearing_seal_t|bearings|mm|0.05|3|Seal outer flange thickness.|密封圈外法兰厚度。
bearing_seal_z0|bearings|mm|0|3|Seal axial start (bearing local).|密封圈轴向起点(轴承局部坐标)。
bearing_seal_z1|bearings|mm|0|5|Seal axial end (bearing local).|密封圈轴向终点(轴承局部坐标)。
board_inner_r|electronics|mm|1|100|Inner radius of the phase board.|相位板内半径。
board_t|electronics|mm|0.2|10|Phase board stack thickness.|相位板叠层厚度。
board_gap|electronics|mm|0|10|Gap between the board face and the end of the coil leads.|相位板表面与引线末端之间的间隙。
board_pin_clear|electronics|mm|0|2|Clearance around each lead sleeve in the board.|相位板中引线套筒周围的间隙。
phase_board_n_offset|electronics|mm|0|10|Height of the N-side copper surface above the lead end.|N侧铜面高出引线末端的高度。
phase_board_n_z0|electronics|mm|-5|5|Phase board STEP frame offset along the axis.|相位板STEP坐标系沿轴向偏移。
phase_board_x|electronics|mm|-500|500|Phase board STEP frame offset X.|相位板STEP坐标系X偏移。
phase_board_y|electronics|mm|-500|500|Phase board STEP frame offset Y.|相位板STEP坐标系Y偏移。
holder_bolt_count|fasteners|count|3|24|Number of holder screws, bosses and spokes.|支架螺钉、凸台与辐条的数量。
bolt_d|fasteners|mm|0.5|10|Holder screw shank diameter in the bosses.|支架凸台中螺钉杆直径。
screw_head_d|fasteners|mm|1|20|Socket head screw: head diameter.|内六角螺钉:头部直径。
screw_head_h|fasteners|mm|0.5|20|Socket head screw: head height.|内六角螺钉:头部高度。
screw_head_fillet|fasteners|mm|0|2|Socket head screw: head edge radius.|内六角螺钉:头部边缘圆角。
screw_shank_d|fasteners|mm|0.5|10|Screw shank diameter as modelled.|螺钉杆直径(建模值)。
screw_socket_af|fasteners|mm|0.5|10|Hex socket across flats.|内六角对边宽度。
screw_socket_depth|fasteners|mm|0.2|20|Hex socket depth.|内六角深度。
screw_socket_point|fasteners|deg|60|179|Included angle of the drill point under the socket.|内六角底部钻尖夹角。
screw_tip_chamfer|fasteners|mm|0|3|Chamfer at the screw tip.|螺钉尖端倒角。
"""

#: CAD-only parameters whose names the simulation also knows must never be here (EM isolation); asserted by a test
EM_FORBIDDEN_PREFIXES = ("stator_", "rotor_up", "rotor_down", "air_gap", "slot_", "tooth", "wire_", "magnet_height", "magnet_fill",
                         "magnet_up", "magnet_down", "num_", "core_", "cut_", "insulation", "motor_length", "sleeve")


def _parse() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    lab = _labels()
    for line in _TABLE.strip().splitlines():
        name, group, unit, lo, hi, en, zh = line.split("|")
        if group not in PT.GROUP_IDS:
            raise ValueError(f"cad_params: unknown group {group!r} for {name}")
        if name not in lab:
            raise ValueError(f"cad_params: no label for {name}")
        out[name] = {"name": name, "group": group, "label": lab[name], "unit": "" if unit == "count" else unit,
                     "kind": "int" if name.endswith("_count") else "float", "min": float(lo), "max": float(hi),
                     "tip": {"en": en, "zh": zh}, "cad_only": True}
    return out


CATALOGUE: Dict[str, Dict[str, Any]] = _parse()

#: Bearing internals (balls, lands, seals, edge radii) belong to the BEARING, not to the motor: they are shown read-only and the only
#: bearing edit is the choice of the bearing card (``bearing_card``).  Owner 2026-10-15.
READ_ONLY_GROUPS = ("bearings",)
READ_ONLY = frozenset(n for n, m in CATALOGUE.items() if m["group"] in READ_ONLY_GROUPS)
READ_ONLY_NOTE = {"en": "Bearing internals come with the bearing; choose the bearing card instead.",
                  "zh": "轴承内部尺寸由轴承型号决定;请改选轴承型号。"}

#: roles of a built-model parameter that an edit may replace (a ``derived`` row is a formula of others)
EDITABLE_ROLES = ("mechanics", "input")
SECTIONS = ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part", "named_parameters_mechanics")


class CadParamError(ValueError):
    """An edit that cannot be accepted; ``violations`` lists the reasons in words (the HTTP layer answers 422)."""

    def __init__(self, message: str, violations: Optional[List[str]] = None):
        super().__init__(message)
        self.violations = list(violations or [])


# ---------------------------------------------------------------------------
# where the built model is
# ---------------------------------------------------------------------------

def cad_root() -> Path:
    from motor_ai_sim import model3d as M
    return M.cad_root()


def model_dir(die: str, config: str, root: Optional[Path] = None) -> Optional[Path]:
    """The folder of the built model of exactly this die/config: names are matched against the directory listing, never joined
    into a path (like ``model3d.find``)."""
    root = Path(root) if root else cad_root()
    if not root.is_dir():
        return None
    for d in root.iterdir():
        if d.is_dir() and d.name == die and not d.name.startswith("."):
            for c in d.iterdir():
                if c.is_dir() and c.name == config and ".building-" not in c.name and ".old-" not in c.name:
                    return c
    return None


def read_built_params(die: str, config: str, root: Optional[Path] = None) -> Optional[Dict[str, Dict[str, Any]]]:
    """``{name: parameter row}`` of the profile the last build was fed (``params.json``), or None when no model is built."""
    d = model_dir(die, config, root)
    if d is None or not (d / "params.json").is_file():
        return None
    try:
        doc = json.loads((d / "params.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows: Dict[str, Dict[str, Any]] = {}
    for sec in SECTIONS:
        for p in doc.get(sec, []) or []:
            if isinstance(p, dict) and p.get("name"):
                rows[str(p["name"])] = p
    return rows or None


def read_manifest(die: str, config: str, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    d = model_dir(die, config, root)
    if d is None or not (d / "manifest.json").is_file():
        return None
    try:
        return json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# overrides: format, hash, application
# ---------------------------------------------------------------------------

def clean_overrides(raw: Any) -> Dict[str, Any]:
    """The stored ``cad_overrides`` block as a plain ``{name: number}`` (+ ``bearing_card``): unknown shapes are dropped."""
    from motor_ai_sim.cad import store
    return store.clean_cad_overrides(raw)


def overrides_hash(overrides: Any) -> str:
    """Short id of an override set; the build records it in the manifest so a stale model is recognised without rebuilding."""
    from motor_ai_sim.cad import store
    return store.cad_overrides_hash(overrides)


def expression_of(value: float, unit: str) -> str:
    return ("%g mm" % value) if unit == "mm" else ("%g deg" % value) if unit == "deg" else ("%g" % value)


def apply_overrides(exprs: Dict[str, str], units: Dict[str, str], overrides: Dict[str, Any]) -> Dict[str, str]:
    """A copy of ``{name: expression}`` with the numeric overrides applied (``bearing_card`` is handled by the caller)."""
    out = dict(exprs)
    for k, v in overrides.items():
        if k == BEARING_CARD_KEY:
            continue
        if k in out:
            out[k] = expression_of(float(v), units.get(k, "mm"))
    return out


# ---------------------------------------------------------------------------
# rows of the editable table
# ---------------------------------------------------------------------------

def _evaluate(exprs: Dict[str, str]) -> Dict[str, float]:
    from motor_ai_sim.cad import store
    return store.evaluate(exprs)


def _reference_expr(p: Dict[str, Any]) -> str:
    """The expression a parameter has WITHOUT any override: the build records it as ``reference_expression`` when it applied one."""
    return str(p.get("reference_expression") or p.get("freecad_expression"))


#: Groups of parameters a FRAMELESS KIT (``cad_variant: frameless``) still has parts for: housing, shaft, bearings and screws are not built.
KIT_GROUPS = ("winding", "rotor", "magnets", "electronics")


def rows(built: Dict[str, Dict[str, Any]], overrides: Dict[str, Any], kit: bool = False) -> List[Dict[str, Any]]:
    """The editable table: every catalogue parameter that the built model has as a ``mechanics`` / ``input`` row.

    ``default`` = value without any override, ``built`` = value in the model on the server, ``value`` = value with the saved
    override (what the next build will use).  Ordered by the part-tree group, then the catalogue."""
    defaults = _evaluate({n: _reference_expr(p) for n, p in built.items()})
    out: List[Dict[str, Any]] = []
    for name, meta in CATALOGUE.items():
        p = built.get(name)
        if p is None or p.get("role") not in EDITABLE_ROLES:
            continue
        if kit and meta["group"] not in KIT_GROUPS:
            continue
        ov = overrides.get(name)
        d = round(float(defaults[name]), 9)
        out.append({**meta, "editable": name not in READ_ONLY, "default": d, "built": round(float(p.get("value", d)), 9),
                    "value": float(ov) if isinstance(ov, (int, float)) else d,
                    "overridden": isinstance(ov, (int, float)) and abs(float(ov) - d) > 1e-9,
                    "cad_only_label": CAD_ONLY_LABEL})
    out.sort(key=lambda r: (PT.GROUP_IDS.index(r["group"]), list(CATALOGUE).index(r["name"])))
    return out


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

_PRECHECK = None


def load_precheck():
    """The template's pre-check function ``precheck(g)``, loaded from its file, or None when the deployment has no template.

    ``MOTRES_CAD_TEMPLATE_DIR`` overrides the location (tests)."""
    global _PRECHECK
    if _PRECHECK is not None:
        return _PRECHECK
    from motor_ai_sim.cad import store
    base = os.environ.get("MOTRES_CAD_TEMPLATE_DIR") or os.path.join(store.CAD_DIR, "motor_templates", "segmented_flatwire")
    path = Path(base) / "health.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("motres_cad_health", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                                       # health.py imports math only at module level
    _PRECHECK = mod.precheck
    return _PRECHECK


def validate(built: Dict[str, Dict[str, Any]], overrides: Dict[str, Any], *, bearing: Optional[Dict[str, float]] = None,
             kit: bool = False) -> List[str]:
    """Every reason in words why ``overrides`` would not build: ranges, integers, and the template's pre-check applied to the
    whole parameter set (``bearing`` = d / D / B of a chosen bearing card).  Empty list = accepted.
    Raises ``CadParamError`` when the pre-check is not available in this deployment."""
    problems: List[str] = []
    units = {n: str(p.get("unit") or "") for n, p in built.items()}
    for name, v in overrides.items():
        if name == BEARING_CARD_KEY:
            continue
        meta = CATALOGUE.get(name)
        if meta is None:
            problems.append(f"{name}: not a CAD-only parameter; electromagnetic parameters are edited in Active, not here")
            continue
        if kit and meta["group"] not in KIT_GROUPS:
            problems.append(f"{name}: the frameless kit has no such part (housing, shaft, bearings and screws are not built)")
            continue
        if name in READ_ONLY:
            problems.append(f"{name}: a bearing internal, read-only; it comes with the bearing - choose the bearing card instead")
            continue
        p = built.get(name)
        if p is None or p.get("role") not in EDITABLE_ROLES:
            problems.append(f"{name}: this motor's CAD model has no such editable parameter")
            continue
        x = float(v)
        if meta["kind"] == "int" and abs(x - round(x)) > 1e-9:
            problems.append(f"{name} = {x:g} must be a whole number")
        if x < meta["min"] - 1e-12 or x > meta["max"] + 1e-12:
            problems.append(f"{name} = {x:g} is outside the allowed range {meta['min']:g} .. {meta['max']:g} "
                            f"{meta['unit']}".rstrip())
    if problems:
        return problems
    pre = load_precheck()
    if pre is None:
        raise CadParamError("the CAD template pre-check is not available in this deployment; the edit cannot be validated",
                            ["template pre-check unavailable"])
    exprs = apply_overrides({n: _reference_expr(p) for n, p in built.items()}, units, overrides)
    if bearing and not kit:
        for key, name in (("d", "bearing_d"), ("D", "bearing_D"), ("B", "bearing_B")):
            if name in exprs and key in bearing:
                exprs[name] = "%g mm" % float(bearing[key])
    try:
        vals = _evaluate(exprs)
    except Exception as exc:                                           # noqa: BLE001 - names the offending expression
        return [f"the parameters cannot be evaluated: {exc}"]
    try:
        return [str(v) for v in pre(lambda n: vals[n])]
    except KeyError as exc:
        return [f"the built model lacks the parameter {exc} the pre-check needs"]
    except Exception as exc:                                           # noqa: BLE001
        return [f"pre-check failed: {type(exc).__name__}: {exc}"]


def coerce(values: Dict[str, Any]) -> Tuple[Dict[str, float], List[str]]:
    """``{name: number}`` parsed from a request body (strings with a decimal comma allowed); ``None`` / "" = reset to default is
    handled by the caller.  Returns (numbers, problems)."""
    out: Dict[str, float] = {}
    bad: List[str] = []
    for k, v in values.items():
        if isinstance(v, bool):
            bad.append(f"{k}: not a number")
            continue
        try:
            x = float(str(v).replace(",", ".")) if not isinstance(v, (int, float)) else float(v)
        except (TypeError, ValueError):
            bad.append(f"{k}: '{v}' is not a number")
            continue
        if not math.isfinite(x):
            bad.append(f"{k}: not a finite number")
            continue
        out[str(k)] = x
    return out, bad
