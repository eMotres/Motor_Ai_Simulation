"""Isolated algebra checks for the continuous P2 slip experiment."""

import math

import numpy as np
import pytest
from skfem import Basis, ElementTriP2, MeshTri

from motor_ai_sim.simulation.p2_projection import SlipProjection
from scripts.continuous_p2_projection_prototype import ContinuousSlipProjection


def _spacing(segments, full_ring):
    return (2*math.pi if full_ring else math.pi/2) / segments


def annular_projection(segments, full_ring, sign):
    """Two polygonal annuli with independent P2 traces at the slip ring."""
    angle = 2*math.pi if full_ring else math.pi/2
    angles = (np.linspace(0., angle, segments, endpoint=False) if full_ring
              else np.linspace(0., angle, segments+1))
    width = len(angles)

    def half(radii):
        points = np.array([(r*math.cos(a), r*math.sin(a))
                           for r in radii for a in angles]).T
        triangles = []
        for layer in range(len(radii)-1):
            for k in range(segments):
                j = (k+1) % width
                a, b = layer*width+k, layer*width+j
                c, d = (layer+1)*width+k, (layer+1)*width+j
                triangles.extend([(a, c, d), (a, d, b)])
        return points, np.asarray(triangles).T

    stator, st = half((1., 1.2, 1.4))
    rotor, rt = half((.5, .8, 1.))
    nsn = stator.shape[1]
    mesh = MeshTri(np.hstack([stator, rotor]), np.hstack([st, rt+nsn]))
    basis = Basis(mesh, ElementTriP2())
    if full_ring:
        masters = slaves = np.array([], int)
    else:
        masters = np.array([nsn, nsn+width, nsn+2*width,
                            0, width, 2*width])
        slaves = masters + width - 1
    outer_nodes = np.arange(2*width, 3*width)
    outer_edges = np.flatnonzero(
        np.all(np.isin(mesh.facets, outer_nodes), axis=0))
    boundary = np.concatenate([basis.nodal_dofs[0, outer_nodes],
                               basis.facet_dofs[0, outer_edges]])
    return SlipProjection(
        n_dof=basis.N, facets=mesh.facets, vdof=basis.nodal_dofs[0],
        fdof=basis.facet_dofs[0], rring=np.arange(2*width, 3*width),
        sring=np.arange(width), nsn=nsn, n_ring=width,
        full_ring=full_ring, bc_sign=sign, Mn=masters, Sn=slaves,
        dirichlet_dofs=boundary)


@pytest.mark.parametrize("full_ring,sign", [(True, 1), (False, 1), (False, -1)])
def test_integer_shifts_have_exact_existing_constraint_space(full_ring, sign):
    discrete = annular_projection(5, full_ring, sign)
    step = _spacing(5, full_ring)
    continuous = ContinuousSlipProjection(discrete, step)
    for shift in (-11, -5, -1, 0, 1, 5, 13):
        old, old_outer = discrete.build(shift)
        new, _, new_outer = continuous.build(shift * step)
        assert old.shape == new.shape
        np.testing.assert_array_equal(new_outer, old_outer)
        # Signed union-find can choose different column representatives, so
        # compare the actual spaces row by row, including the sector seam.
        dense = new.toarray()
        for column in range(old.shape[1]):
            members = old[:, column].tocoo()
            reference = dense[members.row[0]] / members.data[0]
            np.testing.assert_allclose(
                dense[members.row], members.data[:, None] * reference,
                rtol=0., atol=1e-14)
        assert np.linalg.matrix_rank(dense) == new.shape[1]


@pytest.mark.parametrize("full_ring,sign", [(True, 1), (False, 1), (False, -1)])
def test_fractional_interpolation_derivative_and_cut_constraints(full_ring, sign):
    discrete = annular_projection(5, full_ring, sign)
    step = _spacing(5, full_ring)
    continuous = ContinuousSlipProjection(discrete, step)
    theta = 1.37 * step
    p, dp, outer = continuous.build(theta)
    h = 1e-6 * step
    plus = continuous.build(theta + h)[0]
    minus = continuous.build(theta - h)[0]
    rng = np.random.default_rng(731)
    z = rng.normal(size=p.shape[1])
    np.testing.assert_allclose(
        dp @ z, ((plus - minus) @ z) / (2*h), rtol=2e-9, atol=3e-8)
    assert p.shape == dp.shape
    np.testing.assert_array_equal(outer, continuous.outer_columns)
    for slave, master, cut_sign in discrete._cut_v + discrete._cut_e:
        np.testing.assert_allclose(p[slave].toarray(),
                                   cut_sign * p[master].toarray(), atol=1e-14)
        np.testing.assert_allclose(dp[slave].toarray(),
                                   cut_sign * dp[master].toarray(), atol=1e-13)


def test_fractional_collocation_does_not_match_trace_inside_crossed_edge():
    """Three rotor values cannot reproduce two stator P2 pieces exactly."""
    discrete = annular_projection(5, True, 1)
    step = _spacing(5, True)
    continuous = ContinuousSlipProjection(discrete, step)
    p, _, _ = continuous.build(.37 * step)
    z = np.zeros(p.shape[1])
    dof = continuous.stator_vertices[1]
    z[continuous.column[int(continuous.root[dof])]] = 1.
    values = p @ z

    def quadratic(u, triplet):
        weights, _ = continuous._weights(u)
        return float(np.dot(weights, triplet))

    rotor = [int(discrete.vdof[int(discrete.rring[0]) + discrete.nsn]),
             int(discrete._re_dofs[0]),
             int(discrete.vdof[int(discrete.rring[1]) + discrete.nsn])]
    location = .8  # shifted coordinate 1.17, beyond the crossed stator vertex
    rotor_trace = quadratic(location, values[rotor])
    stator = [continuous.stator_vertices[1], continuous.stator_edges[1],
              continuous.stator_vertices[2]]
    stator_trace = quadratic(location + .37 - 1., values[stator])
    assert abs(rotor_trace - stator_trace) == pytest.approx(.0456)
