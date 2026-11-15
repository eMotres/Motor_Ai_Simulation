"""k_end by TURNS (2026-11-15), OPT-IN per machine (`k_end_by_turns` = 1): a coil of `wire_split` = S strips per row has
rows x S - empty series turns, not `rows`.

`masses.cad_coil_length` counted `num_wires_per_slot` layers only: the two leads of the coil were billed once per STRIP-ROW
turn instead of once per coil, and the whole column was bent as one wide wire.  Owner 2026-11-15: nothing stored may move, so
WITHOUT the key (every existing machine) and for S = 1 the old layer formula is used, bit for bit.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim import winding as W
from motor_ai_sim.masses import cad_coil_length, end_winding_factor


class P:
    def __init__(self, L_mm=40.0):
        self.stack_length = L_mm * 1e-3
        self.num_slots = 12


COIL = {"wire_beng_r": 0.12, "coil_straight_extra": 1.0, "coil_lead_total": 6.0}
G12_OLD = {"tooth_width": 1.0, "wire_width": 0.2, "wire_height": 0.8, "insulation_thickness": 0.125, "wire_spacing_x": 0.03,
           "wire_spacing_y": 0.05, "num_wires_per_slot": 2, "wire_split": 3}
G12 = {**G12_OLD, "k_end_by_turns": 1}                       # the new rule, switched on


def _old(L, tw, ww, wh, n, ins, sx, sy, rb, extra, lead):
    """The layer formula as it was before this change (the unsplit coil)."""
    x1 = tw / 2.0 + ins + sx / 2.0
    bridge = 2.0 * max(x1 - rb, 0.0)
    rc = rb + ww / 2.0
    plus = bridge + math.pi * rc
    minus = math.sqrt(plus ** 2 + (wh + sy) ** 2)
    return 2.0 * n * (L + extra) + n * plus + (n - 1) * minus + 2.0 * lead


@pytest.mark.parametrize("n,ww,wh", [(n, ww, wh) for n in (1, 2, 7, 14) for ww, wh in ((2.5, 0.6), (7.5, 1.0), (0.2, 0.8))])
def test_unsplit_coil_is_the_old_formula_bit_for_bit(n, ww, wh):
    g = {"tooth_width": 3.3, "wire_width": ww, "wire_height": wh, "insulation_thickness": 0.07, "wire_spacing_x": 0.1,
         "wire_spacing_y": 0.1, "num_wires_per_slot": n}
    for geo in (g, {**g, "wire_split": 1}, {**g, "wire_split": None}):
        d = cad_coil_length(P(40.0), geo, COIL)
        want = _old(40.0, 3.3, ww, wh, n, 0.07, 0.1, 0.1, 0.12, 1.0, 6.0)
        assert d["total_coil_length_mm"] == want and d["turns_per_coil"] == float(n)


@pytest.mark.parametrize("flag", [None, "", 0, 0.0, False])
def test_split_coil_without_the_key_keeps_the_layer_formula_bit_for_bit(flag):
    """Every stored split machine: S > 1 and no `k_end_by_turns` -> the layer formula on the footprint, exactly as before."""
    g = {**G12_OLD, "k_end_by_turns": flag}
    d = cad_coil_length(P(), g, COIL)
    foot = 3 * 0.2 + 2 * 2 * 0.03                              # winding_footprint_mm
    want = _old(40.0, 1.0, foot, 0.8, 2, 0.125, 0.03, 0.05, 0.12, 1.0, 6.0)
    assert d["total_coil_length_mm"] == want and d["turns_per_coil"] == 2.0
    assert cad_coil_length(P(), G12_OLD, COIL)["total_coil_length_mm"] == want
    assert W.k_end_by_turns(g) is False


def test_the_key_reader():
    assert W.k_end_by_turns({"k_end_by_turns": 1}) is True and W.k_end_by_turns({"k_end_by_turns": "1"}) is True
    for bad in (None, "", 0, "x", float("nan"), False):
        assert W.k_end_by_turns({"k_end_by_turns": bad}) is False
    assert W.k_end_by_turns({}) is False


def test_split_coil_counts_rows_times_strips_and_shares_the_leads_once():
    d = cad_coil_length(P(), G12, COIL)
    assert d["turns_per_coil"] == 6.0
    d5 = cad_coil_length(P(), {**G12, "wire_omit": W.wire_omit_mask([(1, 0)])}, COIL)
    assert d5["turns_per_coil"] == 5.0
    # the leads belong to the COIL: +10 mm of lead adds 20 mm to the coil whatever the turn count
    for geo in (G12, {**G12, "wire_omit": W.wire_omit_mask([(1, 0)])}):
        a = cad_coil_length(P(), geo, COIL)["total_coil_length_mm"]
        b = cad_coil_length(P(), geo, {**COIL, "coil_lead_total": 16.0})["total_coil_length_mm"]
        assert b - a == pytest.approx(20.0, abs=1e-9)
    # an empty position removes exactly one turn: legs + its end turn + one transition
    assert d["total_coil_length_mm"] > d5["total_coil_length_mm"]
    assert d["total_coil_length_mm"] - d5["total_coil_length_mm"] == pytest.approx(
        2 * (40.0 + 1.0) + d["end_turn_plus_mm"] + d["end_turn_minus_mm"], rel=0.06)


def test_outer_columns_have_longer_bridges():
    """One row of three strips: the bridge of column c is 2 x (x1 + c x pitch - rb), pitch = wire_width + 2 x wire_spacing_x."""
    g = {**G12, "num_wires_per_slot": 1}
    d = cad_coil_length(P(), g, COIL)
    x1 = 0.5 + 0.125 + 0.015
    pitch = 0.2 + 2 * 0.03
    assert d["bridge_mm"] == pytest.approx(sum(2 * (x1 + c * pitch - 0.12) for c in range(3)) / 3, rel=1e-12)
    assert d["bend_centre_radius_mm"] == pytest.approx(0.12 + 0.1, rel=1e-12)       # the STRIP's width, not the column's


def test_k_end_of_the_12_40_coil_is_between_the_leads_once_and_the_old_value():
    g = {**G12, "wire_omit": W.wire_omit_mask([(1, 0)])}
    d = cad_coil_length(P(), g, COIL)
    k_new = d["mean_turn_length_mm"] / (2.0 * 40.0)
    k_old = cad_coil_length(P(), G12_OLD, COIL)["mean_turn_length_mm"] / 80.0
    k_bare = (2 * 41.0 + d["end_turn_plus_mm"]) / 80.0                    # a turn without leads and without transitions
    assert k_bare < k_new < k_old
    assert 1.05 < k_new < 1.12 and k_old > 1.12                           # 5 turns share the leads: 6 mm each end per COIL, not per turn
    # k_end follows through end_winding_factor with the coil keys supplied by the configuration
    assert end_winding_factor(P(), g, coil_overrides={"wire_beng_r": 0.12, "coil_straight_extra": 1.0}) > 1.0


def test_split_k_end_is_lower_than_the_layer_count_gave():
    """Before: rows only (2 turns), leads shared by 2.  After: 6 turns, leads shared by 6 -> a smaller mean turn."""
    old_like = cad_coil_length(P(), G12_OLD, COIL)["mean_turn_length_mm"]
    new = cad_coil_length(P(), G12, COIL)["mean_turn_length_mm"]
    assert new < old_like
