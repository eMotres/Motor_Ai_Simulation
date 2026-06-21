"""Conformance: every geometry provider must pass this, regardless of who wrote it.

This is the glue that lets a different agent (or a future geometry-3d / external
geometry) plug in safely: if its output validates here, the rest of the pipeline
accepts it. Keep these checks about the CONTRACT, not about a specific geometry.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import GeometryIR, RegionRole

# A 2D machine cross-section must at least carry these roles.
REQUIRED_ROLES_2D = (RegionRole.STATOR, RegionRole.ROTOR, RegionRole.MAGNET, RegionRole.COIL)


def assert_geometry_ir(gir: GeometryIR, *, dim: int = 2) -> None:
    assert isinstance(gir, GeometryIR), "must return a GeometryIR"
    assert gir.dim == dim, f"expected dim={dim}, got {gir.dim}"
    assert gir.n_regions > 0, "no regions produced"
    for r in gir.regions:
        assert len(r.exterior.points) >= 3, f"region {r.id!r} exterior has <3 points"
    if dim == 2:
        roles = {r.role for r in gir.regions}
        for need in REQUIRED_ROLES_2D:
            assert need in roles, f"missing required role {need.value!r}"
    # round-trips through JSON — the wire format between modules
    GeometryIR.model_validate_json(gir.model_dump_json())


def assert_geometry_provider(provider: Any, *, dim: int = 2, payload: Optional[dict] = None) -> GeometryIR:
    """Run a geometry module through the full conformance gate; returns its GeometryIR."""
    man = provider.manifest()
    assert man.capability in ("geometry.2d", "geometry.3d"), \
        f"geometry provider capability must be geometry.2d/3d, got {man.capability!r}"
    assert man.contracts_version, "manifest must stamp contracts_version"
    gir = provider.run(payload)
    assert_geometry_ir(gir, dim=dim)
    return gir
