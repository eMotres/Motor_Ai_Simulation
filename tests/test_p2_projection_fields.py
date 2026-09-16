"""Independent P2 Laplace oracle on two matched polygonal annular sectors.

The exact harmonic Re(z**k) supplies zero volume source and radial-boundary
values. Rotor coordinates are rotated physically for both values and gradients.
No motor fixture, material model, or previously reported physics pin is used.
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy.sparse.linalg import spsolve
from skfem import Basis, ElementTriP2, MeshTri, asm
from skfem.models.poisson import laplace

from motor_ai_sim.simulation.p2_projection import SlipProjection


ALPHA = np.pi / 2.0


def _sector(radii, angles):
    points = np.array([[r * np.cos(a), r * np.sin(a)]
                       for r in radii for a in angles]).T
    width = len(angles)
    triangles = []
    for row in range(len(radii) - 1):
        for column in range(width - 1):
            a = row * width + column
            triangles.extend([(a, a + width, a + width + 1),
                              (a, a + width + 1, a + 1)])
    return points, np.asarray(triangles, int).T


def _problem(segments):
    nr = segments // 4
    angles = np.linspace(0.0, ALPHA, segments + 1)
    sr = np.linspace(1.0, 1.4, nr + 1)
    rr = np.linspace(0.6, 1.0, nr + 1)
    ps, ts = _sector(sr, angles)
    pr, tr = _sector(rr, angles)
    nsn = ps.shape[1]
    mesh = MeshTri(np.column_stack([ps, pr]), np.column_stack([ts, tr + nsn]))
    basis = Basis(mesh, ElementTriP2(), intorder=12)
    vdof, fdof = basis.nodal_dofs[0], basis.facet_dofs[0]
    width = segments + 1
    sring = np.arange(width)
    rring = nr * width + np.arange(width)
    # Keep each disconnected block's cut sequence contiguous. The intervening
    # pair is not an edge, so it introduces no cross-block radial-cut weld.
    masters = np.concatenate([np.arange(nr + 1) * width,
                              nsn + np.arange(nr + 1) * width])
    slaves = masters + segments
    outer = nr * width + np.arange(width)
    inner = nsn + np.arange(width)
    boundary_facets = np.flatnonzero(
        np.all(np.isin(mesh.facets, outer), axis=0)
        | np.all(np.isin(mesh.facets, inner), axis=0))
    boundary = np.unique(np.concatenate([vdof[outer], vdof[inner],
                                         fdof[boundary_facets]]))
    rotor_elements = np.arange(ts.shape[1], mesh.t.shape[1])
    rotor_dofs = basis.get_dofs(elements=rotor_elements).all()
    return mesh, basis, dict(
        n_dof=basis.N, facets=mesh.facets, vdof=vdof, fdof=fdof,
        rring=rring, sring=sring, nsn=nsn, n_ring=width, full_ring=False,
        Mn=masters, Sn=slaves, dirichlet_dofs=boundary), rotor_dofs, rotor_elements


def _solve(segments, order, sign):
    mesh, basis, kwargs, rotor_dofs, rotor_elements = _problem(segments)
    shift = segments // 4
    delta = shift * ALPHA / segments
    assert np.cos(order * ALPHA) == pytest.approx(sign)
    projection = SlipProjection(**kwargs, bc_sign=sign)
    pro, boundary_columns = projection.build(shift)
    z = basis.doflocs[0] + 1j * basis.doflocs[1]
    phases = np.ones(basis.N, complex)
    phases[rotor_dofs] = np.exp(1j * order * delta)
    exact_nodal = np.real(phases * z ** order)
    stiffness = asm(laplace, basis)
    reduced = (pro.T @ stiffness @ pro).tocsr()
    known = np.zeros(pro.shape[1])
    prescribed = {}
    for dof in kwargs["dirichlet_dofs"]:
        row = pro.getrow(dof)
        column, weight = int(row.indices[0]), float(row.data[0])
        value = exact_nodal[dof] / weight
        if column in prescribed:
            assert value == pytest.approx(prescribed[column], abs=1e-12)
        prescribed[column] = value
        known[column] = value
    assert sorted(prescribed) == list(boundary_columns)
    free = np.ones(pro.shape[1], bool)
    free[boundary_columns] = False
    rhs = -(reduced @ known)
    known[free] = spsolve(reduced[free][:, free].tocsc(), rhs[free])
    solution = pro @ known
    field = basis.interpolate(solution)
    coordinates = basis.global_coordinates().value
    zq = coordinates[0] + 1j * coordinates[1]
    phaseq = np.ones((mesh.t.shape[1], 1), complex)
    phaseq[rotor_elements] = np.exp(1j * order * delta)
    exact = np.real(phaseq * zq ** order)
    derivative = order * phaseq * zq ** (order - 1)
    # These are derivatives in each block's local coordinates. The factor
    # exp(i*k*delta) includes the physical rotor rotation and its chain rule.
    gradient = np.array([np.real(derivative), -np.imag(derivative)])
    l2 = float(np.sqrt(np.sum((np.asarray(field) - exact) ** 2 * basis.dx)))
    h1 = float(np.sqrt(np.sum(np.sum((field.grad - gradient) ** 2, axis=0) * basis.dx)))
    exact_energy = float(np.sum(np.sum(gradient ** 2, axis=0) * basis.dx))
    energy = float(solution @ stiffness @ solution)
    jumps, rotor_trace, stator_trace = [], [], []
    for segment in range(segments):
        wrap, start = divmod(segment + shift, segments)
        factor = float(sign ** wrap)
        rotor = projection.edge_dof(int(kwargs["rring"][segment]) + kwargs["nsn"],
                                     int(kwargs["rring"][segment + 1]) + kwargs["nsn"])
        stator = projection.edge_dof(int(kwargs["sring"][start]),
                                      int(kwargs["sring"][start + 1]))
        rotor_trace.append(float(solution[rotor]))
        stator_trace.append(float(factor * solution[stator]))
        jumps.append(float(solution[rotor] - factor * solution[stator]))
    return dict(segments=int(segments), shift=int(shift), polynomial_order=int(order),
                bc_sign=int(sign), dofs=int(basis.N), reduced_dofs=int(pro.shape[1]),
                l2_error=l2, h1_error=h1, energy=energy, exact_energy=exact_energy,
                relative_energy_error=abs(energy - exact_energy) / exact_energy,
                midpoint_jump_max=float(np.max(np.abs(jumps))),
                rotor_midpoint_trace=rotor_trace, stator_midpoint_trace=stator_trace,
                midpoint_jumps=jumps)


@pytest.mark.parametrize("order,sign", [(4, 1), (6, -1)],
                         ids=["periodic", "antiperiodic"])
def test_annular_harmonic_field_converges_across_sector_cut(order, sign):
    refinements = [_solve(segments, order, sign) for segments in (8, 16, 32)]
    assert all(result["midpoint_jump_max"] < 1e-12 for result in refinements)
    assert all(b["l2_error"] < a["l2_error"]
               for a, b in zip(refinements, refinements[1:]))
    assert all(b["h1_error"] < a["h1_error"]
               for a, b in zip(refinements, refinements[1:]))
    assert refinements[-1]["relative_energy_error"] < refinements[0]["relative_energy_error"]


def test_antiperiodic_quadratic_patch_is_exact_across_sector_cut():
    # Re(z**2) is exactly representable by Cartesian P2 even on the straight
    # chord facets. A missing midpoint weld must not tear this exact solution.
    patch = _solve(8, 2, -1)
    assert patch["midpoint_jump_max"] < 1e-12
    assert patch["l2_error"] < 1e-10
    assert patch["h1_error"] < 1e-10
    assert patch["relative_energy_error"] < 1e-10
