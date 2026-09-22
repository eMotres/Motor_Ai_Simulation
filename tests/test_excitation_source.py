"""The excitation SOURCE interface: five built-ins, and one written from outside.

``fem_transient_sliding_band`` no longer branches on ``drive=`` to decide what
it applies — it asks an :class:`ExcitationSource`.  These tests pin the two
halves of that contract:

* the factory still builds the five shipped sources from the solver's own
  keywords, and each one still declares the settle schedule its pinned numbers
  were produced under;
* a source written OUTSIDE this package, handed in through ``excitation=``, is
  what the solver actually applies — asserted on the payload's own
  ``excitation.A`` series, i.e. on the volt-seconds the circuit integrated.

The second one is the whole point of the refactor: if it passes, an external
controller / co-simulation can drive the FEM machine.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np
import pytest

from motor_ai_sim.simulation import excitation as ex
from motor_ai_sim.simulation.excitation import (
    BldcCurrentSource, CustomCurrentSource, ExcitationError, Feedback,
    PwmVoltageSource, SettlePolicy, SineCurrentSource, SineVoltageSource,
    make_source)

POLE_PAIRS = 7
DAXIS = 60.0
F_ELEC = 1750.0


def _fb(a: float, b: float, *, fine: bool = True, k: int = 0,
        i_abc=None, psi_abc=None) -> Feedback:
    return Feedback(k=k, theta_prev_deg=a, theta_deg=b, t0_s=0.0,
                    t1_s=1.0 / (F_ELEC * 12.0), fine=fine, i_abc=i_abc,
                    psi_abc=psi_abc, v_bus=None)


def _common(**over) -> Dict[str, Any]:
    kw = dict(pole_pairs=POLE_PAIRS, daxis_deg=DAXIS, I_phase_rms=60.0,
              gamma_deg=0.0, n_parallel=1, f_elec=F_ELEC)
    kw.update(over)
    return kw


# ── the factory round-trips every shipped drive ─────────────────────────────
# SIX since 2026-09-22: "inverter" is the CONTROLLER's bridge (Stage 2), the
# ideal two-level modulator with the dead time and the device drops of a named
# part on top.  It is a THIRD voltage source, not a change to the second — the
# ``pwm_voltage`` row below is untouched and still builds the ideal one.
def test_make_source_round_trips_every_drive():
    wf = [(x * 360.0 / 36.0, 40.0 * math.cos(math.radians(x * 360.0 / 36.0)))
          for x in range(36)]
    built = {
        "current": make_source("current", **_common()),
        "voltage": make_source("voltage", **_common(v_phase_peak=7.0,
                                                    v_delta_deg=10.0)),
        "pwm_voltage": make_source("pwm_voltage", **_common(
            v_phase_peak=7.0, v_delta_deg=10.0, v_bus=48.0, f_switch=28000.0)),
        "custom_current": make_source("custom_current", **_common(waveform=wf)),
        "bldc_current": make_source("bldc_current", **_common(i_block=50.0)),
        "inverter": make_source("inverter", **_common(
            v_phase_peak=7.0, v_delta_deg=10.0, v_bus=48.0, f_switch=28000.0,
            star_delta="star",
            inverter_nonideal={"r_ds_ohm": 0.0025, "v_sd_v0_V": 3.1,
                               "v_sd_rd_ohm": 0.004, "dead_time_s": 5e-7,
                               "device": "TEST", "devices_parallel": 2})),
    }
    assert set(built) == set(ex.DRIVES)
    from motor_ai_sim.inverter.coupling import InverterVoltageSource
    types = {"current": SineCurrentSource, "voltage": SineVoltageSource,
             "pwm_voltage": PwmVoltageSource,
             "inverter": InverterVoltageSource,
             "custom_current": CustomCurrentSource,
             "bldc_current": BldcCurrentSource}
    for name, src in built.items():
        assert isinstance(src, types[name]), name
        # The name the payload reports is the drive that was asked for.
        assert src.name == name
        assert src.kind == ("V" if name in ("voltage", "pwm_voltage",
                                            "inverter") else "I")
        d = src.describe()
        assert d["name"] == name
        assert d["series"] == src.kind
        # Every source answers all three signals.
        pol = src.settle_policy()
        assert isinstance(pol, SettlePolicy)
        s = src.on_steps_snapped(48) if hasattr(src, "on_steps_snapped") else src
        for th in (0.0, 7.3, 51.4):
            m = s.mean_over(_fb(th - 1.0, th))
            f = s.fundamental(th)
            assert set(m) == set("ABC") and set(f) == set("ABC")
            assert all(math.isfinite(v) for v in m.values())
            assert all(math.isfinite(v) for v in f.values())


def test_make_source_refuses_an_unknown_drive():
    with pytest.raises(ValueError, match="unknown excitation source"):
        make_source("pwm", **_common())


def test_bldc_needs_its_flat_top_amplitude():
    with pytest.raises(ExcitationError, match="i_block"):
        make_source("bldc_current", **_common(i_block=0.0))


def test_imposed_current_sources_apply_the_waveform_at_the_step_angle():
    """An imposed current is a CONSTRAINT at the frame's angle, not an integral."""
    src = make_source("current", **_common(gamma_deg=-20.0))
    for th in (0.0, 13.7, 200.1):
        assert src.mean_over(_fb(th - 3.0, th)) == src.fundamental(th)

    wf = [(x * 10.0, 40.0 * math.cos(math.radians(x * 10.0))) for x in range(36)]
    cu = make_source("custom_current", **_common(waveform=wf))
    assert cu.mean_over(_fb(1.0, 4.0)) == cu.sampled.currents(4.0)

    bl = make_source("bldc_current", **_common(i_block=50.0)).on_steps_snapped(48)
    assert bl.mean_over(_fb(1.0, 4.0)) == bl.block.currents(4.0)
    # The commutation ramp is exactly one time step wide.
    assert bl.ramp_deg == pytest.approx(360.0 / 48.0)


