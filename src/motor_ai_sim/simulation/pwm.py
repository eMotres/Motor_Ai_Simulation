"""Excitation SOURCES beyond the ideal sinusoid: a two-level PWM inverter and
an arbitrary sampled current waveform.

``simulation/drive.py`` holds the ideal excitation — one sinusoid in current,
one in voltage — because for design work that is the right question.  It is not
the question a real drive answers.  A two-level inverter applies a CHOPPED pole
voltage, and on a low-inductance machine (the CIANO14 40 has Lq ≈ 0.006 mH) the
switching-frequency current ripple that rides on the sinusoidal reference is
large enough to move torque ripple, copper loss and core loss by amounts a
sinusoidal simulation simply does not contain.

Two routes to that number live here, and they answer different questions:

* :class:`PwmVoltageSource` — the honest one.  The inverter's pole voltages are
  imposed and the CURRENTS are the machine's own response, solved by the same
  line-to-line circuit Newton the sinusoidal voltage drive uses, so saturation,
  the real (non-sinusoidal) back-EMF and the eddy reaction are all in the loop.
  Nothing about the current waveform is assumed.
* :class:`CustomCurrentSource` + :func:`synthesize_pwm_current` — the cheap one,
  and the one the published PWM-excitation studies use: synthesise the phase
  current in a CONSTANT-L circuit model, then impose it.  A second instead
  of an hour, at the cost of a current waveform that came from a linear model of
  the machine rather than from the machine.  Kept because it is the route those
  studies are comparable with, and because an imposed waveform can come from a
  measurement or from another tool.

Nothing here touches the mesh or the field: it is algebra on three phase
quantities, exactly like ``drive.py``, and can be exercised without a FEM solve.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# The largest sampled waveform the imposed-current source will accept.  A phase
# current at 48 kHz over a 1.5 kHz electrical period needs ~32 carriers x 32
# points = ~1k samples; 20k is two decades of headroom and still a payload that
# fits in a query string / a JSON body without thinking about it.
MAX_WAVEFORM_POINTS = 20000

# Largest angular hole allowed between consecutive samples of an imposed
# waveform, INCLUDING the wrap from the last sample back to the first.  This is
# what catches the common mistake: pasting half a period, or a waveform whose
# theta column is in radians, both of which leave a gap far wider than this.
MAX_WAVEFORM_GAP_DEG = 30.0

# Sine-triangle modulation index ceiling.  m = 2*V_phase_peak/V_bus; m = 1 is the
# linear limit of plain sine-triangle and m = 2/sqrt(3) = 1.1547 is the linear
# limit WITH the zero-sequence injection every real inverter uses.  Above that a
# two-level inverter is in genuine overmodulation (pulse dropping, then
# six-step), which changes the harmonic problem into a different one and is out
# of scope for this source.
MAX_MODULATION_INDEX = 1.15

# ── THE MODULATION, selectable (Controller settings, 2026-09-26) ──────────────
# ``sine``            plain sine-triangle, each leg on its own reference — the
#                     default, byte-for-byte the modulator every record so far
#                     was made with.  Linear up to m = 1; between 1 and the
#                     1.15 ceiling above its duty clamps (real pulse dropping,
#                     measured by ``applied_fundamental`` and compensated).
# ``svpwm``           the same comparator with the MIN-MAX zero sequence added to
#                     all three references at the SAME sample,
#                     v0 = -(max + min)/2 — the carrier-based form of centred
#                     space-vector PWM (equal zero-vector split).
# ``third_harmonic``  v0 = -(m/6)*cos(3x) — the classic 1/6 third-harmonic
#                     injection.
# Both injections are ZERO SEQUENCE: the same number added to every leg at the
# same instant, so every line-to-line voltage is exactly the sine modulator's,
# and a star machine's floating neutral (or, in delta, the line-to-line
# difference the branch sees) removes it.  What they buy is range: the largest
# reference now peaks at (sqrt(3)/2)*m, so the leg stays inside its [0, 1] duty
# up to m = 2/sqrt(3) — 15.5 % more fundamental on the same link.
PWM_MODULATIONS = ("sine", "svpwm", "third_harmonic")
#: Linear limit of the zero-sequence-injected modulators, m = 2/sqrt(3).
SVPWM_LINEAR_LIMIT = 2.0 / math.sqrt(3.0)


def normalize_modulation(modulation: Optional[str]) -> str:
    """``None``/blank -> ``"sine"``; anything not in :data:`PWM_MODULATIONS`
    is refused by name (never read as the default)."""
    m = str(modulation if modulation is not None else "sine").strip().lower()
    if not m:
        m = "sine"
    if m not in PWM_MODULATIONS:
        raise ExcitationError(
            "pwm modulation must be one of %s; got %r"
            % (", ".join(PWM_MODULATIONS), modulation))
    return m


def modulation_ceiling(modulation: Optional[str] = "sine") -> float:
    """The modulation-index ceiling a source of this modulation is built up to:
    :data:`MAX_MODULATION_INDEX` for sine (unchanged), 2/sqrt(3) for the two
    zero-sequence-injected modulators (their exact linear limit)."""
    return (MAX_MODULATION_INDEX if normalize_modulation(modulation) == "sine"
            else SVPWM_LINEAR_LIMIT)


def zero_sequence(refs: Sequence[float], modulation: str, *,
                  m: float = 0.0, x_deg: float = 0.0) -> float:
    """The zero-sequence term added to every leg's reference at one sample.

    ``refs`` are the three plain references at that sample; ``x_deg`` is phase
    A's reference angle (cos form), used by ``third_harmonic`` — cos(3x) is the
    same for all three phases because their shifts are multiples of 120 deg.
    """
    if modulation == "svpwm":
        return -0.5 * (max(refs) + min(refs))
    if modulation == "third_harmonic":
        return -(float(m) / 6.0) * math.cos(3.0 * math.radians(x_deg))
    return 0.0


class ExcitationError(ValueError):
    """A source the caller asked for cannot be built as described.

    Carries an engineer-readable message: what was given, what it must satisfy,
    and (where there is one) the number to change.  Routes turn it into a 422.
    """


# ═══════════════════════════════════════════════════════════════════════════
#  DELTA ON THE STAR CIRCUIT — the exact equivalent, and the honest m
# ═══════════════════════════════════════════════════════════════════════════
SQRT3 = math.sqrt(3.0)


def is_delta(star_delta: Optional[str]) -> bool:
    """True for the delta terminal connection, on the project's own spelling."""
    return str(star_delta or "star").strip().lower().startswith("d")


def star_equivalent_bus(v_bus_real: float, star_delta: Optional[str] = "star"
                        ) -> float:
    """The bus the STAR circuit must be given to reproduce a DELTA machine on a
    real DC link of ``v_bus_real``.

    WHY (user 2026-09-14 / PWM study §0.3).  ``drive.circuit_residual_ll`` is
    the isolated-neutral STAR circuit: it integrates DIFFERENCES of the applied
    phase voltages, so a branch of that model sees ``pole − common mode``.  A
    DELTA branch sees the inverter's LINE-TO-LINE waveform.  Both legs run the
    SAME comparator (m = 2·V₁/(√3·V_dc) either way), so with s the switch state:

        real   v_AB    = (V_dc/2)·(s_A − s_B)
        model  v_A−CM  = (√3·V_dc/2)·(s_A − ⅓Σs)

    and for a balanced set the harmonic h of (s_A − s_B) is (1 − e^{−jh120°})·S_h
    — magnitude √3·|S_h|, zero on the triplens — against the model's |S_h|·√3·…
    the same magnitude, also zero on the triplens.  So EVERY HARMONIC MAGNITUDE
    matches, hence the rms, the ripple spectrum and every loss built on Σ|I_h|²R.

    TWO THINGS IT IS NOT, and both are measured
    (``tests/test_pwm_delta_star_equivalent.py``):

    * the magnitudes match harmonic-by-harmonic only when the carrier count per
      electrical period is a MULTIPLE OF 3 (the three legs are then true
      time-shifted copies on the shared carrier).  Otherwise the sidebands
      redistribute — a single order can differ by more than 100 % — while the
      AGGREGATE ripple rms still matches within 0.4 % (0.03-0.07 % at the study's
      34.26° load angle, 0.19-0.37 % at 35.73°).  Loss goes as ripple squared,
      so ≲ 0.8 % of the carrier's added watts;
    * the per-harmonic PHASES carry the star-delta rotation, so the two are NOT
      the same waveform in time: the model branch peaks at ⅔·V_model =
      1.1547·V_dc where the real bridge's line voltage peaks at V_dc.  Anything
      built on the INSTANTANEOUS peak of the solved voltage is therefore the
      model's, not the bridge's — the route says so in the result.

    So a delta machine is run on the star circuit at ``v_phase_peak = V₁`` of its
    branch (= the line voltage) and this bus; the terminals are then mapped back
    by the connection's own rules (I_line = √3·I_branch, V_LL = V_branch minus
    its zero sequence).

    Star: a no-op, byte for byte.
    """
    return float(v_bus_real) * (SQRT3 if is_delta(star_delta) else 1.0)


