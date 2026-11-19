"""Linear-solver backends for the Stage-2 GPU benchmark (benchmark code only).

Every backend has the same shape as the production reuse pattern in
``P2Nonlinear.solve_ff``: analyse once per sparsity pattern, then numeric
factorisation + solve for each new set of values with that pattern.

    be = make_backend("cudss_fp64")
    be.analyze(A)          # symbolic phase (reordering), pattern only
    be.factorize(A)        # numeric phase, same pattern, new values
    x = be.solve(b)        # host numpy in, host numpy out (FP64)
    be.close()

``solve_reuse(A, b)`` does the pattern check itself (what the solver does).

Backends
  pardiso        MKL PARDISO via pypardiso, mtype 11, phases 11/22/33 (CPU reference)
  superlu        SciPy SuperLU (the CPU fallback path)
  cudss_fp64     NVIDIA cuDSS (C API via ctypes on CuPy arrays), FP64 factor + solve
                 ("nvmath_cudss_*" = the same through nvmath-python, where it imports)
  cudss_fp32     cuDSS FP32 factor + solve, no refinement (accuracy probe only)
  cudss_mixed    cuDSS FP32 factor, FP64 residuals, iterative refinement to --ir-tol
  cudss_fp64_sym cuDSS FP64 with the matrix declared SYMMETRIC (LDL^T); valid only
                 when the dumped matrix reports sym_rel ~ 0
  cudss_fp64_spd cuDSS FP64 Cholesky (matrix declared SPD); every exported system
                 is SPD (analyze_matrices.py), mirrors PARDISO mtype 2
  cudss_fp32_spd, cudss_mixed_spd   SPD variants of the FP32 / mixed probes
  cupy_qr        cupyx.scipy.sparse.linalg.spsolve (cuSOLVER QR), no reuse
  cupy_gmres_jac cupyx GMRES + Jacobi preconditioner (expected to struggle)
  amgx           pyamgx FGMRES + AMG (only if pyamgx + AmgX are installed)

The NVIDIA libraries are proprietary and optional: nothing here is imported
unless that backend is requested, and nothing is bundled.
"""
from __future__ import annotations

import time

import numpy as np
import scipy.sparse as sp


def _sync():
    try:
        import cupy as cp
        cp.cuda.get_current_stream().synchronize()
    except Exception:
        pass


def gpu_mem_used():
    try:
        import cupy as cp
        free, total = cp.cuda.runtime.memGetInfo()
        return int(total - free)
    except Exception:
        return None


class Backend:
    name = "base"
    device = "cpu"

    def __init__(self):
        self._pat = None
        self.t = dict(h2d=0.0, analyze=0.0, factorize=0.0, solve=0.0, d2h=0.0)
        self.n_analyze = 0
        self.n_factorize = 0
        self.info = {}

    def solve_reuse(self, A, b):
        A = sp.csr_matrix(A)
        A.sort_indices()
        key = (A.shape[0], A.nnz)
        p = self._pat
        if not (p is not None and p[0] == key and np.array_equal(p[1], A.indptr)
                and np.array_equal(p[2], A.indices)):
            self.analyze(A)
            self._pat = (key, A.indptr.copy(), A.indices.copy())
        self.factorize(A)
        return self.solve(b)

    def close(self):
        pass


