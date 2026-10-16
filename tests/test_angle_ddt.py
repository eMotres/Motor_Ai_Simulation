"""Slip-grid derivative — the operator both P1 loss models run on.

It exists to remove an artefact, so the tests are about the artefact: does it
still differentiate correctly, and does the answer stop depending on the step
count? Neither question could be asked while this was a closure.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation.angle_ddt import make_angle_ddt


# Nodes per ELECTRICAL period for the 12s14p machine: 1008 / 7 = 144. The
# solver snaps every step count to a divisor of this (field_ops._snap_steps_to_
# nodes) and these tests only use snapped counts — see TestPrecondition for why
# that snapping is load-bearing and not a nicety.
NODES_PER_PERIOD = 144
SNAPPED = (12, 16, 18, 24, 36, 48, 72)


def _schedule(n_frames: int, nodes_per_rev: int = 1008, n_periods: float = 1.0,
              pole_pairs: int = 7, rpm: float = 15000.0):
    """One run's slip schedule, rotor advancing a whole number of nodes."""
    period_mech = 360.0 / pole_pairs
    spacing_deg = 360.0 / nodes_per_rev
    spacing_rad = math.radians(spacing_deg)
    omega_mech = 2.0 * math.pi * rpm / 60.0
    theta = (np.arange(n_frames) / n_frames) * period_mech * n_periods
    m_arr = np.round(theta / spacing_deg)
    f_elec = rpm / 60.0 * pole_pairs
    dt = (1.0 / f_elec) * n_periods / n_frames
    return make_angle_ddt(m_arr, spacing_rad, omega_mech, dt, n_periods,
                          period_mech), dt, f_elec


class TestDerivative:
    def test_differentiates_a_sine_correctly(self):
        """The floor: for X = sin(w t) the result must be w*cos(w t).

        A smoother that also distorts the signal is worse than no smoother, and
        this is what says it does not.
        """
        n = 48
        ddt, dt, f_elec = _schedule(n)
        w = 2 * np.pi * f_elec
        t = np.arange(n) * dt
        X = np.sin(w * t)[:, None] * np.ones((1, 3))
        got = ddt(X)
        want = (w * np.cos(w * t))[:, None] * np.ones((1, 3))
        # smoothing costs a little amplitude; the shape must be right
        assert np.corrcoef(got[:, 0], want[:, 0])[0, 1] > 0.99
        assert np.max(np.abs(got)) == pytest.approx(w, rel=0.15)

    def test_a_constant_has_zero_derivative(self):
        n = 48
        ddt, _, _ = _schedule(n)
        X = np.full((n, 4), 1.234)
        assert np.allclose(ddt(X), 0.0, atol=1e-6)

    def test_result_does_not_depend_on_the_step_count(self):
        """The reason this operator exists. A raw node-to-node difference makes
        the mean-square derivative grow with the step count — the magnet loss
        tripled going 24 -> 72 — because it differentiates the slip quantisation
        rather than the field.
        """
        rms = []
        for n in (24, 48, 72):
            ddt, dt, f_elec = _schedule(n)
            w = 2 * np.pi * f_elec
            t = np.arange(n) * dt
            X = np.sin(w * t)[:, None]
            d = ddt(X)
            rms.append(float(np.sqrt(np.mean(d ** 2))))
        spread = (max(rms) - min(rms)) / max(rms)
        assert spread < 0.15, f"step-count dependent: {rms}"

    def test_scales_with_speed(self):
        """Same waveform against rotor angle, twice the rpm -> twice dX/dt."""
        n = 48
        out = []
        for rpm in (15000.0, 30000.0):
            ddt, dt, f_elec = _schedule(n, rpm=rpm)
            theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
            X = np.sin(theta)[:, None]
            out.append(float(np.sqrt(np.mean(ddt(X) ** 2))))
        assert out[1] / out[0] == pytest.approx(2.0, rel=0.1)

    def test_shape_is_preserved(self):
        n, e = 36, 7
        ddt, _, _ = _schedule(n)
        assert ddt(np.random.default_rng(0).normal(size=(n, e))).shape == (n, e)

    def test_single_element_history_survives(self):
        """A one-element region (a thin bridge, a single magnet slice) must not
        trip the vectorised path."""
        n = 48
        ddt, _, _ = _schedule(n)
        assert ddt(np.zeros((n, 1))).shape == (n, 1)


class TestPrecondition:
    """The operator is only correct on SNAPPED step counts. Documented, because
    it is a precondition the solver must keep honouring, not a latent bug."""

    def test_snapped_counts_recover_the_peak_derivative(self):
        for n in SNAPPED:
            ddt, dt, f_elec = _schedule(n)
            w = 2 * np.pi * f_elec
            t = np.arange(n) * dt
            got = ddt(np.sin(w * t)[:, None])
            ratio = float(np.max(np.abs(got)) / w)
            assert 0.85 < ratio < 1.15, f"{n} steps: peak off by {ratio:.2f}x"

    def test_an_unsnapped_count_is_wrong_which_is_why_snapping_exists(self):
        """96 frames per period is 1.5 slip nodes per frame — the unique-node
        grid comes out unevenly spaced and the peak derivative overshoots ~48 %.

        96 does not divide 144, so the solver can never ask for it. If this test
        ever starts passing, either the snapping was removed (and the loss
        numbers are now quietly wrong on some step counts) or the operator was
        made robust — check which before deleting it.
        """
        n = 96
        assert NODES_PER_PERIOD % n != 0, "96 must remain an INVALID step count"
        ddt, dt, f_elec = _schedule(n)
        w = 2 * np.pi * f_elec
        t = np.arange(n) * dt
        ratio = float(np.max(np.abs(ddt(np.sin(w * t)[:, None]))) / w)
        assert ratio > 1.3, (
            f"the unsnapped case now behaves (ratio {ratio:.2f}) — see docstring")
