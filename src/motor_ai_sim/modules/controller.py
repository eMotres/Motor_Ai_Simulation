"""controller-foc — capability `controller`: MachineState -> ControlSignal.

The closed-loop partner of the simulation. It READS the machine state (rotor
angle, speed, measured torque) that the simulation feeds back, and WRITES a
ControlSignal — a phase-current command (magnitude + MTPA angle) to track a
target torque. ControlSignal.to_excitation() turns that command into the solver's
Excitation, so controller and solver exchange through declared interfaces:

    sim --MachineState--> controller --ControlSignal--> sim (next step)

Agent brief: own control law only. Input = MachineState (+ target/gains in
payload); output = ControlSignal. Never solve fields or mesh.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts import CONTRACTS_VERSION, ControlSignal
from ..contracts.adapters import stamp
from .base import ModuleManifest, UIContribution


class FocController:
    NAME, CAPABILITY, VERSION = "controller-foc", "controller", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=[],
            inputs=["MachineState"], outputs=["ControlSignal"],
            summary="FOC/MTPA current controller: MachineState -> ControlSignal (i, gamma) tracking target torque",
            ui=UIContribution(panel_id="controller", title="Controller", frontend_module=None,
                              order=55, as_tab=False))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> ControlSignal:
        """payload: {state: MachineState-like, target_torque_Nm, kt_Nm_per_A, mtpa_gamma_deg, kp, i_max_A}."""
        p = payload or {}
        state = p.get("state") or {}
        target = float(p.get("target_torque_Nm", 0.0))
        kt = max(float(p.get("kt_Nm_per_A", 0.13)), 1e-6)     # torque constant estimate (Nm/A)
        gamma = float(p.get("mtpa_gamma_deg", -36.0))         # MTPA angle (negative for spoke-PM)
        i_max = float(p.get("i_max_A", 200.0))
        kp = float(p.get("kp", 0.5))
        measured = float(state.get("torque_Nm") or 0.0)
        # feedforward (target/Kt) + P feedback on the torque error, clamped to i_max
        i_cmd = abs(target) / kt + kp * (target - measured) / kt
        i_cmd = max(0.0, min(i_max, i_cmd))
        return ControlSignal(mode="current", i_phase_rms_A=round(i_cmd, 2), gamma_deg=gamma,
                             provenance=stamp(self.NAME, version=self.VERSION))
