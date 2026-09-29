"""Cholesky (PARDISO mtype 2) for the SPD-by-construction P2 systems.

`P2Nonlinear.solve_ff(spd=True)` factorises with Cholesky only after run-time
checks (structure, value symmetry, positive diagonal) and falls back to the
unsymmetric LU on any doubt or on a Cholesky error.  These tests pin:

* the construction arguments of docs/CHOLESKY_SPD_2026-09-29.md on real P2
  operators: the Newton Jacobian K + T of a saturating field and the bordered
  eddy matrix with g_b = M_b·1, S_b = 1ᵀM_b1 are symmetric to round-off and
  positive definite, and the bordered matrix stops being so when S_b is not
  the Gram value (the proof depends on that identity, not on luck);
* the solve: Cholesky equals LU to round-off (CSR, CSC, several columns);
* the guards: an asymmetric matrix is declined (the per-pattern exact test
  and the per-solve probe), and a symmetric INDEFINITE matrix — the
  series-strand saddle point — fails Cholesky loudly and is solved by LU.
"""
from __future__ import annotations

import numpy as np
import pytest
import scipy.sparse as sp

from skfem import Basis, BilinearForm, ElementTriP0, ElementTriP2, MeshTri, asm

from motor_ai_sim.simulation.field_ops import MU0
from motor_ai_sim.simulation.p2_nonlinear import (
    P2Nonlinear, SPD_SYM_RTOL, stiff_nu2, sym_pattern)

pypardiso = pytest.importorskip("pypardiso")

CURVE = [(0.0, 0.0), (1.2 / (500 * MU0), 1.2), (1e6, 1.2 + MU0 * 1e6)]


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, *a, **k):
        self.warnings.append(a[0] % a[1:] if len(a) > 1 else a[0])

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


@BilinearForm
def _mass(u, v, w):
    return u * v


def _fixture(spd=True):
    m = MeshTri().refined(4)
    b = Basis(m, ElementTriP2())
    b0 = b.with_element(ElementTriP0())
    n_el = m.t.shape[1]
    ids = np.arange(0, n_el, 2)
    nu_const = np.full(n_el, 1.0 / MU0)
    nu_const[ids] = 0.0
    K_const = asm(stiff_nu2, b, nu=b0.interpolate(nu_const)).tocsr()
    sb = Basis(m, ElementTriP2(), elements=ids)
    log = _Log()
    p = P2Nonlinear(basis=b, n_dof=b.N, K_const=K_const, sat=[(ids, CURVE)],
                    sat_sub=[(sb, sb.with_element(ElementTriP0()), ids, CURVE)],
                    pardiso=pypardiso.PyPardisoSolver(), log=log,
                    pardiso_spd=(pypardiso.PyPardisoSolver(mtype=2) if spd
                                 else None))
    free = np.setdiff1d(np.arange(b.N), b.get_dofs().flatten())
    return p, b, m, free, log


def _newton_jacobian(p, b, free):
    # a field strong enough to saturate part of the iron (dν/dB² > 0 there)
    x, y = b.doflocs
    A = 2.5 * np.sin(np.pi * x) * np.sin(np.pi * y)
    K, info = p.Kpw(A)
    T = p.tangent2(info)
    assert T is not None and T.nnz > 0
    J = (K + T).tocsr()
    return J[free][:, free].tocsc()


