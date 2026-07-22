"""End-to-end: run the REAL canonical transient (em_transient_eval) with SuperLU
then PARDISO. Measures total wall, the fraction spent in the linear solve, the
end-to-end speedup, and that torque is unchanged. Patching scipy spsolve covers
both skfem's default solver and the raw solve sites."""
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["PYTHONIOENCODING"] = "utf-8"
import time
import scipy.sparse.linalg as spl

_orig = spl.spsolve
acc = {"t": 0.0, "n": 0}
def _timed(A, b, *a, **k):
    s = time.perf_counter(); x = _orig(A, b, *a, **k); acc["t"] += time.perf_counter() - s; acc["n"] += 1; return x
spl.spsolve = _timed

import motor_ai_sim.simulation.fem_solver_2d as F

KW = dict(n_steps_per_period=24, n_periods=1.0, gamma_deg=-42.0, I_phase_rms=38.0,
          mesh_size_mm=1.0, gap_layers=1.0, n_sectors=2, rotor_eddy=True,
          demag=False, torque_filter=True)

def run(label):
    acc["t"] = 0.0; acc["n"] = 0
    t = time.perf_counter()
    r = F.em_transient_eval(**KW)
    wall = time.perf_counter() - t
    s = r.get("summary") if isinstance(r, dict) else None
    Tavg = (s or {}).get("T_em_avg_Nm", r.get("T_avg_Nm") if isinstance(r, dict) else None)
    frac = 100 * acc["t"] / wall if wall else 0
    print("%-20s wall=%6.2fs   solve=%6.2fs (%2.0f%%)   n_solves=%4d   T_avg=%s"
          % (label, wall, acc["t"], frac, acc["n"], Tavg))
    return wall, Tavg

print("warming up (mesh build + import)...")
run("SuperLU (warmup)")          # first call pays import/JIT/mesh init
w1, T1 = run("SuperLU")

import pypardiso
def _pard(A, b, *a, **k):
    s = time.perf_counter(); x = pypardiso.spsolve(A, b); acc["t"] += time.perf_counter() - s; acc["n"] += 1; return x
spl.spsolve = _pard
run("PARDISO (warmup)")
w2, T2 = run("PARDISO")

print()
print(">> end-to-end speedup: %.2fx" % (w1 / w2))
print(">> torque match: T_superlu=%.4f  T_pardiso=%.4f  |dT|=%.2e"
      % (T1 or 0, T2 or 0, abs((T1 or 0) - (T2 or 0))))
