# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""ISO 6336 gear rating (Parts 1, 2, 3, and 6 Palmgren-Miner life).

Scope and honesty rules (ISO 6336-1 cl. 1 + Astra design review):
- Validated geometric domain of ISO 6336-1:2019: normal pressure angle
  15..25 deg, helix angle <= 30 deg, transverse contact ratio 1.0..2.5,
  metallic wrought/hardened gears without interference or pointed teeth,
  rim backup ratio s_R > 0.5*h_t (external) and s_R > 1.75*m_n (internal).
- External tooth-root factors Y_F, Y_S use the exact ISO 6336-3 cl. 6.2
  30-degree tangent method for hob-cut external teeth.
- Internal gear tooth-root uses the rack approximation and is explicitly marked
  ``state="unsupported"`` (never passes) unless shaper-cutter parameters and
  verified cl. 6.2.5 tables are supplied.
- Simplifications ``Z_W=1``, ``Z_X=1``, or default misalignment ``f_ma`` mark
  affected ratings as ``state="estimated"`` with explicit ``flags``.
- ISO 6336-6 cycle counting counts mesh engagements in the carrier frame:
  ``N * |n_s - n_c|`` for sun, ``N * |n_r - n_c|`` for ring, ``|n_p - n_c|``
  for planets (with alternating bending factor ``Y_M = 0.7`` flagged).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Literal

from .common import DictResultMixin, ValidityState, worst_state, require_number, FieldValidationError
from .geometry import GearDimensions, GearSpec, PairGeometryResult
from .loads import MeshForces
from .lubrication import GearLubricant, LubricationConditionResult, evaluate_mesh_lubrication
from .materials import GearMaterial, get_gear_material


@dataclass
class ISO6336LoadFactors(DictResultMixin):
    """ISO 6336-1 dynamic, face, transverse, and mesh-stiffness factors."""

    c_prime_N_mm_um: float                # single-tooth stiffness c' [N/(mm*um)]
    c_gamma_alpha_N_mm_um: float          # mesh stiffness c_gamma_alpha [N/(mm*um)]
    c_gamma_beta_N_mm_um: float           # mesh stiffness c_gamma_beta [N/(mm*um)]
    K_A: float
    K_gamma: float
    K_v: float                            # Method B dynamic factor
    K_Hbeta: float                        # Method C face load factor (contact)
    K_Fbeta: float                        # Method C face load factor (bending)
    K_Halpha: float                       # Method B transverse load factor (contact)
    K_Falpha: float                       # Method B transverse load factor (bending)
    resonance_ratio_N: float
    f_pt_um: float
    f_pb_um: float
    f_ma_um: float
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ISO6336-1:2019 Method B/C"
    flags: List[str] = field(default_factory=list)
    # Absent in legacy v1 JSON means estimated; explicit supplied source is serialized.
    f_ma_source: Literal["estimated", "supplied"] = field(
        default="estimated", metadata={"omit_if_default": True})

    def __post_init__(self):
        # An omitted v1 source may mean estimated only when its legacy evidence
        # agrees. Otherwise stripping a supplied source would silently relabel it.
        estimated = self.f_ma_source == "estimated"
        if (self.f_ma_source not in ("estimated", "supplied")
                or ("misalignment_f_ma_estimated_rigid_carrier_caveat" in self.flags) != estimated
                or ("f_ma_um_including_shaft_housing_deflection" in self.missing_inputs) != estimated):
            raise FieldValidationError("/f_ma_source", "f_ma_source contradicts misalignment estimate evidence")


@dataclass
class MemberRatingResult(DictResultMixin):
    """Contact (S_H) and tooth-root bending (S_F) safety factors for one gear."""

    member_name: str
    is_internal: bool
    is_planet_idler: bool
    # ISO 6336-2 pitting
    Z_H: float
    Z_E_sqrt_MPa: float
    Z_epsilon: float
    Z_beta: float
    Z_B_or_D: float
    Z_L: float
    Z_v: float
    Z_R: float
    Z_W: float
    Z_X: float
    Z_NT: float
    sigma_H0_MPa: float
    sigma_H_MPa: float
    sigma_HG_MPa: float
    S_H: float
    # ISO 6336-3 bending
    Y_F: float
    Y_S: float
    Y_beta: float
    Y_B: float
    Y_DT: float
    Y_ST: float
    Y_drelT: float
    Y_RrelT: float
    Y_X: float
    Y_NT: float
    Y_M_alternating: float
    sigma_F0_MPa: float
    sigma_F_MPa: float
    sigma_FG_MPa: float
    S_F: Optional[float]                  # None or marked unsupported if unrated
    # Overall states
    pitting_state: ValidityState
    bending_state: ValidityState
    pitting_passed: bool
    bending_passed: bool
    state: ValidityState = "estimated"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    simplified_factors: List[str] = field(default_factory=list)
    method: str = "ISO6336-2/-3 Method B"
    flags: List[str] = field(default_factory=list)


@dataclass
class PairRatingResult(DictResultMixin):
    """Complete ISO 6336-1/-2/-3 + lubrication rating for a meshing gear pair."""

    mesh_name: str
    load_factors: ISO6336LoadFactors
    lubrication: LubricationConditionResult
    gear1_rating: MemberRatingResult
    gear2_rating: MemberRatingResult
    min_S_H: float
    min_S_F: Optional[float]
    passed: bool
    state: ValidityState = "estimated"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ISO6336-1/2/3"
    flags: List[str] = field(default_factory=list)


@dataclass
class MinerDamageResult(DictResultMixin):
    """Palmgren-Miner cumulative fatigue damage and predicted block life (ISO 6336-6)."""

    target_name: str
    mechanism: str                        # "bending_root" | "pitting_flank" | "generic_miner"
    total_block_hours: float
    total_cycles_in_block: float
    damage_D: Optional[float]
    predicted_life_hours: Optional[float]
    bin_damages: List[Optional[float]]
    target_id: str = ""
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ISO6336-6_PalmgrenMiner"
    flags: List[str] = field(default_factory=list)


