"""The P2 nonlinear primitives, as PRIMITIVES.

`tests/test_physics_regression.py` can only say that a whole run moved. These
say which of `P2Nonlinear`'s parts moved, in milliseconds, and they exist
because the memo below is a performance cache sitting directly under the Newton
residual: if it ever serves a stale matrix, every pinned number in the suite
goes with it, and the failure would look like physics.
"""
from __future__ import annotations

import numpy as np
import pytest

from skfem import Basis, ElementTriP0, ElementTriP2, MeshTri, asm

from motor_ai_sim.simulation.field_ops import MU0
from motor_ai_sim.simulation.p2_nonlinear import P2Nonlinear, stiff_nu2


# Simple saturating curve: mu_r = 500 up to 1.2 T, flat after — enough that
# nu(|B|) really does depend on the field, so a stale K would show up.
CURVE = [(0.0, 0.0), (1.2 / (500 * MU0), 1.2), (1e6, 1.2 + MU0 * 1e6)]


class _Log:
    def warning(self, *a, **k): pass
    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass


def _p2():
    m = MeshTri().refined(4)
    b = Basis(m, ElementTriP2())
    b0 = b.with_element(ElementTriP0())
    n_el = m.t.shape[1]
    ids = np.arange(0, n_el, 2)                 # "iron" = every other element
    nu_const = np.full(n_el, 1.0 / MU0)
    nu_const[ids] = 0.0
    K_const = asm(stiff_nu2, b, nu=b0.interpolate(nu_const)).tocsr()
    sb = Basis(m, ElementTriP2(), elements=ids)
    obj = P2Nonlinear(basis=b, n_dof=b.N, K_const=K_const,
                      sat=[(ids, CURVE)],
                      sat_sub=[(sb, sb.with_element(ElementTriP0()),
                                ids, CURVE)],
                      pardiso=None, log=_Log())
    return obj, b


class TestKpwMemo:
    def test_repeat_call_is_served_from_the_memo(self):
        p, b = _p2()
        rng = np.random.default_rng(31)
        A = rng.standard_normal(b.N) * 1e-3
        K1, i1 = p.Kpw(A)
        assert (p.kpw_calls, p.kpw_hits) == (1, 0)
        K2, i2 = p.Kpw(A)
        assert (p.kpw_calls, p.kpw_hits) == (1, 1)
        assert K2 is K1 and i2 is i1

    def test_an_equal_but_distinct_array_also_hits(self):
        """The line search hands back a NEW array holding the same field."""
        p, b = _p2()
        rng = np.random.default_rng(32)
        A = rng.standard_normal(b.N) * 1e-3
        p.Kpw(A)
        K2, _ = p.Kpw(A + 0.0)          # equal content, different object
        assert p.kpw_hits == 1

    def test_a_changed_field_MISSES_and_gives_the_uncached_matrix(self):
        """The whole risk of a memo, checked head on: perturb the field and the
        matrix that comes back must be the one a memo-less solver would build,
        bit for bit."""
        p, b = _p2()
        rng = np.random.default_rng(33)
        A = rng.standard_normal(b.N) * 1e-3
        B = A.copy(); B[5] += 1e-4
        p.Kpw(A)
        Kb, _ = p.Kpw(B)
        assert p.kpw_hits == 0 and p.kpw_calls == 2

        fresh, _ = _p2()
        Kref, _ = fresh.Kpw(B)
        assert np.array_equal(Kb.indptr, Kref.indptr)
        assert np.array_equal(Kb.indices, Kref.indices)
        assert np.array_equal(Kb.data, Kref.data)

    def test_an_in_place_edit_is_not_served_stale(self):
        """The key is the array's CONTENT, not its identity — the one thing
        that makes this cache safe against a caller mutating its own vector."""
        p, b = _p2()
        rng = np.random.default_rng(34)
        A = rng.standard_normal(b.N) * 1e-3
        p.Kpw(A)
        A *= 3.0                           # same object, different field
        K2, _ = p.Kpw(A)
        assert p.kpw_hits == 0 and p.kpw_calls == 2

        fresh, _ = _p2()
        Kref, _ = fresh.Kpw(A)
        assert np.array_equal(K2.data, Kref.data)