def modulation_index(v_phase_peak: float, v_bus_real: float, *,
                     star_delta: Optional[str] = "star") -> float:
    """The REAL bridge's modulation index m = 2·V_phase/V_dc.

    ``v_phase_peak`` is what the drive is asked for — in star the phase voltage,
    in DELTA the BRANCH voltage, which IS the line-to-line voltage.  The
    modulator's own quantity is a POLE voltage referred to the DC mid-point, i.e.
    the per-phase (star-equivalent) one, so in delta the fundamental that faces
    the bus is V₁/√3 (user 2026-09-14 / PWM study §0.2: fed the branch voltage,
    the gate read 1.53 where the real bridge sits at 0.88 and refused a point
    the inverter synthesises comfortably).

    Equivalently — and this is why the substitution above needs no second gate —
    2·V₁/(√3·V_dc) is the branch fundamental against the star-equivalent bus.
    """
    vb = float(v_bus_real)
    if not (vb > 0.0):
        return float("inf")
    return 2.0 * float(v_phase_peak) / (vb * (SQRT3 if is_delta(star_delta)
                                              else 1.0))


# ═══════════════════════════════════════════════════════════════════════════
#  PWM VOLTAGE SOURCE — ideal two-level inverter, synchronous sine-triangle
# ═══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class PwmVoltageSource:
    """Pole voltages of an ideal two-level inverter, as a function of the ROTOR
    angle, in the same electrical frame as :class:`drive.Excitation`.

    MODULATOR.  Sine-triangle, centre-aligned, ASYMMETRIC regular sampling: the
    pulse sits in the middle of its carrier period, its rising edge placed from
    a reference sample taken at the carrier boundary and its falling edge from a
    second sample taken half a carrier later — i.e. what a DSP with an up/down
    counter and shadow-register updates at both underflow and overflow actually
    produces.  Each phase leg outputs +v_bus/2 or -v_bus/2; the machine's neutral
    floats, so the common mode (which for two-level PWM is large) drops out of
    the line-to-line circuit the solver actually integrates.  No dead time, no
    device drops, no bus ripple: an IDEAL inverter, so what the run measures is
    the switching ripple itself and not a model of a particular power stage.

    SAMPLED-REFERENCE GAIN.  Holding a sampled reference across half a carrier
    costs the fundamental a factor sinc(pi/(2*N_c)) — negligible at N_c = 32
    (-0.04 %), 0.3 % at N_c = 11, and 4.5 % at N_c = 3, where three pulses have
    to carry a whole fundamental.  That is a real property of a regular-sampled
    modulator, not a discretisation error, and it does NOT go away with finer FEM
    time steps.  It is measured (:meth:`applied_fundamental`) and reported beside
    the requested amplitude, because at a low pulse ratio a torque comparison
    that does not account for it is comparing two different operating points.
    (Symmetric single-sample regular sampling costs twice as much: sinc(pi/N_c),
    i.e. 17 % at N_c = 3.  Hence the asymmetric scheme.)

    SYNCHRONOUS, and this is not cosmetic.  The transient reports ONE electrical
    period and every consumer of it (the FFT, the ripple metric, the loss cycle
    means) assumes that period repeats.  An asynchronous carrier makes each
    electrical period different from the last, so the reported period would be
    one arbitrary sample of a beat pattern.  The requested ``f_switch`` is
    therefore snapped to the nearest whole number of carriers per electrical
    period and the EFFECTIVE frequency is reported (``f_switch_eff_hz``) — the
    substitution is stated, never silent.

    VOLTAGE SAMPLING.  ``mean_voltages`` returns the EXACT mean pole voltage over
    a rotor-angle interval, integrated analytically across the switching edges
    inside it, because the Crank-Nicolson circuit residual multiplies V by the
    step: V*dt IS the integral of v dt over the step, so the interval mean is the
    right quantity, not a midpoint sample.  A midpoint sample of a square wave is
    +-v_bus/2 at random — pure aliasing — and the solver would integrate noise.
    With the exact mean, a coarse time step under-RESOLVES the ripple (it
    averages toward the sinusoid) instead of manufacturing a fake one; that is
    the honest failure direction, and the caller is warned about it separately
    (steps per switching period).
    """

    pole_pairs: int
    daxis_deg: float          # per-topology d-axis offset, shared with drive.py
    v_delta_deg: float        # REFERENCE angle handed to the modulator [deg el]
    v_bus: float              # DC link voltage [V]
    carriers: int             # carrier periods per ELECTRICAL period (>= 1)
    m: float                  # modulation index of the REFERENCE = 2*Vref/v_bus
    # What the inverter actually puts on the terminals at the fundamental — the
    # quantity that is directly comparable with the sinusoidal voltage drive's
    # v_phase_peak / v_delta_deg, and the one the phasor initialiser needs.  On a
    # source built by `build_pwm_source` these are the REQUESTED values (the
    # reference above was solved so that they come out); left at 0 they fall
    # back to the reference, which is what a directly-constructed (uncompensated)
    # source wants.
    v1_peak: float = 0.0
    v1_delta_deg: float = 0.0
    # "sine" | "svpwm" | "third_harmonic" (see PWM_MODULATIONS).  A zero
    # sequence only: the line-to-line voltages it produces are the sine
    # modulator's, the range is not.
    modulation: str = "sine"

    # Phase shifts of the three references, in the order the abc quantities are
    # written everywhere else in this package (A, B-120, C+120).
    _SHIFT = (0.0, -120.0, 120.0)

    # ── geometry of the electrical frame ─────────────────────────────────
    def _psi_e(self, rotor_angle_deg: float) -> float:
        """Rotor angle -> the electrical angle the CARRIER is locked to.

        The d-axis offset deliberately does NOT enter here: the carrier is
        locked to the rotor, and where the machine's d-axis happens to sit is a
        property of the winding topology, not of the inverter's timebase.  It
        does enter the modulating reference below, exactly as it does for the
        sinusoidal drive, so the fundamental of this source sits at the same
        angle a plain voltage drive at the same v_delta_deg would.
        """
        return float(rotor_angle_deg) * self.pole_pairs

    def _duty_at(self, psi_sample_deg: float, shift_deg: float) -> float:
        """Duty the modulator would command from a reference sample taken at
        this electrical angle.  The clamp to [0, 1] is the leg's physical
        pulse-dropping limit.  For sine it bites at 1 < m <= 1.15, which the
        factory accepts: that is real pulse dropping, measured by
        :meth:`applied_fundamental` and compensated.  For svpwm and
        third_harmonic it never bites below their 2/sqrt(3) ceiling.

        With a zero-sequence modulation the term is computed from ALL THREE
        references at the same sample instant and added to this leg's — so it
        is common to the three legs of every carrier period and cancels from
        every line-to-line voltage exactly, pulse by pulse.
        """
        x = psi_sample_deg + self.v_delta_deg + self.daxis_deg
        ref = self.m * math.cos(math.radians(x + shift_deg))
        if self.modulation != "sine":
            refs = [self.m * math.cos(math.radians(x + s)) for s in self._SHIFT]
            ref += zero_sequence(refs, self.modulation, m=self.m, x_deg=x)
        return min(1.0, max(0.0, 0.5 * (1.0 + ref)))

    def _pulse(self, carrier_index: int, shift_deg: float
               ) -> Tuple[float, float]:
        """(rising edge, falling edge) of one leg's pulse, in electrical degrees.

        Asymmetric regular sampling: the leading half of the pulse is set by the
        sample at the carrier boundary, the trailing half by the sample half a
        carrier later.  Both duties are in [0, 1], so the pulse can never leave
        its own carrier period — which is what makes the interval integral below
        a per-carrier sum with no cross-boundary bookkeeping.
        """
        d_ang = 360.0 / self.carriers
        j0 = carrier_index * d_ang
        centre = j0 + 0.5 * d_ang
        d_lead = self._duty_at(j0, shift_deg)
        d_trail = self._duty_at(centre, shift_deg)
        return (centre - 0.5 * d_lead * d_ang,
                centre + 0.5 * d_trail * d_ang)

    def _pole(self, psi_e_deg: float, shift_deg: float) -> float:
        """Instantaneous pole voltage [V] at an electrical angle."""
        d_ang = 360.0 / self.carriers
        j = math.floor(psi_e_deg / d_ang)
        lo, hi = self._pulse(j, shift_deg)
        return (0.5 * self.v_bus if lo <= psi_e_deg < hi
                else -0.5 * self.v_bus)

    def _pole_mean(self, psi_a: float, psi_b: float, shift_deg: float) -> float:
        """Exact mean pole voltage over [psi_a, psi_b] electrical degrees."""
        if psi_b <= psi_a:
            return self._pole(psi_a, shift_deg)
        d_ang = 360.0 / self.carriers
        j0 = math.floor(psi_a / d_ang)
        j1 = math.floor((psi_b - 1e-12) / d_ang)
        acc = 0.0
        for j in range(j0, j1 + 1):
            a = max(psi_a, j * d_ang)
            b = min(psi_b, (j + 1) * d_ang)
            if b <= a:
                continue
            on_lo, on_hi = self._pulse(j, shift_deg)
            on = max(0.0, min(b, on_hi) - max(a, on_lo))
            # +v/2 for `on`, -v/2 for the rest of the overlap
            acc += 0.5 * self.v_bus * (2.0 * on - (b - a))
        return acc / (psi_b - psi_a)

    # ── the three interfaces the solver uses ─────────────────────────────
    def voltages(self, rotor_angle_deg: float) -> Dict[str, float]:
        """Instantaneous pole voltages — diagnostics and plotting only.

        The frame loop uses :meth:`mean_voltages`; this exists so a caller can
        draw the chopped waveform, and so the source satisfies the same shape as
        ``drive.Excitation.voltages``.
        """
        psi = self._psi_e(rotor_angle_deg)
        return {k: self._pole(psi, s)
                for k, s in zip(('A', 'B', 'C'), self._SHIFT)}

    def mean_voltages(self, rotor_a_deg: float,
                      rotor_b_deg: float) -> Dict[str, float]:
        """Exact mean pole voltage over the rotor motion of ONE time step."""
        pa = self._psi_e(rotor_a_deg)
        pb = self._psi_e(rotor_b_deg)
        return {k: self._pole_mean(pa, pb, s)
                for k, s in zip(('A', 'B', 'C'), self._SHIFT)}

    def fundamental(self, rotor_angle_deg: float) -> Dict[str, float]:
        """The MODULATING sinusoid — identical to ``drive.Excitation.voltages``
        at the same ``v_phase_peak`` / ``v_delta_deg``.

        The steady-state phasor initialiser must see this, not the chopped
        waveform: it solves a fundamental-frequency dq phasor problem to place
        the currents on their periodic orbit, and a square wave has no meaning
        in it.  The switching content is a perturbation ON that orbit, which is
        exactly what the marched settling periods then resolve.
        """
        vpk = self.v_phase_peak
        te = math.radians(self._psi_e(rotor_angle_deg)
                          + (self.v1_delta_deg if self.v1_peak > 0.0
                             else self.v_delta_deg) + self.daxis_deg)
        return {'A': vpk * math.cos(te),
                'B': vpk * math.cos(te - 2 * math.pi / 3),
                'C': vpk * math.cos(te + 2 * math.pi / 3)}

    # ── reporting ────────────────────────────────────────────────────────
    @property
    def v_phase_peak(self) -> float:
        """The fundamental phase voltage the inverter APPLIES [V peak]."""
        return (self.v1_peak if self.v1_peak > 0.0
                else 0.5 * self.v_bus * self.m)

    def f_switch_eff_hz(self, f_elec_hz: float) -> float:
        return float(self.carriers) * float(f_elec_hz)

    def edge_waveform(self, f_elec_hz: float, n_periods: float = 1.0
                      ) -> Dict:
        """Phase A's pole voltage as EXACT SWITCHING EDGES over the reported
        window — the oscilloscope view.

        Returns ``{t_s, v, duty, ...}`` where ``t_s[i]`` is the instant the leg
        switches TO ``v[i]`` (in {+v_bus/2, -v_bus/2}), i.e. a step-after
        encoding of the real pulse train, plus the per-carrier duty fraction.

        EDGES, NOT SAMPLES, and that is the whole design: two floats per
        transition, two transitions per carrier, so a 32-carrier period is ~64
        points — under a kilobyte, safe to persist and restore.  (A dense
        sampling fine enough to look rectangular would be tens of thousands of
        floats per run, which is the mistake that put 790 MB of ``frames`` into
        a result payload.)  The consumer expands it for drawing; the wire and
        the disk only ever see the edges.

        ANALYTIC, from the same comparator the solve integrates — same
        compensated reference, same asymmetric regular sampling, same
        ``_pulse``.  So this is not "the solver output sampled differently": it
        is the exact pulse train whose per-step volt-second means
        (:meth:`mean_voltages`) the circuit integrated, and the two agree to
        round-off by construction (verified in the study harness by integrating
        these edges over each solve step).

        The window is assumed to start at an electrical-period boundary, which
        the frame schedule guarantees (the fine pre-roll is carved out of the
        last settle period precisely so the reported window still starts at
        theta = 0).
        """
        d_ang = 360.0 / self.carriers
        n_rep = max(1, int(round(float(n_periods))))
        ts: List[float] = []
        vs: List[float] = []
        duty: List[float] = []
        hi_v, lo_v = 0.5 * self.v_bus, -0.5 * self.v_bus
        deg_to_s = 1.0 / (360.0 * max(float(f_elec_hz), 1e-9))
        for rep in range(n_rep):
            base = rep * 360.0
            for j in range(self.carriers):
                lo, hi = self._pulse(j, 0.0)
                if rep == 0:
                    duty.append(round((hi - lo) / d_ang, 6))
                if hi - lo <= 1e-12:
                    continue           # pulse dropped: the leg never rises
                # TWO transitions per carrier, and only two: the pulse is
                # strictly inside its carrier period (both duties are in
                # [0, 1]), so the leg is already OFF at the carrier boundary
                # and re-stating it there would be a third, redundant point.
                # The window's initial level is emitted once, below.
                ts.append((base + lo) * deg_to_s); vs.append(hi_v)
                ts.append((base + hi) * deg_to_s); vs.append(lo_v)
        if ts and ts[0] > 0.0:
            ts.insert(0, 0.0); vs.insert(0, lo_v)   # level at window start
        v1, v1_ang = self.applied_fundamental()
        return {
            # NOT rounded: the consumer integrates these against solve steps
            # of ~2e-6 s, and rounding t to 1e-12 s put a 5.6e-06 V error into
            # that comparison — three ulp of a float is free, a fake mismatch
            # in the self-check is not.
            "t_s": ts,
            "v": vs,
            "duty": duty,
            "n_edges": len(ts),
            "v_bus_V": float(self.v_bus),
            "carriers_per_period": int(self.carriers),
            "f_elec_Hz": round(float(f_elec_hz), 6),
            # Unrounded for the same reason as t_s: it is the upper bound
            # of the last segment in any per-step integration of these
            # edges, and 1e-12 s of slop there is 5e-06 V of fake error.
            "t_end_s": n_rep * 360.0 * deg_to_s,
            # The applied FUNDAMENTAL, for the dashed overlay: the modulating
            # sinusoid the duty cycle is tracking.  Phase includes the d-axis
            # offset, so the consumer needs nothing but t:
            #     v1(t) = v1_peak_V * cos(360*f_elec*t + v1_phase_deg)   [deg]
            "v1_peak_V": round(v1, 6),
            "v1_phase_deg": round((v1_ang + self.daxis_deg) % 360.0, 6),
            "note": ("analytic reconstruction from the modulator — the exact "
                     "pulse train whose per-step volt-second means the solve "
                     "integrated, not a solver output sampled coarser"),
        }

    def edge_waveform_ll(self, f_elec_hz: float, n_periods: float = 1.0
                         ) -> Dict:
        """V_AB = pole_A - pole_B at carrier resolution, as exact edges.

        THE VOLTAGE THAT IS ACROSS THE MOTOR.  A leg's pole voltage is measured
        against the DC-link mid-point, which is a construction, not a terminal:
        what the controller puts across two phases is the DIFFERENCE, and it
        swings the FULL bus.  A two-level inverter's LINE voltage is therefore
        THREE-level — {+v_bus, 0, -v_bus} — because the two legs switch at
        different instants inside the same carrier period and the pulse pattern
        spends most of its time with both legs on the same rail.

        Both pulses sit inside their shared carrier period (both duties are in
        [0, 1]), so the line voltage returns to 0 at every carrier boundary and
        each carrier contributes FOUR transitions: leg A's two edges and leg
        B's two.  Coincident edges (which cancel, both legs moving together)
        are dropped, so the count is 4 per carrier generically and less only
        where the modulator genuinely lines two edges up.

        Level evaluation is done at sub-interval MIDPOINTS through the same
        ``_pulse`` comparator the solve integrates — no separate model of the
        pattern, so degenerate and coincident cases resolve themselves instead
        of needing special cases.
        """
        d_ang = 360.0 / self.carriers
        n_rep = max(1, int(round(float(n_periods))))
        deg_to_s = 1.0 / (360.0 * max(float(f_elec_hz), 1e-9))
        ts: List[float] = []
        vs: List[float] = []
        duty_a: List[float] = []
        duty_b: List[float] = []
        last: Optional[float] = None
        for rep in range(n_rep):
            base = rep * 360.0
            for j in range(self.carriers):
                lo_a, hi_a = self._pulse(j, 0.0)
                lo_b, hi_b = self._pulse(j, -120.0)
                if rep == 0:
                    duty_a.append(round((hi_a - lo_a) / d_ang, 6))
                    duty_b.append(round((hi_b - lo_b) / d_ang, 6))
                marks = sorted({j * d_ang, lo_a, hi_a, lo_b, hi_b,
                                (j + 1) * d_ang})
                for _i in range(len(marks) - 1):
                    a0, a1 = marks[_i], marks[_i + 1]
                    if a1 - a0 <= 1e-12:
                        continue
                    mid = 0.5 * (a0 + a1)
                    lvl = self._pole(mid, 0.0) - self._pole(mid, -120.0)
                    if last is None or abs(lvl - last) > 1e-12:
                        ts.append((base + a0) * deg_to_s)
                        vs.append(lvl)
                        last = lvl
        if ts and ts[0] > 0.0:
            # Level in force at the window start (both legs on the low rail at
            # a carrier boundary, so the line voltage is 0).
            ts.insert(0, 0.0); vs.insert(0, 0.0)
        # LINE fundamental, DERIVED not asserted.  With
        #   v_A = V1·cos(x),  v_B = V1·cos(x − 120°)
        # the identity cosX − cosY = −2·sin((X+Y)/2)·sin((X−Y)/2) gives
        #   v_AB = −2·V1·sin(x − 60°)·sin(60°) = √3·V1·cos(x + 30°)
        # i.e. √3 in amplitude and +30° in phase.  The caller gets the numbers;
        # the study harness checks them against a projection of these very
        # edges, so the identity is verified rather than trusted.
        v1, v1_ang = self.applied_fundamental()
        return {
            "t_s": ts,
            "v": vs,
            "duty_A": duty_a,
            "duty_B": duty_b,
            "n_edges": len(ts),
            "v_bus_V": float(self.v_bus),
            "carriers_per_period": int(self.carriers),
            "f_elec_Hz": round(float(f_elec_hz), 6),
            "t_end_s": n_rep * 360.0 * deg_to_s,
            "levels_V": [float(self.v_bus), 0.0, -float(self.v_bus)],
            # v1_ll(t) = v1_ll_peak_V · cos(360·f_elec·t + v1_ll_phase_deg) [deg]
            "v1_ll_peak_V": round(math.sqrt(3.0) * v1, 6),
            "v1_ll_phase_deg": round((v1_ang + self.daxis_deg + 30.0) % 360.0, 6),
            "v1_phase_peak_V": round(v1, 6),
            "note": ("analytic reconstruction from the modulator — the exact "
                     "line-voltage pulse train whose per-step volt-second "
                     "means the solve integrated, not a solver output sampled "
                     "coarser"),
        }

    # ── DC-LINK SIDE: what the bridge draws from (or pushes into) the bus ──
    def _switched_integral(self, psi_a: float, psi_b: float, shift_deg: float,
                           i0: float, i1: float) -> float:
        """∫ s(ψ)·i(ψ) dψ over [psi_a, psi_b] electrical degrees, with s the
        leg's UPPER-switch state (1 while the pole sits on +v_bus/2) and i the
        phase current taken LINEAR between its solved end values i0, i1.

        Exact in s: the comparator instants are the same ``_pulse`` edges the
        volt-second means were built from, so no sub-sampling of a square wave
        happens here — the one approximation is the current between two solve
        steps, which is the same approximation every other per-step quantity in
        the run already makes, and it is second-order in the step.
        """
        span = psi_b - psi_a
        if span <= 0.0:
            return 0.0
        k = (i1 - i0) / span

        def _i(x: float) -> float:
            return i0 + k * (x - psi_a)

        d_ang = 360.0 / self.carriers
        j0 = math.floor(psi_a / d_ang)
        j1 = math.floor((psi_b - 1e-12) / d_ang)
        acc = 0.0
        for j in range(j0, j1 + 1):
            a = max(psi_a, j * d_ang)
            b = min(psi_b, (j + 1) * d_ang)
            if b <= a:
                continue
            on_lo, on_hi = self._pulse(j, shift_deg)
            u = max(a, on_lo)
            v = min(b, on_hi)
            if v > u:
                acc += 0.5 * (v - u) * (_i(u) + _i(v))
        return acc

    def dc_link_step_mean(self, rotor_a_deg: float, rotor_b_deg: float,
                          i_prev: Dict[str, float], i_now: Dict[str, float]
                          ) -> float:
        """MEAN DC-link current over one solve step [A], positive OUT of the
        source into the bridge.

            i_dc(t) = Σ_phase s_phase(t) · i_phase(t)

        with i_phase the TERMINAL current into the machine.  This is the
        definition, not a model of one: with pole voltages v_p = (s_p − ½)·V_bus
        and a floating neutral (Σ i_p = 0),

            Σ v_p·i_p = V_bus·Σ s_p·i_p − ½V_bus·Σ i_p = V_bus · i_dc

        identically at every instant, so V_bus·⟨i_dc⟩ IS the terminal power the
        solved circuit carries — the DC side and the AC side of an ideal bridge
        are the same statement written twice.  Under generation ⟨i_dc⟩ comes out
        NEGATIVE: the bridge is pushing charge back into the pack.

        IDEAL BRIDGE.  No dead time, no device conduction or switching loss, no
        DC-link ripple: those all subtract from what reaches the battery, so a
        real charger delivers LESS than this reports, never more.
        """
        pa = self._psi_e(rotor_a_deg)
        pb = self._psi_e(rotor_b_deg)
        if pb <= pa:
            return 0.0
        acc = 0.0
        for k, s in zip(('A', 'B', 'C'), self._SHIFT):
            acc += self._switched_integral(pa, pb, s,
                                           float(i_prev[k]), float(i_now[k]))
        return acc / (pb - pa)

    def applied_fundamental(self, n_per_carrier: int = 64
                            ) -> Tuple[float, float]:
        """(amplitude [V peak], angle [deg el]) of the fundamental the inverter
        ACTUALLY applies, measured off the exact per-interval volt-seconds.

        This is not the requested ``v_phase_peak``: a regular-sampled modulator
        holds each reference sample across half a carrier, which attenuates the
        fundamental by sinc(pi/(2*N_c)) and delays it by a quarter carrier.  At
        16 kHz on a 1.5 kHz fundamental that is 0.3 %; at 4 kHz it is 4.5 %, and
        4.5 % of voltage on a low-inductance machine is several percent of
        torque.  Reporting it is what lets a switching-frequency comparison say
        whether a torque difference is the ripple or the operating point.

        Cheap: ``carriers * n_per_carrier`` closed-form interval means, i.e.
        microseconds — no FEM, no sampling error beyond the projection grid.

        WITH A ZERO-SEQUENCE MODULATION it is phase A's DIFFERENTIAL part,
        ``v_A − (v_A + v_B + v_C)/3`` — the only part a floating-neutral star
        (or, in delta, a line-to-line branch) receives.  Sampled on a carrier
        count that is not a multiple of 3, the held zero sequence is not
        exactly 120°-periodic and leaks a COMMON-MODE fundamental into the pole
        voltage (0.08 % at 20 carriers, 0.8 % at 7 for svpwm); measured on the
        pole, the compensation below would chase volts no winding sees.  For
        sine the two measures agree to 2e-6 and the pole one is kept, so
        every sine source is bit-identical to before.
        """
        n = max(8, int(n_per_carrier)) * self.carriers
        d = 360.0 / n
        acc_c = acc_s = 0.0
        diff = self.modulation != "sine"
        for k in range(n):
            v = self._pole_mean(k * d, (k + 1) * d, 0.0)
            if diff:
                v -= (v + self._pole_mean(k * d, (k + 1) * d, -120.0)
                      + self._pole_mean(k * d, (k + 1) * d, 120.0)) / 3.0
            th = math.radians((k + 0.5) * d + self.v_delta_deg + self.daxis_deg)
            acc_c += v * math.cos(th)
            acc_s += v * math.sin(th)
        c = 2.0 * acc_c / n
        s = 2.0 * acc_s / n
        return math.hypot(c, s), self.v_delta_deg + math.degrees(
            math.atan2(-s, c))


