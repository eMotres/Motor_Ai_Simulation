# GPU acceleration and time decomposition: study and Stage-2 preparation (2026-09-29)

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no escalation. Branch
`perf/profiling-gpu-tdm` from `pre-migration-freeze-2026-09-15` (5af2c19).
Documents and bench scripts only: **no solver code was changed.** The Stage-1
measurements this study builds on are in `SOLVER_PROFILING_2026-09-29.md`.
The GPU numbers do not exist yet. They will be measured on the owner's
RTX 5070 in the night window with the scripts described in section 5.

## 1. Where the time goes (Stage 1 summary)

Measured on the Hetzner server in a sandbox with 4 MKL threads. The machines
are real duties from the catalogue:

| run | wall | frames solved / reported | linear solves | nonlinear re-assembly (Kpw, tangent2) | rest of the eddy Newton (projection, bmat, residuals) | d-axis calibration |
|---|---:|---:|---:|---:|---:|---:|
| L155 eddy (BDF2) | 534.6 s | 650 / 36 | 209.5 s (39 %) | 192.8 s (36 %) | 99.6 s (19 %) | 35.6 s (7 %)* |
| L13 eddy | 321.2 s | 282 / 40 | 108.8 s (34 %) | 122.7 s (38 %) | 60.5 s (19 %) | 20.0 s (6 %)* |
| Ø40 eddy | 209.6 s | 218 / 36 | 56.9 s (27 %) | 92.9 s (44 %) | 35.9 s (17 %) | 21.1 s (10 %)* |
| L155 static sweep | 101.0 s | 72 / 36 | 36.3 s (36 %) | 33.7 s (33 %) | – | 37.4 s (37 %)* |
| L13 static sweep | 155.6 s | 80 / 40 | 45.5 s (29 %) | 56.9 s (37 %) | – | not run |

\* The calibration row overlaps the solve and re-assembly rows, because it
contains its own solves and assemblies. It runs once per geometry and topology
and is then cached on disk.

Three facts decide the GPU question:

1. **The linear solve is 27–39 % of the wall time.** The rest is sparse-matrix
   construction and Python/NumPy work around the solve. By Amdahl's law, an
   infinitely fast solver gives at most 1.6× on L155 eddy and 1.4× on Ø40.
2. **The systems are small.** The main-loop system has n = 18–44 k DOF and
   nnz = 0.2–0.54 M; the finest exports (mesh ×0.3, ×0.2) are 97 k and 169 k. PARDISO refactorises such a
   system in 8–18 ms on 4 threads when the ordering is reused. With a new
   ordering it takes 76–110 ms (the pattern changes once per rotor position).
   One factor holds 1.0–2.6 M nonzeros and costs 50–170 MFLOP. GPU sparse
   direct solvers are not efficient at this size. Their per-call launch and
   synchronisation overhead is of the same order as the whole CPU phase.
3. **Most eddy frames are warm-up.** L155: 614 of 650 frames (94 %). L13:
   242 of 282 (86 %). Ø40: 182 of 218 (83 %). On L155 the 16-period cap is hit
   only because the **shaft** gauge oscillates (14.9 → 36 → 47 → 105 → 108 →
   11.8 → 32.5 % between periods 6 and 12). Magnets, copper and sleeve are
   below the 2 % tolerance after about 6 periods. This is the largest single
   lever of all. It is a physics or gauge question, not a solver one
   (section 4.5).

## 2. Structure of the linear systems (all measured on exported matrices)

- **Solver in use:** MKL PARDISO through `pypardiso` 0.4.7 (MKL 2026.1),
  `mtype = 11` (real **unsymmetric** LU). Other settings: `iparm[1] = 3`
  (parallel nested dissection METIS), `iparm[9] = 13` (pivot perturbation
  1e-13), `iparm[10] = 1` and `iparm[12] = 1` (scaling and weighted matching),
  FP64 (`iparm[27] = 0`), 2 iterative-refinement steps reported (`iparm[7]`).
  SciPy SuperLU is the fallback, used only after a PARDISO failure.
  All of this was confirmed from the live solver handle during the runs.
