# 2-D P2 solver profiling on real machines (Stage 1, 2026-09-29)

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no escalation. Branch
`perf/profiling-gpu-tdm` from `pre-migration-freeze-2026-09-15` (5af2c19).
**No solver code was changed**; the timers are wrappers installed at run time
by `scripts/bench/profile_fem_run.py`. The GPU and time-decomposition
conclusions are in `GPU_TDM_STUDY_2026-09-29.md`.

## Setup

- Host: Hetzner AX server (16 threads, 61 GB), `deploy-api` image (Python
  3.11.16, NumPy 2.4.4, SciPy 1.17.1, scikit-fem 12.0.1, MKL 2026.1,
  pypardiso 0.4.7). Sandbox `/opt/motres/compute/solver-profiling-20260929`:
  a container per run, read-only source and dies, `nice 19`, `ionice idle`,
  `--cpus 4`, **4 MKL/OMP threads**, one run at a time. The CPU-busy gate
  started a job only when others used < 45 % (another agent's mesher study
  was using about 37 %). The live API and workspaces were not touched, and
  health was `healthy` before and after.
- Code: the branch source as committed (base 5af2c19), default mesher
  (geometry-driven tile mesher; `iron_template` falls back to it because of
  the retaining sleeve), `SB_NO_WARM_CACHE=1`, cold runs.
- Machines, taken from the saved duties in the owner's `config/dies`
  (copied): **L155 motor rated 1x9 mm** (CIANO10 200 opt), **L13 rated**
  (CIANO28 85 20SW1200) and **Ø40 L12 rated** (CIANO14 40 new, the 40 mm
  preset). Each duty's own mesh and simulation settings were used (36/40/36
  steps, demag as saved, 2 sectors).
- Modes: `static` is the magnetostatic sweep (eddy and rotor eddy off);
  `eddy` is the transient eddy exactly as the duty runs it (BDF2, demag
  pre-pass, adaptive warm-up, rotor eddy).
- cProfile adds overhead, so every wall time in the tables comes from runs
  *with* cProfile for `st_*` and `ed_d40`. `ed_l155` and `ed_l13` ran without
  it. The shares are consistent between the two kinds of run.

Engineering results match the committed references (for example L155 eddy:
T = 187.939 N·m, ripple 1.570 %, V_peak 436.61 V, P_mag 92.26 W; the
2026-09-25 A/B reported 187.939 / 1.570 / 436.61 / 92.26). The harness
therefore measures the production path.

## 1. Wall time by stage

Disjoint split. The "rest" row is the remainder: loss and torque
post-processing, source assembly, Python frame bookkeeping, and the
projection and slicing in the magnetostatic loop.

### Transient eddy (BDF2)

| stage | L155 rated | L13 rated | Ø40 L12 rated |
|---|---:|---:|---:|
| **wall** | **534.6 s** | **321.2 s** | **209.6 s** |
| frames solved (reported + warm-up incl. 1-period demag pre-pass) | 650 = 36 + 614 | 282 = 40 + 242 | 218 = 36 + 182 |
| eddy settled? | **no, capped at 16 periods** | yes | yes |
| s per frame | 0.82 | 1.14 | 0.96 |
| geometry + meshing | 0.24 s (0.0 %) | 0.25 s | 0.32 s |
| materials | 0.16 s | 0.26 s | 0.48 s |
| d-axis calibration (incl. its own solves) | 35.6 s (6.7 %) | 20.0 s (6.2 %) | 21.1 s (10.1 %) |
| **linear solves (PARDISO), all** | **209.5 s (39.2 %)** | **108.8 s (33.9 %)** | **56.9 s (27.1 %)** |
| of which with a new ordering (phase 11 + 23) | 64.1 s (666 solves) | 23.6 s (290) | 15.7 s (244) |
| of which reusing the ordering (phase 23) | 132.5 s (4,085) | 77.7 s (3,506) | 34.2 s (2,155) |
| **nonlinear re-assembly** Kpw + tangent2 + elemB | **192.8 s (36.1 %)** | **122.7 s (38.2 %)** | **92.9 s (44.3 %)** |
| eddy Newton body without solves/assembly (Pᵀ J P, bmat, residuals, line search) | 99.6 s (18.6 %) | 60.6 s (18.9 %) | 35.9 s (17.1 %) |
| frequency-domain cross-check (`rotor_eddy_solver_bc`) | 1.7 s | 1.0 s | 1.5 s |
| rest | ~30 s (5.6 %) | ~20 s (6 %) | ~20 s (10 %) |
| bordered Newton iterations per frame (solves / frames) | 7.3 | 13.4 | 10.9 |

