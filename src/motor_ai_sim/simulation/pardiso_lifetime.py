"""Deterministic ownership of the native solver memory for one FEM run.

PyPardisoSolver has no destructor that releases MKL's factorization buffers.
Keep its raw handle (and symbolic reuse) intact during a run, then release
everything on every exit, including failures before P2Nonlinear is created.

Since 2026-09-30 the same scope also owns the run's
``linear_backend.LinearSolver`` (CHOLMOD / MUMPS / PARDISO factors): anything
registered here is released with ``free_memory(everything=True)`` when it has
that method (a PyPardisoSolver) and with ``free()`` otherwise.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
import logging
from threading import RLock


_log = logging.getLogger(__name__)
_scope: ContextVar[tuple[ExitStack, dict] | None] = ContextVar(
    "pardiso_lifetime", default=None)
_global_solver_lock = RLock()


@contextmanager
def global_pardiso_session():
    """Serialize use of pypardiso's shared ``spsolve`` solver.

    Hold this lock through a solve, factorization reset, and retry as one
    operation.  Its reentrancy also permits a reset helper to take the same
    lock.  Explicit ``PyPardisoSolver`` instances have separate ownership and
    do not use this session.
    """
    with _global_solver_lock:
        yield


class _Release:
    def __init__(self, solver):
        self.solver = solver
        self.released = False

    def __call__(self):
        if self.released:
            return
        # Mark first: a failed native release must not be retried on scope
        # exit, and must not prevent the caller's SuperLU fallback.
        self.released = True
        try:
            if hasattr(self.solver, "free_memory"):
                self.solver.free_memory(everything=True)
            else:
                self.solver.free()
        except Exception:
            _log.warning("PARDISO memory cleanup failed" if hasattr(
                self.solver, "free_memory") else "linear solver memory cleanup "
                "failed", exc_info=True)


def pardiso_scope(function):
    """Give each synchronous invocation its own native-resource lifetime.

    Nested calibration solves and simultaneous worker threads own separate
    stacks. No handle is shared, and a nested return restores its parent scope.
    """
    @wraps(function)
    def wrapped(*args, **kwargs):
        with ExitStack() as stack:
            token = _scope.set((stack, {}))
            try:
                return function(*args, **kwargs)
            finally:
                _scope.reset(token)
    return wrapped


def own_pardiso(solver):
    """Register immediately after construction; return the unchanged handle."""
    scope = _scope.get()
    if scope is None:
        raise RuntimeError("PARDISO ownership requires a pardiso_scope")
    stack, owners = scope
    if id(solver) not in owners:
        owner = owners[id(solver)] = _Release(solver)
        stack.callback(owner)
    return solver


def own_pardiso_if_scoped(solver):
    """``own_pardiso`` inside a ``pardiso_scope``; outside one (worker threads,
    standalone callers) the object is returned unregistered and its owner
    releases it (``free()`` / ``release_pardiso``)."""
    if _scope.get() is None:
        return solver
    return own_pardiso(solver)


def release_pardiso(solver):
    """Release a failed backend now, without releasing it again at run exit.

    Standalone P2Nonlinear users may supply an unregistered handle; release
    that too before solve_ff drops its reference and switches to SuperLU.
    """
    scope = _scope.get()
    owner = scope[1].get(id(solver)) if scope is not None else None
    (owner if owner is not None else _Release(solver))()
