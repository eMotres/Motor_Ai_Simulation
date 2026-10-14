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
# the CAD bill of materials and the FULL motor mass
# ---------------------------------------------------------------------------
#
# OWNER DECISION 2026-10-14 ("the FULL mass must now be used everywhere"): the motor mass shown or used anywhere is the
# FULL assembled mass - the app's active parts (stator iron, rotor iron, magnets, winding copper, sleeve) + the real shaft
# (or rotor carrier) + holders, rings, bearings, screws, phase board, pins + the wire enamel - with the CAD BOM's masses
# and the die's own Full material assignment.  The ACTIVE mass (``app_motor_mass().total_kg``: what the electromagnetic
# simulation, the optimizer and the passport use) stays a separate, labelled number.  A motor with no built CAD BOM has
# only the active mass, and says so: ``partial = True`` ("partial mass (no CAD model yet)") - the two are never mixed.
#
# Rotating mass (mechanics: bearing load, spin-up energy, critical speeds): the rotating parts of the full list
# (``ROTATING_KEYS``).  ``masses.rotor_inertia_kg_m2`` keeps its meaning - the ACTIVE rotor (rotor iron + magnets + the
# simulation shaft tube + sleeve) about the shaft axis, from the CAD polygons; the BOM carries no polar moments, so the
# rotating mass below is a mass, not an inertia.

#: parts of the full list that turn with the rotor (rotor iron, magnets, shaft or rotor carrier, sleeve, glass-fibre rings,
#: the propeller fixing plate).  Bearings are not listed: half of each turns, and the app has no split.
ROTATING_KEYS = ("rotor_core", "magnet", "shaft", "sleeve", "carrier_front", "carrier_rear", "rings", "prop_fix")

PARTIAL_LABEL = "partial mass (no CAD model yet)"

#: the consistency guard: the non-active parts counted must be at least this fraction of what the BOM itself lists beyond the
#: simulation (its own "parts the simulation mass does not contain" row), else the mass is flagged partial
GUARD_FRACTION = 0.5

FULL_MASS_BASIS = ("the FULL assembled motor: the app's active parts (masses.compute_masses: stator iron, rotor iron, "
                   "magnets, winding copper, sleeve) + the CAD bill of materials' shaft or rotor carrier, holders, rings, "
                   "bearings, screws, phase board, pins and wire enamel, each with the material of the die's Full "
                   "assignment (a CAD mass is scaled by the density ratio when the material differs from the CAD's). "
                   "Magnets are the nominal magnet (CAD-only assembly clearances are air).")

_BOM_NUM = re.compile(r"density\s+(\d+(?:\.\d+)?)")
_BOM_COIL = re.compile(r"copper\s+(\d+(?:\.\d+)?)\s*g.*?enamel\s+(\d+(?:\.\d+)?)\s*g", re.I | re.S)


@dataclass
class BomLine:
    item: str
    part: str
    material: str
    grams: float                           # the file's ``total mass g`` (unit mass x qty, as the file defines it)
    notes: str
    qty: Optional[float] = None
    unit_g: Optional[float] = None

    @property
    def density(self) -> Optional[float]:
        """The density the CAD mass was computed with (the BOM states it in its notes), kg/m3."""
        m = _BOM_NUM.search(self.notes or "")
        return float(m.group(1)) if m else None

    @property
    def name(self) -> str:
        """The part's name: the first line of a (possibly multi-line) cell, whitespace collapsed, at most 80 characters."""
        first = (self.part or "").strip().splitlines()[0] if (self.part or "").strip() else ""
        return re.sub(r"\s+", " ", first).strip()[:80]

    @property
    def mass_consistent(self) -> bool:
        """unit mass x qty = total mass (the file's own definition), within rounding."""
        if self.qty is None or self.unit_g is None:
            return True
        return abs(self.unit_g * self.qty - self.grams) <= 0.02 * max(self.grams, 1e-9) + 0.01


@dataclass
class BomFile:
    path: str
    lines: List[BomLine] = field(default_factory=list)        # every numbered row WITH a mass
    unmassed: List[str] = field(default_factory=list)         # numbered rows with no mass (adhesive, varnish, liner ...)
    total_g: Optional[float] = None                           # the file's T1 row: total mass of the BOM lines
    non_sim_g: Optional[float] = None                         # the file's C row: "parts the simulation mass does not contain"

    @property
    def density(self) -> Optional[float]:
        """The density the CAD mass was computed with (the BOM states it in its notes), kg/m3."""
        m = _BOM_NUM.search(self.notes or "")
        return float(m.group(1)) if m else None


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


