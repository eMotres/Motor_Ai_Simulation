# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Gear material library and S-N life-curve parameters (ISO 6336-5 / ISO 6336-6).

For stage 1 (no edits outside ``src/motor_ai_sim/gears/``), standard cited gear
steel cards are built into ``BUILTIN_GEAR_MATERIALS``. If ``config/gear_materials.yaml``
or an explicit YAML path is present, cards from that file are loaded and merged
with the same schema. Standard ISO 6336-5 tables are never copied wholesale;
individual representative material-grade cards carry explicit citations.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .common import DictResultMixin

_log = logging.getLogger(__name__)

_DEFAULT_YAML_PATH = Path(__file__).resolve().parents[3] / "config" / "gear_materials.yaml"


class UnknownGearMaterialError(KeyError):
    """Raised when a requested gear material card does not exist."""


@dataclass(frozen=True)
class GearMaterial(DictResultMixin):
    """Mechanical, fatigue, and thermal properties for one gear or shaft steel.

    Notes
    -----
    - ``sigma_Flim_MPa`` is the nominal bending fatigue limit (ISO 6336-5)
      *before* multiplying by the reference test-gear factor ``Y_ST = 2.0``.
      The basic bending endurance strength is ``sigma_FE_MPa = 2.0 * sigma_Flim_MPa``.
    - ``sigma_Hlim_MPa`` is the contact (pitting) endurance limit (ISO 6336-5).
    """

    name: str
    designation: str
    heat_treatment: str          # "case_carburised" | "through_hardened" | "nitrided" | "induction_hardened" | "normalized"
    quality_class: str           # "ME" | "MQ" | "ML"
    sigma_Hlim_MPa: float
    sigma_Flim_MPa: float
    yield_strength_MPa: float
    uts_MPa: float
    E_MPa: float = 206000.0
    nu: float = 0.30
    density_kg_m3: float = 7850.0
    hardness_HV: Optional[float] = None
    hardness_HRC: Optional[float] = None
    thermal_conductivity_W_mK: float = 42.0
    specific_heat_J_kgK: float = 460.0

    # ISO 6336-2 / -3 / -6 life curve anchors
    N_H_static: float = 1.0e5
    N_H_ref: float = 5.0e7
    N_H_endurance_max: float = 1.0e10
    Z_NT_static: float = 1.6
    Z_NT_endurance_floor: float = 0.85

    N_F_static: float = 1.0e3
    N_F_ref: float = 3.0e6
    N_F_endurance_max: float = 1.0e10
    Y_NT_static: float = 2.5
    Y_NT_endurance_floor: float = 0.85

    sources: List[Dict[str, Any]] = field(default_factory=list)
    prov: Dict[str, Any] = field(default_factory=dict)
    method: str = "GearMaterialCard_ISO6336_5"
    flags: List[str] = field(default_factory=list)

    @property
    def sigma_FE_MPa(self) -> float:
        """Basic bending endurance strength = sigma_Flim * Y_ST (Y_ST = 2.0, ISO 6336-3 cl. 5)."""
        return 2.0 * self.sigma_Flim_MPa

    def z_nt(self, cycles: float, *, endurance_floor: Optional[float] = None) -> float:
        """Contact life factor Z_NT(N_L) per ISO 6336-2 Method B / ISO 6336-6.

        Log-linear interpolation on (log10 N, log10 Z_NT) between static plateau
        ``N_H_static`` and reference knee ``N_H_ref``, then shallow endurance
        branch down to ``endurance_floor`` (default ``self.Z_NT_endurance_floor``)
        at ``N_H_endurance_max``.
        """
        n = max(float(cycles), 1.0)
        floor = self.Z_NT_endurance_floor if endurance_floor is None else float(endurance_floor)
        if n <= self.N_H_static:
            return float(self.Z_NT_static)
        if n <= self.N_H_ref:
            t = (math.log10(n) - math.log10(self.N_H_static)) / (
                math.log10(self.N_H_ref) - math.log10(self.N_H_static)
            )
            return float(self.Z_NT_static ** (1.0 - t))
        if n >= self.N_H_endurance_max:
            return floor
        if floor >= 1.0:
            return 1.0
        t = (math.log10(n) - math.log10(self.N_H_ref)) / (
            math.log10(self.N_H_endurance_max) - math.log10(self.N_H_ref)
        )
        return float(floor ** t)

    def y_nt(self, cycles: float, *, endurance_floor: Optional[float] = None) -> float:
        """Tooth-root bending life factor Y_NT(N_L) per ISO 6336-3 cl. 13 / ISO 6336-6."""
        n = max(float(cycles), 1.0)
        floor = self.Y_NT_endurance_floor if endurance_floor is None else float(endurance_floor)
        if n <= self.N_F_static:
            return float(self.Y_NT_static)
        if n <= self.N_F_ref:
            t = (math.log10(n) - math.log10(self.N_F_static)) / (
                math.log10(self.N_F_ref) - math.log10(self.N_F_static)
            )
            return float(self.Y_NT_static ** (1.0 - t))
        if n >= self.N_F_endurance_max:
            return floor
        if floor >= 1.0:
            return 1.0
        t = (math.log10(n) - math.log10(self.N_F_ref)) / (
            math.log10(self.N_F_endurance_max) - math.log10(self.N_F_ref)
        )
        return float(floor ** t)

    def cycles_for_contact_stress(
        self,
        sigma_H_MPa: float,
        *,
        allowable_factor: float = 1.0,
        endurance_floor: Optional[float] = None,
    ) -> Optional[float]:
        """Invert Z_NT(N) = sigma_H / (sigma_Hlim * allowable_factor) to cycles N.

        ``allowable_factor`` bundles Z_L * Z_v * Z_R * Z_W * Z_X (or 1.0 for pure
        reference S-N check). Returns None above the static plateau; returns ``math.inf`` if stress is below the endurance
        floor (when ``floor >= 1.0`` or stress <= floor * sigma_Hlim * allowable_factor).
        """
        s = float(sigma_H_MPa)
        if s <= 0.0:
            return math.inf
        s_ref = self.sigma_Hlim_MPa * float(allowable_factor)
        z_req = s / s_ref
        floor = self.Z_NT_endurance_floor if endurance_floor is None else float(endurance_floor)
        if z_req <= floor:
            return math.inf
        if z_req > self.Z_NT_static:
            return None  # Above the static plateau: no S-N inverse exists.
        if z_req >= 1.0:
            t = 1.0 - math.log10(z_req) / math.log10(self.Z_NT_static)
            log_n = math.log10(self.N_H_static) + t * (
                math.log10(self.N_H_ref) - math.log10(self.N_H_static)
            )
            return 10.0 ** log_n
        # Between 1.0 and floor (ISO 6336-6 modified Miner shallow branch)
        t = math.log10(z_req) / math.log10(floor)
        log_n = math.log10(self.N_H_ref) + t * (
            math.log10(self.N_H_endurance_max) - math.log10(self.N_H_ref)
        )
        return 10.0 ** log_n

    def cycles_for_bending_stress(
        self,
        sigma_F_MPa: float,
        *,
        allowable_factor: float = 1.0,
        endurance_floor: Optional[float] = None,
    ) -> Optional[float]:
        """Invert Y_NT(N) = sigma_F / (sigma_FE * allowable_factor) to cycles N.

        ``allowable_factor`` bundles Y_drelT * Y_RrelT * Y_X (and reverse-bending
        factor if applicable). Returns None above the static plateau.
        """
        s = float(sigma_F_MPa)
        if s <= 0.0:
            return math.inf
        s_ref = self.sigma_FE_MPa * float(allowable_factor)
        y_req = s / s_ref
        floor = self.Y_NT_endurance_floor if endurance_floor is None else float(endurance_floor)
        if y_req <= floor:
            return math.inf
        if y_req > self.Y_NT_static:
            return None  # Above the static plateau: no S-N inverse exists.
        if y_req >= 1.0:
            t = 1.0 - math.log10(y_req) / math.log10(self.Y_NT_static)
            log_n = math.log10(self.N_F_static) + t * (
                math.log10(self.N_F_ref) - math.log10(self.N_F_static)
            )
            return 10.0 ** log_n
        t = math.log10(y_req) / math.log10(floor)
        log_n = math.log10(self.N_F_ref) + t * (
            math.log10(self.N_F_endurance_max) - math.log10(self.N_F_ref)
        )
        return 10.0 ** log_n


