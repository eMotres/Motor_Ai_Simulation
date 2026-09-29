"""Smoothed time-derivative on the slip-node grid.

Ninety-odd lines of signal processing that both loss models depend on, and that
lived as a closure over the run's slip schedule — so it could not be exercised
without a full solve, despite being the piece most likely to be subtly wrong.

The problem it solves: the sliding band advances the rotor by whole slip nodes,
so X(t) carries a frame-to-frame quantisation jitter that a raw difference
amplifies with the step count. The magnet loss tripled going from 24 to 72 steps
before this existed. Differentiating a low-passed X(theta) on the UNIQUE node
grid removes the artefact without touching the physical ripple.

Two further defects, both found by comparing a 1-period run against a 2-period
one and both handled inside:
  * rotor-frame histories are NOT periodic over the window — the stator slotting
    sweeps past at a non-integer count per electrical period — so forcing a
    periodic wrap put a level jump at the seam that the smoother bent into its
    neighbours, humping the magnet loss ~2.5x on the first and last frames
  * the savgol window was set in SAMPLES, so the physical smoothing width
    depended on the run length: a 2-period run smoothed the genuine 15-degree
    slot ripple away and halved the magnet loss on identical physics. The width
    is a constant ANGLE now.

Exposed as a factory rather than a class so the body stays byte-identical to the
closure it replaces — the dependencies become explicit in the signature, and
nothing about the algorithm moves in the same commit.
"""
from __future__ import annotations

import math

import numpy as np


