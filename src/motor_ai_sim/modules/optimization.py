"""optimization — capability `optimization`: search the design space toward a target.

Orchestrates the surrogate (cheap) + the FEM solvers (expensive) to propose a
better design. Thin wrapper over the existing refine/eval machinery, guarded.

Agent brief: own the search loop only — propose design vectors, score them via
the `surrogate` and `solver.em_transient` capabilities (never re-implement them),
return the best design + its ResultIR.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts import CONTRACTS_VERSION
from .base import ModuleManifest, UIContribution


class Optimizer:
    NAME, CAPABILITY, VERSION = "optimization-descent", "optimization", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["surrogate", "solver.em_transient"],
            inputs=["ParameterSet"], outputs=["ParameterSet", "ResultIR"],
            summary="Surrogate-guided + FEM-confirmed search (torque-density x efficiency)",
            ui=UIContribution(panel_id="optimization", title="Optimization",
                              frontend_module="components/optimization/OptimizationPanel", order=60))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Single FEM-confirm eval of a candidate (real loop lives in the route).

        This used to call ``run_one(p.get("params"))`` — ONE argument against a
        signature that requires four — so every invocation of the capability
        returned ok=False with a TypeError since the day it was written (nothing
        in the app calls it; the module is registered for its MANIFEST, which
        feeds the panel registry).  Now it maps the kernel payload onto
        ``run_one``'s real signature, with the operating point defaulting to the
        active config's Simulation values — the same source every other eval
        path uses.
        """
        p = payload or {}
        try:
            from motor_ai_sim.config import get_config
            from motor_ai_sim.optimization import refine_proc  # type: ignore
            sim = get_config().get("simulation", {}) or {}
            res = refine_proc.run_one(
                dict(p.get("params") or p.get("overrides") or {}),
                float(p.get("current_a", sim.get("max_current", 85.0) or 85.0)),
                int(p.get("steps", p.get("steps_per_period", 60)) or 60),
                float(p.get("coil_temp_c", sim.get("coil_temp_c", 120.0) or 120.0)),
                gamma_deg=float(p.get("gamma_deg",
                                      sim.get("phase_offset_deg", 0.0) or 0.0)),
                mesh_size_mm=float(p.get("mesh_size_mm", 4.0) or 4.0),
                min_size_mm=float(p.get("min_size_mm", 0.3) or 0.3),
                n_sectors=int(p.get("n_sectors", -1) or -1),
                rpm=(float(p["rpm"]) if p.get("rpm") else None))
            return {"ok": True, "result": res}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