def find_bom_file(die: str, config: str, cad_dir: Optional[os.PathLike] = None) -> Optional[Path]:
    """``<cad>/<die>/<config>/*_BOM.csv`` written by the CAD build, or ``None`` (no CAD model built yet)."""
    for root in ([Path(cad_dir)] if cad_dir is not None else _cad_dirs()):
        d = root / die / config
        files = sorted(d.glob("*_BOM.csv")) if d.is_dir() else []
        if files:
            return files[0]
    return None


def _num(v: Any) -> Optional[float]:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def read_bom_file(path: os.PathLike) -> BomFile:
    """Parse a server BOM file (``bom.write_csv``): a BOM-encoded header, multi-line quoted cells, numbered part rows, then the
    ``T`` (totals), ``C`` (comparison) and ``E`` (explanation) rows.  A numbered row with a mass is a part; one without
    (adhesive, varnish, slot liner, power leads) is listed in ``unmassed``; T / C / E rows are never parts (only the two numbers
    the consistency guard needs are read from them).  Prices and suppliers are never read."""
    out = BomFile(path=str(path))
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            item = (row.get("item") or "").strip()
            part = (row.get("part") or "").strip()
            grams = _num(row.get("total mass g"))
            if item == "T1" and grams is not None:
                out.total_g = grams
            elif item == "C" and part.lower().startswith("parts the simulation mass does not contain") and grams is not None:
                out.non_sim_g = grams
            if not item.isdigit() or not part:
                continue
            if grams is None or grams <= 0:
                if not part.lower().startswith("enamelled"):      # the wire row: its mass is inside the coil line
                    out.unmassed.append(" ".join(part.split())[:80])
                continue
            out.lines.append(BomLine(item, part, (row.get("material / grade") or "").strip(), grams,
                                     (row.get("notes") or "").strip(), _num(row.get("qty")), _num(row.get("unit mass g"))))
    return out


def read_bom(path: os.PathLike) -> List[BomLine]:
    """The lines of a BOM file that carry a mass (see ``read_bom_file``)."""
    return read_bom_file(path).lines


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


def _density(material_id: str) -> Optional[float]:
    from motor_ai_sim import part_materials as pm
    try:
        return pm.resolve(material_id).get("density_kg_m3")
    except pm.UnknownPartMaterial:
        return None


def _scaled(grams: float, bom_density: Optional[float], material: str, default: str) -> Tuple[float, str]:
    """(kg, note): a CAD mass moved to ``material`` by the density ratio (the CAD volume is fixed).  The reference is the
    density the BOM itself used (stated in its notes), else that of the documented default material."""
    kg = grams / 1000.0
    r0 = bom_density or _density(default)
    r1 = _density(material)
    if not (r0 and r1):
        return kg, ("" if material == default else "; material chosen for this die (density unknown: CAD mass kept)")
    if abs(r1 / r0 - 1.0) < 0.005:
        return kg, ""
    return kg * r1 / r0, f"; mass scaled from the CAD's density ({r0:.0f} kg/m3) to {material} ({r1:.0f} kg/m3)"


def _classify(defs: Dict[str, Dict[str, Any]], part: str) -> Optional[str]:
    """Key of a non-active part (``mechanical_parts``) by the BOM part name, else ``None``."""
    for k, d in defs.items():
        if re.search(d["match"], part, re.I):
            return k
    return None


#: BOM lines the app mass already covers (the active parts) - their mass is the app's, never the BOM's
_BOM_ACTIVE = re.compile(r"^(stator lamination|rotor lamination|permanent magnet|enamelled|rotor sleeve)", re.I)
_BOM_COIL_LINE = re.compile(r"^coil", re.I)
_BOM_SHAFT = re.compile(r"^shaft", re.I)


@dataclass
class FullMass:
    die: str
    config: str
    total_kg: float                        # THE motor mass (partial = the active mass only)
    active_kg: float                       # the app's active mass: what the EM simulation / optimizer use
    rotating_kg: float
    partial: bool
    source: str                            # "cad_bom" | "active_only"
    parts: List[MotorPart] = field(default_factory=list)
    basis: str = ""
    bom_file: Optional[str] = None
    partial_reason: str = ""               # why the mass is partial (no BOM, rows not accounted for, rules unavailable)
    others: List[str] = field(default_factory=list)     # BOM rows no part rule recognised: COUNTED as "other"
    unmassed: List[str] = field(default_factory=list)   # BOM rows with no mass (adhesive, varnish, liner, leads)


