"""Unit tests for the physics primitives — the point of pulling them out.

These run in milliseconds against analytic answers, where the regression suite
runs the whole solver for 90 s and can only say "something moved". Both are
needed: this one says WHICH primitive broke.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation.field_ops import (
    ALPHA_CU, MU0, RHO_CU_20, _b_from_bh_at_H, _mu_r_from_bh,
    _mu_r_from_bh_vec, _snap_steps_to_nodes, band_limit_torque,
)


class TestBHCurve:
    """mu_r from a tabulated B-H curve."""

    # A deliberately simple curve: linear at mu_r = 1000 up to 1 T, then flat
    # (fully saturated) — so the expected answers are computable by hand.
    CURVE = [(0.0, 0.0), (1.0 / (1000 * MU0), 1.0), (1e6, 1.0 + MU0 * 1e6)]

    def test_linear_region(self):
        # halfway up the linear leg the slope is still mu_r = 1000
        mu = _mu_r_from_bh(self.CURVE, 0.5)
        assert mu == pytest.approx(1000.0, rel=0.02)

    def test_saturated_region_tends_to_air(self):
        """Past the knee the incremental permeability must fall toward 1.

        This is the property the Picard depends on: if a saturated element kept
        reporting a high mu_r the iteration would never converge, and torque
        would be optimistic exactly where the iron is working hardest.
        """
        mu = _mu_r_from_bh(self.CURVE, 5.0)
        assert 1.0 <= mu < 50.0

    def test_never_below_vacuum(self):
        """mu_r < 1 is unphysical and would make the stiffness matrix lie."""
        for b in (0.0, 1e-9, 0.5, 1.0, 2.0, 10.0, 1e3):
            assert _mu_r_from_bh(self.CURVE, b) >= 1.0

    def test_vectorised_matches_scalar(self):
        """The Picard uses the vector form; a drift between the two would be a
        silent per-element error, invisible until the torque moved."""
        bs = np.array([0.0, 0.2, 0.5, 0.9, 1.0, 1.5, 3.0])
        vec = _mu_r_from_bh_vec(self.CURVE, bs)
        sca = np.array([_mu_r_from_bh(self.CURVE, float(b)) for b in bs])
        assert np.allclose(vec, sca, rtol=1e-9, atol=1e-9)

    def test_b_at_h_interpolates_and_is_monotone(self):
        hs = [h for h, _ in self.CURVE]
        prev = -np.inf
        for h in (hs[0], hs[1] * 0.5, hs[1], hs[1] * 2, hs[-1]):
            b = _b_from_bh_at_H(self.CURVE, h)
            assert b >= prev - 1e-12, "B(H) must not decrease"
            prev = b


class TestBandLimitTorque:
    """The 6*k band-limit that separates real ripple from slip-band hash."""

    @staticmethod
    def _series(n_per, n_periods, orders):
        n = int(n_per * n_periods)
        t = np.arange(n) / n_per * 2 * np.pi
        y = np.ones(n) * 10.0
        for k, amp in orders.items():
            y = y + amp * np.cos(k * t)
        return y.tolist()

    def test_mean_is_preserved_exactly(self):
        """The filter may only touch the AC content. If it moved the mean it
        would silently rewrite the headline torque."""
        y = self._series(48, 1, {6: 1.0, 5: 0.7, 13: 0.4})
        filt, _, _, _ = band_limit_torque(y, 48, 1)
        assert np.mean(filt) == pytest.approx(np.mean(y), rel=1e-12)

    def test_keeps_order_6_drops_order_5(self):
        """A balanced 3-phase machine cannot produce order 5; whatever sits
        there is numerical and must go, while order 6 is real cogging."""
        keep = self._series(48, 1, {6: 1.0})
        junk = self._series(48, 1, {6: 1.0, 5: 1.0})
        f_keep, _, _, _ = band_limit_torque(keep, 48, 1)
        f_junk, _, _, _ = band_limit_torque(junk, 48, 1)
        assert np.allclose(f_keep, f_junk, atol=1e-9), \
            "the order-5 content should have been removed entirely"

    def test_raw_ripple_exceeds_filtered_when_junk_present(self):
        y = self._series(48, 1, {6: 0.5, 5: 0.5, 7: 0.5})
        _, rip_filt, rip_raw, _ = band_limit_torque(y, 48, 1)
        assert rip_raw > rip_filt


class TestCopperConstants:
    def test_resistivity_rises_with_temperature(self):
        """rho(T) = rho20 * (1 + alpha*(T-20)) — the sign matters: getting it
        backwards makes a hot machine look more efficient than a cold one."""
        rho20 = RHO_CU_20
        rho120 = RHO_CU_20 * (1.0 + ALPHA_CU * (120.0 - 20.0))
        assert rho120 > rho20
        assert rho120 / rho20 == pytest.approx(1.393, rel=1e-3)


class TestStepSnapping:
    def test_snaps_to_a_divisor(self):
        """The rotor only exists at slip-node angles, so the step count must
        divide the node count or frames land between defined positions."""
        nodes = 144
        for req in (10, 13, 20, 25, 50, 100, 200):
            got = _snap_steps_to_nodes(req, nodes)
            assert nodes % got == 0, f"{req} -> {got} does not divide {nodes}"

    def test_exact_divisor_is_left_alone(self):
        for d in (12, 16, 18, 24, 36, 48, 72, 144):
            assert _snap_steps_to_nodes(d, 144) == d
