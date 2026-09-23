"""Synthetic offline tests for production terminal-work sampling sensitivity."""
from __future__ import annotations

import contextlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from io import StringIO

import numpy as np

from scripts import torque_terminal_work_sampling_review as audit


def _pair(torque_fn=None):
    reviews, frame_sets = [], []
    shifts_all = list(range(120))
    step = 2.0 * math.pi / 1680.0
    psi_all, current_all, torque_all = [], [], []
    for shift in shifts_all:
        angle = shift * step
        phase = 14.0 * angle + math.radians(60.0)
        phase_offsets = (0.0, 2.0 * math.pi / 3.0, -2.0 * math.pi / 3.0)
        current = audit.CURRENT_PEAK_A * np.cos(phase - np.asarray(phase_offsets))
        psi = 0.025 * np.cos(14.0 * angle - np.asarray(phase_offsets)) \
            + 0.001 * np.cos(5.0 * (14.0 * angle - np.asarray(phase_offsets)))
        torque = float(torque_fn(shift) if torque_fn else 40.0 + 0.1 * math.sin(2 * math.pi * shift / 120))
        psi_all.append(psi.tolist())
        current_all.append(current.tolist())
        torque_all.append(torque)
    for parity, label in ((0, "even"), (1, "odd")):
        shifts = list(range(parity, 120, 2))
        review = {
            "sector": 4, "bc_sign": -1,
            "solver_sha256": audit._ANGULAR.EXPECTED_SOLVER_SHA256,
            "config_sha256": audit._ANGULAR.EXPECTED_CONFIG_SHA256,
            "material_library_sha256": audit._ANGULAR.EXPECTED_MATERIAL_SHA256,
            "geometry": {"num_slots": 24, "num_poles": 28,
                         "stator_diameter": 150.0, "motor_length": 35.0},
            "n_dofs": 100, "n_elements": 50, "slip_nodes": 1680,
            "slip_spacing_deg": 360.0 / 1680.0,
            "shifts": shifts,
            "angle_deg": [s * 360.0 / 1680.0 for s in shifts],
            "torque_scaled_Nm": [torque_all[s] for s in shifts],
            "current_A": [current_all[s] for s in shifts],
            "psi_Wb": [psi_all[s] for s in shifts],
            "residual": [1e-8] * 60,
            "newton_ok": [True] * 60,
        }
        frames = []
        for index, shift in enumerate(shifts):
            frames.append({
                "torque_sector_Nm": torque_all[shift] / 4,
                "currents_A": current_all[shift], "psi_Wb": psi_all[shift],
                "shift": shift, "angle_deg": shift * 360.0 / 1680.0,
                "bc_sign": -1, "slip_nodes": 1680,
                "slip_spacing_deg": 360.0 / 1680.0,
                "newton_ok": True, "residual": 1e-8,
            })
        reviews.append(review)
        frame_sets.append(frames)
    even, odd = reviews
    odd["combined_shifts"] = shifts_all
    odd["combined_angles_deg"] = [s * 360.0 / 1680.0 for s in shifts_all]
    odd["combined_torque_Nm"] = torque_all
    odd["combined_even_odd"] = {"120": {"mean_Nm": float(np.mean(torque_all)),
                                        "peak_to_peak_Nm": float(np.ptp(torque_all))}}
    params = {
        "n_sectors": 4, "n_parallel": 1, "I_phase_rms": 60.0, "rpm": 15000.0,
        "daxis_deg": 60.0, "connection": "2S", "drive": "current",
        "eddy": False, "rotor_eddy": False, "demag": False, "torque_filter": False,
        "n_steps_per_period": 60, "n_periods": 2.0,
        "mesh_size_mm": 4.0, "min_size_mm": 0.3, "outer_air_factor": 1.3,
        "gap_layers": 1.0, "structured_gap": True, "geo_mesh": False,
        "iron_template": True,
    }
    return even, frame_sets[0], odd, frame_sets[1], params.copy(), params.copy()


