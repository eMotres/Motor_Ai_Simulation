"""Tests for terminal resistance, external lead wires, and phase board losses.

Owner decision 2026-10-17:
- Two resistances everywhere: winding (in-motor, electromagnetic/thermal) and
  terminal (winding + external leads + phase board + solder joints, 4-wire at 20 °C).
- Optional `terminals:` block in configuration schema.
- Default when absent: terminal = winding with visible note "terminal resistance not stated".
- Outside I²R losses go into efficiency / loss budget as 'leads + board', NOT into motor thermal network.
"""
from __future__ import annotations

import math
import pytest
from pathlib import Path

from motor_ai_sim.terminals import (
    AWG_TABLE,
    awg_to_area_mm2,
    rho_cu,
    validate_terminals,
    terminal_phase_resistance,
    terminal_loss_W,
)
from motor_ai_sim.simulation.field_ops import (
    terminal_phase_resistance as field_ops_tpr,
    terminal_loss_W as field_ops_tlw,
)


def test_awg_conversion():
    # 15 AWG metric flexible silicone wire used on CIANO14 40
    assert awg_to_area_mm2(15) == 1.50
    assert awg_to_area_mm2(15.0) == 1.50
    assert awg_to_area_mm2(10) == 5.26
    assert awg_to_area_mm2(12) == 3.31
    assert awg_to_area_mm2(14) == 2.08
    assert awg_to_area_mm2(16) == 1.31
    # Arbitrary non-standard gauge uses standard formula
    area_15_5 = awg_to_area_mm2(15.5)
    assert 1.31 < area_15_5 < 1.50


def test_validate_terminals_empty():
    info = validate_terminals(None)
    assert info["stated"] is False
    assert info["lead_length_mm"] == 0.0
    assert info["lead_area_mm2"] is None
    assert info["board_mohm"] == 0.0
    assert info["lead_resistance_20_ohm"] == 0.0
    assert info["delta_R_phase_20_ohm"] == 0.0
    assert "not stated" in info["note"]

    info2 = validate_terminals({})
    assert info2["stated"] is False
    assert "not stated" in info2["note"]


def test_validate_terminals_validation_errors():
    with pytest.raises(ValueError, match="lead_length_mm must be >= 0"):
        validate_terminals({"lead_length_mm": -10.0})

    with pytest.raises(ValueError, match="requires lead_area_mm2 or lead_awg"):
        validate_terminals({"lead_length_mm": 100.0})

    with pytest.raises(ValueError, match="lead_area_mm2 must be > 0"):
        validate_terminals({"lead_length_mm": 100.0, "lead_area_mm2": -1.0})

    with pytest.raises(ValueError, match="board_mohm must be >= 0"):
        validate_terminals({"board_mohm": -0.5})


def test_validate_terminals_ciano14_40_block():
    block = {
        "lead_length_mm": 200.0,
        "lead_awg": 15,
        "lead_area_mm2": 1.50,
        "board_mohm": 0.30,
        "notes": "200 mm 15 AWG silicone lead wire + Phase Board Rev C; factory acceptance at 20 °C",
    }
    info = validate_terminals(block)
    assert info["stated"] is True
    assert info["lead_length_mm"] == 200.0
    assert info["lead_awg"] == 15.0
    assert info["lead_area_mm2"] == 1.50
    assert info["board_mohm"] == 0.30
    assert info["board_ohm"] == pytest.approx(0.00030)

    # Lead resistance: rho_cu_20 * L / A = 1.724e-8 * 0.2 / (1.5e-6) = 2.2987 mΩ
    expected_lead_r = 1.724e-8 * 0.2 / 1.5e-6
    assert info["lead_resistance_20_ohm"] == pytest.approx(expected_lead_r, rel=1e-4)

    expected_delta = expected_lead_r + 0.00030
    assert info["delta_R_phase_20_ohm"] == pytest.approx(expected_delta, rel=1e-4)
    assert info["delta_R_line_line_20_ohm"] == pytest.approx(2.0 * expected_delta, rel=1e-4)
    assert "200 mm (15 AWG) 1.50 mm² lead (2.30 mΩ) + 0.30 mΩ board" in info["note"]


def test_terminal_phase_resistance_unspecified():
    R_w = 0.007262
    r_term, info = terminal_phase_resistance(R_w, None)
    assert r_term == pytest.approx(R_w)
    assert info["stated"] is False
    assert "not stated" in info["note"]

    # field_ops wrapper
    r_term_fo, info_fo = field_ops_tpr(R_w, None)
    assert r_term_fo == pytest.approx(R_w)


def test_terminal_phase_resistance_ciano14_40_l12_and_l20():
    block = {
        "lead_length_mm": 200.0,
        "lead_awg": 15,
        "board_mohm": 0.30,
    }
    # CIANO14 40 L12 with CAD coil end-turns: R_winding = 7.262 mΩ
    r_w_l12 = 0.007262
    r_term_l12, info_l12 = terminal_phase_resistance(r_w_l12, block, temp_c=20.0)
    # Expected: 7.262 + 2.299 (lead) + 0.300 (board) = 9.86 mΩ
    assert r_term_l12 * 1e3 == pytest.approx(9.8607, abs=0.01)
    # Line-to-line = 2 * R_phase = 19.72 mΩ (matches prototype test 19.6 mΩ within 0.6%)
    assert info_l12["R_line_line_terminal_ohm"] * 1e3 == pytest.approx(19.721, abs=0.02)

    # CIANO14 40 L20 with CAD coil end-turns: R_winding = 9.836 mΩ
    r_w_l20 = 0.009836
    r_term_l20, info_l20 = terminal_phase_resistance(r_w_l20, block, temp_c=20.0)
    # Expected: 9.836 + 2.299 + 0.300 = 12.435 mΩ (~12.44 mΩ, disproving 13.2 mΩ)
    assert r_term_l20 * 1e3 == pytest.approx(12.4347, abs=0.01)
    assert info_l20["R_line_line_terminal_ohm"] * 1e3 == pytest.approx(24.869, abs=0.02)


