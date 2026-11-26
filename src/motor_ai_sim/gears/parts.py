# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Structural rating of all non-tooth loaded gearbox parts (Addition 2 items 12-13):
- Planet pins (bending, shear, bearing-seat surface pressure)
- Shafts at critical sections (DIN 743 level fatigue + static torsion/bending/notches)
- Splines and parallel keys (surface pressure and shear)
- Carrier (pin-hole bearing stress and simplified arm bending beam)
- Ring gear rim (ISO 6336-3 Y_B + simplified thin-ring hoop/bending between supports)
- Bolted or pinned ring fixation (bolt/pin shear and flange bearing pressure)
- Unified governing summary ranking all parts by minimum safety factor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .common import DictResultMixin, ValidityState, worst_state, require_number, validate_fields


@dataclass(frozen=True)
class PartStrengthRecord(DictResultMixin):
    """Standardized strength check record for one loaded component or gear check."""

    part_name: str
    governing_load_case: str              # e.g. "duty_peak", "static_shock", "duty_fatigue"
    min_safety_factor: Optional[float]
    required_safety_factor: Optional[float]
    is_simplified: bool
    passed: bool
    state: ValidityState
    stress_summary: Dict[str, Optional[float]] = field(default_factory=dict)
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "MechanicsOfMaterials"
    flags: List[str] = field(default_factory=list)


@dataclass
class PartsSummaryResult(DictResultMixin):
    """Summary of all loaded parts ordered by ascending minimum safety factor (governing first)."""

    parts: List[PartStrengthRecord]
    governing_part: Optional[PartStrengthRecord]
    min_safety_factor: Optional[float]
    all_passed: bool
    state: ValidityState
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "GearboxAllPartsStrengthSummary"
    flags: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. Planet pin
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlanetPinSpec(DictResultMixin):
    diameter_mm: float
    span_mm: float
    seat_length_mm: float
    mounting: str = "straddle"            # "straddle" | "cantilever"
    yield_strength_MPa: float = 850.0
    uts_MPa: float = 1100.0
    allowable_bearing_pressure_MPa: float = 120.0
    fatigue_endurance_bending_MPa: Optional[float] = None
    method: str = "PlanetPinSpec"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mounting not in ("straddle", "cantilever"):
            raise ValueError(f"Unknown pin mounting {self.mounting!r}")
        for fname in ("diameter_mm", "span_mm", "seat_length_mm", "yield_strength_MPa", "uts_MPa", "allowable_bearing_pressure_MPa"):
            val = float(getattr(self, fname))
            if not math.isfinite(val) or val <= 0.0:
                raise ValueError(f"{fname} must be finite and > 0, got {val!r}")
        if self.fatigue_endurance_bending_MPa is not None and (
            not math.isfinite(float(self.fatigue_endurance_bending_MPa))
            or self.fatigue_endurance_bending_MPa <= 0.0
        ):
            raise ValueError("fatigue_endurance_bending_MPa must be finite and > 0")


