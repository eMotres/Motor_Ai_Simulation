# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Part materials: the material card behind a component part (docs/COMPONENT_CARD.md section 7).

A ``ComponentCard`` lists its parts (name, ``material_id``, mass, role).  ``resolve(material_id)``
turns the id into a ``PartMaterial``: density, Young's modulus, Poisson ratio, yield and ultimate
strength, fatigue limit, thermal expansion, conductivity, specific heat and maximum service
temperature - each property with its SOURCE, its type (standard / datasheet / handbook / derived /
estimate) and a ``verify`` flag.  A property nobody has a source for is ``None`` and listed under
``missing``; nothing is guessed.

Where the cards come from:

  1. ``config/materials_library.yaml`` - the solver's library, read raw and read-only: every
     steel, magnet, conductor and insulator card (stator steel 20SW1200, magnets F52SH_120C,
     copper, 42CrMo4, Aluminium_6061, T800_UD_60, Nomex, ...).  One card per material: the same
     record the solver and the masses use.  The library's own ``sources`` / ``prov`` give the
     source of each value (a field without a ``prov`` entry is ``datasheet`` from ``sources[0]``).
  2. ``config/part_materials.yaml`` - the cards a product needs that the library does not have
     (carbon-fibre laminate, glass-filled nylon, A2-70 stainless, 100Cr6, FR-4, a Li-ion pouch
     cell) and ``library_extensions``: values the library cards lack (ultimate strength, fatigue
     limit, service temperature).  Extensions only FILL gaps; the library wins.

Nothing here is copied from a commercial FEM package's material library (repo rule).  This module
imports nothing from the rest of motor_ai_sim except the raw library loader.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

#: property name -> unit, in display order.
PROPERTIES: Dict[str, str] = {
    "density_kg_m3": "kg/m3",
    "youngs_modulus_GPa": "GPa",
    "poisson_ratio": "-",
    "yield_MPa": "MPa",
    "ultimate_MPa": "MPa",
    "fatigue_limit_MPa": "MPa",
    "cte_ppm_K": "ppm/K",
    "conductivity_W_mK": "W/(m K)",
    "specific_heat_J_kgK": "J/(kg K)",
    "max_service_temp_C": "degC",
}

#: library field(s) feeding each property (first one present wins).
_LIBRARY_FIELDS: Dict[str, tuple] = {
    "density_kg_m3": ("density",),
    "youngs_modulus_GPa": ("youngs_modulus_gpa",),
    "poisson_ratio": ("poisson_ratio",),
    "yield_MPa": ("yield_strength_mpa",),
    "ultimate_MPa": ("tensile_strength_mpa",),
    "cte_ppm_K": ("cte_ppm_k", "cte_ppm_k_1"),
    "conductivity_W_mK": ("thermal_conductivity",),
    "specific_heat_J_kgK": ("specific_heat",),
    "max_service_temp_C": ("max_service_temp_c", "max_working_temp_c"),
}

SourceType = Literal["standard", "datasheet", "handbook", "derived", "estimate"]

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"
_PART_FILE = _REPO_CONFIG / "part_materials.yaml"
#: the API image keeps config/ as a read-only SEED at <app>/config-seed (deploy/Dockerfile.api), never at <app>/config
_SEED_FILE = Path(__file__).resolve().parents[2] / "config-seed" / "part_materials.yaml"


class PartMaterialsUnavailable(FileNotFoundError):
    """``part_materials.yaml`` is nowhere to be found.  LOUD on purpose: an empty answer here once let the server weigh a
    motor without a single holder, bearing or screw (every mechanical part silently fell out of the list)."""


class PropertyValue(BaseModel):
    """One number with its origin."""

    model_config = ConfigDict(extra="forbid")

    value: float
    unit: str
    source: str = Field(description="source id; its text is in PartMaterial.sources")
    type: SourceType
    verify: bool = False
    note: str = ""


class PartMaterial(BaseModel):
    """The resolved material card of a part."""

    model_config = ConfigDict(extra="forbid")

    material_id: str
    name: str
    category: str
    origin: Literal["materials_library", "part_materials"]
    library_category: Optional[str] = None
    props: Dict[str, PropertyValue] = Field(default_factory=dict)
    missing: List[str] = Field(default_factory=list)
    sources: Dict[str, str] = Field(default_factory=dict)
    notes: str = ""

    def get(self, prop: str) -> Optional[float]:
        pv = self.props.get(prop)
        return pv.value if pv is not None else None

    @property
    def density_kg_m3(self) -> float:
        d = self.get("density_kg_m3")
        if d is None:
            raise ValueError(f"material {self.material_id!r} has no density")
        return d