def test_bldc_block_is_unusable_until_the_step_count_is_final():
    """Its ramp is one TIME STEP wide, and that is only known after the snap."""
    raw = make_source("bldc_current", **_common(i_block=50.0))
    with pytest.raises(ExcitationError, match="on_steps_snapped"):
        raw.mean_over(_fb(0.0, 1.0))


# ── the PWM source's volt-seconds ARE its fundamental ───────────────────────
def test_pwm_mean_over_carries_exactly_the_requested_fundamental():
    """Integrating ``mean_over`` over one electrical period recovers the
    modulating sinusoid the source reports as its ``fundamental``.

    This is the identity the whole PWM source rests on: the frame loop only
    ever sees per-step MEANS, so if their fundamental content were not the
    requested one, every PWM run would silently be at a different operating
    point than the sinusoidal run it is compared against.  The modulator's
    delay/gain compensation exists to make it hold, and ``build_pwm_source``
    iterates until it does.
    """
    src = make_source("pwm_voltage", **_common(
        v_phase_peak=7.0, v_delta_deg=10.0, v_bus=48.0, f_switch=56000.0))
    period_mech = 360.0 / POLE_PAIRS
    n = src.carriers * 64
    d = period_mech / n
    acc_c = acc_s = 0.0
    for j in range(n):
        v = src.mean_over(_fb(j * d, (j + 1) * d))["A"]
        th = 2.0 * math.pi * (j + 0.5) / n
        acc_c += v * math.cos(th)
        acc_s += v * math.sin(th)
    c, s = 2.0 * acc_c / n, 2.0 * acc_s / n
    got_pk = math.hypot(c, s)
    # Against the SAME projection of the smooth fundamental the source reports.
    fac = fas = 0.0
    for j in range(n):
        v = src.fundamental((j + 0.5) * d)["A"]
        th = 2.0 * math.pi * (j + 0.5) / n
        fac += v * math.cos(th)
        fas += v * math.sin(th)
    ref_pk = math.hypot(2.0 * fac / n, 2.0 * fas / n)
    assert got_pk == pytest.approx(ref_pk, rel=1e-6)
    assert got_pk == pytest.approx(7.0, rel=1e-4)
    # ... and in PHASE with it, not merely equal in size.
    assert math.degrees(math.atan2(-s, c)) == pytest.approx(
        math.degrees(math.atan2(-2.0 * fas / n, 2.0 * fac / n)), abs=1e-3)


