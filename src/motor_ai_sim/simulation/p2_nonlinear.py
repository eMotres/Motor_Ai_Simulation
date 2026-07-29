"""The P2 nonlinear-iron core: the two element forms, the saturable stiffness
assembly, the differential-reluctivity tangent, and the damped-Picard sweep.

This is the layer every P2 solve in the sliding-band transient sits on — the
magnetostatic Newton, the voltage-drive Newton, the coupled eddy Newton and the
dq phasor initialiser all reach for the SAME ``Kpw``/``tangent2`` pair.  That
sameness is load-bearing rather than tidy: the residual and the Jacobian have to
be built from ONE nonlinearity or Newton converges to a field that is not the
root of the residual it reports, and the eddy branch has to see exactly the iron
the magnetostatic branch saw or the two disagree about the same machine.  Behind
one object, that cannot drift.

Extracted verbatim from ``fem_transient_sliding_band``, where it was eight
closures over ``K_const2``, ``_sat2``, ``_sat_sub2``, ``b2`` and the PARDISO
handle.  Every expression, and every comment recording why an expression is
written the way it is, is unchanged: the pointwise (not element-mean) ν in the
residual, the one-sided dν/dB² difference, the Irons–Tuck damping and the
TWO-consecutive-sweeps stop were each paid for with a measured wrong answer.

The state is per-run, not per-frame: nothing here knows the rotor angle.  The
frame loop passes ``Pro``/``free`` in on every call, which is the only thing
that moves.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
from skfem import BilinearForm, asm
from skfem.helpers import dot as _dot, grad as _grad
from scipy.sparse.linalg import splu as _splu

from motor_ai_sim.simulation.field_ops import (
    MU0, _mu_r_from_bh_vec, _p2_B_at_quad,
)


@BilinearForm
def stiff_nu2(u, v, w):
    return w["nu"] * _dot(_grad(u), _grad(v))


# Newton tangent (differential-reluctivity) term:  T(u,v) =
# 2·(dν/dB²)·(∇A·∇u)(∇A·∇v), with ∇A the current field gradient and
# dν/dB² per element.  Added to the secant stiffness K(ν) to form the
# Jacobian J = K + T of the magnetostatic residual R = K(ν(|B|))·A − f.
@BilinearForm
def tang_nu2(u, v, w):
    gA = w["gA"]                       # (2, nelem, nqp) current ∇A
    gu = _grad(u); gv = _grad(v)
    au = gA[0] * gu[0] + gA[1] * gu[1]
    av = gA[0] * gv[0] + gA[1] * gv[1]
    return w["c"] * au * av            # c = 2·dν/dB² (element-constant)


class P2Nonlinear:
    """Saturable-iron assembly + solve primitives on ONE P2 mesh.

    ``K_const`` is the whole-mesh stiffness of the NON-saturable ν (air,
    magnet, coil, shaft) with the iron elements zeroed — it never changes
    across frames or sweeps.  ``sat_sub`` carries one (sub-basis, P0
    sub-basis, element ids, B-H curve) per saturable tag, so a sweep
    re-assembles only the iron fraction.

    ``pardiso`` is the persistent MKL handle (or ``None`` for SuperLU); it is
    dropped to ``None`` on the first failure, so a broken PARDISO degrades once
    instead of once per solve.
    """

    def __init__(self, *, basis, n_dof: int,
                 K_const, sat: Sequence[tuple],
                 sat_sub: Sequence[tuple], pardiso, log) -> None:
        self.basis = basis
        self.N = int(n_dof)
        self.K_const = K_const
        self.sat = list(sat)
        self.sat_sub = list(sat_sub)
        self._pardiso = pardiso
        self._log = log

    # ── linear algebra ───────────────────────────────────────────────────────
    def solve_ff(self, Mff, rhs):
        """Solve Mff·x = rhs for a 1-D or 2-D (multi-column) rhs."""
        if self._pardiso is not None:
            try:
                return self._pardiso.solve(Mff, rhs)
            except Exception as _pe2:
                self._log.warning(
                    "pypardiso solve failed (%s) — SuperLU fallback", _pe2)
                self._pardiso = None
        return _splu(Mff).solve(rhs)

    def pad2(self, Pro, free, xf):
        _x = np.zeros(Pro.shape[1]); _x[free] = xf
        return Pro @ _x

    # ── iron nonlinearity ────────────────────────────────────────────────────
    def elemB(self, Avec):
        bx, by, dq = _p2_B_at_quad(self.basis, Avec)
        ar = dq.sum(axis=1)
        return (np.sqrt(bx ** 2 + by ** 2) * dq).sum(axis=1) \
            / np.maximum(ar, 1e-30)

    def nu_of(self, Bmag, base):
        nu = base.copy()
        for _ids, _c in self.sat:
            nu[_ids] = 1.0 / (MU0 * np.maximum(
                _mu_r_from_bh_vec(_c, Bmag[_ids]), 1.0))
        return nu

    def asmK(self, nu):
        K = self.K_const.copy()
        for _sb2, _sb02, _ids2, _c2 in self.sat_sub:
            _nf2 = _sb02.zeros(); _nf2[_ids2] = nu[_ids2]
            K = K + asm(stiff_nu2, _sb2, nu=_sb02.interpolate(_nf2))
        return K.tocsr()

    def Kpw(self, Avec):
        """POINTWISE ν(|B|) secant stiffness + the per-element data the
        Newton tangent needs.  Same nonlinearity in residual and tangent."""
        K = self.K_const.copy(); info = []
        for _sb2, _sb02, _ids2, _c2 in self.sat_sub:
            gA = _sb2.interpolate(Avec).grad            # (2,nel,nqp)
            Bm = np.sqrt(np.maximum(gA[0] ** 2 + gA[1] ** 2, 1e-18))
            mur = np.maximum(_mu_r_from_bh_vec(
                _c2, Bm.ravel()).reshape(Bm.shape), 1.0)
            nuq = 1.0 / (MU0 * mur)
            K = K + asm(stiff_nu2, _sb2, nu=nuq)
            info.append((_sb2, _ids2, _c2, gA, Bm, nuq))
        return K.tocsr(), info

    def tangent2(self, info):
        """T = 2·(dν/dB²)·(∇A·∇u)(∇A·∇v), pointwise & consistent with Kpw."""
        T = None
        for _sb2, _ids2, _c2, gA, Bm, nuq in info:
            _dB = 1e-3 * Bm + 1e-6
            nu1 = 1.0 / (MU0 * np.maximum(_mu_r_from_bh_vec(
                _c2, (Bm + _dB).ravel()).reshape(Bm.shape), 1.0))
            nup = np.maximum((nu1 - nuq) / _dB / (2.0 * Bm), 0.0)   # dν/dB²
            Ti = asm(tang_nu2, _sb2, gA=gA, c=2.0 * nup)
            T = Ti if T is None else T + Ti
        return T

    # ── globally convergent fallback / Newton seed ───────────────────────────
    def pic2_sweeps(self, Pro, free, bff, nu_in, n_max, tol,
                    linear_only=False):
        """Damped (Irons–Tuck) successive substitution on ν — one linear solve
        per sweep, ν refreshed from the element-mean |B| it produced.

        Returns (A, ν, res, nit) with ``res`` the RELATIVE ν change of the last
        sweep; stops early on two consecutive sweeps under ``tol`` (one sweep
        can dip by luck, two is a fixed point).

        This is the globally convergent method — it has no tangent to be wrong
        about — which is why it does DOUBLE duty here: it SEEDS the cold
        frame's Newton (see the frame loop) and it is the fallback if Newton
        still fails.  It is not, and must not become, the primary solver: it
        early-stops on a ν residual, which is a far weaker statement than the
        Newton path's |R(A)|/|f| < 1e-7 on the field itself."""
        _sat2 = self.sat
        nu = nu_in.copy()
        A2 = np.zeros(self.N); res = 0.0; nit = 0
        _ok = 0; _rp = None; _om = 0.5           # Irons–Tuck state
        for it in range(max(1, int(n_max))):
            nit = it + 1
            K = self.asmK(nu)
            Kff = (Pro.T @ K @ Pro).tocsr()[free][:, free].tocsc()
            A2 = self.pad2(Pro, free, self.solve_ff(Kff, bff))
            if linear_only or not _sat2:
                break                            # frozen frame: 1 linear solve
            Bmag_el = self.elemB(A2)
            _vo = np.concatenate([nu[_ids] for _ids, _ in _sat2])
            _vn = np.concatenate([
                1.0 / (MU0 * np.maximum(
                    _mu_r_from_bh_vec(_c, Bmag_el[_ids]), 1.0))
                for _ids, _c in _sat2])
            _rr = _vn - _vo
            res = float(np.linalg.norm(_rr) / max(np.linalg.norm(_vo), 1e-30))
            if _rp is not None:
                _dr = _rr - _rp; _den = float(_dr @ _dr)
                if _den > 0.0:
                    _om = float(np.clip(
                        -_om * float(_rp @ _dr) / _den, 0.05, 1.0))
            _rp = _rr
            _vu = _vo + _om * _rr
            _p0 = 0
            for _ids, _c in _sat2:
                nu[_ids] = _vu[_p0:_p0 + _ids.size]; _p0 += _ids.size
            if res < tol:
                _ok += 1
                if _ok >= 2:
                    break
            else:
                _ok = 0
        return A2, nu, res, nit
