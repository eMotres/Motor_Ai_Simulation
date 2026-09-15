"""ONE plug for everything that decides WHAT the transient applies.

``fem_transient_sliding_band`` used to branch on ``drive=`` in a dozen places:
which waveform the frame integrates, which settling schedule it marches, which
report block the payload carries.  Five sources times a dozen branches is how a
solver core stops being a solver core — and it is the wrong shape for the thing
this is heading towards, which is a machine model driven by an EXTERNAL
controller (a co-simulated FOC loop, a measured inverter log, a hardware
model-in-the-loop rig) rather than by one of five hard-coded waveforms.

So the excitation is an OBJECT behind a small interface, and the solver asks it
three questions and nothing else:

* :meth:`ExcitationSource.mean_over` — given a :class:`Feedback` (the step's
  rotor motion, its clock, whether it is a real modulator frame, and the
  PREVIOUS converged step's currents and flux linkages), what is the MEAN of
  the applied quantity over this step?  For a voltage source that mean is
  exactly the volt-seconds/Δt the Crank–Nicolson circuit residual integrates;
  for a current source it is the value imposed at the step's angle.
* :meth:`ExcitationSource.fundamental` — the smooth fundamental at an angle,
  for the coarse settle frames and for the dq phasor initialiser (a chopped
  waveform has no meaning in a fundamental-frequency phasor problem).
* :meth:`ExcitationSource.settle_policy` — how many periods of start-up the
  circuit state needs before the reported window, and which accelerators
  (coarse settle, Aitken flux anchors, the period-mean DC anchor) apply.

``kind`` ('I' or 'V') is the ONE thing the solver switches on, and it switches
on it for a reason that is not a mode flag: an imposed current is a source term
in the field problem, an imposed voltage makes the currents circuit UNKNOWNS
solved by a bordered Newton.  That is two different linear algebras, not two
settings.

The one-step delay in :class:`Feedback` is deliberate and physical: a real
controller samples, computes, and applies on the NEXT PWM update, so a source
that closes a loop here has exactly the sampling delay its hardware would.

Nothing in this module touches the mesh or the field.  It is algebra on three
phase quantities — like ``drive.py`` and ``pwm.py``, whose sources it wraps
rather than re-implements: there is exactly one copy of the modulator maths and
it lives in ``pwm.py``.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Sequence

from motor_ai_sim.simulation.drive import Excitation
from motor_ai_sim.simulation.pwm import (
    BlockCurrentSource,
    CustomCurrentSource as _SampledCurrent,
    ExcitationError,
    PwmVoltageSource as PwmModulator,
    build_pwm_source,
    dc_link_series as _dc_link_series,
    parse_waveform,
)

__all__ = [
    "Feedback", "SettlePolicy", "ExcitationSource", "ExcitationError",
    "SineCurrentSource", "SineVoltageSource", "PwmVoltageSource",
    "BldcCurrentSource", "CustomCurrentSource", "make_source", "DRIVES",
]

DRIVES = ("current", "voltage", "pwm_voltage", "custom_current",
          "bldc_current")


# ═══════════════════════════════════════════════════════════════════════════
#  ENV KNOBS — read HERE, once, so the settle policy has exactly one owner
# ═══════════════════════════════════════════════════════════════════════════
# These used to live at module scope in fem_solver_2d.py.  They belong with the
# source: every one of them answers "how long does THIS excitation take to
# settle", which is a property of what is being applied, not of the field
# solver.  The semantics are unchanged, knob for knob — see SettlePolicy.
#
# SB_V_SETTLE_PERIODS: settling PERIODS marched and discarded before the
# reported window on an imposed-VOLTAGE run.  An EXPLICIT value wins for every
# source AND switches the adaptive L/R rule off; unset lets each source pick
# (10 for the sinusoid — every pinned voltage number was produced with it — and
# 2 for PWM, whose window is tens of percent of switching ripple).
_V_SETTLE_ENV = (os.environ.get("SB_V_SETTLE_PERIODS") or "").strip()
# SB_V_SETTLE_TAU_MULT / SB_V_SETTLE_MAX: the adaptive PWM settle, periods =
# clamp(ceil(mult·τ_e/T_e), static, cap) with τ_e = max(Ld, Lq)/R from the
# phasor initialiser.  k = 3 leaves e⁻³ ≈ 5 % of whatever DC the phasor init
# did not already remove.
_SETTLE_TAU_MULT = max(0.0, float(
    os.environ.get("SB_V_SETTLE_TAU_MULT", "3") or 3))
_SETTLE_MAX = max(1, int(os.environ.get("SB_V_SETTLE_MAX", "12") or 12))
# (SB_PWM_HANDOVER_DC is gone.  It removed the switching turn-on DC at the
# pre-roll -> window handover by comparing the rippled current against a
# trigonometric interpolant of the last COARSE settle period, and it
# over-corrected on CILN28/G2-L40 (residual +3.3 A -> -15.6 A, ripple 50 ->
# 89 %) because the interpolant leaks ripple into the estimate.  Superseded by
# SettlePolicy.dc_anchor, which needs no reference orbit at all.  B5 / PWM
# study 2026-09-13.)
# SB_PWM_COARSE_SETTLE: "0" forces the all-fine march, "1" forces the mixed
# scheme, unset -> AUTO (mixed only while the run is itself a coarse pass; the
# steps-per-carrier test needs the run's step count and stays in the solver).
_PWM_COARSE_SETTLE_ENV = (
    os.environ.get("SB_PWM_COARSE_SETTLE") or "").strip()
# (SB_PWM_PREROLL_CARRIERS is gone with it: a fine pre-roll a couple of CARRIERS
# wide is shorter than one electrical period, and the DC in a window that is not
# a whole period cannot be measured at all.  SB_PWM_FINE_SETTLE below marches
# WHOLE fine periods instead.)
# SB_PWM_FINE_SETTLE: WHOLE fine settling periods at the end of a mixed
# coarse/fine settle — the window the period-mean DC anchor measures the
# switching turn-on DC over.  Two, measured: one anchor takes 40 A to ~3 A
# (the operating-point inductance columns are ~10 % off), the second to <0.5 A.
_PWM_FINE_SETTLE = max(1, int(os.environ.get("SB_PWM_FINE_SETTLE", "2") or 2))


def _settle_periods(default: int) -> int:
    """The static settle count for a source whose own default is ``default``.

    An explicit SB_V_SETTLE_PERIODS outranks every source — the same rule the
    two module constants in fem_solver_2d.py encoded.
    """
    return max(0, int(_V_SETTLE_ENV or default))


# ═══════════════════════════════════════════════════════════════════════════
#  THE SIGNAL INTERFACE
# ═══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Feedback:
    """Everything the solver knows when it asks a source what to apply.

    The measured quantities are ONE STEP OLD by construction: they are the
    values of the last CONVERGED step, because this call happens before the
    current step is solved.  That is not a limitation to work around — it is
    the sampling delay a real controller has, so a closed-loop source written
    against this interface behaves like the hardware it models.
    """

    k: int                          # frame index; NEGATIVE on eddy warm-up frames
    theta_prev_deg: float           # rotor angle at the START of the step [mech deg]
    theta_deg: float                # rotor angle at the END of the step [mech deg]
    t0_s: float                     # elapsed time at the start of the step [s]
    t1_s: float                     # elapsed time at the end of the step [s]
    fine: bool                      # True on frames that run the REAL modulator;
                                    # False on the coarse settle frames, which see
                                    # the smooth fundamental instead
    i_abc: Optional[Dict[str, float]] = None   # previous converged step's currents [A]
    psi_abc: Optional[Dict[str, float]] = None  # ... and flux linkages [Wb]
    v_bus: Optional[float] = None   # DC link voltage [V], where there is one

    @property
    def dt_s(self) -> float:
        """Length of the step [s] — what ``mean_over``'s mean is taken over."""
        return float(self.t1_s) - float(self.t0_s)


