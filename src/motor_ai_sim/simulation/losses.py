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

import math
from typing import Any, Callable, Optional, Sequence, Tuple

import numpy as np

# 2*pi^2 — the classical-eddy denominator in the Bertotti form below.
TWO_PI_SQ = 2.0 * math.pi ** 2

# Fill factor used when a steel declares none. Every steel in the shipped library
# declares its own (B15AHV950M is 0.925), so this is a guard, not a knob — and it
# is ONE value: the same constant appeared as 0.95 in two places and 0.97 in a
# third, which is the sort of split that quietly moves numbers.
DEFAULT_STACKING_FACTOR = 0.97


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
) -> Tuple[np.ndarray, float]:
    """Bertotti iron loss from a per-element B(t) history.

    Returns ``(P_classical(t), P_hysteresis_and_excess)`` — the first ripples
    with the teeth passing, the second is a per-cycle quantity and therefore flat.

        P/V = k_h*f*B^2  +  k_c/(2*pi^2) * <(dB/dt)^2>  +  k_e*f^1.5*B^1.5

    The coefficients come from the material's MEASURED loss curves when it has
    them (relative-error-weighted NNLS over every (f, B) point), falling back to
    the YAML k_h/k_c/k_e. Real curves give real loss.

    ``ddt`` is the caller's time-derivative operator, and it is the ONLY thing
    that differs between element orders: P1 needs a smoothed angle-derivative
    because the sliding band's node re-pairing adds a frame-to-frame jitter that
    a raw difference amplifies (the loss tripled going from 24 to 72 steps),
    while the P2 field is smooth enough for a plain central difference.

    ``bertotti`` is injected rather than imported so this module stays free of
    the materials library and can be tested with hand-written coefficients.
    """
    if material is None or idx.size == 0 or len(hist_x) == 0:
        return np.zeros(n_frames), 0.0
    X = np.asarray(hist_x)
    Y = np.asarray(hist_y)
    if X.size == 0 or np.asarray(hist_x[0]).size == 0:
        return np.zeros(n_frames), 0.0

    kh, kc, ke = bertotti(material)
    sf = float(getattr(material, "stacking_factor", DEFAULT_STACKING_FACTOR))
    # Only the steel carries loss; the inter-laminate insulation is dead volume.
    vol = areas[idx] * stack_length_m * sf

    dX = ddt(X)
    dY = ddt(Y)
    classical = (kc / TWO_PI_SQ) * np.sum((dX ** 2 + dY ** 2) * vol[None, :], axis=1)

    # Peak-to-peak / 2 per element over the captured window — the AC amplitude
    # the per-cycle terms are defined on.
    Bac2 = (((X.max(0) - X.min(0)) * 0.5) ** 2
            + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)
    per_cycle = float(np.sum(
        (kh * f_elec_hz * Bac2
         + ke * f_elec_hz ** 1.5 * np.power(np.maximum(Bac2, 0.0), 0.75)) * vol))
    return classical, per_cycle


def central_difference(dt_s: float) -> Callable[[np.ndarray], np.ndarray]:
    """Periodic central difference — for a field already smooth in time (P2)."""
    def _ddt(X: np.ndarray) -> np.ndarray:
        return (np.roll(X, -1, 0) - np.roll(X, 1, 0)) / (2.0 * dt_s)
    return _ddt


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
