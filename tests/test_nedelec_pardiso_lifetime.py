import sys
import types

import numpy as np
from scipy.sparse import csr_matrix, eye
from scipy.sparse.linalg import splu

from motor_ai_sim.simulation.static3d.nedelec import (
    REG_PRECOND_C,
    _regularised_preconditioner,
)


class FakePardiso:
    def __init__(self, fail=False, fail_release=False):
        self.fail = fail
        self.fail_release = fail_release
        self.freed = []
        self.matrix = None

    def factorize(self, matrix):
        self.matrix = matrix
        if self.fail:
            raise RuntimeError("simulated factorization failure")

    def solve(self, matrix, vector):
        return np.linalg.solve(matrix.toarray(), vector)

    def free_memory(self, *, everything):
        self.freed.append(everything)
        if self.fail_release:
            raise RuntimeError("simulated native cleanup failure")


def _matrices():
    A = csr_matrix([[4.0, 1.0], [1.0, 3.0]])
    M = eye(2, format="csr")
    return A, M


def _install_fake(monkeypatch, solver):
    monkeypatch.setitem(
        sys.modules,
        "pypardiso",
        types.SimpleNamespace(PyPardisoSolver=lambda: solver),
    )


def test_factorize_failure_releases_once_then_superlu_fallback_works(monkeypatch):
    fake = FakePardiso(fail=True)
    _install_fake(monkeypatch, fake)
    A, M = _matrices()

    apply, release = _regularised_preconditioner(A, M)

    assert callable(apply)
    assert callable(release)
    assert fake.freed == [True]
    dK, dM = A.diagonal(), M.diagonal()
    eps = REG_PRECOND_C * np.median(dK[dM > 0] / dM[dM > 0])
    Areg = (A + eps * M).tocsr()
    rhs = np.array([1.0, 2.0])
    np.testing.assert_allclose(apply(rhs), splu(Areg.tocsc()).solve(rhs))


def test_cleanup_failure_does_not_break_fallback_or_repeat_release(monkeypatch):
    fake = FakePardiso(fail=True, fail_release=True)
    _install_fake(monkeypatch, fake)
    A, M = _matrices()

    apply, release = _regularised_preconditioner(A, M)

    assert callable(apply)
    assert callable(release)
    rhs = np.array([1.0, 2.0])
    assert np.isfinite(apply(rhs)).all()
    release()
    assert fake.freed == [True]


def test_successful_factorization_cleanup_releases_once(monkeypatch):
    fake = FakePardiso()
    _install_fake(monkeypatch, fake)
    A, M = _matrices()

    apply, release = _regularised_preconditioner(A, M)

    np.testing.assert_allclose(apply(np.array([1.0, 2.0])), fake.solve(fake.matrix, np.array([1.0, 2.0])))
    assert fake.freed == []
    release()
    assert fake.freed == [True]