_ISO6336_5_SRC = {
    "id": "iso6336_5",
    "doc": "ISO 6336-5:2016 Calculation of load capacity of spur and helical gears - Part 5: Strength and quality of materials",
}
_NIEMANN_SRC = {
    "id": "niemann_winter",
    "doc": "Niemann, Winter, Maschinenelemente Band 2 (Springer): gear and shaft steel properties",
}

BUILTIN_GEAR_MATERIALS: Dict[str, GearMaterial] = {
    "18CrNiMo7-6_case_carburised_MQ": GearMaterial(
        name="18CrNiMo7-6_case_carburised_MQ",
        designation="18CrNiMo7-6",
        heat_treatment="case_carburised",
        quality_class="MQ",
        sigma_Hlim_MPa=1500.0,
        sigma_Flim_MPa=430.0,
        yield_strength_MPa=850.0,
        uts_MPa=1200.0,
        hardness_HV=690.0,
        hardness_HRC=60.0,
        N_H_static=1.0e5,
        N_H_ref=5.0e7,
        Z_NT_static=1.6,
        Z_NT_endurance_floor=0.85,
        N_F_static=1.0e3,
        N_F_ref=3.0e6,
        Y_NT_static=2.5,
        Y_NT_endurance_floor=0.85,
        sources=[_ISO6336_5_SRC, _NIEMANN_SRC],
        prov={
            "sigma_Hlim_MPa": {"type": "standard", "src": "iso6336_5", "note": "Case-carburised steel MQ representative value"},
            "sigma_Flim_MPa": {"type": "standard", "src": "iso6336_5", "note": "Case-carburised steel MQ representative value (before Y_ST=2)"},
        },
    ),
    "16MnCr5_case_carburised_MQ": GearMaterial(
        name="16MnCr5_case_carburised_MQ",
        designation="16MnCr5",
        heat_treatment="case_carburised",
        quality_class="MQ",
        sigma_Hlim_MPa=1450.0,
        sigma_Flim_MPa=420.0,
        yield_strength_MPa=700.0,
        uts_MPa=1000.0,
        hardness_HV=670.0,
        hardness_HRC=59.0,
        N_H_static=1.0e5,
        N_H_ref=5.0e7,
        Z_NT_static=1.6,
        Z_NT_endurance_floor=0.85,
        N_F_static=1.0e3,
        N_F_ref=3.0e6,
        Y_NT_static=2.5,
        Y_NT_endurance_floor=0.85,
        sources=[_ISO6336_5_SRC, _NIEMANN_SRC],
    ),
    "42CrMo4_through_hardened_MQ": GearMaterial(
        name="42CrMo4_through_hardened_MQ",
        designation="42CrMo4",
        heat_treatment="through_hardened",
        quality_class="MQ",
        sigma_Hlim_MPa=730.0,
        sigma_Flim_MPa=310.0,
        yield_strength_MPa=750.0,
        uts_MPa=1000.0,
        hardness_HV=300.0,
        hardness_HRC=30.0,
        N_H_static=1.0e5,
        N_H_ref=5.0e7,
        Z_NT_static=1.6,
        Z_NT_endurance_floor=0.85,
        N_F_static=1.0e3,
        N_F_ref=3.0e6,
        Y_NT_static=2.5,
        Y_NT_endurance_floor=0.85,
        sources=[_ISO6336_5_SRC, _NIEMANN_SRC],
    ),
    "42CrMo4_nitrided_MQ": GearMaterial(
        name="42CrMo4_nitrided_MQ",
        designation="42CrMo4 (nitrided)",
        heat_treatment="nitrided",
        quality_class="MQ",
        sigma_Hlim_MPa=1000.0,
        sigma_Flim_MPa=380.0,
        yield_strength_MPa=750.0,
        uts_MPa=1000.0,
        hardness_HV=550.0,
        hardness_HRC=52.0,
        N_H_static=1.0e5,
        N_H_ref=2.0e6,
        Z_NT_static=1.3,
        Z_NT_endurance_floor=0.85,
        N_F_static=1.0e3,
        N_F_ref=3.0e6,
        Y_NT_static=1.6,
        Y_NT_endurance_floor=0.85,
        sources=[_ISO6336_5_SRC],
    ),
    "31CrMoV9_nitrided_MQ": GearMaterial(
        name="31CrMoV9_nitrided_MQ",
        designation="31CrMoV9",
        heat_treatment="nitrided",
        quality_class="MQ",
        sigma_Hlim_MPa=1200.0,
        sigma_Flim_MPa=420.0,
        yield_strength_MPa=850.0,
        uts_MPa=1100.0,
        hardness_HV=650.0,
        hardness_HRC=58.0,
        N_H_static=1.0e5,
        N_H_ref=2.0e6,
        Z_NT_static=1.3,
        Z_NT_endurance_floor=0.85,
        N_F_static=1.0e3,
        N_F_ref=3.0e6,
        Y_NT_static=1.6,
        Y_NT_endurance_floor=0.85,
        sources=[_ISO6336_5_SRC],
    ),
    "42CrMo4_induction_MQ": GearMaterial(
        name="42CrMo4_induction_MQ",
        designation="42CrMo4 (induction)",
        heat_treatment="induction_hardened",
        quality_class="MQ",
        sigma_Hlim_MPa=1180.0,
        sigma_Flim_MPa=360.0,
        yield_strength_MPa=750.0,
        uts_MPa=1000.0,
        hardness_HV=580.0,
        hardness_HRC=54.0,
        N_H_static=1.0e5,
        N_H_ref=1.0e7,
        Z_NT_static=1.5,
        Z_NT_endurance_floor=0.85,
        N_F_static=1.0e3,
        N_F_ref=3.0e6,
        Y_NT_static=2.2,
        Y_NT_endurance_floor=0.85,
        sources=[_ISO6336_5_SRC],
    ),
    "C45_normalized_ML": GearMaterial(
        name="C45_normalized_ML",
        designation="C45",
        heat_treatment="normalized",
        quality_class="ML",
        sigma_Hlim_MPa=420.0,
        sigma_Flim_MPa=190.0,
        yield_strength_MPa=340.0,
        uts_MPa=600.0,
        hardness_HV=180.0,
        N_H_static=1.0e5,
        N_H_ref=1.0e7,
        Z_NT_static=1.6,
        Z_NT_endurance_floor=0.85,
        N_F_static=1.0e4,
        N_F_ref=3.0e6,
        Y_NT_static=2.0,
        Y_NT_endurance_floor=0.85,
        sources=[_ISO6336_5_SRC, _NIEMANN_SRC],
    ),
}


