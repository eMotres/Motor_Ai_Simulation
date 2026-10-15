# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""THE part tree of a motor: one data source, used everywhere (owner 2026-10-15: "one part tree everywhere").

Geometry (Full), Materials (Full), the 3-D model viewer, the component card ``parts`` and the BOM export all read their part
list from :func:`part_tree`.  Same part ids, same names (EN and zh-CN), same order and the same grouping everywhere:

    Stator / Winding / Rotor / Magnets / Shaft / Housing & holders / Bearings / Electronics / Fasteners  (+ Other)

The rows come from :func:`motor_ai_sim.modules.motor_mass.full_part_list` (the CAD bill of materials of the built model with the
ACTIVE electromagnetic parts mapped onto it - ``em: true`` marks a part the electromagnetic simulation uses).  This module only
adds what every surface needs to agree on: the group of a part, its position, its two names and the viewer nodes that draw
it (``glb`` = the ``family`` of the GLB groups, so a selection in the tree highlights the part in the 3-D viewer).

Read-only; nothing here is solved or written.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

#: (group id, English name, zh-CN name), in display order
GROUPS: Tuple[Tuple[str, str, str], ...] = (
    ("stator", "Stator", "定子"),
    ("winding", "Winding", "绕组"),
    ("rotor", "Rotor", "转子"),
    ("magnets", "Magnets", "磁钢"),
    ("shaft", "Shaft", "轴"),
    ("housing", "Housing & holders", "外壳与支架"),
    ("bearings", "Bearings", "轴承"),
    ("electronics", "Electronics", "电子部件"),
    ("fasteners", "Fasteners", "紧固件"),
    ("other", "Other", "其他"),
)
GROUP_IDS: Tuple[str, ...] = tuple(g[0] for g in GROUPS)

# part id -> (group, English name, zh-CN name, viewer families (GLB ``family`` extras))
# The ORDER of this table is the order of the parts inside their group and in every flat list.
_PARTS: Tuple[Tuple[str, str, str, str, Tuple[str, ...]], ...] = (
    ("stator_core", "stator", "Stator core", "定子铁芯", ("Core",)),
    ("slot", "winding", "Winding copper", "绕组铜线", ("Coils",)),
    ("wire_insulation", "winding", "Wire enamel", "漆包线绝缘", ("Coils",)),
    ("slot_insulation", "winding", "Slot insulation", "槽绝缘", ()),
    ("rotor_core", "rotor", "Rotor core", "转子铁芯", ("Rotor",)),
    ("sleeve", "rotor", "Retaining sleeve", "护套", ()),
    ("magnet", "magnets", "Magnets", "磁钢", ("Magnets", "Magnets_S")),
    ("shaft", "shaft", "Shaft", "轴", ("Shaft",)),
    ("carrier_front", "shaft", "Rotor carrier, front", "转子支架(前)", ("Shaft",)),
    ("carrier_rear", "shaft", "Rotor carrier, rear", "转子支架(后)", ("Shaft_rear",)),
    ("holder_front", "housing", "Stator holder, front", "定子支架(前)", ("Holder_front",)),
    ("holder_back", "housing", "Stator holder, back", "定子支架(后)", ("Holder_back",)),
    ("holder", "housing", "Stator holder", "定子支架", ("Holder_front", "Holder_back")),
    ("rings", "housing", "Glass-fibre rings", "玻璃纤维环", ("Ring_front", "Ring_back")),
    ("prop_fix", "housing", "Propeller fixing plate", "螺旋桨固定板", ("Prop_fix",)),
    ("bearings", "bearings", "Bearings", "轴承", ("Bearings_front", "Bearings_front_balls", "Bearings_front_seals",
                                                    "Bearings_back", "Bearings_back_balls", "Bearings_back_seals")),
    ("board", "electronics", "Phase board", "相位板", ("PhaseBoard_FR4", "PhaseBoard_copper", "PhaseBoard_other")),
    ("pins", "electronics", "Phase pins", "相位针脚", ("PhaseBoard_pins",)),
    ("screws", "fasteners", "Screws", "螺钉", ("Screws",)),
)
_BY_ID: Dict[str, Tuple[str, str, str, Tuple[str, ...]]] = {p[0]: p[1:] for p in _PARTS}
_ORDER: Dict[str, int] = {p[0]: i for i, p in enumerate(_PARTS)}
_GROUP_ORDER: Dict[str, int] = {g: i for i, g in enumerate(GROUP_IDS)}

