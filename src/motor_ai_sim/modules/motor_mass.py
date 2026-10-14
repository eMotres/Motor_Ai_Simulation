# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The motor's mass as the APP computes it, per part - for the component card.

Owner decision 2026-10-14: the card's motor mass is NOT a duty-record number and NOT a placeholder.  It
is ``masses.compute_masses`` evaluated on the die + configuration the passport belongs to: the number
Configure and the datasheet show as "Active material mass" (= ``mass_total_kg`` of a run).  That total
holds the stator iron, rotor iron, magnets, winding copper (end windings included), the shaft tube
and the retaining sleeve.  It does NOT hold holders, rings, bearings, screws, the phase board or
insulation: those exist only in the CAD bill of materials (``cad/bom.py``).  ``bom_extra_parts`` reads
that BOM file when the server has built one and lists those parts as NOT counted in the card mass.

Read-only.  Nothing is solved and nothing is written.  The materials of the parts are the ones the
configuration ASSIGNS (stator_core, rotor_core, magnet, ...); a part the configuration does not name
takes the shipped default of ``config/motor_config.yaml`` (the same fall-back the app applies), and the
part's basis says so.  CAD-only assembly clearances (magnet pocket offsets etc.) are NOT in these
masses: the magnet is the nominal magnet, the clearance is air (docs/CAD_GENERATOR.md).
"""
from __future__ import annotations

import csv
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

log = logging.getLogger(__name__)

#: compute_masses key -> (card part name, assignment key, role)
_APP_PARTS: Tuple[Tuple[str, str, str, str], ...] = (
    ("stator", "Stator core", "stator_core", "magnetic"),
    ("rotor", "Rotor core", "rotor_core", "magnetic"),
    ("mag", "Magnets", "magnet", "magnetic"),
    ("cu", "Winding copper", "slot", "conductor"),
    ("shaft", "Shaft", "shaft", "structural"),
    ("sleeve", "Retaining sleeve", "sleeve", "structural"),
)

APP_MASS_BASIS = ("the app's mass model masses.compute_masses (CAD section x stack length x lamination fill x density of "
                  "the assigned material; copper with the end-winding factor) - the number Configure and the datasheet "
                  "show as the motor mass. It holds stator iron, rotor iron, magnets, winding copper, shaft and "
                  "retaining sleeve only; holders, rings, bearings, screws, board and insulation are not in it. "
                  "Magnets are the nominal magnet (CAD-only assembly clearances are air).")


class MotorMassError(RuntimeError):
    """The mass of a die/configuration cannot be computed here (die not found, geometry unbuildable)."""


@dataclass
class MotorPart:
    name: str
    material_id: str
    mass_kg: Optional[float]
    role: str
    counted: bool = True
    status: str = "computed"
    basis: str = ""
    key: str = ""                              # stable id: an assignment key (stator_core, ...) or a mechanical part key
    em: bool = True                            # True = used by the electromagnetic simulation (the ACTIVE assignment)
    default_material_id: Optional[str] = None  # documented default of a non-active part
    user_set: bool = False                     # the die carries a choice for this part


@dataclass
class MotorMass:
    die: str
    config: str
    total_kg: float
    parts: List[MotorPart] = field(default_factory=list)
    area_source: str = ""
    basis: str = APP_MASS_BASIS
    components: Dict[str, float] = field(default_factory=dict)         # raw compute_masses numbers


# ---------------------------------------------------------------------------
# where the dies are
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]


def find_die_dir(die: str, dies_dir: Optional[os.PathLike] = None) -> Optional[Path]:
    """The folder of ``die``: an explicit ``dies_dir``, ``MOTRES_DIES_DIR``, the workspace/shared catalog, then
    the repository's ``config/dies``."""
    cands: List[Path] = []
    if dies_dir is not None:
        cands.append(Path(dies_dir) / die)
    else:
        env = os.environ.get("MOTRES_DIES_DIR", "").strip()
        if env:
            cands.append(Path(env) / die)
        try:
            from motor_ai_sim.workspace import resolve_die_dir
            d = resolve_die_dir(die)
            if d is not None:
                cands.append(Path(str(d)))
        except Exception:                                                  # noqa: BLE001
            pass
        cands.append(_REPO / "config" / "dies" / die)
    for c in cands:
        if (c / "die.yaml").is_file():
            return c
    return None


def _default_config_doc() -> dict:
    from motor_ai_sim.simulation.geometry_2d import _cfg_path
    return yaml.safe_load(Path(_cfg_path()).read_text(encoding="utf-8")) or {}


_CACHE: Dict[Tuple[str, str, float, float], MotorMass] = {}
_LOCK = threading.Lock()