def rate_planet_pin(
    spec: PlanetPinSpec,
    *,
    F_pin_duty_N: float,
    F_pin_shock_N: Optional[float] = None,
    min_sf_fatigue: float = 1.5,
    min_sf_static: float = 1.4,
    n_bearings: int = 1,
    bearing_spacing_mm: Optional[float] = None,
) -> PartStrengthRecord:
    """Rate a planet pin for bending, shear, fatigue, seat surface pressure, and shock."""
    d = float(spec.diameter_mm)
    l_span = float(spec.span_mm)
    l_seat = float(spec.seat_length_mm)
    if not (math.isfinite(float(F_pin_duty_N)) and (F_pin_shock_N is None or math.isfinite(float(F_pin_shock_N)))):
        raise ValueError("Pin forces must be finite")
    if F_pin_duty_N < 0.0 or (F_pin_shock_N is not None and F_pin_shock_N < 0.0):
        raise ValueError("Pin force magnitudes must be >= 0")
    if n_bearings <= 0:
        raise ValueError(f"n_bearings must be >= 1, got {n_bearings}")

    area_mm2 = 0.25 * math.pi * d * d
    w_b_mm3 = math.pi * (d ** 3) / 32.0
    flags: List[str] = []

    def _eval(force_n: float) -> Tuple[float, float, float, float]:
        if spec.mounting == "cantilever":
            m_b_Nmm = 0.5 * force_n * l_span
            v_n = force_n
            p_seat = force_n / (d * l_seat)
        elif spec.mounting == "straddle":
            if n_bearings >= 2 and bearing_spacing_mm is not None and bearing_spacing_mm > 0.0:
                s_sep = min(float(bearing_spacing_mm), l_span)
                a_arm = max(0.5 * (l_span - s_sep), 0.0)
                m_b_Nmm = 0.5 * force_n * a_arm
                v_n = 0.5 * force_n
                p_seat = force_n / (n_bearings * d * l_seat)
                flags.append("multi_bearing_pin_spacing_modeled")
            else:
                # Simply supported beam with uniform load over active central seat length l_seat
                l_load = min(l_seat, l_span)
                m_b_Nmm = force_n * (2.0 * l_span - l_load) / 8.0
                v_n = 0.5 * force_n
                p_seat = force_n / (d * l_seat)
        else:
            raise ValueError(f"Unknown pin mounting {spec.mounting!r}")
        sigma_b = m_b_Nmm / w_b_mm3
        tau = (4.0 / 3.0) * v_n / area_mm2
        sigma_vm = math.sqrt(sigma_b ** 2 + 3.0 * tau ** 2)
        return sigma_b, tau, sigma_vm, p_seat

    sb_d, tau_d, svm_d, p_d = _eval(float(F_pin_duty_N))
    # Fatigue endurance (rotating/pulsating pin bending ~ 0.45 * Rm if not explicitly supplied)
    if spec.fatigue_endurance_bending_MPa is not None:
        sig_end = float(spec.fatigue_endurance_bending_MPa)
        is_simpl = False
    else:
        sig_end = 0.45 * spec.uts_MPa
        is_simpl = True
        flags.append("simplified_pin_endurance_0.45_Rm")

    sf_fat = sig_end / max(svm_d, 1e-9)
    sf_press_duty = spec.allowable_bearing_pressure_MPa / max(p_d, 1e-9)

    f_shock = float(F_pin_shock_N if F_pin_shock_N is not None else F_pin_duty_N)
    sb_s, tau_s, svm_s, p_s = _eval(f_shock)
    sf_stat = spec.yield_strength_MPa / max(svm_s, 1e-9)
    sf_press_stat = (1.5 * spec.allowable_bearing_pressure_MPa) / max(p_s, 1e-9)

    candidates = [
        ("duty_fatigue_bending", sf_fat, min_sf_fatigue),
        ("duty_seat_pressure", sf_press_duty, 1.0),
        ("static_shock_yield", sf_stat, min_sf_static),
        ("static_shock_seat_pressure", sf_press_stat, 1.0),
    ]
    gov_case, min_sf, req_sf = min(candidates, key=lambda c: c[1] / c[2])
    passed = all(sf >= req for _, sf, req in candidates)
    state: ValidityState = "estimated" if passed else "failed"

    return PartStrengthRecord(
        part_name="planet_pin",
        governing_load_case=gov_case,
        min_safety_factor=min_sf,
        required_safety_factor=req_sf,
        is_simplified=is_simpl,
        passed=passed,
        state=state,
        stress_summary={
            "sigma_b_duty_MPa": sb_d,
            "tau_duty_MPa": tau_d,
            "sigma_vm_duty_MPa": svm_d,
            "p_seat_duty_MPa": p_d,
            "sigma_vm_shock_MPa": svm_s,
            "sf_fatigue": sf_fat,
            "sf_static_yield": sf_stat,
            "sf_seat_pressure": sf_press_duty,
        },
        applicability_evidence={
            "mounting": spec.mounting,
            "d_mm": d,
            "span_mm": l_span,
            "n_bearings": n_bearings,
            "bearing_spacing_mm": bearing_spacing_mm,
        },
        method="BeamTheory_PlanetPin_StressAndPressure",
        flags=sorted(set(flags)),
    )


# ---------------------------------------------------------------------------
# 2. Shafts (DIN 743 level)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShaftSectionSpec(DictResultMixin):
    name: str
    diameter_mm: float
    notch_type: str = "smooth"            # "smooth" | "shoulder" | "keyway" | "spline"
    yield_strength_MPa: float = 650.0
    uts_MPa: float = 950.0
    K_f_bending: Optional[float] = None
    K_f_torsion: Optional[float] = None
    fillet_radius_mm: Optional[float] = None
    step_diameter_D_mm: Optional[float] = None
    method: str = "ShaftSectionSpec_DIN743"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for fname in ("diameter_mm", "yield_strength_MPa", "uts_MPa"):
            val = float(getattr(self, fname))
            if not math.isfinite(val) or val <= 0.0:
                raise ValueError(f"{fname} must be finite and > 0, got {val!r}")
        if self.notch_type not in ("smooth", "shoulder", "keyway", "spline"):
            raise ValueError(f"Unknown notch_type {self.notch_type!r}")