def make_angle_ddt(m_arr, spacing_rad: float, omega_mech: float,
                   dt: float, n_periods: float, period_mech: float):
    """Bind one run's slip schedule; returns the derivative operator.

    m_arr         per-frame slip-node index (the rotor's discrete position)
    spacing_rad   angle between adjacent slip nodes [rad]
    omega_mech    mechanical angular speed [rad/s]
    dt            frame time step [s]
    n_periods     electrical periods in the window
    period_mech   one electrical period in mechanical degrees
    """
    _m_arr = np.asarray(m_arr, float)
    _spacing_rad = float(spacing_rad)
    _omega_mech = float(omega_mech)

    def _angle_ddt_2d(X, quasi_period_rad=None, pre=None, post=None):
        """Smoothed dX/dt on the unique slip-node grid, mapped back to frames.

        Raw node-to-node derivatives amplify slip-merge jitter with the step
        count, so X(θ) is savgol-low-passed before differentiating.  Two
        defects were found by a 1-period vs 2-period ground-truth comparison
        and are handled here:
        (1) ROTOR-frame histories (magnets, rotor iron) are NOT periodic over
            the window — the stator slotting sweeps past at the stator-
            structure period (2 slot pitches: alternating wide/narrow teeth),
            a non-integer count per electrical period — so a forced periodic
            wrap put a LEVEL JUMP at the seam that the smoother bent into the
            neighbours: P_mag humped ~2.5× on the first/last frames.  Fixed by
            a C0 wrap-detrend (see below) — exact for the derivative, no
            assumption about the signal's true period, no-op when already
            periodic (stator-frame histories).
        (2) the savgol window was set in SAMPLES (U//8), so the physical
            smoothing width depended on the run length — a 2-period run
            smoothed the genuine 15° slot ripple away (P_mag halved on
            identical physics).  Fixed: the width is a constant ANGLE.
        When `pre`/`post` are given they are EXACT samples just before/after
        the window (from the pole-shift symmetry — see the magnet-history pad
        block); the derivative then uses real data at both ends and needs no
        wrap assumption at all.
        (quasi_period_rad accepted for compatibility; unused.)
        """
        N = X.shape[0]
        if N < 3:
            return np.zeros_like(X)
        uniq, first = np.unique(_m_arr, return_index=True)   # sorted unique m
        if uniq.size < 3:
            return (np.roll(X, -1, 0) - np.roll(X, 1, 0)) / (2 * dt)
        Bu = X[first]                                        # (U, E)
        theta_u = uniq * _spacing_rad                        # (U,)
        U = uniq.size
        _W = math.radians(period_mech * n_periods)           # window span
        if float(theta_u[-1]) >= _W - 1e-9:
            # degenerate: last node is the periodic image of the first — drop it
            uniq = uniq[:-1]; Bu = Bu[:-1]; theta_u = theta_u[:-1]; U -= 1
        # savgol at a FIXED PHYSICAL width (≈1/3 slot pitch).  Two past bugs:
        # (a) a window set in SAMPLES (the old U//8) made the width depend on
        #     the run length — a 2-period run smoothed the genuine 15° slot
        #     ripple away (P_mag halved on identical physics);
        # (b) the angle→samples conversion divided by the slip-NODE spacing
        #     (_spacing_rad) instead of the SAMPLE spacing, so the effective
        #     width was 5°×(nodes advanced per step): 60° at 12 steps, 30° at
        #     24, 10° at 72 — the filter NARROWED as the step count grew, so
        #     the "converged" rotor eddy loss GREW with steps (450 mm shaft:
        #     0.8→12.7→25.6 kW at 12/24/72) instead of converging.  Fixed:
        #     divide by the actual theta_u sample spacing.
        _w_ang = math.radians(5.0)                            # smoothing width
        _samp_rad = (float(np.median(np.diff(theta_u))) if U >= 2
                     else _spacing_rad)
        w = int(round(_w_ang / max(_samp_rad, 1e-12)))
        w = max(5, w | 1)                                     # odd, ≥5
        _exact = (pre is not None and post is not None
                  and U == N and np.array_equal(uniq, _m_arr))
        if _exact:
            # EXACT pads: real samples beyond both window ends — no wrap, no
            # detrend; the seam simply does not exist.
            _need = w // 2 + 2
            pre_u = pre[-min(_need, pre.shape[0]):]
            post_u = post[:min(_need, post.shape[0])]
            thL = theta_u[0] - _spacing_rad * np.arange(pre_u.shape[0], 0, -1)
            thR = theta_u[-1] + _spacing_rad * np.arange(1, post_u.shape[0] + 1)
            th_ext = np.concatenate([thL, theta_u, thR])
            Bu_ext = np.concatenate([pre_u, Bu, post_u], axis=0)
            i0 = pre_u.shape[0]
            _c = np.zeros((1, Bu.shape[1]))
        else:
            # C0 detrend: per column, remove the linear ramp that makes the two
            # window ends MEET, so the periodic extension has no level jump at
            # the seam; the ramp's constant slope is added back to the
            # derivative exactly.  (A harmonic-regression replacement was tried
            # and rejected: the slot-structure lines sit ~0.86 cycles apart —
            # under the Rayleigh limit of a 1-period window — so the design
            # matrix is near-singular and the fit explodes.)
            _span = float(theta_u[-1] - theta_u[0])
            if _span > 1e-12:
                _c = (Bu[-1] - Bu[0])[None, :] / _span       # (1, E) dB/dθ
                Bu = Bu - _c * (theta_u - theta_u[0])[:, None]
            else:
                _c = np.zeros((1, Bu.shape[1]))
            th_ext = np.concatenate([theta_u - _W, theta_u, theta_u + _W])
            Bu_ext = np.concatenate([Bu, Bu, Bu], axis=0)
            i0 = U
        if U >= 7 and Bu_ext.shape[0] >= w:
            from scipy.signal import savgol_filter as _sg
            Bu_ext = _sg(Bu_ext, w, 3, axis=0, mode="interp")
        dBdt_u = ((np.gradient(Bu_ext, th_ext, axis=0)[i0:i0 + U] + _c)
                  * _omega_mech)
        pos = np.clip(np.searchsorted(uniq, _m_arr), 0, U - 1)   # frame → unique idx
        return dBdt_u[pos]

    return _angle_ddt_2d