@dataclass(frozen=True)
class SettlePolicy:
    """How long the circuit state of THIS source takes to reach its orbit.

    Every field is a knob the solver used to read off ``drive == "pwm_voltage"``
    checks; the numbers are unchanged.
    """

    # Settling PERIODS marched and discarded before the reported window: 10 for
    # the sinusoidal voltage drive (every pinned number was produced with it),
    # 2 for PWM, 0 for an imposed-current source (its currents are not state).
    periods_static: int = 0
    # Adaptive settle: periods = clamp(ceil(mult·τ_e/T_e), periods_static,
    # periods_cap) with τ_e = max(Ld, Lq)/R from the phasor initialiser.
    # None = not adaptive (and an explicit SB_V_SETTLE_PERIODS forces None).
    adaptive_tau_mult: Optional[float] = None
    periods_cap: int = _SETTLE_MAX
    # Mixed coarse/fine settle allowed (PWM only): march the settle on the
    # sinusoid fundamental at a coarse step count, then switch the real
    # modulator on for a short fine pre-roll + the reported window.
    coarse_settle: bool = False
    # SB_PWM_COARSE_SETTLE=1 forces the mixed scheme; unset leaves the
    # steps-per-carrier AUTO rule to the solver (it needs the run's step count).
    coarse_settle_forced: bool = False
    # WHOLE fine settling periods at the END of a mixed coarse/fine settle.
    # The modulator turning on injects a DC of about half the ripple amplitude
    # (the settle hands over a ripple-FREE orbit), and on a τ_e ≈ 27-period
    # machine nothing decays — it has to be MEASURED and removed, and a mean is
    # only a measurement over a whole electrical period.  Two: the first
    # anchors the turn-on DC, the second the ~10 % the operating-point
    # inductance columns got wrong.  (B5 / PWM study 2026-09-13.)
    fine_settle_periods: int = 0
    # Δ² period-boundary flux anchors during the settle (voltage sources only:
    # they anchor circuit STATE, which an imposed-current run does not have).
    aitken: bool = False
    # PERIOD-MEAN DC anchor at every whole settling period (voltage sources).
    # On a periodic orbit ∮dψ = 0, so R·⟨i⟩ = ⟨v⟩ over a whole electrical
    # period — and ⟨v⟩ is exactly 0 for both the sinusoid and the synchronous
    # regular-sampled PWM (measured: 0.0000 V on the L155 runs).  ⟨i⟩ over a
    # whole period IS therefore the DC error, with no interpolant to leak
    # ripple into it — which is what made the handover estimate over-correct.
    dc_anchor: bool = False