# ----------------------------------------------------------------------- CPU
class Pardiso(Backend):
    """mtype 11 = what production uses (real unsymmetric LU).  mtype 2 (SPD
    Cholesky) and -2 (symmetric indefinite LDL^T) are CPU alternatives: MKL
    then reads the UPPER triangle only, which pypardiso does not extract."""
    name = "pardiso"

    def __init__(self, mtype=11):
        super().__init__()
        import pypardiso
        self.s = pypardiso.PyPardisoSolver(mtype=mtype)
        self.mtype = mtype
        self.name = {11: "pardiso", 2: "pardiso_spd", -2: "pardiso_sym"}.get(mtype, f"pardiso_{mtype}")
        self.A = None

    def _prep(self, A):
        A = sp.csr_matrix(A)
        if self.mtype in (2, -2):
            A = sp.triu(A, format="csr")
        A.sort_indices()
        return A

    def analyze(self, A):
        A = self._prep(A)
        t0 = time.perf_counter()
        self.s._check_A(A)
        self.s.set_phase(11)
        self.s._call_pardiso(A, np.zeros((A.shape[0], 1)))
        self.t["analyze"] += time.perf_counter() - t0
        self.n_analyze += 1

    def factorize(self, A):
        A = self._prep(A)
        t0 = time.perf_counter()
        self.s._check_A(A)
        self.s.set_phase(22)
        self.s._call_pardiso(A, np.zeros((A.shape[0], 1)))
        self.A = A
        self.t["factorize"] += time.perf_counter() - t0
        self.n_factorize += 1
        self.info = dict(peak_kb=int(self.s.iparm[14]), fac_kb=int(self.s.iparm[16]),
                         nnz_lu=int(self.s.iparm[17]), mflop=int(self.s.iparm[18]),
                         perturbed=int(self.s.iparm[13]))

    def solve(self, b):
        t0 = time.perf_counter()
        bb = self.s._check_b(self.A, b)
        self.s.set_phase(33)
        x = self.s._call_pardiso(self.A, bb)
        self.t["solve"] += time.perf_counter() - t0
        return x

    def close(self):
        try:
            self.s.free_memory(everything=True)
        except Exception:
            pass


class SuperLU(Backend):
    name = "superlu"

    def analyze(self, A):
        pass

    def factorize(self, A):
        from scipy.sparse.linalg import splu
        t0 = time.perf_counter()
        self.lu = splu(sp.csc_matrix(A))
        self.t["factorize"] += time.perf_counter() - t0
        self.n_factorize += 1

    def solve(self, b):
        t0 = time.perf_counter()
        x = self.lu.solve(np.asarray(b, float))
        self.t["solve"] += time.perf_counter() - t0
        return x


