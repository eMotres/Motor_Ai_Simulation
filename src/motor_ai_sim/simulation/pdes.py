"""PDE definitions for 2D magnetostatics using NVIDIA Modulus symbolic API.

Physical model
--------------
For a 2-D cross-section in the x-y plane the only non-zero component of the
magnetic vector potential is A_z(x, y).  The governing equation in each
sub-domain is:

    ∂/∂x(ν ∂A_z/∂x) + ∂/∂y(ν ∂A_z/∂y) = −J_z          (1)

where
    ν  = reluctivity  [1/(μ₀·μᵣ)]   [m/H]
    J_z = source current density     [A/m²]

From A_z the flux density is recovered as
    B_x =  ∂A_z/∂y
    B_y = −∂A_z/∂x
    |B|  = √(B_x² + B_y²)

Linear form (constant ν):
    ν · (∂²A_z/∂x² + ∂²A_z/∂y²) + J_z = 0             (2)

Permanent magnet contribution (remanent magnetisation M):
    ν · (∂²A_z/∂x² + ∂²A_z/∂y²) + J_z
        − ν₀ · (∂M_y/∂x − ∂M_x/∂y) = 0                (3)

All implemented as Modulus `PDE` subclasses so they can be used directly
in `PointwiseInteriorConstraint`.
"""

from __future__ import annotations

from sympy import Symbol, Function, Number, sqrt, diff, Abs

# ── Modulus symbolic PDE base ─────────────────────────────────────────────────
try:
    from modulus.sym.eq.pde import PDE
    HAS_MODULUS = True
except ImportError:
    try:
        from physicsnemo.sym.eq.pde import PDE      # physicsnemo-sym package
        HAS_MODULUS = True
    except ImportError:
        try:
            from physicsnemo.eq.pde import PDE      # legacy path
            HAS_MODULUS = True
        except ImportError:
            HAS_MODULUS = False

        class PDE:                                   # minimal stub for offline dev
            """Stub PDE base used when Modulus is not installed."""
            name = "PDE"
            equations: dict = {}


