"""Stage B: the machine UNDER LOAD in 3D — closed winding, laminated iron.

What this module is allowed to claim
------------------------------------
It solves the 40 mm sector in the A-formulation with a CLOSED winding
(``winding3d``) and transverse-isotropic iron (``nedelec``'s diagonal nu), at
one rotor position.  From one rotor position you can honestly read:

  * the flux linkage of each phase, and therefore the inductance the 3D model
    sees that a 2D model cannot — the END-WINDING leakage;
  * the demagnetising field inside the magnets, slice by slice, UNDER LOAD;
  * what a loaded solve costs.

What it still does NOT do is torque.  Torque by the energy method is
dW'/d(theta) and needs a second rotor position on a mesh whose connectivity has
not changed; that is the sliding band, and it now exists — in ``band`` (the
three-piece cross-section and the signed edge weld) and ``torque3d`` (the
co-energy and the central difference).  This module still offers no torque
function, and deliberately: it solves ONE rotor position and a torque read from
one position would be a Maxwell-stress integral, which this project does not
allow.  Use ``torque3d.BandedModel``, which returns the same
:class:`LoadedSolve` so every post-processor below applies unchanged.

Flux linkage without a surface to integrate over
------------------------------------------------
The usual 3D flux linkage is a nuisance: pick a surface bounded by the coil,
orient it, integrate B.n.  With a source field it is an identity instead.  For a
winding whose unit-current source field is T_1 (so that curl(T_1) = J at 1 A),

    lambda  =  integral( J_1 . A )  =  integral( curl(T_1) . A )
            =  integral( T_1 . curl(A) )  =  integral( T_1 . B )

— the middle step is exact because T_1 has compact support, so the divergence
term integrates to nothing.  No surface, no orientation, no cutting.  And it is
exact on the DISCRETE solution too, because it is the same bilinear pairing the
load was assembled with.

Units: SI.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .motor_geometry import MM, MotorSection
from .motor_mesh import Section2D, build_motor_mesh
from .nedelec import (ASolution, mirror_plane_dofs, solve_static3d_A,
                      solve_static3d_A_nonlinear, truncation_dofs)
from .periodic import Periodic, edge_dof_pairs
from .solver import MU0, Region
from .winding3d import WindingT, build_winding_T, phase_currents

PHASES = ("A", "B", "C")


# --------------------------------------------------------------------------
# one loaded solve
# --------------------------------------------------------------------------

@dataclass
class LoadedSolve:
    sol: ASolution
    tm: object
    section: MotorSection
    stack_mm: float
    basis: object
    periodic: Optional[Periodic]
    dirichlet: np.ndarray
    winding: Optional[WindingT]
    I_ph: Dict[str, float]
    wall_s: float
    meta: dict = field(default_factory=dict)

    @property
    def stack_half_m(self) -> float:
        return 0.5 * self.stack_mm * MM


def _regions_for(tm, section: MotorSection, linear_iron: bool = False,
                 laminated_iron: bool = True,
                 magnets_off: bool = False) -> List[Region]:
    """The same region list Stage A builds, with one extra switch.

    ``magnets_off`` zeroes the magnetisation while keeping the recoil mu_r — the
    machine with its magnets replaced by their own permeability.  That is what
    separates the winding's OWN flux linkage (and therefore its inductance) from
    the magnet's, and it is a switch rather than a subtraction because on
    saturating iron the two do not superpose.
    """
    regs: List[Region] = []
    for r in section.regions:
        el = tm.elements(r.name)
        if el.size == 0:
            raise ValueError(f"region {r.name!r} has no elements in the 3D mesh")
        regs.append(Region(
            name=r.name, elements=el, mu_r=r.mu_r,
            M=((0.0, 0.0, 0.0) if magnets_off else r.M),
            bh_curve=None if linear_iron else r.bh_curve,
            stack_kf=float(r.stack_kf) if laminated_iron else 1.0))
    air = tm.elements("air")
    if air.size == 0:
        raise ValueError("no air elements — the mesh has no field region")
    regs.append(Region(name="air", elements=air, mu_r=1.0))
    return regs


def solve_sector_loaded(section: MotorSection,
                        sect2d: Section2D,
                        I_ph: Optional[Dict[str, float]] = None,
                        stack_mm: Optional[float] = None,
                        n_stack: int = 8,
                        n_cap: int = 10,
                        h_ew_mm: Optional[float] = None,
                        n_ew: int = 4,
                        ew_shape: str = "cosine",
                        tol: float = 3e-3,
                        max_iter: int = 45,
                        damping: float = 0.35,
                        flux_tight_outer: bool = True,
                        linear_iron: bool = False,
                        laminated_iron: bool = True,
                        magnets_off: bool = False,
                        mu_init: Optional[np.ndarray] = None,
                        cg_tol: float = 1e-10,
                        winding: Optional[WindingT] = None,
                        verbose: bool = False) -> LoadedSolve:
    """One loaded 3D solve of the sector.

    Boundary conditions are Stage A's A-formulation ones
    (``end_effect.solve_sector_A``) and are correct for the LOADED problem for
    the reason spelled out in ``winding3d``: the slot current is even in z and
    the end-turn current is odd in z, so the whole closed loop still has
    B_z = 0 on the mirror plane, and the mirror plane is still Dirichlet here.

    ``I_ph = None`` solves the no-load machine through exactly this code path
    (no winding is built), which is what makes an I = 0 loaded solve comparable
    with Stage A rather than merely similar to it.
    """
    from skfem import Basis
    from skfem.element import ElementTetN0

    t0 = time.perf_counter()
    if h_ew_mm is None and I_ph is not None:
        h_ew_mm = (0.5 * float(section.geo["tooth_width"])
                   + 0.5 * float(section.geo["wire_width"]))
    tm, _ = build_motor_mesh(section, stack_mm=stack_mm, sect=sect2d,
                             n_stack=n_stack, n_cap=n_cap,
                             h_ew_mm=h_ew_mm, n_ew=n_ew)
    regs = _regions_for(tm, section, linear_iron=linear_iron,
                        laminated_iron=laminated_iron, magnets_off=magnets_off)

    wt = winding
    if I_ph is not None and wt is None:
        wt = build_winding_T(section, I_ph, stack_mm=stack_mm,
                             h_ew_mm=h_ew_mm, ew_shape=ew_shape)

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
              cg_tol=cg_tol, T=(wt.field() if wt is not None else None))
    if linear_iron:
        sol = solve_static3d_A(tm.mesh, regs, **kw)
    else:
        sol = solve_static3d_A_nonlinear(tm.mesh, regs, tol=tol,
                                         max_iter=max_iter, damping=damping,
                                         verbose=verbose, mu_init=mu_init, **kw)
    return LoadedSolve(
        sol=sol, tm=tm, section=section,
        stack_mm=float(stack_mm if stack_mm else section.stack_mm),
        basis=basis, periodic=per, dirichlet=D, winding=wt,
        I_ph=dict(I_ph or {}), wall_s=time.perf_counter() - t0,
        meta=dict(laminated_iron=bool(laminated_iron),
                  linear_iron=bool(linear_iron),
                  magnets_off=bool(magnets_off),
                  h_ew_mm=(None if h_ew_mm is None else float(h_ew_mm)),
                  n_ew=int(n_ew) if h_ew_mm else 0, ew_shape=str(ew_shape),
                  n_tets=int(tm.mesh.t.shape[1]), ndofs=int(sol.ndofs),
                  z_levels_mm=list(tm.meta["z_levels_mm"])))


# --------------------------------------------------------------------------
# flux linkage and the end-winding leakage
# --------------------------------------------------------------------------

def unit_phase_windings(section: MotorSection, stack_mm: Optional[float] = None,
                        h_ew_mm: Optional[float] = None,
                        ew_shape: str = "cosine") -> Dict[str, WindingT]:
    """One source field per phase at 1 A of COIL current, the others at zero.

    These are the ``T_1`` of the module docstring.  Built once and reused: they
    depend on the geometry and the layout, not on the operating point."""
    out: Dict[str, WindingT] = {}
    for ph in PHASES:
        I = {p: (1.0 if p == ph else 0.0) for p in PHASES}
        out[ph] = build_winding_T(section, I, stack_mm=stack_mm,
                                  h_ew_mm=h_ew_mm, ew_shape=ew_shape)
    return out


def flux_linkage(ls: LoadedSolve, units: Dict[str, WindingT]) -> Dict[str, float]:
    """``lambda_phase = integral(T_1 . B) dV`` over the WHOLE machine [Wb].

    Two multipliers, and both are properties of the model rather than fudge
    factors, so they are stated here once:

    * ``n_sectors`` — on an anti-periodic sector both T_1 and B change sign
      together, so their PRODUCT is periodic and the sectors add.  (Worth saying
      out loud: the same sector carries plenty of anti-periodic quantities whose
      plain integrals cancel instead.)
    * ``2`` for the axial mirror — the mesh is the half machine z >= 0.  T_1 is
      radial with an even beta(|z|), and B_r is even in z on a mirror-symmetric
      machine, so the missing half contributes exactly the same again.

    The integral uses the SAME quadrature the load was assembled with, so this
    is the discrete pairing rather than a re-integration of it: an elementwise
    midpoint rule would be a first-order approximation to a quantity that is
    available exactly.
    """
    sol = ls.sol
    basis = sol.basis
    Bq = basis.interpolate(sol.A).curl               # (3, nelem, nqp)
    dx = basis.dx                                    # (nelem, nqp)
    xq = np.asarray(basis.global_coordinates())
    k = 2.0 * max(int(ls.section.n_sectors), 1)
    out: Dict[str, float] = {}
    for ph, wt in units.items():
        Tq = wt.field()(xq)                          # (3, nelem, nqp)
        out[ph] = float(((Tq * Bq).sum(axis=0) * dx).sum() * k)
    return out


def flux_linkage_axial_split(ls: LoadedSolve, units: Dict[str, WindingT]
                             ) -> Dict[str, dict]:
    """lambda split at the end of the stack: what 2D can represent, and what it cannot.

    A 2D model is the cross-section times a length.  Everything it can ever say
    about flux linkage lives in ``|z| <= L/2`` and is uniform along z; every
    tesla outside that plane — the axial fringing of the stack's own field AND
    the field of the end turns themselves — is invisible to it by construction.
    Splitting the SAME integral at that plane is therefore the honest measure of
    the 3D-only part, and it needs no second model to compare against.

    Elements are assigned to a side by their centroid; the stack end is a MESH
    PLANE (``motor_mesh.axial_levels`` puts a level exactly on it), so no
    element straddles it and the split is exact rather than approximate.
    """
    sol = ls.sol
    basis = sol.basis
    Bq = basis.interpolate(sol.A).curl
    dx = basis.dx
    xq = np.asarray(basis.global_coordinates())
    zc = ls.tm.mesh.p[2, ls.tm.mesh.t].mean(axis=0)
    inside = zc <= ls.stack_half_m + 1e-12
    k = 2.0 * max(int(ls.section.n_sectors), 1)
    out: Dict[str, dict] = {}
    for ph, wt in units.items():
        w = ((wt.field()(xq) * Bq).sum(axis=0) * dx).sum(axis=1)   # (nelem,)
        tot = float(w.sum()) * k
        stk = float(w[inside].sum()) * k
        out[ph] = dict(total_Wb=tot, stack_Wb=stk, end_Wb=tot - stk,
                       end_fraction=(tot - stk) / tot if tot else float("nan"))
    return out


def inductance_matrix(section: MotorSection, sect2d: Section2D,
                      i_test: float = 1.0,
                      stack_mm: Optional[float] = None,
                      h_ew_mm: Optional[float] = None,
                      ew_shape: str = "cosine",
                      linear_iron: bool = True,
                      **kw) -> dict:
    """The 3x3 phase inductance matrix, magnets OFF, one solve per phase.

    LINEAR iron by default and deliberately so: an inductance is a derivative,
    and on saturating iron ``lambda / i`` is not one.  A nonlinear number would
    be an operating-point secant masquerading as an inductance, and the quantity
    this exists to produce — how much of the linkage the END TURNS add, which 2D
    cannot see at all — has to be a property of the geometry, not of how hard
    the machine happens to be pushed.
    """
    units = unit_phase_windings(section, stack_mm=stack_mm, h_ew_mm=h_ew_mm,
                                ew_shape=ew_shape)
    L = np.zeros((3, 3))
    cost = []
    for j, drive in enumerate(PHASES):
        I = {p: (float(i_test) if p == drive else 0.0) for p in PHASES}
        ls = solve_sector_loaded(section, sect2d, I_ph=I, stack_mm=stack_mm,
                                 h_ew_mm=h_ew_mm, ew_shape=ew_shape,
                                 linear_iron=linear_iron, magnets_off=True,
                                 **kw)
        lam = flux_linkage(ls, units)
        for i, ph in enumerate(PHASES):
            L[i, j] = lam[ph] / float(i_test)
        cost.append(dict(phase=drive, wall_s=ls.wall_s, ndofs=ls.sol.ndofs,
                         iterations=ls.sol.iterations,
                         source_residual=ls.sol.source_residual,
                         source_removed=ls.sol.source_removed_norm))
    return dict(L=L, phases=list(PHASES), i_test=float(i_test),
                h_ew_mm=(None if h_ew_mm is None else float(h_ew_mm)),
                ew_shape=str(ew_shape), linear_iron=bool(linear_iron),
                cost=cost,
                L_self_mean_H=float(np.mean(np.diag(L))),
                L_mutual_mean_H=float((L.sum() - np.trace(L)) / 6.0),
                symmetry_error=float(np.max(np.abs(L - L.T))
                                     / max(np.max(np.abs(L)), 1e-30)))


# --------------------------------------------------------------------------
# demagnetisation under load
# --------------------------------------------------------------------------

def demag_under_load(ls: LoadedSolve, n_slices: int = 6) -> dict:
    """``H . M_hat`` inside the magnets, per axial slice, on a LOADED solve.

    The A-formulation twin of ``end_effect.demag_exposure`` and deliberately the
    same reduction, so a loaded slice profile can be laid next to Stage A's
    I = 0 one without a second post-processor in between.  H is read through the
    DIAGONAL reluctivity (``ASolution.H_elementwise``); the magnets are not
    laminated, so for them that is the scalar value — but reading it through the
    solution object is what keeps it true if that ever changes.
    """
    sol = ls.sol
    tm = ls.tm
    H = sol.H_elementwise()
    vol = sol.basis.dx.sum(axis=1)
    zc = tm.mesh.p[2, tm.mesh.t].mean(axis=0)
    zh = ls.stack_half_m

    edges = np.linspace(0.0, zh, int(n_slices) + 1)
    slices: List[dict] = []
    for k in range(int(n_slices)):
        lo, hi = edges[k], edges[k + 1]
        worst = math.inf
        num = den = 0.0
        for r in ls.section.magnet_regions():
            el = tm.elements(r.name)
            m = np.asarray(r.M, dtype=float)
            n = float(np.linalg.norm(m))
            if n <= 0.0:
                continue
            mhat = m / n
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
    return dict(slices=slices,
                mid_worst_H=mid["worst_H_A_per_m"],
                end_worst_H=end["worst_H_A_per_m"],
                mid_mean_H=mid["mean_H_A_per_m"],
                end_mean_H=end["mean_H_A_per_m"],
                worst_shift_A_per_m=end["worst_H_A_per_m"] - mid["worst_H_A_per_m"],
                mean_shift_A_per_m=end["mean_H_A_per_m"] - mid["mean_H_A_per_m"],
                I_ph={k: float(v) for k, v in ls.I_ph.items()},
                note=("negative H.Mhat is demagnetising.  At I = 0 the end "
                      "slice is LESS negative than the mid-plane one (the end "
                      "flux spills into air instead of being pushed back "
                      "through the magnet); under load the armature field adds "
                      "to that, and the two do not have to have the same sign"))


# --------------------------------------------------------------------------
# what a loaded solve costs
# --------------------------------------------------------------------------

STAGE_B_VERSION = "static3d-stageB-1"

#: The ``stage_b`` block written into ``config/end_effect_3d.json``.  Documented
#: here rather than in the JSON because the JSON is generated and a schema that
#: lives only in its own output cannot be checked against anything.
STAGE_B_SCHEMA_DOC = """\
stage_b
  version              str    schema tag, ``static3d-stageB-1``
  generated_utc        str
  machine              dict   preset name, geometry fingerprint, MATERIALS
                              ACTUALLY USED (Stage A's, pinned in-process)
  operating_point      dict   I_phase_rms, connection, n_parallel,
                              i_coil_peak, gamma_deg, daxis_deg, rpm,
                              rotor_angle_deg, and the per-phase currents
  winding_model        dict   how the coil was closed: formulation ("source
                              field T, curl T = J"), h_ew_mm and the rule it
                              came from, ew_shape, grid, the CLOSURE residual
                              Psi(sector)/scale, the ampere-turn audit
                              (intended vs delivered per slot, worst %)
  load_consistency     dict   |G^T f|/|f| and the norm the Helmholtz
                              projection REMOVED, for the T source and for a
                              J source on the same benchmark — the whole
                              point of blocker 1, as two numbers
  winding_validation   dict   the T path against exact.thick_solenoid_axis_Bz,
                              per mesh: tets, mid error, L2 error
  iron_anisotropy      dict   the k_f = 1 identity, the in-plane/axial
                              signature, and the scalar-vs-A lamination-ratio
                              agreement
  end_winding_leakage  dict   rows over h_ew and shape: L_self, L_stack,
                              L_end, end fraction, mutuals — the split of the
                              SAME integral at the stack end plane
  demag_under_load     dict   per-slice H.M_hat, end vs mid, plus the I = 0
                              Stage A numbers for contrast
  cost                 list   one row per solve: tets, dofs, CG iterations,
                              Picard iterations/residual, assemble/solve/wall
  torque               dict   the energy-method result and what it rests on:
                              the banded mesh and its ring pitch, the co-energy
                              at every rotor shift solved, the DELTA SWEEP (T vs
                              the angular step, showing the plateau between
                              cancellation noise and curvature bias), the 2D leg
                              it is divided by, and k_T.  This key must never
                              carry a number the sliding band has not earned:
                              while a proof was open it carried the REASON
                              instead, and it may do so again.
  warm_start           dict   the measured cost of a nonlinear rotor position
                              with and without carrying the previous angle's
                              permeability in — sweeps, residual and wall time
                              for each, from the SAME process and load
  blockers             dict   one entry per Stage B blocker: status, the
                              evidence that closed it, or the design and the
                              measurement that is missing
"""



def cost_row(ls: LoadedSolve, label: str) -> dict:
    """One line of the cost table: dofs, iterations, wall time, consistency."""
    p = ls.sol.picard or {}
    return dict(label=str(label),
                tets=int(ls.tm.mesh.t.shape[1]), ndofs=int(ls.sol.ndofs),
                cg_iterations=ls.sol.iterations,
                picard_iterations=p.get("iterations"),
                picard_converged=p.get("converged"),
                picard_residual=(p.get("history") or [None])[-1],
                assemble_s=float(ls.sol.assemble_time),
                solve_s=float(ls.sol.solve_time),
                wall_s=float(ls.wall_s),
                source_residual=float(ls.sol.source_residual),
                source_removed_norm=float(ls.sol.source_removed_norm),
                load_norm=float(ls.sol.load_norm))
