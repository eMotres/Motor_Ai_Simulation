"""Build the default module registry for this deployment.

As modules land (mesh, solvers, surrogate, cost, users, geometry-3d, …) they get
registered here. The kernel takes this registry and resolves study DAGs against it.
"""
from __future__ import annotations

from .geometry_2d import AeroStatorGeometry2D
from .registry import ModuleRegistry


def default_registry() -> ModuleRegistry:
    reg = ModuleRegistry()
    reg.register(AeroStatorGeometry2D())
    # future: reg.register(Mesh...()), reg.register(EmTransient...()), reg.register(Geometry3D...()), ...
    reg.check_dependencies()
    return reg
