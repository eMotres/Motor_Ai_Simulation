"""The regularised preconditioner owns one native factorisation and releases
it exactly once (any backend: PARDISO, CHOLMOD, MUMPS, SuperLU)."""
import numpy as np
from scipy.sparse import csr_matrix, eye
from scipy.sparse.linalg import splu

from motor_ai_sim.simulation import linear_backend as LB
from motor_ai_sim.simulation.static3d.nedelec import (
    REG_PRECOND_C,
    _regularised_preconditioner,
)


class FakeFactor:
    def __init__(self, A, fail_release=False):
        self.A = A
        self.fail_release = fail_release
        self.freed = 0

    def solve(self, v):
        return np.linalg.solve(self.A.toarray(), v)

    def free(self):
        self.freed += 1
        if self.fail_release:
            raise RuntimeError("simulated native cleanup failure")


def _matrices():
    A = csr_matrix([[4.0, 1.0], [1.0, 3.0]])
    M = eye(2, format="csr")
    return A, M


def _areg(A, M):
    dK, dM = A.diagonal(), M.diagonal()
    eps = REG_PRECOND_C * np.median(dK[dM > 0] / dM[dM > 0])
    return (A + eps * M).tocsr()


def test_successful_factorization_cleanup_releases_once(monkeypatch):
    made = []

    def factorize(A, spd=False, log=None):
        assert spd, "A + eps*M is SPD and must be offered to Cholesky"
        made.append(FakeFactor(A))
        return made[-1]
    monkeypatch.setattr(LB, "factorize", factorize)
    A, M = _matrices()

    apply, release = _regularised_preconditioner(A, M)

    rhs = np.array([1.0, 2.0])
    np.testing.assert_allclose(apply(rhs), splu(_areg(A, M).tocsc()).solve(rhs))
    assert made[0].freed == 0
    release()
    assert made[0].freed == 1


def test_factorize_failure_gives_no_preconditioner(monkeypatch):
    def factorize(A, spd=False, log=None):
        raise RuntimeError("every backend failed")
    monkeypatch.setattr(LB, "factorize", factorize)
    A, M = _matrices()
    assert _regularised_preconditioner(A, M) == (None, None)


def test_real_backend_solves_the_regularised_system():
    """Whatever backend this environment has, the preconditioner is the exact
    inverse of A + eps*M and releases cleanly."""
    A, M = _matrices()
    apply, release = _regularised_preconditioner(A, M)
    rhs = np.array([1.0, 2.0])
    np.testing.assert_allclose(apply(rhs), splu(_areg(A, M).tocsc()).solve(rhs),
                               rtol=1e-12)
    release()
