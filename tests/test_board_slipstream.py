"""The controller board model at another ambient, in the propeller slipstream and
with two devices per switch.

``passport_v1.stage3``'s one-node board (the Ø40 passport's 6S Si build) and the
board block the S1 model JSON carries (``board_export_block`` /
``board_block_state`` / ``board_block_limit``): the default ambient reproduces
the calibration exactly, a cooler ambient raises the limit, the ``slipstream``
class follows the wash speed and is capped at 2x favourable, and two devices
per switch (or a board scaled x2) raise it.  Closed-form: no FEM — the Board
tests call the inverter loss model a few dozen times each.
"""
from __future__ import annotations

import pytest

from motor_ai_sim.inverter.devices import get_device
from motor_ai_sim.passport_v1 import stage3 as S3

F_SW, DEAD_S, V_DC = 48e3, 100e-9, 22.2
KW = {"f_sw": F_SW, "dead_s": DEAD_S, "v_dc": V_DC}

# the passport card's Si rating currents per cooling class (6S build)
CARD = {"favourable": 26.1, "weak": 22.853311793214864, "hot_motor": 16.44426494345719}


def _board(**kw):
    """The Ø40 passport's 6S Si build — the board the ratings were made on."""
    c = get_device("IQE018N06NM6SC")
    d = S3.drive_for(c, 10.0)
    return c, d, S3.Board(c, d, build="6S", v_dc=V_DC, f_sw=F_SW, dead_s=100e-9, **kw)


def _block():
    """A hand-made board block: P_inverter = 0.5*I, board copper 0.01*I^2, heat
    budget 10 W -> a 15.31 A favourable limit at the 45 C calibration.  R_j-hs
    is small, so only the heat budget binds and every expectation below is
    arithmetic, independent of the inverter loss model."""
    return {
        "T_amb_cal_C": 45.0, "T_hs_limit_C": 95.0, "T_j_at_limit_C": 110.0,
        "T_j_derate_C": 140.0, "R_jhs_K_per_W": 1.0, "K_board_cu_W_per_A2": 0.01,
        "devices_per_switch": 1, "board_scale": 1.0,
        "classes": {"favourable": {"Q_budget_W": 10.0, "R_sys_K_per_W": 5.0},
                    "weak": {"Q_budget_W": 8.0, "R_sys_K_per_W": 6.25}},
        "loss_table": {"I_A": [0.0, 20.0, 40.0, 60.0, 80.0, 100.0, 120.0],
                       "P_inverter_W": [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
                       "P_device_W": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]},
        "slipstream": {"class": "slipstream",
                       "favourable_air_speed_ms": S3.FAVOURABLE_AIR_SPEED_MS,
                       "film_diameter_m": S3.BOARD_FILM_DIAMETER_M,
                       "limit_cap_factor": S3.SLIPSTREAM_LIMIT_CAP_FACTOR},
    }


def test_default_ambient_is_the_calibrated_45c_bit_exact():
    """No argument = t_amb_c = 45 C, every float identical (the passport card
    must not move when the ambient argument is introduced)."""
    c, d, b = _board()
    s0 = b.state(c, d, I_rms=20.0, **KW)
    s45 = b.state(c, d, I_rms=20.0, t_amb_c=45.0, **KW)
    assert (s0["Q_W"], s0["t_hs_C"], s0["t_j_C"]) == (s45["Q_W"], s45["t_hs_C"], s45["t_j_C"])
    assert b.i_limit(c, d, **KW) == b.i_limit(c, d, t_amb_c=45.0, **KW)
    blk = _block()
    assert S3.board_block_state(blk, I_rms=10.0) == S3.board_block_state(blk, I_rms=10.0,
                                                                        t_amb_c=45.0)
    assert S3.board_block_limit(blk) == S3.board_block_limit(blk, t_amb_c=45.0)
    assert S3.board_block_state(blk, I_rms=10.0)["Q_budget_W"] == pytest.approx(10.0)


def test_the_board_model_keeps_the_passport_cards_numbers():
    """The Si build's rating current per cooling class, unchanged."""
    c, d, b = _board()
    assert b.classes["favourable"]["I_rating_si_A"] == CARD["favourable"]
    assert b.i_limit(c, d, **KW) == pytest.approx(CARD["favourable"], rel=2e-3)
    assert b.i_limit(c, d, cls="weak", **KW) == pytest.approx(CARD["weak"], rel=1e-2)
    assert b.i_limit(c, d, cls="hot_motor", **KW) == pytest.approx(CARD["hot_motor"], rel=1e-2)