def _iso1328_deviations_um(m_n_mm: float, d_mm: float, grade: int) -> Tuple[float, float]:
    """Approximate ISO 1328-1 single-pitch (f_pt) and base-pitch (f_pb) tolerances [um].

    Uses the standard geometric progression step ``sqrt(2)^(grade - 5)`` from
    the grade-5 single-pitch formula ``f_pt5 = 0.3*m_n + 0.2*sqrt(d) + 4.0``
    (without copying standard tables). Explicit values can always override this estimate.
    """
    q = min(max(int(grade), 3), 12)
    step = math.sqrt(2.0) ** (q - 5)
    f_pt5 = 0.3 * m_n_mm + 0.2 * math.sqrt(max(d_mm, 1.0)) + 4.0
    f_pt = f_pt5 * step
    f_pb = f_pt * math.cos(math.radians(20.0))
    return f_pt, f_pb


def compute_mesh_stiffness_method_b(
    *,
    pair_geom: PairGeometryResult,
    E1_MPa: float = 206000.0,
    E2_MPa: float = 206000.0,
    solid_blank: bool = True,
) -> Tuple[float, float, float]:
    """Compute (c_prime, c_gamma_alpha, c_gamma_beta) [N/(mm*um)] per ISO 6336-1 cl. 9.3.2."""
    g1, g2 = pair_geom.gear1, pair_geom.gear2
    beta = math.radians(abs(g1.beta_deg))
    bb = math.radians(abs(g1.beta_b_deg))
    cos_b = math.cos(beta)
    z_v1 = g1.z / max((math.cos(bb) ** 2) * cos_b, 1e-6)
    z_v2 = g2.z / max((math.cos(bb) ** 2) * cos_b, 1e-6)
    inv_zv2 = 0.0 if g2.is_internal else (1.0 / z_v2)

    q_prime = (
        0.04723
        + 0.15551 / z_v1
        + 0.25791 * inv_zv2
        - 0.00635 * g1.x
        - 0.11654 * g1.x / z_v1
        - 0.00193 * g2.x
        - 0.24188 * g2.x * inv_zv2
        + 0.00529 * (g1.x ** 2)
        + 0.00182 * (g2.x ** 2)
    )
    c_th = 1.0 / max(q_prime, 1e-6)
    c_m = 0.8
    c_r = 1.0 if solid_blank else 0.85
    c_b = 1.0 - 0.02 * (20.0 - g1.alpha_n_deg)
    e_eff = 2.0 * E1_MPa * E2_MPa / (E1_MPa + E2_MPa)
    c_prime = c_th * c_m * c_r * c_b * cos_b * (e_eff / 206000.0)
    c_gamma_alpha = c_prime * (0.75 * pair_geom.epsilon_alpha + 0.25)
    c_gamma_beta = 0.85 * c_gamma_alpha
    return c_prime, c_gamma_alpha, c_gamma_beta