def _bordered(p, b, m, free, dte=1e-4, sigma=5e6, gram=True):
    """[[Jff + Pᵀ Msig P/dte, −Bf], [−Bfᵀ, diag(S·dte)]] exactly as eddy_solve
    builds it, with two conducting bodies of the fixture mesh."""
    Jff = _newton_jacobian(p, b, free)
    cx = m.p[:, m.t].mean(axis=1)
    bodies = [np.flatnonzero((cx[0] < 0.3) & (cx[1] < 0.5)),
              np.flatnonzero((cx[0] > 0.6) & (cx[1] > 0.4))]
    Msig = sp.csr_matrix((b.N, b.N))
    g, S = [], []
    ones = np.ones(b.N)
    for ids in bodies:
        Mb = (asm(_mass, Basis(m, ElementTriP2(), elements=ids)) * sigma).tocsr()
        Msig = Msig + Mb
        gb = Mb @ ones
        g.append(gb)
        S.append(float(gb.sum()) * (1.0 if gram else 0.5))
    G = sp.csr_matrix(np.column_stack(g))
    Jb = (Jff + (Msig / dte).tocsr()[free][:, free]).tocsr()
    Bf = G.tocsr()[free, :]
    return sp.bmat([[Jb, -Bf], [-Bf.T, sp.diags(np.array(S) * dte)]]).tocsc()


def _lmin(M):
    return float(np.linalg.eigvalsh(M.toarray()).min())


class TestConstruction:
    @staticmethod
    def _pair_asym(J):
        """max |a_ij - a_ji| / sqrt(a_ii a_jj) over all pairs (dense, small)."""
        D = J.toarray()
        s = 1.0 / np.sqrt(np.diag(D))
        return float(np.max(np.abs((D - D.T) * s[:, None] * s[None, :])))

    def test_newton_jacobian_is_symmetric_to_roundoff_and_spd(self):
        p, b, m, free, _ = _fixture()
        J = _newton_jacobian(p, b, free)
        pat = sym_pattern(J.indptr, J.indices, J.shape[0])
        assert pat.ok
        assert self._pair_asym(J) < 1e-14 < SPD_SYM_RTOL
        assert _lmin(J) > 0.0

    def test_bordered_eddy_matrix_is_spd_by_the_gram_identity(self):
        p, b, m, free, _ = _fixture()
        Mb = _bordered(p, b, m, free)
        assert self._pair_asym(Mb) < 1e-14
        assert _lmin(Mb) > 0.0

    def test_without_the_gram_identity_it_is_not(self):
        # S_b = 1/2 1^T M_b 1 instead of 1^T M_b 1: the sigma block is no
        # longer a sum of squares and the matrix goes indefinite; the proof
        # uses the identity.
        p, b, m, free, _ = _fixture()
        Mb = _bordered(p, b, m, free, gram=False)
        assert _lmin(Mb) < 0.0

    def test_upper_triangle_is_the_matrix(self):
        p, b, m, free, _ = _fixture()
        J = _newton_jacobian(p, b, free).tocsr()
        pat = sym_pattern(J.indptr, J.indices, J.shape[0])
        U = sp.csr_matrix((J.data[pat.tri], pat.u_indices, pat.u_indptr),
                          shape=J.shape)
        assert abs(U - sp.triu(J)).max() == 0.0


class TestSolve:
    @pytest.mark.parametrize("fmt", ["csr", "csc"])
    def test_cholesky_equals_lu(self, fmt):
        p, b, m, free, log = _fixture()
        M = _bordered(p, b, m, free).asformat(fmt)
        rng = np.random.default_rng(7)
        rhs = rng.standard_normal((M.shape[0], 3))
        q, *_ = _fixture(spd=False)
        x_lu = q.solve_ff(M.copy(), rhs, spd=True)
        x_ch = p.solve_ff(M.copy(), rhs, spd=True)
        assert p.spd_solves == 1 and p.spd_analyses == 1 and q.spd_solves == 0
        assert np.max(np.abs(x_ch - x_lu)) <= 1e-9 * np.max(np.abs(x_lu))
        # same pattern, new values: the analysis is reused, the probe checks
        x2 = p.solve_ff((M * 1.5).asformat(fmt), rhs[:, 0], spd=True)
        assert p.spd_solves == 2 and p.spd_analyses == 1
        assert np.allclose(x2, x_lu[:, 0] / 1.5, rtol=1e-8, atol=0.0)
        assert not log.warnings

    def test_default_is_lu(self):
        p, b, m, free, _ = _fixture()
        J = _newton_jacobian(p, b, free)
        p.solve_ff(J, np.ones(J.shape[0]))
        assert p.spd_solves == 0 and p.pardiso_solves == 1