# ---------------------------------------------------------------------- cuDSS
class CuDSS(Backend):
    """nvmath-python DirectSolver.  The CSR arrays stay on the device; a new
    set of values with the same pattern is copied into ``a.data`` in place and
    re-factorised without re-planning (nvmath example05_reset_operands)."""
    device = "gpu"

    def __init__(self, dtype="float64", symmetric=False, mixed=False,
                 ir_tol=1e-12, ir_max=20):
        super().__init__()
        import cupy as cp
        import cupyx.scipy.sparse as csp
        import nvmath  # noqa: F401  (import error = backend unavailable)
        self.cp, self.csp = cp, csp
        self.dtype = np.float32 if (dtype == "float32" or mixed) else np.float64
        self.symmetric = symmetric
        self.mixed = mixed
        self.ir_tol, self.ir_max = ir_tol, ir_max
        self.name = ("cudss_mixed" if mixed else
                     f"cudss_{'fp32' if self.dtype == np.float32 else 'fp64'}"
                     + ("_spd" if symmetric == "spd" else "_sym" if symmetric else ""))
        self.solver = None
        self.a = None          # device CSR in factor precision
        self.a64 = None        # device CSR in FP64 for refinement residuals
        self.b = None
        self.ir_iters = []

    def _opts(self):
        import nvmath
        adv = nvmath.sparse.advanced
        mt = adv.DirectSolverMatrixType.SYMMETRIC if self.symmetric \
            else adv.DirectSolverMatrixType.GENERAL
        return adv.DirectSolverOptions(sparse_system_type=mt)

    def analyze(self, A):
        import nvmath
        cp, csp = self.cp, self.csp
        if self.solver is not None:
            self.solver.free()
        t0 = time.perf_counter()
        Ah = sp.csr_matrix(A)
        # full matrix is passed; nvmath/cuDSS default view is FULL
        self.a = csp.csr_matrix(Ah.astype(self.dtype))
        if self.mixed:
            self.a64 = csp.csr_matrix(sp.csr_matrix(A).astype(np.float64))
        self.b = cp.zeros(A.shape[0], dtype=self.dtype)
        _sync()
        self.t["h2d"] += time.perf_counter() - t0
        t0 = time.perf_counter()
        self.solver = nvmath.sparse.advanced.DirectSolver(self.a, self.b, options=self._opts())
        self.solver.plan()
        _sync()
        self.t["analyze"] += time.perf_counter() - t0
        self.n_analyze += 1

    def factorize(self, A):
        cp = self.cp
        t0 = time.perf_counter()
        Ah = sp.csr_matrix(A)
        # same pattern: overwrite values in place (no re-plan)
        self.a.data[...] = cp.asarray(Ah.data.astype(self.dtype))
        if self.mixed:
            self.a64.data[...] = cp.asarray(sp.csr_matrix(A).data.astype(np.float64))
        _sync()
        self.t["h2d"] += time.perf_counter() - t0
        t0 = time.perf_counter()
        self.solver.factorize()
        _sync()
        self.t["factorize"] += time.perf_counter() - t0
        self.n_factorize += 1

    def _solve_dev(self, rhs_dev):
        self.b[...] = rhs_dev.astype(self.dtype)
        x = self.solver.solve()
        return x

    def solve(self, b):
        cp = self.cp
        b = np.asarray(b, dtype=np.float64)
        cols = [b] if b.ndim == 1 else [b[:, j] for j in range(b.shape[1])]
        out = []
        for bj in cols:
            t0 = time.perf_counter()
            bd = cp.asarray(bj)
            _sync()
            self.t["h2d"] += time.perf_counter() - t0
            t0 = time.perf_counter()
            if not self.mixed:
                xd = self._solve_dev(bd).astype(cp.float64)
                it = 0
            else:
                # FP32 factor, FP64 residual; x_{k+1} = x_k + A32^{-1} r_k
                xd = self._solve_dev(bd).astype(cp.float64)
                nb = float(cp.linalg.norm(bd)) or 1.0
                it = 0
                for it in range(1, self.ir_max + 1):
                    r = bd - self.a64 @ xd
                    if float(cp.linalg.norm(r)) / nb < self.ir_tol:
                        it -= 1
                        break
                    xd = xd + self._solve_dev(r).astype(cp.float64)
                self.ir_iters.append(it)
            _sync()
            self.t["solve"] += time.perf_counter() - t0
            t0 = time.perf_counter()
            out.append(cp.asnumpy(xd))
            self.t["d2h"] += time.perf_counter() - t0
        return out[0] if b.ndim == 1 else np.column_stack(out)

    def close(self):
        if self.solver is not None:
            self.solver.free()
            self.solver = None


# ------------------------------------------------------- cuDSS through ctypes
# The direct C-API route (the brief's "CuPy bindings" option): NVIDIA's own
# cudss64_0.dll / libcudss.so.0 from the nvidia-cudss-cu13|cu12 wheel, driven
# with CuPy device arrays.  Used by default because nvmath-python cannot be
# imported on a host whose Windows App Control policy blocks its
# cusolver/cutensor binding modules (nvmath's package __init__ imports them
# unconditionally); cuDSS itself does not need them.
_CUDSS = None