#: Role labels (the roles of ``ComponentPart``) are not translated here; the web owns that.

_OTHER = re.compile(r"^other_")


def part_ids() -> List[str]:
    """Every known part id, in tree order (the unknown ``other_*`` rows of a BOM come after them)."""
    return [p[0] for p in _PARTS]


def group_of(part_id: str) -> str:
    """The group a part belongs to; a BOM row no rule recognises (``other_<n>``) is in ``other``."""
    hit = _BY_ID.get(part_id)
    return hit[0] if hit else "other"


def names_of(part_id: str, fallback: str = "") -> Dict[str, str]:
    """``{"en", "zh"}`` of a part.  An unknown part keeps its BOM name in both languages."""
    hit = _BY_ID.get(part_id)
    if hit:
        return {"en": hit[1], "zh": hit[2]}
    return {"en": fallback or part_id, "zh": fallback or part_id}


def families_of(part_id: str) -> List[str]:
    """The viewer (GLB group ``family``) names that draw this part."""
    hit = _BY_ID.get(part_id)
    return list(hit[3]) if hit else []


def sort_key(part_id: str) -> Tuple[int, int, str]:
    """(group position, position in the table, id) - the order of every list."""
    hit = _BY_ID.get(part_id)
    if hit:
        return (_GROUP_ORDER[hit[0]], _ORDER[part_id], part_id)
    return (_GROUP_ORDER["other"], 10_000, part_id)


def card_part_name(part_id: str, fallback: str) -> str:
    """The English name a component card lists for this part (same as the tree)."""
    return names_of(part_id, fallback)["en"] if part_id in _BY_ID else fallback


def build(rows: List[Dict[str, Any]], *, brief: bool = False) -> Dict[str, Any]:
    """Arrange ``full_part_list`` rows into the tree.

    ``brief=True`` is the form the 3-D model tab gets: ids, names, groups, order and viewer nodes only - no material, no mass
    (a viewer is not a bill of materials)."""
    parts: List[Dict[str, Any]] = []
    for r in sorted(rows, key=lambda r: sort_key(str(r.get("key")))):
        pid = str(r.get("key"))
        nm = names_of(pid, str(r.get("name") or ""))
        item: Dict[str, Any] = {
            "id": pid, "key": pid, "group": group_of(pid), "name": nm["en"], "names": nm,
            "role": r.get("role"), "em": bool(r.get("em")), "glb": families_of(pid),
        }
        if not brief:
            for k in ("material_id", "default_material_id", "user_set", "mass_kg", "counted", "status", "basis"):
                item[k] = r.get(k)
        parts.append(item)
    present = {p["group"] for p in parts}
    groups = [{"id": g, "name": {"en": en, "zh": zh}, "parts": [p["id"] for p in parts if p["group"] == g]}
              for g, en, zh in GROUPS if g in present]
    for i, p in enumerate(parts):
        p["order"] = i
    return {"groups": groups, "parts": parts}


def part_tree(die: str, config: str, *, dies_dir: Optional[Any] = None, cad_dir: Optional[Any] = None,
              brief: bool = False) -> Dict[str, Any]:
    """The part tree of ``die`` / ``config`` (see the module doc).  Raises ``MotorMassError`` when the die is not available.

    Keys: ``die, config, source, partial, total_kg, active_kg, rotating_kg, mass_basis`` (omitted when ``brief``),
    ``groups`` and ``parts``."""
    from motor_ai_sim.modules import motor_mass as MM
    fl = MM.full_part_list(die, config, dies_dir=dies_dir, cad_dir=cad_dir)
    out: Dict[str, Any] = {"die": die, "config": config, "source": fl["source"], "partial": fl["partial"]}
    if not brief:
        out.update(total_kg=fl["total_kg"], active_kg=fl["active_kg"], rotating_kg=fl["rotating_kg"],
                   mass_basis=fl["mass_basis"])
    out.update(build(fl["parts"], brief=brief))
    return out
