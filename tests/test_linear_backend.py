"""The one linear-solver interface (simulation/linear_backend.py).

* backend selection: auto = PARDISO when installed, else CHOLMOD + MUMPS;
  SB_LINEAR_BACKEND; a missing library degrades once, loudly;
* fallbacks: Cholesky failure -> LU for the rest of the run, LU failure ->
  SuperLU, each with a warning and a result note;
* refactorisation reuse: analysis once per sparsity pattern, numeric
  factorisation per call, re-analysis on any index change;
* the real backends (skipped when not installed) agree with SuperLU on SPD
  and unsymmetric systems, reuse their analysis, detect indefiniteness, and
  are safe from several threads;
* the TDM prototype's FrameFactor surface.

Exported production systems (SB_TEST_MATRICES=<dir of *_A.npz/*_bx.npz>, the
Stage-1 profiling export) are checked against the PARDISO solution stored with
them when that directory is given.
"""
from __future__ import annotations

import glob
import json
import os
import threading

import numpy as np
import pytest
import scipy.sparse as sp
from scipy.sparse.linalg import splu

from motor_ai_sim.simulation import linear_backend as LB
from motor_ai_sim.simulation.linear_backend import (
    FrameFactor, LinearSolver, NotPositiveDefinite)


# ── helpers ──────────────────────────────────────────────────────────────
class _Log:
    def __init__(self):
        self.lines = []

    def _add(self, lvl, *a, **k):
        self.lines.append((lvl, a[0] % a[1:] if len(a) > 1 else a[0]))

    def warning(self, *a, **k):
        self._add("warning", *a)

    def info(self, *a, **k):
        self._add("info", *a)

    def debug(self, *a, **k):
        pass

    def warned(self, text):
        return any(lvl == "warning" and text in m for lvl, m in self.lines)


def _laplace2d(m, shift=0.0, seed=0):
    """SPD 5-point Laplacian on an m x m grid (+ a random positive diagonal)."""
    n = m * m
    e = np.ones(n)
    T = sp.diags([-e[:-1], 4 * e, -e[:-1]], [-1, 0, 1], shape=(n, n)).tolil()
    for k in range(1, m):
        T[k * m - 1, k * m] = 0.0
        T[k * m, k * m - 1] = 0.0
    A = (T + sp.diags([-e[:-m], -e[:-m]], [-m, m], shape=(n, n))).tocsr()
    rng = np.random.default_rng(seed)
    A = (A + sp.diags(shift + 0.1 * rng.random(n))).tocsr()
    A.eliminate_zeros()
    A.sort_indices()
    return A


def _unsym(A, seed=1):
    """Same pattern, unsymmetric values (still well conditioned)."""
    B = A.tocsr().copy()
    rng = np.random.default_rng(seed)
    rows = np.repeat(np.arange(B.shape[0]), np.diff(B.indptr))
    off = B.indices != rows
    B.data[off] *= 1.0 + 0.3 * rng.random(int(off.sum()))
    return B


def _indefinite(A):
    """Symmetric, positive diagonal, but indefinite (a saddle point)."""
    n = A.shape[0]
    c = sp.csr_matrix(np.eye(n, 1) * 10.0 * abs(A).max())
    return sp.bmat([[A, c], [c.T, sp.eye(1) * 1e-12]]).tocsr()


def _have(lib):
    return LB.available(lib)


REAL = [b for b, lib in (("cholmod", "cholmod"), ("mumps", "mumps"),
                         ("pardiso", "pardiso"), ("superlu", "superlu"))]


def _need(backend):
    lib = {"cholmod": "cholmod", "mumps": "mumps", "pardiso": "pardiso",
           "superlu": "superlu", "open": "cholmod"}[backend]
    if not _have(lib) or (backend in ("cholmod", "open") and not _have("mumps")):
        pytest.skip("%s not installed" % backend)


@pytest.fixture
def libs(monkeypatch):
    """Pretend exactly these libraries are installed."""
    state = {"cholmod": True, "mumps": True, "pardiso": False, "superlu": True}

    def available(name):
        if name == "pardiso" and os.environ.get("SB_NO_PARDISO") == "1":
            return False
        return state[name]
    monkeypatch.setattr(LB, "available", available)
    monkeypatch.delenv("SB_LINEAR_BACKEND", raising=False)
    monkeypatch.delenv("SB_NO_PARDISO", raising=False)
    return state


