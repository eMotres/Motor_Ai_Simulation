# One linear-solver interface; CHOLMOD + MUMPS as the open build (2026-09-30)

**Status: PARKED (owner, 2026-09-30): we stay with MKL PARDISO only, no CHOLMOD/MUMPS
backend and no open build for now.** This branch is a WIP checkpoint, not a PR. What
was finished: the interface, the image stage (both variants built on the server), the
targeted tests (138 passed in the MKL image, 102 in the open image), and the per-solve
calibration below. What was NOT done: the end-to-end runs (stopped before the first
one finished), so there is no measured whole-run time or accuracy table yet; the
per-solve table below is the best evidence. To resume: `scripts/bench/e2e_linear_backend.py`
(one run per case and backend) and `scripts/bench/e2e_table.py` (the table).

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no escalation, no sub-agents
(owner instruction). Branch `feat/open-solvers-default` from
`pre-migration-freeze-2026-09-15` (fb2c4e7). Inputs: the solver study
`open_solver_vs_mkl_2026-09-30` (per-solve numbers, branch `study/open-solvers`)
and `SOLVER_PROFILING_2026-09-29.md` (the exported production systems).

## Decision (owner, 2026-09-30, revised the same day)

1. First brief: drop Intel MKL/PARDISO from the default build (MKL's licence and
   gmsh's GPL in one AGPL process).
2. Revision before completion: **keep MKL PARDISO on our server** under an AGPL
   section 7 MKL exception (PR #93), with gmsh moved out of process by another
   task. So `SB_LINEAR_BACKEND=auto` (the default) picks PARDISO when pypardiso/MKL
   is installed and CHOLMOD (SPD) + MUMPS (LU, large SPD) when it is not. The
   server image keeps `WITH_PARDISO=1`; `WITH_PARDISO=0` (the Dockerfile default) is
   the fully open build. The end-to-end table below is therefore **the cost of the
   open build**, not a change to the server.

## The interface (`src/motor_ai_sim/simulation/linear_backend.py`)

Three layers, kept small on purpose so the TDM prototype (PR #87) can hold
factor objects directly:

| layer | what it is | methods |
|---|---|---|
| `Factor` | one factorisation of one sparsity pattern from one library: `CholmodFactor`, `MumpsFactor(spd)`, `SuperLUFactor`, `PardisoFactor(spd)` | `analyze(A)`, `factorize(A)`, `refactorize(A)`, `solve(b)`, `free()` |
| `FactorStream` | a factor + the pattern it was analysed for; re-analyses only when `indptr`/`indices` moved | `factor(A)`, `solve(b)`, `factor_solve(A, b)`, `free()` |
| `LinearSolver` | the router one run owns: selection, SPD checks, reuse, fallbacks, counters, notes | `solve(A, rhs, spd)`, `factor(A, spd)` + `solve_factored(b)`, `describe()`, `free()` |
| `FrameFactor` | drop-in for `time_periodic.FrameFactor` (TDM): same surface (`factor(A, threads)`, `solve(b, threads)`, `close()`, `n`, `analyses`, `factorizations`, `solves`, `lu_used`, `lib`) | |

Selection (`SB_LINEAR_BACKEND`): `auto` (default) = `pardiso` if installed, else
`open`; `open` = MUMPS SYM=1 for SPD (CHOLMOD below `SB_SPD_MUMPS_MIN_DOF`, default
**0**, i.e. never: calibrated below) and MUMPS LU for the rest; `cholmod` / `mumps` /
`pardiso` / `superlu` force a family. `SB_NO_PARDISO=1` still hides an installed MKL
from `auto`. MUMPS orders with AMD (LU, and SPD below 55 k unknowns) or AMF (SPD
above), `SB_MUMPS_ORDERING` overrides; CHOLMOD uses AMD (`SB_CHOLMOD_ORDER`) and its own
supernodal/simplicial choice.

Fallbacks, loud and once per run, each with a log warning and a line in the
result's `linear_solver.notes`:

* the SPD checks (moved unchanged from `p2_nonlinear`: symmetric pattern, exact
  pairwise value symmetry to 1e-12 of sqrt(a_ii a_jj), positive diagonal) decline
  a matrix to LU for that solve;
* a Cholesky failure (CHOLMOD not-positive-definite, MUMPS SYM=1 negative pivot
  count INFOG(12) > 0, PARDISO mtype 2 error) sends the rest of the run to LU;
* an LU failure (MUMPS or PARDISO) sends the rest of the run to SciPy SuperLU;
* `pardiso` requested without pypardiso, or a missing library, degrades once.

Pattern reuse: CHOLMOD keeps its symbolic analysis (`CholeskyFactor.factorize`
recomputes only the numbers), MUMPS `factor(reuse_analysis=True)`, PARDISO phase
11 once and phase 23 per solve (the exact calls the old code made).
`SB_LINEAR_NO_REUSE=1` (old name `SB_NO_PARDISO_REUSE`) re-analyses every solve;
`SB_LINEAR_SPD=0` (old `SB_PARDISO_SPD=0`) keeps every solve on LU.

## Consumers covered

| consumer | path | system |
|---|---|---|
| static Newton, Picard sweeps | `P2Nonlinear.solve_ff(spd=True)` | SPD |
| eddy BDF2 march (bordered Jacobian, static start) | `P2Drive.eddy_solve`, `eddy_static_state` | SPD |
| voltage drive (current + voltage Newtons, phasor init) | `P2Drive.v_newton`, `ve_newton`, `v_picard` | SPD; series strands `spd=False` → LU |
| d-axis / psi_PM calibration, frozen-permeability Ldq | `fem_solver_2d.frozen_permeability_*` through the run's `P2Nonlinear` | SPD |
| passports (2-D) | the same `fem_transient_sliding_band` | as above |
| Stage A / 3-D total scalar potential | `static3d/solver._linear_solver` (was the global `pypardiso.spsolve`) | SPD |
| 3-D A-formulation (tree gauge, source cleaning, CG preconditioner) | `static3d/nedelec._direct`, `_regularised_preconditioner` | SPD |
| contact mechanics, rotor stress | `mechanical/contact._solver` | LU |
| TDM prototype (PR #87) | `linear_backend.FrameFactor` | SPD per frame |

The 3-D and mechanical solves used pypardiso's module-level `spsolve` (one global
solver, one global lock, a cache keyed on the matrix content). They now own their
factorisation per solve (`linear_backend.solve_once`): no shared state, no lock,
and the NaN guard (retry once on a fresh factorisation, then raise) applies to
every backend. SciPy-only solves that never used PARDISO (thermal, the
frequency-domain rotor-eddy cross-check, the P2 projection mass matrix, rotor
stress `spsolve`) are unchanged.

`pardiso_subprocess_env` no longer imports pypardiso for a child that will not use
it (`SB_LINEAR_BACKEND` other than `auto`/`pardiso`), so an open run never maps MKL
into the API process.

## Thread safety

One `LinearSolver` (or `FrameFactor`) per thread is the design; each also holds a
lock around its own calls. Distinct objects in several threads (the API's job
threads, TDM's frame pool) are safe on every backend, but parallel only with PARDISO:

* PARDISO: one handle per factor; MKL is re-entrant across handles.
* MUMPS: **not** safe for concurrent instances (sequential MUMPS 5.7.3 + python-mumps
  0.0.4: two threads factorising two matrices segfault the process, measured).
  Every MUMPS call holds one process-wide lock; python-mumps releases the GIL inside
  MUMPS, so other Python threads (API requests) keep running while one waits.
* CHOLMOD: every factor owns its `cholmod_common`; scikit-sparse 0.5 holds the GIL
  inside CHOLMOD, so factorisations from threads are serialised (and block other
  Python threads while they run).

So in the open build two concurrent solves in ONE process share one solver core;
parallel solves need processes (`SOLVE_POOL=1`). Tested: six solvers in six threads
and eight TDM frames from a four-thread pool, every backend, results equal to the
serial ones to 1e-12.

## TDM integration (for PR #87)

The prototype's `FrameFactor` is PARDISO-only. Measured here with a two-line
change in `time_periodic.py`: the PARDISO class renamed `_PardisoFrameFactor`, and

```python
def FrameFactor(own=None, release=None):
    from motor_ai_sim.simulation import linear_backend as _lb
    if _lb.LinearSolver().backend == "pardiso":
        return _PardisoFrameFactor(own=own, release=release)
    return _lb.FrameFactor(own=own, release=release)
```

`fem_solver_2d` keeps importing `own_pardiso as _own_pardiso`, which the TDM code
passes to its frame factors.

## Server image

`deploy/Dockerfile.api` gains an `opensolvers-build` stage (`build-essential
libsuitesparse-dev libmumps-seq-dev`, `pip wheel` of `requirements-opensolvers.txt`:
scikit-sparse 0.5.0, python-mumps 0.0.4, the last release for Python 3.11); the runtime
stages install only the shared libraries (SuiteSparse 7.10.1, MUMPS 5.7.3 sequential,
SCOTCH 7.0.7, `libopenblas0-pthread` 0.3.29) and the two wheels, and import-check them
at build time. `deploy/compute-worker/Dockerfile` (a distributed image) gets the same
stage. The compose file keeps `WITH_PARDISO=1` for our server (owner's revision);
`WITH_PARDISO=0` is the Dockerfile default and the fully open build.

Built on the server (throwaway tags, deleted afterwards) and checked in each image:

| image | size | MKL files | numpy / scipy BLAS | libblas.so.3 / liblapack.so.3 (CHOLMOD, MUMPS) | `linear_backend` auto |
|---|---:|---|---|---|---|
| `WITH_PARDISO=1 WITH_TRIANGLE=1`, test target (= our server) | 4.25 GB | `libmkl_rt.so.3` & co. (pypardiso 0.4.7, mkl 2026.1.0) | scipy-openblas 0.3.31 / 0.3.30 (wheels) | `openblas-pthread` | `pardiso` (Cholesky mtype 2, LU mtype 11) |
| `WITH_PARDISO=0 WITH_TRIANGLE=0`, api target (fully open) | 2.89 GB | none (`find / -iname "*mkl*"`: nothing) | scipy-openblas 0.3.31 / 0.3.30 | `openblas-pthread` | `open` (MUMPS SYM=1, MUMPS LU) |
| compute worker | 2.89 GB | none | scipy-openblas | `openblas-pthread` | (see note) |

numpy and scipy come from their manylinux wheels, which bundle OpenBLAS (never MKL);
Debian's alternatives point `libblas.so.3`/`liblapack.so.3` at OpenBLAS because
`libopenblas0-pthread` is installed explicitly (without it `--no-install-recommends`
would pull the reference BLAS). `ldd` of both extension modules shows
`libopenblas.so.0`; no METIS (CHOLMOD carries its own Apache-2.0 copy, MUMPS uses SCOTCH
and PORD). Note: the compute-worker image cannot import `motor_ai_sim` at all
(`ModuleNotFoundError: sympy`: its `pip install -e .` misses the dependencies that live
only in `requirements.txt`). That predates this change and is flagged separately.

## Calibration of the CHOLMOD → MUMPS switch

All exported SPD production systems (`solver-profiling-20260929/matrices`, read-only;
Ø40 L12, L13, L155 rated, L155 refined to mesh ×0.7…×0.2), each a first/later pair on
one pattern, timed through `linear_backend`'s own `FactorStream` (the production call
path) in the MKL test image, 4 threads, `nice 19`: cold = analyse + factorise + solve of
the first matrix, warm = factorise + solve of the later one with the analysis reused
(median of 5). "Weighted" = 0.12 × cold + 0.88 × warm, the measured share of solves on a
new pattern (`SOLVER_PROFILING_2026-09-29.md`: 10-14 %). MKL PARDISO is the same
weighting of the profiling run's own per-call times (same server, 4 MKL threads,
2026-09-29), so that column is a reference, not a same-minute measurement.
Weighted ms per solve (`scripts/bench/calibrate_spd_threshold.py`, raw JSON kept in the
PR description):

| system | n | MKL PARDISO (ref.) | CHOLMOD, same object | CHOLMOD, symbolic copy | MUMPS SYM=1, MUMPS ordering | MUMPS SYM=1, AMD | MUMPS SYM=1, AMF | MUMPS LU, AMD | open default ÷ PARDISO | max rel. diff. vs PARDISO x |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Ø40 eddy Jacobian ×1 | 23931 | 16.9 | 25.3 | 32.1 | 39.5 | **19.1** | 18.7 | 29.9 | 1.13 | 2.5e-13 |
| L13 eddy Jacobian ×1 | 30768 | 21.5 | 34.9 | 36.0 | 39.9 | **24.9** | 27.9 | 37.8 | 1.15 | 8.9e-13 |
| L155 static K ×1 | 39030 | 21.7 | 38.4 | 44.1 | 43.4 | **28.6** | 28.9 | 42.8 | 1.32 | 1.1e-12 |
| L155 eddy Jacobian ×1 | 44277 | 24.0 | 46.5 | 49.2 | 52.1 | **36.4** | 35.6 | 52.2 | 1.52 | 1.1e-12 |
| L155 eddy Jacobian ×0.7 | 49375 | 32.6 | 66.7 | 55.0 | 61.7 | **41.3** | 45.2 | 53.7 | 1.27 | 1.5e-12 |
| L155 static K ×0.5 | 55607 | 48.0 | 96.3 | 66.2 | 68.9 | 55.1 | **49.4** | 75.2 | 1.03 | 1.1e-12 |
| L155 eddy Jacobian ×0.5 | 60754 | 41.2 | 100.8 | 78.0 | 85.5 | 56.3 | **53.8** | 106.5 | 1.31 | 1.2e-12 |
| L155 eddy Jacobian ×0.3 | 97437 | 102.7 | 322.6 | 169.2 | 162.9 | 154.6 | **142.0** | 205.1 | 1.38 | 2.1e-12 |
| L155 eddy Jacobian ×0.2 | 168527 | 171.8 | 890.0 | 311.6 | 300.6 | 252.7 | **231.9** | 391.1 | 1.35 | 8.5e-13 |

Bold = the open default's choice for that size.

Findings:

* **MUMPS SYM=1 (the positive-definite LDLᵀ, no pivoting) with an AMD-family ordering is
  the fastest open SPD solver at every size**, 17 % to 30 % faster per solve than
  CHOLMOD. The brief expected CHOLMOD below a DOF threshold and MUMPS above it; the data
  put that threshold at 0. `SPD_MUMPS_MIN_DOF_DEFAULT = 0`; CHOLMOD stays selectable
  (`SB_LINEAR_BACKEND=cholmod`) and is the SPD fallback when MUMPS is absent.
* MUMPS' own ordering choice (`auto`, SCOTCH/PORD here) costs 3-6× AMD in the analysis for
  a 5-15 % faster factorisation: a loss at 12 % new patterns. Policy: AMD for LU and for
  SPD below 55 k unknowns, AMF above (`SB_MUMPS_ORDERING` overrides).
* scikit-sparse 0.5 turns every numeric CHOLMOD factor into a simplicial one after the
  factorisation, so refactorising the same object runs the simplicial algorithm from the
  second call: faster up to ~45 k, 2-3× slower above (169 k: 955 against 298 ms warm).
  `CholmodFactor` therefore refactorises from a copy of the kept symbolic analysis from
  `SB_CHOLMOD_SUPER_MIN_DOF` = 50 000 up. The CHOLMOD warm times of the study
  (`study/open-solvers`) were taken on the same object, i.e. on this slow path.
* The open default costs 1.03-1.52× MKL PARDISO per solve, against 1.4-2.8× for the
  study's CHOLMOD. Every open solution matches PARDISO's FP64 answer to ≤ 2.1e-12
  relative.
* Sequential MUMPS 5.7.3 is not safe for two concurrent instances: two threads
  factorising two different matrices segfault the process (reproduced with 1 and 4 BLAS
  threads). All MUMPS calls hold one process-wide lock (`_MUMPS_LOCK`).

## End to end on the server (the cost of the open build)

Not measured: the queue (Ø40 L12, L13, L155 rated march with Coulomb torque and one
gap layer per side, L155 static Newton, L155 TDM; PARDISO, open and CHOLMOD each) was
stopped when the owner parked the task, before the first run finished. The harness is
`scripts/bench/e2e_linear_backend.py` (times every `LinearSolver` call, records frames,
peak RSS, whether MKL was mapped, Coulomb mean torque, ripple, total loss and the torque
series) and `scripts/bench/e2e_table.py`; the case definitions are
`scripts/gap_layers_study/` (`SB_GAP_LAYERS_MIN=1 SB_GAP_REFINE=0`, steps 48/60/72).
Estimate from the calibration and the linear-solve share of the profiling
(27-39 %): the open default would cost about +5 % (Ø40) to +20 % (L155 eddy) of wall
time against PARDISO. An estimate, not a measurement.

## Licences added

See `THIRD_PARTY_NOTICES.md`, section "Open-source sparse direct solvers".

## Tests

`tests/test_linear_backend.py` (selection, fallbacks with fake factors, reuse,
real backends, thread safety, TDM surface, optional parity with the exported
systems via `SB_TEST_MATRICES`), plus the updated `test_p2_cholesky`,
`test_p2_nonlinear` (reuse on PARDISO and MUMPS), `test_pardiso_lifetime`,
`test_pardiso_global_session` (now: no shared state), `test_nedelec_pardiso_lifetime`,
`test_static3d_stage_a::TestLinearSolverGuard`, `test_pardiso_runtime`.

Server, 2026-09-30 (throwaway images from this branch): MKL image 138 passed, 1 skipped
(no Cholesky in SuperLU); open image 102 passed, 8 skipped (PARDISO absent), including
the exported-system parity (`SB_TEST_MATRICES`, every x1 system ≤ 1e-8 of PARDISO) and
the thread tests. The first run segfaulted in the MUMPS thread test, which is how the
MUMPS thread-safety problem above was found.
