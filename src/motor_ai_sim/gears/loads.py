# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Gearbox member torques (signed ON stage), tooth forces, and bearing reaction loads.

Signed torque and power convention (Astra review Q4):
- All member torques ``T_j`` act **on** the gearbox stage from external shafts
  or housing reaction.
- All shaft speeds ``omega_j`` are signed angular velocities [rad/s] or [rpm].
- Port power is ``P_j = T_j * omega_j``: driving ports have ``P_j > 0``, driven
  (power-delivering) ports have ``P_j < 0`` (useful output = ``-P_driven``), and
  fixed housing reaction ports have ``omega_j = 0, P_j = 0``.
- Steady-state equilibrium requires ``sum(T_j) = 0`` (mesh-only, or including
  housing drag torque) and ``sum(P_j) = P_loss >= 0``.

Load-sharing separation (Blocker 3):
- Physical parallel branch loads (``branch_torques_gear1_Nm``, summing exactly to
  ``transmitted_torque_gear1_Nm``) are used for torque balance and nominal losses.
- ``K_gamma`` (worst-planet load-sharing factor) scales ONLY the separate
  ``*_worst_branch_*`` fields used for worst-planet strength/bearing ratings. It
  is never applied to all planets simultaneously and never scales losses.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .common import DictResultMixin, ValidityState, require_number
from .geometry import PairGeometryResult


# Screening planet load-sharing default by planet count (user input overrides).
# Note: per AGMA 6123-C16 ch. 9 / Annex I, true load sharing depends on torque,
# tolerances, and compliance; this screening table is NOT an AGMA guarantee and
# is explicitly flagged when used as a default.
_K_GAMMA_FLOATING = {1: 1.00, 2: 1.00, 3: 1.00, 4: 1.10, 5: 1.25, 6: 1.35}
_K_GAMMA_NON_FLOATING = {1: 1.00, 2: 1.10, 3: 1.10, 4: 1.25, 5: 1.35, 6: 1.40}


def default_k_gamma(n_planets: int, *, floating_member: bool = True) -> float:
    """Return the screening default K_gamma for ``n_planets`` (always overridable)."""
    if n_planets <= 0:
        raise ValueError(f"n_planets must be >= 1, got {n_planets}")
    table = _K_GAMMA_FLOATING if floating_member else _K_GAMMA_NON_FLOATING
    if n_planets in table:
        return table[n_planets]
    return 1.40 if floating_member else 1.50


@dataclass
class MeshForces(DictResultMixin):
    """Physical branch loads and worst-branch (K_gamma) rating loads at one mesh."""

    mesh_name: str
    transmitted_torque_gear1_Nm: float          # total torque across all branches
    n_parallel_paths: int
    branch_shares: List[float]                  # physical shares summing to 1.0
    branch_torques_gear1_Nm: List[float]        # physical branch torques summing to total
    torque_nominal_branch_Nm: float             # mean branch torque = total / N
    torque_worst_branch_Nm: float               # K_gamma * mean (or max physical branch)
    F_t_N: float                                # nominal branch tangential force at d1 (ISO 6336)
    F_tw_N: float                               # nominal branch tangential force at d_w1
    F_r_N: float                                # nominal branch radial force
    F_a_N: float                                # nominal branch axial force
    F_n_N: float                                # nominal branch normal force
    F_t_worst_branch_N: float                   # worst-planet rating F_t (K_gamma * nominal)
    F_tw_worst_branch_N: float
    F_r_worst_branch_N: float
    F_a_worst_branch_N: float
    pitch_line_velocity_m_s: float
    k_gamma: float
    k_gamma_source: str                         # "input" | "screening_default"
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ISO_ToothForce_BranchSeparated"
    flags: List[str] = field(default_factory=list)


