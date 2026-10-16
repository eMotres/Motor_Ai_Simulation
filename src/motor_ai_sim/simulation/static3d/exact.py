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

That convenience hid a bug for a whole stage.  mu_r = 1 is exactly the value at
which the two candidate magnet conventions,

    B = mu0*mu_r*H + mu0*M        (remanence mu0*M — the physical one)
    B = mu0*mu_r*(H + M)          (remanence mu_r*mu0*M — off by mu_rec)

give the SAME answer, so a whole benchmark suite at mu_r = 1 cannot tell them
apart.  ``demag_body_B_inside`` below is the closed form at ARBITRARY mu_r and
demagnetising factor, and it separates them by exactly mu_rec.  Use it, not the
mu_r = 1 shortcuts, whenever a new solver or a new reference leg is wired in.

Units: SI (meters, A/m, tesla).
"""
from __future__ import annotations

import math
from typing import Tuple

import numpy as np

MU0 = 4e-7 * math.pi


# --------------------------------------------------------------------------
# the constitutive law itself, at arbitrary recoil permeability
# --------------------------------------------------------------------------

#: demagnetising factor along the magnetisation, for the two bodies that have
#: one in closed form.  ``cylinder_transverse`` is the 2D case: an infinite
#: circular cylinder magnetised ACROSS its axis.
DEMAG_FACTOR = {"sphere": 1.0 / 3.0, "cylinder_transverse": 0.5}


def demag_body_B_inside(M: float, mu_r: float, N: float) -> float:
    """|B| inside a uniformly magnetised ellipsoid-equivalent body, exactly.

    The body obeys  B = mu0*mu_r*H + mu0*M  (permanent magnetisation M [A/m],
    recoil permeability mu_r), and has demagnetising factor ``N`` along M.
    Writing the total magnetisation M_tot = B/mu0 - H = (mu_r - 1)*H + M and
    imposing the demagnetising relation H = -N*M_tot,

        H    = -N*M / (1 + N*(mu_r - 1))
        B_in = mu0*(mu_r*H + M) = mu0 * M * (1 - N) / (1 + N*(mu_r - 1))

    Checks: N = 1/3, mu_r = 1 gives (2/3) mu0 M (the sphere); N = 1/2, mu_r = 1
    gives (1/2) mu0 M (the transverse cylinder).  At mu_r = 1.05 the sphere is
    2*mu0*M/(mu_r+2) and the cylinder mu0*M/(mu_r+1) — both LOWER than the
    mu_r = 1 value, and a solver that instead scales UP by mu_r has the magnet
    convention the wrong way round.
    """
    N = float(N)
    return MU0 * float(M) * (1.0 - N) / (1.0 + N * (float(mu_r) - 1.0))


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


def thick_solenoid_axis_Bz(z: np.ndarray, J: float, r_inner: float,
                           r_outer: float, length: float) -> np.ndarray:
    """Exact on-axis B_z of a finite THICK solenoid: an annulus r_inner..r_outer,
    axial length ``length``, centred at z = 0, carrying a uniform azimuthal
    current density ``J`` [A/m^2].

        B_z(z) = (mu0 J / 2) [ x1 ln( (r_o + sqrt(r_o^2+x1^2))
                                    / (r_i + sqrt(r_i^2+x1^2)) )
                             - x2 ln( (r_o + sqrt(r_o^2+x2^2))
                                    / (r_i + sqrt(r_i^2+x2^2)) ) ]

    with x1 = z + L/2, x2 = z - L/2.  It is the radial integral of the thin
    solenoid formula, so in the thin limit (r_outer -> r_inner = R, J*t -> K) it
    collapses onto ``cylinder_axis_Bz`` with M = K:

        ln((r_o + sqrt(r_o^2+x^2))/(r_i + sqrt(r_i^2+x^2)))  ->  t / sqrt(R^2+x^2)

    That collapse is the whole point of this function.  A magnet of
    magnetisation M is EQUIVALENT to a surface current K = M x n; a solver that
    gets the magnet right and the current wrong (or vice versa) has a source
    bug, not a physics disagreement, and the two formulas above are what
    separate the two.  This one is the reference for the source ACTUALLY
    discretised (a shell of finite thickness); ``cylinder_axis_Bz`` is the ideal
    sheet the shell is standing in for.
    """
    z = np.asarray(z, dtype=float)
    x1 = z + 0.5 * length
    x2 = z - 0.5 * length

    def _term(x):
        return np.log((r_outer + np.sqrt(r_outer ** 2 + x ** 2))
                      / (r_inner + np.sqrt(r_inner ** 2 + x ** 2)))

    return 0.5 * MU0 * J * (x1 * _term(x1) - x2 * _term(x2))


def cylinder_end_to_mid_ratio(radius: float, length: float) -> float:
    """B_z(end face) / B_z(mid-plane) on the axis — the analytic 'spill' number.

        ratio = sqrt(L^2/4 + R^2) / sqrt(L^2 + R^2)

    It tends to 1/2 for a long thin magnet (half the flux has already leaked out
    by the time you reach the end face) and stays near 1/2 for any aspect ratio
    a motor magnet actually has.  This single number is the whole reason a 3D
    model is needed: a 2D solve reports the mid-plane value everywhere."""
    L, R = float(length), float(radius)
    return math.sqrt(0.25 * L * L + R * R) / math.sqrt(L * L + R * R)


# --------------------------------------------------------------------------
# magnetised sphere inside a concentric IRON SHELL — the iron ladder's exact
# reference, at ARBITRARY mu_r
# --------------------------------------------------------------------------

def sphere_in_shell_coeffs(M: float, a: float, b: float, c: float,
                           mu_r: float, mu_m: float = 1.0) -> dict:
    """Legendre coefficients for a uniformly magnetised sphere inside a
    concentric spherical iron shell, in free space.

    Geometry (all radii in metres, concentric, a < b < c):

        r < a        magnet, magnetisation M*z_hat, permeability mu0*mu_m
        a < r < b    air
        b < r < c    iron, permeability mu0*mu_r
        r > c        air

    Everything obeys ``B = mu0*mu_r*H + mu0*M`` with H = -grad(phi) and, because
    M is uniform, div B = 0 reduces to Laplace's equation in EVERY region: the
    magnet enters only through the jump conditions at r = a.  A uniform M along z
    excites the l = 1 Legendre harmonic and nothing else, so the solution is
    exactly

        phi_1 = A1 r cos(t)                      r < a
        phi_2 = (A2 r + B2/r^2) cos(t)           a < r < b
        phi_3 = (A3 r + B3/r^2) cos(t)           b < r < c
        phi_4 = (B4/r^2) cos(t)                  r > c

    and the six unknowns follow from continuity of phi (tangential H) and of
    B_r at r = a, b, c — a 6x6 linear system, solved here in closed form by
    ``numpy.linalg.solve``.  Solving a 6x6 system is not a discretisation: this
    IS the exact solution, evaluated.

    Why this is the iron benchmark.  The known weakness of the TOTAL scalar
    potential is cancellation inside high-mu iron, where B = -mu grad(phi) is a
    huge permeability times a tiny gradient; the residual it leaves grows with
    mu_r.  A benchmark whose reference is another mesh cannot separate that from
    ordinary discretisation error.  This one has an exact answer at EVERY mu_r,
    so the ladder mu_r = 10 / 200 / 1000 / 4625 is measured against the truth
    rather than against a finer version of the same formulation.

    Sanity: at mu_r = 1 the shell disappears and B_in falls back on
    ``demag_body_B_inside(M, mu_m, 1/3)`` exactly.
    """
    a, b, c = float(a), float(b), float(c)
    mu_r, mu_m = float(mu_r), float(mu_m)
    if not (0.0 < a < b < c):
        raise ValueError(f"need 0 < a < b < c, got {a}, {b}, {c}")
    K = np.zeros((6, 6))
    rhs = np.zeros(6)
    # r = a : phi continuous
    K[0] = [a, -a, -1.0 / a ** 2, 0.0, 0.0, 0.0]
    # r = a : B_r continuous ->  -mu_m A1 + M = -(A2 - 2 B2/a^3)
    K[1] = [-mu_m, 1.0, -2.0 / a ** 3, 0.0, 0.0, 0.0]
    rhs[1] = -float(M)
    # r = b : phi continuous
    K[2] = [0.0, b, 1.0 / b ** 2, -b, -1.0 / b ** 2, 0.0]
    # r = b : B_r continuous
    K[3] = [0.0, -1.0, 2.0 / b ** 3, mu_r, -2.0 * mu_r / b ** 3, 0.0]
    # r = c : phi continuous
    K[4] = [0.0, 0.0, 0.0, c, 1.0 / c ** 2, -1.0 / c ** 2]
    # r = c : B_r continuous
    K[5] = [0.0, 0.0, 0.0, -mu_r, 2.0 * mu_r / c ** 3, -2.0 / c ** 3]
    A1, A2, B2, A3, B3, B4 = np.linalg.solve(K, rhs)
    return dict(A1=A1, A2=A2, B2=B2, A3=A3, B3=B3, B4=B4,
                a=a, b=b, c=c, M=float(M), mu_r=mu_r, mu_m=mu_m)


def sphere_in_shell_B_inside(M: float, a: float, b: float, c: float,
                             mu_r: float, mu_m: float = 1.0) -> float:
    """|B| inside the magnet (uniform, along M): mu0*(mu_m*H + M), H = -A1."""
    co = sphere_in_shell_coeffs(M, a, b, c, mu_r, mu_m)
    return MU0 * (-co["mu_m"] * co["A1"] + co["M"])


def sphere_in_shell_B(points: np.ndarray, M: float, a: float, b: float,
                      c: float, mu_r: float, mu_m: float = 1.0) -> np.ndarray:
    """Exact B at arbitrary points (3, N) -> (3, N), all four regions.

    In every region  B = -mu grad(phi) + mu0 M ,  with

        grad( (A r + B/r^2) cos(t) )
            = (A - 2B/r^3) cos(t) r_hat  -  (A + B/r^3) sin(t) t_hat

    written back in cartesian components.  Points exactly on an interface are
    assigned to the INNER region; B_r is continuous there but B_t is not, so a
    benchmark should not probe an interface tangentially.
    """
    co = sphere_in_shell_coeffs(M, a, b, c, mu_r, mu_m)
    p = np.asarray(points, dtype=float)
    r = np.linalg.norm(p, axis=0)
    r_safe = np.where(r > 0, r, 1.0)
    ct = p[2] / r_safe                                   # cos(theta)
    # rho_hat in the xy-plane (theta_hat = ct*rho_hat - st*z_hat)
    rho = np.hypot(p[0], p[1])
    rho_safe = np.where(rho > 0, rho, 1.0)
    st = rho / r_safe
    rhat = p / r_safe[None]
    that = np.zeros_like(p)
    that[0] = ct * p[0] / rho_safe
    that[1] = ct * p[1] / rho_safe
    that[2] = -st

    A = np.zeros(r.shape)
    Bc = np.zeros(r.shape)
    mu = np.full(r.shape, MU0)
    Mz = np.zeros(r.shape)
    inner = r <= a
    A[inner], Bc[inner] = co["A1"], 0.0
    mu[inner] = MU0 * co["mu_m"]
    Mz[inner] = co["M"]
    mid = (r > a) & (r <= b)
    A[mid], Bc[mid] = co["A2"], co["B2"]
    iron = (r > b) & (r <= c)
    A[iron], Bc[iron] = co["A3"], co["B3"]
    mu[iron] = MU0 * co["mu_r"]
    out = r > c
    A[out], Bc[out] = 0.0, co["B4"]

    dphi_dr = (A - 2.0 * Bc / r_safe ** 3) * ct
    dphi_dt = -(A + Bc / r_safe ** 3) * st               # (1/r) dphi/dtheta
    grad = dphi_dr[None] * rhat + dphi_dt[None] * that
    # r = 0 is a coordinate singularity of the spherical form, not of the field:
    # phi_1 = A1 r cos(t) IS A1*z, so grad(phi) = A1 z_hat there (and the limit
    # of the expression above is the same, which is why only the exact origin
    # needs saying).
    org = r <= 1e-14 * max(c, 1.0)
    if org.any():
        grad[:, org] = 0.0
        grad[2, org] = co["A1"]
    B = -mu[None] * grad
    B[2] += MU0 * Mz
    return B


def cylinder_axis_Bz_mid(M: float, radius: float, length: float) -> float:
    """Convenience: on-axis B_z at the mid-plane."""
    return float(cylinder_axis_Bz(np.array([0.0]), M, radius, length)[0])


def cylinder_axis_Bz_end(M: float, radius: float, length: float) -> float:
    """Convenience: on-axis B_z at the end face z = +L/2."""
    return float(cylinder_axis_Bz(np.array([0.5 * length]), M, radius, length)[0])
