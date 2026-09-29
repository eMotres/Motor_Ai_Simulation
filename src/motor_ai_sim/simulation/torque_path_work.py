"""High-order terminal flux-work integration for offline diagnostics only."""
from __future__ import annotations

from dataclasses import dataclass
import operator

import numpy as np
from scipy.interpolate import CubicSpline


@dataclass(frozen=True)
class PathWorkResult:
    """Uncertified terminal path-work mean and its sample provenance."""

    mean_work_per_mechanical_radian: float
    sample_count: int
    parallel_branches: int
    signed_angle_span_rad: float


def spline_terminal_flux_work_per_mechanical_radian(
        currents_a, flux_linkages_wb, mechanical_angle_rad,
        *, parallel_branches: int = 1) -> PathWorkResult:
    """Integrate ``sum(i dψ) / signed Δθ`` from every supplied sample.

    Waveforms use ``(phase, sample)`` shape. Cubic splines interpolate the
    supplied samples without smoothing or periodic endpoint extension. A
    three-point Gauss rule integrates the product of the cubic current and
    quadratic flux derivative exactly on each spline interval. The result is
    a diagnostic path-work mean, not a certified motor torque.
    """
    branches = _positive_int(parallel_branches, "parallel_branches")
    current = np.asarray(currents_a, dtype=float)
    flux = np.asarray(flux_linkages_wb, dtype=float)
    angle = np.asarray(mechanical_angle_rad, dtype=float)
    if (current.ndim != 2 or current.shape != flux.shape
            or current.shape[0] < 1):
        raise ValueError("currents and flux linkages need matching (phase, sample) shapes")
    if angle.ndim != 1 or angle.size != current.shape[1]:
        raise ValueError("angle count must match waveform samples")
    if angle.size < 4:
        raise ValueError("at least four samples are required for cubic interpolation")
    if not (np.all(np.isfinite(current)) and np.all(np.isfinite(flux))
            and np.all(np.isfinite(angle))):
        raise ValueError("waveforms and angles must be finite")
    delta = np.diff(angle)
    if not (np.all(delta > 0.0) or np.all(delta < 0.0)):
        raise ValueError("mechanical angles must be strictly monotone")

    signed_span = float(angle[-1] - angle[0])
    direction = 1.0 if signed_span > 0.0 else -1.0
    x = direction * (angle - angle[0])
    i_spline = CubicSpline(x, current.T, axis=0)
    psi_spline = CubicSpline(x, flux.T, axis=0)
    dpsi_spline = psi_spline.derivative()

    nodes = np.array([-np.sqrt(3.0 / 5.0), 0.0, np.sqrt(3.0 / 5.0)])
    weights = np.array([5.0 / 9.0, 8.0 / 9.0, 5.0 / 9.0])
    left, right = x[:-1], x[1:]
    mid = 0.5 * (left + right)
    half = 0.5 * (right - left)
    points = mid[:, None] + half[:, None] * nodes[None, :]
    current_q = i_spline(points)             # (interval, gauss, phase)
    dpsi_q = dpsi_spline(points)              # dψ/dx, same shape
    power_q = np.sum(current_q * dpsi_q, axis=2)
    work_per_branch = float(np.sum(
        half[:, None] * weights[None, :] * power_q))
    mean = branches * work_per_branch / signed_span
    if not np.isfinite(mean):
        raise ValueError("computed path-work mean is non-finite")
    return PathWorkResult(mean, int(angle.size), branches, signed_span)


def _positive_int(value, name: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be a positive integer")
    try:
        integer = operator.index(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if integer < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(integer)
