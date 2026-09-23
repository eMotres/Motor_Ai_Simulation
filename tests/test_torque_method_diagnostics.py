"""Standalone checks for additive, explicitly uncertified torque diagnostics.

Run directly with Python 3.11; this avoids application config and FEM imports.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import unittest

import numpy as np


_MODULE_PATH = (Path(__file__).resolve().parents[1]
                / "src/motor_ai_sim/simulation/sb_postproc.py")
_SPEC = importlib.util.spec_from_file_location("torque_method_diag_postproc", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
hybrid_torque = _MODULE.hybrid_torque
torque_method_diagnostics = _MODULE.torque_method_diagnostics


def _fundamental(branch_peak: float, n: int = 720):
    theta = 2.0 * np.pi * np.arange(n) / n
    offsets = (0.0, 2.0 * np.pi / 3.0, -2.0 * np.pi / 3.0)
    phase = np.array([theta - offset for offset in offsets])
    psi = 0.02 * np.cos(phase)
    current = -branch_peak * np.sin(phase)
    torque = np.sum(current * (-7.0 * 0.02 * np.sin(phase)), axis=0)
    maxwell = torque + 0.12 + 0.01 * np.cos(6.0 * theta)
    return psi, current, maxwell


def _diagnose(psi, current, maxwell, n_parallel=1, selected="maxwell_stress"):
    return torque_method_diagnostics(
        *psi, *current, maxwell, 7, n_parallel=n_parallel,
        selected_method=selected)


class TestTorqueMethodDiagnostics(unittest.TestCase):
    def test_p2_wiring_uses_raw_maxwell_and_selected_method(self):
        solver_path = (_MODULE_PATH.parent / "fem_solver_2d.py")
        tree = ast.parse(solver_path.read_text(encoding="utf-8"))
        diagnostic_calls = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name)
                    and target.id == "_torque_method_diag"
                    for target in node.targets)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_torque_method_diagnostics"
        ]
        self.assertEqual(len(diagnostic_calls), 1)
        call = diagnostic_calls[0]
        self.assertGreaterEqual(len(call.args), 8)
        self.assertEqual([arg.id for arg in call.args[:8]
                          if isinstance(arg, ast.Name)],
                         ["_psiA", "_psiB", "_psiC", "_IA", "_IB", "_IC",
                          "_T2raw", "pole_pairs"])
        self.assertTrue(any(keyword.arg == "selected_method"
                            and isinstance(keyword.value, ast.Name)
                            and keyword.value.id == "_torque_method"
                            for keyword in call.keywords))
        result_field = any(
            isinstance(node, ast.Dict)
            and any(isinstance(key, ast.Constant)
                    and key.value == "torque_method_diagnostics"
                    and isinstance(value, ast.Name)
                    and value.id == "_torque_method_diag"
                    for key, value in zip(node.keys, node.values))
            for node in ast.walk(tree))
        self.assertTrue(result_field)

    def test_peak_selector_boundaries_and_selected_formula_is_unchanged(self):
        for peak in (0.999, 1.0, 1.001):
            with self.subTest(peak=peak):
                psi, current, maxwell = _fundamental(peak)
                diagnostic = _diagnose(psi, current, maxwell)
                selected, method = hybrid_torque(
                    *psi, *current, maxwell, 7, n_parallel=1)
                self.assertEqual(diagnostic["validation_status"], "uncertified")
                self.assertAlmostEqual(diagnostic["per_branch_peak_current_A"], peak)
                self.assertEqual(diagnostic["legacy_selector_would_use_space_vector_mean"],
                                 peak > 1.0)
                if peak <= 1.0:
                    self.assertEqual(method, "maxwell_stress")
                    np.testing.assert_array_equal(selected, maxwell)
                    self.assertAlmostEqual(diagnostic["raw_maxwell_mean_Nm"],
                                           float(maxwell.mean()))
                else:
                    self.assertEqual(method, "energy_mean+maxwell_ripple")
                    np.testing.assert_allclose(
                        selected, maxwell - maxwell.mean()
                        + diagnostic["space_vector_mean_candidate_Nm"],
                        rtol=0, atol=1e-14)
                self.assertIsNone(diagnostic["certified_energy_balance_Nm"])

    def test_parallel_scaling_is_reported_without_claiming_validation(self):
        psi, current, maxwell = _fundamental(2.0)
        one = _diagnose(psi, current, maxwell, n_parallel=1)
        two = _diagnose(psi, current, maxwell, n_parallel=2)
        self.assertAlmostEqual(two["space_vector_mean_candidate_Nm"],
                               2.0 * one["space_vector_mean_candidate_Nm"],
                               places=12)
        self.assertEqual(two["validation_status"], "uncertified")

    def test_zero_current_is_not_cogging_validation(self):
        psi, _, maxwell = _fundamental(0.0)
        diagnostic = _diagnose(psi, np.zeros((3, 720)), maxwell)
        self.assertEqual(diagnostic["space_vector_mean_candidate_Nm"], 0.0)
        self.assertAlmostEqual(diagnostic["raw_maxwell_mean_Nm"],
                               float(maxwell.mean()))
        self.assertEqual(diagnostic["validation_status"], "uncertified")
        self.assertIn("do not certify", diagnostic["validation_reason"])

    def test_malformed_or_nonfinite_inputs_return_json_safe_unavailable_values(self):
        psi, current, maxwell = _fundamental(2.0)
        cases = (
            (psi, current[:, :-1], maxwell),
            (psi, current, np.full_like(maxwell, np.nan)),
            (psi, current.reshape(3, 240, 3), maxwell),
        )
        for bad_psi, bad_current, bad_maxwell in cases:
            with self.subTest(shape=np.shape(bad_current)):
                diagnostic = _diagnose(bad_psi, bad_current, bad_maxwell)
                self.assertEqual(diagnostic["validation_status"], "uncertified")
                self.assertIsNone(diagnostic["space_vector_mean_candidate_Nm"])
                self.assertIsNone(diagnostic["raw_maxwell_mean_Nm"])
                self.assertIsNotNone(diagnostic["diagnostic_input_reason"])
                self.assertNotIn("NaN", repr(diagnostic))

        huge = np.full((3, 720), 1e308)
        diagnostic = _diagnose(huge, huge, np.full(720, 1e308))
        self.assertIsNone(diagnostic["space_vector_mean_candidate_Nm"])
        self.assertIsNotNone(diagnostic["diagnostic_input_reason"])
        json.dumps(diagnostic, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
