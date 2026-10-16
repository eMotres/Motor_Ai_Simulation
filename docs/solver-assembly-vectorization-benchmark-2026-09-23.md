# P2 stiffness assembly vectorization check — 2026-09-23

Compared the existing `_Skeleton.stiff` assembly with one broadcasting-based
variant using the same reluctivity data, operation order, quadrature reduction,
and scikit-fem COO-to-CSR conversion. Reproduce with
`scratch_perf/bench_skeleton_vectorized.py`.

The fixture is synthetic: `MeshTri().refined(6)`, 8,192 P2 triangles, 16,641
degrees of freedom, six quadrature points, and deterministic random positive
reluctivity. It benchmarks matrix assembly only; it runs no motor FEM solve.
The generated CSR matrices matched exactly in shape, `indptr`, `indices`, and
all data values (bitwise equality; maximum absolute difference 0).

With one numerical-library thread and BelowNormal process priority, the median
of seven calls was 14.379 ms for current assembly and 15.649 ms for the
vectorized candidate. The candidate was about 8.8% slower in this fixture. Its
explicit integrand temporary was 14,155,776 bytes versus 393,216 bytes for the
current loop's largest per-pair temporary; the cached geometry tensor was
14,155,776 bytes in either case.

Decision: reject this vectorized candidate. The result is limited to a
synthetic assembly microbenchmark, not a motor mesh or full transient; it does
not rule out other candidates or establish end-to-end performance.

Verification used the isolated Python 3.11 runner, one thread, a 60-second
hard wall limit, and BelowNormal priority. The process exited successfully.
No production source was changed and no FEM solve was run. Codex Luna; no
escalation.
