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


def hybrid_torque(psi_a: Sequence[float], psi_b: Sequence[float],
                  psi_c: Sequence[float], i_a: Sequence[float],
                  i_b: Sequence[float], i_c: Sequence[float],
                  t_maxwell: Sequence[float], pole_pairs: int,
                  n_parallel: int = 1) -> Tuple[List[float], str]:
    """Fundamental space-vector mean + raw Maxwell AC. Returns (T(t), method).

    ``psi_*`` and ``i_*`` are PER-BRANCH (one parallel path), which is how the
    solver carries them everywhere — ``_sc_psi2`` divides psi by n_parallel and
    the excitation's i_peak is ``I_phase / n_parallel``. The space-vector
    expression uses PHASE quantities, so ``n_parallel`` restores the phase
    current (n_parallel branches carry the phase current between them, each at
    the same flux linkage).  Omitting it reported ``T_true / n_parallel``; it
    was invisible while every config in the repo had one parallel path, and it
    surfaced the moment the per-request winding channel let a stored ``2S-2P``
    machine be evaluated on its own connection (F3).  ``P_elec_in`` in the
    solver already carried exactly this factor for exactly this reason.

    The selected mean is (3/2)*p*<ψα*iβ - ψβ*iα>. This is the usual torque
    identity for a rotationally covariant sinusoidal-winding dq model, not a
    general finite-element virtual-work calculation. Arbitrary temporal
    current waveforms alone need not invalidate that identity; explicit
    rotor-position dependence of coenergy, spatial harmonics and cogging
    require additional terms. Eddy-current redistribution and irreversible
    magnet changes require a verified energy/port-work balance too. Agreement
    at particular validated operating points is not a universal guarantee.

    The reported waveform is raw Maxwell torque minus its mean plus the
    space-vector mean. Its retained AC can contain physical ripple AND mesh
    or sliding-band artifacts; this helper does not establish convergence.
    Historical mean discrepancies do not establish a fixed Maxwell bias for
    every geometry, material or operating point.

    The legacy selector uses peak PER-BRANCH current > 1 A, subject to the
    existing terminal-data check. Otherwise it returns the raw Maxwell series,
    including at no-load. This threshold is not a physical conservation law
    and can change the selected mean discontinuously. Zero terminal current
    does not rule out cogging or eddy drag. Numerical behavior and the legacy
    method strings are retained pending the energy-method validation plan in
    docs/solver-torque-validation-plan.md.
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
        # Retain raw Maxwell AC; select the fundamental space-vector mean.
        return (_mx - _mx.mean() + _emean).tolist(), "energy_mean+maxwell_ripple"
    # Legacy low-current/terminal-data fallback: retain the raw Maxwell series.
    return list(t_maxwell), "maxwell_stress"


def torque_method_diagnostics(psi_a: Sequence[float], psi_b: Sequence[float],
                              psi_c: Sequence[float], i_a: Sequence[float],
                              i_b: Sequence[float], i_c: Sequence[float],
                              t_maxwell: Sequence[float], pole_pairs: int,
                              n_parallel: int = 1,
                              selected_method: Optional[str] = None
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
