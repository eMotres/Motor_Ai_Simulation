"""Benchmark sparse linear solvers on the REAL 40mm motor stiffness matrix.

Captures the actual (A, b) skfem solves during a static magnetostatic solve of
the 40mm design point (gamma=-42, I=38), then times SuperLU vs UMFPACK vs PARDISO
on that exact system. Direct solvers -> identical answer; we measure factorize+solve
wall-time (the per-step cost in the transient today)."""
import os, time, numpy as np
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import scipy.sparse as sp
import scipy.sparse.linalg as sla
import skfem

cap = {}
def _grab(A, b):
    if "A" not in cap:
        cap["A"] = sp.csr_matrix(A)
        cap["b"] = np.asarray(b).ravel()

# Intercept both possible solve paths (skfem.solve via condense, or raw scipy spsolve).
_orig_sk = skfem.solve
def _sk(A, b, *a, **k):
    _grab(A, b); return _orig_sk(A, b, *a, **k)
skfem.solve = _sk
_orig_sp = sla.spsolve
def _sp(A, b, *a, **k):
    _grab(A, b); return _orig_sp(A, b, *a, **k)
sla.spsolve = _sp

import motor_ai_sim.simulation.fem_solver_2d as F
t0 = time.perf_counter()
res = F.fem_solve_for_sim(rotor_angle_deg=0.0, gamma_deg=-42.0,
                          mesh_size_mm=1.0, n_sectors=2, I_phase_rms=38.0)
dt_static = time.perf_counter() - t0
skfem.solve = _orig_sk
sla.spsolve = _orig_sp

T = res.get("T_em_Nm", float("nan")) if isinstance(res, dict) else float("nan")
print("static fem_solve_for_sim wall = %.3f s   T_em = %.4f Nm" % (dt_static, T))

if "A" not in cap:
    print("NO MATRIX CAPTURED — solver used a path we didn't patch."); raise SystemExit(1)

A = cap["A"].tocsc(); b = cap["b"]
n, nnz = A.shape[0], A.nnz
amax = abs(A).max()
sym = "n/a"
try:
    sym = bool(abs(A - A.T).max() <= 1e-9 * amax)
except Exception:
    pass
print("captured A: n=%d  nnz=%d  density=%.2e  symmetric=%s" % (n, nnz, nnz/(n*n), sym))
print()

def bench(fn, label, reps=7):
    try:
        x = fn(A, b)                       # warmup (also triggers any one-time init)
    except Exception as e:
        print("%-22s FAILED: %s" % (label, e)); return None, None
    ts = []
    for _ in range(reps):
        t = time.perf_counter(); x = fn(A, b); ts.append(time.perf_counter() - t)
    ts.sort(); med = ts[len(ts)//2]
    resid = np.linalg.norm(A @ x - b) / (np.linalg.norm(b) + 1e-30)
    print("%-22s median=%8.1f ms   min=%8.1f ms   resid=%.1e" % (label, med*1e3, ts[0]*1e3, resid))
    return med, x

print("=== solve timing (factorize + solve, %d reps) ===" % 7)
m_lu, x_lu = bench(lambda A, b: sla.spsolve(A, b, use_umfpack=False), "SuperLU (scipy)")

m_um = None
try:
    import scikits.umfpack  # noqa
    m_um, x_um = bench(lambda A, b: sla.spsolve(A, b, use_umfpack=True), "UMFPACK")
except Exception:
    print("%-22s not installed" % "UMFPACK")

m_pd = None
try:
    import pypardiso
    try:
        import mkl
        print("(MKL max threads: %d)" % mkl.get_max_threads())
    except Exception:
        pass
    m_pd, x_pd = bench(lambda A, b: pypardiso.spsolve(A, b), "PARDISO (MKL)")
except Exception as e:
    print("%-22s not installed (%s)" % ("PARDISO (MKL)", e))

print()
if m_lu and m_pd:
    print(">> PARDISO speedup vs SuperLU: %.2fx" % (m_lu / m_pd))
    print(">> max|x_SuperLU - x_PARDISO| = %.2e (should be ~0)" % np.abs(x_lu - x_pd).max())
if m_lu and m_um:
    print(">> UMFPACK speedup vs SuperLU: %.2fx" % (m_lu / m_um))

# ── Factorization-reuse potential (part 2) ────────────────────────────────────
# Split factorize vs solve-only. The Picard loop / time-steps re-solve with the
# SAME sparsity pattern; reusing the factorization (or at least the symbolic
# analysis) collapses the per-solve cost toward "solve-only".
print("\n=== factorization-reuse split (SuperLU via splu) ===")
def t(fn, reps=7):
    fn()  # warmup
    ts = []
    for _ in range(reps):
        s = time.perf_counter(); fn(); ts.append(time.perf_counter() - s)
    ts.sort(); return ts[len(ts)//2]
lu_holder = {}
t_fact = t(lambda: lu_holder.__setitem__("lu", sla.splu(A)))
lu = lu_holder["lu"]
t_solve = t(lambda: lu.solve(b))
print("splu factorize     median=%8.1f ms" % (t_fact*1e3))
print("lu.solve (reuse)   median=%8.1f ms" % (t_solve*1e3))
print(">> solve-only is %.1fx cheaper than factorize+solve (%.1f -> %.1f ms)"
      % ((t_fact+t_solve)/max(t_solve,1e-6), (t_fact+t_solve)*1e3, t_solve*1e3))

if m_pd is not None:
    try:
        from pypardiso import PyPardisoSolver
        ps = PyPardisoSolver()
        ps.factorize(A)                       # analysis + numeric factorization
        t_pd_solve = t(lambda: ps.solve(A, b))  # same A -> solve-only (reuses factor)
        print("\n=== factorization-reuse (PARDISO) ===")
        print("PARDISO solve-only median=%8.1f ms  (vs %.1f ms fresh factor+solve)"
              % (t_pd_solve*1e3, m_pd*1e3))
    except Exception as e:
        print("PARDISO reuse probe failed:", e)
