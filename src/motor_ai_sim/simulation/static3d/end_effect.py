"""Stage A: the axial-spill answer for the real machine, and the passport it writes.

What is actually computed
-------------------------
One magnet-only (I = 0) nonlinear P2 solve of HALF the stack plus an end air cap,
on one anti-periodic symmetry sector, then:

  a. ``B1(z)``  — the amplitude of the pole-pair-order harmonic of B_r on the
     mid-gap cylinder, at a ladder of axial stations.  Normalised by its
     mid-plane value that is the SPILL CURVE.
  b. ``k_flux`` — the axial mean of B1 over the stack, divided by the 2D value.
     This is the number an EMF or flux-linkage computed in 2D must be multiplied
     by.  It is reported twice: against the 3D mid-plane (pure end effect) and
     against the 2D solver's own answer (end effect + whatever the two models
     disagree about).  Quoting only the first would hide a modelling error
     inside a "3D correction".
  c. demagnetisation exposure — ``H . M_hat`` per magnet element, worst and
     mean, in the END slice against the MID slice.  At I = 0 nothing crosses the
     knee; what the number says is how much of the margin the ends have already
     eaten before any stator current is added.
  d. ``k_flux(L_stack)`` — the same solve at several stack lengths, i.e. the
     curve that says at what length 2D stops lying.
  e. dofs and wall time for every solve, plus the Dirichlet/Neumann truncation
     bracket at the chosen air-box size.

Sign conventions worth stating once
-----------------------------------
* z = 0 is the axial MID-plane and is a mirror plane.  The magnetisation is
  purely in-plane (spoke PM), so B_z(-z) = -B_z(z) and B_z = 0 there: the
  natural boundary condition of this weak form is already the right physics, and
  nothing is imposed on it.  Only r = r_box and z = z_box are truncation.
* B1 is taken from the HALF sector directly.  With an odd number of pole pairs
  the anti-periodic half carries the full-ring harmonic exactly (the missing
  half of the DFT is the algebraic twin of the half that is there), so no
  reconstruction of the other 180 deg is needed — see ``ref2d.gap_fundamental``.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

_trapz = getattr(np, "trapezoid", None) or np.trapz

from .motor_geometry import MM, MotorSection, load_motor_section
from .motor_mesh import Section2D, build_motor_mesh, build_section_mesh_2d
from .periodic import Periodic, dof_pairs, outer_boundary_dofs
from .ref2d import gap_fundamental
from .solver import MU0, Region, Solution, solve_static3d, solve_static3d_nonlinear

# -2 (2026-11-24): the l_stack_curve rows divide by their OWN mid-plane / 2D
# ratio and the passport says so in ``k_flux_vs_L_method``.  A -1 passport's
# per-length rows used the reference length's ratio (only its reference row and
# scalar k_flux are right); a table without the marker is such a table.
# -3 (2026-11-25): two_d.verdict / k_flux_usable come from the end-deficit
# agreement check (two_d.agreement_check, end_deficit_check); the -1/-2 verdict
# was the mid-plane ratio within 2 % at the reference stack (two_d
# "agreement_tolerance" 0.02).  A -3 passport has no "agreement_tolerance" key:
# the eps tolerance lives in agreement_check.tolerance_eps only.
PASSPORT_VERSION = "static3d-stageA-3"
#: ``k_flux_vs_L_method`` of a sweep with a 2D leg: k_flux(L) = k_flux_self(L) * B1_mid(L) / B1_2D.
K_FLUX_VS_L_OWN_MID = "own_mid_ratio"
#: ... and without one: k_flux(L) = k_flux_self(L) (no 2D ratio measured).
K_FLUX_VS_L_SELF = "self_no_2d"

#: The 3D-vs-2D agreement check (2026-11-24).  The end deficit D(L) = L (1 - k_flux(L)) of a real end effect is a
#: length per machine, nearly constant in L (measured: a 28-pole 80 mm machine 0.93-0.96 mm from 12 to 48 mm; a 12s14p
#: 40 mm machine 0.707-0.709 mm from 12 to 22 mm; a 48s56p 400 mm machine 2.41-2.76 mm from 90 to 180 mm, the same at
#: two axial meshes); a 3D-vs-2D model offset eps (dimensionless) adds eps x L.  So the sweep rows at the reference
#: stack and longer, and outside the end-interaction zone, are fitted to D = D0 + eps L and the check passes when
#: |eps| <= AGREEMENT_EPS_TOL and D0 > 0.  D0 > 0 assumes no magnet / rotor overhang (an overhang can make k > 1 and
#: D0 < 0 by physics).  The mid-plane ratio alone (the earlier 2 % test) is NOT a model check: at a short stack the
#: mid-plane itself sits below 2D (0.957 at 12 mm on the 80 mm machine, 0.9996 at 48 mm) and that is end physics.
AGREEMENT_EPS_TOL = 0.01
#: the earlier mid-plane tolerance, kept as a diagnostic beside the check
MID_PLANE_TOL = 0.02
#: lengths closer than this ratio cannot separate D0 from eps (Sxx -> 0)
MIN_SPAN_RATIO = 1.25
#: 3+ rows: an rms residual above this share of the mean D means D is not linear in L -> not checked
MAX_REL_RESIDUAL = 0.03
#: rows shorter than this many pole pitches sit in the end-interaction zone (the two ends see each other; D falls:
#: 0.90 -> 0.85 mm from 8 to 6 mm on the 80 mm machine, pole pitch 5.6 mm) and stay out of the fit
END_ZONE_POLE_PITCHES = 2.0
#: D0 above this share of a pole pitch is flagged (an end loss longer than half a pole pitch is unusual)
D0_FLAG_POLE_PITCHES = 0.5
#: Coarse screen for a run the fit cannot check (one-sided, 2026-11-25).  End physics only LOWERS the mid-plane / 2D
#: ratio (short stack: 0.891 at 6 mm, 0.923 at 8 mm, 0.957 at 12 mm on the 80 mm machine), so a ratio above
#: 1 + COARSE_SCREEN_HIGH is a model fault (an unconverged 2D Picard ~11 % low reads ~1.08); the largest correct value
#: measured is 1.0064 (400 mm machine at 180 mm, a 3D-above-2D offset of ~0.5 %).  Below, the allowed deficit grows
#: as the stack shortens: ratio < 1 - max(COARSE_SCREEN_LOW, COARSE_SCREEN_TAU * pole pitch / L) is a fault
#: (6 mm on the 80 mm machine, pole pitch 5.6 mm: bound 0.72, measured 0.891).
COARSE_SCREEN_HIGH = 0.02
COARSE_SCREEN_LOW = 0.15
COARSE_SCREEN_TAU = 0.3


#: the factors of a final / catalogue passport sweep when the reference stack is already outside the end zone
AUTO_L_FACTORS = (1.0, 1.5, 2.0)


def auto_l_factors(stack_mm: float, pole_pitch_mm: Optional[float]) -> Tuple[float, ...]:
    """Stack-length factors that give the agreement check 2-3 usable rows: (1.0, 1.5, 2.0) when the reference stack is
    at least ``END_ZONE_POLE_PITCHES`` pole pitches long; else (1.0, f0, 1.5 f0, 2 f0) with f0 = that length / the
    reference, so three rows clear the end zone with a span of 2 (the 1.0 row is the reference, outside the fit)."""
    L = float(stack_mm)
    tau = float(pole_pitch_mm) if pole_pitch_mm and pole_pitch_mm > 0 else 0.0
    zone = END_ZONE_POLE_PITCHES * tau
    if L <= 0 or zone <= L * (1.0 + 1e-9):
        return AUTO_L_FACTORS
    f0 = zone / L
    return (1.0, round(f0, 6), round(1.5 * f0, 6), round(2.0 * f0, 6))


def coarse_screen_bounds(stack_mm: float, pole_pitch_mm: Optional[float]) -> Tuple[float, float]:
    """``(lowest, highest)`` mid-plane / 2D ratio a run the fit cannot check may have (see COARSE_SCREEN_*)."""
    low = COARSE_SCREEN_LOW
    if pole_pitch_mm and pole_pitch_mm > 0 and stack_mm and stack_mm > 0:
        low = max(low, COARSE_SCREEN_TAU * float(pole_pitch_mm) / float(stack_mm))
    return 1.0 - low, 1.0 + COARSE_SCREEN_HIGH


def end_deficit_check(curve: Sequence[dict], ref_stack_mm: float,
                      tol: float = AGREEMENT_EPS_TOL,
                      pole_pitch_mm: Optional[float] = None) -> dict:
    """Fit D(L) = L (1 - k_flux(L)) = D0 + eps L over the rows at ``ref_stack_mm`` and longer (and at
    ``END_ZONE_POLE_PITCHES`` pole pitches and longer when ``pole_pitch_mm`` is given) that carry their own finite,
    positive 3D-mid / 2D ratio.  ``verdict``: PASS (|eps| <= tol and D0 > 0), FAIL, or NOT CHECKED with a ``reason``
    (fewer than two such lengths, lengths too close, or D not linear in L) - not a failure.  Every dropped row is
    recorded with its reason.  Two rows are an EXACT fit (no residual): a per-row k error dk gives an eps error of
    dk (L1 + L2) / (L2 - L1) - five times dk for the default 1.0 / 1.5 rows of the catalog sweep."""
    l_min = float(ref_stack_mm)
    if pole_pitch_mm and pole_pitch_mm > 0:
        l_min = max(l_min, END_ZONE_POLE_PITCHES * float(pole_pitch_mm))
    pts: Dict[float, float] = {}
    dropped: List[dict] = []
    for r in curve:
        try:
            L, k = float(r["stack_mm"]), float(r["k_flux"])
        except (KeyError, TypeError, ValueError):
            _l = r.get("stack_mm") if isinstance(r, dict) else None
            try:
                _l = float(_l) if math.isfinite(float(_l)) else None
            except (TypeError, ValueError):
                _l = None
            dropped.append(dict(stack_mm=_l, reason="no stack length / k_flux"))
            continue
        try:
            rt = float(r.get("ratio_3d_mid_over_2d"))
        except (TypeError, ValueError):
            rt = float("nan")
        if not (math.isfinite(L) and L > 0 and math.isfinite(k)):
            why = "not finite"
        elif not (math.isfinite(rt) and rt > 0):
            why = "no own finite 3D-mid / 2D ratio"
        elif L < float(ref_stack_mm) * (1.0 - 1e-9):
            why = "shorter than the reference stack"
        elif L < l_min * (1.0 - 1e-9):
            why = "inside the end-interaction zone (< %g pole pitches = %.2f mm)" % (END_ZONE_POLE_PITCHES, l_min)
        else:
            pts[round(L, 9)] = L * (1.0 - k)
            continue
        dropped.append(dict(stack_mm=(L if math.isfinite(L) else None), reason=why))
    rows = sorted(pts.items())
    xs_l = [L for L, _ in rows]
    out = dict(rule="D(L) = L(1 - k_flux) = D0 + eps*L (eps dimensionless) over the rows at >= the reference stack "
                    "and >= %g pole pitches; PASS when |eps| <= %g %% and D0 > 0 (D0 > 0 assumes no overhang)"
                    % (END_ZONE_POLE_PITCHES, 100 * tol),
               tolerance_eps=tol, rows_mm=xs_l, D_mm=[d for _, d in rows], dropped_rows=dropped,
               n_rows=len(rows), l_min_fit_mm=l_min, pole_pitch_mm=pole_pitch_mm,
               span_ratio=(xs_l[-1] / xs_l[0] if len(rows) >= 2 else None),
               exact_fit=(len(rows) == 2), residual_rms_mm=None, eps=None, D0_mm=None)
    if len(rows) < 2:
        out["verdict"] = "NOT CHECKED"
        out["reason"] = "fewer than two usable stack lengths (at >= the reference stack, outside the end zone)"
        return out
    if out["span_ratio"] < MIN_SPAN_RATIO:
        out["verdict"] = "NOT CHECKED"
        out["reason"] = ("the usable lengths span only %.3f x (< %g): D0 and eps cannot be separated"
                         % (out["span_ratio"], MIN_SPAN_RATIO))
        return out
    xs = np.array(xs_l)
    ys = np.array([d for _, d in rows])
    xm, ym = float(xs.mean()), float(ys.mean())
    eps = float(((xs - xm) * (ys - ym)).sum() / ((xs - xm) ** 2).sum())
    d0 = ym - eps * xm
    out.update(eps=eps, D0_mm=d0)
    if len(rows) == 2:
        out["note"] = ("exact two-row fit: a per-row k error dk gives an eps error of dk (L1 + L2) / (L2 - L1) = "
                       "%.1f x dk here" % ((xs_l[0] + xs_l[1]) / (xs_l[1] - xs_l[0])))
    else:
        res = ys - (d0 + eps * xs)
        # n - 2 degrees of freedom: the fit spends two on D0 and eps (with 3 rows the residual has one)
        rms = float(np.sqrt(np.sum(res ** 2) / (len(rows) - 2)))
        out["residual_rms_mm"] = rms
        out["residual_dof"] = len(rows) - 2
        if ym > 0 and rms / ym > MAX_REL_RESIDUAL:
            out["verdict"] = "NOT CHECKED"
            out["reason"] = ("D is not linear in L: rms residual %.3f mm = %.1f %% of the mean D (> %g %%)"
                             % (rms, 100 * rms / ym, 100 * MAX_REL_RESIDUAL))
            return out
    if pole_pitch_mm and pole_pitch_mm > 0:
        out["D0_over_pole_pitch"] = d0 / float(pole_pitch_mm)
        if d0 > D0_FLAG_POLE_PITCHES * float(pole_pitch_mm):
            out["flag"] = "D0 is more than %g pole pitch: an unusually long end loss" % D0_FLAG_POLE_PITCHES
    out["verdict"] = "PASS" if (abs(eps) <= tol and d0 > 0.0) else "FAIL"
    return out


# --------------------------------------------------------------------------
# one solve
# --------------------------------------------------------------------------

@dataclass
class SectorSolve:
    sol: Solution
    tm: object                      # TaggedTetMesh
    section: MotorSection
    stack_mm: float
    basis: object
    periodic: Optional[Periodic]
    dirichlet: np.ndarray
    wall_s: float

    @property
    def stack_half_m(self) -> float:
        return 0.5 * self.stack_mm * MM



_KFLUX_FAILED_LABEL = "2-D check failed: k_flux is applied as stored (k_flux_self x mid-plane/2-D ratio); k_flux_self is the 3-D-only alternative"
#: a FAIL of the end-deficit check (passport static3d-stageA-3): the factor to apply is k_flux_self
#: in a run's own passport the scalar k_flux is the RAW value; the writers (adopt, full card) store k_flux_self and say
#: "applied" (routes/static3d.adopt_entry, end3d_verdict.applied_flux_factor)
_KFLUX_FAILED_NEW_LABEL = "2-D check failed: k_flux here is the raw value; the passport writers apply k_flux_self"
_KFLUX_INVALID_LABEL = "2-D check failed: the 2-D leg is invalid (no 2-D ratio exists), k_flux = k_flux_self"


def k_flux_basis_label(two_d):
    """Short honest label of how ``k_flux`` was formed, read from the 2-D agreement ``verdict`` (not from
    ``k_flux_usable``, which is also True for a check that was NOT CHECKED).  PASS -> "2-D check passed";
    NOT CHECKED -> the agreement was not checked, with the check's own reason when it has one; FAIL of the end-deficit
    check -> apply k_flux_self (what ``end3d_verdict.applied_flux_factor`` applies); FAIL of the old mid-plane rule ->
    the stored ``k_flux`` is applied as stored; INVALID -> no 2-D ratio exists; no verdict (older passports) -> the
    ``k_flux_usable`` flag as before; no 2-D block at all -> self-normalised."""
    if two_d is None:
        return "self-normalised (2-D check not run; k_flux = k_flux_self)"
    v = str(two_d.get("verdict") or "").strip().upper()
    chk = two_d.get("agreement_check") if isinstance(two_d.get("agreement_check"), dict) else None
    if v == "PASS":
        return "2-D check passed"
    if v == "NOT CHECKED":
        if chk is not None:
            return "2-D agreement not checked (%s): k_flux applied, unverified" % (
                chk.get("reason") or "fewer than two usable stack lengths")
        return "2-D agreement not checked (single length): k_flux = k_flux_self"
    if v == "INVALID":
        return _KFLUX_INVALID_LABEL
    if v == "FAIL":
        return _KFLUX_FAILED_NEW_LABEL if chk is not None else _KFLUX_FAILED_LABEL
    return "2-D check passed" if two_d.get("k_flux_usable") else _KFLUX_FAILED_LABEL


def _regions_for(tm, section: MotorSection, linear_iron: bool = False,
                 iron_mu_r: Optional[float] = None,
                 laminated_iron: bool = True) -> List[Region]:
    """3D regions from the machine's own materials.

    ``iron_mu_r`` replaces the permeability of the IRON regions only (never the
    magnets' recoil mu_r, never the shaft, never air) and implies
    ``linear_iron``.  It exists so that a mu_r LADDER is a one-variable
    experiment: the same mu_r goes into this model and into
    ``ref2d.solve_2d_reference_on_section_mesh(..., linear_iron_mu_r=...)``, and
    nothing else moves between the rungs.

    ``laminated_iron`` (default) gives the cores the transverse-isotropic
    permeability of a stack — in-plane as homogenised by the 2D materials,
    across the stack the series average the insulation dominates
    (``solver.axial_mu``).  Setting it False solves the same machine as a SOLID
    block of the homogenised iron, which is what every revision before this one
    did and what the 2D model is obliged to assume; it is kept so the difference
    is one flag and can be measured rather than argued about.
    """
    if iron_mu_r is not None:
        # A mu_r LADDER is a comparison against the 2D solver, and 2D has no z:
        # it cannot carry an across-stack permeability at all.  So a ladder rung
        # is solved with SOLID iron, or "the same mu_r in both models" would be
        # false at the rung it is measured at.
        linear_iron = True
        laminated_iron = False
    regs: List[Region] = []
    for r in section.regions:
        el = tm.elements(r.name)
        if el.size == 0:
            raise ValueError(f"region {r.name!r} has no elements in the 3D mesh")
        mu = r.mu_r
        if iron_mu_r is not None and r.kind == "iron":
            mu = float(iron_mu_r)
        regs.append(Region(name=r.name, elements=el, mu_r=mu, M=r.M,
                           bh_curve=None if linear_iron else r.bh_curve,
                           stack_kf=float(r.stack_kf) if laminated_iron else 1.0))
    air = tm.elements("air")
    if air.size == 0:
        raise ValueError("no air elements — the mesh has no field region")
    regs.append(Region(name="air", elements=air, mu_r=1.0))
    return regs


def compatible_mu_init(mu_init: Optional[np.ndarray], n_elements: int
                       ) -> Optional[np.ndarray]:
    """A warm-start permeability only if it belongs to a mesh of this size.

    The warm start is a first guess, never an input to the answer (the Picard
    converges on the constitutive residual), so a guess from a mesh with a
    different element count is DROPPED rather than crashing the solve — the
    2026-09-27 ``mu_init must be one value per element`` abort.  Element counts
    can legitimately differ between stack lengths (the axial box grows with a
    long stack; see ``motor_mesh.axial_box_mm``).
    """
    if mu_init is None:
        return None
    a = np.asarray(mu_init)
    if a.shape[-1] != int(n_elements):
        return None
    return a


def solve_sector(section: MotorSection,
                 sect2d: Section2D,
                 stack_mm: Optional[float] = None,
                 order: int = 2,
                 n_stack: int = 8,
                 n_cap: int = 10,
                 tol: float = 3e-3,
                 max_iter: int = 45,
                 damping: float = 0.35,
                 neumann_outer: bool = False,
                 linear_iron: bool = False,
                 iron_mu_r: Optional[float] = None,
                 linear_solver: Optional[str] = None,
                 mu_init: Optional[np.ndarray] = None,
                 laminated_iron: bool = True,
                 h_axial_end: Optional[float] = None,
                 h_axial_max: Optional[float] = None,
                 verbose: bool = False) -> SectorSolve:
    """Mesh + solve one stack length on a prebuilt cross-section."""
    from skfem import Basis, ElementTetP1, ElementTetP2

    t0 = time.perf_counter()
    tm, _ = build_motor_mesh(section, stack_mm=stack_mm, sect=sect2d,
                             n_stack=n_stack, n_cap=n_cap,
                             h_axial_end=h_axial_end, h_axial_max=h_axial_max)
    regs = _regions_for(tm, section, linear_iron=linear_iron,
                        iron_mu_r=iron_mu_r, laminated_iron=laminated_iron)
    linear_iron = linear_iron or iron_mu_r is not None
    mu_init = compatible_mu_init(mu_init, tm.n_elements)
    basis = Basis(tm.mesh, ElementTetP2() if order == 2 else ElementTetP1())
    per = None
    if section.n_sectors > 1:
        per = dof_pairs(basis, math.radians(section.sector_deg),
                        -1.0 if section.antiperiodic else 1.0)
    D = outer_boundary_dofs(basis, sect2d.r_box_mm * MM,
                            tm.meta["z_box_mm"] * MM)
    kw = dict(basis=basis, periodic=per, dirichlet_dofs=D,
              neumann_outer=neumann_outer, linear_solver=linear_solver)
    if linear_iron:
        sol = solve_static3d(tm.mesh, regs, order=order, **kw)
    else:
        sol = solve_static3d_nonlinear(tm.mesh, regs, order=order, tol=tol,
                                       max_iter=max_iter, damping=damping,
                                       verbose=verbose, mu_init=mu_init, **kw)
    return SectorSolve(sol=sol, tm=tm, section=section,
                       stack_mm=float(stack_mm if stack_mm else section.stack_mm),
                       basis=basis, periodic=per, dirichlet=D,
                       wall_s=time.perf_counter() - t0)


def solve_sector_A(section: MotorSection,
                   sect2d: Section2D,
                   stack_mm: Optional[float] = None,
                   n_stack: int = 8,
                   n_cap: int = 10,
                   tol: float = 3e-3,
                   max_iter: int = 45,
                   damping: float = 0.35,
                   flux_tight_outer: bool = True,
                   linear_iron: bool = False,
                   iron_mu_r: Optional[float] = None,
                   mu_init: Optional[np.ndarray] = None,
                   cg_tol: float = 1e-10,
                   verbose: bool = False) -> SectorSolve:
    """The same sector, solved in the A-formulation on Nedelec edges.

    Deliberately built to be swappable with :func:`solve_sector`: it returns the
    same :class:`SectorSolve`, so ``airgap_B1`` and ``spill_profile`` read the
    two formulations through exactly one code path and a three-way comparison
    cannot quietly be three different post-processors.

    Boundary conditions are the DUALS of the scalar solver's, which is the one
    thing that cannot be copied across (see ``nedelec``'s module docstring):

      * z = 0 (the axial mirror plane) carries B_z = 0.  For the scalar potential
        that is the natural condition and costs nothing; here it is A x n = 0 and
        must be imposed, or the model is a machine with no mirror symmetry.
      * the truncation surface: ``flux_tight_outer=True`` clamps A x n = 0 there
        (B . n = 0), which is the scalar solver's NEUMANN case; False leaves it
        natural, which is the scalar solver's phi = 0.  Stage A measured the two
        scalar cases 0.007 % apart on this machine, so the choice is not what any
        3D-vs-2D residual is made of — but it is stated rather than assumed.
      * the two cut planes carry the anti-periodic constraint on EDGE dofs,
        including the per-edge orientation sign — see ``periodic.edge_dof_pairs``.

    The gauge is ``"none"`` (ungauged CG): tree-cotree is not available on a
    periodically reduced system, and for a magnet-only source the load is
    consistent to round-off, so CG is not a compromise here.
    """
    from skfem import Basis
    from skfem.element import ElementTetN0

    from .nedelec import (mirror_plane_dofs, solve_static3d_A,
                          solve_static3d_A_nonlinear, truncation_dofs)
    from .periodic import edge_dof_pairs

    t0 = time.perf_counter()
    tm, _ = build_motor_mesh(section, stack_mm=stack_mm, sect=sect2d,
                             n_stack=n_stack, n_cap=n_cap)
    regs = _regions_for(tm, section, linear_iron=linear_iron,
                        iron_mu_r=iron_mu_r)
    linear_iron = linear_iron or iron_mu_r is not None
    mu_init = compatible_mu_init(mu_init, tm.n_elements)
    basis = Basis(tm.mesh, ElementTetN0())
    per = None
    if section.n_sectors > 1:
        per = edge_dof_pairs(basis, math.radians(section.sector_deg),
                             -1.0 if section.antiperiodic else 1.0)
    D = mirror_plane_dofs(basis, 0.0)
    if flux_tight_outer:
        D = np.unique(np.concatenate(
            [D, truncation_dofs(basis, sect2d.r_box_mm * MM,
                                tm.meta["z_box_mm"] * MM)]))
    kw = dict(basis=basis, periodic=per, dirichlet_dofs=D, gauge="none",
              cg_tol=cg_tol)
    if linear_iron:
        sol = solve_static3d_A(tm.mesh, regs, **kw)
    else:
        sol = solve_static3d_A_nonlinear(tm.mesh, regs, tol=tol,
                                         max_iter=max_iter, damping=damping,
                                         verbose=verbose, mu_init=mu_init, **kw)
    return SectorSolve(sol=sol, tm=tm, section=section,
                       stack_mm=float(stack_mm if stack_mm else section.stack_mm),
                       basis=basis, periodic=per, dirichlet=D,
                       wall_s=time.perf_counter() - t0)


# --------------------------------------------------------------------------
# (a) the spill curve
# --------------------------------------------------------------------------

def airgap_B1(ss: SectorSolve, z_m: Sequence[float], n_theta: int = 180
              ) -> Tuple[np.ndarray, np.ndarray]:
    """(|B1|, phase) of the pole-pair harmonic of B_r at each z station."""
    s = ss.section
    p = s.pole_pairs
    # Taking the order-p coefficient from ONE anti-periodic sector instead of
    # the full ring is exact only when the missing sectors are the algebraic
    # twins of the one that is there.  Summing the full ring,
    #   sum_j (-1)^j exp(-i 2 pi p j / N)  =  N   iff   2p/N is an ODD integer,
    # and 2p/N is exactly the pole count per sector — which is odd precisely
    # when the sector is anti-periodic.  So the condition is already implied,
    # and it is asserted rather than assumed because getting it wrong would
    # silently return a coefficient that is not the machine's.
    per_sector = s.num_poles / max(s.n_sectors, 1)
    if s.antiperiodic and (per_sector != int(per_sector)
                           or int(per_sector) % 2 == 0):
        raise ValueError(
            f"an anti-periodic {s.sector_deg:.1f} deg sector carries "
            f"{per_sector} poles; the order-{p} coefficient can only be read off "
            "one sector when that count is an ODD integer")
    span = math.radians(s.sector_deg)
    th = np.linspace(0.0, span, int(n_theta), endpoint=False)
    r = s.mid_r_m
    amp = np.empty(len(z_m))
    pha = np.empty(len(z_m))
    for i, z in enumerate(z_m):
        pts = np.vstack([r * np.cos(th), r * np.sin(th),
                         np.full(th.shape, float(z))])
        B = ss.sol.B_at_points(pts)
        if not np.all(np.isfinite(B)):
            raise RuntimeError(f"gap sample at z={z:.6f} m fell outside the mesh")
        Br = B[0] * np.cos(th) + B[1] * np.sin(th)
        amp[i], pha[i] = gap_fundamental(th, Br, p)
    return amp, pha


def spill_profile(ss: SectorSolve, n_in: int = 33, n_out: int = 8,
                  n_theta: int = 180) -> dict:
    """The normalised axial profile of the gap fundamental, and k_flux.

    Stations cluster toward the end of the stack (that is where the curve bends);
    the trapezoid over [0, L/2] is the stack average, which by mirror symmetry
    is also the average over the whole stack.
    """
    zh = ss.stack_half_m
    z_box = ss.tm.meta["z_box_mm"] * MM
    # cosine clustering toward z = zh, and never exactly ON the interface plane
    u = 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, int(n_in))))
    z_in = zh * u
    z_in[0] = min(1e-7, 0.02 * zh)
    z_in[-1] = zh * (1.0 - 1e-4)
    z_out = zh + (z_box - zh) * (np.linspace(0.0, 1.0, int(n_out) + 1)[1:] ** 2) * 0.35
    z_out = z_out[z_out < 0.9 * z_box]
    z = np.concatenate([z_in, z_out])

    amp, pha = airgap_B1(ss, z, n_theta=n_theta)
    B1_mid = float(amp[0])
    if not B1_mid > 0:          # also refuses NaN
        raise RuntimeError("mid-plane gap fundamental is zero or not a number — no field")
    prof = amp / B1_mid
    inside = z <= zh + 1e-12
    k = float(_trapz(amp[inside], z[inside]) / (zh * B1_mid))
    return dict(z_m=z, z_over_half=z / zh, B1_T=amp, phase=pha,
                profile=prof, B1_mid_T=B1_mid, k_flux_self=k,
                stack_half_m=zh)


# --------------------------------------------------------------------------
# (c) end-region demagnetisation exposure
# --------------------------------------------------------------------------

def demag_exposure(ss: SectorSolve, n_slices: int = 6) -> dict:
    """``H . M_hat`` inside the magnets, per axial slice.

    ``H`` comes straight from the solved state, H = (B - mu0 M)/mu, which is
    -grad(phi) exactly.  A NEGATIVE projection is demagnetising.  The reported
    quantities are the worst element and the volume-weighted mean per slice; the
    "shift" is the end slice against the mid slice.  Both are stated because the
    worst element in a magnet corner is a genuine field singularity and moves
    with the mesh, while the mean does not.
    """
    sol = ss.sol
    tm = ss.tm
    B = sol.B_elementwise()
    H = (B - MU0 * sol.M_el) / sol.mu_diag()   # per-direction: laminated iron
    vol = sol.basis.dx.sum(axis=1)
    zc = tm.mesh.p[2, tm.mesh.t].mean(axis=0)
    zh = ss.stack_half_m

    edges = np.linspace(0.0, zh, int(n_slices) + 1)
    slices: List[dict] = []
    for k in range(int(n_slices)):
        lo, hi = edges[k], edges[k + 1]
        worst = math.inf
        num = den = 0.0
        for r in ss.section.magnet_regions():
            el = tm.elements(r.name)
            m = np.asarray(r.M, dtype=float)
            mhat = m / max(np.linalg.norm(m), 1e-300)
            sel = el[(zc[el] >= lo) & (zc[el] < hi + 1e-15)]
            if sel.size == 0:
                continue
            proj = (H[:, sel] * mhat[:, None]).sum(axis=0)
            worst = min(worst, float(proj.min()))
            num += float((proj * vol[sel]).sum())
            den += float(vol[sel].sum())
        if den <= 0:
            continue
        slices.append(dict(z_lo_m=float(lo), z_hi_m=float(hi),
                           z_mid_m=float(0.5 * (lo + hi)),
                           worst_H_A_per_m=float(worst),
                           mean_H_A_per_m=float(num / den)))
    if len(slices) < 2:
        raise RuntimeError("not enough magnet slices to compare end vs mid")
    mid, end = slices[0], slices[-1]
    return dict(
        slices=slices,
        mid_worst_H=mid["worst_H_A_per_m"], end_worst_H=end["worst_H_A_per_m"],
        mid_mean_H=mid["mean_H_A_per_m"], end_mean_H=end["mean_H_A_per_m"],
        worst_shift_A_per_m=end["worst_H_A_per_m"] - mid["worst_H_A_per_m"],
        mean_shift_A_per_m=end["mean_H_A_per_m"] - mid["mean_H_A_per_m"],
        note=("negative H.Mhat is demagnetising; at I=0 the END elements sit at "
              "a LESS negative H than the mid-plane ones because the end flux "
              "spills into air instead of being pushed back through the magnet"))


# --------------------------------------------------------------------------
# passport
# --------------------------------------------------------------------------

def _jsonable(o):
    if isinstance(o, np.ndarray):
        return [float(v) for v in o.ravel()]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


SCHEMA = {
    "version": PASSPORT_VERSION,
    "_doc": "3D end-effect passport for ONE machine (magnet-only, I = 0).",
    "fields": {
        "geometry_fingerprint": "routes.simulation._geometry_fingerprint of the "
                                "geometry this was solved for; a passport whose "
                                "fingerprint does not match the active design "
                                "describes a different motor and must be ignored.",
        "machine": "slots/poles/radii/stack/materials actually solved, mm and T.",
        "model": "sector angle, periodicity sign, element order, air box, "
                 "mesh sizes, z levels — everything needed to reproduce.",
        "k_flux": "axial mean of the gap fundamental over the stack, divided by "
                  "the 2D fundamental. Multiply a 2D flux linkage / EMF / "
                  "back-EMF constant by this.",
        "k_flux_self": "same average divided by the 3D MID-PLANE value: the pure "
                       "end effect, with any 2D-vs-3D modelling difference "
                       "removed. k_flux = k_flux_self * (B1_3D_mid / B1_2D).",
        "spill_profile": "z stations (m, and z/(L/2)) with B1(z) in T and "
                         "B1(z)/B1(0). Stations beyond z/(L/2) = 1 are OUTSIDE "
                         "the iron and are the fringing tail, not stack flux.",
        "two_d": "the 2D reference: solver, mesh, B1 and the 3D-mid/2D ratio; "
                 "verdict / k_flux_usable / k_flux_status from agreement_check "
                 "(end deficit L(1 - k_flux) = D0 + eps*L, eps dimensionless, "
                 "over the rows at >= the reference stack and >= 2 pole "
                 "pitches; PASS |eps| <= 1 % and D0 > 0 -> verified; FAIL -> "
                 "not usable; NOT CHECKED (fewer than two usable lengths, span "
                 "< 1.25 x, or D not linear in L) -> usable, unverified, unless "
                 "the mid-plane ratio is above 1.02 or below 1 - max(0.15, "
                 "0.3 pole pitch / L) (one-sided coarse screen -> FAIL)); "
                 "two rows are an exact fit (eps error = dk (L1+L2)/(L2-L1), 5 x "
                 "dk for the catalog's 1.0 / 1.5 rows); INVALID when the 2D "
                 "fundamental is zero / not a number; mid_plane_check is a "
                 "diagnostic. Versions -1 / -2 judged the mid-plane ratio within "
                 "2 % (key agreement_tolerance); -3 has no such key.",
        "demag": "H.Mhat per axial magnet slice; negative is demagnetising.",
        "l_stack_curve": "k_flux (both normalisations) vs stack length. Each row: "
                         "k_flux = k_flux_self * ratio_3d_mid_over_2d, the row's OWN "
                         "B1_mid / B1_2D (method in k_flux_vs_L_method).",
        "k_flux_vs_L_method": "how the l_stack_curve k_flux rows were formed: "
                              "'own_mid_ratio' (each length's own mid-plane / 2D "
                              "ratio) or 'self_no_2d' (no 2D leg, k_flux = "
                              "k_flux_self). Absent (version static3d-stageA-1): "
                              "every row used the REFERENCE length's ratio, so only "
                              "the reference row is right.",
        "bracket": "Dirichlet vs Neumann outer boundary at the chosen box — a "
                   "CONSERVATIVE bound on the truncation error, not an estimate.",
        "solves": "dofs, elements, wall time and Picard state of every solve.",
    },
}


def write_passport(passport: dict, path: str) -> str:
    """Write the passport JSON (schema included in the file itself)."""
    d = dict(passport)
    d["_schema"] = SCHEMA
    p = os.path.abspath(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_jsonable(d), fh, indent=2, sort_keys=False)
    os.replace(tmp, p)
    return p


def read_passport(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# the Stage A driver
# --------------------------------------------------------------------------

def run_stage_a(geo_override: Optional[dict] = None,
                n_sectors: Optional[int] = None,
                box_factor: float = 4.0,
                h_gap: Optional[float] = None,
                h_solid: Optional[float] = None,
                order: int = 2,
                n_stack: int = 8,
                n_cap: int = 10,
                tol: float = 3e-3,
                max_iter: int = 45,
                l_factors: Union[Sequence[float], str] = (0.5, 0.75, 1.0, 1.5, 2.0),
                do_bracket: bool = True,
                do_2d: bool = True,
                laminated_iron: bool = True,
                n_theta: int = 180,
                linear_solver: Optional[str] = None,
                verbose: bool = True) -> dict:
    """Everything Stage A promises, as one passport dict.

    The cross-section is meshed ONCE and reused for every stack length, so the
    L-sweep is a comparison between models that differ only in axial extent —
    no remesh noise anywhere in the k_flux(L) curve.
    """
    t_all = time.perf_counter()
    # The sweep's factors, checked before any solve.  The reference length
    # (factor 1.0) is ALWAYS solved — it is step [1] — so when a caller leaves
    # it out of l_factors its row is appended at the END of the curve (no extra
    # solve; callers that read curve[0] still get their own first factor): a
    # per-length table without its reference row could not be anchored to the
    # passport's own k_flux.
    auto = isinstance(l_factors, str) and l_factors.strip().lower() == "auto"
    if isinstance(l_factors, str) and not auto:
        raise ValueError("l_factors must be a sequence of factors or 'auto'; got %r" % (l_factors,))
    l_factors = [1.0] if auto else [float(f) for f in l_factors]
    bad = [f for f in l_factors if not (math.isfinite(f) and f > 0.0)]
    if bad:
        raise ValueError("l_factors must be finite and positive stack-length "
                         "factors (1.0 = the reference stack); got %r" % (bad,))
    if not any(abs(f - 1.0) < 1e-12 for f in l_factors):
        l_factors.append(1.0)
    section = load_motor_section(geo_override=geo_override, n_sectors=n_sectors)
    if auto:
        # "auto": 2-3 rows clear the end zone with span >= 1.5 (auto_l_factors); explicit factors are never touched
        l_factors = list(auto_l_factors(section.stack_mm,
                                        math.pi * float(section.mid_r_mm) / max(1, int(section.pole_pairs))))
    if verbose:
        print(section.summary(), flush=True)

    # Mesh sizes follow the PHYSICAL scales (elements across the air gap and
    # across the thinnest magnet), never the machine diameter.  An explicit
    # value may only be FINER than the physical rule, so a caller cannot put
    # one element across a gap again.
    from .motor_mesh import physical_mesh_sizes
    phys = physical_mesh_sizes(section)
    h_gap = phys["h_gap"] if h_gap is None else min(float(h_gap), phys["h_gap"])
    h_solid = (phys["h_solid"] if h_solid is None
               else min(float(h_solid), phys["h_solid"]))
    h_solid = max(h_solid, h_gap)
    ax = dict(h_axial_end=phys["h_axial_end"], h_axial_max=phys["h_axial_max"])
    if verbose:
        print(f"mesh from physical scales: gap {phys['gap_mm']:.3f} mm -> "
              f"h_gap {h_gap:.4f} mm ({phys['n_gap']} across); thinnest magnet "
              f"{phys['magnet_width_mm']:.3f} mm -> h_solid {h_solid:.4f} mm",
              flush=True)

    sect2d = build_section_mesh_2d(section, box_factor=box_factor,
                                   h_gap=h_gap, h_solid=h_solid)
    if verbose:
        print(f"cross-section: {sect2d.n_tri} tri, {sect2d.p.shape[1]} nodes, "
              f"box r = {sect2d.r_box_mm:.1f} mm, "
              f"{sect2d.meta['n_periodic_curves']} periodic curves", flush=True)

    solves: List[dict] = []

    def _record(tag: str, ss: SectorSolve) -> dict:
        pic = ss.sol.picard or {}
        d = dict(tag=tag, stack_mm=ss.stack_mm, elements=ss.tm.n_elements,
                 ndofs=ss.sol.ndofs, order=ss.sol.order,
                 linear_solver=ss.sol.solver,
                 assemble_s=ss.sol.assemble_time,
                 last_linear_solve_s=ss.sol.solve_time,
                 wall_s=ss.wall_s,
                 picard_iterations=pic.get("iterations"),
                 picard_converged=pic.get("converged"),
                 picard_residual=(pic.get("history") or [None])[-1],
                 picard_tol=pic.get("tol"),
                 warm_started=pic.get("warm_started", False),
                 boundary_flux_Wb=float(ss.sol.boundary_flux()))
        solves.append(d)
        return d

    # ---- reference stack -------------------------------------------------
    if verbose:
        print(f"\n[1] reference stack {section.stack_mm:.2f} mm", flush=True)
    ss = solve_sector(section, sect2d, stack_mm=section.stack_mm, order=order,
                      n_stack=n_stack, n_cap=n_cap, tol=tol, max_iter=max_iter,
                      laminated_iron=laminated_iron, **ax,
                      linear_solver=linear_solver, verbose=verbose)
    _record("reference", ss)
    mu_ref = getattr(ss.sol, "mu_converged", None)
    prof = spill_profile(ss, n_theta=n_theta)
    demag = demag_exposure(ss)
    if verbose:
        print(f"    B1(mid) = {prof['B1_mid_T']:.5f} T, "
              f"k_flux_self = {prof['k_flux_self']:.5f}, "
              f"{ss.sol.ndofs} dofs, {ss.wall_s:.1f} s", flush=True)

    k_self = prof["k_flux_self"]

    # ---- truncation bracket ---------------------------------------------
    bracket = None
    if do_bracket:
        if verbose:
            print("\n[2] Dirichlet / Neumann truncation bracket", flush=True)
        ssn = solve_sector(section, sect2d, stack_mm=section.stack_mm,
                           order=order, n_stack=n_stack, n_cap=n_cap, tol=tol,
                           max_iter=max_iter, neumann_outer=True,
                           mu_init=mu_ref, laminated_iron=laminated_iron, **ax,
                           linear_solver=linear_solver, verbose=False)
        _record("neumann_outer", ssn)
        pn = spill_profile(ssn, n_theta=n_theta)
        bracket = dict(
            box_r_mm=sect2d.r_box_mm, box_z_mm=ss.tm.meta["z_box_mm"],
            box_factor_vs_stator_radius=box_factor,
            dirichlet=dict(B1_mid_T=prof["B1_mid_T"], k_flux_self=k_self),
            neumann=dict(B1_mid_T=pn["B1_mid_T"], k_flux_self=pn["k_flux_self"]),
            rel_spread_B1_mid=abs(pn["B1_mid_T"] - prof["B1_mid_T"])
            / max(prof["B1_mid_T"], 1e-30),
            rel_spread_k_flux=abs(pn["k_flux_self"] - k_self) / max(k_self, 1e-30),
            note=("phi=0 forces B normal to the truncation surface, dphi/dn=0 "
                  "forces it tangential; the true answer lies between, so the "
                  "spread is a BOUND on the truncation error, not an estimate"))
        if verbose:
            print(f"    B1 mid: D {prof['B1_mid_T']:.5f} / N {pn['B1_mid_T']:.5f} T "
                  f"-> {100*bracket['rel_spread_B1_mid']:.3f} %  |  "
                  f"k_flux spread {100*bracket['rel_spread_k_flux']:.3f} %",
                  flush=True)

    # ---- L sweep ---------------------------------------------------------
    curve: List[dict] = []
    if verbose:
        print("\n[3] k_flux vs stack length", flush=True)
    mu_prev = mu_ref
    for fct in l_factors:
        Lm = float(fct) * section.stack_mm
        if abs(fct - 1.0) < 1e-12:
            p_i, ss_i = prof, ss
        else:
            ss_i = solve_sector(section, sect2d, stack_mm=Lm, order=order,
                                n_stack=n_stack, n_cap=n_cap, tol=tol,
                                max_iter=max_iter, mu_init=mu_prev,
                                laminated_iron=laminated_iron, **ax,
                                linear_solver=linear_solver, verbose=False)
            _record(f"L={Lm:g}mm", ss_i)
            mu_prev = getattr(ss_i.sol, "mu_converged", mu_prev)
            p_i = spill_profile(ss_i, n_theta=n_theta)
        row = dict(factor=float(fct), stack_mm=Lm,
                   B1_mid_T=p_i["B1_mid_T"],
                   k_flux_self=p_i["k_flux_self"],
                   k_flux=p_i["k_flux_self"],   # rescaled once 2D is known
                   ndofs=ss_i.sol.ndofs, elements=ss_i.tm.n_elements,
                   wall_s=ss_i.wall_s,
                   picard_converged=(ss_i.sol.picard or {}).get("converged"))
        curve.append(row)
        if verbose:
            print(f"    L = {Lm:6.2f} mm  k_self = {row['k_flux_self']:.5f}  "
                  f"k_flux = {row['k_flux']:.5f}  ({ss_i.wall_s:.0f} s)",
                  flush=True)

    # ---- 2D reference, LAST ---------------------------------------------
    # Deliberately after the 3D legs.  It is a consistency CHECK, not an input
    # to any of them, and it runs in someone else's solver whose cost this
    # module does not control — putting it first once cost an hour of wall time
    # in front of work that did not depend on it.
    two_d = None
    # the 2D fundamental the k_flux rows divide by; None = no usable 2D leg (not
    # run, or its fundamental is zero / not a number: then every k_flux is the
    # self-normalised k_flux_self, as without a 2D leg, and two_d says why)
    b1_2d = None
    if do_2d:
        from .ref2d import (solve_2d_reference_converged,
                            solve_2d_reference_on_section_mesh)
        if verbose:
            print("\n[4] 2D reference (full ring, I=0, P2)", flush=True)
        # The project's own 2D weak form in A_z, its materials, its B-H curve, on
        # the cross-section mirrored to a full ring.  Its mesh GENERATOR is not
        # used: mesher.build_mesh_from_polygons at n_sectors=1 is the documented
        # closed-360 OCC pathology and did not complete in over an hour on this
        # machine.  See ref2d for the full reasoning.
        #
        # CONVERGED, and the shipped relaxation alongside it.  The shipped one
        # limit-cycles at a constitutive residual of ~0.8 on this machine and
        # its answer is 11 % low; quoting only it is what made this check fail
        # for three rounds.  Both are recorded so the difference stays visible.
        sec_full = load_motor_section(geo_override=geo_override, n_sectors=1)
        r2 = solve_2d_reference_converged(sec_full, sect2d)
        r2s = solve_2d_reference_on_section_mesh(sec_full, sect2d,
                                                 nonlinear_iterations=80)
        two_d = dict(B1_T=r2.B1_T,
                     harmonics={str(k): v for k, v in r2.harmonics.items()},
                     n_tri=r2.n_tri, ndofs=r2.ndofs, wall_s=r2.wall_s,
                     outer_r_mm=r2.outer_r_mm,
                     solver=("fem_solver_2d weak form (P2, full ring, static3d "
                             "cross-section mirrored), converged Picard"),
                     relaxation=r2.relaxation,
                     picard_sweeps=r2.picard_sweeps,
                     picard_residual=r2.picard_residual,
                     picard_converged=r2.picard_converged,
                     antiperiodicity_even_over_odd=r2.even_over_odd,
                     shipped_relaxation=dict(
                         B1_T=r2s.B1_T, sweeps=80,
                         solver="fem_solver_2d.solve_magnetostatics_fem",
                         note=("its Picard does not converge on this machine — "
                               "constitutive residual ~0.8, 3 % violation of an "
                               "exact anti-periodicity, 3 % band over sweep "
                               "counts; kept for the before/after only")),
                     ratio_3d_mid_over_2d=None)
        try:
            _b2 = float(r2.B1_T)
        except (TypeError, ValueError):
            _b2 = float("nan")
        if math.isfinite(_b2) and _b2 > 0.0:
            b1_2d = r2.B1_T
            two_d["ratio_3d_mid_over_2d"] = prof["B1_mid_T"] / r2.B1_T
        if verbose:
            if b1_2d is not None:
                print(f"    2D B1 = {r2.B1_T:.5f} T (converged, resid "
                      f"{r2.picard_residual:.1e}, {r2.picard_sweeps} sweeps; "
                      f"shipped relaxation says {r2s.B1_T:.5f} T), "
                      f"3D mid / 2D = {two_d['ratio_3d_mid_over_2d']:.4f}",
                      flush=True)
            else:
                print(f"    2D B1 = {r2.B1_T!r} T: zero or not a number, no "
                      "3D mid / 2D ratio formed (k_flux = k_flux_self)", flush=True)
    ratio = two_d["ratio_3d_mid_over_2d"] if b1_2d is not None else 1.0
    k_vs2d = k_self * ratio
    if two_d is not None and b1_2d is None:
        two_d["verdict"] = "INVALID"
        two_d["k_flux_status"] = "invalid"
        two_d["k_flux_usable"] = False
        two_d["warning"] = (
            "the 2D reference fundamental is zero or not a number (B1 = %r T): "
            "no 3D-mid / 2D ratio is formed, k_flux = k_flux_self and every "
            "stack-length row is self-normalised" % (r2.B1_T,))
    # Each stack length is divided by the 2D fundamental through ITS OWN
    # mid-plane value: k_flux(L) = <B1>_L / B1_2D = k_flux_self(L) * B1_mid(L) / B1_2D.
    # The mid-plane deficit against 2D is itself a stack-length effect (it
    # vanishes in the long-stack limit), so the reference length's ratio must
    # not be carried to the other lengths: that held k_flux nearly flat in L
    # and read short stacks several per cent high.  The reference row is the
    # same float division as ``ratio`` above, so it is bit-identical to the
    # passport's k_flux.  Without a 2D leg the row keeps k_flux_self (x 1.0).
    for row in curve:
        if b1_2d is not None:
            row["ratio_3d_mid_over_2d"] = row["B1_mid_T"] / b1_2d
            row["k_flux"] = row["k_flux_self"] * row["ratio_3d_mid_over_2d"]
        else:
            row["k_flux"] = row["k_flux_self"] * ratio

    if two_d is not None and b1_2d is not None:
        # The 3D-vs-2D agreement check, on the rows just formed.  A FAIL means
        # the end deficit grows with the stack length, i.e. the two models
        # disagree beyond the end effect: k_flux (which multiplies a 2D result
        # by a number derived from BOTH models) would inherit that, so it is
        # marked not usable.  NOT CHECKED (fewer than two usable lengths, lengths
        # too close, or D not linear in L) leaves k_flux usable, unless the
        # one-sided coarse screen refuses the mid-plane ratio.  The mid-plane
        # ratio stays as a diagnostic.
        off = abs(ratio - 1.0)
        pole_pitch = math.pi * float(section.mid_r_mm) / max(1, int(section.pole_pairs))
        chk = end_deficit_check(curve, section.stack_mm, pole_pitch_mm=pole_pitch)
        lo, hi = coarse_screen_bounds(section.stack_mm, pole_pitch)
        if chk["verdict"] == "NOT CHECKED" and not (lo <= ratio <= hi):
            chk["verdict"] = "FAIL"
            chk["coarse_screen"] = dict(low=lo, high=hi, ratio=ratio)
            chk["reason"] = ("coarse screen: not checkable (%s) and the mid-plane / 2D ratio %.4f is outside "
                             "[%.4f, %.4f]" % (chk.get("reason"), ratio, lo, hi))
        two_d["agreement_check"] = chk
        two_d["mid_plane_check"] = dict(
            ratio=ratio, tolerance=MID_PLANE_TOL,
            within=bool(off <= MID_PLANE_TOL),
            note=("diagnostic only: at a short stack the mid-plane sits below "
                  "2D by end physics, it is not a model check"))
        two_d["verdict"] = chk["verdict"]
        two_d["k_flux_usable"] = chk["verdict"] != "FAIL"
        two_d["k_flux_status"] = {"PASS": "verified", "FAIL": "failed"}.get(chk["verdict"], "unverified")
        if chk["verdict"] == "FAIL":
            two_d["warning"] = (
                ("the end deficit L(1 - k_flux) grows with the stack length "
                 f"(eps = {100 * chk['eps']:.2f} %, dimensionless; D0 = {chk['D0_mm']:.3f} mm)"
                 if chk.get("eps") is not None and not chk.get("coarse_screen")
                 else chk.get("reason", "")) +
                ": 3D and 2D disagree beyond the end effect. Use k_flux_self "
                "and the spill profile; do NOT use k_flux until the two models "
                "are reconciled. First two things to check, in order: (1) the "
                "magnet constitutive law - the 2D A_z source must be M/mu_rec, "
                "not M (see ref2d's docstring); (2) the axial mesh (n_stack) of "
                "the longer rows.")

    passport = dict(
        version=PASSPORT_VERSION,
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        geometry_fingerprint=section.fingerprint,
        machine=dict(
            num_slots=section.num_slots, num_poles=section.num_poles,
            pole_pairs=section.pole_pairs,
            stator_od_mm=2 * section.r_stator_out_mm,
            stator_bore_mm=2 * section.r_stator_in_mm,
            rotor_od_mm=2 * section.r_rotor_out_mm,
            air_gap_mm=section.r_stator_in_mm - section.r_rotor_out_mm,
            mid_gap_r_mm=section.mid_r_mm,
            stack_mm=section.stack_mm,
            topology="spoke PM (tangential magnetisation)",
            Br_T=section.Br_T, mu_rec=section.mu_rec,
            materials=section.materials),
        model=dict(
            sector_deg=section.sector_deg, n_sectors=section.n_sectors,
            periodicity="anti-periodic" if section.antiperiodic else "periodic",
            element_order=order, formulation="total magnetic scalar potential",
            mirror_plane_z0="natural (dphi/dn = 0) — B_z = 0 by symmetry",
            box_r_mm=sect2d.r_box_mm, box_z_mm=ss.tm.meta["z_box_mm"],
            h_gap_mm=h_gap, h_solid_mm=h_solid,
            mesh_rule=("physical: air gap / %d, thinnest magnet / %d"
                       % (phys["n_gap"], phys["n_magnet"])
                       + "; axial: end layer 2 x gap growing x1.5 to one pole pitch"),
            h_axial_end_mm=ax["h_axial_end"], h_axial_max_mm=ax["h_axial_max"],
            iron_stack_kf={r.name: float(r.stack_kf) for r in section.regions
                           if r.kind == "iron"},
            cross_section_tri=sect2d.n_tri,
            axial_layers=ss.tm.meta["n_layers"],
            z_levels_mm=ss.tm.meta["z_levels_mm"],
            n_theta_samples=n_theta,
            iron_bh="laminated B-H from fem_solver_2d.build_materials",
            iron_lamination=(
                "transverse-isotropic: the 2D materials' homogenised curve IN "
                "PLANE, the insulation-limited series average ACROSS the stack "
                "(solver.axial_mu, k_f from the same material)"
                if laminated_iron else
                "isotropic (solid block of the in-plane homogenised iron) — the "
                "only thing a 2D model can assume")),
        k_flux=float(k_vs2d),
        k_flux_self=float(k_self),
        # the 2-D consistency check was not run: the ratio is NOT measured as 1.0, k_flux is the self-normalised
        # 3-D ratio (= k_flux_self) and says so, so a reader can tell it from a checked one
        k_flux_basis=k_flux_basis_label(two_d),
        spill_profile=dict(
            z_m=prof["z_m"], z_over_half_stack=prof["z_over_half"],
            B1_T=prof["B1_T"], B1_normalised=prof["profile"],
            B1_mid_T=prof["B1_mid_T"], stack_half_m=prof["stack_half_m"]),
        two_d=two_d,
        demag=demag,
        l_stack_curve=curve,
        k_flux_vs_L_method=(K_FLUX_VS_L_OWN_MID if b1_2d is not None
                            else K_FLUX_VS_L_SELF),
        bracket=bracket,
        solves=solves,
        total_wall_s=time.perf_counter() - t_all,
    )
    return passport