# ── selection ────────────────────────────────────────────────────────────
class TestSelection:
    def test_auto_prefers_pardiso_when_installed(self, libs):
        libs["pardiso"] = True
        ls = LinearSolver(log=_Log())
        assert ls.backend == "pardiso"
        assert ls.lu_backend == "pardiso-lu"
        assert ls._spd_name(10) == "pardiso-cholesky"

    def test_auto_without_mkl_is_the_open_build(self, libs):
        ls = LinearSolver(log=_Log())
        assert ls.backend == "open" and not ls.notes
        assert ls.lu_backend == "mumps-lu"
        # calibrated: MUMPS SYM=1 at every size (switch point 0)
        assert ls.spd_min_mumps == 0
        assert ls._spd_name(1) == "mumps-spd" and ls.spd_backend == "mumps-spd"

    def test_env_selects_and_no_pardiso_switch_is_honoured(self, libs, monkeypatch):
        libs["pardiso"] = True
        monkeypatch.setenv("SB_LINEAR_BACKEND", "cholmod")
        ls = LinearSolver(log=_Log())
        assert ls.backend == "cholmod"
        assert ls._spd_name(10 ** 7) == "cholmod"       # no DOF switch
        assert ls.lu_backend == "mumps-lu"
        monkeypatch.setenv("SB_LINEAR_BACKEND", "mumps")
        assert LinearSolver(log=_Log())._spd_name(10) == "mumps-spd"
        monkeypatch.setenv("SB_LINEAR_BACKEND", "auto")
        monkeypatch.setenv("SB_NO_PARDISO", "1")
        assert LinearSolver(log=_Log()).backend == "open"

    def test_pardiso_requested_but_missing_is_loud(self, libs, monkeypatch):
        monkeypatch.setenv("SB_LINEAR_BACKEND", "pardiso")
        log = _Log()
        ls = LinearSolver(log=log)
        assert ls.backend == "open"
        assert log.warned("pypardiso/MKL is not installed")
        assert ls.describe()["notes"]

    def test_unknown_value_falls_back_to_auto(self, libs, monkeypatch):
        monkeypatch.setenv("SB_LINEAR_BACKEND", "magic")
        ls = LinearSolver(log=_Log())
        assert ls.requested == "auto" and ls.backend == "open"

    def test_threshold_from_env(self, libs, monkeypatch):
        monkeypatch.setenv("SB_SPD_MUMPS_MIN_DOF", "1000")
        ls = LinearSolver(log=_Log())
        assert ls._spd_name(999) == "cholmod" and ls._spd_name(1000) == "mumps-spd"

    def test_missing_libraries_degrade_once_with_a_note(self, libs):
        libs["mumps"] = False
        log = _Log()
        ls = LinearSolver(log=log)
        assert ls._spd_name(10) == "cholmod"
        assert log.warned("mumps-spd is not installed")
        assert ls.lu_backend == "superlu" and log.warned("MUMPS is not installed")
        libs["mumps"] = True
        libs["cholmod"] = False
        ls = LinearSolver("cholmod", log=log)
        assert ls._spd_name(10) == "mumps-spd"
        assert log.warned("cholmod is not installed")
        libs["mumps"] = False
        ls = LinearSolver(log=log)
        assert ls._spd_name(10) is None and ls.lu_backend == "superlu"
        assert log.warned("neither CHOLMOD nor MUMPS")

    def test_runtime_hint_only_for_a_pardiso_run(self):
        assert LB.pardiso_selected({})
        assert LB.pardiso_selected({"SB_LINEAR_BACKEND": "pardiso"})
        for v in ("cholmod", "mumps", "open", "superlu"):
            assert not LB.pardiso_selected({"SB_LINEAR_BACKEND": v})
        assert not LB.pardiso_selected({"SB_NO_PARDISO": "1"})


# ── fallbacks and reuse, with fake factors ──────────────────────────────
class _Fake(LB.Factor):
    """Dense solve; records the calls; can be told to fail."""

    registry: list = []

    def __init__(self, name, fail=None):
        self.backend = name
        self.kind = LB.make_kind(name)
        self.fail = fail
        self.calls = []
        self._A = None
        _Fake.registry.append(self)

    def analyze(self, A, sym=None):
        self.calls.append("analyze")
        if self.fail == "analyze":
            raise RuntimeError("fake analyze failure")

    def factorize(self, A, sym=None):
        self.calls.append("factorize")
        if self.fail == "factorize":
            raise (NotPositiveDefinite("fake: not PD") if self.kind == "cholesky"
                   else RuntimeError("fake LU failure"))
        self._A = A.toarray()

    def solve(self, b):
        self.calls.append("solve")
        return np.linalg.solve(self._A, b)

    def free(self):
        self.calls.append("free")


