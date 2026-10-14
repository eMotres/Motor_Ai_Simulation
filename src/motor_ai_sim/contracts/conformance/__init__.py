"""Conformance suites — what an implementation of a capability MUST satisfy.

A new geometry / mesh / solver (from any agent, in any repo) is acceptable iff it
passes the matching suite here. This is how module autonomy stays safe: the
contract + its conformance gate, not trust.
"""
from __future__ import annotations

from .drivetrain import (COMPONENT_PREFIXES, COMPONENT_STATUSES, ConformanceError, assert_component_provider,
                         assert_load_ir, assert_load_provider, assert_motor_performance_ir,
                         assert_propulsor_ir, assert_shaft_load_ir, assert_transmission_ir,
                         assert_transmission_provider)
from .geometry import assert_geometry_ir, assert_geometry_provider
from .materials import assert_material_ir, assert_materials_provider
from .mesh import assert_mesh_ir, assert_mesh_provider
from .result import assert_result_ir

__all__ = [
    "assert_geometry_ir", "assert_geometry_provider",
    "assert_material_ir", "assert_materials_provider",
    "assert_mesh_ir", "assert_mesh_provider",
    "assert_result_ir",
    "COMPONENT_PREFIXES", "COMPONENT_STATUSES", "ConformanceError", "assert_component_provider",
    "assert_load_ir", "assert_load_provider", "assert_motor_performance_ir", "assert_propulsor_ir",
    "assert_shaft_load_ir", "assert_transmission_ir", "assert_transmission_provider",
]
