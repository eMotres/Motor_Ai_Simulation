"""Closed-loop signal contracts — the controller <-> simulation exchange.

These make module I/O explicit and typed:
  * a CONTROLLER reads `MachineState` (rotor position, speed, currents, torque)
    and writes a `ControlSignal` (the drive command);
  * a SIMULATION consumes an `Excitation` (the operating point it applies) and
    emits `MachineState`.
`ControlSignal.to_excitation()` maps a command into the solver's Excitation, so a
controller and a solver close the loop through declared interfaces — not ad-hoc dicts.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import Provenance


class Excitation(BaseModel):
    """Simulation INPUT — the operating point / drive the solver applies."""

    model_config = ConfigDict(extra="allow")

    i_phase_rms_A: float = 0.0
    gamma_deg: float = 0.0                 # current-vector angle from the q-axis (FOC)
    rpm: float = 0.0
    n_steps_per_period: int = 24
    coil_temp_c: float = 120.0
    ambient_temp_c: float = 40.0
    mode: str = "current"                  # "current" | "voltage"
    v_phase_rms_V: Optional[float] = None


class ControlSignal(BaseModel):
    """Controller OUTPUT -> simulation: the drive command for the next step."""

    model_config = ConfigDict(extra="forbid")

    mode: str = "current"                  # "current" | "voltage" | "duty"
    i_phase_rms_A: Optional[float] = None
    gamma_deg: Optional[float] = None      # commanded current-vector angle
    v_phase_rms_V: Optional[float] = None
    duty: Optional[float] = None
    provenance: Optional[Provenance] = None

    def to_excitation(self, *, rpm: float = 0.0, **extra) -> "Excitation":
        """Map this command into a solver Excitation (closes controller -> sim)."""
        return Excitation(
            i_phase_rms_A=float(self.i_phase_rms_A or 0.0),
            gamma_deg=float(self.gamma_deg or 0.0),
            rpm=float(rpm), mode=self.mode, v_phase_rms_V=self.v_phase_rms_V, **extra)


class MachineState(BaseModel):
    """Simulation OUTPUT -> controller: state fed back each step (rotor position, …)."""

    model_config = ConfigDict(extra="forbid")

    time_s: Optional[float] = None
    rotor_angle_deg: Optional[float] = None
    speed_rpm: Optional[float] = None
    i_a_A: Optional[float] = None
    i_b_A: Optional[float] = None
    i_c_A: Optional[float] = None
    torque_Nm: Optional[float] = None
    provenance: Optional[Provenance] = None
