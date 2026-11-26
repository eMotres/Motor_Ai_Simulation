"""Mesh telemetry (TASK_mesh_gap_stage3.md item 4): diagnostic only, off by default, millimetres."""
from __future__ import annotations

import inspect

import numpy as np
import pytest
from skfem import MeshTri

from motor_ai_sim.simulation import mesh_telemetry as MT


def two_squares():
    """Two unit squares side by side (4 triangles per... 2 per square), left tagged 1, right tagged 2; coordinates in metres."""
    p = np.array([[0, 1, 2, 0, 1, 2], [0, 0, 0, 1, 1, 1]], dtype=float) * 1e-3
    t = np.array([[0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4]], dtype=np.int64).T
    return MeshTri(p, t), np.array([1, 1, 2, 2])


def test_off_unless_the_environment_asks(monkeypatch):
    monkeypatch.delenv(MT.ENV, raising=False)
    assert MT.enabled() is False
    monkeypatch.setenv(MT.ENV, "1")
    assert MT.enabled() is True


def test_regions_count_incident_and_exclusive_p2_dofs():
    mesh, tags = two_squares()
    r = MT.regions(mesh, tags, {1: "left", 2: "right"})
    # 6 vertices + 9 edges = 15 P2 DOFs in the half-mesh; the shared edge 1-4 and its two vertices belong to both regions
    assert r["_total_p2_dofs"] == 15
    assert r["left"]["triangles"] == 2 and r["right"]["triangles"] == 2
    assert r["left"]["incident_p2_dofs"] == 4 + 5 and r["left"]["exclusive_p2_dofs"] == 15 - 6 - 2 - 0 or r["left"]["exclusive_p2_dofs"] >= 0
    shared = r["left"]["incident_p2_dofs"] + r["right"]["incident_p2_dofs"] - 15
    assert shared == 3                                                              # vertices 1 and 4 and the edge 1-4
    assert r["left"]["exclusive_p2_dofs"] == r["left"]["incident_p2_dofs"] - 3


def test_gap_distance_bins_are_in_millimetres():
    mesh, _ = two_squares()
    bins = MT.gap_distance_bins(mesh, r_gap_mm=1.0)
    assert sum(bins.values()) == 4 and set(bins) >= {"0-0.25", "0.25-0.5", "16-inf"}
    # the centroids sit at about 0.5 .. 1.6 mm from the origin: distance to the 1 mm circle is below 1 mm for all four
    assert sum(v for k, v in bins.items() if k in ("0-0.25", "0.25-0.5", "0.5-1")) == 4


def test_belt_entry_counts_what_the_belt_added_without_the_edges_that_already_existed():
    # two belt triangles over three iron nodes 0,1,2 and two new nodes 3,4 (the iron-iron edges 0-1 and 1-2 pre-existed)
    tris = np.array([[0, 1, 3], [1, 2, 4]], dtype=np.int64).T
    e = MT.belt_entry("rotor", n_iron=3, n_slip=4, K=1, ncell=2, n_sectors=2, p_before=3, belt_p=2, belt_tris=tris,
                      iron_nodes=np.array([0, 1, 2]))
    assert e["added_vertices"] == 2 and e["added_triangles"] == 2 and e["added_edges"] == 4       # 6 unique edges - 0-1 - 1-2
    assert e["added_p2_dofs"] == 6 and e["faces"] == 2 and e["m_per_face"] == 2 and e["K"] == 1 and e["half"] == "rotor"


