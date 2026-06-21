"""contracts — the versioned integration spine of the modular engineering portal.

Every module (geometry, mesh, EM/thermal/mechanical solvers, surrogate, cost,
optimization, users) couples ONLY through these IR schemas, never to each other.
Get these right and the physical layout (one process today, isolated workers /
separate services later) can evolve without rewrites.

Versioning: bump CONTRACTS_VERSION on any breaking change. Conformance tests in
contracts/conformance/ gate every implementation against the schema, so a new
geometry or solver from a different agent plugs in iff its IR validates.
"""
from __future__ import annotations

# Defined first so adapters.py (which imports it) sees it without a cycle.
CONTRACTS_VERSION = "0.1.0"

from .common import MaterialRef, Provenance
from .geometry_ir import GeometryIR, Region, RegionRole, Ring, Symmetry, WindingTag
from .mesh_ir import MeshIR
from .result_ir import FieldArray, FieldResults, ResultIR, ScalarResults, SeriesResults

__all__ = [
    "CONTRACTS_VERSION",
    "Provenance", "MaterialRef",
    "GeometryIR", "Region", "RegionRole", "Ring", "Symmetry", "WindingTag",
    "MeshIR",
    "ResultIR", "ScalarResults", "SeriesResults", "FieldArray", "FieldResults",
]
