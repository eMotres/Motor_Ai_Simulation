"""Synthetic offline tests for angular sampling review; no solver or API use."""
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


def _fixture(root: Path, torque_fn=None):
    frames_dir = root / "frames"
    frames_dir.mkdir(parents=True)
    shifts = list(range(0, 120, 2))
    angles = [s * 360.0 / 1680.0 for s in shifts]
    torque = [float(torque_fn(i) if torque_fn else
                    40.0 + 0.2 * math.sin(2 * math.pi * 10 * i / 60))
              for i in range(60)]
    residual = [1e-8] * 60
    review = {
        "sector": 4, "bc_sign": -1, "solver_sha256": sampling.EXPECTED_SOLVER_SHA256,
        "config_sha256": sampling.EXPECTED_CONFIG_SHA256,
        "material_library_sha256": sampling.EXPECTED_MATERIAL_SHA256,
        "shifts": shifts, "angle_deg": angles, "torque_scaled_Nm": torque,
        "residual": residual, "newton_ok": [True] * 60,
        "current_A": [[1.0, -0.5, -0.5]] * 60,
    }
    frames = []
    for i, shift in enumerate(shifts):
        frame = {"torque_sector_Nm": torque[i] / 4, "shift": shift,
                 "angle_deg": angles[i], "bc_sign": -1, "slip_nodes": 1680,
                 "slip_spacing_deg": 360.0 / 1680.0, "newton_ok": True,
                 "residual": residual[i]}
        frames.append(frame)
        np.savez(frames_dir / f"frame_{i}.npz", **frame)
    review_path = root / "review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    return review, frames, review_path, frames_dir


class AngularSamplingReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.review, self.frames, self.review_path, self.frames_dir = _fixture(self.root)

    def test_nested_grid_metrics_full_spectra_aliases_and_nonconverged_ripple(self):
        before = copy.deepcopy((self.review, self.frames))
        result = sampling.analyze_sampling(self.review, self.frames)
        self.assertTrue(result["mean_reproducibility_passed"])
        self.assertFalse(result["ripple_sampling_converged"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["failed_gates"], ["ripple_sampling_convergence"])
        self.assertEqual([len(result["grids"][str(n)]["raw_torque_Nm"])
                          for n in (20, 30, 60)], [20, 30, 60])
        self.assertEqual([len(result["grids"][str(n)]["full_rfft_bins"])
                          for n in (20, 30, 60)], [11, 16, 31])
        for n in (20, 30, 60):
            bins = result["grids"][str(n)]["full_rfft_bins"]
            self.assertFalse(bins[0]["amplitude_doubled"])
            self.assertFalse(bins[-1]["amplitude_doubled"])
            self.assertTrue(all(row["amplitude_doubled"] for row in bins[1:-1]))
        self.assertEqual(result["grids"]["20"]["source_indices"], list(range(0, 60, 3)))
        self.assertEqual(result["grids"]["30"]["source_indices"], list(range(0, 60, 2)))
        self.assertEqual(result["grids"]["60"]["source_indices"], list(range(60)))
        self.assertEqual(result["sampling"]["aliasing"]["groups_60_to_20"][2]
                         ["source_full_fft_bins"], [2, 22, 42])
        self.assertEqual(result["sampling"]["aliasing"]["groups_60_to_30"][2]
                         ["source_full_fft_bins"], [2, 32])
        self.assertEqual((self.review, self.frames), before)

    def test_constant_waveform_can_pass_both_gates(self):
        review, frames, *_ = _fixture(self.root / "constant", lambda _i: 12.5)
        result = sampling.analyze_sampling(review, frames)
        self.assertTrue(result["mean_reproducibility_passed"])
        self.assertTrue(result["ripple_sampling_converged"])
        self.assertTrue(result["passed"])

    def test_alias_groups_reconstruct_complex_decimated_dft_including_nyquist(self):
        source = np.arange(60, dtype=float)
        source = (0.7 + 0.4 * np.cos(2 * np.pi * 10 * source / 60)
                  + 0.3 * np.sin(2 * np.pi * 22 * source / 60)
                  + 0.2 * np.cos(2 * np.pi * 30 * source / 60))
        full = np.fft.fft(source) / 60
        for target in (20, 30):
            nested = np.fft.rfft(source[::60 // target]) / target
            groups = sampling.alias_groups(60, target)
            self.assertEqual(len(groups), target // 2 + 1)
            for group in groups:
                k = group["target_bin"]
                self.assertTrue(np.allclose(
                    nested[k], sum(full[j] for j in group["source_full_fft_bins"]),
                    rtol=0, atol=1e-14))

    def test_invalid_archive_reports_convergence_and_frame_provenance_errors(self):
        self.review["newton_ok"][17] = False
        self.frames[8]["bc_sign"] = 1
        result = sampling.analyze_sampling(self.review, self.frames)
        self.assertFalse(result["passed"])
        self.assertTrue(any("all 60 archived frames" in error for error in result["errors"]))
        self.assertIn("frame 8 symmetry/slip-grid metadata mismatch", result["errors"])

        self.review["newton_ok"] = ["False"] * 60
        self.frames[8]["bc_sign"] = -1
        result = sampling.analyze_sampling(self.review, self.frames)
        self.assertFalse(result["passed"])
        self.assertTrue(any("all 60 archived frames" in error for error in result["errors"]))

    def test_cli_outputs_json_and_nonzero_on_gate_failure_or_bad_path(self):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            status = sampling.main(["--review", str(self.review_path),
                                    "--frames-dir", str(self.frames_dir)])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertTrue(payload["mean_reproducibility_passed"])
        self.assertFalse(payload["ripple_sampling_converged"])

        output = StringIO()
        with contextlib.redirect_stdout(output):
            status = sampling.main(["--review", str(self.root / "missing.json"),
                                    "--frames-dir", str(self.frames_dir)])
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertFalse(payload["passed"])
        self.assertTrue(payload["errors"])


if __name__ == "__main__":
    unittest.main()
