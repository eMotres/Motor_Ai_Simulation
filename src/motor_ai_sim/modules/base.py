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
    contracts_version: str
    summary: str = ""
    ui: Optional[UIContribution] = None


@runtime_checkable
class Module(Protocol):
    """Structural interface — any object with these two methods is a module."""

    def manifest(self) -> ModuleManifest: ...
    def run(self, payload: Any) -> Any: ...