def compute_iso6336_load_factors(
    *,
    pair_geom: PairGeometryResult,
    mesh_forces: MeshForces,
    gear1_spec: GearSpec,
    gear2_spec: GearSpec,
    n1_rel_rpm: float,
    K_A: float = 1.0,
    f_ma_um: Optional[float] = None,
    f_pt_um: Optional[float] = None,
    f_pb_um: Optional[float] = None,
    E1_MPa: float = 206000.0,
    E2_MPa: float = 206000.0,
    rho1_kg_m3: float = 7850.0,
    rho2_kg_m3: float = 7850.0,
) -> ISO6336LoadFactors:
    """Compute the existing estimated Method B/C load-factor model.

    f_ma_um replaces only the manufacturing/assembly misalignment estimate.
    The separate placeholder 1.33*f_sh and running-in approximation remain;
    do not supply a total effective F_bx/F_by or double-count shaft bending.
    None preserves the legacy estimate; zero is an explicit supplied value.
    """
    for name, val in (
        ("n1_rel_rpm", n1_rel_rpm),
        ("K_A", K_A),
        ("E1_MPa", E1_MPa),
        ("E2_MPa", E2_MPa),
        ("rho1_kg_m3", rho1_kg_m3),
        ("rho2_kg_m3", rho2_kg_m3),
    ):
        if not math.isfinite(float(val)):
            raise ValueError(f"{name} must be finite, got {val!r}")
    if K_A <= 0.0 or E1_MPa <= 0.0 or E2_MPa <= 0.0 or rho1_kg_m3 <= 0.0 or rho2_kg_m3 <= 0.0:
        raise ValueError("K_A, E1_MPa, E2_MPa, rho1_kg_m3, and rho2_kg_m3 must be > 0")
    if f_ma_um is not None:
        require_number("f_ma_um", f_ma_um, minimum=0.)
    for opt_name, opt_val in (("f_pt_um", f_pt_um), ("f_pb_um", f_pb_um)):
        if opt_val is not None and (not math.isfinite(float(opt_val)) or float(opt_val) < 0.0):
            raise ValueError(f"{opt_name} must be finite and >= 0, got {opt_val!r}")

    flags: List[str] = [
        "simplified:placeholder_shaft_bending_f_sh",
        "simplified:solid_cylinder_mass_and_fixed_Bk=0.2",
        "simplified:estimated_running_in_y_alpha_y_beta",
    ]
    missing: List[str] = []
    g1, g2 = pair_geom.gear1, pair_geom.gear2
    b_eff = min(gear1_spec.b_mm, gear2_spec.b_mm)

    c_prime, c_ga, c_gb = compute_mesh_stiffness_method_b(
        pair_geom=pair_geom, E1_MPa=E1_MPa, E2_MPa=E2_MPa
    )

    # Reduced mass per unit face width [kg/mm] for solid cylinders (ISO 6336-1 cl. 6.4)
    # m_red = (pi/8) * (d_m1/d_b1)^2 * ...; for solid wheels m* = (pi/8) * rho * d^2 * 1e-9 / cos^2(alpha_t)...
    # Using solid-cylinder polar inertia J* = (pi/32)*rho*d^4/1e12, m_red = J*/r_b^2 [kg/mm]
    r_b1_m = 0.5 * g1.d_b_mm * 1.0e-3
    r_b2_m = 0.5 * g2.d_b_mm * 1.0e-3
    j1_per_mm = (math.pi / 32.0) * rho1_kg_m3 * ((g1.d_mm * 1.0e-3) ** 4) * 1.0e-3
    j2_per_mm = (math.pi / 32.0) * rho2_kg_m3 * ((g2.d_mm * 1.0e-3) ** 4) * 1.0e-3
    m1_star = j1_per_mm / max(r_b1_m ** 2, 1e-12)
    m2_star = j2_per_mm / max(r_b2_m ** 2, 1e-12)
    m_red = m1_star if g2.is_internal else (m1_star * m2_star) / max(m1_star + m2_star, 1e-12)

    # Resonance speed n_E1 [rpm] (ISO 6336-1 cl. 6.4.3): c_gamma in N/(mm*um) = 1e6 N/(m*mm)
    # c_gamma [N/(mm*um)] -> 1e6 * c_ga [N/(m*mm)] = 1e6 N/(mm * m)? Wait:
    # c_ga in N/(mm*um) = 1e6 N/(mm*m) => (N/m) per mm face width = 1e6 * c_ga!
    omega_n = math.sqrt((1.0e6 * c_ga) / max(m_red, 1e-9))
    n_e1 = (30.0 / math.pi) * omega_n / g1.z
    res_N = abs(float(n1_rel_rpm)) / max(n_e1, 1e-6)

    q_worst = max(gear1_spec.accuracy_grade, gear2_spec.accuracy_grade)
    fpt_est, fpb_est = _iso1328_deviations_um(g1.m_n_mm, 0.5 * (g1.d_mm + g2.d_mm), q_worst)
    f_pt = float(f_pt_um) if f_pt_um is not None else fpt_est
    f_pb = float(f_pb_um) if f_pb_um is not None else fpb_est
    if f_pt_um is None or f_pb_um is None:
        flags.append("iso1328_pitch_deviations_estimated_from_grade")

    # ISO 6336-1 cl. 6.5 Method B K_v
    f_m_per_mm = max(K_A * mesh_forces.k_gamma * mesh_forces.F_t_N / max(b_eff, 1e-6), 1.0)
    # Effective pitch / profile deviations after running-in y_p, y_f (approx ~ 0.1 * f_p for hardened)
    f_peff = max(f_pb * 0.9, 1.0)
    ca_tip = max(gear1_spec.tip_relief_um, gear2_spec.tip_relief_um)
    b_p = (c_prime * max(f_peff - ca_tip, 0.0)) / f_m_per_mm
    b_f = (c_prime * max(f_peff - ca_tip, 0.0)) / f_m_per_mm
    # C_v coefficients (ISO 6336-1 cl. 6.5.3)
    if pair_geom.epsilon_gamma <= 2.0:
        cv1, cv2, cv3, cv4 = 0.32, 0.34, 0.23, 0.90
        cv5, cv6, cv7 = 0.47, 0.47, 0.75
    else:
        eg_frac = min(pair_geom.epsilon_gamma - 2.0, 1.0)
        cv1 = 0.32
        cv2 = 0.57 / max(pair_geom.epsilon_gamma - 0.3, 1.0)
        cv3 = 0.096 / max(pair_geom.epsilon_gamma - 1.56, 0.3)
        cv4 = (0.57 - 0.05 * pair_geom.epsilon_gamma) / max(pair_geom.epsilon_gamma - 1.44, 0.4)
        cv5 = 0.47
        cv6 = 0.12 / max(pair_geom.epsilon_gamma - 1.74, 0.2)
        cv7 = 0.75 if pair_geom.epsilon_gamma <= 2.5 else (0.125 * math.sin(math.pi * eg_frac) + 0.875)

    c_v_k = cv1 * b_p + cv2 * b_f + cv3 * 0.2
    # ISO 6336-1:2019 cl. 6.5.2 subcritical resonance limit N_S
    n_s = 0.85 if f_m_per_mm >= 100.0 else (0.5 + 0.35 * math.sqrt(max(f_m_per_mm, 0.0) / 100.0))
    k_res = 1.0 + cv1 * b_p + cv2 * b_f + cv4 * 0.2
    k_sup = cv5 * b_p + cv6 * b_f + cv7
    if res_N <= n_s:
        k_v = 1.0 + c_v_k * res_N
    elif res_N <= 1.15:
        k_v = k_res
    elif res_N >= 1.5:
        k_v = k_sup
    else:
        k_v = k_sup + (k_res - k_sup) * (1.5 - res_N) / (1.5 - 1.15)
    k_v = max(k_v, 1.0)

    # ISO 6336-1 cl. 7.5 Method C K_Hbeta / K_Fbeta
    if f_ma_um is None:
        # Estimated housing/shaft/carrier misalignment from ISO grade + nominal elastic allowance
        f_ma = 0.5 * f_pt + 0.08 * b_eff
        flags.append("misalignment_f_ma_estimated_rigid_carrier_caveat")
        missing.append("f_ma_um_including_shaft_housing_deflection")
    else:
        f_ma = float(f_ma_um)
    f_sh = 0.023 * f_m_per_mm * (b_eff / max(g1.d_mm, 1.0)) ** 2 * 10.0  # small shaft bending placeholder [um]
    f_bx = 1.33 * f_sh + f_ma
    y_beta = min(0.15 * f_bx, 6.0)
    f_by = max(f_bx - y_beta, 0.0)
    # Method C face load ratio: ratio_hb = (F_by * c_gamma_beta) / (2 * F_m / b);
    # for ratio_hb <= 1 full contact width gives peak/mean = 1 + ratio_hb, else partial contact gives 2*sqrt(ratio_hb).
    ratio_hb = (f_by * c_gb) / (2.0 * max(f_m_per_mm * k_v, 1.0))
    k_hbeta = (1.0 + ratio_hb) if ratio_hb <= 1.0 else (2.0 * math.sqrt(ratio_hb))

    h_tooth = (gear1_spec.ha_star + gear1_spec.hf_star) * g1.m_n_mm
    b_over_h = max(b_eff / max(h_tooth, 0.1), 3.0)  # ISO 6336-1 cl. 7.6 minimum b/h = 3
    n_f = (b_over_h ** 2) / (1.0 + b_over_h + b_over_h ** 2)
    k_fbeta = k_hbeta ** n_f

    # ISO 6336-1:2019 cl. 8.3 Method B K_Halpha / K_Falpha and upper bounds
    y_alpha = min(0.075 * f_pb, 3.0)
    f_t_h = f_m_per_mm * k_v * k_hbeta
    ea = pair_geom.epsilon_alpha
    eb = pair_geom.epsilon_beta
    eg = pair_geom.epsilon_gamma
    if eg <= 2.0:
        k_alpha_raw = 0.5 * eg * (0.9 + 0.4 * (c_ga * max(f_pb - y_alpha, 0.0)) / max(f_t_h, 1.0))
    else:
        k_alpha_raw = 0.9 + 0.4 * math.sqrt(2.0 * (eg - 1.0) / eg) * (c_ga * max(f_pb - y_alpha, 0.0)) / max(f_t_h, 1.0)
    if eb <= 0.0:
        z_eps_sq = (4.0 - ea) / 3.0
    elif eb < 1.0:
        z_eps_sq = ((4.0 - ea) / 3.0) * (1.0 - eb) + eb / ea
    else:
        z_eps_sq = 1.0 / ea
    k_halpha_cap = eg / max(ea * z_eps_sq, 1.0e-9)
    k_falpha_cap = eg / max(0.25 * ea + 0.75, 1.0e-9)
    k_halpha = min(max(k_alpha_raw, 1.0), k_halpha_cap)
    k_falpha = min(max(k_alpha_raw, 1.0), k_falpha_cap)

    # Always "estimated" because placeholder shaft bending f_sh, fixed Bk=0.2,
    # solid-cylinder m_red, and approximate running-in y_alpha/y_beta are used.
    state: ValidityState = "estimated"
    return ISO6336LoadFactors(
        c_prime_N_mm_um=c_prime,
        c_gamma_alpha_N_mm_um=c_ga,
        c_gamma_beta_N_mm_um=c_gb,
        K_A=float(K_A),
        K_gamma=float(mesh_forces.k_gamma),
        K_v=k_v,
        K_Hbeta=k_hbeta,
        K_Fbeta=k_fbeta,
        K_Halpha=k_halpha,
        K_Falpha=k_falpha,
        resonance_ratio_N=res_N,
        f_pt_um=f_pt,
        f_pb_um=f_pb,
        f_ma_um=f_ma,
        f_ma_source="estimated" if f_ma_um is None else "supplied",
        state=state,
        applicability_evidence={
            "iso_edition": "ISO 6336-1:2019",
            "n_E1_rpm": n_e1,
            "N_s": n_s,
            "m_red_kg_mm": m_red,
            "K_v_method": "Method B (ISO 6336-1:2019 cl. 6.5)",
            "K_Hbeta_method": "Method C (ISO 6336-1:2019 cl. 7.5)",
            "K_alpha_method": "Method B (ISO 6336-1:2019 cl. 8.3)",
        },
        missing_inputs=missing,
        flags=flags,
    )


