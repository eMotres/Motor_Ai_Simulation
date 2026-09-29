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

import os as _os
import zlib as _zlib
from collections import OrderedDict as _OrderedDict
from typing import Sequence

import numpy as np
from skfem import BilinearForm, asm
from skfem.assembly.form.coo_data import COOData as _COOData
from skfem.helpers import dot as _dot, grad as _grad
from scipy.sparse import csr_matrix as _csr_matrix
from scipy.sparse.linalg import splu as _splu

from motor_ai_sim.simulation.pardiso_lifetime import release_pardiso

from motor_ai_sim.simulation.field_ops import (
    MU0, _grad_at_quad, _mu_r_from_bh_vec, _p2_B_at_quad,
)


# Value-symmetry tolerance of the Cholesky path, relative to √(a_ii·a_jj).
# The assembled operators are symmetric to round-off only (the tangent's
# (c·a_j)·a_i vs (c·a_i)·a_j products, sparse sums in different orders):
# measured ≤ 1e-15 on every exported system.  A genuinely unsymmetric
# operator is off by O(1); 1e-12 separates the two by three decades each way.
SPD_SYM_RTOL = 1e-12


class _SymPattern:
    """What the Cholesky path needs to know about one sparsity pattern."""

    __slots__ = ("key", "indptr", "indices", "ok", "why", "tri", "diag",
                 "u_indptr", "u_indices", "probe", "probe_tol", "analysed",
                 "digest")


def sym_pattern(indptr, indices, n: int) -> _SymPattern:
    """Upper triangle and diagonal of a sorted CSR pattern (for CSC arrays:
    of the transpose), built once per pattern like the LU ordering.  ``ok``
    is False, with ``why``, when a diagonal entry is missing: such a matrix
    cannot be SPD as stored and goes to LU.  Structural symmetry is not
    tested here: an entry without its mirror is an asymmetric VALUE, which the
    per-solve probe of :meth:`P2Nonlinear._solve_spd` catches."""
    p = _SymPattern()
    p.key = (int(n), int(indices.size))
    p.indptr = np.array(indptr, copy=True)
    p.indices = np.array(indices, copy=True)
    p.ok, p.why, p.analysed = False, None, None
    # the key of the ordering cache (P2Nonlinear._solve_spd): n, nnz and two
    # checksums of the pattern.  A collision could only cost fill-in, never
    # accuracy (any permutation is a valid ordering), and it is detected by
    # the factor size (see there).
    p.digest = (int(n), int(indices.size),
                _zlib.crc32(np.ascontiguousarray(indptr).view(np.uint8)),
                _zlib.crc32(np.ascontiguousarray(indices).view(np.uint8)))
    rows = np.repeat(np.arange(n, dtype=np.int32), np.diff(indptr))
    cols = np.asarray(indices, dtype=np.int32)
    diag = np.flatnonzero(cols == rows)
    if diag.size != n:
        p.why = "missing diagonal entries"
        return p
    p.diag = diag.astype(np.int32)                  # diag[i] = row i's entry
    p.tri = np.flatnonzero(cols >= rows).astype(np.int32)   # upper incl. diag
    p.u_indptr = np.concatenate(
        [[0], np.cumsum(np.asarray(indptr[1:], np.int64) - diag)]).astype(np.int32)
    p.u_indices = cols[p.tri]
    # fixed +-1 probe vector for the per-solve symmetry test (see _solve_spd)
    p.probe = np.where(np.random.default_rng(20260929).random(n) < 0.5,
                       -1.0, 1.0)
    p.probe_tol = SPD_SYM_RTOL * float(np.sqrt(max(int(np.max(np.diff(indptr),
                                                              initial=1)), 1)))
    p.ok = True
    return p


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


