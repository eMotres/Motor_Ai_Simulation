"""Direct production zipper tests: roundoff ties do not change gap diagonals."""
import numpy as np
import pytest
from scipy.spatial import cKDTree

from motor_ai_sim.simulation.mesher import _weld_belt_into_half
from motor_ai_sim.simulation.sb_domains import DOM_AIRGAP, DOM_OUTER

# Trig generation plus 2x2 rotation/roundtrip arithmetic can accumulate several
# binary64 ulps at this radius. This bounds coordinates only; topology is exact.
COORD_TOL = 16 * np.finfo(float).eps * .0403


class RingMesh:
    """Coordinate/connectivity container preserving the zipper's signed order.

    A mesh constructor that sorts triangle node IDs would obscure orientation
    of the production zipper before that constructor is called.
    """

    def __init__(self, p, t):
        self.p = np.asarray(p)
        self.t = np.asarray(t, dtype=np.int64)


def rotation(angle):
    return np.array([[np.cos(angle), -np.sin(angle)],
                     [np.sin(angle), np.cos(angle)]])


def belt(n_sectors, half, layers, *, offset=0., rotated_frame=0., nonuniform=False):
    n_slip = 128
    columns = n_slip if n_sectors == 1 else n_slip // n_sectors + 1
    angles = np.arange(columns) * (2 * np.pi / n_slip)
    if nonuniform:
        # Different iron/grid counts force multiple advances in both pointers.
        step = 2 * np.pi / n_slip
        angles = np.sort(np.concatenate([
            np.delete(angles, [5, 11, 17]),
            (np.array([2.31, 8.67, 20.45, 22.22, 27.61])) * step,
        ]))
    # Only interior angles are perturbed, keeping exact sector cuts compatible.
    angles[1:-1] += offset
    radius = .04 if half == "rotor" else .0403
    points = radius * np.array([np.cos(angles), np.sin(angles)])
    original = points.copy()
    if rotated_frame:
        transform = rotation(rotated_frame)
        points = transform.T @ (transform @ points)
        # Cuts are the same analytic rays in both local frames. This test is
        # about the zipper comparison, not a different near-zero cut selector.
        points[:, 0] = original[:, 0]
        if n_sectors > 1:
            points[:, -1] = original[:, -1]
    mesh = RingMesh(points, np.empty((3, 0), dtype=np.int64))
    spec = dict(n_slip=n_slip, K=layers, r_lo=40., r_hi=40.3)
    result, tags = _weld_belt_into_half(mesh, np.empty(0, dtype=np.int32),
                                        spec, half, n_sectors)
    return result, tags, original


def triangle_keys(triangles):
    return {tuple(sorted(map(int, tri))) for tri in triangles.T}


def signed_double_areas(mesh):
    a, b, c = mesh.p[:, mesh.t[0]], mesh.p[:, mesh.t[1]], mesh.p[:, mesh.t[2]]
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


@pytest.mark.parametrize("n_sectors", [1, 2, 4])
@pytest.mark.parametrize("half", ["rotor", "stator"])
@pytest.mark.parametrize("layers", [1, 3])
def test_local_zipper_is_equivariant_under_ninety_degree_frame_rotation(n_sectors, half, layers):
    reference, tags, _ = belt(n_sectors, half, layers)
    rotated, rotated_tags, _ = belt(n_sectors, half, layers, rotated_frame=np.pi / 2)
    np.testing.assert_allclose(rotated.p, reference.p, atol=COORD_TOL, rtol=0.)
    assert triangle_keys(rotated.t) == triangle_keys(reference.t)
    np.testing.assert_array_equal(rotated_tags, tags)


@pytest.mark.parametrize("reference_sectors,angle", [(1, 0.), (1, np.pi / 2),
                                                     (1, 3 * np.pi / 2), (2, np.pi / 2)])
@pytest.mark.parametrize("half", ["rotor", "stator"])
@pytest.mark.parametrize("layers", [1, 3])
def test_full_and_half_gap_blocks_match_a_rotated_quarter(reference_sectors, angle, half, layers):
    reference, _, _ = belt(reference_sectors, half, layers)
    quarter, _, _ = belt(4, half, layers)
    moved_quarter = rotation(angle) @ quarter.p
    distance, mapping = cKDTree(reference.p.T).query(moved_quarter.T)
    assert distance.max() < COORD_TOL
    assert np.unique(mapping).size == quarter.p.shape[1]
    centers = reference.p[:, reference.t].mean(axis=1)
    local_angle = np.mod(np.arctan2(centers[1], centers[0]) - angle, 2 * np.pi)
    selected = reference.t[:, local_angle < np.pi / 2]
    assert triangle_keys(selected) == triangle_keys(mapping[quarter.t])