def _external_yf_ys_method_b(
    gear_spec: GearSpec,
    gear_dim: GearDimensions,
    pair_geom: PairGeometryResult,
    *,
    is_gear1: bool,
) -> Tuple[float, float, float]:
    """Compute (Y_F, Y_S, q_s) for hob-cut external teeth via ISO 6336-3 cl. 6.2 30-deg tangent."""
    an = math.radians(gear_spec.alpha_n_deg)
    bb = math.radians(abs(gear_dim.beta_b_deg))
    cos_b = math.cos(math.radians(abs(gear_spec.beta_deg)))
    zn = gear_spec.z / max((math.cos(bb) ** 2) * cos_b, 1e-6)

    hf_s = gear_spec.hf_star
    rho_fp = gear_spec.rho_fp_star
    x = gear_spec.x

    # ISO 6336-3 eq. (19)-(22)
    e_val = 0.25 * math.pi - hf_s * math.tan(an) - (1.0 - math.sin(an)) * rho_fp / math.cos(an)
    g_val = rho_fp - hf_s + x
    h_val = (2.0 / zn) * (0.5 * math.pi - e_val) - math.pi / 3.0

    theta = math.pi / 6.0
    for _ in range(40):
        f = theta - (2.0 * g_val / zn) * math.tan(theta) + h_val
        df = 1.0 - (2.0 * g_val / zn) / max(math.cos(theta) ** 2, 1e-12)
        dth = f / df if abs(df) > 1e-12 else 0.0
        theta -= dth
        if abs(dth) < 1e-12:
            break

    s_fn_star = zn * math.sin(math.pi / 3.0 - theta) + math.sqrt(3.0) * (
        g_val / math.cos(theta) - rho_fp
    )
    y_g_star = 0.5 * zn * math.cos(math.pi / 3.0 - theta) + 0.5 * (
        g_val / math.cos(theta) - rho_fp
    )
    rho_f_star = rho_fp + 2.0 * (g_val ** 2) / (
        math.cos(theta) * max(zn * (math.cos(theta) ** 2) - 2.0 * g_val, 0.1)
    )

    # Outer point of single pair contact on virtual spur gear (ISO 6336-3 cl. 6.2.1)
    r_bn = 0.5 * zn * math.cos(an)
    r_an = 0.5 * zn + gear_spec.ha_star + x - (gear_dim.tip_shortening_k_mm / gear_spec.m_n_mm)
    t_an = math.sqrt(max(r_an ** 2 - r_bn ** 2, 1e-9))
    eps_an = pair_geom.epsilon_alpha / max(math.cos(bb) ** 2, 1e-6)
    p_bn = math.pi * math.cos(an)
    # OPSC vs tip interpolated by epsilon_beta in [0, 1]
    eb = min(max(pair_geom.epsilon_beta, 0.0), 1.0)
    t_opsc = max(t_an - (eps_an - 1.0) * p_bn, 1e-6)
    t_en = t_opsc + eb * (t_an - t_opsc)
    r_en = math.hypot(r_bn, t_en)
    alpha_en = math.acos(min(1.0, r_bn / max(r_en, r_bn)))
    gamma_e = (0.5 * math.pi + 2.0 * x * math.tan(an)) / zn + (math.tan(an) - an) - (
        math.tan(alpha_en) - alpha_en
    )
    alpha_fen = alpha_en - gamma_e
    h_fe_star = max(r_bn / max(math.cos(alpha_fen), 1e-6) - y_g_star, 0.1)

    y_f = (6.0 * h_fe_star * math.cos(alpha_fen)) / max((s_fn_star ** 2) * math.cos(an), 1e-6)
    q_s = max(s_fn_star / max(2.0 * rho_f_star, 1e-6), 0.1)
    # ISO 6336-3 cl. 7.2: L_a = s_Fn / h_Fe (stress concentration factor Y_S)
    l_ratio = max(s_fn_star / max(h_fe_star, 1e-6), 0.1)
    y_s = (1.2 + 0.13 * l_ratio) * (q_s ** (1.0 / (1.21 + 2.3 / l_ratio)))
    return y_f, y_s, q_s