- **Direct, not iterative.** Each Newton iteration builds a new matrix with
  the same pattern. `P2Nonlinear.solve_ff` reuses the symbolic analysis
  (phase 11) while `indptr`/`indices` are unchanged, and runs phase 23
  (numeric factorisation + solve) on every iteration. **One analysis per
  rotor position:** the sliding-band slip pairing `Pro` changes the pattern
  every frame. L155 eddy: 666 analyses and 4,751 main solves. The analyses cost
  64 s (12 % of the wall), with a median of 110 ms for a solve with analysis
  against 17.6 ms without.
- **K changes on every Newton iteration** (ν(B) and its tangent), at every
  time step, and at every rotor position (pattern). Nothing can be factorised
  once and reused across iterations. Only the ordering can be reused.
- **Symmetry:** ‖A−Aᵀ‖_F/‖A‖_F ≤ 1e-17 for every exported system. That covers
  the magnetostatic Newton Jacobian K+T, the bordered eddy Jacobian
  [[J+σM/Δt, −B], [−Bᵀ, diag(S/Δt)]] and the Picard stiffness. The diagonal is
  positive everywhere. The unsymmetric LU does roughly twice the necessary
  work (section 3).
- **All systems are symmetric positive definite**, including the bordered
  eddy Jacobian (λmin > 0 measured on all 68 exports). Conditioning, fill
  and bandwidth are in section 3.

## 3. CPU matrix benchmark (server, measured)

`gpu_solver_bench.py --backends pardiso,pardiso_spd,pardiso_sym,superlu`,
run on every exported system at 1, 2, 4 and 6 MKL threads (server
container, `nice 19`). One analysis per pattern, then the median of 5
(factorise + solve) cycles with the same pattern. Times are in ms, "later"
systems (mid-Newton), 4 threads unless stated.

| system | n | nnz | LU mtype 11 (production), 1 / 2 / 4 / 6 thr | new ordering (phase 11), 4 thr | Cholesky mtype 2, 1 / 4 thr | LDLᵀ mtype −2, 4 thr | SuperLU, 1 thr | LU ÷ Cholesky, 4 thr |
|---|---:|---:|---|---:|---|---:|---:|---:|
| Ø40 static Newton | 17,584 | 201,796 | 17.7 / 12.4 / 8.1 / 8.1 | 30.1 | 14.2 / 5.2 | 6.3 | 65.1 | 1.55× |
| Ø40 eddy bordered | 23,931 | 279,785 | 25.9 / 15.4 / 10.4 / 7.4 | 44.3 | 18.7 / 9.9 | 11.8 | 137.1 | 1.05× |
| L13 static Newton | 27,013 | 309,379 | 25.9 / 20.7 / 16.1 / 13.7 | 39.6 | 19.6 / 12.2 | 14.8 | 132.3 | 1.32× |
| L13 eddy bordered | 30,768 | 370,970 | 42.2 / 22.4 / 14.6 / 13.4 | 43.6 | 24.4 / 12.4 | 13.6 | 207.0 | 1.18× |
| L13 ×0.5 static | 35,652 | 408,744 | 61.0 / 30.2 / 20.6 / 16.2 | 51.8 | 22.4 / 14.8 | 17.6 | 278.3 | 1.39× |
| L13 ×0.5 eddy | 38,959 | 470,181 | 55.2 / 33.7 / 23.6 / 19.4 | 58.7 | 31.3 / 16.8 | 24.7 | 418.8 | 1.40× |
| L155 static Newton | 39,030 | 448,064 | 39.3 / 31.9 / 22.6 / 15.5 | 63.0 | 26.6 / 15.1 | 17.9 | 210.5 | 1.50× |
| L155 eddy bordered | 44,277 | 536,271 | 47.3 / 38.1 / 25.8 / 20.2 | 71.8 | 30.6 / 17.8 | 21.1 | 378.8 | 1.45× |
| L155 ×0.5 eddy | 60,754 | 733,912 | 79.9 / 65.8 / 45.9 / 24.9 | 111.3 | 52.9 / 30.2 | 32.3 | 1107.7 | 1.52× |
| L155 ×0.3 eddy | 97,437 | 1,175,275 | 137.9 / 120.4 / 87.4 / 65.5 | 184.2 | 88.1 / 54.2 | 62.6 | 1479.1 | 1.61× |
| L155 ×0.2 eddy | 168,527 | 2,033,763 | 281.7 / 244.3 / 164.5 / 128.2 | 369.2 | 173.8 / 107.1 | 129.4 | 4145.6 | 1.54× |

