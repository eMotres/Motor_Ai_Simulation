"""Post-processing: compute B, H, torque from a trained A_z network.

After the PINN is trained it gives us A_z(x, y).  From this we recover:

    B_x =  ∂A_z/∂y        [T]
    B_y = −∂A_z/∂x        [T]
    |B|  = √(B_x² + B_y²) [T]

Torque (Maxwell stress tensor on a circle in the air gap):

    T = (L / μ₀) · ∮ (B_r · B_t) r dφ           [N·m]

where L is stack length, B_r / B_t are radial / tangential flux density on a
circle of radius r_airgap, and the integral is taken over one full revolution.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, Optional, Tuple

import numpy as np

MU_0 = 4e-7 * math.pi   # [H/m]


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Numerical gradient (finite differences on a grid)
# ─────────────────────────────────────────────────────────────────────────────

def compute_flux_density(
    A_z_fn: Callable[[np.ndarray, np.ndarray], np.ndarray],
    x_grid: np.ndarray,
    y_grid: np.ndarray,
    h: float = 1e-5,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute B_x, B_y, |B| on a 2-D grid using central differences.

    Parameters
    ----------
    A_z_fn : callable(x, y) → ndarray
        Trained network or analytic function giving A_z at grid points.
    x_grid, y_grid : ndarray, shape (N, M)
        Meshgrid of evaluation coordinates [m].
    h : float
        Finite-difference step [m].

    Returns
    -------
    B_x, B_y, B_mag : ndarray, same shape as x_grid
    """
    #  B_x =  ∂A_z/∂y  ≈  (A_z(x, y+h) − A_z(x, y−h)) / (2h)
    B_x = (A_z_fn(x_grid, y_grid + h) - A_z_fn(x_grid, y_grid - h)) / (2 * h)
    #  B_y = −∂A_z/∂x  ≈ −(A_z(x+h, y) − A_z(x−h, y)) / (2h)
    B_y = -(A_z_fn(x_grid + h, y_grid) - A_z_fn(x_grid - h, y_grid)) / (2 * h)
    B_mag = np.sqrt(B_x**2 + B_y**2)
    return B_x, B_y, B_mag


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Torque via Maxwell stress tensor
# ─────────────────────────────────────────────────────────────────────────────

def compute_torque_maxwell(
    A_z_fn: Callable[[np.ndarray, np.ndarray], np.ndarray],
    r_eval: float,
    stack_length: float,
    n_points: int = 720,
    h: float = 1e-6,
) -> float:
    """Torque on the rotor via the Maxwell stress tensor.

    Integrates T = (L / μ₀) · ∮ B_r · B_t · r dφ along a circle of radius
    r_eval inside the air gap.

    Parameters
    ----------
    A_z_fn     : callable(x, y) → ndarray
    r_eval     : float   Radius of the integration contour [m].
    stack_length: float  Axial length of motor [m].
    n_points   : int     Number of integration points.
    h          : float   Finite-difference step [m].

    Returns
    -------
    torque : float  [N·m]
    """
    phi = np.linspace(0, 2 * math.pi, n_points, endpoint=False)
    x = r_eval * np.cos(phi)
    y = r_eval * np.sin(phi)

    # Flux density on the contour
    B_x, B_y, _ = compute_flux_density(A_z_fn, x, y, h=h)

    # Radial and tangential components
    B_r = B_x * np.cos(phi) + B_y * np.sin(phi)
    B_t = -B_x * np.sin(phi) + B_y * np.cos(phi)

    # Maxwell stress tensor torque integral
    #   T = (L·r / μ₀) · (1/2π) · ∫₀²π B_r · B_t dφ  (× 2π for full circle)
    integrand = B_r * B_t
    torque = (stack_length * r_eval / MU_0) * np.trapz(integrand, phi)
    return float(torque)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Core losses (Bertotti model)
# ─────────────────────────────────────────────────────────────────────────────

