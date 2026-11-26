# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Gear lubrication, viscosity-temperature (ISO 3104 / ASTM D341), EHL film
thickness, ISO 6336-2 Z_L/Z_v/Z_R factors, scuffing & micropitting screening,
and sealed grease thermal-balance estimation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from motor_ai_sim.bearings import walther_nu

from .common import DictResultMixin, ValidityState
from .geometry import PairGeometryResult


class UnknownGearLubricantError(KeyError):
    """Raised when a requested gear lubricant name is unknown."""


@dataclass(frozen=True)
class GearLubricant(DictResultMixin):
    """Oil or grease card with ASTM D341 / ISO 3104 Walther viscosity points."""

    name: str
    kind: str                               # "oil" | "grease"
    nu40_mm2_s: float                       # kinematic viscosity at 40 C [mm^2/s]
    nu100_mm2_s: float                      # kinematic viscosity at 100 C [mm^2/s]
    density_15C_kg_m3: float = 875.0
    base_oil_type: str = "mineral"          # "mineral" | "PAO" | "ester" | "polyglycol"
    nlgi_grade: Optional[float] = None      # e.g. 2.0 for NLGI 2 grease
    thickener: Optional[str] = None
    pressure_viscosity_coeff_GPa_inv: Optional[float] = None
    X_L_friction: float = 1.0               # ISO/TR 14179-2 lubricant factor
    scuffing_temp_limit_C: Optional[float] = None  # flash/contact limit if tested
    fzg_load_stage: Optional[int] = None
    temp_min_C: float = -30.0
    temp_max_C: float = 120.0
    method: str = "GearLubricant_ISO3104_ASTMD341"
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in ("oil", "grease"):
            raise ValueError(f"Lubricant kind must be 'oil' or 'grease', got {self.kind!r}")
        for fname in ("nu40_mm2_s", "nu100_mm2_s", "density_15C_kg_m3", "X_L_friction", "temp_min_C", "temp_max_C"):
            if not math.isfinite(float(getattr(self, fname))):
                raise ValueError(f"{fname} must be finite, got {getattr(self, fname)!r}")
        if self.nu40_mm2_s <= 0.0 or self.nu100_mm2_s <= 0.0 or self.density_15C_kg_m3 <= 0.0 or self.X_L_friction <= 0.0:
            raise ValueError("Viscosities, density, and X_L_friction must be > 0")
        if self.temp_min_C >= self.temp_max_C:
            raise ValueError("temp_min_C must be < temp_max_C")
        if self.pressure_viscosity_coeff_GPa_inv is not None and (
            not math.isfinite(float(self.pressure_viscosity_coeff_GPa_inv))
            or self.pressure_viscosity_coeff_GPa_inv <= 0.0
        ):
            raise ValueError("pressure_viscosity_coeff_GPa_inv must be finite and > 0")
        if self.scuffing_temp_limit_C is not None and not math.isfinite(float(self.scuffing_temp_limit_C)):
            raise ValueError("scuffing_temp_limit_C must be finite")

    def kinematic_viscosity_mm2_s(self, temp_C: float) -> float:
        """Interpolate kinematic viscosity [mm^2/s] at ``temp_C`` via Walther / ASTM D341."""
        return walther_nu(self.nu40_mm2_s, self.nu100_mm2_s, float(temp_C))

    def density_kg_m3(self, temp_C: float) -> float:
        """Temperature-adjusted lubricant density [kg/m^3] (~0.65 kg/(m^3*K))."""
        return max(self.density_15C_kg_m3 - 0.65 * (float(temp_C) - 15.0), 500.0)

    def dynamic_viscosity_mPas(self, temp_C: float) -> float:
        """Dynamic viscosity [mPa*s] = nu [mm^2/s] * rho [kg/m^3] * 1e-3."""
        return self.kinematic_viscosity_mm2_s(temp_C) * self.density_kg_m3(temp_C) * 1.0e-3

    def effective_alpha_p_GPa_inv(self) -> Tuple[float, bool]:
        """Return pressure-viscosity coefficient [GPa^-1] and whether it is estimated."""
        if self.pressure_viscosity_coeff_GPa_inv is not None:
            return float(self.pressure_viscosity_coeff_GPa_inv), False
        defaults = {
            "mineral": 20.0,
            "PAO": 16.0,
            "ester": 15.0,
            "polyglycol": 14.0,
        }
        return defaults.get(self.base_oil_type, 18.0), True