Accuracy, over all 68 systems and all backends: the largest FP64 relative
residual is 4.2e-13 for all three PARDISO types and 9.2e-13 for SuperLU. The
largest difference to the production solution captured at dump time is
2.6e-12 (LU, run-to-run), 7.6e-12 (Cholesky), 7.2e-12 (LDLᵀ) and 3.6e-10
(SuperLU).

Structure (`analyze_matrices.py`, all 68): **symmetric positive definite
without exception**. Magnetostatic systems have cond₂ 1.4e6–9e7. The
bordered eddy Jacobian has cond₂ 3e11–1e13 (λmin ≈ 1e-5, set by the
conductor constraint rows, and independent of the mesh). SuperLU fill is
10–25× nnz. After RCM the bandwidth is 1–18 k.

What this means:

1. **A CPU win exists today: Cholesky instead of LU**, typically 1.45× per
   solve at equal accuracy. On L155 eddy that is about 60–65 s of 535 s, with
   no new dependency. It is the first thing to prototype behind the backend
   interface (section 8), guarded by a loud fallback to mtype 11 on a
   non-positive pivot.
2. **Threads scale poorly at this size.** 1 → 6 threads gives only
   2.1–3.5× on L155 eddy, and 1.5× from 4 to 6 threads on the 44 k system.
   Six single-thread processes deliver about 2.5× the solve throughput of
   one six-thread process. Process-level parallelism over independent work
   items (section 8.2) is worth more than any faster single solve.
3. **FP32 is ruled out for the eddy systems.** cond₂ ≈ 3e12 × ε₃₂ (6e-8)
   ≈ 2e5 ≫ 1: an FP32 factorisation with FP64 iterative refinement cannot
   converge. For the magnetostatic systems, κ·ε₃₂ ≈ 0.1–5: refinement
   converges slowly on coarse meshes and fails on fine ones. The mixed
   backend is kept in the night bench to prove this, not in the expectation
   that it wins. Row and column equilibration of the constraint block could
   lower the eddy cond₂ by orders of magnitude, since λmin comes from the
   scaling of the constraint rows. That is worth one test before closing the
   FP32 question.
4. **Break-even for a GPU direct solver:** the CPU cycle on the production
   L155 system is 18–26 ms at 4 threads (17.6 ms median measured inside the
   real run). Moving 0.54 M values (4.3 MB) to the device alone takes about
   0.2–0.4 ms over PCIe. The cuDSS numeric factorisation for 2.6 M factor
   entries and 170 MFLOP is launch- and latency-bound. Only the ×0.3 and
   ×0.2 systems (97 k and 169 k, 88–165 ms on CPU) are plausible GPU wins.
   The night run measures exactly this.

## 4. Time decomposition (TDM) for rotating machines

### 4.1 The idea (public literature, not Ansys internals)

A transient eddy-current run marches sequentially:
A_k = F(A_{k−1}, A_{k−2}, θ_k). The periodic steady state is reached only
after the slowest conductor time constant τ has decayed, i.e. after several τ
of simulated time. Time-periodic formulations instead solve **all N steps of
one electrical period at once**. For an m-phase machine with an odd symmetry
(A(t+T/2) = −A(t) in the rotor frame, with the rotor image map), half a
period is enough. The last step is coupled to the first, so the block system
is cyclic:

    [ J_1              −C_1 ] [A_1]   [b_1]
    [ −C_2  J_2             ] [A_2] = [b_2]      C_k = σM/Δt (BDF: two levels)
    [       ...   ...       ] [...]   [...]      periodic or anti-periodic wrap
    [            −C_N  J_N  ] [A_N]   [b_N]

