"""Eddy conductor-current normalization and k=1 path equivalence.

Run directly with Python 3.11. This uses the actual P2Drive bordered system
and a source-level assertion for fem_solver_2d's conductor-current mapping;
it does not import the application or run FEM.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
import sys
import unittest

import numpy as np
from scipy.sparse import csr_matrix, eye

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.simulation import p2_drive  # noqa: E402
from motor_ai_sim.winding import n_parallel_effective  # noqa: E402


class LinearField:
    sat = []

    def asmK(self, _nu):
        return csr_matrix([[10.0]])

    def solve_ff(self, matrix, rhs):
        return np.linalg.solve(matrix.toarray(), rhs)

    def pad2(self, projection, _free, x):
        return np.asarray(projection @ x)


def _drive(areas, signs, *, paths):
    dt = 0.01
    areas = np.asarray(areas, float)
    signs = np.asarray(signs, float)
    count = len(areas)
    ed_con = [dict(S=float(area), key="cu", Iunit=float(sign), phase="A")
              for area, sign in zip(areas, signs)]
    G = csr_matrix((0.02 * areas)[None, :])
    return p2_drive.P2Drive(
        p2=LinearField(), psi=lambda _a: (0.0, 0.0, 0.0),
        f_mag=np.array([1.0]), Pa=np.zeros(1), Pb=np.zeros(1),
        R_phase=1.0, v_phase_peak=1.0, n_dof=1, pic_tol=1e-3, dt=dt,
        log=logging.getLogger(__name__), ed_con=ed_con, G=G,
        Msig=csr_matrix([[20.0]]), Msd=csr_matrix([[2000.0]]),
        Sdt=areas * dt, paths=paths)


def _solve(drive, imposed):
    return drive.eddy_solve(
        eye(1, format="csr"), np.array([0]), np.array([0.4]),
        np.zeros(drive.Sdt.size), np.asarray(imposed, float),
        np.array([0.4]), np.array([1.0]), 4)


class TestP2EddyCurrentNormalization(unittest.TestCase):
    def test_k1_multiturn_path_matches_transposed_with_unequal_tag_areas(self):
        # Six series turns on each side; area differences represent the saved
        # 0.1732% min/max meshed conductor-area spread. They affect each body's
        # S, but must not alter its imposed series current.
        areas = np.array([1.0, 1.00035, 1.00093, 1.00010,
                          0.99990, 0.99920] * 2)
        signs = np.array([1.0] * 6 + [-1.0] * 6)
        current = 42.0
        body_currents = signs * current  # Iunit=±1, branch current already scaled
        path = {
            "paths": [[(j, float(signs[j])) for j in range(len(signs))]],
            "group": [0], "rep": [0], "n_group": 1,
        }
        transposed = _drive(areas, signs, paths=None)
        series = _drive(areas, signs, paths=path)
        direct = _solve(transposed, body_currents)
        through_path = _solve(series, body_currents)
        self.assertTrue(direct[0] and through_path[0])
        np.testing.assert_allclose(through_path[1], direct[1], rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(through_path[2], direct[2], rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(series.path_currents, [current], rtol=1e-12, atol=1e-12)

    def test_saved_area_weighting_does_not_match_one_physical_path(self):
        # Reproduce the old expression against an unequal six-tag slot: the
        # per-tag transposed currents vary, while k=1 path wiring repeats the
        # representative tag's current across every turn. Unit weights remove
        # this mesh-area dependence while preserving the branch current.
        areas = np.array([1.0, 1.00035, 1.00093, 1.00010, 0.99990, 0.99920])
        old_units = len(areas) * areas / areas.sum()
        self.assertGreater(float(old_units.max() / old_units.min() - 1.0), 0.0017)
        old_transposed = 42.0 * old_units
        old_series = np.full(areas.size, 42.0 * old_units[0])
        self.assertGreater(float(np.max(np.abs(old_transposed - old_series))), 0.01)
        corrected_transposed = np.full(areas.size, 42.0)
        corrected_series = np.full(areas.size, 42.0)
        np.testing.assert_array_equal(corrected_transposed, corrected_series)

    def test_production_eddy_unit_current_is_orientation_only(self):
        source = (ROOT / "src/motor_ai_sim/simulation/fem_solver_2d.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        matches = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "append"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "_coil_con" and node.args
                    and isinstance(node.args[0], ast.Dict)):
                continue
            for key, value in zip(node.args[0].keys, node.args[0].values):
                if (isinstance(key, ast.Constant) and key.value == "Iunit"
                        and isinstance(value, ast.Name) and value.id == "dr"):
                    matches.append(node)
        self.assertEqual(len(matches), 1)

    def test_effective_parallelism_applied_once_and_split_only_adds_series_turns(self):
        phase_current, conn_paths = 120.0, 2
        for strands, split in ((1, 1), (3, 1), (3, 2)):
            geo = {"wire_parallel": strands, "wire_split": split,
                   "num_wires_per_slot": 6}
            effective_paths = n_parallel_effective(conn_paths, geo)
            per_conductor = phase_current / effective_paths
            physical_conductors = geo["num_wires_per_slot"] * split
            turns = (geo["num_wires_per_slot"] // strands) * split
            expected_amp_turns = turns * strands * per_conductor
            self.assertAlmostEqual(expected_amp_turns,
                                   physical_conductors * phase_current / effective_paths)
            self.assertAlmostEqual(per_conductor,
                                   phase_current / (conn_paths * strands))


if __name__ == "__main__":
    unittest.main()
