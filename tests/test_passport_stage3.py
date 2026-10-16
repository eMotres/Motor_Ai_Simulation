"""Passport stage 3 (PWM / controller) — the board model and the PWM scaling."""
from __future__ import annotations

import pytest

from motor_ai_sim.inverter.devices import get_device
from motor_ai_sim.passport_v1 import stage3 as S3


def test_hdf_svpwm_is_zero_at_zero_and_grows_in_the_linear_range():
    assert S3.hdf_svpwm(0.0) == 0.0
    vals = [S3.hdf_svpwm(m) for m in (0.2, 0.4, 0.6, 0.8, 1.0)]
    assert all(v > 0 for v in vals)
    # below the SPWM curve of the same paper at the top of the linear range
    M = 0.25 * 3.141592653589793
    hdf_spwm = 1.5 * M ** 2 - (4 * 3 ** 0.5 / 3.141592653589793) * M ** 3 + 9 / 8 * M ** 4
    assert vals[-1] < hdf_spwm


@pytest.mark.parametrize("build, part, rg, vdc", [("6S", "IQE018N06NM6SC", 10.0, 22.2),
                                                  ("12S", "IQE036N08NM6SC", 12.0, 44.4)])
def test_board_model_reproduces_the_controller_rating_on_its_own_device(build, part, rg, vdc):
    c = get_device(part)
    d = S3.drive_for(c, rg)
    b = S3.Board(c, d, build=build, v_dc=vdc, f_sw=48e3, dead_s=100e-9)
    I_fav = S3.RATINGS[build]["I_fav"]
    s = b.state(c, d, I_rms=I_fav, f_sw=48e3, dead_s=100e-9, v_dc=vdc)
    assert s["t_j_C"] == pytest.approx(S3.T_J_AT_LIMIT_C, abs=0.2)
    assert s["t_hs_C"] == pytest.approx(S3.T_HS_LIMIT_C, abs=0.2)
    I_lim = b.i_limit(c, d, f_sw=48e3, dead_s=100e-9, v_dc=vdc)
    assert I_lim == pytest.approx(I_fav, rel=2e-3)
    # weaker cooling -> lower board-limit current, in the README's ratio
    I_hot = b.i_limit(c, d, f_sw=48e3, dead_s=100e-9, v_dc=vdc, cls="hot_motor")
    assert I_hot < I_lim


def test_pwm_model_is_exact_at_anchors_and_holds_outside():
    a = [{"tag": "lo", "rpm": 13000.0, "I": 20.0, "dP_harm_W": 4.0, "m_index": 0.9},
         {"tag": "hi", "rpm": 13000.0, "I": 40.0, "dP_harm_W": 6.0, "m_index": 1.0}]
    pm = S3.PwmModel(a)
    v, how, fem = pm.at(13000.0, 20.0, 0.9)
    assert fem and v == pytest.approx(4.0) and "lo" in how
    # between the anchors: k linear in m, times HDF at the point
    k_mid = 0.5 * (4.0 / S3.hdf_svpwm(0.9) + 6.0 / S3.hdf_svpwm(1.0))
    v, how, fem = pm.at(6500.0, 30.0, 0.95)
    assert not fem and v == pytest.approx(k_mid * S3.hdf_svpwm(0.95))
    # below the lowest anchor m: k held, HDF follows the point
    v, how, _ = pm.at(3250.0, 30.0, 0.5)
    assert "HELD" in how
    assert v == pytest.approx(4.0 / S3.hdf_svpwm(0.9) * S3.hdf_svpwm(0.5))
    est = S3.PwmModel(a, scale=0.5, derived="ESTIMATE")
    v, how, fem = est.at(13000.0, 40.0, 1.0)
    assert not fem and v == pytest.approx(3.0) and how.startswith("ESTIMATE")


def test_gan_drive_is_the_reference_board_drive():
    d = S3.drive_for(get_device("IGC016K10S2"), 12.0)
    assert d["tech"] == "GaN" and d["v_gs_on"] == 5.0 and d["r_g"] == 5.1
    assert d["source"] == "datasheet"