def app_motor_mass(die: str, config: str, *, dies_dir: Optional[os.PathLike] = None) -> MotorMass:
    """The app's mass of ``die`` / ``config`` with its per-part breakdown.  Raises ``MotorMassError``."""
    ddir = find_die_dir(die, dies_dir)
    if ddir is None:
        raise MotorMassError(f"die {die!r} not found (no die.yaml in the catalog); the mass is unknown, not zero")
    dfile, cfile = ddir / "die.yaml", ddir / f"{config}.yaml"
    if not cfile.is_file():
        raise MotorMassError(f"configuration {config!r} of die {die!r} not found")
    key = (str(ddir), config, dfile.stat().st_mtime, cfile.stat().st_mtime)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    dy = yaml.safe_load(dfile.read_text(encoding="utf-8")) or {}
    cy = yaml.safe_load(cfile.read_text(encoding="utf-8")) or {}
    geo = dict(dy.get("geometry") or {})
    geo.update(cy.get("geometry_overrides") or {})
    cfg_doc = _default_config_doc()
    assigned = {k: v for k, v in (cfg_doc.get("materials") or {}).items()
                if k in ("stator_core", "rotor_core", "magnet", "slot", "shaft") and v}
    own = {k: v for k, v in (cy.get("materials") or {}).items() if v}
    assigned.update(own)
    try:
        from motor_ai_sim.masses import compute_masses
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override, params_from_config
        p = params_from_config(geo_override=geo)
        g = merge_geo_override(dict(cfg_doc.get("geometry") or {}), geo)
        m = compute_masses(p, g, k_end=0.0, materials=assigned)
    except Exception as exc:                                               # noqa: BLE001
        raise MotorMassError(f"mass of {die} {config} could not be computed: {exc}") from exc
    mats = m.get("MAT") or {}
    phys = m.get("PHYS") or {}
    parts: List[MotorPart] = []
    for ckey, name, akey, role in _APP_PARTS:
        mass = float(m.get(ckey) or 0.0)
        modelled = float(phys.get(ckey) or 0.0)
        mat = str(mats.get(akey) or "")
        if not mat:
            continue
        src = "the configuration" if akey in own else "the shipped default of config/motor_config.yaml"
        if mass > 0:
            parts.append(MotorPart(name, mat, mass, role, True, "computed", f"material assigned by {src}", key=akey))
        elif modelled > 0:                                                 # 'reference' state: customer's part
            parts.append(MotorPart(name, mat, modelled, role, False, "computed",
                                   f"material assigned by {src}; part state 'reference' (not billed in the mass)",
                                   key=akey))
    out = MotorMass(die=die, config=config, total_kg=float(m["total"]), parts=parts,
                    area_source=str(m.get("area_source") or ""),
                    components={k: float(m[k]) for k in ("stator", "rotor", "mag", "cu", "shaft", "sleeve", "active", "total")
                                if k in m})
    with _LOCK:
        _CACHE[key] = out
    return out


# ---------------------------------------------------------------------------
# parts that exist only in the CAD bill of materials
# ---------------------------------------------------------------------------

#: BOM lines the app mass already covers (never listed twice)
_BOM_COVERED = re.compile(r"^(stator lamination|rotor lamination|permanent magnet|coil|enamelled|shaft|rotor sleeve)", re.I)


def _cad_dirs() -> List[Path]:
    out: List[Path] = []
    env = os.environ.get("MOTRES_CAD_DIR", "").strip()
    if env:
        out.append(Path(env))
    out.append(Path("/srv/motres/shared/cad"))
    try:
        from motor_ai_sim.workspace import shared_root
        out.append(Path(str(shared_root())) / "cad")
    except Exception:                                                      # noqa: BLE001
        pass
    return out


def die_part_choices(die_dir: Optional[os.PathLike]) -> Dict[str, str]:
    """The die's own material choices for its NON-ACTIVE parts (die.yaml ``part_materials``).  The electromagnetic
    solver never reads this block: the active assignment is the configuration's ``materials``."""
    if die_dir is None:
        return {}
    f = Path(die_dir) / "die.yaml"
    try:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception:                                                      # noqa: BLE001
        return {}
    pm = doc.get("part_materials") or {}
    return {str(k): str(v) for k, v in pm.items() if v} if isinstance(pm, dict) else {}


def bom_extra_parts(die: str, config: str, *, cad_dir: Optional[os.PathLike] = None,
                    choices: Optional[Dict[str, str]] = None) -> List[MotorPart]:
    """Parts of the CAD BOM that the app mass does not contain, ``counted=False``.  Empty when the server has built
    no BOM for this die/configuration.  Only the part name, material and total mass are read from the file.
    ``choices`` = the die's own material choices (``die_part_choices``)."""
    roots = [Path(cad_dir)] if cad_dir is not None else _cad_dirs()
    for root in roots:
        d = root / die / config
        files = sorted(d.glob("*_BOM.csv")) if d.is_dir() else []
        if files:
            return parts_from_bom_csv(files[0], choices=choices)
    return []