@dataclass
class BearingReactionLoad(DictResultMixin):
    """Resultant radial/axial reaction loads on one bearing support position."""

    role: str                                   # "planet" | "input_A" | "input_B" | "output_A" | "output_B" | "carrier"
    F_r_N: float                                # nominal (physical mean) radial force
    F_a_N: float                                # nominal (physical mean) axial force
    F_r_worst_branch_N: float                   # worst-branch radial force (includes K_gamma for planets)
    F_a_worst_branch_N: float                   # worst-branch axial force
    speed_rpm: float                            # relative inner-to-outer ring speed [rpm]
    overturning_moment_Nm: float = 0.0
    n_identical_bearings: int = 1
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "StaticBeamEquilibrium"
    flags: List[str] = field(default_factory=list)


@dataclass
class StageLoadsResult(DictResultMixin):
    """Signed port torques, speeds, power balance, mesh forces, and bearing loads."""

    stage_kind: str
    driving_member: str
    driven_member: str
    fixed_member: Optional[str]
    member_torques_Nm: Dict[str, float]         # external torques acting ON stage (sum == 0)
    member_speeds_rpm: Dict[str, float]         # signed speeds [rpm]
    member_powers_W: Dict[str, float]           # P_j = T_j * omega_j (sum == power_loss_W >= 0)
    power_in_W: float                           # positive power at driving port
    power_out_useful_W: float                   # positive useful power leaving driven port (-P_driven)
    power_loss_W: float                         # sum(P_j) >= 0
    efficiency: Optional[float]                 # power_out_useful_W / power_in_W; None at zero speed
    k_gamma: float
    mesh_forces: Dict[str, MeshForces]
    bearing_loads: List[BearingReactionLoad]
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "SignedPowerFlow_StageStatics"
    flags: List[str] = field(default_factory=list)


def compute_mesh_forces(
    *,
    mesh_name: str,
    pair_geom: PairGeometryResult,
    total_torque_gear1_Nm: float,
    gear1_rel_speed_rpm: float,
    n_parallel_paths: int = 1,
    k_gamma: Optional[float] = None,
    floating_member: bool = True,
    branch_shares: Optional[Sequence[float]] = None,
) -> MeshForces:
    """Resolve physical branch tooth forces and worst-branch rating forces."""
    require_number("total_torque_gear1_Nm",total_torque_gear1_Nm)
    require_number("gear1_rel_speed_rpm",gear1_rel_speed_rpm)
    require_number("n_parallel_paths",n_parallel_paths,integer=True,positive=True)
    if k_gamma is not None: require_number("k_gamma",k_gamma,minimum=1.)
    if branch_shares is not None:
        for share in branch_shares: require_number("branch share",share,minimum=0.)
    t_total = abs(float(total_torque_gear1_Nm))
    n_paths = max(int(n_parallel_paths), 1)
    flags: List[str] = []

    if branch_shares is not None:
        if len(branch_shares) != n_paths:
            raise ValueError(f"branch_shares length ({len(branch_shares)}) must equal n_parallel_paths ({n_paths})")
        shares = [float(s) for s in branch_shares]
        if any(s < 0.0 for s in shares) or abs(sum(shares) - 1.0) > 1e-6:
            raise ValueError("branch_shares must be >= 0 and sum to 1.0")
    else:
        shares = [1.0 / n_paths] * n_paths

    if k_gamma is None:
        if branch_shares is not None:
            k_use = max(shares) * n_paths
            k_src = "branch_shares"
        else:
            k_use = 1.0 if n_paths == 1 else default_k_gamma(n_paths, floating_member=floating_member)
            k_src = "input" if n_paths == 1 else "screening_default"
            if n_paths > 1:
                flags.append("k_gamma_screening_default_used")
    else:
        k_use = float(k_gamma)
        if k_use < 1.0 or k_use > n_paths:
            raise ValueError(f"k_gamma ({k_use}) must lie in [1.0, {n_paths}]")
        k_shares = max(shares) * n_paths
        if branch_shares is not None and k_shares > k_use + 1e-12:
            k_use = k_shares
            k_src = "max(input,branch_shares)"
            flags.append("k_gamma_raised_to_match_max_physical_branch_share")
        else:
            k_src = "input"

    branch_torques = [t_total * s for s in shares]
    t_nom = t_total / n_paths
    t_worst = t_nom * k_use

    d1_mm = pair_geom.gear1.d_mm
    dw1_mm = pair_geom.d_w1_mm
    awt = math.radians(pair_geom.alpha_wt_deg)
    beta = math.radians(abs(pair_geom.gear1.beta_deg))
    at = math.radians(pair_geom.gear1.alpha_t_deg)
    bb = math.radians(abs(pair_geom.gear1.beta_b_deg))

    f_t = 2000.0 * t_nom / d1_mm
    f_tw = 2000.0 * t_nom / dw1_mm
    f_r = f_tw * math.tan(awt)
    f_a = f_t * math.tan(beta)
    f_n = f_t / max(math.cos(at) * math.cos(bb), 1e-12)

    v_t = abs(float(gear1_rel_speed_rpm)) * math.pi * dw1_mm / 60000.0
    if v_t < 0.05 and gear1_rel_speed_rpm != 0.0:
        flags.append("very_low_pitch_velocity")
    if abs(beta) > 1e-9:
        flags.append("helical_axial_thrust_present")

    state: ValidityState = "estimated" if k_src == "screening_default" else "valid"
    return MeshForces(
        mesh_name=mesh_name,
        transmitted_torque_gear1_Nm=t_total,
        n_parallel_paths=n_paths,
        branch_shares=shares,
        branch_torques_gear1_Nm=branch_torques,
        torque_nominal_branch_Nm=t_nom,
        torque_worst_branch_Nm=t_worst,
        F_t_N=f_t,
        F_tw_N=f_tw,
        F_r_N=f_r,
        F_a_N=f_a,
        F_n_N=f_n,
        F_t_worst_branch_N=f_t * k_use,
        F_tw_worst_branch_N=f_tw * k_use,
        F_r_worst_branch_N=f_r * k_use,
        F_a_worst_branch_N=f_a * k_use,
        pitch_line_velocity_m_s=v_t,
        k_gamma=k_use,
        k_gamma_source=k_src,
        state=state,
        applicability_evidence={"sum_branch_torques_Nm": sum(branch_torques), "k_gamma": k_use},
        flags=flags,
    )


