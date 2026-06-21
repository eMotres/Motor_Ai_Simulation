"""geometry-2d-aerostator — AeroStator Core's parametric 2D cross-section, as a module.

A THIN SEAM over the existing CadQueryMotor: it reuses the proven geometry build
and emits GeometryIR(dim=2) via the contracts adapter. The existing endpoints are
untouched — this is the module-shaped entry the kernel/registry calls, and the
input the future geometry-3d module (capability "geometry.3d", depends_on
["geometry.2d"]) will extrude/revolve into solids.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ...contracts import CONTRACTS_VERSION, GeometryIR
from ...contracts.adapters import geometry_ir_from_polys, stamp
from ..base import ModuleManifest, UIContribution

_PASSPORT_KEYS = (
    "stator_outer_radius", "stator_inner_radius", "rotor_outer_radius",
    "rotor_inner_radius", "shaft_radius", "num_slots", "num_poles",
)


class AeroStatorGeometry2D:
    """The reference geometry.2d provider."""

    NAME = "geometry-2d-aerostator"
    CAPABILITY = "geometry.2d"
    VERSION = "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME,
            version=self.VERSION,
            capability=self.CAPABILITY,
            kind="compute",
            contracts_version=CONTRACTS_VERSION,
            summary="AeroStator Core fully-parametric 2D cross-section -> GeometryIR(dim=2)",
            ui=UIContribution(
                panel_id="geometry",
                title="Geometry",
                frontend_module="components/geometry/GeometryPanel",
                order=20,
                as_tab=True,
            ),
        )

    def build(self, params: Optional[Dict[str, Any]] = None, *, rotor_angle_deg: float = 0.0) -> GeometryIR:
        """Build the cross-section from the live config (optionally overridden) -> GeometryIR."""
        # Imports are local so the module stays import-light until actually run.
        from motor_ai_sim.config import get_config
        from motor_ai_sim.cadquery_geometry import CadQueryMotor

        cfg = get_config()
        geo = dict(cfg["geometry"])
        if params:
            geo.update(params)
        mats = cfg.get("materials", {}) or {}

        t0 = time.time()
        motor = CadQueryMotor()
        motor.set_parameters(geo)
        polys = motor.get_2d_polygons(rotor_angle_deg)

        passport: Dict[str, Any] = {}
        try:
            from motor_ai_sim.services.geometry_service import get_current_geometry
            p = get_current_geometry(reload=True)
            for k in _PASSPORT_KEYS:
                if hasattr(p, k):
                    passport[k] = getattr(p, k)
        except Exception:
            pass  # passport is best-effort metadata; geometry is still valid without it

        return geometry_ir_from_polys(
            polys,
            passport=passport,
            parameters=geo,
            materials={
                "stator": mats.get("stator_core"),
                "rotor": mats.get("rotor_core"),
                "magnet": mats.get("magnet"),
            },
            provenance=stamp(self.NAME, version=self.VERSION, elapsed_s=time.time() - t0),
        )

    def run(self, payload: Optional[Dict[str, Any]] = None) -> GeometryIR:
        payload = payload or {}
        return self.build(payload.get("params"), rotor_angle_deg=float(payload.get("rotor_angle_deg", 0.0)))