### Magnetostatic sweep (eddy off)

| stage | L155 rated | L13 rated | Ø40 L12 rated |
|---|---:|---:|---:|
| **wall** | **101.0 s** | **155.6 s** | **106.6 s** |
| frames solved / reported | 72 / 36 | 80 / 40 | 72 / 36 |
| Newton iterations per frame, mean / max | 9.2 / 10 | 2.2 / 3 | 3.0 / 9 |
| geometry + meshing | 0.36 s | 0.29 s | 0.28 s |
| d-axis calibration | 37.4 s (37.1 %) | not run in this mode | not run in this mode |
| **linear solves** | **36.3 s (35.9 %)** | **45.5 s (29.2 %)** | **28.1 s (26.4 %)** |
| **Kpw + tangent2 + elemB** | **33.7 s (33.4 %)** | **56.9 s (36.6 %)** | **44.0 s (41.3 %)** |
| rest (projection Pᵀ K P, free-set slicing, source, post) | ~20 s (20 %) | ~53 s (34 %) | ~34 s (32 %) |

The sweep solves each reported frame twice. `n_frames_solved` is 2× the
reported count because of the settling pass. Every frame re-analyses the
pattern (72–81 analyses per run).

### Module view (cProfile own time, disjoint)

| group | L155 static | L13 static | Ø40 eddy |
|---|---:|---:|---:|
| pypardiso / MKL | 35.3 % | 28.7 % | 26.7 % |
| scipy.sparse (CSR/COO conversion, `+`, `@`, slicing, sort, sum_duplicates) | 31.8 % | 31.0 % | 30.4 % |
| NumPy (95 % of it is `np.sum` in `_Skeleton.stiff/tang`) | 18.8 % | 24.1 % | 25.5 % |
| P2 kernels (Python) | 5.8 % | 6.9 % | 8.9 % |
| losses / post-processing | 1.7 % | 2.8 % | 2.5 % |
| mesher, geometry, triangle | 0.4 % | 0.3 % | 0.2 % |

Top individual hotspots (Ø40 eddy, 209.6 s):