def _din743_screening_kf(spec: ShaftSectionSpec) -> Tuple[float, float, float, bool]:
    """Return (K_f_b, K_f_t, K_size_2, is_simplified) at DIN 743 screening level."""
    d = spec.diameter_mm
    # DIN 743-2 technological / geometrical size factor K2 approximation for bending/torsion
    k_size = max(1.0 - 0.2 * math.log10(max(d, 7.5) / 7.5), 0.70)
    if spec.K_f_bending is not None and spec.K_f_torsion is not None:
        return float(spec.K_f_bending), float(spec.K_f_torsion), k_size, False

    if spec.notch_type == "smooth":
        return 1.0, 1.0, k_size, False
    if spec.notch_type == "shoulder":
        if spec.fillet_radius_mm and spec.step_diameter_D_mm and spec.step_diameter_D_mm > d:
            # Peterson/DIN 743 approximate shoulder Kt scaling
            r_over_d = max(spec.fillet_radius_mm / d, 0.01)
            kf_b = min(1.0 + 0.55 / math.sqrt(r_over_d) * 0.35, 2.8)
            kf_t = min(1.0 + 0.40 / math.sqrt(r_over_d) * 0.30, 2.2)
            return kf_b, kf_t, k_size, True
        return 1.8, 1.45, k_size, True
    if spec.notch_type == "keyway":
        return 2.0, 1.7, k_size, True
    if spec.notch_type == "spline":
        return 1.9, 1.8, k_size, True
    raise ValueError(f"Unknown notch_type {spec.notch_type!r}")


def rate_shaft_din743(
    spec: ShaftSectionSpec,
    *,
    torque_duty_Nm: float,
    bending_moment_duty_Nm: float = 0.0,
    axial_force_duty_N: float = 0.0,
    torque_shock_Nm: Optional[float] = None,
    bending_moment_shock_Nm: Optional[float] = None,
    axial_force_shock_N: Optional[float] = None,
    reversing_torque: bool = True,
    min_sf_fatigue: float = 1.5,
    min_sf_static: float = 1.2,
) -> PartStrengthRecord:
    """Rate a shaft critical section at DIN 743 level (fatigue + static yield)."""
    d = float(spec.diameter_mm)
    if d <= 0.0:
        raise ValueError("Shaft diameter_mm must be > 0")
    for fname, val in (
        ("torque_duty_Nm", torque_duty_Nm),
        ("bending_moment_duty_Nm", bending_moment_duty_Nm),
        ("axial_force_duty_N", axial_force_duty_N),
        ("min_sf_fatigue", min_sf_fatigue),
        ("min_sf_static", min_sf_static),
    ):
        if not math.isfinite(float(val)):
            raise ValueError(f"{fname} must be finite, got {val!r}")
    for opt_name, opt_val in (
        ("torque_shock_Nm", torque_shock_Nm),
        ("bending_moment_shock_Nm", bending_moment_shock_Nm),
        ("axial_force_shock_N", axial_force_shock_N),
    ):
        if opt_val is not None and not math.isfinite(float(opt_val)):
            raise ValueError(f"{opt_name} must be finite, got {opt_val!r}")
    area = 0.25 * math.pi * d * d
    w_b = math.pi * (d ** 3) / 32.0
    w_t = math.pi * (d ** 3) / 16.0

    kf_b, kf_t, k2, is_simpl = _din743_screening_kf(spec)
    flags: List[str] = []
    if is_simpl:
        flags.append("simplified_DIN743_notch_factors")
    flags.append("DIN743_level_screening_surface_factor_K_Fsigma_assumed_0.9")

    # Material endurance limits (DIN 743-1: sigma_bW ~ 0.5*Rm, tau_tW ~ 0.29*Rm)
    sig_bw = 0.50 * spec.uts_MPa
    tau_tw = 0.29 * spec.uts_MPa
    k_surf = 0.90
    sig_bwk = sig_bw * k2 * k_surf / kf_b
    tau_twk = tau_tw * k2 * k_surf / kf_t

    # Duty alternating and mean stresses (rotating shaft has fully reversed bending!)
    sig_ba = abs(float(bending_moment_duty_Nm)) * 1000.0 / w_b
    sig_am = float(axial_force_duty_N) / area
    tau_t_nom = abs(float(torque_duty_Nm)) * 1000.0 / w_t
    if reversing_torque:
        tau_ta = tau_t_nom
        tau_tm = 0.0
    else:
        tau_ta = 0.5 * tau_t_nom
        tau_tm = 0.5 * tau_t_nom

    # DIN 743-1 modified Goodman mean-stress sensitivity psi_sigma, psi_tau
    psi_sig = 0.25 * spec.uts_MPa / 1000.0
    psi_tau = 0.15 * spec.uts_MPa / 1000.0
    sig_eq_a = sig_ba + psi_sig * abs(sig_am)
    tau_eq_a = tau_ta + psi_tau * abs(tau_tm)

    denom_fat = math.hypot(sig_eq_a / max(sig_bwk, 1e-6), tau_eq_a / max(tau_twk, 1e-6))
    sf_fat = (1.0 / denom_fat) if denom_fat > 1e-12 else 999.0

    # Static / shock von Mises yield check (DIN 743-1 cl. 4)
    t_sh = abs(float(torque_shock_Nm if torque_shock_Nm is not None else torque_duty_Nm))
    m_sh = abs(float(bending_moment_shock_Nm if bending_moment_shock_Nm is not None else bending_moment_duty_Nm))
    fa_sh = abs(float(axial_force_shock_N if axial_force_shock_N is not None else axial_force_duty_N))
    sig_sh = (m_sh * 1000.0 / w_b) + (fa_sh / area)
    tau_sh = t_sh * 1000.0 / w_t
    sig_vm_sh = math.sqrt(sig_sh ** 2 + 3.0 * tau_sh ** 2)
    sf_stat = spec.yield_strength_MPa / max(sig_vm_sh, 1e-9)

    candidates = [
        ("duty_fatigue_DIN743", sf_fat, min_sf_fatigue),
        ("static_shock_yield", sf_stat, min_sf_static),
    ]
    gov_case, min_sf, req_sf = min(candidates, key=lambda c: c[1] / c[2])
    passed = all(sf >= req for _, sf, req in candidates)
    state: ValidityState = "estimated" if passed else "failed"

    return PartStrengthRecord(
        part_name=spec.name,
        governing_load_case=gov_case,
        min_safety_factor=min_sf,
        required_safety_factor=req_sf,
        is_simplified=True,
        passed=passed,
        state=state,
        stress_summary={
            "sigma_ba_MPa": sig_ba,
            "tau_ta_MPa": tau_ta,
            "sigma_bWK_MPa": sig_bwk,
            "tau_tWK_MPa": tau_twk,
            "sigma_vm_shock_MPa": sig_vm_sh,
            "sf_fatigue": sf_fat,
            "sf_static_yield": sf_stat,
        },
        applicability_evidence={"notch_type": spec.notch_type, "K_fb": kf_b, "K_ft": kf_t, "K2_size": k2},
        method="DIN743_Level_ShaftFatigueAndStatic",
        flags=flags,
    )


