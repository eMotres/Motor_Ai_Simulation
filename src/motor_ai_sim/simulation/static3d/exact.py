"""Closed-form magnetostatic solutions the 3D solver is measured against.

Nothing in this file may ever be tuned to make the solver look good.  These are
textbook results, derived from the equivalent-source picture:

  * a uniformly magnetised body in free space is equivalent to a bound surface
    current K = M x n (equivalently a bound surface charge sigma = M.n for the
    scalar potential),
  * the resulting B is what an ideal current sheet of that strength produces.

Both bodies below are magnetised with the RECOIL permeability equal to mu0
(mu_r = 1 in the magnet), which is the condition under which these formulas are
exact.  A real NdFeB magnet has mu_rec ~ 1.05, which changes the answer by a few
percent — so the benchmarks set mu_r = 1 in the magnet, and any deviation the
solver reports is solver error, not modelling error.

Units: SI (meters, A/m, tesla).
"""
from __future__ import annotations

import math
from typing import Tuple

import numpy as np

MU0 = 4e-7 * math.pi


# --------------------------------------------------------------------------
# uniformly magnetised sphere
# --------------------------------------------------------------------------

def sphere_B_inside(M: float) -> float:
    """|B| inside a uniformly magnetised sphere: exactly (2/3) mu0 M, uniform.

    Derivation: the demagnetising factor of a sphere is 1/3, so H_in = -M/3 and
    B_in = mu0 (H_in + M) = (2/3) mu0 M, pointing along M."""
    return (2.0 / 3.0) * MU0 * M


def sphere_H_inside(M: float) -> float:
    """H inside (signed, along M): -M/3."""
    return -M / 3.0


def sphere_B(points: np.ndarray, M: float, radius: float,
             axis: Tuple[float, float, float] = (0.0, 0.0, 1.0)) -> np.ndarray:
    """Exact B at arbitrary points for a sphere of ``radius`` magnetised
    uniformly with magnitude ``M`` along ``axis``.

    Inside: the uniform (2/3) mu0 M field.
    Outside: a pure point dipole of moment m = M * (4/3) pi a^3, i.e.
        B = (mu0 / 4 pi) [ 3 r (m.r) / |r|^5  -  m / |r|^3 ].
    (A uniformly magnetised sphere's exterior field is EXACTLY a dipole — no
    higher multipoles — which is what makes it such a good benchmark.)

    ``points`` is (3, N); returns (3, N).
    """
    p = np.asarray(points, dtype=float)
    n = np.asarray(axis, dtype=float)
    n = n / np.linalg.norm(n)
    r = np.linalg.norm(p, axis=0)
    m = M * (4.0 / 3.0) * math.pi * radius ** 3 * n          # A m^2

    B = np.zeros_like(p)
    inside = r <= radius
    B[:, inside] = ((2.0 / 3.0) * MU0 * M * n)[:, None]

    out = ~inside
    if out.any():
        po = p[:, out]
        ro = r[out]
        mdotr = m[0] * po[0] + m[1] * po[1] + m[2] * po[2]
        B[:, out] = (MU0 / (4.0 * math.pi)) * (
            3.0 * po * mdotr / ro ** 5 - m[:, None] / ro ** 3)
    return B


# --------------------------------------------------------------------------
# uniformly axially magnetised finite cylinder
# --------------------------------------------------------------------------

def cylinder_axis_Bz(z: np.ndarray, M: float, radius: float, length: float
                     ) -> np.ndarray:
    """Exact on-axis B_z of a finite cylinder magnetised M*z_hat, centred at
    z = 0, radius R, axial length L.

        B_z(z) = (mu0 M / 2) [ (z + L/2) / sqrt((z + L/2)^2 + R^2)
                             - (z - L/2) / sqrt((z - L/2)^2 + R^2) ]

    This is the field of the equivalent solenoid (bound surface current
    K = M on the curved wall).  It is valid for ALL z on the axis — inside the
    magnet, at the end face and out in the air — and it is continuous through
    the end face, which is exactly why it makes such a sharp test of a 3D
    solver's end handling.
    """
    z = np.asarray(z, dtype=float)
    a = z + 0.5 * length
    b = z - 0.5 * length
    return 0.5 * MU0 * M * (a / np.sqrt(a * a + radius * radius)
                            - b / np.sqrt(b * b + radius * radius))


def cylinder_end_to_mid_ratio(radius: float, length: float) -> float:
    """B_z(end face) / B_z(mid-plane) on the axis — the analytic 'spill' number.

        ratio = sqrt(L^2/4 + R^2) / sqrt(L^2 + R^2)

    It tends to 1/2 for a long thin magnet (half the flux has already leaked out
    by the time you reach the end face) and stays near 1/2 for any aspect ratio
    a motor magnet actually has.  This single number is the whole reason a 3D
    model is needed: a 2D solve reports the mid-plane value everywhere."""
    L, R = float(length), float(radius)
    return math.sqrt(0.25 * L * L + R * R) / math.sqrt(L * L + R * R)


def cylinder_axis_Bz_mid(M: float, radius: float, length: float) -> float:
    """Convenience: on-axis B_z at the mid-plane."""
    return float(cylinder_axis_Bz(np.array([0.0]), M, radius, length)[0])


def cylinder_axis_Bz_end(M: float, radius: float, length: float) -> float:
    """Convenience: on-axis B_z at the end face z = +L/2."""
    return float(cylinder_axis_Bz(np.array([0.5 * length]), M, radius, length)[0])
