"""STAGE 2 — the controller driving the electromagnetic solver.

Owner, 2026-09-22: *«как отладим каплинг с контроллером, нам не нужен будет PWM
в электромагнитном моделировании — всё будет задаваться в меню Controller»*, and
*«как закончишь лимиты, запускай каплинг — сначала стандартный инвертор на L155
motor»*.

WHAT IS NEW, AND WHAT IS NOT
============================
``drive: "pwm"`` feeds the machine an IDEAL two-level bridge: the leg terminal
is ±V_dc/2, exactly, at the instant the comparator says so.  This module is the
same bridge with the three things a real one has:

* **device drops** — the conducting channel drops ``i·R_DS(on)(T_j)`` and the
  sign does not depend on which switch is on (``waveforms`` §: HS on with i > 0
  gives ``+V/2 − i·R``, LS on with i > 0 gives ``−V/2 − i·R``), so the drop is
  ``−i·R`` on the pole, always.  ``R_DS(on)`` is the CARD's, at the junction
  temperature the controller solve last converged on, divided by the parallel
  device count.
* **dead time** — for ``t_d`` after every commanded edge both channels are off
  and the leg terminal is pulled by the CURRENT into a body diode:
  ``−V/2 − V_SD`` while the current leaves the leg, ``+V/2 + V_SD`` while it
  enters.  Over a whole carrier period that is the classic square-wave error
  ``ΔV = −sign(i)·t_d·f_sw·(V_dc + 2·V_SD)`` — ±8.99 V per leg on the 750.4 V
  link at L155 rated, i.e. 1.2 % of the link and 3.8 % of that leg's own
  fundamental pole voltage — and a SQUARE WAVE in the sign of the current,
  which is why it injects 5th and 7th and why the ideal modulator cannot stand
  in for it.
* **the current that decides both** — not an assumed sinusoid: the solver hands
  every source the PREVIOUS converged step's phase currents
  (``excitation.Feedback.i_abc``), which is the sampling delay real hardware
  has.  So the loop *controller → EM → controller* is closed INSIDE the
  transient at the time-step level, and the outer iteration this module drives
  is only the slow one: solved current → device losses → junction temperature →
  new ``R_DS(on)`` and ``V_SD`` → run again.

THE STAR-EQUIVALENT SUBSTITUTION IS INHERITED, NOT RE-INVENTED.  A delta machine
is solved on the star equivalent with a √3-scaled model bus (``simulation/pwm``,
PWM study §0.3), so the model's "phase voltage" IS the real LINE-TO-LINE
voltage.  The non-ideal correction is therefore mapped the same way and it is
EXACT, not a scaling:

    star    e_A = err(i_leg_A)
    delta   e_A = err(i_leg_A) − err(i_leg_B)      (= the real v_AB error)

with the LEG (device) current reconstructed from the solved branch currents —
``i_leg_A = n_parallel·I_A`` in star, ``n_parallel·(I_A − I_C)`` in delta, which
is the √3, 30°-lagging line current the bridge outside the delta really carries
(the bridge sits outside the delta; the circulating triplen stays inside it and
stays physical, which is the whole point of comparing with the H-bridge later).

WHAT IS MEASURED AND WHAT IS ASSUMED is not hidden behind an interface: the
body-diode drop is a TWO-PARAMETER fit (``V_SD(i) = v0 + r_d·i``) to the card's
own curve over the current this run actually carries, because a per-sample card
lookup inside a FEM time loop is thousands of interpolations per frame for a
number whose own tolerance is ±10 %.  The fit's worst deviation over that span
is reported (:attr:`DeviceDrop.v_sd_fit_max_err_V`) and it is a card field
nobody has to take on trust.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

import numpy as np

from motor_ai_sim.simulation.excitation import (Feedback,
                                                PwmVoltageSource as _ExcPwm,
                                                _modulator_words)

__all__ = ["DeviceDrop", "fit_device_drop", "leg_currents",
           "InverterVoltageSource", "build_inverter_source",
           "pole_error_volts"]

#: Phase order, the one every abc quantity in this project is written in.
_ABC = ("A", "B", "C")
#: Leg reference phase shifts, the modulator's own (``pwm.PwmVoltageSource``).
_SHIFT = {"A": 0.0, "B": -120.0, "C": 120.0}


# ---------------------------------------------------------------------------
# The device, reduced to what a time loop can afford to evaluate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceDrop:
    """One leg's non-ideal terminal behaviour, in the LEG's own current.

    Every field is per LEG and already carries the parallel device count, so a
    caller inside the time loop never has to remember to divide by ``N``:

    ``r_ds_ohm``      ``R_DS(on)(T_j, V_GS)/N`` — the leg's channel resistance.
    ``v_sd_v0_V``     the body-diode fit's threshold.
    ``v_sd_rd_ohm``   its slope IN THE LEG CURRENT (the card's own slope over
                      ``N``), so ``V_SD = v0 + r_d·|i_leg|``.
    ``dead_time_s``   one dead-time window.
    """

    r_ds_ohm: float
    v_sd_v0_V: float
    v_sd_rd_ohm: float
    dead_time_s: float
    #: Provenance, printed rather than assumed.
    device: str = ""
    t_j_c: float = 0.0
    devices_parallel: int = 1
    v_gs_on_V: float = 0.0
    v_gs_off_V: float = 0.0
    r_ds_on_mohm_device: float = 0.0
    v_sd_fit_max_err_V: float = 0.0
    v_sd_fit_span_A: float = 0.0
    v_sd_fit_from_A: float = 0.0

    def v_sd(self, i_leg_abs: float) -> float:
        """Body-diode drop at a LEG current [V], never negative."""
        return max(0.0, self.v_sd_v0_V + self.v_sd_rd_ohm * abs(i_leg_abs))

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: (round(v, 8) if isinstance(v, float) else v)
                for k, v in d.items()}


def fit_device_drop(card, *, t_j_c: float, n_parallel: int,
                    i_leg_peak_A: float, v_gs_on_V: float = 18.0,
                    v_gs_off_V: float = 0.0, dead_time_s: float = 0.0
                    ) -> DeviceDrop:
    """Reduce a device card to a :class:`DeviceDrop` at one junction temperature.

    The channel is exact — ``R_DS(on)`` is a single card look-up.  The body
    diode is a least-squares straight line through the card's own ``V_SD(I_SD)``
    over ``0.1·i_peak … i_peak`` of DEVICE current, and the worst deviation over
    that span is carried with it.

    THE TOE IS DELIBERATELY OUTSIDE THE FIT.  A diode's characteristic is
    logarithmic below its knee — this card reads 0.5 V at 1 A and 3.0 V at 25 A
    — so a straight line asked to cover zero would be several tenths of a volt
    wrong everywhere in exchange for being right where nothing happens.  Above
    the knee the same line tracks the card to a few hundredths.  What the model
    costs at the toe is that it over-reads the clamp by up to ``v0`` while the
    current is crossing zero, which is ``2·v0`` of ``V_dc + 2·V_SD`` — under one
    per cent of a dead-time error that is itself ~1 % of the fundamental.

    The card's characteristics are CLAMPED to the hottest temperature it
    tabulates, exactly as ``losses.solve_controller`` clamps them, so the two
    halves of one solve cannot be quoting the device at two temperatures.
    """
    n = max(int(n_parallel), 1)
    t_cap = float(card.t_j_curve_max_c())
    t_eval = min(float(t_j_c), t_cap)
    r_dev = float(card.r_ds_on_ohm(t_eval, v_gs_on_V))
    i_dev_peak = max(abs(float(i_leg_peak_A)) / n, 1e-6)
    i_lo = 0.1 * i_dev_peak
    grid = np.linspace(i_lo, i_dev_peak, 17)
    v = np.array([float(card.v_sd_V(float(x), t_eval, v_gs_off_V))
                  for x in grid])
    # Least squares on the card's own curve.  Not a fit through two endpoints:
    # the curve is convex and the endpoints would over-read the middle, which
    # is where a sinusoid spends its time.
    a = np.vstack([np.ones_like(grid), grid]).T
    (v0, slope), *_ = np.linalg.lstsq(a, v, rcond=None)
    err = float(np.max(np.abs(a @ np.array([v0, slope]) - v))) if v.size else 0.0
    return DeviceDrop(
        r_ds_ohm=r_dev / n,
        v_sd_v0_V=float(v0),
        v_sd_rd_ohm=float(slope) / n,
        dead_time_s=float(dead_time_s),
        device=str(getattr(card, "part", "")),
        t_j_c=float(t_j_c),
        devices_parallel=n,
        v_gs_on_V=float(v_gs_on_V),
        v_gs_off_V=float(v_gs_off_V),
        r_ds_on_mohm_device=r_dev * 1e3,
        v_sd_fit_max_err_V=err,
        v_sd_fit_span_A=float(i_dev_peak),
        v_sd_fit_from_A=float(i_lo))


# ---------------------------------------------------------------------------
# From the solved branch currents to the device currents
# ---------------------------------------------------------------------------

def leg_currents(i_abc: Dict[str, float], *, star_delta: str,
                 n_parallel: int = 1) -> Dict[str, float]:
    """The current each BRIDGE LEG carries [A], from the solver's branch currents.

    ``i_abc`` is what the transient hands a source: the current of ONE parallel
    path of one winding branch.  The bridge sits outside the winding, so:

    * **star** — the leg current is the branch current times the parallel paths;
    * **delta** — the bridge is outside the triangle, so a leg carries the
      DIFFERENCE of the two branches that meet at its terminal:
      ``i_leg_A = i_AB − i_CA``, which for a balanced set is √3 times the branch
      current and lags it by 30°.  The circulating triplen current stays inside
      the delta and never reaches a device, which is exactly right.
    """
    k = float(max(int(n_parallel), 1))
    a, b, c = (float(i_abc.get("A", 0.0)), float(i_abc.get("B", 0.0)),
               float(i_abc.get("C", 0.0)))
    if str(star_delta or "star").strip().lower() == "delta":
        return {"A": k * (a - c), "B": k * (b - a), "C": k * (c - b)}
    return {"A": k * a, "B": k * b, "C": k * c}


# ---------------------------------------------------------------------------
# The pole-voltage error of ONE leg over ONE time step
# ---------------------------------------------------------------------------

def pole_error_volts(*, modulator, drop: DeviceDrop, phase: str,
                     i_leg_A: float, psi_a_deg: float, psi_b_deg: float,
                     v_dc_real_V: float, deg_per_s: float) -> float:
    """Mean error of one leg's terminal voltage over ``[psi_a, psi_b]`` [V].

    Two terms, and they are added to the IDEAL pole voltage the modulator
    already produced:

    **the channel drop** ``−i·R_DS(on)/N`` — independent of which switch is on,
    so it is a plain constant over the step (the current moves by well under a
    per cent inside one FEM step).

    **the dead time** — for each commanded edge whose window overlaps this step,
    the volt-seconds the diode clamp puts in instead of what was commanded:

    ======================  ====================  ====================
    edge                    i > 0 (leaving)       i < 0 (entering)
    ======================  ====================  ====================
    rising  (LS → HS)       ``−(V_dc + V_SD)``    ``+V_SD``
    falling (HS → LS)       ``−V_SD``             ``+(V_dc + V_SD)``
    ======================  ====================  ====================

    Their sum over a whole carrier period is ``−sign(i)·(V_dc + 2·V_SD)·t_d``,
    i.e. the textbook ``ΔV = −sign(i)·t_d·f_sw·(V_dc + 2·V_SD)`` — which is what
    ``tests/test_inverter_coupling.py`` measures this function against.

    Windows are CLIPPED to the step, so a step finer than the dead time gets the
    distortion resolved in time rather than smeared, and a step coarser than a
    carrier gets its exact mean.  ``V_dc`` here is the REAL DC link, never the
    star-equivalent model bus: a device drop is a property of the power stage,
    not of the change of variable the delta machine is solved through.
    """
    dt_deg = float(psi_b_deg) - float(psi_a_deg)
    if dt_deg <= 0.0:
        return 0.0
    s = 0.0 if i_leg_A == 0.0 else math.copysign(1.0, i_leg_A)
    out = -float(i_leg_A) * float(drop.r_ds_ohm)
    td_deg = float(drop.dead_time_s) * float(deg_per_s)
    if td_deg <= 0.0 or s == 0.0:
        return out
    v_sd = drop.v_sd(abs(i_leg_A))
    vdc = float(v_dc_real_V)
    e_rise = (-(vdc + v_sd)) if s > 0 else (+v_sd)
    e_fall = (-v_sd) if s > 0 else (+(vdc + v_sd))
    shift = _SHIFT[phase]
    d_ang = 360.0 / max(int(modulator.carriers), 1)
    acc = 0.0
    j0 = int(math.floor((psi_a_deg - td_deg) / d_ang))
    j1 = int(math.floor(psi_b_deg / d_ang))
    for j in range(j0, j1 + 1):
        lo, hi = modulator._pulse(j, shift)          # noqa: SLF001 — one package
        if hi - lo <= 1e-12:
            continue                                 # pulse dropped: no edges
        # The window cannot be longer than the segment it eats into, or the
        # leg would never reach the state that was commanded at all.
        w_rise = min(td_deg, hi - lo)
        w_fall = min(td_deg, (j + 1) * d_ang - hi)
        acc += e_rise * _overlap(lo, lo + w_rise, psi_a_deg, psi_b_deg)
        acc += e_fall * _overlap(hi, hi + w_fall, psi_a_deg, psi_b_deg)
    return out + acc / dt_deg


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------

class InverterVoltageSource(_ExcPwm):
    """The Controller's own bridge, driving the electromagnetic transient.

    Everything the IDEAL source does is inherited — the compensated reference,
    the exact per-step volt-second means, the settle policy, the DC-link switch
    function — and exactly one method is overridden: :meth:`mean_over`, which
    adds the device drop and the dead-time clamp on top of the ideal pole means,
    using the current the solver has just measured.
    """

    name = "inverter"

    def __init__(self, exc, modulator, *, v_phase_peak: float,
                 v_delta_deg: float, f_switch_requested_hz: float,
                 drop: DeviceDrop, v_dc_real_V: float, star_delta: str,
                 n_parallel: int = 1, pole_pairs: int = 1,
                 f_elec_hz: float = 0.0, topology: str = "one_3ph"):
        super().__init__(exc, modulator, v_phase_peak=v_phase_peak,
                         v_delta_deg=v_delta_deg,
                         f_switch_requested_hz=f_switch_requested_hz)
        self.drop = drop
        self.v_dc_real_V = float(v_dc_real_V)
        self.star_delta = str(star_delta or "star").strip().lower()
        self.n_parallel = max(int(n_parallel), 1)
        self.pole_pairs = max(int(pole_pairs), 1)
        self.f_elec_hz = float(f_elec_hz)
        self.topology = str(topology)
        #: Electrical degrees per second — the dead-time window's width in the
        #: modulator's own coordinate.
        self.deg_per_s = 360.0 * float(f_elec_hz)
        #: Measured while the run marches — the peak leg current any device
        #: saw, which is what the limit table's I_DM row is judged on, and the
        #: rms the loss model is re-seeded with.
        self._i_leg_sq = {k: 0.0 for k in _ABC}
        self._i_leg_peak = 0.0
        self._fine_seen = 0

    # ── the one thing that is different ────────────────────────────────────
    def mean_over(self, fb: Feedback) -> Dict[str, float]:
        base = super().mean_over(fb)
        if not fb.fine or fb.i_abc is None:
            return base
        legs = leg_currents(fb.i_abc, star_delta=self.star_delta,
                            n_parallel=self.n_parallel)
        self._fine_seen += 1
        for k in _ABC:
            self._i_leg_sq[k] += legs[k] ** 2
            self._i_leg_peak = max(self._i_leg_peak, abs(legs[k]))
        psi_a = float(fb.theta_prev_deg) * self.pole_pairs
        psi_b = float(fb.theta_deg) * self.pole_pairs
        if psi_b <= psi_a:
            return base
        err = {k: pole_error_volts(modulator=self._mod, drop=self.drop,
                                   phase=k, i_leg_A=legs[k], psi_a_deg=psi_a,
                                   psi_b_deg=psi_b,
                                   v_dc_real_V=self.v_dc_real_V,
                                   deg_per_s=self.deg_per_s)
               for k in _ABC}
        if self.star_delta == "delta":
            # The model's phase voltage IS the real line-to-line voltage, so
            # the error injected into model pole A is the error of v_AB.
            return {"A": base["A"] + err["A"] - err["B"],
                    "B": base["B"] + err["B"] - err["C"],
                    "C": base["C"] + err["C"] - err["A"]}
        return {k: base[k] + err[k] for k in _ABC}

    # ── what the record has to be able to say about it ─────────────────────
    def measured(self) -> Dict[str, Any]:
        """Leg currents as the RUN saw them — rms per leg and the worst peak."""
        n = max(self._fine_seen, 1)
        rms = {k: math.sqrt(self._i_leg_sq[k] / n) for k in _ABC}
        return {
            "i_leg_rms_A": {k: round(v, 3) for k, v in rms.items()},
            "i_leg_rms_mean_A": round(sum(rms.values()) / 3.0, 3),
            "i_leg_peak_A": round(self._i_leg_peak, 3),
            "fine_steps": int(self._fine_seen),
            "note": ("over every FINE step the transient marched (the settle "
                     "prefix included); the reported-window rms is the "
                     "summary's own I_line"),
        }

    def describe(self, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        d = super().describe(ctx)
        pwm = dict(d.get("pwm") or {})
        pwm["modulator"] = (
            "the CONTROLLER's bridge: synchronous regular-sampled "
            "centre-aligned %s WITH dead time and device drops "
            "(%s, %d per switch, %.3g us dead time on a %.1f V link)"
            % (_modulator_words(getattr(self._mod, "modulation", "sine")),
               self.drop.device or "?", self.drop.devices_parallel,
               self.drop.dead_time_s * 1e6, self.v_dc_real_V))
        pwm["nonideal"] = {
            "source": "controller",
            "device": self.drop.device,
            "devices_parallel": self.drop.devices_parallel,
            "topology": self.topology,
            "dead_time_us": round(self.drop.dead_time_s * 1e6, 4),
            # DERIVED, not remembered: a source built from the four physics
            # scalars the route carries knows the leg's resistance and the
            # parallel count, so one device's is arithmetic.  The fields it
            # genuinely does NOT know — the gate drive it was read at, the
            # body-diode fit's own quality — are LEFT OUT rather than printed
            # as 0.0; the record's `device_drop` block carries all of them,
            # because that one is built by the module that read the card.
            "r_ds_on_mohm_device": round(
                self.drop.r_ds_ohm * self.drop.devices_parallel * 1e3, 4),
            "r_ds_on_mohm_leg": round(self.drop.r_ds_ohm * 1e3, 4),
            "t_j_c": round(self.drop.t_j_c, 2),
            "v_sd_model_V": ("%.3f + %.5g*i_leg"
                             % (self.drop.v_sd_v0_V, self.drop.v_sd_rd_ohm)),
            **({"v_sd_fit_max_err_V": round(self.drop.v_sd_fit_max_err_V, 4),
                "v_sd_fit_span_A": round(self.drop.v_sd_fit_span_A, 2)}
               if self.drop.v_sd_fit_span_A > 0.0 else {}),
            **({"v_gs_on_V": self.drop.v_gs_on_V,
                "v_gs_off_V": self.drop.v_gs_off_V}
               if self.drop.v_gs_on_V else {}),
            # The textbook fundamental error this dead time produces at the
            # measured leg current — the number the note quotes, so a reader
            # can size the effect without re-deriving it.
            "dead_time_error_V": round(self.dead_time_error_V(), 3),
            "star_delta": self.star_delta,
            "leg_current_from": (
                "i_leg = n_parallel*(I_A - I_C) — the bridge is OUTSIDE the "
                "delta" if self.star_delta == "delta" else
                "i_leg = n_parallel*I_A — the bridge is in series with the "
                "star branch"),
            "measured": self.measured(),
            # WHAT THIS SOURCE DOES NOT CHANGE, said rather than left to be
            # discovered: the DC-link series beside it is still computed from
            # the COMMANDED switching functions, so the bus current carries no
            # dead-time notch.  Over a carrier period the notch moves charge
            # between the two rails without changing the mean, and the
            # capacitor rms it costs is second order in t_d·f_sw (1.2 % here) —
            # but it is not modelled, and "not modelled" is not "zero".
            "dc_link_note": ("the DC-link series is the COMMANDED switching "
                             "function's — the dead-time notch is not in it"),
        }
        d["pwm"] = pwm
        d["quantity"] = ("controller leg voltage [V] relative to the DC-link "
                         "mid-point, with dead-time distortion and device "
                         "drops")
        return d

    def dead_time_error_V(self) -> float:
        """``t_d·f_sw·(V_dc + 2·V_SD)`` at the measured peak leg current [V]."""
        f_sw = float(self._mod.carriers) * max(self.f_elec_hz, 0.0)
        i = self._i_leg_peak or 0.0
        return (self.drop.dead_time_s * f_sw
                * (self.v_dc_real_V + 2.0 * self.drop.v_sd(i)))


def build_inverter_source(*, pole_pairs: int, daxis_deg: float,
                          v_phase_peak: float, v_delta_deg: float,
                          v_bus_model: float, v_dc_real: float,
                          f_switch_hz: float, f_elec_hz: float,
                          drop: DeviceDrop, star_delta: str,
                          n_parallel: int = 1, I_phase_rms: float = 0.0,
                          gamma_deg: float = 0.0, topology: str = "one_3ph",
                          modulation: Optional[str] = "sine"
                          ) -> InverterVoltageSource:
    """Build the Stage-2 source through the SAME factory the ideal one uses.

    ``v_bus_model`` is the bus the modulator chops (√3·V_dc on a delta machine
    solved through its star equivalent); ``v_dc_real`` is the link the devices
    are actually bolted across, and it is the one the drops and the dead-time
    clamp are computed on.  Keeping the two apart is the whole reason this
    signature is not shorter.

    ``modulation`` — the Controller's three-phase modulation (``sine``,
    ``svpwm``, ``third_harmonic``).  The dead-time clamp below reads the
    edges of the SAME comparator, so it follows the injected references with
    no second model of them.
    """
    from motor_ai_sim.simulation.drive import Excitation
    from motor_ai_sim.simulation.pwm import build_pwm_source

    n_par = max(int(n_parallel), 1)
    exc = Excitation(pole_pairs=int(pole_pairs), daxis_deg=float(daxis_deg),
                     i_peak=float(I_phase_rms) / n_par * math.sqrt(2.0),
                     gamma_deg=float(gamma_deg),
                     v_peak=float(v_phase_peak), v_delta_deg=float(v_delta_deg))
    mod = build_pwm_source(
        pole_pairs=int(pole_pairs), daxis_deg=float(daxis_deg),
        v_phase_peak=float(v_phase_peak), v_delta_deg=float(v_delta_deg),
        v_bus=float(v_bus_model), f_switch_hz=float(f_switch_hz),
        f_elec_hz=float(f_elec_hz), v_bus_real=float(v_dc_real),
        modulation=modulation)
    return InverterVoltageSource(
        exc, mod, v_phase_peak=float(v_phase_peak),
        v_delta_deg=float(v_delta_deg),
        f_switch_requested_hz=float(f_switch_hz), drop=drop,
        v_dc_real_V=float(v_dc_real), star_delta=star_delta,
        n_parallel=n_par, pole_pairs=int(pole_pairs),
        f_elec_hz=float(f_elec_hz), topology=topology)
