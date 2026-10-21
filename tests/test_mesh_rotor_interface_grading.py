"""Regression coverage for locally graded rotor material interfaces."""
import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from motor_ai_sim.simulation import geo_mesh
from motor_ai_sim.simulation import geo_mesh_gmsh as gg

_build_pslg = geo_mesh._build_pslg


def _case(target=2.0, angle=0.0):
    left = Polygon([(-5, -5), (0, -5), (0, 5), (-5, 5)])
    right = Polygon([(0, -5), (5, -5), (5, 5), (0, 5)])
    pocket = Polygon([(0.05, 4), (0.25, 4), (0.25, 4.2), (0.05, 4.2)])
    V, S = _build_pslg([LineString(left.exterior.coords), LineString(right.exterior.coords),
                        LineString(pocket.exterior.coords)])
    q = np.deg2rad(angle)
    Q = np.array([[np.cos(q), -np.sin(q)], [np.sin(q), np.cos(q)]])
    V = V @ Q.T
    seeds = np.array([[-2.5, 0], [2.5, 0]]) @ Q.T
    regions = np.c_[seeds, [5, 6], [0.433*target**2]*2]
    holes = np.array([[0.15, 4.1]]) @ Q.T
    return V, S, regions, holes


def test_triangulate_pipeline_replaces_scalar_whole_chord_size(monkeypatch):
    rows = []
    monkeypatch.setattr(geo_mesh, "cdt_backend", lambda: "gmsh")
    for target in (1.0, 2.0):
        V, S, regions, holes = _case(target=target)
        captured = {}
        def fake_backend(A, area, regions, rotor_bridge):
            captured.update(A)
            return np.asarray(A["vertices"]), np.zeros((0, 3), np.int64)
        monkeypatch.setattr(geo_mesh, "_triangulate_gmsh", fake_backend)
        geo_mesh._triangulate(V, S, 0.433*target**2, regions=regions,
                              hole_pts=holes, rotor_bridge=True)
        assert len(captured["vertices"]) > len(V)
        fixed = np.asarray(captured["fixed_segments"], int)
        lengths = np.linalg.norm(captured["vertices"][fixed[:, 1]] -
                                 captured["vertices"][fixed[:, 0]], axis=1)
        assert lengths.min() < 0.35 and lengths.max() > 0.65*target
        rows.append(lengths.max())
    assert rows[1] > 1.4*rows[0]


def test_endpoint_feature_is_local_rotation_invariant_and_geometry_exact():
    rows = []
    for angle in (0, 37):
        V, S, regions, holes = _case(angle=angle)
        v, s, fixed, _sizes = geo_mesh._plan_graded_rotor_interfaces(
            V, S, 0.433*2**2, regions, holes)
        assert np.array_equal(v[:len(V)], V)
        assert len(fixed) and len(v) > len(V)
        assert unary_union(gg._faces(V, S)).symmetric_difference(
            unary_union(gg._faces(v, s))).area == 0
        lengths = np.linalg.norm(v[fixed[:, 1]] - v[fixed[:, 0]], axis=1)
        source_lengths = np.linalg.norm(V[S[:, 1]] - V[S[:, 0]], axis=1)
        lfs, near = gg._feature_sizes(V, S)
        old_h = gg._segment_sizes(source_lengths, lfs, near,
                                  np.zeros(len(S), bool), gg._LFS_K)
        # The old scalar rule assigns the endpoint clearance to the complete
        # 10 mm chord.  This is the causal assertion, independent of backend.
        flank = np.where(np.linalg.norm(
            0.5 * (V[S[:, 0]] + V[S[:, 1]]), axis=1) < 1e-8)[0]
        assert len(flank) == 1
        assert np.ceil(10.0 / old_h[flank[0]]) > 30
        rows.append(np.sort(lengths))
        assert lengths.min() < 0.35 and lengths.max() > 1.0
    assert np.allclose(rows[0], rows[1], atol=2e-12, rtol=0)


def test_global_area_holes_and_frozen_edges():
    V, S, _regions, holes = _case()
    v, s, fixed, _sizes = geo_mesh._plan_graded_rotor_interfaces(
        V, S, 0.433*2**2, None, holes)
    segset = {tuple(sorted(x)) for x in s.tolist()}
    faces = gg._faces(V, S)
    key = {(float(x), float(y)): i for i, (x, y) in reversed(list(enumerate(V)))}
    sid = {(int(a), int(b)): k for k, (a, b) in enumerate(S)}
    keep = [not any(f.contains(Point(h)) for h in holes) for f in faces]
    frozen = gg._ring_use_counts(S, faces, keep, key, sid) != 2
    assert all(tuple(sorted(S[i].tolist())) in segset for i in np.where(frozen)[0])
    assert len(fixed)


def test_planner_resource_fence_fails_loudly(monkeypatch):
    V, S, regions, holes = _case()
    monkeypatch.setenv("SB_ROTOR_INTERFACE_MAX_DISTANCE_EVALS", "1")
    with pytest.raises(geo_mesh.MeshBudgetExceeded,
                       match="planner exceeded.*resource limit"):
        geo_mesh._plan_graded_rotor_interfaces(
            V, S, 0.433*2**2, regions, holes)