def shaft_bearing_split(
    *,
    f_r_mesh_N: float,
    f_a_mesh_N: float,
    rpm: float,
    span_mm: Optional[float],
    gear_offset_from_a_mm: Optional[float],
    role_prefix: str,
    f_t_mesh_N: float = 0.0,
    axial_on_both: bool = False,
) -> List[BearingReactionLoad]:
    """Compute radial/axial bearing reactions on a two-bearing shaft (beam lever rule).

    Radial bearing load combines transverse tooth force resultant ``hypot(F_tw, F_r)``
    unless ``f_t_mesh_N`` is zero (already pre-combined resultant).
    """
    f_trans_N = math.hypot(float(f_t_mesh_N), float(f_r_mesh_N))
    flags: List[str] = []
    missing: List[str] = []
    if span_mm is None or span_mm <= 0.0 or gear_offset_from_a_mm is None:
        f_a_rad = 0.5 * f_trans_N
        f_b_rad = 0.5 * f_trans_N
        state: ValidityState = "estimated"
        flags.append("simplified_equal_bearing_radial_split")
        if span_mm is None or span_mm <= 0.0:
            missing.append("bearing_span_mm")
        if gear_offset_from_a_mm is None:
            missing.append("gear_offset_from_a_mm")
    else:
        a = float(gear_offset_from_a_mm)
        b_arm = float(span_mm) - a
        f_b_rad = abs(f_trans_N * a / float(span_mm))
        f_a_rad = abs(f_trans_N * b_arm / float(span_mm))
        state = "valid"

    fa_a = 0.5 * f_a_mesh_N if axial_on_both else f_a_mesh_N
    fa_b = 0.5 * f_a_mesh_N if axial_on_both else 0.0

    return [
        BearingReactionLoad(
            role=f"{role_prefix}_A",
            F_r_N=f_a_rad,
            F_a_N=fa_a,
            F_r_worst_branch_N=f_a_rad,
            F_a_worst_branch_N=fa_a,
            speed_rpm=abs(float(rpm)),
            state=state,
            missing_inputs=list(missing),
            flags=list(flags),
        ),
        BearingReactionLoad(
            role=f"{role_prefix}_B",
            F_r_N=f_b_rad,
            F_a_N=fa_b,
            F_r_worst_branch_N=f_b_rad,
            F_a_worst_branch_N=fa_b,
            speed_rpm=abs(float(rpm)),
            state=state,
            missing_inputs=list(missing),
            flags=list(flags),
        ),
    ]


