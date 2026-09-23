"""Standalone no-config gate for the actual P2 terminal-linkage closure.

Run this file directly with the project Python; do not collect the shared pytest
suite, whose conftest manages a separate live-configuration sandbox.
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
from skfem import Basis, ElementTriP2, LinearForm, MeshTri, asm


SOLVER = Path(__file__).resolve().parents[1] / "src/motor_ai_sim/simulation/fem_solver_2d.py"


def actual_psi_closure(env):
    tree = ast.parse(SOLVER.read_text(encoding="utf-8"))
    candidates = [node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_psi2"]
    assert len(candidates) == 1
    module = ast.fix_missing_locations(ast.Module(body=candidates, type_ignores=[]))
    exec(compile(module, str(SOLVER), "exec"), env)
    return env["_psi2"]


def exercise(nparallel):
    # Two conductor domains in one slot, intentionally different areas.
    p = np.array([[0., 1., 0., 2.], [0., 0., 1., 1.]])
    t = np.array([[0, 1], [1, 3], [2, 2]])
    mesh = MeshTri(p, t)
    basis = Basis(mesh, ElementTriP2(), intorder=6)
    areas = basis.dx.sum(axis=1)
    assert not np.isclose(areas[0], areas[1])
    A = (basis.doflocs[0]**2 + .7*basis.doflocs[1]
         + .2*basis.doflocs[0]*basis.doflocs[1])
    Amean = A[basis.facet_dofs[0][mesh.t2f]].mean(axis=0)

    @LinearForm
    def unit(v, _w):
        return v

    nwire = 2
    JperI = nwire / areas.sum()
    fA = np.zeros(basis.N)
    for elem in (0, 1):
        fA += JperI * asm(unit, Basis(mesh, ElementTriP2(), elements=[elem], intorder=6))
    factor = .02 / nparallel
    coil_info = [(np.asarray([j]), areas[[j]], 1., "A", float(areas.sum()))
                 for j in (0, 1)]
    env = dict(np=np, eddy=False, _sc_psi2=factor,
               f_coil2={"A": fA, "B": np.zeros(basis.N), "C": np.zeros(basis.N)},
               _As_e=basis.facet_dofs[0][mesh.t2f], coil_info=coil_info)
    psi = actual_psi_closure(env)
    expected = factor * float(A @ fA)
    assert np.isclose(psi(A)[0], expected, rtol=0, atol=1e-15)
    assert psi(A)[1:] == (0., 0.)
    old_equal_tag = factor * float(np.sum(Amean))
    assert abs(old_equal_tag - expected) > 1e-5 * abs(expected)
    # The same source/terminal functional is needed for imposed-current and
    # voltage-circuit residuals; changing the trial current does not change it.
    for trial_current in (-3.2, 0.7, 12.):
        source_work = factor * float(A @ (trial_current*fA))
        assert np.isclose(source_work, trial_current*psi(A)[0], rtol=0, atol=1e-15)
    env["eddy"] = True
    assert np.isclose(psi(A)[0], old_equal_tag, rtol=0, atol=1e-15)


def test_actual_p2_linkage_reciprocity():
    for nparallel in (1, 2):
        exercise(nparallel)


if __name__ == "__main__":
    test_actual_p2_linkage_reciprocity()
    print("P2 linkage reciprocity: n_parallel=1,2 and eddy branch passed")
