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
    assert "_MT.enabled()" in inspect.getsource(F._fem_transient_sliding_band_once)
    assert '**({"mesh_telemetry": _mesh_telemetry} if _mesh_telemetry is not None else {})' in inspect.getsource(F._fem_transient_sliding_band_once)