class UnknownPartMaterial(KeyError):
    """A ``material_id`` that no card resolves."""


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}


def part_file_candidates() -> List[Path]:
    """Where ``part_materials.yaml`` is looked for, in order: ``MOTRES_PART_MATERIALS``; the shared catalog root (like the
    materials library); the repository's ``config/``; the image's ``config-seed/``; beside the process's config file."""
    out: List[Path] = []
    env = os.environ.get("MOTRES_PART_MATERIALS", "").strip()
    if env:
        return [Path(env)]
    try:
        from .workspace import shared_root
        out.append(Path(str(shared_root())) / "part_materials.yaml")
    except Exception:                                                  # noqa: BLE001
        pass
    out += [_PART_FILE, _SEED_FILE]
    try:
        from .config import DEFAULT_CONFIG_PATH
        out.append(Path(str(DEFAULT_CONFIG_PATH)).parent / "part_materials.yaml")
    except Exception:                                                  # noqa: BLE001
        pass
    return out


def _part_file() -> Path:
    cands = part_file_candidates()
    for c in cands:
        if c.is_file():
            return c
    raise PartMaterialsUnavailable("part_materials.yaml not found (looked in: " + "; ".join(str(c) for c in cands) + ")")


def _part_doc() -> dict:
    p = _part_file()
    key = f"{p}:{p.stat().st_mtime_ns}"
    with _LOCK:
        if _CACHE.get("key") != key:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not (doc.get("materials") and doc.get("mechanical_parts")):
                raise PartMaterialsUnavailable(f"{p} has no materials / mechanical_parts section")
            _CACHE.update(key=key, doc=doc)
        return _CACHE["doc"]


def _library() -> dict:
    from . import materials as mat
    return mat._load()                                                 # noqa: SLF001 - raw, read-only


def _find_library_card(material_id: str):
    from . import materials as mat
    name = mat.canonical_material_name(material_id)
    lib = _library()
    for cat in ("steel", "magnet", "conductor", "insulator", "coolant"):
        card = (lib.get(cat) or {}).get(name)
        if isinstance(card, dict):
            return cat, name, card
    return None


def _prop(value: float, unit: str, src: str, typ: str, verify: bool, note: str = "") -> PropertyValue:
    return PropertyValue(value=float(value), unit=unit, source=src, type=typ, verify=bool(verify), note=note)


def _extension_props(doc: dict, ext_id: str, props: Dict[str, PropertyValue], sources: Dict[str, str],
                     *, fill_only: bool) -> None:
    src_doc = doc.get("sources") or {}
    for pname, spec in (ext_id and ((doc.get("library_extensions") or {}).get(ext_id) or {}).get("props") or {}).items():
        _put(pname, spec, props, sources, src_doc, fill_only)


def _put(pname: str, spec: dict, props: Dict[str, PropertyValue], sources: Dict[str, str], src_doc: dict,
         fill_only: bool) -> None:
    if pname not in PROPERTIES:
        raise ValueError(f"unknown material property {pname!r}")
    if fill_only and pname in props:
        return
    sid = str(spec["src"])
    if sid not in src_doc:
        raise ValueError(f"property {pname}: unknown source id {sid!r}")
    sources[sid] = str(src_doc[sid].get("doc", ""))
    props[pname] = _prop(spec["v"], PROPERTIES[pname], sid, spec.get("type", "estimate"), spec.get("verify", True),
                         spec.get("note", ""))