def _internal_rack_approx_yf_ys(gear_spec: GearSpec) -> Tuple[float, float, float]:
    """ISO 6336-3 cl. 6.3 rack approximation for internal gears (unverified/flagged)."""
    an = math.radians(gear_spec.alpha_n_deg)
    hf_s = gear_spec.hf_star
    rho_fp = gear_spec.rho_fp_star
    # Equivalent basic rack trapezoid chord and moment arm
    s_fn_star = 0.5 * math.pi + 2.0 * hf_s * math.tan(an)
    h_fe_star = gear_spec.ha_star + hf_s - 0.5 * rho_fp
    rho_f_star = max(rho_fp, 0.25)
    y_f = (6.0 * h_fe_star) / max((s_fn_star ** 2), 1e-6)
    q_s = s_fn_star / (2.0 * rho_f_star)
    l_ratio = max(s_fn_star / max(h_fe_star, 1e-6), 0.1)
    y_s = (1.2 + 0.13 * l_ratio) * (q_s ** (1.0 / (1.21 + 2.3 / l_ratio)))
    return y_f, y_s, q_s


def _bending_relative_factors(
    mat: GearMaterial,
    gear_spec: GearSpec,
    q_s: float,
) -> Tuple[float, float, float, float]:
    """Return (Y_B, Y_drelT, Y_RrelT, Y_X) per ISO 6336-3 cl. 9-12."""
    # Y_B (cl. 9): rim thickness factor
    h_t = (gear_spec.ha_star + gear_spec.hf_star) * gear_spec.m_n_mm
    if gear_spec.rim_thickness_mm is None:
        y_b = 1.0
    else:
        m_b = gear_spec.rim_thickness_mm / max(h_t, 1e-6)
        if m_b >= 1.2:
            y_b = 1.0
        else:
            y_b = max(1.6 * math.log(2.242 / max(m_b, 0.5)), 1.0)

    # Y_drelT (cl. 10 Method B): relative notch sensitivity vs reference test gear q_sT=2.5
    ht_type = mat.heat_treatment
    if ht_type == "nitrided":
        chi_base = 1.0 + 0.08 * math.sqrt(max(2.0 * q_s, 0.5))
        chi_ref = 1.0 + 0.08 * math.sqrt(5.0)
    elif ht_type == "case_carburised" or ht_type == "induction_hardened":
        chi_base = 1.0 + 0.14 * math.sqrt(max(2.0 * q_s, 0.5))
        chi_ref = 1.0 + 0.14 * math.sqrt(5.0)
    else:
        chi_base = 1.0 + 0.22 * math.sqrt(max(2.0 * q_s, 0.5)) * (800.0 / max(mat.yield_strength_MPa, 250.0)) ** 0.3
        chi_ref = 1.0 + 0.22 * math.sqrt(5.0) * (800.0 / max(mat.yield_strength_MPa, 250.0)) ** 0.3
    y_drel_t = chi_base / max(chi_ref, 1e-6)

    # Y_RrelT (cl. 11 Method B): surface roughness factor
    rz = max(float(gear_spec.Rz_root_um), 0.1)
    if rz < 1.0:
        y_rrel_t = 1.025 if ht_type == "nitrided" else 1.070
    else:
        if ht_type == "nitrided":
            y_rrel_t = max(1.520 - 0.440 * ((rz + 1.0) ** 0.1), 0.70)
        else:
            y_rrel_t = max(1.674 - 0.529 * ((rz + 1.0) ** 0.1), 0.70)

    # Y_X (cl. 12): size factor
    m_n = gear_spec.m_n_mm
    if m_n <= 5.0:
        y_x = 1.0
    elif ht_type in ("case_carburised", "induction_hardened", "nitrided"):
        y_x = max(1.05 - 0.01 * m_n, 0.80)
    else:
        y_x = max(1.03 - 0.006 * m_n, 0.85)

    return y_b, y_drel_t, y_rrel_t, y_x


