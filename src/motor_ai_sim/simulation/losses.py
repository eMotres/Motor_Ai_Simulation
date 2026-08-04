"""Loss models that turn a captured B(t) history into watts.

One implementation per model, shared by every element order. The iron loss used
to be written twice — once in the P1 branch, once in P2 — identical line for
line except for how dB/dt was estimated. Duplicated physics is how a fix reaches
one path and misses the other, which is exactly what happened here: P2 spent a
while reporting ZERO core loss because its copy sat behind a flag the P1 copy
did not have. Passing the derivative in as a callable keeps the one genuine
difference and removes the copy.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Callable, Optional, Sequence, Tuple

import numpy as np

from motor_ai_sim.core_loss_surface import get_surface as _get_surface

log = logging.getLogger(__name__)

# 2*pi^2 — the classical-eddy denominator in the Bertotti form below.
TWO_PI_SQ = 2.0 * math.pi ** 2

# Harmonics whose amplitude is below this ANYWHERE in the half are skipped in
# the measured-surface sum. 1 mT against a ~1 T fundamental is a 1e-6 relative
# loss contribution — below the noise of the field snapshot it came from — and
# skipping them is what keeps the per-harmonic surface evaluation cheap.
HARMONIC_FLOOR_T = 1e-3

# Leakage guard taper on |end-to-start step| / peak-to-peak: nothing removed
# below the first, all of it above the second. See ``harmonic_amplitudes``.
WRAP_TAPER = (0.15, 0.30)

# Fill factor used when a steel declares none. Every steel in the shipped library
# declares its own (B15AHV950M is 0.92, the datasheet's guaranteed minimum), so
# this is a guard, not a knob — and it is ONE value: the same constant appeared
# as 0.95 in two places and 0.97 in a third, which is the sort of split that
# quietly moves numbers.
DEFAULT_STACKING_FACTOR = 0.97


def harmonic_amplitudes(H: np.ndarray, f_elec_hz: float, n_periods: float,
                        wrap: Optional[dict] = None
                        ) -> Tuple[np.ndarray, np.ndarray]:
    """(amplitudes, frequencies) of one field COMPONENT's per-element history.

    ``H`` is (n_frames, n_elements) over a window of ``n_periods`` electrical
    periods. Returns PEAK amplitudes (not RMS, not peak-to-peak): bin ``m`` of a
    real DFT carries amplitude ``2|C_m|`` except at Nyquist, where the single
    real cosine carries ``|C_m|``. The DC bin is dropped — a static offset
    stores energy, it does not dissipate it. Bin ``m`` sits at
    ``m·f_elec/n_periods``; content above Nyquist aliases down, exactly as it
    does in every other quantity taken from this snapshot (a 40-step capture
    resolves 20 harmonics and nothing above).

    LEAKAGE GUARD — the part that is not boilerplate. The DFT assumes the
    window closes on itself, and on the STATOR half it does: the field there is
    periodic in one electrical period by construction. On the ROTOR half it
    does NOT. A 24-slot / 14-pole-pair machine slides 24/14 = 1.71 slot pitches
    past a rotor point per electrical period, so the slot-passing waveform the
    rotor iron actually sees is incommensurate with the capture window and the
    window ends mid-cycle. A rectangular DFT of that reads the end-to-start STEP
    as broadband content, and because loss rises with frequency the spurious
    tail is billed at up to 19 kHz. Measured on the 150 mm: 30 % of the raw
    rotor harmonic sum sat in bins k ≥ 8, RISING toward Nyquist — the signature
    of a step, not of physics.

    So the step is estimated and removed as a linear ramp before the transform,
    PER ELEMENT and TAPERED — a clean capture must not be touched at all:

    * the jump is the periodic-extension step ``x[N] − x[0]``, with ``x[N]``
      extrapolated one sample past the end by the quadratic through the last
      three (the naive ``x[N−1] − x[0]`` is one sample of slope, not a step:
      on a sampled sinusoid it reads 1.2 e-2 of the amplitude where the
      quadratic reads 9 e-4, and subtracting THAT as a ramp corrupts a signal
      that was periodic all along — measured, it cost 2 % of the stator loss);
    * it is removed with weight ``smoothstep`` of ``|jump|/peak-to-peak``
      between ``WRAP_TAPER`` = (0.15, 0.30). Below 15 % nothing is removed;
      above 30 % all of it is. The taper is C1 in the data, so no element
      crosses a threshold and hands the optimizer a step.

    Measured on both machines: mean guard weight 0.00 (150 mm) and 0.06 (40 mm)
    on the STATOR half — i.e. it does nothing there, and the stator total moves
    by <0.01 % — against 0.75 and 0.81 on the ROTOR half, where it removes 37 %
    of the raw harmonic sum and drops the k ≥ 8 tail from 30 % to 8 %. Moving
    the taper band to (0.10, 0.25) or (0.20, 0.40) changes the 150 mm total by
    0.2 %, so the thresholds are not a tuning knob.

    What it costs: the removed ramp is real flux excursion, sitting below the
    electrical fundamental (it is the beat of an incommensurate frequency
    against the window). Dropping it makes the rotor harmonic sum a LOWER
    bracket; the raw sum is the upper one — 10.8 W against 17.1 W on the
    150 mm rotor. Closing that gap needs a capture window commensurate with
    slot passing — seven electrical periods on this machine — not a change here.

    ``wrap``, when a dict is passed, receives the measured jump statistics so
    the run can say which half it was talking about.
    """
    n = int(H.shape[0])
    if n >= 4:
        jump = 3.0 * H[-1] - 3.0 * H[-2] + H[-3] - H[0]
        ptp = np.maximum(H.max(0) - H.min(0), 1e-12)
        r = np.abs(jump) / ptp
        u = np.clip((r - WRAP_TAPER[0]) / (WRAP_TAPER[1] - WRAP_TAPER[0]),
                    0.0, 1.0)
        w = u * u * (3.0 - 2.0 * u)
        if wrap is not None:
            live = ptp > 1e-3
            if live.any():
                wrap["frac"] = max(wrap.get("frac", 0.0),
                                   float(np.median(r[live])))
                wrap["p90"] = max(wrap.get("p90", 0.0),
                                  float(np.percentile(r[live], 90)))
                wrap["weight"] = max(wrap.get("weight", 0.0),
                                     float(np.mean(w[live])))
        H = H - np.outer(np.arange(n, dtype=float) / n, jump * w)
    C = np.fft.rfft(H, axis=0) / max(n, 1)
    amp = 2.0 * np.abs(C)
    if n % 2 == 0 and amp.shape[0] > 1:
        amp[-1] = np.abs(C[-1])
    freqs = np.arange(amp.shape[0]) * (float(f_elec_hz) / max(float(n_periods), 1e-9))
    return amp[1:], freqs[1:]


def surface_loss_density(
    X: np.ndarray, Y: np.ndarray, surface: Any, inv_kf: float,
    f_elec_hz: float, n_periods: float,
    weight: Optional[np.ndarray] = None,
    excursion: Optional[dict] = None,
    fundamental_only: Optional[list] = None,
    wrap: Optional[dict] = None,
) -> np.ndarray:
    """Per-element iron loss density [W/m³ of STEEL] from the measured surface.

    HARMONIC SUPERPOSITION — stated, because it is an assumption and not a
    theorem. A measured P(B, f) point is the loss of a SINUSOIDAL excitation of
    peak induction B at frequency f. A machine's B(t) is not sinusoidal, so the
    locus is decomposed into its harmonics and the surface is evaluated at each
    (amplitude, frequency) and SUMMED:

        P = Σ_axes Σ_{m≥1} P_meas(B_m / k_f, m·f_elec/n_periods)

    This is the standard engineering treatment (Ansys Maxwell's own harmonic
    core-loss option does the same thing) and it is imperfect in a known
    direction: hysteresis is a memory process, so the loss of a sum of
    harmonics is not exactly the sum of their losses — a superimposed ripple
    that creates a MINOR LOOP costs more than its own sinusoid would, and one
    that merely distorts the major loop costs less. It is nonetheless a strict
    improvement on the alternative (evaluating one equivalent amplitude at the
    fundamental), which cannot see slot ripple at all. See Bertotti,
    *Hysteresis in Magnetism* (1998) ch. 12 on loss separation, and IEC 60404-2
    for what the table underneath actually measured.

    Exactness that survives: the classical (eddy) part of the loss IS additive
    over harmonics by Parseval, so the eddy content of this sum equals the
    ⟨(dB/dt)²⟩ time-series integral of the same waveform. Only the hysteresis
    and excess parts carry the superposition assumption.

    The two field components are decomposed and summed SEPARATELY — the axis
    decomposition of the Bertotti path, unchanged: rotating loci still read low.

    ``fundamental_only``, when a list is passed, receives the m = 1 term alone,
    so the harmonic content's contribution can be reported rather than assumed.
    """
    out = np.zeros(X.shape[1], float)
    fund = np.zeros(X.shape[1], float)
    for H in (X, Y):
        amp, freqs = harmonic_amplitudes(H, f_elec_hz, n_periods, wrap)
        for m in range(amp.shape[0]):
            A = amp[m] * inv_kf
            if not A.size or float(A.max()) < HARMONIC_FLOOR_T:
                continue
            p = surface.w_per_m3(A, float(freqs[m]), excursion, weight)
            out += p
            if m == 0:                     # m is 0-based over harmonics 1, 2, …
                fund += p
    if fundamental_only is not None:
        fundamental_only.append(fund)
    return out


def iron_loss_series(
    hist_x: Sequence,
    hist_y: Sequence,
    idx: np.ndarray,
    areas: np.ndarray,
    material: Any,
    stack_length_m: float,
    f_elec_hz: float,
    n_frames: int,
    ddt: Callable[[np.ndarray], np.ndarray],
    bertotti: Callable[[Any], Tuple[float, float, float]],
    terms: Optional[dict] = None,
    n_periods: float = 1.0,
) -> Tuple[np.ndarray, float]:
    """Iron loss from a per-element B(t) history — measured surface, or Bertotti.

    TWO models, chosen by the RECORD (see MEASURED SURFACE below). The Bertotti
    form is described first because it is still the fallback, still what every
    non-opted-in steel gets, and still what the surface blends into outside the
    measured envelope.

    Returns ``(P_classical(t), P_hysteresis_and_excess)`` — the first ripples
    with the teeth passing, the second is a per-cycle quantity and therefore flat.

        P/V_steel = k_h*f*B_s^2 + k_c/(2*pi^2)*<(dB_s/dt)^2> + k_e*f^1.5*B_s^1.5

    The coefficients come from the material's MEASURED loss curves when it has
    them (relative-error-weighted NNLS over every (f, B) point), falling back to
    the YAML k_h/k_c/k_e. Real curves give real loss.

    LAMINATION (k_f) — the one place the homogenised field and the measured
    coefficients have to be reconciled, and it was wrong before this:

      * The magnetic solve runs on HOMOGENISED iron (``fem_solver_2d``,
        "lamination in the magnetic model"): steel and inter-laminate insulation
        are smeared into one medium over the GEOMETRIC section the 2-D model
        draws. Flux only crosses the steel, so what the solve reports is
        ``B_homog = k_f * B_steel`` — the steel inside carries ``B/k_f``, i.e.
        +8.7 % at the shipped k_f = 0.92.
      * The datasheet coefficients are W per m^3 OF STEEL at the peak
        polarisation IN the steel. So the loss must be evaluated at ``B/k_f``
        and billed on the steel volume ``k_f * V_geom``:

            P = k_f*V * [ k_h*f*(B/k_f)^2
                        + k_c/(2*pi^2)*<(dB/dt)^2>/k_f^2
                        + k_e*f^1.5*(B/k_f)^1.5 ]
              = V * [ k_h*f*B^2 / k_f
                    + k_c/(2*pi^2)*<(dB/dt)^2> / k_f
                    + k_e*f^1.5*B^1.5 / sqrt(k_f) ]

        Against plain Bertotti at B_homog over the geometric volume that is
        1/k_f (+8.7 % at k_f = 0.92) on the two B^2 terms and 1/sqrt(k_f)
        (+4.3 %) on the excess term. Against what this function used to compute
        — Bertotti at B_homog over the STEEL volume, i.e. the k_f applied to the
        volume and NOT to the field — it is 1/k_f^2 = +18.2 % and
        1/k_f^1.5 = +13.3 %.
        The old form was the worst of both: it removed the insulation from the
        mass but never put the flux it displaces back into the steel.

    WAVEFORM (Bertotti path). The classical term is the true per-element ``dB/dt`` TIME SERIES
    of BOTH components — every harmonic the solve resolved (slot ripple, minor
    loops) is in it by construction, no sinusoidal equivalent anywhere. The
    per-cycle terms (hysteresis, excess) are MAJOR-LOOP ONLY: each component's
    amplitude is half its peak-to-peak over the captured period and is counted
    once per electrical cycle. There is no rainflow minor-loop count — a tooth
    whose flux dips and recovers within the cycle pays for that dip in the eddy
    term but not in the hysteresis term. That is a deliberate (and stated)
    modelling choice, matching the classical Bertotti separation; it makes the
    hysteresis term a LOWER bound on machines with strong slot ripple. The
    SURFACE path does not have this limitation — it decomposes the locus and
    bills each harmonic at its own frequency, so a ripple that makes a minor
    loop is paid for. It has a different one instead: loss superposition over
    harmonics (see ``surface_loss_density``).

    ROTATIONAL. The two field components are treated as two independent
    alternating loci and their hysteresis contributions SUMMED (the standard
    axis-decomposition approximation). Elements at a tooth root, where B rotates
    rather than alternates, are therefore under-read — classically by up to
    ~1.5-2x locally. Correcting it needs a measured rotational-loss surface the
    material records do not carry, so it stays an UNDER-READ that is reported as
    such. Nothing here multiplies the computed loss by a correction coefficient:
    the number this returns is the raw calculation, and the difference to a
    measured core is a difference, not a coefficient to be tuned away.

    ``ddt`` is the caller's time-derivative operator, and it is the ONLY thing
    that differs between element orders: P1 needs a smoothed angle-derivative
    because the sliding band's node re-pairing adds a frame-to-frame jitter that
    a raw difference amplifies (the loss tripled going from 24 to 72 steps),
    while the P2 field is smooth enough for a plain central difference.

    ``bertotti`` is injected rather than imported so this module stays free of
    the materials library and can be tested with hand-written coefficients. The
    surface comes off the ``material`` OBJECT through ``core_loss_surface``,
    which is pure interpolation with no library dependency of its own, so that
    property survives.

    MEASURED SURFACE. A steel whose record says ``core_loss_model:
    measured_surface`` does NOT go through the three coefficients at all: its
    measured P(B, f) table is interpolated directly (``core_loss_surface``),
    per harmonic of the per-element locus, per axis. The three-coefficient fit
    survives only as the out-of-envelope fallback the surface blends into, and
    as the SHAPE of the reported hysteresis/excess split. Every other steel is
    on exactly the code path it was on before — the surface activates per
    record, not per library.

    Why it was worth doing: a fixed B² cannot follow the saturation upturn, and
    on the 150 mm 24s28p at 933 Hz, 43 % of the stator loss sits above 1.5 T
    where the fit reads 10-15 % low. Integrated over that machine the fit
    returned 53.6 W per cycle against the measured surface's 62.3 W.

    ``terms``, when a dict is passed, is filled with the cycle-mean watts of
    each Bertotti term (``hysteresis_W`` / ``eddy_W`` / ``excess_W``) and the
    ``k_f`` used — the CORE tile's breakdown, so the split is reported instead
    of being re-derived by whoever asks. On the surface path the TOTAL is the
    measured surface and ``eddy_W`` is still the honest ⟨(dB/dt)²⟩ series
    integral; the hysteresis/excess pair is what is left over, divided in the
    Bertotti fit's proportion, and ``terms['model']`` says so.

    ``n_periods`` is how many electrical periods the captured window spans —
    the DFT needs it to put harmonic ``m`` at ``m·f_elec/n_periods`` instead of
    at ``m·f_elec``. It only matters on the surface path.
    """
    if material is None or idx.size == 0 or len(hist_x) == 0:
        return np.zeros(n_frames), 0.0
    X = np.asarray(hist_x)
    Y = np.asarray(hist_y)
    if X.size == 0 or np.asarray(hist_x[0]).size == 0:
        return np.zeros(n_frames), 0.0

    kh, kc, ke = bertotti(material)
    kf = float(getattr(material, "stacking_factor", DEFAULT_STACKING_FACTOR))
    kf = kf if kf > 0 else 1.0
    # Only the steel dissipates; the inter-laminate insulation is dead volume.
    vol_steel = areas[idx] * stack_length_m * kf
    # …and the flux the insulation cannot carry is crowded into that steel.
    inv_kf = 1.0 / kf

    dX = ddt(X) * inv_kf
    dY = ddt(Y) * inv_kf
    classical = (kc / TWO_PI_SQ) * np.sum(
        (dX ** 2 + dY ** 2) * vol_steel[None, :], axis=1)

    # Peak-to-peak / 2 per element over the captured window — the AC amplitude
    # the per-cycle terms are defined on, in the STEEL.
    Bac2 = ((((X.max(0) - X.min(0)) * 0.5) ** 2
             + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)) * inv_kf ** 2
    hyst = float(np.sum(kh * f_elec_hz * Bac2 * vol_steel))
    excess = float(np.sum(
        ke * f_elec_hz ** 1.5 * np.power(np.maximum(Bac2, 0.0), 0.75) * vol_steel))
    model = "bertotti"
    # What the three coefficients would have said, kept whole — it is the
    # reported comparison on the surface path, not an intermediate.
    P_bert = float(np.mean(classical)) + hyst + excess

    # ── MEASURED SURFACE — replaces the total, keeps the eddy series ──────
    surface = _get_surface(material, (kh, kc, ke))
    if surface is not None:
        model = "measured_surface"
        exc: dict = {}
        fund: list = []
        wrap: dict = {}
        # X/Y are ALREADY restricted to this half's iron elements — the
        # classical term above broadcasts them straight against vol_steel.
        dens = surface_loss_density(
            X, Y, surface, inv_kf, f_elec_hz, n_periods,
            weight=vol_steel, excursion=exc, fundamental_only=fund, wrap=wrap)
        P_surf = float(np.sum(dens * vol_steel))
        P_fund = float(np.sum(fund[0] * vol_steel)) if fund else 0.0
        P_eddy = float(np.mean(classical))
        # The time RIPPLE of the iron loss is the classical term — it is the
        # only part that is instantaneous.  Keep that series as the shape and
        # put the rest of the measured total in the per-cycle bucket, so the
        # reported total IS the surface and P_fe(t) still ripples with the
        # teeth passing.  If the surface came out below the honest eddy
        # integral (it should not — a real steel has hysteresis), rescale the
        # series instead of reporting a negative remainder, and say so.
        if P_surf <= 0.0:
            # Every harmonic of this half is below HARMONIC_FLOOR_T — a half
            # with no resolvable AC field at all. Nothing to bill on top of the
            # (equally negligible) eddy series; the alternative is a NEGATIVE
            # per-cycle remainder.
            rest = 0.0
        elif P_eddy > P_surf:
            log.warning(
                "core loss | %s: the measured surface (%.4g W) came out BELOW "
                "the classical dB/dt integral (%.4g W) — the eddy series is "
                "scaled to the surface total and the per-cycle remainder is 0. "
                "Check k_c against the table.",
                getattr(material, "name", "?"), P_surf, P_eddy)
            classical = classical * (P_surf / P_eddy)
            P_eddy, rest = P_surf, 0.0
        else:
            rest = P_surf - P_eddy
        # hysteresis / excess are not separable from a measured TOTAL; the
        # split shown is the fit's proportion of the remainder, and terms
        # ['model'] carries that caveat downstream.
        share = hyst / (hyst + excess) if (hyst + excess) > 0 else 1.0
        hyst, excess = rest * share, rest * (1.0 - share)
        _log_surface(material, surface, exc, P_surf, P_fund, P_bert, wrap)
        if terms is not None:
            terms.update({
                "hysteresis_W": hyst, "excess_W": excess, "eddy_W": P_eddy,
                "k_f": kf, "model": model,
                "surface_W": P_surf, "fundamental_only_W": P_fund,
                "bertotti_W": P_bert,
                "wrap_jump_frac": wrap.get("frac", 0.0),
                "wrap_guard_weight": wrap.get("weight", 0.0),
                "envelope_out_frac": (exc.get("w_out", 0.0)
                                      / max(exc.get("w_all", 0.0), 1e-30))})
        return classical, rest

    if terms is not None:
        terms.update({"hysteresis_W": hyst, "excess_W": excess,
                      "eddy_W": float(np.mean(classical)), "k_f": kf,
                      "model": model})
    return classical, hyst + excess


def _log_surface(material: Any, surface: Any, exc: dict, P_surf: float,
                 P_fund: float, P_bert: float, wrap: dict) -> None:
    """ONE line per half per solve: what the surface did, and what left it.

    Loud on purpose when a solve leaves the measured envelope — outside it the
    answer is a blend into an extrapolation, which is a different epistemic
    object from an interpolated measurement, and the user is entitled to know
    which one their watts came from.
    """
    frac = exc.get("w_out", 0.0) / max(exc.get("w_all", 0.0), 1e-30)
    head = ("core loss | %s: measured P(B,f) surface, %d points over %d "
            "frequency curves | %.4g W (harmonic sum) vs %.4g W (fundamental "
            "only, %+.1f %%) vs %.4g W (3-coefficient Bertotti, %+.1f %%)"
            % (getattr(material, "name", "?"), surface.n_points,
               surface.f.size, P_surf, P_fund,
               100.0 * (P_surf / max(P_fund, 1e-30) - 1.0), P_bert,
               100.0 * (P_surf / max(P_bert, 1e-30) - 1.0)))
    # The capture window's own periodicity — the rotor half's is poor by
    # construction (slot passing is incommensurate with the electrical period),
    # and the number below says how much ramp the leakage guard had to remove.
    if wrap.get("weight", 0.0) > 0.02:
        log.warning(
            "core loss | %s: the captured window does NOT close on itself — "
            "end-to-start step is %.0f %% of peak-to-peak (p90 %.0f %%), so "
            "the leakage guard removed it as a ramp on %.0f %% of the field "
            "(mean weight). That makes this half's harmonic sum a LOWER "
            "bracket: the removed drift is real flux the window is too short "
            "to resolve. Slot passing is incommensurate with the electrical "
            "period on a rotor half — this is expected there and nowhere else.",
            getattr(material, "name", "?"), 100.0 * wrap.get("frac", 0.0),
            100.0 * wrap.get("p90", 0.0), 100.0 * wrap["weight"])
    if frac <= 0.0:
        log.info("%s | entirely INSIDE the measured envelope", head)
        return
    log.warning(
        "%s | OUTSIDE the measured envelope: %.2f %% of these WATTS came from "
        "the extrapolation, not the table; the worst point is B = %.3f T at "
        "f = %.4g Hz where the table reaches %.3f T (measured %.4g-%.4g Hz) — "
        "blended into the three-coefficient Bertotti extrapolation there",
        head, 100.0 * frac, exc.get("B", 0.0), exc.get("f", 0.0),
        exc.get("B_env", 0.0), exc.get("f_lo", 0.0), exc.get("f_hi", 0.0))


def central_difference(dt_s: float) -> Callable[[np.ndarray], np.ndarray]:
    """Periodic central difference — for a field already smooth in time (P2)."""
    def _ddt(X: np.ndarray) -> np.ndarray:
        return (np.roll(X, -1, 0) - np.roll(X, 1, 0)) / (2.0 * dt_s)
    return _ddt


# ─────────────────────────────────────────────────────────────────────────────
# MAGNET AXIAL SEGMENTATION — the 3-D thing a 2-D magnet loss cannot know
# ─────────────────────────────────────────────────────────────────────────────
# ``magnet_lamination`` (mm) is the AXIAL slice length a magnet is cut into.
# It reached the CAD, the masses and the validation and stopped there: BOTH
# routes to the magnet eddy loss — the coupled σ·∂A/∂t solve and the
# frequency-domain ``honest_rotor_eddy`` — are 2-D, i.e. they describe an
# axially INFINITE magnet whose induced current closes at infinity.  Cutting the
# magnet into slices is the single most effective way to kill that loss in a
# real machine, and neither model could see it.  These two functions are the
# correction, and they are a MODEL, not a solve (see below).

#: Below this argument ``1 − tanh(x)/x`` loses its leading digits to
#: cancellation, so the series x²/3 − 2x⁴/15 is used instead.
_RN_SERIES_X = 1e-3


def russell_norsworthy_factor(slice_len: float, width: float) -> float:
    """Resistance-limited END-EFFECT factor of ONE axial slice.  Dimensionless.

        k(l, w) = 1 − tanh(x)/x,      x = π·l / (2·w)

    ``slice_len`` and ``width`` are any ONE consistent length unit (only their
    ratio is used).

    WHERE IT COMES FROM.  Russell & Norsworthy, "Eddy currents and wall losses
    in screened-rotor induction motors", Proc. IEE 105A (1958) 163, derived for
    a conducting sheet of finite axial length carrying an axial current sheet
    that has to turn round and come back inside the sheet's own transverse
    span.  With no end ring / overhang their factor collapses to the form above,
    where the transverse span is the half-wavelength of the current pattern.  A
    segmented magnet is the same electrical picture: inside one slice the eddy
    current runs axially up one side of the block and back down the other, and
    the return path across the block's WIDTH is pure added resistance the 2-D
    model (l → ∞, return at infinity) never charged for.  The same expression is
    the standard segmentation correction in the magnet-loss literature
    (e.g. Ruoho et al., IEEE Trans. Magn. 45(10) 2009; Yamazaki & Fukushima,
    IEEE Trans. Ind. Appl. 47(2) 2011).

    ASYMPTOTES — the two ends this has to get right, and both are tested:

      * ``l/w → ∞``  →  ``k → 1 − 2w/(πl) → 1``.  A long slice is the 2-D
        answer: the end return path is a vanishing share of the loop.
      * ``l/w → 0``  →  ``tanh(x)/x → 1 − x²/3``, so ``k → x²/3 =
        (π·l/(2w))²/3``, i.e. k ∝ l².  Cut a magnet into N equal slices and the
        loss falls as 1/N² — the well-known resistance-limited segmentation
        scaling, recovered here rather than asserted.
      * monotone increasing in ``l`` and never outside [0, 1].

    RESISTANCE-LIMITED means the reaction field of the eddy current itself is
    neglected in the END correction (the slice is assumed thin against the skin
    depth in the axial sense).  The screening that DOES matter in-plane is
    already in the field solve this factor multiplies, so the two do not
    double-count; but on a very large, very conductive magnet at high frequency
    this factor is an over-estimate of the reduction.
    """
    w = float(width)
    l = float(slice_len)
    if w <= 0.0 or l <= 0.0:
        return 0.0
    x = math.pi * l / (2.0 * w)
    if x < _RN_SERIES_X:
        return (x * x) / 3.0 - 2.0 * x ** 4 / 15.0
    if x > 30.0:                       # tanh(30) = 1 to 1e-26
        return 1.0 - 1.0 / x
    return 1.0 - math.tanh(x) / x


def magnet_segmentation_factor(slice_mm: float, width_mm: float,
                               stack_mm: float) -> float:
    """What the 2-D magnet eddy loss must be MULTIPLIED by for a sliced magnet.

        factor = k(l, w) / k(L, w)          0 < l ≤ L
        factor = 1                          l = 0  (the SOLID marker)

    with ``k`` the Russell-Norsworthy factor above, ``l`` = ``magnet_lamination``
    (the axial slice length, mm), ``L`` = the stack length (mm) and ``w`` = the
    eddy loop's characteristic transverse width (see ``char_width_m``).

    WHY THE RATIO AND NOT ``k(l)`` ALONE.  ``k(L)`` is the end-effect a SOLID
    magnet of this stack already has — the 2-D solve does not apply it either,
    so the reported solid loss carries a known 1/k(L) over-read (≈ 1.4× on the
    150 mm's 16 mm-wide, 35 mm-long magnet).  Billing the segmented magnet at
    ``k(l)`` while the solid one is billed at 1 would charge the end effect ONCE
    on one design and TWICE on the other, and the ratio between two designs is
    the number this product is read for.  Normalising by ``k(L)`` leaves the
    SAME axially-infinite over-read on both, so:

      * a solid magnet (``magnet_lamination = 0``) is bit-identical to what this
        code reported before this function existed — deliberately, and pinned;
      * one slice as long as the stack (``l = L``) also returns exactly 1, so
        the marker and the physics agree at the boundary rather than stepping;
      * the SEGMENTED/SOLID ratio is the honest one.

    Correcting the remaining 1/k(L) is a 3-D end-effect question and belongs to
    the 3-D static program, not here.

    THIS IS A MODEL, PENDING 3-D VALIDATION.  Nothing in this repository has
    solved a magnet's axial current return; the factor is a closed form fitted
    to a rectangular sheet, applied to a bread-loaf polygon through one
    characteristic width, and it neglects (a) the reaction field of the axial
    return current, (b) the slice-to-slice coupling through the (small) gaps,
    (c) any circulating current through a conductive bond line between slices,
    and (d) the difference between the flux pattern over a wide magnet and the
    single half-wavelength the derivation assumes.  Every one of those pushes
    the true loss ABOVE this estimate, so the number is a LOWER bracket on a
    segmented magnet's loss.  Treat it as the direction and the order of
    magnitude, not the third digit, until a 3-D solve has been run against it.
    """
    l = float(slice_mm or 0.0)
    L = float(stack_mm or 0.0)
    w = float(width_mm or 0.0)
    if l <= 0.0 or L <= 0.0 or w <= 0.0:
        return 1.0                      # 0 = SOLID; no geometry = no claim
    l = min(l, L)                       # a slice cannot be longer than the stack
    k_L = russell_norsworthy_factor(L, w)
    if k_L <= 0.0:
        return 1.0
    return min(russell_norsworthy_factor(l, w) / k_L, 1.0)


def char_width_m(xy: np.ndarray) -> float:
    """The eddy loop's characteristic transverse width of one magnet polygon.

    ``xy`` is that magnet's point cloud, shape (2, N) — its mesh nodes or its
    CAD polygon vertices, in metres.  Returns the LARGER of the two principal
    in-plane extents, in the same unit.

    Principal axes (the eigenvectors of the point cloud's covariance) rather
    than a bounding box in x/y: a magnet sits at whatever angle its pole does,
    and an axis-aligned box of a 45°-rotated block reads √2 too wide.  The
    LARGER extent is taken because the eddy loop the axial current closes is the
    one enclosing the most flux, and on both live machines (a bread-loaf surface
    magnet) that is the long chord of the block.  A magnet whose two extents are
    close makes the choice immaterial; one that is much deeper than it is wide
    (a buried/spoke magnet) is served by the same rule for the same reason.
    """
    P = np.asarray(xy, float)
    if P.ndim != 2 or P.shape[0] != 2 or P.shape[1] < 3:
        return 0.0
    Q = P - P.mean(axis=1, keepdims=True)
    # 2x2 covariance -> orthonormal principal axes; eigh is exact enough here
    # and cannot return complex axes for a symmetric matrix.
    _, V = np.linalg.eigh(Q @ Q.T)
    ext = [float(np.ptp(V[:, i] @ Q)) for i in (0, 1)]
    return max(ext)


def magnet_segmentation(geo: dict, points_by_body: Sequence[np.ndarray],
                        stack_length_m: float) -> Tuple[float, dict]:
    """(factor, report) for the whole magnet set — the solver's one entry point.

    ``points_by_body`` is one (2, N) point cloud per magnet body (mesh nodes of
    that body's elements, in metres).  Each body gets its OWN width and its own
    factor; the returned factor is their AREA-BLIND arithmetic mean, because the
    per-body loss split is not exposed by either eddy route and every magnet on
    a symmetric rotor is the same block anyway.  The report carries the spread,
    so a rotor with genuinely different magnets says so instead of hiding behind
    a mean.

    ``report`` is what the result dict and the card's tooltip quote: ``slice_mm``,
    ``width_mm`` (mean), ``width_min_mm``/``width_max_mm``, ``factor``,
    ``n_bodies``, and ``model``.
    """
    slice_mm = float(geo.get("magnet_lamination", 0.0) or 0.0)
    L_mm = float(stack_length_m) * 1e3
    widths = [char_width_m(P) * 1e3 for P in (points_by_body or [])]
    widths = [w for w in widths if w > 0.0]
    rep = {"slice_mm": slice_mm, "stack_mm": round(L_mm, 4),
           "n_bodies": len(widths), "factor": 1.0,
           "width_mm": 0.0, "width_min_mm": 0.0, "width_max_mm": 0.0,
           "model": ("Russell-Norsworthy axial segmentation, normalised to the "
                     "solid stack — MODEL, pending 3-D validation")}
    if not widths:
        return 1.0, rep
    rep["width_mm"] = round(float(np.mean(widths)), 4)
    rep["width_min_mm"] = round(float(np.min(widths)), 4)
    rep["width_max_mm"] = round(float(np.max(widths)), 4)
    if slice_mm <= 0.0:
        rep["model"] = "solid magnet (magnet_lamination = 0) — 2-D loss unchanged"
        return 1.0, rep
    f = float(np.mean([magnet_segmentation_factor(slice_mm, w, L_mm)
                       for w in widths]))
    rep["factor"] = float("%.6g" % f)
    return f, rep


def copper_ac_dims(geo: dict, coil_temp_c: float, f_elec_hz: float,
                   rho_cu_20: float, alpha_cu: float, mu0: float
                   ) -> Tuple[float, float, float]:
    """Conductor dimensions the proximity loss sees, and the conductivity.

    Returns ``(sigma, d_radial, d_tangential)`` in SI.

    Two caps, whichever bites first:
      * ``wire_split`` — the wide flat bar is wound as N insulated, transposed
        strips across its WIDTH, so the width-direction loops see w/N and that
        loss term falls as N^2. Assumes ideal transposition (no circulating
        current between strips).
      * two skin depths — beyond that the field does not reach the middle of the
        conductor and a larger dimension buys no extra loss.

    ONLY THE MODELLED PATH SEES ``wire_split``.  These dimensions feed
    ``proximity_loss_series``, i.e. the copper AC term reported when the coupled
    σ·∂A/∂t solve did NOT run.  When it DID run, the reported Cu AC is the
    solved ∫σE² of the conductor polygons the mesher was given — and the mesher
    is given the whole bar, because ``wire_split`` is an electrical subdivision
    (insulated, transposed strips) with no CAD geometry behind it.  So the
    solved value models SOLID drawn conductors and is an OVER-read by up to
    ``wire_split²`` on the width-direction term.  That is not fixed by changing
    the number here — it needs the strips in the mesh — so it is REPORTED
    instead: ``routes/simulation`` flags it on the summary and the Stranded
    (copper) tile's tooltip says so.
    """
    rho = rho_cu_20 * (1.0 + alpha_cu * (float(coil_temp_c) - 20.0))
    omega = 2.0 * math.pi * max(1e-6, float(f_elec_hz))
    delta = math.sqrt(2.0 * rho / (omega * mu0))
    n_split = max(1, int(round(float(geo.get("wire_split", 1) or 1))))
    d_r = min(float(geo.get("wire_width", 5.0)) * 1e-3 / n_split, 2.0 * delta)
    d_t = min(float(geo.get("wire_height", 0.8)) * 1e-3, 2.0 * delta)
    return 1.0 / rho, d_r, d_t


def proximity_loss_series(
    hist_x: Sequence,
    hist_y: Sequence,
    idx: np.ndarray,
    centroids: np.ndarray,
    areas: np.ndarray,
    sigma: float,
    d_for_Br: float,
    d_for_Bt: float,
    stack_length_m: float,
    n_frames: int,
    ddt: Callable[[np.ndarray], np.ndarray],
    scale: float = 1.0,
    post: Optional[Callable[[np.ndarray], np.ndarray]] = None,
) -> Tuple[list, float]:
    """Proximity/skin loss in a SOLID conductor, field split by direction.

        P = sigma/12 * sum( d_r^2 * (dB_r/dt)^2 + d_t^2 * (dB_t/dt)^2 ) * V

    The split matters. Pairing each field component with the conductor dimension
    PERPENDICULAR to it — B_r with the tangential width, B_theta (slot leakage)
    with the radial height — avoids the single-d slab over-count: a tall thin bar
    barely sees the tangential leakage field, and treating it as a cube says
    otherwise.

    ``scale`` multiplies up from the modelled sector to the whole machine.
    ``post`` is the caller's outlier treatment (P1 clips to median +- 5 MAD to
    catch a single bad frame; P2's field is smooth and only needs a floor at 0).
    """
    if sigma <= 0.0 or idx.size == 0 or len(hist_x) == 0:
        return [0.0] * n_frames, 0.0
    X = np.asarray(hist_x)
    Y = np.asarray(hist_y)
    if X.size == 0 or np.asarray(hist_x[0]).size == 0:
        return [0.0] * n_frames, 0.0

    r = np.hypot(centroids[0], centroids[1])
    r = np.where(r < 1e-9, 1e-9, r)
    ux = (centroids[0] / r)[None, :]
    uy = (centroids[1] / r)[None, :]
    Br = X * ux + Y * uy                       # radial
    Bt = -X * uy + Y * ux                      # tangential
    vol = areas[idx] * stack_length_m
    Pt = (sigma / 12.0) * np.sum(
        (d_for_Br ** 2 * ddt(Br) ** 2 + d_for_Bt ** 2 * ddt(Bt) ** 2)
        * vol[None, :], axis=1) * scale
    Pt = (post or (lambda a: np.maximum(a, 0.0)))(Pt)
    return Pt.tolist(), float(np.mean(Pt))


def declip(a: np.ndarray) -> np.ndarray:
    """Safety net: clip any residual single-frame outlier to median±5·MAD.

    The P1 path's outlier treatment, passed to ``proximity_loss_series`` as
    ``post`` and applied by hand to every other P1 loss series. It is here
    rather than inline in the solver so that "which series are de-clipped" is
    answerable by grep instead of by reading a 3000-line function.
    """
    a = np.asarray(a, float)
    if a.size < 5:
        return a
    med = float(np.median(a)); mad = float(np.median(np.abs(a - med)))
    if mad <= 0:
        return a
    return np.clip(a, max(0.0, med - 5 * mad), med + 5 * mad)


def loss_density_map(
    *,
    n_stator_elems: int,
    n_elems: int,
    hist_sx, hist_sy, hist_rx, hist_ry, hist_mx, hist_my, hist_cx, hist_cy,
    iron_s_idx: np.ndarray, iron_r_idx: np.ndarray,
    mag_idx: np.ndarray, coil_idx: np.ndarray,
    areas_s: np.ndarray, areas_r: np.ndarray, coil_centroids: np.ndarray,
    steel_s: Any, steel_r: Any, bertotti: Callable[[Any], Tuple[float, float, float]],
    f_elec_hz: float, stack_length_m: float, sector_scale: float,
    P_fe_avg: float, P_mag_avg: float, P_cu_dc: float, P_cu_ac_avg: float,
    sigma_cu: float, d_cu_r: float, d_cu_t: float,
    ddt: Callable,
    solved_dens: Optional[np.ndarray] = None,
    solved_groups: Sequence[str] = (),
    solved_elems: Optional[dict] = None,
    P_cu_end_winding_W: float = 0.0,
    log_line: Optional[Callable[[str], None]] = None,
    n_periods: float = 1.0,
) -> Tuple[np.ndarray, str, list]:
    """Per-element loss DENSITY (W/m³) for the Ansys-style spatial map.

    Returns ``(density, label, unmodelled)`` — the label says, in the picture's
    own words, which component came from where, and ``unmodelled`` lists the
    material classes no model produced a value for, so the view can leave them
    BLANK instead of painting them the bottom of the scale (which on a loss map
    is what air looks like, i.e. "no loss here").  ``"air"`` is ALWAYS in that
    list — there is no air-loss model in a 2-D magnetic solve (σ=0, no
    hysteresis, no windage), so the air gap must be blank, never band 0.

    TWO sources, never mixed inside one component:

    * ``solved_dens`` — the cycle-averaged per-element σE² of the COUPLED
      σ·∂A/∂t solve, for whichever of ``solved_groups`` ⊆ {cu, mag, shaft} the
      run actually solved.  This is the density itself, not a shape: it is
      taken UNRENORMALISED, because renormalising a measured quantity to a
      number derived from it can only add error.  It is also the only way the
      map can show the corner/edge crowding an Ansys Total-Loss plot shows —
      the eddy current concentrates where the conductor faces the changing
      field, and no per-body average knows that.
    * everything else — the modelled shapes (Bertotti for iron always; the slab
      |dB/dt|² magnet term and the proximity copper term when the coupled solve
      did not run), each NORMALISED so its volume-integral equals the reported
      component loss.  A shape normalised to a total is smooth by construction;
      the label says so.

    ``P_cu_end_winding_W`` is the DC copper loss OUTSIDE the modelled plane
    (the end turns).  It is real, the sidebar reports it, and the 2-D map has
    nowhere honest to put it — so it is spread uniformly over the copper and
    called out in the label, rather than being folded into the solved σE²
    shape as if the end turns crowded like the active length does.

    Element order matches the field snapshot: [stator-half | rotor-half].
    """
    _nst_e = int(n_stator_elems)
    _dens = np.zeros(int(n_elems))
    _solved = {str(g) for g in (solved_groups or ())}
    _sel = solved_elems or {}
    _parts = []                       # label fragments, in draw order

    def _log(msg: str) -> None:
        if log_line is not None:
            log_line(msg)

    # Element areas in the SNAPSHOT's global order, so a cross-check can be
    # written once against global element ids instead of once per half.
    _areas_all = np.concatenate([np.asarray(areas_s, float),
                                 np.asarray(areas_r, float)])

    def _integ_glob(glob_idx) -> float:
        """Volume integral [W, machine] of the map over global element ids."""
        if glob_idx is None or np.size(glob_idx) == 0:
            return 0.0
        gi = np.asarray(glob_idx, int)
        return float(np.sum(_dens[gi] * _areas_all[gi])) \
            * stack_length_m * sector_scale

    def _mean_sq_ddt(hx, hy, qp=None):              # time-avg |dB/dt|² per elem
        dX = ddt(np.asarray(hx), qp)
        dY = ddt(np.asarray(hy), qp)
        return np.mean(dX ** 2 + dY ** 2, axis=0)

    def _bac2(hx, hy):                              # (½ peak-peak)² per elem
        X = np.asarray(hx); Y = np.asarray(hy)
        return (((X.max(0) - X.min(0)) * 0.5) ** 2
                + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)

    def _norm_into(local_idx, shape_e, areas_half, base, P_target_W):
        if local_idx.size == 0 or shape_e.size == 0 or P_target_W <= 0:
            return
        integ = float(np.sum(shape_e * areas_half[local_idx])) * stack_length_m * sector_scale
        if integ > 1e-30:
            _dens[base + local_idx] += shape_e * (P_target_W / integ)

    # Iron — stator + rotor share one Bertotti total (P_fe_avg).
    # Per GEOMETRIC volume, with the same k_f algebra ``iron_loss_series`` uses:
    # the steel carries B/k_f and occupies k_f of the section, so the B^2 terms
    # carry 1/k_f and the excess term 1/sqrt(k_f).  The shape is normalised to
    # the reported watts, so this does not move the total — it moves the SPLIT
    # between stator and rotor whenever the two halves are different steels
    # (different k_f), which is exactly what the picture is read for.
    def _iron_shape(hx, hy, idx, mat, qp=None):
        if (mat is None or idx.size == 0 or not np.size(hx)
                or np.asarray(hx[0]).size == 0):
            return np.zeros(idx.size)
        kh, kc, ke = bertotti(mat)
        kf = float(getattr(mat, "stacking_factor", DEFAULT_STACKING_FACTOR))
        kf = kf if kf > 0 else 1.0
        surface = _get_surface(mat, (kh, kc, ke))
        if surface is not None:
            # Same model as the reported watts, so the PICTURE and the NUMBER
            # are one thing.  The shape is normalised to the total below, so
            # this cannot move P_fe — it moves the stator/rotor split and the
            # tooth-vs-yoke contrast, which is what the map is read for, and
            # those DO change when a saturating tooth stops obeying B².
            return kf * surface_loss_density(
                np.asarray(hx, float), np.asarray(hy, float), surface,
                1.0 / kf, f_elec_hz, n_periods)
        b2 = _bac2(hx, hy) / kf ** 2
        return kf * (kh * f_elec_hz * b2
                     + ke * f_elec_hz ** 1.5 * np.power(np.maximum(b2, 0.0), 0.75)
                     + (kc / TWO_PI_SQ) * _mean_sq_ddt(hx, hy, qp) / kf ** 2)
    _sh_is = _iron_shape(hist_sx, hist_sy, iron_s_idx, steel_s)
    _sh_ir = _iron_shape(hist_rx, hist_ry, iron_r_idx, steel_r)
    _integ_fe = ((float(np.sum(_sh_is * areas_s[iron_s_idx])) if iron_s_idx.size else 0.0)
                 + (float(np.sum(_sh_ir * areas_r[iron_r_idx])) if iron_r_idx.size else 0.0)
                 ) * stack_length_m * sector_scale
    if _integ_fe > 1e-30 and P_fe_avg > 0:
        _kfe = P_fe_avg / _integ_fe
        if iron_s_idx.size: _dens[iron_s_idx] += _sh_is * _kfe
        if iron_r_idx.size: _dens[_nst_e + iron_r_idx] += _sh_ir * _kfe
        _parts.append("iron: Bertotti shape normalised to %.3g W" % P_fe_avg)
        _fe_glob = np.concatenate([np.asarray(iron_s_idx, int),
                                   _nst_e + np.asarray(iron_r_idx, int)])
        _fe_int = _integ_glob(_fe_glob)
        _log("loss map | iron   : ∫ = %.4g W vs reported %.4g W (%+.3f %%) "
             "[Bertotti shape, normalised]"
             % (_fe_int, P_fe_avg,
                100.0 * (_fe_int / max(P_fe_avg, 1e-30) - 1.0)))

    # ── Magnets ───────────────────────────────────────────────────────────
    _mag_glob = ((_nst_e + np.asarray(mag_idx, int)) if np.size(mag_idx)
                 else np.array([], int))
    if "mag" in _solved and solved_dens is not None:
        _gi = np.asarray(_sel.get("mag", _mag_glob), int)
        _dens[_gi] += np.asarray(solved_dens, float)[_gi]
        _parts.append("magnets: solved σE² (coupled σ·∂A/∂t), unrenormalised")
        _log("loss map | magnet : ∫ = %.4g W vs reported %.4g W (%+.3f %%) "
             "[per-element σE², NOT renormalised]"
             % (_integ_glob(_gi), P_mag_avg,
                100.0 * (_integ_glob(_gi) / max(P_mag_avg, 1e-30) - 1.0)))
    elif np.size(mag_idx) and np.size(hist_mx) and np.asarray(hist_mx[0]).size:
        _norm_into(np.asarray(mag_idx, int), _mean_sq_ddt(hist_mx, hist_my),
                   areas_r, _nst_e, P_mag_avg)
        _parts.append("magnets: slab |dB/dt|² shape normalised to %.3g W" % P_mag_avg)
        _log("loss map | magnet : ∫ = %.4g W vs reported %.4g W (%+.3f %%) "
             "[slab |dB/dt|² shape, normalised — smooth by construction]"
             % (_integ_glob(_mag_glob), P_mag_avg,
                100.0 * (_integ_glob(_mag_glob) / max(P_mag_avg, 1e-30) - 1.0)))

    # ── Shaft ─────────────────────────────────────────────────────────────
    # Only the coupled solve produces a shaft map at all: the frequency-domain
    # rotor solve reports a shaft WATT with no per-element field behind it, so
    # before this there was simply no shaft component in the picture.
    if "shaft" in _solved and solved_dens is not None and _sel.get("shaft") is not None:
        _gi = np.asarray(_sel["shaft"], int)
        if _gi.size:
            _dens[_gi] += np.asarray(solved_dens, float)[_gi]
            _parts.append("shaft: solved σE²")
            _log("loss map | shaft  : ∫ = %.4g W [per-element σE², "
                 "NOT renormalised]" % _integ_glob(_gi))

    # ── Copper ────────────────────────────────────────────────────────────
    _cu_glob = np.asarray(coil_idx, int)
    if _cu_glob.size:
        _vol_cu = float(np.sum(areas_s[_cu_glob])) * stack_length_m * sector_scale
        if "cu" in _solved and solved_dens is not None:
            _gi = np.asarray(_sel.get("cu", _cu_glob), int)
            _dens[_gi] += np.asarray(solved_dens, float)[_gi]
            _p_act = _integ_glob(_gi)
            # End turns: real watts, outside the modelled plane.  Uniform over
            # the copper and SAID so — not smeared into the solved shape.
            if _vol_cu > 1e-30 and P_cu_end_winding_W > 0:
                _dens[_cu_glob] += P_cu_end_winding_W / _vol_cu
            _parts.append(
                "copper: solved σE² (active length)"
                + (" + uniform end-winding DC %.3g W"
                   % P_cu_end_winding_W if P_cu_end_winding_W > 0 else ""))
            _log("loss map | copper : ∫ = %.4g W = solved active-length σE² "
                 "%.4g W + uniform end-winding %.4g W; reported copper %.4g W "
                 "(%+.3f %%)"
                 % (_integ_glob(_cu_glob), _p_act, P_cu_end_winding_W,
                    P_cu_dc + P_cu_ac_avg,
                    100.0 * (_integ_glob(_cu_glob)
                             / max(P_cu_dc + P_cu_ac_avg, 1e-30) - 1.0)))
        else:
            # Uniform DC ohmic + crowded AC proximity (radial/tangential).
            if _vol_cu > 1e-30 and P_cu_dc > 0:
                _dens[_cu_glob] += P_cu_dc / _vol_cu
            if (np.size(hist_cx) and np.asarray(hist_cx[0]).size
                    and P_cu_ac_avg > 0):
                _Xc = np.asarray(hist_cx); _Yc = np.asarray(hist_cy)
                _rc = np.hypot(coil_centroids[0], coil_centroids[1])
                _rc = np.where(_rc < 1e-9, 1e-9, _rc)
                _uxc = (coil_centroids[0] / _rc)[None, :]; _uyc = (coil_centroids[1] / _rc)[None, :]
                _dBrc = ddt(_Xc * _uxc + _Yc * _uyc)
                _dBtc = ddt(-_Xc * _uyc + _Yc * _uxc)
                _sh_cu = (sigma_cu / 12.0) * np.mean(
                    d_cu_r ** 2 * _dBrc ** 2 + d_cu_t ** 2 * _dBtc ** 2, axis=0)
                _norm_into(_cu_glob, _sh_cu, areas_s, 0, P_cu_ac_avg)
            _parts.append("copper: uniform DC I²R + proximity AC shape "
                          "normalised to %.3g W" % P_cu_ac_avg)
            _log("loss map | copper : ∫ = %.4g W vs reported %.4g W (%+.3f %%) "
                 "[uniform DC + normalised proximity AC]"
                 % (_integ_glob(_cu_glob), P_cu_dc + P_cu_ac_avg,
                    100.0 * (_integ_glob(_cu_glob)
                             / max(P_cu_dc + P_cu_ac_avg, 1e-30) - 1.0)))

    # ── components that are NOT IN THIS MAP ──────────────────────────────
    # A component whose model produced nothing — the magnet term when the run
    # had neither the coupled eddy solve nor the frequency-domain rotor solve,
    # so P_mag_avg came through as 0 — used to be normalised to zero watts and
    # painted at the bottom of the scale.  On a loss map the bottom of the scale
    # is what AIR looks like, so "we did not model this" rendered as "there is
    # no loss here", three times in a row, in the one component the user was
    # looking for.  Naming it here lets the view grey the material out and say
    # what is missing instead of quietly showing a zero.
    # ── invariant: nothing outside a modelled MATERIAL carries loss ───────
    # Every write above is indexed by a material's element ids, so this can
    # only fire on an index bug — the [stator-half | rotor-half] offset being
    # applied twice, or a solved-σE² id set from a different mesh.  It is
    # checked rather than assumed because the failure is silent and PRETTY:
    # loss landing in air paints a gradient across the air gap that looks like
    # physics.  Zeroed AND logged, so the picture stays honest even when the
    # indexing is not — and zeroed BEFORE the "what is missing" pass below, so
    # a stray write cannot make an unmodelled material look modelled.
    #
    # The mask is built from the MATERIAL index arrays, deliberately NOT from
    # ``solved_elems``: a solved-σE² id set that has drifted off the material it
    # claims to be is precisely the bug this is here to catch, so it cannot also
    # be the thing that declares itself legitimate.  (The shaft is the one
    # exception — the coupled solve is its only source, so there is nothing else
    # to check it against.)
    _shaft_glob = np.asarray(_sel.get("shaft") if _sel.get("shaft") is not None
                             else [], int)
    _mod_mask = np.zeros(int(n_elems), bool)
    for _ids in (np.asarray(iron_s_idx, int),
                 _nst_e + np.asarray(iron_r_idx, int),
                 _mag_glob, _cu_glob, _shaft_glob):
        if _ids.size:
            _mod_mask[_ids] = True
    _stray = np.flatnonzero((~_mod_mask) & (_dens != 0.0))
    if _stray.size:
        _log("loss map | ERROR : %d element(s) OUTSIDE every modelled material "
             "(air / gap / band) carried loss, max %.4g W/m³ — zeroed.  This is "
             "an element-index bug in the map, not physics."
             % (int(_stray.size), float(np.max(np.abs(_dens[_stray])))))
        _dens[_stray] = 0.0

    _unmodelled = []
    if np.size(mag_idx) and not float(np.sum(_dens[_mag_glob])) > 0.0:
        _unmodelled.append("magnets")
    if _cu_glob.size and not float(np.sum(_dens[_cu_glob])) > 0.0:
        _unmodelled.append("copper")
    if (np.size(iron_s_idx) or np.size(iron_r_idx)) and not (
            P_fe_avg > 0 and _integ_fe > 1e-30):
        _unmodelled.append("iron")
    # The SHAFT has exactly one source — the coupled σ·∂A/∂t solve.  Without it
    # there is no shaft term at all, and a shaft drawn at the bottom of the
    # scale says "no loss in the shaft", which is a claim this run cannot make.
    if not (_shaft_glob.size and float(np.sum(_dens[_shaft_glob])) > 0.0):
        _unmodelled.append("shaft")
    # AIR is never modelled, in any run.  σ = 0 → no eddy current, no
    # hysteresis, and windage is not part of a 2-D magnetic solve — so there is
    # no air loss to draw, ever.  It is listed here (rather than left to be
    # inferred from a zero) because on a LOG map the bottom of the scale is a
    # COLOUR: the air gap came out painted as a smooth blue-to-cyan ring, which
    # reads as a real, small, measured loss.  Blank is the only honest fill.
    _unmodelled.append("air")
    for _u in _unmodelled:
        if _u == "air":
            _parts.append("air: no loss model exists (σ=0, no hysteresis, "
                          "windage not modelled) — left BLANK, not coloured")
            continue
        _parts.append("%s: NOT MODELLED in this run — nothing is drawn there"
                      % _u)
        _log("loss map | %-6s : NOT MODELLED (no loss model produced a value; "
             "the map leaves it blank rather than showing zero)" % _u)

    _label = ("cycle-averaged loss density — " + "; ".join(_parts)) if _parts \
        else "loss density unavailable (no loss component could be built)"
    _log("loss map | TOTAL  : ∫ = %.4g W over the whole map"
         % _integ_glob(np.arange(int(n_elems))))
    return _dens, _label, _unmodelled


def rotor_eddy_tags(cells: dict, n_tri: int, dom_mag_base: int
                    ) -> Tuple[np.ndarray, list]:
    """Per-element domain tags for the rotor half, and which of them are magnets.

    Both element orders built this by hand, identically. The coupled rotor-eddy
    solve needs a dense tag array (it assembles per-region), and the magnet list
    decides which regions get their loss reported separately from the shaft.
    """
    tags = np.zeros(int(n_tri), int)
    for tag, elems in cells.items():
        tags[np.asarray(elems, int)] = int(tag)
    mag_tags = [int(t) for t in np.unique(tags) if int(t) >= dom_mag_base]
    return tags, mag_tags


def rotor_mu_lookup(mu_back_iron: float, dom_mag_base: int, dom_rotor: int,
                    mu_magnet: float = 1.05) -> Callable[[int], float]:
    """tag -> relative permeability for the rotor-eddy solve.

    Magnets recoil at ~1.05, the back iron uses the CONVERGED nu from the run
    that just finished (a linear 1000 would put the eddy solve on a different
    machine than the transient), and everything else — shaft aluminium, air — is
    non-magnetic.
    """
    def _mu(tag: int) -> float:
        tag = int(tag)
        if tag >= dom_mag_base:
            return mu_magnet
        if tag == dom_rotor:
            return float(mu_back_iron)
        return 1.0
    return _mu
