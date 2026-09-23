"""Small, exact lossless torque gates; no motor solve or live configuration.

Run standalone with ``python tests/test_torque_energy_validation.py``.  The
production helper is loaded directly to avoid importing the API or test suite.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

import numpy as np


def _hybrid_torque():
    path = Path(__file__).resolve().parents[1] / "src/motor_ai_sim/simulation/sb_postproc.py"
    spec = importlib.util.spec_from_file_location("torque_gate_sb_postproc", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.hybrid_torque


def _three_phase(harmonics, current_harmonics, n=720, pole_pairs=7):
    theta = 2 * np.pi * np.arange(n) / n
    offsets = (0.0, 2 * np.pi / 3, -2 * np.pi / 3)
    x = np.array([theta - offset for offset in offsets])
    psi = sum(a * np.cos(k * x) for k, a in harmonics)
    current = -sum(a * np.sin(k * x) for k, a in current_harmonics)
    dpsi_dtheta_m = -pole_pairs * sum(k * a * np.sin(k * x) for k, a in harmonics)
    torque = np.sum(current * dpsi_dtheta_m, axis=0)
    return psi, current, torque


class TorqueEnergyValidation(unittest.TestCase):
    def test_spatial_harmonic_fixed_current_coenergy_derivative(self):
        cases = [
            ([(1, .02)], [(1, 10)], 2.1),
            ([(1, .02), (5, .002)], [(1, 10), (5, 2)], 2.31),
            ([(7, .003)], [(7, 2)], .441),
        ]
        for flux, current_coeffs, expected in cases:
            with self.subTest(flux=flux):
                psi, current, torque = _three_phase(flux, current_coeffs)
                self.assertAlmostEqual(float(torque.mean()), expected, places=12)
                # W'_PM = sum(i_k psi_PM,k); differentiate angle at fixed i.
                h = 1e-5
                x = 2 * np.pi * np.arange(720) / 720
                offsets = (0.0, 2 * np.pi / 3, -2 * np.pi / 3)
                def coenergy(shift):
                    return sum(current[j] * sum(a * np.cos(k * (x - off + shift))
                                                    for k, a in flux)
                               for j, off in enumerate(offsets))
                finite_difference = 7 * (coenergy(h) - coenergy(-h)) / (2 * h)
                np.testing.assert_allclose(finite_difference, torque, rtol=2e-9, atol=1e-9)

    def test_current_position_sign_zero_sequence_and_parallel_paths(self):
        psi, current, positive = _three_phase([(1, .02), (5, .002)],
                                               [(1, 10), (5, 2)])
        self.assertAlmostEqual(float(positive.mean()), 2.31, places=12)
        x = np.array([2 * np.pi * np.arange(720) / 720 - off
                      for off in (0, 2 * np.pi / 3, -2 * np.pi / 3)])
        dpsi_forward = -7 * (.02 * np.sin(x) + 5 * .002 * np.sin(5*x))
        self.assertAlmostEqual(float(np.sum((-current) * dpsi_forward, axis=0).mean()),
                               -2.31, places=12)
        # Reverse physical position, with the same frozen currents.
        h = 1e-5
        psi_reverse_plus = .02*np.cos(x-h) + .002*np.cos(5*(x-h))
        psi_reverse_minus = .02*np.cos(x+h) + .002*np.cos(5*(x+h))
        reversed_torque = 7*np.sum(current*(psi_reverse_plus-psi_reverse_minus), axis=0)/(2*h)
        self.assertAlmostEqual(float(reversed_torque.mean()), -2.31, places=9)
        # Equal phase currents carry no torque for this three-phase PM linkage.
        zero_sequence = current + 3.0 * np.sin(3 * np.arange(720) * 2 * np.pi / 720)
        dpsi = dpsi_forward
        np.testing.assert_allclose(np.sum(zero_sequence * dpsi, axis=0), positive,
                                   atol=1e-13)
        # Branch linkage stays fixed; n parallel branches each carry I_phase/n.
        for branches in (1, 2, 12):
            branch_torque = branches * np.sum((current / branches) * dpsi, axis=0)
            np.testing.assert_allclose(branch_torque, positive, atol=1e-13)

    def test_nonlinear_position_dependent_coenergy_and_storage(self):
        theta = 2 * np.pi * np.arange(4096) / 4096
        current = 4.0 + 1.5 * np.sin(theta + .3)
        psi_pm = .02 * np.cos(theta)
        inductance = .004 + .0008 * np.cos(2 * theta)
        cubic = 2e-5 + 4e-6 * np.sin(theta)
        dpsi_pm = -.02 * np.sin(theta)
        d_inductance = -.0016 * np.sin(2 * theta)
        d_cubic = 4e-6 * np.cos(theta)
        d_current = 1.5 * np.cos(theta + .3)
        flux = psi_pm + inductance * current + cubic * current**3
        coenergy = current * psi_pm + .5 * inductance * current**2 + .25 * cubic * current**4
        storage = current * flux - coenergy
        torque = current * dpsi_pm + .5 * current**2 * d_inductance + .25 * current**4 * d_cubic
        # Finite difference displaces position while freezing current.
        h = 1e-5
        def coenergy_at(position):
            return (current * .02 * np.cos(position)
                    + .5 * (.004 + .0008 * np.cos(2 * position)) * current**2
                    + .25 * (2e-5 + 4e-6 * np.sin(position)) * current**4)
        np.testing.assert_allclose((coenergy_at(theta + h) - coenergy_at(theta - h)) / (2*h),
                                   torque, rtol=3e-9, atol=1e-10)
        dflux = (dpsi_pm + d_inductance * current + inductance * d_current
                 + d_cubic * current**3 + 3 * cubic * current**2 * d_current)
        terminal_work = current * dflux
        dstorage = (.5 * d_inductance * current**2
                    + inductance * current * d_current
                    + .75 * d_cubic * current**4
                    + 3 * cubic * current**3 * d_current)
        np.testing.assert_allclose(terminal_work, dstorage + torque,
                                   rtol=2e-13, atol=1e-13)
        self.assertGreater(float(np.max(np.abs(dstorage))), .01)
        self.assertGreater(float(np.max(np.abs(terminal_work - torque))), .01)
        self.assertAlmostEqual(float(dstorage.mean()), 0.0, places=10)
        self.assertAlmostEqual(float(terminal_work.mean()), float(torque.mean()), places=10)
        # Storage is periodic and is not the linear shortcut 0.5*i*psi.
        self.assertGreater(float(np.max(np.abs(storage - .5 * current * flux))), 1e-3)

    def test_actual_helper_documents_harmonic_limit_without_expected_failure(self):
        helper = _hybrid_torque()
        psi, current, exact = _three_phase([(1, .02), (5, .002)],
                                            [(1, 10), (5, 2)])
        output, method = helper(*psi, *current, np.zeros(720), 7)
        self.assertEqual(method, "energy_mean+maxwell_ripple")
        self.assertAlmostEqual(float(np.mean(output)), 2.058, places=12)
        self.assertAlmostEqual(float(np.mean(exact)), 2.31, places=12)
        self.assertAlmostEqual(float(np.mean(exact) - np.mean(output)), .252, places=12)
        # The sinusoidal subcase is inside the helper's documented scope.
        psi, current, exact = _three_phase([(1, .02)], [(1, 10)])
        output, _ = helper(*psi, *current, np.zeros(720), 7)
        self.assertAlmostEqual(float(np.mean(output)), float(np.mean(exact)), places=12)
        for branches in (1, 2, 12):
            per_branch = current / branches
            output, method = helper(*psi, *per_branch, np.zeros(720), 7,
                                    n_parallel=branches)
            if branches < 12:
                self.assertEqual(method, "energy_mean+maxwell_ripple")
                self.assertAlmostEqual(float(np.mean(output)), 2.1, places=12)
            else:
                # Synthetic zero Maxwell exposes the legacy 1 A branch gate.
                self.assertEqual(method, "maxwell_stress")
                self.assertEqual(float(np.mean(output)), 0.0)


if __name__ == "__main__":
    unittest.main()
