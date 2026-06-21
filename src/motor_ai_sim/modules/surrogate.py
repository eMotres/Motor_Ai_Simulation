"""surrogate-rf — capability `surrogate`: cheap predicted metrics for a design vector.

Trains on ResultIR datasets; predicts scalars without a full FEM solve so the
optimizer can explore fast. Thin wrapper over the existing surrogate evaluator
(evaluate_design), guarded so it degrades instead of crashing.

Agent brief: own approximation only. Input = a design vector (params); output =
predicted contracts.ResultIR scalars. Keep it FAST — it exists to avoid FEM.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts import CONTRACTS_VERSION, ResultIR, ScalarResults
from ..contracts.adapters import stamp
from .base import ModuleManifest, UIContribution


class SurrogateRF:
    NAME, CAPABILITY, VERSION = "surrogate-rf", "surrogate", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=[],
            inputs=["ParameterSet"], outputs=["ResultIR"],
            summary="Calibrated surrogate -> predicted torque/efficiency for a design (no FEM)",
            ui=UIContribution(panel_id="optimization", title="Optimization",
                              frontend_module="components/optimization/OptimizationPanel", order=60, as_tab=False))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> ResultIR:
        p = payload or {}
        try:
            from motor_ai_sim.optimization.surrogate import evaluate_design  # type: ignore
            pred = evaluate_design(p.get("params") or p.get("design") or {}) or {}
            return ResultIR(physics="surrogate",
                            scalars=ScalarResults(torque_Nm=pred.get("torque_Nm") or pred.get("T_avg_Nm"),
                                                  efficiency=pred.get("efficiency")),
                            provenance=stamp(self.NAME, version=self.VERSION))
        except Exception as e:  # noqa: BLE001
            return ResultIR.failed("surrogate", f"{type(e).__name__}: {e}",
                                   provenance=stamp(self.NAME, version=self.VERSION))
