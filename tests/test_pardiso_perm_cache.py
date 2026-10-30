"""The PARDISO ordering cache changes the cost of an analysis, never its answer.

A cached ordering is METIS's own ordering for that exact pattern, so a solve
analysed from the cache must be BIT-IDENTICAL to one analysed from scratch —
for the same matrix, and for the same pattern carrying different values (the
Newton iterations of a revisited slip position).  Small synthetic SPD
matrices only; no field solve.
"""
from __future__ import annotations

import numpy as np
import pytest
import scipy.sparse as sp

from skfem import Basis, BilinearForm, ElementTriP2, MeshTri, asm
from skfem.helpers import dot, grad

from motor_ai_sim.simulation import pardiso_perm_cache as ppc
from motor_ai_sim.simulation.p2_nonlinear import P2Nonlinear

pypardiso = pytest.importorskip("pypardiso")


@pytest.fixture(autouse=True)
def _mkl_rt_path(monkeypatch):
    """Skip PyPardisoSolver's recursive search for mkl_rt on every handle
    (the solver publishes the same path; see fem_solver_2d)."""
    import os
    if not os.environ.get("PYPARDISO_MKL_RT"):
        path = pypardiso.scipy_aliases.pypardiso_solver.libmkl._name
        if path:
            monkeypatch.setenv("PYPARDISO_MKL_RT", str(path))


class _Log:
    def warning(self, *a, **k):
        pass

    info = debug = warning


@BilinearForm
def _lap(u, v, w):
    return w["nu"] * dot(grad(u), grad(v))


def _spd(scale=1.0, refine=4, drop=0):
    """A P2 stiffness with a positive shift, Dirichlet-free and SPD; ``drop``
    removes that many leading rows/columns (a different pattern)."""
    m = MeshTri().refined(refine)
    b = Basis(m, ElementTriP2())
    nu = 1.0 + scale * np.linspace(0.0, 1.0, b.dx.size).reshape(b.dx.shape)
    K = asm(_lap, b, nu=nu).tocsr()
    K = (K + 1e-2 * sp.eye(K.shape[0])).tocsr()[drop:, drop:].tocsc()
    K.sort_indices()
    return K


def _revalued(A, f=1.7):
    """A's pattern carrying other (still SPD) values — a later Newton
    iteration at the same slip position."""
    A2 = A.copy()
    A2.data = A2.data * f
    A2.setdiag(A2.diagonal() + 0.3)
    assert np.array_equal(A2.indptr, A.indptr)
    assert np.array_equal(A2.indices, A.indices)
    return A2


def _solver(cache):
    p = P2Nonlinear(basis=None, n_dof=0, K_const=None, sat=[], sat_sub=[],
                    pardiso=pypardiso.PyPardisoSolver(),
                    pardiso_spd=pypardiso.PyPardisoSolver(mtype=2), log=_Log())
    return p


@pytest.fixture()
def fresh_cache(monkeypatch):
    c = ppc.PermCache(64 * 1024 * 1024)
    monkeypatch.setattr(ppc, "PERM_CACHE", c)
    return c


def _solve_uncached(M, rhs, monkeypatch):
    off = ppc.PermCache(0)
    with monkeypatch.context() as mp:
        mp.setattr(ppc, "PERM_CACHE", off)
        return _solver(off).solve_ff(M.copy(), rhs, spd=True)


def test_revisited_pattern_is_bit_identical_and_hits(fresh_cache, monkeypatch):
    A = _spd(0.3)
    B = _spd(0.3, drop=3)           # another pattern in between
    A2 = _revalued(A)               # A's pattern, other values
    rhs_a = np.linspace(1.0, 2.0, A.shape[0])
    rhs_b = np.linspace(1.0, 2.0, B.shape[0])
    ref = [_solve_uncached(M, r, monkeypatch)
           for M, r in ((A, rhs_a), (B, rhs_b), (A, rhs_a), (A2, rhs_a))]
    p = _solver(fresh_cache)
    got = [p.solve_ff(M.copy(), r, spd=True)
           for M, r in ((A, rhs_a), (B, rhs_b), (A, rhs_a), (A2, rhs_a))]
    for x, y in zip(got, ref):
        assert np.array_equal(x, y)
    # A, B analysed by METIS; A again (new pattern after B) from the cache;
    # A2 has A's pattern and is on the handle already: no analysis at all
    assert p.spd_analyses == 3
    assert p.spd_perm_hits == 1
    assert fresh_cache.hits == 1 and fresh_cache.misses == 2


def test_cache_is_shared_across_runs(fresh_cache, monkeypatch):
    """A second run (a coupled pass on the same mesh) starts warm."""
    A = _spd(0.3)
    rhs = np.ones(A.shape[0])
    x1 = _solver(fresh_cache).solve_ff(A.copy(), rhs, spd=True)
    p2 = _solver(fresh_cache)
    x2 = p2.solve_ff(A.copy(), rhs, spd=True)
    assert p2.spd_perm_hits == 1
    assert np.array_equal(x1, x2)
    assert np.array_equal(x2, _solve_uncached(A, rhs, monkeypatch))


def test_disabled_cache_never_touches_iparm(monkeypatch):
    off = ppc.PermCache(0)
    monkeypatch.setattr(ppc, "PERM_CACHE", off)
    p = _solver(off)
    A = _spd(0.1)
    p.solve_ff(A, np.ones(A.shape[0]), spd=True)
    assert p.spd_perm_hits == 0 and off.hits == 0 and off.misses == 0


def test_handle_returns_to_mkl_ordering_after_each_analysis(fresh_cache):
    A = _spd(0.2)
    p = _solver(fresh_cache)
    p.solve_ff(A.copy(), np.ones(A.shape[0]), spd=True)
    s = p._pardiso_spd
    assert int(s.iparm[4]) == 0 and s.perm.size == 0
    assert int(s.iparm[0]) == 1


def test_lru_budget():
    c = ppc.PermCache(3 * 40)                     # three 10-entry orderings
    keys = [("k", i) for i in range(5)]
    for k in keys:
        c.put(k, np.arange(1, 11, dtype=np.int32))
    assert c.get(keys[0]) is None and c.get(keys[1]) is None
    assert c.get(keys[4]) is not None
    assert c._bytes <= c.budget


def test_key_depends_on_pattern_not_values():
    A, B = _spd(0.2), _spd(0.2, drop=1)
    A2 = _revalued(A)
    k = ppc.PermCache.key
    assert k(2, A.indptr, A.indices) == k(2, A2.indptr, A2.indices)
    assert k(2, A.indptr, A.indices) != k(2, B.indptr, B.indices)
    assert k(2, A.indptr, A.indices) != k(11, A.indptr, A.indices)
