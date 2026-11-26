# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Gear mesh, bearing, churning/drag losses and signed planetary efficiency
(ISO/TR 14179-2, Kreines / Mueller VDI 2157, and SKF bearings bridge).

Validity ranges (ISO/TR 14179-2 cl. 6.2.2):
- Tooth friction correlation ``mu_mz`` is calibrated for oil-lubricated steel
  involute gears with pitch-line speed ``v_t in [0.5, 50.0] m/s``, specific
  normal load ``w_bt in [50, 2000] N/mm``, and dynamic viscosity
  ``eta_oil in [2, 500] mPa*s``. Outside those bounds results are flagged and
  marked ``state="estimated"``.
- Grease churning drag is outside ISO/TR 14179-2 oil-splash models; when grease
  is used, an explicit ``grease_drag_torque_Nm`` input is required (flagged as an
  estimate).
- At zero speed (standstill), transmission efficiency is undefined (``None``,
  ``state="unsupported"``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .bearings import GearBearingSpec, evaluate_bearing
from .common import DictResultMixin, ValidityState, worst_state
from .geometry import PairGeometryResult
from .kinematics import StageKinematicsResult
from .loads import BearingReactionLoad, MeshForces, compute_mesh_forces
from .lubrication import GearLubricant


@dataclass
class MeshLossResult(DictResultMixin):
    """Mesh friction loss P_VZ = |P_mesh_rel| * mu_mz * H_V (ISO/TR 14179-2)."""

    mesh_name: str
    transmitted_power_rel_W: float        # |P_mesh_rel| in carrier frame
    H_V: float                            # geometric tooth loss factor
    mu_mz: float                          # mean coefficient of tooth friction
    P_VZ_W: float                         # mesh friction power loss [W]
    mesh_efficiency: Optional[float]      # 1 - mu_mz * H_V (None at standstill)
    pitch_velocity_m_s: float
    eta_oil_mPas: float
    w_bt_N_mm: float
    rho_red_C_mm: float
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "ISO_TR_14179_2_MeshLoss"
    flags: List[str] = field(default_factory=list)


@dataclass
class PlanetaryEfficiencyResult(DictResultMixin):
    """Signed power-flow solution for a simple or compound planetary stage (VDI 2157)."""

    fixed_member: str
    driving_member: str
    driven_member: str
    stationary_carrier_ratio_u: float
    eta0_sr: Optional[float]
    eta0_rs: Optional[float]
    active_branch: str                    # "sun_to_ring_rel" | "ring_to_sun_rel" | "co_rotation" | "standstill" | "inadmissible"
    member_torques_Nm: Dict[str, float]   # torques acting ON stage (sum == 0)
    member_speeds_rad_s: Dict[str, float]
    member_powers_W: Dict[str, float]     # P_j = T_j * omega_j
    power_in_W: float
    power_out_useful_W: float
    power_loss_mesh_W: float
    power_loss_total_W: float
    efficiency_mesh_only: Optional[float]
    efficiency_total: Optional[float]
    state: ValidityState = "valid"
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    method: str = "VDI2157_Kreines_Mueller_SignedPowerFlow"
    flags: List[str] = field(default_factory=list)


def geometric_loss_factor_hv(pair_geom: PairGeometryResult) -> float:
    """Geometric gear loss factor H_V (Ohlendorf / ISO/TR 14179-2 cl. 6.2.1).

    For external pairs: ``H_V = pi*(u + 1)/(z1*u*cos(beta_b)) * (1 - eps_a + eps_1^2 + eps_2^2)``
    For internal pairs: ``H_V = pi*(u - 1)/(z1*u*cos(beta_b)) * (1 - eps_a + eps_1^2 + eps_2^2)``
    """
    z1 = pair_geom.gear1.z
    u = pair_geom.u_ratio
    bb = math.radians(abs(pair_geom.gear1.beta_b_deg))
    sign_term = (u - 1.0) if pair_geom.is_internal_mesh else (u + 1.0)
    geom_factor = math.pi * sign_term / (z1 * u * max(math.cos(bb), 1e-9))
    eps_term = (
        1.0
        - pair_geom.epsilon_alpha
        + pair_geom.epsilon_1 ** 2
        + pair_geom.epsilon_2 ** 2
    )
    return max(geom_factor * eps_term, 0.0)


def compute_mesh_loss(
    *,
    mesh_name: str,
    pair_geom: PairGeometryResult,
    mesh_forces: MeshForces,
    transmitted_power_rel_W: float,
    lubricant: GearLubricant,
    temp_C: float,
    Ra1_um: float = 0.8,
    Ra2_um: float = 0.8,
) -> MeshLossResult:
    """Compute ISO/TR 14179-2 mesh friction coefficient mu_mz and loss P_VZ [W].

    Uses the physical nominal branch force (``mesh_forces.F_n_N``), never inflated
    by ``K_gamma``.
    """
    flags: List[str] = list(lubricant.flags)
    missing: List[str] = []

    h_v = geometric_loss_factor_hv(pair_geom)
    nu_op = lubricant.kinematic_viscosity_mm2_s(temp_C)
    rho_op = lubricant.density_kg_m3(temp_C)
    eta_mPas = nu_op * rho_op * 1.0e-3

    awt = math.radians(pair_geom.alpha_wt_deg)
    bb = math.radians(abs(pair_geom.gear1.beta_b_deg))
    r_w1 = 0.5 * pair_geom.d_w1_mm
    r_w2 = 0.5 * pair_geom.d_w2_mm
    rho1 = r_w1 * math.sin(awt) / max(math.cos(bb), 1e-9)
    rho2 = r_w2 * math.sin(awt) / max(math.cos(bb), 1e-9)
    inv_rho = (1.0 / rho1 - 1.0 / rho2) if pair_geom.is_internal_mesh else (1.0 / rho1 + 1.0 / rho2)
    rho_red_mm = 1.0 / max(inv_rho, 1e-9)

    b_eff = min(pair_geom.gear1.b_mm, pair_geom.gear2.b_mm)
    w_bt = mesh_forces.F_n_N / max(b_eff, 1e-6)
    vt = abs(mesh_forces.pitch_line_velocity_m_s)
    ra_mean = max(0.5 * (float(Ra1_um) + float(Ra2_um)), 0.1)

    p_rel = abs(float(transmitted_power_rel_W))
    if vt == 0.0:
        return MeshLossResult(
            mesh_name=mesh_name,
            transmitted_power_rel_W=0.0,
            H_V=h_v,
            mu_mz=0.0,
            P_VZ_W=0.0,
            mesh_efficiency=None,
            pitch_velocity_m_s=0.0,
            eta_oil_mPas=eta_mPas,
            w_bt_N_mm=w_bt,
            rho_red_C_mm=rho_red_mm,
            state="unsupported",
            applicability_evidence={"reason": "zero pitch velocity"},
            flags=["zero_speed_efficiency_undefined"],
        )

    # ISO/TR 14179-2 cl. 6.2.2 validity checks
    outside_domain = False
    if vt < 0.5 or vt > 50.0:
        outside_domain = True
        flags.append("pitch_velocity_outside_iso14179_2_domain_0.5_50_ms")
    if w_bt < 50.0 or w_bt > 2000.0:
        outside_domain = True
        flags.append("specific_load_outside_iso14179_2_domain_50_2000_N_mm")
    if eta_mPas < 2.0 or eta_mPas > 500.0:
        outside_domain = True
        flags.append("viscosity_outside_iso14179_2_domain_2_500_mPas")
    if lubricant.kind == "grease":
        outside_domain = True
        flags.append("grease_mesh_friction_uses_base_oil_estimate")

    v_sigma_c = max(2.0 * vt * math.sin(awt), 1e-3)
    w_use = max(w_bt, 1.0)
    mu_raw = (
        0.048
        * ((w_use / (v_sigma_c * rho_red_mm)) ** 0.2)
        * (max(eta_mPas, 0.5) ** -0.05)
        * (ra_mean ** 0.25)
        * float(lubricant.X_L_friction)
    )
    # Physical Coulomb bound in boundary regime (0.005 .. 0.15)
    mu_mz = min(max(mu_raw, 0.005), 0.15)
    if mu_mz != mu_raw:
        flags.append("mu_mz_clamped_to_physical_bounds")

    p_vz = p_rel * mu_mz * h_v
    eta_mesh = max(1.0 - mu_mz * h_v, 0.0)
    state: ValidityState = "estimated" if outside_domain else "valid"

    return MeshLossResult(
        mesh_name=mesh_name,
        transmitted_power_rel_W=p_rel,
        H_V=h_v,
        mu_mz=mu_mz,
        P_VZ_W=p_vz,
        mesh_efficiency=eta_mesh,
        pitch_velocity_m_s=vt,
        eta_oil_mPas=eta_mPas,
        w_bt_N_mm=w_bt,
        rho_red_C_mm=rho_red_mm,
        state=state,
        applicability_evidence={
            "method": "ISO/TR 14179-2 cl. 6.2.1-6.2.2",
            "v_t_domain_m_s": [0.5, 50.0],
            "w_bt_domain_N_mm": [50.0, 2000.0],
            "eta_domain_mPas": [2.0, 500.0],
            "mu_mz_raw": mu_raw,
        },
        missing_inputs=missing,
        flags=flags,
    )


def oil_churning_loss_W(
    *,
    d_a_mm: float,
    b_mm: float,
    rpm: float,
    lubricant: GearLubricant,
    temp_C: float,
    immersion_depth_mm: float = 0.0,
) -> float:
    """Oil splash churning loss [W] for a partially immersed gear (ISO/TR 14179-2 style).

    Returns 0.0 when ``immersion_depth_mm <= 0`` (e.g. oil-air / dry-sump / spray).
    """
    n = abs(float(rpm))
    h_imm = max(float(immersion_depth_mm), 0.0)
    if n <= 0.0 or h_imm <= 0.0 or lubricant.kind != "oil":
        return 0.0
    # Simplified ISO/TR 14179-2 cl. 5.2.3 drag torque scaling with submerged area and v_t
    r_m = 0.5 * d_a_mm * 1.0e-3
    b_m = b_mm * 1.0e-3
    omega = n * math.pi / 30.0
    v_t = omega * r_m
    rho = lubricant.density_kg_m3(temp_C)
    nu = lubricant.kinematic_viscosity_mm2_s(temp_C) * 1.0e-6
    re = max(v_t * r_m / max(nu, 1e-9), 1.0)
    c_m = 0.08 * (re ** -0.25) * min(h_imm / max(0.5 * d_a_mm, 1e-3), 1.0)
    # Submerged swept area ~ 2 * r * (b + r * frac)
    a_sub = 2.0 * math.pi * r_m * b_m * min(h_imm / max(d_a_mm, 1e-3), 0.5)
    t_churn = 0.5 * rho * (v_t ** 2) * r_m * a_sub * c_m
    return t_churn * omega


def solve_planetary_power_flow(
    *,
    u: float,
    eta0_sr: float,
    eta0_rs: Optional[float] = None,
    fixed_member: str,
    omega_s_rad_s: float,
    omega_r_rad_s: float,
    omega_c_rad_s: float,
    known_member: str,
    known_torque_Nm: float,
    drag_loss_W: float = 0.0,
) -> PlanetaryEfficiencyResult:
    """Solve signed planetary torques, powers, and efficiency (Kreines / Mueller / VDI 2157).

    Implements the exact signed balance (Astra review Q4):
    - ``T_j`` acts **on** the stage; ``P_j = T_j * omega_j``.
    - ``sum(T_j) = 0`` (mesh-only) and ``sum(P_j) = P_loss >= 0``.
    - Branch selection by relative virtual power in the carrier frame:
      when ``T_s * (omega_s - omega_c) > 0`` -> ``T_r = u * eta0_sr * T_s``;
      when ``T_r * (omega_r - omega_c) > 0`` -> ``T_r = (u / eta0_rs) * T_s``.
    - Housing/shaft drag ``drag_loss_W >= 0`` is applied separately at the moving
      driving port without reapplying mesh loss.
    """
    if u <= 1.0:
        raise ValueError(f"Planetary ratio u must be > 1, got {u}")
    e_sr = float(eta0_sr)
    e_rs = float(eta0_sr if eta0_rs is None else eta0_rs)
    if not (0.0 < e_sr <= 1.0 and 0.0 < e_rs <= 1.0):
        raise ValueError("Stationary-carrier efficiencies eta0 must lie in (0, 1]")

    omegas = {
        "sun": float(omega_s_rad_s),
        "ring": float(omega_r_rad_s),
        "carrier": float(omega_c_rad_s),
    }
    if abs(omegas[fixed_member]) > 1e-9:
        raise ValueError(f"fixed_member {fixed_member!r} must have speed 0, got {omegas[fixed_member]}")

    v_s = omegas["sun"] - omegas["carrier"]
    v_r = omegas["ring"] - omegas["carrier"]

    # Check standstill (all speeds zero)
    if max(abs(w) for w in omegas.values()) <= 1e-12:
        # Ideal static equilibrium at zero speed; efficiency undefined
        if known_member == "sun":
            ts = float(known_torque_Nm)
            tr = u * ts
        elif known_member == "ring":
            tr = float(known_torque_Nm)
            ts = tr / u
        else:
            tc = float(known_torque_Nm)
            ts = -tc / (1.0 + u)
            tr = u * ts
        tc = -(ts + tr)
        torques = {"sun": ts, "ring": tr, "carrier": tc}
        powers = {k: 0.0 for k in ("sun", "ring", "carrier")}
        return PlanetaryEfficiencyResult(
            fixed_member=fixed_member,
            driving_member=known_member,
            driven_member="",
            stationary_carrier_ratio_u=u,
            eta0_sr=e_sr,
            eta0_rs=e_rs,
            active_branch="standstill",
            member_torques_Nm=torques,
            member_speeds_rad_s=omegas,
            member_powers_W=powers,
            power_in_W=0.0,
            power_out_useful_W=0.0,
            power_loss_mesh_W=0.0,
            power_loss_total_W=0.0,
            efficiency_mesh_only=None,
            efficiency_total=None,
            state="unsupported",
            applicability_evidence={"reason": "standstill: efficiency is undefined at zero speed"},
            flags=["zero_speed_efficiency_undefined"],
        )

    # Helper to solve one mesh-only branch given an arbitrary known_member torque
    def _solve_branch_for(r_eff: float, member: str, val_Nm: float) -> Dict[str, float]:
        if member == "sun":
            ts = float(val_Nm)
            tr = r_eff * ts
            tc = -(ts + tr)
        elif member == "ring":
            tr = float(val_Nm)
            ts = tr / r_eff
            tc = -(ts + tr)
        elif member == "carrier":
            tc = float(val_Nm)
            ts = -tc / (1.0 + r_eff)
            tr = r_eff * ts
        else:
            raise ValueError(f"Unknown member {member!r}")
        return {"sun": ts, "ring": tr, "carrier": tc}

    drag = max(float(drag_loss_W), 0.0)

    # Co-rotation check (v_s == 0 and v_r == 0): zero mesh-relative motion
    if abs(v_s) <= 1e-12 and abs(v_r) <= 1e-12:
        trial = _solve_branch_for(u, known_member, known_torque_Nm)
        trial_powers = {k: trial[k] * omegas[k] for k in ("sun", "ring", "carrier")}
        moving = [k for k in ("sun", "ring", "carrier") if k != fixed_member]
        drv = max(moving, key=lambda k: trial_powers[k])
        drn = min(moving, key=lambda k: trial_powers[k])
        if drag > 0.0 and abs(omegas[drv]) > 1e-12:
            t_drag = drag / omegas[drv]
            if known_member == drv:
                mesh_torques = _solve_branch_for(u, drv, float(known_torque_Nm) - t_drag)
                torques = dict(mesh_torques)
                torques[drv] = float(known_torque_Nm)
            else:
                mesh_torques = trial
                torques = dict(mesh_torques)
                torques[drv] = mesh_torques[drv] + t_drag
            torques["housing_drag"] = -t_drag
        else:
            mesh_torques = trial
            torques = dict(mesh_torques)
        powers = {k: torques[k] * omegas.get(k, 0.0) for k in torques}
        p_in_tot = powers[drv] if (drag == 0.0 or abs(omegas[drv]) > 1e-12) else (trial_powers[drv] + drag)
        p_out_useful = -powers[drn]
        eta_tot = (p_out_useful / p_in_tot) if p_in_tot > 0.0 else None
        return PlanetaryEfficiencyResult(
            fixed_member=fixed_member,
            driving_member=drv,
            driven_member=drn,
            stationary_carrier_ratio_u=u,
            eta0_sr=e_sr,
            eta0_rs=e_rs,
            active_branch="co_rotation",
            member_torques_Nm=torques,
            member_speeds_rad_s=omegas,
            member_powers_W=powers,
            power_in_W=p_in_tot,
            power_out_useful_W=p_out_useful,
            power_loss_mesh_W=0.0,
            power_loss_total_W=drag,
            efficiency_mesh_only=1.0,
            efficiency_total=eta_tot,
            state="valid",
            applicability_evidence={
                "mesh_torques_Nm": mesh_torques,
                "housing_drag_reaction_Nm": torques.get("housing_drag", 0.0),
                "torque_sum_total_Nm": sum(torques.values()),
                "torque_sum_mesh_Nm": sum(mesh_torques.values()),
                "power_balance_residual_W": p_in_tot - (p_out_useful + drag),
            },
            flags=["co_rotation_zero_mesh_relative_loss"],
        )

    # Select admissible branch via trial solve (before subtracting driving drag):
    # Branch A: sun -> ring in carrier frame (T_s * v_s > 0): T_r = u * e_sr * T_s
    # Branch B: ring -> sun in carrier frame (T_r * v_r > 0): T_r = (u / e_rs) * T_s
    cand_a = _solve_branch_for(u * e_sr, known_member, known_torque_Nm)
    cand_b = _solve_branch_for(u / e_rs, known_member, known_torque_Nm)

    chosen_branch = "inadmissible"
    trial_torques: Dict[str, float] = {}
    r_eff_chosen = u * e_sr
    if cand_a["sun"] * v_s >= -1e-12:
        p_loss_a = sum(cand_a[k] * omegas[k] for k in ("sun", "ring", "carrier"))
        if p_loss_a >= -1e-12:
            chosen_branch = "sun_to_ring_rel"
            trial_torques = cand_a
            r_eff_chosen = u * e_sr
    if chosen_branch == "inadmissible" and cand_b["ring"] * v_r >= -1e-12:
        p_loss_b = sum(cand_b[k] * omegas[k] for k in ("sun", "ring", "carrier"))
        if p_loss_b >= -1e-12:
            chosen_branch = "ring_to_sun_rel"
            trial_torques = cand_b
            r_eff_chosen = u / e_rs

    if chosen_branch == "inadmissible":
        return PlanetaryEfficiencyResult(
            fixed_member=fixed_member,
            driving_member=known_member,
            driven_member="",
            stationary_carrier_ratio_u=u,
            eta0_sr=e_sr,
            eta0_rs=e_rs,
            active_branch="inadmissible",
            member_torques_Nm=cand_a,
            member_speeds_rad_s=omegas,
            member_powers_W={k: cand_a[k] * omegas[k] for k in ("sun", "ring", "carrier")},
            power_in_W=0.0,
            power_out_useful_W=0.0,
            power_loss_mesh_W=0.0,
            power_loss_total_W=0.0,
            efficiency_mesh_only=None,
            efficiency_total=None,
            state="failed",
            flags=["inadmissible_power_flow_branch"],
        )

    trial_powers = {k: trial_torques[k] * omegas[k] for k in ("sun", "ring", "carrier")}
    moving = [k for k in ("sun", "ring", "carrier") if k != fixed_member]
    drv = max(moving, key=lambda k: trial_powers[k])
    drn = min(moving, key=lambda k: trial_powers[k])

    state: ValidityState = "valid"
    flags: List[str] = []

    if drag > 0.0 and abs(omegas[drv]) > 1e-12:
        t_drag = drag / omegas[drv]
        if known_member == drv:
            t_mesh_drv = float(known_torque_Nm) - t_drag
            if t_mesh_drv * omegas[drv] < -1e-12:
                t_mesh_drv = 0.0
                state = "failed"
                flags.append("input_torque_insufficient_to_overcome_drag")
            mesh_torques = _solve_branch_for(r_eff_chosen, drv, t_mesh_drv)
            torques = dict(mesh_torques)
            torques[drv] = float(known_torque_Nm)
        else:
            mesh_torques = dict(trial_torques)
            torques = dict(mesh_torques)
            torques[drv] = mesh_torques[drv] + t_drag
        torques["housing_drag"] = -t_drag
    else:
        mesh_torques = dict(trial_torques)
        torques = dict(mesh_torques)

    mesh_powers = {k: mesh_torques[k] * omegas[k] for k in ("sun", "ring", "carrier")}
    powers = {k: torques[k] * omegas.get(k, 0.0) for k in torques}

    p_in_mesh = mesh_powers[drv]
    p_out_useful = -mesh_powers[drn]
    p_loss_mesh = p_in_mesh - p_out_useful
    eta_mesh = (p_out_useful / p_in_mesh) if p_in_mesh > 0.0 else None

    p_in_tot = powers[drv] if abs(omegas[drv]) > 1e-12 else (p_in_mesh + drag)
    p_loss_tot = p_loss_mesh + drag
    eta_tot = (p_out_useful / p_in_tot) if (p_in_tot > 0.0 and state != "failed") else None
    if eta_mesh is not None and eta_mesh < 0.0:
        state = "failed"
        flags.append("negative_efficiency_self_locking_branch")

    return PlanetaryEfficiencyResult(
        fixed_member=fixed_member,
        driving_member=drv,
        driven_member=drn,
        stationary_carrier_ratio_u=u,
        eta0_sr=e_sr,
        eta0_rs=e_rs,
        active_branch=chosen_branch,
        member_torques_Nm=torques,
        member_speeds_rad_s=omegas,
        member_powers_W=powers,
        power_in_W=p_in_tot,
        power_out_useful_W=p_out_useful,
        power_loss_mesh_W=p_loss_mesh,
        power_loss_total_W=p_loss_tot,
        efficiency_mesh_only=eta_mesh,
        efficiency_total=eta_tot,
        state=state,
        applicability_evidence={
            "mesh_torques_Nm": mesh_torques,
            "housing_drag_reaction_Nm": torques.get("housing_drag", 0.0),
            "torque_sum_total_Nm": sum(torques.values()),
            "torque_sum_mesh_Nm": sum(mesh_torques.values()),
            "power_balance_residual_W": p_in_tot - (p_out_useful + p_loss_tot),
        },
        flags=flags,
    )