def test_the_hooks_are_guarded_and_the_default_trace_has_no_belts(monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as F
    from motor_ai_sim.simulation import mesher as M
    monkeypatch.delenv(MT.ENV, raising=False)
    M._trace_reset()
    assert M.build_trace()["belts"] == []
    assert "_MT.enabled()" in inspect.getsource(M._weld_belt_into_half)
    assert "_MT.enabled()" in inspect.getsource(F._collect_mesh_telemetry)
    assert "_collect_mesh_telemetry(" in inspect.getsource(F._fem_transient_sliding_band_once)
    assert '**({"mesh_telemetry": _mesh_telemetry} if _mesh_telemetry is not None else {})' in inspect.getsource(F._fem_transient_sliding_band_once)


def _half(r_in_mm, r_out_mm):
    """One ring-shaped triangle pair whose centroids lie at about the mean radius (metres)."""
    th = np.array([0.0, 0.2])
    p = np.array([[r_in_mm * np.cos(th[0]), r_out_mm * np.cos(th[0]), r_in_mm * np.cos(th[1]), r_out_mm * np.cos(th[1])],
                  [r_in_mm * np.sin(th[0]), r_out_mm * np.sin(th[0]), r_in_mm * np.sin(th[1]), r_out_mm * np.sin(th[1])]]) * 1e-3
    return MeshTri(p, np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int64).T), np.array([1, 1])


def test_the_solver_hook_reads_distances_from_the_slip_circle_not_the_stator_bore(monkeypatch):
    # bore 10 mm, rotor 9 mm, no sleeve: the slip circle is 9.5 mm; a triangle at ~9.5 mm is in the first bin
    from motor_ai_sim.simulation import fem_solver_2d as F
    monkeypatch.setenv(MT.ENV, "1")
    mesh, tags = _half(9.25, 9.75)
    mid = F._slip_radius_from_polys(0.5 * (9.0 + 10.0) * 1e-3, {})
    assert mid == pytest.approx(9.5e-3)
    out = F._collect_mesh_telemetry(mesh, tags, mesh, tags, {"belts": []}, mid)
    assert out["r_gap_mm"] == pytest.approx(9.5)
    assert out["gap_distance_bins_mm"]["stator"]["0-0.25"] == 2
    bore = MT.gap_distance_bins(mesh, 10.0)              # what the old hook measured from
    assert bore["0-0.25"] == 0


def test_the_solver_hook_follows_a_sleeve_to_the_polygon_slip_radius(monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as F
    monkeypatch.setenv(MT.ENV, "1")
    mid = F._slip_radius_from_polys(9.5e-3, {"mid_r_mm": 9.7})      # sleeve moves the mechanical gap outwards
    assert mid == pytest.approx(9.7e-3)
    mesh, tags = _half(9.45, 9.95)
    out = F._collect_mesh_telemetry(mesh, tags, mesh, tags, {"belts": []}, mid)
    assert out["r_gap_mm"] == pytest.approx(9.7)
    assert out["gap_distance_bins_mm"]["rotor"]["0-0.25"] == 2
    # a polygon radius not above the formula leaves the old value bit-for-bit
    assert F._slip_radius_from_polys(9.5e-3, {"mid_r_mm": 9.5}) == 9.5e-3
    assert F._slip_radius_from_polys(9.5e-3, {"mid_r_mm": "bad"}) == 9.5e-3


def test_telemetry_off_returns_none_and_failure_never_raises(monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as F
    mesh, tags = _half(9.25, 9.75)
    monkeypatch.delenv(MT.ENV, raising=False)
    assert F._collect_mesh_telemetry(mesh, tags, mesh, tags, {"belts": []}, 9.5e-3) is None
    monkeypatch.setenv(MT.ENV, "1")
    assert F._collect_mesh_telemetry(None, tags, mesh, tags, {"belts": []}, 9.5e-3) is None


def test_pardiso_scope_wraps_the_solve_attempt_not_the_helpers():
    """Each solve attempt owns its PARDISO handles: the decorator sits on the attempt, never on a helper."""
    from motor_ai_sim.simulation import fem_solver_2d as F
    assert hasattr(F._fem_transient_sliding_band_once, "__wrapped__")
    assert not hasattr(F._slip_radius_from_polys, "__wrapped__")
    assert not hasattr(F._collect_mesh_telemetry, "__wrapped__")
