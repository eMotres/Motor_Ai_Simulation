# PARDISO ordering reuse across frames (2026-09-29): measured, opt-in

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no escalation. Branch
`perf/pardiso-ordering-reuse` on top of `perf/cholesky` (the Cholesky PR). Owner-approved
extra task: stop re-running the reordering (phase 11) on every frame.

**Verdict: the reordering is saved, but the run is not faster at 6 threads.** Phase 11
falls from 79 to 41 ms per new pattern (−25 s on an L155 eddy run). PARDISO, however,
factorises 16–34 % slower from a permutation it is given than from its own METIS tree: the
fill is identical and the parallel schedule is lost. Over 4733 factorisations that costs
+40 s. The mechanism ships **opt-in** (`SB_PARDISO_ORDER_REUSE=1`), exact and tested, for
low-thread workers where it is neutral-to-positive. The default is unchanged.

## The problem

`P2Nonlinear.solve_ff` already reuses the analysis while the sparsity pattern holds, which
is within one frame's Newton. The pattern changes at every rotor position, because the
sliding-band slip pairing is part of the projection. So every frame pays one full phase 11:
METIS nested dissection plus the symbolic factorisation. On the L155 eddy run (PR #56) that
was 666 analyses at about 110 ms against 17.6 ms for a reused solve, 64 s of 535 s.

## Options, measured

| option | verdict |
|---|---|
| (a) one PARDISO handle per pattern, kept analysed | about 20 MB of symbolic data per handle on the L155 eddy system (`iparm[15]`), times 36 angles = 0.7 GB per run: rejected |
| (b) one union pattern over the sliding band, explicit zeros | over one electrical period the rotor turns one pole pair, so every rotor band dof would couple to a fifth of the stator band: the fill would explode. Rejected without building it |
| **(c) cached METIS permutation per pattern, phase 11 with `iparm[4] = 1`** | 0.18 MB per pattern. Phase 11 then does the symbolic factorisation only. **Chosen** |

Why (c) is exact:

- METIS's ordering is a function of the pattern.
- The patterns of a run repeat every electrical period. Every warm-up extension, the demag
  pre-pass and the reported window march the same rotor angles, and the slip pairing, the
  only thing that moves the pattern, repeats with them.
- Giving phase 11 the permutation METIS produced for the same pattern gives the same
  ordering, so the same factorisation.
- Measured on the exported L155 eddy system (44,277 dofs) with one MKL thread: the solution
  is **bit-identical** (max |Δx| = 0) and the analysis drops from 99 ms to 30–35 ms. Threaded
  MKL keeps its usual run-to-run noise (below).

A collision of the pattern key (n, nnz and two CRC-32 checksums) could only cost fill-in,
never accuracy: any permutation is a valid ordering. It is also detected. A cached
permutation that gives a different factor size (`iparm[17]`) than when it was computed is
logged as a warning, dropped, and re-ordered with METIS.

## Implementation

- `P2Nonlinear._analyse_spd` (Cholesky path only).
- The first analysis of a handle is plain. MKL fills its defaults and ignores `iparm[4]`,
  then leaves `iparm[0] = 1`.
- Afterwards a new pattern runs METIS with `iparm[4] = 2`, which returns the permutation. It
  is stored in an LRU of at most 512 patterns.
- A known pattern runs phase 11 with `iparm[4] = 1` and the stored permutation.
- Counters go to the result (`linear_solver.orderings_computed`, `orderings_reused`,
  `ordering_mismatches`) and to the run's cost log line.
- **Opt-in:** `SB_PARDISO_ORDER_REUSE=1`. By default every new pattern is re-ordered by METIS,
  as before (measured below).
- The unsymmetric LU path (series strand paths, fallbacks) is unchanged. Its analysis also
  computes value-dependent matchings, so a stored ordering is not the whole analysis there.

## Measured

Server sandbox, 6 MKL threads, nice 19, idle IO. Runs started only while the mesher queue
was idle and the load was below 4. Phase timings are wrapper timers around
`_analyse_spd` / `_solve_spd`; the d-axis calibration is subtracted (it is cached on disk
and ran in some jobs only).

**L155 rated eddy (650 frames), ABBA order:**

| run | net wall [s] | frames/s | phase 11 total (per call) | phase 23 + checks | orderings reused |
|---|---:|---:|---|---:|---:|
| off (1) | 504.9 | 1.287 | 54.9 s (78.9 ms) | 112.0 s | 0 |
| on (1) | 508.2 | 1.279 | 29.1 s (42.0 ms) | 151.1 s | 581 of 669 |
| on (2) | 505.8 | 1.285 | 28.6 s (41.1 ms) | 147.9 s | 585 of 672 |
| off (2) | 495.9 | 1.311 | 53.3 s (76.9 ms) | 107.2 s | 0 |

- Mean: off 500.4 s against on 507.0 s, **−1.3 %**, within the spread of the pairs.
- Per frame: 0.770 against 0.780 s.
- Accuracy on vs off: headlines 1.9e-11 and series 1.3e-12. That is the same as off vs off
  (1.9e-11): run-to-run noise of threaded MKL. Frames and settle verdicts are identical.

**Why:** the same L155 eddy system (exported, local, 6 threads, median of 15):

| analysis | phase 11 | phase 23 | fill nnz(L) |
|---|---:|---:|---:|
| METIS (default) | 100 ms | 20.9 ms | 1,466,346 |
| given permutation (`iparm[4] = 1`) | 33 ms | 24.4 ms | 1,466,346 |

**Ø40 L12 rated eddy:**

- 6 threads: 76 of 173 orderings reused, wall 177.5 → 172.2 s, solves 35.8 → 36.3 s. Noise
  level.
- **1 MKL thread: every scalar and every time series bit-identical** (off against on, 76
  reuses). This is the exactness claim.

**Static sweeps** reuse almost nothing: 0 of 72 (Ø40) and 2 of 75 (L155). The settling pass
of the sweep does not reproduce the first pass's patterns. There is nothing to gain there.

**Rejected on the way:** `iparm[23] = 10` (improved two-level factorisation).

- With METIS it factorises the L155 eddy system in 16.6 ms instead of 20.9 ms (−20 %, a
  difference of 4e-13).
- With a given permutation it returns garbage: a relative error of 6.5e+69, with no error
  code.
- It is not part of this change. As a separate change on the default path it is worth one
  validated A/B, with an assertion that it is never combined with `iparm[4] = 1`.

## Tests

`tests/test_p2_cholesky.py::TestOrderingCache`:

- alternating patterns reuse their orderings (3 computed, 3 reused over 6 solves), and every
  solution matches the uncached path to 1e-12 relative;
- a wrong cached ordering (tampered factor size) is detected, logged and redone, and the
  solve is still correct.
