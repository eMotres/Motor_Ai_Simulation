"""Torque in 3D by the ENERGY METHOD, on the sliding band.

Maxwell stress is banned in this project (``config/end_effect_3d.json`` records
why: on the 2D sliding band it over-read torque by ~37 % under load), so the only
torque here is

    T  =  + dW' / d(theta)   at CONSTANT current,

with ``W'`` the magnetic CO-energy of the whole machine and ``theta`` the
mechanical rotor angle.  The derivative is a central difference over the sliding
band's own legal angles — integer multiples of the ring pitch — so the two states
being differenced sit on meshes that are bit-identical (``band.SlipWeld``); the
only thing that changed between them is which edge dof is welded to which.

The co-energy, region by region
-------------------------------
``W' = integral( integral_0^H B . dH' ) dV``, and it is worth writing out what
that is for each material this machine has, because the temptation to use
``1/2 A^T f`` everywhere is strong and it is only right for some of them:

* **air, shaft, and any LINEAR isotropic region** — ``w' = 1/2 nu |B|^2``.
* **a magnet** (linear recoil mu_r, fixed M) — expanding
  ``w' = 1/2 mu0 mu_r |H|^2 + mu0 M . H`` with ``H = nu B - Hc`` gives
  ``1/2 nu |B|^2`` plus a term in ``M`` and ``Hc`` alone.  That term is constant
  in theta (the magnet elements never move; only the weld changes), so it
  cancels in every difference taken here.  This is why the LINEAR machine's
  co-energy really is ``1/2 A^T f`` up to a constant, and
  :func:`co_energy_from_load` checks that identity against the volume integral.
* **saturating iron** — ``w' = |H| |B| - integral_0^{|B|} H dB'``, the Legendre
  transform of the stored energy, read off the SAME B-H curve the Picard used.
  There is no shortcut here: ``1/2 nu |B|^2`` with the converged nu is the
  co-energy of a LINEAR material with that permeability, and on a saturated
  bridge the two differ by tens of percent.
* **laminated iron** — the solver reads the curve at the IN-PLANE ``|B|`` and
  carries the axial component through ``nu_z``, so the co-energy is built the
  same way: ``w'(|B_t|)`` from the curve plus ``1/2 nu_z B_z^2``.  Stated as a
  modelling choice because it is one: it is the co-energy OF THE CONSTITUTIVE
  LAW THE SOLVER USED, which is what a virtual-work derivative needs, rather
  than of some other homogenisation.

Multipliers: the model is one anti-periodic sector of the machine's ``n_sectors``
and the axial half ``z >= 0``, so every energy here is multiplied by
``2 * n_sectors``.  Both B and the sources change sign together between sectors,
so the co-energy DENSITY is periodic and the sectors simply add.

Units: SI (joules, newton-metres, mechanical radians).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from .band import BandedSection, SlipWeld
from .loaded import LoadedSolve, _regions_for
from .motor_geometry import MM, MotorSection
from .motor_mesh import build_motor_mesh
from .nedelec import (mirror_plane_dofs, solve_static3d_A,
                      solve_static3d_A_nonlinear, truncation_dofs)
from .solver import MU0, Region
from .winding3d import build_winding_T


# --------------------------------------------------------------------------
# co-energy
# --------------------------------------------------------------------------

def _curve_coenergy(bh_curve, Bmag: np.ndarray) -> np.ndarray:
    """``|H||B| - integral_0^{|B|} H dB'`` for an isotropic saturable material.

    ``H(B)`` is read exactly as ``field_ops._mu_r_from_bh_vec`` reads it — linear
    interpolation in B between samples, and the differential-mu0 slope above the
    last one — so the co-energy and the permeability the Picard converged on come
    off the SAME curve.  A co-energy built from a different reading of the curve
    than the solve used is not the co-energy of anything that was solved.
    """
    B = np.asarray(Bmag, dtype=float)
    hs = np.asarray([pt[0] for pt in bh_curve], dtype=float)
    bs = np.asarray([pt[1] for pt in bh_curve], dtype=float)
    if bs[0] > 0.0:                      # the curve starts at (0, 0) implicitly
        hs = np.concatenate([[0.0], hs])
        bs = np.concatenate([[0.0], bs])
    # E(B) = int_0^B H dB' for the piecewise-LINEAR H the solver interpolates:
    # exact at the samples by a cumulative trapezoid, and exact BETWEEN them by
    # a trapezoid on the part-segment.  Interpolating E linearly instead would
    # be an O(h^2) error on a quantity whose DIFFERENCE is the torque — measured
    # at 6e-4 relative on a 0.1 T sample spacing, which is a hundred times the
    # torque signal this function exists to produce.
    E_nodes = np.concatenate([[0.0], np.cumsum(0.5 * (hs[1:] + hs[:-1])
                                               * np.diff(bs))])
    H = np.interp(B, bs, hs)
    i = np.clip(np.searchsorted(bs, B) - 1, 0, bs.size - 2)
    E = E_nodes[i] + 0.5 * (hs[i] + H) * (B - bs[i])
    # Above the last sample the curve continues at the differential mu0 slope,
    # which is the same continuation ``_mu_r_from_bh_vec`` uses.  dB is built
    # over the WHOLE array (clamped at zero) rather than over the selection, so
    # the three arrays np.where sees have the same shape.
    above = B >= bs[-1]
    dB = np.maximum(B - bs[-1], 0.0)
    H = np.where(above, hs[-1] + dB / MU0, H)
    E = np.where(above, E_nodes[-1] + hs[-1] * dB + 0.5 * dB * dB / MU0, E)
    return H * B - E


def co_energy_density(sol, regions: Sequence[Region]) -> np.ndarray:
    """Co-energy density per element [J/m^3], with the constant terms dropped.

    The dropped constants are the magnets' ``M``-only terms; they are the same
    at every rotor angle, so they cancel in the central difference and carrying
    them would only make the absolute number look meaningful when the difference
    is what is.
    """
    B = sol.B_elementwise()
    nu = sol.nu_diag()
    w = 0.5 * (nu * B * B).sum(axis=0)
    for r in regions:
        if not r.nonlinear:
            continue
        idx = np.asarray(r.elements, dtype=np.int64)
        if r.laminated:
            Bt = np.hypot(B[0][idx], B[1][idx])
            w[idx] = (_curve_coenergy(r.bh_curve, Bt)
                      + 0.5 * nu[2][idx] * B[2][idx] ** 2)
        else:
            w[idx] = _curve_coenergy(r.bh_curve, np.linalg.norm(B[:, idx],
                                                                axis=0))
    return w


def co_energy(sol, regions: Sequence[Region], n_sectors: int) -> float:
    """Whole-machine co-energy [J] (sector x 2 for the axial mirror)."""
    w = co_energy_density(sol, regions)
    vol = sol.basis.dx.sum(axis=1)
    return float((w * vol).sum()) * 2.0 * max(int(n_sectors), 1)


def co_energy_from_load(sol, n_sectors: int,
                        T_field=None) -> float:
    """``1/2 integral(S . B)`` — the LINEAR co-energy, up to the same constant.

    ``S = Hc + T`` is the total source field the load was assembled from, so this
    is ``1/2 A^T f`` computed without keeping ``f`` around.  Only valid when every
    region is linear; it exists as an INDEPENDENT route to the same number, and
    the test suite pins the two against each other on a linear machine.  If they
    ever drift, one of the two readings of the constitutive law is wrong.
    """
    basis = sol.basis
    Bq = basis.interpolate(sol.A).curl
    dx = basis.dx
    nqp = dx.shape[1]
    S = np.repeat(sol.Hc_el[:, :, None], nqp, axis=2)
    if T_field is not None:
        S = S + np.asarray(T_field(np.asarray(basis.global_coordinates())),
                           dtype=float)
    return float(((S * Bq).sum(axis=0) * dx).sum()) * 0.5 * 2.0 * \
        max(int(n_sectors), 1)


# --------------------------------------------------------------------------
# the model: mesh, basis and weld built ONCE, rotor angle by re-labelling
# --------------------------------------------------------------------------

@dataclass
class BandedModel:
    """One machine on one banded mesh, solvable at any legal rotor shift.

    Everything expensive — the cross-section, the extrusion, the edge lookup,
    the winding's Psi grid — is built once in ``__init__``.  ``solve(m)`` then
    costs one projection build (milliseconds) plus the linear algebra, which is
    what makes a delta sweep and a multi-position torque affordable at all.
    """
    section: MotorSection
    banded: BandedSection
    stack_mm: Optional[float] = None
    n_stack: int = 5
    n_cap: int = 6
    n_ew: int = 4
    h_ew_mm: Optional[float] = None
    ew_shape: str = "cosine"
    I_ph: Optional[Dict[str, float]] = None
    linear_iron: bool = False
    laminated_iron: bool = True
    magnets_off: bool = False
    flux_tight_outer: bool = True
    verbose: bool = False

    def __post_init__(self):
        from skfem import Basis
        from skfem.element import ElementTetN0

        t0 = time.perf_counter()
        if self.h_ew_mm is None and self.I_ph is not None:
            self.h_ew_mm = (0.5 * float(self.section.geo["tooth_width"])
                            + 0.5 * float(self.section.geo["wire_width"]))
        self.tm, _ = build_motor_mesh(self.section, stack_mm=self.stack_mm,
                                      sect=self.banded.sect,
                                      n_stack=self.n_stack, n_cap=self.n_cap,
                                      h_ew_mm=self.h_ew_mm, n_ew=self.n_ew)
        self.regions = _regions_for(self.tm, self.section,
                                    linear_iron=self.linear_iron,
                                    laminated_iron=self.laminated_iron,
                                    magnets_off=self.magnets_off)
        self.winding = None
        if self.I_ph is not None:
            self.winding = build_winding_T(self.section, dict(self.I_ph),
                                           stack_mm=self.stack_mm,
                                           h_ew_mm=self.h_ew_mm,
                                           ew_shape=self.ew_shape)
        self.basis = Basis(self.tm.mesh, ElementTetN0())
        self.dirichlet = mirror_plane_dofs(self.basis, 0.0)
        if self.flux_tight_outer:
            self.dirichlet = np.unique(np.concatenate(
                [self.dirichlet,
                 truncation_dofs(self.basis, self.banded.sect.r_box_mm * MM,
                                 self.tm.meta["z_box_mm"] * MM)]))
        self.weld = SlipWeld(self.basis, self.banded, self.tm, self.section)
        self._proj: Dict[int, tuple] = {}
        self.build_s = time.perf_counter() - t0

    # -- the rotor angle ---------------------------------------------------
    @property
    def pitch_rad(self) -> float:
        return self.banded.pitch_rad

    def angle_deg(self, m: int) -> float:
        return self.banded.shift_angle_deg(m)

    def projection(self, m: int):
        # Keyed on the shift ITSELF, not on the shift modulo a sector.  Shifting
        # by a whole sector flips every ring weld's sign, and while that leaves
        # the MAGNET-only co-energy identical (the stator field simply reverses),
        # under load it is a different machine: the winding does not turn with
        # the rotor, so m and m + n_cell put the reversed magnets against the
        # same currents.
        key = int(m)
        if key not in self._proj:
            P, inv, D = self.weld.build(key, self.dirichlet)
            self._proj[key] = (P, D)
        return self._proj[key]

    # -- solve -------------------------------------------------------------
    def solve(self, m: int = 0, mu_init: Optional[np.ndarray] = None,
              tol: float = 3e-3, max_iter: int = 45, damping: float = 0.35,
              damping_init: Optional[float] = None,
              cg_tol: float = 1e-10) -> LoadedSolve:
        t0 = time.perf_counter()
        P, Dcols = self.projection(m)
        kw = dict(basis=self.basis, projection=(P, Dcols),
                  dirichlet_dofs=self.dirichlet, gauge="none", cg_tol=cg_tol,
                  T=(self.winding.field() if self.winding is not None else None))
        if self.linear_iron:
            sol = solve_static3d_A(self.tm.mesh, self.regions, **kw)
        else:
            sol = solve_static3d_A_nonlinear(
                self.tm.mesh, self.regions, tol=tol, max_iter=max_iter,
                damping=damping, damping_init=damping_init,
                verbose=self.verbose, mu_init=mu_init, **kw)
        return LoadedSolve(
            sol=sol, tm=self.tm, section=self.section,
            stack_mm=float(self.stack_mm if self.stack_mm
                           else self.section.stack_mm),
            basis=self.basis, periodic=None, dirichlet=self.dirichlet,
            winding=self.winding, I_ph=dict(self.I_ph or {}),
            wall_s=time.perf_counter() - t0,
            meta=dict(shift=int(m), rotor_angle_deg=self.angle_deg(m),
                      laminated_iron=bool(self.laminated_iron),
                      linear_iron=bool(self.linear_iron),
                      magnets_off=bool(self.magnets_off),
                      h_ew_mm=(None if self.h_ew_mm is None
                               else float(self.h_ew_mm)),
                      n_ew=int(self.n_ew) if self.h_ew_mm else 0,
                      ew_shape=str(self.ew_shape),
                      n_tets=int(self.tm.mesh.t.shape[1]),
                      ndofs=int(sol.ndofs),
                      n_reduced=int(P.shape[1]),
                      z_levels_mm=list(self.tm.meta["z_levels_mm"])))

    # -- energy ------------------------------------------------------------
    def co_energy(self, ls: LoadedSolve) -> float:
        return co_energy(ls.sol, self.regions, self.section.n_sectors)

    def co_energy_linear(self, ls: LoadedSolve) -> float:
        return co_energy_from_load(
            ls.sol, self.section.n_sectors,
            T_field=(self.winding.field() if self.winding is not None else None))


# --------------------------------------------------------------------------
# torque
# --------------------------------------------------------------------------

def torque_central_difference(model: BandedModel, m0: int, dm: int,
                              warm_start: bool = True, **kw) -> dict:
    """``T = (W'(m0+dm) - W'(m0-dm)) / (2 dm * pitch)`` [N.m].

    ``warm_start`` carries the converged permeability from the first solve into
    the second.  That is not an optimisation here but the difference between a
    torque that is affordable and one that is not: a cold nonlinear loaded solve
    of this machine is ~33 Picard sweeps at ~75 s each.
    """
    out = {}
    energies = {}
    cost = []
    mu = None
    for tag, m in (("minus", m0 - dm), ("plus", m0 + dm)):
        ls = model.solve(m, mu_init=(mu if warm_start else None), **kw)
        energies[tag] = model.co_energy(ls)
        p = ls.sol.picard or {}
        cost.append(dict(tag=tag, shift=int(m), angle_deg=model.angle_deg(m),
                         wall_s=ls.wall_s,
                         picard_iterations=p.get("iterations"),
                         picard_converged=p.get("converged"),
                         picard_residual=(p.get("history") or [None])[-1],
                         cg_iterations=ls.sol.iterations,
                         warm_started=p.get("warm_started")))
        if warm_start and ls.sol.mu_converged is not None:
            mu = ls.sol.mu_converged
        out[tag] = ls
    dtheta = 2.0 * int(dm) * model.pitch_rad
    T = (energies["plus"] - energies["minus"]) / dtheta
    return dict(T_Nm=float(T), dm=int(dm), d_theta_deg=math.degrees(dtheta),
                W_minus_J=float(energies["minus"]),
                W_plus_J=float(energies["plus"]),
                dW_J=float(energies["plus"] - energies["minus"]),
                m0=int(m0), angle0_deg=model.angle_deg(m0),
                cost=cost, solves={k: v for k, v in out.items()})


def torque_from_energy_curve(model: BandedModel, shifts: Sequence[int],
                             warm_start: bool = True, **kw) -> dict:
    """``W'(m)`` at a list of shifts, plus every central difference it supports.

    One solve per shift, warm-started along the sweep.  This is what a delta
    sweep is made of: with ``W'`` on a uniform grid of shifts, every step size
    ``dm`` is a free re-reading of the same solves rather than a new set of them.
    """
    sh = [int(s) for s in shifts]
    W: List[float] = []
    cost: List[dict] = []
    mu = None
    for m in sh:
        ls = model.solve(m, mu_init=(mu if warm_start else None), **kw)
        W.append(model.co_energy(ls))
        p = ls.sol.picard or {}
        cost.append(dict(shift=int(m), angle_deg=model.angle_deg(m),
                         wall_s=ls.wall_s, cg_iterations=ls.sol.iterations,
                         picard_iterations=p.get("iterations"),
                         picard_converged=p.get("converged"),
                         picard_residual=(p.get("history") or [None])[-1],
                         warm_started=p.get("warm_started")))
        if warm_start and ls.sol.mu_converged is not None:
            mu = ls.sol.mu_converged
    return dict(shifts=sh, angle_deg=[model.angle_deg(m) for m in sh],
                W_J=W, pitch_rad=model.pitch_rad, cost=cost)


def delta_sweep(curve: dict, i_centre: int) -> List[dict]:
    """Torque at the centre point for every step size the curve supports.

    The plateau between cancellation noise (dm too small: the difference of two
    nearly equal energies loses digits) and curvature bias (dm too large: the
    central difference starts measuring the third derivative) is the thing to
    look at, and it can only be seen by reading the SAME solves at several
    step sizes.
    """
    W = np.asarray(curve["W_J"], dtype=float)
    sh = np.asarray(curve["shifts"], dtype=int)
    pitch = float(curve["pitch_rad"])
    out = []
    n = W.size
    for d in range(1, min(i_centre, n - 1 - i_centre) + 1):
        dm = int(sh[i_centre + d] - sh[i_centre - d])
        dth = dm * pitch
        out.append(dict(dm_total=dm, d_theta_deg=math.degrees(dth),
                        dW_J=float(W[i_centre + d] - W[i_centre - d]),
                        rel_dW=float(abs(W[i_centre + d] - W[i_centre - d])
                                     / max(abs(W[i_centre]), 1e-30)),
                        T_Nm=float((W[i_centre + d] - W[i_centre - d]) / dth)))
    return out


# --------------------------------------------------------------------------
# the flux-linkage cross-check (a DIFFERENT method, reported as such)
# --------------------------------------------------------------------------

def dq_torque(psi: Dict[str, float], I_ph: Dict[str, float], pole_pairs: int,
              n_parallel: int = 1) -> float:
    """``(3/2) p n_par (psi_alpha i_beta - psi_beta i_alpha)`` [N.m].

    Exactly the convention ``sb_postproc.hybrid_torque`` uses for the 2D mean,
    reproduced here so the 3D flux linkages can be read through the SAME formula.
    It is a cross-check and nothing more: instantaneously it is the winding-
    FILTERED torque and cannot see cogging, so it agrees with the co-energy
    derivative on the mean and not frame by frame.
    """
    s, kc = 2.0 / 3.0, math.sqrt(3.0) / 2.0
    pa, pb, pc = psi["A"], psi["B"], psi["C"]
    ia, ib, ic = I_ph["A"], I_ph["B"], I_ph["C"]
    p_al = s * (pa - 0.5 * pb - 0.5 * pc)
    p_be = s * kc * (pb - pc)
    i_al = s * (ia - 0.5 * ib - 0.5 * ic)
    i_be = s * kc * (ib - ic)
    return float(1.5 * float(pole_pairs) * float(max(1, int(n_parallel)))
                 * (p_al * i_be - p_be * i_al))