def _load_yaml_materials(path: Path) -> Dict[str, GearMaterial]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must parse to a YAML mapping")
    cards_raw = raw.get("gear_materials", raw.get("materials", raw))
    if not isinstance(cards_raw, dict):
        return {}
    known = {f.name for f in GearMaterial.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    out: Dict[str, GearMaterial] = {}
    for name, body in cards_raw.items():
        if not isinstance(body, dict) or "sigma_Hlim_MPa" not in body:
            continue
        kw = {k: v for k, v in body.items() if k in known}
        kw["name"] = str(name)
        out[str(name)] = GearMaterial(**kw)
    return out


def list_gear_materials(yaml_path: Optional[Path] = None) -> List[str]:
    """Return sorted names of all available gear material cards."""
    merged = dict(BUILTIN_GEAR_MATERIALS)
    merged.update(_load_yaml_materials(yaml_path or _DEFAULT_YAML_PATH))
    return sorted(merged.keys())


def get_gear_material(name: str, yaml_path: Optional[Path] = None) -> GearMaterial:
    """Return the named ``GearMaterial`` card or raise ``UnknownGearMaterialError``."""
    merged = dict(BUILTIN_GEAR_MATERIALS)
    merged.update(_load_yaml_materials(yaml_path or _DEFAULT_YAML_PATH))
    if name not in merged:
        raise UnknownGearMaterialError(
            f"Unknown gear material {name!r}; available: {', '.join(sorted(merged))}"
        )
    return merged[name]