This is solved by Newton's method on the whole block system. No initial
transient exists, because the periodicity condition is imposed rather than
waited for. Established variants:

- **Time-periodic FEM (TPFEM) and its parallelisation.** Takahashi et al.,
  "Parallel Time-Periodic Finite-Element Method for Steady-State Analysis of
  Rotating Machines", IEEE Trans. Magn. 48(2), 2012. Takahashi, Tokumasu,
  Fujita, Iwashita, Nakashima, Wakao, Fujiwara, "Time-Domain Parallel
  Finite-Element Method for Fast Magnetic Field Analysis of Induction Motors",
  IEEE Trans. Magn. 49(5):2413–2416, 2013 (doi 10.1109/TMAG.2013.2245114).
  Also the space-time domain decomposition papers of the same group, IEEE
  Trans. Magn. 2019 and 2021.
- **Time-periodic explicit error correction (TP-EEC).** This is the
  sequential cousin. After half a period, the error of the (anti)periodic
  condition is estimated and corrected, which removes the slow mode. It is
  cheap and has no memory cost.
- **Harmonic balance / multiharmonic FEM.** The unknowns are Fourier
  coefficients, and the nonlinearity is evaluated in the time domain through
  the FFT. See Gyselinck, Dular, Geuzaine, Legros, "Harmonic-balance
  finite-element modeling of electromagnetic devices: a novel approach",
  IEEE Trans. Magn. 38(2), 2002. This is efficient when few harmonics matter.
  PWM and slot harmonics make it expensive.
- **Parareal and MGRIT for time-periodic problems.** Gander, Jiang, Song,
  Zhang, "Analysis of two parareal algorithms for time-periodic problems",
  SIAM J. Sci. Comput. 2013. Kulchytska-Ruchka and Schöps, "Efficient
  parallel-in-time solution of time-periodic problems using a multiharmonic
  coarse grid correction", SIAM J. Sci. Comput. 43(1):C61–C88, 2021
  (arXiv:1908.05245; tested on an induction machine). Friedhoff, Hahne,
  Kulchytska-Ruchka, Schöps, "Parallel-in-time simulation of an electrical
  machine using MGRIT" (arXiv:1912.03106). Gander, Kulchytska-Ruchka,
  Niyonzima, Schöps, "A new parareal algorithm for problems with
  discontinuous sources", SIAM J. Sci. Comput. 2019 (PWM-type excitation).
- Ansys Maxwell's "Time Decomposition Method" is publicly described as
  solving the time steps of a transient simultaneously and distributing them
  over cores or nodes. How it is implemented is not public and is not
  assumed here.

### 4.2 Compared with what we do now

Current method (see `EDDY_TIME_INTEGRATION_2026-09-25.md`):

- sequential BDF2 with a cold start from the static field;
- a 36-frame demag pre-pass;
- whole-period warm-up extensions, up to 16 periods;
- the exact pole-pair **period-shift map**, which carries the rotor eddy
  state across each splice so the march stays continuous;
- a whole-period-mean gauge per conductor group, with a 2 % tolerance.

This is an honest march, but its length is set by physics: the slowest τ
relative to the period, and, as found here, a shaft gauge that does not
decay. There is **no** Richardson or RRE extrapolation of the periodic state
in the code. The "accelerator" is the continuity map plus the adaptive
gauge.

