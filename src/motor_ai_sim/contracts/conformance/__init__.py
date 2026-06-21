"""Conformance suites — what an implementation of a capability MUST satisfy.

A new geometry / mesh / solver (from any agent, in any repo) is acceptable iff it
passes the matching suite here. This is how module autonomy stays safe: the
contract + its conformance gate, not trust.
"""
from __future__ import annotations

from .geometry import assert_geometry_ir, assert_geometry_provider
from .mesh import assert_mesh_ir, assert_mesh_provider
from .result import assert_result_ir

__all__ = [
    "assert_geometry_ir", "assert_geometry_provider",
    "assert_mesh_ir", "assert_mesh_provider",
    "assert_result_ir",
]
