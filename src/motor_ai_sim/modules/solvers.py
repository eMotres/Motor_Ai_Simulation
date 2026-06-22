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
            inputs=["MeshIR", "Excitation"], outputs=["ResultIR"],
            summary="Magnetostatic field solve (A_z, |B|) at one rotor angle -> ResultIR",
            ui=UIContribution(panel_id="simulation", title="Simulation",
                              frontend_module="components/simulation/SimulationPanel", order=50))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> ResultIR:
        p = payload or {}
        try:
            # ── end-to-end mesh -> solver handoff ────────────────────────────
            # If the `mesh` module ran upstream (run_study threads its MeshIR
            # under payload['upstream']['mesh']), solve on THAT exact mesh — no
            # re-meshing. This is the modules genuinely composing: geometry.2d
            # -> mesh (MeshIR) -> solver.em_static (solves the MeshIR).
            mesh_ir = (p.get("upstream") or {}).get("mesh")
            if mesh_ir is not None and getattr(mesh_ir, "vertices", None) is not None:
                from motor_ai_sim.contracts.adapters import skfem_from_mesh_ir
                from motor_ai_sim.simulation.fem_solver_2d import solve_field2d_on_mesh
                mesh, cell_tags = skfem_from_mesh_ir(mesh_ir)
                res = solve_field2d_on_mesh(
                    mesh, cell_tags,
                    rotor_angle_deg=float(p.get("rotor_angle_deg", 0.0)),
                    gamma_deg=float(p.get("gamma_deg", 0.0)),
                    I_phase_rms=p.get("I_phase_rms"))
                prov = stamp(self.NAME, version=self.VERSION, elapsed_s=res.get("solve_time_s"))
                prov.notes["mesh_source"] = "mesh (upstream MeshIR)"
                prov.notes["n_nodes"] = str(res.get("n_nodes"))
                return ResultIR(
                    physics="em_static",
                    scalars=ScalarResults(b_mag_max_T=res.get("B_mag_max_T"),
                                          b_mag_mean_T=res.get("B_mag_mean_T")),
                    raw=res, provenance=prov)

            # ── fallback: no upstream mesh -> self-meshing field route ────────
            from motor_ai_sim.routes.simulation import get_fem_field2d
            res = _call_filtered(get_fem_field2d, p) or {}
            prov = stamp(self.NAME, version=self.VERSION)
            prov.notes["mesh_source"] = "self (field route)"
            return ResultIR(physics="em_static",
                            scalars=ScalarResults(torque_Nm=res.get("torque_Nm")),
                            provenance=prov)
        except Exception as e:  # noqa: BLE001
            return ResultIR.failed("em_static", f"{type(e).__name__}: {e}",
                                   provenance=stamp(self.NAME, version=self.VERSION))


class EmTransientSolver:
    NAME, CAPABILITY, VERSION = "solver-em-transient", "solver.em_transient", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["mesh"],
            inputs=["MeshIR", "Excitation"], outputs=["ResultIR", "MachineState"],
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
            inputs=["MeshIR", "ResultIR"], outputs=["ResultIR"],
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


class EmThermalCoupled:
    """Coupled EM <-> thermal solve — the inter-module feedback interface in action.

    Loosely-coupled fixed-point iteration:
      EM loss field (at copper temp T)  ->  thermal solve  ->  winding temperature
      -> fed BACK as the next EM operating temp (rho_Cu rises with T => more copper
      loss => hotter) -> repeat until the copper reaches thermal EQUILIBRIUM.

    The single thermal route already does the forward EM->loss->thermal pass; this
    module adds the temperature FEEDBACK loop, so the result is the self-consistent
    operating point instead of an assumed fixed coil temperature. The same pattern
    extends to mechanical (temps -> thermal expansion -> stress -> ...).
    """

    NAME, CAPABILITY, VERSION = "solver-em-thermal", "solver.em_thermal", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["solver.thermal"],
            inputs=["MeshIR", "Excitation"], outputs=["ResultIR"],
            summary="Coupled EM<->thermal: iterate loss<->temperature to the equilibrium operating point",
            ui=UIContribution(panel_id="simulation", title="Simulation",
                              frontend_module="components/simulation/SimulationPanel", order=50, as_tab=False))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> ResultIR:
        try:
            from motor_ai_sim.routes.simulation import get_thermal_field2d
            p = dict(payload or {})
            # Initial copper temp guess: explicit coil_temp_c, else ambient.
            T = float(p.get("coil_temp_c", p.get("ambient_temp", 25.0)) or 25.0)
            relax = float(p.get("relax", 0.6))         # under-relaxation for stability
            tol = float(p.get("tol_C", 0.5))           # convergence on copper temp [°C]
            max_iter = max(1, int(p.get("max_iter", 6)))
            hist = []
            th: Dict[str, Any] = {}
            converged = False
            RUNAWAY_C = 400.0   # past any feasible motor → no stable equilibrium
            for _ in range(max_iter):
                th = _call_filtered(get_thermal_field2d, {**p, "coil_temp_c": T}) or {}
                winding = (th.get("components") or {}).get("winding") or {}
                T_cu = winding.get("avg")
                if T_cu is None:
                    break
                T_cu = float(T_cu)
                hist.append(round(T_cu, 1))
                if T_cu > RUNAWAY_C:
                    T = T_cu
                    break                                   # diverging — thermal runaway
                if abs(T_cu - T) < tol:
                    T = T_cu
                    converged = True
                    break
                T = relax * T_cu + (1.0 - relax) * T        # temperature signal fed BACK to EM
            runaway = (not converged) and bool(hist) and hist[-1] > RUNAWAY_C
            prov = stamp(self.NAME, version=self.VERSION)
            prov.notes["coupling"] = "em<->thermal (loss<->temperature fixed point)"
            prov.notes["coil_temp_history_C"] = ",".join(str(h) for h in hist)
            prov.notes["coil_temp_converged_C"] = str(round(T, 1))
            prov.notes["iterations"] = str(len(hist))
            prov.notes["converged"] = str(converged)
            if runaway:
                prov.notes["warning"] = ("thermal runaway: no stable equilibrium at this operating "
                                         "point — increase cooling (h_conv) or reduce current")
            return ResultIR(
                physics="em_thermal",
                scalars=ScalarResults(t_max_C=th.get("T_max"), p_copper_W=th.get("P_cu_W"),
                                      p_iron_W=th.get("P_fe_W"), p_loss_total_W=th.get("P_loss_total_W"),
                                      coil_temp_converged_C=round(T, 1), converged=converged),
                raw={"coil_temp_converged_C": round(T, 1), "coil_temp_history_C": hist,
                     "iterations": len(hist), "converged": converged, "runaway": runaway,
                     "components": th.get("components"), "T_max": th.get("T_max"), "T_min": th.get("T_min"),
                     "P_cu_W": th.get("P_cu_W"), "P_fe_W": th.get("P_fe_W"),
                     "P_loss_total_W": th.get("P_loss_total_W")},
                provenance=prov)
        except Exception as e:  # noqa: BLE001
            return ResultIR.failed("em_thermal", f"{type(e).__name__}: {e}",
                                   provenance=stamp(self.NAME, version=self.VERSION))