BUILTIN_GEAR_LUBRICANTS: Dict[str, GearLubricant] = {
    "ISO_VG_100_mineral": GearLubricant(
        name="ISO_VG_100_mineral",
        kind="oil",
        nu40_mm2_s=100.0,
        nu100_mm2_s=11.4,
        density_15C_kg_m3=885.0,
        base_oil_type="mineral",
        pressure_viscosity_coeff_GPa_inv=21.0,
        X_L_friction=1.0,
        scuffing_temp_limit_C=210.0,
        fzg_load_stage=10,
        temp_min_C=-10.0,
        temp_max_C=110.0,
    ),
    "ISO_VG_220_mineral": GearLubricant(
        name="ISO_VG_220_mineral",
        kind="oil",
        nu40_mm2_s=220.0,
        nu100_mm2_s=19.0,
        density_15C_kg_m3=895.0,
        base_oil_type="mineral",
        pressure_viscosity_coeff_GPa_inv=22.0,
        X_L_friction=1.0,
        scuffing_temp_limit_C=230.0,
        fzg_load_stage=11,
        temp_min_C=-5.0,
        temp_max_C=115.0,
    ),
    "ISO_VG_220_PAO": GearLubricant(
        name="ISO_VG_220_PAO",
        kind="oil",
        nu40_mm2_s=220.0,
        nu100_mm2_s=25.5,
        density_15C_kg_m3=855.0,
        base_oil_type="PAO",
        pressure_viscosity_coeff_GPa_inv=16.5,
        X_L_friction=0.85,
        scuffing_temp_limit_C=260.0,
        fzg_load_stage=12,
        temp_min_C=-35.0,
        temp_max_C=140.0,
    ),
    "ISO_VG_68_PAO": GearLubricant(
        name="ISO_VG_68_PAO",
        kind="oil",
        nu40_mm2_s=68.0,
        nu100_mm2_s=10.8,
        density_15C_kg_m3=845.0,
        base_oil_type="PAO",
        pressure_viscosity_coeff_GPa_inv=15.5,
        X_L_friction=0.85,
        scuffing_temp_limit_C=240.0,
        fzg_load_stage=11,
        temp_min_C=-40.0,
        temp_max_C=130.0,
    ),
    "ROBOT_GREASE_NLGI1_PAO": GearLubricant(
        name="ROBOT_GREASE_NLGI1_PAO",
        kind="grease",
        nu40_mm2_s=100.0,
        nu100_mm2_s=14.5,
        density_15C_kg_m3=880.0,
        base_oil_type="PAO",
        nlgi_grade=1.0,
        thickener="lithium_complex",
        pressure_viscosity_coeff_GPa_inv=16.0,
        X_L_friction=0.90,
        scuffing_temp_limit_C=200.0,
        temp_min_C=-35.0,
        temp_max_C=120.0,
        flags=["grease_base_oil_properties_only"],
    ),
    "GEAR_GREASE_NLGI2_MINERAL": GearLubricant(
        name="GEAR_GREASE_NLGI2_MINERAL",
        kind="grease",
        nu40_mm2_s=110.0,
        nu100_mm2_s=11.0,
        density_15C_kg_m3=890.0,
        base_oil_type="mineral",
        nlgi_grade=2.0,
        thickener="lithium",
        pressure_viscosity_coeff_GPa_inv=20.0,
        X_L_friction=1.0,
        scuffing_temp_limit_C=185.0,
        temp_min_C=-30.0,
        temp_max_C=110.0,
        flags=["grease_base_oil_properties_only"],
    ),
}


def get_gear_lubricant(name: str) -> GearLubricant:
    if name not in BUILTIN_GEAR_LUBRICANTS:
        raise UnknownGearLubricantError(
            f"Unknown lubricant {name!r}; available: {', '.join(sorted(BUILTIN_GEAR_LUBRICANTS))}"
        )
    return BUILTIN_GEAR_LUBRICANTS[name]


