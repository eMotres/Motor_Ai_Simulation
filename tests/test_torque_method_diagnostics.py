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
    theta = 2.0 * np.pi * np.arange(len(maxwell)) / (len(maxwell) * 7)
    return torque_method_diagnostics(
        *psi, *current, maxwell, 7, n_parallel=n_parallel,
        selected_method=selected, mechanical_angle_rad=theta,
        imposed_current_drive=True, all_frames_converged=True,
        integer_period_window=True)


def _eligible_kwargs(peak):
    psi, current, maxwell = _fundamental(peak)
    theta = 2.0 * np.pi * np.arange(len(maxwell)) / (len(maxwell) * 7)
    return psi, current, maxwell, theta


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
        hybrid_calls = [node for node in ast.walk(tree)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "_hybrid_torque"]
        self.assertEqual(len(hybrid_calls), 1)
        self.assertTrue(any(keyword.arg is None
                            and isinstance(keyword.value, ast.Name)
                            and keyword.value.id == "_torque_method_args"
                            for keyword in hybrid_calls[0].keywords))
        kwargs_assign = next(
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name)
                    and target.id == "_torque_method_args"
                    for target in node.targets)
            and isinstance(node.value, ast.Dict))
        kw = {key.value: value for key, value
              in zip(kwargs_assign.keys, kwargs_assign.values)
              if isinstance(key, ast.Constant)}
        self.assertIsInstance(kw["mechanical_angle_rad"], ast.Name)
        self.assertEqual(kw["mechanical_angle_rad"].id, "_theta_samples")
        self.assertTrue(any(isinstance(node, ast.Name) and node.id == "_frame_converged"
                            for node in ast.walk(kw["all_frames_converged"])))
        self.assertTrue(any(isinstance(node, ast.Name) and node.id == "_vdrive"
                            for node in ast.walk(kw["imposed_current_drive"])))
        self.assertTrue(any(isinstance(node, ast.Name) and node.id == "rotor_eddy"
                            for node in ast.walk(kw["rotor_eddy"])))
        self.assertTrue(any(isinstance(node, ast.Name) and node.id == "demag"
                            for node in ast.walk(kw["demag"])))
        self.assertTrue(any(isinstance(node, ast.Name) and node.id == "frozen_nu"
                            for node in ast.walk(kw["frozen_nu"])))
        result_field = any(
            isinstance(node, ast.Dict)
            and any(isinstance(key, ast.Constant)
                    and key.value == "torque_method_diagnostics"
                    and isinstance(value, ast.Name)
                    and value.id == "_torque_method_diag"
                    for key, value in zip(node.keys, node.values))
            for node in ast.walk(tree))
        self.assertTrue(result_field)

    def test_current_boundary_is_continuous_and_legacy_threshold_is_diagnostic_only(self):
        for peak in (0.0, 0.5, 1.0, 1.001):
            with self.subTest(peak=peak):
                psi, current, maxwell, theta = _eligible_kwargs(peak)
                diagnostic = _diagnose(psi, current, maxwell)
                selected, method = hybrid_torque(
                    *psi, *current, maxwell, 7, n_parallel=1,
                    mechanical_angle_rad=theta, imposed_current_drive=True,
                    all_frames_converged=True, integer_period_window=True)
                self.assertEqual(diagnostic["validation_status"], "uncertified")
                self.assertAlmostEqual(diagnostic["per_branch_peak_current_A"], peak)
                self.assertEqual(diagnostic["legacy_selector_would_use_space_vector_mean"],
                                 peak > 1.0)
                self.assertEqual(method, "terminal_work_mean+maxwell_ripple")
                self.assertTrue(diagnostic["terminal_work_method_eligible"])
                self.assertAlmostEqual(
                    diagnostic["terminal_work_mean_candidate_Nm"], 0.21 * peak,
                    places=12)
                self.assertAlmostEqual(float(np.mean(selected)), 0.21 * peak,
                                       places=12)
                np.testing.assert_allclose(
                    selected - np.mean(selected), maxwell - np.mean(maxwell),
                    rtol=0, atol=1e-14)
                self.assertIsNone(diagnostic["certified_energy_balance_Nm"])

    def test_ineligible_modes_keep_raw_maxwell_series_and_report_reason(self):
        psi, current, maxwell, theta = _eligible_kwargs(0.5)
        cases = (
            {"imposed_current_drive": False},
            {"eddy": True}, {"rotor_eddy": True}, {"demag": True},
            {"frozen_nu": True},
            {"all_frames_converged": False},
            {"integer_period_window": False},
        )
        for override in cases:
            with self.subTest(override=override):
                flags = dict(imposed_current_drive=True, all_frames_converged=True,
                             integer_period_window=True)
                flags.update(override)
                selected, method = hybrid_torque(
                    *psi, *current, maxwell, 7, mechanical_angle_rad=theta,
                    **flags)
                diagnostic = torque_method_diagnostics(
                    *psi, *current, maxwell, 7, mechanical_angle_rad=theta,
                    **flags)
                self.assertEqual(method, "maxwell_stress")
                np.testing.assert_array_equal(selected, maxwell)
                self.assertFalse(diagnostic["terminal_work_method_eligible"])
                self.assertTrue(diagnostic["terminal_work_eligibility_reason"])
                if override.get("frozen_nu"):
                    self.assertIn("frozen permeability", diagnostic[
                        "terminal_work_eligibility_reason"])

    def test_parallel_scaling_is_reported_without_claiming_validation(self):
        psi, current, maxwell = _fundamental(2.0)
        one = _diagnose(psi, current, maxwell, n_parallel=1)
        two = _diagnose(psi, current, maxwell, n_parallel=2)
        self.assertAlmostEqual(two["space_vector_mean_candidate_Nm"],
                               2.0 * one["space_vector_mean_candidate_Nm"],
                               places=12)
        self.assertEqual(two["validation_status"], "uncertified")
        self.assertAlmostEqual(two["terminal_work_mean_candidate_Nm"],
                               2.0 * one["terminal_work_mean_candidate_Nm"],
                               places=12)

    def test_zero_current_is_not_cogging_validation(self):
        psi, _, maxwell = _fundamental(0.0)
        diagnostic = _diagnose(psi, np.zeros((3, 720)), maxwell)
        self.assertEqual(diagnostic["space_vector_mean_candidate_Nm"], 0.0)
        self.assertAlmostEqual(diagnostic["raw_maxwell_mean_Nm"],
                               float(maxwell.mean()))
        self.assertEqual(diagnostic["validation_status"], "uncertified")
        self.assertIn("do not certify", diagnostic["validation_reason"])
        self.assertTrue(diagnostic["terminal_work_method_eligible"])
        self.assertEqual(diagnostic["terminal_work_mean_candidate_Nm"], 0.0)

    def test_malformed_or_nonfinite_inputs_return_json_safe_unavailable_values(self):
        psi, current, maxwell, theta = _eligible_kwargs(2.0)
        nonuniform_steps = np.full(len(theta) - 1, 2*np.pi/(len(theta)*7))
        nonuniform_steps[-1] += 1e-7
        nonuniform_theta = theta[0] + np.r_[0.0, np.cumsum(nonuniform_steps)]
        cases = (
            (psi, current[:, :-1], maxwell, theta),
            (psi, current, np.full_like(maxwell, np.nan), theta),
            (psi, current.reshape(3, 240, 3), maxwell, theta),
            (psi, current, maxwell, theta * 1.01),
            (psi, current, maxwell, nonuniform_theta),
        )
        for bad_psi, bad_current, bad_maxwell, bad_theta in cases:
            with self.subTest(shape=np.shape(bad_current)):
                diagnostic = torque_method_diagnostics(
                    *bad_psi, *bad_current, bad_maxwell, 7,
                    mechanical_angle_rad=bad_theta, imposed_current_drive=True,
                    all_frames_converged=True, integer_period_window=True)
                self.assertEqual(diagnostic["validation_status"], "uncertified")
                self.assertFalse(diagnostic["terminal_work_method_eligible"])
                self.assertIsNone(diagnostic["terminal_work_mean_candidate_Nm"])
                self.assertTrue(
                    diagnostic["diagnostic_input_reason"] is not None
                    or diagnostic["terminal_work_eligibility_reason"] is not None)
                self.assertNotIn("NaN", repr(diagnostic))

        huge = np.full((3, 720), 1e308)
        diagnostic = _diagnose(huge, huge, np.full(720, 1e308))
        self.assertIsNone(diagnostic["space_vector_mean_candidate_Nm"])
        self.assertIsNotNone(diagnostic["diagnostic_input_reason"])
        json.dumps(diagnostic, allow_nan=False)

    def test_invalid_terminal_work_angles_raise_value_error(self):
        psi, current, _, theta = _eligible_kwargs(0.5)
        work = _MODULE.terminal_work_mean
        nonuniform_steps = np.full(len(theta) - 1, 2*np.pi/(len(theta)*7))
        nonuniform_steps[-1] += 1e-7
        nonuniform_theta = theta[0] + np.r_[0.0, np.cumsum(nonuniform_steps)]
        bad = (
            theta * 1.01,
            nonuniform_theta,
            np.full_like(theta, np.nan),
        )
        for angle in bad:
            with self.subTest(angle=angle[:2]):
                with self.assertRaises(ValueError):
                    work(*psi, *current, angle, 7)


if __name__ == "__main__":
    unittest.main()