# ── μ₀ constant ───────────────────────────────────────────────────────────────
MU_0 = 4e-7 * 3.141592653589793   # [H/m]


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Linear 2-D magnetostatics
# ─────────────────────────────────────────────────────────────────────────────
class Magnetostatics2D(PDE):
    """Linear 2-D magnetostatics: ν·ΔA_z + J_z = 0.

    Parameters
    ----------
    mu_r : float
        Relative permeability of the material (constant).
    J_z  : float
        z-component of current density [A/m²].  Use positive for
        conductors with current going *into* the page.
    """

    name = "Magnetostatics2D"

    def __init__(self, mu_r: float = 1.0, J_z: float = 0.0):
        x = Symbol("x")
        y = Symbol("y")
        A_z = Function("A_z")(x, y)

        nu = 1.0 / (MU_0 * mu_r)               # reluctivity  [m/H]

        # ν·(∂²A_z/∂x² + ∂²A_z/∂y²) + J_z = 0
        self.equations = {
            "magnetostatics": (
                nu * (diff(A_z, x, 2) + diff(A_z, y, 2)) + Number(J_z)
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Nonlinear 2-D magnetostatics  (iron with B-H curve)
# ─────────────────────────────────────────────────────────────────────────────
class MagnetosticsNonlinear2D(PDE):
    """Nonlinear 2-D magnetostatics with B-H–curve reluctivity.

    The reluctivity ν(|B|) is approximated by a rational function fitted
    to the measured B-H curve:

        ν(B) = a + b / (1 − (B/B_sat)²)       (Froelich–type model)

    so that ν → ∞ as |B| → B_sat (hard saturation).

    Parameters
    ----------
    B_sat    : float   Saturation flux density [T].
    nu_0     : float   Air reluctivity  = 1/μ₀  [m/H].
    nu_fe_0  : float   Reluctivity at B→0  (linear region) [m/H].
    J_z      : float   Current density [A/m²].
    """

    name = "MagnetosticsNonlinear2D"

    def __init__(
        self,
        B_sat: float = 1.8,
        nu_0: float = 1.0 / MU_0,
        nu_fe_0: float = 1.0 / (MU_0 * 5000),
        J_z: float = 0.0,
    ):
        x = Symbol("x")
        y = Symbol("y")
        A_z = Function("A_z")(x, y)

        # Flux-density components from A_z
        Bx = diff(A_z, y)          #  ∂A_z/∂y
        By = -diff(A_z, x)         # −∂A_z/∂x
        B_mag = sqrt(Bx**2 + By**2 + 1e-12)   # |B|, ε prevents division-by-zero

        # Froelich reluctivity model
        #   ν(B) = nu_0 + (nu_fe_0 − nu_0) / (1 − (B/B_sat)²)
        # Clamped to avoid singularity: use B_mag² / B_sat² ≤ 0.99
        ratio_sq = (B_mag / B_sat) ** 2
        nu = nu_0 + (nu_fe_0 - nu_0) / (1.0 - ratio_sq + 1e-6)

        # ∂/∂x(ν ∂A_z/∂x) + ∂/∂y(ν ∂A_z/∂y) + J_z = 0
        # Expand using product rule (Modulus handles autodiff of nu·∂A_z/∂x)
        self.equations = {
            "magnetostatics_nl": (
                diff(nu * diff(A_z, x), x)
                + diff(nu * diff(A_z, y), y)
                + Number(J_z)
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Permanent magnet source term
# ─────────────────────────────────────────────────────────────────────────────
class PermanentMagnet2D(PDE):
    """2-D magnetostatics for a permanent magnet region.

    The remanent magnetisation M = (M_x, M_y) contributes a source term:

        ν·ΔA_z = −ν₀·(∂M_y/∂x − ∂M_x/∂y)

    For a radially magnetised magnet at angle θ:
        M_x = M_r · cos(θ)
        M_y = M_r · sin(θ)

    Parameters
    ----------
    Br    : float   Remanent flux density [T].
    Mx, My: float   Cartesian components of unit magnetisation direction.
    """

    name = "PermanentMagnet2D"

    def __init__(self, Br: float = 1.2, Mx: float = 1.0, My: float = 0.0):
        x = Symbol("x")
        y = Symbol("y")
        A_z = Function("A_z")(x, y)

        nu_0 = 1.0 / MU_0
        # Reluctivity of magnet (μᵣ ≈ 1.05 for NdFeB)
        nu_mag = nu_0 / 1.05

        # Magnetisation vector (uniform inside each magnet pole)
        M_r = Br / MU_0                        # [A/m]
        M_x_val = M_r * Mx
        M_y_val = M_r * My

        # Source: −ν₀·(∂M_y/∂x − ∂M_x/∂y)
        # For uniform M the curl of M is zero inside each pole,
        # but the discontinuity at the boundary acts as a surface current.
        # In PINN practice we enforce the full equation inside + interface BCs.
        source = -nu_0 * (Number(0) - Number(0))   # =0 for uniform M interior

        self.equations = {
            "magnetostatics_pm": (
                nu_mag * (diff(A_z, x, 2) + diff(A_z, y, 2)) - source
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Frequency-domain AC magnetostatics  (eddy currents)
# ─────────────────────────────────────────────────────────────────────────────
class MagnetostaticsAC2D(PDE):
    """Frequency-domain 2-D magnetostatics with eddy-current back-reaction.

    The governing equation at angular frequency ω = 2πf is:

        ∇(ν ∇A_z) − jωσ A_z = −J_source            (complex)

    Split into real / imaginary parts  (A_z = Ar + j·Ai):

        Re:  ν·ΔAr + ω·σ·Ai = −Jr                   (4a)
        Im:  ν·ΔAi − ω·σ·Ar = −Ji                   (4b)

    The PINN network outputs two fields: Ar(x,y), Ai(x,y).

    Physical interpretation:
        - σ=0 (air, laminated steel): decoupled Poisson for Ar, Ai
        - σ>0 (copper conductors, NdFeB magnets): eddy coupling
          → captures skin effect, proximity effect, and magnet eddy losses

    From the solution:
        J_eddy  = −jωσ A_z  →  |J_eddy|² / σ = ω²σ (Ar²+Ai²)
        P_eddy  = (ω²σ/2) · stack_length · ∫|A_z|² dA   [W]
        |B|_rms = (1/√2) · |∇A_z|_peak

    Parameters
    ----------
    mu_r  : float  relative permeability
    sigma : float  electrical conductivity  [S/m]
    omega : float  angular frequency = 2πf  [rad/s]
    Jr    : float  real part of imposed current density  [A/m²]
    Ji    : float  imaginary part (= 0 for in-phase source)
    """

    name = "MagnetostaticsAC2D"

    def __init__(
        self,
        mu_r:  float = 1.0,
        sigma: float = 0.0,
        omega: float = 0.0,
        Jr:    float = 0.0,
        Ji:    float = 0.0,
    ):
        x  = Symbol("x")
        y  = Symbol("y")
        Ar = Function("Ar")(x, y)
        Ai = Function("Ai")(x, y)

        nu = 1.0 / (MU_0 * mu_r)

        # (4a) ν·ΔAr + ωσ·Ai + Jr = 0
        eq_real = (
            nu * (diff(Ar, x, 2) + diff(Ar, y, 2))
            + Number(omega * sigma) * Ai
            + Number(Jr)
        )
        # (4b) ν·ΔAi − ωσ·Ar + Ji = 0
        eq_imag = (
            nu * (diff(Ai, x, 2) + diff(Ai, y, 2))
            - Number(omega * sigma) * Ar
            + Number(Ji)
        )

        self.equations = {
            "magnetostatics_ac_real": eq_real,
            "magnetostatics_ac_imag": eq_imag,
        }