@pytest.mark.parametrize("n_sectors", [1, 2, 4])
@pytest.mark.parametrize("half", ["rotor", "stator"])
def test_roundoff_offsets_tie_but_real_angular_offsets_keep_both_branches(n_sectors, half):
    reference, _, _ = belt(n_sectors, half, 1)
    for offset in (-1e-15, 1e-15):
        near, _, original_points = belt(n_sectors, half, 1, offset=offset)
        assert triangle_keys(near.t) == triangle_keys(reference.t)
        # Geometry is retained exactly, including the nonzero offset.
        np.testing.assert_array_equal(near.p[:, :original_points.shape[1]], original_points)
    before, _, _ = belt(n_sectors, half, 1, offset=-1e-10)
    after, _, _ = belt(n_sectors, half, 1, offset=1e-10)
    assert triangle_keys(before.t) == triangle_keys(reference.t)
    assert triangle_keys(after.t) != triangle_keys(before.t)


@pytest.mark.parametrize("n_sectors", [1, 2, 4])
@pytest.mark.parametrize("half", ["rotor", "stator"])
@pytest.mark.parametrize("layers", [1, 3])
def test_zipper_has_consistent_orientation_area_and_sector_bounds(n_sectors, half, layers):
    mesh, tags, iron_points = belt(n_sectors, half, layers, offset=1e-10)
    signed = signed_double_areas(mesh)
    expected_sign = -1. if half == "rotor" else 1.
    assert np.all(expected_sign * signed > 0.)
    assert np.all(np.abs(signed) > 1e-12)
    np.testing.assert_array_equal(tags, np.full(tags.size,
                                  DOM_AIRGAP if half == "rotor" else DOM_OUTER))
    if n_sectors > 1:
        angles = np.mod(np.arctan2(mesh.p[1], mesh.p[0]), 2 * np.pi)
        assert np.all(angles <= 2 * np.pi / n_sectors + 1e-14)
    # No cracks: all interior edges have two cells, boundary edges have one.
    edges = np.sort(np.concatenate([mesh.t[[0, 1]], mesh.t[[1, 2]], mesh.t[[2, 0]]], axis=1), axis=0)
    _, counts = np.unique(edges.T, axis=0, return_counts=True)
    assert np.all((counts == 1) | (counts == 2))
    ncell = 128 // n_sectors
    assert np.count_nonzero(counts == 1) == 2 * ncell + (0 if n_sectors == 1 else 2 * layers)
    # Independent polygon shoelace areas close on the annular slice exactly.
    slip_points = mesh.p[:, -iron_points.shape[1]:]

    def polygon_area(points):
        following = np.roll(points, -1, axis=1) if n_sectors == 1 else points[:, 1:]
        preceding = points if n_sectors == 1 else points[:, :-1]
        return .5 * np.sum(preceding[0] * following[1] - preceding[1] * following[0])

    expected_area = abs(polygon_area(slip_points) - polygon_area(iron_points))
    assert .5 * np.sum(np.abs(signed)) == pytest.approx(expected_area, rel=1e-12, abs=1e-18)


@pytest.mark.parametrize("n_sectors", [1, 2, 4])
@pytest.mark.parametrize("half", ["rotor", "stator"])
@pytest.mark.parametrize("layers", [1, 3])
def test_nonuniform_iron_ring_with_extra_and_missing_rays(n_sectors, half, layers):
    reference, tags, iron = belt(n_sectors, half, layers, nonuniform=True)
    rotated, rotated_tags, _ = belt(n_sectors, half, layers, nonuniform=True,
                                   rotated_frame=np.pi / 2)
    assert triangle_keys(rotated.t) == triangle_keys(reference.t)
    np.testing.assert_array_equal(rotated_tags, tags)
    expected_sign = -1. if half == "rotor" else 1.
    assert np.all(expected_sign * signed_double_areas(reference) > 0.)
    ncell = 128 // n_sectors
    iron_edges = iron.shape[1] if n_sectors == 1 else iron.shape[1] - 1
    assert reference.t.shape[1] == iron_edges + ncell + 2 * ncell * (layers - 1)
    edges = np.sort(np.concatenate([reference.t[[0, 1]], reference.t[[1, 2]],
                                    reference.t[[2, 0]]], axis=1), axis=0)
    _, counts = np.unique(edges.T, axis=0, return_counts=True)
    assert np.all((counts == 1) | (counts == 2))
    assert np.count_nonzero(counts == 1) == iron_edges + ncell + (0 if n_sectors == 1 else 2 * layers)
    # Exact and nearly coincident rays keep the same topology amid real offsets.
    near, _, _ = belt(n_sectors, half, layers, nonuniform=True, offset=1e-15)
    assert triangle_keys(near.t) == triangle_keys(reference.t)