def _cudss_lib():
    global _CUDSS
    if _CUDSS is not None:
        return _CUDSS
    import ctypes
    import glob
    import os
    import sys
    import cupy  # noqa: F401  (loads the CUDA runtime first)
    names = []
    for sp_dir in sys.path:
        for sub in ("nvidia/cu13", "nvidia/cu12", "nvidia/cudss"):
            d = os.path.join(sp_dir, *sub.split("/"))
            if os.path.isdir(d):
                names += glob.glob(os.path.join(d, "bin", "cudss64_*.dll"))
                names += glob.glob(os.path.join(d, "lib", "libcudss.so*"))
    names = [n for n in names if "mtlayer" not in n and "commlayer" not in n]
    if not names:
        raise ImportError("cuDSS library not found (pip install nvidia-cudss-cu13 or -cu12)")
    path = names[0]
    if os.name == "nt":
        os.add_dll_directory(os.path.dirname(path))
        x64 = os.path.join(os.path.dirname(path), "x86_64")
        if os.path.isdir(x64):
            os.add_dll_directory(x64)
    lib = ctypes.CDLL(path)
    vp, i64, ci = ctypes.c_void_p, ctypes.c_int64, ctypes.c_int
    P = ctypes.POINTER
    sig = {
        "cudssCreate": [P(vp)], "cudssDestroy": [vp], "cudssSetStream": [vp, vp],
        "cudssConfigCreate": [P(vp)], "cudssConfigDestroy": [vp],
        "cudssConfigSet": [vp, ci, vp, ctypes.c_size_t],
        "cudssDataCreate": [vp, P(vp)], "cudssDataDestroy": [vp, vp],
        "cudssDataGet": [vp, vp, ci, vp, ctypes.c_size_t, P(ctypes.c_size_t)],
        "cudssMatrixCreateCsr": [P(vp), i64, i64, i64, vp, vp, vp, vp, ci, ci, ci, ci, ci, ci],
        "cudssMatrixCreateDn": [P(vp), i64, i64, i64, vp, ci, ci],
        "cudssMatrixDestroy": [vp],
        "cudssExecute": [vp, ci, vp, vp, vp, vp, vp],
    }
    for fn, at in sig.items():
        f = getattr(lib, fn)
        f.argtypes = at
        f.restype = ci
    _CUDSS = lib
    return lib


_CUDSS_STATUS = {0: "SUCCESS", 1: "NOT_INITIALIZED", 2: "ALLOC_FAILED (out of GPU memory)",
                 3: "INVALID_VALUE", 4: "NOT_SUPPORTED", 5: "EXECUTION_FAILED",
                 6: "INTERNAL_ERROR", 7: "IR_FAILED"}


class CuDSSError(RuntimeError):
    pass


def _ck(st, what):
    if st != 0:
        raise CuDSSError("%s: cuDSS status %d %s" % (what, st, _CUDSS_STATUS.get(st, "")))