Would a time-periodic solve remove the warm-up? It removes the *physical*
transient, because periodicity is imposed and not waited for. The cost
becomes the iteration count of the block Newton or parareal method. That
count depends on the conditioning of the cyclic coupling: slow modes with
τ ≫ T make block-Jacobi-in-time converge slowly. This is why every practical
method adds a coarse correction (TP-EEC, multiharmonic coarse grid, MGRIT
coarse levels). Literature reports convergence in O(5–10) outer iterations
with a coarse correction. Without one, the count is O(τ/T), the same as
marching. The demag ratchet (irreversible Br) is not time-periodic within the
first pass. It has to stay a sequential pre-pass, as now.

### 4.3 Memory: the one-big-block direct solve is not viable here

For L155, half a period means N = 18 steps × 44 k DOF = 0.8 M unknowns. The
blocks are coupled only through σM on the conducting DOF (magnets, sleeve,
shaft, eddy wires). Nested dissection of the space-time graph then has
separators of the size of the conducting DOF set. With N_c ≈ 10–20 k
(estimated from the rotor and wire mesh, not measured), each dense separator
block holds N_c² × 8 B = 0.8–3.2 GB, and there are several levels. So one
direct factorisation of the whole cyclic system needs tens of GB. The server
(61 GB) could perhaps hold one; the 12 GB RTX 5070 cannot. **Iterative-in-time
methods** (parareal, MGRIT, block-Jacobi + GMRES with a multiharmonic coarse
solve) need only N independent per-step factorisations of 26 MB each (the
PARDISO factor size measured for L155), i.e. about 0.5 GB for 18 steps.

### 4.4 How it would parallelise, and an estimate of the speed-up

Parallel-in-time turns one long chain of small solves into N independent
small solves per outer iteration. This suits our problem size better than
threading one small factorisation over 16 threads. PARDISO at 44 k DOF scales
poorly beyond a few threads (section 3, thread scaling). On the 16-thread
server, a natural layout is 4–8 concurrent steps × 2–4 MKL threads.

A GPU does not help with the time-parallel part at this size either. The
per-step systems are too small for one GPU direct solve to beat a CPU core
group. A batched GPU solver over many time steps (cuDSS batching, nvmath
`DirectSolver` batched operands) is the only GPU form that fits. It would be
worth one experiment after the Stage-2 single-matrix results are in.

Estimate for the steady periodic eddy runs, in "frame solves". A frame solve
is the measured 0.82 s (L155) or 1.14 s (L13) per frame on 4 threads, and
includes Newton, assembly and projection. Assumptions: 5–8 outer iterations,
each costing one period of frame solves; half-period anti-periodicity where
the winding allows it; a parallel efficiency of 0.6–0.8 for 4 concurrent
4-thread streams (memory-bandwidth bound).

| machine | today (frames, wall) | periodic, sequential cost | periodic, 4-way parallel in time | estimated speed-up |
|---|---|---|---|---|
| L155 rated | 650, 535 s | 180–290 frames (full period), 90–145 (half) | 60–120 s | **4–8×** (1.8–3.6× from the work, the rest from parallel time) |
| L13 rated | 282, 321 s | 200–320 frames (full), 100–160 (half) | 50–130 s | **2.5–6×** |
| Ø40 rated | 218, 210 s | similar to L13 | – | **2–5×** |

These are estimates from the Stage-1 numbers under the stated assumptions.
The iteration count is the unknown that decides the result, and only a
prototype measures it.

### 4.5 Recommendation

1. **First, and cheapest: investigate the L155 shaft gauge.** If the shaft
   were quiet like the other groups after about 6 periods, the L155 run would
   drop from 650 to about 290 frames (**~2.2×, 535 → ~240 s**) with no new
   method. The gauge series (14.9, 36.4, 47, 105, 108, 11.8, 32.5 %) is not
   an exponential decay. Possible causes are a sub-harmonic, the frame
   sampling of a small shaft signal, or a non-periodic state of the shaft
   ring. This needs an Opus physics look. It must not be "fixed" by loosening
   the tolerance.
2. **Then prototype TP-EEC-style (anti)periodic error correction** in the
   existing sequential march. It is cheap and memory-free, it sits next to the
   period-shift map, and it attacks exactly the slow-mode warm-up. The
   literature reports warm-up reduced several-fold.