class ExcitationSource(Protocol):
    """What ``fem_transient_sliding_band`` needs from an excitation.

    Implement these five members and the solver will run your source — with the
    full field solve, saturation, the real back-EMF and the eddy reaction all in
    the loop — via ``fem_transient_sliding_band(..., excitation=my_source)``.
    See ``scripts/excitation_demo.py`` for a closed-loop example in 30 lines.
    """

    #: 'I' = the currents are imposed (a source term in the field problem);
    #: 'V' = the voltages are imposed and the currents are circuit UNKNOWNS
    #: solved with the field by a bordered Newton.
    kind: str
    #: Reported as ``drive`` / ``excitation.kind`` in the result payload.
    name: str

    def mean_over(self, fb: Feedback) -> Dict[str, float]:
        """MEAN of the applied quantity over the step, per phase 'A','B','C'.

        'V' sources: exactly the volt-seconds/Δt the Crank–Nicolson circuit
        integrates (V·Δt_k IS ∫v dt over the step, so the interval mean is the
        quantity that integral wants — a midpoint sample of a chopped waveform
        is aliasing).  'I' sources: the current imposed at ``fb.theta_deg``.
        """

    def fundamental(self, theta_deg: float) -> Dict[str, float]:
        """The SMOOTH fundamental at a rotor angle [mech deg].

        Used by the coarse settle frames and by the dq phasor initialiser,
        which solves a fundamental-frequency problem in which a chopped
        waveform has no meaning.
        """

    def settle_policy(self) -> SettlePolicy:
        """How long this source's circuit state takes to reach its orbit."""

    def describe(self, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """What the payload and the charts report about this source.

        ``ctx`` carries the run-dependent numbers a source cannot know on its
        own (f_elec, the reported window's period count, the step count, the
        settle composition, the solved DC-link series).  Called with no ctx it
        must still return the static description.
        """


# ═══════════════════════════════════════════════════════════════════════════
#  IMPOSED CURRENT
# ═══════════════════════════════════════════════════════════════════════════
class _CurrentSourceBase:
    """Shared plumbing of the three imposed-current sources.

    ``mean_over`` is the imposed value AT the step's angle rather than an
    interval mean, and that is not an approximation being papered over: the
    current is a CONSTRAINT the field solve satisfies at the frame's rotor
    position, not something integrated across the step.
    """

    kind = "I"
    name = "current"
    #: The run's rms is the SOLVED/IMPOSED series, not the ``I_phase_rms``
    #: argument — so the copper loss must be recomputed from it.
    rms_from_series = False
    #: Carrier periods per electrical period; 0 = this source has no carrier.
    carriers = 0

    def currents(self, theta_deg: float) -> Dict[str, float]:  # pragma: no cover
        raise NotImplementedError

    def mean_over(self, fb: Feedback) -> Dict[str, float]:
        return self.currents(fb.theta_deg)

    def fundamental(self, theta_deg: float) -> Dict[str, float]:
        return self.currents(theta_deg)

    def nominal_currents(self, theta_deg: float) -> Dict[str, float]:
        """Currents the MATERIAL assignment is built on (build_materials).

        The same waveform for an imposed-current source; the config sinusoid
        for a voltage one, whose currents are not known until they are solved.
        """
        return self.currents(theta_deg)

    def settle_policy(self) -> SettlePolicy:
        # Imposed-current runs have no circuit state, so nothing to settle.
        return SettlePolicy(periods_static=0)

    def on_steps_snapped(self, n_steps_per_period: int) -> "ExcitationSource":
        """Rebuilt copy for a source whose waveform depends on the time step.

        The step count is only final after the slip-node snap, so a source with
        a one-step-wide feature (the BLDC commutation ramp) cannot be finished
        before then.  Everyone else returns itself.
        """
        return self

    def describe(self, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"name": self.name, "series": "I",
                "quantity": "imposed phase current [A per branch]",
                "v_phase_peak_V": None, "v_delta_deg": None,
                "pwm": None, "custom_current": None, "bldc": None}


class SineCurrentSource(_CurrentSourceBase):
    """The default: imposed sinusoidal phase currents at ``I_phase_rms``/γ."""

    name = "current"

    def __init__(self, exc: Excitation):
        self._exc = exc

    def currents(self, theta_deg: float) -> Dict[str, float]:
        return self._exc.currents(theta_deg)


class CustomCurrentSource(_CurrentSourceBase):
    """An imposed ARBITRARY periodic phase-current waveform (sampled)."""

    name = "custom_current"
    rms_from_series = True

    def __init__(self, src: _SampledCurrent):
        self._src = src
        self._i1, self._g1 = src.fundamental()

    @property
    def sampled(self) -> _SampledCurrent:
        return self._src

    def currents(self, theta_deg: float) -> Dict[str, float]:
        return self._src.currents(theta_deg)

    def describe(self, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        d = super().describe(ctx)
        d["custom_current"] = {
            "n_samples": int(self._src._th.size),
            "I_phase_rms_A": round(self._src.rms_terminal(), 4),
            "I1_phase_rms_A": round(self._i1, 4),
            "gamma1_deg": round(self._g1, 3),
        }
        return d


class BldcCurrentSource(_CurrentSourceBase):
    """120° six-step block commutation, ramped over ONE time step.

    The ramp width is only known after the slip-node snap has decided what a
    time step is, so the source is finished in :meth:`on_steps_snapped`.
    """

    name = "bldc_current"
    rms_from_series = True

    def __init__(self, *, pole_pairs: int, daxis_deg: float, gamma_deg: float,
                 n_parallel: int, i_block: float,
                 src: Optional[BlockCurrentSource] = None):
        self._kw = dict(pole_pairs=int(pole_pairs), daxis_deg=float(daxis_deg),
                        gamma_deg=float(gamma_deg),
                        n_parallel=int(n_parallel), i_block=float(i_block))
        self._src = src
        self._i1 = self._g1 = 0.0
        if src is not None:
            self._i1, self._g1 = src.fundamental()

    @property
    def block(self) -> Optional[BlockCurrentSource]:
        return self._src

    @property
    def ramp_deg(self) -> float:
        return float(self._src.ramp_deg) if self._src is not None else 0.0

    def on_steps_snapped(self, n_steps_per_period: int) -> "BldcCurrentSource":
        return BldcCurrentSource(
            src=BlockCurrentSource(
                ramp_deg=360.0 / max(1, int(n_steps_per_period)), **self._kw),
            **self._kw)

    def currents(self, theta_deg: float) -> Dict[str, float]:
        if self._src is None:
            raise ExcitationError(
                "the BLDC block source is not finished: its commutation ramp "
                "is one TIME STEP wide, so on_steps_snapped() must run after "
                "the step count is final")
        return self._src.currents(theta_deg)

    def describe(self, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        d = super().describe(ctx)
        if self._src is None:
            return d
        d["bldc"] = {
            "i_block_A": float(self._kw["i_block"]),
            "I_phase_rms_A": round(self._src.rms_terminal(), 4),
            "I1_phase_rms_A": round(self._i1, 4),
            "gamma1_deg": round(self._g1, 3),
            "commutation_ramp_deg": round(float(self._src.ramp_deg), 4),
            "note": ("ideal 120° block ramped over one time step at each "
                     "commutation; the three phases still sum to zero "
                     "throughout, and a finer step gives a sharper edge"),
        }
        return d


# ═══════════════════════════════════════════════════════════════════════════
#  IMPOSED VOLTAGE
# ═══════════════════════════════════════════════════════════════════════════
class _VoltageSourceBase:
    """Shared plumbing of the imposed-voltage sources."""

    kind = "V"
    name = "voltage"
    rms_from_series = True
    carriers = 0
    v_bus: Optional[float] = None

    def __init__(self, exc: Excitation):
        # The CONFIG sinusoid, kept for one job only: build_materials sizes the
        # slot current density before anything is solved, and under an imposed
        # voltage the real current is the answer, not the input.
        self._exc = exc

    def nominal_currents(self, theta_deg: float) -> Dict[str, float]:
        return self._exc.currents(theta_deg)


class SineVoltageSource(_VoltageSourceBase):
    """Imposed sinusoidal phase voltage — the FOC-drive verification mode."""

    name = "voltage"

    def __init__(self, exc: Excitation, *, v_phase_peak: float,
                 v_delta_deg: float):
        super().__init__(exc)
        self.v_phase_peak = float(v_phase_peak)
        self.v_delta_deg = float(v_delta_deg)

    def fundamental(self, theta_deg: float) -> Dict[str, float]:
        return self._exc.voltages(theta_deg)

    def mean_over(self, fb: Feedback) -> Dict[str, float]:
        # The midpoint sample — that is the Crank–Nicolson rule, and every
        # pinned voltage-drive number was produced by it.
        return self._exc.voltages(0.5 * (fb.theta_deg + fb.theta_prev_deg))

    def settle_policy(self) -> SettlePolicy:
        # TEN periods, never adaptive: the sinusoid's Aitken anchoring already
        # handles a long τ and every regression number was produced with 10.
        return SettlePolicy(periods_static=_settle_periods(10), aitken=True)

    def on_steps_snapped(self, n_steps_per_period: int) -> "SineVoltageSource":
        return self

    def describe(self, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"name": self.name, "series": "V",
                "quantity": "applied phase voltage [V]",
                "v_phase_peak_V": float(self.v_phase_peak),
                "v_delta_deg": float(self.v_delta_deg),
                "pwm": None, "custom_current": None, "bldc": None}


class PwmVoltageSource(_VoltageSourceBase):
    """An ideal two-level inverter's chopped pole voltages.

    A thin adapter over ``pwm.PwmVoltageSource`` (kept as ``PwmModulator``
    here): the modulator maths — regular sampling, the exact per-interval
    volt-second means, the delay/gain compensation, the DC-link switch
    function — has exactly one home and this is not it.
    """

    name = "pwm_voltage"

    def __init__(self, exc: Excitation, modulator: PwmModulator, *,
                 v_phase_peak: float, v_delta_deg: float,
                 f_switch_requested_hz: float):
        super().__init__(exc)
        self._mod = modulator
        # The REQUESTED fundamental, which is what v_phase_peak / v_delta_deg
        # mean on the sinusoidal voltage drive too (the modulator's reference
        # was solved so that this is what comes out).  The modulator's own
        # v_delta_deg is the COMPENSATED reference and is reported separately.
        self.v_phase_peak = float(v_phase_peak)
        self.v_delta_deg = float(v_delta_deg)
        self.v_bus = float(modulator.v_bus)
        self.carriers = int(modulator.carriers)
        self.f_switch_requested_hz = float(f_switch_requested_hz)
        self.v1_applied = modulator.applied_fundamental()

    @property
    def modulator(self) -> PwmModulator:
        return self._mod

    def f_switch_eff_hz(self, f_elec_hz: float) -> float:
        return self._mod.f_switch_eff_hz(f_elec_hz)

    def fundamental(self, theta_deg: float) -> Dict[str, float]:
        return self._mod.fundamental(theta_deg)

    def mean_over(self, fb: Feedback) -> Dict[str, float]:
        if fb.fine:
            # The EXACT mean pole voltage over the step's rotor motion.
            return self._mod.mean_voltages(fb.theta_prev_deg, fb.theta_deg)
        # Coarse settle frames march the plain sinusoid FUNDAMENTAL — the very
        # amplitude and angle the modulator is compensated to apply — so the
        # coarse step is only ever asked to resolve a sinusoid.
        return self._mod.fundamental(0.5 * (fb.theta_deg + fb.theta_prev_deg))

    def settle_policy(self) -> SettlePolicy:
        return SettlePolicy(
            periods_static=_settle_periods(2),
            # An EXPLICIT settle count outranks the adaptive rule entirely.
            adaptive_tau_mult=(None if _V_SETTLE_ENV else _SETTLE_TAU_MULT),
            periods_cap=_SETTLE_MAX,
            coarse_settle=(_PWM_COARSE_SETTLE_ENV != "0"),
            coarse_settle_forced=(_PWM_COARSE_SETTLE_ENV == "1"),
            fine_settle_periods=_PWM_FINE_SETTLE,
            # The Δ² FLUX anchor is off on PWM since B5: it extrapolates the
            # period-boundary flux, and on a rippled orbit the sample it
            # extrapolates from carries the carrier.  The DC anchor below
            # measures the same DC directly and exactly.  (It never fired here
            # anyway — v_anchor_applied 0 of 3 on the L155 diagnostics.)  The
            # sinusoidal drive keeps it: every pinned number was made with it.
            aitken=False,
            dc_anchor=True)

    def on_steps_snapped(self, n_steps_per_period: int) -> "PwmVoltageSource":
        return self

    def dc_link_series(self, *, rotor_angle_deg: Sequence[float],
                       i_a: Sequence[float], i_b: Sequence[float],
                       i_c: Sequence[float], n_parallel: int = 1):
        """Bus-side current of the SAME bridge, from the SAME comparator."""
        return _dc_link_series(self._mod, rotor_angle_deg=rotor_angle_deg,
                               i_a=i_a, i_b=i_b, i_c=i_c,
                               n_parallel=n_parallel)

    def describe(self, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        c = dict(ctx or {})
        src = self._mod
        f_elec = float(c.get("f_elec", 0.0))
        n_periods = float(c.get("n_periods", 1.0))
        nspp = float(c.get("n_steps_per_period", 0.0))
        pwm: Dict[str, Any] = {
            "v_bus_V": float(src.v_bus),
            "modulation_index": round(float(src.m), 4),
            # Sampled-reference delay/gain compensation (simulation/pwm.py):
            # what the modulator was HANDED so that what it APPLIES is the
            # requested v_phase_peak / v_delta_deg.
            "reference_delta_deg": round(float(src.v_delta_deg), 3),
            "v1_applied_V": round(float(self.v1_applied[0]), 4),
            "v1_applied_delta_deg": round(float(self.v1_applied[1]), 3),
            "f_switch_requested_Hz": float(self.f_switch_requested_hz),
            "f_switch_eff_Hz": round(src.f_switch_eff_hz(f_elec), 2),
            "carriers_per_period": int(src.carriers),
            "steps_per_switching_period": round(
                float(nspp) / float(src.carriers), 2),
            "modulator": ("ideal two-level, synchronous regular-sampled "
                          "centre-aligned sine-triangle; no dead time, no "
                          "device drops, ideal bus"),
            # MIXED-RESOLUTION SETTLING — present only when it was used.
            "mixed_settle": ({
                "composition": c.get("progress_comp"),
                "coarse_steps_per_period": int(c.get("c_nspp", 0)),
                # WHOLE fine settling periods at the end of the prefix, and the
                # period-mean DC anchors that fired on them — the pair that
                # replaced the two-carrier pre-roll (B5 / PWM study
                # 2026-09-13); v_dc_residual_A is what they achieved.
                "fine_settle_periods": int(c.get("fine_settle_periods", 0)),
                "fine_settle_frames": int(c.get("fine_frames", 0)),
                "dc_anchors_applied": int(c.get("dc_anchors", 0)),
                "disable_with": "SB_PWM_COARSE_SETTLE=0",
            } if c.get("sched_mixed") else None),
        }
        if f_elec > 0.0:
            # OSCILLOSCOPE VIEWS — the real pulse train (per leg) and the LINE
            # voltage actually across the motor, as EDGES rather than samples.
            # They need the run's electrical frequency and reported window, so
            # a context-free describe() — the solver's set-up call, which only
            # wants the parameters — leaves them out rather than drawing a
            # window that does not exist.
            pwm["wave_A"] = src.edge_waveform(f_elec, n_periods)
            pwm["wave_AB"] = src.edge_waveform_ll(f_elec, n_periods)
        # DC-LINK SIDE: Σ_phase s_phase·i_phase per solved step — what the
        # bridge draws from (or, under generation, pushes into) the bus, with
        # the switch states taken from this very modulator.
        pwm["dc_link"] = c.get("dc_link")
        return {"name": self.name, "series": "V",
                "quantity": ("inverter pole voltage [V], relative to the "
                             "DC-link mid-point (the common mode falls on the "
                             "floating neutral and drives no current)"),
                "v_phase_peak_V": float(self.v_phase_peak),
                "v_delta_deg": float(self.v_delta_deg),
                "pwm": pwm, "custom_current": None, "bldc": None}


# ═══════════════════════════════════════════════════════════════════════════
#  FACTORY
# ═══════════════════════════════════════════════════════════════════════════
def make_source(drive: str, *, pole_pairs: int, daxis_deg: float,
                I_phase_rms: float = 0.0, gamma_deg: float = 0.0,
                n_parallel: int = 1, v_phase_peak: float = 0.0,
                v_delta_deg: float = 0.0, v_bus: float = 0.0,
                f_switch: float = 0.0, f_elec: float = 0.0,
                waveform: Any = None, i_block: float = 0.0
                ) -> ExcitationSource:
    """Build the source ``drive=`` names, from the solver's own keywords.

    The names are matched EXACTLY, not by prefix: a typo used to fall through
    to the current drive and report a sinusoidal answer to a question about an
    inverter.
    """
    name = str(drive or "current").strip().lower()
    if name not in DRIVES:
        raise ValueError(
            "unknown excitation source %r — expected one of %s"
            % (drive, ", ".join(DRIVES)))
    n_parallel = max(1, int(n_parallel))
    exc = Excitation(pole_pairs=int(pole_pairs), daxis_deg=float(daxis_deg),
                     i_peak=float(I_phase_rms) / n_parallel * math.sqrt(2),
                     gamma_deg=gamma_deg,
                     v_peak=float(v_phase_peak), v_delta_deg=v_delta_deg)
    if name == "current":
        return SineCurrentSource(exc)
    if name == "voltage":
        return SineVoltageSource(exc, v_phase_peak=float(v_phase_peak),
                                 v_delta_deg=float(v_delta_deg))
    if name == "pwm_voltage":
        mod = build_pwm_source(
            pole_pairs=int(pole_pairs), daxis_deg=float(daxis_deg),
            v_phase_peak=float(v_phase_peak), v_delta_deg=float(v_delta_deg),
            v_bus=float(v_bus), f_switch_hz=float(f_switch),
            f_elec_hz=float(f_elec))
        return PwmVoltageSource(exc, mod, v_phase_peak=float(v_phase_peak),
                                v_delta_deg=float(v_delta_deg),
                                f_switch_requested_hz=float(f_switch))
    if name == "custom_current":
        return CustomCurrentSource(_SampledCurrent.build(
            parse_waveform(waveform), pole_pairs=int(pole_pairs),
            daxis_deg=float(daxis_deg), gamma_deg=gamma_deg,
            n_parallel=n_parallel))
    # bldc_current.  Only the amplitude can be checked here: the commutation
    # ramp is one TIME STEP wide and the step count is not final until the
    # slip-node snap, so the block itself is built by on_steps_snapped().
    if not (float(i_block) > 0.0):
        raise ExcitationError(
            "drive='bldc_current' needs the flat-top block amplitude "
            "i_block [A]; got %r.  It is NOT an rms: a 120° block of "
            "amplitude I has rms I·√(2/3), so matching a sinusoidal run at "
            "I_rms means i_block = I_rms·√(3/2)." % (i_block,))
    return BldcCurrentSource(
        pole_pairs=int(pole_pairs), daxis_deg=float(daxis_deg),
        gamma_deg=float(gamma_deg), n_parallel=n_parallel,
        i_block=float(i_block))
