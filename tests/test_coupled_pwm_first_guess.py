"""drive = "inverter", final_pass: the FIRST PWM command carries the bridge's
own drops (closed form from the controller model), and a third PWM pass is
spent ONLY when the current is still outside ``i_tol_pct`` (2026-09-26).

Before this, the first pass commanded the sine state's TERMINAL fundamental,
the bridge lost its channel drop and dead time on the way, and the damped
regulator's one step left the Ø40 L12 steady answer at −3.4 % of its current
after the 2-pass cap (docs/CONTROLLER_MODULE_2026-09-22.md §7c).

The electromagnetic pass is MOCKED as an affine machine behind a real-shaped
bridge: the terminals get ``V_cmd − E_true`` with ``E_true`` 10 % larger than
the closed form (what the closed form leaves out), and the current moves
``s = 1.5`` per-unit per per-unit of terminal voltage — the sensitivity that
reproduces §7c's −3.4 % with the old seed.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.inverter.coupling import DeviceDrop
from motor_ai_sim.routes import coupled as cp

# Ø40 L12-like: 22.2 V link, 24 kHz, 0.2 µs, one IQE050N08NM5SC per switch.
DROP = DeviceDrop(r_ds_ohm=0.005, v_sd_v0_V=0.7, v_sd_rd_ohm=0.004,
                  dead_time_s=0.2e-6, device="fake", t_j_c=48.0)
V_SINE = 9.5          # the sine state's terminal fundamental [V peak]
DELTA_DEG = 25.0      # its load angle; the current sits at gamma = 0
I_T = 43.84           # the duty's current [A rms]
S_PU = 1.5            # dI/I per dV/V of the mocked machine
UNMODELLED = 1.10     # the true bridge loses 10 % more than the closed form
COIL_SINE = 76.0


def _guess(**kw):
    a = dict(v_sine_peak_V=V_SINE, v_delta_deg=DELTA_DEG, gamma_deg=0.0,
             i_phase_rms_A=I_T, drop=DROP, v_dc_V=22.2,
             f_carrier_hz=24000.0, star_delta="star")
    a.update(kw)
    return cp._pwm_v1_first_guess(**a)


def _pole_err(i, drop, vdc, fsw):
    """``pole_error_volts`` averaged over a carrier, on a current sample."""
    return (-i * drop.r_ds_ohm
            - np.sign(i) * drop.dead_time_s * fsw
            * (vdc + 2.0 * (drop.v_sd_v0_V + drop.v_sd_rd_ohm * np.abs(i))))


# ── the closed form ─────────────────────────────────────────────────────────

def test_closed_form_is_the_fundamental_of_the_pole_error_star():
    g = _guess()
    th = np.linspace(0.0, 2 * math.pi, 20000, endpoint=False)
    ipk = I_T * math.sqrt(2.0)
    e = _pole_err(ipk * np.sin(th), DROP, 22.2, 24000.0)
    # fundamental component IN PHASE with the current (it opposes it)
    e1 = -2.0 * np.mean(e * np.sin(th))
    assert g["E1_drop_peak_V"] == pytest.approx(e1, rel=1e-3)
    assert g["E1_drop_peak_V"] == pytest.approx(
        g["E1_channel_V"] + g["E1_dead_time_V"], abs=1e-3)
    cphi = math.cos(math.radians(DELTA_DEG))
    assert g["v_command_peak_V"] == pytest.approx(V_SINE + e1 * cphi,
                                                  rel=1e-4)
    assert g["v_command_peak_V"] > V_SINE


def test_closed_form_delta_is_the_line_error_along_the_branch_current():
    """Delta: the bridge leg carries i_AB − i_CA; the model's voltage is
    v_AB = e_A − e_B.  Its fundamental lies along the BRANCH current."""
    g = _guess(star_delta="delta", gamma_deg=DELTA_DEG)   # cos φ = 1
    th = np.linspace(0.0, 2 * math.pi, 20000, endpoint=False)
    ipk = I_T * math.sqrt(2.0)
    i_ab = ipk * np.sin(th)
    i_ca = ipk * np.sin(th + 2 * math.pi / 3)
    i_bc = ipk * np.sin(th - 2 * math.pi / 3)
    e_a = _pole_err(i_ab - i_ca, DROP, 22.2, 24000.0)
    e_b = _pole_err(i_bc - i_ab, DROP, 22.2, 24000.0)
    v_ab = e_a - e_b
    along = -2.0 * np.mean(v_ab * np.sin(th))
    quad = 2.0 * np.mean(v_ab * np.cos(th))
    assert abs(quad) < 1e-3 * abs(along)          # no quadrature part
    # the √3 leg current and the √3 of the line error are both in it
    assert g["E1_drop_peak_V"] == pytest.approx(along, rel=2e-3)
    assert g["i_leg_peak_A"] == pytest.approx(ipk * math.sqrt(3.0), rel=1e-6)
    assert g["v_command_peak_V"] == pytest.approx(V_SINE + along, rel=1e-3)


def test_generator_commands_below_the_terminal():
    g = _guess(v_delta_deg=150.0)                  # cos φ < 0
    assert g["cos_phi"] < 0.0
    assert g["v_command_peak_V"] < V_SINE


@pytest.mark.parametrize("kw", [
    {"v_dc_V": 0.0}, {"i_phase_rms_A": float("nan")},
    {"f_carrier_hz": -1.0}, {"v_sine_peak_V": None},
    {"gamma_deg": float("inf")},
    {"drop": DeviceDrop(r_ds_ohm=-1.0, v_sd_v0_V=0.7, v_sd_rd_ohm=0.0,
                        dead_time_s=0.2e-6)},
    {"drop": object()},
])
def test_bad_inputs_refuse_by_name(kw):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        _guess(**kw)
    assert ei.value.status_code == 422
    assert ei.value.detail["error_code"] == "pwm_first_guess_input"


# ── the passes, on a mocked machine ────────────────────────────────────────

class _Ctl:
    def __init__(self, drop):
        if drop is not None:
            self.drop = drop
        self.star_delta = "star"
        self.t_j_c = 48.0
        self.solve = {}
        self.steps = []

    def reseed(self, i):
        pass

    def state(self):
        return (self.t_j_c, dict(self.solve))

    def restore(self, st):
        self.t_j_c, self.solve = st

    def step(self, em, *, it, phase="loop"):
        self.steps.append(phase)
        return 0.0


def _true_e1_cos():
    g = _guess()
    return UNMODELLED * g["E1_drop_peak_V"] * g["cos_phi"]


def _rig(monkeypatch, *, coil_rise_per_pass=(5.0, 0.2, 0.0)):
    """The mocked machine and a thermal map that heats the winding by
    ``coil_rise_per_pass[k]`` over the temperature pass ``k+1`` ran at."""
    calls = {"v": [], "coil": []}
    e_true = _true_e1_cos()

    def _em(body, *, coil_temp_c, magnet_temp_c, inverter, **_k):
        v_cmd = float(inverter["v_phase_peak_V"])
        v_term = v_cmd - e_true
        # hotter copper → a little less current (−0.05 %/K)
        i = I_T * (1.0 + S_PU * (v_term / V_SINE - 1.0)
                   - 5e-4 * (coil_temp_c - COIL_SINE))
        calls["v"].append(v_cmd)
        calls["coil"].append(coil_temp_c)
        return {"summary": {"T_em_avg_Nm": 0.58, "P_loss_total_W": 68.0},
                "I_phase_rms_solved_A": i}

    def _th(body, cooling, *, coil_temp_c, **_k):
        k = len(calls["v"]) - 1
        w = coil_temp_c + coil_rise_per_pass[min(k, len(coil_rise_per_pass)
                                                 - 1)]
        return {"ok": True, "components": {"winding": {"avg": w},
                                           "magnet": {"avg": 100.0}}}

    monkeypatch.setattr(cp, "_em_run", _em)
    monkeypatch.setattr(cp, "_thermal_solve", _th)
    monkeypatch.setattr(cp, "_pwm_dc_verdict",
                        lambda inv, s: (True, True, 0.0, 1.0, None))
    monkeypatch.setattr(cp, "_pwm_loss_map", lambda *a, **k: ({}, "mock"))
    return calls


_INV = {"f_carrier_hz": 24000.0, "v_phase_peak_V": V_SINE, "v_dc_V": 22.2,
        "carriers_per_period": 16, "f_elec_hz": 1500.0,
        "n_steps_per_period": 320, "target_I_phase_rms_A": I_T,
        "i_tol_pct": 1.0, "v_phase_peak_max_V": 12.5,
        "v_phase_peak_seed_V": V_SINE, "v_delta_deg": DELTA_DEG,
        "sources": {}}


def _run(drop):
    return cp._pwm_final_passes(
        {"I_phase_rms": I_T, "gamma_deg": 0.0}, cooling={}, rpm=12857.0,
        inverter=dict(_INV), ctl=_Ctl(drop),
        sine_em={"summary": {"V1_seed_peak_V": V_SINE,
                             "V1_seed_delta_deg": DELTA_DEG}},
        coil_c=COIL_SINE, magnet_c=100.0, bearing_c=None, i_body=I_T,
        i_target=I_T, tol=1.0, adjust_temps=True)


def _errs(out):
    return [p["point_error_pct"] for p in out["passes"]]


def test_old_seed_with_the_old_cap_lands_short(monkeypatch):
    """The regression the task is about, reproduced: the terminal value
    commanded, two passes, the damped step → about −3.5 % of the current."""
    _rig(monkeypatch)
    monkeypatch.setattr(cp, "PWM_FINAL_CURRENT_EXTRA_PASSES", 0)
    out = _run(drop=None)
    assert out["v1_first_guess"]["applied"] is False
    assert "device drop" in out["v1_first_guess"]["reason"]
    e = _errs(out)
    assert len(e) == 2
    assert e[0] < -6.0 and -4.5 < e[1] < -2.5
    assert out["converged"] is False


def test_closed_form_seed_lands_inside_the_band_in_two_passes(monkeypatch):
    calls = _rig(monkeypatch)
    out = _run(drop=DROP)
    g = out["v1_first_guess"]
    assert g["applied"] is True
    assert calls["v"][0] == pytest.approx(g["v_command_peak_V"])
    assert "device drop" in out["inverter"]["sources"]["v_phase_peak_V"]
    e = _errs(out)
    # pass 1 is off only by what the closed form leaves out (10 % of 5 %)
    assert abs(e[0]) < 1.0
    # pass 2 is there for the temperature (+5 K), not the current
    assert len(e) == 2 and abs(e[1]) < 1.0
    assert out["converged"] is True
    assert out["current_extra_pass"] is False


def test_third_pass_only_for_the_current(monkeypatch):
    """No drop to correct with (old seed): the current is still out after
    the cap, so ONE more pass is spent — and the secant lands it."""
    calls = _rig(monkeypatch)
    out = _run(drop=None)
    e = _errs(out)
    assert len(e) == 3 and len(calls["v"]) == 3
    assert out["current_extra_pass"] is True
    assert abs(e[2]) < 1.0
    blk = cp._pwm_final_block(out, sine_coil_c=COIL_SINE, sine_magnet_c=100.0,
                              sine_bearing_c=None, tol=1.0, t_wall_s=1.0)
    assert blk["n_pwm_passes"] == 3 and blk["current_extra_pass"] is True
    assert blk["final_point_error_pct"] == pytest.approx(e[2], abs=1e-3)


def test_no_third_pass_for_a_temperature_residual_alone(monkeypatch):
    """Temperatures still moving after two passes, current inside the band:
    the cap holds and the residual is reported, not chased."""
    calls = _rig(monkeypatch, coil_rise_per_pass=(5.0, 3.0, 3.0))
    out = _run(drop=DROP)
    e = _errs(out)
    assert len(e) == 2 and len(calls["v"]) == 2
    assert all(abs(x) < 1.0 for x in e)
    assert out["converged"] is False
    assert out["current_extra_pass"] is False


def test_command_is_clamped_at_the_modulation_ceiling(monkeypatch):
    _rig(monkeypatch)
    monkeypatch.setitem(_INV, "v_phase_peak_max_V", 9.6)
    out = _run(drop=DROP)
    g = out["v1_first_guess"]
    assert g["at_modulation_ceiling"] is True
    assert out["passes"][0]["v_phase_peak_V"] == pytest.approx(9.6)


def test_report_pass_counts_and_errors(monkeypatch, capsys):
    """Not an assertion: the numbers the PR description quotes."""
    rows = []
    for label, drop, extra in (("old seed, 2-pass cap", None, 0),
                               ("old seed, +current pass", None, 1),
                               ("closed-form seed", DROP, 1)):
        _rig(monkeypatch)
        monkeypatch.setattr(cp, "PWM_FINAL_CURRENT_EXTRA_PASSES", extra)
        out = _run(drop)
        rows.append((label, len(out["passes"]), _errs(out)))
    with capsys.disabled():
        for label, n, e in rows:
            print("\n  %-26s passes %d  errors %s"
                  % (label, n, ", ".join("%+.2f %%" % x for x in e)))