def test_terminal_resistance_temperature_scaling():
    block = {
        "lead_length_mm": 200.0,
        "lead_awg": 15,
        "board_mohm": 0.30,
    }
    r_w_20 = 0.007262
    # At 120 °C, copper resistivity increases by 1 + 0.00393 * 100 = 1.393
    r_w_120 = r_w_20 * (1.0 + 0.00393 * 100.0)
    r_term_120, info_120 = terminal_phase_resistance(r_w_120, block, temp_c=120.0)
    expected_lead_120 = (1.724e-8 * (1.0 + 0.00393 * 100.0)) * 0.2 / 1.5e-6
    # Board copper traces also scale with temp (assuming copper)
    assert info_120["lead_resistance_ohm"] == pytest.approx(expected_lead_120, rel=1e-4)
    assert r_term_120 == pytest.approx(r_w_120 + expected_lead_120 + 0.00030)


def test_terminal_loss_W():
    block = {
        "lead_length_mm": 200.0,
        "lead_awg": 15,
        "board_mohm": 0.30,
    }
    # When block is absent
    loss_empty = terminal_loss_W(10.0, None)
    assert loss_empty["P_leads_board_total_W"] == 0.0

    # At zero current
    loss_zero = terminal_loss_W(0.0, block)
    assert loss_zero["P_leads_board_total_W"] == 0.0

    # At rated current 15.0 A rms:
    I = 15.0
    loss = terminal_loss_W(I, block, temp_c=20.0)
    r_lead = 1.724e-8 * 0.2 / 1.5e-6  # ~2.2987 mΩ
    r_board = 0.00030                  # 0.30 mΩ
    expected_p_leads = 3.0 * (I ** 2) * r_lead
    expected_p_board = 3.0 * (I ** 2) * r_board
    assert loss["P_leads_W"] == pytest.approx(expected_p_leads, rel=1e-4)
    assert loss["P_board_W"] == pytest.approx(expected_p_board, rel=1e-4)
    assert loss["P_leads_board_total_W"] == pytest.approx(expected_p_leads + expected_p_board, rel=1e-4)

    # field_ops wrapper matches
    loss_fo = field_ops_tlw(I, block, temp_c=20.0)
    assert loss_fo["P_leads_board_total_W"] == pytest.approx(loss["P_leads_board_total_W"])


def test_passport_terminals_module_and_card_fields():
    from motor_ai_sim.passport_v1.terminals import (
        validate_terminals as pv1_vt,
        terminal_phase_resistance as pv1_tpr,
        terminal_loss_W as pv1_tlw,
    )

    # 1. Verify re-exports from passport_v1.terminals
    assert pv1_vt is validate_terminals
    assert pv1_tpr is terminal_phase_resistance
    assert pv1_tlw is terminal_loss_W

    # 2. Test card logic with absent terminals block
    R20 = 0.007262
    R_hot = 0.008945
    term_blk_none = None
    term_info = validate_terminals(term_blk_none)
    r20_term, info_c = terminal_phase_resistance(R20, term_blk_none, temp_c=20.0)
    r_hot_term, info_h = terminal_phase_resistance(R_hot, term_blk_none, temp_c=120.0)
    loss = terminal_loss_W(15.0, term_blk_none, temp_c=120.0)

    assert r20_term == pytest.approx(R20)
    assert r_hot_term == pytest.approx(R_hot)
    assert loss["P_leads_board_total_W"] == 0.0
    assert "not stated" in info_c["note"]

    # 3. Test card logic with CIANO14 40 terminals block
    term_blk_ciano = {
        "lead_length_mm": 200.0,
        "lead_awg": 15,
        "board_mohm": 0.30,
        "notes": "200 mm 15 AWG silicone lead wire + Phase Board Rev C; factory acceptance at 20 °C",
    }
    r20_term, info_c = terminal_phase_resistance(R20, term_blk_ciano, temp_c=20.0)
    r_hot_term, info_h = terminal_phase_resistance(R_hot, term_blk_ciano, temp_c=120.0)
    loss = terminal_loss_W(15.0, term_blk_ciano, temp_c=120.0)

    # Cold terminal resistance: 7.262 mΩ + 2.30 mΩ + 0.30 mΩ = 9.86 mΩ
    assert r20_term * 1e3 == pytest.approx(9.861, abs=0.01)
    # Line-to-line cold: 2 * 9.861 = 19.72 mΩ
    assert 2.0 * r20_term * 1e3 == pytest.approx(19.722, abs=0.02)
    # Hot terminal resistance: 8.945 mΩ + 2.30 mΩ*(1+0.00393*100) + 0.30 mΩ = 12.45 mΩ
    assert r_hot_term * 1e3 == pytest.approx(12.447, abs=0.02)
    # External loss outside the motor
    assert loss["P_leads_board_total_W"] > 0.0
    assert "Phase Board Rev C" in info_c["note"]