def test_planner_child_fence_handles_tiny_cap_without_candidates(monkeypatch):
    left = Polygon([(-5, -5), (0, -5), (0, 5), (-5, 5)])
    right = Polygon([(0, -5), (5, -5), (5, 5), (0, 5)])
    V, S = _build_pslg([LineString(left.exterior.coords),
                        LineString(right.exterior.coords)])
    regions = np.array([[-2.5, 0, 5, 0.433e-8],
                        [2.5, 0, 6, 0.433e-8]])
    monkeypatch.setenv("SB_ROTOR_INTERFACE_MAX_CHILDREN", "100")
    with pytest.raises(geo_mesh.MeshBudgetExceeded,
                       match="100-child resource limit"):
        geo_mesh._plan_graded_rotor_interfaces(V, S, 0.433e-8, regions, ())


def test_planner_honours_runtime_lfs_and_grading_overrides(monkeypatch):
    V, S, regions, holes = _case()
    def pieces(k, growth):
        monkeypatch.setenv("SB_GMSH_CDT_LFS_K", str(k))
        monkeypatch.setenv("SB_NETGEN_CDT_GRADING", str(growth))
        v, _s, fixed, _sizes = geo_mesh._plan_graded_rotor_interfaces(
            V, S, 0.433*2**2, regions, holes)
        return np.linalg.norm(v[fixed[:, 1]]-v[fixed[:, 0]], axis=1)
    narrow = pieces(1.0, 0.5)
    wider = pieces(2.5, 0.5)
    slow_growth = pieces(2.5, 0.2)
    assert len(narrow) > len(wider)
    assert len(slow_growth) > len(wider)


def test_true_parallel_thin_feature_remains_fine_and_rotation_invariant():
    def build(angle):
        left = Polygon([(-5, -5), (0, -5), (0, 5), (-5, 5)])
        right = Polygon([(0, -5), (5, -5), (5, 5), (0, 5)])
        ys = np.linspace(-4, 4, 41)
        hole = [(0.1, float(y)) for y in ys]
        hole += [(0.3, 4), (0.3, -4), (0.1, -4)]
        V, S = _build_pslg([LineString(left.exterior.coords),
                            LineString(right.exterior.coords), LineString(hole)])
        q = np.deg2rad(angle)
        Q = np.array([[np.cos(q), -np.sin(q)], [np.sin(q), np.cos(q)]])
        V = V @ Q.T
        seeds = np.array([[-2.5, 0], [2.5, 0]]) @ Q.T
        regions = np.c_[seeds, [5, 6], [0.433*2**2]*2]
        holes = np.array([[0.2, 0]]) @ Q.T
        v, _s, fixed, _sizes = geo_mesh._plan_graded_rotor_interfaces(
            V, S, 0.433*2**2, regions, holes)
        return np.sort(np.linalg.norm(v[fixed[:, 1]]-v[fixed[:, 0]], axis=1))
    a, b = build(0), build(37)
    assert np.allclose(a, b, atol=2e-12, rtol=0)
    # The true 0.10 mm parallel gap remains resolved near K*gap=0.25 mm.
    assert np.median(a[:len(a)//2]) <= 0.25


def test_gmsh_honours_planned_edges_and_avoids_boundary_fan():
    V, S, regions, holes = _case()
    v, s, fixed, sizes = geo_mesh._plan_graded_rotor_interfaces(
        V, S, 0.433*2**2, regions, holes)
    vo, tri = gg.triangulate_gmsh(
        v, s, 0.433*2**2, hole_pts=holes, regions=regions,
        fixed_segments=fixed, segment_sizes=sizes)
    edges = {tuple(sorted((int(a), int(b)))) for row in tri
             for a, b in ((row[0], row[1]), (row[1], row[2]), (row[2], row[0]))}
    assert all(tuple(sorted(map(int, row))) in edges for row in fixed)
    remote = fixed[np.mean(v[fixed][:, :, 1], axis=1) < 3.0]
    fixed_keys = {tuple(sorted(map(int, row))) for row in remote}
    owners = []
    for ti, row in enumerate(tri):
        if any(tuple(sorted((int(a), int(b)))) in fixed_keys
               for a, b in ((row[0], row[1]), (row[1], row[2]), (row[2], row[0]))):
            owners.append(ti)
    q = vo[tri[np.asarray(owners)]]
    angle = []
    for i in range(3):
        a = q[:, (i+1) % 3]-q[:, i]
        b = q[:, (i+2) % 3]-q[:, i]
        c = np.sum(a*b, axis=1) / (np.linalg.norm(a, axis=1)*np.linalg.norm(b, axis=1))
        angle.append(np.degrees(np.arccos(np.clip(c, -1, 1))))
    assert np.min(np.concatenate(angle)) > 10.0
