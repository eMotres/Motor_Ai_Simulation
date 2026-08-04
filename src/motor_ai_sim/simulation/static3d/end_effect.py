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
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_trapz = getattr(np, "trapezoid", None) or np.trapz

from .motor_geometry import MM, MotorSection, load_motor_section
from .motor_mesh import Section2D, build_motor_mesh, build_section_mesh_2d
from .periodic import Periodic, dof_pairs, outer_boundary_dofs
from .ref2d import gap_fundamental
from .solver import MU0, Region, Solution, solve_static3d, solve_static3d_nonlinear

PASSPORT_VERSION = "static3d-stageA-1"


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
                 verbose: bool = False) -> SectorSolve:
    """Mesh + solve one stack length on a prebuilt cross-section."""
    from skfem import Basis, ElementTetP1, ElementTetP2

    t0 = time.perf_counter()
    tm, _ = build_motor_mesh(section, stack_mm=stack_mm, sect=sect2d,
                             n_stack=n_stack, n_cap=n_cap)
    regs = _regions_for(tm, section, linear_iron=linear_iron,
                        iron_mu_r=iron_mu_r, laminated_iron=laminated_iron)
    linear_iron = linear_iron or iron_mu_r is not None
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
    if B1_mid <= 0:
        raise RuntimeError("mid-plane gap fundamental is zero — no field")
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
        "two_d": "the 2D reference: solver, mesh, B1 and the 3D-mid/2D ratio.",
        "demag": "H.Mhat per axial magnet slice; negative is demagnetising.",
        "l_stack_curve": "k_flux (both normalisations) vs stack length.",
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
                h_gap: float = 0.28,
                h_solid: float = 0.85,
                order: int = 2,
                n_stack: int = 8,
                n_cap: int = 10,
                tol: float = 3e-3,
                max_iter: int = 45,
                l_factors: Sequence[float] = (0.5, 0.75, 1.0, 1.5, 2.0),
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
    section = load_motor_section(geo_override=geo_override, n_sectors=n_sectors)
    if verbose:
        print(section.summary(), flush=True)

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
                      laminated_iron=laminated_iron,
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
                           mu_init=mu_ref, laminated_iron=laminated_iron,
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
                                laminated_iron=laminated_iron,
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
                     ratio_3d_mid_over_2d=prof["B1_mid_T"] / r2.B1_T)
        if verbose:
            print(f"    2D B1 = {r2.B1_T:.5f} T (converged, resid "
                  f"{r2.picard_residual:.1e}, {r2.picard_sweeps} sweeps; "
                  f"shipped relaxation says {r2s.B1_T:.5f} T), "
                  f"3D mid / 2D = {two_d['ratio_3d_mid_over_2d']:.4f}",
                  flush=True)
    ratio = two_d["ratio_3d_mid_over_2d"] if two_d else 1.0
    k_vs2d = k_self * ratio
    if two_d is not None:
        # The consistency check is only passed if the two models agree at the
        # mid-plane.  When they do not, k_flux (which multiplies a 2D result by
        # a number derived from BOTH models) inherits the disagreement, and
        # publishing it would dress a modelling difference up as an end effect.
        # k_flux_self — a ratio taken entirely inside the 3D model — does not.
        off = abs(ratio - 1.0)
        two_d["agreement_tolerance"] = 0.02
        two_d["verdict"] = "PASS" if off <= 0.02 else "FAIL"
        two_d["k_flux_usable"] = bool(off <= 0.02)
        if off > 0.02:
            two_d["warning"] = (
                f"3D mid-plane and 2D differ by {100*off:.2f} % on the same "
                "cross-section. Use k_flux_self and the spill profile; do NOT "
                "use k_flux until the two models are reconciled. First two "
                "things to check, in order: (1) the magnet constitutive law — "
                "the 2D A_z source must be M/mu_rec, not M (see ref2d's "
                "docstring); (2) the iron, by re-running this with "
                "linear_iron=True at a ladder of mu_r: a residual that grows "
                "with mu_r and vanishes with no iron is the total scalar "
                "potential's cancellation, not a magnet error.")
    for row in curve:
        row["k_flux"] = row["k_flux_self"] * ratio

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
        spill_profile=dict(
            z_m=prof["z_m"], z_over_half_stack=prof["z_over_half"],
            B1_T=prof["B1_T"], B1_normalised=prof["profile"],
            B1_mid_T=prof["B1_mid_T"], stack_half_m=prof["stack_half_m"]),
        two_d=two_d,
        demag=demag,
        l_stack_curve=curve,
        bracket=bracket,
        solves=solves,
        total_wall_s=time.perf_counter() - t_all,
    )
    return passport
