"""The DC-orbit Newton must see the controller bridge's own DC damping.

L180 gen 'rated 1x9 mm' (delta, 20 900 rpm, 24 kHz, night 2026-09-28/29):
the coupled PWM pass through the Controller's bridge was refused with
-645 A (WCMS900B170E53 x2) / -886 A (IMCQ120R004M2H x3) of DC left.  Root
cause: the dead time and the device drop act on the previous step's current
and are an extra resistance for the circuit's DC mode (~2E/(pi*I_leg) per leg,
3x per delta branch), but the period Jacobian M of the DC-orbit solve was
built from R_phase alone.  M decayed ~3x too slowly, the Newton step
M(I-M)^-1*drift over-shot by that factor every period, and the iteration
diverged with an alternating sign.  Here: the SAME source (the real
InverterVoltageSource, SVPWM, 14 carriers/period) on a linear machine,
marched with the solver's CN line-to-line rows.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.inverter.coupling import (DeviceDrop, build_inverter_source,
                                            feedback_gain_ll)
from motor_ai_sim.simulation.dc_orbit import (DcOrbitSolve, flux_shift_to_state,
                                              ll_flux, ll_inductance)
from motor_ai_sim.simulation.excitation import Feedback

from test_dc_orbit import Machine, _D, _S

_F = 1741.67          # L180 gen at 20 900 rpm, 5 pole pairs
_R = 0.0127           # ohm per delta branch (4574 W at 346.6 A rms)
_L = 120e-6           # tau ~ 16 electrical periods
_PSI = 0.073
_NSPP = 140           # 10 steps per carrier: enough for the mechanism


def _case():
    w = 2 * math.pi * _F
    i_ph = 490.0 * np.exp(1j * math.radians(160.0))        # generating
    v_ph = 1j * w * _PSI + (_R + 1j * w * _L) * i_ph
    return i_ph, v_ph


def _march(drop, *, use_gain, n_settle=12, dc0=(20.0, -10.0), damped=True):
    i_ph, v_ph = _case()
    src = build_inverter_source(
        pole_pairs=1, daxis_deg=0.0, v_phase_peak=abs(v_ph),
        v_delta_deg=math.degrees(np.angle(v_ph)),
        v_bus_model=math.sqrt(3) * 799.2, v_dc_real=799.2,
        f_switch_hz=24000.0, f_elec_hz=_F, drop=drop, star_delta="delta",
        n_parallel=1, I_phase_rms=346.6, gamma_deg=0.0, modulation="svpwm")
    m = Machine(Ld=_L, Lq=_L, R=_R, psi_m=_PSI)
    u = np.array([i_ph.real, (i_ph * np.exp(-2j * math.pi / 3)).real]) \
        + np.asarray(dc0)
    y = _D @ (m.Q(0.0) @ u + m.psi_pm(0.0))
    sol = DcOrbitSolve(R_phase=_R, damped=damped)
    dth, dt = 2 * math.pi / _NSPP, 1.0 / (_F * _NSPP)
    q0 = m.Q(0.0)
    sol.frame(ll_inductance(q0[:, 0], q0[:, 1]), dt)
    fbi = {'A': u[0], 'B': u[1], 'C': -u.sum()}
    dcs = []
    for n in range(n_settle + 1):
        sol.period_start(y)
        cur = []
        for j in range(_NSPP):
            th = n * 2 * math.pi + (j + 1) * dth
            v = src.mean_over(Feedback(
                k=n * _NSPP + j, theta_prev_deg=math.degrees(th - dth),
                theta_deg=math.degrees(th), t0_s=0.0, t1_s=dt, fine=True,
                i_abc=fbi))
            va = np.array([v['A'], v['B'], v['C']])
            q = m.Q(th)
            rhs = (_D @ va) * dt + y - _D @ m.psi_pm(th) \
                - 0.5 * _R * dt * (_S @ u)
            u = np.linalg.solve(_D @ q + 0.5 * _R * dt * _S, rhs)
            y = _D @ (q @ u + m.psi_pm(th))
            fbi = {'A': u[0], 'B': u[1], 'C': -u.sum()}
            sol.frame(ll_inductance(q[:, 0], q[:, 1]), dt,
                      src.ll_feedback_gain if use_gain else None)
            cur.append((u[0], u[1], -u[0] - u[1]))
        dc = np.mean(cur, axis=0)
        dcs.append(float(dc[np.argmax(np.abs(dc))]))
        w = sol.period_end(y, tag="fine", correct=(n < n_settle - 1))
        if w is not None:
            q = m.Q(0.0)
            dpsi, di = flux_shift_to_state(w, q[:, 0], q[:, 1])
            y = y + ll_flux(dpsi)
            u = u + np.array([di['A'], di['B']])
    return dcs, sol


_WCMS = DeviceDrop(r_ds_ohm=0.0009, v_sd_v0_V=3.0, v_sd_rd_ohm=0.002,
                   dead_time_s=0.6e-6)


def test_without_the_bridge_gain_the_newton_diverges():
    """The defect, reproduced: M from R_phase alone (the pre-fix solve, full
    Newton steps), sign-alternating growth."""
    dcs, _ = _march(_WCMS, use_gain=False, damped=False)
    assert abs(dcs[-2]) > 10.0 * abs(dcs[0]), dcs


def test_with_the_bridge_gain_the_verification_period_is_on_the_orbit():
    dcs, sol = _march(_WCMS, use_gain=True)
    assert abs(dcs[-2]) < 0.5, dcs          # verification period (free)
    assert abs(dcs[-1]) < 0.5, dcs          # the reported one after it
    assert sol.refused == 0
    assert sol.describe()["source_feedback_frames"] > 0
    # the bridge makes the DC mode decay faster than R_phase alone says
    # (the product of the two DC-mode decays per period, i.e. |det M|)
    det = np.prod([abs(complex(*e)) for e in sol.records[0]["decay_per_period"]])
    lam_r = math.exp(-1.0 / (_F * _L / _R))
    assert det < 0.98 * lam_r ** 2, (det, lam_r ** 2)


def test_an_ideal_bridge_is_unchanged():
    """No dead time, no drop: the gain is zero and nothing moves."""
    ideal = DeviceDrop(r_ds_ohm=0.0, v_sd_v0_V=0.0, v_sd_rd_ohm=0.0,
                       dead_time_s=0.0)
    a, _ = _march(ideal, use_gain=False, n_settle=4)
    b, _ = _march(ideal, use_gain=True, n_settle=4)
    np.testing.assert_allclose(a, b, atol=1e-9)
    assert abs(b[-1]) < 1e-6


@pytest.mark.parametrize("sd,factor", [("star", 1.0), ("delta", 3.0)])
def test_feedback_gain_of_a_pure_resistance(sd, factor):
    """A leg resistance r is r in star and 3r per delta branch (Y-delta)."""
    r = 2e-3
    G = feedback_gain_ll({'A': -r, 'B': -r, 'C': -r}, star_delta=sd)
    np.testing.assert_allclose(G, -factor * r * _S, atol=1e-15)


def test_symmetric_carrier_ratio_rule():
    from motor_ai_sim.simulation.pwm import symmetric_carriers_per_period as f
    assert f(24000.0, _F) == 15          # L180 gen: 13.78 -> 15, not 14
    assert f(24000.0, 1000.0) == 27      # tie 21/27 -> the higher
    assert all(f(fs, _F) % 6 == 3 for fs in (5e3, 12e3, 24e3, 48e3))


def _march_at(fsw, ang_deg, nspp):
    """The same march at another carrier and load angle."""
    import inspect
    i = 490.0 * np.exp(1j * math.radians(ang_deg))
    v = 1j * 2 * math.pi * _F * _PSI + (_R + 1j * 2 * math.pi * _F * _L) * i
    g = dict(globals(), _case=lambda: (i, v), _NSPP=nspp)
    ns = {}
    exec(inspect.getsource(_march).replace("f_switch_hz=24000.0",
                                           "f_switch_hz=%r" % fsw), g, ns)
    return ns["_march"](_WCMS, use_gain=True, n_settle=24)[0]


def test_even_carrier_ratio_with_dead_time_carries_a_real_dc():
    """14 carriers (no half-wave symmetry) + dead time: the CONVERGED orbit
    has ~20 A of DC (the server's 21.6 A); 15 carriers: none."""
    d14 = _march_at(24000.0, 140.0, 280)
    d15 = _march_at(15 * _F, 140.0, 300)
    assert abs(d14[-1]) > 5.0 and abs(d14[-1] - d14[-2]) < 0.1, d14[-4:]
    assert abs(d15[-1]) < 0.5, d15[-4:]
