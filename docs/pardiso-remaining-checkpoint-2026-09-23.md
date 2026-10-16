# Remaining PARDISO lifetime checkpoint

`simulation/pardiso_lifetime.py` scopes explicitly constructed
`PyPardisoSolver` handles with a `ContextVar`-local `ExitStack`. `own_pardiso`
registers idempotent cleanup; `release_pardiso` supports early/fallback release
even for an unregistered handle. It protects the P2 solver's per-run handle,
not module-global `pypardiso.spsolve` state, and it is not a mutex for MKL.

Remaining explicit handle: `simulation/static3d/nedelec.py`,
`_regularised_preconditioner` (line 561). Its normal `_cg` caller releases in
`finally` (line 611), but if `ps.factorize(Areg)` allocates native state and
then raises, the broad fallback handler drops the Python reference without
calling `free_memory`. This is the clearest bounded cleanup gap.

Remaining singleton users are `simulation/static3d/solver.py::_linear_solver`
(line 358), `simulation/static3d/nedelec.py::_direct` (line 636), and
`simulation/mechanical/contact.py::_solver` (line 1079). They call
`pypardiso.spsolve`; static3d also clears its module-global stored
factorization after a non-finite result. That cached solver/factorization is
process-lived and intentionally reused, so this is retention rather than a
per-call handle leak. The static3d route serializes its own endpoint, but no
common lock is visible around these singleton solve/clear operations; overlap
with Nedelec or contact work could race if their callers run concurrently.

Smallest safe next fix: in `_regularised_preconditioner`, call
`release_pardiso(ps)` in the factorization-failure path before falling back to
SuperLU. Add a fake `PyPardisoSolver` test whose `factorize` allocates then
raises; assert one `free_memory(everything=True)` call and successful fallback.
Do not claim that `pardiso_scope` serializes or owns global `spsolve`; covering
that would require a shared lock adapter at all singleton call sites and a
separate concurrency test.

## Applied bounded fix

`nedelec._regularised_preconditioner` now initializes the handle to `None` and,
if factorization raises after construction, calls `release_pardiso` before
attempting SuperLU. Cleanup is guarded so a cleanup exception cannot suppress
the existing fallback. Successful PARDISO cleanup remains on the existing
returned callback path. Focused fake-solver tests verify failed factorization
releases once and SuperLU solves the regularized system, and normal callback
cleanup releases once. No singleton `spsolve` behavior or concurrency policy
was changed.