class _Skeleton:
    """The geometry half of the two forms above, computed once per sub-basis.

    ``skfem.asm`` re-derives everything on every call: the COO row/column
    index arrays, and — far more expensively — the pairwise products of the
    basis-function gradients, once for each of the ``Nbfun²`` = 36 local
    entries.  On a saturating Newton NONE of that moves.  The mesh, the
    element dofs and ∇φ at the quadrature points are fixed for the whole run;
    the only thing that changes between iterations is a scalar per quadrature
    point (ν for the stiffness, 2·dν/dB² for the tangent).

    So the geometry is pulled out and stored:

    * ``gg[j, i]`` = ∇φ_j · ∇φ_i at every (element, quadrature point) — the
      whole of ``dot(grad(u), grad(v))``, indexed the way skfem's own kernel
      loop is (``u`` = trial = j, ``v`` = test = i), and 36 of them where the
      caller only ever needed the SAME 36 again;
    * ``rows``/``cols`` — the COO scatter, identical every call;
    * ``dx`` — the integration measure.

    The tangent cannot precompute its full product (∇A moves), but it can
    stop recomputing ``gA·∇φ_i`` inside all 36 pairs when there are only 6
    distinct ones.

    BIT-IDENTICAL, deliberately and checkably.  Each ``data[j, i]`` below is
    written as the same expression skfem's kernel evaluates, with the same
    grouping and the same reduction — ``np.sum(form * dx, axis=1)`` where the
    form is ``(ν · gg)`` and ``(c · a_j) · a_i`` respectively — and the COO is
    handed to skfem's OWN ``_assemble_scipy_csr``, so the duplicate summation
    and the ``eliminate_zeros`` that decides the final pattern are not
    reimplemented here.  ``tests/test_p2_nonlinear.py::TestSkeletonAssembly``
    asserts array_equal against ``asm(...)`` on both forms.

    Memory: ``gg`` is Nbfun²·n_elem·n_qp float64 — 36·8 bytes per element and
    quadrature point, ~12 MB for the 40 mm machine's iron, against a run that
    already carries the factorization.
    """

    __slots__ = ("nb", "nt", "idx", "shape", "lshape", "dx", "gg", "g")

    def __init__(self, sb):
        self.nb = int(sb.Nbfun)
        self.nt = int(sb.nelems)
        nb, nt = self.nb, self.nt
        rows = np.zeros(nb * nb * nt, dtype=np.int32)
        cols = np.zeros(nb * nb * nt, dtype=np.int32)
        for j in range(nb):
            for i in range(nb):
                ix = slice(nt * (nb * j + i), nt * (nb * j + i + 1))
                rows[ix] = sb.element_dofs[i]
                cols[ix] = sb.element_dofs[j]
        self.idx = np.array([rows, cols])
        self.shape = (sb.N, sb.N)
        self.lshape = (nb, nb)
        self.dx = sb.dx
        # ∇φ_i at the quadrature points, (dim, n_elem, n_qp) per basis fn
        self.g = [sb.basis[i][0].get(1) for i in range(nb)]
        self.gg = np.empty((nb, nb, nt, self.dx.shape[-1]))
        for j in range(nb):
            for i in range(nb):
                gu, gv = self.g[j], self.g[i]
                self.gg[j, i] = gu[0] * gv[0] + gu[1] * gv[1]

    def stiff(self, nu):
        """``asm(stiff_nu2, sb, nu=nu)`` with the gradients already dotted."""
        nb, nt = self.nb, self.nt
        data = np.empty((nb, nb, nt))
        for j in range(nb):
            for i in range(nb):
                data[j, i] = np.sum((nu * self.gg[j, i]) * self.dx, axis=1)
        return _COOData._assemble_scipy_csr(
            self.idx, data.flatten('C'), self.shape, self.lshape)

    def tang(self, gA, c):
        """``asm(tang_nu2, sb, gA=gA, c=c)`` with gA·∇φ hoisted out of the
        36-way loop — there are only ``Nbfun`` distinct ones, not ``Nbfun²``."""
        nb, nt = self.nb, self.nt
        a = [gA[0] * gi[0] + gA[1] * gi[1] for gi in self.g]
        data = np.empty((nb, nb, nt))
        for j in range(nb):
            caj = c * a[j]
            for i in range(nb):
                data[j, i] = np.sum((caj * a[i]) * self.dx, axis=1)
        return _COOData._assemble_scipy_csr(
            self.idx, data.flatten('C'), self.shape, self.lshape)


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

    ``pardiso_spd`` is an optional second handle created with ``mtype=2``
    (real symmetric positive definite: Cholesky).  A caller that passes
    ``spd=True`` to :meth:`solve_ff` asserts that its operator is SPD BY
    CONSTRUCTION; the matrix is still checked at run time (structure, value
    symmetry, positive diagonal) and any doubt, or a Cholesky failure, takes
    the unsymmetric LU path above for that solve.  See
    ``docs/CHOLESKY_SPD_2026-09-29.md`` for the per-system proofs.
    """

    def __init__(self, *, basis, n_dof: int,
                 K_const, sat: Sequence[tuple],
                 sat_sub: Sequence[tuple], pardiso, log,
                 pardiso_spd=None) -> None:
        self.basis = basis
        self.N = int(n_dof)
        self.K_const = K_const
        self.sat = list(sat)
        self.sat_sub = list(sat_sub)
        self._pardiso = pardiso
        self._pardiso_spd = pardiso_spd
        self._log = log
        # geometry half of the two forms, one per saturable sub-basis, built
        # on first use (a cold run that never saturates never pays for it)
        self._skel = [None] * len(self.sat_sub)
        # ONE-DEEP MEMO for Kpw — see the note there.  (last A, K, info).
        self._kpw_memo = None
        self.kpw_calls = 0          # Kpw bodies actually executed
        self.kpw_hits = 0           # ...and calls served from the memo
        # PARDISO symbolic-factorization reuse — see solve_ff.
        self._pat = None            # (key, indptr, indices) last ANALYSED
        self._reuse = _os.environ.get("SB_NO_PARDISO_REUSE") != "1"
        self.pardiso_analyses = 0   # phase-11 calls
        self.pardiso_solves = 0     # phase-23 calls
        self.pardiso_perturbed = 0  # frames PARDISO had to perturb a pivot on
        # Cholesky (mtype 2) path — see _solve_spd.
        self._spd_pat = None        # _SymPattern of the last SPD analysis
        self.spd_solves = 0         # solves done by the Cholesky handle
        self.spd_analyses = 0       # its phase-11 calls
        self.spd_declined = 0       # spd=True solves sent to LU by the checks
        self.spd_failures = 0       # Cholesky errors (then LU for the run)
        # ORDERING CACHE of the Cholesky path (2026-09-29): the fill-reducing
        # permutation METIS computes in phase 11 depends on the PATTERN only,
        # and the patterns of a run repeat — every warm-up extension, the
        # demag pre-pass and the reported window march the same rotor angles,
        # so the slip pairing (the only thing that moves the pattern) repeats
        # every electrical period.  Phase 11 is then run with that permutation
        # given (iparm[4] = 1): symbolic factorisation only, no reordering —
        # the same ordering, so the same factorisation (bit for bit with one
        # MKL thread; threaded MKL has its usual run-to-run noise).
        # SB_PARDISO_ORDER_REUSE=0 re-orders every new pattern as before.
        self._order_reuse = _os.environ.get("SB_PARDISO_ORDER_REUSE", "1") != "0"
        self._orders = _OrderedDict()   # digest -> (1-based perm, nnz(L))
        self.spd_orders_computed = 0     # phase 11 with METIS
        self.spd_orders_reused = 0       # phase 11 with a cached permutation
        self.spd_orders_mismatch = 0     # cached permutation, other fill: redone

    # ── linear algebra ───────────────────────────────────────────────────────
    def solve_ff(self, Mff, rhs, spd: bool = False):
        """Solve Mff·x = rhs for a 1-D or 2-D (multi-column) rhs.

        REUSES THE SYMBOLIC FACTORIZATION while the sparsity pattern holds.
        ``pypardiso.solve`` runs MKL PARDISO **phase 13** — reordering +
        symbolic factorization + numeric factorization + back-solve — on every
        call, because it only skips the analysis when the matrix it is handed
        is byte-identical to the one it factorized last.  Inside a Newton
        sweep, or a Picard sweep, or the bordered eddy iteration, the matrix
        changes in its VALUES on every iteration and never in its PATTERN: the
        mesh, the dof numbering, the constraint projection and the free set are
        all fixed for the duration.  Reordering a 23 k-dof, 275 k-nnz Jacobian
        that was already reordered one iteration ago is the single largest line
        item in a P2 frame.

        Measured on one such Jacobian dumped out of the 40 mm frame loop
        (median of 7, 12 MKL threads):

            phase 13 (analysis+numeric+solve)   165 ms   <- every call today
            phase 23 (numeric+solve)             22 ms   <- analysis reused
            phase 33 (solve only)                 8 ms

        so ~87 % of every linear solve in this solver was re-deriving a
        permutation it already had.

        The pattern is CHECKED, not assumed: ``indptr``/``indices`` are
        compared against the analysed ones (a ~0.2 ms memcmp against a 20 ms
        solve) and a mismatch re-runs the analysis.  It has to be checked —
        skfem's assembler calls ``eliminate_zeros()`` on the COO before the
        CSR conversion, so an element matrix that happens to come out exactly
        zero (the Newton tangent's ``max(dν/dB², 0)`` makes whole unsaturated
        blocks exactly zero) drops structure, and the slip pairing ``Pro``
        changes shape between frames.

        NOT BIT-IDENTICAL, and neither is the code it replaces.  Phase 11
        computes the weighted matching and the scaling vectors from the
        matrix's VALUES, so an analysis inherited from the previous iterate
        pivots differently and the answer moves in its last digits.  That is
        only meaningful against the floor this solver already has: MKL's
        threaded numeric factorization is not run-to-run reproducible here
        either — the SAME matrix solved twice in one process differs by
        ~6e-14 relative, and two full runs of the pinned ``p2_load`` case on
        unmodified HEAD differ by 1.1e-14 in T_avg and 2.4e-12 in ripple.
        The reused analysis moves a single solve by ~6e-13 relative, i.e. the
        same order as the noise that was always there.  See the commit message
        for the whole-run numbers; ``SB_NO_PARDISO_REUSE=1`` turns it off.

        ``spd=True`` (2026-09-29): the caller's operator is symmetric positive
        definite by construction — the secant stiffness Pᵀ K(ν) P (ν > 0,
        Dirichlet rows removed), the Newton Jacobian Pᵀ(K + T)P (T is the
        Gram form of 2·max(dν/dB², 0) ≥ 0), and the bordered eddy matrix,
        whose σ block is Σ_b (a − Δt·U_b·1)ᵀ M_b (a − Δt·U_b·1)/Δt with
        g_b = M_b·1 and S_b = 1ᵀM_b1 exactly.  It is then factorised by
        Cholesky (PARDISO mtype 2, the upper triangle) instead of LU: half
        the work, no pivoting.  Checked, not assumed: :meth:`_solve_spd`
        declines (→ LU) a matrix whose pattern is not symmetric, whose
        values differ from their transpose beyond round-off, or whose
        diagonal is not positive, and a Cholesky error switches the run back
        to LU loudly.  ``SB_PARDISO_SPD=0`` (read where the handle is made)
        keeps every solve on LU.
        """
        if self._pardiso is not None:
            try:
                if spd and self._pardiso_spd is not None:
                    x = self._solve_spd(Mff, rhs)
                    if x is not None:
                        return x
                if self._reuse:
                    return self._solve_reuse(Mff, rhs)
                return self._pardiso.solve(Mff, rhs)
            except Exception as _pe2:
                self._log.warning(
                    "pypardiso solve failed (%s) — SuperLU fallback", _pe2)
                release_pardiso(self._pardiso)
                self._pardiso = None
                self._pat = None
                self._drop_spd()
        return _splu(Mff).solve(rhs)

    #: patterns whose ordering is kept (one int32 permutation each: 0.18 MB
    #: on the 44 k-dof L155 eddy system); a run needs one per rotor angle
    ORDER_CACHE_MAX = 512

    def _analyse_spd(self, s, U, pat):
        """Phase 11 of the Cholesky handle, with the pattern's cached METIS
        permutation when there is one (see the ordering cache in __init__).

        iparm[0] = 0 (pypardiso's initial state) makes MKL fill its defaults
        and ignore iparm[4], so the very first analysis of a handle is a plain
        one; MKL leaves iparm[0] = 1 and every later call honours iparm[4]:
        2 returns the permutation it computed, 1 uses the one given.  A
        cached permutation that yields a different factor size (iparm[17])
        than when it was computed means the pattern is not the one it was
        computed for (a checksum collision): logged, and the analysis is redone
        with METIS."""
        n = U.shape[0]
        z = np.zeros((n, 1))
        use = self._order_reuse and int(s.iparm[0]) == 1
        hit = self._orders.get(pat.digest) if use else None
        if hit is not None:
            s.iparm[4] = 1
            s.perm = hit[0]
            s.set_phase(11)
            s._call_pardiso(U, z)
            if int(s.iparm[17]) == hit[1]:
                self._orders.move_to_end(pat.digest)
                self.spd_orders_reused += 1
                s.iparm[4] = 0
                return
            self.spd_orders_mismatch += 1
            self._log.warning(
                "PARDISO ordering cache: the permutation stored for this "
                "pattern gives nnz(L) = %d instead of %d (n=%d) — pattern "
                "checksum collision; re-ordering with METIS",
                int(s.iparm[17]), hit[1], n)
            del self._orders[pat.digest]
        if use:
            s.iparm[4] = 2
            s.perm = np.zeros(n, dtype=np.int32)
        s.set_phase(11)
        s._call_pardiso(U, z)
        self.spd_orders_computed += 1
        if use:
            self._orders[pat.digest] = (s.perm.copy(), int(s.iparm[17]))
            if len(self._orders) > self.ORDER_CACHE_MAX:
                self._orders.popitem(last=False)
                if len(self._orders) == self.ORDER_CACHE_MAX:
                    self._log.info("PARDISO ordering cache full (%d patterns): "
                                   "oldest orderings are dropped",
                                   self.ORDER_CACHE_MAX)
        s.iparm[4] = 0

    def _drop_spd(self):
        if self._pardiso_spd is not None:
            release_pardiso(self._pardiso_spd)
            self._pardiso_spd = None
            self._spd_pat = None

    def _solve_spd(self, A, rhs):
        """Cholesky solve of an SPD-by-construction matrix, or ``None`` (the
        caller then takes the LU path) when the run-time checks decline it.

        Per PATTERN (once per analysis, like the LU ordering): the
        upper-triangle and diagonal positions (:func:`sym_pattern`, ~5 ms on
        the 0.5 M-nnz L155 system).  Per SOLVE: a positive diagonal and the
        scale-free symmetry test |a_ij - a_ji| <= SPD_SYM_RTOL*sqrt(a_ii*a_jj)
        (the natural scale of a matrix meant to be SPD, where
        |a_ij| <= sqrt(a_ii a_jj)) through a fixed +-1 probe v:
        ``max|D^-1/2 (A - A^T) D^-1/2 v|`` <= tol*sqrt(max row count), two
        sparse mat-vecs, ~1 ms (the pairwise gather costs ~4 ms per solve and
        a transpose map another ~10 ms per pattern).  An asymmetric pair
        (i, j) shows in rows i and j unless it cancels EXACTLY against other
        asymmetric entries of both rows; an entry without its mirror is such
        a pair.  Then phase 23 on the upper triangle.  A CSC matrix's arrays
        are the CSR arrays of A^T, which the test has just shown to equal A
        to round-off.
        """
        if A.format not in ("csr", "csc"):
            A = A.tocsr()
        if not A.has_sorted_indices:
            A.sort_indices()
        key = (int(A.shape[0]), int(A.nnz))
        pat = self._spd_pat
        new = not (pat is not None and pat.key == key
                   and np.array_equal(pat.indptr, A.indptr)
                   and np.array_equal(pat.indices, A.indices))
        if new:
            pat = sym_pattern(A.indptr, A.indices, A.shape[0])
            self._spd_pat = pat
        if not pat.ok:
            self.spd_declined += 1
            if self.spd_declined == 1:
                self._log.info("PARDISO Cholesky declined (%s, n=%d) — LU "
                               "for this matrix", pat.why, A.shape[0])
            return None
        d = A.data
        dg = d[pat.diag]
        why = None
        if not bool(np.all(dg > 0.0)):
            why = "non-positive diagonal"
        else:
            sc = 1.0 / np.sqrt(dg)
            w = sc * pat.probe
            y = A @ w
            y -= A.T @ w
            y *= sc
            asym = float(np.max(np.abs(y), initial=0.0))
            if not asym <= pat.probe_tol:
                why = "asymmetric values (%.3g of sqrt(a_ii a_jj))" % asym
        if why is not None:
            self.spd_declined += 1
            if self.spd_declined == 1:
                self._log.warning("PARDISO Cholesky declined (%s, n=%d) — LU "
                                  "for this matrix", why, A.shape[0])
            return None
        s = self._pardiso_spd
        U = _csr_matrix((d[pat.tri], pat.u_indices, pat.u_indptr),
                        shape=A.shape, copy=False)
        b = s._check_b(U, rhs)
        s.iparm[11] = 0                     # no transposed solve (symmetric)
        try:
            if new or pat.analysed is not s:
                self._analyse_spd(s, U, pat)
                pat.analysed = s
                self.spd_analyses += 1
            s.set_phase(23)
            x = s._call_pardiso(U, b)
        except Exception as _e:              # noqa: BLE001 — loud, then LU
            self.spd_failures += 1
            self._log.warning(
                "PARDISO Cholesky failed (%s, n=%d) on a matrix that passed "
                "the symmetry checks — not positive definite?  The rest of "
                "this run uses the unsymmetric LU.", _e, A.shape[0])
            self._drop_spd()
            return None
        self.spd_solves += 1
        return x

    def _solve_reuse(self, A, rhs):
        """phase 11 only when the pattern moved, then phase 23 (numeric+solve).

        Everything pypardiso's own ``solve`` does to the inputs is done here
        too, through its own helpers, so the matrix and the right-hand side
        reach MKL in exactly the state they reach it in today: ``_check_A``
        sets the transposed flag for CSC, sorts the indices and rejects an
        empty row; ``_check_b`` makes the rhs Fortran-ordered float64 and
        preserves its rank, which is what makes the return shape (1-D for a
        1-D rhs, 2-D for the multi-column back-solves) unchanged.
        """
        s = self._pardiso
        s._check_A(A)
        b = s._check_b(A, rhs)
        key = (A.format, int(A.shape[0]), int(A.nnz))
        _p = self._pat
        if not (_p is not None and _p[0] == key
                and np.array_equal(_p[1], A.indptr)
                and np.array_equal(_p[2], A.indices)):
            s.set_phase(11)
            s._call_pardiso(A, np.zeros((A.shape[0], 1)))
            self._pat = (key, A.indptr.copy(), A.indices.copy())
            self.pardiso_analyses += 1
        s.set_phase(23)
        self.pardiso_solves += 1
        x = s._call_pardiso(A, b)
        # iparm[13] (0-based) = number of perturbed pivots.  A reused ordering
        # is a valid ordering, not necessarily the best one for THIS matrix, so
        # this is the number that would say so.  Counted, and said once.
        if int(s.iparm[13]) > 0:
            self.pardiso_perturbed += 1
            if self.pardiso_perturbed == 1:
                self._log.info(
                    "PARDISO perturbed %d pivot(s) on a reused ordering "
                    "(n=%d); set SB_NO_PARDISO_REUSE=1 to re-analyse every "
                    "solve", int(s.iparm[13]), A.shape[0])
        return x

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

    # ``K_const`` is never written to, so the ``.copy()`` these two used to open
    # with was a whole matrix allocated and thrown away on the very next line:
    # ``K + asm(...)`` already returns a new matrix.  The accumulation ORDER is
    # unchanged — still ((K_const + K₁) + K₂) — so every entry is bit-identical;
    # only the no-saturable-iron case still copies, so a caller keeps getting a
    # matrix of its own rather than a handle on ``K_const``.
    def asmK(self, nu):
        K = self.K_const
        for _sb2, _sb02, _ids2, _c2 in self.sat_sub:
            _nf2 = _sb02.zeros(); _nf2[_ids2] = nu[_ids2]
            K = K + asm(stiff_nu2, _sb2, nu=_sb02.interpolate(_nf2))
        if K is self.K_const:
            K = K.copy()
        return K.tocsr()

    def Kpw(self, Avec):
        """POINTWISE ν(|B|) secant stiffness + the per-element data the
        Newton tangent needs.  Same nonlinearity in residual and tangent.

        MEMOISED ONE DEEP on the CONTENT of ``Avec``, because every Newton in
        this solver asks for the same field twice in a row and nobody noticed:

            for it in ...:
                K, info = Kpw(A)                 # <- (2) identical to (1)
                ...
                for ls in range(6):              # backtracking line search
                    A_try = A + lam*dA
                    Kt, _ = Kpw(A_try)           # <- (1) the ACCEPTED trial
                    if |R(A_try, Kt)| < |R|: A = A_try; break

        The accepted trial's field IS the next iteration's field, so (2)
        recomputes (1) bit for bit.  Measured on the 40 mm 12s/14p at 36
        steps: 2.29 Kpw bodies per Newton iteration, of which 1.0 is this
        duplicate.  Magnetostatic Newton, the bordered eddy Newton and both
        voltage Newtons share the pattern, which is why the memo lives here
        and not in any one of them.

        This is a PURE-PERF cache, and it is only sound because ``Kpw`` is a
        pure function of ``Avec``: it reads ``K_const`` and ``sat_sub``, both
        built once before the frame loop and never rewritten (the demag pass
        rebuilds the SOURCE ``f``, not the stiffness).  The key is the array's
        CONTENT (~20 µs for 23 k dofs against a 35 ms body), not its identity,
        so an in-place edit by a caller cannot serve a stale matrix.  The
        returned ``K``/``info`` are shared with the previous caller — every
        consumer in this repo reads them (``K + T``, ``K @ A``) and none
        mutates them.
        """
        _m = self._kpw_memo
        if _m is not None and _m[0].shape == Avec.shape \
                and np.array_equal(_m[0], Avec):
            self.kpw_hits += 1
            return _m[1], _m[2]
        self.kpw_calls += 1
        K = self.K_const; info = []
        for _k2, (_sb2, _sb02, _ids2, _c2) in enumerate(self.sat_sub):
            gA = _grad_at_quad(_sb2, Avec)              # (2,nel,nqp)
            Bm = np.sqrt(np.maximum(gA[0] ** 2 + gA[1] ** 2, 1e-18))
            mur = np.maximum(_mu_r_from_bh_vec(
                _c2, Bm.ravel()).reshape(Bm.shape), 1.0)
            nuq = 1.0 / (MU0 * mur)
            K = K + self.skel(_k2, _sb2).stiff(nuq)
            info.append((_k2, _ids2, _c2, gA, Bm, nuq))
        if K is self.K_const:
            K = K.copy()
        K = K.tocsr()
        self._kpw_memo = (Avec.copy(), K, info)
        return K, info

    def skel(self, k, sb):
        """The cached geometry of sub-basis ``k`` (built on first use)."""
        s = self._skel[k]
        if s is None:
            s = _Skeleton(sb)
            self._skel[k] = s
        return s

    def tangent2(self, info):
        """T = 2·(dν/dB²)·(∇A·∇u)(∇A·∇v), pointwise & consistent with Kpw."""
        T = None
        for _k2, _ids2, _c2, gA, Bm, nuq in info:
            _dB = 1e-3 * Bm + 1e-6
            nu1 = 1.0 / (MU0 * np.maximum(_mu_r_from_bh_vec(
                _c2, (Bm + _dB).ravel()).reshape(Bm.shape), 1.0))
            nup = np.maximum((nu1 - nuq) / _dB / (2.0 * Bm), 0.0)   # dν/dB²
            Ti = self._skel[_k2].tang(gA, 2.0 * nup)
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
            A2 = self.pad2(Pro, free, self.solve_ff(Kff, bff, spd=True))
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
