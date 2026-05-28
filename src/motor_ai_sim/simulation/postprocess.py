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
# 4.  Convenience: build evaluation grid
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