@pytest.fixture
def fakes(libs, monkeypatch):
    fail = {}
    _Fake.registry = []

    def make(name, handle=None):
        return _Fake(name, fail.get(name))
    monkeypatch.setattr(LB, "make_factor", make)
    return fail


class TestFallbacks:
    def test_cholesky_failure_goes_to_lu_for_the_rest_of_the_run(self, fakes):
        fakes["cholmod"] = "factorize"
        log = _Log()
        ls = LinearSolver("cholmod", log=log)
        A = _laplace2d(6)
        b = np.ones(A.shape[0])
        x = ls.solve(A, b, spd=True)
        assert np.allclose(A @ x, b)
        assert ls.spd_failures == 1 and ls.lu_solves == 1
        assert log.warned("cholmod Cholesky failed") and log.warned("mumps-lu LU")
        assert any("Cholesky failed" in n for n in ls.describe()["notes"])
        ls.solve(A * 2.0, b, spd=True)                 # no second attempt
        assert ls.spd_failures == 1 and ls.lu_solves == 2
        chol = [f for f in _Fake.registry if f.backend == "cholmod"]
        assert len(chol) == 1 and chol[0].calls[-1] == "free"

    def test_lu_failure_goes_to_superlu(self, fakes):
        fakes["mumps-lu"] = "factorize"
        log = _Log()
        ls = LinearSolver(log=log)
        A = _unsym(_laplace2d(5))
        b = np.arange(A.shape[0], dtype=float)
        x = ls.solve(A, b)
        assert np.allclose(A @ x, b)
        assert ls.lu_failures == 1 and ls.lu_backend == "superlu"
        assert log.warned("SuperLU")

    def test_asymmetric_input_is_declined_not_failed(self, fakes):
        ls = LinearSolver(log=_Log())
        A = _unsym(_laplace2d(5))
        x = ls.solve(A, np.ones(A.shape[0]), spd=True)
        assert np.allclose(A @ x, 1.0)
        assert ls.spd_declined == 1 and ls.spd_failures == 0 and ls.spd_solves == 0

    def test_spd_switch_off_keeps_everything_on_lu(self, fakes, monkeypatch):
        monkeypatch.setenv("SB_LINEAR_SPD", "0")
        ls = LinearSolver(log=_Log())
        A = _laplace2d(5)
        ls.solve(A, np.ones(A.shape[0]), spd=True)
        assert ls.spd_solves == 0 and ls.lu_solves == 1


class TestReuse:
    def test_analysis_once_per_pattern(self, fakes):
        ls = LinearSolver("cholmod", log=_Log())
        A = _laplace2d(6)
        b = np.ones(A.shape[0])
        for k in range(4):
            ls.solve(A * (1.0 + k), b, spd=True)
        f = [f for f in _Fake.registry if f.backend == "cholmod"][0]
        assert f.calls.count("analyze") == 1 and f.calls.count("factorize") == 4
        assert ls.spd_analyses == 1 and ls.spd_solves == 4

    def test_changed_indices_same_nnz_reanalyse(self, fakes):
        ls = LinearSolver(log=_Log())
        A = _laplace2d(6).tolil()
        n = A.shape[0]
        b = np.ones(n)
        ls.solve(A.tocsr(), b, spd=True)
        B = A.copy()
        B[0, 1] = B[1, 0] = 0.0
        B[0, n - 1] = B[n - 1, 0] = -0.5
        B = B.tocsr()
        B.eliminate_zeros()
        assert B.nnz == A.tocsr().nnz
        x = ls.solve(B, b, spd=True)
        assert ls.spd_analyses == 2 and np.allclose(B @ x, b)

    def test_no_reuse_switch(self, fakes):
        ls = LinearSolver(log=_Log(), reuse=False)
        A = _laplace2d(4)
        for _ in range(3):
            ls.solve(A, np.ones(A.shape[0]), spd=True)
        assert ls.spd_analyses == 3

    def test_factor_once_solve_many(self, fakes):
        ls = LinearSolver("cholmod", log=_Log())
        A = _laplace2d(5)
        assert ls.factor(A, spd=True) == "cholmod"
        B = np.random.default_rng(0).standard_normal((A.shape[0], 3))
        for j in range(3):
            assert np.allclose(A @ ls.solve_factored(B[:, j]), B[:, j])
        assert ls.spd_solves == 3 and ls.factorizations == 1

    def test_failed_factorisation_is_never_reused(self, fakes):
        fakes["mumps-lu"] = "analyze"
        ls = LinearSolver(log=_Log())
        A = _unsym(_laplace2d(4))
        ls.solve(A, np.ones(A.shape[0]))
        assert ls.lu_backend == "superlu"