def rate_pair_iso6336(
    *,
    mesh_name: str,
    pair_geom: PairGeometryResult,
    gear1_spec: GearSpec,
    gear2_spec: GearSpec,
    mesh_forces: MeshForces,
    n1_rel_rpm: float,
    lubricant: GearLubricant,
    temp_C: float = 70.0,
    cycles_1: float = 5.0e7,
    cycles_2: float = 5.0e7,
    K_A: float = 1.0,
    f_ma_um: Optional[float] = None,
    gear1_is_planet_idler: bool = False,
    gear2_is_planet_idler: bool = False,
    shaper_cutter_z0: Optional[int] = None,
    min_S_H: float = 1.1,
    min_S_F: float = 1.5,
    allow_zero_backlash: bool = False,
) -> PairRatingResult:
    """Rate a meshing gear pair per ISO 6336-1/-2/-3 with lubricant factors."""
    for spec, dim in ((gear1_spec, pair_geom.gear1), (gear2_spec, pair_geom.gear2)):
        if spec.z != dim.z or spec.is_internal != dim.is_internal or not math.isclose(spec.m_n_mm, dim.m_n_mm, rel_tol=1e-12):
            raise ValueError("Rating specifications must match solved tooth counts, module and internal identity")
    gear1_spec = gear1_spec.with_x(pair_geom.gear1.x)
    gear2_spec = gear2_spec.with_x(pair_geom.gear2.x)
    mat1 = get_gear_material(gear1_spec.material_name)
    mat2 = get_gear_material(gear2_spec.material_name)
    g1, g2 = pair_geom.gear1, pair_geom.gear2
    b_eff = min(gear1_spec.b_mm, gear2_spec.b_mm)

    lf = compute_iso6336_load_factors(
        pair_geom=pair_geom,
        mesh_forces=mesh_forces,
        gear1_spec=gear1_spec,
        gear2_spec=gear2_spec,
        n1_rel_rpm=n1_rel_rpm,
        K_A=K_A,
        f_ma_um=f_ma_um,
        E1_MPa=mat1.E_MPa,
        E2_MPa=mat2.E_MPa,
        rho1_kg_m3=mat1.density_kg_m3,
        rho2_kg_m3=mat2.density_kg_m3,
    )

    lube_res = evaluate_mesh_lubrication(
        pair_geom=pair_geom,
        lubricant=lubricant,
        temp_C=temp_C,
        pitch_velocity_m_s=mesh_forces.pitch_line_velocity_m_s,
        F_n_N=mesh_forces.F_n_N,
        b_eff_mm=b_eff,
        sigma_Hlim_MPa=min(mat1.sigma_Hlim_MPa, mat2.sigma_Hlim_MPa),
        E1_MPa=mat1.E_MPa,
        nu1=mat1.nu,
        E2_MPa=mat2.E_MPa,
        nu2=mat2.nu,
        Ra1_um=gear1_spec.Ra_flank_um,
        Ra2_um=gear2_spec.Ra_flank_um,
        Rz1_um=gear1_spec.Rz_flank_um,
        Rz2_um=gear2_spec.Rz_flank_um,
    )

    # ISO 6336-1 domain checks & functional clearance check
    domain_flags: List[str] = []
    domain_missing: List[str] = []
    domain_unsupported = False
    if not pair_geom.valid_mesh:
        domain_unsupported = True
        domain_flags.append("invalid_mesh_geometry_outside_iso6336_domain")
    if not (15.0 <= gear1_spec.alpha_n_deg <= 25.0):
        domain_unsupported = True
        domain_flags.append("pressure_angle_outside_iso6336_1_domain_15_25_deg")
    if abs(gear1_spec.beta_deg) > 30.0:
        domain_unsupported = True
        domain_flags.append("helix_angle_above_iso6336_1_limit_30_deg")
    if not (1.0 <= pair_geom.epsilon_alpha <= 2.5):
        domain_unsupported = True
        domain_flags.append("contact_ratio_outside_iso6336_1_domain_1.0_2.5")
    zero_backlash_unsafe = (pair_geom.circumferential_backlash_mm <= 0.0) and (not allow_zero_backlash)
    if zero_backlash_unsafe:
        domain_flags.append("zero_operating_backlash_jamming_risk")
        domain_missing.append("positive_tooth_thickness_allowance_mm")

    # ISO 6336-2 common factors
    awt = math.radians(pair_geom.alpha_wt_deg)
    at = math.radians(g1.alpha_t_deg)
    bb = math.radians(abs(g1.beta_b_deg))
    beta = math.radians(abs(g1.beta_deg))

    z_h = math.sqrt((2.0 * math.cos(bb) * math.cos(awt)) / ((math.cos(at) ** 2) * math.sin(awt)))
    z_e = math.sqrt(1.0 / (math.pi * ((1.0 - mat1.nu ** 2) / mat1.E_MPa + (1.0 - mat2.nu ** 2) / mat2.E_MPa)))
    ea, eb = pair_geom.epsilon_alpha, pair_geom.epsilon_beta
    if eb <= 0.0:
        z_eps = math.sqrt((4.0 - ea) / 3.0)
    elif eb < 1.0:
        z_eps = math.sqrt(((4.0 - ea) / 3.0) * (1.0 - eb) + eb / ea)
    else:
        z_eps = math.sqrt(1.0 / ea)
    z_beta = 1.0 / math.sqrt(max(math.cos(beta), 1.0e-9))  # ISO 6336-2:2019 cl. 9

    # Single-pair tooth contact factors Z_B and Z_D (Method B, cl. 10)
    t1_tan = math.sqrt(max((g1.d_a_mm / g1.d_b_mm) ** 2 - 1.0, 0.0))
    t2_tan = math.sqrt(max((g2.d_a_mm / g2.d_b_mm) ** 2 - 1.0, 0.0))
    if eb >= 1.0:
        z_b, z_d = 1.0, 1.0
    else:
        if pair_geom.is_internal_mesh:
            d1_in = t1_tan - 2.0 * math.pi / g1.z
            d2_in = t2_tan + (ea - 1.0) * 2.0 * math.pi / g2.z
            m1 = math.tan(awt) / math.sqrt(max(d1_in * d2_in, 1e-6))
            m2 = 1.0
        else:
            d1_in = t1_tan - 2.0 * math.pi / g1.z
            d2_in = t2_tan - (ea - 1.0) * 2.0 * math.pi / g2.z
            m1 = math.tan(awt) / math.sqrt(max(d1_in * d2_in, 1e-6))
            d2_in_b = t2_tan - 2.0 * math.pi / g2.z
            d1_in_b = t1_tan - (ea - 1.0) * 2.0 * math.pi / g1.z
            m2 = math.tan(awt) / math.sqrt(max(d2_in_b * d1_in_b, 1e-6))
        z_b = max(1.0, m1 - eb * (m1 - 1.0))
        z_d = max(1.0, m2 - eb * (m2 - 1.0))

    u = pair_geom.u_ratio
    u_term = ((u - 1.0) / u) if pair_geom.is_internal_mesh else ((u + 1.0) / u)
    # Note: worst-branch F_t for strength rating includes K_gamma inside mesh_forces.F_t_N * K_gamma!
    sigma_h0 = z_h * z_e * z_eps * z_beta * math.sqrt(
        (mesh_forces.F_t_N / (g1.d_mm * b_eff)) * u_term
    )
    k_h_tot = math.sqrt(lf.K_A * lf.K_gamma * lf.K_v * lf.K_Hbeta * lf.K_Halpha)
    sigma_h1 = z_b * sigma_h0 * k_h_tot
    sigma_h2 = z_d * sigma_h0 * k_h_tot

    y_beta = max(1.0 - min(eb, 1.0) * min(abs(gear1_spec.beta_deg), 30.0) / 120.0, 0.75)
    epsilon_alphan = ea / (math.cos(bb) ** 2)

    def _rate_member(
        name: str,
        spec: GearSpec,
        dim: GearDimensions,
        mat: GearMaterial,
        z_bd: float,
        sigma_h: float,
        cycles: float,
        is_planet_idler: bool,
    ) -> MemberRatingResult:
        y_dt = 1.0
        if max(gear1_spec.accuracy_grade, gear2_spec.accuracy_grade) <= 4 and epsilon_alphan > 2.05:
            y_dt = 0.7 if epsilon_alphan > 2.5 else 2.366 - 0.666 * epsilon_alphan
        m_flags = list(domain_flags)
        m_missing: List[str] = list(lf.missing_inputs) + list(domain_missing)
        simpl: List[str] = ["Z_W=1.0", "Z_X=1.0"]
        m_flags.append("simplified:Z_W=1.0")
        m_flags.append("simplified:Z_X=1.0")
        if lf.missing_inputs:
            m_flags.append("simplified:estimated_misalignment_f_ma")

        z_w, z_x = 1.0, 1.0
        z_nt = mat.z_nt(cycles)
        sigma_hg = mat.sigma_Hlim_MPa * z_nt * lube_res.Z_L * lube_res.Z_v * lube_res.Z_R * z_w * z_x
        s_h = sigma_hg / max(sigma_h, 1e-9)

        h_t = (spec.ha_star + spec.hf_star) * spec.m_n_mm
        thin_rim_unsupported = False
        if spec.rim_thickness_mm is not None:
            if (not spec.is_internal and spec.rim_thickness_mm <= 0.5 * h_t) or (
                spec.is_internal and spec.rim_thickness_mm <= 1.75 * spec.m_n_mm
            ):
                thin_rim_unsupported = True
                m_flags.append("rim_thickness_below_iso6336_3_scope")
                m_missing.append("FEA_thin_rim_bending_verification")

        if spec.is_internal:
            y_f, y_s, q_s = _internal_rack_approx_yf_ys(spec)
            simpl.append("internal_rack_approximation")
            m_flags.append("simplified:internal_rack_approximation")
            # Internal tooth-root bending per ISO 6336-3:2019 cl. 6.2.5 is not implemented;
            # providing shaper_cutter_z0 alone does not turn the rack approximation into a valid rating.
            if shaper_cutter_z0 is None:
                m_missing.extend(["shaper_cutter_z0", "shaper_cutter_x0"])
            m_missing.append("ISO6336-3:2019 cl.6.2.5 cutter tables")
            bend_state: ValidityState = "unsupported"
        else:
            y_f, y_s, q_s = _external_yf_ys_method_b(spec, dim, pair_geom, is_gear1=(name == "gear1" or not spec.is_internal))
            bend_state = "unsupported" if (domain_unsupported or thin_rim_unsupported) else ("estimated" if m_missing else "valid")

        y_b, y_drel_t, y_rrel_t, y_x = _bending_relative_factors(mat, spec, q_s)
        y_st = 2.0
        y_nt = mat.y_nt(cycles)
        y_m = 0.7 if is_planet_idler else 1.0
        if is_planet_idler:
            m_flags.append("planet_alternating_root_bending_YM=0.7")

        sigma_f0 = (mesh_forces.F_t_N / (b_eff * spec.m_n_mm)) * y_f * y_s * y_beta * y_b * y_dt
        sigma_f = sigma_f0 * lf.K_A * lf.K_gamma * lf.K_v * lf.K_Fbeta * lf.K_Falpha
        sigma_fg = mat.sigma_Flim_MPa * y_st * y_nt * y_drel_t * y_rrel_t * y_x * y_m
        s_f = sigma_fg / max(sigma_f, 1e-9)

        if domain_unsupported or lube_res.state == "unsupported":
            pit_state: ValidityState = "unsupported"
            bend_state = "unsupported"
        elif zero_backlash_unsafe:
            pit_state = "failed"
            bend_state = "unsupported" if bend_state == "unsupported" else "failed"
        else:
            pit_state = "estimated"

        pit_passed = (pit_state not in ("unsupported", "failed")) and (s_h >= min_S_H)
        bend_passed = (bend_state not in ("unsupported", "failed")) and (s_f >= min_S_F)
        overall_state = worst_state([pit_state, bend_state])

        return MemberRatingResult(
            member_name=name,
            is_internal=spec.is_internal,
            is_planet_idler=is_planet_idler,
            Z_H=z_h,
            Z_E_sqrt_MPa=z_e,
            Z_epsilon=z_eps,
            Z_beta=z_beta,
            Z_B_or_D=z_bd,
            Z_L=lube_res.Z_L,
            Z_v=lube_res.Z_v,
            Z_R=lube_res.Z_R,
            Z_W=z_w,
            Z_X=z_x,
            Z_NT=z_nt,
            sigma_H0_MPa=sigma_h0,
            sigma_H_MPa=sigma_h,
            sigma_HG_MPa=sigma_hg,
            S_H=s_h,
            Y_F=y_f,
            Y_S=y_s,
            Y_beta=y_beta,
            Y_B=y_b,
            Y_DT=y_dt,
            Y_ST=y_st,
            Y_drelT=y_drel_t,
            Y_RrelT=y_rrel_t,
            Y_X=y_x,
            Y_NT=y_nt,
            Y_M_alternating=y_m,
            sigma_F0_MPa=sigma_f0,
            sigma_F_MPa=sigma_f,
            sigma_FG_MPa=sigma_fg,
            S_F=(None if bend_state == "unsupported" else s_f),
            pitting_state=pit_state,
            bending_state=bend_state,
            pitting_passed=pit_passed,
            bending_passed=bend_passed,
            state=overall_state,
            applicability_evidence={
                "life_cycles": cycles,
                "Y_DT_pair_accuracy_grade": max(gear1_spec.accuracy_grade, gear2_spec.accuracy_grade),
                "pitting_edition": "ISO 6336-2:2019 Method B (Z_beta = 1/sqrt(cos beta))",
                "Z_L_formula": lube_res.applicability_evidence.get(
                    "Z_L_formula", "ISO 6336-2:2019 (134 / nu_40)"
                ),
                "bending_edition": (
                    "ISO 6336-3:2019 internal rack approx (unsupported)"
                    if spec.is_internal
                    else "ISO 6336-3:2019 Method B"
                ),
                "cycles_evaluated": cycles,
            },
            missing_inputs=sorted(set(m_missing)),
            simplified_factors=simpl,
            flags=sorted(set(m_flags)),
        )

    r1 = _rate_member("gear1", gear1_spec, g1, mat1, z_b, sigma_h1, cycles_1, gear1_is_planet_idler)
    r2 = _rate_member("gear2", gear2_spec, g2, mat2, z_d, sigma_h2, cycles_2, gear2_is_planet_idler)

    min_sh = min(r1.S_H, r2.S_H)
    sfs = [v for v in (r1.S_F, r2.S_F) if v is not None]
    min_sf = min(sfs) if (r1.bending_state != "unsupported" and r2.bending_state != "unsupported" and sfs) else None

    overall_state = worst_state([r1.state, r2.state, lf.state, lube_res.state])
    passed = (
        r1.pitting_passed
        and r2.pitting_passed
        and r1.bending_passed
        and r2.bending_passed
        and overall_state not in ("unsupported", "failed")
    )

    all_flags = sorted(set(lf.flags + lube_res.flags + r1.flags + r2.flags))
    all_missing = sorted(set(lf.missing_inputs + lube_res.missing_inputs + r1.missing_inputs + r2.missing_inputs))

    return PairRatingResult(
        mesh_name=mesh_name,
        load_factors=lf,
        lubrication=lube_res,
        gear1_rating=r1,
        gear2_rating=r2,
        min_S_H=min_sh,
        min_S_F=min_sf,
        passed=passed,
        state=overall_state,
        applicability_evidence={
            "iso_edition": "ISO 6336-1/-2/-3:2019 Method B/C (Z_beta = 1/sqrt(cos beta))",
            "Z_L_formula": lube_res.applicability_evidence.get(
                "Z_L_formula", "ISO 6336-2:2019 (134 / nu_40)"
            ),
            "gear1_state": r1.state,
            "gear2_state": r2.state,
            "load_factors_state": lf.state,
        },
        missing_inputs=all_missing,
        flags=all_flags,
    )