def compute_core_losses(
    B_mag_grid: np.ndarray,
    frequency: float,
    kh: float,
    kc: float,
    ke: float,
    cell_area_m2: float,
    stack_length: float,
) -> Dict[str, float]:
    """Bertotti core-loss model: P = kh·f·B² + kc·f²·B² + ke·f^1.5·B^1.5·√B

    Parameters
    ----------
    B_mag_grid  : ndarray   |B| at each grid cell [T].
    frequency   : float     Electrical frequency [Hz].
    kh, kc, ke  : float     Bertotti coefficients from materials library.
    cell_area_m2: float     Area of each grid cell [m²].
    stack_length: float     Axial length [m].

    Returns
    -------
    dict with keys: hysteresis_W, eddy_W, excess_W, total_W
    """
    B2 = B_mag_grid**2
    B15 = B_mag_grid**1.5

    p_hyst  = kh * frequency * B2
    p_eddy  = kc * frequency**2 * B2
    p_exc   = ke * frequency**1.5 * B15

    volume_per_cell = cell_area_m2 * stack_length

    def total(p):
        return float(np.sum(p) * volume_per_cell)   # [W]

    return {
        "hysteresis_W": total(p_hyst),
        "eddy_W":       total(p_eddy),
        "excess_W":     total(p_exc),
        "total_W":      total(p_hyst + p_eddy + p_exc),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Copper losses  (analytical — no field needed)
# ─────────────────────────────────────────────────────────────────────────────

def compute_copper_losses(
    I_phase_rms: float,       # Phase RMS current [A]
    n_coils_per_phase: int,   # Coils per phase (e.g. 4)
    n_parallel: int,          # Parallel branches
    n_series: int,            # Series coils per branch
    n_wires_per_slot: int,    # Conductors in one slot (= turns per coil)
    wire_width_m: float,      # Wire width [m]
    wire_height_m: float,     # Wire height [m]
    motor_length_m: float,    # Axial stack length [m]
    r_slot_mid_m: float,      # Radius at mid-slot (for end-winding estimate) [m]
    n_slots: int,             # Total number of stator slots
    rho_cu: float = 1.72e-8,  # Cu resistivity at 20°C [Ω·m]
    temp_coeff: float = 0.0039,  # Cu temperature coefficient [1/°C]
    temperature_c: float = 120.0,  # Operating temperature [°C]
) -> Dict[str, float]:
    """Joule losses in the 3-phase winding.

    R_coil = ρ(T) × L_turn × n_turns / A_wire

    End winding estimated as 1.4 × slot_pitch at mid-slot radius.
    """
    # Temperature-corrected resistivity
    rho = rho_cu * (1 + temp_coeff * (temperature_c - 20))

    # Wire cross-section [m²]
    A_wire = wire_width_m * wire_height_m

    # Slot pitch at mid-slot radius [m]
    slot_pitch = 2 * math.pi * r_slot_mid_m / n_slots

    # One turn length: 2 × active + 2 × end-winding
    L_end_winding = 1.4 * slot_pitch   # empirical factor
    L_turn = 2 * (motor_length_m + L_end_winding)  # [m]

    # Resistance of one coil (n_wires = turns, all in series)
    R_coil = rho * L_turn * n_wires_per_slot / A_wire  # [Ω]

    # Phase resistance: n_series coils / n_parallel branches
    R_phase = (n_series * R_coil) / n_parallel  # [Ω]

    # Copper loss per phase = R × I²
    P_cu_phase = R_phase * I_phase_rms ** 2     # [W]
    P_cu_total = 3 * P_cu_phase                 # [W]

    # I_coil for reference
    I_coil_rms = I_phase_rms / n_parallel

    return {
        "R_coil_ohm":      round(R_coil, 5),
        "R_phase_ohm":     round(R_phase, 5),
        "L_turn_mm":       round(L_turn * 1e3, 1),
        "I_coil_rms_A":    round(I_coil_rms, 2),
        "P_cu_phase_W":    round(P_cu_phase, 2),
        "P_cu_total_W":    round(P_cu_total, 2),
        "rho_used":        rho,
        "temperature_c":   temperature_c,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Iron losses by domain  (Bertotti, requires B-field from PINN)
# ─────────────────────────────────────────────────────────────────────────────

def compute_iron_losses_by_domain(
    A_z_fn: Callable[[np.ndarray, np.ndarray], np.ndarray],
    frequency: float,
    stack_length: float,
    # Domain radii [m]
    r_stator_in: float, r_stator_out: float,
    r_rotor_in: float,  r_rotor_out: float,
    # Steel Bertotti coefficients
    kh: float, kc: float, ke: float,
    n_r: int = 30, n_phi: int = 180,
) -> Dict[str, float]:
    """Evaluate Bertotti core loss separately for stator and rotor iron."""

    def _domain_loss(r_min: float, r_max: float) -> float:
        x, y = make_polar_grid(r_min, r_max, n_r=n_r, n_phi=n_phi)
        _, _, B = compute_flux_density(A_z_fn, x, y)
        # Cell area: annular sector
        dr   = (r_max - r_min) / n_r
        dphi = 2 * math.pi / n_phi
        r_vals = np.linspace(r_min, r_max, n_r)
        # Weighted area per cell (r × dr × dφ)
        R_mat, _ = np.meshgrid(r_vals, np.ones(n_phi))
        cell_area = R_mat * dr * dphi       # shape (n_phi, n_r)
        B2   = B ** 2
        B15  = B ** 1.5
        p    = kh * frequency * B2 + kc * frequency**2 * B2 + ke * frequency**1.5 * B15
        return float(np.sum(p * cell_area) * stack_length)

    P_stator = _domain_loss(r_stator_in, r_stator_out)
    P_rotor  = _domain_loss(r_rotor_in,  r_rotor_out)

    return {
        "P_fe_stator_W": round(P_stator, 2),
        "P_fe_rotor_W":  round(P_rotor,  2),
        "P_fe_total_W":  round(P_stator + P_rotor, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Magnet eddy-current losses  (simplified, requires B in magnet region)
# ─────────────────────────────────────────────────────────────────────────────

def compute_magnet_losses(
    A_z_fn: Callable[[np.ndarray, np.ndarray], np.ndarray],
    frequency: float,
    stack_length: float,
    r_magnet_in: float, r_magnet_out: float,
    sigma_magnet: float = 6.25e5,   # NdFeB conductivity [S/m]  (~F45SH)
    n_r: int = 10, n_phi: int = 90,
) -> Dict[str, float]:
    """Simplified eddy-current loss in magnets using Bertotti excess term only.

    P_eddy_magnet ≈ σ × (2πf)² × A_z² × V  (classical eddy approximation)
    """
    x, y = make_polar_grid(r_magnet_in, r_magnet_out, n_r=n_r, n_phi=n_phi)
    _, _, B = compute_flux_density(A_z_fn, x, y)

    dr   = (r_magnet_out - r_magnet_in) / n_r
    dphi = 2 * math.pi / n_phi
    r_vals = np.linspace(r_magnet_in, r_magnet_out, n_r)
    R_mat, _ = np.meshgrid(r_vals, np.ones(n_phi))
    cell_area = R_mat * dr * dphi

    omega = 2 * math.pi * frequency
    # Classical eddy loss density: σ × ω² × B² / (2 × (2π)²)  — simplified
    p_eddy = sigma_magnet * omega**2 * B**2 / (2 * (2 * math.pi)**2)

    P_mag = float(np.sum(p_eddy * cell_area) * stack_length)
    return {
        "P_mag_eddy_W": round(P_mag, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Efficiency summary
# ─────────────────────────────────────────────────────────────────────────────

def compute_efficiency(
    torque_Nm: float,
    rpm: float,
    P_cu_W: float,
    P_fe_W: float = 0.0,
    P_mag_W: float = 0.0,
    P_mech_extra_W: float = 0.0,  # bearing friction, windage
) -> Dict[str, float]:
    """Calculate efficiency from torque + all losses.

    η = P_mech / (P_mech + P_losses)
    """
    omega  = 2 * math.pi * rpm / 60          # [rad/s]
    P_mech = torque_Nm * omega               # [W]
    P_loss = P_cu_W + P_fe_W + P_mag_W + P_mech_extra_W
    P_in   = P_mech + P_loss

    efficiency = (P_mech / P_in * 100) if P_in > 0 else 0.0

    return {
        "P_mech_W":      round(P_mech,      2),
        "P_cu_W":        round(P_cu_W,       2),
        "P_fe_W":        round(P_fe_W,       2),
        "P_mag_W":       round(P_mag_W,      2),
        "P_loss_total_W": round(P_loss,      2),
        "P_input_W":     round(P_in,         2),
        "efficiency_pct": round(efficiency,   2),
        "torque_Nm":     round(torque_Nm,    4),
        "omega_rad_s":   round(omega,        3),
        "rpm":           rpm,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Convenience: build evaluation grid
# ─────────────────────────────────────────────────────────────────────────────

def make_polar_grid(
    r_min: float,
    r_max: float,
    n_r: int = 50,
    n_phi: int = 360,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (x_grid, y_grid) on a polar mesh covering r_min..r_max."""
    r   = np.linspace(r_min, r_max, n_r)
    phi = np.linspace(0, 2 * math.pi, n_phi, endpoint=False)
    R, PHI = np.meshgrid(r, phi)
    return R * np.cos(PHI), R * np.sin(PHI)
