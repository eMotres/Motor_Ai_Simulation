"""Terminal resistance and external lead / phase board model (owner decision 2026-10-17).

Physical motor resistance has two distinct quantities:
  1. Winding resistance (inside motor core): what the electromagnetic solver
     computes; drives in-motor copper loss (P_cu = 3·I²·R) and thermal heat.
  2. Terminal resistance: winding + external lead wires + phase board PCB traces +
     solder joints.  This is what the factory and customers measure with a
     4-wire milliohm meter at the lead ends at 20 °C.

Configuration schema (`terminals:` block):
  lead_length_mm: float (mm)
  lead_awg: float | int (AWG wire gauge)
  lead_area_mm2: float (mm² bare copper conductor area)
  board_mohm: float (mΩ phase board copper traces + solder joints)
  lead_wire_count: int (default 3)
  lead_temp_c: float (temperature of leads, default 20.0 °C)
  notes / source: str

When `terminals:` is absent:
  terminal resistance = winding resistance, with visible note "terminal resistance not stated".
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

RHO_CU_20 = 1.724e-8  # Ω·m at 20 °C
ALPHA_CU = 0.00393   # 1/K

# Standard wire gauge cross-section table (mm²)
# For metric flexible silicone wire commonly used in motor leads (e.g. 15 AWG = 1.50 mm² nominal)
AWG_TABLE: Dict[int, float] = {
    8: 8.37,
    10: 5.26,
    12: 3.31,
    13: 2.50,
    14: 2.08,
    15: 1.50,  # 15 AWG 200 °C silicone wire (CIANO14 40 BOM Part 13)
    16: 1.31,
    18: 0.823,
    20: 0.518,
    22: 0.326,
    24: 0.205,
    26: 0.129,
    28: 0.081,
    30: 0.051,
}


def awg_to_area_mm2(awg: float | int) -> float:
    """Convert American Wire Gauge (AWG) to conductor area in mm²."""
    a_int = int(round(awg))
    if a_int in AWG_TABLE and abs(float(awg) - a_int) < 1e-3:
        return AWG_TABLE[a_int]
    # Standard formula for solid round wire: d = 0.127 * 92 ** ((36 - awg) / 39) mm
    d = 0.127 * (92.0 ** ((36.0 - float(awg)) / 39.0))
    return math.pi * 0.25 * d * d


def rho_cu(temp_c: float) -> float:
    """Copper resistivity at temperature in °C."""
    return RHO_CU_20 * (1.0 + ALPHA_CU * (float(temp_c) - 20.0))


def validate_terminals(block: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Validate and normalize the optional `terminals:` configuration block.

    Returns a structured dictionary with precalculated resistances and notes.
    Fails closed if values are invalid or contradictory.
    """
    if not block:
        return {
            "stated": False,
            "lead_length_mm": 0.0,
            "lead_area_mm2": None,
            "lead_awg": None,
            "board_mohm": 0.0,
            "board_ohm": 0.0,
            "lead_resistance_20_ohm": 0.0,
            "delta_R_phase_20_ohm": 0.0,
            "delta_R_line_line_20_ohm": 0.0,
            "note": "terminal resistance not stated (equals winding resistance)",
        }

    length_mm = float(block.get("lead_length_mm", 0.0))
    if length_mm < 0.0:
        raise ValueError(f"terminals.lead_length_mm must be >= 0, got {length_mm}")

    awg = block.get("lead_awg")
    awg_val = float(awg) if awg is not None else None

    area_mm2 = block.get("lead_area_mm2")
    if area_mm2 is not None:
        area_val = float(area_mm2)
        if area_val <= 0.0:
            raise ValueError(f"terminals.lead_area_mm2 must be > 0, got {area_val}")
    elif awg_val is not None:
        area_val = awg_to_area_mm2(awg_val)
    else:
        area_val = None

    if length_mm > 0.0 and area_val is None:
        raise ValueError("terminals.lead_length_mm > 0 requires lead_area_mm2 or lead_awg")

    board_mohm = float(block.get("board_mohm", 0.0))
    if board_mohm < 0.0:
        raise ValueError(f"terminals.board_mohm must be >= 0, got {board_mohm}")
    board_ohm = board_mohm * 1e-3

    if length_mm > 0.0 and area_val:
        r_lead_20 = RHO_CU_20 * (length_mm * 1e-3) / (area_val * 1e-6)
    else:
        r_lead_20 = 0.0

    delta_R_20 = r_lead_20 + board_ohm

    parts = []
    if length_mm > 0.0 and area_val:
        awg_str = f" ({int(awg_val)} AWG)" if (awg_val is not None and abs(awg_val - round(awg_val)) < 1e-3) else ""
        parts.append(f"{length_mm:g} mm{awg_str} {area_val:.2f} mm² lead ({r_lead_20 * 1e3:.2f} mΩ)")
    if board_mohm > 0.0:
        parts.append(f"{board_mohm:.2f} mΩ board")

    note = " + ".join(parts) if parts else "terminal resistance specified without added leads/board"
    if block.get("notes"):
        note += f"; {block['notes']}"
    elif block.get("source"):
        note += f"; source: {block['source']}"

    return {
        "stated": True,
        "lead_length_mm": length_mm,
        "lead_awg": awg_val,
        "lead_area_mm2": area_val,
        "board_mohm": board_mohm,
        "board_ohm": board_ohm,
        "lead_resistance_20_ohm": r_lead_20,
        "delta_R_phase_20_ohm": delta_R_20,
        "delta_R_line_line_20_ohm": 2.0 * delta_R_20,
        "note": note,
    }