def miner_damage_from_cycles(
    *,
    target_name: str,
    bins: Sequence[Tuple[float, float]],    # (n_cycles_i, N_capacity_i)
    block_hours: float,
    mechanism: str = "generic_miner",
    upstream_states: Optional[Sequence[ValidityState]] = None,
    upstream_flags: Sequence[str] = (),
    upstream_missing: Sequence[str] = (),
) -> MinerDamageResult:
    """Pure Palmgren-Miner cumulative damage D = sum(n_i / N_i) and block life t_b / D."""
    if not bins:
        raise ValueError("Miner bins must be non-empty")
    if not math.isfinite(float(block_hours)) or float(block_hours) < 0.0:
        raise ValueError(f"block_hours must be finite and >= 0, got {block_hours!r}")
    damages: List[Optional[float]] = []
    capacity_missing = False
    tot_cycles = 0.0
    for n_i, cap_i in bins:
        if not math.isfinite(float(n_i)) or float(n_i) < 0.0:
            raise ValueError(f"Bin cycle count n_i must be finite and >= 0, got {n_i!r}")
        n_val = float(n_i)
        tot_cycles += n_val
        if cap_i is None:
            capacity_missing = True
            damages.append(None)
        elif cap_i == math.inf:
            damages.append(0.0)
        elif not math.isfinite(float(cap_i)) or float(cap_i) <= 0.0:
            raise ValueError(f"Capacity N_i must be > 0 or inf, got {cap_i}")
        else:
            damages.append(n_val / float(cap_i))

    # Use sorted math.fsum so cumulative Miner damage D is strictly invariant to bin permutation
    d_tot = math.fsum(sorted(d for d in damages if d is not None))
    t_b = math.fsum(sorted([float(block_hours)]))
    flags: List[str] = list(dict.fromkeys(upstream_flags))
    missing: List[str] = list(dict.fromkeys(upstream_missing))
    res_state: ValidityState = worst_state(list(upstream_states)) if upstream_states else "valid"

    if capacity_missing:
        res_state = worst_state([res_state, "unsupported"])
        flags.append("missing_fatigue_capacity")
        missing.append("fatigue_capacity_cycles")

    if res_state in ("unsupported", "failed"):
        life_h: Optional[float] = None
        if "upstream_rating_or_geometry_not_valid" not in flags:
            flags.append("upstream_rating_or_geometry_not_valid")
    elif d_tot <= 0.0:
        life_h = None
        flags.append("zero_fatigue_damage_under_miner_model")
    else:
        life_h = t_b / d_tot

    return MinerDamageResult(
        target_name=target_name,
        mechanism=mechanism,
        total_block_hours=t_b,
        total_cycles_in_block=tot_cycles,
        damage_D=None if capacity_missing else d_tot,
        predicted_life_hours=life_h,
        bin_damages=damages,
        state=res_state,
        applicability_evidence={"n_bins": len(bins), "block_hours": t_b},
        missing_inputs=missing,
        flags=flags,
    )


