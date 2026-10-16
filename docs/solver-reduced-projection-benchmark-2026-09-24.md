# Reduced P2 projection microbenchmark — 2026-09-24

## Fixture and method

`scratch_perf/bench_reduced_projection.py` compares the current reduction
`(Pro.T @ K @ Pro).tocsr()[free][:, free].tocsc()` with a cached reduced
projection `Q = Pro[:, free]`, followed by `(Q.T @ K @ Q).tocsc()`. It also
compares residual projection (`Pro.T @ v` then `free` versus `Q.T @ v`).
The script imports no project code and runs no FEM, API, or configuration
access.

No saved `Pro`/`free` or full stiffness/Jacobian was available. The saved
`scratch_perf/dump/Mff_3.npz` and `rhs_3.npy` were read only to obtain the
reduced reference dimensions, nnz, and RHS. That reference is 23,138 DOF and
274,982 nnz. The benchmark builds deterministic synthetic symmetric sparse
matrices and a one-nonzero-per-row binary/sign projection: `Pro` is
25,574 × 24,356 with 25,574 nnz; synthetic full `K` is 25,574 × 25,574 with
280,348 nnz; reduced `Q` has 24,294 nnz. It is close in size to the saved
reduced solve, but is not a motor matrix or an actual sliding-band projection.

The run used one numerical-library thread and seven timed repetitions after a
warm-up. Median times were:

| Operation | Existing path | Cached `Q` path |
|---|---:|---:|
| K reduction | 8.853 ms | 3.961 ms |
| J reduction | 8.307 ms | 3.986 ms |
| Five residual-vector projections | 0.214 ms | 0.154 ms |

Preparing `Q` took 0.178 ms; its CSR storage was 393,828 bytes (about 394 KB).
K and J had identical shapes and sparse patterns, with maximum absolute
matrix difference 0. Five residual projections were exactly equal. Using the
saved RHS, the two SuperLU solutions were exactly equal; both relative
residuals were 3.5125e-16.

## Decision

This is a positive synthetic result, not enough evidence for a production
change. The existing `scratch_perf/r_base_4_both.json` run measured 4.895 s of
sparse multiplication in 119.893 s total (979 calls across 10 solved frames),
about 4.1% of that run. This bounds only the instrumented multiplication
component; it is not a prediction of end-to-end savings. Conversion and slice
costs were not timed separately. Other saved full-run profiles vary widely, so
their wall times do not provide a stable extrapolation.

**No production change is recommended yet.** First capture actual `Pro`,
`free`, and representative full K/J from one existing guarded solver frame.
Then compare matrix shape, indices, values, reduced solve, and residual using
the two paths, and time the same captured inputs. Until that check passes, the
synthetic binary/sign fixture does not establish equivalence for the actual
sliding-band interpolation projection. `scratch_perf/bench_reduced_projection.py`
is an untracked reproducibility aid; no FEM was run and no production source
was edited. Codex Luna; no escalation.