@dataclass
class LubricationConditionResult(DictResultMixin):
    """Operating viscosity, speed regime, EHL film, scuffing & micropitting screening."""

    lubricant_name: str
    lubricant_kind: str
    operating_temp_C: float
    nu_mm2_s: float
    rho_kg_m3: float
    eta_mPas: float
    pitch_line_velocity_m_s: float
    speed_regime: str                       # "standstill" | "boundary_low_speed" | "medium" | "high_speed"
    Z_L: float                              # ISO 6336-2 cl. 12 lubricant factor
    Z_v: float                              # ISO 6336-2 cl. 12 velocity factor
    Z_R: float                              # ISO 6336-2 cl. 12 roughness factor
    h_min_um: float                         # Dowson-Higginson minimum EHL film [um]
    Rq_composite_um: float                  # composite RMS roughness [um]
    lambda_film: float                      # specific film thickness h_min / Rq
    lubrication_regime: str                 # "boundary" | "mixed" | "partial_ehl" | "full_ehl"
    boundary_lubrication_flag: bool
    micropitting_risk: str                  # "low" | "moderate" | "high" | "unrated"
    scuffing_flash_delta_C: float
    scuffing_contact_temp_C: float
    scuffing_limit_temp_C: Optional[float]
    scuffing_safety_factor: Optional[float]
    peclet_number: float
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ISO3104_DowsonHigginson_ISO6336_2_20_22"
    flags: List[str] = field(default_factory=list)


@dataclass
class GreaseThermalBalanceResult(DictResultMixin):
    """Steady-state thermal equilibrium estimate for a sealed grease-filled gearbox."""

    ambient_temp_C: float
    equilibrium_temp_C: Optional[float]
    power_loss_W: float
    heat_rejection_W: float
    housing_area_m2: float
    h_conv_W_m2K: float
    nu_at_equilibrium_mm2_s: Optional[float]
    iterations: int
    converged: bool
    residual_W: float
    within_lubricant_limit: bool
    state: ValidityState = "estimated"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "SteadyConvectionThermalBalance_Estimate"
    flags: List[str] = field(default_factory=list)


def iso6336_2_lubrication_factors(
    *,
    sigma_Hlim_MPa: float,
    nu40_mm2_s: float,
    pitch_velocity_m_s: float,
    Rz_mean_um: float,
    rho_red_mm: float,
) -> Tuple[float, float, float]:
    """ISO 6336-2:2019 Method B; viscosity argument is nominal nu40, not operating viscosity."""
    sh = min(max(float(sigma_Hlim_MPa), 850.0), 1200.0)
    czl = ((sh - 850.0) / 350.0) * 0.08 + 0.83
    nu_use = max(float(nu40_mm2_s), 1.0)
    z_l = czl + 4.0 * (1.0 - czl) / ((1.2 + 134.0 / nu_use) ** 2)

    czv = czl + 0.02
    v_use = max(float(pitch_velocity_m_s), 0.1)
    z_v = czv + 2.0 * (1.0 - czv) / math.sqrt(0.8 + 32.0 / v_use)

    # ISO 6336-2 cl. 12.4: Rz10 = Rz_mean * (10 / rho_red)^(1/3)
    rho_clamped = max(float(rho_red_mm), 1.0)
    rz10 = max(float(Rz_mean_um), 0.1) * ((10.0 / rho_clamped) ** (1.0 / 3.0))
    czr = 0.32 - 0.0002 * sh
    z_r = (3.0 / max(rz10, 0.3)) ** czr
    return z_l, z_v, z_r


