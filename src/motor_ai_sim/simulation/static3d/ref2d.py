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
                                       n_theta: int = 720) -> Ref2D:
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
    """
    from motor_ai_sim.simulation.fem_solver_2d import (build_materials,
                                                       solve_magnetostatics_fem)
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout
    from skfem import MeshTri

    from .motor_mesh import mirror_to_full_ring

    t0 = time.perf_counter()
    full = mirror_to_full_ring(sect_sector, section_full)
    mesh = MeshTri(np.ascontiguousarray(full.p * 1e-3),
                   np.ascontiguousarray(full.t))
    tags = _dom_tags_for(section_full, full)

    geo = section_full.geo
    layout = build_winding_layout(section_full.num_slots,
                                  section_full.num_poles // 2)
    mats = build_materials({"A": 0.0, "B": 0.0, "C": 0.0}, layout,
                           section_full.polys_full, 0.0, 1e-5,
                           int(round(geo["num_wires_per_slot"])))
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
