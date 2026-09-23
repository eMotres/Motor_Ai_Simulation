"""Standalone analytic checks for periodic terminal-flux work."""
from __future__ import annotations

import unittest

import numpy as np

from motor_ai_sim.simulation.torque_energy_diagnostic import (
    periodic_terminal_flux_work_per_mechanical_radian,
)


def _nonlinear_period(n: int, rpm: float = 1500.0, parallel: int = 1):
    direction = 1.0 if rpm > 0.0 else -1.0
    theta = direction * 2.0 * np.pi * np.arange(n) / n
    current = 4.0 + 1.5 * np.sin(theta + 0.3)
    psi_pm = 0.02 * np.cos(theta)
    inductance = 0.004 + 0.0008 * np.cos(2.0 * theta)
    cubic = 2e-5 + 4e-6 * np.sin(theta)
    flux = psi_pm + inductance * current + cubic * current**3
    d_psi_pm = -0.02 * np.sin(theta)
    d_inductance = -0.0016 * np.sin(2.0 * theta)
    d_cubic = 4e-6 * np.cos(theta)
    torque = (current * d_psi_pm + 0.5 * current**2 * d_inductance
              + 0.25 * current**4 * d_cubic)
    return (current[None, :] / parallel, flux[None, :], theta,
            float(np.mean(torque)))


def _diagnose(current, flux, angle, *, rpm=1500.0, **overrides):
    args = dict(
        rpm=rpm,
        pole_pairs=1,
        n_periods=1,
        drive_mode="current",
        periodic_window_certified=True,
        settled=True,
        conservative_lossless_certified=True,
    )
    args.update(overrides)
    return periodic_terminal_flux_work_per_mechanical_radian(
        current, flux, angle, **args)


class TestPeriodicTerminalFluxWork(unittest.TestCase):
    def test_nonlinear_periodic_work_matches_independent_coenergy_torque(self):
        errors = []
        for n in (256, 512, 1024, 2048):
            current, flux, angle, expected = _nonlinear_period(n)
            result = _diagnose(current, flux, angle)
            errors.append(abs(result.mean_work_per_mechanical_radian - expected))
        self.assertTrue(all(a > b for a, b in zip(errors, errors[1:])), errors)
        self.assertLess(errors[-1], 3e-8, errors)

    def test_fifth_and_seventh_harmonics_match_independent_means(self):
        n = 1440
        pole_pairs = 7
        angle = (2.0 * np.pi / pole_pairs) * np.arange(n) / n
        offsets = (0.0, 2.0 * np.pi / 3.0, -2.0 * np.pi / 3.0)
        electrical = pole_pairs * angle[None, :] - np.asarray(offsets)[:, None]
        cases = (
            ([(1, 0.02), (5, 0.002)], [(1, 10.0), (5, 2.0)], 2.31),
            ([(7, 0.003)], [(7, 2.0)], 0.441),
        )
        for flux_harmonics, current_harmonics, expected in cases:
            flux = sum(a * np.cos(k * electrical)
                       for k, a in flux_harmonics)
            current = -sum(a * np.sin(k * electrical)
                           for k, a in current_harmonics)
            dpsi_dmechanical_angle = -pole_pairs * sum(
                k * a * np.sin(k * electrical) for k, a in flux_harmonics)
            independent_mean = float(np.mean(np.sum(
                current * dpsi_dmechanical_angle, axis=0)))
            self.assertAlmostEqual(independent_mean, expected, places=12)
            got = _diagnose(current, flux, angle, pole_pairs=pole_pairs)
            self.assertAlmostEqual(
                got.mean_work_per_mechanical_radian, independent_mean, delta=1e-4)

    def test_parallel_branch_scaling_preserves_total_work(self):
        base = _nonlinear_period(512, parallel=1)
        want = _diagnose(base[0], base[1], base[2]).mean_work_per_mechanical_radian
        for branches in (2, 12):
            current, flux, angle, _ = _nonlinear_period(512, parallel=branches)
            got = _diagnose(current, flux, angle,
                            parallel_branches=branches).mean_work_per_mechanical_radian
            self.assertAlmostEqual(got, want, places=12)

    def test_reverse_speed_respects_path_orientation_and_current_sign(self):
        forward = _nonlinear_period(512, rpm=1500.0)
        reverse = _nonlinear_period(512, rpm=-1500.0)
        fwd = _diagnose(forward[0], forward[1], forward[2], rpm=1500.0)
        rev = _diagnose(reverse[0], reverse[1], reverse[2], rpm=-1500.0)
        self.assertAlmostEqual(
            rev.mean_work_per_mechanical_radian,
            fwd.mean_work_per_mechanical_radian,
            places=10,
        )

        theta = 2.0 * np.pi * np.arange(512) / 512
        psi = 0.03 * np.cos(theta)[None, :]
        current = -2.0 * np.sin(theta)[None, :]
        positive = _diagnose(current, psi, theta).mean_work_per_mechanical_radian
        negative = _diagnose(-current, psi, theta).mean_work_per_mechanical_radian
        self.assertAlmostEqual(positive, -negative, places=12)

    def test_refuses_uncertified_or_ineligible_windows(self):
        current, flux, angle, _ = _nonlinear_period(128)
        for overrides in (
            {"periodic_window_certified": False},
            {"settled": False},
            {"conservative_lossless_certified": False},
            {"drive_mode": "voltage"},
            {"drive_mode": "pwm_voltage"},
            {"eddy_coupled": True},
            {"rotor_eddy": True},
            {"demag": True},
            {"n_periods": 1.5},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                _diagnose(current, flux, angle, **overrides)
        with self.assertRaises(ValueError):
            _diagnose(current, flux, angle, rpm=0.0)

    def test_rejects_bad_shapes_nonuniform_nan_zero_and_coarse_series(self):
        current, flux, angle, _ = _nonlinear_period(128)
        with self.assertRaises(ValueError):
            _diagnose(current[:, :-1], flux, angle)
        with self.assertRaises(ValueError):
            _diagnose(current, flux[:, :-1], angle)
        with self.assertRaises(ValueError):
            _diagnose(current, flux, angle + np.linspace(0, 1e-4, angle.size))
        bad_current = current.copy(); bad_current[0, 3] = np.nan
        with self.assertRaises(ValueError):
            _diagnose(bad_current, flux, angle)
        with self.assertRaises(ValueError):
            _diagnose(np.zeros_like(current), flux, angle)
        with self.assertRaises(ValueError):
            _diagnose(current[:, :8], flux[:, :8], angle[:8])


if __name__ == "__main__":
    unittest.main()
