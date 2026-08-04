"""The 2D answer, from the project's OWN 2D solver, for the honesty check.

k_flux only means something against a stated 2D baseline.  If this module
re-derived the 2D field with its own mesh, its own materials and its own weak
form, then "3D is 4 % below 2D" would be a statement about two of my own models
agreeing or not — worth nothing.  So the reference is produced by calling
``mesher.build_mesh_from_polygons`` + ``fem_solver_2d.build_materials`` +
``fem_solver_2d.solve_magnetostatics_fem`` in-process, read-only, on the SAME
polygons ``motor_geometry`` hands the 3D mesher, at I = 0.

It runs the FULL RING (n_sectors = 1).  The sector reduction is a property of
the model, not of the machine, and the 2D leg of a consistency check is the one
place to spend a few seconds not having it.

The comparable quantity is the FUNDAMENTAL of the radial flux density on the
mid-gap cylinder — pole-pair order p, so the harmonic index is p.  Peak B_r is
not comparable: it is a slot-harmonic artefact and moves with the mesh.

The one thing the two legs MUST share
-------------------------------------
Both must mean the same thing by "a magnet".  The law is

    B = mu0 * mu_rec * H + mu0 * M ,     remanence Br = mu0 * M ,

which is what ``static3d.solver`` integrates directly and what
``fem_solver_2d.build_materials`` means by ``Mx``/``My`` (``M = Br/mu0``) —
its own demag pass reads the full-strength remanence back as ``mu0*|M|``.
Feeding that law into the 2D A_z weak form gives a SOURCE of ``M/mu_rec``, the
equivalent coercivity Br/(mu0*mu_rec), not M.  Assembling M itself models a
magnet of remanence mu_rec*Br, i.e. 5 % strong for NdFeB — and because every
benchmark in ``exact.py`` was written at mu_r = 1, where the two conventions
coincide, nothing caught it until this comparison did.  Stage A's iron-free
case measured that offset as a flat 4.71 %, independent of stack length.
``tests/test_static3d_stage_a.py::
test_the_2d_reference_leg_uses_the_same_magnet_law_as_3d`` pins it.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .motor_geometry import MotorSection


@dataclass
class Ref2D:
    B1_T: float                  # |fundamental| of B_r on the mid-gap circle
    B1_phase_rad: float
    Br_theta: np.ndarray         # sampled B_r
    theta: np.ndarray
    n_tri: int
    ndofs: int
    wall_s: float
    r_gap_m: float
    pole_pairs: int
    outer_r_mm: float
    n_coil_elements_as_air: int = 0
    harmonics: Dict[int, float] = field(default_factory=dict)
    # -- how the nonlinear fixed point was found, and whether it WAS found ----
    relaxation: str = "fem_solver_2d (arithmetic in nu, damping 0.5)"
    picard_sweeps: int = 0
    picard_residual: Optional[float] = None    # constitutive residual in H
    picard_converged: Optional[bool] = None
    even_over_odd: Optional[float] = None      # anti-periodicity violation


def _grad_at_points_tri(mesh, basis, A: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """grad(A) at arbitrary (2, N) points — the genuine element shape functions,
    not a centroid stand-in (same construction as ``Solution.B_at_points``)."""
    mapping = mesh._mapping()
    finder = mesh.element_finder(mapping)
    tind = finder(pts[0], pts[1]).astype(np.int64)
    X = mapping.invF(pts[:, :, None], tind=tind)
    elem = basis.elem
    edofs = basis.element_dofs[:, tind]
    g = np.zeros((2, pts.shape[1]))
    for i in range(edofs.shape[0]):
        f = elem.gbasis(mapping, X, i, tind=tind)[0]
        g += f.grad[:, :, 0] * A[edofs[i]]
    return g


def gap_fundamental(theta: np.ndarray, Br: np.ndarray, p: int
                    ) -> Tuple[float, float]:
    """(amplitude, phase) of the order-p harmonic of B_r(theta).

    ``theta`` must be a uniform open sampling of a full 2*pi (or, for an
    anti-periodic half model, of pi — see ``end_effect``)."""
    n = theta.size
    c = (2.0 / n) * np.sum(Br * np.exp(-1j * p * theta))
    return float(abs(c)), float(np.angle(c))


def _dom_tags_for(section: MotorSection, sect) -> np.ndarray:
    """Per-triangle DOM_* tags for a static3d ``Section2D``.

    The magnet tag must be ``DOM_MAG_BASE + i`` with i the index in the FULL
    ring's ``polys['magnets']`` list, because that is the index
    ``build_materials`` keyed its per-magnet magnetisation on.  The region names
    on a full-ring MotorSection are ``magnet_<i>`` with exactly that i.
    """
    from motor_ai_sim.simulation.fem_solver_2d import (DOM_AIR, DOM_MAG_BASE,
                                                       DOM_ROTOR, DOM_SHAFT,
                                                       DOM_STATOR)
    tags = np.full(sect.t.shape[1], DOM_AIR, dtype=np.int32)
    simple = {"stator": DOM_STATOR, "rotor": DOM_ROTOR, "shaft": DOM_SHAFT}
    for name, rid in sect.names.items():
        sel = sect.tri_region == rid
        if name in simple:
            tags[sel] = simple[name]
        elif name.startswith("magnet_"):
            tags[sel] = DOM_MAG_BASE + int(name.split("_")[1])
    return tags


def solve_2d_reference_on_section_mesh(section_full: MotorSection,
                                       sect_sector,
                                       nonlinear_iterations: int = 16,
                                       element_order: int = 2,
                                       n_theta: int = 720,
                                       linear_iron_mu_r: Optional[float] = None
                                       ) -> Ref2D:
    """2D reference on the static3d cross-section mesh, mirrored to a full ring.

    Why not ``mesher.build_mesh_from_polygons`` (see ``solve_2d_reference``):
    on this machine that call is the project's documented closed-360 OCC
    pathology and did not complete in over an hour under the compute budget for
    this task.  What is actually needed for the honesty check is the project's
    2D SOLVER — its weak form in A_z, its materials, its B-H Picard — run on the
    same cross-section.  That is what this does; only the mesh generator differs,
    and a check whose two legs share a mesh generator is a weaker check, not a
    stronger one.

    ``sect_sector`` is a :class:`motor_mesh.Section2D` of the 180 deg sector;
    ``section_full`` must be the ``n_sectors=1`` MotorSection whose magnet
    numbering the tags key on.

    NONLINEAR CAVEAT: on a saturating machine the Picard inside
    ``solve_magnetostatics_fem`` does not reach a fixed point (measured: a
    constitutive residual of ~0.8 that no sweep count leaves, and a 3 %
    violation of an exact anti-periodicity).  Use
    :func:`solve_2d_reference_converged` for anything that has to be TRUE rather
    than merely produced by the shipped code path; this function stays because
    "what does the shipped 2D solver say" is its own question — the linear-iron
    ladder above, and the before/after in the passport.
    """
    from motor_ai_sim.simulation.fem_solver_2d import solve_magnetostatics_fem

    t0 = time.perf_counter()
    mesh, tags, mats, full = _ring_problem(section_full, sect_sector)
    if linear_iron_mu_r is not None:
        # A LINEAR-iron ladder: the same machine with the B-H curve replaced by a
        # fixed permeability, in BOTH models, so that "how does the 3D-vs-2D
        # residual depend on mu_r" is a question with one variable in it.  A
        # ladder run against nonlinear 2D would be measuring the Picard's
        # operating point as well, and the two effects are not separable
        # afterwards.
        import dataclasses
        mats = {k: dataclasses.replace(v, mu_r=float(linear_iron_mu_r),
                                       bh_curve=None)
                if (v.bh_curve or v.mu_r > 1.5) and not (v.Mx or v.My) else v
                for k, v in mats.items()}
        nonlinear_iterations = 1
    A, basis = solve_magnetostatics_fem(mesh, tags, mats,
                                        element_order=element_order,
                                        nonlinear_iterations=nonlinear_iterations)
    wall = time.perf_counter() - t0

    r = section_full.mid_r_m
    th = np.linspace(0.0, 2.0 * math.pi, int(n_theta), endpoint=False)
    g = _grad_at_points_tri(mesh, basis, A,
                            np.vstack([r * np.cos(th), r * np.sin(th)]))
    Br = g[1] * np.cos(th) - g[0] * np.sin(th)
    p = section_full.pole_pairs
    amp, ph = gap_fundamental(th, Br, p)
    return Ref2D(B1_T=amp, B1_phase_rad=ph, Br_theta=Br, theta=th,
                 n_tri=int(mesh.t.shape[1]), ndofs=int(basis.N), wall_s=wall,
                 r_gap_m=r, pole_pairs=p,
                 outer_r_mm=full.r_box_mm,
                 harmonics={int(k): gap_fundamental(th, Br, k)[0]
                            for k in (p, 3 * p, 5 * p)})


def _ring_problem(section_full: MotorSection, sect_sector):
    """(mesh, tags, materials) for the full-ring 2D problem — the pieces both 2D
    drivers below share, built exactly once and exactly the same way."""
    from motor_ai_sim.simulation.fem_solver_2d import build_materials
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout
    from skfem import MeshTri

    from .motor_mesh import mirror_to_full_ring

    full = mirror_to_full_ring(sect_sector, section_full)
    mesh = MeshTri(np.ascontiguousarray(full.p * 1e-3),
                   np.ascontiguousarray(full.t))
    tags = _dom_tags_for(section_full, full)
    layout = build_winding_layout(section_full.num_slots,
                                  section_full.num_poles // 2)
    mats = build_materials({"A": 0.0, "B": 0.0, "C": 0.0}, layout,
                           section_full.polys_full, 0.0, 1e-5,
                           int(round(section_full.geo["num_wires_per_slot"])))
    return mesh, tags, mats, full


def solve_2d_reference_converged(section_full: MotorSection,
                                 sect_sector,
                                 tol: float = 3e-3,
                                 max_sweeps: int = 140,
                                 damping: float = 0.35,
                                 n_theta: int = 720,
                                 element_order: int = 2) -> Ref2D:
    """The 2D reference at a CONVERGED nonlinear fixed point.

    Why this exists
    ---------------
    ``solve_magnetostatics_fem`` relaxes its B-H Picard arithmetically in nu with
    a fixed 0.5 and stops when the STEP is small.  On this spoke rotor that loop
    does not converge: measured on the 40 mm machine, at 16 / 40 / 80 / 160
    sweeps the constitutive residual — how far the returned state violates the
    B-H curve it was handed, ``|| |B|(nu_curve - nu_used) || / || |B| nu_curve ||``
    — sits at 0.87 / 0.79 / 0.84 / 0.79.  Not 1 % out: 80 % out, in a limit cycle
    that no sweep count leaves.  Two independent symptoms confirm it.  The gap
    fundamental wanders over a 3 % band with no trend, and the returned field
    breaks the machine's EXACT anti-periodicity (7 poles per 180 deg sector) by
    3 % — the even harmonics of B_r, which must be zero, are 3 % of the odd ones.
    A monotone magnetostatic problem has a unique solution, so that is not a
    solution.

    The same weak form, same mesh, same materials, relaxed GEOMETRICALLY in
    log(mu) with an adaptive step and stopped on the constitutive residual — the
    loop ``static3d.solver.solve_static3d_nonlinear`` already uses — converges to
    3e-3 in ~50 sweeps, restores the anti-periodicity to 0.03 %, is stable to
    0.1 % across four cross-sections from 4 000 to 12 800 triangles, and lands
    12 % HIGHER: 1.515 T against 1.354-1.385 T.  That 12 % was the whole of the
    "3D is 8 % above 2D" disagreement this module's honesty check kept failing on.

    Only the relaxation differs.  The weak form assembled here is
    ``solve_magnetostatics_fem``'s, term for term, and
    ``tests/test_static3d_stage_a.py::test_the_converged_2d_leg_is_the_shipped_
    weak_form`` pins that: at equal permeability the two return the same B1 to
    the last bit.  The fix belongs in ``fem_solver_2d`` itself — this module may
    not edit it, so it carries its own loop and says so.
    """
    from skfem import (Basis, BilinearForm, ElementTriP0, ElementTriP1,
                       ElementTriP2, LinearForm, asm, condense, solve)
    from skfem.helpers import dot, grad

    from motor_ai_sim.simulation.fem_solver_2d import (DOM_ROTOR, DOM_SHAFT,
                                                       DOM_STATOR)
    from motor_ai_sim.simulation.field_ops import (MU0, _mu_r_from_bh_vec,
                                                   _p2_B_at_quad)

    t0 = time.perf_counter()
    mesh, tags, mats, full = _ring_problem(section_full, sect_sector)
    elem = ElementTriP2() if int(element_order) == 2 else ElementTriP1()
    basis = Basis(mesh, elem)
    b0 = basis.with_element(ElementTriP0())

    @BilinearForm
    def _stiff(u, v, w):
        return w["nu"] * dot(grad(u), grad(v))

    @LinearForm
    def _dvdy(v, w):
        return grad(v)[1]

    @LinearForm
    def _dvdx(v, w):
        return grad(v)[0]

    # source: H_c = M / mu_rec, the equivalent coercivity (see the docstring)
    f_src = np.zeros(basis.N)
    for tag in np.unique(tags):
        m = mats.get(int(tag))
        if m is None or (abs(m.Mx) == 0.0 and abs(m.My) == 0.0):
            continue
        idx = np.where(tags == tag)[0]
        if idx.size == 0:
            continue
        sub = Basis(mesh, elem, elements=idx)
        if abs(m.Mx) > 0:
            f_src += asm(_dvdy, sub) * (m.Mx / max(m.mu_r, 1.0))
        if abs(m.My) > 0:
            f_src -= asm(_dvdx, sub) * (m.My / max(m.mu_r, 1.0))
    rmax = float(np.sqrt(mesh.p[0] ** 2 + mesh.p[1] ** 2).max())
    D = basis.get_dofs(facets=mesh.facets_satisfying(
        lambda x: np.sqrt(x[0] ** 2 + x[1] ** 2) >= rmax - 5e-4))

    def _linear(nu):
        nf = b0.zeros()
        nf[:] = nu
        K = asm(_stiff, basis, nu=b0.interpolate(nf)).tocsr()
        return solve(*condense(K, f_src, D=D))

    nl_tags = [t for t in (DOM_STATOR, DOM_ROTOR, DOM_SHAFT)
               if (tags == t).any() and mats.get(t) is not None
               and mats[t].bh_curve and len(mats[t].bh_curve) >= 2]
    nu = np.empty(mesh.t.shape[1])
    for tag in np.unique(tags):
        m = mats.get(int(tag))
        nu[tags == tag] = 1.0 / (MU0 * max(m.mu_r if m else 1.0, 1.0))

    A = _linear(nu)
    d_cur, good, prev, sweeps, resid = float(damping), 0, float("inf"), 0, None
    converged = False
    for it in range(int(max_sweeps)):
        sweeps = it + 1
        Bx, By, dx = _p2_B_at_quad(basis, A)
        area = dx.sum(axis=1)
        Bm = ((np.sqrt(Bx ** 2 + By ** 2) * dx).sum(axis=1)
              / np.maximum(area, 1e-30))
        nu_new = nu.copy()
        num = den = 0.0
        for tag in nl_tags:
            idx = np.where(tags == tag)[0]
            nu_c = 1.0 / (MU0 * np.maximum(
                _mu_r_from_bh_vec(mats[tag].bh_curve, Bm[idx]), 1.0))
            nu_new[idx] = nu_c
            h_c, h_u = Bm[idx] * nu_c, Bm[idx] * nu[idx]
            num += float((((h_c - h_u) ** 2) * area[idx]).sum())
            den += float(((h_c ** 2) * area[idx]).sum())
        resid = math.sqrt(num / max(den, 1e-300))
        if resid < tol:
            converged = True
            break
        if resid > prev:                       # overshot — back the step off
            d_cur, good = max(0.5 * d_cur, 0.02), 0
        else:
            good += 1
            if good >= 3:
                d_cur, good = min(1.6 * d_cur, float(damping)), 0
        prev = resid
        nu = np.exp((1.0 - d_cur) * np.log(nu) + d_cur * np.log(nu_new))
        A = _linear(nu)

    r = section_full.mid_r_m
    th = np.linspace(0.0, 2.0 * math.pi, int(n_theta), endpoint=False)
    g = _grad_at_points_tri(mesh, basis, A,
                            np.vstack([r * np.cos(th), r * np.sin(th)]))
    Br = g[1] * np.cos(th) - g[0] * np.sin(th)
    p = section_full.pole_pairs
    amp, ph = gap_fundamental(th, Br, p)
    # the anti-periodicity the machine HAS: B_r(theta+pi) = -B_r(theta), so every
    # even harmonic is a pure numerical artefact and their norm is a free error
    # bar on the whole solution
    c = np.abs(np.fft.rfft(Br)) * (2.0 / int(n_theta))
    ev = float(np.sqrt((c[2::2] ** 2).sum())
               / max(np.sqrt((c[1::2] ** 2).sum()), 1e-30))
    return Ref2D(B1_T=amp, B1_phase_rad=ph, Br_theta=Br, theta=th,
                 n_tri=int(mesh.t.shape[1]), ndofs=int(basis.N),
                 wall_s=time.perf_counter() - t0, r_gap_m=r, pole_pairs=p,
                 outer_r_mm=full.r_box_mm,
                 harmonics={int(k): gap_fundamental(th, Br, k)[0]
                            for k in (p, 3 * p, 5 * p)},
                 relaxation="geometric in log(mu), adaptive, constitutive-residual stop",
                 picard_sweeps=int(sweeps),
                 picard_residual=None if resid is None else float(resid),
                 picard_converged=bool(converged), even_over_odd=ev)


def solve_2d_reference(section: MotorSection,
                       n_theta: int = 720,
                       mesh_size_mm: Optional[float] = None,
                       min_size_mm: float = 0.15,
                       gap_layers: float = 3.0,
                       outer_air_factor: Optional[float] = None,
                       nonlinear_iterations: int = 14,
                       element_order: int = 2) -> Ref2D:
    """One magnetostatic 2D frame at I = 0, full ring, P2 + the same B-H Picard."""
    from motor_ai_sim.simulation.fem_solver_2d import (build_materials,
                                                       solve_magnetostatics_fem)
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout
    from motor_ai_sim.simulation.mesher import build_mesh_from_polygons

    polys = section.polys_full
    if polys is None:
        raise ValueError("MotorSection carries no full-ring polygons")
    geo = section.geo
    try:
        from motor_ai_sim.config import get_config
        mcfg = dict((get_config() or {}).get("mesh", {}) or {})
    except Exception:
        mcfg = {}
    ms = float(mesh_size_mm if mesh_size_mm else mcfg.get("mesh_size_mm", 0.6))
    oaf = float(outer_air_factor if outer_air_factor
                else mcfg.get("outer_air_factor", 1.2))

    t0 = time.perf_counter()
    mesh, tags, _classify = build_mesh_from_polygons(
        polys, rotor_angle_deg=0.0, mesh_size_mm=ms, min_size_mm=min_size_mm,
        normal_deviation_deg=float(mcfg.get("normal_deviation", 8.0)),
        geo_cfg=geo, outer_air_factor=oaf, gap_layers=gap_layers, n_sectors=1)
    layout = build_winding_layout(section.num_slots, section.num_poles // 2)
    mats = build_materials({"A": 0.0, "B": 0.0, "C": 0.0}, layout, polys,
                           0.0, 1e-5, int(round(geo["num_wires_per_slot"])))
    # Collapse every COIL / insulation tag onto plain air before solving.
    #
    # Two reasons, and neither is a shortcut.  Physically, at I = 0 copper and
    # insulation are mu_r = 1 with no source: they ARE air, and the 3D model does
    # not mesh them as anything else, so collapsing them here is what makes the
    # two models solve the same material distribution.  Practically,
    # solve_magnetostatics_fem builds one sub-Basis per distinct tag, and this
    # winding emits ~170 per-wire coil polygons; on the full-ring mesh that setup
    # alone ran past an hour before the first Picard sweep.
    from motor_ai_sim.simulation.fem_solver_2d import (DOM_AIR, DOM_COIL,
                                                       DOM_COIL_BASE)
    tags = np.asarray(tags).copy()
    coil = (tags == DOM_COIL) | (tags >= DOM_COIL_BASE)
    tags[coil] = DOM_AIR
    n_coil = int(coil.sum())

    A, basis = solve_magnetostatics_fem(mesh, tags, mats,
                                        element_order=element_order,
                                        nonlinear_iterations=nonlinear_iterations)
    wall = time.perf_counter() - t0

    r = section.mid_r_m
    th = np.linspace(0.0, 2.0 * math.pi, int(n_theta), endpoint=False)
    pts = np.vstack([r * np.cos(th), r * np.sin(th)])
    g = _grad_at_points_tri(mesh, basis, A, pts)
    # B = (dA/dy, -dA/dx);  B_r = B.e_r
    Bx, By = g[1], -g[0]
    Br = Bx * np.cos(th) + By * np.sin(th)

    p = section.pole_pairs
    amp, ph = gap_fundamental(th, Br, p)
    harm = {}
    for k in (p, 3 * p, 5 * p):
        harm[int(k)] = gap_fundamental(th, Br, k)[0]
    return Ref2D(B1_T=amp, B1_phase_rad=ph, Br_theta=Br, theta=th,
                 n_tri=int(mesh.t.shape[1]), ndofs=int(basis.N), wall_s=wall,
                 n_coil_elements_as_air=n_coil,
                 r_gap_m=r, pole_pairs=p,
                 outer_r_mm=oaf * section.r_stator_out_mm, harmonics=harm)
