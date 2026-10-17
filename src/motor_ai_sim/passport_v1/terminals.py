"""Passport v1 re-export of terminal resistance functions."""
from __future__ import annotations

from motor_ai_sim.terminals import (
    AWG_TABLE,
    ALPHA_CU,
    RHO_CU_20,
    awg_to_area_mm2,
    rho_cu,
    terminal_loss_W,
    terminal_phase_resistance,
    validate_terminals,
)

__all__ = [
    "AWG_TABLE",
    "ALPHA_CU",
    "RHO_CU_20",
    "awg_to_area_mm2",
    "rho_cu",
    "terminal_loss_W",
    "terminal_phase_resistance",
    "validate_terminals",
]