class TestSkeletonAssembly:
    """The precomputed element skeleton must reproduce ``skfem.asm`` EXACTLY.

    This is the one place in the perf work where arithmetic was rearranged
    rather than merely repeated less often, so "same answer" is not good
    enough: the stiffness and the tangent are the residual and the Jacobian of
    the Newton the whole solver rests on, and a rounding-level difference here
    would show up as physics. array_equal on `.data`, `.indices` and `.indptr`
    — the pattern matters too, because skfem's `eliminate_zeros()` lets the
    VALUES decide it.
    """

    @staticmethod
    def _sub():
        from motor_ai_sim.simulation.p2_nonlinear import _Skeleton
        m = MeshTri().refined(4)
        ids = np.arange(0, m.t.shape[1], 2)
        sb = Basis(m, ElementTriP2(), elements=ids)
        return sb, _Skeleton(sb)

    def test_stiffness_is_bit_identical(self):
        sb, sk = self._sub()
        rng = np.random.default_rng(11)
        nu = 1.0 / (MU0 * (1.0 + 900.0 * rng.random(
            (sb.nelems, sb.dx.shape[-1]))))
        want = asm(stiff_nu2, sb, nu=nu)
        got = sk.stiff(nu)
        assert np.array_equal(got.indptr, want.indptr)
        assert np.array_equal(got.indices, want.indices)
        assert np.array_equal(got.data, want.data), (
            f"max |diff| = {np.max(np.abs(got.data - want.data)):.3e}")

    def test_tangent_is_bit_identical(self):
        from motor_ai_sim.simulation.p2_nonlinear import tang_nu2
        sb, sk = self._sub()
        rng = np.random.default_rng(12)
        shp = (sb.nelems, sb.dx.shape[-1])
        gA = rng.standard_normal((2,) + shp)
        c = rng.random(shp)
        want = asm(tang_nu2, sb, gA=gA, c=c)
        got = sk.tang(gA, c)
        assert np.array_equal(got.indptr, want.indptr)
        assert np.array_equal(got.indices, want.indices)
        assert np.array_equal(got.data, want.data), (
            f"max |diff| = {np.max(np.abs(got.data - want.data)):.3e}")

    def test_tangent_with_exact_zeros_keeps_skfems_pattern(self):
        """`max(dnu/dB^2, 0)` really does zero whole unsaturated blocks, and
        skfem drops those entries. The skeleton must drop the same ones, or
        the Jacobian's pattern — and the reused symbolic factorization keyed on
        it — would differ from what the solver used to build."""
        from motor_ai_sim.simulation.p2_nonlinear import tang_nu2
        sb, sk = self._sub()
        rng = np.random.default_rng(13)
        shp = (sb.nelems, sb.dx.shape[-1])
        gA = rng.standard_normal((2,) + shp)
        c = np.maximum(rng.standard_normal(shp), 0.0)     # ~half exactly zero
        assert (c == 0.0).any()
        want = asm(tang_nu2, sb, gA=gA, c=c)
        got = sk.tang(gA, c)
        assert got.nnz == want.nnz < sb.N ** 2
        assert np.array_equal(got.indices, want.indices)
        assert np.array_equal(got.data, want.data)

    def test_Kpw_matches_a_pure_skfem_assembly(self):
        """End to end through the class: the memo, the grad shortcut and the
        skeleton together still produce skfem's matrix."""
        p, b = _p2()
        rng = np.random.default_rng(14)
        A = rng.standard_normal(b.N) * 2e-3
        K, _ = p.Kpw(A)

        want = p.K_const
        for sb, _sb0, _ids, curve in p.sat_sub:
            gA = np.asarray(sb.interpolate(A).grad)
            Bm = np.sqrt(np.maximum(gA[0] ** 2 + gA[1] ** 2, 1e-18))
            from motor_ai_sim.simulation.field_ops import _mu_r_from_bh_vec
            mur = np.maximum(
                _mu_r_from_bh_vec(curve, Bm.ravel()).reshape(Bm.shape), 1.0)
            want = want + asm(stiff_nu2, sb, nu=1.0 / (MU0 * mur))
        want = want.tocsr()
        assert np.array_equal(K.indptr, want.indptr)
        assert np.array_equal(K.indices, want.indices)
        assert np.array_equal(K.data, want.data)


class TestAsmKConstMatrix:
    def test_K_const_is_not_mutated(self):
        """Both asmK and Kpw now start from K_const WITHOUT copying it."""
        p, b = _p2()
        before = p.K_const.data.copy()
        p.Kpw(np.zeros(b.N))
        p.asmK(np.full(b.mesh.t.shape[1], 1.0 / MU0))
        assert np.array_equal(p.K_const.data, before)

    def test_no_saturable_iron_still_returns_its_own_matrix(self):
        p, b = _p2()
        p.sat_sub = []
        p.sat = []
        K, info = p.Kpw(np.zeros(b.N))
        assert info == []
        assert K is not p.K_const
        K.data[:] = 0.0
        assert p.K_const.data.any(), "K_const was handed out, not copied"
