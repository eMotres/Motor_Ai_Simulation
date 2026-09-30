"""Sparse direct linear solves behind one small interface (2026-09-30).

Every sparse direct solve of the FEM code (the P2 static Newton, the eddy BDF2
march, the voltage drive, the d-axis / psi_PM calibration, Stage A / 3-D, the
contact mechanics and the TDM prototype) goes through this module.  It has
three layers:

``Factor``
    One factorisation of one sparsity pattern, from one library:

    ============  ==========  =====================================================
    class          kind        library (licence)
    ============  ==========  =====================================================
    CholmodFactor  cholesky    SuiteSparse CHOLMOD via scikit-sparse
                               (CHOLMOD LGPL-2.1+, its Supernodal module GPL-2.0+;
                               scikit-sparse BSD-2-Clause)
    MumpsFactor    cholesky    MUMPS via python-mumps, SYM=1 (MUMPS CeCILL-C;
                   or lu       python-mumps BSD-2-Clause), or SYM=0 (LU)
    SuperLUFactor  lu          SciPy SuperLU (BSD-3-Clause); last resort
    PardisoFactor  cholesky    Intel MKL PARDISO via pypardiso, mtype 2 or 11
                   or lu       (MKL: Intel Simplified Software License; OPTIONAL)
    ============  ==========  =====================================================

    Methods: ``analyze(A)`` (symbolic: ordering), ``factorize(A)`` (numeric,
    on the analysed pattern), ``refactorize(A)`` (the same, spelled for the
    "same pattern, new values" call), ``solve(b)``, ``free()``.  A Cholesky
    factor is handed the FULL symmetric matrix and takes the triangle it needs.

``FactorStream``
    A factor plus the pattern it was analysed for: ``factor(A)`` re-runs the
    analysis only when ``indptr``/``indices`` moved (a memcmp against a
    factorisation), then factorises.  One stream per matrix family.

``LinearSolver``
    The router one run owns: ``solve(A, rhs, spd=False)``, or ``factor(A, spd)``
    followed by any number of ``solve_factored(b)``.  ``spd=True`` asserts the
    operator is symmetric positive definite BY CONSTRUCTION
    (docs/CHOLESKY_SPD_2026-09-29.md); it is still checked (structure, exact
    pairwise value symmetry, positive diagonal) and any doubt goes to LU.

Backend selection (``SB_LINEAR_BACKEND``, default ``auto``):

* ``auto``    -- PARDISO when pypardiso/MKL is importable (our server image),
  otherwise ``open``.  ``SB_NO_PARDISO=1`` makes auto ignore an installed MKL.
* ``open``    -- SPD: CHOLMOD below ``SB_SPD_MUMPS_MIN_DOF`` unknowns, MUMPS
  (SYM=1) at or above it; LU: MUMPS.  What auto picks without MKL.
* ``cholmod`` -- SPD: CHOLMOD at every size; LU: MUMPS.
* ``mumps``   -- SPD: MUMPS SYM=1; LU: MUMPS.
* ``pardiso`` -- MKL PARDISO for both (mtype 2 / 11).  Without pypardiso the
  request is refused loudly (warning + result note) and ``open`` is used.
* ``superlu`` -- SciPy SuperLU for everything (reference / debugging).

A library that is not installed degrades to the next one once, with a warning
and a note (``LinearSolver.notes``, carried into the run result).

Fallbacks, loud and once per run: a Cholesky failure (not positive definite)
sends the rest of the run to LU; an LU failure (MUMPS or PARDISO) sends the
rest of the run to SuperLU.  Each writes a warning and a note.

Thread safety: one ``LinearSolver`` (or ``FrameFactor``) per thread is the
design; each also serialises its own calls with a lock, so sharing one is safe
but not parallel.  Distinct objects are independent: every CHOLMOD factor owns
its ``cholmod_common`` and every MUMPS instance its own structure and lock.
python-mumps releases the GIL inside MUMPS, so MUMPS factorisations in several
threads run in parallel; scikit-sparse 0.5 does NOT release the GIL inside
CHOLMOD, so CHOLMOD factorisations from several threads are correct but
serialised.  ``FrameFactor(prefer_parallel=True)`` (TDM with workers > 1)
therefore uses MUMPS SYM=1 for the SPD frames in the open build.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import warnings
from typing import Any, Callable, Dict, List, Optional

import numpy as np
from scipy.sparse import csc_matrix as _csc_matrix, csr_matrix as _csr_matrix

_log = logging.getLogger(__name__)

#: Value-symmetry tolerance of the Cholesky path, relative to sqrt(a_ii*a_jj).
#: The assembled operators are symmetric to round-off only: measured <= 1e-15 on
#: every exported system; a genuinely unsymmetric operator is off by O(1).
SPD_SYM_RTOL = 1e-12

#: SPD systems with at least this many unknowns go to MUMPS (SYM=1) instead of
#: CHOLMOD in the ``open`` selection.  Calibrated on the exported production
#: matrices (docs/OPEN_SOLVERS_2026-09-30.md): CHOLMOD-AMD is faster up to the
#: L155 x0.5 mesh (60.8 k DOF), MUMPS from the L155 x0.3 mesh (97.4 k) up.
SPD_MUMPS_MIN_DOF_DEFAULT = 80_000

BACKENDS = ("auto", "open", "cholmod", "mumps", "pardiso", "superlu")


# ════════════════════════════════════════════════════════════════════════
#  availability
# ════════════════════════════════════════════════════════════════════════
_avail: Dict[str, bool] = {}
_avail_lock = threading.Lock()


def _probe(name: str) -> bool:
    with _avail_lock:
        if name not in _avail:
            ok = False
            try:
                if name == "cholmod":
                    from sksparse import cholmod as _c  # noqa: F401
                    ok = hasattr(_c, "CholeskyFactor")
                elif name == "mumps":
                    import mumps as _m
                    ok = hasattr(_m, "Context") and hasattr(
                        getattr(_m, "_mumps", None), "dmumps")
                    if not ok:
                        from mumps import _mumps  # noqa: F401
                        ok = hasattr(_mumps, "dmumps")
                elif name == "pardiso":
                    import pypardiso as _p
                    ok = hasattr(_p, "PyPardisoSolver")
                elif name == "superlu":
                    ok = True
            except Exception:                 # noqa: BLE001 — not installed / broken
                ok = False
            _avail[name] = ok
        return _avail[name]


def available(name: str) -> bool:
    """Is library ``name`` (cholmod, mumps, pardiso, superlu) importable?"""
    if name == "pardiso" and os.environ.get("SB_NO_PARDISO") == "1":
        return False
    return _probe(name)


def available_backends() -> Dict[str, bool]:
    return {k: available(k) for k in ("cholmod", "mumps", "pardiso", "superlu")}


def requested_backend() -> str:
    v = (os.environ.get("SB_LINEAR_BACKEND") or "auto").strip().lower()
    return v if v in BACKENDS else "auto"


def pardiso_selected(env: Optional[Dict[str, str]] = None) -> bool:
    """Would a run with this environment use MKL PARDISO?  Used to decide
    whether a child process needs the MKL runtime hint (pardiso_runtime)."""
    e = os.environ if env is None else env
    if e.get("SB_NO_PARDISO") == "1":
        return False
    req = (e.get("SB_LINEAR_BACKEND") or "auto").strip().lower()
    return req in ("auto", "pardiso", "") or req not in BACKENDS


def spd_mumps_min_dof() -> int:
    try:
        return max(0, int(os.environ.get("SB_SPD_MUMPS_MIN_DOF",
                                         SPD_MUMPS_MIN_DOF_DEFAULT)))
    except ValueError:
        return SPD_MUMPS_MIN_DOF_DEFAULT


# ════════════════════════════════════════════════════════════════════════
#  the SPD checks (moved from p2_nonlinear, unchanged)
# ════════════════════════════════════════════════════════════════════════
class SymPattern:
    """What the Cholesky path needs to know about one sparsity pattern."""

    __slots__ = ("key", "indptr", "indices", "ok", "why", "tri", "diag",
                 "u_indptr", "u_indices", "up", "lo", "up_rep", "up_c",
                 "analysed")


def sym_pattern(indptr, indices, n: int) -> SymPattern:
    """Everything the Cholesky path needs about one CANONICAL CSR pattern
    (sorted, duplicate-free; for CSC arrays: the pattern of the transpose),
    built once per pattern like the LU ordering:

    * ``tri``/``u_indptr``/``u_indices``: the upper triangle incl. the
      diagonal as a CSR matrix.  Row i of the upper triangle is the entries
      from the diagonal to the end of row i, so its count is
      ``indptr[i+1] - diag[i]`` (``diag[i]`` = global position of a_ii) and
      ``u_indptr`` is the cumulative sum of those counts;
    * ``up``/``lo``: the position of every strictly-upper entry (i, j) and of
      its mirror (j, i) -- the transpose map of the stored entries;
    * ``up_rep``/``up_c``: row counts and column of each strictly-upper
      entry, for the scale sqrt(a_ii a_jj) of the exact symmetry test.

    ``ok`` is False, with ``why``, when a diagonal entry is missing or an
    entry has no mirror (not structurally symmetric): such a matrix cannot
    be SPD as stored and goes to LU."""
    p = SymPattern()
    p.key = (int(n), int(indices.size))
    p.indptr = np.array(indptr, copy=True)
    p.indices = np.array(indices, copy=True)
    p.ok, p.why, p.analysed = False, None, None
    nnz = int(indices.size)
    counts = np.diff(indptr)
    rows = np.repeat(np.arange(n, dtype=np.int32), counts)
    cols = np.asarray(indices, dtype=np.int32)
    diag = np.flatnonzero(cols == rows)
    if diag.size != n:
        p.why = "missing diagonal entries"
        return p
    # the stored position of (j, i) for every stored (i, j): transpose an
    # index-valued copy of the pattern (C-level csr->csc, O(nnz))
    T = _csr_matrix((np.arange(nnz, dtype=np.int32), indices, indptr),
                    shape=(n, n)).tocsc()
    if not (np.array_equal(T.indptr, indptr)
            and np.array_equal(T.indices, indices)):
        p.why = "entries without a mirror (not structurally symmetric)"
        return p
    p.diag = diag.astype(np.int32)                  # diag[i] = row i's entry
    upper = cols >= rows
    p.tri = np.flatnonzero(upper).astype(np.int32)  # upper incl. diag
    u_cnt = np.asarray(indptr[1:], np.int64) - diag  # per-row upper counts
    p.u_indptr = np.concatenate([[0], np.cumsum(u_cnt)]).astype(np.int32)
    p.u_indices = cols[p.tri]
    p.up = np.flatnonzero(cols > rows).astype(np.int32)      # strictly upper
    p.lo = np.asarray(T.data, dtype=np.int32)[p.up]          # their (j, i)
    p.up_rep = (u_cnt - 1).astype(np.int64)                  # per row
    p.up_c = cols[p.up]
    p.ok = True
    return p


def spd_value_check(A, pat: SymPattern) -> Optional[str]:
    """``None`` when A (canonical, pattern ``pat``) has a positive diagonal and
    |a_ij - a_ji| <= SPD_SYM_RTOL*sqrt(a_ii a_jj) on every stored pair; else
    the reason.  Deterministic and exact (no probe vector)."""
    d = A.data
    dg = d[pat.diag]
    if not bool(np.all(dg > 0.0)):
        return "non-positive diagonal"
    sc = 1.0 / np.sqrt(dg)
    dd = d[pat.up]
    dd -= d[pat.lo]
    dd *= np.repeat(sc, pat.up_rep)       # 1/sqrt(a_ii), row order
    dd *= sc[pat.up_c]                    # 1/sqrt(a_jj)
    asym = float(np.max(np.abs(dd), initial=0.0))
    if not asym <= SPD_SYM_RTOL:
        return "asymmetric values (%.3g of sqrt(a_ii a_jj))" % asym
    return None


def canonical(A):
    """CSR/CSC with sorted, duplicate-free indices (a copy only if needed)."""
    if getattr(A, "format", None) not in ("csr", "csc"):
        A = _csr_matrix(A)
    if not A.has_canonical_format:
        A = A.copy()
        A.sum_duplicates()               # sorts and merges duplicates
    return A


class NotPositiveDefinite(RuntimeError):
    """A Cholesky factorisation found the matrix not positive definite."""


# ════════════════════════════════════════════════════════════════════════
#  factors
# ════════════════════════════════════════════════════════════════════════
class Factor:
    """One factorisation of one sparsity pattern (the backend interface).

    Contract: ``analyze(A)`` once per pattern, then ``factorize(A)`` (or
    ``refactorize(A)``) for each new set of values on THAT pattern, then any
    number of ``solve(b)`` (1-D or 2-D ``b``; the result has the same rank).
    ``free()`` releases the native memory now (it is also released when the
    object is collected).  A Cholesky factor gets the full symmetric matrix.
    Not re-entrant: callers serialise (``FactorStream`` does)."""

    backend = "?"
    kind = "lu"

    def analyze(self, A, sym: Optional[SymPattern] = None) -> None:
        raise NotImplementedError

    def factorize(self, A, sym: Optional[SymPattern] = None) -> None:
        raise NotImplementedError

    def refactorize(self, A, sym: Optional[SymPattern] = None) -> None:
        self.factorize(A, sym)

    def solve(self, b) -> np.ndarray:
        raise NotImplementedError

    def factorize_solve(self, A, b, sym: Optional[SymPattern] = None):
        self.factorize(A, sym)
        return self.solve(b)

    def free(self) -> None:
        pass

    def stats(self) -> Dict[str, Any]:
        return {}


def _as_symmetric_csc(A):
    """CSC view of a SYMMETRIC canonical CSR/CSC matrix without a copy: the
    CSR arrays read as CSC are the transpose, which is the matrix itself."""
    if A.format == "csc":
        return A
    return _csc_matrix((A.data, A.indices, A.indptr), shape=A.shape, copy=False)


class CholmodFactor(Factor):
    """SuiteSparse CHOLMOD (supernodal/simplicial chosen by CHOLMOD), AMD
    ordering by default (``SB_CHOLMOD_ORDER``): the cheaper weighted choice at
    production sizes (docs/OPEN_SOLVERS_2026-09-30.md).  The symbolic analysis
    is kept and ``factorize`` recomputes only the numbers."""

    backend = "cholmod"
    kind = "cholesky"

    def __init__(self, order: Optional[str] = None,
                 mode: Optional[str] = None) -> None:
        self.order = order or os.environ.get("SB_CHOLMOD_ORDER", "amd")
        self.mode = mode or os.environ.get("SB_CHOLMOD_MODE", "auto")
        self._f = None
        self.rcond_min = None
        self.near_singular_solves = 0

    def analyze(self, A, sym=None) -> None:
        from sksparse import cholmod as _cm
        self._f = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._f = _cm.CholeskyFactor(_as_symmetric_csc(A), lower=True,
                                         order=self.order,
                                         supernodal_mode=self.mode)

    def factorize(self, A, sym=None) -> None:
        from sksparse import cholmod as _cm
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._f.factorize(_as_symmetric_csc(A))
        except _cm.CholmodNotPositiveDefiniteError as e:
            raise NotPositiveDefinite(str(e)) from e
        rc = float(getattr(self._f, "rcond", -1.0))
        if rc >= 0.0:
            self.rcond_min = rc if self.rcond_min is None else min(self.rcond_min, rc)

    def solve(self, b) -> np.ndarray:
        from sksparse import cholmod as _cm
        b = np.asarray(b, dtype=float)
        # scikit-sparse warns "nearly singular" when rcond < n*eps; the bordered
        # eddy systems sit there by construction (cond2 3e11-1e13) and are
        # still solved to ~1e-13 of PARDISO.  Counted, not printed per solve.
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", _cm.CholmodWarning)
            try:
                x = self._f.solve(b)
            except _cm.CholmodNotPositiveDefiniteError as e:
                raise NotPositiveDefinite(str(e)) from e
        if w:
            self.near_singular_solves += 1
        return np.asarray(x)

    def free(self) -> None:
        self._f = None          # CholeskyFactor.__dealloc__ frees factor + common

    def stats(self) -> Dict[str, Any]:
        return {"rcond_min": self.rcond_min,
                "near_singular_solves": self.near_singular_solves}


class MumpsFactor(Factor):
    """MUMPS (sequential) through python-mumps.  ``spd=True``: SYM=1 (the
    positive-definite LDLᵀ, no pivoting, upper triangle); a negative pivot
    count (INFOG(12) > 0) is reported as :class:`NotPositiveDefinite`.
    ``spd=False``: SYM=0 unsymmetric LU with MUMPS' threshold pivoting.
    Ordering ``SB_MUMPS_ORDERING`` (default ``auto``: MUMPS picks)."""

    def __init__(self, spd: bool, ordering: Optional[str] = None) -> None:
        self.spd = bool(spd)
        self.kind = "cholesky" if self.spd else "lu"
        self.backend = "mumps-spd" if self.spd else "mumps-lu"
        self.ordering = ordering or os.environ.get("SB_MUMPS_ORDERING", "auto")
        self._ctx = None

    def _set(self, A) -> None:
        ctx = self._ctx
        if self.spd:
            # python-mumps builds its instance with SYM=2 (general symmetric);
            # the SPD code path needs SYM=1, which is fixed at construction:
            # give the Context an SYM=1 instance of the same dtype first.
            if ctx.mumps_instance is None:
                from mumps import _mumps
                ctx.mumps_instance = _mumps.dmumps(False, 1)   # (verbose, sym)
                ctx.dtype = "d"
            ctx.set_matrix(A, symmetric=True)          # keeps triu, COO
        else:
            ctx.set_matrix(A, symmetric=False)

    def analyze(self, A, sym=None) -> None:
        import mumps
        self._ctx = mumps.Context()
        self._set(A)
        self._ctx.analyze(ordering=self.ordering)

    def factorize(self, A, sym=None) -> None:
        import mumps
        self._set(A)
        try:
            self._ctx.factor(reuse_analysis=True)
        except mumps.MUMPSError as e:
            if self.spd:
                raise NotPositiveDefinite("MUMPS SYM=1: %s" % e) from e
            raise
        if self.spd and int(self._ctx.mumps_instance.infog[12]) > 0:
            raise NotPositiveDefinite(
                "MUMPS SYM=1 found %d negative pivot(s)"
                % int(self._ctx.mumps_instance.infog[12]))

    def solve(self, b) -> np.ndarray:
        b = np.asarray(b, dtype=float)
        return np.asarray(self._ctx.solve(b))

    def free(self) -> None:
        # Dropping the last reference runs the instance's __dealloc__, which
        # is MUMPS' JOB=-2 (calling JOB=-2 here as well would free twice).
        ctx, self._ctx = self._ctx, None
        if ctx is not None:
            ctx.mumps_instance = None

    def stats(self) -> Dict[str, Any]:
        if self._ctx is None or self._ctx.mumps_instance is None:
            return {}
        inf = self._ctx.mumps_instance.infog
        return {"infog_22_peak_MB": int(inf[22]), "infog_29_factor_entries": int(inf[29])}


class SuperLUFactor(Factor):
    """SciPy SuperLU: no reusable analysis in SciPy's API, so ``analyze`` is a
    no-op and every ``factorize`` re-orders.  The last resort."""

    backend = "superlu"
    kind = "lu"

    def __init__(self) -> None:
        self._lu = None

    def analyze(self, A, sym=None) -> None:
        self._lu = None

    def factorize(self, A, sym=None) -> None:
        from scipy.sparse.linalg import splu
        self._lu = splu(A.tocsc())

    def solve(self, b) -> np.ndarray:
        return self._lu.solve(np.asarray(b, dtype=float))

    def free(self) -> None:
        self._lu = None


def _pardiso_handle(mtype: int):
    """A new PyPardisoSolver, with the MKL runtime located once per process
    (PyPardisoSolver.__init__ otherwise globs sys.prefix: 12.5 s on Windows),
    registered with the active run scope so it is released on every exit."""
    import pypardiso
    if not os.environ.get("PYPARDISO_MKL_RT"):
        try:
            p = pypardiso.scipy_aliases.pypardiso_solver.libmkl._name
            if p:
                os.environ["PYPARDISO_MKL_RT"] = str(p)
        except Exception:   # noqa: BLE001 — older/rearranged pypardiso: pay the glob
            pass
    h = pypardiso.PyPardisoSolver(mtype=int(mtype))
    need = ("_call_pardiso", "_check_b", "set_phase", "iparm")
    if not all(hasattr(h, a) for a in need):
        raise RuntimeError("pypardiso %s lacks %s" % (
            getattr(pypardiso, "__version__", "?"),
            [a for a in need if not hasattr(h, a)]))
    from motor_ai_sim.simulation.pardiso_lifetime import own_pardiso_if_scoped
    return own_pardiso_if_scoped(h)


class PardisoFactor(Factor):
    """Intel MKL PARDISO (optional).  mtype 11 = unsymmetric LU with scaling
    and weighted matching, mtype 2 = Cholesky on the upper triangle.  Exactly
    the calls ``P2Nonlinear`` made before this module existed: phase 11 per
    pattern, phase 23 (numeric + solve) per solve, phase 22 / 33 when the
    factor is kept for several solves.  ``handle`` wraps an existing
    ``PyPardisoSolver`` (tests, legacy callers)."""

    def __init__(self, spd: bool, handle=None) -> None:
        self.spd = bool(spd)
        self.kind = "cholesky" if self.spd else "lu"
        self.backend = "pardiso-cholesky" if self.spd else "pardiso-lu"
        self._h = handle
        self._M = None
        self.perturbed = 0          # factorisations with perturbed pivots
        self.last_perturbed = 0

    @property
    def handle(self):
        return self._h

    def _handle(self):
        if self._h is None:
            self._h = _pardiso_handle(2 if self.spd else 11)
        return self._h

    def _prep(self, A, sym):
        """The matrix MKL sees: the upper triangle (Cholesky) or A (LU)."""
        if self.spd:
            if sym is None or not sym.ok:
                sym = sym_pattern(A.indptr, A.indices, A.shape[0])
            return _csr_matrix((A.data[sym.tri], sym.u_indices, sym.u_indptr),
                               shape=A.shape, copy=False)
        return A

    def _call(self, M, b, phase: int):
        s = self._handle()
        if self.spd:
            s.iparm[11] = 0                  # no transposed solve (symmetric)
        else:
            s._check_A(M)                    # CSC -> transposed flag; sorts
        s.set_phase(phase)
        return s._call_pardiso(M, b)

    def _count(self):
        n = int(self._h.iparm[13]) if self._h is not None else 0
        self.last_perturbed = n
        if n > 0:
            self.perturbed += 1

    def analyze(self, A, sym=None) -> None:
        M = self._prep(A, sym)
        self._call(M, np.zeros((M.shape[0], 1)), 11)

    def factorize(self, A, sym=None) -> None:
        M = self._prep(A, sym)
        self._call(M, np.zeros((M.shape[0], 1)), 22)
        self._M = M
        self._count()

    def solve(self, b) -> np.ndarray:
        s = self._handle()
        bb = s._check_b(self._M, b)
        return self._call(self._M, bb, 33)

    def factorize_solve(self, A, b, sym=None):
        M = self._prep(A, sym)
        s = self._handle()
        if not self.spd:
            s._check_A(M)
        bb = s._check_b(M, b)
        x = self._call(M, bb, 23)
        self._M = M
        self._count()
        return x

    def free(self) -> None:
        h, self._h = self._h, None
        self._M = None
        if h is not None:
            from motor_ai_sim.simulation.pardiso_lifetime import release_pardiso
            release_pardiso(h)


def make_factor(name: str, handle=None) -> Factor:
    if name == "cholmod":
        return CholmodFactor()
    if name == "mumps-spd":
        return MumpsFactor(spd=True)
    if name == "mumps-lu":
        return MumpsFactor(spd=False)
    if name == "pardiso-cholesky":
        return PardisoFactor(spd=True, handle=handle)
    if name == "pardiso-lu":
        return PardisoFactor(spd=False, handle=handle)
    if name == "superlu":
        return SuperLUFactor()
    raise ValueError("unknown factor %r" % name)


LIBRARY_OF = {"cholmod": "cholmod", "mumps-spd": "mumps", "mumps-lu": "mumps",
              "pardiso-cholesky": "pardiso", "pardiso-lu": "pardiso",
              "superlu": "superlu"}


# ════════════════════════════════════════════════════════════════════════
#  pattern-reusing stream
# ════════════════════════════════════════════════════════════════════════
class FactorStream:
    """A factor and the pattern it was analysed for.  ``factor(A)`` analyses
    only when the pattern moved (``indptr``/``indices`` compared: a ~0.2 ms
    memcmp against a 10-50 ms factorisation), then factorises the values.
    The pattern is CHECKED, not assumed: skfem's ``eliminate_zeros`` drops
    exactly-zero tangent blocks and the slip pairing changes every frame."""

    def __init__(self, factor: Factor, reuse: bool = True) -> None:
        self.f = factor
        self.reuse = bool(reuse)
        self._key = None
        self._indptr = None
        self._indices = None
        self.analyses = 0
        self.factorizations = 0
        self.solves = 0
        self.t_analyze = 0.0
        self.t_factor = 0.0
        self.t_solve = 0.0

    @property
    def name(self) -> str:
        return self.f.backend

    def _new_pattern(self, A) -> bool:
        key = (A.format, int(A.shape[0]), int(A.nnz))
        return not (self.reuse and self._key == key
                    and np.array_equal(self._indptr, A.indptr)
                    and np.array_equal(self._indices, A.indices))

    def _analyse(self, A, sym) -> None:
        t0 = time.perf_counter()
        self._key = None                       # invalid until analyze returns
        self.f.analyze(A, sym)
        self._key = (A.format, int(A.shape[0]), int(A.nnz))
        self._indptr = A.indptr.copy()
        self._indices = A.indices.copy()
        self.analyses += 1
        self.t_analyze += time.perf_counter() - t0

    def factor(self, A, sym: Optional[SymPattern] = None) -> bool:
        new = self._new_pattern(A)
        if new:
            self._analyse(A, sym)
        t0 = time.perf_counter()
        try:
            self.f.refactorize(A, sym)
        except BaseException:
            self._key = None                   # never reuse a failed state
            raise
        self.factorizations += 1
        self.t_factor += time.perf_counter() - t0
        return new

    def solve(self, b) -> np.ndarray:
        t0 = time.perf_counter()
        x = self.f.solve(b)
        self.solves += 1
        self.t_solve += time.perf_counter() - t0
        return x

    def factor_solve(self, A, b, sym: Optional[SymPattern] = None):
        if self._new_pattern(A):
            self._analyse(A, sym)
        t0 = time.perf_counter()
        try:
            x = self.f.factorize_solve(A, b, sym)
        except BaseException:
            self._key = None
            raise
        self.factorizations += 1
        self.solves += 1
        self.t_factor += time.perf_counter() - t0
        return x

    def free(self) -> None:
        self._key = self._indptr = self._indices = None
        self.f.free()


# ════════════════════════════════════════════════════════════════════════
#  the router one run owns
# ════════════════════════════════════════════════════════════════════════
class LinearSolver:
    """Backend selection, SPD checks, pattern reuse and the loud fallbacks
    for one run (or one TDM frame).  See the module docstring.

    Counters (the run result's ``linear_solver`` block): ``spd_solves``,
    ``spd_analyses``, ``spd_declined``, ``spd_failures``, ``lu_solves``,
    ``lu_analyses``, ``lu_failures``, ``lu_perturbed``; ``notes`` lists every
    fallback and substitution in words."""

    def __init__(self, backend: Optional[str] = None, *, log=None,
                 spd_min_mumps: Optional[int] = None,
                 reuse: Optional[bool] = None, spd: Optional[bool] = None,
                 prefer_parallel: bool = False,
                 pardiso_lu=None, pardiso_spd=None) -> None:
        self.log = log if log is not None else _log
        self._lock = threading.RLock()
        self.notes: List[str] = []
        self.requested = (backend or requested_backend()).strip().lower()
        if self.requested not in BACKENDS:
            self._note("warning", "unknown SB_LINEAR_BACKEND=%r; using auto"
                       % self.requested)
            self.requested = "auto"
        self.spd_min_mumps = (spd_mumps_min_dof() if spd_min_mumps is None
                              else int(spd_min_mumps))
        if reuse is None:
            reuse = not (os.environ.get("SB_LINEAR_NO_REUSE") == "1"
                         or os.environ.get("SB_NO_PARDISO_REUSE") == "1")
        self.reuse = bool(reuse)
        if spd is None:
            spd = (os.environ.get("SB_LINEAR_SPD", "1") != "0"
                   and os.environ.get("SB_PARDISO_SPD", "1") != "0")
        self._spd_enabled = bool(spd)
        self.prefer_parallel = bool(prefer_parallel)
        self._handles = {"pardiso-lu": pardiso_lu, "pardiso-cholesky": pardiso_spd}
        self._streams: Dict[str, FactorStream] = {}
        self._current: Optional[FactorStream] = None
        self._lu_dead: set = set()
        self._spd_pat: Optional[SymPattern] = None
        self.spd_solves = self.spd_analyses = self.spd_declined = 0
        self.spd_failures = 0
        self.lu_solves = self.lu_analyses = self.lu_failures = 0
        self.factorizations = 0
        self.solves = 0
        self.n = 0
        self.backend = self._resolve()

    # ── selection ─────────────────────────────────────────────────────────
    def _note(self, level: str, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)
            getattr(self.log, level)("linear solver: %s", msg)

    def _resolve(self) -> str:
        """The effective family: pardiso, open, cholmod, mumps or superlu."""
        req = self.requested
        legacy = any(h is not None for h in self._handles.values())
        if legacy:
            return "pardiso"
        if req == "auto":
            return "pardiso" if available("pardiso") else "open"
        if req == "pardiso" and not available("pardiso"):
            self._note("warning", "SB_LINEAR_BACKEND=pardiso but pypardiso/MKL "
                       "is not installed; using the open solvers "
                       "(CHOLMOD + MUMPS)")
            return "open"
        return req

    def _spd_name(self, n: int) -> Optional[str]:
        """Which factor takes an SPD system of n unknowns, or None (-> LU)."""
        fam = self.backend
        if fam == "pardiso":
            if self._handles.get("pardiso-lu") is not None \
                    and self._handles.get("pardiso-cholesky") is None:
                return None           # legacy caller gave an LU handle only
            return "pardiso-cholesky"
        if fam == "superlu":
            return None
        want = []
        if fam == "cholmod":
            want = ["cholmod", "mumps-spd"]
        elif fam == "mumps":
            want = ["mumps-spd", "cholmod"]
        else:                                  # open
            if self.prefer_parallel or n >= self.spd_min_mumps:
                want = ["mumps-spd", "cholmod"]
            else:
                want = ["cholmod", "mumps-spd"]
        for w in want:
            if available(LIBRARY_OF[w]):
                if w != want[0]:
                    self._note("warning", "%s is not installed; SPD systems "
                               "use %s" % (want[0], w))
                return w
        self._note("warning", "neither CHOLMOD nor MUMPS is installed; SPD "
                   "systems use the LU path")
        return None

    def _lu_name(self) -> str:
        fam = self.backend
        if fam == "pardiso" and "pardiso-lu" not in self._lu_dead:
            if self._handles.get("pardiso-lu") is not None or available("pardiso"):
                return "pardiso-lu"
        if fam in ("open", "cholmod", "mumps") and "mumps-lu" not in self._lu_dead:
            if available("mumps"):
                return "mumps-lu"
            self._note("warning", "MUMPS is not installed; LU systems use "
                       "SciPy SuperLU")
        return "superlu"

    def _stream(self, name: str) -> FactorStream:
        s = self._streams.get(name)
        if s is None:
            s = FactorStream(make_factor(name, self._handles.get(name)),
                             reuse=self.reuse)
            self._streams[name] = s
        return s

    def _drop(self, name: str) -> None:
        s = self._streams.pop(name, None)
        if s is not None:
            if self._current is s:
                self._current = None
            try:
                s.free()
            except Exception:                  # noqa: BLE001 — cleanup
                pass
        self._handles[name] = None

    # ── SPD ───────────────────────────────────────────────────────────────
    def _spd_accept(self, A) -> Optional[SymPattern]:
        """The checks; the pattern when A may go to Cholesky, else None."""
        key = (int(A.shape[0]), int(A.nnz))
        pat = self._spd_pat
        if not (pat is not None and pat.key == key
                and np.array_equal(pat.indptr, A.indptr)
                and np.array_equal(pat.indices, A.indices)):
            pat = sym_pattern(A.indptr, A.indices, A.shape[0])
            self._spd_pat = pat
        why = pat.why if not pat.ok else spd_value_check(A, pat)
        if why is not None:
            self.spd_declined += 1
            if self.spd_declined == 1:
                lvl = "info" if not pat.ok else "warning"
                getattr(self.log, lvl)(
                    "Cholesky declined (%s, n=%d) — LU for this matrix",
                    why, A.shape[0])
            return None
        return pat

    def _spd_failed(self, name: str, err: BaseException, n: int) -> None:
        self.spd_failures += 1
        lu = self._lu_name()
        self._note("warning", "%s Cholesky failed (%s, n=%d) on a matrix that "
                   "passed the symmetry checks — not positive definite? The "
                   "rest of this run uses the %s LU." % (name, err, n, lu))
        self._spd_enabled = False
        for k in [k for k in self._streams if make_kind(k) == "cholesky"]:
            self._drop(k)

    def _run_spd(self, A, b, keep: bool):
        name = self._spd_name(A.shape[0])
        if name is None:                       # no Cholesky backend: LU
            return None
        pat = self._spd_accept(A)
        if pat is None:
            return None
        s = self._stream(name)
        a0 = s.analyses
        try:
            if keep:
                s.factor(A, pat)
                x = None
            else:
                x = s.factor_solve(A, b, pat)
        except Exception as e:                 # noqa: BLE001 — loud, then LU
            self._spd_failed(name, e, A.shape[0])
            return None
        self.spd_analyses += s.analyses - a0
        self.factorizations += 1
        if keep:
            self._current = s
            return True
        self.spd_solves += 1
        self.solves += 1
        return x

    # ── LU ────────────────────────────────────────────────────────────────
    def _run_lu(self, A, b, keep: bool):
        while True:
            name = self._lu_name()
            s = self._stream(name)
            a0 = s.analyses
            try:
                if keep:
                    s.factor(A)
                    x = None
                else:
                    x = s.factor_solve(A, b)
            except Exception as e:             # noqa: BLE001 — loud, then SuperLU
                if name == "superlu":
                    raise
                self.lu_failures += 1
                self._note("warning", "%s LU failed (%s, n=%d); the rest of "
                           "this run uses SciPy SuperLU" % (name, e, A.shape[0]))
                self._lu_dead.add(name)
                self._drop(name)
                if name == "pardiso-lu":
                    self._drop("pardiso-cholesky")
                    self._spd_enabled = False
                continue
            self.lu_analyses += s.analyses - a0
            self.factorizations += 1
            if keep:
                self._current = s
                return True
            self.lu_solves += 1
            self.solves += 1
            return x

    # ── public ────────────────────────────────────────────────────────────
    def solve(self, A, rhs, spd: bool = False) -> np.ndarray:
        """Factorise A and solve A x = rhs (rhs 1-D or 2-D, x of the same
        rank).  ``spd=True``: Cholesky after the checks, LU otherwise."""
        with self._lock:
            A = canonical(A)
            if spd and self._spd_enabled:
                x = self._run_spd(A, rhs, keep=False)
                if x is not None:
                    return x
            return self._run_lu(A, rhs, keep=False)

    def factor(self, A, spd: bool = False) -> str:
        """Factorise A and keep it for :meth:`solve_factored`; returns the
        factor used (e.g. ``cholmod``, ``mumps-lu``)."""
        with self._lock:
            A = canonical(A)
            self._current = None
            if spd and self._spd_enabled:
                if self._run_spd(A, None, keep=True):
                    self.n = int(A.shape[0])
                    return self._current.name
            self._run_lu(A, None, keep=True)
            self.n = int(A.shape[0])
            return self._current.name

    def solve_factored(self, b) -> np.ndarray:
        with self._lock:
            s = self._current
            if s is None:
                raise RuntimeError("solve_factored() before factor()")
            x = s.solve(b)
            self.solves += 1
            if make_kind(s.name) == "cholesky":
                self.spd_solves += 1
            else:
                self.lu_solves += 1
            return x

    @property
    def lu_backend(self) -> str:
        return self._lu_name()

    @property
    def spd_backend(self) -> Optional[str]:
        if not self._spd_enabled:
            return None
        if self.backend == "open":
            return "cholmod<%d<=mumps-spd" % self.spd_min_mumps
        return self._spd_name(0)

    @property
    def lu_perturbed(self) -> int:
        s = self._streams.get("pardiso-lu")
        return int(s.f.perturbed) if s is not None else 0

    def factor_seconds(self) -> Dict[str, float]:
        return {k: round(s.t_analyze + s.t_factor + s.t_solve, 3)
                for k, s in self._streams.items()}

    def describe(self) -> Dict[str, Any]:
        """Provenance for the run result (no value depends on it beyond
        solver round-off)."""
        st = {}
        for k, s in self._streams.items():
            try:
                st[k] = s.f.stats()
            except Exception:                  # noqa: BLE001
                st[k] = {}
        return {"backend": self.backend, "requested": self.requested,
                "spd_backend": self.spd_backend, "lu_backend": self.lu_backend,
                "lu_solves": int(self.lu_solves),
                "lu_analyses": int(self.lu_analyses),
                "lu_failures": int(self.lu_failures),
                "cholesky_solves": int(self.spd_solves),
                "cholesky_analyses": int(self.spd_analyses),
                "cholesky_declined": int(self.spd_declined),
                "cholesky_failures": int(self.spd_failures),
                "perturbed_pivot_factorizations": self.lu_perturbed,
                "factor_stats": {k: v for k, v in st.items() if v},
                "notes": list(self.notes)}

    def free(self) -> None:
        with self._lock:
            for k in list(self._streams):
                self._drop(k)
            self._current = None

    close = free


def make_kind(name: str) -> str:
    return "cholesky" if name in ("cholmod", "mumps-spd", "pardiso-cholesky") else "lu"


def make_linear_solver(log=None, **kw) -> LinearSolver:
    """A run's solver, registered with the active native-resource scope
    (``pardiso_lifetime.pardiso_scope``) so it is freed on every exit."""
    ls = LinearSolver(log=log, **kw)
    from motor_ai_sim.simulation.pardiso_lifetime import own_pardiso_if_scoped
    own_pardiso_if_scoped(ls)
    return ls


# ════════════════════════════════════════════════════════════════════════
#  one-shot helpers for the 3-D and mechanical consumers
# ════════════════════════════════════════════════════════════════════════
def solver_label(ls: LinearSolver, used: str) -> str:
    return {"cholmod": "CHOLMOD(SuiteSparse)", "mumps-spd": "MUMPS(SYM=1)",
            "mumps-lu": "MUMPS(LU)", "pardiso-cholesky": "pypardiso(MKL PARDISO)",
            "pardiso-lu": "pypardiso(MKL PARDISO)",
            "superlu": "scipy.splu(SuperLU)"}.get(used, used)


def factorize(A, spd: bool = False, log=None) -> "OneShot":
    """Factorise one matrix for several solves (3-D preconditioners, the
    Stage-A condensed systems).  ``.solve(b)``, ``.name``, ``.free()``."""
    ls = LinearSolver(log=log)
    used = ls.factor(A, spd=spd)
    return OneShot(ls, used)


class OneShot:
    def __init__(self, ls: LinearSolver, used: str) -> None:
        self.ls = ls
        self.used = used
        self.name = solver_label(ls, used)

    def solve(self, b) -> np.ndarray:
        return np.asarray(self.ls.solve_factored(np.asarray(b, dtype=float)))

    __call__ = solve

    def free(self) -> None:
        self.ls.free()


def solve_once(A, b, spd: bool = False, log=None, retry_nonfinite: bool = True):
    """One factorise + solve with a solver of its own: no state is shared
    between callers or threads, so no global lock is needed (pypardiso's
    module-level ``spsolve`` needed one).  A non-finite answer is retried once
    on a factorisation built from scratch, which separates a transient native
    failure from a singular matrix; a second non-finite answer is returned
    for the caller to reject.  Returns ``(x, label)``."""
    f = factorize(A, spd=spd, log=log)
    try:
        x = np.asarray(f.solve(b))
    finally:
        f.free()
    if retry_nonfinite and not np.all(np.isfinite(x)):
        (log or _log).warning("linear solver: %s returned non-finite entries "
                              "(n=%d); retrying on a fresh factorisation",
                              f.name, A.shape[0])
        f = factorize(A, spd=spd, log=log)
        try:
            x = np.asarray(f.solve(b))
        finally:
            f.free()
    return x, f.name


def default_label(spd: bool) -> str:
    """The label a solve would carry before any fallback (for callers that
    report the solver name up front)."""
    ls = LinearSolver()
    name = ls._spd_name(0) if spd else None
    return solver_label(ls, name or ls._lu_name())


# ════════════════════════════════════════════════════════════════════════
#  TDM: one factor per frame (drop-in for time_periodic.FrameFactor)
# ════════════════════════════════════════════════════════════════════════
class FrameFactor:
    """One frame's bordered Jacobian, factorised once and back-solved many
    times (TDM prototype, PR #87).  Same public surface as the PARDISO-only
    ``time_periodic.FrameFactor`` it replaces: ``factor(A, threads)``,
    ``solve(b, threads)``, ``close()``, ``n``, ``analyses``,
    ``factorizations``, ``solves``, ``lu_used``, ``lib``.

    Cholesky after the SPD checks, LU when declined or when Cholesky fails
    (then LU for this frame from on); the symbolic analysis is reused while
    the pattern holds.  ``threads`` is accepted for compatibility and
    ignored: OpenBLAS has no per-thread count, and CHOLMOD/MUMPS gain little
    from BLAS threads at these sizes (docs/OPEN_SOLVERS_2026-09-30.md).
    ``prefer_parallel`` (several frames factorised from a thread pool):
    MUMPS SYM=1 for the SPD frames of the open build, because CHOLMOD holds
    the GIL.  One object per frame; each serialises its own calls."""

    def __init__(self, own: Optional[Callable] = None,
                 release: Optional[Callable] = None, *,
                 backend: Optional[str] = None,
                 prefer_parallel: bool = False, log=None) -> None:
        self._own = own
        self._release = release
        self._ls = LinearSolver(backend, log=log, prefer_parallel=prefer_parallel)
        self.n = 0
        self.used: Optional[str] = None

    @property
    def lib(self):
        return None                  # no MKL thread control through this class

    @property
    def analyses(self) -> int:
        return self._ls.spd_analyses + self._ls.lu_analyses

    @property
    def factorizations(self) -> int:
        return self._ls.factorizations

    @property
    def solves(self) -> int:
        return self._ls.solves

    @property
    def lu_used(self) -> bool:
        return bool(self._ls.spd_declined or self._ls.spd_failures
                    or (self.used is not None and make_kind(self.used) == "lu"))

    @property
    def backend(self) -> str:
        return self._ls.backend

    def factor(self, A, mkl_threads: Optional[int] = None) -> None:
        A = A.tocsr() if getattr(A, "format", None) != "csr" else A
        self.used = self._ls.factor(A, spd=True)
        self.n = int(A.shape[0])

    def solve(self, b: np.ndarray, mkl_threads: Optional[int] = None) -> np.ndarray:
        return self._ls.solve_factored(np.asfortranarray(np.asarray(b, float)))

    def close(self) -> None:
        self._ls.free()
        self.used = None
