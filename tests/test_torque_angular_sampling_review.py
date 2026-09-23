"""Synthetic-only tests for merged all-phase torque sampling audit."""
from __future__ import annotations

import contextlib
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from io import StringIO

import numpy as np

from scripts import torque_angular_sampling_review as sampling


def _archive(root: Path, parity: int, torque_fn=None):
    label = "even" if parity == 0 else "odd"
    folder = root / label
    folder.mkdir(parents=True)
    shifts = list(range(parity, 120, 2))
    angles = [shift * 360.0 / 1680.0 for shift in shifts]
    torque = [float(torque_fn(shift) if torque_fn else 12.5) for shift in shifts]
    theta_e = 14.0 * np.deg2rad(np.asarray(shifts) * 360.0 / 1680.0)
    currents = (60.0 * np.sqrt(2.0) * np.column_stack((
        np.cos(theta_e + np.pi / 3.0),
        np.cos(theta_e - np.pi / 3.0),
        np.cos(theta_e + np.pi),
    ))).tolist()
    residuals = [1e-8] * 60
    review = {
        "sector": 4, "bc_sign": -1,
        "solver_sha256": sampling.EXPECTED_SOLVER_SHA256,
        "config_sha256": sampling.EXPECTED_CONFIG_SHA256,
        "material_library_sha256": sampling.EXPECTED_MATERIAL_SHA256,
        "geometry": {"num_slots": 24, "num_poles": 28},
        "n_dofs": 2000, "n_elements": 1000,
        "slip_nodes": 1680, "slip_spacing_deg": 360.0 / 1680.0,
        "shifts": shifts, "angle_deg": angles,
        "torque_scaled_Nm": torque, "current_A": currents,
        "residual": residuals, "newton_ok": [True] * 60,
    }
    frames = []
    for i, shift in enumerate(shifts):
        frame = {"torque_sector_Nm": torque[i] / 4, "currents_A": currents[i],
                 "shift": shift, "angle_deg": angles[i], "bc_sign": -1,
                 "slip_nodes": 1680, "slip_spacing_deg": 360.0 / 1680.0,
                 "newton_ok": True, "residual": residuals[i]}
        frames.append(frame)
        np.savez(folder / f"frame_{i}.npz", **frame)
    return review, frames, folder


def _pair(root: Path, torque_fn=None):
    even, even_frames, even_folder = _archive(root, 0, torque_fn)
    odd, odd_frames, odd_folder = _archive(root, 1, torque_fn)
    merged = [float(torque_fn(i) if torque_fn else 12.5) for i in range(120)]
    odd["combined_shifts"] = list(range(120))
    odd["combined_angles_deg"] = [i * 360.0 / 1680.0 for i in range(120)]
    odd["combined_torque_Nm"] = merged
    odd["combined_even_odd"] = {"120": {
        "mean_Nm": float(np.mean(merged)), "peak_to_peak_Nm": float(np.ptp(merged))}}
    return even, even_frames, odd, odd_frames, even_folder, odd_folder


class AngularSamplingReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.even, self.even_frames, self.odd, self.odd_frames,
         self.even_folder, self.odd_folder) = _pair(self.root)

    def analyze(self):
        return sampling.analyze_sampling(self.even, self.even_frames,
                                         self.odd, self.odd_frames)

    def test_merge_metrics_every_phase_spectrum_and_input_immutability(self):
        before = copy.deepcopy((self.even, self.even_frames, self.odd, self.odd_frames))
        result = self.analyze()
        self.assertTrue(result["passed"])
        self.assertTrue(result["mean_reproducibility_passed"])
        self.assertTrue(result["ripple_sampling_converged"])
        self.assertFalse(result["continuum_or_subcell_convergence_certified"])
        self.assertEqual(result["sampling"]["source_shifts"], list(range(120)))
        self.assertEqual(result["reference_120"]["raw_torque_Nm"], [12.5] * 120)
        for count, stride in sampling.GRID_STRIDES.items():
            grid = result["grids"][str(count)]
            self.assertEqual(len(grid["phase_offset_rows"]), stride)
            for offset, row in enumerate(grid["phase_offset_rows"]):
                self.assertEqual(row["source_indices"], list(range(offset, 120, stride)))
                self.assertEqual(len(row["raw_torque_Nm"]), count)
                self.assertEqual(len(row["full_rfft_bins"]), count // 2 + 1)
                bins = row["full_rfft_bins"]
                self.assertFalse(bins[0]["amplitude_doubled"])
                self.assertFalse(bins[-1]["amplitude_doubled"])
                self.assertTrue(all(x["amplitude_doubled"] for x in bins[1:-1]))
        self.assertEqual((self.even, self.even_frames, self.odd, self.odd_frames), before)

    def test_all_phase_offsets_catch_extrema_missed_by_offset_zero(self):
        root = self.root / "aliased"
        torque_fn = lambda shift: 40.0 + 0.2 * math.cos(2 * math.pi * 20 * shift / 120)
        even, ef, odd, of, *_ = _pair(root, torque_fn)
        result = sampling.analyze_sampling(even, ef, odd, of)
        grid20 = result["grids"]["20"]
        self.assertEqual(len(grid20["phase_offset_rows"]), 6)
        self.assertFalse(grid20["ripple_extrema_sensitivity"]["passed_all_phase_offsets"])
        self.assertFalse(result["ripple_sampling_converged"])
        self.assertGreater(grid20["ripple_extrema_sensitivity"][
            "max_abs_peak_to_peak_delta_vs_120_Nm"], 0.39)

    def test_exact_complex_alias_relation_all_offsets_and_target_nyquist(self):
        n = np.arange(120, dtype=float)
        source = (0.7 + 0.4 * np.cos(2 * np.pi * 10 * n / 120)
                  + 0.3 * np.sin(2 * np.pi * 50 * n / 120)
                  + 0.2 * np.cos(2 * np.pi * 60 * n / 120))
        full = np.fft.fft(source) / 120
        for target, stride in sampling.GRID_STRIDES.items():
            groups = sampling.alias_groups(120, target)
            self.assertEqual(len(groups), target // 2 + 1)
            for offset in range(stride):
                decimated = source[offset::stride]
                coefficients = np.fft.rfft(decimated) / target
                for group in groups:
                    k = group["target_bin"]
                    reconstructed = sum(full[h] * np.exp(2j * np.pi * h * offset / 120)
                                        for h in group["source_full_fft_bins"])
                    self.assertTrue(np.allclose(coefficients[k], reconstructed,
                                                rtol=0, atol=2e-14))
                nyquist = coefficients[-1]
                self.assertLess(abs(nyquist.imag), 2e-14)

    def test_both_archives_validate_hash_shift_angles_and_strict_convergence(self):
        self.odd["solver_sha256"] = "bad"
        self.even["shifts"][4] = 9
        self.odd["newton_ok"][2] = 1
        self.odd_frames[3]["angle_deg"] += 0.01
        result = self.analyze()
        self.assertFalse(result["passed"])
        self.assertIn("even archive: source shift sequence mismatch", result["errors"])
        self.assertIn("odd archive: solver_sha256 mismatch", result["errors"])
        self.assertIn("odd archive: all 60 strict Newton flags must be true", result["errors"])
        self.assertIn("odd archive frame 3: angle mismatch", result["errors"])

    def test_synchronous_current_law_is_checked_independently_of_frame_match(self):
        self.odd["current_A"][3][0] += 0.01
        self.odd_frames[3]["currents_A"][0] += 0.01
        result = self.analyze()
        self.assertFalse(result["passed"])
        self.assertIn("odd archive: current_A differs from the prescribed 60 A RMS synchronous law",
                      result["errors"])

    def test_frame_torque_scaling_and_embedded_merge_are_checked(self):
        self.even_frames[0]["torque_sector_Nm"] += 0.1
        self.odd["combined_torque_Nm"][17] += 0.1
        result = self.analyze()
        self.assertFalse(result["passed"])
        self.assertIn("even archive frame 0: raw sector torque times 4 differs from review",
                      result["errors"])
        self.assertIn("odd archive embedded combined torque differs from reconstructed merge",
                      result["errors"])

    def test_cli_json_exit_codes_for_nonconvergence_and_missing_archive(self):
        even_path = self.root / "even.json"
        odd_path = self.root / "odd.json"
        even_path.write_text(json.dumps(self.even), encoding="utf-8")
        odd_path.write_text(json.dumps(self.odd), encoding="utf-8")
        output = StringIO()
        with contextlib.redirect_stdout(output):
            status = sampling.main(["--even-review", str(even_path), "--even-frames",
                                    str(self.even_folder), "--odd-review", str(odd_path),
                                    "--odd-frames", str(self.odd_folder)])
        self.assertEqual(status, 0)
        self.assertTrue(json.loads(output.getvalue())["passed"])

        aliased_root = self.root / "cli-aliased"
        aliased_fn = lambda shift: 40.0 + 0.2 * math.cos(2 * math.pi * 20 * shift / 120)
        a_even, _a_ef, a_odd, _a_of, a_even_folder, a_odd_folder = _pair(aliased_root, aliased_fn)
        a_even_path, a_odd_path = aliased_root / "even.json", aliased_root / "odd.json"
        a_even_path.write_text(json.dumps(a_even), encoding="utf-8")
        a_odd_path.write_text(json.dumps(a_odd), encoding="utf-8")
        output = StringIO()
        with contextlib.redirect_stdout(output):
            status = sampling.main(["--even-review", str(a_even_path), "--even-frames",
                                    str(a_even_folder), "--odd-review", str(a_odd_path),
                                    "--odd-frames", str(a_odd_folder)])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertFalse(payload["ripple_sampling_converged"])

        output = StringIO()
        with contextlib.redirect_stdout(output):
            status = sampling.main(["--even-review", str(even_path), "--even-frames",
                                    str(self.even_folder), "--odd-review",
                                    str(self.root / "missing.json"), "--odd-frames",
                                    str(self.odd_folder)])
        self.assertEqual(status, 2)
        self.assertTrue(json.loads(output.getvalue())["errors"])


if __name__ == "__main__":
    unittest.main()