class CuDSSDirect(Backend):
    """cuDSS via its C API.  Pattern arrays live on the device; a new set of
    values with the same pattern is copied into the value buffer in place and
    refactorised without a new analysis (the production reuse cycle)."""
    device = "gpu"
    PH_ANALYSIS, PH_FACT, PH_SOLVE = 3, 4, 1008
    MT = {"general": 0, "symmetric": 1, "spd": 3}

    def __init__(self, dtype="float64", mtype="general", mixed=False,
                 ir_tol=1e-12, ir_max=30):
        super().__init__()
        import ctypes
        import cupy as cp
        import cupyx.scipy.sparse as csp
        self.ct, self.cp, self.csp = ctypes, cp, csp
        self.lib = _cudss_lib()
        self.mixed = mixed
        self.dtype = np.float32 if (dtype == "float32" or mixed) else np.float64
        self.vtype = 0 if self.dtype == np.float32 else 1       # CUDA_R_32F / CUDA_R_64F
        self.mtype = mtype
        self.ir_tol, self.ir_max = ir_tol, ir_max
        prec = "mixed" if mixed else ("fp32" if self.dtype == np.float32 else "fp64")
        self.name = "cudss_" + prec + {"general": "", "symmetric": "_sym", "spd": "_spd"}[mtype]
        self.h = ctypes.c_void_p()
        _ck(self.lib.cudssCreate(ctypes.byref(self.h)), "create")
        self.cfg = ctypes.c_void_p()
        _ck(self.lib.cudssConfigCreate(ctypes.byref(self.cfg)), "config")
        self.data = None
        self.mA = self.mx = self.mb = None
        self.ir_iters = []

    def _free_objs(self):
        lib = self.lib
        for m in (self.mA, self.mx, self.mb):
            if m is not None:
                lib.cudssMatrixDestroy(m)
        self.mA = self.mx = self.mb = None
        if self.data is not None:
            lib.cudssDataDestroy(self.h, self.data)
            self.data = None

    def analyze(self, A):
        ct, cp, lib = self.ct, self.cp, self.lib
        self._free_objs()
        A = sp.csr_matrix(A)
        A.sort_indices()
        n = A.shape[0]
        t0 = time.perf_counter()
        self.d_ptr = cp.asarray(A.indptr.astype(np.int32))
        self.d_ind = cp.asarray(A.indices.astype(np.int32))
        self.d_val = cp.asarray(A.data.astype(self.dtype))
        self.d_b = cp.zeros(n, dtype=self.dtype)
        self.d_x = cp.zeros(n, dtype=self.dtype)
        if self.mixed:
            self.a64 = self.csp.csr_matrix((cp.asarray(A.data), self.d_ind, self.d_ptr),
                                           shape=A.shape)
        _sync()
        self.t["h2d"] += time.perf_counter() - t0
        t0 = time.perf_counter()
        self.data = ct.c_void_p()
        _ck(lib.cudssDataCreate(self.h, ct.byref(self.data)), "data")
        self.mA = ct.c_void_p()
        _ck(lib.cudssMatrixCreateCsr(ct.byref(self.mA), n, n, int(A.nnz), self.d_ptr.data.ptr,
                                     None, self.d_ind.data.ptr, self.d_val.data.ptr, 10, 10,
                                     self.vtype, self.MT[self.mtype], 0, 0), "matrix A")
        self.mb = ct.c_void_p()
        self.mx = ct.c_void_p()
        _ck(lib.cudssMatrixCreateDn(ct.byref(self.mb), n, 1, n, self.d_b.data.ptr, self.vtype, 0), "b")
        _ck(lib.cudssMatrixCreateDn(ct.byref(self.mx), n, 1, n, self.d_x.data.ptr, self.vtype, 0), "x")
        _ck(lib.cudssExecute(self.h, self.PH_ANALYSIS, self.cfg, self.data, self.mA, self.mx,
                             self.mb), "analysis")
        _sync()
        self.t["analyze"] += time.perf_counter() - t0
        self.n_analyze += 1
        est = (ct.c_int64 * 16)()
        w = ct.c_size_t()
        if lib.cudssDataGet(self.h, self.data, 13, est, ct.sizeof(est), ct.byref(w)) == 0:
            self.info["mem_est_device_permanent_MB"] = est[0] / 2 ** 20
            self.info["mem_est_device_peak_MB"] = est[1] / 2 ** 20
        self.n = n

    def factorize(self, A):
        ct, cp, lib = self.ct, self.cp, self.lib
        A = sp.csr_matrix(A)
        A.sort_indices()
        t0 = time.perf_counter()
        self.d_val[...] = cp.asarray(A.data.astype(self.dtype))
        if self.mixed:
            self.a64.data[...] = cp.asarray(A.data)
        _sync()
        self.t["h2d"] += time.perf_counter() - t0
        t0 = time.perf_counter()
        _ck(lib.cudssExecute(self.h, self.PH_FACT, self.cfg, self.data, self.mA, self.mx, self.mb),
            "factorization")
        _sync()
        self.t["factorize"] += time.perf_counter() - t0
        self.n_factorize += 1
        info = ct.c_int()
        w = ct.c_size_t()
        lib.cudssDataGet(self.h, self.data, 0, ct.byref(info), ct.sizeof(info), ct.byref(w))
        if info.value != 0:
            raise CuDSSError("factorization info=%d (zero/negative pivot for mtype %s)"
                             % (info.value, self.mtype))
        nnz = ct.c_int64()
        if lib.cudssDataGet(self.h, self.data, 1, ct.byref(nnz), 8, ct.byref(w)) == 0:
            self.info["lu_nnz"] = int(nnz.value)

    def _solve_dev(self, rhs):
        cp, lib = self.cp, self.lib
        self.d_b[...] = rhs.astype(self.dtype)
        _ck(lib.cudssExecute(self.h, self.PH_SOLVE, self.cfg, self.data, self.mA, self.mx, self.mb),
            "solve")
        return self.d_x.astype(cp.float64)       # a copy

    def solve(self, b):
        cp = self.cp
        b = np.asarray(b, dtype=np.float64)
        cols = [b] if b.ndim == 1 else [b[:, j] for j in range(b.shape[1])]
        out = []
        for bj in cols:
            t0 = time.perf_counter()
            bd = cp.asarray(bj)
            _sync()
            self.t["h2d"] += time.perf_counter() - t0
            t0 = time.perf_counter()
            xd = self._solve_dev(bd)
            if self.mixed:
                nb = float(cp.linalg.norm(bd)) or 1.0
                it = 0
                for it in range(1, self.ir_max + 1):
                    r = bd - self.a64 @ xd
                    if float(cp.linalg.norm(r)) / nb < self.ir_tol:
                        it -= 1
                        break
                    xd = xd + self._solve_dev(r)
                self.ir_iters.append(it)
            _sync()
            self.t["solve"] += time.perf_counter() - t0
            t0 = time.perf_counter()
            out.append(cp.asnumpy(xd))
            self.t["d2h"] += time.perf_counter() - t0
        return out[0] if b.ndim == 1 else np.column_stack(out)

    def close(self):
        if getattr(self, "h", None) is None:
            return
        self._free_objs()
        self.lib.cudssConfigDestroy(self.cfg)
        self.lib.cudssDestroy(self.h)
        self.h = None
        try:
            self.cp.get_default_memory_pool().free_all_blocks()
        except Exception:
            pass