def evaluate_gear_miner_spectrum(
    *,
    target_name: str,
    material: GearMaterial,
    bins: Sequence[Tuple[float, float, float]],  # (stress_MPa, cycles_per_min, duration_h)
    mechanism: str = "bending_root",              # "bending_root" | "pitting_flank"
    allowable_factor: float = 1.0,
    allowable_factors: Optional[Sequence[float]] = None,
    upstream_states: Optional[Sequence[ValidityState]] = None,
    upstream_flags: Sequence[str] = (),
    upstream_missing: Sequence[str] = (),
) -> MinerDamageResult:
    """Evaluate ISO 6336-6 Miner cumulative damage from stress/cycles-per-min/duration bins."""
    if not bins:
        raise ValueError("Spectrum bins must be non-empty")
    if allowable_factors is not None and len(allowable_factors) != len(bins):
        raise ValueError("allowable_factors length must match bins length")
    cycle_pairs: List[Tuple[float, Optional[float]]] = []
    out_of_range_bins = []
    durations: List[float] = []
    for idx, (stress_mpa, cpm, dt_h) in enumerate(bins):
        s_val = float(stress_mpa)
        cpm_val = float(cpm)
        dt_val = float(dt_h)
        if not (math.isfinite(s_val) and math.isfinite(cpm_val) and math.isfinite(dt_val)):
            raise ValueError(f"Spectrum bin {idx} contains non-finite values: {(stress_mpa, cpm, dt_h)!r}")
        if s_val < 0.0 or dt_val < 0.0:
            raise ValueError(f"Spectrum bin {idx} stress and duration must be >= 0, got {(stress_mpa, dt_h)!r}")
        fac = float(allowable_factors[idx]) if allowable_factors is not None else float(allowable_factor)
        if not math.isfinite(fac) or fac <= 0.0:
            raise ValueError(f"allowable_factor must be finite and > 0, got {fac!r}")
        n_i = 60.0 * abs(cpm_val) * dt_val
        durations.append(dt_val)
        if mechanism == "bending_root":
            cap = material.cycles_for_bending_stress(s_val, allowable_factor=fac)
        elif mechanism == "pitting_flank":
            cap = material.cycles_for_contact_stress(s_val, allowable_factor=fac)
        else:
            raise ValueError(f"Unknown mechanism {mechanism!r}")
        if cap is None: out_of_range_bins.append(idx)
        cycle_pairs.append((n_i, cap))
    tot_h = math.fsum(sorted(durations))

    res = miner_damage_from_cycles(
        target_name=target_name,
        bins=cycle_pairs,
        block_hours=tot_h,
        mechanism=mechanism,
        upstream_states=upstream_states,
        upstream_flags=upstream_flags,
        upstream_missing=upstream_missing,
    )
    if out_of_range_bins:
        res.flags.append("stress_above_static_plateau_outside_SN_domain")
        res.applicability_evidence["outside_SN_domain_bins"] = out_of_range_bins
    res.applicability_evidence["material"] = material.name
    res.applicability_evidence["allowable_factor"] = (
        "per_bin" if allowable_factors is not None else allowable_factor
    )
    return res
