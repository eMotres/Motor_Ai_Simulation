"""Build the default module registry — the complete portal module catalog.

Real wrappers over proven code where it exists (geometry-2d, mesh, the three
solvers, surrogate, optimization, cost, users) + roadmap stubs (geometry-3d,
mechanical). The kernel takes this registry and resolves study DAGs against it.
check_dependencies() guarantees every depends_on capability has a provider.
"""
from __future__ import annotations

from ..contracts import CONTRACTS_VERSION
from .base import ModuleManifest, StubModule, UIContribution
from .controller import FocController
from .cost import BasicCost
from .geometry_2d import AeroStatorGeometry2D
from .geometry_3d import AeroStatorGeometry3D
from .mesh import SkfemMesh2D
from .optimization import Optimizer
from .registry import ModuleRegistry
from .solvers import EmStaticSolver, EmTransientSolver, ThermalSolver
from .surrogate import SurrogateRF
from .users import UsersModule


def _mechanical_stub() -> StubModule:
    return StubModule(ModuleManifest(
        name="solver-mechanical", version="0.0.1", capability="solver.mechanical", kind="compute",
        contracts_version=CONTRACTS_VERSION, depends_on=["mesh"],
        inputs=["MeshIR"], outputs=["ResultIR"],
        summary="ROADMAP: structural stress + modal on the same mesh -> ResultIR(stress_max_MPa)",
        ui=UIContribution(panel_id="simulation", title="Simulation", order=50, as_tab=False)))


def default_registry() -> ModuleRegistry:
    reg = ModuleRegistry()
    for module in (
        AeroStatorGeometry2D(),   # geometry.2d
        AeroStatorGeometry3D(),   # geometry.3d  -> geometry.2d   (roadmap)
        SkfemMesh2D(),            # mesh         -> geometry.2d
        EmStaticSolver(),         # solver.em_static     -> mesh
        EmTransientSolver(),      # solver.em_transient  -> mesh
        ThermalSolver(),          # solver.thermal       -> solver.em_transient
        _mechanical_stub(),       # solver.mechanical    -> mesh   (roadmap)
        FocController(),          # controller   (MachineState -> ControlSignal)
        SurrogateRF(),            # surrogate
        Optimizer(),              # optimization -> surrogate + solver.em_transient
        BasicCost(),              # cost         -> geometry.2d
        UsersModule(),            # users
    ):
        reg.register(module)
    reg.check_dependencies()
    return reg
