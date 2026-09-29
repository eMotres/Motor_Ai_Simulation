"""The Controller bridge's discrete current loop (owner 2026-09-29).

The real InverterVoltageSource (SVPWM, dead time, device drops, delta, 14
carriers per electrical period — the L180 gen's ratio) drives a linear machine
through the solver's own CN line-to-line rows.  No DC-orbit correction is
applied: a DC current must decay PHYSICALLY through the regulator, and the
fundamental must track the sine pass's current (I, gamma).
"""
from __future__ import annotations

import math

import numpy as np

from motor_ai_sim.inverter.coupling import DeviceDrop, build_inverter_source
from motor_ai_sim.simulation.excitation import Feedback

from test_dc_orbit import Machine, _D, _S

_F, _R, _L, _PSI = 1741.67, 0.0127, 120e-6, 0.073
_NSPP = 280                    # 20 steps per carrier, 14 carriers
_I_RMS, _GAMMA = 346.6, 160.0
_WCMS = DeviceDrop(r_ds_ohm=0.0009, v_sd_v0_V=3.0, v_sd_rd_ohm=0.002,
                   dead_time_s=0.6e-6)


def _phasors(v_scale=1.0):
    w = 2 * math.pi * _F
    i = _I_RMS * math.sqrt(2) * np.exp(1j * math.radians(_GAMMA))
    v = 1j * w * _PSI + (_R + 1j * w * _L) * i
    return i, v * v_scale


def _run(*, periods=6, dc0=(20.0, -10.0), v_scale=1.0, loop=True,
         i_rms=_I_RMS):
    i_ph, v_ph = _phasors(v_scale)
    src = build_inverter_source(
        pole_pairs=1, daxis_deg=0.0, v_phase_peak=abs(v_ph),
        v_delta_deg=math.degrees(np.angle(v_ph)),
        v_bus_model=math.sqrt(3) * 799.2, v_dc_real=799.2,
        f_switch_hz=24000.0, f_elec_hz=_F, drop=_WCMS, star_delta="delta",
        n_parallel=1, I_phase_rms=i_rms, gamma_deg=_GAMMA,
        modulation="svpwm")
    assert src.carriers == 14
    if loop:
        src.configure_current_loop(R_phase=_R, L_d=_L, L_q=_L)
    m = Machine(Ld=_L, Lq=_L, R=_R, psi_m=_PSI)
    u = np.array([i_ph.real, (i_ph * np.exp(-2j * math.pi / 3)).real]) \
        + np.asarray(dc0)
    y = _D @ (m.Q(0.0) @ u + m.psi_pm(0.0))
    dth, dt = 2 * math.pi / _NSPP, 1.0 / (_F * _NSPP)
    fbi = {'A': u[0], 'B': u[1], 'C': -u.sum()}
    per = []
    for n in range(periods):
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
            cur.append((th, u[0], u[1], -u[0] - u[1]))
        c = np.asarray(cur)
        dc = c[:, 1:].mean(axis=0)
        h1 = 2.0 * np.mean(c[:, 1] * np.exp(-1j * c[:, 0]))   # phase A phasor
        per.append((float(dc[np.argmax(np.abs(dc))]), abs(h1),
                     math.degrees(np.angle(h1))))
    return per, src


def test_a_20A_start_dc_decays_through_the_regulator():
    per, src = _run(periods=8)
    open_loop, _ = _run(periods=8, loop=False)
    assert abs(per[0][0]) > 1.0                 # it really started off
    # …and the loop removed it: what is left (~0.9 A) is the PHYSICAL residue
    # of 14 carriers + dead time under this loop (open loop holds ~20 A)
    assert abs(per[-1][0]) < 2.0, per
    assert abs(per[-1][0]) < 0.1 * abs(open_loop[-1][0]), (per, open_loop)
    rep = src.describe()["pwm"]["nonideal"]["current_loop"]
    assert rep["active"] and not rep["overmodulation"]
    assert rep["samples"] >= 14 * 6


def test_open_loop_bridge_keeps_its_dc():
    """The same bridge without the loop: 14 carriers + dead time hold a DC."""
    per, _ = _run(loop=False)
    assert abs(per[-1][0]) > 2.0, per


def test_fundamental_tracks_the_setpoint_even_with_a_wrong_feedforward():
    """Feedforward 8 % short: the integral action still lands on (I, gamma)."""
    per, src = _run(periods=10, v_scale=0.92)
    ipk = _I_RMS * math.sqrt(2)
    assert abs(per[-1][1] - ipk) / ipk < 0.01, per[-1]
    assert abs(((per[-1][2] - _GAMMA + 180) % 360) - 180) < 1.0, per[-1]
    assert src.cc.tracking_error_rms() < 0.02 * ipk


def test_an_impossible_setpoint_is_flagged_as_overmodulation():
    per, src = _run(periods=3, i_rms=3.0 * _I_RMS)
    rep = src.cc.report()
    assert rep["overmodulation"] and rep["overmodulated_samples"] > 0
    # anti-windup: the integrator stays bounded by the voltage it could use
    assert max(abs(x) for x in rep["integrator_V"]) < 2.0 * rep["v_limit_V"]


def test_closed_loop_bridge_measures_but_never_corrects_the_dc_orbit():
    _, src = _run(periods=1)
    assert src.settle_policy().dc_orbit_correct is False
