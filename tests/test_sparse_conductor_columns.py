"""Conductor assembly preserves the dense operator without a dense stack."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest
from scipy import sparse


@pytest.fixture(scope="module")
def conductor_column_block():
    source = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
              / "simulation" / "fem_solver_2d.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    transient = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef)
                     and node.name == "fem_transient_sliding_band")
    guards = [node for node in ast.walk(transient)
              if isinstance(node, ast.If) and any(
                  isinstance(statement, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "_G2"
                          for target in statement.targets)
                  for statement in node.body)]
    assert len(guards) == 1
    return compile(ast.Module(body=guards, type_ignores=[]), str(source), "exec")


@pytest.mark.parametrize("columns", [
    [np.zeros(7)],
    [np.array([2.0, 0.0, -3.0, 0.0, 0.0, 0.0, 4.0]),
     np.array([-1.0, 0.0, 0.0, 0.0, 5.0, 0.0, -6.0])],
    # Both column views are noncontiguous, with zeros and signed boundary values.
    [np.array([2.0, 8.0, 0.0, 8.0, -3.0, 8.0, 4.0, 8.0])[::2],
     np.array([0.0, 8.0, -2.0, 8.0, 5.0, 8.0, 0.0, 8.0])[::2]],
    [np.array([1, 0, -2], dtype=np.int32),
     np.array([0.0, 3.5, -0.0], dtype=np.float64)],
])
def test_sparse_column_assembly_matches_original_operator(
        conductor_column_block, monkeypatch, columns):
    expected = sparse.csr_matrix(np.column_stack(columns))
    originals = [column.copy() for column in columns]
    hstack = sparse.hstack
    assembled = []

    def checked_hstack(blocks, **kwargs):
        assert len(blocks) == len(columns)
        assert all(sparse.isspmatrix_csc(block) for block in blocks)
        assert all(block.shape == (columns[0].size, 1) for block in blocks)
        assert all(block.indptr.size == 2 for block in blocks)
        assembled.append(True)
        return hstack(blocks, **kwargs)

    monkeypatch.setattr(sparse, "hstack", checked_hstack)
    namespace = {"np": np, "_csr": sparse.csr_matrix, "_gcols": columns,
                 "_ed_con": [{"g": column, "S": index + 1.0}
                             for index, column in enumerate(columns)], "dt": 0.25}
    exec(conductor_column_block, namespace)
    actual = namespace["_G2"]
    assert assembled == [True]
    assert sparse.isspmatrix_csr(actual)
    assert actual.shape == expected.shape
    assert actual.dtype == expected.dtype
    for attribute in ("data", "indices", "indptr"):
        np.testing.assert_array_equal(getattr(actual, attribute),
                                      getattr(expected, attribute))
    np.testing.assert_array_equal(actual @ np.arange(len(columns)),
                                  expected @ np.arange(len(columns)))
    for column, original in zip(columns, originals):
        np.testing.assert_array_equal(column, original)
    np.testing.assert_array_equal(namespace["_Sdt2"],
                                  np.arange(1, len(columns) + 1) * 0.25)


def test_empty_column_guard_keeps_existing_initial_values(conductor_column_block):
    operator = object()
    conductances = object()
    namespace = {"_gcols": [], "_G2": operator, "_Sdt2": conductances}
    exec(conductor_column_block, namespace)
    assert namespace["_G2"] is operator
    assert namespace["_Sdt2"] is conductances