def _density(material_id: str) -> Optional[float]:
    from motor_ai_sim import part_materials as pm
    try:
        return pm.resolve(material_id).get("density_kg_m3")
    except pm.UnknownPartMaterial:
        return None


def parts_from_bom_csv(path: os.PathLike, *, choices: Optional[Dict[str, str]] = None) -> List[MotorPart]:
    """The non-active parts of a BOM file.  A part's material is the die's choice when there is one (the BOM mass, a
    CAD volume times the default density, is then scaled by the density ratio), else the documented default."""
    from motor_ai_sim import part_materials as pm
    defs = pm.mechanical_parts()
    rx = {k: re.compile(v["match"], re.I) for k, v in defs.items()}
    choices = choices or {}
    out: List[MotorPart] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            item = (row.get("item") or "").strip()
            part = (row.get("part") or "").strip()
            mass_g = (row.get("total mass g") or "").strip()
            if not item.isdigit() or not part or not mass_g or _BOM_COVERED.search(part):
                continue
            try:
                grams = float(mass_g)
            except ValueError:
                continue
            if grams <= 0:
                continue
            key = next((k for k, r in rx.items() if r.search(part)), None)
            if key is None:
                continue
            d = defs[key]
            default = str(d["material"])
            mat = choices.get(key) or default
            mass = grams / 1000.0
            note = "CAD bill of materials, not in the app mass" + (f"; {d['note']}" if d.get("note") else "")
            if mat != default:
                r0, r1 = _density(default), _density(mat)
                if r0 and r1:
                    mass *= r1 / r0
                    note += f"; material chosen for this die, mass scaled by density ({r1:.0f}/{r0:.0f})"
                else:
                    note += "; material chosen for this die (density unknown: BOM mass kept)"
            name = d["name"] if key != "holder" else part.split(" (")[0]
            out.append(MotorPart(name, mat, mass, str(d["role"]), False, str(d.get("status", "computed")), note,
                                 key=key, em=False, default_material_id=default, user_set=key in choices))
    return out


def config_bearing_cards(die_dir: Path, config: str) -> List[str]:
    """Bearing cards named by the configuration (its mechanical description when no CAD BOM exists)."""
    try:
        cy = yaml.safe_load((die_dir / f"{config}.yaml").read_text(encoding="utf-8")) or {}
    except Exception:                                                      # noqa: BLE001
        return []
    br = cy.get("bearings") or {}
    if not isinstance(br, dict):
        return []
    return sorted({str(v.get("card")) for v in br.values() if isinstance(v, dict) and v.get("card")})


def full_part_list(die: str, config: str, *, dies_dir: Optional[os.PathLike] = None,
                   cad_dir: Optional[os.PathLike] = None) -> Dict[str, Any]:
    """The whole motor, part by part (Materials tab "Full"): the ACTIVE parts (the electromagnetic assignment, read
    only here) plus every non-active part the CAD bill of materials lists, each with its material and mass.  A motor
    without a built BOM lists only what its configuration names (its bearings card) - no part is invented.
    Raises ``MotorMassError`` when the die is not available."""
    from motor_ai_sim import part_materials as pm
    ddir = find_die_dir(die, dies_dir)
    mm = app_motor_mass(die, config, dies_dir=dies_dir)
    choices = die_part_choices(ddir)
    rows: List[MotorPart] = list(mm.parts)
    try:
        cy = yaml.safe_load((ddir / f"{config}.yaml").read_text(encoding="utf-8")) or {}
    except Exception:                                                      # noqa: BLE001
        cy = {}
    # insulation is assigned (a thermal / EM role) but the app does not weigh it
    for akey, name in (("slot_insulation", "Slot insulation"), ("wire_insulation", "Wire enamel")):
        mat = (cy.get("materials") or {}).get(akey)
        if mat:
            rows.append(MotorPart(name, str(mat), None, "insulation", False, "computed",
                                  "assigned for the simulation; the app does not weigh it", key=akey))
    extras = bom_extra_parts(die, config, cad_dir=cad_dir, choices=choices)
    source = "app+cad_bom" if extras else "app"
    if not extras and config_bearing_cards(ddir, config):
        d = pm.mechanical_parts()["bearings"]
        rows.append(MotorPart("Bearings", choices.get("bearings") or d["material"], None, d["role"], False,
                              d.get("status", "computed"),
                              "named by the configuration's bearings; no CAD bill of materials built yet, so no mass",
                              key="bearings", em=False, default_material_id=d["material"],
                              user_set="bearings" in choices))
        source = "app+config"
    rows += extras
    return {"die": die, "config": config, "source": source, "total_kg": mm.total_kg, "mass_basis": mm.basis,
            "parts": [dict(key=p.key, name=p.name, role=p.role, em=p.em, material_id=p.material_id,
                           default_material_id=p.default_material_id, user_set=p.user_set, mass_kg=p.mass_kg,
                           counted=p.counted, status=p.status, basis=p.basis) for p in rows]}
