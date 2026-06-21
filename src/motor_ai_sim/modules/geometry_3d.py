"""geometry-3d — capability `geometry.3d`, depends_on `geometry.2d`.

ROADMAP module: extrude/revolve the 2D cross-section (GeometryIR dim=2) into solids
-> GeometryIR(dim=3). Registered now so the dependency graph + UI map are complete;
run() raises until the extruder is built. Vadim's explicit design: 3D builds ON the
2D module, never re-deriving the cross-section.

Agent brief (when implemented): consume geometry.2d's GeometryIR; produce dim=3
regions; keep region ids aligned with the 2D source so meshing/solvers map across.
"""
from __future__ import annotations

from typing import Any, Optional

from ..contracts import CONTRACTS_VERSION
from .base import ModuleManifest, UIContribution


class AeroStatorGeometry3D:
    NAME, CAPABILITY, VERSION = "geometry-3d-aerostator", "geometry.3d", "0.0.1"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["geometry.2d"],
            summary="ROADMAP: extrude/revolve geometry.2d cross-section -> GeometryIR(dim=3)",
            ui=UIContribution(panel_id="geometry3d", title="3D", frontend_module=None, order=25, as_tab=False))

    def run(self, payload: Optional[Any] = None) -> Any:
        raise NotImplementedError(
            "geometry-3d not built yet — will extrude/revolve the geometry.2d GeometryIR into solids")
