"""GaN HEMT cards in the Controller loss model (2026-10-05).

* third quadrant: reverse channel conduction at the card's own V_SD (volts,
  not a Si diode's 0.8 V) during the dead time;
* no reverse recovery (Q_rr = 0 on the card -> E_fr = 0);
* the times-and-charges overlap model carries no C_oss energy, so a card that
  publishes E_oss gets it ADDED — and a card that does not (every Si OptiMOS
  card) is unchanged bit for bit.
"""
from __future__ import annotations

import numpy as np
import pytest

from motor_ai_sim.inverter import devices as D
from motor_ai_sim.inverter.losses import _leg_losses


def _leg(card, *, v_gs_on, r_g, f_sw=48e3, dead=100e-9, policy="included_in_eon"):
    th = np.linspace(0.0, 2.0 * np.pi, 400, endpoint=False)
    i = 60.0 * np.sin(th)
    return _leg_losses(card=card, i_leg=i, n_par=1, f_sw=f_sw, t_j_c=110.0, v_dc=22.2,
                       v_gs_on=v_gs_on, v_gs_off=0.0, r_g=r_g, dead_time_s=dead,
                       e_oss_policy=policy)


@pytest.mark.parametrize("part", ["IGC019S06S1", "IGC016K10S2"])
def test_gan_cards_validate_and_model_as_gan(part):
    c = D.get_device(part)
    assert c.doc["technology"] == "gan_hemt"
    r = _leg(c, v_gs_on=5.0, r_g=5.1, dead=20e-9)
    assert r["p_switching_recovery_W"] == 0.0          # Q_rr = 0
    e_oss = c.e_oss_J(22.2)
    assert e_oss is not None and e_oss > 0
    assert r["p_e_oss_W"] == pytest.approx(48e3 * e_oss)   # added on this basis
    assert any("E_oss added" in n for n in r["notes"])
    # dead-time reverse conduction at volts, from the card's own curve
    assert c.v_sd_V(30.0, 25.0) > 1.5


def test_schottky_gan_conducts_lower_than_plain_gan():
    plain, sch = D.get_device("IGC019S06S1"), D.get_device("IGC016K10S2")
    assert sch.v_sd_V(30.0, 25.0) < plain.v_sd_V(30.0, 25.0)


def test_si_card_unchanged_no_e_oss_added():
    """IQE018N06NM6SC publishes no E_oss: the extension never fires and the
    result equals the explicit 'not added' computation exactly."""
    c = D.get_device("IQE018N06NM6SC")
    assert c.e_oss_J(22.2) is None
    a = _leg(c, v_gs_on=12.0, r_g=10.0)
    assert a["p_e_oss_W"] == 0.0
    assert not any("E_oss added" in n for n in a["notes"])
    assert a["p_total_W"] == (a["p_conduction_W"] + a["p_third_quadrant_W"]
                              + a["p_switching_W"])


def test_e_oss_curve_interpolates_not_v_squared():
    c = D.get_device("IGC016K10S2")
    assert c.e_oss_J(50.0) == pytest.approx(1.28e-6)
    assert c.e_oss_J(45.0) == pytest.approx(0.5 * (0.88 + 1.28) * 1e-6)
