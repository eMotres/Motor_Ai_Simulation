"""Eligibility-gated periodic terminal-flux-work diagnostic.

This reports mean terminal flux work per mechanical radian. It is not a
production torque estimate, a torque selector, or a no-load cogging method.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PeriodicFluxWorkResult:
    """Periodic terminal work per mechanical radian and a sampling check."""

    mean_work_per_mechanical_radian: float
    coarse_mean_work_per_mechanical_radian: float
    relative_coarsening_change: float
    sample_count: int
    parallel_branches: int


def periodic_terminal_flux_work_per_mechanical_radian(
    currents_a,
    flux_linkages_wb,
    rotor_angle_rad,
    *,
    rpm: float,
    pole_pairs: int,
    n_periods: float,
    drive_mode: str,
    periodic_window_certified: bool,
    settled: bool,
    conservative_lossless_certified: bool,
    eddy_coupled: bool = False,
    rotor_eddy: bool = False,
    demag: bool = False,
    parallel_branches: int = 1,
    coarsening_change_rtol: float = 1e-3,
) -> PeriodicFluxWorkResult:
    """Return periodic ``sum(i dψ) / Δθ`` for an explicitly certified window.

    Inputs have shape ``(phase, sample)``; the last sample is not a duplicated
    endpoint. The periodic trapezoid closes the path from its final sample to
    its first. Mechanical angle is in radians and must span an integer number
    of electrical periods, with direction matching ``rpm``.

    The caller must certify periodicity and settling from solver provenance;
    equal-looking endpoint samples are not proof. The conservative, current-
    driven, no-eddy/no-demag restrictions are eligibility rules for this
    diagnostic only. They never filter or alter the underlying waveforms.
    Comparing every sample with every other sample is a coarsening-sensitivity
    check only; aliasing can make both grids agree without proving convergence.
    """
    rpm = float(rpm)
    if not np.isfinite(rpm) or rpm == 0.0:
        raise ValueError("nonzero finite speed is required")
    if int(pole_pairs) != pole_pairs or int(pole_pairs) <= 0:
        raise ValueError("pole_pairs must be a positive integer")
    pole_pairs = int(pole_pairs)
    if not np.isfinite(n_periods) or float(n_periods) < 1.0 \
            or not float(n_periods).is_integer():
        raise ValueError("n_periods must be a positive integer")
    n_periods = int(n_periods)
    if str(drive_mode).lower() != "current":
        raise ValueError("only imposed-current windows are eligible")
    if not periodic_window_certified:
        raise ValueError("periodicity must be certified by the caller")
    if not settled:
        raise ValueError("settled window is required")
    if not conservative_lossless_certified:
        raise ValueError("conservative lossless behavior must be certified")
    if eddy_coupled or rotor_eddy or demag:
        raise ValueError("lossy or changing-state windows are ineligible")
    if int(parallel_branches) != parallel_branches or int(parallel_branches) < 1:
        raise ValueError("parallel_branches must be a positive integer")
    parallel_branches = int(parallel_branches)
    if not np.isfinite(coarsening_change_rtol) or coarsening_change_rtol <= 0.0:
        raise ValueError("coarsening_change_rtol must be positive and finite")

    currents = np.asarray(currents_a, dtype=float)
    flux = np.asarray(flux_linkages_wb, dtype=float)
    angle = np.asarray(rotor_angle_rad, dtype=float)
    if (currents.ndim != 2 or currents.shape != flux.shape
            or currents.shape[0] < 1):
        raise ValueError("currents and flux linkages must have matching 2-D shapes")
    if angle.ndim != 1 or angle.size != currents.shape[1]:
        raise ValueError("angle count must match the waveform sample count")
    n = int(angle.size)
    if n < 16 or n % 2:
        raise ValueError("at least 16 even samples are required for refinement")
    if not (np.all(np.isfinite(currents)) and np.all(np.isfinite(flux))
            and np.all(np.isfinite(angle))):
        raise ValueError("waveforms and angles must be finite")
    if not np.any(currents != 0.0):
        raise ValueError("zero-current work cannot diagnose cogging torque")

    signed_span = np.copysign(2.0 * np.pi * n_periods / pole_pairs, rpm)
    step = signed_span / n
    angle_steps = np.diff(angle)
    if angle_steps.size and not np.allclose(
            angle_steps, step, rtol=1e-8, atol=1e-12):
        raise ValueError("rotor angles must be uniform and match the certified span")

    def _mean_work(i, psi):
        psi_delta = np.roll(psi, -1, axis=1) - psi
        i_mid = 0.5 * (i + np.roll(i, -1, axis=1))
        return float(parallel_branches * np.sum(i_mid * psi_delta) / signed_span)

    fine = _mean_work(currents, flux)
    coarse = _mean_work(currents[:, ::2], flux[:, ::2])
    scale = max(abs(fine), abs(coarse), 1e-12)
    relative_coarsening_change = abs(fine - coarse) / scale
    if (not np.isfinite(relative_coarsening_change)
            or relative_coarsening_change > coarsening_change_rtol):
        raise ValueError("periodic line integral coarsening sensitivity exceeds limit")
    return PeriodicFluxWorkResult(
        mean_work_per_mechanical_radian=fine,
        coarse_mean_work_per_mechanical_radian=coarse,
        relative_coarsening_change=relative_coarsening_change,
        sample_count=n,
        parallel_branches=parallel_branches,
    )