# ---------------------------------------------------------------------------
# 3. Splines / Keys
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KeyOrSplineSpec(DictResultMixin):
    name: str
    kind: str                             # "key" | "spline"
    pitch_or_shaft_diameter_mm: float
    effective_length_mm: float
    allowable_pressure_MPa: float = 100.0
    shear_yield_MPa: float = 350.0
    # Key fields (DIN 6885)
    key_width_mm: float = 4.0
    key_height_mm: float = 4.0
    n_keys: int = 1
    # Spline fields (DIN 5480 / ISO 4156)
    n_teeth: int = 12
    tooth_contact_height_mm: float = 1.0
    load_sharing_factor: float = 0.75     # K_lambda (fraction of teeth sharing load)
    method: str = "KeyOrSplineSpec"
    flags: List[str] = field(default_factory=list)


def rate_key_or_spline(
    spec: KeyOrSplineSpec,
    *,
    torque_duty_Nm: float,
    torque_shock_Nm: Optional[float] = None,
    min_sf_duty: float = 1.2,
    min_sf_static: float = 1.1,
) -> PartStrengthRecord:
    """Rate a parallel key (DIN 6885) or involute spline for surface pressure and shear."""
    d = float(spec.pitch_or_shaft_diameter_mm)
    l_eff = float(spec.effective_length_mm)
    if d <= 0.0 or l_eff <= 0.0:
        raise ValueError("Diameter and effective_length_mm must be > 0")

    def _eval(t_nm: float) -> Tuple[float, float]:
        f_tan = 2000.0 * abs(float(t_nm)) / d
        if spec.kind == "key":
            h_contact = 0.45 * spec.key_height_mm
            p_surf = f_tan / max(spec.n_keys * h_contact * l_eff, 1e-6)
            tau_sh = f_tan / max(spec.n_keys * spec.key_width_mm * l_eff, 1e-6)
        elif spec.kind == "spline":
            p_surf = f_tan / max(
                spec.n_teeth * spec.load_sharing_factor * spec.tooth_contact_height_mm * l_eff, 1e-6
            )
            # Tooth root shear approx half circular pitch (~ pi*d/(2*n_teeth))
            t_chord = 0.5 * math.pi * d / max(spec.n_teeth, 1)
            tau_sh = f_tan / max(spec.n_teeth * spec.load_sharing_factor * t_chord * l_eff, 1e-6)
        else:
            raise ValueError(f"Unknown kind {spec.kind!r}")
        return p_surf, tau_sh

    p_d, tau_d = _eval(torque_duty_Nm)
    t_sh = torque_shock_Nm if torque_shock_Nm is not None else torque_duty_Nm
    p_s, tau_s = _eval(t_sh)

    sf_p_duty = spec.allowable_pressure_MPa / max(p_d, 1e-9)
    sf_tau_duty = spec.shear_yield_MPa / max(tau_d, 1e-9)
    sf_p_stat = (1.35 * spec.allowable_pressure_MPa) / max(p_s, 1e-9)
    sf_tau_stat = spec.shear_yield_MPa / max(tau_s, 1e-9)

    candidates = [
        ("duty_surface_pressure", sf_p_duty, min_sf_duty),
        ("duty_shear", sf_tau_duty, min_sf_duty),
        ("static_shock_pressure", sf_p_stat, min_sf_static),
        ("static_shock_shear", sf_tau_stat, min_sf_static),
    ]
    gov_case, min_sf, req_sf = min(candidates, key=lambda c: c[1] / c[2])
    passed = all(sf >= req for _, sf, req in candidates)
    state: ValidityState = "estimated" if passed else "failed"
    return PartStrengthRecord(
        part_name=spec.name,
        governing_load_case=gov_case,
        min_safety_factor=min_sf,
        required_safety_factor=req_sf,
        is_simplified=True,
        passed=passed,
        state=state,
        stress_summary={
            "p_duty_MPa": p_d,
            "tau_duty_MPa": tau_d,
            "p_shock_MPa": p_s,
            "tau_shock_MPa": tau_s,
            "sf_pressure_duty": sf_p_duty,
            "sf_shear_shock": sf_tau_stat,
        },
        applicability_evidence={"kind": spec.kind, "d_mm": d, "L_eff_mm": l_eff},
        method="DIN6885_DIN5480_SurfacePressureAndShear",
        flags=["simplified:uniform_contact_and_chord_shear_screening"],
    )