class TestGuards:
    def test_asymmetric_values_declined_on_a_new_pattern(self):
        p, b, m, free, log = _fixture()
        J = _newton_jacobian(p, b, free).tocsr()
        J.data[J.indices > np.repeat(np.arange(J.shape[0]), np.diff(J.indptr))] *= 1.001
        rhs = np.ones(J.shape[0])
        x = p.solve_ff(J, rhs, spd=True)
        assert p.spd_solves == 0 and p.spd_declined == 1
        assert np.linalg.norm(J @ x - rhs) <= 1e-10 * np.linalg.norm(rhs)
        assert any("declined" in w for w in log.warnings)

    def test_asymmetric_values_on_a_known_pattern_caught_by_the_probe(self):
        p, b, m, free, log = _fixture()
        J = _newton_jacobian(p, b, free).tocsr()
        rhs = np.ones(J.shape[0])
        p.solve_ff(J.copy(), rhs, spd=True)                 # analysed, symmetric
        assert p.spd_solves == 1
        J2 = J.copy()
        k = int(np.flatnonzero(J2.indices > 0)[5])          # one entry, one side
        J2.data[k] *= 1.0 + 1e-6
        x = p.solve_ff(J2, rhs, spd=True)
        assert p.spd_solves == 1 and p.spd_declined == 1
        assert np.linalg.norm(J2 @ x - rhs) <= 1e-10 * np.linalg.norm(rhs)

    def test_symmetric_indefinite_fails_cholesky_and_lu_solves_it(self):
        # the series-strand saddle point: symmetric, zero diagonal block —
        # with the diagonal made positive so it passes the cheap tests and
        # only the factorisation can tell (λmin < 0).
        p, b, m, free, log = _fixture()
        J = _newton_jacobian(p, b, free)
        n = J.shape[0]
        c = sp.csr_matrix(np.eye(n, 1) * 10.0 * abs(J).max())
        M = sp.bmat([[J, c], [c.T, sp.eye(1) * 1e-12]]).tocsc()
        assert _lmin(M) < 0.0
        rhs = np.ones(n + 1)
        x = p.solve_ff(M, rhs, spd=True)
        assert p.spd_failures == 1 and p._pardiso_spd is None
        assert np.linalg.norm(M @ x - rhs) <= 1e-8 * np.linalg.norm(rhs)
        assert any("Cholesky failed" in w for w in log.warnings)
        # the rest of the run stays on LU, without complaint
        p.solve_ff(M, rhs, spd=True)
        assert p.spd_failures == 1

    def test_entry_without_its_mirror_is_declined(self):
        p, b, m, free, log = _fixture()
        J = _newton_jacobian(p, b, free).tolil()
        n = J.shape[0]
        i, j = 3, n - 5                        # far apart: no mirror entry
        assert J[i, j] == 0.0 and J[j, i] == 0.0
        J[i, j] = 0.1 * np.sqrt(J[i, i] * J[j, j])
        J = J.tocsr()
        rhs = np.ones(n)
        x = p.solve_ff(J, rhs, spd=True)
        assert p.spd_solves == 0 and p.spd_declined == 1
        assert np.linalg.norm(J @ x - rhs) <= 1e-10 * np.linalg.norm(rhs)

    def test_missing_diagonal_pattern(self):
        A = sp.csr_matrix(np.array([[2.0, 1.0, 0.0], [1.0, 0.0, 1.0],
                                    [0.0, 1.0, 2.0]]))
        A.eliminate_zeros()
        pat = sym_pattern(A.indptr, A.indices, 3)
        assert not pat.ok and "diagonal" in pat.why