def evaluate_mesh_lubrication(
    *,
    pair_geom: PairGeometryResult,
    lubricant: GearLubricant,
    temp_C: float,
    pitch_velocity_m_s: float,
    F_n_N: float,
    b_eff_mm: float,
    sigma_Hlim_MPa: float,
    E1_MPa: float = 206000.0,
    nu1: float = 0.30,
    E2_MPa: float = 206000.0,
    nu2: float = 0.30,
    Ra1_um: float = 0.8,
    Ra2_um: float = 0.8,
    Rz1_um: float = 4.8,
    Rz2_um: float = 4.8,
    mu_mz: float = 0.05,
    k_thermal_W_mK: float = 42.0,
    rho_steel_kg_m3: float = 7850.0,
    cp_steel_J_kgK: float = 460.0,
) -> LubricationConditionResult:
    """Evaluate viscosity, EHL film thickness, ISO 6336-2 Z_L/Z_v/Z_R, scuffing, and micropitting."""
    for name, val in (
        ("temp_C", temp_C),
        ("pitch_velocity_m_s", pitch_velocity_m_s),
        ("F_n_N", F_n_N),
        ("b_eff_mm", b_eff_mm),
        ("sigma_Hlim_MPa", sigma_Hlim_MPa),
        ("E1_MPa", E1_MPa),
        ("E2_MPa", E2_MPa),
    ):
        if not math.isfinite(float(val)):
            raise ValueError(f"{name} must be finite, got {val!r}")
    if F_n_N < 0.0 or b_eff_mm <= 0.0 or sigma_Hlim_MPa <= 0.0:
        raise ValueError("F_n_N must be >= 0 and b_eff_mm / sigma_Hlim_MPa must be > 0")
    flags: List[str] = list(lubricant.flags)
    missing: List[str] = []

    nu_op = lubricant.kinematic_viscosity_mm2_s(temp_C)
    rho_op = lubricant.density_kg_m3(temp_C)
    eta_mPas = nu_op * rho_op * 1.0e-3
    eta_Pas = eta_mPas * 1.0e-3

    if temp_C < lubricant.temp_min_C or temp_C > lubricant.temp_max_C:
        flags.append("operating_temp_outside_lubricant_range")

    vt = abs(float(pitch_velocity_m_s))
    if vt == 0.0:
        speed_regime = "standstill"
    elif vt < 1.0:
        speed_regime = "boundary_low_speed"
        flags.append("low_speed_below_1m_s_wear_risk")
    elif vt <= 10.0:
        speed_regime = "medium"
    else:
        speed_regime = "high_speed"

    # Reduced radius of curvature at working pitch point [mm]
    awt = math.radians(pair_geom.alpha_wt_deg)
    bb = math.radians(abs(pair_geom.gear1.beta_b_deg))
    r_w1 = 0.5 * pair_geom.d_w1_mm
    r_w2 = 0.5 * pair_geom.d_w2_mm
    rho1 = r_w1 * math.sin(awt) / max(math.cos(bb), 1e-9)
    rho2 = r_w2 * math.sin(awt) / max(math.cos(bb), 1e-9)
    if pair_geom.is_internal_mesh:
        inv_rho = max(1.0 / rho1 - 1.0 / rho2, 1e-9)
    else:
        inv_rho = 1.0 / rho1 + 1.0 / rho2
    rho_red_mm = 1.0 / inv_rho
    r_eff_m = rho_red_mm * 1.0e-3

    nu40 = lubricant.nu40_mm2_s
    rz_mean = 0.5 * (float(Rz1_um) + float(Rz2_um))
    z_l, z_v, z_r = iso6336_2_lubrication_factors(
        sigma_Hlim_MPa=sigma_Hlim_MPa,
        nu40_mm2_s=nu40,  # Nominal ISO 6336-2:2019 lubricant viscosity
        pitch_velocity_m_s=vt,
        Rz_mean_um=rz_mean,
        rho_red_mm=rho_red_mm,
    )

    # Dowson-Higginson minimum EHL film thickness at pitch point
    e_star_pa = 1.0e6 / (
        0.5 * ((1.0 - nu1 ** 2) / E1_MPa + (1.0 - nu2 ** 2) / E2_MPa)
    )
    alpha_p_gpa_inv, alpha_est = lubricant.effective_alpha_p_GPa_inv()
    if alpha_est:
        flags.append("estimated_pressure_viscosity_coeff")
    alpha_p_pa_inv = alpha_p_gpa_inv * 1.0e-9

    # Entraining velocity u_e = vt * sin(alpha_wt) / cos(beta_b) [m/s]
    u_e = vt * math.sin(awt) / max(math.cos(bb), 1e-9)
    # Unit normal load w [N/m]
    l_eff_m = max(b_eff_mm * 1.0e-3 / max(math.cos(bb), 1e-9), 1.0e-6)
    w_n_m = max(float(F_n_N), 1e-3) / l_eff_m

    if u_e <= 0.0:
        h_min_um = 0.0
    else:
        u_dim = (eta_Pas * u_e) / (e_star_pa * r_eff_m)
        g_dim = alpha_p_pa_inv * e_star_pa
        w_dim = w_n_m / (e_star_pa * r_eff_m)
        h_min_m = 2.65 * r_eff_m * (u_dim ** 0.70) * (g_dim ** 0.54) * (w_dim ** -0.13)
        h_min_um = h_min_m * 1.0e6

    rq_comp = 1.25 * math.hypot(float(Ra1_um), float(Ra2_um))
    lam = h_min_um / max(rq_comp, 1.0e-3)

    if lam < 0.4:
        lube_regime = "boundary"
        boundary_flag = True
        micropitting = "high"
        flags.append("boundary_lubrication")
        flags.append("micropitting_high_risk_simplified")
    elif lam < 1.0:
        lube_regime = "mixed"
        boundary_flag = True
        micropitting = "moderate"
        flags.append("mixed_lubrication")
        flags.append("micropitting_moderate_risk_simplified")
    elif lam < 2.0:
        lube_regime = "partial_ehl"
        boundary_flag = False
        micropitting = "low"
    else:
        lube_regime = "full_ehl"
        boundary_flag = False
        micropitting = "low"

    if lubricant.kind == "grease":
        flags.append("grease_base_oil_film_estimate_no_starvation_model")

    # Simplified scuffing check (ISO/TS 6336-20 flash temperature estimate at pinion tip)
    r_b1 = 0.5 * pair_geom.gear1.d_b_mm
    r_a1 = 0.5 * pair_geom.gear1.d_a_mm
    t1_tip = math.sqrt(max(r_a1 * r_a1 - r_b1 * r_b1, 0.0))
    a_sin = pair_geom.a_w_mm * math.sin(awt)
    t2_tip = (t1_tip + a_sin) if pair_geom.is_internal_mesh else max(a_sin - t1_tip, 0.0)
    v_r1_tip = vt * t1_tip / max(r_w1 * math.cos(bb), 1e-9)
    v_r2_tip = vt * t2_tip / max(r_w2 * math.cos(bb), 1e-9)
    v_slide_tip = abs(v_r1_tip - v_r2_tip)

    # Local Hertzian half-width b_H [m] at pitch point for Peclet check
    b_hertz_m = math.sqrt((4.0 * w_n_m * r_eff_m) / (math.pi * e_star_pa))
    chi_th = k_thermal_W_mK / (rho_steel_kg_m3 * cp_steel_J_kgK)
    peclet = (b_hertz_m * max(v_r1_tip, v_r2_tip, u_e)) / max(2.0 * chi_th, 1e-12)

    # Blok flash temperature rise with rigid-body flank speeds v_r1_tip, v_r2_tip
    b_m = math.sqrt(k_thermal_W_mK * rho_steel_kg_m3 * cp_steel_J_kgK)
    if vt <= 0.0 or w_n_m <= 0.0:
        flash_delta_c = 0.0
    else:
        denom_v = math.sqrt(max(v_r1_tip, 1e-6)) + math.sqrt(max(v_r2_tip, 1e-6))
        flash_delta_c = (
            1.11 * mu_mz * w_n_m / (b_m * math.sqrt(max(2.0 * b_hertz_m, 1e-12)))
        ) * (v_slide_tip / denom_v)

    t_bulk_est = float(temp_C) + 0.35 * flash_delta_c
    t_contact = t_bulk_est + flash_delta_c

    if peclet < 5.0 and vt > 0.0:
        flags.append("low_peclet_outside_iso6336_20_flash_domain")

    if lubricant.scuffing_temp_limit_C is not None:
        t_lim = float(lubricant.scuffing_temp_limit_C)
        sf_scuff = (t_lim - float(temp_C)) / max(t_contact - float(temp_C), 1e-6) if t_contact > temp_C else 10.0
    else:
        t_lim = None
        sf_scuff = None
        missing.append("scuffing_temp_limit_C")

    flags.append("simplified_iso6336_20_flash_temp")
    flags.append("simplified_iso6336_22_lambda_micropitting")

    out_of_temp_range = temp_C < lubricant.temp_min_C or temp_C > lubricant.temp_max_C
    if out_of_temp_range:
        state: ValidityState = "unsupported"
        missing.append("lubricant_qualified_for_operating_temperature_C")
    else:
        state = "estimated"
    if peclet < 5.0 and vt > 0.0 and lubricant.kind == "grease":
        flags.append("scuffing_check_outside_calibrated_peclet_and_oil_domain")

    return LubricationConditionResult(
        lubricant_name=lubricant.name,
        lubricant_kind=lubricant.kind,
        operating_temp_C=float(temp_C),
        nu_mm2_s=nu_op,
        rho_kg_m3=rho_op,
        eta_mPas=eta_mPas,
        pitch_line_velocity_m_s=vt,
        speed_regime=speed_regime,
        Z_L=z_l,
        Z_v=z_v,
        Z_R=z_r,
        h_min_um=h_min_um,
        Rq_composite_um=rq_comp,
        lambda_film=lam,
        lubrication_regime=lube_regime,
        boundary_lubrication_flag=boundary_flag,
        micropitting_risk=micropitting,
        scuffing_flash_delta_C=flash_delta_c,
        scuffing_contact_temp_C=t_contact,
        scuffing_limit_temp_C=t_lim,
        scuffing_safety_factor=sf_scuff,
        peclet_number=peclet,
        state=state,
        applicability_evidence={
            "method_edition": "ISO 3104 / ASTM D341 + Dowson-Higginson + ISO/TS 6336-20/-22 (simplified)",
            "Z_L_formula": "ISO 6336-2:2019 (134 / nu_40)",
            "rho_red_mm": rho_red_mm,
            "nu40_mm2_s_for_Z_L": nu40,
            "v1_tip_m_s": v_r1_tip,
            "v2_tip_m_s": v_r2_tip,
            "v_slide_tip_m_s": v_slide_tip,
            "peclet_number": peclet,
            "alpha_p_GPa_inv": alpha_p_gpa_inv,
        },
        missing_inputs=missing,
        flags=flags,
    )


