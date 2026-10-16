"""Standalone tests for raw scalar histories around the existing P2 settle trims."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

import numpy as np

from motor_ai_sim.simulation.sb_postproc import (
    drop_settling_frames,
    retained_window_metadata,
    snapshot_scalar_history,
)


def _channels(n=6):
    return {
        "time_s_absolute": [10.0 + i for i in range(n)],
        "mechanical_angle_rad": [0.1 * i for i in range(n)],
        "torque_em_Nm": [1.0 + i for i in range(n)],
        "psi_A_Wb": [2.0 + i for i in range(n)],
        "psi_B_Wb": [3.0 + i for i in range(n)],
        "psi_C_Wb": [4.0 + i for i in range(n)],
        "current_A_A": [5.0 + i for i in range(n)],
        "current_B_A": [6.0 + i for i in range(n)],
        "current_C_A": [7.0 + i for i in range(n)],
        "voltage_solver_residual_V": [0.01 * i for i in range(n)],
        # Some histories intentionally have different coverage. They must
        # retain independent counts rather than be zipped or padded.
        "eddy_copper_power_W": [8.0, 9.0],
    }


class TransientSampleRetentionTests(unittest.TestCase):
    def test_sequential_voltage_and_demag_trims_keep_original_samples_and_time(self):
        live = _channels()
        raw = snapshot_scalar_history(live)
        voltage = {"kind": "voltage_settling", "requested_frames": 2}
        demag = {"kind": "demag_settling", "requested_frames": 1}

        # Exercise the same in-place trimming helper used by the P2 solver.
        drop_settling_frames(live.values(), 2, live["time_s_absolute"])
        drop_settling_frames(live.values(), 1, live["time_s_absolute"])
        meta = retained_window_metadata(
            raw, live,
            core_series=("time_s_absolute", "mechanical_angle_rad",
                         "torque_em_Nm", "psi_A_Wb", "psi_B_Wb", "psi_C_Wb",
                         "current_A_A", "current_B_A", "current_C_A"),
            trim_operations=(voltage, demag), nominal_retained_frames=3,
            retained_periods=1.0)

        self.assertEqual(raw["samples"]["time_s_absolute"],
                         [10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
        self.assertEqual(raw["samples"]["torque_em_Nm"],
                         [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertEqual(live["time_s_absolute"], [0.0, 1.0, 2.0])
        self.assertTrue(meta["core_waveform_aligned"])
        self.assertEqual(meta["retained_start_raw_sample_index"], 3)
        self.assertEqual(meta["retained_time_start_s_absolute"], 13.0)
        self.assertEqual(meta["retained_time_end_s_absolute"], 15.0)
        self.assertAlmostEqual(meta["retained_mechanical_angle_start_rad"], 0.3)
        self.assertAlmostEqual(meta["retained_mechanical_angle_end_rad"], 0.5)
        self.assertEqual(meta["removed_sample_count_by_series"]["eddy_copper_power_W"], 1)
        self.assertFalse(meta["all_scalar_series_aligned_before_trim"])
        self.assertEqual([op["kind"] for op in meta["trim_operations"]],
                         ["voltage_settling", "demag_settling"])

    def test_core_misalignment_is_reported_without_guessing_a_start_index(self):
        live = _channels()
        live["psi_C_Wb"].pop()
        raw = snapshot_scalar_history(live)
        drop_settling_frames(live.values(), 2, live["time_s_absolute"])
        meta = retained_window_metadata(
            raw, live,
            core_series=("time_s_absolute", "mechanical_angle_rad",
                         "torque_em_Nm", "psi_A_Wb", "psi_B_Wb", "psi_C_Wb",
                         "current_A_A", "current_B_A", "current_C_A"),
            trim_operations=({"kind": "demag_settling", "requested_frames": 2},),
            nominal_retained_frames=4, retained_periods=1.0)

        self.assertFalse(meta["core_waveform_aligned"])
        self.assertIsNone(meta["retained_start_raw_sample_index"])
        self.assertEqual(meta["sample_count_by_series_before_trim"]["psi_C_Wb"], 5)
        self.assertEqual(meta["sample_count_by_series_after_trim"]["psi_C_Wb"], 3)

    def test_snapshot_skips_large_vector_samples_without_raising(self):
        # 2026-09-23: a vector channel is left out and named, never raised on —
        # this runs on a finished solve and must not be what fails it.
        raw = snapshot_scalar_history({"field_B": [np.zeros(100)],
                                       "torque_em_Nm": [np.array(1.0)]})
        self.assertNotIn("field_B", raw["samples"])
        self.assertIn("non-scalar", raw["skipped_series"]["field_B"])
        self.assertEqual(raw["samples"]["torque_em_Nm"], [1.0])

    def test_solver_converts_effective_angle_degrees_to_snapshot_radians(self):
        source_path = (Path(__file__).resolve().parents[1]
                       / "src/motor_ai_sim/simulation/fem_solver_2d.py")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        writes = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "append"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "_theta_samples" and node.args):
                writes.append(node.args[0])
        self.assertEqual(len(writes), 1)
        self.assertIsInstance(writes[0], ast.Call)
        self.assertIsInstance(writes[0].func, ast.Attribute)
        self.assertEqual(writes[0].func.attr, "radians")
        self.assertIsInstance(writes[0].args[0], ast.Call)
        self.assertEqual(writes[0].args[0].func.id, "float")
        self.assertEqual(writes[0].args[0].args[0].id, "theta_eff")


if __name__ == "__main__":
    unittest.main()