- `_call_pardiso`: 55.9 s
- `np.sum` inside `_Skeleton.stiff`/`tang`: 30.0 s
- `csr_plus_csr`: 12.8 s (K_const + iron part, J = K + T, J + σM)
- `coo_tocsr`: 9.5 s (the skeleton's COO → CSR every call)
- `csr_matmat`: 8.2 s (Pᵀ J P)
- `csr_sort_indices` + `sum_duplicates`: 8.7 s
- `c_einsum`: 4.1 s
- `_grad_at_quad`: 3.0 s
- `p2_projection.find`: 1.3 s over 7.1 M calls

**Geometry and meshing are not a cost** (≤ 0.4 s per run; the tile mesher
meshes one cell and replicates it). Neither is post-processing (≤ 3 %).

## 2. The linear systems

| property | finding (measured) |
|---|---|
| solver | MKL PARDISO via `pypardiso.PyPardisoSolver`, one handle per run (`pardiso_lifetime`); SciPy SuperLU only after a PARDISO failure; no run used it |
| matrix type | `mtype = 11` real unsymmetric, LU with scaling + weighted matching (`iparm[10] = iparm[12] = 1`), pivot perturbation 1e-13, 0 perturbed pivots on every run |
| ordering | `iparm[1] = 3` (parallel nested dissection, METIS), phase 11 re-run only when `indptr`/`indices` change |
| precision | FP64 (`iparm[27] = 0`); 2 refinement steps reported (`iparm[7]`) |
| direct vs iterative | direct; no iterative solver anywhere in the 2-D path |
| main-loop size | Ø40 17.6 k (static) / 23.9 k (eddy); L13 27.0 k / 30.8 k; L155 39.0 k / 44.3 k DOF; nnz 0.20–0.54 M (11.5–12.1 per row) |
| factor | nnz(L+U) 0.96 M (Ø40 static) … 2.58 M (L155 eddy), 52–168 MFLOP, 10–26 MB, peak 23–59 MB |
| time per solve (4 threads) | ordering reused: 7.9 ms (Ø40) … 17.6 ms (L155); with a new ordering: 76 … 110 ms |
| when K changes | every Newton iteration (ν(B) and the tangent), every time step, every rotor position |
| when the pattern changes | every rotor position (sliding-band pairing `Pro`), and occasionally within a frame (exact zeros from `max(dν/dB², 0)` dropped by `eliminate_zeros`) |
| factorisations per run | L155 eddy 4,751 numeric (666 with a new ordering); L13 eddy 3,796 (290); Ø40 eddy 2,399 (244); static sweeps 695–2,200 (72–81) |
| symmetry | ‖A−Aᵀ‖_F/‖A‖_F ≤ 1e-17 for every system type (magnetostatic Jacobian, bordered eddy Jacobian, Picard stiffness); positive diagonal throughout |
| definiteness, conditioning | **SPD, all of them**; cond₂ 1.4e6–9e7 (magnetostatic), 3e11–1e13 (bordered eddy), section 3 |

The bordered eddy system is
[[Pᵀ(J+σM/Δt)P, −B], [−Bᵀ, diag(S/Δt)]], with 65 extra rows on L155 (one
per conducting body: the ∫J = 0 constraints). The magnetostatic Newton
Jacobian is Pᵀ(K+T)P restricted to the free set.

## 3. Exported matrices (Stage-2 input)

All 68 exported systems (34 pairs "first"/"later" per caller). The table
lists the magnetostatic Newton and bordered eddy systems per mesh level.
cond₂ = λmax/λmin from shift-invert Lanczos (`analyze_matrices.py`).

| machine, mesh scale | static Newton K+T: n / nnz / cond₂ | bordered eddy Jacobian: n / nnz / cond₂ | eddy static start: cond₂ | SuperLU fill (eddy) |
|---|---|---|---|---:|
| Ø40 L12 ×1 | 17,584 / 201,796 / 2.1e+06 | 23,931 / 279,785 / 1.1e+13 | 2.0e+07 | 11.6 |
| L13 ×1 | 27,013 / 309,379 / 1.4e+06 | 30,768 / 370,970 / 3.3e+11 | 2.2e+06 | 13.2 |
| L13 ×0.5 | 35,652 / 408,744 / 4.8e+06 | 38,959 / 470,181 / 3.3e+11 | 7.5e+06 | 17.4 |
| L155 ×2 | 36,957 / 424,395 / 3.0e+06 | 42,384 / 513,632 / 4.9e+12 | 7.0e+06 | 13.7 |
| L155 ×1 | 39,030 / 448,064 / 4.9e+06 | 44,277 / 536,271 / 3.4e+12 | 4.9e+06 | 15.2 |
| L155 ×0.7 | 44,170 / 506,974 / 8.6e+06 | 49,375 / 597,589 / 3.0e+12 | 8.0e+06 | 18.4 |
| L155 ×0.5 | 55,607 / 638,015 / 1.7e+07 | 60,754 / 733,912 / 2.9e+12 | 1.6e+07 | 25.0 |
| L155 ×0.3 | 97,372 / 1,117,818 / 3.9e+07 (Picard K) | 97,437 / 1,175,275 / 2.9e+12 | 4.3e+07 | 19.7 |
| L155 ×0.2 | 168,462 / 1,935,416 / 8.9e+07 (Picard K) | 168,527 / 2,033,763 / 2.9e+12 | 9.6e+07 | 22.6 |

**Every system is symmetric positive definite**: the magnetostatic Newton
Jacobian, the Picard stiffness, the bordered eddy static start *and* the
bordered eddy Jacobian. λmin > 0 was measured on all 68. The magnetostatic
systems have cond₂ 1.4e6–9e7, growing with refinement. The bordered eddy
Jacobian has cond₂ 3e11–1e13, almost independent of the mesh. Its λmin
≈ 1e-5 is set by the conductor constraint rows and the σ/Δt scaling, not by
the mesh. The ×0.3 and ×0.2 levels were exported from the eddy path only,
so their "static" column is the Picard stiffness of the static start.

Location: server `/opt/motres/compute/solver-profiling-20260929/matrices/`,
owner's PC `C:\Users\vadim\Downloads\solver_matrices\`. Not in git. For each
system: `_A.npz` (scipy CSR), `_A.mtx` + `_b.mtx` (MatrixMarket), `_bx.npz`
(b and the CPU PARDISO FP64 solution x) and `_meta.json` (caller, sizes,
symmetry, the CPU residual 1e-15..3e-13, the PARDISO statistics of that
solve).

**The mesh-scale knob does little until it is pushed hard.** Scaling
`mesh_size_mm`, `min_size_mm` and the absolute component sizes by
2.0 → 0.5 moves the L155 eddy system only from 42 k to 61 k DOF. 0.3 gives
97 k and 0.2 gives 169 k. The element size in these machines is set by the tile
budgets, the structured gap belt (2 × 720 elements), the wire patches and the
skin-depth rules (`mesh-follows-physical-scales`), not by the global size.
The "very fine" systems are therefore modest. The break-even search in
Stage 2 uses what exists; larger synthetic systems would not be real
matrices.

## 4. Python overhead hotspots

1. **Sparse assembly bookkeeping: 30 % of every run.** Every Newton
   iteration builds new CSR matrices through COO → CSR → sort →
   sum_duplicates → `+ K_const` → `+ T` → `Pᵀ(·)P` → free-set slicing →
   `tocsc`. The **pattern** of all of these is fixed within a frame, and so
   is the scatter map. A precomputed value-scatter map (COO slot → final
   Pᵀ(K+T)P CSR slot, one sparse matvec or `np.add.at` per iteration) would
   remove most of the scipy.sparse time. Caveat: the summation order changes,
   so the result is not bit-identical. That matters because the solver's
   existing optimisations were held to bit-identity; an A/B against the
   physics baseline would be needed.
2. **`_Skeleton.stiff/tang` quadrature: `np.sum(..., axis=1)` over 36 local
   pairs** (25 % in NumPy). `stiff` is symmetric in (i, j) *bit for bit*,
   since gg[j,i] and gg[i,j] are the same IEEE products. Computing 21 pairs
   and mirroring would cut its kernel time by ~40 % and stay bit-identical.
   `tang` is not bit-symmetric as written. A single `einsum`/`matmul`
   contraction over the quadrature axis instead of 36 Python-level sums is
   the larger (non-bit-identical) option.
3. **One PARDISO analysis per rotor position: 12 % of the L155 eddy wall.**
   The sliding-band pairing changes the pattern every frame. A superset
   pattern (the union over the band's slip positions, with explicit zeros
   kept and `eliminate_zeros` avoided) would allow one analysis per run:
   ≈ 60 s of 535 s on L155. Also not bit-identical (ordering from a
   different pattern). The existing perturbed-pivot counter would show
   whether pivoting suffers.
4. **The d-axis calibration costs 20–37 s per geometry** (37 % of the L155
   static sweep). It is cached on disk per topology, so sweeps pay it once,
   but every new optimiser geometry pays it again.
5. `p2_projection.find` is called 5–7 M times per run (1–1.3 s). This is
   small, but it is a per-element Python loop.

6. **The factorisation type.** Every system is SPD, but PARDISO runs an
   unsymmetric LU (mtype 11). On the same matrices, Cholesky (mtype 2) is
   1.05–1.61× faster per factorise + solve at 4 threads (typically about
   1.45×), with identical accuracy: FP64 residual ≤ 4.2e-13 for both, and a
   difference to the production solution ≤ 7.6e-12
   (`GPU_TDM_STUDY_2026-09-29.md` §3). On L155 eddy that is about 60–65 s of
   535 s. It is not bit-identical. It needs a guard: mtype 2 fails loudly on
   a non-positive pivot, so keep mtype 11 as the fallback, never silently.

The linear solve itself is only 27–39 %. **No single backend change,
CPU or GPU, can deliver more than 1.4–1.6× on these runs** (Amdahl). The
largest levers are the eddy warm-up length (94 % of the L155 frames are
warm-up; section 4.5 of the GPU/TDM study) and the sparse bookkeeping above.

## 5. Reproduction

On the server (the sandbox is already populated):

```sh
cd /opt/motres/compute/solver-profiling-20260929
./job.sh ed_l155 l155_rated eddy                    # one run, 4 threads, nice/idle
./job.sh st_l155 l155_rated static --cprofile /work/out/st_l155.pstats
./job.sh dm_l155_s1 l155_rated dump --mesh-scale 1.0 --dump /work/matrices --dump-stop 2 --dump-later 5
docker run --rm --entrypoint python -v $PWD:/w deploy-api /w/code/summarize_profile.py /w/out/st_l155.pstats
./cpu_mat.sh                                        # structure analysis + CPU PARDISO types/threads
```

Results: `out/<tag>.json` (timers, per-caller solve statistics, PARDISO
iparm, engineering results), `out/<tag>_solves.json` (one row per linear
solve: time, caller, n, nnz, analysis flag, duration, factor statistics) and
`out/<tag>.pstats`.