def terminal_phase_resistance(
    R_winding_ohm: float,
    terminals_block: Optional[Mapping[str, Any]] = None,
    temp_c: float = 20.0,
) -> Tuple[float, Dict[str, Any]]:
    """Compute terminal phase resistance and breakdown at a given temperature.

    Args:
        R_winding_ohm: Winding phase resistance at temp_c.
        terminals_block: Optional `terminals:` configuration block.
        temp_c: Temperature in °C (default 20.0).

    Returns:
        (R_terminal_ohm, terminal_info_dict)
    """
    info = validate_terminals(terminals_block)
    if not info["stated"]:
        return float(R_winding_ohm), dict(info, R_terminal_ohm=float(R_winding_ohm))

    length_mm = info["lead_length_mm"]
    area_mm2 = info["lead_area_mm2"]
    board_ohm = info["board_ohm"]

    # Resistance of lead wire at temp_c
    if length_mm > 0.0 and area_mm2:
        r_lead = rho_cu(temp_c) * (length_mm * 1e-3) / (area_mm2 * 1e-6)
    else:
        r_lead = 0.0

    delta_R = r_lead + board_ohm
    R_term = float(R_winding_ohm) + delta_R

    res = dict(info)
    res["temp_c"] = float(temp_c)
    res["R_winding_ohm"] = float(R_winding_ohm)
    res["lead_resistance_ohm"] = r_lead
    res["board_resistance_ohm"] = board_ohm
    res["delta_R_phase_ohm"] = delta_R
    res["R_terminal_ohm"] = R_term
    res["R_line_line_terminal_ohm"] = 2.0 * R_term
    return R_term, res


def terminal_loss_W(
    I_phase_rms: float,
    terminals_block: Optional[Mapping[str, Any]] = None,
    temp_c: float = 20.0,
) -> Dict[str, float]:
    """Calculate external 3-phase I²R losses in lead wires and phase board.

    These losses occur OUTSIDE the motor and do NOT enter the motor's thermal network.
    They are reported separately in the efficiency / loss budget as 'leads + board'.
    """
    info = validate_terminals(terminals_block)
    if not info["stated"] or float(I_phase_rms) <= 0.0:
        return {
            "P_leads_board_total_W": 0.0,
            "P_leads_W": 0.0,
            "P_board_W": 0.0,
            "delta_R_phase_ohm": 0.0,
        }

    length_mm = info["lead_length_mm"]
    area_mm2 = info["lead_area_mm2"]
    board_ohm = info["board_ohm"]

    if length_mm > 0.0 and area_mm2:
        r_lead = rho_cu(temp_c) * (length_mm * 1e-3) / (area_mm2 * 1e-6)
    else:
        r_lead = 0.0

    I = float(I_phase_rms)
    # 3-phase star: 3 * I_phase_rms^2 * R_ext_per_phase
    p_leads = 3.0 * (I ** 2) * r_lead
    p_board = 3.0 * (I ** 2) * board_ohm
    return {
        "P_leads_board_total_W": p_leads + p_board,
        "P_leads_W": p_leads,
        "P_board_W": p_board,
        "delta_R_phase_ohm": r_lead + board_ohm,
    }