def _from_library(material_id: str, found) -> PartMaterial:
    cat, name, card = found
    props: Dict[str, PropertyValue] = {}
    sources: Dict[str, str] = {}
    lib_sources = {s.get("id"): s for s in (card.get("sources") or []) if isinstance(s, dict)}
    first = next(iter(lib_sources), None)
    prov = card.get("prov") or {}
    for pname, fields in _LIBRARY_FIELDS.items():
        for f in fields:
            if card.get(f) is None:
                continue
            p = prov.get(f) or {}
            typ = str(p.get("type") or "datasheet")             # envelope rule: no prov entry = datasheet, sources[0]
            sid = p.get("src")
            if sid in lib_sources:
                doc = str(lib_sources[sid].get("doc", ""))
            elif typ != "estimate" and first is not None:
                sid, doc = first, str(lib_sources[first].get("doc", ""))
            else:
                sid, doc = "library_estimate", ("materials_library.yaml flags this figure as an estimate "
                                                "without an independent source")
            note = str(p.get("note") or "")
            if f == "cte_ppm_k_1":
                note = (note + "; along the fibres / magnetisation axis (cte_ppm_k_1)").strip("; ")
            sources[sid] = doc
            props[pname] = _prop(card[f], PROPERTIES[pname], sid,
                                 typ if typ in ("standard", "datasheet", "handbook", "derived", "estimate")
                                 else "datasheet", bool(p.get("verify", False)), note)
            break
    doc = _part_doc()
    _extension_props(doc, name, props, sources, fill_only=True)
    return PartMaterial(
        material_id=material_id, name=str(card.get("description") or name), category=cat,
        origin="materials_library", library_category=cat, props=props,
        missing=[p for p in PROPERTIES if p not in props], sources=sources,
        notes=str(card.get("source_note") or card.get("note") or ""))


def _from_part_file(material_id: str, spec: dict, doc: dict) -> PartMaterial:
    props: Dict[str, PropertyValue] = {}
    sources: Dict[str, str] = {}
    for pname, ps in (spec.get("props") or {}).items():
        _put(pname, ps, props, sources, doc.get("sources") or {}, fill_only=False)
    return PartMaterial(
        material_id=material_id, name=str(spec.get("name") or material_id), category=str(spec.get("category") or "other"),
        origin="part_materials", props=props, missing=[p for p in PROPERTIES if p not in props], sources=sources,
        notes=str(spec.get("notes") or ""))


def resolve(material_id: str) -> PartMaterial:
    """The material card of ``material_id`` (a part_materials.yaml id or a materials-library name).
    Raises ``UnknownPartMaterial`` - an unresolvable id is never replaced by a default."""
    mid = str(material_id)
    doc = _part_doc()
    spec = (doc.get("materials") or {}).get(mid)
    if spec is not None:
        return _from_part_file(mid, spec, doc)
    found = _find_library_card(mid)
    if found is not None:
        return _from_library(mid, found)
    raise UnknownPartMaterial(f"no material card {mid!r} (not in part_materials.yaml nor in the materials library)")


def exists(material_id: str) -> bool:
    try:
        resolve(material_id)
        return True
    except UnknownPartMaterial:
        return False


def list_ids() -> Dict[str, List[str]]:
    """``{"part_materials": [...], "materials_library": [...]}``."""
    lib = _library()
    names: List[str] = []
    for cat in ("steel", "magnet", "conductor", "insulator"):
        names += list((lib.get(cat) or {}).keys())
    return {"part_materials": sorted((_part_doc().get("materials") or {}).keys()), "materials_library": sorted(names)}


# ---------------------------------------------------------------------------
# the non-active parts of an assembled motor (Materials tab, "Full")
# ---------------------------------------------------------------------------

def mechanical_parts() -> Dict[str, Dict[str, Any]]:
    """``{part_key: {name, match, role, material, status, note}}`` - the documented default material of every
    non-active motor part the CAD bill of materials can list (part_materials.yaml ``mechanical_parts``)."""
    return {k: dict(v) for k, v in (_part_doc().get("mechanical_parts") or {}).items()}


#: picker group of a material id (the library category, or the category of a part_materials.yaml card)
_GROUP_OF_CATEGORY = {"steel": "steel", "magnet": "magnet", "conductor": "metal", "insulator": "insulator",
                      "coolant": "coolant", "stainless_steel": "metal", "aluminium": "metal", "laminate": "insulator",
                      "composite": "insulator", "polymer": "insulator", "cell": "other"}


def choices() -> List[Dict[str, Any]]:
    """Every material a part may be given, for a picker: ``{id, name, group, origin, density_kg_m3}``.  Coolants are
    not offered for a solid part."""
    out: List[Dict[str, Any]] = []
    ids = list_ids()
    for origin in ("part_materials", "materials_library"):
        for mid in ids[origin]:
            try:
                card = resolve(mid)
            except UnknownPartMaterial:
                continue
            group = _GROUP_OF_CATEGORY.get(card.category, "other")
            if group == "coolant" or card.get("density_kg_m3") is None:
                continue
            out.append({"id": mid, "name": card.name, "group": group, "origin": origin,
                        "density_kg_m3": card.get("density_kg_m3")})
    return out
