# Cholesky (PARDISO mtype 2) for the SPD P2 systems (2026-09-29)

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no escalation. Branch `perf/cholesky`
from `pre-migration-freeze-2026-09-15` (6ed3641). Input: `SOLVER_PROFILING_2026-09-29.md`
and `GPU_TDM_STUDY_2026-09-29.md` (PR #56): all 68 exported systems were symmetric
positive definite, yet every solve used the unsymmetric LU (mtype 11).

## What changed

- `P2Nonlinear.solve_ff(M, rhs, spd=False)`: a caller whose operator is SPD **by
  construction** passes `spd=True`. The matrix is then factorised by Cholesky
  (PARDISO mtype 2, upper triangle, second handle owned by the same run scope), with
  the symbolic analysis reused while the pattern holds, exactly like the LU path.
- Run-time checks before every Cholesky solve; any doubt goes to the unchanged LU:
  - canonical input: an unsorted or duplicated matrix is canonicalised on a copy first;
  - per pattern (once per analysis): a full diagonal, structural symmetry, and the
    transpose map of the stored entries (7–8 ms at 0.5 M nnz);
  - per solve, deterministic and exact: a_ii > 0 and |a_ij − a_ji| ≤ 1e-12·√(a_ii a_jj) on
    every stored pair (about 3 ms at 0.5 M nnz on the L155 eddy system);
  - a Cholesky error is the positive-definiteness test: it is logged as a warning and the
    rest of the run uses LU. Counters travel in the result: `linear_solver`
    (`cholesky_solves`, `cholesky_declined`, `cholesky_failures`, LU counts).
- `SB_PARDISO_SPD=0` keeps every solve on LU (the previous code path, bit-identical).
- SuperLU fallback, pattern reuse and the perturbed-pivot counter of the LU path are
  unchanged.

## Why each system is SPD (by construction)

Notation: P = the projection of the run, `free` = all reduced columns but the outer
Dirichlet A = 0 ring. Every factored matrix is PᵀXP restricted to `free`, possibly
bordered. PᵀXP is symmetric for any P when X is.

**Full column rank, no free null mode.** `SlipProjection.build` makes P a *signed
selection*: a signed union-find of the ring welds (vertices and edge midpoints) and of
the (anti)periodic cut gives every full dof exactly one reduced column, with weight ±1.
The column supports are therefore disjoint, PᵀP is a positive diagonal, and P has full
column rank for every rotor shift. There is no interpolating moving band in this code;
the slip is snapped to the ring grid.

The only null space of K is the piecewise constants on the two meshes. The ring welds tie
the rotor constant to the stator constant (with the cut's sign), and the outer Dirichlet
ring, which is not in `free`, forces the stator constant to 0. So aᵀPᵀKPa > 0 for every
a ≠ 0 on `free`.

`TestProjectionRank` checks this for every shift of an annular model, anti-periodic,
periodic and full ring: one ±1 per row, diagonal PᵀP, and λmin(PᵀKP|free) > 0. The 68
exported production systems had λmin > 0 by Lanczos (PR #56). If a future construction
broke it, the Cholesky factorisation is the guard: a zero or negative pivot is an error,
and LU takes over, logged.

| system (callers) | operator | symmetric because | positive definite because | path |
|---|---|---|---|---|
| secant stiffness (`pic2_sweeps`, `v_picard`, phasor initialiser, Ld/Lq and 6×6 L back-solves) | K(ν): ∫ν ∇u·∇v | bilinear form symmetric in u, v | ν = 1/(μ0·max(μr, 1)) > 0 everywhere; ∫ν‖∇A‖² = 0 only for A constant, which the Dirichlet ring forces to 0 | Cholesky |
| magnetostatic Newton (frame loop, `v_newton`, `eddy_static_state`, the L0 probe) | K + T, T = ∫c (∇A·∇u)(∇A·∇v) | both forms symmetric in u, v | c = 2·max(dν/dB², 0) ≥ 0 (the clamp in `tangent2`), so T is a Gram form, PSD; K is PD | Cholesky |
| bordered eddy Jacobian (`eddy_solve`, `ve_newton`) | [[Pᵀ(K+T+Msig/Δt)P, −B], [−Bᵀ, diag(S·Δt)]] | off-diagonal blocks are −B and −Bᵀ | g_b = M_b·1 and S_b = 1ᵀM_b1 **exactly** (`fem_solver_2d`), so the σ part equals Σ_b (a − Δt·U_b·1)ᵀM_b(a − Δt·U_b·1)/Δt ≥ 0 (paired cut magnets and strands in hand: sums of such terms with one shared U); a = 0, U ≠ 0 gives Σ Δt·S_b·U_b² > 0 | Cholesky |
| bordered eddy with **series strand paths** (`strand_bonding="series"`) | the above plus Kirchhoff rows [0, −ΔtT; −ΔtTᵀ, 0, ΔtQ; ΔtQᵀ, 0] | symmetric | **not definite**: zero diagonal blocks (a saddle point) | LU (`spd=not _sp`) |
| voltage drive (`v_newton`, `ve_newton`) | the circuit is a separate 2×2 Schur complement solved with two extra back-solves | – | only the SPD field (or bordered) matrix is factorised | Cholesky |

The sliding band needs no separate argument: its coupling is part of P. The
frequency-domain cross-check (`eddy_solver_2d`, complex), the mechanical and the 3-D
solvers are not touched.

Tests (`tests/test_p2_cholesky.py`) pin the construction on real P2 operators:

- **Symmetry and definiteness.** The Newton Jacobian of a saturating field is symmetric to
  1e-14 of √(a_ii a_jj) and PD. The bordered matrix built like `eddy_solve` is PD, and **it
  stops being PD when S_b is not the Gram value**, so the proof depends on that identity.
- **The upper triangle.** Its CSR pointers are well formed (start 0, monotone, end = nnz of
  the triangle) and equal `triu(A)`, for CSR and CSC input. Duplicated and unsorted input
  is canonicalised.
- **Cholesky equals LU** to round-off, for CSR, CSC and several right-hand sides.
- **The guards.**
  - Asymmetric values are declined, on a new pattern and on a known one.
  - A skew pair invisible to any single ±1 probe is declined.
  - An entry without its mirror is declined, and so is a missing diagonal.
  - A symmetric indefinite matrix fails Cholesky loudly and is solved by LU.
- **Handles.** LU and Cholesky alternate on changing patterns, and LU keeps working after a
  failed Cholesky.
- **Routing.** A voltage-drive eddy run with series strand paths is refused before any
  solve. It never reaches Cholesky.
- **The pypardiso surface.** pypardiso is pinned to `>=0.4.7,<0.5`, and a test fails if the
  internals the LU-reuse and Cholesky paths call move. The run also checks for them before
  creating the Cholesky handle, and uses LU only otherwise.

## Measured (server sandbox, 6 MKL threads, nice 19, ionice idle, one run at a time)

### Final code (after the 2026-09-30 review), quiet box

The final code does the exact per-pair symmetry check. The box was quiet: no other
container and a 1-min load below 4 at the start. The LU and Cholesky runs of each pair were
back to back.

| machine, mode | frames | headlines max rel. | solve_ff LU → Chol | per-solve median (main) | wall LU → Chol | net of the d-axis calibration |
|---|---|---:|---|---|---|---|
| Ø40 L12, eddy | 146 = 146 | 2.4e-12 | 49.5 → 36.6 s (**1.35×**) | – | 181.6 → 177.7 s (1.02×) | 160.0 → 158.0 s |
| **L155, eddy** | 650 = 650 | 1.6e-11 | 211.7 → 174.5 s (**1.21×**) | 17.7 → 12.3 ms | 581.3 → 545.6 s (**1.065×**) | 543.9 → 510.8 s (1.065×) |

The exact check costs about 3 ms per solve at 0.5 M nnz, about 15 s on an L155 eddy run,
which is included above. On a quiet box the gain is smaller than in the loaded table
below. Contention hurt the LU runs there more than the Cholesky runs: the LU factor is
twice the work and uses more memory bandwidth. The quiet numbers are the honest ones: **the
L155 eddy run is about 6 % faster, and its linear solves 1.2× faster.**

### First version (±1 probe), loaded box

The box was shared with a mesher campaign and another agent's test runs, with a 1-min load
of 5–23 during these runs. Wall and solve totals therefore carry contention noise, and the
pairs ran back to back, not simultaneously.

**Accuracy and totals (LU = `SB_PARDISO_SPD=0`, the previous path):**

| machine, mode | frames LU / Chol | headlines, max rel. diff | time series, max rel. diff | Cholesky solves (declined, failed) | solve_ff total LU → Chol | wall LU → Chol |
|---|---|---:|---:|---|---|---|
| Ø40 L12, static | 72 / 72 | 8e-13 | 5e-13 | 1666 (0, 0) | 32.3 → 59.0 s ¹ | 121 → 182 s ¹ |
| Ø40 L12, eddy | 146 / 146 | 1.3e-12 | 3.1e-12 | 1887 (0, 0) | 87.8 → 47.3 s (1.86×) | 282 → 205 s (1.37×) |
| L13, static | 80 / 80 | 2.9e-12 | 6e-13 | 2200 (0, 0) | 57.1 → 41.9 s (1.36×) | 189 → 182 s |
| L13, eddy | 162 / 162 | 8e-13 | 1.9e-12 | 2854 (0, 0) | 110.3 → 65.9 s (1.67×) | 357 → 274 s (1.30×) |
| L155, static | 72 / 72 | 2.5e-11 ² | 8e-13 | 695 (0, 0) | 35.6 → 27.2 s (1.31×) | 105 → 99 s |
| **L155, eddy** | 650 / 650 | 8.4e-12 | 7.9e-12 | 4733 (0, 0) | **256.0 → 175.6 s (1.46×)** | **798 → 631 s (1.27×)** |

- Headlines are T_avg, ripple, V_peak, P_mag, P_shaft, P_sleeve and the total loss.
- Time series are T(θ), V_A, I_A and the per-frame magnet and shaft losses.
- Every frame count, settle verdict and gauge decision is the same. The differences are the
  threaded MKL's own run-to-run level: LU against LU differs by about 1e-14…2e-12 on the same
  runs (PR #56).
- ¹ The LU run had a quiet box and the Cholesky run a loaded one (main-solve median 6.0 against
  19.8 ms, which is contention, not the factorisation). The same Cholesky case, profiled
  earlier at a lower load, took 27.6 s of solves against 39.2 s for LU (median 4.7 against
  8.6 ms).
- ² The ripple, 1.56 % against 1.56 %: a peak-to-peak of the reported window at 2.5e-11
  relative.

**Per solve, same matrices, same code path** (`P2Nonlinear.solve_ff`, server, 6 threads,
median of 7 with the analysis reused, exported systems of PR #56):

| system | n | LU (mtype 11) | Cholesky incl. checks | speed-up | max rel. difference |
|---|---:|---:|---:|---:|---:|
| Ø40 static Newton | 17,584 | 7.8 ms | 4.8 ms | 1.6× | 6.9e-14 |
| L13 eddy bordered | 30,768 | 15.8 ms | 10.0 ms | 1.6× | 1.6e-13 |
| L155 static Newton | 39,030 | 18.6 ms | 13.2 ms | 1.4× | 2.1e-13 |
| L155 eddy bordered | 44,277 | 24.7 ms | 13.7 ms | 1.8× | 3.0e-13 |

A new pattern (once per frame) costs one analysis either way. The Cholesky analysis is
cheaper (no matching or scaling): 91 against 110 ms on the L155 eddy system.

The physics-regression pins (`tests/test_physics_regression.py`) fail with 9 cases on this
base in the server test image. They fail identically on the LU path: the same drift lines at
the printed six digits. So the baseline at 6ed3641 is stale, which is flagged separately.
`tests/test_p2_cholesky.py` and `tests/test_p2_nonlinear.py` pass.

## Reproduction

Server sandbox `/opt/motres/compute/shaft-chol-20260929` (harness
`profile_fem_run.py` from PR #56; saved duties of the owner's dies, copied):
`EXTRA_ENV="-e SB_PARDISO_SPD=0" ./job.sh <tag> src_chol <case> <static|eddy>` for LU,
the same without the variable for Cholesky.