def test_pwm_per_carrier_mean_tracks_the_fundamental_and_converges():
    """Per CARRIER the two differ by the sampled-reference gain — real physics.

    A regular-sampled modulator holds each reference sample across half a
    carrier, so ONE carrier's volt-second mean is not the fundamental's value
    there; the gap is O(1/N_c^2) and vanishes as the pulse ratio rises.  Pinned
    as a CONVERGENCE, because reading it as an error is how someone "fixes" the
    modulator into a natural-sampled one that no DSP implements.
    """
    period_mech = 360.0 / POLE_PAIRS
    devs = []
    for nc, f_sw in ((16, 28000.0), (64, 112000.0), (256, 448000.0)):
        src = make_source("pwm_voltage", **_common(
            v_phase_peak=7.0, v_delta_deg=10.0, v_bus=48.0, f_switch=f_sw))
        assert src.carriers == nc
        dth = period_mech / nc
        worst = 0.0
        for j in range(nc):
            m = src.mean_over(_fb(j * dth, (j + 1) * dth))
            f = src.fundamental((j + 0.5) * dth)
            worst = max(worst, max(abs(m[p] - f[p]) for p in "ABC") / 7.0)
        devs.append(worst)
    assert devs[0] > devs[1] > devs[2]
    assert devs[2] < 1e-3


def test_pwm_coarse_settle_frames_see_the_smooth_fundamental():
    """fine=False is the mixed-settle contract: no modulator on those frames."""
    src = make_source("pwm_voltage", **_common(
        v_phase_peak=7.0, v_delta_deg=10.0, v_bus=48.0, f_switch=28000.0))
    a, b = 3.0, 4.5
    assert src.mean_over(_fb(a, b, fine=False)) == src.fundamental(0.5 * (b + a))
    assert src.mean_over(_fb(a, b, fine=True)) != src.fundamental(0.5 * (b + a))


# ── settle policies ─────────────────────────────────────────────────────────
@pytest.mark.skipif(bool(ex._V_SETTLE_ENV or ex._PWM_COARSE_SETTLE_ENV),
                    reason="SB_V_SETTLE_PERIODS / SB_PWM_COARSE_SETTLE set")
def test_settle_policy_defaults_per_source():
    wf = [(x * 10.0, 40.0 * math.cos(math.radians(x * 10.0))) for x in range(36)]
    imposed = [make_source("current", **_common()),
               make_source("custom_current", **_common(waveform=wf)),
               make_source("bldc_current", **_common(i_block=50.0))]
    for src in imposed:
        p = src.settle_policy()
        # Imposed currents are not circuit STATE: there is nothing to settle.
        assert p.periods_static == 0
        assert p.adaptive_tau_mult is None
        assert not p.aitken and not p.coarse_settle
        assert not p.dc_anchor

    p = make_source("voltage", **_common(v_phase_peak=7.0,
                                         v_delta_deg=10.0)).settle_policy()
    # TEN periods, never adaptive — every pinned voltage number used them.
    assert p.periods_static == 10
    assert p.adaptive_tau_mult is None
    # …and the Δ² flux anchor, not the period-mean DC one: every pinned
    # sinusoid number was produced with exactly this pair.
    assert p.aitken and not p.coarse_settle and not p.dc_anchor

    p = make_source("pwm_voltage", **_common(
        v_phase_peak=7.0, v_delta_deg=10.0, v_bus=48.0,
        f_switch=28000.0)).settle_policy()
    assert p.periods_static == 2          # a PWM window is mostly carrier ripple
    assert p.adaptive_tau_mult == pytest.approx(3.0)   # ceil(3·tau_e/T_e)
    assert p.periods_cap == 12
    # PWM anchors its DC on the PERIOD MEAN (exact) instead of Δ²-extrapolating
    # the period-boundary flux (which reads the carrier on a rippled sample),
    # and its settle ends in WHOLE fine periods so that mean is measurable.
    # B5 / PWM study 2026-09-13.
    assert p.coarse_settle and p.dc_anchor and not p.aitken
    assert p.fine_settle_periods == 2


def test_settle_policy_env_override_wins_for_every_source(monkeypatch):
    """An explicit SB_V_SETTLE_PERIODS outranks the source AND the adaptive rule."""
    monkeypatch.setattr(ex, "_V_SETTLE_ENV", "4")
    assert ex._settle_periods(10) == 4
    assert ex._settle_periods(2) == 4
    p = make_source("pwm_voltage", **_common(
        v_phase_peak=7.0, v_delta_deg=10.0, v_bus=48.0,
        f_switch=28000.0)).settle_policy()
    assert p.periods_static == 4
    assert p.adaptive_tau_mult is None    # explicit count => no L/R adaptation