# ----------------------------------------------------------------------- CuPy
class CupyQR(Backend):
    name = "cupy_qr"
    device = "gpu"

    def analyze(self, A):
        pass

    def factorize(self, A):
        import cupy as cp  # noqa: F401
        import cupyx.scipy.sparse as csp
        t0 = time.perf_counter()
        self.a = csp.csr_matrix(sp.csr_matrix(A))
        _sync()
        self.t["h2d"] += time.perf_counter() - t0

    def solve(self, b):
        import cupy as cp
        from cupyx.scipy.sparse.linalg import spsolve
        b = np.asarray(b, float)
        cols = [b] if b.ndim == 1 else [b[:, j] for j in range(b.shape[1])]
        out = []
        t0 = time.perf_counter()
        for bj in cols:
            out.append(cp.asnumpy(spsolve(self.a, cp.asarray(bj))))
        self.t["solve"] += time.perf_counter() - t0
        return out[0] if b.ndim == 1 else np.column_stack(out)


class CupyGmresJacobi(Backend):
    name = "cupy_gmres_jac"
    device = "gpu"

    def __init__(self, tol=1e-10, maxiter=20, restart=100):
        super().__init__()
        self.tol, self.maxiter, self.restart = tol, maxiter, restart
        self.iters = []

    def analyze(self, A):
        pass

    def factorize(self, A):
        import cupy as cp
        import cupyx.scipy.sparse as csp
        t0 = time.perf_counter()
        A = sp.csr_matrix(A)
        self.a = csp.csr_matrix(A)
        d = A.diagonal()
        d[d == 0] = 1.0
        self.dinv = cp.asarray(1.0 / d)
        _sync()
        self.t["h2d"] += time.perf_counter() - t0

    def solve(self, b):
        import cupy as cp
        from cupyx.scipy.sparse.linalg import LinearOperator, gmres
        n = self.a.shape[0]
        M = LinearOperator((n, n), matvec=lambda v: self.dinv * v)
        b = np.asarray(b, float)
        cols = [b] if b.ndim == 1 else [b[:, j] for j in range(b.shape[1])]
        out = []
        t0 = time.perf_counter()
        for bj in cols:
            cnt = [0]

            def cb(_):
                cnt[0] += 1
            x, info = gmres(self.a, cp.asarray(bj), rtol=self.tol, restart=self.restart,
                            maxiter=self.maxiter, M=M, callback=cb, callback_type="pr_norm")
            self.iters.append((cnt[0], int(info)))
            out.append(cp.asnumpy(x))
        self.t["solve"] += time.perf_counter() - t0
        return out[0] if b.ndim == 1 else np.column_stack(out)


