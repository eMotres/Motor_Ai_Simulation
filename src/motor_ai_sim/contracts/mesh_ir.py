"""MeshIR — a tagged 2D triangular mesh.

Solver modules (EM static/transient, thermal, mechanical) consume this and never
see the geometry or mesh module that produced it. Heavy arrays are numpy
(not validated element-by-element); the CONTRACT is the field set + shapes +
semantics, declared below.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import Provenance


class MeshIR(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    vertices: Any                                  # float ndarray (N, 2), millimetres
    triangles: Any                                 # int   ndarray (M, 3), node indices
    cell_tags: Any                                 # int   ndarray (M,),  domain id per triangle
    tag_names: Dict[int, str] = Field(default_factory=dict)  # domain id -> role name
    slip_radius_mm: Optional[float] = None
    # Named boundary facet sets, each an int ndarray (K, 2) of node-index pairs.
    boundary_facets: Dict[str, Any] = Field(default_factory=dict)
    provenance: Optional[Provenance] = None

    @field_validator("vertices", "triangles", "cell_tags")
    @classmethod
    def _as_ndarray(cls, v: Any) -> np.ndarray:
        return np.asarray(v)

    @field_validator("triangles")
    @classmethod
    def _check_tri_shape(cls, v: np.ndarray) -> np.ndarray:
        if v.ndim != 2 or v.shape[1] != 3:
            raise ValueError(f"triangles must be (M, 3), got {v.shape}")
        return v

    @field_validator("vertices")
    @classmethod
    def _check_vtx_shape(cls, v: np.ndarray) -> np.ndarray:
        if v.ndim != 2 or v.shape[1] != 2:
            raise ValueError(f"vertices must be (N, 2), got {v.shape}")
        return v

    @property
    def n_nodes(self) -> int:
        return int(self.vertices.shape[0])

    @property
    def n_cells(self) -> int:
        return int(self.triangles.shape[0])

    def check_consistent(self) -> None:
        """Cheap structural invariants — call after construction in conformance tests."""
        if self.cell_tags.shape[0] != self.n_cells:
            raise ValueError(
                f"cell_tags length {self.cell_tags.shape[0]} != n_cells {self.n_cells}")
        if self.n_cells and int(self.triangles.max()) >= self.n_nodes:
            raise ValueError("triangle references a node index >= n_nodes")
