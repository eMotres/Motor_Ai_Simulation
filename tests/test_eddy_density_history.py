"""Execute the production density-history block against known quadrature data."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(scope="module")
def density_history_block():
    source = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
              / "simulation" / "fem_solver_2d.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    transient = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef)
                     and node.name == "fem_transient_sliding_band")
    blocks = [node for node in ast.walk(transient)
              if isinstance(node, ast.If) and any(
                  isinstance(child, ast.Call)
                  and isinstance(child.func, ast.Attribute)
                  and isinstance(child.func.value, ast.Name)
                  and child.func.value.id == "_ed_dens_hist"
                  and child.func.attr == "append"
                  for statement in node.body for child in ast.walk(statement))]
    # Select the innermost guard, not its enclosing eddy/frame branches.
    block = min(blocks, key=lambda node: node.end_lineno - node.lineno)
    return compile(ast.Module(body=[block], type_ignores=[]), str(source), "exec")


class CountingBasis:
    def __init__(self):
        self.calls = 0

    def interpolate(self, field):
        self.calls += 1
        np.testing.assert_array_equal(field, [3.0, 4.0])
        return np.array([[1.0, 3.0], [2.0, 4.0]])


@pytest.mark.parametrize("return_field,return_frames,frame,expected_calls", [
    (False, 0, 0, 0),
    (False, 2, 0, 0),
    (True, 0, -1, 0),
    (True, 0, 0, 1),
    (True, 2, 0, 1),
])
def test_density_history_only_interpolates_for_requested_field(
        density_history_block, return_field, return_frames, frame, expected_calls):
    basis = CountingBasis()
    history = []
    namespace = {
        "np": np, "return_field": return_field, "return_frames": return_frames,
        "k": frame, "_snap2": None, "_ed_elems": np.array([1, 4]),
        "_ed_con": [{}, {}], "_ed_uloc": [np.array([0]), np.array([1])],
        "_Ued": [2.0, 5.0], "_ed_basis": basis, "_dAe": np.array([3.0, 4.0]),
        "_ed_sig_e": np.array([2.0, 3.0]),
        "_ed_dx": np.array([[0.25, 0.75], [0.5, 1.5]]),
        "_ed_area": np.array([1.0, 2.0]), "_ed_dens_hist": history,
    }
    exec(density_history_block, namespace)
    assert basis.calls == expected_calls
    assert len(history) == expected_calls
    if expected_calls:
        # E = -dA/dt + U; the two elements have area-averaged sigma E^2
        # of 2*(.25 + .75)/1 and 3*(9*.5 + 1*1.5)/2 respectively.
        np.testing.assert_array_equal(history[0], [2.0, 9.0])
