"""Deterministic ownership of the native PARDISO memory for one FEM run.

PyPardisoSolver has no destructor that releases MKL's factorization buffers.
Keep its raw handle (and symbolic reuse) intact during a run, then release
everything on every exit, including failures before P2Nonlinear is created.
"""
from __future__ import annotations

from contextlib import ExitStack
from contextvars import ContextVar
from functools import wraps
import logging


_log = logging.getLogger(__name__)
_scope: ContextVar[tuple[ExitStack, dict] | None] = ContextVar(
    "pardiso_lifetime", default=None)


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
            self.solver.free_memory(everything=True)
        except Exception:
            _log.warning("PARDISO memory cleanup failed", exc_info=True)


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


def release_pardiso(solver):
    """Release a failed backend now, without releasing it again at run exit.

    Standalone P2Nonlinear users may supply an unregistered handle; release
    that too before solve_ff drops its reference and switches to SuperLU.
    """
    scope = _scope.get()
    owner = scope[1].get(id(solver)) if scope is not None else None
    (owner if owner is not None else _Release(solver))()
