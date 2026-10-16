"""Focused consistency checks for BH curves with an implicit origin."""
import unittest

import numpy as np

from motor_ai_sim.simulation.field_ops import (
    MU0,
    _mu_r_from_bh,
    _mu_r_from_bh_vec,
)
from motor_ai_sim.simulation.static3d.torque3d import _curve_coenergy


class TestBHOriginConsistency(unittest.TestCase):
    def _assert_helpers_match(self, curve, fields):
        vector = _mu_r_from_bh_vec(curve, fields)
        scalar = np.array([_mu_r_from_bh(curve, float(b)) for b in fields])
        np.testing.assert_allclose(vector, scalar, rtol=1e-12, atol=0.0)

    def test_linear_curve_without_origin_matches_coenergy_below_first_knot(self):
        mu_r = 1000.0
        curve = [(b / (MU0 * mu_r), b) for b in (0.1, 0.2, 0.4, 1.0)]
        fields = np.array([0.025, 0.05, 0.075, 0.1])

        self._assert_helpers_match(curve, fields)
        np.testing.assert_allclose(
            _mu_r_from_bh_vec(curve, fields), mu_r, rtol=1e-12, atol=0.0)
        np.testing.assert_allclose(
            _curve_coenergy(curve, fields),
            0.5 * fields**2 / (MU0 * mu_r),
            rtol=1e-12,
            atol=0.0,
        )

    def test_nonlinear_curve_uses_origin_segment_then_continues_at_knot(self):
        curve = [(100.0, 0.1), (400.0, 0.4), (2000.0, 1.0), (6000.0, 1.5)]
        fields = np.array([0.025, 0.05, 0.075, 0.1, 0.100001, 0.2])
        expected_low_mu = 0.1 / (MU0 * 100.0)

        self._assert_helpers_match(curve, fields)
        got = _mu_r_from_bh_vec(curve, fields)
        np.testing.assert_allclose(got[:3], expected_low_mu, rtol=1e-12)
        np.testing.assert_allclose(got[3], expected_low_mu, rtol=1e-12)
        np.testing.assert_allclose(
            _curve_coenergy(curve, fields[:3]),
            0.5 * 1000.0 * fields[:3] ** 2,
            rtol=1e-12,
            atol=0.0,
        )

        # H recovered from μ has the same limit on each side of the first knot.
        h_eff = fields / (MU0 * got)
        self.assertAlmostEqual(float(h_eff[2]), 75.0, places=10)
        self.assertAlmostEqual(float(h_eff[3]), 100.0, places=10)
        self.assertAlmostEqual(float(h_eff[4]), 100.001, places=6)

    def test_explicit_origin_and_high_field_behavior_are_unchanged(self):
        implicit = [(100.0, 0.1), (400.0, 0.4), (2000.0, 1.0)]
        explicit = [(0.0, 0.0), *implicit]
        fields = np.array([0.1, 0.2, 0.4, 1.0, 1.5])

        np.testing.assert_array_equal(
            _mu_r_from_bh_vec(implicit, fields),
            _mu_r_from_bh_vec(explicit, fields),
        )
        np.testing.assert_array_equal(
            np.array([_mu_r_from_bh(implicit, float(b)) for b in fields]),
            np.array([_mu_r_from_bh(explicit, float(b)) for b in fields]),
        )

        expected_tail_h = 2000.0 + (1.5 - 1.0) / MU0
        expected_tail_mu = 1.5 / (MU0 * expected_tail_h)
        self.assertAlmostEqual(
            float(_mu_r_from_bh_vec(implicit, np.array([1.5]))[0]),
            expected_tail_mu,
            places=12,
        )

    def test_zero_and_tiny_field_cutoffs_are_preserved(self):
        implicit = [(100.0, 0.1), (400.0, 0.4)]
        explicit = [(0.0, 0.0), *implicit]
        fields = np.array([0.0, 1e-13, 1e-12])

        for curve in (implicit, explicit):
            self.assertEqual(_mu_r_from_bh(curve, 0.0), 1.0)
            self.assertEqual(_mu_r_from_bh(curve, 1e-12), 1.0)
            np.testing.assert_array_equal(_mu_r_from_bh_vec(curve, fields), 1.0)


if __name__ == "__main__":
    unittest.main()
