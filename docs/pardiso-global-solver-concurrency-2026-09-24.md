# Shared PARDISO solver concurrency (2026-09-24)

`pypardiso.spsolve` uses one module-global `PyPardisoSolver`. The job queue can
run the static 3-D, Nédélec direct, and mechanical contact paths in parallel.
Their solve calls, and the two paths that clear a damaged factorization before
retrying, previously had no common lock. A second job could use the same native
handle during a solve or between the cache reset and retry.

`pardiso_lifetime.global_pardiso_session()` now supplies one process-wide
reentrant lock for these three paths. Each solve holds it through any reset and
retry. A successful factorization is still cached for later solves. Explicit
`PyPardisoSolver` instances keep their existing per-run lifetime; the lock does
not apply to those independent handles.

Fake-backend thread tests cover all three callers, plus atomic reset and retry
from static 3-D and mechanical contact. The focused PARDISO and Stage-A tests
passed (28 passed, 27 deselected). No MKL or FEM run was performed; numerical
results and performance under actual concurrent jobs remain unmeasured.