def carriers_per_period(f_switch_hz: float, f_elec_hz: float) -> int:
    """Whole carriers per electrical period (>= 1) — the synchronisation snap."""
    if f_elec_hz <= 1e-9:
        raise ExcitationError(
            "PWM needs a non-zero electrical frequency; this run is at "
            "%.4g Hz (rpm = 0?).  Set a speed, or use the sinusoidal voltage "
            "drive for a static point." % f_elec_hz)
    return max(1, int(round(float(f_switch_hz) / float(f_elec_hz))))


def build_pwm_source(*, pole_pairs: int, daxis_deg: float, v_phase_peak: float,
                     v_delta_deg: float, v_bus: float, f_switch_hz: float,
                     f_elec_hz: float, v_bus_real: float = 0.0,
                     modulation: Optional[str] = "sine"
                     ) -> PwmVoltageSource:
    """Validate the inverter description and build the source.

    Raises :class:`ExcitationError` (-> HTTP 422) rather than clamping anything:
    a modulation index the inverter cannot produce is a question about a machine
    that does not exist, and answering a different question quietly is how a
    solver ends up trusted for the wrong number.

    ``v_bus`` is the bus THIS SOURCE chops.  On a delta machine run through the
    star equivalent (:func:`star_equivalent_bus`) that is the MODEL bus, √3 ×
    the real DC link, and ``v_bus_real`` carries the physical one so the refusals
    below quote the link the user can actually change (user 2026-09-14 / PWM
    study B2).  Left at 0 it IS ``v_bus`` — every existing caller unchanged.
    NB the modulation index needs no such correction: m = 2·V₁_branch/(√3·V_dc)
    is already the real bridge's index.

    ``modulation`` — :data:`PWM_MODULATIONS`; ``"sine"`` (the default) is the
    source every record so far was built on, unchanged.  The zero-sequence
    modulators are refused above their exact linear limit 2/sqrt(3).
    """
    modulation = normalize_modulation(modulation)
    _max_m = modulation_ceiling(modulation)
    _mod_txt = ("" if modulation == "sine" else " (%s)" % modulation)
    _vb_real = float(v_bus_real) if float(v_bus_real) > 0.0 else float(v_bus)
    _eq_star = abs(_vb_real - float(v_bus)) > 1e-9
    _bus_txt = ("%.1f V bus" % float(v_bus) if not _eq_star else
                "%.1f V DC link, star-equivalent model bus %.1f V"
                % (_vb_real, float(v_bus)))
    if not (float(v_bus) > 0.0):
        raise ExcitationError(
            "PWM drive needs a DC bus voltage; got v_bus = %r.  Use the "
            "machine's battery v_nom (Family tab -> battery) or type one."
            % (v_bus,))
    if not (float(v_phase_peak) > 0.0):
        raise ExcitationError(
            "PWM drive needs the fundamental phase-voltage amplitude "
            "(v_phase_peak, V peak); got %r.  Run a current-drive simulation "
            "first and use its V1." % (v_phase_peak,))
    if not (float(f_switch_hz) > 0.0):
        raise ExcitationError(
            "PWM drive needs a switching frequency; got f_switch = %r Hz."
            % (f_switch_hz,))
    m = 2.0 * float(v_phase_peak) / float(v_bus)
    if m > _max_m:
        raise ExcitationError(
            "modulation index m = 2*V_phase_peak/V_bus = %.3f exceeds the "
            "%.4g linear-modulation limit%s (V_phase_peak %.1f V on a %s).  "
            "Overmodulation (pulse dropping / six-step) is out of "
            "scope for this source: raise V_bus above %.0f V, or lower "
            "V_phase_peak below %.1f V."
            % (m, _max_m, _mod_txt, float(v_phase_peak), _bus_txt,
               math.ceil(_vb_real * m / _max_m),
               0.5 * _max_m * float(v_bus)))
    nc = carriers_per_period(f_switch_hz, f_elec_hz)

    # ── MODULATOR DELAY / GAIN COMPENSATION ──────────────────────────────
    # A regular-sampled modulator does not apply the reference it was given: the
    # sample-and-hold delays the fundamental by a quarter carrier (90/N_c
    # electrical degrees) and attenuates it by sinc(pi/(2*N_c)).  On this machine
    # at 4 kHz that is 30 deg of load angle and 4.5 % of voltage — so a
    # switching-frequency sweep at a FIXED v_delta_deg would silently be a load-
    # angle sweep, and the "effect of f_switch" it measured would mostly be the
    # operating point moving.  Every real drive compensates this (sampling-delay
    # compensation in the current loop); so does this source, by solving for the
    # reference whose APPLIED fundamental is the requested one.  Two or three
    # closed-form passes, microseconds.  v_phase_peak and v_delta_deg therefore
    # mean exactly what they mean on the sinusoidal voltage drive, and what the
    # inverter really applied is measured and reported alongside.
    tgt_pk = float(v_phase_peak)
    req_m = m
    req_delta = float(v_delta_deg)
    src = PwmVoltageSource(
        pole_pairs=int(pole_pairs), daxis_deg=float(daxis_deg),
        v_delta_deg=req_delta, v_bus=float(v_bus), carriers=int(nc), m=req_m,
        v1_peak=tgt_pk, v1_delta_deg=float(v_delta_deg), modulation=modulation)
    for _ in range(6):
        got_pk, got_delta = src.applied_fundamental()
        if got_pk < 0.5 * tgt_pk:
            raise ExcitationError(
                "%d carriers per electrical period cannot synthesise this "
                "fundamental (asked %.2f V peak, the modulator applies %.2f "
                "V).  f_switch = %.4g Hz against f_elec = %.4g Hz is a pulse "
                "ratio of %d — raise f_switch."
                % (nc, tgt_pk, got_pk, float(f_switch_hz), float(f_elec_hz),
                   nc))
        if (abs(got_pk - tgt_pk) <= 1e-4 * tgt_pk
                and abs(got_delta - float(v_delta_deg)) <= 1e-3):
            break
        req_m *= tgt_pk / max(got_pk, 1e-9)
        req_delta += float(v_delta_deg) - got_delta
        if req_m > _max_m:
            raise ExcitationError(
                "compensating the modulator's sampled-reference gain for %d "
                "carriers per period needs m = %.3f, past the %.4g linear "
                "limit%s (the uncompensated request was m = %.3f).  Raise "
                "V_bus or f_switch."
                % (nc, req_m, _max_m, _mod_txt, m))
        src = PwmVoltageSource(
            pole_pairs=int(pole_pairs), daxis_deg=float(daxis_deg),
            v_delta_deg=req_delta, v_bus=float(v_bus), carriers=int(nc),
            m=req_m, v1_peak=tgt_pk, v1_delta_deg=float(v_delta_deg),
            modulation=modulation)
    return src


