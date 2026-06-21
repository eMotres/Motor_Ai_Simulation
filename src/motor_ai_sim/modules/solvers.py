"""Solver modules — capabilities `solver.em_static`, `solver.em_transient`, `solver.thermal`.

Each consumes a meshed motor + an operating point and returns a ResultIR. They are
thin seams over the proven route functions (get_fem_field2d / get_fem_transient /
get_thermal_field2d); lazy imports + kwarg-filtered delegation keep the registry
cheap and tolerant of signature drift. Mechanical is a roadmap stub (registered in
bootstrap) — no structural solver exists yet.

Agent brief (per solver): own ONE physics. Input = mesh + Excitation; output =
contracts.ResultIR(physics=...). Never mesh or build geometry. Return
ResultIR.failed(...) on solver error so a study degrades, not crashes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts import CONTRACTS_VERSION, ResultIR, ScalarResults
from ..contracts.adapters import result_ir_from_transient, stamp
from .base import ModuleManifest, UIContribution


def _call_filtered(fn, payload: Optional[Dict[str, Any]]):
    """Call fn with only the payload keys it actually accepts (robust to drift)."""
    import inspect
    params = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in (payload or {}).items() if k in params}
    return fn(**kwargs)


class EmStaticSolver:
    NAME, CAPABILITY, VERSION = "solver-em-static", "solver.em_static", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["mesh"],
            summary="Magnetostatic field solve (A_z, |B|) at one rotor angle -> ResultIR",
            ui=UIContribution(panel_id="simulation", title="Simulation",
                              frontend_module="components/simulation/SimulationPanel", order=50))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> ResultIR:
        try:
            from motor_ai_sim.routes.simulation import get_fem_field2d
            res = _call_filtered(get_fem_field2d, payload) or {}
            return ResultIR(physics="em_static",
                            scalars=ScalarResults(torque_Nm=res.get("torque_Nm")),
                            provenance=stamp(self.NAME, version=self.VERSION))
        except Exception as e:  # noqa: BLE001
            return ResultIR.failed("em_static", f"{type(e).__name__}: {e}",
                                   provenance=stamp(self.NAME, version=self.VERSION))


class EmTransientSolver:
    NAME, CAPABILITY, VERSION = "solver-em-transient", "solver.em_transient", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["mesh"],
            summary="Sliding-band transient over one electrical period -> torque/losses/V ResultIR",
            ui=UIContribution(panel_id="simulation", title="Simulation",
                              frontend_module="components/simulation/SimulationPanel", order=50))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> ResultIR:
        try:
            from motor_ai_sim.routes.simulation import get_fem_transient
            res = _call_filtered(get_fem_transient, payload) or {}
            return result_ir_from_transient(res, provenance=stamp(self.NAME, version=self.VERSION))
        except Exception as e:  # noqa: BLE001
            return ResultIR.failed("em_transient", f"{type(e).__name__}: {e}",
                                   provenance=stamp(self.NAME, version=self.VERSION))


class ThermalSolver:
    NAME, CAPABILITY, VERSION = "solver-thermal", "solver.thermal", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["solver.em_transient"],
            summary="Steady 2D heat conduction from EM losses -> temperature-map ResultIR (T_max)",
            ui=UIContribution(panel_id="simulation", title="Simulation",
                              frontend_module="components/simulation/SimulationPanel", order=50))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> ResultIR:
        try:
            from motor_ai_sim.routes.simulation import get_thermal_field2d
            res = _call_filtered(get_thermal_field2d, payload) or {}
            return ResultIR(physics="thermal",
                            scalars=ScalarResults(t_max_C=res.get("T_max")),
                            provenance=stamp(self.NAME, version=self.VERSION))
        except Exception as e:  # noqa: BLE001
            return ResultIR.failed("thermal", f"{type(e).__name__}: {e}",
                                   provenance=stamp(self.NAME, version=self.VERSION))
