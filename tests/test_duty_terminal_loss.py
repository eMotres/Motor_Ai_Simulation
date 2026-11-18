"""The lead / phase-board loss of a duty as its own loss term, and the efficiency at the terminals (owner 2026-11-18).

Pinned: nothing is added without a stated `terminals:` block (the tree row of every other configuration is unchanged); the
duty's own efficiency is never replaced - both are returned; motor and generator formulas; the lead temperature rule.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import terminals as T
from motor_ai_sim.routes import family as fam

# CIANO14 40 L12 rated, as the investigation quotes it: 23.8 W on a 93.7 W loss budget, shaft output 914.6 W
BLOCK = {"board_mohm": 19.83}                       # 3 * 20 A^2 * 19.83 mOhm = 23.8 W
SUMMARY = {"I_phase_rms_A": 20.0, "coil_temp_C": 120.0, "efficiency": 0.907, "P_mech_W": 914.6, "op_mode": "motor"}


def test_without_a_stated_block_or_current_nothing_is_added():
    assert T.duty_terminal_budget(SUMMARY, None) is None
    assert T.duty_terminal_budget(SUMMARY, {}) is None
    assert T.duty_terminal_budget({**SUMMARY, "I_phase_rms_A": 0.0}, BLOCK) is None
    assert T.duty_terminal_budget(None, BLOCK) is None
    assert T.duty_terminal_budget(SUMMARY, {"lead_length_mm": -1.0}) is None          # invalid block: no number, no raise


def test_motor_efficiency_at_the_terminals_is_the_second_number_and_the_first_is_kept():
    b = T.duty_terminal_budget(SUMMARY, BLOCK)
    assert b["P_leads_board_W"] == pytest.approx(23.8, abs=0.01) and b["P_leads_W"] == 0.0
    assert b["efficiency"] == 0.907 and b["efficiency_basis"] == "electromagnetic"
    assert b["efficiency_at_terminals"] == pytest.approx(0.886, abs=0.001)             # 90.7 % -> 88.6 %


def test_the_shaft_basis_wins_when_the_summary_has_it():
    s = {**SUMMARY, "efficiency_shaft": 0.90, "P_shaft_net_W": 900.0}
    b = T.duty_terminal_budget(s, BLOCK)
    assert b["efficiency_basis"] == "shaft" and b["efficiency"] == 0.9
    assert b["efficiency_at_terminals"] == pytest.approx(900.0 / (900.0 / 0.90 + b["P_leads_board_W"]), abs=1e-5)


def test_generator_loses_the_terminal_watts_from_the_input_power():
    s = {**SUMMARY, "op_mode": "generator", "efficiency": 0.90, "P_mech_W": 1000.0}
    b = T.duty_terminal_budget(s, BLOCK)
    assert b["efficiency_at_terminals"] == pytest.approx(0.90 - 23.8 / 1000.0, abs=1e-4)


def test_losses_only_when_the_efficiency_inputs_are_missing():
    b = T.duty_terminal_budget({"I_phase_rms_A": 20.0}, BLOCK)
    assert b["P_leads_board_W"] == pytest.approx(23.8, abs=0.01) and "efficiency_at_terminals" not in b


def test_lead_temperature_rule():
    blk = {"lead_length_mm": 500.0, "lead_area_mm2": 1.5}
    hot = T.duty_terminal_budget(SUMMARY, blk)
    assert hot["lead_temp_c"] == 120.0 and hot["lead_temp_source"] == "coil temperature of the run"
    cold = T.duty_terminal_budget(SUMMARY, {**blk, "lead_temp_c": 20.0})
    assert cold["lead_temp_source"] == "terminals.lead_temp_c" and cold["P_leads_W"] < hot["P_leads_W"]
    assert T.duty_terminal_budget({"I_phase_rms_A": 20.0}, blk)["lead_temp_c"] == 20.0


def test_the_catalog_view_adds_the_key_only_for_a_configuration_with_terminals():
    d = {"name": "rated", "summary": dict(SUMMARY)}
    assert fam._terminal_loss_view(d, {}) is None
    assert fam._terminal_loss_view(d, {"terminals": None}) is None
    assert fam._terminal_loss_view(d, {"terminals": {"lead_length_mm": -1.0}}) is None      # malformed: never raises
    v = fam._terminal_loss_view(d, {"terminals": BLOCK})
    assert v["efficiency_at_terminals"] < v["efficiency"]
    assert d == {"name": "rated", "summary": SUMMARY}                                       # the duty itself is untouched