def planet_bearing_reaction(
    *,
    mf_sp: MeshForces,
    mf_pr: MeshForces,
    planet_rel_rpm: float,
    n_planets: int,
    d_w_planet_sp_mm: float,
    d_w_planet_pr_mm: Optional[float] = None,
    n_bearings_per_planet: int = 1,
    bearing_spacing_mm: float = 0.0,
) -> BearingReactionLoad:
    """Resultant nominal and worst-branch loads on one planet bearing set.

    Note: ``speed_rpm`` is ``|n_planet - n_carrier|`` (relative to carrier), NOT
    the planet's absolute orbital spin.
    """
    d_p1 = d_w_planet_sp_mm
    d_p2 = d_w_planet_pr_mm if d_w_planet_pr_mm is not None else d_p1

    ft_sum_nom = mf_sp.F_tw_N + mf_pr.F_tw_N
    fr_diff_nom = mf_sp.F_r_N - mf_pr.F_r_N
    f_rad_nom = math.hypot(ft_sum_nom, fr_diff_nom)
    f_ax_nom = abs(mf_sp.F_a_N - mf_pr.F_a_N)

    ft_sum_worst = mf_sp.F_tw_worst_branch_N + mf_pr.F_tw_worst_branch_N
    fr_diff_worst = mf_sp.F_r_worst_branch_N - mf_pr.F_r_worst_branch_N
    f_rad_worst = math.hypot(ft_sum_worst, fr_diff_worst)
    f_ax_worst = abs(mf_sp.F_a_worst_branch_N - mf_pr.F_a_worst_branch_N)

    m_tilt_worst_Nm = 0.5 * (mf_sp.F_a_worst_branch_N * d_p1 + mf_pr.F_a_worst_branch_N * d_p2) / 1000.0

    flags: List[str] = []
    if n_bearings_per_planet == 2 and bearing_spacing_mm > 0.0:
        f_couple_nom = (0.5 * (mf_sp.F_a_N * d_p1 + mf_pr.F_a_N * d_p2)) / bearing_spacing_mm
        f_couple_worst = (m_tilt_worst_Nm * 1000.0) / bearing_spacing_mm
        f_rad_nom_brg = math.hypot(0.5 * f_rad_nom, f_couple_nom)
        f_rad_worst_brg = math.hypot(0.5 * f_rad_worst, f_couple_worst)
        n_total_brg = 2 * n_planets
    else:
        div = max(n_bearings_per_planet, 1)
        f_rad_nom_brg = f_rad_nom / div
        f_rad_worst_brg = f_rad_worst / div
        n_total_brg = div * n_planets
        if m_tilt_worst_Nm > 1e-6:
            flags.append("helical_planet_overturning_moment_present")

    return BearingReactionLoad(
        role="planet",
        F_r_N=f_rad_nom_brg,
        F_a_N=f_ax_nom,
        F_r_worst_branch_N=f_rad_worst_brg,
        F_a_worst_branch_N=f_ax_worst,
        speed_rpm=abs(float(planet_rel_rpm)),
        overturning_moment_Nm=m_tilt_worst_Nm,
        n_identical_bearings=n_total_brg,
        state="valid",
        applicability_evidence={
            "planet_relative_speed_rpm": abs(float(planet_rel_rpm)),
            "nominal_pin_force_N": f_rad_nom,
            "worst_branch_pin_force_N": f_rad_worst,
        },
        method="PlanetaryPinLoad_Equilibrium",
        flags=flags,
    )
