# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Top-level Gearbox stages, multi-stage chains, spectrum evaluation,
reflected kinetic-energy inertia, stiffness/backlash/mass properties,
catalogue gearbox checks, and ``TransmissionIR`` export.
"""
from __future__ import annotations

import math
import uuid
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from motor_ai_sim.contracts import ModelSource, ShaftInterface, TransmissionIR, TransmissionLimits, Validity

from .bearings import (
    BearingLifeResult,
    BearingSpectrumLifeResult,
    GearBearingSpec,
    evaluate_bearing,
    evaluate_bearing_spectrum,
)
from .common import DictResultMixin, ValidityState, worst_state, require_number, validate_fields
from .geometry import GearSpec, PairGeometryResult, analyze_pair
from .kinematics import (
    ChainKinematicsResult,
    StageKinematicsResult,
    chain_kinematics,
    pair_kinematics,
    planetary_kinematics,
)
from .loads import BearingReactionLoad, MeshForces, compute_mesh_forces, planet_bearing_reaction, shaft_bearing_split
from .losses import (
    MeshLossResult,
    PlanetaryEfficiencyResult,
    compute_mesh_loss,
    oil_churning_loss_W,
    solve_planetary_power_flow,
)
from .lubrication import (
    GearLubricant,
    GreaseThermalBalanceResult,
    get_gear_lubricant,
    solve_grease_thermal_balance,
)
from .planetary_equilibrium import solve_local_planetary

_PLANETARY_MAX_ITER = 80
_PLANETARY_TOL = 1.e-11

from .materials import GearMaterial, get_gear_material
from .parts import (
    CarrierSpec,
    KeyOrSplineSpec,
    PartStrengthRecord,
    PartsSummaryResult,
    PlanetPinSpec,
    RingRimFixationSpec,
    ShaftSectionSpec,
    rate_carrier,
    rate_key_or_spline,
    rate_planet_pin,
    rate_ring_rim_and_fixation,
    rate_shaft_din743,
    summarize_part_strengths,
)
from .peaks import StaticStrengthLimits, PeakMemberResult, rate_peak_member
from .presets import Preset, PresetEvaluationResult, evaluate_against_preset, get_preset
from .rating_iso6336 import (
    MinerDamageResult,
    PairRatingResult,
    evaluate_gear_miner_spectrum,
    miner_damage_from_cycles,
    rate_pair_iso6336,
)


# ---------------------------------------------------------------------------
# Duty spectrum & motor-duty fallback (OWNER_DECISIONS.md)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DutyBin(DictResultMixin):
    """Single operating-point bin in a duty spectrum (referred to output shaft by default)."""

    torque_out_Nm: float
    speed_out_rpm: float
    duration_h: float
    ambient_temp_C: float = 25.0
    label: str = ""
    method: str = "DutyBin"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for fname in ("torque_out_Nm", "speed_out_rpm", "duration_h", "ambient_temp_C"):
            val = require_number(fname, getattr(self, fname))
            if not math.isfinite(val):
                raise ValueError(f"{fname} must be finite, got {val!r}")
        if self.duration_h < 0.0:
            raise ValueError(f"duration_h must be >= 0, got {self.duration_h}")


@dataclass(frozen=True)
class DutySpectrum(DictResultMixin):
    """Load spectrum driving gearbox fatigue life and operating ratings."""

    bins: List[DutyBin]
    peak_torque_out_Nm: Optional[float] = None
    peak_events: int = 1
    peak_speed_out_rpm: Optional[float] = None
    peak_member_engagements: Dict[str, int] = field(default_factory=dict)
    static_strength_limits: Dict[str, StaticStrengthLimits] = field(default_factory=dict)
    peak_reversing: bool = False
    source: str = "spectrum"               # "spectrum" | "motor_duties_fallback" | "preset_default_shape"
    method: str = "DutySpectrum"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.bins:
            raise ValueError("DutySpectrum bins must be non-empty")
        if any(not isinstance(b, DutyBin) for b in self.bins):
            raise ValueError("bins must contain DutyBin objects")
        if math.fsum(b.duration_h for b in self.bins) <= 0:
            raise ValueError("DutySpectrum total duration must be positive")
        if self.peak_torque_out_Nm is not None:
            require_number("peak_torque_out_Nm", self.peak_torque_out_Nm, minimum=0)
        require_number("peak_events", self.peak_events, minimum=0, integer=True)
        if self.peak_speed_out_rpm is not None: require_number("peak_speed_out_rpm",self.peak_speed_out_rpm)
        if not isinstance(self.peak_reversing,bool): raise ValueError("peak_reversing must be boolean")
        if not isinstance(self.peak_member_engagements,dict) or not isinstance(self.static_strength_limits,dict):
            raise ValueError("Peak member inputs must be dictionaries keyed by stable member ID")
        for key,value in self.peak_member_engagements.items():
            if not isinstance(key,str) or not key: raise ValueError("Member ID required")
            require_number("peak engagements",value,integer=True,minimum=0)
        if any(not isinstance(v,StaticStrengthLimits) for v in self.static_strength_limits.values()):
            raise ValueError("static_strength_limits must contain StaticStrengthLimits")



def spectrum_from_motor_duties(
    *,
    continuous_torque_in_Nm: float,
    continuous_speed_in_rpm: float,
    gearbox_ratio: float,
    assumed_efficiency: float = 0.95,
    continuous_hours: float = 9500.0,
    peak_torque_in_Nm: Optional[float] = None,
    peak_speed_in_rpm: Optional[float] = None,
    peak_hours: float = 500.0,
) -> DutySpectrum:
    """Fallback builder from motor continuous/peak duties when no project spectrum exists."""
    bins = [
        DutyBin(
            torque_out_Nm=continuous_torque_in_Nm * gearbox_ratio * assumed_efficiency,
            speed_out_rpm=continuous_speed_in_rpm / gearbox_ratio,
            duration_h=continuous_hours,
            label="motor_continuous_fallback",
        )
    ]
    peak_out: Optional[float] = None
    if peak_torque_in_Nm is not None:
        spd = peak_speed_in_rpm if peak_speed_in_rpm is not None else continuous_speed_in_rpm
        peak_out = peak_torque_in_Nm * gearbox_ratio * assumed_efficiency
        bins.append(
            DutyBin(
                torque_out_Nm=peak_out,
                speed_out_rpm=spd / gearbox_ratio,
                duration_h=peak_hours,
                label="motor_peak_fallback",
            )
        )
    return DutySpectrum(
        bins=bins,
        peak_torque_out_Nm=peak_out,
        peak_events=1,
        source="motor_duties_fallback",
        flags=["motor_duties_fallback_used_no_project_spectrum"],
    )


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

@dataclass
class PairStageSpec(DictResultMixin):
    """Single external or internal spur/helical gear stage."""

    name: str
    gear1: GearSpec
    gear2: GearSpec
    centre_distance_mm: Optional[float] = None
    bearing_1a: Optional[GearBearingSpec] = None
    bearing_1b: Optional[GearBearingSpec] = None
    bearing_2a: Optional[GearBearingSpec] = None
    bearing_2b: Optional[GearBearingSpec] = None
    shaft1_span_mm: Optional[float] = None
    shaft1_offset_mm: Optional[float] = None
    shaft2_span_mm: Optional[float] = None
    shaft2_offset_mm: Optional[float] = None
    shaft1_section: Optional[ShaftSectionSpec] = None
    shaft2_section: Optional[ShaftSectionSpec] = None
    key_or_spline_1: Optional[KeyOrSplineSpec] = None
    key_or_spline_2: Optional[KeyOrSplineSpec] = None
    immersion_depth_mm: float = 0.0
    grease_drag_torque_Nm: Optional[float] = None
    kind: str = "spur_pair"
    method: str = "PairStageSpec"
    flags: List[str] = field(default_factory=list)

    stage_id: str = field(default_factory=lambda: "stage_" + uuid.uuid4().hex)

    def __post_init__(self):
        from typing import get_type_hints, get_args
        for field_name, hint in get_type_hints(type(self)).items():
            value = getattr(self, field_name, None)
            expected = next((tp for tp in get_args(hint) if isinstance(tp, type) and issubclass(tp, DictResultMixin)), None)
            if expected is not None and value is not None and not isinstance(value, expected):
                raise ValueError(f"{field_name} must be {expected.__name__}")
        if not isinstance(self.stage_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.stage_id):
            raise ValueError("stage_id must contain letters, numbers, underscore or hyphen")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Stage name must be nonempty")
        if not isinstance(self.gear1, GearSpec) or not isinstance(self.gear2, GearSpec):
            raise ValueError("Pair members must be GearSpec")
        if self.kind not in ("spur_pair", "helical_pair", "internal_pair"):
            raise ValueError("Unknown pair stage kind")
        validate_fields(self, positive=("centre_distance_mm", "shaft1_span_mm", "shaft2_span_mm"),
                        nonnegative=("immersion_depth_mm", "grease_drag_torque_Nm"),
                        signed=("shaft1_offset_mm", "shaft2_offset_mm"))


@dataclass
class PlanetaryStageSpec(DictResultMixin):
    """Simple or stepped-planet (compound) planetary stage."""

    name: str
    sun: GearSpec
    planet: GearSpec
    ring: GearSpec
    n_planets: int = 3
    planet2: Optional[GearSpec] = None      # stepped-planet ring-side gear (compound_planetary)
    fixed_member: str = "ring"
    input_member: str = "sun"
    output_member: str = "carrier"
    k_gamma: Optional[float] = None
    floating_member: bool = True
    planet_bearing: Optional[GearBearingSpec] = None
    n_bearings_per_planet: int = 1
    planet_bearing_spacing_mm: float = 0.0
    input_bearing: Optional[GearBearingSpec] = None
    output_bearing: Optional[GearBearingSpec] = None
    planet_pin: Optional[PlanetPinSpec] = None
    carrier: Optional[CarrierSpec] = None
    ring_rim_fixation: Optional[RingRimFixationSpec] = None
    input_shaft_section: Optional[ShaftSectionSpec] = None
    output_shaft_section: Optional[ShaftSectionSpec] = None
    key_or_spline_in: Optional[KeyOrSplineSpec] = None
    key_or_spline_out: Optional[KeyOrSplineSpec] = None
    input_external_radial_N: float = 0.
    output_external_radial_N: float = 0.
    input_external_axial_N: float = 0.
    output_external_axial_N: float = 0.
    input_shaft_span_mm: Optional[float] = None
    output_shaft_span_mm: Optional[float] = None
    carrier_inertia_kg_m2: float = 0.0    # carrier body ONLY (excludes planet masses)
    carrier_mass_kg: float = 0.0
    immersion_depth_mm: float = 0.0
    grease_drag_torque_Nm: Optional[float] = None
    method: str = "PlanetaryStageSpec"
    flags: List[str] = field(default_factory=list)

    stage_id: str = field(default_factory=lambda: "stage_" + uuid.uuid4().hex)

    def __post_init__(self):
        from typing import get_type_hints, get_args
        for field_name, hint in get_type_hints(type(self)).items():
            value = getattr(self, field_name, None)
            expected = next((tp for tp in get_args(hint) if isinstance(tp, type) and issubclass(tp, DictResultMixin)), None)
            if expected is not None and value is not None and not isinstance(value, expected):
                raise ValueError(f"{field_name} must be {expected.__name__}")
        if not isinstance(self.stage_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.stage_id):
            raise ValueError("stage_id must contain letters, numbers, underscore or hyphen")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Stage name must be nonempty")
        if not all(isinstance(g, GearSpec) for g in (self.sun, self.planet, self.ring)) or (self.planet2 is not None and not isinstance(self.planet2, GearSpec)):
            raise ValueError("Planetary members must be GearSpec")
        if self.sun.is_internal or self.planet.is_internal or not self.ring.is_internal or (self.planet2 is not None and self.planet2.is_internal):
            raise ValueError("Only ring may be internal in planetary stage")
        if set((self.fixed_member, self.input_member, self.output_member)) != {"sun", "ring", "carrier"}:
            raise ValueError("Fixed/input/output members must be distinct sun/ring/carrier")
        validate_fields(self, counts=("n_planets", "n_bearings_per_planet"),
                        nonnegative=("planet_bearing_spacing_mm", "carrier_inertia_kg_m2", "carrier_mass_kg",
                                     "immersion_depth_mm", "grease_drag_torque_Nm"))
        validate_fields(self,nonnegative=("input_external_radial_N","output_external_radial_N",
                        "input_external_axial_N","output_external_axial_N"),
                        positive=("input_shaft_span_mm","output_shaft_span_mm"))
        if self.k_gamma is not None:
            require_number("k_gamma", self.k_gamma, minimum=1.)
        if not isinstance(self.floating_member, bool):
            raise ValueError("floating_member must be boolean")
        if self.planet_pin is not None and self.planet_bearing_spacing_mm > self.planet_pin.span_mm:
            raise ValueError("Planet bearing spacing exceeds pin support span")
        if self.n_bearings_per_planet > 2:
            raise ValueError("Only one or two planet bearing seats are supported")

    @property
    def kind(self) -> str:
        return "compound_planetary" if self.planet2 is not None else "planetary"


StageSpec = Union[PairStageSpec, PlanetaryStageSpec]


def stage_member_ids(stage):
    roles = (["gear1", "gear2"] if isinstance(stage, PairStageSpec) else
             ["sun", "ring", "carrier", "planet1" if stage.planet2 else "planet"]
             + (["planet2"] if stage.planet2 else []))
    return {role: f"{stage.stage_id}.{role}" for role in roles}


@dataclass
class StageEvaluationResult(DictResultMixin):
    """Complete mechanical, efficiency, strength, and bearing results at one operating point."""

    stage_name: str
    stage_kind: str
    kinematics: StageKinematicsResult
    pair_geometries: Dict[str, PairGeometryResult]
    mesh_forces: Dict[str, MeshForces]
    mesh_losses: Dict[str, MeshLossResult]
    planetary_power_flow: Optional[PlanetaryEfficiencyResult]
    bearing_loads: List[BearingReactionLoad]
    bearing_ratings: List[BearingLifeResult]
    pair_ratings: Dict[str, PairRatingResult]
    member_torques_Nm: Dict[str, float]
    power_in_W: float
    power_out_useful_W: float
    power_loss_mesh_W: float
    power_loss_bearings_W: float
    power_loss_churning_drag_W: float
    power_loss_total_W: float
    efficiency: Optional[float]
    torsional_stiffness_out_Nm_rad: float
    backlash_out_deg: float
    backlash_out_arcmin: float
    mass_kg: float
    reflected_inertia_local_in_kg_m2: float
    stage_id: str = ""
    member_ids: Dict[str, str] = field(default_factory=dict)
    state: ValidityState = "estimated"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "StageEvaluation"
    flags: List[str] = field(default_factory=list)


@dataclass
class GearboxEvaluationResult(DictResultMixin):
    """Full gearbox chain evaluation at one operating point or across a spectrum."""

    gearbox_name: str
    preset_name: str
    duty_source: str
    overall_ratio: float
    abs_overall_ratio: float
    reverses_direction: bool
    stage_results: List[StageEvaluationResult]
    chain_kinematics: ChainKinematicsResult
    input_torque_Nm: float
    output_torque_Nm: float
    input_speed_rpm: float
    output_speed_rpm: float
    power_in_W: float
    power_out_useful_W: float
    power_loss_W: float
    overall_efficiency: Optional[float]
    reverse_efficiency: Optional[float]
    backlash_out_deg: float
    backlash_out_arcmin: float
    torsional_stiffness_out_Nm_rad: float
    reflected_inertia_in_kg_m2: float
    mass_kg: float
    envelope_diameter_mm: float
    envelope_length_mm: float
    gear_miner_lives: List[MinerDamageResult] = field(default_factory=list)
    bearing_spectrum_lives: List[BearingSpectrumLifeResult] = field(default_factory=list)
    peak_member_ratings: List[PeakMemberResult] = field(default_factory=list)
    bin_evaluations: List[GearboxEvaluationResult] = field(default_factory=list)
    parts_summary: Optional[PartsSummaryResult] = None
    thermal_balance: Optional[GreaseThermalBalanceResult] = None
    preset_evaluation: Optional[PresetEvaluationResult] = None
    min_gear_life_h: Optional[float] = None
    min_bearing_life_h: Optional[float] = None
    state: ValidityState = "estimated"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "GearboxChainEvaluation"
    flags: List[str] = field(default_factory=list)
    passed: bool = False
    pass_reasons: List[str] = field(default_factory=list)

    def __post_init__(self):
        reasons = []
        if self.state in ("failed", "unsupported"):
            reasons.append("overall_state:" + self.state)
        if self.preset_evaluation is None:
            reasons.append("preset_not_evaluated")
        else:
            reasons.extend("preset:" + c.criterion + ":" + c.state
                for c in self.preset_evaluation.checks if not c.passed)
            if not self.preset_evaluation.all_passed and not reasons:
                reasons.append("preset_not_passed")
        if self.parts_summary is None:
            reasons.append("parts_not_evaluated")
        else:
            reasons.extend("part:" + p.part_name + ":" + p.state
                for p in self.parts_summary.parts if not p.passed or p.state in ("failed", "unsupported"))
            if not self.parts_summary.all_passed:
                reasons.append("parts_not_passed")
        self.pass_reasons = sorted(set(reasons))
        self.passed = not self.pass_reasons


# ---------------------------------------------------------------------------
# Catalogue Gearbox (OWNER_DECISIONS.md)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogueGearboxSpec(DictResultMixin):
    """Datasheet-defined catalogue gearbox (checked against limits, no invented ISO 6336 rating)."""

    name: str
    ratio: float                          # > 0 magnitude (or signed)
    efficiency: float                     # (0, 1]
    rated_torque_out_Nm: float            # continuous / nominal output rating
    peak_torque_out_Nm: float             # intermittent / E-stop output limit
    rated_speed_in_max_rpm: float
    rated_life_h: float                   # life at (rated_torque_out_Nm, rated_speed_out_rpm)
    rated_speed_out_rpm: float
    backlash_arcmin: Optional[float] = None
    mass_kg: float = 1.0
    inertia_in_kg_m2: float = 1.0e-5
    inertia_out_kg_m2: float = 1.0e-4
    backdrivable: Optional[bool] = None
    reverse_efficiency: Optional[float] = None
    torque_life_exponent: Optional[float] = None
    source_doc: str = "manufacturer_datasheet"
    method: str = "CatalogueGearboxSpec"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for fname in (
            "ratio", "efficiency", "rated_torque_out_Nm", "peak_torque_out_Nm",
            "rated_speed_in_max_rpm", "rated_life_h", "rated_speed_out_rpm",
            "mass_kg", "inertia_in_kg_m2", "inertia_out_kg_m2",
        ):
            val = float(getattr(self, fname))
            if not math.isfinite(val):
                raise ValueError(f"{fname} must be finite, got {val!r}")
        if self.ratio == 0.0:
            raise ValueError("CatalogueGearboxSpec ratio must be non-zero")
        if not (0.0 < self.efficiency <= 1.0):
            raise ValueError(f"CatalogueGearboxSpec efficiency must be in (0, 1], got {self.efficiency}")
        if (
            self.rated_torque_out_Nm <= 0.0
            or self.peak_torque_out_Nm <= 0.0
            or self.rated_speed_in_max_rpm <= 0.0
            or self.rated_life_h <= 0.0
            or self.rated_speed_out_rpm <= 0.0
            or self.mass_kg <= 0.0
        ):
            raise ValueError("CatalogueGearboxSpec torque, speed, life, and mass ratings must be > 0")
        if self.peak_torque_out_Nm < self.rated_torque_out_Nm:
            raise ValueError("peak_torque_out_Nm must be >= rated_torque_out_Nm")
        if self.inertia_in_kg_m2 < 0.0 or self.inertia_out_kg_m2 < 0.0:
            raise ValueError("Inertias must be >= 0")
        if self.backlash_arcmin is not None and (
            not math.isfinite(float(self.backlash_arcmin)) or self.backlash_arcmin < 0.0
        ):
            raise ValueError("backlash_arcmin must be finite and >= 0")
        if self.reverse_efficiency is not None and (
            not math.isfinite(float(self.reverse_efficiency)) or not (0.0 < self.reverse_efficiency <= 1.0)
        ):
            raise ValueError(f"reverse_efficiency must be in (0, 1], got {self.reverse_efficiency!r}")
        if self.torque_life_exponent is not None and (
            not math.isfinite(float(self.torque_life_exponent)) or self.torque_life_exponent <= 0.0
        ):
            raise ValueError("torque_life_exponent must be finite and > 0")


@dataclass
class CatalogueGearboxCheckResult(DictResultMixin):
    """Result of checking a ``CatalogueGearboxSpec`` against a ``DutySpectrum``."""

    gearbox_name: str
    preset_name: str
    duty_source: str
    equivalent_torque_out_Nm: float
    equivalent_speed_out_rpm: float
    max_torque_out_Nm: float
    max_speed_in_rpm: float
    predicted_life_h: Optional[float]
    torque_utilization: float
    peak_utilization: float
    speed_utilization: float
    passed: bool
    preset_evaluation: PresetEvaluationResult
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "CatalogueGearbox_CubeRootMeanCheck"
    flags: List[str] = field(default_factory=list)


def check_catalogue_gearbox(
    spec: CatalogueGearboxSpec,
    spectrum: DutySpectrum,
    *,
    preset_name: str = "standard",
) -> CatalogueGearboxCheckResult:
    """Check a catalogue gearbox datasheet against a load spectrum.

    Equivalent output torque uses the speed-weighted power-mean rule with
    ``spec.torque_life_exponent`` (or assumed cube-root-mean exponent ``3.0``,
    flagged as ``"estimated"`` when not specified on the datasheet).
    Peak torque, speed, backlash, and back-drivability are checked strictly
    against both datasheet limits and ``preset`` requirements.
    """
    preset = get_preset(preset_name)
    flags: List[str] = list(spectrum.flags)
    missing: List[str] = []
    if not spectrum.bins:
        raise ValueError("DutySpectrum must have at least one bin")

    if spec.torque_life_exponent is None:
        p_life = 3.0
        exponent_assumed = True
        flags.append("assumed_catalogue_torque_life_exponent_3")
        missing.append("torque_life_exponent")
    else:
        p_life = float(spec.torque_life_exponent)
        if p_life <= 0.0:
            raise ValueError(f"torque_life_exponent must be > 0, got {p_life}")
        exponent_assumed = False

    tot_t = sum(max(b.duration_h, 0.0) for b in spectrum.bins)
    tot_revs_weight = sum(max(b.duration_h, 0.0) * abs(b.speed_out_rpm) for b in spectrum.bins)
    if tot_t <= 0.0:
        raise ValueError("Spectrum total duration_h must be > 0")

    if tot_revs_weight > 0.0:
        t_eq_p = math.fsum(sorted([
            max(b.duration_h, 0.0) * abs(b.speed_out_rpm) * (abs(b.torque_out_Nm) ** p_life)
            for b in spectrum.bins
        ])) / tot_revs_weight
        n_eq = tot_revs_weight / tot_t
    else:
        t_eq_p = math.fsum(sorted([
            max(b.duration_h, 0.0) * (abs(b.torque_out_Nm) ** p_life)
            for b in spectrum.bins
        ])) / tot_t
        n_eq = 0.0
        flags.append("standstill_spectrum_time_weighted_power_mean")

    t_eq = t_eq_p ** (1.0 / p_life)
    max_t = max([abs(b.torque_out_Nm) for b in spectrum.bins] + ([abs(spectrum.peak_torque_out_Nm)] if spectrum.peak_torque_out_Nm is not None else []))
    max_n_in = max(abs(b.speed_out_rpm) * abs(spec.ratio) for b in spectrum.bins)

    if t_eq > 0.0 and n_eq > 0.0:
        l_pred = spec.rated_life_h * ((spec.rated_torque_out_Nm / t_eq) ** p_life) * (spec.rated_speed_out_rpm / n_eq)
    else:
        l_pred = None
        flags.append("zero_torque_or_speed_life_undefined")

    t_util = t_eq / spec.rated_torque_out_Nm
    p_util = max_t / spec.peak_torque_out_Nm
    s_util = max_n_in / spec.rated_speed_in_max_rpm

    life_ok = (l_pred >= preset.required_life_h) if l_pred is not None else False
    if p_util > 1.0:
        flags.append("exceeds_datasheet_peak_torque")
    if s_util > 1.0:
        flags.append("exceeds_datasheet_max_input_speed")

    # Never substitute forward efficiency when reverse_efficiency is omitted.
    rev_eff_for_preset = spec.reverse_efficiency
    # Map datasheet metrics into preset checks (catalogue has no ISO 6336 S_F/S_H)
    preset_eval = evaluate_against_preset(
        preset,
        min_S_F_duty=None,
        min_S_H_duty=None,
        S_F_duty_state="unsupported",
        S_H_duty_state="unsupported",
        min_S_F_static=None,
        min_S_H_static=None,
        gear_life_h=l_pred,
        gear_life_state=("valid" if l_pred is not None else "unsupported"),
        bearing_life_h=l_pred,
        bearing_life_state=("valid" if l_pred is not None else "unsupported"),
        min_bearing_S0=None,
        backlash_arcmin=spec.backlash_arcmin,
        reverse_efficiency=rev_eff_for_preset,
        backdrivable=spec.backdrivable,
    )

    backlash_ok = (preset.max_backlash_arcmin is None) or (
        spec.backlash_arcmin is not None and spec.backlash_arcmin <= preset.max_backlash_arcmin
    )
    backdrive_unsupported = False
    if preset.require_backdrivable:
        if spec.backdrivable is False:
            backdrive_ok = False
            flags.append("non_backdrivable_fails_preset_requirement")
        elif spec.backdrivable is None or rev_eff_for_preset is None:
            backdrive_ok = False
            backdrive_unsupported = True
            if spec.backdrivable is None:
                missing.append("backdrivable")
            if rev_eff_for_preset is None:
                missing.append("reverse_efficiency")
            flags.append("missing_backdrivable_verification_for_preset")
        elif preset.min_reverse_efficiency is not None and rev_eff_for_preset < preset.min_reverse_efficiency:
            backdrive_ok = False
            flags.append("reverse_efficiency_below_preset_limit")
        else:
            backdrive_ok = True
    else:
        backdrive_ok = True

    if preset.max_backlash_arcmin is not None and spec.backlash_arcmin is None:
        missing.append("backlash_arcmin")

    passed = (
        (t_util <= 1.0)
        and (p_util <= 1.0)
        and (s_util <= 1.0)
        and life_ok
        and backlash_ok
        and backdrive_ok
    )
    if l_pred is None or backdrive_unsupported or (preset.max_backlash_arcmin is not None and spec.backlash_arcmin is None):
        state: ValidityState = "unsupported"
        passed = False
    elif not passed:
        state = "failed"
    elif exponent_assumed:
        state = "estimated"
    else:
        state = "valid"

    return CatalogueGearboxCheckResult(
        gearbox_name=spec.name,
        preset_name=preset.name,
        duty_source=spectrum.source,
        equivalent_torque_out_Nm=t_eq,
        equivalent_speed_out_rpm=n_eq,
        max_torque_out_Nm=max_t,
        max_speed_in_rpm=max_n_in,
        predicted_life_h=l_pred,
        torque_utilization=t_util,
        peak_utilization=p_util,
        speed_utilization=s_util,
        passed=passed,
        preset_evaluation=preset_eval,
        state=state,
        applicability_evidence={
            "source_doc": spec.source_doc,
            "rule": "speed-weighted power-mean equivalent torque; peak checked against datasheet limit",
            "torque_life_exponent": p_life,
            "torque_life_exponent_assumed": exponent_assumed,
            "no_iso6336_rating_for_catalogue_unit": True,
        },
        missing_inputs=missing,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Cylinder inertia / mass helpers
# ---------------------------------------------------------------------------

def _gear_cylinder_mass_and_inertia(spec: GearSpec, mat: GearMaterial) -> Tuple[float, float]:
    """Return (mass_kg, polar_inertia_kg_m2) of a cylindrical gear blank."""
    m_t = spec.m_n_mm / math.cos(math.radians(abs(spec.beta_deg)))
    d_m = spec.z * m_t * 1.0e-3
    b_m = spec.b_mm * 1.0e-3
    if not spec.is_internal:
        r_out = 0.5 * d_m
        r_in = 0.5 * (spec.bore_diameter_mm * 1.0e-3) if spec.bore_diameter_mm else 0.0
    else:
        r_in = 0.5 * d_m
        rim = spec.rim_thickness_mm if spec.rim_thickness_mm is not None else 2.5 * spec.m_n_mm
        r_out = r_in + (spec.hf_star * spec.m_n_mm + rim) * 1.0e-3
    r_out = max(r_out, r_in + 1.0e-4)
    vol = math.pi * (r_out ** 2 - r_in ** 2) * b_m
    mass = mat.density_kg_m3 * vol
    inertia = 0.5 * mass * (r_out ** 2 + r_in ** 2)
    return mass, inertia


# ---------------------------------------------------------------------------
# Stage Evaluator & Gearbox Chain
# ---------------------------------------------------------------------------

def evaluate_stage(
    stage: StageSpec,
    *,
    input_speed_rpm: float,
    known_torque_Nm: float,
    known_torque_member: Optional[str] = None,
    lubricant: GearLubricant,
    temp_C: float = 70.0,
    K_A: float = 1.0,
    member_cycles: Optional[Dict[str, float]] = None,
    required_life_h: float = 10000.0,
    min_S_H: float = 1.1,
    min_S_F: float = 1.5,
) -> StageEvaluationResult:
    """Evaluate one stage (geometry, kinematics, signed loads, losses, ISO 6336, bearings, properties)."""
    stage.__post_init__()
    require_number("input_speed_rpm", input_speed_rpm)
    require_number("known_torque_Nm", known_torque_Nm)
    require_number("required_life_h", required_life_h, positive=True)
    if member_cycles is not None:
        for key, value in member_cycles.items(): require_number(key, value, minimum=0.)
    if known_torque_member is not None and known_torque_member not in (("gear1", "gear2") if isinstance(stage, PairStageSpec) else ("sun", "ring", "carrier")):
        raise ValueError("Unknown torque member")
    if isinstance(stage, PairStageSpec):
        return _evaluate_pair_stage(
            stage,
            input_speed_rpm=input_speed_rpm,
            known_torque_Nm=known_torque_Nm,
            known_torque_member=known_torque_member or "gear1",
            lubricant=lubricant,
            temp_C=temp_C,
            K_A=K_A,
            member_cycles=member_cycles,
            required_life_h=required_life_h,
            min_S_H=min_S_H,
            min_S_F=min_S_F,
        )
    if isinstance(stage, PlanetaryStageSpec):
        return _evaluate_planetary_stage(
            stage,
            input_speed_rpm=input_speed_rpm,
            known_torque_Nm=known_torque_Nm,
            known_torque_member=known_torque_member or stage.input_member,
            lubricant=lubricant,
            temp_C=temp_C,
            K_A=K_A,
            member_cycles=member_cycles,
            required_life_h=required_life_h,
            min_S_H=min_S_H,
            min_S_F=min_S_F,
        )
    raise TypeError(f"Unsupported stage type {type(stage).__name__}")


def _evaluate_pair_stage(
    stage: PairStageSpec,
    *,
    input_speed_rpm: float,
    known_torque_Nm: float,
    known_torque_member: str,
    lubricant: GearLubricant,
    temp_C: float,
    K_A: float,
    member_cycles: Optional[Dict[str, float]],
    required_life_h: float,
    min_S_H: float,
    min_S_F: float,
) -> StageEvaluationResult:
    geom = analyze_pair(stage.gear1, stage.gear2, centre_distance_mm=stage.centre_distance_mm)
    kin = pair_kinematics(stage.gear1, stage.gear2, input_speed_rpm=input_speed_rpm, pair_geom=geom)
    flags: List[str] = list(kin.flags)
    missing: List[str] = []

    w1 = kin.member_speeds_rpm["gear1"] * math.pi / 30.0
    w2 = kin.member_speeds_rpm["gear2"] * math.pi / 30.0
    nu_op = lubricant.kinematic_viscosity_mm2_s(temp_C)
    brg_map = {
        "input_A": stage.bearing_1a,
        "input_B": stage.bearing_1b,
        "output_A": stage.bearing_2a,
        "output_B": stage.bearing_2b,
    }

    def _eval_pair_losses(t1_mesh_mag_Nm: float) -> Tuple[MeshForces, MeshLossResult, List[BearingReactionLoad], List[BearingLifeResult], float, float, float, float]:
        mf_i = compute_mesh_forces(
            mesh_name="main_mesh",
            pair_geom=geom,
            total_torque_gear1_Nm=t1_mesh_mag_Nm,
            gear1_rel_speed_rpm=kin.member_speeds_rpm["gear1"],
            n_parallel_paths=1,
            k_gamma=1.0,
        )
        p_mesh_i = abs(t1_mesh_mag_Nm * w1)
        ml_i = compute_mesh_loss(
            mesh_name="main_mesh",
            pair_geom=geom,
            mesh_forces=mf_i,
            transmitted_power_rel_W=p_mesh_i,
            lubricant=lubricant,
            temp_C=temp_C,
            Ra1_um=stage.gear1.Ra_flank_um,
            Ra2_um=stage.gear2.Ra_flank_um,
        )
        bl_i: List[BearingReactionLoad] = []
        br_i: List[BearingLifeResult] = []
        p_brg_in = 0.0
        p_brg_out = 0.0
        if stage.bearing_1a or stage.bearing_1b:
            bl_i.extend(
                shaft_bearing_split(
                    f_t_mesh_N=mf_i.F_tw_N,
                    f_r_mesh_N=mf_i.F_r_N,
                    f_a_mesh_N=mf_i.F_a_N,
                    rpm=kin.member_speeds_rpm["gear1"],
                    span_mm=stage.shaft1_span_mm,
                    gear_offset_from_a_mm=stage.shaft1_offset_mm,
                    role_prefix="input",
                )
            )
        if stage.bearing_2a or stage.bearing_2b:
            bl_i.extend(
                shaft_bearing_split(
                    f_t_mesh_N=mf_i.F_tw_N,
                    f_r_mesh_N=mf_i.F_r_N,
                    f_a_mesh_N=mf_i.F_a_N,
                    rpm=kin.member_speeds_rpm["gear2"],
                    span_mm=stage.shaft2_span_mm,
                    gear_offset_from_a_mm=stage.shaft2_offset_mm,
                    role_prefix="output",
                )
            )
        for bl in bl_i:
            bspec = brg_map.get(bl.role)
            if bspec is not None:
                br = evaluate_bearing(
                    bspec,
                    role=bl.role,
                    F_r_N=bl.F_r_worst_branch_N,
                    F_a_N=bl.F_a_worst_branch_N,
                    speed_rel_rpm=bl.speed_rpm,
                    nu_op_mm2_s=nu_op,
                    temp_C=temp_C,
                    lubrication=("grease" if lubricant.kind == "grease" else "oil_air"),
                    required_life_h=required_life_h,
                )
                br_i.append(br)
                if bl.role.startswith("input"):
                    p_brg_in += br.friction_power_W
                else:
                    p_brg_out += br.friction_power_W
        return mf_i, ml_i, bl_i, br_i, p_brg_in + p_brg_out, p_brg_in, p_brg_out, 0.0

    # Churning / grease drag
    if lubricant.kind == "oil":
        p_drag = oil_churning_loss_W(
            d_a_mm=geom.gear2.d_a_mm,
            b_mm=stage.gear2.b_mm,
            rpm=kin.member_speeds_rpm["gear2"],
            lubricant=lubricant,
            temp_C=temp_C,
            immersion_depth_mm=stage.immersion_depth_mm,
        )
    else:
        if stage.grease_drag_torque_Nm is not None:
            p_drag = abs(float(stage.grease_drag_torque_Nm) * w1)
            flags.append("grease_churning_estimate_from_input_drag")
        else:
            p_drag = 0.0
            flags.append("grease_drag_not_provided_assumed_zero")
            missing.append("grease_drag_torque_Nm")

    t1_nom_guess = abs(float(known_torque_Nm) if known_torque_member == "gear1" else (float(known_torque_Nm) / kin.ratio))
    if abs(w1) <= 1e-12:
        mf, ml, b_loads, b_ratings, p_brg_loss, _, _, _ = _eval_pair_losses(t1_nom_guess)
        p_loss_tot = ml.P_VZ_W + p_brg_loss + p_drag
        t1_signed = float(known_torque_Nm) if known_torque_member == "gear1" else (-float(known_torque_Nm) / kin.ratio)
        t2_signed = -t1_signed * kin.ratio
        p_in_w, p_out_w = 0.0, 0.0
        eff: Optional[float] = None
        flags.append("zero_speed_efficiency_undefined")
    else:
        p_known = (known_torque_Nm * w1) if known_torque_member == "gear1" else (known_torque_Nm * w2)
        is_forward = (known_torque_member == "gear1" and p_known >= 0.0) or (
            known_torque_member == "gear2" and p_known < 0.0
        )
        if is_forward and known_torque_member == "gear2":
            # Driven output torque specified: keep t2_signed == known_torque_Nm & P_out exact
            p_out_w = abs(known_torque_Nm * w2)
            t1_trial = t1_nom_guess
            for _ in range(3):
                mf, ml, b_loads, b_ratings, p_brg_loss, p_brg_in, p_brg_out, _ = _eval_pair_losses(t1_trial)
                p_drag_out = p_drag if lubricant.kind == "oil" else 0.0
                t1_trial = (p_out_w + p_brg_out + p_drag_out + ml.P_VZ_W) / abs(w1)
            mf, ml, b_loads, b_ratings, p_brg_loss, _, _, _ = _eval_pair_losses(t1_trial)
            p_loss_tot = ml.P_VZ_W + p_brg_loss + p_drag
            p_in_w = p_out_w + p_loss_tot
            t1_signed = math.copysign(p_in_w / abs(w1), w1)
            t2_signed = float(known_torque_Nm)
        elif (not is_forward) and known_torque_member == "gear1":
            # Reverse driven on gear1: keep t1_signed == known_torque_Nm & P_out exact
            p_out_w = abs(known_torque_Nm * w1)
            t1_trial = t1_nom_guess
            for _ in range(3):
                mf, ml, b_loads, b_ratings, p_brg_loss, p_brg_in, _, _ = _eval_pair_losses(t1_trial)
                t1_trial = (p_out_w + p_brg_in) / abs(w1)
            mf, ml, b_loads, b_ratings, p_brg_loss, _, _, _ = _eval_pair_losses(t1_trial)
            p_loss_tot = ml.P_VZ_W + p_brg_loss + p_drag
            p_in_w = p_out_w + p_loss_tot
            t2_signed = math.copysign(p_in_w / abs(w2), w2)
            t1_signed = float(known_torque_Nm)
        elif is_forward:
            # Forward driving specified ON gear1
            mf, ml, b_loads, b_ratings, p_brg_loss, p_brg_in, _, _ = _eval_pair_losses(t1_nom_guess)
            p_in_w = abs(known_torque_Nm * w1)
            p_drag_in = 0.0 if lubricant.kind == "oil" else p_drag
            t1_mesh_eff = max((p_in_w - p_brg_in - p_drag_in) / abs(w1), 0.0)
            if abs(t1_mesh_eff - t1_nom_guess) > 1e-9:
                mf, ml, b_loads, b_ratings, p_brg_loss, _, _, _ = _eval_pair_losses(t1_mesh_eff)
            p_loss_tot = ml.P_VZ_W + p_brg_loss + p_drag
            p_out_w = max(p_in_w - p_loss_tot, 0.0)
            t1_signed = float(known_torque_Nm)
            t2_signed = -math.copysign(p_out_w / abs(w2), w2)
        else:
            # Reverse driving specified ON gear2
            mf, ml, b_loads, b_ratings, p_brg_loss, _, p_brg_out, _ = _eval_pair_losses(t1_nom_guess)
            p_in_w = abs(known_torque_Nm * w2)
            p_drag_out = p_drag if lubricant.kind == "oil" else 0.0
            t1_mesh_eff = max((p_in_w - p_brg_out - p_drag_out - ml.P_VZ_W) / abs(w1), 0.0)
            if abs(t1_mesh_eff - t1_nom_guess) > 1e-9:
                mf, ml, b_loads, b_ratings, p_brg_loss, _, _, _ = _eval_pair_losses(t1_mesh_eff)
            p_loss_tot = ml.P_VZ_W + p_brg_loss + p_drag
            p_out_w = max(p_in_w - p_loss_tot, 0.0)
            t2_signed = float(known_torque_Nm)
            t1_signed = -math.copysign(p_out_w / abs(w1), w1)
        eff = (p_out_w / p_in_w) if p_in_w > 0.0 else None

    if ml.mesh_efficiency is None or ml.state in ("failed", "unsupported"):
        eff = None
        ml.state = worst_state([ml.state, "unsupported"])
        flags.append("mesh_efficiency_unavailable")

    rating = rate_pair_iso6336(
        mesh_name="main_mesh",
        pair_geom=geom,
        gear1_spec=stage.gear1,
        gear2_spec=stage.gear2,
        mesh_forces=mf,
        n1_rel_rpm=kin.member_speeds_rpm["gear1"],
        lubricant=lubricant,
        temp_C=temp_C,
        cycles_1=(member_cycles or {}).get("gear1", abs(kin.member_speeds_rpm["gear1"])*60.*required_life_h),
        cycles_2=(member_cycles or {}).get("gear2", abs(kin.member_speeds_rpm["gear2"])*60.*required_life_h),
        K_A=K_A,
        min_S_H=min_S_H,
        min_S_F=min_S_F,
    )

    # Properties: mesh-only stiffness, backlash, mass, inertia
    mat1 = get_gear_material(stage.gear1.material_name)
    mat2 = get_gear_material(stage.gear2.material_name)
    m1, j1 = _gear_cylinder_mass_and_inertia(stage.gear1, mat1)
    m2, j2 = _gear_cylinder_mass_and_inertia(stage.gear2, mat2)
    j_ref_in = j1 + j2 / (kin.ratio ** 2)

    b_eff = min(stage.gear1.b_mm, stage.gear2.b_mm)
    k_loa = rating.load_factors.c_gamma_alpha_N_mm_um * b_eff * 1.0e6  # N/m
    r_b2_m = 0.5 * geom.gear2.d_b_mm * 1.0e-3
    k_tors_out = k_loa * (r_b2_m ** 2)
    flags.append("mesh_stiffness_only_excludes_shafts_pins_housing")
    flags.append("estimated_blank_inertia_and_mass")

    st = worst_state([geom.state, ml.state, rating.state] + [br.state for br in b_ratings])
    if eff is None:
        st = worst_state([st, "unsupported"])

    return StageEvaluationResult(
        stage_name=stage.name,
        stage_id=stage.stage_id,
        member_ids=stage_member_ids(stage),
        stage_kind=stage.kind,
        kinematics=kin,
        pair_geometries={"main_mesh": geom},
        mesh_forces={"main_mesh": mf},
        mesh_losses={"main_mesh": ml},
        planetary_power_flow=None,
        bearing_loads=b_loads,
        bearing_ratings=b_ratings,
        pair_ratings={"main_mesh": rating},
        member_torques_Nm={"gear1": t1_signed, "gear2": t2_signed},
        power_in_W=p_in_w,
        power_out_useful_W=p_out_w,
        power_loss_mesh_W=ml.P_VZ_W,
        power_loss_bearings_W=p_brg_loss,
        power_loss_churning_drag_W=p_drag,
        power_loss_total_W=p_loss_tot,
        efficiency=eff,
        torsional_stiffness_out_Nm_rad=k_tors_out,
        backlash_out_deg=geom.angular_backlash_deg,
        backlash_out_arcmin=geom.angular_backlash_deg * 60.0,
        mass_kg=m1 + m2,
        reflected_inertia_local_in_kg_m2=j_ref_in,
        state=st,
        applicability_evidence={"mesh_efficiency": ml.mesh_efficiency},
        missing_inputs=sorted(set(missing + rating.missing_inputs)),
        flags=sorted(set(flags + ml.flags + rating.flags)),
    )


def _evaluate_planetary_stage(
    stage: PlanetaryStageSpec,
    *,
    input_speed_rpm: float,
    known_torque_Nm: float,
    known_torque_member: str,
    lubricant: GearLubricant,
    temp_C: float,
    K_A: float,
    member_cycles: Optional[Dict[str, float]],
    required_life_h: float,
    min_S_H: float,
    min_S_F: float,
) -> StageEvaluationResult:
    p_ring_gear = stage.planet2 if stage.planet2 is not None else stage.planet
    sp_geom = analyze_pair(stage.sun, stage.planet)
    pr_geom = analyze_pair(p_ring_gear, stage.ring)
    kin = planetary_kinematics(
        sun=stage.sun,
        planet=stage.planet,
        ring=stage.ring,
        n_planets=stage.n_planets,
        fixed_member=stage.fixed_member,
        input_member=stage.input_member,
        output_member=stage.output_member,
        input_speed_rpm=input_speed_rpm,
        planet2=stage.planet2,
        sun_planet_geom=sp_geom,
        planet_ring_geom=pr_geom,
    )
    flags: List[str] = list(kin.flags)
    missing: List[str] = []

    sp_rpm = kin.member_speeds_rpm
    w_s = sp_rpm["sun"] * math.pi / 30.0
    w_r = sp_rpm["ring"] * math.pi / 30.0
    w_c = sp_rpm["carrier"] * math.pi / 30.0
    v_s_rpm = sp_rpm["sun"] - sp_rpm["carrier"]
    v_r_rpm = sp_rpm["ring"] - sp_rpm["carrier"]
    w_p_rel_rpm = sp_rpm["planet_rel_carrier"]
    w_p_abs_rpm = sp_rpm["planet_abs"]

    # Iterative fixed-point convergence of planetary mesh torques, mesh losses,
    # carrier-frame power balance, and bearing friction (B3).
    u = kin.stationary_carrier_ratio_u or (stage.ring.z / stage.sun.z)
    if known_torque_member == "sun":
        t_s_est = float(known_torque_Nm)
    elif known_torque_member == "ring":
        t_s_est = float(known_torque_Nm) / u
    else:
        t_s_est = -float(known_torque_Nm) / (1.0 + u)

    if lubricant.kind == "oil":
        p_drag = oil_churning_loss_W(
            d_a_mm=sp_geom.gear2.d_a_mm,
            b_mm=stage.planet.b_mm,
            rpm=abs(w_c * 30.0 / math.pi) + abs(w_p_rel_rpm),
            lubricant=lubricant,
            temp_C=temp_C,
            immersion_depth_mm=stage.immersion_depth_mm,
        )
    else:
        if stage.grease_drag_torque_Nm is not None:
            w_in_abs = abs(sp_rpm[stage.input_member] * math.pi / 30.0)
            p_drag = abs(float(stage.grease_drag_torque_Nm)) * w_in_abs
            flags.append("grease_churning_estimate_from_input_drag")
        else:
            p_drag = 0.0
            flags.append("grease_drag_not_provided_assumed_zero")
            missing.append("grease_drag_torque_Nm")

    if p_drag > 0. and lubricant.kind == "oil":
        flags.append("estimated_oil_churning_reaction_allocated_to_input_member")
    nu_op = lubricant.kinematic_viscosity_mm2_s(temp_C)
    t_s_mesh = abs(t_s_est)
    t_p2_mesh = t_s_mesh * (stage.planet.z / stage.sun.z)
    w_s_rel_rad = abs(v_s_rpm * math.pi / 30.0)
    w_r_rel_rad = abs(v_r_rpm * math.pi / 30.0)
    p_sp_rel = t_s_mesh * w_s_rel_rad
    p_pr_rel = (t_s_mesh * u) * w_r_rel_rad
    prev_p_brg = -1.0

    def _member_axial_thrust_N(member: str, f_a_sp: float, f_a_pr: float) -> float:
        if member == "sun":
            return f_a_sp * stage.n_planets
        if member == "ring":
            return f_a_pr * stage.n_planets
        return abs(f_a_sp - f_a_pr) * stage.n_planets

    converged_fp = False
    fp_residual = math.inf
    for _iter in range(_PLANETARY_MAX_ITER):
        mf_sp = compute_mesh_forces(
            mesh_name="sun_planet",
            pair_geom=sp_geom,
            total_torque_gear1_Nm=t_s_mesh,
            gear1_rel_speed_rpm=v_s_rpm,
            n_parallel_paths=stage.n_planets,
            k_gamma=stage.k_gamma,
            floating_member=stage.floating_member,
        )
        mf_pr = compute_mesh_forces(
            mesh_name="planet_ring",
            pair_geom=pr_geom,
            total_torque_gear1_Nm=t_p2_mesh,
            gear1_rel_speed_rpm=w_p_rel_rpm,
            n_parallel_paths=stage.n_planets,
            k_gamma=mf_sp.k_gamma,
            floating_member=stage.floating_member,
        )
        ml_sp = compute_mesh_loss(
            mesh_name="sun_planet",
            pair_geom=sp_geom,
            mesh_forces=mf_sp,
            transmitted_power_rel_W=p_sp_rel,
            lubricant=lubricant,
            temp_C=temp_C,
            Ra1_um=stage.sun.Ra_flank_um,
            Ra2_um=stage.planet.Ra_flank_um,
        )
        ml_pr = compute_mesh_loss(
            mesh_name="planet_ring",
            pair_geom=pr_geom,
            mesh_forces=mf_pr,
            transmitted_power_rel_W=p_pr_rel,
            lubricant=lubricant,
            temp_C=temp_C,
            Ra1_um=p_ring_gear.Ra_flank_um,
            Ra2_um=stage.ring.Ra_flank_um,
        )

        eta_sp, eta_pr = ml_sp.mesh_efficiency, ml_pr.mesh_efficiency
        mesh_available = all(ml.mesh_efficiency is not None and
            0. < ml.mesh_efficiency <= 1. and ml.state not in ("failed", "unsupported")
            for ml in (ml_sp, ml_pr))
        eta0_mesh = eta_sp * eta_pr if mesh_available else None

        planet_brg_load = planet_bearing_reaction(
            mf_sp=mf_sp,
            mf_pr=mf_pr,
            planet_rel_rpm=w_p_rel_rpm,
            n_planets=stage.n_planets,
            d_w_planet_sp_mm=sp_geom.d_w2_mm,
            d_w_planet_pr_mm=pr_geom.d_w1_mm,
            n_bearings_per_planet=stage.n_bearings_per_planet,
            bearing_spacing_mm=stage.planet_bearing_spacing_mm,
        )
        b_loads: List[BearingReactionLoad] = [planet_brg_load]
        b_ratings: List[BearingLifeResult] = []
        p_planet_brg_loss = 0.0
        p_shaft_brg_loss = 0.0
        shaft_drag = {stage.input_member: p_drag}

        if stage.planet_bearing is not None:
            br_nom = evaluate_bearing(
                stage.planet_bearing,
                role="planet_nominal_for_loss",
                F_r_N=planet_brg_load.F_r_N,
                F_a_N=planet_brg_load.F_a_N,
                speed_rel_rpm=planet_brg_load.speed_rpm,
                nu_op_mm2_s=nu_op,
                temp_C=temp_C,
                lubrication=("grease" if lubricant.kind == "grease" else "oil_air"),
                required_life_h=required_life_h,
            )
            p_planet_brg_loss = br_nom.friction_power_W * planet_brg_load.n_identical_bearings
            br_worst = evaluate_bearing(
                stage.planet_bearing,
                role="planet",
                F_r_N=planet_brg_load.F_r_worst_branch_N,
                F_a_N=planet_brg_load.F_a_worst_branch_N,
                speed_rel_rpm=planet_brg_load.speed_rpm,
                nu_op_mm2_s=nu_op,
                temp_C=temp_C,
                lubrication=("grease" if lubricant.kind == "grease" else "oil_air"),
                required_life_h=required_life_h,
            )
            b_ratings.append(br_worst)

        vector_sum = math.hypot(math.fsum(math.cos(math.radians(a)) for a in kin.planet_angles_deg),
                                math.fsum(math.sin(math.radians(a)) for a in kin.planet_angles_deg))
        radial = {"sun":vector_sum*math.hypot(mf_sp.F_tw_N,mf_sp.F_r_N),
                  "ring":vector_sum*math.hypot(mf_pr.F_tw_N,mf_pr.F_r_N),
                  "carrier":vector_sum*math.hypot(mf_sp.F_tw_N+mf_pr.F_tw_N,mf_sp.F_r_N-mf_pr.F_r_N)}
        for role,member,bspec in (("input",stage.input_member,stage.input_bearing),
                                  ("output",stage.output_member,stage.output_bearing)):
            rpm=abs(sp_rpm[member])
            fr=radial[member]+getattr(stage,role+"_external_radial_N")
            fa=abs(_member_axial_thrust_N(member,mf_sp.F_a_N,mf_pr.F_a_N))+getattr(stage,role+"_external_axial_N")
            b_loads.append(BearingReactionLoad(role=role,F_r_N=fr,F_a_N=fa,
                F_r_worst_branch_N=fr,F_a_worst_branch_N=fa,speed_rpm=rpm,
                flags=["external_force_magnitudes_conservatively_aligned","nominal_equal_branch_share_radial_resultant"]))
            if bspec is not None:
                br=evaluate_bearing(bspec,role=role,F_r_N=fr,F_a_N=fa,speed_rel_rpm=rpm,
                    nu_op_mm2_s=nu_op,temp_C=temp_C,required_life_h=required_life_h)
                b_ratings.append(br)
                p_shaft_brg_loss += br.friction_power_W
                shaft_drag[member]=shaft_drag.get(member,0.)+br.friction_power_W

        p_brg_tot = p_planet_brg_loss + p_shaft_brg_loss

        if not mesh_available:
            from .planetary_equilibrium import unavailable_planetary_flow
            pf = unavailable_planetary_flow(u=u, omegas={"sun": w_s, "ring": w_r, "carrier": w_c},
                fixed_member=stage.fixed_member, known_member=known_torque_member,
                known_torque_Nm=known_torque_Nm,
                state=worst_state([ml_sp.state, ml_pr.state, "unsupported"]))
            break

        pf = solve_local_planetary(
            u=u, eta_sp=eta_sp, eta_pr=eta_pr,
            omegas={"sun": w_s, "ring": w_r, "carrier": w_c},
            planet_rel_rad_s=w_p_rel_rpm*math.pi/30.,
            fixed_member=stage.fixed_member, known_member=known_torque_member,
            known_torque_Nm=known_torque_Nm,
            planet_bearing_power_W=p_planet_brg_loss, shaft_drag_power_W=shaft_drag,
        )
        mesh_t_dict = pf.applicability_evidence.get("mesh_torques_Nm", pf.member_torques_Nm)
        if pf.active_branch == "standstill":
            t_s_new = abs(mesh_t_dict["sun"])
            t_p2_new = t_s_new * stage.planet.z / stage.sun.z
            p_sp_new = p_pr_new = 0.
        else:
            p_sp_new = max(pf.applicability_evidence["sun_planet_input_power_W"], 0.)
            p_pr_new = max(pf.applicability_evidence["planet_ring_input_power_W"], 0.)
            t_s_new = p_sp_new / w_s_rel_rad
            # Use input-side load at each mesh, including when the ring drives.
            t_p2_new = p_pr_new / abs(w_p_rel_rpm*math.pi/30.)
        fp_residual = max(abs(new-old)/max(1.,abs(new)) for new,old in (
            (t_s_new,t_s_mesh),(t_p2_new,t_p2_mesh),
            (p_sp_new,p_sp_rel),(p_pr_new,p_pr_rel),(p_brg_tot,prev_p_brg)))
        converged_fp = fp_residual <= _PLANETARY_TOL
        if converged_fp:
            break  # Returned forces/losses are from this checked iterate.
        t_s_mesh, t_p2_mesh, p_sp_rel, p_pr_rel = t_s_new, t_p2_new, p_sp_new, p_pr_new
        prev_p_brg = p_brg_tot

    pf.applicability_evidence.update({"converged": converged_fp, "iterations": _iter+1,
        "relative_iteration_residual": fp_residual, "iteration_tolerance": _PLANETARY_TOL})
    if not converged_fp and mesh_available:
        pf.state = "failed"
        pf.efficiency_total = None
        pf.flags.append("planetary_equilibrium_not_converged")

    r_sp = rate_pair_iso6336(
        mesh_name="sun_planet",
        pair_geom=sp_geom,
        gear1_spec=stage.sun,
        gear2_spec=stage.planet,
        mesh_forces=mf_sp,
        n1_rel_rpm=v_s_rpm,
        lubricant=lubricant,
        temp_C=temp_C,
        cycles_1=(member_cycles or {}).get("sun", stage.n_planets*abs(v_s_rpm)*60.*required_life_h),
        cycles_2=(member_cycles or {}).get("planet", abs(w_p_rel_rpm)*60.*required_life_h),
        K_A=K_A,
        gear2_is_planet_idler=True,
        min_S_H=min_S_H,
        min_S_F=min_S_F,
    )
    r_pr = rate_pair_iso6336(
        mesh_name="planet_ring",
        pair_geom=pr_geom,
        gear1_spec=p_ring_gear,
        gear2_spec=stage.ring,
        mesh_forces=mf_pr,
        n1_rel_rpm=w_p_rel_rpm,
        lubricant=lubricant,
        temp_C=temp_C,
        cycles_1=(member_cycles or {}).get("planet2" if stage.planet2 else "planet", abs(w_p_rel_rpm)*60.*required_life_h),
        cycles_2=(member_cycles or {}).get("ring", stage.n_planets*abs(v_r_rpm)*60.*required_life_h),
        K_A=K_A,
        gear1_is_planet_idler=True,
        min_S_H=min_S_H,
        min_S_F=min_S_F,
    )

    # Reflected inertia from kinetic energy (planet absolute spin + orbital m*a^2)
    mat_s = get_gear_material(stage.sun.material_name)
    mat_p = get_gear_material(stage.planet.material_name)
    mat_r = get_gear_material(stage.ring.material_name)
    m_s, j_s = _gear_cylinder_mass_and_inertia(stage.sun, mat_s)
    m_p1, j_p1 = _gear_cylinder_mass_and_inertia(stage.planet, mat_p)
    m_r, j_r = _gear_cylinder_mass_and_inertia(stage.ring, mat_r)
    if stage.planet2 is not None and stage.planet2 is not stage.planet:
        mat_p2 = get_gear_material(stage.planet2.material_name)
        m_p2, j_p2 = _gear_cylinder_mass_and_inertia(stage.planet2, mat_p2)
    else:
        m_p2, j_p2 = 0.0, 0.0
    m_p_tot = m_p1 + m_p2
    j_p_tot = j_p1 + j_p2

    w_in = sp_rpm[stage.input_member]
    if abs(w_in) > 1e-12:
        a_orb_m = sp_geom.a_w_mm * 1.0e-3
        j_ref_in = (
            j_s * ((sp_rpm["sun"] / w_in) ** 2)
            + j_r * ((sp_rpm["ring"] / w_in) ** 2)
            + stage.carrier_inertia_kg_m2 * ((sp_rpm["carrier"] / w_in) ** 2)
            + stage.n_planets * (
                j_p_tot * ((w_p_abs_rpm / w_in) ** 2)
                + m_p_tot * (a_orb_m ** 2) * ((sp_rpm["carrier"] / w_in) ** 2)
            )
        )
    else:
        j_ref_in = j_s

    # Output mesh torsional stiffness and backlash via virtual-work Jacobian with stepped-planet ratio q
    b_sp = min(stage.sun.b_mm, stage.planet.b_mm)
    b_pr = min(p_ring_gear.b_mm, stage.ring.b_mm)
    k_sp_loa = r_sp.load_factors.c_gamma_alpha_N_mm_um * b_sp * 1.0e6
    k_pr_loa = r_pr.load_factors.c_gamma_alpha_N_mm_um * b_pr * 1.0e6
    q_b = pr_geom.gear1.d_b_mm / sp_geom.gear2.d_b_mm
    r_bs_m = 0.5 * sp_geom.gear1.d_b_mm * 1.0e-3
    r_br_m = 0.5 * pr_geom.gear2.d_b_mm * 1.0e-3
    if stage.output_member == "carrier":
        lever_m = q_b * r_bs_m + r_br_m
    elif stage.output_member == "ring":
        lever_m = r_br_m
    else:
        lever_m = q_b * r_bs_m
    k_tors_out = stage.n_planets * (lever_m ** 2) / ((q_b ** 2) / k_sp_loa + 1.0 / k_pr_loa)

    # Geometric output backlash from both meshes referred to output
    q_w = pr_geom.d_w1_mm / sp_geom.d_w2_mm
    r_ws_mm = 0.5 * sp_geom.d_w1_mm
    r_wr_mm = 0.5 * pr_geom.d_w2_mm
    if stage.output_member == "carrier":
        r_w_out_mm = q_w * r_ws_mm + r_wr_mm
    elif stage.output_member == "ring":
        r_w_out_mm = r_wr_mm
    else:
        r_w_out_mm = q_w * r_ws_mm
    j_t_sum = q_w * sp_geom.circumferential_backlash_mm + pr_geom.circumferential_backlash_mm
    backlash_out_deg = math.degrees(j_t_sum / max(r_w_out_mm, 1.0e-6))

    mass_tot = m_s + stage.n_planets * m_p_tot + m_r + stage.carrier_mass_kg
    flags.append("mesh_stiffness_only_excludes_shafts_pins_housing")
    flags.append("estimated_blank_inertia_and_mass")

    st = worst_state([kin.state, ml_sp.state, ml_pr.state, pf.state, r_sp.state, r_pr.state] + [br.state for br in b_ratings])
    return StageEvaluationResult(
        stage_name=stage.name,
        stage_id=stage.stage_id,
        member_ids=stage_member_ids(stage),
        stage_kind=stage.kind,
        kinematics=kin,
        pair_geometries={"sun_planet": sp_geom, "planet_ring": pr_geom},
        mesh_forces={"sun_planet": mf_sp, "planet_ring": mf_pr},
        mesh_losses={"sun_planet": ml_sp, "planet_ring": ml_pr},
        planetary_power_flow=pf,
        bearing_loads=b_loads,
        bearing_ratings=b_ratings,
        pair_ratings={"sun_planet": r_sp, "planet_ring": r_pr},
        member_torques_Nm=pf.member_torques_Nm,
        power_in_W=pf.power_in_W,
        power_out_useful_W=pf.power_out_useful_W,
        power_loss_mesh_W=pf.power_loss_mesh_W,
        power_loss_bearings_W=p_brg_tot,
        power_loss_churning_drag_W=p_drag,
        power_loss_total_W=pf.power_loss_total_W,
        efficiency=pf.efficiency_total,
        torsional_stiffness_out_Nm_rad=k_tors_out,
        backlash_out_deg=backlash_out_deg,
        backlash_out_arcmin=backlash_out_deg * 60.0,
        mass_kg=mass_tot,
        reflected_inertia_local_in_kg_m2=j_ref_in,
        state=st,
        applicability_evidence={
            "eta0_mesh": eta0_mesh,
            "k_gamma": mf_sp.k_gamma,
            "active_branch": pf.active_branch,
        },
        missing_inputs=sorted(set(missing + r_sp.missing_inputs + r_pr.missing_inputs)),
        flags=sorted(set(flags + pf.flags + ml_sp.flags + ml_pr.flags + r_sp.flags + r_pr.flags)),
    )


class Gearbox:
    """Ordered chain of gear stages joined shaft-to-shaft."""

    def __init__(
        self,
        stages: Sequence[StageSpec],
        *,
        name: str = "gearbox",
        lubricant: Optional[Union[str, GearLubricant]] = None,
        housing_area_m2: Optional[float] = None,
        h_conv_W_m2K: float = 15.0,
        input_shaft_mm: float = 10.0,
        output_shaft_mm: float = 16.0,
    ) -> None:
        if not stages:
            raise ValueError("Gearbox requires at least one stage")
        if len({st.stage_id for st in stages}) != len(stages):
            raise ValueError("Stage IDs must be unique")
        if len({st.name for st in stages}) != len(stages):
            raise ValueError("Stage names must be unique")
        for stage in stages: stage.__post_init__()
        self.stages = list(stages)
        self.name = str(name)
        self.lubricant_spec = lubricant
        self.housing_area_m2 = housing_area_m2
        self.h_conv_W_m2K = h_conv_W_m2K
        self.input_shaft_mm = float(input_shaft_mm)
        self.output_shaft_mm = float(output_shaft_mm)

    def _resolve_lubricant(self, preset: Preset) -> GearLubricant:
        if isinstance(self.lubricant_spec, GearLubricant):
            return self.lubricant_spec
        if isinstance(self.lubricant_spec, str):
            return get_gear_lubricant(self.lubricant_spec)
        return get_gear_lubricant(preset.default_lubricant)

    def kinematics(self, input_speed_rpm: float = 1000.0) -> ChainKinematicsResult:
        """Compute stage-by-stage and overall kinematics across the chain."""
        if not math.isfinite(float(input_speed_rpm)):
            raise ValueError(f"input_speed_rpm must be finite, got {input_speed_rpm!r}")
        res_list: List[StageKinematicsResult] = []
        rpm = float(input_speed_rpm)
        for st in self.stages:
            if isinstance(st, PairStageSpec):
                k = pair_kinematics(
                    st.gear1, st.gear2, input_speed_rpm=rpm, centre_distance_mm=st.centre_distance_mm
                )
            else:
                k = planetary_kinematics(
                    sun=st.sun,
                    planet=st.planet,
                    ring=st.ring,
                    n_planets=st.n_planets,
                    fixed_member=st.fixed_member,
                    input_member=st.input_member,
                    output_member=st.output_member,
                    input_speed_rpm=rpm,
                    planet2=st.planet2,
                )
            res_list.append(k)
            rpm = k.output_speed_rpm
        return chain_kinematics(res_list)

    def evaluate_point(
        self,
        *,
        input_speed_rpm: float,
        torque_Nm: float,
        torque_at: str = "input",         # "input" | "output"
        power_direction: str = "forward", # "forward" (in->out) | "reverse" (out->in)
        temp_C: float = 70.0,
        preset_name: str = "standard",
        K_A: Optional[float] = None,
        compute_reverse_check: bool = True,
        member_cycles_by_stage: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> GearboxEvaluationResult:
        """Evaluate the entire gearbox chain at a single steady operating point."""
        for fname, val in (("input_speed_rpm", input_speed_rpm), ("torque_Nm", torque_Nm), ("temp_C", temp_C)):
            if not math.isfinite(float(val)):
                raise ValueError(f"{fname} must be finite, got {val!r}")
        if torque_at not in ("input", "output"):
            raise ValueError(f"torque_at must be 'input' or 'output', got {torque_at!r}")
        if power_direction not in ("forward", "reverse"):
            raise ValueError(f"power_direction must be 'forward' or 'reverse', got {power_direction!r}")
        preset = get_preset(preset_name)
        lube = self._resolve_lubricant(preset)
        ka_use = float(preset.K_A if K_A is None else K_A)
        if not math.isfinite(ka_use) or ka_use <= 0.0:
            raise ValueError(f"K_A must be finite and > 0, got {ka_use!r}")

        kin_chain = self.kinematics(input_speed_rpm)
        n_stages = len(self.stages)

        # Propagate stage-by-stage along power flow or from prescribed boundary
        stage_results: List[Optional[StageEvaluationResult]] = [None] * n_stages
        if torque_at == "input":
            if power_direction == "reverse" and abs(input_speed_rpm) > 1e-12 and (float(torque_Nm) * float(input_speed_rpm)) > 1e-12:
                raise ValueError(
                    "For power_direction='reverse' with torque_at='input', input torque ON stage must oppose input speed (T_in * omega_in <= 0)"
                )
            cur_rpm = float(input_speed_rpm)
            cur_t_in = float(torque_Nm)
            for idx, st in enumerate(self.stages):
                in_key = "gear1" if isinstance(st, PairStageSpec) else st.input_member
                out_key = "gear2" if isinstance(st, PairStageSpec) else st.output_member
                res = evaluate_stage(
                    st,
                    input_speed_rpm=cur_rpm,
                    known_torque_Nm=cur_t_in,
                    known_torque_member=in_key,
                    lubricant=lube,
                    temp_C=temp_C,
                    K_A=ka_use,
                    required_life_h=preset.required_life_h,
                    member_cycles=(member_cycles_by_stage or {}).get(st.stage_id),
                    min_S_H=preset.min_S_H_duty,
                    min_S_F=preset.min_S_F_duty,
                )
                stage_results[idx] = res
                # Coupled downstream shaft receives -T_out_on_stage
                cur_t_in = -res.member_torques_Nm[out_key]
                cur_rpm = res.kinematics.output_speed_rpm
        elif torque_at == "output":
            # Propagate from output back to input using stage kinematics speeds
            stage_rpms = [kin_chain.stages[i].input_speed_rpm for i in range(n_stages)]
            cur_t_out_on_stage = float(torque_Nm) if power_direction == "reverse" else -float(torque_Nm)
            if power_direction == "forward":
                w_out = kin_chain.output_speed_rpm
                cur_t_out_on_stage = -math.copysign(abs(float(torque_Nm)), w_out if w_out != 0.0 else 1.0)
            for idx in range(n_stages - 1, -1, -1):
                st = self.stages[idx]
                out_key = "gear2" if isinstance(st, PairStageSpec) else st.output_member
                in_key = "gear1" if isinstance(st, PairStageSpec) else st.input_member
                res = evaluate_stage(
                    st,
                    input_speed_rpm=stage_rpms[idx],
                    known_torque_Nm=cur_t_out_on_stage,
                    known_torque_member=out_key,
                    lubricant=lube,
                    temp_C=temp_C,
                    K_A=ka_use,
                    required_life_h=preset.required_life_h,
                    member_cycles=(member_cycles_by_stage or {}).get(st.stage_id),
                    min_S_H=preset.min_S_H_duty,
                    min_S_F=preset.min_S_F_duty,
                )
                stage_results[idx] = res
                # Next upstream stage's output experiences -T_in of this stage
                cur_t_out_on_stage = -res.member_torques_Nm[in_key]
        else:
            raise ValueError(f"Unsupported power_direction={power_direction!r}, torque_at={torque_at!r}")

        ordered: List[StageEvaluationResult] = [r for r in stage_results if r is not None]

        # Reflected inertia, cumulative stiffness, backlash, mass, envelope
        j_ref_tot = 0.0
        cum_ratio = 1.0
        comp_out_sum = 0.0
        backlash_out_deg = 0.0
        mass_tot = 0.0
        env_d = 0.0
        env_l = 0.0

        # Cumulative ratio after stage k to output for backlash/stiffness referral
        ratios_to_out = [1.0] * n_stages
        acc = 1.0
        for idx in range(n_stages - 1, -1, -1):
            ratios_to_out[idx] = acc
            acc *= abs(ordered[idx].kinematics.ratio)

        for idx, res in enumerate(ordered):
            j_ref_tot += res.reflected_inertia_local_in_kg_m2 / (cum_ratio ** 2)
            cum_ratio *= abs(res.kinematics.ratio)
            r_to_out = ratios_to_out[idx]
            backlash_out_deg += res.backlash_out_deg / r_to_out
            comp_out_sum += 1.0 / (max(res.torsional_stiffness_out_Nm_rad, 1.0e-6) * (r_to_out ** 2))
            mass_tot += res.mass_kg
        from .layout import chain_bounds
        bounds = chain_bounds(self.stages)
        env_d = max(bounds["size_mm"][:2])
        env_l = bounds["size_mm"][2]

        k_tors_out_tot = 1.0 / max(comp_out_sum, 1.0e-15)

        p_loss_tot = sum(r.power_loss_total_W for r in ordered)
        first_res, last_res = ordered[0], ordered[-1]
        in_key0 = "gear1" if isinstance(self.stages[0], PairStageSpec) else self.stages[0].input_member
        out_key_last = "gear2" if isinstance(self.stages[-1], PairStageSpec) else self.stages[-1].output_member
        t_in_shaft = first_res.member_torques_Nm[in_key0]
        t_out_shaft = last_res.member_torques_Nm[out_key_last]

        if abs(input_speed_rpm) <= 1e-12:
            p_in_tot = 0.0
            p_out_useful = 0.0
            eff_tot: Optional[float] = None
        elif power_direction == "forward":
            p_in_tot = first_res.power_in_W
            p_out_useful = last_res.power_out_useful_W
            eff_tot = (p_out_useful / p_in_tot) if p_in_tot > 0.0 else None
        else:
            p_in_tot = last_res.power_in_W
            p_out_useful = first_res.power_out_useful_W
            eff_tot = (p_out_useful / p_in_tot) if p_in_tot > 0.0 else None

        rev_eff: Optional[float] = None
        if compute_reverse_check and all(r.efficiency is not None for r in ordered) and abs(input_speed_rpm) > 1e-12 and power_direction == "forward":
            w_out = kin_chain.output_speed_rpm * math.pi / 30.0
            t_back = abs(t_out_shaft) if abs(t_out_shaft) > 0.0 else abs(t_in_shaft * kin_chain.overall_ratio)
            rev_res = self.evaluate_point(
                input_speed_rpm=input_speed_rpm,
                torque_Nm=math.copysign(t_back, w_out if w_out != 0.0 else 1.0),
                torque_at="output",
                power_direction="reverse",
                temp_C=temp_C,
                preset_name=preset_name,
                K_A=ka_use,
                compute_reverse_check=False,
                member_cycles_by_stage=member_cycles_by_stage,
            )
            rev_eff = rev_res.overall_efficiency

        if any(r.efficiency is None for r in ordered):
            eff_tot = None
        all_flags = sorted(set(fl for r in ordered for fl in r.flags))
        all_missing = sorted(set(m for r in ordered for m in r.missing_inputs))
        st_overall = worst_state([r.state for r in ordered])

        return GearboxEvaluationResult(
            gearbox_name=self.name,
            preset_name=preset.name,
            duty_source="single_operating_point",
            overall_ratio=kin_chain.overall_ratio,
            abs_overall_ratio=kin_chain.abs_overall_ratio,
            reverses_direction=kin_chain.reverses_direction,
            stage_results=ordered,
            chain_kinematics=kin_chain,
            input_torque_Nm=t_in_shaft,
            output_torque_Nm=-t_out_shaft,
            input_speed_rpm=kin_chain.input_speed_rpm,
            output_speed_rpm=kin_chain.output_speed_rpm,
            power_in_W=p_in_tot,
            power_out_useful_W=p_out_useful,
            power_loss_W=p_loss_tot,
            overall_efficiency=eff_tot,
            reverse_efficiency=rev_eff,
            backlash_out_deg=backlash_out_deg,
            backlash_out_arcmin=backlash_out_deg * 60.0,
            torsional_stiffness_out_Nm_rad=k_tors_out_tot,
            reflected_inertia_in_kg_m2=j_ref_tot,
            mass_kg=mass_tot,
            envelope_diameter_mm=env_d,
            envelope_length_mm=env_l,
            state=st_overall,
            applicability_evidence={
                "power_balance_residual_W": p_in_tot - (p_out_useful + p_loss_tot),
                "envelope_bounds": bounds,
                "preset": preset.name,
            },
            missing_inputs=all_missing,
            flags=all_flags,
        )

    def evaluate_spectrum(
        self,
        spectrum: Optional[DutySpectrum] = None,
        *,
        fallback_motor_continuous_Nm: Optional[float] = None,
        fallback_motor_continuous_rpm: Optional[float] = None,
        fallback_motor_peak_Nm: Optional[float] = None,
        preset_name: str = "standard",
        temp_C: Optional[float] = None,
        K_A: Optional[float] = None,
    ) -> GearboxEvaluationResult:
        """Evaluate gearbox over a duty spectrum (with Miner gear/bearing life, parts, thermal, and preset check).

        Follows OWNER_DECISIONS.md: a ``DutySpectrum`` drives life evaluation;
        motor two-duty inputs are used ONLY as a fallback when no spectrum is
        supplied, and ``duty_source`` records which source was used.
        """
        preset = get_preset(preset_name)
        lube = self._resolve_lubricant(preset)
        ka_use = float(preset.K_A if K_A is None else K_A)
        kin_ref = self.kinematics(1000.0)

        if spectrum is not None:
            spec_use = spectrum
        elif fallback_motor_continuous_Nm is not None and fallback_motor_continuous_rpm is not None:
            spec_use = spectrum_from_motor_duties(
                continuous_torque_in_Nm=fallback_motor_continuous_Nm,
                continuous_speed_in_rpm=fallback_motor_continuous_rpm,
                gearbox_ratio=kin_ref.abs_overall_ratio,
                peak_torque_in_Nm=fallback_motor_peak_Nm,
            )
        else:
            raise ValueError("Either DutySpectrum or motor continuous fallback (torque, speed) is required")

        if not spec_use.bins:
            raise ValueError("DutySpectrum bins cannot be empty")

        # Symmetric permutation-invariant representative bin and thermal balance
        def _bin_sort_key(b: DutyBin) -> Tuple[float, float, float, float, float, float]:
            return (
                b.duration_h,
                abs(b.torque_out_Nm) * abs(b.speed_out_rpm),
                abs(b.torque_out_Nm),
                abs(b.speed_out_rpm),
                b.torque_out_Nm,
                b.speed_out_rpm,
            )

        sorted_bins = sorted(spec_use.bins, key=_bin_sort_key)
        rep_bin = sorted_bins[-1]
        rep_key = _bin_sort_key(rep_bin)
        rep_idx = next(i for i, b in enumerate(spec_use.bins) if _bin_sort_key(b) == rep_key)

        t_eval = float(temp_C) if temp_C is not None else 70.0
        therm_res: Optional[GreaseThermalBalanceResult] = None
        if temp_C is None and lube.kind == "grease" and self.housing_area_m2 is not None:
            tot_dt_therm = math.fsum(sorted([max(b.duration_h, 0.0) for b in spec_use.bins]))

            def _loss_at_t(tc: float) -> float:
                weighted_losses: List[float] = []
                for b in sorted_bins:
                    p_dir_b = "forward" if (b.torque_out_Nm * b.speed_out_rpm >= 0.0 or b.speed_out_rpm == 0.0) else "reverse"
                    t_arg_b = b.torque_out_Nm if p_dir_b == "forward" else -b.torque_out_Nm
                    pt = self.evaluate_point(
                        input_speed_rpm=b.speed_out_rpm * kin_ref.overall_ratio,
                        torque_Nm=t_arg_b,
                        torque_at="output",
                        power_direction=p_dir_b,
                        temp_C=tc,
                        preset_name=preset_name,
                        K_A=ka_use,
                        compute_reverse_check=False,
                    )
                    weighted_losses.append(max(b.duration_h, 0.0) * pt.power_loss_W)
                return math.fsum(sorted(weighted_losses)) / max(tot_dt_therm, 1e-12)

            amb_mean = math.fsum(sorted([max(b.duration_h, 0.0) * b.ambient_temp_C for b in sorted_bins])) / max(tot_dt_therm, 1e-12)
            therm_res = solve_grease_thermal_balance(
                lubricant=lube,
                loss_fn_W=_loss_at_t,
                ambient_temp_C=amb_mean,
                housing_area_m2=self.housing_area_m2,
                h_conv_W_m2K=self.h_conv_W_m2K,
            )
            t_eval = (
                therm_res.equilibrium_temp_C
                if therm_res.equilibrium_temp_C is not None
                else float(lube.temp_max_C)
            )

        # Repeat this duty block to the required life. All bins use the same
        # per-member exposure; stress and lubricant factors still vary per bin.
        total_h = sum(b.duration_h for b in spec_use.bins)
        cycle_terms = {st.stage_id: {} for st in self.stages}
        for b in spec_use.bins:
            chain = self.kinematics(b.speed_out_rpm * kin_ref.overall_ratio)
            for st, sk in zip(self.stages, chain.stages):
                rpm = sk.member_speeds_rpm
                rates = ({key: abs(rpm[key]) for key in ("gear1", "gear2")}
                    if isinstance(st, PairStageSpec) else {
                        "sun": st.n_planets*abs(rpm["sun"]-rpm["carrier"]),
                        "planet": abs(rpm["planet_rel_carrier"]),
                        "planet2": abs(rpm["planet_rel_carrier"]),
                        "ring": st.n_planets*abs(rpm["ring"]-rpm["carrier"])})
                for key, rate in rates.items():
                    cycle_terms[st.stage_id].setdefault(key, []).append(rate*b.duration_h)
        required_cycles = {sid: {key: math.fsum(sorted(terms))*60.*preset.required_life_h/total_h
            for key, terms in members.items()} for sid, members in cycle_terms.items()}

        # Evaluate each bin across all stages
        bin_evals: List[GearboxEvaluationResult] = []
        for idx_b, b in enumerate(spec_use.bins):
            in_rpm = b.speed_out_rpm * kin_ref.overall_ratio
            p_dir = "forward" if (b.torque_out_Nm * b.speed_out_rpm >= 0.0 or b.speed_out_rpm == 0.0) else "reverse"
            # For reverse regeneration in DutyBin, b.torque_out_Nm is torque delivered TO load,
            # so torque acting ON output port is -b.torque_out_Nm (matching speed direction).
            t_arg = abs(b.torque_out_Nm) if p_dir == "forward" else -b.torque_out_Nm
            ev = self.evaluate_point(
                input_speed_rpm=in_rpm,
                torque_Nm=t_arg,
                torque_at="output",
                power_direction=p_dir,
                temp_C=t_eval,
                preset_name=preset_name,
                K_A=ka_use,
                compute_reverse_check=(idx_b == rep_idx),
                member_cycles_by_stage=required_cycles,
            )
            bin_evals.append(ev)

        # Accumulate ISO 6336-6 Miner gear fatigue damage & bearing/part checks (order-invariant)
        miner_results: List[MinerDamageResult] = []
        bearing_spec_results: List[BearingSpectrumLifeResult] = []
        part_records: List[PartStrengthRecord] = []

        min_sh_duty = math.inf
        min_sf_duty = math.inf
        sh_states: List[ValidityState] = [ev.chain_kinematics.state for ev in bin_evals]
        sf_states: List[ValidityState] = list(sh_states)

        worst_idx = max(
            range(len(spec_use.bins)),
            key=lambda i: (
                abs(spec_use.bins[i].torque_out_Nm),
                abs(spec_use.bins[i].speed_out_rpm),
                spec_use.bins[i].duration_h,
            ),
        )
        peak_out = spec_use.peak_torque_out_Nm if spec_use.peak_torque_out_Nm is not None else (
            abs(spec_use.bins[worst_idx].torque_out_Nm) * preset.shock_factor
        )
        shock_scale = max(peak_out / max(abs(spec_use.bins[worst_idx].torque_out_Nm), 1e-6), 1.0)
        nu_eval = lube.kinematic_viscosity_mm2_s(t_eval)

        # Collect gear member Miner lives & bearing lives stage by stage
        for s_idx, st_spec in enumerate(self.stages):
            if isinstance(st_spec, PairStageSpec):
                g1_mat = get_gear_material(st_spec.gear1.material_name)
                g2_mat = get_gear_material(st_spec.gear2.material_name)
                bins_1_f, bins_1_h, f1_facs, h1_facs = [], [], [], []
                bins_2_f, bins_2_h, f2_facs, h2_facs = [], [], [], []
                st_1_f, st_1_h, st_2_f, st_2_h = [], [], [], []
                flags_m1: List[str] = []
                miss_m1: List[str] = []
                flags_m2: List[str] = []
                miss_m2: List[str] = []
                pair_brg_bins: Dict[str, List[Tuple[float, float, float, float]]] = {
                    "input_A": [], "input_B": [], "output_A": [], "output_B": []
                }
                assembly_states = [worst_state([ev.stage_results[s_idx].kinematics.state, (ev.stage_results[s_idx].planetary_power_flow.state if ev.stage_results[s_idx].planetary_power_flow is not None else "valid")]) for ev in bin_evals]
                assembly_flags = [f for ev in bin_evals for f in ev.stage_results[s_idx].kinematics.flags]
                assembly_missing = [f for ev in bin_evals for f in ev.stage_results[s_idx].kinematics.missing_inputs]
                for states in (st_1_f, st_1_h, st_2_f, st_2_h): states.extend(assembly_states)
                for flags in (flags_m1, flags_m2): flags.extend(assembly_flags)
                for missing in (miss_m1, miss_m2): missing.extend(assembly_missing)
                for b_idx, b in enumerate(spec_use.bins):
                    sr = bin_evals[b_idx].stage_results[s_idx]
                    pg = sr.pair_geometries["main_mesh"]
                    pr = sr.pair_ratings["main_mesh"]
                    min_sh_duty = min(min_sh_duty, pr.min_S_H)
                    sh_states.append(pr.gear1_rating.pitting_state)
                    sh_states.append(pr.gear2_rating.pitting_state)
                    if pr.min_S_F is not None:
                        min_sf_duty = min(min_sf_duty, pr.min_S_F)
                    sf_states.append(pr.gear1_rating.bending_state)
                    sf_states.append(pr.gear2_rating.bending_state)

                    n1_cpm = abs(sr.kinematics.member_speeds_rpm["gear1"])
                    n2_cpm = abs(sr.kinematics.member_speeds_rpm["gear2"])
                    r1, r2 = pr.gear1_rating, pr.gear2_rating
                    bins_1_f.append((r1.sigma_F_MPa, n1_cpm, b.duration_h))
                    bins_1_h.append((r1.sigma_H_MPa, n1_cpm, b.duration_h))
                    f1_facs.append(r1.Y_drelT * r1.Y_RrelT * r1.Y_X)
                    h1_facs.append(r1.Z_L * r1.Z_v * r1.Z_R)
                    st_1_f.extend([pg.state, r1.bending_state])
                    st_1_h.extend([pg.state, r1.pitting_state])
                    flags_m1.extend(r1.flags)
                    miss_m1.extend(r1.missing_inputs)

                    bins_2_f.append((r2.sigma_F_MPa, n2_cpm, b.duration_h))
                    bins_2_h.append((r2.sigma_H_MPa, n2_cpm, b.duration_h))
                    f2_facs.append(r2.Y_drelT * r2.Y_RrelT * r2.Y_X)
                    h2_facs.append(r2.Z_L * r2.Z_v * r2.Z_R)
                    st_2_f.extend([pg.state, r2.bending_state])
                    st_2_h.extend([pg.state, r2.pitting_state])
                    flags_m2.extend(r2.flags)
                    miss_m2.extend(r2.missing_inputs)

                    for bl in sr.bearing_loads:
                        if bl.role in pair_brg_bins:
                            pair_brg_bins[bl.role].append(
                                (bl.F_r_worst_branch_N, bl.F_a_worst_branch_N, bl.speed_rpm, b.duration_h)
                            )

                miner_results.append(evaluate_gear_miner_spectrum(
                    target_name=f"{st_spec.name}.gear1", material=g1_mat, bins=bins_1_f,
                    mechanism="bending_root", allowable_factors=f1_facs,
                    upstream_states=st_1_f, upstream_flags=flags_m1, upstream_missing=miss_m1,
                ))
                miner_results.append(evaluate_gear_miner_spectrum(
                    target_name=f"{st_spec.name}.gear1", material=g1_mat, bins=bins_1_h,
                    mechanism="pitting_flank", allowable_factors=h1_facs,
                    upstream_states=st_1_h, upstream_flags=flags_m1, upstream_missing=miss_m1,
                ))
                miner_results.append(evaluate_gear_miner_spectrum(
                    target_name=f"{st_spec.name}.gear2", material=g2_mat, bins=bins_2_h,
                    mechanism="pitting_flank", allowable_factors=h2_facs,
                    upstream_states=st_2_h, upstream_flags=flags_m2, upstream_missing=miss_m2,
                ))
                miner_results.append(evaluate_gear_miner_spectrum(
                    target_name=f"{st_spec.name}.gear2", material=g2_mat, bins=bins_2_f,
                    mechanism="bending_root", allowable_factors=f2_facs,
                    upstream_states=st_2_f, upstream_flags=flags_m2, upstream_missing=miss_m2,
                ))

                pair_brg_specs = {
                    "input_A": st_spec.bearing_1a,
                    "input_B": st_spec.bearing_1b,
                    "output_A": st_spec.bearing_2a,
                    "output_B": st_spec.bearing_2b,
                }
                for brole, bspec in pair_brg_specs.items():
                    if bspec is not None and pair_brg_bins[brole]:
                        bearing_spec_results.append(
                            evaluate_bearing_spectrum(
                                bspec,
                                role=f"{st_spec.name}.{brole}",
                                bins=pair_brg_bins[brole],
                                nu_op_mm2_s=nu_eval,
                                temp_C=t_eval,
                                required_life_h=preset.required_life_h,
                                min_S0=preset.min_bearing_S0,
                            )
                        )

                wsr = bin_evals[worst_idx].stage_results[s_idx]
                w_mf = wsr.mesh_forces["main_mesh"]
                f_trans_w = math.hypot(w_mf.F_tw_N, w_mf.F_r_N)
                fa_w = abs(w_mf.F_a_N)

                def _pair_shaft_moment_Nm(span_mm: Optional[float], offset_mm: Optional[float]) -> Tuple[float, bool]:
                    if span_mm is None or span_mm <= 0.0:
                        return 0.0, False
                    l_s = float(span_mm)
                    a_s = float(offset_mm) if offset_mm is not None else 0.5 * l_s
                    if 0.0 <= a_s <= l_s:
                        return f_trans_w * (a_s * (l_s - a_s) / l_s) * 1.0e-3, True
                    arm = abs(a_s - l_s) if a_s > l_s else abs(a_s)
                    return f_trans_w * arm * 1.0e-3, True

                if st_spec.shaft1_section is not None:
                    t1_d = abs(wsr.member_torques_Nm["gear1"])
                    m1_d, has_span1 = _pair_shaft_moment_Nm(st_spec.shaft1_span_mm, st_spec.shaft1_offset_mm)
                    rec1 = rate_shaft_din743(
                        st_spec.shaft1_section,
                        torque_duty_Nm=t1_d,
                        bending_moment_duty_Nm=m1_d,
                        axial_force_duty_N=fa_w,
                        torque_shock_Nm=t1_d * shock_scale,
                        bending_moment_shock_Nm=m1_d * shock_scale,
                        axial_force_shock_N=fa_w * shock_scale,
                        min_sf_fatigue=preset.min_S_F_duty,
                        min_sf_static=preset.min_S_F_static,
                    )
                    if not has_span1:
                        rec1 = replace(rec1, passed=False, state="unsupported")
                        rec1.flags.append("shaft_bending_omitted_span_not_provided")
                        rec1.missing_inputs.append("shaft1_span_mm")
                    part_records.append(rec1)
                if st_spec.shaft2_section is not None:
                    t2_d = abs(wsr.member_torques_Nm["gear2"])
                    m2_d, has_span2 = _pair_shaft_moment_Nm(st_spec.shaft2_span_mm, st_spec.shaft2_offset_mm)
                    rec2 = rate_shaft_din743(
                        st_spec.shaft2_section,
                        torque_duty_Nm=t2_d,
                        bending_moment_duty_Nm=m2_d,
                        axial_force_duty_N=fa_w,
                        torque_shock_Nm=t2_d * shock_scale,
                        bending_moment_shock_Nm=m2_d * shock_scale,
                        axial_force_shock_N=fa_w * shock_scale,
                        min_sf_fatigue=preset.min_S_F_duty,
                        min_sf_static=preset.min_S_F_static,
                    )
                    if not has_span2:
                        rec2 = replace(rec2, passed=False, state="unsupported")
                        rec2.flags.append("shaft_bending_omitted_span_not_provided")
                        rec2.missing_inputs.append("shaft2_span_mm")
                    part_records.append(rec2)
                if st_spec.key_or_spline_1 is not None:
                    t1_d = abs(wsr.member_torques_Nm["gear1"])
                    part_records.append(rate_key_or_spline(
                        st_spec.key_or_spline_1, torque_duty_Nm=t1_d, torque_shock_Nm=t1_d * shock_scale,
                    ))
                if st_spec.key_or_spline_2 is not None:
                    t2_d = abs(wsr.member_torques_Nm["gear2"])
                    part_records.append(rate_key_or_spline(
                        st_spec.key_or_spline_2, torque_duty_Nm=t2_d, torque_shock_Nm=t2_d * shock_scale,
                    ))

            elif isinstance(st_spec, PlanetaryStageSpec):
                is_comp_st = st_spec.planet2 is not None
                mat_s = get_gear_material(st_spec.sun.material_name)
                mat_p = get_gear_material(st_spec.planet.material_name)
                mat_p2 = get_gear_material(st_spec.planet2.material_name) if st_spec.planet2 is not None else mat_p
                mat_r = get_gear_material(st_spec.ring.material_name)
                bins_sf, bins_sh, bins_pf, bins_ph, bins_rh = [], [], [], [], []
                sf_facs, sh_facs, pf_facs, ph_facs, rh_facs = [], [], [], [], []
                st_sf, st_sh, st_pf, st_ph, st_rh = [], [], [], [], []
                fl_sf, fl_sh, fl_pf, fl_ph, fl_rh = [], [], [], [], []
                mi_sf, mi_sh, mi_pf, mi_ph, mi_rh = [], [], [], [], []
                bins_p2f, bins_p2h, p2f_facs, p2h_facs, st_p2f, st_p2h, fl_p2, mi_p2 = (
                    [], [], [], [], [], [], [], []
                )
                brg_p_bins, brg_in_bins, brg_out_bins = [], [], []
                assembly_states = [worst_state([ev.stage_results[s_idx].kinematics.state, (ev.stage_results[s_idx].planetary_power_flow.state if ev.stage_results[s_idx].planetary_power_flow is not None else "valid")]) for ev in bin_evals]
                assembly_flags = [f for ev in bin_evals for f in ev.stage_results[s_idx].kinematics.flags]
                assembly_missing = [f for ev in bin_evals for f in ev.stage_results[s_idx].kinematics.missing_inputs]
                for states in (st_sf, st_sh, st_pf, st_ph, st_rh, st_p2f, st_p2h): states.extend(assembly_states)
                for flags in (fl_sf, fl_sh, fl_pf, fl_ph, fl_rh, fl_p2): flags.extend(assembly_flags)
                for missing in (mi_sf, mi_sh, mi_pf, mi_ph, mi_rh, mi_p2): missing.extend(assembly_missing)
                miner_results.append(miner_damage_from_cycles(
                    target_name=f"{st_spec.name}.ring", mechanism="bending_root",
                    bins=[(0., None)], block_hours=sum(b.duration_h for b in spec_use.bins),
                    upstream_states=assembly_states + ["unsupported"],
                    upstream_flags=assembly_flags + ["internal_root_rating_not_implemented"],
                    upstream_missing=assembly_missing + ["validated_internal_root_method"],
                ))
                for b_idx, b in enumerate(spec_use.bins):
                    sr = bin_evals[b_idx].stage_results[s_idx]
                    pg_sp = sr.pair_geometries["sun_planet"]
                    pg_pr = sr.pair_geometries["planet_ring"]
                    r_sp = sr.pair_ratings["sun_planet"]
                    r_pr = sr.pair_ratings["planet_ring"]
                    for pr_item in (r_sp, r_pr):
                        min_sh_duty = min(min_sh_duty, pr_item.min_S_H)
                        sh_states.extend([pr_item.gear1_rating.pitting_state, pr_item.gear2_rating.pitting_state])
                        sf_states.extend([pr_item.gear1_rating.bending_state, pr_item.gear2_rating.bending_state])
                        if pr_item.min_S_F is not None:
                            min_sf_duty = min(min_sf_duty, pr_item.min_S_F)

                    sp_rpm = sr.kinematics.member_speeds_rpm
                    # Carrier-frame engagement counting (ASTRA_DESIGN_REVIEW.md Q2)
                    cpm_sun = st_spec.n_planets * abs(sp_rpm["sun"] - sp_rpm["carrier"])
                    cpm_planet = abs(sp_rpm["planet_rel_carrier"])
                    cpm_ring = st_spec.n_planets * abs(sp_rpm["ring"] - sp_rpm["carrier"])
                    rs = r_sp.gear1_rating
                    rp1 = r_sp.gear2_rating
                    rp2 = r_pr.gear1_rating
                    rr = r_pr.gear2_rating

                    bins_sf.append((rs.sigma_F_MPa, cpm_sun, b.duration_h))
                    bins_sh.append((rs.sigma_H_MPa, cpm_sun, b.duration_h))
                    sf_facs.append(rs.Y_drelT * rs.Y_RrelT * rs.Y_X)
                    sh_facs.append(rs.Z_L * rs.Z_v * rs.Z_R)
                    st_sf.extend([pg_sp.state, rs.bending_state])
                    st_sh.extend([pg_sp.state, rs.pitting_state])
                    fl_sf.extend(rs.flags); mi_sf.extend(rs.missing_inputs)
                    fl_sh.extend(rs.flags); mi_sh.extend(rs.missing_inputs)

                    if is_comp_st:
                        bins_pf.append((rp1.sigma_F_MPa, cpm_planet, b.duration_h))
                        pf_facs.append(rp1.Y_M_alternating * rp1.Y_drelT * rp1.Y_RrelT * rp1.Y_X)
                        bins_ph.append((rp1.sigma_H_MPa, cpm_planet, b.duration_h))
                        ph_facs.append(rp1.Z_L * rp1.Z_v * rp1.Z_R)
                        st_pf.extend([pg_sp.state, rp1.bending_state])
                        st_ph.extend([pg_sp.state, rp1.pitting_state])
                        fl_pf.extend(rp1.flags); mi_pf.extend(rp1.missing_inputs)
                        fl_ph.extend(rp1.flags); mi_ph.extend(rp1.missing_inputs)

                        bins_p2f.append((rp2.sigma_F_MPa, cpm_planet, b.duration_h))
                        p2f_facs.append(rp2.Y_M_alternating * rp2.Y_drelT * rp2.Y_RrelT * rp2.Y_X)
                        bins_p2h.append((rp2.sigma_H_MPa, cpm_planet, b.duration_h))
                        p2h_facs.append(rp2.Z_L * rp2.Z_v * rp2.Z_R)
                        st_p2f.extend([pg_pr.state, rp2.bending_state])
                        st_p2h.extend([pg_pr.state, rp2.pitting_state])
                        fl_p2.extend(rp2.flags); mi_p2.extend(rp2.missing_inputs)
                    else:
                        if rp1.sigma_F_MPa >= rp2.sigma_F_MPa:
                            bins_pf.append((rp1.sigma_F_MPa, cpm_planet, b.duration_h))
                            pf_facs.append(rp1.Y_M_alternating * rp1.Y_drelT * rp1.Y_RrelT * rp1.Y_X)
                            st_pf.extend([pg_sp.state, rp1.bending_state])
                            fl_pf.extend(rp1.flags); mi_pf.extend(rp1.missing_inputs)
                        else:
                            bins_pf.append((rp2.sigma_F_MPa, cpm_planet, b.duration_h))
                            pf_facs.append(rp2.Y_M_alternating * rp2.Y_drelT * rp2.Y_RrelT * rp2.Y_X)
                            st_pf.extend([pg_pr.state, rp2.bending_state])
                            fl_pf.extend(rp2.flags); mi_pf.extend(rp2.missing_inputs)

                        if rp1.sigma_H_MPa >= rp2.sigma_H_MPa:
                            bins_ph.append((rp1.sigma_H_MPa, cpm_planet, b.duration_h))
                            ph_facs.append(rp1.Z_L * rp1.Z_v * rp1.Z_R)
                            st_ph.extend([pg_sp.state, rp1.pitting_state])
                            fl_ph.extend(rp1.flags); mi_ph.extend(rp1.missing_inputs)
                        else:
                            bins_ph.append((rp2.sigma_H_MPa, cpm_planet, b.duration_h))
                            ph_facs.append(rp2.Z_L * rp2.Z_v * rp2.Z_R)
                            st_ph.extend([pg_pr.state, rp2.pitting_state])
                            fl_ph.extend(rp2.flags); mi_ph.extend(rp2.missing_inputs)

                    bins_rh.append((rr.sigma_H_MPa, cpm_ring, b.duration_h))
                    rh_facs.append(rr.Z_L * rr.Z_v * rr.Z_R)
                    st_rh.extend([pg_pr.state, rr.pitting_state])
                    fl_rh.extend(rr.flags); mi_rh.extend(rr.missing_inputs)

                    p_load = next((bl for bl in sr.bearing_loads if bl.role == "planet"), None)
                    if p_load is not None:
                        brg_p_bins.append((p_load.F_r_worst_branch_N, p_load.F_a_worst_branch_N, p_load.speed_rpm, b.duration_h))
                    in_load = next((bl for bl in sr.bearing_loads if bl.role == "input"), None)
                    if in_load is not None:
                        brg_in_bins.append((in_load.F_r_worst_branch_N, in_load.F_a_worst_branch_N, in_load.speed_rpm, b.duration_h))
                    out_load = next((bl for bl in sr.bearing_loads if bl.role == "output"), None)
                    if out_load is not None:
                        brg_out_bins.append((out_load.F_r_worst_branch_N, out_load.F_a_worst_branch_N, out_load.speed_rpm, b.duration_h))

                miner_results.append(evaluate_gear_miner_spectrum(
                    target_name=f"{st_spec.name}.sun", material=mat_s, bins=bins_sf,
                    mechanism="bending_root", allowable_factors=sf_facs,
                    upstream_states=st_sf, upstream_flags=fl_sf, upstream_missing=mi_sf,
                ))
                miner_results.append(evaluate_gear_miner_spectrum(
                    target_name=f"{st_spec.name}.sun", material=mat_s, bins=bins_sh,
                    mechanism="pitting_flank", allowable_factors=sh_facs,
                    upstream_states=st_sh, upstream_flags=fl_sh, upstream_missing=mi_sh,
                ))
                p1_target = f"{st_spec.name}.planet1" if is_comp_st else f"{st_spec.name}.planet"
                miner_results.append(evaluate_gear_miner_spectrum(
                    target_name=p1_target, material=mat_p, bins=bins_pf,
                    mechanism="bending_root", allowable_factors=pf_facs,
                    upstream_states=st_pf, upstream_flags=fl_pf, upstream_missing=mi_pf,
                ))
                miner_results.append(evaluate_gear_miner_spectrum(
                    target_name=p1_target, material=mat_p, bins=bins_ph,
                    mechanism="pitting_flank", allowable_factors=ph_facs,
                    upstream_states=st_ph, upstream_flags=fl_ph, upstream_missing=mi_ph,
                ))
                if is_comp_st:
                    miner_results.append(evaluate_gear_miner_spectrum(
                        target_name=f"{st_spec.name}.planet2", material=mat_p2, bins=bins_p2f,
                        mechanism="bending_root", allowable_factors=p2f_facs,
                        upstream_states=st_p2f, upstream_flags=fl_p2, upstream_missing=mi_p2,
                    ))
                    miner_results.append(evaluate_gear_miner_spectrum(
                        target_name=f"{st_spec.name}.planet2", material=mat_p2, bins=bins_p2h,
                        mechanism="pitting_flank", allowable_factors=p2h_facs,
                        upstream_states=st_p2h, upstream_flags=fl_p2, upstream_missing=mi_p2,
                    ))
                miner_results.append(evaluate_gear_miner_spectrum(
                    target_name=f"{st_spec.name}.ring", material=mat_r, bins=bins_rh,
                    mechanism="pitting_flank", allowable_factors=rh_facs,
                    upstream_states=st_rh, upstream_flags=fl_rh, upstream_missing=mi_rh,
                ))

                for brg_role_name, brg_spec_item, brg_bins_item in (
                    (f"{st_spec.name}.planet_bearing", st_spec.planet_bearing, brg_p_bins),
                    (f"{st_spec.name}.input_bearing", st_spec.input_bearing, brg_in_bins),
                    (f"{st_spec.name}.output_bearing", st_spec.output_bearing, brg_out_bins),
                ):
                    if brg_spec_item is not None and brg_bins_item:
                        bearing_spec_results.append(
                            evaluate_bearing_spectrum(
                                brg_spec_item,
                                role=brg_role_name,
                                bins=brg_bins_item,
                                nu_op_mm2_s=nu_eval,
                                temp_C=t_eval,
                                required_life_h=preset.required_life_h,
                                min_S0=preset.min_bearing_S0,
                            )
                        )

                # Rate optional mechanical parts at worst duty bin & shock load
                wsr = bin_evals[worst_idx].stage_results[s_idx]
                w_p_load = next((bl for bl in wsr.bearing_loads if bl.role == "planet"), None)
                w_pin_force = (
                    float(w_p_load.applicability_evidence.get(
                        "worst_branch_pin_force_N",
                        w_p_load.F_r_worst_branch_N * st_spec.n_bearings_per_planet,
                    ))
                    if w_p_load is not None
                    else 0.0
                )
                if st_spec.planet_pin is not None and w_p_load is not None:
                    part_records.append(rate_planet_pin(
                        st_spec.planet_pin,
                        F_pin_duty_N=w_pin_force,
                        F_pin_shock_N=w_pin_force * shock_scale,
                        min_sf_fatigue=preset.min_S_F_duty,
                        min_sf_static=preset.min_S_F_static,
                        n_bearings=st_spec.n_bearings_per_planet,
                        bearing_spacing_mm=(st_spec.planet_bearing_spacing_mm if st_spec.planet_bearing_spacing_mm > 0.0 else None),
                    ))
                if st_spec.carrier is not None and w_p_load is not None:
                    part_records.append(rate_carrier(
                        st_spec.carrier,
                        F_pin_worst_duty_N=w_pin_force,
                        T_carrier_duty_Nm=abs(wsr.member_torques_Nm["carrier"]),
                        orbit_radius_mm=wsr.pair_geometries["sun_planet"].a_w_mm,
                        F_pin_worst_shock_N=w_pin_force * shock_scale,
                        T_carrier_shock_Nm=abs(wsr.member_torques_Nm["carrier"]) * shock_scale,
                        min_sf_duty=preset.min_S_F_duty,
                        min_sf_static=preset.min_S_F_static,
                    ))
                if st_spec.ring_rim_fixation is not None:
                    mf_pr_w = wsr.mesh_forces["planet_ring"]
                    part_records.extend(rate_ring_rim_and_fixation(
                        st_spec.ring_rim_fixation,
                        F_t_ring_branch_N=mf_pr_w.F_t_worst_branch_N,
                        F_r_ring_branch_N=mf_pr_w.F_r_worst_branch_N,
                        T_ring_duty_Nm=abs(wsr.member_torques_Nm["ring"]),
                        F_t_ring_shock_N=mf_pr_w.F_t_worst_branch_N * shock_scale,
                        F_r_ring_shock_N=mf_pr_w.F_r_worst_branch_N * shock_scale,
                        T_ring_shock_Nm=abs(wsr.member_torques_Nm["ring"]) * shock_scale,
                        min_sf_duty=preset.min_S_F_duty,
                        min_sf_static=preset.min_S_F_static,
                    ))
                mf_sp_w = wsr.mesh_forces["sun_planet"]
                mf_pr_w = wsr.mesh_forces["planet_ring"]
                def _w_axial(mem: str) -> float:
                    if mem == "sun":
                        return abs(mf_sp_w.F_a_N) * st_spec.n_planets
                    if mem == "ring":
                        return abs(mf_pr_w.F_a_N) * st_spec.n_planets
                    return abs(mf_sp_w.F_a_N - mf_pr_w.F_a_N) * st_spec.n_planets
                for role,member,section in (("input",st_spec.input_member,st_spec.input_shaft_section),
                                            ("output",st_spec.output_member,st_spec.output_shaft_section)):
                    if section is None: continue
                    port_load=next(bl for bl in wsr.bearing_loads if bl.role==role)
                    span=getattr(st_spec,role+"_shaft_span_mm")
                    fr=port_load.F_r_worst_branch_N
                    fa=port_load.F_a_worst_branch_N
                    bending=fr*span/4000. if span is not None else 0.
                    torque=abs(wsr.member_torques_Nm[member])
                    record=rate_shaft_din743(section,torque_duty_Nm=torque,
                        bending_moment_duty_Nm=bending,axial_force_duty_N=fa,
                        torque_shock_Nm=torque*shock_scale,bending_moment_shock_Nm=bending*shock_scale,
                        axial_force_shock_N=fa*shock_scale,min_sf_fatigue=preset.min_S_F_duty,
                        min_sf_static=preset.min_S_F_static)
                    record.flags.append("simply_supported_central_resultant_load")
                    if span is None and fr>1.e-9:
                        record=replace(record,passed=False,state="unsupported")
                        record.missing_inputs.append(role+"_shaft_span_mm")
                    part_records.append(record)
                if st_spec.key_or_spline_in is not None:
                    t_in_d = abs(wsr.member_torques_Nm[st_spec.input_member])
                    part_records.append(rate_key_or_spline(
                        st_spec.key_or_spline_in, torque_duty_Nm=t_in_d, torque_shock_Nm=t_in_d * shock_scale
                    ))
                if st_spec.key_or_spline_out is not None:
                    t_out_d = abs(wsr.member_torques_Nm[st_spec.output_member])
                    part_records.append(rate_key_or_spline(
                        st_spec.key_or_spline_out, torque_duty_Nm=t_out_d, torque_shock_Nm=t_out_d * shock_scale
                    ))

        # Deduplicate gear-tooth PartStrengthRecord entries across bins by keeping governing (worst) per part_name
        tooth_by_name: Dict[str, PartStrengthRecord] = {}
        state_rank = {"failed": 0, "unsupported": 1, "estimated": 2, "valid": 3}

        def _tooth_key(rec: PartStrengthRecord) -> Tuple[int, float]:
            sf = rec.min_safety_factor
            req = rec.required_safety_factor or 1.0
            return (state_rank.get(rec.state, 1), -1.0 if sf is None else (sf / max(req, 1e-6)))

        for b_ev in bin_evals:
            for sr in b_ev.stage_results:
                for mname, pr in sr.pair_ratings.items():
                    for gr in (pr.gear1_rating, pr.gear2_rating):
                        flank_rec = PartStrengthRecord(
                            part_name=f"{sr.stage_name}.{mname}.{gr.member_name}_tooth_flank",
                            governing_load_case="duty_pitting",
                            min_safety_factor=gr.S_H,
                            required_safety_factor=preset.min_S_H_duty,
                            is_simplified=bool(gr.simplified_factors),
                            passed=gr.pitting_passed and sr.kinematics.state != "failed",
                            state=worst_state([gr.pitting_state,sr.kinematics.state]),
                            missing_inputs=gr.missing_inputs,
                            method="ISO6336-2",
                            flags=gr.flags,
                        )
                        root_rec = PartStrengthRecord(
                            part_name=f"{sr.stage_name}.{mname}.{gr.member_name}_tooth_root",
                            governing_load_case="duty_bending",
                            min_safety_factor=gr.S_F,
                            required_safety_factor=preset.min_S_F_duty,
                            is_simplified=bool(gr.simplified_factors),
                            passed=gr.bending_passed and sr.kinematics.state != "failed",
                            state=worst_state([gr.bending_state,sr.kinematics.state]),
                            missing_inputs=gr.missing_inputs,
                            method="ISO6336-3",
                            flags=gr.flags,
                        )
                        for trec in (flank_rec, root_rec):
                            prev = tooth_by_name.get(trec.part_name)
                            if prev is None or _tooth_key(trec) < _tooth_key(prev):
                                tooth_by_name[trec.part_name] = trec

        part_records.extend(tooth_by_name.values())
        parts_summary = summarize_part_strengths(part_records) if part_records else None

        target_ids = {f"{st.name}.{role}": identity for st in self.stages
                      for role, identity in stage_member_ids(st).items()}
        for result in miner_results:
            result.target_id = target_ids[result.target_name]

        peak_records = self._peak_ratings(spec_use, peak_out, t_eval, preset)
        peak_lookup = {(r.target_id,r.mechanism):r for r in peak_records}
        for result in miner_results:
            peak = peak_lookup[(result.target_id,result.mechanism)]
            if spec_use.peak_torque_out_Nm is not None and spec_use.peak_events > 0:
                result.state = worst_state([result.state,peak.fatigue_state])
                result.flags = sorted(set(result.flags+peak.flags))
                result.missing_inputs = sorted(set(result.missing_inputs+[
                    m for m in peak.missing_inputs if not m.startswith("provenanced_static")]))
                result.applicability_evidence["peak_event_damage_D"] = peak.damage_D
                result.applicability_evidence["peak_event_cycles"] = peak.total_cycles
                result.total_cycles_in_block += peak.total_cycles or 0.
                result.bin_damages.append(peak.damage_D)
                result.damage_D = (result.damage_D + peak.damage_D
                    if result.damage_D is not None and peak.damage_D is not None else None)
                result.predicted_life_hours = (result.total_block_hours/result.damage_D
                    if result.state not in ("failed","unsupported") and result.damage_D else None)
                if result.damage_D and "zero_fatigue_damage_under_miner_model" in result.flags:
                    result.flags.remove("zero_fatigue_damage_under_miner_model")
        root_peaks = [r for r in peak_records if r.mechanism == "bending_root"]
        flank_peaks = [r for r in peak_records if r.mechanism == "pitting_flank"]
        static_sf_state = worst_state([r.static_state for r in root_peaks])
        static_sh_state = worst_state([r.static_state for r in flank_peaks])
        static_sf = min((r.static_safety for r in root_peaks if r.static_safety is not None),default=None)
        static_sh = min((r.static_safety for r in flank_peaks if r.static_safety is not None),default=None)

        gear_lives = [m.predicted_life_hours for m in miner_results if m.predicted_life_hours is not None]
        miner_worst_st = worst_state([m.state for m in miner_results]) if miner_results else "unsupported"
        if miner_worst_st in ("unsupported", "failed"):
            min_gear_life_h: Optional[float] = None
            gear_life_st: ValidityState = miner_worst_st
        else:
            complete = all(m.predicted_life_hours is not None or (
                m.damage_D == 0. and "zero_fatigue_damage_under_miner_model" in m.flags)
                for m in miner_results)
            min_gear_life_h = (min(gear_lives) if gear_lives else math.inf) if complete else None
            gear_life_st = miner_worst_st if min_gear_life_h is not None else "unsupported"

        brg_lives = [
            (b.L10m_spectrum_h if b.L10m_spectrum_h is not None else b.L10_spectrum_h)
            for b in bearing_spec_results
        ]
        brg_lives_valid = [v for v in brg_lives if v is not None]
        min_brg_life_h = min(brg_lives_valid) if brg_lives_valid else None
        brg_s0s = [b.min_S0_static for b in bearing_spec_results if b.min_S0_static is not None]
        min_brg_s0 = min(brg_s0s) if brg_s0s else None
        brg_life_st: ValidityState = worst_state([b.state for b in bearing_spec_results]) if bearing_spec_results else "unsupported"

        if brg_life_st in ("failed", "unsupported"):
            min_brg_life_h = None

        rep_ev = bin_evals[rep_idx]

        sf_val = None if math.isinf(min_sf_duty) else min_sf_duty
        sh_val = None if math.isinf(min_sh_duty) else min_sh_duty
        sf_st = worst_state(sf_states) if sf_states else "unsupported"
        sh_st = worst_state(sh_states) if sh_states else "unsupported"

        preset_eval = evaluate_against_preset(
            preset,
            min_S_F_duty=sf_val,
            min_S_H_duty=sh_val,
            S_F_duty_state=sf_st,
            S_H_duty_state=sh_st,
            min_S_F_static=static_sf,
            min_S_H_static=static_sh,
            S_F_static_state=static_sf_state,
            S_H_static_state=static_sh_state,
            gear_life_h=min_gear_life_h,
            gear_life_state=gear_life_st,
            bearing_life_h=min_brg_life_h,
            bearing_life_state=brg_life_st,
            min_bearing_S0=min_brg_s0,
            backlash_arcmin=rep_ev.backlash_out_arcmin,
            reverse_efficiency=rep_ev.reverse_efficiency,
            parts_summary=parts_summary,
            required_checks_state=worst_state([ev.state for ev in bin_evals] +
                [miner_worst_st] + ([therm_res.state] if therm_res else [])),
        )

        all_flags = sorted(set(rep_ev.flags + spec_use.flags + preset_eval.flags))
        all_missing = sorted(set(rep_ev.missing_inputs + preset_eval.missing_inputs))
        st_tot = worst_state([ev.state for ev in bin_evals] + [preset_eval.state, miner_worst_st] + ([parts_summary.state] if parts_summary else []) + ([therm_res.state] if therm_res else []))

        return GearboxEvaluationResult(
            gearbox_name=self.name,
            preset_name=preset.name,
            duty_source=spec_use.source,
            overall_ratio=rep_ev.overall_ratio,
            abs_overall_ratio=rep_ev.abs_overall_ratio,
            reverses_direction=rep_ev.reverses_direction,
            stage_results=rep_ev.stage_results,
            chain_kinematics=rep_ev.chain_kinematics,
            input_torque_Nm=rep_ev.input_torque_Nm,
            output_torque_Nm=rep_ev.output_torque_Nm,
            input_speed_rpm=rep_ev.input_speed_rpm,
            output_speed_rpm=rep_ev.output_speed_rpm,
            power_in_W=rep_ev.power_in_W,
            power_out_useful_W=rep_ev.power_out_useful_W,
            power_loss_W=rep_ev.power_loss_W,
            overall_efficiency=rep_ev.overall_efficiency,
            reverse_efficiency=rep_ev.reverse_efficiency,
            backlash_out_deg=rep_ev.backlash_out_deg,
            backlash_out_arcmin=rep_ev.backlash_out_arcmin,
            torsional_stiffness_out_Nm_rad=rep_ev.torsional_stiffness_out_Nm_rad,
            reflected_inertia_in_kg_m2=rep_ev.reflected_inertia_in_kg_m2,
            mass_kg=rep_ev.mass_kg,
            envelope_diameter_mm=rep_ev.envelope_diameter_mm,
            envelope_length_mm=rep_ev.envelope_length_mm,
            gear_miner_lives=miner_results,
            peak_member_ratings=peak_records,
            bin_evaluations=bin_evals,
            bearing_spectrum_lives=bearing_spec_results,
            parts_summary=parts_summary,
            thermal_balance=therm_res,
            preset_evaluation=preset_eval,
            min_gear_life_h=min_gear_life_h,
            min_bearing_life_h=min_brg_life_h,
            state=st_tot,
            applicability_evidence={
                "duty_source": spec_use.source,
                "n_bins": len(spec_use.bins),
                "peak_events": spec_use.peak_events,
                "operating_temp_C": t_eval,
            },
            missing_inputs=all_missing,
            flags=all_flags,
        )

    def _peak_ratings(self, spectrum, peak_out, temp_C, preset):
        """Worst mesh per physical member; explicit engagement and static-limit coverage."""
        ids = {identity for st in self.stages for role,identity in stage_member_ids(st).items() if role != "carrier"}
        unknown = (set(spectrum.peak_member_engagements)|set(spectrum.static_strength_limits))-ids
        if unknown: raise ValueError(f"Unknown peak member IDs: {sorted(unknown)}")
        speed = spectrum.peak_speed_out_rpm or 0.
        kin = self.kinematics(1000.)
        point = self.evaluate_point(input_speed_rpm=speed*kin.overall_ratio,
            torque_Nm=peak_out,torque_at="output",temp_C=temp_C,preset_name=preset.name,
            K_A=1.,compute_reverse_check=False)
        found = {}
        for stage,sr in zip(self.stages,point.stage_results):
            if isinstance(stage,PairStageSpec):
                pr=sr.pair_ratings["main_mesh"]
                entries=[("gear1",stage.gear1,pr.gear1_rating,sr.pair_geometries["main_mesh"]),
                         ("gear2",stage.gear2,pr.gear2_rating,sr.pair_geometries["main_mesh"])]
            else:
                sp,pr=sr.pair_ratings["sun_planet"],sr.pair_ratings["planet_ring"]
                entries=[("sun",stage.sun,sp.gear1_rating,sr.pair_geometries["sun_planet"]),
                         ("planet1" if stage.planet2 else "planet",stage.planet,sp.gear2_rating,sr.pair_geometries["sun_planet"]),
                         ("planet2" if stage.planet2 else "planet",stage.planet2 or stage.planet,pr.gear1_rating,sr.pair_geometries["planet_ring"]),
                         ("ring",stage.ring,pr.gear2_rating,sr.pair_geometries["planet_ring"])]
            for role,spec,rating,geom in entries:
                identity=stage_member_ids(stage)[role]
                limits=spectrum.static_strength_limits.get(identity)
                source=limits.source if limits and math.isclose(limits.temperature_C,temp_C,abs_tol=1e-9) else ""
                for mechanism in ("bending_root","pitting_flank"):
                    root=mechanism=="bending_root"
                    static_state=worst_state([geom.state,sr.kinematics.state,"estimated"])
                    root_unsupported = spec.is_internal or (spec.rim_thickness_mm is not None and spec.rim_thickness_mm <= .5*(spec.ha_star+spec.hf_star)*spec.m_n_mm)
                    if (root and root_unsupported) or not (15. <= spec.alpha_n_deg <= 25. and abs(spec.beta_deg)<=30. and 1.<=geom.epsilon_alpha<=2.5):
                        static_state=worst_state([static_state,"unsupported"])
                    if sr.planetary_power_flow is not None and sr.planetary_power_flow.state == "failed": static_state="failed"
                    fatigue_state=worst_state([geom.state,sr.kinematics.state,rating.bending_state if root else rating.pitting_state])
                    if sr.planetary_power_flow is not None and sr.planetary_power_flow.state == "failed": fatigue_state="failed"
                    if spectrum.peak_reversing: fatigue_state=worst_state([fatigue_state,"unsupported"])
                    rec=rate_peak_member(target_id=identity,target_name=f"{stage.name}.{role}",mechanism=mechanism,
                        stress_MPa=rating.sigma_F_MPa if root else rating.sigma_H_MPa,
                        material=get_gear_material(spec.material_name),
                        fatigue_allowable_factor=(rating.Y_drelT*rating.Y_RrelT*rating.Y_X*rating.Y_M_alternating if root else rating.Z_L*rating.Z_v*rating.Z_R),
                        events=spectrum.peak_events if spectrum.peak_torque_out_Nm is not None else 0,
                        engagements_per_event=spectrum.peak_member_engagements.get(identity),
                        speed_known=spectrum.peak_speed_out_rpm is not None,
                        static_allowable_MPa=(limits.root_MPa if root else limits.flank_MPa) if source else None,
                        static_source=source,required_static_safety=preset.min_S_F_static if root else preset.min_S_H_static,
                        static_upstream=static_state,fatigue_upstream=fatigue_state)
                    if spectrum.peak_reversing:
                        rec.flags.append("reversal_history_not_supported")
                        rec.missing_inputs.append("signed_peak_reversal_contact_history")
                    if root and root_unsupported: rec.flags.append("internal_or_thin_rim_root_not_supported")
                    if spectrum.peak_torque_out_Nm is None: rec.flags.append("peak_torque_assumed_from_preset_shock_factor")
                    key=(identity,mechanism)
                    prev=found.get(key)
                    if prev is None or rec.stress_peak_MPa>prev.stress_peak_MPa: found[key]=rec
        return list(found.values())

    def to_transmission_ir(
        self,
        *,
        ir_id: Optional[str] = None,
        speed_in_max_rpm: float = 6000.0,
        torque_in_max_Nm: float = 2.0,
        n_speed_points: int = 7,
        n_torque_points: int = 6,
        temp_C: float = 70.0,
        preset_name: str = "standard",
    ) -> TransmissionIR:
        """Export a motoring-only ``TransmissionIR`` loss map passing ``assert_transmission_ir``.

        Per Astra design review Blocker 2, ``TransmissionIR`` (v0.1 contract) covers
        motoring only (``backdrivable=False``); bidirectional regeneration requires
        evaluating ``Gearbox.evaluate_point(..., power_direction='reverse')``.
        """
        clean_id = (ir_id or self.name).lower().replace("-", "_").replace(" ", "_")
        if not clean_id or not clean_id[0].isalpha():
            clean_id = "gb_" + clean_id
        clean_id = clean_id[:48]

        n_ax = [speed_in_max_rpm * i / max(n_speed_points - 1, 1) for i in range(n_speed_points)]
        t_ax = [torque_in_max_Nm * j / max(n_torque_points - 1, 1) for j in range(n_torque_points)]

        mesh_tab: List[List[float]] = []
        brg_tab: List[List[float]] = []
        drag_tab: List[List[float]] = []

        rep = self.evaluate_point(
            input_speed_rpm=0.5 * speed_in_max_rpm,
            torque_Nm=0.5 * torque_in_max_Nm,
            temp_C=temp_C,
            preset_name=preset_name,
            compute_reverse_check=False,
        )

        if any(sr.kinematics.state == "failed" or not sr.kinematics.assembly_ok
               or any(not pg.valid_mesh for pg in sr.pair_geometries.values()) for sr in rep.stage_results):
            raise ValueError("Cannot export TransmissionIR for invalid assembly geometry")

        for n in n_ax:
            m_row: List[float] = []
            b_row: List[float] = []
            d_row: List[float] = []
            for t in t_ax:
                if n <= 0.0:
                    m_row.append(0.0)
                    b_row.append(0.0)
                    d_row.append(0.0)
                else:
                    pt = self.evaluate_point(
                        input_speed_rpm=n,
                        torque_Nm=t,
                        temp_C=temp_C,
                        preset_name=preset_name,
                        compute_reverse_check=False,
                    )
                    m_w = sum(sr.power_loss_mesh_W for sr in pt.stage_results)
                    b_w = sum(sr.power_loss_bearings_W for sr in pt.stage_results)
                    d_w = sum(sr.power_loss_churning_drag_W for sr in pt.stage_results)
                    m_row.append(max(m_w, 0.0))
                    b_row.append(max(b_w, 0.0))
                    d_row.append(max(d_w, 0.0))
            mesh_tab.append(m_row)
            brg_tab.append(b_row)
            drag_tab.append(d_row)

        # Monotonic clamp in case speed-dependent mu_mz drops slightly with speed
        for tab in (mesh_tab, brg_tab, drag_tab):
            for i in range(len(tab)):
                for j in range(1, len(tab[i])):
                    if tab[i][j] < tab[i][j - 1]:
                        tab[i][j] = tab[i][j - 1]
            for i in range(1, len(tab)):
                for j in range(len(tab[i])):
                    if tab[i][j] < tab[i - 1][j]:
                        tab[i][j] = tab[i - 1][j]

        ratio_abs = rep.abs_overall_ratio
        top_pt = self.evaluate_point(
            input_speed_rpm=speed_in_max_rpm,
            torque_Nm=torque_in_max_Nm,
            temp_C=temp_C,
            preset_name=preset_name,
            compute_reverse_check=False,
        )
        t_out_max = min(abs(top_pt.output_torque_Nm), torque_in_max_Nm * ratio_abs)

        return TransmissionIR(
            id=clean_id,
            name=self.name,
            kind="gearbox",
            ratio=ratio_abs,
            reverses_direction=rep.reverses_direction,
            speed_in_rpm=n_ax,
            torque_in_Nm=t_ax,
            loss_W={
                "mesh_loss": mesh_tab,
                "bearing_loss": brg_tab,
                "churning_drag": drag_tab,
            },
            inertia_in_kg_m2=rep.reflected_inertia_in_kg_m2,
            inertia_out_kg_m2=rep.reflected_inertia_in_kg_m2 * (ratio_abs ** 2),
            mass_kg=max(rep.mass_kg, 1.0e-3),
            backlash_deg=min(rep.backlash_out_deg, 30.0),
            backdrivable=False,
            limits=TransmissionLimits(
                torque_in_max_Nm=torque_in_max_Nm,
                torque_out_max_Nm=max(t_out_max, 1.0e-6),
                speed_in_max_rpm=speed_in_max_rpm,
                speed_out_max_rpm=speed_in_max_rpm / ratio_abs,
            ),
            input_shaft=ShaftInterface(kind="keyed", diameter_mm=self.input_shaft_mm),
            output_shaft=ShaftInterface(kind="flange", diameter_mm=self.output_shaft_mm),
            validity=Validity(
                ambient_C=(-20.0, 50.0),
                notes="Motoring-only v0.1; partial, unqualified rating coverage. Monotonic map is not a rated torque limit.",
            ),
            source=ModelSource(
                kind="analytical",
                reference="ISO/TR 14179-2 + VDI 2157 + SKF bearing model (gears stage 1)",
                validated_against=None,
            ),
            basis={
                "rating_state": rep.state,
                "rating_missing_inputs": "; ".join(rep.missing_inputs),
                "rating_flags": "; ".join(rep.flags),
                "ratio": "Willis planetary/pair gear ratio from tooth counts",
                "loss_W": "ISO/TR 14179-2 mesh friction + VDI 2157 planetary transform + SKF bearings",
                "inertia": "Kinetic-energy reflected cylinder blanks including planet spin + orbital translation",
                "mass": "Analytical cylindrical blank + carrier mass estimate",
            },
        )