class TerminalWorkSamplingTests(unittest.TestCase):
    def setUp(self):
        (self.even, self.ef, self.odd, self.of,
         self.eparams, self.oparams) = _pair()

    def analyze(self):
        return audit.analyze_terminal_sampling(self.even, self.ef, self.odd, self.of,
                                               self.eparams, self.oparams)

    def test_all_nested_offsets_export_raw_ports_derivatives_and_all_bins(self):
        result = self.analyze()
        self.assertTrue(result["passed"])
        self.assertTrue(result["terminal_work_sampling_reproducible"])
        self.assertEqual(result["reference_120"]["source_shifts"], list(range(120)))
        for count, stride in audit.GRID_STRIDES.items():
            grid = result["grids"][str(count)]
            self.assertEqual(len(grid["phase_offset_rows"]), stride)
            for offset, row in enumerate(grid["phase_offset_rows"]):
                self.assertEqual(row["source_indices_and_shifts"], list(range(offset, 120, stride)))
                self.assertEqual(len(row["mechanical_angle_rad"]), count)
                self.assertEqual(len(row["phase_current_A"]["A"]), count)
                self.assertEqual(len(row["phase_flux_linkage_Wb"]["B"]), count)
                self.assertEqual(len(row["dpsi_dmechanical_angle_Wb_per_rad"]["C"]), count)
                self.assertEqual(len(row["spectra"]["flux_linkage"]["A"]), count // 2 + 1)
                self.assertEqual(len(row["spectra"]["flux_derivative"]["B"]), count // 2 + 1)
                self.assertEqual(row["production_vs_derivative_abs_delta_Nm"], 0.0)
                bins = row["spectra"]["flux_linkage"]["A"]
                self.assertFalse(bins[0]["amplitude_doubled"])
                self.assertFalse(bins[-1]["amplitude_doubled"])
        self.assertTrue(result["sampling"]["source_samples_demeaned_or_filtered"] is False)
        self.assertIn("stored-energy closure", result["does_not_certify"])

    def test_reverse_signed_angle_preserves_mean_for_reversed_sample_path(self):
        n = 120
        angle = 2.0 * np.pi * np.arange(n) / (14.0 * n)
        electrical = 14.0 * angle
        psi = np.asarray([0.02 * np.cos(electrical - shift)
                          for shift in (0.0, 2 * np.pi / 3, -2 * np.pi / 3)])
        current = np.asarray([-10.0 * np.sin(electrical - shift)
                              for shift in (0.0, 2 * np.pi / 3, -2 * np.pi / 3)])
        forward = audit.terminal_work_mean(*psi, *current, angle, 14, 1)
        reverse = audit.terminal_work_mean(*psi[:, ::-1], *current[:, ::-1],
                                           angle[::-1], 14, 1)
        self.assertAlmostEqual(forward, reverse, places=12)

    def test_production_even_grid_nyquist_derivative_is_zero_but_bin_is_retained(self):
        n = 20
        angle = 2.0 * np.pi * np.arange(n) / (14.0 * n)
        nyquist = (-1.0) ** np.arange(n)
        psi = np.vstack([nyquist, 2 * nyquist, -0.5 * nyquist])
        derivative = audit.production_derivative(psi, angle)
        self.assertTrue(np.allclose(derivative, np.zeros_like(derivative), rtol=0, atol=1e-12))
        current = np.ones_like(psi)
        self.assertAlmostEqual(audit.terminal_work_mean(*psi, *current, angle, 14, 1), 0.0)
        evidence = audit._rfft_evidence(psi[0], "Wb")
        self.assertEqual(evidence[-1]["bin"], 10)
        self.assertEqual(evidence[-1]["single_sided_peak_amplitude"], 1.0)
        self.assertFalse(evidence[-1]["amplitude_doubled"])

    def test_rejects_bad_current_law_or_provenance_and_reports_error(self):
        self.odd["solver_sha256"] = "incorrect"
        result = self.analyze()
        self.assertFalse(result["passed"])
        self.assertTrue(any("solver_sha256 mismatch" in error for error in result["errors"]))
        self.odd["solver_sha256"] = self.even["solver_sha256"]
        self.eparams["n_parallel"] = 2
        result = self.analyze()
        self.assertFalse(result["passed"])
        self.assertTrue(any("n_parallel expected 1" in error for error in result["errors"]))

    def test_cli_json_and_nonzero_for_invalid_archive(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        output = StringIO()
        with contextlib.redirect_stdout(output):
            status = audit.main(["--even-review", str(root / "missing.json")])
        self.assertEqual(status, 2)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["passed"])
        self.assertTrue(payload["errors"])


if __name__ == "__main__":
    unittest.main()
