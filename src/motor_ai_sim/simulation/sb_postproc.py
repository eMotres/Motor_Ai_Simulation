"""Post-processing of the sliding-band frame series: settling-window trimming,
the reported torque, and its harmonic spectrum.

Everything here runs AFTER the frame loop, on plain lists of per-frame numbers.
It has no view of the mesh, the field or the element order — which is exactly
why the P1 and P2 branches were able to grow their own copies of it. The hybrid
torque in particular was written twice, and the whole reason it exists is that
one of the two once reported a mean ~35 % above the other on the same machine.
One definition, used by both, is the only way that stays fixed.

Pure functions with explicit arguments, except ``drop_settling_frames``, which
trims lists in place because the frame loop's series ARE those lists.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np


def _scalar_sample(value) -> Tuple[bool, object]:
    """``(is_scalar, json_safe_value)`` for one per-frame sample.

    Python numbers, ``None``, numpy scalars and ONE-element numpy arrays (a 0-d
    array, or a ``(1,)`` slice of one) are scalars here and come back as plain
    Python values.  Anything with more than one element is not.
    """
    if isinstance(value, np.generic):     # before `float`: np.float64 IS a float
        return True, value.item()
    if value is None or isinstance(value, (bool, int, float, complex, str)):
        return True, value
    if isinstance(value, np.ndarray):
        if value.size == 1:
            return True, value.reshape(()).item()
        return False, None
    try:                                  # anything else that reads as a float
        return True, float(value)
    except (TypeError, ValueError):
        return False, None


def snapshot_scalar_history(series: Mapping[str, Sequence]) -> dict:
    """Copy scalar per-frame histories before settling prefixes are trimmed.

    The returned lists are independent of later in-place prefix deletion.  This
    helper is for compact scalar traces only: a channel whose samples are
    genuinely vectors (a field history) is NOT copied — it is left out of
    ``samples`` and named in ``skipped_series`` with the reason, so the record
    says what is missing and why.  It never raises on a sample's type: this
    runs after the frame loop on a finished solve, and a bookkeeping helper
    must not be the thing that throws that solve away.  A 0-d numpy array or a
    numpy scalar is a scalar (``float()`` of it), not a reason to skip.
    """
    samples = {}
    skipped = {}
    for name, values in series.items():
        copied = []
        reason = None
        for index, value in enumerate(values):
            ok, converted = _scalar_sample(value)
            if not ok:
                shape = getattr(value, "shape", None)
                reason = ("non-scalar sample at index %d: %s%s" % (
                    index, type(value).__name__,
                    (" shape %s" % (tuple(shape),)) if shape is not None else ""))
                break
            copied.append(converted)
        if reason is not None:
            skipped[str(name)] = reason
            continue
        samples[str(name)] = copied
    return {
        "samples": samples,
        "sample_count_by_series": {name: len(values) for name, values in samples.items()},
        "all_series_aligned": len({len(values) for values in samples.values()}) <= 1,
        "skipped_series": skipped,
    }


def retained_window_metadata(raw_history: Mapping, retained_series: Mapping[str, Sequence],
                              core_series: Sequence[str],
                              trim_operations: Sequence[Mapping],
                              nominal_retained_frames: int,
                              retained_periods: float) -> dict:
    """Describe what remains after the existing sequential settling trims.

    Core indices and absolute endpoints are provided only when all designated
    waveform channels have matching raw and retained lengths. Other channels
    retain independent counts; they are never zipped to force alignment.
    """
    raw_samples = raw_history["samples"]
    raw_counts = dict(raw_history["sample_count_by_series"])
    kept_counts = {name: len(values) for name, values in retained_series.items()}
    removed_counts = {name: raw_counts[name] - kept_counts[name]
                      for name in raw_counts if name in kept_counts}
    aligned = all(name in raw_samples and name in retained_series
                  and name in raw_counts and name in kept_counts
                  for name in core_series)
    raw_core_counts = ({raw_counts[name] for name in core_series}
                       if aligned else set())
    kept_core_counts = ({kept_counts[name] for name in core_series}
                        if aligned else set())
    aligned = (aligned and len(raw_core_counts) == 1
               and len(kept_core_counts) == 1)
    start_index = start_time = end_time = start_angle = end_angle = None
    raw_core_count = kept_core_count = None
    if aligned:
        raw_core_count = next(iter(raw_core_counts))
        kept_core_count = next(iter(kept_core_counts))
        aligned = 0 <= kept_core_count <= raw_core_count
    if aligned:
        start_index = raw_core_count - kept_core_count
        times = raw_samples.get("time_s_absolute", [])
        angles = raw_samples.get("mechanical_angle_rad", [])
        if len(times) == raw_core_count and kept_core_count:
            start_time, end_time = times[start_index], times[-1]
        if len(angles) == raw_core_count and kept_core_count:
            start_angle, end_angle = angles[start_index], angles[-1]
    return {
        "trim_operations": [dict(op) for op in trim_operations],
        # Channels the snapshot could not copy (vector samples), with the reason
        # — the warning travels with the record instead of failing the solve.
        "skipped_series": dict(raw_history.get("skipped_series") or {}),
        "nominal_retained_frames": int(nominal_retained_frames),
        "retained_periods": float(retained_periods),
        "sample_count_by_series_before_trim": raw_counts,
        "sample_count_by_series_after_trim": kept_counts,
        "removed_sample_count_by_series": removed_counts,
        "all_scalar_series_aligned_before_trim": bool(raw_history["all_series_aligned"]),
        "core_waveform_series": list(core_series),
        "core_waveform_aligned": bool(aligned),
        "raw_core_sample_count": raw_core_count,
        "retained_core_sample_count": kept_core_count,
        "retained_start_raw_sample_index": start_index,
        "retained_time_start_s_absolute": start_time,
        "retained_time_end_s_absolute": end_time,
        "retained_mechanical_angle_start_rad": start_angle,
        "retained_mechanical_angle_end_rad": end_angle,
    }


def drop_settling_frames(series: Iterable[list], n_skip: int,
                         time_series: Optional[list] = None) -> None:
    """Delete the first ``n_skip`` frames from every per-frame list, in place.

    Voltage drive and demagnetisation each prepend their own settling window and
    each strips its OWN prefix afterwards, in sequence — never double-counted.
    EVERY per-frame series has to be in ``series``: the circuit diagnostics were
    once left out, and their indices were then offset against I/psi/T, so the
    reported max residual was the SETTLING residual rather than the steady-state
    one.

    ``time_series`` is re-based to start at zero after the trim (pass the same
    list again — it must also appear in ``series`` to be trimmed).
    """
    for lst in series:
        if len(lst) > n_skip:
            del lst[:n_skip]
    if time_series:
        t0 = time_series[0]
        time_series[:] = [t - t0 for t in time_series]


def eddy_settle_resid(p_solid: Sequence[float], n_steps_per_period: int,
                      dt: float) -> Tuple[float, Optional[float]]:
    """How much START-UP TRANSIENT is LEFT at the end of an eddy warm-up march.

    ``p_solid`` is the solid-conductor sigma*E^2 power [W], one entry per warm-up
    frame, chronological; the last entry is the handoff frame (theta = -dtheta).
    Returns ``(residual, tau_s)`` — the residual as a FRACTION of the settled
    level, and the fitted decay time constant when the decay is clean enough to
    read one off (else ``None``).

    Two traps this exists to avoid:

    * "the last frame barely moved" is NOT settled.  A geometric decay with
      ratio q leaves q/(1-q) times the last step still to come: at q = 0.9 a
      2 % per-frame move means a 18 % transient is still running.  So the last
      three samples are Aitken-extrapolated to the geometric tail and what is
      REPORTED is that tail, not the step.
    * the solid loss RIPPLES with rotor position — 20 % frame to frame on a 30 mm
      machine that settled in one step, because a 3-phase machine's loss carries
      the 6th electrical harmonic.  Reading a per-frame difference as decay would
      call every machine un-settled forever.  So the samples are averaged in
      blocks of one ripple period (n_steps_per_period / 6) before the tail is
      fitted, and a "decay" whose two steps do not both point down, or whose
      ratio is not in (0, 1), is read as ripple (no decay detected) rather than
      extrapolated into a number.
    """
    p = [float(x) for x in p_solid]
    n = len(p)
    if n < 3:
        return float("inf"), None            # nothing to say yet
    w = max(1, min(int(round(n_steps_per_period / 6.0)), n // 3))
    m = [float(np.mean(p[n - (j + 1) * w: n - j * w])) for j in (2, 1, 0)]
    ref = max(abs(m[2]), 1e-30)
    d1 = m[1] - m[0]
    d2 = m[2] - m[1]
    if d1 < 0.0 and d2 < 0.0:
        q = d2 / d1
        if 0.0 < q < 1.0:
            tau = (-(w * float(dt)) / math.log(q)) if (dt and q > 0.0) else None
            # q is capped for the EXTRAPOLATION only: three block means cannot
            # tell q = 0.99 from ripple, and 1/(1-q) explodes there.  Capped, a
            # very slow decay still reports ~9x the last step, i.e. "not settled".
            qq = min(q, 0.9)
            return abs(d2) * qq / (1.0 - qq) / ref, tau
    return abs(d2) / ref, None


#: Tail ratio assumed when only TWO whole periods exist (a slower decay cannot
#: be ruled out from two means): the residual is then 9x the last change.
EDDY_PERIOD_Q_CAP = 0.9


def eddy_period_resid(groups: Mapping[str, Sequence[float]],
                      n_per_period: int,
                      floor_frac: float = 1e-3
                      ) -> Tuple[float, Dict[str, Optional[float]], int]:
    """Remaining start-up transient judged on WHOLE ELECTRICAL PERIODS, per body.

    ``groups`` maps a conductor group (magnet / shaft / sleeve / copper) to its
    solved σE² per warm-up frame [W], chronological and CONTINUOUS in time (the
    march splices whole periods with the pole-pair remap, so consecutive
    samples are consecutive steps).  The last whole periods are averaged: on
    the periodic steady state every period has the SAME mean — the angular
    ripple, the 6th harmonic, slotting, any carrier of a synchronous PWM, all
    cancel exactly inside one electrical period — so the change of the period
    mean IS the transient, with nothing left for a block average to mistake
    for decay (the defect of ``eddy_settle_resid`` on short records: three
    probe samples read "settled" on a shaft whose loss later halved).

    Per group, with ``m`` the last (up to four) period means and Δ their
    changes:
      * four or more periods: geometric tail max(|Δ|)·q/(1−q) over the last
        TWO changes, with q the LARGER of the last two ratios when all three
        changes share a sign and both ratios lie in (0, 1) (q capped at
        ``EDDY_PERIOD_Q_CAP``); otherwise the cap is assumed.  Two ratios, not
        one: a multi-mode decay can flatten for one period and steepen again
        (measured on the L155 shaft: −1.10, −0.14, then −0.5 W per period), and
        a single ratio read that flat step as "settled";
      * two or three periods: q is unknown, the tail is bounded with q = cap
        (9× the largest of the last changes);
      * fewer: infinite.
    Each group is judged against its OWN level, floored at ``floor_frac`` of
    the total solid loss (a milliwatt group beside kilowatts is judged on the
    watts it could move, not divided noise by noise).  Returns
    ``(worst_residual, per_group_residual, n_whole_periods)``.
    """
    N = max(1, int(n_per_period))
    per: Dict[str, Optional[float]] = {}
    series = {k: np.asarray(v, float) for k, v in groups.items()}
    n = min((s.size for s in series.values()), default=0)
    nP = n // N
    if nP < 2 or not series:
        return float("inf"), {k: None for k in series}, int(nP)
    use = min(nP, 4)
    means = {k: [float(np.mean(s[s.size - (j + 1) * N: s.size - j * N]))
                 for j in range(use - 1, -1, -1)] for k, s in series.items()}
    total = sum(abs(m[-1]) for m in means.values())
    worst = 0.0
    cap = EDDY_PERIOD_Q_CAP
    for k, m in means.items():
        ref = max(abs(m[-1]), floor_frac * total, 1e-30)
        d = [m[i + 1] - m[i] for i in range(len(m) - 1)]
        dmax = max(abs(d[-1]), abs(d[-2]) if len(d) >= 2 else 0.0)
        qq = cap
        if len(d) >= 3:
            same = (d[-1] * d[-2] > 0.0) and (d[-2] * d[-3] > 0.0)
            q1 = (d[-2] / d[-3]) if d[-3] != 0.0 else 1.0
            q2 = (d[-1] / d[-2]) if d[-2] != 0.0 else 1.0
            if same and 0.0 < q1 < 1.0 and 0.0 < q2 < 1.0:
                qq = min(max(q1, q2), cap)
        r = dmax * qq / (1.0 - qq) / ref
        per[k] = float(r)
        worst = max(worst, float(r))
    return worst, per, int(nP)


def terminal_work_mean(psi_a: Sequence[float], psi_b: Sequence[float],
                       psi_c: Sequence[float], i_a: Sequence[float],
                       i_b: Sequence[float], i_c: Sequence[float],
                       mechanical_angle_rad: Sequence[float], pole_pairs: int,
                       n_parallel: int = 1) -> float:
    """Return the all-bin periodic mean ``n_parallel * Σ(i * dψ/dθ_m)``.

    Samples are endpoint-excluded and equally spaced in signed mechanical
    angle. The derivative uses the full DFT of every supplied sample; for an
    even sample count, the real-grid Nyquist derivative has the usual zero
    convention because its sine quadrature is not represented by samples.
    This is a terminal-work candidate, not by itself a universal torque or
    energy-balance certification.
    """
    try:
        arrays = [np.asarray(v, dtype=float) for v in
                  (psi_a, psi_b, psi_c, i_a, i_b, i_c,
                   mechanical_angle_rad)]
    except Exception as exc:
        raise ValueError("terminal-work inputs must be numeric 1-D arrays") from exc
    if any(a.ndim != 1 for a in arrays):
        raise ValueError("terminal-work inputs must be 1-D arrays")
    sizes = {int(a.size) for a in arrays}
    if len(sizes) != 1:
        raise ValueError("terminal-work phase and angle arrays must have equal length")
    n = next(iter(sizes))
    if n < 8:
        raise ValueError("terminal-work requires at least 8 samples")
    if not all(np.all(np.isfinite(a)) for a in arrays):
        raise ValueError("terminal-work inputs must be finite")
    p, npar = int(pole_pairs), int(n_parallel)
    if p <= 0 or p != pole_pairs or npar < 1 or npar != n_parallel:
        raise ValueError("pole_pairs and n_parallel must be positive integers")

    angles = arrays[6]
    steps = np.diff(angles)
    step = float(steps[0])
    if not math.isfinite(step) or step == 0.0:
        raise ValueError("mechanical angles must have a nonzero signed step")
    step_tol = max(1e-14, abs(step) * 1e-10)
    if not np.allclose(steps, step, rtol=1e-10, atol=step_tol):
        raise ValueError("mechanical angles must be uniformly spaced")
    electrical_periods = abs(n * step * p / (2.0 * math.pi))
    nearest_periods = round(electrical_periods)
    if nearest_periods < 1 or not math.isclose(
            electrical_periods, nearest_periods, rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError(
            "mechanical-angle samples must span endpoint-excluded integer electrical periods")

    pa, pb, pc, ia, ib, ic = arrays[:6]
    omega = 2.0 * math.pi * np.fft.fftfreq(n, d=step)
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            derivative = np.fft.ifft(
                1j * omega[None, :] * np.fft.fft(
                    np.stack((pa, pb, pc)), axis=-1), axis=-1).real
            work = float(npar * np.mean(
                ia * derivative[0] + ib * derivative[1] + ic * derivative[2]))
    except (FloatingPointError, ValueError) as exc:
        raise ValueError("terminal-work arithmetic was nonfinite") from exc
    if not math.isfinite(work):
        raise ValueError("terminal-work arithmetic was nonfinite")
    return work


def _terminal_work_ineligibility(*, imposed_current_drive: bool, eddy: bool,
                                  rotor_eddy: bool, demag: bool,
                                  frozen_nu: bool,
                                  all_frames_converged: bool,
                                  integer_period_window: bool,
                                  mechanical_angle_rad: Optional[Sequence[float]]
                                  ) -> Optional[str]:
    checks = (
        (imposed_current_drive, "requires imposed-current drive"),
        (not eddy, "eddy solve is active"),
        (not rotor_eddy, "rotor eddy solve is active"),
        (not demag, "demagnetization is active"),
        (not frozen_nu, "frozen permeability is active"),
        (all_frames_converged, "one or more retained frames are unconverged"),
        (integer_period_window, "retained window is not an integer number of periods"),
        (mechanical_angle_rad is not None, "actual mechanical angles are unavailable"),
    )
    return next((reason for ok, reason in checks if not ok), None)


def space_vector_hybrid_torque(psi_a: Sequence[float], psi_b: Sequence[float],
                               psi_c: Sequence[float], i_a: Sequence[float],
                               i_b: Sequence[float], i_c: Sequence[float],
                               t_maxwell: Sequence[float], pole_pairs: int,
                               n_parallel: int = 1) -> Tuple[List[float], str]:
    """Flux-linkage (space-vector) mean + raw Maxwell AC — 68de0ca verbatim.

    The mean is ``(3/2)·p·n_parallel·<ψα·iβ − ψβ·iα>`` over the retained
    window (PER-BRANCH ψ and i; ``n_parallel`` restores the phase current).
    The AC is the raw Maxwell series, untouched. Above 1 A peak per branch
    (and with aligned terminal data) the mean replaces the Maxwell mean;
    otherwise the raw Maxwell series is returned unchanged, as it always was.
    Arithmetic, order of operations and the 1 A gate are exactly 68de0ca's, so
    every run that is not terminal-work eligible reproduces 68de0ca's mean
    bit-for-bit on the same field.
    """
    _pa = np.asarray(psi_a, float); _pb = np.asarray(psi_b, float)
    _pc = np.asarray(psi_c, float)
    _ea = np.asarray(i_a, float); _eb = np.asarray(i_b, float)
    _ec = np.asarray(i_c, float)
    _Ipk = (float(np.max(np.abs(np.concatenate([_ea, _eb, _ec]))))
            if _ea.size else 0.0)
    if _pa.size and _pa.size == _ea.size and _Ipk > 1.0:
        _s = 2.0 / 3.0; _kc = math.sqrt(3.0) / 2.0
        _psial = _s * (_pa - 0.5 * _pb - 0.5 * _pc); _psibe = _s * _kc * (_pb - _pc)
        _ial = _s * (_ea - 0.5 * _eb - 0.5 * _ec); _ibe = _s * _kc * (_eb - _ec)
        _Te = (1.5 * float(pole_pairs) * float(max(1, int(n_parallel)))
               * (_psial * _ibe - _psibe * _ial))
        _emean = float(_Te.mean())
        _mx = np.asarray(t_maxwell, float)     # raw Maxwell σ_rθ series
        return (_mx - _mx.mean() + _emean).tolist(), "energy_mean+maxwell_ripple"
    return list(t_maxwell), "maxwell_stress"


# What each selected-method string means, for the result record.
TORQUE_MEAN_SOURCE = {
    "terminal_work_mean+maxwell_ripple": "terminal_work",
    "energy_mean+maxwell_ripple": "flux_linkage_space_vector",
    "maxwell_stress": "raw_maxwell",
}


def hybrid_torque(psi_a: Sequence[float], psi_b: Sequence[float],
                  psi_c: Sequence[float], i_a: Sequence[float],
                  i_b: Sequence[float], i_c: Sequence[float],
                  t_maxwell: Sequence[float], pole_pairs: int,
                  n_parallel: int = 1, *,
                  mechanical_angle_rad: Optional[Sequence[float]] = None,
                  imposed_current_drive: bool = False, eddy: bool = False,
                  rotor_eddy: bool = False, demag: bool = False,
                  frozen_nu: bool = False,
                  all_frames_converged: bool = False,
                  integer_period_window: bool = False
                  ) -> Tuple[List[float], str]:
    """Mean torque from terminal work or flux linkage, AC from raw Maxwell.

    Two branches, chosen by eligibility (never by a filter):

    * ELIGIBLE (imposed current, no eddy / rotor eddy / demag / frozen ν, every
      retained frame converged, integer-period uniform window): the all-bin
      terminal-work mean ``n_parallel * mean(Σ i_branch * dψ/dθ_m)``,
      method ``"terminal_work_mean+maxwell_ripple"``.
    * EVERY OTHER RUN (eddy, demag, voltage / PWM drive — every client report):
      the flux-linkage space-vector mean ``(3/2)·p·n_par·<ψα iβ − ψβ iα>``,
      computed exactly as 68de0ca did, method ``"energy_mean+maxwell_ripple"``.
      House rule: energy / flux-linkage torque only, never the Maxwell mean.
      As in 68de0ca, a run whose peak per-branch current is ≤ 1 A (no-load
      cogging with eddy, say) keeps the raw Maxwell series
      (``"maxwell_stress"``): the space-vector mean is identically 0 there and
      cannot see cogging or eddy drag.

    ``psi_*`` and ``i_*`` are PER-BRANCH (one parallel path), which is how the
    solver carries them everywhere — ``_sc_psi2`` divides psi by n_parallel and
    the excitation's i_peak is ``I_phase / n_parallel``. The selected terminal
    work uses ``n_parallel * mean(sum(i_branch * dψ/dθ_m))``, restoring the
    full phase current once. The legacy fundamental space-vector expression
    remains a diagnostic candidate; its sinusoidal-winding assumptions omit
    material spatial-harmonic terms in general.

    The all-bin terminal path still needs a conservative, complete periodic
    state and an error budget before its mean can be regarded as virtual-work
    torque. Eddy-current redistribution, irreversible magnet changes, and
    frozen permeability are outside this selector's validated regime.

    The reported waveform is raw Maxwell torque minus its mean plus the
    all-bin terminal-work mean. Its retained AC can contain physical ripple AND mesh
    or sliding-band artifacts; this helper does not establish convergence.
    Historical mean discrepancies do not establish a fixed Maxwell bias for
    every geometry, material or operating point.

    Terminal-work eligibility is explicit conservative-periodic eligibility.
    The ripple is the raw Maxwell AC in both branches (no filter); only the
    mean is replaced. Zero-current terminal work is not a cogging estimate.
    """
    _ineligible = _terminal_work_ineligibility(
        imposed_current_drive=imposed_current_drive, eddy=eddy,
        rotor_eddy=rotor_eddy, demag=demag, frozen_nu=frozen_nu,
        all_frames_converged=all_frames_converged,
        integer_period_window=integer_period_window,
        mechanical_angle_rad=mechanical_angle_rad)
    if _ineligible is not None:
        return space_vector_hybrid_torque(
            psi_a, psi_b, psi_c, i_a, i_b, i_c, t_maxwell, pole_pairs,
            n_parallel=n_parallel)
    _mx = np.asarray(t_maxwell, float)
    _mean = terminal_work_mean(
        psi_a, psi_b, psi_c, i_a, i_b, i_c, mechanical_angle_rad,
        pole_pairs, n_parallel)
    if _mx.ndim != 1 or not np.all(np.isfinite(_mx)) \
            or _mx.size != np.asarray(psi_a).size:
        raise ValueError("Maxwell torque must be finite and aligned with terminal samples")
    return (_mx - _mx.mean() + _mean).tolist(), "terminal_work_mean+maxwell_ripple"


def torque_method_diagnostics(psi_a: Sequence[float], psi_b: Sequence[float],
                              psi_c: Sequence[float], i_a: Sequence[float],
                              i_b: Sequence[float], i_c: Sequence[float],
                              t_maxwell: Sequence[float], pole_pairs: int,
                              n_parallel: int = 1,
                              selected_method: Optional[str] = None, *,
                              mechanical_angle_rad: Optional[Sequence[float]] = None,
                              imposed_current_drive: bool = False,
                              eddy: bool = False, rotor_eddy: bool = False,
                              demag: bool = False, frozen_nu: bool = False,
                              all_frames_converged: bool = False,
                              integer_period_window: bool = False
                              ) -> Dict[str, object]:
    """Compare torque mean candidates without certifying either physically.

    This is additive diagnostic metadata only. It deliberately does not pick
    the reported torque or infer energy balance, periodicity, or dq validity.
    Invalid or unavailable inputs yield JSON-safe ``None`` values and a reason.
    """
    result: Dict[str, object] = {
        "validation_status": "uncertified",
        "validation_reason": (
            "available solver metadata do not certify a general torque method"),
        "candidate_kind": "fundamental_space_vector_mean_candidate",
        "selected_method": (str(selected_method)
                            if selected_method is not None else None),
        "space_vector_mean_candidate_Nm": None,
        "raw_maxwell_mean_Nm": None,
        "space_vector_minus_maxwell_mean_Nm": None,
        "terminal_work_mean_candidate_Nm": None,
        "terminal_work_minus_maxwell_mean_Nm": None,
        "terminal_work_method_eligible": False,
        "terminal_work_eligibility_reason": None,
        "per_branch_peak_current_A": None,
        "legacy_selector_would_use_space_vector_mean": None,
        "certified_energy_balance_Nm": None,
        "diagnostic_input_reason": None,
    }
    try:
        arrays = [np.asarray(v, dtype=float) for v in
                  (psi_a, psi_b, psi_c, i_a, i_b, i_c, t_maxwell)]
        if any(a.ndim != 1 for a in arrays):
            result["diagnostic_input_reason"] = "all inputs must be 1-D arrays"
            return result
        sizes = {int(a.size) for a in arrays}
        if len(sizes) != 1 or not sizes or next(iter(sizes)) == 0:
            result["diagnostic_input_reason"] = (
                "phase flux, phase current, and Maxwell arrays must have equal nonzero length")
            return result
        if not all(np.all(np.isfinite(a)) for a in arrays):
            result["diagnostic_input_reason"] = "inputs must contain only finite values"
            return result
        p = int(pole_pairs)
        npar = int(n_parallel)
        if p <= 0 or p != pole_pairs or npar < 1 or npar != n_parallel:
            result["diagnostic_input_reason"] = (
                "pole_pairs and n_parallel must be positive integers")
            return result

        pa, pb, pc, ia, ib, ic, mx = arrays
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            peak = float(np.max(np.abs(np.concatenate((ia, ib, ic)))))
            scale, k_clarke = 2.0 / 3.0, math.sqrt(3.0) / 2.0
            psi_alpha = scale * (pa - 0.5 * pb - 0.5 * pc)
            psi_beta = scale * k_clarke * (pb - pc)
            i_alpha = scale * (ia - 0.5 * ib - 0.5 * ic)
            i_beta = scale * k_clarke * (ib - ic)
            candidate = (1.5 * float(p) * float(npar)
                         * (psi_alpha * i_beta - psi_beta * i_alpha))
            candidate_mean = float(np.mean(candidate))
            maxwell_mean = float(np.mean(mx))
            mean_difference = candidate_mean - maxwell_mean
        if not all(math.isfinite(v) for v in
                   (peak, candidate_mean, maxwell_mean, mean_difference)):
            result["diagnostic_input_reason"] = (
                "diagnostic arithmetic produced a nonfinite value")
            return result
        result.update({
            "space_vector_mean_candidate_Nm": candidate_mean,
            "raw_maxwell_mean_Nm": maxwell_mean,
            "space_vector_minus_maxwell_mean_Nm": mean_difference,
            "per_branch_peak_current_A": peak,
            "legacy_selector_would_use_space_vector_mean": bool(peak > 1.0),
        })
        reason = _terminal_work_ineligibility(
            imposed_current_drive=imposed_current_drive, eddy=eddy,
            rotor_eddy=rotor_eddy, demag=demag, frozen_nu=frozen_nu,
            all_frames_converged=all_frames_converged,
            integer_period_window=integer_period_window,
            mechanical_angle_rad=mechanical_angle_rad)
        if reason is None:
            try:
                work_mean = terminal_work_mean(
                    pa, pb, pc, ia, ib, ic, mechanical_angle_rad, p, npar)
                difference = work_mean - maxwell_mean
                if not all(math.isfinite(v) for v in (work_mean, difference)):
                    raise ValueError("terminal-work arithmetic was nonfinite")
                result.update({
                    "terminal_work_mean_candidate_Nm": work_mean,
                    "terminal_work_minus_maxwell_mean_Nm": difference,
                    "terminal_work_method_eligible": True,
                    "terminal_work_eligibility_reason": None,
                })
            except Exception as exc:
                result["terminal_work_eligibility_reason"] = str(exc)
        else:
            result["terminal_work_eligibility_reason"] = reason
        return result
    except Exception as exc:
        result["diagnostic_input_reason"] = (
            "diagnostic inputs could not be evaluated: " + type(exc).__name__)
        return result


def torque_harmonics(t_raw: Sequence[float], n_steps_per_period: int,
                     step_periods: Optional[float] = None,
                     ) -> Tuple[List[float], List[float]]:
    """Single-sided FFT of the entire available RAW torque waveform.

    Return all resolved orders at full precision, without discarding content
    or classifying it as numerical noise. Amplitudes are peak values in N·m.
    The order of bin k is k / (sample_count * step_periods), so finite windows
    can have fractional electrical orders. The even-length Nyquist bin is not
    doubled. DC is reported separately as T_avg_maxwell_Nm by the caller.
    No samples are truncated or demeaned before the transform.
    """
    _Tp = np.asarray(t_raw, float)
    if not _Tp.size:
        return [], []
    _step = 1.0 / float(n_steps_per_period) if step_periods is None else float(step_periods)
    if not np.isfinite(_step) or _step <= 0.0:
        raise ValueError("Torque spectrum requires a finite positive sampling step")
    _F = np.abs(np.fft.rfft(_Tp)) / _Tp.size * 2.0
    if _Tp.size % 2 == 0:
        _F[-1] *= 0.5
    _nh = _F.size - 1
    return ([float(k / (_Tp.size * _step)) for k in range(1, _nh + 1)],
            [float(_F[k]) for k in range(1, _nh + 1)])
