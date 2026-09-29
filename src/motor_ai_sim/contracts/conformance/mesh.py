"""Conformance: any MeshIR a mesh module produces must satisfy these.

A mesh from any mesher (gmsh today, an external one later) is acceptable iff it
passes here — structural invariants the solvers rely on.
"""
from __future__ import annotations

from typing import Any

from .. import MeshIR


def assert_mesh_ir(mir: MeshIR, *, min_cells: int = 1) -> None:
    assert isinstance(mir, MeshIR), "must return a MeshIR"
    mir.check_consistent()                          # cell_tags length + node-index bounds
    assert mir.vertices.shape[1] == 2, f"vertices must be (N,2), got {mir.vertices.shape}"
    assert mir.triangles.shape[1] == 3, f"triangles must be (M,3), got {mir.triangles.shape}"
    assert mir.n_nodes >= 3, "mesh has < 3 nodes"
    assert mir.n_cells >= min_cells, f"mesh has {mir.n_cells} cells (< {min_cells})"
    assert mir.cell_tags.shape[0] == mir.n_cells, "one tag per cell required"
    assert int(mir.triangles.max()) < mir.n_nodes, "triangle references a non-existent node"


def assert_mesh_provider(provider: Any, *, payload: dict | None = None, min_cells: int = 1) -> MeshIR:
    man = provider.manifest()
    assert man.capability == "mesh", f"expected capability 'mesh', got {man.capability!r}"
    assert "MeshIR" in man.outputs, "a mesh provider must declare MeshIR as an output"
    mir = provider.run(payload)
    assert_mesh_ir(mir, min_cells=min_cells)
    return mir