# ---------------------------------------------------------------------------
# 4. Carrier (pin-hole bearing & simple arm bending)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CarrierSpec(DictResultMixin):
    pin_diameter_mm: float
    plate_thickness_mm: float
    n_plates: int = 2                     # 2 = straddle double-wall, 1 = cantilever single-wall
    arm_width_mm: float = 12.0            # circumferential width of one bridge/post
    arm_thickness_mm: float = 10.0        # radial thickness of bridge/post
    arm_length_mm: float = 15.0           # axial span of post between walls
    n_posts: int = 3                      # bridges connecting front/back carrier plates
    yield_strength_MPa: float = 400.0
    allowable_hole_pressure_MPa: float = 120.0
    method: str = "CarrierSpec"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self):
        validate_fields(self, positive=('pin_diameter_mm', 'plate_thickness_mm', 'arm_width_mm', 'arm_thickness_mm', 'arm_length_mm', 'yield_strength_MPa', 'allowable_hole_pressure_MPa'), counts=('n_plates', 'n_posts'))
        if self.n_plates not in (1, 2):
            raise ValueError("n_plates must be 1 or 2")


def rate_carrier(
    spec: CarrierSpec,
    *,
    F_pin_worst_duty_N: float,
    T_carrier_duty_Nm: float,
    orbit_radius_mm: float,
    F_pin_worst_shock_N: Optional[float] = None,
    T_carrier_shock_Nm: Optional[float] = None,
    min_sf_duty: float = 1.5,
    min_sf_static: float = 1.4,
) -> PartStrengthRecord:
    """Rate planetary carrier pin-hole bearing pressure and post/arm bending (simple beam)."""
    if F_pin_worst_duty_N is not None:
        require_number("F_pin_worst_duty_N", F_pin_worst_duty_N, minimum=0)
    if F_pin_worst_shock_N is not None:
        require_number("F_pin_worst_shock_N", F_pin_worst_shock_N, minimum=0)
    if T_carrier_duty_Nm is not None:
        require_number("T_carrier_duty_Nm", T_carrier_duty_Nm)
    if T_carrier_shock_Nm is not None:
        require_number("T_carrier_shock_Nm", T_carrier_shock_Nm)
    require_number("orbit_radius_mm", orbit_radius_mm, positive=True)
    require_number("min_sf_duty", min_sf_duty, positive=True)
    require_number("min_sf_static", min_sf_static, positive=True)
    flags = ["simplified:simple_beam_carrier_model"]
    a_hole = max(spec.n_plates * spec.plate_thickness_mm * spec.pin_diameter_mm, 1e-6)
    p_hole_d = float(F_pin_worst_duty_N) / a_hole

    # Carrier bridge posts transmit torque between front/back plates (2 plates share 0.5 each; 1 plate takes 100%)
    plate_share = 0.5 if spec.n_plates >= 2 else 1.0
    w_arm = (spec.arm_thickness_mm * (spec.arm_width_mm ** 2)) / 6.0
    f_tan_total_d = 1000.0 * abs(float(T_carrier_duty_Nm)) / max(orbit_radius_mm, 1.0)
    f_post_d = (plate_share * f_tan_total_d) / max(spec.n_posts, 1)
    # Double-clamped post bending moment ~ F * L / 2 (or cantilever F * L)
    m_post_d = f_post_d * (0.5 * spec.arm_length_mm if spec.n_plates >= 2 else spec.arm_length_mm)
    sig_arm_d = m_post_d / max(w_arm, 1e-6)

    f_pin_sh = float(F_pin_worst_shock_N if F_pin_worst_shock_N is not None else F_pin_worst_duty_N)
    t_c_sh = abs(float(T_carrier_shock_Nm if T_carrier_shock_Nm is not None else T_carrier_duty_Nm))
    p_hole_s = f_pin_sh / a_hole
    f_tan_total_s = 1000.0 * t_c_sh / max(orbit_radius_mm, 1.0)
    f_post_s = (plate_share * f_tan_total_s) / max(spec.n_posts, 1)
    m_post_s = f_post_s * (0.5 * spec.arm_length_mm if spec.n_plates >= 2 else spec.arm_length_mm)
    sig_arm_s = m_post_s / max(w_arm, 1e-6)

    sf_hole_d = spec.allowable_hole_pressure_MPa / max(p_hole_d, 1e-9)
    sf_arm_d = (0.65 * spec.yield_strength_MPa) / max(sig_arm_d, 1e-9)  # endurance proxy
    sf_hole_s = (1.4 * spec.allowable_hole_pressure_MPa) / max(p_hole_s, 1e-9)
    sf_arm_s = spec.yield_strength_MPa / max(sig_arm_s, 1e-9)

    candidates = [
        ("duty_pin_hole_pressure", sf_hole_d, 1.0),
        ("duty_arm_bending", sf_arm_d, min_sf_duty),
        ("static_shock_pin_hole", sf_hole_s, 1.0),
        ("static_shock_arm_yield", sf_arm_s, min_sf_static),
    ]
    gov_case, min_sf, req_sf = min(candidates, key=lambda c: c[1] / c[2])
    passed = all(sf >= req for _, sf, req in candidates)
    state: ValidityState = "estimated" if passed else "failed"

    return PartStrengthRecord(
        part_name="carrier",
        governing_load_case=gov_case,
        min_safety_factor=min_sf,
        required_safety_factor=req_sf,
        is_simplified=True,
        passed=passed,
        state=state,
        stress_summary={
            "p_hole_duty_MPa": p_hole_d,
            "sigma_arm_duty_MPa": sig_arm_d,
            "p_hole_shock_MPa": p_hole_s,
            "sigma_arm_shock_MPa": sig_arm_s,
            "sf_hole_duty": sf_hole_d,
            "sf_arm_duty": sf_arm_d,
            "sf_arm_static": sf_arm_s,
        },
        applicability_evidence={"n_plates": spec.n_plates, "n_posts": spec.n_posts},
        method="SimpleBeam_Carrier_Model",
        flags=flags,
    )