# ── an OUTSIDE source drives the real solver ────────────────────────────────
class _ScriptedVoltage:
    """A voltage source written outside the package, with a memory.

    It applies the same 7 V / +10 deg sinusoid the pinned ``p2_voltage`` case
    uses (so the machine sees a sane operating point) and RECORDS every value
    it handed out.  The payload must then contain exactly that record.
    """

    kind = "V"
    name = "scripted_v"
    rms_from_series = True

    def __init__(self, pole_pairs: int, daxis_deg: float, v_pk: float,
                 v_delta_deg: float):
        self.pp, self.daxis = int(pole_pairs), float(daxis_deg)
        self.v_pk, self.delta = float(v_pk), float(v_delta_deg)
        self.applied: List[Dict[str, float]] = []
        self.saw_feedback = 0

    def _at(self, theta_deg: float) -> Dict[str, float]:
        te = math.radians(theta_deg * self.pp + self.delta + self.daxis)
        return {"A": self.v_pk * math.cos(te),
                "B": self.v_pk * math.cos(te - 2 * math.pi / 3),
                "C": self.v_pk * math.cos(te + 2 * math.pi / 3)}

    def mean_over(self, fb: Feedback) -> Dict[str, float]:
        assert fb.t1_s >= fb.t0_s and fb.dt_s > 0.0
        if fb.i_abc is not None:          # the one-step measurement delay
            self.saw_feedback += 1
        v = self._at(0.5 * (fb.theta_deg + fb.theta_prev_deg))
        self.applied.append(v)
        return v

    def fundamental(self, theta_deg: float) -> Dict[str, float]:
        return self._at(theta_deg)

    def nominal_currents(self, theta_deg: float) -> Dict[str, float]:
        te = math.radians(theta_deg * self.pp + self.daxis)
        return {"A": 85.0 * math.cos(te),
                "B": 85.0 * math.cos(te - 2 * math.pi / 3),
                "C": 85.0 * math.cos(te + 2 * math.pi / 3)}

    def settle_policy(self) -> SettlePolicy:
        # No settle: the reported window is then every frame the source saw, so
        # the assertion below is on the WHOLE record with nothing trimmed.
        return SettlePolicy(periods_static=0, aitken=False)

    def describe(self, ctx=None) -> Dict[str, Any]:
        return {"name": self.name, "series": "V",
                "quantity": "scripted phase voltage [V]",
                "v_phase_peak_V": self.v_pk, "v_delta_deg": self.delta,
                "pwm": None, "custom_current": None, "bldc": None}


@pytest.mark.slow
def test_an_outside_source_is_what_the_solver_applies():
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
    from tests.test_physics_regression import (
        COMMON, CONNECTION, GEO_30MM, OVERRIDE, RPM)

    src = _ScriptedVoltage(pole_pairs=int(GEO_30MM["num_poles_per_segment"]),
                           daxis_deg=60.0, v_pk=7.0, v_delta_deg=10.0)
    kw = dict(COMMON)
    kw.update(element_order=2, demag=False, n_steps_per_period=12,
              n_periods=1.0, I_phase_rms=60.0, gamma_deg=0.0)
    set_request_materials(OVERRIDE)
    try:
        d = fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                       connection=CONNECTION, excitation=src,
                                       **kw)
    finally:
        set_request_materials(None)

    # The payload names the source, not a built-in drive.
    assert d["drive"] == "scripted_v"
    assert d["excitation"]["kind"] == "scripted_v"
    assert d["excitation"]["series"] == "V"
    assert d["excitation"]["quantity"] == "scripted phase voltage [V]"
    assert d["pwm"] is None and d["bldc"] is None and d["custom_current"] is None

    # ... and carries EXACTLY the volt-seconds the source handed out.
    assert len(src.applied) == len(d["excitation"]["A"]) == d["n_steps"]
    for ph in "ABC":
        got = np.asarray(d["excitation"][ph], float)
        want = np.asarray([v[ph] for v in src.applied], float)
        assert np.array_equal(got, want), ph

    # The circuit was really solved on them: the currents are the ANSWER.
    assert d["v_drive_diag"] is not None
    assert max(d["v_drive_diag"]["resid"]) < 1e-6
    assert float(d["I_phase_rms_solved_A"]) > 1.0
    # And the source saw the one-step-delayed measurement it is entitled to.
    assert src.saw_feedback >= int(d["n_steps"]) - 1