def solve_grease_thermal_balance(
    *,
    lubricant: GearLubricant,
    loss_fn_W: Callable[[float], float],
    ambient_temp_C: float = 25.0,
    housing_area_m2: Optional[float] = None,
    h_conv_W_m2K: float = 15.0,
    tol_W: float = 1.0e-5,
    max_iter: int = 80,
) -> GreaseThermalBalanceResult:
    """Estimate steady-state operating temperature for a sealed grease-filled unit.

    Solves ``P_loss(T) = h_conv * A_housing * (T - T_amb)`` by strictly
    bracketed bisection; reports ``converged=False`` and ``state="failed"`` if
    no steady-state root exists at or above ``ambient_temp_C``.
    """
    flags = ["convection_only_omits_mounting_and_motor_conduction"]
    missing = ["mounting_conduction_W_K", "thermal_capacitance_J_K"]
    if housing_area_m2 is None or housing_area_m2 <= 0.0:
        raise ValueError("housing_area_m2 must be > 0 for thermal balance")

    ua = float(h_conv_W_m2K) * float(housing_area_m2)
    t_amb = float(ambient_temp_C)

    def residual(t_c: float) -> float:
        return float(loss_fn_W(t_c)) - ua * (t_c - t_amb)

    lo = t_amb
    hi = max(t_amb + 1.0, lubricant.temp_max_C + 100.0)
    r_lo = residual(lo)
    conv = False
    iters = 0
    state: ValidityState = "estimated"

    if abs(r_lo) <= tol_W:
        t_eq = lo
        p_l = float(loss_fn_W(t_eq))
        conv = True
    elif r_lo < 0.0:
        t_eq = lo
        p_l = float(loss_fn_W(t_eq))
        conv = False
        state = "failed"
        flags.append("thermal_balance_negative_loss_no_root")
    else:
        bracketed = False
        step = 100.0
        for _ in range(12):
            if residual(hi) <= 0.0:
                bracketed = True
                break
            hi += step
            step *= 2.0
        if not bracketed:
            t_eq = hi
            p_l = float(loss_fn_W(t_eq))
            conv = False
            state = "failed"
            flags.append("thermal_balance_no_equilibrium_root_or_runaway")
        else:
            for iters in range(1, max_iter + 1):
                mid = 0.5 * (lo + hi)
                r_mid = residual(mid)
                if abs(r_mid) <= tol_W:
                    lo = hi = mid
                    conv = True
                    break
                if r_mid > 0.0:
                    lo = mid
                else:
                    hi = mid
                if (hi - lo) < 1e-9:
                    break
            t_eq = 0.5 * (lo + hi)
            p_l = float(loss_fn_W(t_eq))
            res_final = p_l - ua * (t_eq - t_amb)
            conv = abs(res_final) <= tol_W
            if not conv:
                state = "failed"
                flags.append("thermal_balance_did_not_meet_tolerance")

    q_out = ua * (t_eq - t_amb)
    within = conv and (lubricant.temp_min_C <= t_eq <= lubricant.temp_max_C)
    if conv and not within:
        flags.append("equilibrium_temp_exceeds_lubricant_limit")

    return GreaseThermalBalanceResult(
        ambient_temp_C=t_amb,
        equilibrium_temp_C=(t_eq if conv else None),
        power_loss_W=p_l,
        heat_rejection_W=q_out,
        housing_area_m2=float(housing_area_m2),
        h_conv_W_m2K=float(h_conv_W_m2K),
        nu_at_equilibrium_mm2_s=(lubricant.kinematic_viscosity_mm2_s(t_eq) if conv else None),
        iterations=iters,
        converged=conv,
        residual_W=p_l - q_out,
        within_lubricant_limit=within,
        state=state,
        applicability_evidence={
            "UA_W_K": ua,
            "lubricant_temp_max_C": lubricant.temp_max_C,
            "last_bracket_temp_C": (None if conv else t_eq),
        },
        missing_inputs=missing,
        flags=flags,
    )
