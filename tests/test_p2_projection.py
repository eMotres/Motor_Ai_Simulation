"""P2 integer slip coupling must weld the complete trace across sector cuts."""
import math

import numpy as np
import pytest
from scipy.sparse import coo_matrix
from skfem import Basis, ElementTriP2, MeshTri

from motor_ai_sim.simulation.p2_projection import SignedUF, SlipProjection


def annular_projection(segments, full_ring, sign):
    """Two independently meshed annuli with distinct coincident ring vertices."""
    angle = 2*math.pi if full_ring else math.pi/2
    angles = np.linspace(0., angle, segments, endpoint=False) if full_ring else np.linspace(0., angle, segments+1)
    nv = len(angles)

    def half(radii):
        points = np.array([(r*math.cos(a), r*math.sin(a))
                           for r in radii for a in angles]).T
        triangles = []
        for layer in range(len(radii)-1):
            for k in range(segments):
                j = (k+1) % nv
                a, b, c, d = layer*nv+k, layer*nv+j, (layer+1)*nv+k, (layer+1)*nv+j
                triangles.extend([(a, c, d), (a, d, b)])
        return points, np.array(triangles).T

    stator, st = half((1., 1.2, 1.4))
    rotor, rt = half((.5, .8, 1.))
    nsn = stator.shape[1]
    mesh = MeshTri(np.hstack([stator, rotor]), np.hstack([st, rt+nsn]))
    basis = Basis(mesh, ElementTriP2())
    if full_ring:
        masters = slaves = np.array([], int)
    else:
        masters = np.array([nsn, nsn+nv, nsn+2*nv, 0, nv, 2*nv])
        slaves = masters+nv-1
    outer_nodes = np.arange(2*nv, 3*nv)
    outer_edges = np.flatnonzero(np.all(np.isin(mesh.facets, outer_nodes), axis=0))
    boundary = np.concatenate([basis.nodal_dofs[0, outer_nodes], basis.facet_dofs[0, outer_edges]])
    return SlipProjection(
        n_dof=basis.N, facets=mesh.facets, vdof=basis.nodal_dofs[0], fdof=basis.facet_dofs[0],
        rring=np.arange(2*nv, 3*nv), sring=np.arange(nv), nsn=nsn,
        n_ring=nv, full_ring=full_ring, bc_sign=sign, Mn=masters, Sn=slaves,
        dirichlet_dofs=boundary)


def original_build(proj, shift):
    """Original vertex-derived midpoint mapping for compatibility comparisons."""
    union = SignedUF(proj.N)
    for slave, master, sign in proj._cut_v+proj._cut_e:
        union.union(slave, master, sign)
    nodes, signs = proj.ring_map(shift)
    for k in range(proj.Nring):
        union.union(int(proj.vdof[proj.rring[k]+proj.nsn]),
                    int(proj.vdof[proj.sring[nodes[k]]]), float(signs[k]))
    for edge, (a, b) in enumerate(proj._re_pairs):
        rotor_edge = proj._re_dofs[edge]
        if rotor_edge is None or signs[a] != signs[b]:
            continue
        stator_edge = proj.edge_dof(int(proj.sring[nodes[a]]), int(proj.sring[nodes[b]]))
        if stator_edge is not None:
            union.union(rotor_edge, stator_edge, float(signs[a]))
    roots = [union.find(k) for k in range(proj.N)]
    ids, inverse = np.unique([root for root, _ in roots], return_inverse=True)
    matrix = coo_matrix(([sign for _, sign in roots], (np.arange(proj.N), inverse)),
                        shape=(proj.N, len(ids))).tocsr()
    return matrix, np.unique(inverse[proj.D_ids])


def assert_rows_equal(matrix, first, second, sign):
    difference = matrix[first] - sign*matrix[second]
    assert difference.nnz == 0 or np.max(abs(difference.data)) == 0., (first, second, sign)


def assert_all_constraints(proj, matrix, shift):
    for slave, master, sign in proj._cut_v+proj._cut_e:
        assert_rows_equal(matrix, slave, master, sign)
    nodes, signs = proj.ring_map(shift)
    for k in range(proj.Nring):
        assert_rows_equal(matrix, int(proj.vdof[proj.rring[k]+proj.nsn]),
                          int(proj.vdof[proj.sring[nodes[k]]]), signs[k])
    width = proj.Nring if proj.full_ring else proj.Nring-1
    for a, b in proj._re_pairs:
        wraps, start = divmod(a+shift, width)
        end = (start+1) % width if proj.full_ring else start+1
        sign = 1. if proj.full_ring or wraps % 2 == 0 else proj.bc_sign
        rotor = (int(proj.rring[a]+proj.nsn), int(proj.rring[b]+proj.nsn))
        stator = (int(proj.sring[start]), int(proj.sring[end]))
        for rdof, sdof in ((int(proj.vdof[rotor[0]]), int(proj.vdof[stator[0]])),
                           (proj.edge_dof(*rotor), proj.edge_dof(*stator)),
                           (int(proj.vdof[rotor[1]]), int(proj.vdof[stator[1]]))):
            assert rdof is not None and sdof is not None
            assert_rows_equal(matrix, rdof, sdof, sign)