# ----------------------------------------------------------------------- AmgX
AMGX_CFG = {
    "config_version": 2,
    "solver": {
        "solver": "FGMRES", "max_iters": 500, "gmres_n_restart": 50,
        "convergence": "RELATIVE_INI_CORE", "tolerance": 1e-10, "norm": "L2",
        "monitor_residual": 1, "store_res_history": 1,
        "preconditioner": {"solver": "AMG", "algorithm": "AGGREGATION",
                           "selector": "SIZE_2", "smoother": "MULTICOLOR_GS",
                           "presweeps": 1, "postsweeps": 1, "max_iters": 1,
                           "cycle": "V", "max_levels": 25},
    },
}


class AmgX(Backend):
    name = "amgx"
    device = "gpu"

    def __init__(self):
        super().__init__()
        import pyamgx
        pyamgx.initialize()
        self.px = pyamgx
        self.cfg = pyamgx.Config().create_from_dict(AMGX_CFG)
        self.rsc = pyamgx.Resources().create_simple(self.cfg)
        self.M = pyamgx.Matrix().create(self.rsc)
        self.bv = pyamgx.Vector().create(self.rsc)
        self.xv = pyamgx.Vector().create(self.rsc)
        self.slv = pyamgx.Solver().create(self.rsc, self.cfg)
        self.iters = []

    def analyze(self, A):
        pass

    def factorize(self, A):
        t0 = time.perf_counter()
        self.M.upload_CSR(sp.csr_matrix(A))
        self.t["h2d"] += time.perf_counter() - t0
        t0 = time.perf_counter()
        self.slv.setup(self.M)
        self.t["factorize"] += time.perf_counter() - t0
        self.n = A.shape[0]

    def solve(self, b):
        b = np.asarray(b, float)
        cols = [b] if b.ndim == 1 else [b[:, j] for j in range(b.shape[1])]
        out = []
        t0 = time.perf_counter()
        for bj in cols:
            self.bv.upload(bj)
            self.xv.upload(np.zeros(self.n))
            self.slv.solve(self.bv, self.xv)
            self.iters.append((int(self.slv.iterations_number), str(self.slv.status)))
            out.append(self.xv.download())
        self.t["solve"] += time.perf_counter() - t0
        return out[0] if b.ndim == 1 else np.column_stack(out)

    def close(self):
        for o in (self.slv, self.xv, self.bv, self.M, self.rsc, self.cfg):
            try:
                o.destroy()
            except Exception:
                pass
        try:
            self.px.finalize()
        except Exception:
            pass


def make_backend(name, **kw):
    if name == "pardiso":
        return Pardiso()
    if name == "pardiso_spd":
        return Pardiso(mtype=2)
    if name == "pardiso_sym":
        return Pardiso(mtype=-2)
    if name == "superlu":
        return SuperLU()
    # cuDSS: C API via ctypes (default) or nvmath-python ("nvmath_" prefix)
    if name.startswith("nvmath_"):
        nm = name[len("nvmath_"):]
        if nm == "cudss_fp64":
            return CuDSS("float64")
        if nm == "cudss_fp64_sym":
            return CuDSS("float64", symmetric=True)
        if nm == "cudss_fp64_spd":
            return CuDSS("float64", symmetric="spd")
        if nm == "cudss_fp32":
            return CuDSS("float32")
        if nm == "cudss_mixed":
            return CuDSS(mixed=True, ir_tol=kw.get("ir_tol", 1e-12))
    if name == "cudss_fp64":
        return CuDSSDirect("float64")
    if name == "cudss_fp64_sym":
        return CuDSSDirect("float64", mtype="symmetric")
    if name == "cudss_fp64_spd":
        return CuDSSDirect("float64", mtype="spd")
    if name == "cudss_fp32":
        return CuDSSDirect("float32")
    if name == "cudss_fp32_spd":
        return CuDSSDirect("float32", mtype="spd")
    if name == "cudss_mixed":
        return CuDSSDirect(mixed=True, ir_tol=kw.get("ir_tol", 1e-12))
    if name == "cudss_mixed_spd":
        return CuDSSDirect(mtype="spd", mixed=True, ir_tol=kw.get("ir_tol", 1e-12))
    if name == "cupy_qr":
        return CupyQR()
    if name == "cupy_gmres_jac":
        return CupyGmresJacobi()
    if name == "amgx":
        return AmgX()
    raise ValueError(name)
