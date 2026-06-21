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
            summary="Surrogate-guided + FEM-confirmed search (torque-density x efficiency)",
            ui=UIContribution(panel_id="optimization", title="Optimization",
                              frontend_module="components/optimization/OptimizationPanel", order=60))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Single FEM-confirm eval of a candidate (real loop lives in the route)."""
        p = payload or {}
        try:
            from motor_ai_sim.optimization import refine_proc  # type: ignore
            return {"ok": True, "result": refine_proc.run_one(p.get("params") or {})}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