@pytest.mark.parametrize("segments", [3, 5, 8])
@pytest.mark.parametrize("sign", [-1, 1], ids=["antiperiodic", "periodic"])
def test_sector_welds_every_vertex_midpoint_and_radial_cut_for_integer_shifts(segments, sign):
    proj = annular_projection(segments, False, sign)
    assert proj.n_redge == proj.n_re_pairs == segments
    assert proj.n_cut_v == 6 and proj.n_cut_e == 4
    zero, zero_d = proj.build(0)
    old_zero, old_zero_d = original_build(proj, 0)
    assert (zero != old_zero).nnz == 0
    np.testing.assert_array_equal(zero_d, old_zero_d)
    # Covers both endpoint conventions, every location of the wrap segment,
    # exact full-sector turns and positive/negative multi-turn shifts.
    for shift in list(range(-2*segments, 2*segments+1))+[-101*segments-1, 100*segments+1]:
        matrix, boundary = proj.build(shift)
        assert matrix.shape == zero.shape
        assert np.all(np.diff(matrix.indptr) == 1)
        assert set(np.unique(matrix.data)).issubset({-1., 1.})
        assert_all_constraints(proj, matrix, shift)
        assert np.array_equal(boundary, np.unique(matrix[proj.D_ids].indices))
        assert np.all(np.bincount(matrix.indices, minlength=matrix.shape[1]) > 0)


@pytest.mark.parametrize("segments", [3, 5, 8])
def test_full_ring_is_exactly_the_original_projection_and_boundary(segments):
    proj = annular_projection(segments, True, 1)
    for shift in list(range(-2*segments, 2*segments+1))+[-103*segments, 104*segments+2]:
        actual, actual_boundary = proj.build(shift)
        expected, expected_boundary = original_build(proj, shift)
        assert actual.shape == expected.shape
        assert (actual != expected).nnz == 0
        np.testing.assert_array_equal(actual_boundary, expected_boundary)
        assert_all_constraints(proj, actual, shift)


@pytest.mark.parametrize("sign", [-1, 1])
def test_fix_removes_the_single_independent_wrap_midpoint(sign):
    proj = annular_projection(5, False, sign)
    for shift in (1, -1, 5, -5, 17, -17):
        original, _ = original_build(proj, shift)
        fixed, _ = proj.build(shift)
        assert original.shape[1] == fixed.shape[1]+1
        # A real lost constraint: the old projection admits at least one
        # rotor midpoint independently of its corresponding stator midpoint.
        with pytest.raises(AssertionError):
            assert_all_constraints(proj, original, shift)
        assert_all_constraints(proj, fixed, shift)


@pytest.mark.parametrize("sign", [-1, 1])
def test_quadratic_trace_matches_at_interior_samples_after_multiple_wraps(sign):
    segments = 5
    proj = annular_projection(segments, False, sign)
    rng = np.random.default_rng(601)
    for shift in (1, -1, 2*segments+2, -3*segments-2):
        matrix, _ = proj.build(shift)
        values = matrix @ rng.normal(size=matrix.shape[1])
        for a, b in proj._re_pairs:
            rv = int(proj.rring[a]+proj.nsn), int(proj.rring[b]+proj.nsn)
            rotor_values = values[[proj.vdof[rv[0]], proj.edge_dof(*rv), proj.vdof[rv[1]]]]
            for location in (.17, .43, .81):
                # Locate the physically shifted point in the repeated sector,
                # independently of ring_map's inclusive endpoint convention.
                global_coordinate = a+location+shift
                sector = math.floor(global_coordinate/segments)
                local = global_coordinate-sector*segments
                edge = math.floor(local)
                fraction = local-edge
                sv = int(proj.sring[edge]), int(proj.sring[edge+1])
                stator_values = values[[proj.vdof[sv[0]], proj.edge_dof(*sv), proj.vdof[sv[1]]]]
                def weights(x):
                    return np.array([(1-x)*(1-2*x), 4*x*(1-x), x*(2*x-1)])
                rotor_trace = weights(location) @ rotor_values
                stator_trace = weights(fraction) @ stator_values
                expected = (sign if sector % 2 else 1)*stator_trace
                np.testing.assert_allclose(rotor_trace, expected, rtol=0., atol=2e-14)