def dc_link_series(src: PwmVoltageSource, *, rotor_angle_deg: Sequence[float],
                   i_a: Sequence[float], i_b: Sequence[float],
                   i_c: Sequence[float], n_parallel: int = 1) -> Dict:
    """Per-solve-step DC-link current of a finished PWM run.

    ``i_*`` are the solver's per-BRANCH phase currents at the END of each step
    (``I_A``/``I_B``/``I_C`` in the transient dict) and ``rotor_angle_deg`` the
    matching angles; the terminal current the bus carries is the branch current
    times the winding's parallel paths, exactly as the terminal power is.

    One value per solve step — the MEAN over that step, on the same grid as the
    torque and the phase currents, so the ripple in it lines up edge-for-edge
    with theirs.  Sampling the switch function instead would alias (a square
    wave sampled at the step grid is ±1 at random, the same trap
    ``mean_voltages`` exists to avoid on the voltage side).

    THE FIRST STEP wraps: the current at its start is the LAST sample of the
    window, because the reported window is a whole number of electrical
    periods starting at θ = 0 on a settled periodic orbit.  On a window that is
    not settled that wrap is wrong in exactly one step out of n — and a window
    that is not settled has bigger problems, which the run reports separately.
    """
    n = len(rotor_angle_deg)
    if n < 2 or len(i_a) != n or len(i_b) != n or len(i_c) != n:
        return {}
    k_par = float(max(1, int(n_parallel)))
    ang = [float(a) for a in rotor_angle_deg]
    dth = ang[1] - ang[0]
    if not (dth > 0.0):
        return {}
    out: List[float] = []
    for k in range(n):
        kp = (k - 1) if k > 0 else (n - 1)
        a0 = ang[k] - dth
        prev = {'A': k_par * float(i_a[kp]), 'B': k_par * float(i_b[kp]),
                'C': k_par * float(i_c[kp])}
        now = {'A': k_par * float(i_a[k]), 'B': k_par * float(i_b[k]),
               'C': k_par * float(i_c[k])}
        out.append(src.dc_link_step_mean(a0, ang[k], prev, now))
    arr = np.asarray(out, float)
    return {
        "I_dc_A": [round(float(v), 4) for v in out],
        "I_dc_mean_A": round(float(np.mean(arr)), 4),
        "I_dc_rms_A": round(float(np.sqrt(np.mean(arr ** 2))), 4),
        "I_dc_ripple_pp_A": round(float(arr.max() - arr.min()), 4),
        "note": ("Σ_phase s_phase(t)·i_phase(t) integrated exactly across the "
                 "modulator's own switching edges inside each solve step, with "
                 "the phase current linear between solved steps; ideal bridge "
                 "(no dead time, no device drops, stiff bus)"),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  IMPOSED ARBITRARY CURRENT WAVEFORM
# ═══════════════════════════════════════════════════════════════════════════
def parse_waveform(raw, *, what: str = "waveform"
                   ) -> List[Tuple[float, float]]:
    """Parse + VALIDATE a sampled periodic waveform.

    Accepts a JSON string or an already-decoded sequence of ``[x, y]`` pairs.
    Every rejection names the offending sample and what the rule is, because the
    caller is an engineer pasting a column out of a spreadsheet and "invalid
    waveform" is not a message anyone can act on.

    Returns the samples as a list of tuples, unchanged (no resampling, no
    smoothing): the solver interpolates them, so what is pasted is what is
    imposed.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ExcitationError(
            "%s is empty — the imposed-current drive needs a phase-A current "
            "waveform: a JSON array of [theta_e_deg, i_A] samples spanning one "
            "electrical period." % what)
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise ExcitationError(
                "%s is not valid JSON (%s).  Expected an array of "
                "[theta_e_deg, i_A] pairs, e.g. [[0, 57.5], [1, 57.4], ...]."
                % (what, exc))
    else:
        data = raw
    if not isinstance(data, (list, tuple)) or len(data) == 0:
        raise ExcitationError(
            "%s must be a non-empty array of [theta_e_deg, i_A] pairs; got %s."
            % (what, type(data).__name__))
    if len(data) < 2:
        raise ExcitationError(
            "%s has 1 sample — at least 2 are needed to interpolate a period."
            % what)
    if len(data) > MAX_WAVEFORM_POINTS:
        raise ExcitationError(
            "%s has %d samples; the limit is %d.  Decimate it — a waveform "
            "finer than the solver's own time step cannot be resolved anyway."
            % (what, len(data), MAX_WAVEFORM_POINTS))
    pts: List[Tuple[float, float]] = []
    for idx, s in enumerate(data):
        if (not isinstance(s, (list, tuple))) or len(s) != 2:
            raise ExcitationError(
                "%s sample %d is %r — each sample must be a [theta_e_deg, i_A] "
                "pair." % (what, idx, s))
        try:
            th = float(s[0]); iv = float(s[1])
        except Exception:
            raise ExcitationError(
                "%s sample %d is %r — both entries must be numbers "
                "(degrees, amperes)." % (what, idx, s))
        if not (math.isfinite(th) and math.isfinite(iv)):
            raise ExcitationError(
                "%s sample %d is [%r, %r] — NaN/inf is not an angle or a "
                "current." % (what, idx, s[0], s[1]))
        pts.append((th, iv))
    for idx in range(1, len(pts)):
        if pts[idx][0] <= pts[idx - 1][0]:
            raise ExcitationError(
                "%s theta must increase strictly: sample %d is at %.4g deg, "
                "sample %d at %.4g deg.  Sort the waveform by angle (and drop "
                "duplicate angles) before sending it."
                % (what, idx - 1, pts[idx - 1][0], idx, pts[idx][0]))
    span = pts[-1][0] - pts[0][0]
    if span > 360.0 + 1e-6:
        raise ExcitationError(
            "%s spans %.4g deg — one ELECTRICAL period (<= 360 deg) is what "
            "the solver repeats.  Send a single period, not %.2f of them."
            % (what, span, span / 360.0))
    # The wrap gap is a real gap: after the last sample the waveform returns to
    # the first, 360 deg after it.  Checking it here is what catches half a
    # period, a radians theta column, and a waveform that stops at 180 deg.
    gaps = [(pts[i][0] - pts[i - 1][0], pts[i - 1][0], pts[i][0])
            for i in range(1, len(pts))]
    gaps.append((360.0 - span, pts[-1][0], pts[0][0] + 360.0))
    g, ga, gb = max(gaps)
    if g > MAX_WAVEFORM_GAP_DEG:
        raise ExcitationError(
            "%s leaves a %.1f deg hole between theta = %.4g and %.4g deg "
            "(the wrap back to the first sample counts).  The samples must "
            "cover a whole electrical period with gaps <= %.0f deg — a common "
            "cause is sending half a period, or theta in radians."
            % (what, g, ga, gb, MAX_WAVEFORM_GAP_DEG))
    if abs(span - 360.0) < 1e-6:
        # First and last sample are the SAME point of the period; if they
        # disagree the waveform has a step discontinuity at the wrap, and every
        # period boundary in the march would kick the machine.
        scale = max(abs(v) for _, v in pts) or 1.0
        if abs(pts[-1][1] - pts[0][1]) > 1e-3 * scale:
            raise ExcitationError(
                "%s does not wrap: i(%.4g deg) = %.4g A but i(%.4g deg) = "
                "%.4g A, and those are the same instant of the period.  Drop "
                "the duplicated end sample, or make the two agree."
                % (what, pts[0][0], pts[0][1], pts[-1][0], pts[-1][1]))
    return pts


@dataclass(frozen=True)
class CustomCurrentSource:
    """Imposed periodic phase current from a sampled waveform.

    Phase A follows the samples; B and C are the SAME shape shifted -/+120 deg
    electrical.  The angle the waveform is indexed by is the same electrical
    angle ``drive.Excitation.currents`` uses — rotor angle x pole pairs, plus
    gamma, plus the topology's d-axis offset — so feeding this source a sampled
    ``I_peak*cos(theta)`` reproduces the stock current drive exactly, and gamma
    still rotates the imposed waveform against the rotor the way it rotates the
    sinusoid.

    The samples are TERMINAL phase current [A]; the per-coil value the solver
    drives is divided by the winding's parallel paths, again exactly as
    ``I_phase_rms`` is.
    """

    pole_pairs: int
    daxis_deg: float
    gamma_deg: float
    n_parallel: int
    _th: np.ndarray            # closed sample angles (last = first + 360)
    _iv: np.ndarray            # closed sample currents [A, terminal]

    @staticmethod
    def build(points: Sequence[Tuple[float, float]], *, pole_pairs: int,
              daxis_deg: float, gamma_deg: float,
              n_parallel: int = 1) -> "CustomCurrentSource":
        th = np.array([p[0] for p in points], float)
        iv = np.array([p[1] for p in points], float)
        if abs((th[-1] - th[0]) - 360.0) > 1e-6:
            th = np.append(th, th[0] + 360.0)
            iv = np.append(iv, iv[0])
        return CustomCurrentSource(
            pole_pairs=int(pole_pairs), daxis_deg=float(daxis_deg),
            gamma_deg=float(gamma_deg), n_parallel=max(1, int(n_parallel)),
            _th=th, _iv=iv)

    def _at(self, theta_e_deg: float) -> float:
        t0 = float(self._th[0])
        x = t0 + math.fmod(math.fmod(theta_e_deg - t0, 360.0) + 360.0, 360.0)
        return float(np.interp(x, self._th, self._iv))

    def currents(self, rotor_angle_deg: float) -> Dict[str, float]:
        te = (float(rotor_angle_deg) * self.pole_pairs
              + self.gamma_deg + self.daxis_deg)
        k = 1.0 / float(self.n_parallel)
        return {'A': k * self._at(te),
                'B': k * self._at(te - 120.0),
                'C': k * self._at(te + 120.0)}

    def rms_terminal(self) -> float:
        """RMS of the imposed terminal phase current over one period."""
        th = self._th; iv = self._iv
        # trapezoid on the closed period (written out: numpy renamed trapz)
        y = iv * iv
        area = float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(th)))
        return float(math.sqrt(max(0.0, area / 360.0)))

    def fundamental(self) -> Tuple[float, float]:
        """(I1 rms [A], gamma1 [deg el]) of the imposed waveform.

        gamma1 is in the SAME convention as the panel's gamma: the angle of the
        current fundamental measured against the q-axis, so a pure
        ``I*cos(theta + gamma)`` waveform reports back the gamma it was built
        with.
        """
        # Dense resample so the projection does not inherit the sample spacing.
        x = np.linspace(self._th[0], self._th[0] + 360.0, 2048, endpoint=False)
        y = np.interp(x, self._th, self._iv)
        r = np.radians(x)
        c = 2.0 * float(np.mean(y * np.cos(r)))
        s = -2.0 * float(np.mean(y * np.sin(r)))
        amp = math.hypot(c, s)
        return amp / math.sqrt(2.0), math.degrees(math.atan2(s, c))


# ═══════════════════════════════════════════════════════════════════════════
#  BLDC — 120° SIX-STEP BLOCK COMMUTATION
# ═══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class BlockCurrentSource:
    """Classic trapezoidal (six-step) drive: +I for 120°el, float 60°, -I for
    120°, float 60°.

    Exactly two phases conduct at any instant and the third is open, so the
    three currents sum to zero at every instant — as they must on an isolated
    neutral.  The block is CENTRED on the peak of the sinusoid the same gamma
    would produce, so gamma means the same thing here as on every other source:
    the advance of the current against the q-axis, and gamma = 0 is the
    six-step commutation a Hall-sensored controller runs at zero advance.

    AMPLITUDE.  ``i_block`` is the FLAT-TOP terminal current, not an rms and not
    a sinusoid peak — the number a block-commutated controller's current limit
    is set to.  The equivalences, because a torque comparison against the
    sinusoid is meaningless without them:

        rms of a 120° block   = i_block * sqrt(2/3)  = 0.8165 * i_block
        fundamental amplitude = i_block * 2*sqrt(3)/pi = 1.1027 * i_block

    So matching COPPER LOSS against a sinusoidal drive at I_rms means
    i_block = I_rms * sqrt(3/2) = 1.2247 * I_rms, and that is the comparison
    worth making: same watts in the winding, different waveform.

    COMMUTATION EDGE.  An ideal block has infinite di/dt at commutation, which
    is not a current any winding carries and not a source term a time-marched
    solver can accept: the imposed step would appear entirely inside one frame,
    and dpsi/dt (hence the reported terminal voltage) would be an artifact of
    the step size.  The edges are therefore RAMPED linearly over ``ramp_deg``,
    which the solver sets to one time step, and this is reported.  The ramps
    pair up — the phase leaving conduction and the phase entering it ramp
    together — so the three currents still sum to zero throughout, and a finer
    time step gives a sharper edge (converging to the ideal block) rather than a
    different machine.
    """

    pole_pairs: int
    daxis_deg: float
    gamma_deg: float
    n_parallel: int
    i_block: float            # flat-top terminal current [A]
    ramp_deg: float           # commutation ramp width [deg el]

    def _shape(self, x_deg: float) -> float:
        """Phase-A block, indexed by the same electrical angle the sinusoid's
        cos() uses: +1 on [-60, 60), 0 on [60, 120), -1 on [120, 240), 0 on
        [240, 300), with each edge ramped over ``ramp_deg``."""
        x = math.fmod(math.fmod(x_deg, 360.0) + 360.0, 360.0)
        w = max(1e-9, float(self.ramp_deg))
        h = 0.5 * w
        # Edge centres and the (before, after) levels either side of each.
        for c, a, b in ((60.0, 1.0, 0.0), (120.0, 0.0, -1.0),
                        (240.0, -1.0, 0.0), (300.0, 0.0, 1.0)):
            if c - h < x < c + h:
                return a + (b - a) * (x - (c - h)) / w
        if x < 60.0 or x >= 300.0:
            return 1.0
        if x < 120.0:
            return 0.0
        if x < 240.0:
            return -1.0
        return 0.0

    def currents(self, rotor_angle_deg: float) -> Dict[str, float]:
        te = (float(rotor_angle_deg) * self.pole_pairs
              + self.gamma_deg + self.daxis_deg)
        k = float(self.i_block) / float(max(1, self.n_parallel))
        return {'A': k * self._shape(te),
                'B': k * self._shape(te - 120.0),
                'C': k * self._shape(te + 120.0)}

    def rms_terminal(self) -> float:
        n = 4096
        s = sum(self._shape(i * 360.0 / n) ** 2 for i in range(n)) / n
        return float(self.i_block) * math.sqrt(s)

    def fundamental(self) -> Tuple[float, float]:
        """(I1 rms [A], gamma1 [deg el]) of the block, panel convention."""
        n = 4096
        c = s = 0.0
        for i in range(n):
            x = i * 360.0 / n
            y = self._shape(x)
            c += y * math.cos(math.radians(x))
            s += y * math.sin(math.radians(x))
        c *= 2.0 * float(self.i_block) / n
        s *= -2.0 * float(self.i_block) / n
        return math.hypot(c, s) / math.sqrt(2.0), math.degrees(math.atan2(s, c))

    def waveform(self, step_deg: float = 1.0) -> List[Tuple[float, float]]:
        """The block as sampled [(theta_e_deg, i_A)] — for plotting, and so the
        same shape can be handed to ``custom_current`` if someone wants to edit
        it.  Sampled on a grid that lands on every ramp end, so the linear
        interpolation of an imposed waveform reproduces this source exactly."""
        pts = set()
        h = 0.5 * max(1e-9, float(self.ramp_deg))
        for c in (60.0, 120.0, 240.0, 300.0):
            pts.add(round((c - h) % 360.0, 6))
            pts.add(round((c + h) % 360.0, 6))
        k = max(1, int(round(360.0 / max(step_deg, 1e-6))))
        for i in range(k):
            pts.add(round(i * 360.0 / k, 6))
        xs = sorted(pts)
        return [(x, float(self.i_block) * self._shape(x)) for x in xs]


# ═══════════════════════════════════════════════════════════════════════════
#  PWM CURRENT SYNTHESIS — the constant-L circuit model (no FEM)
# ═══════════════════════════════════════════════════════════════════════════
def synthesize_pwm_current(
    *, r_phase: float, ld_h: float, lq_h: float, psi_pm_wb: float,
    pole_pairs: int, rpm: float, v_bus: float, f_switch_hz: float,
    i_phase_rms: float, gamma_deg: float,
    emf_waveform: Optional[Sequence[Tuple[float, float]]] = None,
    n_samples: int = 0, sub_steps: int = 32, settle_periods: int = 6,
    regulator_iters: int = 8,
) -> Dict:
    """Integrate the phase current a PWM inverter forces through a CONSTANT-L
    machine, and return it as a waveform ready for ``drive="custom_current"``.

    MODEL.  The synchronous-frame machine equations, which handle saliency
    exactly and reduce to the per-phase RL circuit when Ld = Lq:

        v_d = R*i_d + Ld*di_d/dt - w*Lq*i_q
        v_q = R*i_q + Lq*di_q/dt + w*(Ld*i_d + psi_pm)

    with (v_d, v_q) the Park transform of the inverter phase voltages (pole
    voltages minus their common mode — the neutral floats, so the zero sequence
    drives no current).  Integrated with RK4 on a sub-step grid, each step
    forced by the EXACT volt-seconds of that step rather than by a sample of the
    square wave: switching edges fall between grid points, and sampling makes
    RK4 first-order in where they land (measured at 16 kHz, 33 steps per
    carrier: the regulator sat 6.6 A off its setpoint and the ripple read 13 %
    low).  With the closed-form interval mean the answer is grid-independent to
    four figures.

    NON-SINUSOIDAL BACK-EMF.  Pass ``emf_waveform`` — [theta_e_deg, e_A_V]
    samples of the phase-A open-circuit EMF at THIS speed — and the rotational
    term is taken from it instead of from ``w*psi_pm``; the harmonic currents a
    stiff supply forces through the winding then appear alongside the switching
    ripple.  Without it the EMF is the ideal sinusoid ``-w*psi_pm*sin(theta_e)``
    and the answer is switching ripple only.

    THE REGULATOR is a feed-forward voltage reference corrected by a few
    synchronous-frame deadbeat iterations (NOT a PI loop with a bandwidth to
    argue about): the steady-state dq voltage for the requested (i_d, i_q) is
    computed from the equations above, the model is run, the achieved
    FUNDAMENTAL current is measured, and the reference is corrected through the
    impedance matrix.  It converges in a handful of passes and leaves a
    fundamental that lands on the setpoint, so the returned ripple is a property
    of the inverter and the machine, not of a controller tuning.  What it cannot
    do is invent bus voltage: if the required m exceeds the linear limit the
    call is refused, exactly as the FEM PWM drive refuses it.

    WHAT IT IS NOT.  A constant-L model.  Saturation, the real slot-harmonic
    inductance variation and the eddy reaction are absent, so this is the FAST
    route (a second, against an hour of FEM) and ``drive="pwm_voltage"`` is the
    honest one.  Use this to size a study, to compare against the published
    constant-L PWM work, or when the current waveform comes from somewhere else
    entirely.
    """
    pp = max(1, int(pole_pairs))
    f_elec = float(rpm) * pp / 60.0
    if f_elec <= 1e-9:
        raise ExcitationError(
            "PWM synthesis needs a non-zero speed; rpm = %r gives f_elec = "
            "%.4g Hz." % (rpm, f_elec))
    ld = float(ld_h); lq = float(lq_h)
    if not (ld > 0.0 and lq > 0.0):
        raise ExcitationError(
            "PWM synthesis needs positive dq inductances; got Ld = %.6g H, "
            "Lq = %.6g H.  Measure them first (Simulation -> bench Ld/Lq)."
            % (ld, lq))
    w = 2.0 * math.pi * f_elec
    r = float(r_phase)
    ipk = float(i_phase_rms) * math.sqrt(2.0)
    # gamma is measured against the q-axis, positive = current advanced into
    # -d (field weakening); the same convention drive.Excitation uses.
    id_t = -ipk * math.sin(math.radians(gamma_deg))
    iq_t = ipk * math.cos(math.radians(gamma_deg))

    emf_pts = None
    if emf_waveform:
        emf_pts = parse_waveform(emf_waveform, what="emf_waveform")
        eth = np.array([p[0] for p in emf_pts], float)
        eiv = np.array([p[1] for p in emf_pts], float)
        if abs((eth[-1] - eth[0]) - 360.0) > 1e-6:
            eth = np.append(eth, eth[0] + 360.0)
            eiv = np.append(eiv, eiv[0])

    def _emf_abc(te_deg: float) -> Tuple[float, float, float]:
        if emf_pts is None:
            s = math.radians(te_deg)
            e = -w * float(psi_pm_wb)
            return (e * math.sin(s),
                    e * math.sin(s - 2 * math.pi / 3),
                    e * math.sin(s + 2 * math.pi / 3))
        t0 = float(eth[0])

        def at(x):
            xx = t0 + math.fmod(math.fmod(x - t0, 360.0) + 360.0, 360.0)
            return float(np.interp(xx, eth, eiv))
        return at(te_deg), at(te_deg - 120.0), at(te_deg + 120.0)

    # ── feed-forward voltage reference, then deadbeat corrections ────────
    vd = r * id_t - w * lq * iq_t
    vq = r * iq_t + w * (ld * id_t + float(psi_pm_wb))
    # Impedance matrix of the fundamental — the correction Jacobian.
    Z = np.array([[r, -w * lq], [w * ld, r]], float)

    nc = carriers_per_period(f_switch_hz, f_elec)
    n_pts = int(n_samples) if int(n_samples) > 0 else max(360, 32 * nc)
    n_pts = min(int(n_pts), MAX_WAVEFORM_POINTS)
    # Integration grid: at least `sub_steps` per carrier AND at least the output
    # grid, so the reported samples are grid points rather than interpolations.
    n_int = max(int(sub_steps) * nc, n_pts)
    n_int = int(math.ceil(n_int / n_pts) * n_pts)      # n_pts divides n_int
    dth = 360.0 / n_int                                 # electrical deg / step
    dt = 1.0 / (f_elec * n_int)

    out: Dict = {}
    for _pass in range(max(1, int(regulator_iters))):
        vpk = math.hypot(vd, vq)
        m = 2.0 * vpk / float(v_bus)
        if m > MAX_MODULATION_INDEX:
            raise ExcitationError(
                "the requested operating point needs %.1f V peak per phase, "
                "i.e. m = %.3f on a %.1f V bus — past the %.2f linear limit.  "
                "Raise V_bus above %.0f V, lower the current, or advance gamma "
                "(field weakening)."
                % (vpk, m, float(v_bus), MAX_MODULATION_INDEX,
                   math.ceil(2.0 * vpk / MAX_MODULATION_INDEX)))
        # INTERNAL frame.  Here the EMF is written e_A = -w*psi_pm*sin(theta_e),
        # so the inverse Park is x_A = x_d*cos - x_q*sin and a reference
        # V*cos(theta_e + delta_int) has (v_d, v_q) = V*(cos, sin)(delta_int) —
        # i.e. delta_int = atan2(v_q, v_d).  That is NOT the panel's delta (which
        # is measured from the q-axis: delta_panel = delta_int - 90); the two
        # differ by exactly the quarter turn between "aligned with the PM flux"
        # and "aligned with the EMF".  The returned waveform is re-indexed onto
        # the panel/solver frame at the end, so nothing outside this function
        # ever sees delta_int.
        delta = math.degrees(math.atan2(vq, vd))
        src = PwmVoltageSource(pole_pairs=pp, daxis_deg=0.0,
                               v_delta_deg=delta, v_bus=float(v_bus),
                               carriers=nc, m=m)

        def deriv(te_deg: float, idq: np.ndarray, v) -> np.ndarray:
            vn = (v['A'] + v['B'] + v['C']) / 3.0  # floating neutral
            ea, eb, ec = _emf_abc(te_deg)
            va = v['A'] - vn - ea
            vb = v['B'] - vn - eb
            vc = v['C'] - vn - ec
            th = math.radians(te_deg)
            vdq_d = (2.0 / 3.0) * (va * math.cos(th)
                                   + vb * math.cos(th - 2 * math.pi / 3)
                                   + vc * math.cos(th + 2 * math.pi / 3))
            vdq_q = -(2.0 / 3.0) * (va * math.sin(th)
                                    + vb * math.sin(th - 2 * math.pi / 3)
                                    + vc * math.sin(th + 2 * math.pi / 3))
            # The PM term is already inside the EMF above, so the dq equations
            # here carry only R, the L*di/dt and the cross-coupling.
            return np.array([(vdq_d - r * idq[0] + w * lq * idq[1]) / ld,
                             (vdq_q - r * idq[1] - w * ld * idq[0]) / lq])

        idq = np.array([id_t, iq_t], float)
        rec_th = np.empty(n_int); rec_ia = np.empty(n_int)
        for per in range(max(1, int(settle_periods))):
            last = (per == max(1, int(settle_periods)) - 1)
            for k in range(n_int):
                te = k * dth
                if last:
                    th = math.radians(te)
                    rec_th[k] = te
                    rec_ia[k] = (idq[0] * math.cos(th) - idq[1] * math.sin(th))
                # EXACT volt-seconds over the step, not an instantaneous
                # sample.  A two-level waveform's edges fall between grid
                # points, and sampling it makes RK4 first-order in the edge
                # placement: measured on the 40 mm at 16 kHz, 33 steps per
                # carrier left the regulator 6.6 A off its setpoint and read
                # the ripple 13 % low.  The interval mean is closed-form
                # (pwm.PwmVoltageSource._pole_mean), so the answer stops
                # depending on where the grid happens to land — the same
                # argument, and the same function, the FEM PWM source uses.
                vm = src.mean_voltages(te / pp, (te + dth) / pp)
                k1 = deriv(te, idq, vm)
                k2 = deriv(te + 0.5 * dth, idq + 0.5 * dt * k1, vm)
                k3 = deriv(te + 0.5 * dth, idq + 0.5 * dt * k2, vm)
                k4 = deriv(te + dth, idq + dt * k3, vm)
                idq = idq + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        # measured fundamental of the achieved phase-A current
        rr = np.radians(rec_th)
        c1 = 2.0 * float(np.mean(rec_ia * np.cos(rr)))
        s1 = -2.0 * float(np.mean(rec_ia * np.sin(rr)))
        # i_A = i_d cos - i_q sin  =>  c1 = i_d1, s1 = i_q1
        i_d1, i_q1 = c1, s1
        err = np.array([id_t - i_d1, iq_t - i_q1], float)
        out = dict(th=rec_th.copy(), ia=rec_ia.copy(), delta=delta, m=m,
                   vpk=vpk, i_d1=i_d1, i_q1=i_q1,
                   err=float(np.linalg.norm(err)))
        if float(np.linalg.norm(err)) <= 1e-4 * max(ipk, 1e-9):
            break
        dv = Z @ err
        vd += float(dv[0]); vq += float(dv[1])

    step = len(out["th"]) // n_pts
    th = out["th"][::step][:n_pts]
    ia = out["ia"][::step][:n_pts]
    i1_pk = math.hypot(out["i_d1"], out["i_q1"])
    i_rms = float(math.sqrt(float(np.mean(ia * ia))))
    # Ripple: peak-to-peak of what is left after the fundamental is removed,
    # as a percentage of the fundamental amplitude — the number the article's
    # figure is showing.
    rr = np.radians(th)
    resid = ia - (out["i_d1"] * np.cos(rr) - out["i_q1"] * np.sin(rr))
    # ── RE-INDEX onto the frame drive="custom_current" reads ─────────────
    # CustomCurrentSource evaluates the waveform at theta_e = rotor*pp + gamma +
    # daxis, so a waveform whose fundamental is cos(theta) reproduces the stock
    # sinusoidal current drive at that gamma.  Internally the fundamental sits
    # at cos(theta_e_int + gamma + 90) (the same quarter turn as delta above),
    # so shifting the angle axis by +(gamma + 90) hands back a waveform that is
    # imposed at the requested gamma WITHOUT gamma being baked into it — set the
    # panel's gamma on the run exactly as for any other source, and the two
    # agree.  A pure roll of a uniform grid: no resampling, no smoothing.
    th_out = np.mod(th + float(gamma_deg) + 90.0, 360.0)
    _order = np.argsort(th_out, kind="stable")
    th_out = th_out[_order]
    ia_out = ia[_order]
    thd = (float(math.sqrt(max(0.0, i_rms ** 2 - (i1_pk / math.sqrt(2)) ** 2)))
           / max(i1_pk / math.sqrt(2), 1e-12))
    return {
        "waveform": [[round(float(a), 4), round(float(b), 4)]
                     for a, b in zip(th_out, ia_out)],
        "waveform_frame": ("theta_e = rotor_angle*pole_pairs + gamma + daxis, "
                           "i.e. the frame drive='custom_current' reads — feed "
                           "it back with the SAME gamma_deg used here"),
        "n_samples": int(len(th_out)),
        "f_elec_Hz": round(f_elec, 4),
        "f_switch_requested_Hz": float(f_switch_hz),
        "f_switch_eff_Hz": round(nc * f_elec, 2),
        "carriers_per_period": int(nc),
        "modulation_index": round(out["m"], 4),
        # The modulator REFERENCE the regulator settled on, reported in the
        # panel's frame (delta measured from the q-axis, directly comparable to
        # gamma and to the sinusoidal voltage drive's v_delta_deg).  It already
        # contains the modulator's transport delay, so it is bigger than the
        # steady-state load angle by roughly 90/N_c degrees — which is exactly
        # what `pwm.build_pwm_source` computes when it compensates.
        "v_ref_peak_V": round(out["vpk"], 4),
        "v_ref_delta_deg": round(
            ((out["delta"] - 90.0 + 180.0) % 360.0) - 180.0, 3),
        "v_bus_V": float(v_bus),
        "I1_phase_rms_A": round(i1_pk / math.sqrt(2.0), 4),
        "gamma1_deg": round(math.degrees(math.atan2(-out["i_d1"],
                                                    out["i_q1"])), 3),
        "I_phase_rms_A": round(i_rms, 4),
        "I_ripple_pp_A": round(float(resid.max() - resid.min()), 4),
        "I_ripple_pp_pct": round(100.0 * float(resid.max() - resid.min())
                                 / max(i1_pk, 1e-12), 3),
        "I_thd_pct": round(100.0 * thd, 3),
        "setpoint_error_A": round(out["err"], 5),
        "integration_steps_per_period": int(len(out["th"])),
        "emf_source": ("waveform" if emf_pts is not None else "sinusoidal psi_pm"),
        "model": ("constant-Ld/Lq synchronous-frame RL circuit, RK4, ideal "
                  "two-level synchronous regular-sampled sine-triangle PWM, "
                  "floating neutral; feed-forward + deadbeat fundamental match"),
    }
