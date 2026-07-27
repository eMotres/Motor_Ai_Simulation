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


class TestProximityLoss:
    """AC copper — same model for both element orders now."""

    @staticmethod
    def _radial_history(n, amp, n_elem=4):
        """Purely RADIAL field: only the d_r term may contribute."""
        t = np.arange(n) / n * 2 * np.pi
        # elements on the +x axis, so r_hat = +x and B = (amp*sin, 0) is radial
        cen = np.vstack([np.linspace(1.0, 2.0, n_elem), np.zeros(n_elem)])
        X = np.outer(amp * np.sin(t), np.ones(n_elem))
        Y = np.zeros_like(X)
        return X, Y, cen

    def test_radial_field_uses_the_radial_dimension_only(self):
        """The direction split is the whole point: a field with no tangential
        component must not be charged the tangential conductor dimension. The
        single-d slab form gets this wrong and over-counts a tall thin bar."""
        n = 64
        X, Y, cen = self._radial_history(n, 1.0)
        from motor_ai_sim.simulation.losses import proximity_loss_series
        _, with_t = proximity_loss_series(
            X, Y, np.arange(4), cen, np.ones(4), 5.8e7, 2e-3, 9e-3,
            0.01, n, central_difference(1e-4))
        _, without_t = proximity_loss_series(
            X, Y, np.arange(4), cen, np.ones(4), 5.8e7, 2e-3, 0.0,
            0.01, n, central_difference(1e-4))
        assert with_t == pytest.approx(without_t, rel=1e-9)

    def test_loss_scales_with_the_dimension_squared(self):
        n = 64
        X, Y, cen = self._radial_history(n, 1.0)
        from motor_ai_sim.simulation.losses import proximity_loss_series
        out = []
        for d in (1e-3, 2e-3):
            _, avg = proximity_loss_series(
                X, Y, np.arange(4), cen, np.ones(4), 5.8e7, d, 0.0,
                0.01, n, central_difference(1e-4))
            out.append(avg)
        assert out[1] / out[0] == pytest.approx(4.0, rel=1e-9)

    def test_wire_split_cuts_the_width_term(self):
        """N transposed strips across the width -> that loss term falls as N^2."""
        from motor_ai_sim.simulation.losses import copper_ac_dims
        geo1 = {"wire_width": 2.0, "wire_height": 0.5, "wire_split": 1}
        geo4 = dict(geo1, wire_split=4)
        _, d1, _ = copper_ac_dims(geo1, 120.0, 50.0, 1.724e-8, 0.00393, 4e-7 * math.pi)
        _, d4, _ = copper_ac_dims(geo4, 120.0, 50.0, 1.724e-8, 0.00393, 4e-7 * math.pi)
        assert d4 == pytest.approx(d1 / 4.0, rel=1e-9)

    def test_skin_depth_caps_the_dimension(self):
        """At high frequency the field cannot reach the middle of the bar, so a
        wider conductor buys no extra loss — without the cap the model would
        keep charging for copper the field never sees."""
        from motor_ai_sim.simulation.losses import copper_ac_dims
        geo = {"wire_width": 50.0, "wire_height": 50.0, "wire_split": 1}
        _, d_r, d_t = copper_ac_dims(geo, 120.0, 20000.0, 1.724e-8, 0.00393,
                                     4e-7 * math.pi)
        assert d_r < 50e-3 and d_t < 50e-3, "the skin cap never engaged"
        assert d_r == pytest.approx(d_t, rel=1e-12), "both are capped by the same delta"

    def test_zero_conductivity_is_zero_loss(self):
        n = 16
        X, Y, cen = self._radial_history(n, 1.0)
        from motor_ai_sim.simulation.losses import proximity_loss_series
        ser, avg = proximity_loss_series(
            X, Y, np.arange(4), cen, np.ones(4), 0.0, 2e-3, 9e-3,
            0.01, n, central_difference(1e-4))
        assert avg == 0.0 and len(ser) == n