def test_cooler_ambient_raises_the_board_limit():
    """The S1's own air temperature on the board: 25 C air allows more heat at
    the same 95 C heatsink limit (the stated budget law), so more current."""
    c, d, b = _board()
    a45, a25 = b.i_limit(c, d, **KW), b.i_limit(c, d, t_amb_c=25.0, **KW)
    assert a25 > a45 and 1.05 < a25 / a45 < 1.4
    blk = _block()
    l45, l25 = S3.board_block_limit(blk), S3.board_block_limit(blk, t_amb_c=25.0)
    assert l45 == pytest.approx(15.31, abs=0.05) and l25 > l45
    s = S3.board_block_state(blk, I_rms=10.0, t_amb_c=25.0)
    assert s["Q_budget_W"] == pytest.approx(1.4 * 10.0)     # 10 W x (95-25)/(95-45)
    assert s["t_hs_C"] < S3.board_block_state(blk, I_rms=10.0)["t_hs_C"]   # same Q, colder air


def test_slipstream_follows_the_wash_speed_and_is_capped():
    """The board in the propeller wash: R_sys scaled by h(favourable)/h(slip)
    and the heat budget with it, so the limit follows the wash speed — never
    below favourable at its own air speed, never above the stated 2x cap."""
    blk = _block()
    fav = S3.board_block_limit(blk)
    same = S3.board_block_limit(blk, cls="slipstream",
                                air_speed_ms=S3.FAVOURABLE_AIR_SPEED_MS)
    assert same == fav                      # h(favourable)/h(favourable) = 1 exactly
    mid = S3.board_block_limit(blk, cls="slipstream", air_speed_ms=5.0)
    assert fav < mid < S3.SLIPSTREAM_LIMIT_CAP_FACTOR * fav
    cap = S3.board_block_limit(blk, cls="slipstream", air_speed_ms=200.0)
    assert cap == pytest.approx(S3.SLIPSTREAM_LIMIT_CAP_FACTOR * fav, rel=1e-12)
    assert S3.slipstream_rsys_factor(S3.FAVOURABLE_AIR_SPEED_MS, 25.0) == 1.0
    assert 0.0 < S3.slipstream_rsys_factor(20.0, 25.0) \
        < S3.slipstream_rsys_factor(5.0, 25.0) < 1.0
    with pytest.raises(ValueError):
        S3.board_block_state(blk, I_rms=5.0, cls="slipstream")     # needs the wash speed
    # the board model itself, on its own device
    c, d, b = _board()
    f = b.i_limit(c, d, **KW)
    assert f <= b.i_limit(c, d, cls="slipstream", air_speed_ms=20.0, **KW) \
        <= S3.SLIPSTREAM_LIMIT_CAP_FACTOR * f
    with pytest.raises(ValueError):
        b.state(c, d, I_rms=5.0, cls="slipstream", **KW)


def test_two_devices_per_switch_and_a_scaled_board_raise_the_limit():
    """Two devices share the current (lower bridge loss at the same I) and a
    board scaled x2 takes twice the heat (the Ø85 rule: Q* x scale,
    R_sys / scale, R_j-hs per device)."""
    c, d, b1 = _board()
    b2 = _board(n_par=2)[2]
    assert b2.i_limit(c, d, **KW) > b1.i_limit(c, d, **KW)
    s2 = S3.scaled_classes(b1.classes, 2.0)
    for cls in b1.classes:
        assert s2[cls]["Q_budget_W"] == pytest.approx(2.0 * b1.classes[cls]["Q_budget_W"])
        assert s2[cls]["R_sys_K_per_W"] == pytest.approx(b1.classes[cls]["R_sys_K_per_W"] / 2.0)
    one, two = _block(), _block()
    two["classes"] = S3.scaled_classes(two["classes"], 2.0)
    assert S3.board_block_limit(two) > S3.board_block_limit(one)


def test_the_exported_block_agrees_with_the_board_model():
    """The block the S1 model JSON carries: the passport's calibration verbatim
    + a per-current loss table at the design T_j.  It answers like the board
    model (within the stated table tolerance) and takes the same ambient and
    slipstream arguments."""
    c, d, b = _board()
    blk = b.export_block(c, d, i_max_A=60.0, **KW)
    assert blk["T_amb_cal_C"] == 45.0 and blk["T_hs_limit_C"] == 95.0
    assert blk["devices_per_switch"] == 1 and blk["board_scale"] == 1.0
    assert blk["loss_table"]["I_A"][0] == 0.0 and blk["loss_table"]["I_A"][-1] == 60.0
    ss = blk["slipstream"]
    assert ss["favourable_air_speed_ms"] == S3.FAVOURABLE_AIR_SPEED_MS
    assert ss["film_diameter_m"] == S3.BOARD_FILM_DIAMETER_M
    assert ss["limit_cap_factor"] == S3.SLIPSTREAM_LIMIT_CAP_FACTOR == 2.0
    for cls in CARD:
        assert blk["classes"][cls]["I_rating_si_A"] == pytest.approx(CARD[cls], rel=1e-4)
        assert S3.board_block_limit(blk, cls=cls) == pytest.approx(b.i_limit(c, d, cls=cls, **KW),
                                                                  abs=0.5)
    assert S3.board_block_limit(blk, t_amb_c=25.0) > S3.board_block_limit(blk)
    fav = S3.board_block_limit(blk)
    assert fav <= S3.board_block_limit(blk, cls="slipstream", air_speed_ms=20.0) \
        <= 2.0 * fav + 1e-9