# ── the real libraries ──────────────────────────────────────────────────
@pytest.mark.parametrize("backend", ["cholmod", "mumps", "open", "pardiso", "superlu"])
class TestRealBackends:
    def test_spd_and_lu_agree_with_superlu(self, backend):
        _need(backend)
        ls = LinearSolver(backend, log=_Log())
        A = _laplace2d(30)
        U = _unsym(A)
        rng = np.random.default_rng(3)
        B = rng.standard_normal((A.shape[0], 2))
        for M, spd in ((A, True), (U, False), (A.tocsc(), True), (U.tocsc(), False)):
            X = ls.solve(M, B, spd=spd)
            ref = splu(M.tocsc()).solve(B)
            assert X.shape == B.shape
            assert np.max(np.abs(X - ref)) <= 1e-11 * np.max(np.abs(ref)), (backend, spd)
            x1 = ls.solve(M, B[:, 0], spd=spd)
            assert x1.ndim == 1
        if backend != "superlu":
            assert ls.spd_solves >= 1 or backend == "superlu"

    def test_refactorisation_reuse_is_exact(self, backend):
        _need(backend)
        ls = LinearSolver(backend, log=_Log())
        A = _laplace2d(25)
        b = np.ones(A.shape[0])
        for k in range(3):
            M = (A + sp.diags(np.full(A.shape[0], 0.5 * k))).tocsr()
            x = ls.solve(M, b, spd=True)
            assert np.linalg.norm(M @ x - b) <= 1e-11 * np.linalg.norm(b)
        if backend != "superlu":
            assert ls.spd_analyses == 1 and ls.spd_solves == 3

    def test_indefinite_matrix_falls_back_loudly(self, backend):
        _need(backend)
        if backend == "superlu":
            pytest.skip("no Cholesky in this backend")
        log = _Log()
        ls = LinearSolver(backend, log=log)
        M = _indefinite(_laplace2d(8))
        rhs = np.ones(M.shape[0])
        x = ls.solve(M, rhs, spd=True)
        assert np.linalg.norm(M @ x - rhs) <= 1e-8 * np.linalg.norm(rhs)
        assert ls.spd_failures == 1 and log.warned("Cholesky failed")
        assert ls.describe()["cholesky_failures"] == 1

    def test_distinct_solvers_are_thread_safe(self, backend):
        """The API runs jobs in threads.  Sequential MUMPS segfaults on two
        concurrent instances (measured); linear_backend serialises it."""
        _need(backend)
        mats = [_laplace2d(28, seed=k) for k in range(6)]
        b = np.ones(mats[0].shape[0])
        serial = [LinearSolver(backend).solve(M, b, spd=True) for M in mats]
        out = [None] * len(mats)
        errors = []

        def work(k):
            try:
                ls = LinearSolver(backend)
                for _ in range(3):
                    out[k] = ls.solve(mats[k] * 1.0, b, spd=True)
                ls.free()
            except BaseException as e:          # noqa: BLE001
                errors.append(e)
        ts = [threading.Thread(target=work, args=(k,)) for k in range(len(mats))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=120)
        assert not errors, errors
        for k in range(len(mats)):
            assert np.max(np.abs(out[k] - serial[k])) <= 1e-12 * np.max(np.abs(serial[k]))


def test_p2nonlinear_uses_the_solver_it_is_given():
    from motor_ai_sim.simulation.p2_nonlinear import P2Nonlinear
    ls = LinearSolver("superlu")
    p = P2Nonlinear(basis=None, n_dof=4, K_const=None, sat=[], sat_sub=[],
                    log=_Log(), linear=ls)
    A = _laplace2d(4)
    x = p.solve_ff(A, np.ones(A.shape[0]), spd=True)
    assert np.allclose(A @ x, 1.0)
    assert p.linear is ls and p.pardiso_solves == 1