_FULL_CACHE: Dict[Tuple, FullMass] = {}


def _full_mass_impl(die: str, config: str, *, dies_dir: Optional[os.PathLike] = None,
                    cad_dir: Optional[os.PathLike] = None) -> FullMass:
    """The FULL assembled motor mass of ``die`` / ``config`` (see the header).  Raises ``MotorMassError`` when the die
    is not available.  Without a built CAD BOM the answer is the active mass with ``partial = True``."""
    from dataclasses import replace
    from motor_ai_sim import part_materials as pm
    ddir = find_die_dir(die, dies_dir)
    mm = app_motor_mass(die, config, dies_dir=dies_dir)
    bom = find_bom_file(die, config, cad_dir)
    choices = die_part_choices(ddir)
    key = (str(ddir), config, (ddir / "die.yaml").stat().st_mtime_ns, (ddir / f"{config}.yaml").stat().st_mtime_ns,
           str(bom), bom.stat().st_mtime_ns if bom else 0, mm.total_kg)
    if key in _FULL_CACHE:
        return _FULL_CACHE[key]
    app = {p.key: p for p in mm.parts}
    try:
        cy = yaml.safe_load((ddir / f"{config}.yaml").read_text(encoding="utf-8")) or {}
    except Exception:                                                      # noqa: BLE001
        cy = {}
    em_mats = cy.get("materials") or {}
    bf = read_bom_file(bom) if bom else None
    lines = bf.lines if bf else []
    parts: List[MotorPart] = []
    if not lines:
        parts = [replace(p, basis=(p.basis + "; " if p.basis else "") + PARTIAL_LABEL) for p in mm.parts]
        why = ("no CAD bill of materials has been built for this motor" if bf is None
               else "the CAD bill of materials carries no part with a mass")
        out = FullMass(die, config, mm.total_kg, mm.total_kg, 0.0, True, "active_only", parts,
                       APP_MASS_BASIS + " " + PARTIAL_LABEL + ": " + why + ".", partial_reason=why,
                       unmassed=list(bf.unmassed) if bf else [])
    else:
        defs = pm.mechanical_parts()
        keyed: Dict[str, BomLine] = {}
        others: List[BomLine] = []
        shaft_line = coil_line = None
        for ln in lines:
            if _BOM_ACTIVE.search(ln.part):
                continue
            if _BOM_COIL_LINE.search(ln.part) and coil_line is None:
                coil_line = ln
            elif _BOM_SHAFT.search(ln.part) and shaft_line is None:
                shaft_line = ln
            else:
                k = _classify(defs, ln.part)
                if k and k not in keyed:
                    keyed[k] = ln
                else:
                    others.append(ln)          # unknown (or a second row of a known part): counted as "other", never dropped
        has_carrier = "carrier_front" in keyed or "carrier_rear" in keyed
        for k in ("stator_core", "rotor_core", "magnet", "slot", "sleeve"):
            if k in app:
                parts.append(app[k])
        sh = app.get("shaft")
        if has_carrier and sh is not None:
            parts.append(replace(sh, counted=False, basis="simulation stand-in (hollow tube) of the rotor carrier below; "
                                                          "not counted in the full mass"))
        elif shaft_line is not None and sh is not None:
            kg, note = _scaled(shaft_line.grams, shaft_line.density, sh.material_id, sh.material_id)
            r1, r0 = _density(sh.material_id), shaft_line.density
            if r0 and r1:
                kg = shaft_line.grams / 1000.0 * r1 / r0
                note = f"; mass scaled from the CAD's density ({r0:.0f} kg/m3) to {sh.material_id} ({r1:.0f} kg/m3)"
            parts.append(MotorPart("Shaft", sh.material_id, kg, "structural", True, "computed",
                                   "CAD bill of materials (the real stepped shaft), material of the EM shaft assignment" + note,
                                   key="shaft"))
        elif sh is not None:
            parts.append(sh)
        if coil_line is not None:
            m = _BOM_COIL.search(coil_line.notes or "")
            if m:
                enamel_g = float(m.group(2))
                parts.append(MotorPart("Wire enamel", str(em_mats.get("wire_insulation") or "polyimide"), enamel_g / 1000.0,
                                       "insulation", True, "computed", "CAD bill of materials (coil enamel)",
                                       key="wire_insulation"))
        for k, ln in keyed.items():
            d = defs[k]
            default = str(d["material"])
            mat = choices.get(k) or default
            kg, note = _scaled(ln.grams, ln.density, mat, default)
            parts.append(MotorPart(d["name"] if k != "holder" else ln.part.split(" (")[0], mat, kg, str(d["role"]), True,
                                   str(d.get("status", "computed")),
                                   "CAD bill of materials" + (f"; {d['note']}" if d.get("note") else "") + note,
                                   key=k, em=False, default_material_id=default, user_set=k in choices))
        for ln in others:
            parts.append(MotorPart(ln.name or f"BOM row {ln.item}", "Unspecified_BOM_part", ln.grams / 1000.0, "other", True,
                                   "assumed", "CAD bill of materials row no part rule recognises: counted as 'other' at the "
                                              "BOM's own mass; its material is not identified", key=f"other_{ln.item}",
                                   em=False))
        total = sum(p.mass_kg for p in parts if p.counted and p.mass_kg)
        rot = sum(p.mass_kg for p in parts if p.counted and p.mass_kg and p.key in ROTATING_KEYS)
        # CONSISTENCY GUARD: the non-active parts counted here must be most of what the BOM itself says the simulation
        # does not model (its own "parts the simulation mass does not contain" row).  If not, the matcher missed rows (or the
        # rules were unavailable): say partial, with the reason, instead of claiming a full mass.
        counted_extra_g = 1000.0 * sum(p.mass_kg for p in parts if p.counted and p.mass_kg
                                       and (not p.em or p.key == "wire_insulation") and p.key != "carrier_front")
        ref_g = bf.non_sim_g if bf and bf.non_sim_g else None
        if ref_g is None:                                     # no C row: the same sum from the file's own massed lines
            act = sum(ln.grams for ln in lines if _BOM_ACTIVE.search(ln.part))
            shaft_g = (shaft_line.grams if shaft_line else (keyed["carrier_front"].grams if "carrier_front" in keyed else 0.0))
            enamel_m = _BOM_COIL.search(coil_line.notes or "") if coil_line else None
            copper_g = (coil_line.grams - (float(enamel_m.group(2)) if enamel_m else 0.0)) if coil_line else 0.0
            ref_g = sum(ln.grams for ln in lines) - act - shaft_g - copper_g
        if ref_g > 1.0 and counted_extra_g < GUARD_FRACTION * ref_g:
            why = (f"only {counted_extra_g:.1f} g of the {ref_g:.1f} g of parts the CAD bill of materials lists beyond "
                   f"the simulation could be accounted for")
            log.error("full mass of %s %s is PARTIAL: %s", die, config, why)
            parts = [replace(p, basis=(p.basis + "; " if p.basis else "") + PARTIAL_LABEL) for p in mm.parts]
            out = FullMass(die, config, mm.total_kg, mm.total_kg, 0.0, True, "active_only", parts,
                           APP_MASS_BASIS + " " + PARTIAL_LABEL + ": " + why + ".", str(bom), why,
                           [ln.name for ln in others], list(bf.unmassed))
        else:
            out = FullMass(die, config, total, mm.total_kg, rot, False, "cad_bom", parts, FULL_MASS_BASIS, str(bom), "",
                           [ln.name for ln in others], list(bf.unmassed))
    if out.partial:
        out.rotating_kg = sum(p.mass_kg for p in out.parts if p.counted and p.mass_kg and p.key in ROTATING_KEYS)
    with _LOCK:
        if len(_FULL_CACHE) > 256:
            _FULL_CACHE.clear()
        _FULL_CACHE[key] = out
    return out