# ---------------------------------------------------------------------------
# 5. Ring gear rim & bolted/pinned fixation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RingRimFixationSpec(DictResultMixin):
    d_f_ring_mm: float                    # ring root diameter [mm]
    rim_thickness_mm: float               # s_R radial rim wall outside root [mm]
    b_ring_mm: float                      # face width [mm]
    m_n_mm: float                         # module [mm]
    yield_strength_MPa: float = 750.0
    uts_MPa: float = 1000.0
    # Fixation (bolts or dowel pins around housing bolt circle)
    fixation_kind: str = "bolts"          # "bolts" | "pins"
    n_fasteners: int = 8
    fastener_diameter_mm: float = 4.0
    bolt_circle_diameter_mm: float = 75.0
    flange_thickness_mm: float = 5.0
    fastener_shear_allow_MPa: float = 350.0
    flange_bearing_allow_MPa: float = 200.0
    method: str = "RingRimFixationSpec"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self):
        validate_fields(self, positive=('d_f_ring_mm', 'rim_thickness_mm', 'b_ring_mm', 'm_n_mm', 'yield_strength_MPa', 'uts_MPa', 'fastener_diameter_mm', 'bolt_circle_diameter_mm', 'flange_thickness_mm', 'fastener_shear_allow_MPa', 'flange_bearing_allow_MPa'), counts=('n_fasteners',))
        if self.fixation_kind not in ("bolts", "pins"):
            raise ValueError("fixation_kind must be bolts or pins")