# ── TDM FrameFactor ──────────────────────────────────────────────────────
class TestFrameFactor:
    SURFACE = ("factor", "solve", "close", "n", "analyses", "factorizations",
               "solves", "lu_used", "lib")

    def test_surface_matches_the_tdm_prototype(self):
        f = FrameFactor(own=None, release=None)
        for a in self.SURFACE:
            assert hasattr(f, a), a
        assert f.lib is None
        try:                                   # the TDM branch, when present
            from motor_ai_sim.simulation import time_periodic as tp
        except ImportError:
            return
        for a in self.SURFACE:
            assert hasattr(tp.FrameFactor, a) or a in ("n", "analyses",
                                                       "factorizations",
                                                       "solves", "lu_used"), a

    @pytest.mark.parametrize("backend", ["cholmod", "mumps", "open", "pardiso",
                                         "superlu"])
    def test_factor_once_back_solve_many(self, backend):
        _need(backend)
        f = FrameFactor(backend=backend)
        A = _laplace2d(20)
        f.factor(A, 4)
        assert f.n == A.shape[0] and f.factorizations == 1
        rng = np.random.default_rng(5)
        for _ in range(4):
            b = rng.standard_normal(A.shape[0])
            assert np.allclose(A @ f.solve(b, 0), b, rtol=1e-10, atol=1e-10)
        assert f.solves == 4
        assert f.lu_used == (backend == "superlu")
        f.factor((A * 3.0).tocsr(), None)              # same pattern
        if backend != "superlu":
            assert f.analyses == 1
        f.factor(_unsym(A), None)                      # declined -> LU
        assert f.lu_used
        b = np.ones(A.shape[0])
        assert np.allclose(_unsym(A) @ f.solve(b), b, rtol=1e-10, atol=1e-10)
        f.close()
        f.close()                                      # idempotent

    @pytest.mark.parametrize("backend", ["cholmod", "mumps", "open", "pardiso"])
    def test_frames_from_a_thread_pool(self, backend):
        _need(backend)
        from concurrent.futures import ThreadPoolExecutor
        mats = [_laplace2d(24, seed=k) for k in range(8)]
        facs = [FrameFactor(backend=backend) for _ in mats]
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda k: facs[k].factor(mats[k], 1), range(len(mats))))
        b = np.ones(mats[0].shape[0])
        for M, f in zip(mats, facs):
            assert np.allclose(M @ f.solve(b), b, rtol=1e-10, atol=1e-10)
            f.close()


# ── exported production systems (optional) ──────────────────────────────
_MDIR = os.environ.get("SB_TEST_MATRICES", "")


@pytest.mark.skipif(not _MDIR, reason="SB_TEST_MATRICES not set")
@pytest.mark.parametrize("backend", ["cholmod", "mumps", "open"])
def test_exported_systems_match_pardiso(backend):
    _need(backend)
    metas = sorted(glob.glob(os.path.join(_MDIR, "*_s1_*_meta.json")))
    assert metas
    for mp in metas:
        tag = mp[:-len("_meta.json")]
        A = sp.load_npz(tag + "_A.npz").tocsr()
        if A.shape[0] > 60000:
            continue
        bx = np.load(tag + "_bx.npz")
        meta = json.load(open(mp))
        spd = meta.get("sym_rel", 1.0) < 1e-12
        ls = LinearSolver(backend)
        x = ls.solve(A, bx["b"], spd=spd)
        rel = np.max(np.abs(x - bx["x"])) / max(np.max(np.abs(bx["x"])), 1e-300)
        assert rel < 1e-8, (os.path.basename(tag), rel)


def test_mumps_ordering_policy(monkeypatch):
    """AMD for LU and small SPD, AMF for large SPD (calibrated); the env wins."""
    monkeypatch.delenv("SB_MUMPS_ORDERING", raising=False)
    assert LB.MumpsFactor(spd=True)._ordering(40_000) == "amd"
    assert LB.MumpsFactor(spd=True)._ordering(LB.MUMPS_AMF_MIN_DOF) == "amf"
    assert LB.MumpsFactor(spd=False)._ordering(10 ** 6) == "amd"
    monkeypatch.setenv("SB_MUMPS_ORDERING", "scotch")
    assert LB.MumpsFactor(spd=True)._ordering(10) == "scotch"
