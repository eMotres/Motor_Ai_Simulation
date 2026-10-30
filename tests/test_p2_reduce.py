"""``P2Nonlinear.reduce`` is the reduced linear system, bit for bit.

Every Newton/Picard iteration used to build its system as
``(Pro.T @ M @ Pro).tocsr()[free][:, free].tocsc()``; ``reduce`` builds the
frame's restricted projection once and reproduces SciPy's summation order, so
the matrix PARDISO receives must be array_equal — indptr, indices, data and
dtypes — on real slip projections at many rotor shifts, sector and full ring.
Small synthetic meshes only; no field solve.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import scipy.sparse as sp
from skfem import Basis, BilinearForm, ElementTriP2, asm
from skfem.helpers import dot, grad

from motor_ai_sim.simulation.p2_nonlinear import P2Nonlinear

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_p2_projection import annular_projection  # noqa: E402


@BilinearForm
def _lap(u, v, w):
    return w["nu"] * dot(grad(u), grad(v))


def _reference(M, Pro, free):
    return (Pro.T @ M @ Pro).tocsr()[free][:, free].tocsc()


def _p2():
    return P2Nonlinear(basis=None, n_dof=0, K_const=None, sat=[], sat_sub=[],
                       pardiso=None, log=None)


def _assert_identical(a, b):
    assert a.format == b.format == "csc"
    assert a.shape == b.shape
    for name in ("indptr", "indices", "data"):
        x, y = getattr(a, name), getattr(b, name)
        assert x.dtype == y.dtype, name
        np.testing.assert_array_equal(x, y, err_msg=name)


@pytest.mark.parametrize("full_ring,sign", [(False, -1), (False, 1), (True, 1)])
def test_identical_on_slip_projections_over_many_shifts(full_ring, sign):
    proj = annular_projection(7, full_ring, sign)
    rng = np.random.default_rng(11)
    # an SPD-shaped operator with the P2 sparsity of an n-dof mesh: random
    # symmetric values on a pattern that couples welded and free dofs alike
    n = proj.N
    A = sp.random(n, n, density=min(1.0, 12.0 / n), random_state=3,
                  data_rvs=lambda k: rng.normal(size=k))
    M = (A + A.T + sp.eye(n) * 5.0).tocsr()
    p = _p2()
    for shift in list(range(-9, 10)) + [37, -41]:
        Pro, outer = proj.build(shift)
        free = np.setdiff1d(np.arange(Pro.shape[1]), outer)
        for scale in (1.0, -0.3, 1e-9):     # later iterations: new values
            Ms = (M * scale).tocsr()
            _assert_identical(p.reduce(Ms, Pro, free), _reference(Ms, Pro, free))


def test_identical_on_a_p2_stiffness_with_cancellations():
    """Uniform ν on a symmetric mesh makes exact zeros at both product stages
    (SciPy drops them); the pattern must come out the same."""
    from skfem import MeshTri
    m = MeshTri.init_sqsymmetric().refined(3)
    b = Basis(m, ElementTriP2())
    rng = np.random.default_rng(5)
    n = b.N
    roots = np.arange(n)
    slaves = rng.choice(n, n // 10, replace=False)
    roots[slaves] = rng.choice(np.setdiff1d(np.arange(n), slaves), slaves.size)
    sgn = np.ones(n)
    sgn[slaves] = rng.choice([-1.0, 1.0], slaves.size)
    uniq, inv = np.unique(roots, return_inverse=True)
    Pro = sp.coo_matrix((sgn, (np.arange(n), inv)), shape=(n, uniq.size)).tocsr()
    free = np.setdiff1d(np.arange(uniq.size), rng.choice(uniq.size, 9, replace=False))
    p = _p2()
    for nu in (np.ones(b.dx.shape), 1.0 + rng.random(b.dx.shape)):
        M = asm(_lap, b, nu=nu).tocsr()
        _assert_identical(p.reduce(M, Pro, free), _reference(M, Pro, free))


def test_non_canonical_operator_and_cache_per_frame():
    proj = annular_projection(5, False, -1)
    n = proj.N
    rng = np.random.default_rng(2)
    C = sp.random(n, n, density=min(1.0, 8.0 / n), random_state=4,
                  data_rvs=lambda k: rng.normal(size=k)).tocsr()
    # every row reversed (unsorted) and its first entry stored twice
    # (duplicates): a CSR SciPy has not canonicalised
    data, indices, indptr = [], [], [0]
    for i in range(n):
        lo, hi = C.indptr[i], C.indptr[i + 1]
        d, j = C.data[lo:hi][::-1], C.indices[lo:hi][::-1]
        if hi > lo:
            d, j = np.append(d, 0.25 * d[0]), np.append(j, j[0])
        data.extend(d); indices.extend(j); indptr.append(len(data))
    M = sp.csr_matrix((np.array(data), np.array(indices, np.int32),
                       np.array(indptr, np.int32)), shape=(n, n))
    assert not M.has_canonical_format
    p = _p2()
    for shift in (0, 3, 3, -4):
        Pro, outer = proj.build(shift)
        free = np.setdiff1d(np.arange(Pro.shape[1]), outer)
        _assert_identical(p.reduce(M, Pro, free), _reference(M, Pro, free))
        assert p._red[0] is Pro and p._red[1] is free


def test_falls_back_outside_the_proof():
    proj = annular_projection(5, True, 1)
    Pro, outer = proj.build(2)
    free = np.setdiff1d(np.arange(Pro.shape[1]), outer)
    n = proj.N
    M = (sp.random(n, n, density=0.02, random_state=1) + sp.eye(n)).tocsr()
    p = _p2()
    _assert_identical(p.reduce(M.tocsc(), Pro, free), _reference(M.tocsc(), Pro, free))
    P2x = (Pro * 2.0).tocsr()               # not ±1: the original expression
    _assert_identical(p.reduce(M, P2x, free), _reference(M, P2x, free))
    assert p._red[2] is None