def full_mass(die: str, config: str, *, dies_dir: Optional[os.PathLike] = None,
              cad_dir: Optional[os.PathLike] = None) -> FullMass:
    """The FULL assembled motor mass of ``die`` / ``config`` (see the header).  Raises ``MotorMassError`` when the die
    is not available.  Without a built CAD BOM - or when the BOM cannot be accounted for (consistency guard) or the part rules
    are unavailable - the answer is the active mass with ``partial = True`` and the REASON in ``partial_reason``."""
    from motor_ai_sim.part_materials import PartMaterialsUnavailable
    try:
        return _full_mass_impl(die, config, dies_dir=dies_dir, cad_dir=cad_dir)
    except PartMaterialsUnavailable as exc:
        log.error("full mass of %s %s is PARTIAL: part-material rules unavailable: %s", die, config, exc)
        mm = app_motor_mass(die, config, dies_dir=dies_dir)
        why = f"the part-material rules are unavailable ({exc})"
        return FullMass(die, config, mm.total_kg, mm.total_kg, 0.0, True, "active_only", list(mm.parts),
                        APP_MASS_BASIS + " " + PARTIAL_LABEL + ": " + why, partial_reason=why)


def mass_summary(die: str, config: str, *, dies_dir: Optional[os.PathLike] = None,
                 cad_dir: Optional[os.PathLike] = None) -> Optional[Dict[str, Any]]:
    """The mass numbers every surface shows: ``{full_kg, active_kg, rotating_kg, partial, source}`` or ``None`` when the
    die is not available (the caller keeps what it had and flags it partial)."""
    try:
        fm = full_mass(die, config, dies_dir=dies_dir, cad_dir=cad_dir)
    except (MotorMassError, OSError):
        return None
    return {"full_kg": fm.total_kg, "active_kg": fm.active_kg, "rotating_kg": fm.rotating_kg, "partial": fm.partial,
            "partial_reason": fm.partial_reason, "others": list(fm.others), "unmassed": list(fm.unmassed),
            "source": fm.source, "non_active_kg": fm.total_kg - fm.active_kg}


