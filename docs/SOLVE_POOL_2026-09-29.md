# Process-level solve pool (`SOLVE_POOL=1`), 2026-09-29

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no escalation. Branch
`feat/solve-pool` from `pre-migration-freeze-2026-09-15`. Evidence behind the
decision: `SOLVER_PROFILING_2026-09-29.md` and `GPU_TDM_STUDY_2026-09-29.md`
(PR #56): the 2-D P2 solve scales poorly with threads, and six single-thread
processes gave 1.7-1.8x (LU) / 2.65-2.7x (Cholesky) the solve throughput of one
six-thread process on the AX42.

**Opt-in.** With `SOLVE_POOL` unset (the default) every code path is the
in-process one, byte for byte. The owner decides the default after this bench.

## Design

| piece | where | what |
|---|---|---|
| scheduler | `src/motor_ai_sim/solve_pool.py` (`SolvePool`) | `QUEUE_PROCS` slots (default physical cores - 2; 6 on the AX42); a slot only while free RAM (cgroup limit included) covers the per-solve RSS estimate; ordering = job priority, then solves the owner has running, then solves the owner has been served, then arrival |
| load rule | `SolvePool._threads_for` | `threads = clamp(procs // n_active, 1, solo)`, `n_active` = running + waiting: alone = 1 job x `SOLVE_POOL_SOLO_THREADS` (default `min(procs, 4)`, see the bench), full pool or a queue behind it = N jobs x 1 thread, even split in between; re-decided on every dispatch and release |
| worker | `src/motor_ai_sim/solve_pool_child.py` | a fresh interpreter per solve; thread variables set in its environment before numpy loads; length-prefixed pickles on the original stdout (fd 1 is re-pointed at stderr, so solver prints cannot corrupt it); re-threaded between frames with threadpoolctl when the load changes; exits if the API process disappears |
| context | `solve_pool.capture_context` | workspace, caller, write layer, material override, bearing temperature, optimizer-candidate and no-warm-cache flags, mesh triangle budget, and the parsed config the API holds for the caller's file |
| Simulation / duty path | `routes/simulation.get_fem_transient` | `em_transient_eval` through the pool (progress and cancel replay on the job thread; the warm seed and new d-axis calibrations the child produced are copied back into this process's caches); only identical requests (same workspace, same key) still serialise |
| optimizer / sweep path | `routes/optimization._subprocess_eval` | the same `refine_proc` command, but slot, width (pinned 1, or up to the solo point's request when the pool is idle) and priority from the pool; Stop withdraws waiting evals and kills running trees |
| accounting | `job_usage` | each child is registered with the run and account it solves for; a full tick weights a job by its thread CPU plus its children's CPU; a child of an un-metered job (a campaign under `jobs.admit`) is credited to its account directly |
| queue | `jobs.default_workers` | with the pool on and `QUEUE_WORKERS` unset, as many admitted jobs as process slots (an explicit `QUEUE_WORKERS` still wins; the server sets 2 today) |

Cancel kills the child's whole tree within one poll (0.2 s). A child that exits
without an answer raises `SolveWorkerDied` with the decoded exit code (137 /
SIGKILL = out-of-memory kill, SIGSEGV, Windows access violation, ...) and the
last stderr lines; the job is recorded `failed` and the slot is released.
Children run at BelowNormal priority on Windows and `nice 10` on Linux
(`SOLVE_POOL_NICE`).

Environment: `SOLVE_POOL`, `QUEUE_PROCS`, `SOLVE_POOL_SOLO_THREADS`,
`SOLVE_POOL_RSS_MB` (fixed estimate; default: learned, 1500 MB before the
first sample), `SOLVE_POOL_RAM_RESERVE_MB` (2048), `SOLVE_POOL_NICE` (10).

What stays in-process: field views (`field_jobs`, short), the 3-D static
path, a call with an `excitation` object or an active P2 state capture, and any
call whose arguments do not pickle (logged).

## Verification

**Unit tests** (`tests/test_solve_pool.py`, 36 tests, Windows and Linux
(the `motres-api:test` image)): dispatch, the load rule (alone / even split /
full), fixed-width commands counted by width, priority, round robin and
fewer-running-first among owners, the RAM cap and the cgroup limit, the learned
RSS estimate, cancel while waiting, cancel by tag, the context a child sees
(workspace, caller, material override, thread variables, lowered priority),
progress replayed on the calling thread, re-threading when the load changes,
`jobs.cancel_run` killing child and grandchild within 3 s, a child exiting
with 137 failing its job with `SolveWorkerDied` (and the pool serving the next
solve), a remote `ValueError` re-raised as itself, command mode (pinned width,
timeout, cancel while waiting), per-child CPU attribution, the keyed transient
lock. `tests/test_jobs.py`, `tests/test_job_usage.py` and the route tests that
stub the solver (`test_run_ledger*.py`, `test_physics_cache_refresh.py`,
`test_cogging_frame_policy.py`) pass unchanged.

**Bench** — server AX42, sandbox `/opt/motres/compute/solvepool-20260929`
(deleted afterwards), `deploy-api` image, the live API container's limits
(`--cpus 12 --cpuset-cpus 0-11 --memory 40g`: 8 physical cores + 4 SMT
siblings), `nice 19`, `ionice -c3`, `SB_NO_WARM_CACHE=1`, started only after
every other campaign queue had stopped. Case: the 40 mm preset (CIANO14 40
new / L12 / rated duty), magnetostatic with the duty's demag pre-pass, 36
steps, 2 sectors. Script: `scripts/bench/solve_pool_bench.py`.

Equality, one point, in-process vs pool, both single-threaded: **bit-identical**.
Static: 347 numeric fields compared, every one with relative difference 0
(torque, EMF `V_peak` / `V_line_rms_solved_V`, `P_cu_W`, `P_fe_W`,
`P_mag_eddy_W`, total loss, mechanical power); only the solve's own wall-clock
field differs. Transient eddy (BDF2, rotor eddy): 258 fields, all 0. The pooled
run replayed 717 / 791 progress events. Across thread counts the 12 sweep
points agree to <= 2.9e-14 relative in torque.

Throughput, 12 independent sweep points (current 50-105 % of the duty):

| layout | wall, 12 points | points / h | vs today | one solve alone | peak RSS |
|---|---:|---:|---:|---:|---:|
| in-process, MKL default (today's API container: 12 threads) | 2198 s | 19.7 | 1.0x | 180 s | 390 MB |
| in-process, 1 thread | 671 s | 64.4 | 3.3x | 54 s | 369 MB |
| pool, `QUEUE_PROCS=4` | 213 s | 203 | 10.3x | 58 s (4 thr) | 1.17 GB tree, 347 MB / child |
| **pool, `QUEUE_PROCS=6`** | **180 s** | **240** | **12.2x** | **58 s** (4 thr) | **1.66 GB** tree, 355 MB / child |
| pool, `QUEUE_PROCS=8` | 209 s | 206 | 10.5x | 60 s (4 thr) | 2.19 GB tree, 353 MB / child |

Against the fair single-thread in-process baseline the pool gives 3.2x (4),
3.7x (6) and 3.2x (8). Eight concurrent solves are slower than six: each solve
then takes ~120 s instead of ~85 s (memory bandwidth and SMT siblings in the
0-11 cpuset). One lone solve at 1 / 4 / 6 / 8 threads took 54-60 / 58-61 / 64
/ 171 s, so threads give nothing on this machine size and more than six are
harmful; the default solo width is therefore `min(procs, 4)`. The pooled
single solve costs ~4-6 s more than the in-process one at one thread
(interpreter start and imports), and is 3x faster than today's 12-thread
in-process run.

A first pass (before the fix now in `solve_pool_child.em_transient_eval_target`)
dispatched the first points of a burst wide and never narrowed them when the
caller passed no progress callback: 6 procs took 291 s and 8 procs 325 s. The
child now always gives the solver a hook that applies a pending width change.

**Recommendation for the AX42:** `SOLVE_POOL=1`, `QUEUE_PROCS=6` (the default,
physical cores - 2), `SOLVE_POOL_SOLO_THREADS` unset (4), and raise
`QUEUE_WORKERS` from 2 to 6 so that six users' jobs can actually reach the
pool. Independently of the pool: today's API container lets MKL start 12
threads for one solve, which the bench measured 3.3x slower than one thread;
`MKL_NUM_THREADS=4` (and `OMP_NUM_THREADS=4`) in `api.env` would fix that
even with the pool off.

## Risks

- With the pool on, `get_fem_transient` bodies of different requests run
  concurrently in the API process (only identical requests still serialise).
  The solve itself is in the child, but the route's post-processing now runs in
  parallel threads for the first time; the per-workspace stores are thread-safe
  by design (Stage 3), but this has not been load-tested with real users.
- A child starts cold: its in-memory mesh caches are empty (disk caches, the
  warm seed and the d-axis calibration are shared through disk and the echo).
  Measured cost: ~4-6 s per solve.
- Memory: ~350 MB per 40 mm child; L155 children are larger. The RSS estimate
  starts at 1.5 GB per solve and then learns from finished children.
- The child protocol is pickle over the child's own pipes (no network).
- Not measured on the Cholesky branch; PARDISO Cholesky may scale better with
  threads, in which case the solo cap can be revisited with
  `SOLVE_POOL_SOLO_THREADS`.
