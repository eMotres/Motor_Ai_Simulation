"""Iron-loss model — the properties both element orders must share.

This is now ONE implementation used by P1 and P2, so a test here covers both.
That is the point: the previous duplicate copies let a flag gate one and not the
other, and P2 reported zero core loss for a while with nothing to catch it.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from motor_ai_sim.simulation.losses import (
    DEFAULT_STACKING_FACTOR, TWO_PI_SQ, central_difference, iron_loss_series,
)

# Hand-picked coefficients so every expected number is computable on paper.
KH, KC, KE = 100.0, 0.5, 0.0


def _bertotti(_mat):
    return KH, KC, KE


def _sine_history(n: int, amp: float, n_elem: int = 3):
    """B(t) = amp*sin, identical in every element — one cycle over n frames."""
    t = np.arange(n) / n * 2 * np.pi
    X = np.outer(amp * np.sin(t), np.ones(n_elem))
    Y = np.zeros_like(X)
    return X, Y


def _steel(sf=None):
    m = SimpleNamespace()
    if sf is not None:
        m.stacking_factor = sf
    return m


class TestIronLoss:
    def test_zero_field_is_zero_loss(self):
        n = 32
        X = np.zeros((n, 3)); Y = np.zeros((n, 3))
        cl, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(0.95),
                                  0.01, 100.0, n, central_difference(1e-4), _bertotti)
        assert np.allclose(cl, 0.0) and pc == pytest.approx(0.0)

    def test_hysteresis_scales_with_B_squared(self):
        """k_h*f*B^2 — doubling the flux must quadruple the per-cycle term."""
        n, f = 64, 100.0
        out = []
        for amp in (0.5, 1.0):
            X, Y = _sine_history(n, amp)
            _, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(1.0),
                                     0.01, f, n, central_difference(1e-4), _bertotti)
            out.append(pc)
        assert out[1] / out[0] == pytest.approx(4.0, rel=1e-6)

    def test_classical_eddy_scales_with_frequency_squared(self):
        """k_c*<(dB/dt)^2> — same waveform at twice the speed is 4x the loss.

        Encoded by halving dt, which is what doubling rpm does to the history.
        """
        n = 128
        X, Y = _sine_history(n, 1.0)
        mean = []
        for dt in (1e-4, 0.5e-4):
            cl, _ = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(1.0),
                                     0.01, 100.0, n, central_difference(dt), _bertotti)
            mean.append(float(np.mean(cl)))
        assert mean[1] / mean[0] == pytest.approx(4.0, rel=1e-6)

    def test_classical_eddy_matches_the_analytic_value(self):
        """For B = A*sin(wt): <(dB/dt)^2> = (A*w)^2/2, so

            P_cl = k_c/(2*pi^2) * (A*w)^2/2 * V

        Checks the constant, not just the scaling — a wrong 2*pi^2 would pass
        every ratio test above and still be wrong by a factor of twenty.
        """
        n, amp, dt = 256, 1.3, 1e-5
        X, Y = _sine_history(n, amp, n_elem=1)
        vol = 0.02 * 0.01 * 1.0                       # area * stack * sf
        w = 2 * np.pi / (n * dt)
        cl, _ = iron_loss_series(X, Y, np.array([0]), np.array([0.02]), _steel(1.0),
                                 0.01, 100.0, n, central_difference(dt), _bertotti)
        want = KC / TWO_PI_SQ * (amp * w) ** 2 / 2.0 * vol
        assert float(np.mean(cl)) == pytest.approx(want, rel=2e-3)

    def test_stacking_factor_scales_the_volume(self):
        """Only the steel dissipates; the insulation is dead volume."""
        n = 64
        X, Y = _sine_history(n, 1.0)
        loss = {}
        for sf in (1.0, 0.5):
            cl, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(sf),
                                      0.01, 100.0, n, central_difference(1e-4), _bertotti)
            loss[sf] = (float(np.mean(cl)), pc)
        assert loss[0.5][0] == pytest.approx(loss[1.0][0] * 0.5, rel=1e-9)
        assert loss[0.5][1] == pytest.approx(loss[1.0][1] * 0.5, rel=1e-9)

    def test_missing_stacking_factor_uses_one_documented_default(self):
        """The same constant used to be 0.95 in two places and 0.97 in a third.
        Whatever the value, there must be exactly one of it."""
        n = 64
        X, Y = _sine_history(n, 1.0)
        _, bare = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(None),
                                   0.01, 100.0, n, central_difference(1e-4), _bertotti)
        _, told = iron_loss_series(X, Y, np.arange(3), np.ones(3),
                                   _steel(DEFAULT_STACKING_FACTOR),
                                   0.01, 100.0, n, central_difference(1e-4), _bertotti)
        assert bare == pytest.approx(told, rel=1e-12)

    def test_empty_inputs_are_survivable(self):
        """A design with no rotor iron must not crash the loss pass."""
        n = 16
        for args in (([], [], np.array([], int)),
                     (np.zeros((n, 0)), np.zeros((n, 0)), np.array([], int))):
            cl, pc = iron_loss_series(args[0], args[1], args[2], np.ones(0), _steel(1.0),
                                      0.01, 100.0, n, central_difference(1e-4), _bertotti)
            assert cl.shape == (n,) and pc == 0.0

    def test_no_material_is_zero_not_a_crash(self):
        n = 16
        X, Y = _sine_history(n, 1.0)
        cl, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), None,
                                  0.01, 100.0, n, central_difference(1e-4), _bertotti)
        assert np.allclose(cl, 0.0) and pc == 0.0
