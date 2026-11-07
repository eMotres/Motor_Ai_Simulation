"""The DC-link mean current is the power balance: (P_terminal + P_loss) / V_dc.

Ideal synthetic cases, no FEM: sinusoidal leg currents, the modulator's own
switching functions, a known power factor.  The grid-snapped switch states used
to round every pulse to a multiple of 1/samples_per_carrier, which biased the
mean by +6 % at m = 0.1 on the default 24 samples per carrier (0.5 % at 0.9);
the cell-exact on-times of ``switch_average`` remove that bias.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.inverter import losses as lo
from motor_ai_sim.inverter import waveforms as wf

V_DC = 540.0
F_EL = 233.0
F_SW = 10000.0
IP = 69.0


def _mean(m, mod, phi_deg, spc=24, *, snapped=False, p_loss=0.0):
    st = wf.ModulatorSetup(f_elec_hz=F_EL, f_carrier_hz=F_SW, v_dc_V=V_DC,
                           modulation_index=m, samples_per_carrier=spc,
                           modulation=mod)
    u = st.grid()
    states, avg, cur = [], [], []
    for k in range(3):
        ph = -120.0 * k
        cur.append(IP * np.sin(2 * math.pi * u + math.radians(ph - phi_deg)))
        states.append(wf.switch_states(st, u, ph))
        avg.append(wf.switch_average(st, u, ph))
    return wf.dc_link_current(states, cur, mean_states=None if snapped else avg,
                              p_loss_W=p_loss, v_dc_V=V_DC)


@pytest.mark.parametrize("mod", ["sine", "svpwm"])
@pytest.mark.parametrize("m", [0.1, 0.3, 0.9])
@pytest.mark.parametrize("phi", [20.0, 160.0])      # motor / generator
def test_mean_is_terminal_power_over_vdc(mod, m, phi):
    p_term = 1.5 * (m * V_DC / 2.0) * IP * math.cos(math.radians(phi))
    got = _mean(m, mod, phi)["i_dc_mean_A"]
    assert got == pytest.approx(p_term / V_DC, rel=2e-3)
    assert (got > 0) == (phi < 90.0)


def test_loss_is_drawn_from_the_link_either_way():
    for phi in (20.0, 160.0):
        base = _mean(0.3, "svpwm", phi)
        with_loss = _mean(0.3, "svpwm", phi, p_loss=400.0)
        assert with_loss["i_dc_mean_A"] - base["i_dc_mean_A"] == pytest.approx(
            400.0 / V_DC, abs=1e-9)
        assert with_loss["i_cap_rms_A"] == pytest.approx(base["i_cap_rms_A"])
        assert with_loss["i_dc_pp_A"] == pytest.approx(base["i_dc_pp_A"])


def test_ripple_is_the_pulses_not_the_cell_average():
    """The cell-exact on-times feed the mean only; the capacitor rms and the
    peak-to-peak are those of the binary pulses, unchanged by the fix."""
    new, old = _mean(0.3, "svpwm", 20.0), _mean(0.3, "svpwm", 20.0, snapped=True)
    assert new["i_cap_rms_A"] == old["i_cap_rms_A"]
    assert new["i_dc_pp_A"] == old["i_dc_pp_A"]
    assert new["i_dc_rms_A"] ** 2 == pytest.approx(
        new["i_cap_rms_A"] ** 2 + new["i_dc_mean_A"] ** 2)


def test_grid_snapped_states_were_biased_at_low_modulation():
    """Documents the old behaviour so the cell-exact path is not undone."""
    m = 0.1
    ideal = 1.5 * (m * V_DC / 2.0) * IP * math.cos(math.radians(20.0)) / V_DC
    old = _mean(m, "svpwm", 20.0, snapped=True)["i_dc_mean_A"]
    assert old / ideal - 1.0 > 0.04
    assert _mean(m, "svpwm", 20.0)["i_dc_mean_A"] / ideal - 1.0 < 1e-3


def _req(**kw):
    req = dict(num_slots=24, num_poles=28, single_layer=False, star_delta="star",
               device="IMCQ120R004M2H", devices_parallel=2, v_dc_V=V_DC,
               i_phase_rms_A=48.85, p_ac_W=9770.0, f_elec_hz=F_EL,
               f_carrier_hz=F_SW, modulation_index=0.388, topology="one_3ph",
               dead_time_us=2.0, pwm_modulation="svpwm")
    req.update(kw)
    return req


@pytest.mark.parametrize("direction", ["motor", "generator"])
@pytest.mark.parametrize("m,p_ac", [(0.15, 3500.0), (0.388, 9770.0)])   # pf 0.83 / 0.90
def test_controller_dc_link_matches_power_balance_at_default_grid(direction, m, p_ac):
    out = lo.solve_controller(_req(power_direction=direction,
                                   modulation_index=m, p_ac_W=p_ac))
    p_loss = out["losses"]["total_W"]
    expect = ((p_ac + p_loss) if direction == "motor" else -(p_ac - p_loss)) / V_DC
    assert out["dc_link"]["i_dc_mean_A"] == pytest.approx(expect, abs=0.15)


# Two real coupled PWM controller points (24 slots, 14 pole pairs, 540 V,
# 2 us dead time, SVPWM 10 kHz, IGBT module): the stored bus mean was 20.3 A
# and 35.4 A against P/Vdc of 18.13 A and 34.61 A.  The grid-snapped states
# gave +11.9 % and +2.3 % (43 and 21 carriers per period, 24 samples each);
# the second happened to sit on (P + P_loss)/Vdc because the loss it left out
# (0.9 A) and the snapping bias (+0.8 A) cancelled.
_REAL = [  # rpm, f_el, m, P_ac, I_phase_rms, stored cap rms, stored pp
    (1000, 233.333, 0.443, 9792.5, 48.8484, 27.1, 75.8),
    (2000, 466.667, 0.8163, 18690.8, 48.4, 27.3, 68.4),
]


@pytest.mark.parametrize("rpm,f_el,m,p_ac,i_rms,cap,pp", _REAL)
def test_real_coupled_points_balance_and_ripple_unchanged(rpm, f_el, m, p_ac, i_rms, cap, pp):
    out = lo.solve_controller(dict(
        num_slots=24, num_poles=28, single_layer=False, star_delta="star",
        device="2MBI800XNE120-50", devices_parallel=1, v_dc_V=V_DC,
        i_phase_rms_A=i_rms, p_ac_W=p_ac, f_elec_hz=f_el, f_carrier_hz=F_SW,
        modulation_index=m, topology="one_3ph", dead_time_us=2.0,
        pwm_modulation="svpwm", r_g_ext_ohm=0.5, r_g_off_ext_ohm=0.5))
    dc = out["dc_link"]
    p_loss = out["losses"]["total_W"]
    assert dc["i_dc_mean_A"] == pytest.approx((p_ac + p_loss) / V_DC, abs=0.06)
    assert dc["i_dc_mean_A"] - p_loss / V_DC == pytest.approx(p_ac / V_DC, rel=3e-3)
    assert dc["i_cap_rms_A"] == pytest.approx(cap, abs=0.05)
    assert dc["i_dc_pp_A"] == pytest.approx(pp, abs=0.05)