def rate_ring_rim_and_fixation(
    spec: RingRimFixationSpec,
    *,
    F_t_ring_branch_N: float,
    F_r_ring_branch_N: float,
    T_ring_duty_Nm: float,
    F_t_ring_shock_N: Optional[float] = None,
    F_r_ring_shock_N: Optional[float] = None,
    T_ring_shock_Nm: Optional[float] = None,
    min_sf_duty: float = 1.5,
    min_sf_static: float = 1.4,
) -> List[PartStrengthRecord]:
    """Rate ring gear thin-rim hoop/bending stress (plus Y_B) and ring bolt/pin fixation."""
    if F_t_ring_branch_N is not None:
        require_number("F_t_ring_branch_N", F_t_ring_branch_N, minimum=0)
    if F_r_ring_branch_N is not None:
        require_number("F_r_ring_branch_N", F_r_ring_branch_N, minimum=0)
    if F_t_ring_shock_N is not None:
        require_number("F_t_ring_shock_N", F_t_ring_shock_N, minimum=0)
    if F_r_ring_shock_N is not None:
        require_number("F_r_ring_shock_N", F_r_ring_shock_N, minimum=0)
    if T_ring_duty_Nm is not None:
        require_number("T_ring_duty_Nm", T_ring_duty_Nm)
    if T_ring_shock_Nm is not None:
        require_number("T_ring_shock_Nm", T_ring_shock_Nm)
    require_number("min_sf_duty", min_sf_duty, positive=True)
    require_number("min_sf_static", min_sf_static, positive=True)
    s_r = float(spec.rim_thickness_mm)
    b = float(spec.b_ring_mm)
    r_rim = 0.5 * spec.d_f_ring_mm + 0.5 * s_r
    rim_flags = ["simplified:thin_ring_curved_beam_between_supports"]

    # ISO 6336-3 cl. 9 rim thickness scope check for internal gears: s_R > 1.75 * m_n
    below_iso_scope = s_r <= 1.75 * spec.m_n_mm
    if below_iso_scope:
        rim_flags.append("ring_rim_below_1.75mn_thin_ring_ovalization_critical")

    theta_span = 2.0 * math.pi / max(spec.n_fasteners, 2)
    # Approximate ring bending between adjacent supports under local radial/tangential mesh force:
    # Clamped curved segment M_max ~ 0.125 * F_r * R * sin(theta_span/2) + hoop F_t / (b * s_R)
    a_rim = max(b * s_r, 1e-6)
    w_rim = max(b * (s_r ** 2) / 6.0, 1e-6)

    def _rim_stress(ft: float, fr: float) -> Tuple[float, float, float]:
        sig_hoop = ft / a_rim
        m_bend = 0.125 * fr * r_rim * max(theta_span, 0.2)
        sig_bend = m_bend / w_rim
        return sig_hoop, sig_bend, sig_hoop + sig_bend

    sh_d, sb_d, stot_d = _rim_stress(float(F_t_ring_branch_N), float(F_r_ring_branch_N))
    ft_sh = float(F_t_ring_shock_N if F_t_ring_shock_N is not None else F_t_ring_branch_N)
    fr_sh = float(F_r_ring_shock_N if F_r_ring_shock_N is not None else F_r_ring_branch_N)
    sh_s, sb_s, stot_s = _rim_stress(ft_sh, fr_sh)

    # Pulsating fatigue allowance ~ 0.4 * Rm
    sf_rim_d = (0.40 * spec.uts_MPa) / max(stot_d, 1e-9)
    sf_rim_s = spec.yield_strength_MPa / max(stot_s, 1e-9)
    gov_r, min_sf_r, req_r = min(
        [("duty_rim_hoop_bending", sf_rim_d, min_sf_duty), ("static_shock_rim_yield", sf_rim_s, min_sf_static)],
        key=lambda c: c[1] / c[2],
    )
    if below_iso_scope:
        ok_r = False
        state_r: ValidityState = "unsupported"
        rim_missing = ["FEA_thin_rim_bending_verification"]
    else:
        ok_r = (sf_rim_d >= min_sf_duty) and (sf_rim_s >= min_sf_static)
        state_r = "estimated" if ok_r else "failed"
        rim_missing = []
    rim_rec = PartStrengthRecord(
        part_name="ring_rim",
        governing_load_case=gov_r,
        min_safety_factor=min_sf_r,
        required_safety_factor=req_r,
        is_simplified=True,
        passed=ok_r,
        state=state_r,
        stress_summary={
            "sigma_hoop_duty_MPa": sh_d,
            "sigma_bend_duty_MPa": sb_d,
            "sigma_total_duty_MPa": stot_d,
            "sigma_total_shock_MPa": stot_s,
            "s_R_over_mn": s_r / spec.m_n_mm,
        },
        applicability_evidence={"s_R_mm": s_r, "m_n_mm": spec.m_n_mm, "n_supports": spec.n_fasteners},
        missing_inputs=rim_missing,
        method="ThinRing_HoopAndSupportBending_Estimate",
        flags=rim_flags,
    )

    # Fixation (bolts or pins in shear + flange bearing pressure)
    a_fast = 0.25 * math.pi * (spec.fastener_diameter_mm ** 2)
    r_pcd = 0.5 * spec.bolt_circle_diameter_mm

    def _fix_stress(t_nm: float) -> Tuple[float, float]:
        f_per = (1000.0 * abs(float(t_nm)) / max(r_pcd, 1.0)) / max(spec.n_fasteners, 1)
        tau_f = f_per / max(a_fast, 1e-6)
        p_fl = f_per / max(spec.fastener_diameter_mm * spec.flange_thickness_mm, 1e-6)
        return tau_f, p_fl

    tau_fd, p_fd = _fix_stress(T_ring_duty_Nm)
    t_sh = T_ring_shock_Nm if T_ring_shock_Nm is not None else T_ring_duty_Nm
    tau_fs, p_fs = _fix_stress(t_sh)

    sf_f_tau_d = spec.fastener_shear_allow_MPa / max(tau_fd, 1e-9)
    sf_f_p_d = spec.flange_bearing_allow_MPa / max(p_fd, 1e-9)
    sf_f_tau_s = (1.25 * spec.fastener_shear_allow_MPa) / max(tau_fs, 1e-9)
    sf_f_p_s = (1.25 * spec.flange_bearing_allow_MPa) / max(p_fs, 1e-9)

    cands_f = [
        ("duty_fastener_shear", sf_f_tau_d, min_sf_duty),
        ("duty_flange_bearing", sf_f_p_d, 1.0),
        ("static_shock_shear", sf_f_tau_s, min_sf_static),
        ("static_shock_flange_bearing", sf_f_p_s, 1.0),
    ]
    gov_f, min_sf_f, req_f = min(cands_f, key=lambda c: c[1] / c[2])
    ok_f = all(sf >= req for _, sf, req in cands_f)
    fix_rec = PartStrengthRecord(
        part_name=f"ring_{spec.fixation_kind}",
        governing_load_case=gov_f,
        min_safety_factor=min_sf_f,
        required_safety_factor=req_f,
        is_simplified=True,
        passed=ok_f,
        state=("estimated" if ok_f else "failed"),
        stress_summary={
            "tau_duty_MPa": tau_fd,
            "p_flange_duty_MPa": p_fd,
            "tau_shock_MPa": tau_fs,
            "p_flange_shock_MPa": p_fs,
        },
        applicability_evidence={"fixation_kind": spec.fixation_kind, "n_fasteners": spec.n_fasteners},
        method="BoltOrPinShear_And_FlangeBearing",
        flags=["simplified:uniform_fastener_load_share_no_bolt_bending"],
    )

    return [rim_rec, fix_rec]