def bom_extra_parts(die: str, config: str, *, cad_dir: Optional[os.PathLike] = None,
                    choices: Optional[Dict[str, str]] = None) -> List[MotorPart]:
    """The non-active parts of the CAD BOM (kept for callers that want only those): empty without a BOM."""
    ddir = find_die_dir(die)
    bom = find_bom_file(die, config, cad_dir)
    if not bom:
        return []
    from motor_ai_sim import part_materials as pm
    defs = pm.mechanical_parts()
    choices = choices or {}
    out: List[MotorPart] = []
    for ln in read_bom(bom):
        if _BOM_ACTIVE.search(ln.part) or _BOM_COIL_LINE.search(ln.part) or _BOM_SHAFT.search(ln.part):
            continue
        k = _classify(defs, ln.part)
        if not k:
            continue
        d = defs[k]
        default = str(d["material"])
        mat = choices.get(k) or default
        kg, note = _scaled(ln.grams, ln.density, mat, default)
        out.append(MotorPart(d["name"] if k != "holder" else ln.part.split(" (")[0], mat, kg, str(d["role"]), True,
                             str(d.get("status", "computed")), "CAD bill of materials" + note, key=k, em=False,
                             default_material_id=default, user_set=k in choices))
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
    """The whole motor, part by part (Materials tab "Full"): the parts of ``full_mass`` plus the insulation the app does
    not weigh.  A motor without a built BOM lists the active parts (partial) and what its configuration names (its
    bearings card, no mass) - no part is invented.  Raises ``MotorMassError`` when the die is not available."""
    from motor_ai_sim import part_materials as pm
    ddir = find_die_dir(die, dies_dir)
    fm = full_mass(die, config, dies_dir=dies_dir, cad_dir=cad_dir)
    choices = die_part_choices(ddir)
    rows: List[MotorPart] = list(fm.parts)
    try:
        cy = yaml.safe_load((ddir / f"{config}.yaml").read_text(encoding="utf-8")) or {}
    except Exception:                                                      # noqa: BLE001
        cy = {}
    have = {p.key for p in rows}
    for akey, name in (("slot_insulation", "Slot insulation"), ("wire_insulation", "Wire enamel")):
        mat = (cy.get("materials") or {}).get(akey)
        if mat and akey not in have:
            rows.append(MotorPart(name, str(mat), None, "insulation", False, "computed",
                                  "assigned for the simulation; no mass is known for it", key=akey))
    source = fm.source
    if fm.partial and config_bearing_cards(ddir, config):
        d = pm.mechanical_parts()["bearings"]
        rows.append(MotorPart("Bearings", choices.get("bearings") or d["material"], None, d["role"], False,
                              d.get("status", "computed"),
                              "named by the configuration's bearings; no CAD bill of materials built yet, so no mass",
                              key="bearings", em=False, default_material_id=d["material"], user_set="bearings" in choices))
        source = "active_only+config"
    return {"die": die, "config": config, "source": source, "partial": fm.partial, "total_kg": fm.total_kg,
            "active_kg": fm.active_kg, "rotating_kg": fm.rotating_kg, "mass_basis": fm.basis,
            "parts": [dict(key=p.key, name=p.name, role=p.role, em=p.em, material_id=p.material_id,
                           default_material_id=p.default_material_id, user_set=p.user_set, mass_kg=p.mass_kg,
                           counted=p.counted, status=p.status, basis=p.basis) for p in rows]}
