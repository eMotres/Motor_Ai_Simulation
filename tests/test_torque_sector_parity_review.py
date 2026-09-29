"""Synthetic-only tests for the archived torque-sector offline review."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from scripts import torque_sector_parity_review as review_tool


GEOMETRY = {"stator_diameter": 150.0, "motor_length": 35.0,
            "num_slots": 24, "num_poles": 28}


def _current(angle_deg):
    peak = 60.0 * math.sqrt(2.0)
    phase = math.radians(14.0 * angle_deg + 60.0)
    return [peak * math.cos(phase),
            peak * math.cos(phase - 2.0 * math.pi / 3.0),
            peak * math.cos(phase + 2.0 * math.pi / 3.0)]


def _synthetic_artifacts(tmp_path: Path):
    reviews, params, provenances, frames, config_hashes = {}, {}, {}, {}, {}
    step = 360.0 / 1680.0
    for ns, shifts in ((1, list(range(0, 120, 6))),
                       (2, list(range(0, 120, 6))),
                       (4, list(range(0, 120, 2)))):
        folder = tmp_path / f"ns{ns}"
        folder.mkdir()
        full_torque, all_angles, all_currents, all_residuals = [], [], [], []
        for index, shift in enumerate(shifts):
            angle = shift * step
            phase = 2.0 * math.pi * shift / 120.0
            torque = (40.0 + 2.0 * math.cos(phase) + 0.3 * math.sin(2 * phase)
                      + 0.05 * math.cos(10 * phase) + 0.001 * ns * math.sin(3 * phase))
            currents = _current(angle)
            residual = 1e-8 + index * 1e-12
            row = {"torque_sector_Nm": torque / ns, "currents_A": currents,
                   "shift": shift, "angle_deg": angle,
                   "bc_sign": review_tool.EXPECTED_BC_SIGN[ns],
                   "slip_nodes": 1680, "slip_spacing_deg": step,
                   "newton_ok": True, "residual": residual}
            full_torque.append(torque)
            all_angles.append(angle)
            all_currents.append(currents)
            all_residuals.append(residual)
            np.savez(folder / f"frame_{index}.npz",
                     torque_sector_Nm=row["torque_sector_Nm"],
                     currents_A=np.asarray(currents), shift=shift, angle_deg=angle,
                     bc_sign=row["bc_sign"], slip_nodes=1680,
                     slip_spacing_deg=step, newton_ok=True, residual=residual)

        source_review = {
            "sector": ns,
            "grid_shifts" if ns in (1, 2) else "shifts": shifts,
            "sector_scaled_torque_Nm" if ns in (1, 2) else "torque_scaled_Nm": full_torque,
            "angle_deg": all_angles, "current_A": all_currents,
            "residual": all_residuals, "newton_ok": [True] * len(shifts),
            "all_newton_ok": True, "geometry": GEOMETRY,
            "solver_sha256": review_tool.EXPECTED_SOLVER_SHA256,
            "config_sha256": review_tool.EXPECTED_CONFIG_SHA256,
            "material_library_sha256": review_tool.EXPECTED_MATERIAL_LIBRARY_SHA256,
            "slip_nodes": 1680, "slip_spacing_deg": step,
        }
        review_path = folder / "review.json"
        review_path.write_text(json.dumps(source_review), encoding="utf-8")
        reviews[ns] = review_tool._read_json(review_path)
        params[ns] = dict(review_tool.EXPECTED_PARAMS, n_sectors=ns,
                          n_steps_per_period=review_tool.SOURCE_COUNTS[ns],
                          n_periods=2.0, geo_override=GEOMETRY)
        run_log = folder / "run.log"
        run_log.write_text("Explicit parameters: " + json.dumps(params[ns]) + "\n",
                           encoding="utf-8")
        params[ns] = review_tool._read_run_params(run_log)
        provenance = None
        if ns in (1, 2):
            provenance = {
                "sector": ns, "requested_n_sectors": ns,
                "solver_sha256": review_tool.EXPECTED_SOLVER_SHA256,
                "config_source_sha256_before": review_tool.EXPECTED_CONFIG_SHA256,
                "config_scratch_sha256": review_tool.EXPECTED_CONFIG_SHA256,
                "material_library_sha256": review_tool.EXPECTED_MATERIAL_LIBRARY_SHA256,
                "mesh_size_mm": 4.0, "min_size_mm": 0.3, "geo_mesh": False,
                "eddy": False, "rotor_eddy": False, "demag": False,
                "n_parallel": 1, "geometry": GEOMETRY,
                "current_law": "60*sqrt(2) A peak; phase=14*mechanical_angle_deg+60deg",
                "sample_shifts": shifts,
                "samples_cover_full_endpoint_excluded_electrical_period": True,
            }
        provenances[ns] = provenance
        frames[ns] = review_tool._read_frame_metadata(folder, len(shifts))
        config_hashes[ns] = review_tool.EXPECTED_CONFIG_SHA256
    return reviews, params, provenances, frames, config_hashes


class TorqueSectorParityReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.reviews, self.params, self.provenances,
         self.frames, self.config_hashes) = _synthetic_artifacts(self.root)

    def analyze(self):
        return review_tool.analyze_archives(
            self.reviews, self.params, self.provenances, self.frames,
            self.config_hashes, review_tool.EXPECTED_MATERIAL_LIBRARY_SHA256)

    def test_matched_grid_retains_all_dft_bins_and_does_not_mutate_inputs(self):
        before = copy.deepcopy((self.reviews, self.params, self.provenances,
                                self.frames, self.config_hashes))
        result = self.analyze()
        self.assertTrue(result["passed"])
        grid = result["comparison_grid"]
        self.assertEqual(grid["source_sample_count"], {"1": 20, "2": 20, "4": 60})
        self.assertEqual(grid["ns4_selected_nested_comparison_indices"], list(range(0, 60, 3)))
        self.assertEqual(grid["ns4_selected_nested_comparison_shifts"], list(range(0, 120, 6)))
        self.assertFalse(grid["harmonic_filtering_or_deletion"])
        self.assertFalse(grid["mesh_refinement_performed"])
        for sector in (1, 2, 4):
            bins = result["sectors"][str(sector)]["full_rfft_bins"]
            self.assertEqual([row["bin"] for row in bins], list(range(11)))
            self.assertFalse(bins[0]["amplitude_doubled"])
            self.assertFalse(bins[-1]["amplitude_doubled"])
            self.assertTrue(all(row["amplitude_doubled"] for row in bins[1:-1]))
            self.assertEqual(len(result["sectors"][str(sector)]["raw_maxwell_torque_Nm"]), 20)
        after = (self.reviews, self.params, self.provenances,
                 self.frames, self.config_hashes)
        self.assertEqual(after, before)

    def test_provenance_and_boundary_failures_are_reported(self):
        self.frames[4][7]["bc_sign"] = 1
        self.params[2]["drive"] = "voltage"
        result = self.analyze()
        self.assertFalse(result["passed"])
        self.assertIn("NS4 frame 7: incorrect boundary sign", result["errors"])
        self.assertTrue(any("NS2: run parameter drive" in error for error in result["errors"]))

    def test_numeric_gate_failure_is_reported_by_sector_and_metric(self):
        key = "torque_scaled_Nm"
        self.reviews[4][key][0] += 0.2
        self.frames[4][0]["torque_sector_Nm"] += 0.2 / 4
        result = self.analyze()
        self.assertFalse(result["passed"])
        self.assertIn("NS4:pointwise_waveform", result["failed_gates"])
        self.assertIn("NS4:mean", result["failed_gates"])

    def test_cli_emits_json_and_nonzero_status_on_failure(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        with patch.object(review_tool, "run_review", return_value={
                "passed": False, "errors": ["bad fixture"]}):
            with redirect_stdout(output):
                status = review_tool.main([])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue()),
                         {"passed": False, "errors": ["bad fixture"]})


if __name__ == "__main__":
    unittest.main()