3. **Parallel-in-time (parareal or block-Jacobi-GMRES with a multiharmonic
   coarse correction): prototype only if 1–2 leave the eddy runs dominant.**
   It is the most invasive option (BDF2 history, circuit coupling, demag
   pre-pass, strand paths). Its payoff (4–8× on L155) is real but is
   estimated, not measured.
4. A single direct block TDM solve: **no** (memory, section 4.3).

## 5. Stage 2: GPU proof of concept (prepared, to run at night on the RTX 5070)

Scripts are in `scripts/bench/`. Nothing runs by day, and nothing touches
the live API or configuration.

| file | what it does |
|---|---|
| `profile_fem_run.py` | Stage-1 profiler: a solver-direct run of a catalogue duty (`l155_rated`, `l13_rated`, `d40_rated`; modes `static`, `eddy`, `dump`). Records timers and every linear solve, optional cProfile, optional matrix export. `--backend cudss_fp64|cudss_mixed|...` routes every P2 linear solve through a GPU backend (bench-only monkeypatch) for the engineering A/B. |
| `summarize_profile.py` | cProfile → own time per module group, top functions, named stages |
| `analyze_matrices.py` | symmetry, definiteness (shift-invert Lanczos), condition estimate, fill, bandwidth |
| `gpu_backends.py` | one interface `analyze / factorize / solve / solve_reuse`: `pardiso` (mtype 11), `pardiso_spd` (2), `pardiso_sym` (−2), `superlu`, `cudss_fp64`, `cudss_fp64_sym`, `cudss_fp64_spd`, `cudss_fp32`, `cudss_mixed` (FP32 factor + FP64 iterative refinement), `cupy_qr`, `cupy_gmres_jac`, `amgx` |
| `gpu_solver_bench.py` | per matrix and backend: analysis once, then 7× (factorise + solve) with the same pattern (the production cycle). Reports transfer time, GPU memory, FP64 residual, difference to the CPU solution, and the break-even n |
| `compare_engineering.py` | full-run A/B: torque, ripple, V_peak/EMF, flux linkage, Ld/Lq, peak B, every loss, the waveforms, and the frame count. PASS/FAIL at a relative tolerance of 1e-6 |
| `ginkgo/` | small C++ driver (GMRES + block-Jacobi, GMRES + ParILU, experimental sparse direct LU) plus CMake. Built by the owner only if he wants the Ginkgo data point |
| `night_gpu_bench.ps1` | refuses to run between 06:00 and 22:00. Runs 1) the matrix bench, 2) Ginkgo if built, 3) the engineering A/B (CPU vs cuDSS FP64 vs cuDSS mixed on Ø40, L13 and L155, static and eddy), then comparisons |

Matrices (68 systems plus metadata, each with b and the CPU FP64 solution x) are in:

- the server: `/opt/motres/compute/solver-profiling-20260929/matrices/`
- the owner's PC: `C:\Users\vadim\Downloads\solver_matrices\` (not in git)

Naming: `<case>_s<mesh scale>_<caller>_<first|later>_{A.npz,A.mtx,b.mtx,bx.npz,meta.json}`.
Callers: `fem_transient_sliding_band` (magnetostatic Newton K+T),
`eddy_solve` (bordered eddy Jacobian), `eddy_static_state` (bordered static
start), `pic2_sweeps` (Picard stiffness). Sizes run from 17.6 k to 169 k DOF
(section 3).

What to expect, stated before measuring so it can be proved wrong: cuDSS FP64
**will not beat** PARDISO below roughly 100–200 k DOF on this hardware. The
production systems are 18–44 k. FP32 or mixed factorisation fails on the
eddy systems (cond₂ ≈ 3e12, section 3) and is marginal on the magnetostatic
ones. `cudss_fp64_spd` / `cudss_fp64_sym` (Cholesky / LDLᵀ on the GPU) are valid on every system,
because all are SPD, and gets the same ~1.5× as on CPU.
The engineering A/B **must** show identical frame counts, and headline
quantities within 1e-9..1e-6 for FP64. If the matrix bench confirms this,
the GPU is not worth integrating for the 2-D solver, and effort should go to
sections 4.5 and 6 of `SOLVER_PROFILING_2026-09-29.md`.