# ---------------------------------------------------------------------------
# 6. Summary aggregator
# ---------------------------------------------------------------------------

def summarize_part_strengths(records: List[PartStrengthRecord]) -> PartsSummaryResult:
    """Sort all rated components by ascending safety-factor margin (governing weakest first)."""
    def _sort_key(r: PartStrengthRecord) -> Tuple[int, float]:
        # Unsupported / failed first, then lowest normalized safety factor
        if r.state == "failed":
            rank = 0
        elif r.state == "unsupported":
            rank = 1
        else:
            rank = 2
        if r.min_safety_factor is None:
            return (rank, -1.0)
        req = r.required_safety_factor or 1.0
        return (rank, r.min_safety_factor / max(req, 1e-6))

    ordered = sorted(records, key=_sort_key)
    gov = ordered[0] if ordered else None
    numeric_sfs = [r.min_safety_factor for r in ordered if r.min_safety_factor is not None]
    min_sf = min(numeric_sfs) if numeric_sfs else None
    all_ok = bool(ordered) and all(r.passed and r.state not in ("unsupported", "failed") for r in ordered)
    overall_state = worst_state([r.state for r in ordered]) if ordered else "unsupported"
    flags = sorted(set(fl for r in ordered for fl in r.flags))
    missing = sorted(set(m for r in ordered for m in r.missing_inputs))

    return PartsSummaryResult(
        parts=ordered,
        governing_part=gov,
        min_safety_factor=min_sf,
        all_passed=all_ok,
        state=overall_state,
        applicability_evidence={"n_parts_rated": len(ordered)},
        missing_inputs=missing,
        flags=flags,
    )
