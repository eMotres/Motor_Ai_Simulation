"""Solver modules — capabilities `solver.em_static`, `solver.em_transient`, `solver.thermal`.

Each consumes a meshed motor + an operating point and returns a ResultIR. They are
thin seams over the proven route functions (get_fem_field2d / get_fem_transient /
routes.thermal.solve_thermal_field); lazy imports + kwarg-filtered delegation keep
the registry cheap and tolerant of signature drift. Mechanical is a roadmap stub
(registered in bootstrap) — no structural solver exists yet.

Agent brief (per solver): own ONE physics. Input = mesh + Excitation; output =
contracts.ResultIR(physics=...). Never mesh or build geometry. Return
ResultIR.failed(...) on solver error so a study degrades, not crashes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts import CONTRACTS_VERSION, ResultIR, ScalarResults
from ..contracts.adapters import result_ir_from_transient, stamp
from .base import ModuleManifest, UIContribution


def _capability_error(exc: Exception) -> str:
    """One line an engineer can act on, out of whatever the route raised.

    The thermal capabilities call route functions, and a route function refuses
    with an ``HTTPException`` whose ``detail`` is this project's structured 422
    ({error, invalid_parameters, error_code}).  ``str()`` on that gives
    ``"422: {'error': ...}"`` — a dict repr in a ResultIR error string, which is
    how a perfectly clear message ("no Electromagnetic run of this machine at
    I = 480.8 A ... run it on the Electromagnetic tab first") reaches a study log
    as punctuation.  So the detail's own ``error`` line is surfaced verbatim, with
    the machine-readable ``error_code`` in front of it where there is one.
    """
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        msg = str(detail.get("error") or detail.get("message") or detail)
        code = detail.get("error_code")
        return f"{code}: {msg}" if code else msg
    if isinstance(detail, str) and detail:
        return detail
    return f"{type(exc).__name__}: {exc}"


def _call_filtered(fn, payload: Optional[Dict[str, Any]]):
    """Call fn with only the payload keys it actually accepts (robust to drift).

    Underscore-prefixed parameters are NEVER taken from a payload: they are a
    function's private plumbing (e.g. ``routes.thermal.solve_thermal_field``'s
    ``_em_map`` — a whole pre-computed loss field the coupled loop hands
    forward), and a study blackboard that happened to carry the name would be
    injecting solver internals from data.
    """
    import inspect
    params = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in (payload or {}).items()
              if k in params and not k.startswith("_")}
    return fn(**kwargs)


class EmStaticSolver:
    NAME, CAPABILITY, VERSION = "solver-em-static", "solver.em_static", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["mesh"],
            inputs=["MeshIR", "Excitation"], outputs=["ResultIR"],
            summary="Magnetostatic field solve (A_z, |B|) at one rotor angle -> ResultIR",
            ui=UIContribution(panel_id="simulation", title="Electromagnetic",
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
            ui=UIContribution(panel_id="simulation", title="Electromagnetic",
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
            ui=UIContribution(panel_id="simulation", title="Electromagnetic",
                              frontend_module="components/simulation/SimulationPanel", order=50))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> ResultIR:
        """Conduction only — this capability NEVER solves electromagnetics.

        Since 2026-09-07 the loss map comes from an Electromagnetic run
        (``solver.em_transient`` / the Electromagnetic tab), and a study that has
        not run one gets ``ResultIR.failed`` carrying the route's own sentence —
        which names the operating point to run — instead of a six-minute solve
        started inside a temperature step.  That is the point of ``depends_on``
        above being ``solver.em_transient``: it is now a real dependency, not a
        recommendation.
        """
        try:
            # The thermal solve moved to routes.thermal on 2026-09-07; this
            # capability calls the SHARED function the /api/thermal/field route
            # calls, so a module study and the Thermal tab can never disagree
            # about what "the temperature map" is.
            from motor_ai_sim.routes.thermal import solve_thermal_field
            res = _call_filtered(solve_thermal_field, payload) or {}
            return ResultIR(physics="thermal",
                            scalars=ScalarResults(t_max_C=res.get("T_max")),
                            provenance=stamp(self.NAME, version=self.VERSION))
        except Exception as e:  # noqa: BLE001
            return ResultIR.failed("thermal", _capability_error(e),
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

    Since 2026-09-07 the loop itself is NOT written here: it is
    ``routes.thermal.solve_coupled``, which ``GET /api/thermal/coupled`` also
    calls.  Two copies of a fixed point drift — one gains a runaway guard or an
    under-relaxation tweak and the other does not — and then a module study and
    the Thermal tab report two different equilibrium temperatures for one motor.
    This class is now the ResultIR adapter around that one function.

    "EM ↔ thermal" no longer means this capability SOLVES the EM side (same day,
    same user decision): the loss map is taken from an Electromagnetic run and
    only the copper is moved between passes.  With no matching run the capability
    fails with the route's own message naming the run to make — a study degrades
    with an instruction, rather than quietly starting the very solver the two
    tabs were separated to keep apart.
    """

    NAME, CAPABILITY, VERSION = "solver-em-thermal", "solver.em_thermal", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["solver.thermal"],
            inputs=["MeshIR", "Excitation"], outputs=["ResultIR"],
            summary="Coupled EM<->thermal: iterate loss<->temperature to the equilibrium operating point",
            ui=UIContribution(panel_id="simulation", title="Electromagnetic",
                              frontend_module="components/simulation/SimulationPanel", order=50, as_tab=False))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> ResultIR:
        try:
            from motor_ai_sim.routes.thermal import (COUPLED_TOL_C,
                                                     solve_coupled,
                                                     solve_thermal_field)
            p = dict(payload or {})
            # Only the keys the field solve actually takes travel into the loop
            # (the payload is a whole study's blackboard); the loop's own knobs
            # are read off the payload here, with the route's defaults.  The
            # underscore-prefixed ones are the loop's OWN plumbing (the map it
            # carries between passes) and are never taken from a blackboard.
            import inspect
            _accepts = [k for k in inspect.signature(solve_thermal_field).parameters
                        if not k.startswith("_")]
            out = solve_coupled(
                # Defaults track the /coupled route's, so a module study and the
                # Thermal tab converge to the same temperature: since 2026-09-07
                # the loss map is solved once and the passes are seconds, so the
                # loop runs to 0.5 K instead of 2 K.
                max_iter=max(1, int(p.get("max_iter", 12))),
                tol_c=float(p.get("tol_C", COUPLED_TOL_C)),
                relax=float(p.get("relax", 0.6)),
                # `verify_em` is deliberately NOT forwarded: the loop no longer
                # has the parameter, because the audit it named was an
                # electromagnetic solve.  A blackboard that still carries the key
                # is ignored here rather than crashing a study on a TypeError.
                **{k: v for k, v in p.items() if k in _accepts})
            th: Dict[str, Any] = out.get("field") or {}
            hist = out.get("coil_temp_history_C") or []
            T = float(out.get("coil_temp_converged_C") or 0.0)
            converged = bool(out.get("converged"))
            runaway = bool(out.get("runaway"))
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
                     "P_loss_total_W": th.get("P_loss_total_W"),
                     "cooling": th.get("cooling"),
                     # Where the one loss map came from and how the copper was
                     # moved off it — a study that cannot say which run its
                     # losses belong to is not reproducible.
                     "loss_source": out.get("loss_source"),
                     "copper_scaling": out.get("copper_scaling")},
                provenance=prov)
        except Exception as e:  # noqa: BLE001
            return ResultIR.failed("em_thermal", _capability_error(e),
                                   provenance=stamp(self.NAME, version=self.VERSION))
