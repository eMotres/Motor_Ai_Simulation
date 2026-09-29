"""Module framework — the contract every portal module implements.

A module is an autonomous unit (geometry-2d, geometry-3d, mesh, a solver,
surrogate, cost, users, …). Modules couple ONLY through capabilities + contract
IR — never by importing each other — so:
  * geometry-3d can `depends_on=["geometry.2d"]` and consume its GeometryIR
    without a code-level dependency, and
  * each module is owned by its own agent (see the module's CLAUDE.md).

Web interfaces are modules too: a module declares a UIContribution describing the
panel it surfaces in the portal, so the frontend can build itself from manifests.
"""
from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class UIContribution(BaseModel):
    """How a module shows up in the portal frontend (web interface = a module)."""

    panel_id: str                              # stable id the frontend registry keys on
    title: str
    frontend_module: Optional[str] = None      # path the frontend lazy-loads (micro-frontend)
    order: int = 100                           # sort order among panels/tabs
    as_tab: bool = True                        # top-level tab vs embedded panel


class ModuleManifest(BaseModel):
    """Self-description a module returns — the only thing the kernel/registry reads."""

    name: str                                  # "geometry-2d-aerostator"
    version: str = "0.1.0"
    capability: str                            # "geometry.2d" | "geometry.3d" | "mesh" | "solver.em_transient" | "cost" | ...
    kind: str = "compute"                      # "compute" | "ui"
    depends_on: List[str] = Field(default_factory=list)  # upstream CAPABILITIES required (e.g. geometry.3d -> ["geometry.2d"])
    # Explicit I/O interface: the contract TYPES this module consumes / produces.
    # e.g. mesh inputs=["GeometryIR"] outputs=["MeshIR"]; a controller
    # inputs=["MachineState"] outputs=["ControlSignal"]. The kernel checks that a
    # pipeline's stages are port-compatible (upstream outputs cover downstream inputs).
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    contracts_version: str
    summary: str = ""
    ui: Optional[UIContribution] = None


@runtime_checkable
class Module(Protocol):
    """Structural interface — any object with these two methods is a module."""

    def manifest(self) -> ModuleManifest: ...
    def run(self, payload: Any) -> Any: ...


class StubModule:
    """A capability on the roadmap but not implemented yet.

    Carries a REAL manifest (so the module catalog + dependency graph + UI map
    are complete and the portal can show what's coming), but run() raises so a
    caller learns it isn't live yet. This is how the system stays honest about
    partial coverage instead of hiding unbuilt modules.
    """

    def __init__(self, manifest: ModuleManifest) -> None:
        self._m = manifest

    def manifest(self) -> ModuleManifest:
        return self._m

    def run(self, payload: Any) -> Any:
        raise NotImplementedError(
            f"{self._m.name} (capability {self._m.capability!r}) is on the roadmap, not implemented yet")