## 6. What the owner must install (not installed by the agent)

Hardware: RTX 5070 is Blackwell GB205, compute capability **12.0 (sm_120)**.
It needs CUDA **12.8 or newer** and an NVIDIA driver **R570 or newer**
(R580+ for CUDA 13).

Minimum, and all the matrix bench and A/B need (pip wheels; no system CUDA
Toolkit required):

1. NVIDIA Game Ready or Studio driver, current version (≥ 580 covers both
   CUDA 12.9 and 13.x). Check with `nvidia-smi`: the "CUDA Version" in the
   header must be ≥ 12.8.
2. A separate venv, so the production environment is untouched:

   ```powershell
   & 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' -m venv C:\Users\vadim\venvs\gpu_bench
   C:\Users\vadim\venvs\gpu_bench\Scripts\python.exe -m pip install -U pip
   C:\Users\vadim\venvs\gpu_bench\Scripts\python.exe -m pip install -r C:\Users\vadim\Projects\motor_ai_sim\requirements.txt
   # CUDA 12 line (driver >= 570):
   C:\Users\vadim\venvs\gpu_bench\Scripts\python.exe -m pip install "cupy-cuda12x>=14.2" "nvmath-python[cu12]>=1.0"
   # or, if the installed driver/toolkit is CUDA 13 (driver >= 580), INSTEAD:
   # ... -m pip install "cupy-cuda13x>=14.2" "nvmath-python[cu13]>=1.0"
   ```

   `nvmath-python[cu12]` pulls `nvidia-cudss-cu12==0.8.*` (win_amd64 wheels
   exist for 0.8.0.10), cuBLAS, cuSPARSE, cuSOLVER and the runtime as pip
   wheels.
3. Check: `C:\Users\vadim\venvs\gpu_bench\Scripts\python.exe -c "import cupy, nvmath; print(cupy.cuda.runtime.getDeviceProperties(0)['name'], nvmath.__version__)"`

Optional:

- CUDA Toolkit 12.9 (or 13.x) system install: needed only to build Ginkgo or AmgX.
- Ginkgo ≥ 1.9 built with `-DGINKGO_BUILD_CUDA=ON`, then
  `cmake -S scripts\bench\ginkgo -B scripts\bench\ginkgo\build -DCMAKE_PREFIX_PATH=<ginkgo install>`.
  I recommend skipping it for the first night.
- AmgX + pyamgx: pyamgx is not on PyPI (source build only) and is
  Linux-oriented. AMG targets SPD elliptic systems and needs a symmetric
  saddle-point treatment for the bordered eddy system. Skip it unless the
  cuDSS results make iterative GPU solvers interesting.

Run (night only) from the branch checkout (the worktree
`C:\Users\vadim\Projects\motor_ai_sim_perf` now, or the main checkout once the
PR is merged):
`powershell -ExecutionPolicy Bypass -File C:\Users\vadim\Projects\motor_ai_sim_perf\scripts\bench\night_gpu_bench.ps1`.
The code comes from the `src` next to the script. `config\dies` and the
config files are read-only from `C:\Users\vadim\Projects\motor_ai_sim\config`,
with a copied `motor_config.yaml` per run. Results go to
`C:\Users\vadim\Downloads\solver_matrices\results_<timestamp>\`.

## 7. Licences (repository: AGPL-3.0-or-later)

| component | licence | how it may be used |
|---|---|---|
| CuPy | MIT | compatible; optional dependency |
| nvmath-python | Apache-2.0 | compatible with (A)GPLv3 (one-way); optional dependency |
| NVIDIA cuDSS, cuSPARSE, cuSOLVER, CUDA runtime | NVIDIA proprietary licence agreements | **optional, user-installed backend only, never bundled or vendored**, same pattern as MKL/pypardiso today |
| Ginkgo | BSD-3-Clause | compatible |
| AmgX | BSD-3-Clause | compatible; still needs the proprietary CUDA runtime |
| pyamgx | licence in the repository's LICENSE.txt; not verified | verify before any use |
| MKL (via pypardiso) | Intel Simplified Software License | already an optional, user-installed dependency |

Note: `pyproject.toml` still declares `license = {text = "MIT"}` with an MIT
classifier, although the brief states the project is AGPL-3.0-or-later. This
should be reconciled by the owner. It was not changed here (docs and scripts
only).

## 8. Solver abstraction and hybrid scheduler: design, not built

### 8.1 One interface for all linear-solver backends

The production seam already exists: every P2 linear solve goes through
`P2Nonlinear.solve_ff(M, rhs)`, which owns the "analyse once per pattern,
factorise per iteration" logic. The design keeps that seam and moves the
backend behind it:

```python
class LinearBackend(Protocol):
    name: str                      # "pardiso", "cudss_fp64", ...
    device: Literal["cpu", "gpu"]
    def analyze(self, A: csr_matrix) -> None: ...        # pattern only
    def factorize(self, A: csr_matrix) -> None: ...      # same pattern, new values
    def solve(self, b: np.ndarray) -> np.ndarray: ...    # host FP64 in and out
    def release(self) -> None: ...                       # deterministic (pardiso_lifetime)
    capabilities: frozenset  # {"symmetric", "multi_rhs", "batched", "fp32_factor"}
```

- `solve_ff` keeps the pattern check, the perturbed-pivot counter and the
  SuperLU fallback. The backend is chosen once per run from an explicit
  setting (`solver_backend: auto|pardiso|cudss`), is **never silently
  switched mid-run**, and is recorded in the result (`linear_backend`) for
  provenance.
- `auto` = PARDISO unless a GPU backend is installed **and** n exceeds the
  measured break-even for this host (from the Stage-2 table, stored in config).
- Symmetric matrix types become a backend capability. The caller passes a
  `symmetric=True` hint where the assembly guarantees symmetry (all current
  callers do). Section 3 measures what that is worth on CPU.
- Acceptance for any backend: `compare_engineering.py` PASS on the three
  catalogue duties plus the physics-regression suite.

### 8.2 Hybrid CPU+GPU scheduler for independent work items

The granularity is the **independent work item**, not the single solve:

- an optimizer candidate (a whole `em_transient_eval`);
- a point of an operating-map or speed sweep;
- magnetostatic positions *only* where a cold start is acceptable. Today's
  sweeps warm-start frame k from k−1, so positions are not independent
  without losing that warm start. The extra Newton cost of cold frames has
  not been measured (`solver-performance-profile-plan-821f3df-2026-09-24.md`
  lists it as a known loss). Eddy and demag state are strictly sequential.

Design:

1. A queue of work items, each with a cost estimate from its own mesh size
   (n_dof × frames × Newton iterations; the Stage-1 per-frame costs give
   the coefficients).
2. CPU workers: `floor(threads / t)` processes with `t` MKL threads each. The
   thread-scaling curve in section 3 decides t. At 44 k DOF, more processes
   with fewer threads beat one process with 16 threads.
3. GPU worker (0 or 1 per GPU): takes items whose n is above the break-even
   and uses the GPU backend for its solves. Its own CPU thread still does the
   assembly, which is 36–44 % of the wall and stays on the CPU.
4. Admission uses the existing sandbox rules (nice 19, a CPU-busy gate) and
   each item's own `pardiso_scope`. No shared PARDISO handle crosses
   processes (see `pardiso-global-solver-concurrency-2026-09-24.md`).
5. Results carry `linear_backend`, threads and host, for provenance and
   reproducibility.

The scheduler is worth building **before** any GPU backend: process-level
parallelism over candidates uses the 16-thread server better than faster
single solves (section 3). A GPU worker is simply one more worker type.
